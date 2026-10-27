"""The persistence interface the graph and CLI depend on. Every method is scoped by user_id."""

import re
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from ebb import mastery
from ebb.domain import (
    Answer,
    Chunk,
    Concept,
    ConceptResult,
    Dispute,
    DisputeOutcome,
    Document,
    GradeValue,
    LlmCall,
    NewDocument,
    Question,
    ResumableSession,
    Subject,
)
from ebb.progress import AnswerPoint

RESUME_WINDOW = timedelta(hours=24)
PRUNE_AFTER = timedelta(days=7)
MAX_DOCUMENTS = 5


class DocumentRefused(Exception):
    """A document was not stored: the subject already has MAX_DOCUMENTS ("full") or this
    exact file ("duplicate")."""

    def __init__(self, reason: Literal["full", "duplicate"]) -> None:
        super().__init__(reason)
        self.reason = reason


class SubjectExists(Exception):
    """The user already has a subject of this name, compared ignoring case."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def new_concepts(
    existing_names: Sequence[str], concepts: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The concepts whose names match neither an existing name nor an earlier one in the
    batch, compared stripped and case-insensitively; shared by every Store's add_concepts."""
    seen = {name.strip().casefold() for name in existing_names}
    fresh = []
    for name, description in concepts:
        key = name.strip().casefold()
        if key not in seen:
            seen.add(key)
            fresh.append((name, description))
    return fresh


