"""Test doubles: an in-memory Store and a scripted Tutor. No network, no database."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ebb.domain import Attempt, Concept, Grade, GradeValue, Option, Question, Subject
from ebb.llm.schemas import ConceptDraft
from ebb.llm.tutor import AllProvidersExhausted, Generated

# (type, difficulty) of each question in a fake batch, in order.
BATCH_PATTERN = [("short", 1), ("mcq", 1), ("mcq", 2), ("short", 3), ("mcq", 3)]


class MemoryStore:
    def __init__(self) -> None:
        self.profiles: set[UUID] = set()
        self.subjects: dict[UUID, tuple[UUID, Subject]] = {}
        self.concepts: dict[UUID, tuple[UUID, Concept]] = {}  # insertion order = position
        self.sessions: dict[UUID, dict] = {}
        self.questions: dict[UUID, Question] = {}
        self.attempts: dict[UUID, Attempt] = {}

    def ensure_profile(self, user_id: UUID) -> None:
        self.profiles.add(user_id)

    def create_subject(self, user_id: UUID, name: str, concepts: Sequence[tuple[str, str]]) -> UUID:
        subject = Subject(uuid4(), name, datetime.now(UTC))
        self.subjects[subject.id] = (user_id, subject)
        for concept_name, description in concepts:
            concept = Concept(uuid4(), subject.id, concept_name, description)
            self.concepts[concept.id] = (user_id, concept)
        return subject.id

    def list_subjects(self, user_id: UUID) -> list[Subject]:
        owned = [s for owner, s in self.subjects.values() if owner == user_id]
        return sorted(owned, key=lambda s: s.created_at, reverse=True)

    def list_concepts(self, user_id: UUID, subject_id: UUID) -> list[Concept]:
        return [
            c
            for owner, c in self.concepts.values()
            if owner == user_id and c.subject_id == subject_id
        ]

    def create_session(self, user_id: UUID, subject_id: UUID, thread_id: str) -> UUID:
        if self.subjects.get(subject_id, (None,))[0] != user_id:
            raise LookupError(f"subject {subject_id} not found")
        session_id = uuid4()
        self.sessions[session_id] = {"user_id": user_id, "thread_id": thread_id, "ended_at": None}
        return session_id

    def end_session(self, user_id: UUID, session_id: UUID, now: datetime) -> None:
        if self.sessions[session_id]["user_id"] == user_id:
            self.sessions[session_id]["ended_at"] = now

    def save_questions(
        self, user_id: UUID, session_id: UUID, questions: Sequence[Question]
    ) -> None:
        for question in questions:
            self.questions[question.id] = question

    def record_attempt(self, user_id: UUID, attempt: Attempt, concept_after: Concept) -> bool:
        if attempt.id in self.attempts:
            return False
        self.attempts[attempt.id] = attempt
        owner, _ = self.concepts[concept_after.id]
        self.concepts[concept_after.id] = (owner, concept_after)
        return True

    def concept(self, concept_id: UUID) -> Concept:
        return self.concepts[concept_id][1]


class FakeTutor:
    """Short answers: "right" is correct, "meh" partial, anything else incorrect.
    Multiple choice: option 1 is always the correct one."""

    def __init__(
        self,
        batch_size: int = 5,
        fail_grade_times: int = 0,
        fail_generate: bool = False,
        fail_teach: bool = False,
        fell_back: bool = False,
        fail_generate_on_call: int | None = None,
    ) -> None:
        self.batch_size = batch_size
        self.fail_grade_times = fail_grade_times
        self.fail_generate = fail_generate
        self.fail_teach = fail_teach
        self.fell_back = fell_back
        self.fail_generate_on_call = fail_generate_on_call  # 1-based call number that fails
        self.generate_attempts = 0
        self.generate_calls: list[str] = []
        self.counter = 0

    def _out[T](self, value: T) -> Generated[T]:
        return Generated(value, "fake-model", self.fell_back)

    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]:
        drafts = [
            ConceptDraft(name=f"{topic} {n}", description=f"Idea {n} of {topic}.")
            for n in range(1, 7)
        ]
        return self._out(drafts)

    def generate_questions(self, concept: Concept, avoid: list[str]) -> Generated[list[Question]]:
        self.generate_attempts += 1
        if self.fail_generate or self.generate_attempts == self.fail_generate_on_call:
            raise AllProvidersExhausted("generation down")
        self.generate_calls.append(concept.name)
        questions = []
        for kind, difficulty in BATCH_PATTERN[: self.batch_size]:
            self.counter += 1
            options: tuple[Option, ...] = ()
            if kind == "mcq":
                options = (
                    Option("right option", True, "This is the definition."),
                    Option("wrong option A", False, "That confuses cause and effect."),
                    Option("wrong option B", False, "That is a different layer."),
                    Option("wrong option C", False, "That is the opposite."),
                )
            questions.append(
                Question(
                    id=uuid4(),
                    concept_id=concept.id,
                    type=kind,
                    difficulty=difficulty,
                    prompt=f"{concept.name} question {self.counter}",
                    reference_answer="right",
                    options=options,
                )
            )
        return self._out(questions)

    def grade(self, question: Question, answer: str) -> Generated[Grade]:
        if self.fail_grade_times > 0:
            self.fail_grade_times -= 1
            raise AllProvidersExhausted("grading down")
        value = {"right": GradeValue.CORRECT, "meh": GradeValue.PARTIAL}.get(
            answer, GradeValue.INCORRECT
        )
        return self._out(Grade(value, f"Graded {value.value}.", None))

    def teach(self, question: Question, answer: str, grade: Grade) -> Generated[str]:
        if self.fail_teach:
            raise AllProvidersExhausted("teaching down")
        return self._out("Here is the idea, with an example.")


def make_subject(store: MemoryStore, user_id: UUID, concepts: int = 4) -> UUID:
    store.ensure_profile(user_id)
    return store.create_subject(
        user_id,
        "Networks",
        [(f"Concept {n}", f"About concept {n}.") for n in range(1, concepts + 1)],
    )


def seed_concept(store: MemoryStore, concept_id: UUID, **changes) -> None:
    owner, concept = store.concepts[concept_id]
    store.concepts[concept_id] = (owner, replace(concept, **changes))
