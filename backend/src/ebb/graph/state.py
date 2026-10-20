"""Graph state and its (de)serialisation.

State holds only JSON-style values (str, int, list, dict). LangGraph checkpointers
warn on — and will soon block — custom classes, and turn tuples into lists, so
domain objects are converted at the node boundary with the helpers below.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID

from ebb.citations import from_dict as citation_from_dict
from ebb.citations import to_dict as citation_to_dict
from ebb.domain import Claim, Concept, Grade, GradeValue, Option, Question


class QuizState(TypedDict, total=False):
    # set by the caller
    user_id: str
    subject_id: str
    thread_id: str
    # session
    session_id: str
    studied_concept_ids: list[str]
    results: list[dict[str, Any]]
    quit: bool
    caught_up: bool  # everything learned and the tutor had nothing more to add
    fatal: str | None
    notices: list[str]
    has_notes: bool  # the subject has documents
    # current concept
    concept: dict[str, Any] | None
    start: dict[str, Any]  # {"stage": int, "tide": float | None} when the concept was picked
    tally: dict[str, int]
    batch: list[dict[str, Any]]
    asked_prompts: list[str]
    asked_on_concept: int
    correct_streak: int
    generation_calls_on_concept: int
    sources: list[dict[str, Any]]  # the concept's retrieved note chunks, as Source dicts
    candidates: list[dict[str, Any]]  # generated questions waiting for verify_citations
    citation_retries: int
    citation_failures: list[str]  # why the last candidates' citations failed; empty when done
    # current question
    question: dict[str, Any] | None
    difficulty: int | None
    retry_question: bool
    attempt_id: str | None
    answer: str | None
    grade: dict[str, Any] | None
    model_used: str | None
    lesson: str | None
    lesson_citations: list[dict[str, Any]]


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def concept_to_dict(c: Concept) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "subject_id": str(c.subject_id),
        "name": c.name,
        "description": c.description,
        "stage": c.stage,
        "next_review_at": _dt(c.next_review_at),
        "last_reviewed_at": _dt(c.last_reviewed_at),
        "review_count": c.review_count,
    }


def concept_from_dict(d: dict[str, Any]) -> Concept:
    return Concept(
        id=UUID(d["id"]),
        subject_id=UUID(d["subject_id"]),
        name=d["name"],
        description=d["description"],
        stage=d["stage"],
        next_review_at=_parse_dt(d["next_review_at"]),
        last_reviewed_at=_parse_dt(d["last_reviewed_at"]),
        review_count=d["review_count"],
    )


# Dicts saved by milestone 2 lack source, claims and citations, hence the defaults below.


def question_to_dict(q: Question) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "concept_id": str(q.concept_id),
        "type": q.type,
        "difficulty": q.difficulty,
        "prompt": q.prompt,
        "reference_answer": q.reference_answer,
        "options": [asdict(o) for o in q.options],
        "source": q.source,
        "claims": [asdict(c) for c in q.claims],
        "citations": [citation_to_dict(c) for c in q.citations],
    }


def question_from_dict(d: dict[str, Any]) -> Question:
    return Question(
        id=UUID(d["id"]),
        concept_id=UUID(d["concept_id"]),
        type=d["type"],
        difficulty=d["difficulty"],
        prompt=d["prompt"],
        reference_answer=d["reference_answer"],
        options=tuple(Option(o["text"], o["is_correct"], o["why"]) for o in d["options"]),
        source=d.get("source", "general"),
        claims=_claims(d.get("claims", [])),
        citations=tuple(citation_from_dict(c) for c in d.get("citations", [])),
    )


def grade_to_dict(g: Grade, general_knowledge: bool = False) -> dict[str, Any]:
    """`general_knowledge`: the subject has notes, yet nothing from them backs this grade."""
    return {
        "value": g.value.value,
        "explanation": g.explanation,
        "misconception": g.misconception,
        "claims": [asdict(c) for c in g.claims],
        "citations": [citation_to_dict(c) for c in g.citations],
        "general_knowledge": general_knowledge,
    }


def grade_from_dict(d: dict[str, Any]) -> Grade:
    return Grade(
        GradeValue(d["value"]),
        d["explanation"],
        d["misconception"],
        claims=_claims(d.get("claims", [])),
        citations=tuple(citation_from_dict(c) for c in d.get("citations", [])),
    )


def _claims(items: list[dict[str, Any]]) -> tuple[Claim, ...]:
    return tuple(Claim(c["source"], c["quote"]) for c in items)
