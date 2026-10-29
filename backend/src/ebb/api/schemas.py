"""Request and response bodies. Each route adds its own here."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from ebb.domain import DisputeReason


class Health(BaseModel):
    database: str
    tutor: str
    timezone: str
    costs: bool  # the dev Costs page is on (EBB_SHOW_COSTS)


class SubjectSummary(BaseModel):
    id: UUID
    name: str
    concept_count: int
    due_count: int


def _not_blank(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("must not be blank")
    return name


# A subject's name, stripped: the stores don't strip.
type SubjectName = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_not_blank)]


class NewSubject(BaseModel):
    topic: SubjectName


class ConceptBrief(BaseModel):
    name: str
    description: str


class CreatedSubject(BaseModel):
    id: UUID
    name: str
    concepts: list[ConceptBrief]


class NewDraft(BaseModel):
    name: SubjectName


class CreatedDraft(BaseModel):
    id: str


class CommittedDraft(BaseModel):
    subject_id: UUID


class Source(BaseModel):
    id: UUID
    filename: str
    kind: str
    page_count: int | None
    size_bytes: int
    chunk_count: int
    detail: str


class ConceptState(BaseModel):
    id: UUID
    name: str
    description: str
    stage: int
    tide: float | None
    band: str
    next_review_at: datetime | None
    due: bool
    projected_tide: float | None


class Concepts(BaseModel):
    concepts: list[ConceptState]
    due_count: int
    next_review_at: datetime | None


# The id a browser tab drives sessions by.
type DriverId = Annotated[str, Field(min_length=1, max_length=100)]


class Driver(BaseModel):
    driver: DriverId


class Offer(BaseModel):
    thread_id: str
    concept: str
    number: int
    last_active_at: datetime
    busy: bool


class SessionOffer(BaseModel):
    offer: Offer | None


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")  # one kind of reply, never a mix of them


class AnswerReply(_Reply):
    attempt_id: UUID
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DisputeRequest(_Reply):
    id: UUID
    reason: DisputeReason
    explanation: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class DisputeReply(_Reply):
    dispute: DisputeRequest


class QuitReply(_Reply):
    quit: Literal[True]


class ReplyBody(BaseModel):
    driver: DriverId
    reply: AnswerReply | DisputeReply | QuitReply


class Streak(BaseModel):
    current: int
    best: int
    days: list[bool]  # the last 35 days, oldest first, ending today


class DueConcept(BaseModel):
    name: str
    tide: float | None


class Average(BaseModel):
    now: float | None
    change: float | None
    since: str
    counted: int
    not_counted: list[str]


class ChartConcept(BaseModel):
    id: UUID
    name: str
    studied: bool
    now: float | None
    past: list[tuple[datetime, float | None]]
    ahead: list[tuple[datetime, float]]


class Chart(BaseModel):
    enough_days: bool
    concepts: list[ChartConcept]


class StagedConcept(BaseModel):
    name: str
    next_review_at: datetime | None


class Stage(BaseModel):
    stage: int
    concepts: list[StagedConcept]


class Progress(BaseModel):
    streak: Streak
    due: list[DueConcept]
    average: Average
    chart: Chart
    stages: list[Stage]


# The Costs page. Decimals serialise as strings, so money keeps its exact digits; None stays null.
class CostSummary(BaseModel):
    cost_usd: Decimal | None
    unpriced_calls: int
    input_tokens: int | None
    output_tokens: int | None
    calls: int
    retried: int
    fell_back: int
    ttft_p50_ms: int | None
    ttft_p95_ms: int | None
    total_p50_ms: int | None
    total_p95_ms: int | None
    fallback_rate: float | None
    retry_rate: float | None
    all_free_tier: bool


class CostBreakdown(BaseModel):
    key: str
    label: str
    calls: int
    cost_usd: Decimal | None
    unpriced_calls: int
    input_tokens: int | None
    output_tokens: int | None
    ttft_p50_ms: int | None
    ttft_p95_ms: int | None
    total_p50_ms: int | None
    total_p95_ms: int | None
    output_tokens_per_second: float | None
    fallbacks: int


class CostDay(BaseModel):
    day: date
    input_tokens: int | None
    output_tokens: int | None
    tokens: int | None
    calls: int


class ModelRef(BaseModel):
    provider: str
    model: str


class CostRole(BaseModel):
    role: str
    main: ModelRef
    backup: ModelRef | None
    attempts: int
    failed_share: float | None
    p95_total_ms: int | None


class CostAnomaly(BaseModel):
    kind: Literal["tokens", "ttft", "total", "day"]
    call_id: UUID | None
    day: date | None
    endpoint: str | None
    ratio: float
    message: str


class CostCall(BaseModel):
    """One model-call attempt."""

    id: UUID
    call_id: UUID
    request_id: UUID | None
    user_id: UUID
    at: datetime
    role: str
    endpoint: str
    endpoint_label: str
    provider: str
    model: str
    attempt: int
    outcome: str
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    ttft_ms: int | None
    total_ms: int
    cost_usd: Decimal | None
    backup: bool
    error_kind: str | None
    anomaly: str | None = None  # the message of the first anomaly for this call, if any


class CostSummaryPage(BaseModel):
    summary: CostSummary
    by_feature: list[CostBreakdown]
    per_day: list[CostDay]
    roles: list[CostRole]
    by_model: list[CostBreakdown]
    by_endpoint: list[CostBreakdown]
    by_tenant: list[CostBreakdown]
    anomalies: list[CostAnomaly]
    failures: list[CostCall]


class CostPrice(BaseModel):
    provider: str
    model: str
    input_per_m: Decimal
    output_per_m: Decimal
    source: str
    as_of: date
    free_tier: bool
