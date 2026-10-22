from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ebb.domain import Concept
from ebb.routing import (
    after_ask,
    after_extend,
    after_generate,
    after_mastery,
    after_pick,
    after_search,
    after_verify,
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


def test_due_reviews_come_before_new_concepts():
    new = make("new")
    due = make("due", stage=2, due_in_days=-1, reviewed_days_ago=4)
    assert pick_next_concept([new, due], [], NOW) is due


def test_most_faded_review_first():
    recent = make("recent", stage=2, due_in_days=-1, reviewed_days_ago=4)  # tide ~0.62
    faded = make("faded", stage=2, due_in_days=-7, reviewed_days_ago=10)  # tide ~0.30
    assert pick_next_concept([recent, faded], [], NOW) is faded


def test_equal_tide_keeps_list_order():
    a = make("a", stage=2, due_in_days=-1, reviewed_days_ago=4)
    b = make("b", stage=2, due_in_days=-1, reviewed_days_ago=4)
    assert pick_next_concept([a, b], [], NOW) is a
    assert pick_next_concept([b, a], [], NOW) is b


def test_new_concepts_in_list_order_when_nothing_is_due():
    first, second = make("first"), make("second")
    assert pick_next_concept([first, second], [], NOW) is first


def test_learned_concepts_that_are_not_due_are_never_picked():
    not_due = make("later", stage=2, due_in_days=2, reviewed_days_ago=1)
    assert pick_next_concept([not_due], [], NOW) is None


def test_studied_concepts_are_skipped():
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
        "studied_concept_ids": ["a"],
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


def test_finished_concept_always_moves_on():
    assert decide_next(state(correct_streak=2, studied_concept_ids=list("abcdefgh"))) == (
        "next_concept"
    )


def test_empty_batch_regenerates_until_the_cap():
    assert decide_next(state(batch=[], generation_calls_on_concept=1)) == "generate_questions"
    assert decide_next(state(batch=[], generation_calls_on_concept=2)) == "next_concept"


def test_incorrect_answers_go_to_teach_first():
    assert after_mastery(state(grade={"value": "incorrect"})) == "teach"
    assert after_mastery(state(grade={"value": "partial"})) == "ask_question"


def test_duplicate_answer_routes_like_an_uncounted_answer():
    assert after_mastery(state(grade=None)) == "ask_question"
    assert after_mastery(state(grade=None, correct_streak=2)) == "next_concept"


def test_after_pick_retrieves_notes_when_a_concept_was_picked():
    assert after_pick({"concept": {"id": "a"}, "caught_up": False}) == "retrieve_notes"


def test_after_pick_extends_when_nothing_is_left():
    assert after_pick({"concept": None, "caught_up": False}) == "extend_subject"


def test_after_pick_summarizes_when_caught_up():
    assert after_pick({"concept": None, "caught_up": True}) == "summarize"


def test_after_extend():
    assert after_extend({"caught_up": False, "fatal": False}) == "pick_concept"
    assert after_extend({"caught_up": True, "fatal": False}) == "summarize"
    assert after_extend({"caught_up": False, "fatal": True}) == "summarize"


def test_after_generate_verifies_unless_fatal():
    assert after_generate({"fatal": None}) == "verify_citations"
    assert after_generate({"fatal": "down"}) == "summarize"


def test_after_verify_retries_on_citation_failures():
    pending = state(batch=[], citation_failures=["question 1 cites nothing"])
    assert after_verify(pending, web=True) == "generate_questions"


def test_after_verify_asks_from_a_finalised_batch():
    # state() has none of the web keys, like a milestone-2 session
    assert after_verify(state(citation_failures=[]), web=True) == "ask_question"


def test_after_verify_with_an_empty_batch_follows_decide_next():
    assert after_verify(state(batch=[], citation_failures=[]), web=True) == "generate_questions"
    empty = state(batch=[], citation_failures=[], generation_calls_on_concept=2)
    assert after_verify(empty, web=True) == "next_concept"


def repeats(**overrides):
    """A finalised notes batch that dropped one repeat on a concept not yet searched."""
    base = {
        "citation_failures": [],
        "concept": {"id": "c1"},
        "run": {"mode": "notes", "candidates": [], "replacing": 1},
        "web_searched": [],
        "web_failed": False,
    }
    return state(**{**base, **overrides})


def test_after_verify_searches_the_web_for_dropped_repeats():
    assert after_verify(repeats(), web=True) == "search_web"
    assert after_verify(repeats(batch=[]), web=True) == "search_web"


@pytest.mark.parametrize(
    ("overrides", "web"),
    [
        ({}, False),  # no Tavily key
        ({"run": {"mode": "notes", "candidates": [], "replacing": 0}}, True),
        # web questions never search again
        ({"run": {"mode": "web", "candidates": [], "replacing": 1}}, True),
        ({"web_searched": ["c1"]}, True),
        ({"web_failed": True}, True),
    ],
)
def test_after_verify_skips_the_search(overrides, web):
    assert after_verify(repeats(**overrides), web=web) == "ask_question"
    assert after_verify(repeats(batch=[], **overrides), web=web) == "generate_questions"


def run(mode):
    return {"mode": mode, "candidates": [], "replacing": 0}


def test_routers_read_a_checkpoint_saved_with_the_old_keys():
    old = repeats()
    del old["run"]
    old |= {"generation_mode": "notes", "replacements_needed": 1}
    assert after_verify(old, web=True) == "search_web"
    assert after_search({**state(), "generation_mode": "web"}) == "generate_questions"
    assert after_search(state()) == "ask_question"  # no run and no old keys


def test_after_search_writes_web_questions_when_pages_were_found():
    assert after_search(state(run=run("web"))) == "generate_questions"


def test_after_search_without_pages_continues_with_the_batch():
    assert after_search(state(run=run("notes"))) == "ask_question"
    assert after_search(state(batch=[], run=run("notes"))) == "generate_questions"
    empty = state(batch=[], run=run("notes"), generation_calls_on_concept=2)
    assert after_search(empty) == "next_concept"


def test_after_ask_regrades_a_dispute():
    assert after_ask({"quit": False, "dispute": {"reason": "correct"}}) == "regrade"
    assert after_ask({"quit": False, "dispute": None}) == "grade_answer"
    assert after_ask({"quit": False}) == "grade_answer"  # a milestone-2 state
    assert after_ask({"quit": True, "dispute": None}) == "summarize"
