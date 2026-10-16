"""Plain domain types shared by every layer. No I/O, no third-party imports."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class GradeValue(StrEnum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"


@dataclass(frozen=True)
class Subject:
    id: UUID
    name: str
    created_at: datetime


@dataclass(frozen=True)
class Concept:
    id: UUID
    subject_id: UUID
    name: str
    description: str
    stage: int = 0
    next_review_at: datetime | None = None
    last_reviewed_at: datetime | None = None
    review_count: int = 0


@dataclass(frozen=True)
class Option:
    text: str
    is_correct: bool
    why: str


@dataclass(frozen=True)
class Question:
    id: UUID
    concept_id: UUID
    type: str  # "mcq" or "short"
    difficulty: int  # 1 recall, 2 apply, 3 analyse
    prompt: str
    reference_answer: str
    options: tuple[Option, ...] = ()


@dataclass(frozen=True)
class Grade:
    value: GradeValue
    explanation: str
    misconception: str | None = None


@dataclass(frozen=True)
class Answer:
    id: UUID
    question_id: UUID
    concept_id: UUID
    answer_text: str
    grade: GradeValue
    feedback: str
    model_used: str
    graded_at: datetime


@dataclass(frozen=True)
class ResumableSession:
    session_id: UUID
    thread_id: str
    last_active_at: datetime
