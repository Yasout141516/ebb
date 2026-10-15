from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ebb.domain import Concept
from ebb.ui import due_label, format_tide, read_reply, stage_dots

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
