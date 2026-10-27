"""Plain domain types shared by every layer. No I/O, no third-party imports."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
    # `name` is the file name (chunk) or the domain (web), `heading` the chunk's heading and
    # `title` the web page's title; `label` joins them for display.
    name: str | None = None
    heading: str | None = None
    title: str | None = None


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
    site: str | None = None  # a web source's domain
    page_title: str | None = None  # a web source's page title


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
class ConceptResult:
    """One concept's part of a session summary: its counted answers by grade, and where its
    stage and tide stood before the session's first of them and stand now."""

    concept_id: UUID
    concept: str
    correct: int
    partial: int
    incorrect: int
    stage_before: int
    stage_after: int
    tide_before: float | None
    tide_after: float | None
    next_review_at: datetime | None


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


@dataclass(frozen=True)
class LlmCall:
    """One attempt the LLM gateway made. A request that retries or falls back writes several,
    sharing a call_id."""

    id: UUID
    call_id: UUID
    request_id: UUID | None
    user_id: UUID
    at: datetime
    role: str  # teach | quiz | eval_writer
    endpoint: str
    provider: str
    model: str
    attempt: int
    outcome: str  # ok | invalid | error | refused
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    ttft_ms: int | None
    total_ms: int
    cost_usd: Decimal | None
    error_kind: str | None
    backup: bool = False  # served by the role's backup model
