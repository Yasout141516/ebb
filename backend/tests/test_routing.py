from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ebb.domain import Concept
from ebb.routing import (
    after_mastery,
    choose_question,
    decide_next,
    pick_next_concept,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def make(name, stage=0, due_in_days=None, reviewed_days_ago=None):
    return Concept(
        uuid4(),
        uuid4(),
        name,
        "",
        stage=stage,
        next_review_at=None if due_in_days is None else NOW + timedelta(days=due_in_days),
        last_reviewed_at=None
        if reviewed_days_ago is None
        else NOW - timedelta(days=reviewed_days_ago),
    )


def test_due_concepts_come_first():
    fresh = make("fresh", stage=1, due_in_days=2, reviewed_days_ago=1)
    due = make("due", stage=3, due_in_days=-1, reviewed_days_ago=8)
    assert pick_next_concept([fresh, due], [], NOW) is due


def test_then_lowest_stage_then_oldest_review():
    a = make("a", stage=2, due_in_days=-1, reviewed_days_ago=4)
    b = make("b", stage=1, due_in_days=-1, reviewed_days_ago=2)
    c = make("c", stage=1, due_in_days=-1, reviewed_days_ago=9)
    assert pick_next_concept([a, b, c], [], NOW) is c


def test_new_concepts_keep_list_order():
    first, second = make("first"), make("second")
    assert pick_next_concept([first, second], [], NOW) is first


def test_studied_concepts_are_skipped_and_none_when_exhausted():
    a, b = make("a"), make("b")
    assert pick_next_concept([a, b], [str(a.id)], NOW) is b
    assert pick_next_concept([a, b], [str(a.id), str(b.id)], NOW) is None


BATCH = [{"difficulty": d} for d in (2, 1, 3, 1, 2)]


@pytest.mark.parametrize(
    ("difficulty", "last", "first", "expected"),
    [
        (None, None, True, 1),  # first question: easiest
        (1, "correct", False, 0),  # correct: next harder (2)
        (2, "correct", False, 2),  # correct: harder (3)
        (3, "correct", False, 2),  # nothing harder: same difficulty
        (2, "partial", False, 0),  # partial: same difficulty
        (1, "incorrect", False, 1),  # incorrect: same difficulty
    ],
)
def test_choose_question(difficulty, last, first, expected):
    assert choose_question(BATCH, difficulty, last, first) == expected


def test_choose_question_falls_back_to_closest_difficulty():
    assert choose_question([{"difficulty": 3}, {"difficulty": 1}], 2, "partial", False) == 0


def state(**overrides):
    base = {
        "correct_streak": 0,
        "asked_on_concept": 1,
        "concepts_done": 0,
        "studied_concept_ids": ["a"],
        "concept_count": 5,
        "batch": [{"difficulty": 1}],
        "generation_calls_on_concept": 1,
        "grade": {"value": "correct"},
    }
    return {**base, **overrides}


def test_keeps_asking_while_batch_has_questions():
    assert decide_next(state()) == "ask_question"


def test_two_correct_in_a_row_finishes_the_concept():
    assert decide_next(state(correct_streak=2)) == "next_concept"


def test_four_questions_finish_the_concept():
    assert decide_next(state(asked_on_concept=4)) == "next_concept"


def test_third_finished_concept_ends_the_session():
    assert decide_next(state(correct_streak=2, concepts_done=2)) == "summarize"


def test_running_out_of_concepts_ends_the_session():
    assert decide_next(state(correct_streak=2, concept_count=1)) == "summarize"


def test_empty_batch_regenerates_until_the_cap():
    assert decide_next(state(batch=[], generation_calls_on_concept=1)) == "generate_questions"
    assert decide_next(state(batch=[], generation_calls_on_concept=2)) == "next_concept"


def test_incorrect_answers_go_to_teach_first():
    assert after_mastery(state(grade={"value": "incorrect"})) == "teach"
    assert after_mastery(state(grade={"value": "partial"})) == "ask_question"