def merge_note_concepts(
    existing_names: Sequence[str], found: Sequence[tuple[str, str, str | None]]
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split concepts found in notes, given as (name, description, same_as), into the existing
    names they match and the fresh ones. A same_as naming an existing concept (stripped,
    case-insensitive) counts as a match; one naming nothing is dropped."""
    existing = {name.strip().casefold(): name for name in existing_names}
    matched: list[str] = []
    unlinked: list[tuple[str, str]] = []
    for name, description, same_as in found:
        if same_as is None:
            unlinked.append((name, description))
        elif (hit := existing.get(same_as.strip().casefold())) is not None:
            if hit not in matched:
                matched.append(hit)
    return matched, new_concepts(existing_names, unlinked)


def search_words(text: str) -> list[str]:
    """Distinct lowercase words of letters and digits, three characters or longer: what a
    chunk search looks for, shared by every Store's search_chunks."""
    words = (word.lower() for word in re.findall(r"[^\W_]+", text))
    return list(dict.fromkeys(word for word in words if len(word) >= 3))


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _from_iso(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def snapshot(concept: Concept) -> dict[str, Any]:
    """The concept's review state as JSON, stored with each attempt as concept_before."""
    return {
        "stage": concept.stage,
        "next_review_at": _iso(concept.next_review_at),
        "last_reviewed_at": _iso(concept.last_reviewed_at),
        "review_count": concept.review_count,
    }


def restore(concept: Concept, before: Mapping[str, Any]) -> Concept:
    """`concept` with the review state of a `snapshot`."""
    return replace(
        concept,
        stage=before["stage"],
        next_review_at=_from_iso(before["next_review_at"]),
        last_reviewed_at=_from_iso(before["last_reviewed_at"]),
        review_count=before["review_count"],
    )


def concept_result(
    current: Concept,
    counts: Mapping[GradeValue, int],
    first: tuple[int, Mapping[str, Any] | None, datetime],
    now: datetime,
) -> ConceptResult:
    """A session summary row for `current`. `first` is the session's first counted answer on
    it: its stage_before column, its concept_before snapshot (if any) and its graded_at."""
    stage, before, graded_at = first
    if before is None:
        stage_before, tide_before = stage, None
    else:
        earlier = restore(current, before)
        stage_before, tide_before = earlier.stage, mastery.tide(earlier, graded_at)
    return ConceptResult(
        concept_id=current.id,
        concept=current.name,
        correct=counts.get(GradeValue.CORRECT, 0),
        partial=counts.get(GradeValue.PARTIAL, 0),
        incorrect=counts.get(GradeValue.INCORRECT, 0),
        stage_before=stage_before,
        stage_after=current.stage,
        tide_before=tide_before,
        tide_after=mastery.tide(current, now),
        next_review_at=current.next_review_at,
    )


def concept_after_dispute(
    current: Concept, before: Concept, dispute: Dispute, graded: GradeValue, graded_at: datetime
) -> Concept:
    """The concept once `dispute` is applied to its latest answer, which was graded `graded` at
    `graded_at` from the state `before`: restored if voided, regraded from `before` if changed,
    `current` if upheld. Raises ValueError if the dispute doesn't fit the answer."""
    if dispute.original_grade != graded:
        raise ValueError(f"attempt {dispute.attempt_id} was graded {graded}")
    if dispute.outcome is DisputeOutcome.VOIDED:
        return before
    if dispute.outcome is DisputeOutcome.CHANGED:
        if dispute.new_grade is None:
            raise ValueError("a changed grade needs the new grade")
        return mastery.apply_grade(before, dispute.new_grade, graded_at)
    return current


class Store(Protocol):
    def ping(self) -> None:
        """Raises psycopg.Error when the database can't be reached."""
        ...

    def ensure_profile(self, user_id: UUID) -> None: ...

    def create_subject(self, user_id: UUID, name: str, concepts: Sequence[tuple[str, str]]) -> UUID:
        """Raises SubjectExists if the user has a subject of that name, ignoring case."""
        ...

    def list_subjects(self, user_id: UUID) -> list[Subject]: ...

    def list_concepts(self, user_id: UUID, subject_id: UUID) -> list[Concept]: ...

    def create_session(self, user_id: UUID, subject_id: UUID, thread_id: str) -> UUID: ...

    def end_session(self, user_id: UUID, session_id: UUID, now: datetime) -> None: ...

    def save_questions(
        self, user_id: UUID, session_id: UUID, questions: Sequence[Question]
    ) -> None:
        """Stores each question with its source and citations (not its unverified claims).

        A question whose id is already stored is left as it is, so a replayed save is a
        no-op. Raises LookupError if the session or a question's concept is not the user's."""
        ...

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept:
        """Grade `answer` against the concept's current row, under a row lock.

        Stores the answer's citations and a `snapshot` of the row before the grade. Returns
        the concept after the grade. If answer.id was already recorded, nothing is written
        and the stored row (which already holds that grade) is returned.
        Raises LookupError if the referenced question/concept is not the user's.
        """
        ...

    def answered_prompts(self, user_id: UUID, concept_id: UUID, limit: int = 40) -> list[str]:
        """Distinct prompts of this concept's answered questions, newest attempt first.
        Voided answers count, so a voided question is never asked again."""
        ...

    def resolve_dispute(
        self, user_id: UUID, dispute: Dispute, now: datetime
    ) -> tuple[Dispute, Concept]:
        """Apply `dispute` to its attempt and record it, in one transaction under the
        concept's row lock; returns the dispute and the concept after it.

        Voided restores the attempt's snapshot and marks the attempt voided at `now`; changed
        regrades from the snapshot at the original graded_at and updates the attempt's grade
        and stage_after; upheld changes nothing. If dispute.id was already recorded, the
        stored dispute and the current concept are returned and nothing is written.
        Raises LookupError if the attempt is not the user's, and ValueError if it is already
        disputed, voided, has no snapshot, is not the concept's latest counted attempt, or
        doesn't match the dispute's original grade.
        """
        ...

    def session_summary(
        self, user_id: UUID, session_id: UUID, now: datetime
    ) -> list[ConceptResult]:
        """One row per concept with an answer in this session that wasn't voided, ordered by
        its first such answer: those answers counted by grade; stage and tide before from
        the first one's concept_before snapshot (tide measured at its graded_at; an answer
        without a snapshot gives its stage_before and no tide); stage, tide at `now` and
        next review from the concept as it is."""
        ...

    def get_dispute(self, user_id: UUID, dispute_id: UUID) -> Dispute | None:
        """The recorded dispute with this id, or None if it isn't the user's or doesn't exist."""
        ...

    def dispute_counts(self, user_id: UUID) -> dict[str, int]:
        """How many disputes were raised, and how many were changed, voided and upheld."""
        ...

    def get_subject(self, user_id: UUID, subject_id: UUID) -> Subject | None: ...

    def add_concepts(
        self, user_id: UUID, subject_id: UUID, concepts: Sequence[tuple[str, str]]
    ) -> int:
        """Append concepts whose names are new (case-insensitive); returns how many were added.

        Raises LookupError if the subject is not the user's.
        """
        ...

    def next_review_at(self, user_id: UUID, subject_id: UUID) -> datetime | None: ...

    def set_timezone(self, user_id: UUID, name: str) -> None: ...

    def subject_answers(self, user_id: UUID, subject_id: UUID) -> list[AnswerPoint]:
        """Answers not voided, by graded_at then id; a grade a dispute changed shows as changed."""
        ...

    def answer_times(self, user_id: UUID) -> list[datetime]:
        """When each answer not voided was graded, in every subject."""
        ...

    def take_ai_call(self, user_id: UUID, day: date, limit: int) -> bool:
        """Count one AI call for `day` and return True while fewer than `limit` have been made;
        otherwise return False and change nothing."""
        ...

    def record_llm_call(self, user_id: UUID, call: LlmCall) -> None:
        """Keep one gateway attempt."""
        ...

    def llm_calls(
        self, user_id: UUID, since: datetime, until: datetime, all_tenants: bool = False
    ) -> list[LlmCall]:
        """The user's attempts with `since <= at < until`, oldest first. `all_tenants` drops the
        user filter; only the dev-only costs page asks for that."""
        ...

    def resumable_session(
        self, user_id: UUID, subject_id: UUID, now: datetime
    ) -> ResumableSession | None:
        """The most recently active open session of this subject within RESUME_WINDOW."""
        ...

    def close_stale_sessions(self, user_id: UUID, now: datetime) -> int:
        """End open sessions idle for RESUME_WINDOW, at their last activity; returns the count.

        A session whose lock is held is skipped: someone is still on it. One whose lease is
        live never qualifies, since claiming a lease also marks the session active."""
        ...

    def get_session(self, user_id: UUID, thread_id: str) -> ResumableSession | None:
        """The user's open session on this thread, or None."""
        ...

    def claim_session(
        self, user_id: UUID, thread_id: str, driver: str, now: datetime, lease: timedelta
    ) -> bool:
        """Make `driver` the session's driver as of `now`, and mark the session active, if it
        is open and its lease is free, expired (seen `lease` or longer ago) or already this
        driver's; returns whether it did."""
        ...

    def session_driver(
        self, user_id: UUID, thread_id: str, now: datetime, lease: timedelta
    ) -> str | None:
        """The driver of the open session whose lease is live at `now`, or None."""
        ...

    def release_session(self, user_id: UUID, thread_id: str, driver: str) -> None:
        """Free the open session's lease if `driver` holds it (a tab closing)."""
        ...

    def touch_session(self, user_id: UUID, thread_id: str, now: datetime) -> None:
        """Mark the open session active at `now`."""
        ...

    def threads_to_prune(self, user_id: UUID, now: datetime) -> list[str]:
        """Thread ids of sessions ended at least PRUNE_AFTER ago whose checkpoints remain."""
        ...

    def mark_pruned(self, user_id: UUID, thread_ids: Sequence[str], now: datetime) -> None: ...

    def session_lock(self, user_id: UUID, thread_id: str) -> AbstractContextManager[bool]:
        """Yields True if this process now holds the thread's lock, False if another does."""
        ...

    def create_subject_with_notes(
        self,
        user_id: UUID,
        name: str,
        documents: Sequence[NewDocument],
        concepts: Sequence[tuple[str, str]],
    ) -> UUID:
        """Create the subject, its documents with their chunks, and its concepts in one
        transaction. Raises SubjectExists if the user has a subject of that name."""
        ...

    def add_document(
        self,
        user_id: UUID,
        subject_id: UUID,
        document: NewDocument,
        concepts: Sequence[tuple[str, str]],
    ) -> int:
        """Store the document, its chunks and the new concepts in one transaction; returns how
        many concepts were added. Raises DocumentRefused at MAX_DOCUMENTS or for a file already
        stored, and LookupError if the subject is not the user's."""
        ...

    def list_documents(self, user_id: UUID, subject_id: UUID) -> list[Document]:
        """The subject's documents, oldest first."""
        ...

    def has_document(self, user_id: UUID, subject_id: UUID, sha256: str) -> bool: ...

    def count_documents(self, user_id: UUID, subject_id: UUID) -> int: ...

    def search_chunks(
        self, user_id: UUID, subject_id: UUID, text: str, limit: int = 4
    ) -> list[Chunk]:
        """The subject's note chunks that best match `text`, best first; empty if none do."""
        ...
