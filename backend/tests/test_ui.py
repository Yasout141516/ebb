from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from rich.console import Console

from ebb.domain import Concept
from ebb.ui import due_label, format_tide, read_reply, render_turn, stage_dots, status_table

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def test_format_tide():
    assert format_tide(None) == "new"
    assert format_tide(0.7) == "70%"
    assert format_tide(0.996) == "100%"


def test_stage_dots():
    assert stage_dots(0) == "○○○○○"
    assert stage_dots(3) == "●●●○○"


def test_due_label():
    base = Concept(uuid4(), uuid4(), "DNS", "d")
    assert due_label(base, NOW) == "new"
    due = Concept(uuid4(), uuid4(), "DNS", "d", stage=2, next_review_at=NOW - timedelta(hours=1))
    assert due_label(due, NOW) == "due now"
    later = Concept(
        uuid4(), uuid4(), "DNS", "d", stage=2, next_review_at=datetime(2026, 10, 20, tzinfo=UTC)
    )
    assert due_label(later, NOW) == "Tue 20 Oct"


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

    console.print(status_table([concept], NOW))

    assert "list[int]" in console.export_text()
