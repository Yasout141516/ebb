"""The progress dashboard's numbers."""

from bisect import bisect_right
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from ebb import mastery, progress
from ebb.api.schemas import (
    Average,
    Chart,
    ChartConcept,
    DueConcept,
    Progress,
    Stage,
    StagedConcept,
    Streak,
)
from ebb.api.services import owned_subject, services_of
from ebb.domain import Concept
from ebb.mastery import MAX_STAGE
from ebb.progress import AnswerPoint

router = APIRouter()

WINDOW = timedelta(days=30)
MIN_CHART_DAYS = 3


def build_progress(
    concepts: Sequence[Concept],
    answers: Sequence[AnswerPoint],
    all_times: Sequence[datetime],
    now: datetime,
    tz: ZoneInfo,
) -> Progress:
    start = now - WINDOW
    today = now.astimezone(tz).date()
    days = progress.answer_days(all_times, tz)
    current, best = progress.streak(days, today)
    last14 = [today - timedelta(days=13 - i) in days for i in range(14)]

    histories = {
        c.id: progress.replay(c, [a for a in answers if a.concept_id == c.id]) for c in concepts
    }
    # Each concept as it stood 30 days ago: the replayed state in force then, if any.
    then = []
    for history in histories.values():
        index = bisect_right([at for at, _ in history], start)
        if index:
            then.append(history[index - 1][1])
    average_now = progress.average_tide(concepts, now)
    average_then = progress.average_tide(then, start)
    change = None if average_now is None or average_then is None else average_now - average_then

    chart = []
    for c in concepts:
        line = progress.tide_line(histories[c.id], c, start, now)
        chart.append(
            ChartConcept(
                id=c.id,
                name=c.name,
                studied=c.stage >= 1,
                now=line.past[-1][1],
                past=line.past,
                ahead=line.ahead,
            )
        )
    subject_days = progress.answer_days([a.graded_at for a in answers], tz)
    return Progress(
        streak=Streak(current=current, best=best, last14=last14),
        due=[
            DueConcept(name=c.name, tide=mastery.tide(c, now))
            for c in mastery.reviews_due(concepts, now)
        ],
        average=Average(
            now=average_now,
            change=change,
            since=start.astimezone(tz).date().isoformat(),
            counted=sum(1 for c in concepts if c.stage >= 1),
            not_counted=[c.name for c in concepts if c.stage == 0],
        ),
        chart=Chart(enough_days=len(subject_days) >= MIN_CHART_DAYS, concepts=chart),
        stages=[
            Stage(
                stage=stage,
                concepts=[
                    StagedConcept(name=c.name, next_review_at=c.next_review_at)
                    for c in sorted(
                        (c for c in concepts if c.stage == stage),
                        key=lambda c: (
                            c.next_review_at or datetime.max.replace(tzinfo=UTC),
                            c.name,
                        ),
                    )
                ],
            )
            for stage in range(1, MAX_STAGE + 1)
        ],
    )


@router.get("/subjects/{subject_id}/progress")
def subject_progress(subject_id: UUID, request: Request) -> Progress:
    services = services_of(request)
    owned_subject(services, subject_id)
    user = services.user_id
    return build_progress(
        services.store.list_concepts(user, subject_id),
        services.store.subject_answers(user, subject_id),
        services.store.answer_times(user),
        services.now(),
        services.tz,
    )
