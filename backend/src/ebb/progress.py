"""Progress maths for the dashboard. Pure functions, like mastery and routing."""

from bisect import bisect_right
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from ebb import mastery
from ebb.domain import Concept, GradeValue

HIGH_TIDE = 0.80
EBBING = 0.60


@dataclass(frozen=True)
class AnswerPoint:
    concept_id: UUID
    graded_at: datetime
    grade: GradeValue


@dataclass(frozen=True)
class TideLine:
    past: list[tuple[datetime, float | None]]
    ahead: list[tuple[datetime, float]]


def replay(concept: Concept, answers: Sequence[AnswerPoint]) -> list[tuple[datetime, Concept]]:
    """The concept after each answer, from a new (stage 0) copy; answers are in time order."""
    state = replace(concept, stage=0, next_review_at=None, last_reviewed_at=None, review_count=0)
    history = []
    for answer in answers:
        state = mastery.apply_grade(state, answer.grade, answer.graded_at)
        history.append((answer.graded_at, state))
    return history


def projected_tide(concept: Concept, now: datetime, days: int = 3) -> float | None:
    return mastery.tide(concept, now + timedelta(days=days))


def tide_line(
    history: Sequence[tuple[datetime, Concept]],
    stored: Concept,
    start: datetime,
    now: datetime,
    step: timedelta = timedelta(hours=6),
    days_ahead: int = 3,
) -> TideLine:
    """Past from the replay; "now" and the future from the stored state, which wins."""
    moments = [at for at, _ in history]
    past: list[tuple[datetime, float | None]] = []
    at = start
    while at < now:
        index = bisect_right(moments, at)
        past.append((at, mastery.tide(history[index - 1][1], at) if index else None))
        at += step
    past.append((now, mastery.tide(stored, now)))
    ahead: list[tuple[datetime, float]] = []
    if stored.stage >= 1:
        end = now + timedelta(days=days_ahead)
        at = now + step
        while at <= end:
            level = mastery.tide(stored, at)
            if level is not None:
                ahead.append((at, level))
            at += step
    return TideLine(past, ahead)


def answer_days(times: Iterable[datetime], tz: ZoneInfo) -> set[date]:
    return {moment.astimezone(tz).date() for moment in times}


def streak(days: Collection[date], today: date) -> tuple[int, int]:
    """(current run ending today, or yesterday if today has none; best run ever)."""
    day_set = set(days)
    best = 0
    for day in day_set:
        if day - timedelta(days=1) not in day_set:
            run = 1
            while day + timedelta(days=run) in day_set:
                run += 1
            best = max(best, run)
    end = today if today in day_set else today - timedelta(days=1)
    current = 0
    while end - timedelta(days=current) in day_set:
        current += 1
    return current, best


def average_tide(concepts: Iterable[Concept], at: datetime) -> float | None:
    levels = [t for c in concepts if c.stage >= 1 if (t := mastery.tide(c, at)) is not None]
    return sum(levels) / len(levels) if levels else None


def band(tide: float | None) -> str:
    if tide is None:
        return "new"
    if tide >= HIGH_TIDE:
        return "high tide"
    return "ebbing" if tide >= EBBING else "low tide"
