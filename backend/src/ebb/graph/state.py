"""Graph state and its (de)serialisation.

State holds only JSON-style values (str, int, list, dict). LangGraph checkpointers
warn on — and will soon block — custom classes, and turn tuples into lists, so
domain objects are converted at the node boundary with the helpers below.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID

from ebb.domain import Concept, Grade, GradeValue, Option, Question


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
    # current concept
    concept: dict[str, Any] | None
    start: dict[str, Any]  # {"stage": int, "tide": float | None} when the concept was picked
    tally: dict[str, int]
    batch: list[dict[str, Any]]
    asked_prompts: list[str]
    asked_on_concept: int
    correct_streak: int
    generation_calls_on_concept: int
    # current question
    question: dict[str, Any] | None
    difficulty: int | None
    retry_question: bool
    attempt_id: str | None
    answer: str | None
    grade: dict[str, Any] | None
    model_used: str | None
    lesson: str | None


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


def question_to_dict(q: Question) -> dict[str, Any]:
    return {
        "id": str(q.id),
        "concept_id": str(q.concept_id),
        "type": q.type,
        "difficulty": q.difficulty,
        "prompt": q.prompt,
        "reference_answer": q.reference_answer,
        "options": [asdict(o) for o in q.options],
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
    )


def grade_to_dict(g: Grade) -> dict[str, Any]:
    return {"value": g.value.value, "explanation": g.explanation, "misconception": g.misconception}


def grade_from_dict(d: dict[str, Any]) -> Grade:
    return Grade(GradeValue(d["value"]), d["explanation"], d["misconception"])
