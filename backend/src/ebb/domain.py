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
class Claim:
    """An unverified citation from the model: a source label and a quote."""

    source: str
    quote: str


@dataclass(frozen=True)
class Citation:
    """A verified citation. `kind` is "chunk" or "web"; `ref` is the chunk id or the URL."""

    kind: str
    ref: str
    label: str
    page: int | None
    quote: str


@dataclass(frozen=True)
class Source:
    """One piece of source text shown to the model, under a label like [S1]."""

    label: str
    kind: str
    ref: str
    title: str  # the file name, or "domain · page title"
    text: str
    heading: str | None = None
    page_start: int | None = None
    page_offsets: tuple[int, ...] = ()  # offset in text where page page_start + i begins


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
    source: str = "general"  # "notes", "web" or "general"
    claims: tuple[Claim, ...] = ()
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class Lesson:
    text: str
    claims: tuple[Claim, ...] = ()
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class Grade:
    value: GradeValue
    explanation: str
    misconception: str | None = None
    claims: tuple[Claim, ...] = ()
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class Regrade:
    grade: Grade
    ambiguous: bool


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
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class ResumableSession:
    session_id: UUID
    thread_id: str
    last_active_at: datetime


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    content: str
    heading: str | None
    page_start: int | None
    page_end: int | None
    page_offsets: tuple[int, ...]


@dataclass(frozen=True)
class NewDocument:
    filename: str
    kind: str  # "pdf", "txt" or "md"
    size_bytes: int
    page_count: int | None
    sha256: str
    chunks: tuple[ChunkDraft, ...]


@dataclass(frozen=True)
class Document:
    id: UUID
    filename: str
    kind: str
    size_bytes: int
    page_count: int | None
    chunk_count: int
    created_at: datetime


@dataclass(frozen=True)
class Chunk:
    id: UUID
    filename: str
    content: str
    heading: str | None
    page_start: int | None
    page_offsets: tuple[int, ...]


class DisputeReason(StrEnum):
    CORRECT = "correct"
    AMBIGUOUS = "ambiguous"
    NOTES_DIFFER = "notes_differ"
    OTHER = "other"


class DisputeOutcome(StrEnum):
    UPHELD = "upheld"
    CHANGED = "changed"
    VOIDED = "voided"


@dataclass(frozen=True)
class Dispute:
    id: UUID
    attempt_id: UUID
    reason: DisputeReason
    explanation: str
    original_grade: GradeValue
    new_grade: GradeValue | None  # None when voided
    outcome: DisputeOutcome
    regrade_explanation: str
    citations: tuple[Citation, ...]
    model_used: str
