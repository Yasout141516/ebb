"""Progress maths and the history queries (spec: Progress dashboard)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import count
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb import mastery
from ebb.domain import Answer, Concept, GradeValue, Question
from ebb.graph.build import build_graph
from ebb.progress import (
    AnswerPoint,
    answer_days,
    average_tide,
    band,
    projected_tide,
    replay,
    streak,
    tide_line,
)
from tests.fakes import FakeTutor, MemoryStore, make_subject

USER = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def concept(stage: int = 0, reviewed: datetime | None = None) -> Concept:
    return Concept(
        uuid4(),
        uuid4(),
        "DNS",
        "Names.",
        stage=stage,
        last_reviewed_at=reviewed,
        next_review_at=reviewed + timedelta(days=mastery.INTERVAL_DAYS[stage])
        if reviewed
        else None,
        review_count=1 if reviewed else 0,
    )


def drive_session(store: MemoryStore, subject_id: UUID, clock) -> None:
    graph = build_graph(FakeTutor(), store, InMemorySaver(), now=clock)
    config = {"configurable": {"thread_id": str(uuid4())}}
    start = {"user_id": str(USER), "subject_id": str(subject_id), "thread_id": "t-1"}
    result = graph.invoke(start, config)
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        text = "1" if payload["question"]["options"] else "right"
        resume = {"answer": text, "attempt_id": str(uuid4())}
        result = graph.invoke(Command(resume=resume), config)


def one_answered_concept(store: MemoryStore, grades: list[GradeValue]) -> tuple[UUID, list[Answer]]:
    subject_id = make_subject(store, USER, concepts=1)
    session_id = store.create_session(USER, subject_id, "t")
    c = store.list_concepts(USER, subject_id)[0]
    question = Question(uuid4(), c.id, "short", 1, "Why?", "Because.")
    store.save_questions(USER, session_id, [question])
    answers = [
        Answer(uuid4(), question.id, c.id, "x", grade, "ok", "m", NOW + timedelta(days=n))
        for n, grade in enumerate(grades)
    ]
    for a in answers:
        store.record_answer(USER, a)
    return subject_id, answers


def test_replay_ends_at_the_stored_state():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=3)
    ticks = count()
    clock = lambda: NOW + timedelta(days=2 * next(ticks))  # noqa: E731
    drive_session(store, subject_id, clock)
    drive_session(store, subject_id, clock)  # a later session promotes the due concepts

    stored = store.list_concepts(USER, subject_id)
    answers = store.subject_answers(USER, subject_id)
    assert max(c.stage for c in stored) >= 2
    for c in stored:
        history = replay(c, [a for a in answers if a.concept_id == c.id])
        assert history[-1][1] == c


def test_replay_skips_voided_and_uses_changed_grades():
    store = MemoryStore()
    subject_id, answers = one_answered_concept(store, [GradeValue.CORRECT, GradeValue.CORRECT])
    store.voided[answers[0].id] = NOW
    store.attempts[answers[1].id] = replace(answers[1], grade=GradeValue.INCORRECT)

    points = store.subject_answers(USER, subject_id)

    assert [p.grade for p in points] == [GradeValue.INCORRECT]
    assert replay(store.list_concepts(USER, subject_id)[0], points)[-1][1].stage == 1


def test_tide_line_uses_stored_state_at_now_when_replay_disagrees():
    start = NOW - timedelta(days=2)
    replayed = concept(2, start)
    stored = concept(3, NOW - timedelta(hours=1))

    line = tide_line([(start, replayed)], stored, start, NOW)

    assert line.past[-1] == (NOW, mastery.tide(stored, NOW))
    assert line.past[0] == (start, mastery.tide(replayed, start))
    when, level = line.past[-2]
    assert level == mastery.tide(replayed, when)
    assert line.ahead[0][0] == NOW + timedelta(hours=6)
    assert line.ahead[-1][0] == NOW + timedelta(days=3)
    assert line.ahead[-1][1] == projected_tide(stored, NOW)


def test_tide_line_for_a_new_concept_is_blank_and_has_no_future():
    new = concept()

    line = tide_line([], new, NOW - timedelta(days=1), NOW)

    assert line.ahead == []
    assert all(level is None for _, level in line.past)
    assert projected_tide(new, NOW) is None


def test_streak_counts_today_or_yesterday():
    days = {date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)}
    assert streak(days, date(2026, 10, 22)) == (3, 3)
    assert streak(days, date(2026, 10, 23)) == (3, 3)
    assert streak(days, date(2026, 10, 24)) == (0, 3)
    assert streak(set(), date(2026, 10, 24)) == (0, 0)
    older = {date(2026, 10, n) for n in (10, 11, 12, 13)}
    assert streak(days | older, date(2026, 10, 22)) == (3, 4)


def test_answer_days_use_the_zone():
    late = datetime(2026, 10, 15, 23, 30, tzinfo=UTC)
    assert answer_days([late], ZoneInfo("UTC")) == {date(2026, 10, 15)}
    assert answer_days([late], ZoneInfo("Asia/Karachi")) == {date(2026, 10, 16)}


def test_average_tide_ignores_new_concepts():
    assert average_tide([concept(), concept(1, NOW)], NOW) == pytest.approx(1.0)
    assert average_tide([concept()], NOW) is None
    assert average_tide([], NOW) is None


def test_band_thresholds():
    assert band(0.80) == "high tide"
    assert band(0.7999) == "ebbing"
    assert band(0.60) == "ebbing"
    assert band(0.5999) == "low tide"
    assert band(None) == "new"


def test_memory_store_history_is_scoped_and_skips_voided():
    store = MemoryStore()
    subject_id, [answer] = one_answered_concept(store, [GradeValue.CORRECT])
    c = store.list_concepts(USER, subject_id)[0]

    assert store.subject_answers(USER, subject_id) == [AnswerPoint(c.id, NOW, GradeValue.CORRECT)]
    assert store.subject_answers(OTHER, subject_id) == []
    assert store.answer_times(OTHER) == []
    store.voided[answer.id] = NOW
    assert store.answer_times(USER) == []
    assert store.subject_answers(USER, subject_id) == []
