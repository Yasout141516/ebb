"""Leitner stages and the tide gauge. Pure functions; `now` is always passed in."""

from dataclasses import replace
from datetime import datetime, timedelta

from ebb.domain import Concept, GradeValue

INTERVAL_DAYS = {1: 1, 2: 3, 3: 7, 4: 16, 5: 35}
DUE_TIDE = 0.7
MAX_STAGE = 5


def is_due(concept: Concept, now: datetime) -> bool:
    return concept.next_review_at is None or concept.next_review_at <= now


def apply_grade(concept: Concept, grade: GradeValue, now: datetime) -> Concept:
    """Return the concept after one graded answer (spec: Mastery rules)."""
    if concept.stage == 0 or grade is GradeValue.INCORRECT:
        new_stage = 1
    elif not is_due(concept, now):
        return concept
    elif grade is GradeValue.CORRECT:
        new_stage = min(concept.stage + 1, MAX_STAGE)
    else:
        new_stage = concept.stage
    return replace(
        concept,
        stage=new_stage,
        last_reviewed_at=now,
        next_review_at=now + timedelta(days=INTERVAL_DAYS[new_stage]),
        review_count=concept.review_count + 1,
    )


def tide(concept: Concept, now: datetime) -> float | None:
    """Estimated recall right now: 1.0 just after review, 0.7 on the due date."""
    if concept.stage == 0 or concept.last_reviewed_at is None:
        return None
    days = max((now - concept.last_reviewed_at).total_seconds(), 0.0) / 86400
    return DUE_TIDE ** (days / INTERVAL_DAYS[concept.stage])
