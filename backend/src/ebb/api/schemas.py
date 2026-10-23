"""Request and response bodies. Each route adds its own here."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class Health(BaseModel):
    database: str
    tutor: str


class SubjectSummary(BaseModel):
    id: UUID
    name: str
    concept_count: int
    due_count: int


class NewSubject(BaseModel):
    topic: str = Field(min_length=1, max_length=120)

    @field_validator("topic")
    @classmethod
    def not_blank(cls, topic: str) -> str:
        topic = topic.strip()
        if not topic:
            raise ValueError("topic must not be blank")
        return topic


class ConceptBrief(BaseModel):
    name: str
    description: str


class CreatedSubject(BaseModel):
    id: UUID
    name: str
    concepts: list[ConceptBrief]


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
