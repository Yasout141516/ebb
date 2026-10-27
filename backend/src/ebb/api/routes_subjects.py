"""Subjects, their sources and their concepts."""

from uuid import UUID

from fastapi import APIRouter, Request

from ebb import mastery, progress
from ebb.api.schemas import (
    ConceptBrief,
    Concepts,
    ConceptState,
    CreatedSubject,
    NewSubject,
    Source,
    SubjectSummary,
)
from ebb.api.services import owned_subject, services_of
from ebb.subjects import subject_from_topic
from ebb.ui import pages_prefix
from ebb.units import format_megabytes

router = APIRouter()


@router.get("/subjects")
def list_subjects(request: Request) -> list[SubjectSummary]:
    services = services_of(request)
    now = services.now()
    summaries = []
    for subject in services.store.list_subjects(services.user_id):
        concepts = services.store.list_concepts(services.user_id, subject.id)
        due = mastery.reviews_due(concepts, now)
        summaries.append(
            SubjectSummary(
                id=subject.id, name=subject.name, concept_count=len(concepts), due_count=len(due)
            )
        )
    return summaries


@router.post("/subjects", status_code=201)
def create_subject(body: NewSubject, request: Request) -> CreatedSubject:
    services = services_of(request)
    subject_id, concepts = subject_from_topic(
        services.require_tutor(), services.store, services.user_id, body.topic
    )
    return CreatedSubject(
        id=subject_id,
        name=body.topic,
        concepts=[ConceptBrief(name=n, description=d) for n, d in concepts],
    )


@router.get("/subjects/{subject_id}/sources")
def list_sources(subject_id: UUID, request: Request) -> list[Source]:
    services = services_of(request)
    owned_subject(services, subject_id)
    return [
        Source(
            id=d.id,
            filename=d.filename,
            kind=d.kind,
            page_count=d.page_count,
            size_bytes=d.size_bytes,
            chunk_count=d.chunk_count,
            detail=f"{pages_prefix(d.page_count)}{format_megabytes(d.size_bytes)}",
        )
        for d in services.store.list_documents(services.user_id, subject_id)
    ]


@router.get("/subjects/{subject_id}/concepts")
def list_concepts(subject_id: UUID, request: Request) -> Concepts:
    services = services_of(request)
    owned_subject(services, subject_id)
    now = services.now()
    concepts = services.store.list_concepts(services.user_id, subject_id)
    states = []
    for c in concepts:
        level = mastery.tide(c, now)
        states.append(
            ConceptState(
                id=c.id,
                name=c.name,
                description=c.description,
                stage=c.stage,
                tide=level,
                band=progress.band(level),
                next_review_at=c.next_review_at,
                due=mastery.is_review_due(c, now),
                projected_tide=progress.projected_tide(c, now),
            )
        )
    return Concepts(
        concepts=states,
        due_count=sum(s.due for s in states),
        next_review_at=services.store.next_review_at(services.user_id, subject_id),
    )
