"""Request and response bodies. Each route adds its own here."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from ebb.domain import DisputeReason


class Health(BaseModel):
    database: str
    tutor: str


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
