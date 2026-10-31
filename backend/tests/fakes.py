"""Test doubles: an in-memory Store and a scripted Tutor. No network, no database."""

import random
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ebb import mastery
from ebb.citations import MAX_QUOTE
from ebb.domain import (
    Answer,
    Chunk,
    ChunkDraft,
    Claim,
    Concept,
    ConceptResult,
    Dispute,
    DisputeOutcome,
    DisputeReason,
    Document,
    Exchange,
    Grade,
    GradeValue,
    LearnText,
    Lesson,
    LlmCall,
    NewDocument,
    Option,
    Question,
    Regrade,
    ResumableSession,
    Source,
    StoredLesson,
    Subject,
)
from ebb.llm.schemas import ConceptDraft, EvalCaseDraft, NoteConcept
from ebb.llm.tutor import AllProvidersExhausted, Generated, TutorRefused
from ebb.progress import AnswerPoint
from ebb.store import (
    MAX_DOCUMENTS,
    PRUNE_AFTER,
    RESUME_WINDOW,
    DocumentRefused,
    SubjectExists,
    concept_after_dispute,
    concept_result,
    new_concepts,
    restore,
    search_words,
    snapshot,
)
from ebb.web import WebPage, WebSearchFailed

# (type, difficulty) of each question in a fake batch, in order.
BATCH_PATTERN = [("short", 1), ("mcq", 1), ("mcq", 2), ("short", 3), ("mcq", 3)]


def _words(text: str) -> set[str]:
    return set(search_words(text))


