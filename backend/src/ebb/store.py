"""The persistence interface the graph and CLI depend on. Every method is scoped by user_id."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ebb.domain import Attempt, Concept, Question, Subject


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
    ) -> None: ...

    def record_attempt(self, user_id: UUID, attempt: Attempt, concept_after: Concept) -> bool:
        """Insert the attempt and apply `concept_after`; False if attempt.id already existed."""
        ...
