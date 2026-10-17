"""The persistence interface the graph and CLI depend on. Every method is scoped by user_id."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from ebb.domain import Answer, Concept, Question, ResumableSession, Subject

RESUME_WINDOW = timedelta(hours=24)
PRUNE_AFTER = timedelta(days=7)


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
        """Raises LookupError if the session or a question's concept is not the user's."""
        ...

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept:
        """Grade `answer` against the concept's current row, under a row lock.

        Returns the concept after the grade. If answer.id was already recorded, nothing is
        written and the stored row (which already holds that grade) is returned.
        Raises LookupError if the referenced question/concept is not the user's.
        """
        ...

    def answered_prompts(self, user_id: UUID, concept_id: UUID, limit: int = 40) -> list[str]:
        """Distinct prompts of this concept's answered questions, newest attempt first."""
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
