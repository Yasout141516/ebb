"""Test doubles: an in-memory Store and a scripted Tutor. No network, no database."""

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ebb import mastery
from ebb.domain import (
    Answer,
    Chunk,
    Concept,
    Document,
    Grade,
    GradeValue,
    NewDocument,
    Option,
    Question,
    ResumableSession,
    Subject,
)
from ebb.llm.schemas import ConceptDraft
from ebb.llm.tutor import AllProvidersExhausted, Generated
from ebb.store import MAX_DOCUMENTS, PRUNE_AFTER, RESUME_WINDOW, DocumentRefused, new_concepts

# (type, difficulty) of each question in a fake batch, in order.
BATCH_PATTERN = [("short", 1), ("mcq", 1), ("mcq", 2), ("short", 3), ("mcq", 3)]


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[^\W_]+", text) if len(w) >= 3}


class MemoryStore:
    def __init__(self) -> None:
        # document id -> (user id, subject id, what was added, what list_documents shows)
        self.documents: dict[UUID, tuple[UUID, UUID, NewDocument, Document]] = {}
        self.profiles: set[UUID] = set()
        self.subjects: dict[UUID, tuple[UUID, Subject]] = {}
        self.concepts: dict[UUID, tuple[UUID, Concept]] = {}  # insertion order = position
        self.sessions: dict[UUID, dict] = {}
        self.questions: dict[UUID, Question] = {}
        self.attempts: dict[UUID, Answer] = {}
        self.stages: dict[UUID, tuple[int, int]] = {}  # answer id -> (stage before, after)
        self.question_sessions: dict[UUID, UUID] = {}  # question id -> session id
        self.timezones: dict[UUID, str] = {}
        self.locks: set[str] = set()

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
        self.sessions[session_id] = {
            "user_id": user_id,
            "subject_id": subject_id,
            "thread_id": thread_id,
            "ended_at": None,
            "last_active_at": datetime.now(UTC),
            "pruned_at": None,
        }
        return session_id

    def end_session(self, user_id: UUID, session_id: UUID, now: datetime) -> None:
        if self.sessions[session_id]["user_id"] == user_id:
            self.sessions[session_id]["ended_at"] = now

    def save_questions(
        self, user_id: UUID, session_id: UUID, questions: Sequence[Question]
    ) -> None:
        if self.sessions.get(session_id, {}).get("user_id") != user_id:
            raise LookupError(f"session {session_id} not found")
        for question in questions:
            if self.concepts.get(question.concept_id, (None,))[0] != user_id:
                raise LookupError("concept not found")
        for question in questions:
            self.questions[question.id] = question
            self.question_sessions[question.id] = session_id

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept:
        question = self.questions.get(answer.question_id)
        owner, concept = self.concepts.get(answer.concept_id, (None, None))
        session = self.sessions[self.question_sessions[question.id]] if question else None
        if (
            question is None
            or question.concept_id != answer.concept_id
            or session["user_id"] != user_id
            or owner != user_id
        ):
            raise LookupError(f"question {answer.question_id} not found")
        if answer.id in self.attempts:
            return concept  # already recorded: the stored concept holds this grade
        after = mastery.apply_grade(concept, answer.grade, answer.graded_at)
        self.attempts[answer.id] = answer
        self.stages[answer.id] = (concept.stage, after.stage)
        self.concepts[concept.id] = (owner, after)
        session["last_active_at"] = answer.graded_at
        return after

    def answered_prompts(self, user_id: UUID, concept_id: UUID, limit: int = 40) -> list[str]:
        newest: dict[str, datetime] = {}
        for answer in self.attempts.values():
            question = self.questions[answer.question_id]
            if question.concept_id != concept_id or self.concepts[concept_id][0] != user_id:
                continue
            newest[question.prompt] = max(
                newest.get(question.prompt, answer.graded_at), answer.graded_at
            )
        return sorted(newest, key=newest.__getitem__, reverse=True)[:limit]

    def get_subject(self, user_id: UUID, subject_id: UUID) -> Subject | None:
        owner, subject = self.subjects.get(subject_id, (None, None))
        return subject if owner == user_id else None

    def add_concepts(
        self, user_id: UUID, subject_id: UUID, concepts: Sequence[tuple[str, str]]
    ) -> int:
        if self.subjects.get(subject_id, (None,))[0] != user_id:
            raise LookupError(f"subject {subject_id} not found")
        existing = [c.name for c in self.list_concepts(user_id, subject_id)]
        fresh = new_concepts(existing, concepts)
        for name, description in fresh:
            concept = Concept(uuid4(), subject_id, name, description)
            self.concepts[concept.id] = (user_id, concept)
        return len(fresh)

    def create_subject_with_notes(
        self,
        user_id: UUID,
        name: str,
        documents: Sequence[NewDocument],
        concepts: Sequence[tuple[str, str]],
    ) -> UUID:
        subject_id = self.create_subject(user_id, name, new_concepts([], concepts))
        for document in documents:
            self._store_document(user_id, subject_id, document)
        return subject_id

    def add_document(
        self,
        user_id: UUID,
        subject_id: UUID,
        document: NewDocument,
        concepts: Sequence[tuple[str, str]],
    ) -> int:
        if self.subjects.get(subject_id, (None,))[0] != user_id:
            raise LookupError(f"subject {subject_id} not found")
        if self.has_document(user_id, subject_id, document.sha256):
            raise DocumentRefused("duplicate")
        if len(self.list_documents(user_id, subject_id)) >= MAX_DOCUMENTS:
            raise DocumentRefused("full")
        self._store_document(user_id, subject_id, document)
        return self.add_concepts(user_id, subject_id, concepts)

    def _store_document(self, user_id: UUID, subject_id: UUID, document: NewDocument) -> None:
        shown = Document(
            uuid4(),
            document.filename,
            document.kind,
            document.size_bytes,
            document.page_count,
            len(document.chunks),
            datetime.now(UTC),
        )
        self.documents[shown.id] = (user_id, subject_id, document, shown)

    def list_documents(self, user_id: UUID, subject_id: UUID) -> list[Document]:
        return [
            shown
            for owner, subject, _, shown in self.documents.values()
            if owner == user_id and subject == subject_id
        ]

    def has_document(self, user_id: UUID, subject_id: UUID, sha256: str) -> bool:
        return any(
            owner == user_id and subject == subject_id and document.sha256 == sha256
            for owner, subject, document, _ in self.documents.values()
        )

    def search_chunks(
        self, user_id: UUID, subject_id: UUID, text: str, limit: int = 4
    ) -> list[Chunk]:
        """Ranks by the number of words shared with `text`; at least two are needed."""
        wanted = _words(text)
        scored: list[tuple[int, Chunk]] = []
        for owner, subject, document, shown in self.documents.values():
            if owner != user_id or subject != subject_id:
                continue
            for c in document.chunks:
                shared = len(wanted & _words(f"{c.heading or ''} {c.content}"))
                if shared >= 2:
                    chunk = Chunk(
                        uuid4(), shown.filename, c.content, c.heading, c.page_start, c.page_offsets
                    )
                    scored.append((shared, chunk))
        scored.sort(key=lambda pair: -pair[0])
        return [chunk for _, chunk in scored[:limit]]

    def count_due(self, user_id: UUID, subject_id: UUID, now: datetime) -> int:
        return sum(
            1
            for c in self.list_concepts(user_id, subject_id)
            if c.stage >= 1 and mastery.is_due(c, now)
        )

    def count_new(self, user_id: UUID, subject_id: UUID) -> int:
        return sum(1 for c in self.list_concepts(user_id, subject_id) if c.stage == 0)

    def next_review_at(self, user_id: UUID, subject_id: UUID) -> datetime | None:
        times = [
            c.next_review_at
            for c in self.list_concepts(user_id, subject_id)
            if c.stage >= 1 and c.next_review_at is not None
        ]
        return min(times, default=None)

    def set_timezone(self, user_id: UUID, name: str) -> None:
        self.timezones[user_id] = name

    def resumable_session(
        self, user_id: UUID, subject_id: UUID, now: datetime
    ) -> ResumableSession | None:
        open_sessions = [
            (session_id, s)
            for session_id, s in self.sessions.items()
            if s["user_id"] == user_id
            and s["subject_id"] == subject_id
            and s["ended_at"] is None
            and s["last_active_at"] > now - RESUME_WINDOW
        ]
        if not open_sessions:
            return None
        session_id, s = max(open_sessions, key=lambda item: item[1]["last_active_at"])
        return ResumableSession(session_id, s["thread_id"], s["last_active_at"])

    def close_stale_sessions(self, user_id: UUID, now: datetime) -> int:
        closed = 0
        for s in self.sessions.values():
            if (
                s["user_id"] == user_id
                and s["ended_at"] is None
                and s["last_active_at"] <= now - RESUME_WINDOW
            ):
                s["ended_at"] = s["last_active_at"]
                closed += 1
        return closed

    def threads_to_prune(self, user_id: UUID, now: datetime) -> list[str]:
        return [
            s["thread_id"]
            for s in self.sessions.values()
            if s["user_id"] == user_id
            and s["ended_at"] is not None
            and s["ended_at"] <= now - PRUNE_AFTER
            and s["pruned_at"] is None
        ]

    def mark_pruned(self, user_id: UUID, thread_ids: Sequence[str], now: datetime) -> None:
        for s in self.sessions.values():
            if s["user_id"] == user_id and s["thread_id"] in thread_ids:
                s["pruned_at"] = now

    @contextmanager
    def session_lock(self, user_id: UUID, thread_id: str) -> Iterator[bool]:
        if thread_id in self.locks:
            yield False
            return
        self.locks.add(thread_id)
        try:
            yield True
        finally:
            self.locks.discard(thread_id)

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
        extensions: list[list[str]] | None = None,
        fail_extend: bool = False,
    ) -> None:
        self.batch_size = batch_size
        self.fail_grade_times = fail_grade_times
        self.fail_generate = fail_generate
        self.fail_teach = fail_teach
        self.fell_back = fell_back
        self.fail_generate_on_call = fail_generate_on_call  # 1-based call number that fails
        self.extensions = extensions  # concept names returned per extend call, in order
        self.fail_extend = fail_extend
        self.extend_calls: list[tuple[str, list[str]]] = []
        self.generate_attempts = 0
        self.generate_calls: list[str] = []
        self.avoid_calls: list[list[str]] = []
        self.counter = 0

    def _out[T](self, value: T) -> Generated[T]:
        return Generated(value, "fake-model", self.fell_back)

    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]:
        drafts = [
            ConceptDraft(name=f"{topic} {n}", description=f"Idea {n} of {topic}.")
            for n in range(1, 7)
        ]
        return self._out(drafts)

    def extend_concepts(
        self, subject: str, existing: Sequence[Concept]
    ) -> Generated[list[ConceptDraft]]:
        self.extend_calls.append((subject, [c.name for c in existing]))
        if self.fail_extend:
            raise AllProvidersExhausted("extension down")
        call = len(self.extend_calls) - 1
        scripted = self.extensions or []
        names = scripted[call] if call < len(scripted) else []
        return self._out([ConceptDraft(name=n, description=f"About {n}.") for n in names])

    def generate_questions(self, concept: Concept, avoid: list[str]) -> Generated[list[Question]]:
        self.generate_attempts += 1
        self.avoid_calls.append(list(avoid))
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
