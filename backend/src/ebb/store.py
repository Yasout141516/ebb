"""The persistence interface the graph and CLI depend on. Every method is scoped by user_id."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ebb.domain import Answer, Concept, Question, Subject


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

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept | None:
        """Grade `answer` against the concept's current row, under a row lock.

        Returns the concept after the grade, or None if answer.id was already recorded.
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
