"""The persistence interface the graph and CLI depend on. Every method is scoped by user_id."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from ebb import mastery
from ebb.domain import (
    Answer,
    Chunk,
    Concept,
    Dispute,
    DisputeOutcome,
    Document,
    GradeValue,
    NewDocument,
    Question,
    ResumableSession,
    Subject,
)

RESUME_WINDOW = timedelta(hours=24)
PRUNE_AFTER = timedelta(days=7)
MAX_DOCUMENTS = 5


class DocumentRefused(Exception):
    """A document was not stored: the subject already has MAX_DOCUMENTS ("full") or this
    exact file ("duplicate")."""

    def __init__(self, reason: Literal["full", "duplicate"]) -> None:
        super().__init__(reason)
        self.reason = reason


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
    def ensure_profile(self, user_id: UUID) -> None: ...

    def create_subject(
        self, user_id: UUID, name: str, concepts: Sequence[tuple[str, str]]
    ) -> UUID: ...

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

    def count_due(self, user_id: UUID, subject_id: UUID, now: datetime) -> int: ...

    def count_new(self, user_id: UUID, subject_id: UUID) -> int: ...

    def next_review_at(self, user_id: UUID, subject_id: UUID) -> datetime | None: ...

    def set_timezone(self, user_id: UUID, name: str) -> None: ...

    def resumable_session(
        self, user_id: UUID, subject_id: UUID, now: datetime
    ) -> ResumableSession | None:
        """The most recently active open session of this subject within RESUME_WINDOW."""
        ...

    def close_stale_sessions(self, user_id: UUID, now: datetime) -> int:
        """End open sessions idle for RESUME_WINDOW, at their last activity; returns the count."""
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
        transaction. Name clashes are the caller's to check."""
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