def _to_claims(pairs: Sequence[tuple[str, str]]) -> tuple[Claim, ...]:
    return tuple(Claim(source, quote) for source, quote in pairs)


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
        self.snapshots: dict[UUID, dict] = {}  # answer id -> concept_before
        self.voided: dict[UUID, datetime] = {}  # answer id -> voided at
        self.disputes: dict[UUID, tuple[UUID, Dispute]] = {}  # dispute id -> (user id, dispute)
        self.question_sessions: dict[UUID, UUID] = {}  # question id -> session id
        self.timezones: dict[UUID, str] = {}
        self.ai_calls: dict[tuple[UUID, date], int] = {}
        self.llm_call_log: list[LlmCall] = []
        self.lessons: dict[UUID, tuple[UUID, StoredLesson]] = {}  # concept id -> (user id, lesson)
        self.lesson_exchanges: dict[UUID, tuple[UUID, dict[int, Exchange]]] = {}
        self.locks: set[str] = set()

    def ping(self) -> None:
        pass

    def ensure_profile(self, user_id: UUID) -> None:
        self.profiles.add(user_id)

    def create_subject(self, user_id: UUID, name: str, concepts: Sequence[tuple[str, str]]) -> UUID:
        if any(
            owner == user_id and s.name.lower() == name.lower()
            for owner, s in self.subjects.values()
        ):
            raise SubjectExists(name)
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
            "driver": None,
            "driver_seen_at": None,
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
            if question.id in self.questions:
                continue  # already saved: a replay is a no-op
            self.questions[question.id] = replace(question, claims=())  # claims aren't stored
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
        self.snapshots[answer.id] = snapshot(concept)
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

    def resolve_dispute(
        self, user_id: UUID, dispute: Dispute, now: datetime
    ) -> tuple[Dispute, Concept]:
        answer = self.attempts.get(dispute.attempt_id)
        owner, current = self.concepts[answer.concept_id] if answer else (None, None)
        if owner != user_id:
            raise LookupError(f"attempt {dispute.attempt_id} not found")
        stored_owner, stored = self.disputes.get(dispute.id, (None, None))
        if stored_owner == user_id:
            if stored.attempt_id != dispute.attempt_id:
                raise ValueError(f"dispute {dispute.id} is about another attempt")
            return stored, current
        counted = [
            a
            for a in self.attempts.values()
            if a.concept_id == answer.concept_id and a.id not in self.voided
        ]
        latest = max(counted, key=lambda a: (a.graded_at, a.id), default=None)
        if (
            any(d.attempt_id == answer.id for _, d in self.disputes.values())
            or answer.id in self.voided
            or answer.id not in self.snapshots
            or latest is not answer
        ):
            raise ValueError(f"attempt {answer.id} can't be disputed")
        before = restore(current, self.snapshots[answer.id])
        after = concept_after_dispute(current, before, dispute, answer.grade, answer.graded_at)
        if dispute.outcome is DisputeOutcome.VOIDED:
            self.voided[answer.id] = now
        elif dispute.outcome is DisputeOutcome.CHANGED:
            self.attempts[answer.id] = replace(answer, grade=dispute.new_grade)
            self.stages[answer.id] = (self.stages[answer.id][0], after.stage)
        self.concepts[current.id] = (owner, after)
        self.disputes[dispute.id] = (user_id, dispute)
        return dispute, after

    def session_summary(
        self, user_id: UUID, session_id: UUID, now: datetime
    ) -> list[ConceptResult]:
        counted: dict[UUID, list[Answer]] = {}
        for answer in self.attempts.values():
            if (
                self.question_sessions[answer.question_id] == session_id
                and self.sessions[session_id]["user_id"] == user_id
                and self.concepts[answer.concept_id][0] == user_id
                and answer.id not in self.voided
            ):
                counted.setdefault(answer.concept_id, []).append(answer)
        position = {concept_id: n for n, concept_id in enumerate(self.concepts)}
        by_first = sorted(
            (
                # attempts are in the order they were recorded, which settles equal times
                (min(answers, key=lambda a: a.graded_at), answers)
                for answers in counted.values()
            ),
            key=lambda pair: (pair[0].graded_at, position[pair[0].concept_id]),
        )
        return [
            concept_result(
                self.concepts[first.concept_id][1],
                {grade: [a.grade for a in answers].count(grade) for grade in GradeValue},
                (self.stages[first.id][0], self.snapshots.get(first.id), first.graded_at),
                now,
            )
            for first, answers in by_first
        ]

    def get_dispute(self, user_id: UUID, dispute_id: UUID) -> Dispute | None:
        owner, dispute = self.disputes.get(dispute_id, (None, None))
        return dispute if owner == user_id else None

    def dispute_counts(self, user_id: UUID) -> dict[str, int]:
        outcomes = [d.outcome for owner, d in self.disputes.values() if owner == user_id]
        counts = {"raised": len(outcomes)}
        return counts | {o.value: outcomes.count(o) for o in DisputeOutcome}

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

    def count_documents(self, user_id: UUID, subject_id: UUID) -> int:
        return len(self.list_documents(user_id, subject_id))

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

    def next_review_at(self, user_id: UUID, subject_id: UUID) -> datetime | None:
        times = [
            c.next_review_at
            for c in self.list_concepts(user_id, subject_id)
            if c.stage >= 1 and c.next_review_at is not None
        ]
        return min(times, default=None)

    def subject_answers(self, user_id: UUID, subject_id: UUID) -> list[AnswerPoint]:
        answers = [
            a
            for a in self.attempts.values()
            if a.id not in self.voided
            and self.concepts[a.concept_id][0] == user_id
            and self.concepts[a.concept_id][1].subject_id == subject_id
        ]
        answers.sort(key=lambda a: (a.graded_at, a.id))
        return [AnswerPoint(a.concept_id, a.graded_at, a.grade) for a in answers]

    def answer_times(self, user_id: UUID) -> list[datetime]:
        return sorted(
            a.graded_at
            for a in self.attempts.values()
            if a.id not in self.voided and self.concepts[a.concept_id][0] == user_id
        )

    def set_timezone(self, user_id: UUID, name: str) -> None:
        self.timezones[user_id] = name

    def take_ai_call(self, user_id: UUID, day: date, limit: int) -> bool:
        used = self.ai_calls.get((user_id, day), 0)
        if used >= limit:
            return False
        self.ai_calls[(user_id, day)] = used + 1
        return True

    def ai_calls_used(self, user_id: UUID, day: date) -> int:
        return self.ai_calls.get((user_id, day), 0)

    def record_llm_call(self, user_id: UUID, call: LlmCall) -> None:
        if call.user_id != user_id:
            raise ValueError("the call belongs to another user")
        self.llm_call_log.append(call)

    def llm_calls(
        self, user_id: UUID, since: datetime, until: datetime, all_tenants: bool = False
    ) -> list[LlmCall]:
        mine = [c for c in self.llm_call_log if all_tenants or c.user_id == user_id]
        return sorted((c for c in mine if since <= c.at < until), key=lambda c: (c.at, c.id))

    def get_lesson(self, user_id: UUID, concept_id: UUID) -> StoredLesson | None:
        owner, lesson = self.lessons.get(concept_id, (None, None))
        return lesson if owner == user_id else None

    def save_lesson(self, user_id: UUID, lesson: StoredLesson) -> None:
        self.lessons.setdefault(lesson.concept_id, (user_id, lesson))

    def mark_learned(self, user_id: UUID, concept_id: UUID, at: datetime) -> bool:
        lesson = self.get_lesson(user_id, concept_id)
        if lesson is None or lesson.learned_at is not None:
            return False
        self.lessons[concept_id] = (user_id, replace(lesson, learned_at=at))
        return True

    def is_learned(self, user_id: UUID, concept_id: UUID) -> bool:
        lesson = self.get_lesson(user_id, concept_id)
        return lesson is not None and lesson.learned_at is not None

    def add_exchange(self, user_id: UUID, concept_id: UUID, exchange: Exchange) -> None:
        _, kept = self.lesson_exchanges.setdefault(concept_id, (user_id, {}))
        kept.setdefault(exchange.position, exchange)

    def exchanges(self, user_id: UUID, concept_id: UUID) -> list[Exchange]:
        owner, kept = self.lesson_exchanges.get(concept_id, (None, {}))
        return [kept[p] for p in sorted(kept)] if owner == user_id else []

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
                and s["thread_id"] not in self.locks
            ):
                s["ended_at"] = s["last_active_at"]
                closed += 1
        return closed

    def _open_session(self, user_id: UUID, thread_id: str) -> tuple[UUID, dict] | None:
        for session_id, s in self.sessions.items():
            if s["user_id"] == user_id and s["thread_id"] == thread_id and s["ended_at"] is None:
                return session_id, s
        return None

    def get_session(self, user_id: UUID, thread_id: str) -> ResumableSession | None:
        found = self._open_session(user_id, thread_id)
        if found is None:
            return None
        session_id, s = found
        return ResumableSession(session_id, s["thread_id"], s["last_active_at"])

    def claim_session(
        self, user_id: UUID, thread_id: str, driver: str, now: datetime, lease: timedelta
    ) -> bool:
        found = self._open_session(user_id, thread_id)
        if found is None:
            return False
        s = found[1]
        if s["driver"] not in (None, driver) and s["driver_seen_at"] > now - lease:
            return False
        s.update(driver=driver, driver_seen_at=now, last_active_at=now)
        return True

    def session_driver(
        self, user_id: UUID, thread_id: str, now: datetime, lease: timedelta
    ) -> str | None:
        found = self._open_session(user_id, thread_id)
        seen = None if found is None else found[1]["driver_seen_at"]
        return found[1]["driver"] if seen is not None and seen > now - lease else None

    def release_session(self, user_id: UUID, thread_id: str, driver: str) -> None:
        found = self._open_session(user_id, thread_id)
        if found is not None and found[1]["driver"] == driver:
            found[1].update(driver=None, driver_seen_at=None)

    def touch_session(self, user_id: UUID, thread_id: str, now: datetime) -> None:
        found = self._open_session(user_id, thread_id)
        if found is not None:
            found[1]["last_active_at"] = now

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
    Multiple choice: option 1 is the correct one, unless an `rng` shuffles the options."""

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
        cite: list[list[tuple[str, str]]] | None = None,
        cite_each: list[list[list[tuple[str, str]]]] | None = None,
        grade_cites: list[list[tuple[str, str]]] | None = None,
        teach_cites: list[list[tuple[str, str]]] | None = None,
        web_prompts: list[str] | None = None,
        prompts: list[str] | None = None,
        note_concepts: list[list[tuple[str, str | None]]] | None = None,
        regrades: list[tuple[str, bool]] | None = None,
        fail_regrade: bool = False,
        fail_concepts: bool = False,
        fail_eval_on_calls: Sequence[int] = (),
        learn_cites: list[list[tuple[str, str]]] | None = None,
        fail_learn_times: int = 0,
        break_learn_times: int = 0,
        refuse: set[str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.rng = rng  # when given, shuffles each question's options, as the real tutor does
        self.refuse = refuse or set()  # names of the methods that raise TutorRefused
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
        self.cite = cite  # claims per generate call, applied to every question in that batch
        self.cite_each = cite_each  # claims per generate call, per question; overrides cite
        self.grade_cites = grade_cites  # claims per grade call
        self.teach_cites = teach_cites  # claims per teach call
        self.generated: list[list[Question]] = []  # what each generate call returned
        self.rewrite_calls: list[list[Question]] = []
        self.grade_previous: list[Grade | None] = []
        self.teach_previous: list[Lesson | None] = []
        self.regrade_previous: list[Grade | None] = []
        self.web_prompts = web_prompts  # prompts returned in web mode
        self.prompts = prompts  # prompts returned for normal batches
        self.sources_calls: list[list[Source]] = []
        self.failures_calls: list[list[str]] = []
        self.web_count_calls: list[int | None] = []
        self.grade_attempts = 0
        self.teach_attempts = 0
        self.note_concepts = note_concepts  # (name, same_as) per concepts_from_notes call
        self.regrades = regrades  # (grade, ambiguous) per regrade call
        self.fail_regrade = fail_regrade
        self.fail_concepts = fail_concepts
        self.concepts_calls: list[tuple[str, list[str], str]] = []
        self.regrade_calls: list[tuple[str, DisputeReason, str]] = []
        self.fail_eval_on_calls = fail_eval_on_calls  # 1-based write_eval_case call numbers
        self.eval_calls: list[tuple[str, GradeValue, str | None]] = []
        # Lessons and learn answers share these. By default each cites its first source.
        self.learn_cites = learn_cites  # claims per successful learn call, instead
        self.fail_learn_times = fail_learn_times  # calls that fail before any text
        self.break_learn_times = break_learn_times  # calls that fail after one piece of text
        self.learn_attempts = 0
        self.lesson_calls: list[dict[str, Any]] = []
        self.answer_calls: list[dict[str, Any]] = []

    @staticmethod
    def _claims(scripted: list[list[tuple[str, str]]] | None, call: int) -> tuple[Claim, ...]:
        return _to_claims(scripted[call] if scripted and call < len(scripted) else [])

    def _out[T](self, value: T) -> Generated[T]:
        return Generated(value, "fake-model", self.fell_back)

    def _maybe_refuse(self, method: str) -> None:
        if method in self.refuse:
            raise TutorRefused()

    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]:
        self._maybe_refuse("extract_concepts")
        drafts = [
            ConceptDraft(name=f"{topic} {n}", description=f"Idea {n} of {topic}.")
            for n in range(1, 7)
        ]
        return self._out(drafts)

    def extend_concepts(
        self, subject: str, existing: Sequence[Concept]
    ) -> Generated[list[ConceptDraft]]:
        self.extend_calls.append((subject, [c.name for c in existing]))
        self._maybe_refuse("extend_concepts")
        if self.fail_extend:
            raise AllProvidersExhausted("extension down")
        call = len(self.extend_calls) - 1
        scripted = self.extensions or []
        names = scripted[call] if call < len(scripted) else []
        return self._out([ConceptDraft(name=n, description=f"About {n}.") for n in names])

    def generate_questions(
        self,
        concept: Concept,
        avoid: list[str],
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        web_count: int | None = None,
        rewrite: Sequence[Question] = (),
    ) -> Generated[list[Question]]:
        """A rewrite keeps each question's type, difficulty and prompt: only its claims change."""
        self.generate_attempts += 1
        self.avoid_calls.append(list(avoid))
        self.sources_calls.append(list(sources))
        self.failures_calls.append(list(failures))
        self.web_count_calls.append(web_count)
        self.rewrite_calls.append(list(rewrite))
        self._maybe_refuse("generate_questions")
        if self.fail_generate or self.generate_attempts == self.fail_generate_on_call:
            raise AllProvidersExhausted("generation down")
        self.generate_calls.append(concept.name)
        call = self.generate_attempts - 1
        claims = self._claims(self.cite, call)
        if rewrite:
            pattern = [(q.type, q.difficulty) for q in rewrite]
        elif web_count is None:
            pattern = BATCH_PATTERN[: self.batch_size]
        else:
            pattern = [BATCH_PATTERN[n % len(BATCH_PATTERN)] for n in range(web_count)]
        scripted = self.prompts if web_count is None else self.web_prompts
        if rewrite:
            scripted = [q.prompt for q in rewrite]
        questions = []
        for index, (kind, difficulty) in enumerate(pattern):
            if self.cite_each is not None:
                each = self.cite_each[call] if call < len(self.cite_each) else []
                claims = _to_claims(each[index] if index < len(each) else [])
            self.counter += 1
            options: tuple[Option, ...] = ()
            if kind == "mcq":
                options = (
                    Option("right option", True, "This is the definition."),
                    Option("wrong option A", False, "That confuses cause and effect."),
                    Option("wrong option B", False, "That is a different layer."),
                    Option("wrong option C", False, "That is the opposite."),
                )
                if self.rng is not None:
                    shuffled = list(options)
                    self.rng.shuffle(shuffled)
                    options = tuple(shuffled)
            if scripted is not None and index < len(scripted):
                prompt = scripted[index]
            else:
                # Two words of its own keep each prompt from looking like a repeat of another.
                n = self.counter
                prompt = f"{concept.name} question {n} (part {n}a or {n}b)"
            questions.append(
                Question(
                    id=uuid4(),
                    concept_id=concept.id,
                    type=kind,
                    difficulty=difficulty,
                    prompt=prompt,
                    reference_answer="right",
                    options=options,
                    claims=claims,
                )
            )
        self.generated.append(questions)
        return self._out(questions)

    def grade(
        self,
        question: Question,
        answer: str,
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        previous: Grade | None = None,
    ) -> Generated[Grade]:
        self.grade_previous.append(previous)
        self._maybe_refuse("grade")
        if self.fail_grade_times > 0:
            self.fail_grade_times -= 1
            raise AllProvidersExhausted("grading down")
        claims = self._claims(self.grade_cites, self.grade_attempts)
        self.grade_attempts += 1
        value = {"right": GradeValue.CORRECT, "meh": GradeValue.PARTIAL}.get(
            answer, GradeValue.INCORRECT
        )
        return self._out(Grade(value, f"Graded {value.value}.", None, claims=claims))

    def teach(
        self,
        question: Question,
        answer: str,
        grade: Grade,
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        previous: Lesson | None = None,
    ) -> Generated[Lesson]:
        self.teach_previous.append(previous)
        self._maybe_refuse("teach")
        if self.fail_teach:
            raise AllProvidersExhausted("teaching down")
        claims = self._claims(self.teach_cites, self.teach_attempts)
        self.teach_attempts += 1
        return self._out(Lesson("Here is the idea, with an example.", claims))

    def write_lesson(
        self,
        concept: Concept,
        subject: str,
        sources: Sequence[Source],
        on_text: Callable[[str], None] | None = None,
        failures: Sequence[str] = (),
        previous: LearnText | None = None,
    ) -> Generated[LearnText]:
        """A titled lesson with one table, streamed in three pieces."""
        self.lesson_calls.append(
            {
                "concept": concept.name,
                "subject": subject,
                "sources": list(sources),
                "failures": list(failures),
                "previous": previous,
            }
        )
        name, about = concept.name, concept.description
        pieces = [
            f"# {name}\n\n",
            f"**In one sentence:** {about}\n\n",
            f"| Part | What it does |\n|---|---|\n| {name} | {about} |",
        ]
        checks = (
            f"Why does {name} matter?",
            f"Where would you see {name} at work?",
            f"What would break without {name}?",
        )
        return self._learn("write_lesson", pieces, checks, sources, on_text, failures, previous)

    def answer(
        self,
        concept: Concept,
        lesson: str,
        history: Sequence[tuple[str, str]],
        question: str,
        sources: Sequence[Source],
        on_text: Callable[[str], None] | None = None,
        failures: Sequence[str] = (),
        previous: LearnText | None = None,
    ) -> Generated[LearnText]:
        """A plain answer that repeats the question, streamed in three pieces."""
        self.answer_calls.append(
            {
                "concept": concept.name,
                "lesson": lesson,
                "history": list(history),
                "question": question,
                "sources": list(sources),
                "failures": list(failures),
                "previous": previous,
            }
        )
        pieces = [f"You asked: {question} ", f"Here is how {concept.name} ", "answers it."]
        return self._learn("answer", pieces, (), sources, on_text, failures, previous)

    def _learn(
        self,
        method: str,
        pieces: list[str],
        checks: tuple[str, ...],
        sources: Sequence[Source],
        on_text: Callable[[str], None] | None,
        failures: Sequence[str],
        previous: LearnText | None,
    ) -> Generated[LearnText]:
        """Streams `pieces` to `on_text`; a citation retry keeps `previous` and streams
        nothing. Fails as `fail_learn_times` and `break_learn_times` say."""
        if failures and previous is None:
            raise ValueError("a citation retry needs the previous text")  # as the real tutor
        self._maybe_refuse(method)
        if self.fail_learn_times > 0:
            self.fail_learn_times -= 1
            raise AllProvidersExhausted("learning down")
        if not failures:
            for n, piece in enumerate(pieces):
                if n == 1 and self.break_learn_times > 0:
                    self.break_learn_times -= 1
                    raise AllProvidersExhausted("the stream broke")
                if on_text is not None:
                    on_text(piece)
        if self.learn_cites is not None:
            claims = self._claims(self.learn_cites, self.learn_attempts)
        else:
            claims = (Claim(sources[0].label, sources[0].text[:MAX_QUOTE]),) if sources else ()
        self.learn_attempts += 1
        if previous is not None and failures:
            return self._out(replace(previous, claims=claims))
        suggestions = ("How is this used in practice?", "What is it often confused with?")
        return self._out(LearnText("".join(pieces), claims, suggestions, checks))

    def concepts_from_notes(
        self, subject: str, existing: Sequence[Concept], digest: str
    ) -> Generated[list[NoteConcept]]:
        self.concepts_calls.append((subject, [c.name for c in existing], digest))
        self._maybe_refuse("concepts_from_notes")
        if self.fail_concepts:
            raise AllProvidersExhausted("concepts down")
        call = len(self.concepts_calls) - 1
        scripted = self.note_concepts or []
        pairs = scripted[call] if call < len(scripted) else []
        return self._out(
            [NoteConcept(name=n, description=f"About {n}.", same_as=s) for n, s in pairs]
        )

    def regrade(
        self,
        question: Question,
        answer: str,
        grade: Grade,
        reason: DisputeReason,
        explanation: str,
        sources: Sequence[Source] = (),
        failures: Sequence[str] = (),
        previous: Grade | None = None,
    ) -> Generated[Regrade]:
        self.regrade_calls.append((answer, reason, explanation))
        self.regrade_previous.append(previous)
        self._maybe_refuse("regrade")
        if self.fail_regrade:
            raise AllProvidersExhausted("regrade down")
        call = len(self.regrade_calls) - 1
        scripted = self.regrades or []
        value, ambiguous = scripted[call] if call < len(scripted) else (grade.value.value, False)
        new = Grade(GradeValue(value), f"Regraded {value}.", grade.misconception)
        return self._out(Regrade(new, ambiguous))

    def write_eval_case(
        self, topic: str, target: GradeValue, notes: str | None
    ) -> Generated[EvalCaseDraft]:
        """An answer this tutor's own `grade` marks as the target: right, meh or wrong."""
        self.eval_calls.append((topic, target, notes))
        self._maybe_refuse("write_eval_case")
        if len(self.eval_calls) in self.fail_eval_on_calls:
            raise AllProvidersExhausted("writer down")
        answer = {"correct": "right", "partial": "meh", "incorrect": "wrong"}[target.value]
        draft = EvalCaseDraft(
            question=f"What is {topic}?",
            reference="right",
            answer=answer,
            reason=f"Written to be {target.value}.",
        )
        return self._out(draft)


