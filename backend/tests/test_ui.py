from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console

from ebb.domain import Concept
from ebb.ui import (
    caught_up_line,
    due_label,
    format_ago,
    format_day,
    format_tide,
    greeting,
    read_reply,
    read_yes_no,
    render_summary,
    render_turn,
    resume_question,
    stage_dots,
    status_table,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)
DHAKA = ZoneInfo("Asia/Dhaka")


def test_format_tide():
    assert format_tide(None) == "new"
    assert format_tide(0.7) == "70%"
    assert format_tide(0.996) == "100%"


def test_stage_dots():
    assert stage_dots(0) == "○○○○○"
    assert stage_dots(3) == "●●●○○"


def test_due_label():
    base = Concept(uuid4(), uuid4(), "DNS", "d")
    assert due_label(base, NOW, UTC) == "new"
    due = Concept(uuid4(), uuid4(), "DNS", "d", stage=2, next_review_at=NOW - timedelta(hours=1))
    assert due_label(due, NOW, UTC) == "due now"
    later = Concept(
        uuid4(), uuid4(), "DNS", "d", stage=2, next_review_at=datetime(2026, 10, 20, tzinfo=UTC)
    )
    assert due_label(later, NOW, UTC) == "Tue 20 Oct"


@pytest.mark.parametrize(
    ("raw", "options", "expected"),
    [
        ("q", [], {"quit": True}),
        (" Q ", ["a", "b"], {"quit": True}),
        ("", [], None),
        ("   ", ["a", "b"], None),
        ("caching saves time", [], {"answer": "caching saves time"}),
        ("2", ["a", "b"], {"answer": "2"}),
        ("b", ["a", "b"], {"answer": "b"}),
        ("3", ["a", "b"], None),
        ("maybe", ["a", "b"], None),
    ],
)
def test_read_reply(raw, options, expected):
    assert read_reply(raw, options) == expected


def test_generated_text_with_brackets_is_shown_literally():
    console = Console(record=True, width=100)
    payload = {
        "concept": "Routing",
        "feedback": {"value": "incorrect", "explanation": "Close [/bold] but no."},
        "lesson": "Use [SYN] first.",
        "notices": ["[x] note"],
        "question": {
            "type": "mcq",
            "prompt": "Which flag opens it: [SYN] or [/x]?",
            "options": ["list[int]", "[ACK]"],
        },
        "number": 1,
    }

    render_turn(console, payload)

    text = console.export_text()
    for literal in [
        "Which flag opens it: [SYN] or [/x]?",
        "list[int]",
        "[ACK]",
        "Close [/bold] but no.",
        "Use [SYN] first.",
        "[x] note",
    ]:
        assert literal in text


def test_status_table_shows_brackets_in_concept_names():
    console = Console(record=True, width=100)
    concept = Concept(uuid4(), uuid4(), "list[int]", "d", stage=1, next_review_at=NOW)

    console.print(status_table([concept], NOW, UTC))

    assert "list[int]" in console.export_text()


def test_format_day_uses_the_time_zone():
    assert format_day(datetime(2026, 10, 16, 20, 0, tzinfo=UTC), DHAKA) == "Sat 17 Oct"


def test_due_label_and_summary_use_the_time_zone():
    instant = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    concept = Concept(uuid4(), uuid4(), "DNS", "d", stage=2, next_review_at=instant)
    assert due_label(concept, NOW, DHAKA) == "Sat 17 Oct"
    assert due_label(concept, NOW, UTC) == "Fri 16 Oct"

    console = Console(record=True, width=100)
    final = {
        "results": [
            {
                "concept": "DNS",
                "tally": {"correct": 1, "partial": 0, "incorrect": 0},
                "tide_before": None,
                "tide_after": 1.0,
                "stage_before": 0,
                "stage_after": 1,
                "next_review_at": instant.isoformat(),
            }
        ]
    }
    render_summary(console, final, DHAKA)
    assert "Sat 17 Oct" in console.export_text()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", True), ("y", True), ("YES", True), (" n ", False), ("No", False), ("maybe", None)],
)
def test_read_yes_no(raw, expected):
    assert read_yes_no(raw) is expected


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (timedelta(seconds=20), "a moment ago"),
        (timedelta(minutes=1), "1 minute ago"),
        (timedelta(minutes=12), "12 minutes ago"),
        (timedelta(hours=1), "1 hour ago"),
        (timedelta(hours=3, minutes=50), "3 hours ago"),
    ],
)
def test_format_ago(delta, text):
    assert format_ago(delta) == text


def test_greeting_variants():
    assert greeting(3, 2) == "Welcome back — 3 ideas are ebbing. Reviewing those first."
    assert greeting(1, 0) == "Welcome back — 1 idea is ebbing. Reviewing those first."
    assert greeting(0, 4) == "Nothing is due. Let's learn something new."
    assert greeting(0, 0) == "You've learned every concept here. Finding more advanced ones…"


def test_resume_question():
    assert (
        resume_question({"concept": "DNS resolution", "number": 2}, "3 hours ago")
        == "You stopped on DNS resolution, question 2, 3 hours ago. Resume? [Y/n]"
    )


def test_caught_up_line_in_the_time_zone():
    due = datetime(2026, 10, 16, 20, tzinfo=UTC)
    assert caught_up_line(due, DHAKA) == "You're all caught up — next review Sat 17 Oct."
    assert caught_up_line(None, DHAKA) == "You're all caught up."