class FakeWebSearch:
    """Returns the given pages for every query and records the queries."""

    def __init__(self, pages: list[WebPage] | None = None, fail: bool = False) -> None:
        self.pages = pages or []
        self.fail = fail
        self.queries: list[str] = []

    def search(self, query: str) -> list[WebPage]:
        self.queries.append(query)
        if self.fail:
            raise WebSearchFailed("search down")
        return list(self.pages)


def new_document(
    filename: str, *chunks: str | ChunkDraft, sha: str | None = None, page_count: int | None = None
) -> NewDocument:
    """A notes file of `chunks`, of the kind its name ends in. A plain text is chunk i,
    headed "Part i", on page 1. The sha256 is made from the name unless given."""
    drafts = tuple(
        c if isinstance(c, ChunkDraft) else ChunkDraft(i, c, f"Part {i}", 1, 1, (0,))
        for i, c in enumerate(chunks)
    )
    kind = filename.rpartition(".")[2]
    return NewDocument(filename, kind, 100, page_count, sha or f"sha-{filename}", drafts)


def make_subject(
    store: MemoryStore, user_id: UUID, concepts: int = 4, name: str = "Networks"
) -> UUID:
    store.ensure_profile(user_id)
    return store.create_subject(
        user_id,
        name,
        [(f"Concept {n}", f"About concept {n}.") for n in range(1, concepts + 1)],
    )


def seed_concept(store: MemoryStore, concept_id: UUID, **changes) -> None:
    owner, concept = store.concepts[concept_id]
    store.concepts[concept_id] = (owner, replace(concept, **changes))
