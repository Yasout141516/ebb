from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from rich.console import Console

from ebb.domain import Concept, Document
from ebb.ui import (
    caught_up_line,
    dispute_line,
    due_label,
    format_ago,
    format_day,
    format_tide,
    greeting,
    notes_report,
    read_reply,
    read_yes_no,
    render_citations,
    render_summary,
    render_turn,
    resume_question,
    source_lines,
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


def test_notes_report_for_a_pdf_with_matches_and_new_concepts():
    lines = notes_report(
        "wk1.pdf", 38, 112, ["Subnetting", "DNS"], [("ARP", "Maps IPs to MACs."), ("NAT", "x")]
    )
    assert lines == [
        "wk1.pdf · 38 pages · 112 passages",
        "4 concepts in these notes — 2 already here (Subnetting, DNS), 2 new:",
        "  1. ARP — Maps IPs to MACs.",
        "  2. NAT — x",
    ]


def test_notes_report_leaves_out_pages_for_text_files():
    lines = notes_report("ch3.md", None, 7, [], [("ARP", "x")])
    assert lines[0] == "ch3.md · 7 passages"
    assert lines[1] == "1 concepts in these notes — 1 new:"


def test_notes_report_leaves_out_the_list_when_nothing_is_new():
    lines = notes_report("ch3.md", None, 7, ["DNS"], [])
    assert lines == [
        "ch3.md · 7 passages",
        "1 concepts in these notes — 1 already here (DNS), 0 new:",
    ]


def test_source_lines():
    now = datetime(2026, 10, 15, tzinfo=UTC)
    pdf = Document(uuid4(), "wk1.pdf", "pdf", 2 * 1024 * 1024, 38, 112, now)
    text = Document(uuid4(), "ch3.md", "md", 512 * 1024, None, 7, now)
    assert source_lines([pdf, text]) == ["wk1.pdf · 38 pages · 2.0 MB", "ch3.md · 0.5 MB"]


def recorded(render, *args) -> str:
    console = Console(record=True, width=100)
    render(console, *args)
    return console.export_text()


def test_render_citations_for_pages_headings_and_web():
    citations = [
        {"kind": "chunk", "label": "CSE321.pdf · p. 12", "quote": "The first address."},
        {"kind": "chunk", "label": "networks.md · § Subnet masks", "quote": "Masks [x]."},
        {"kind": "web", "label": "cloudflare.com · What is DNS", "quote": "DNS maps names."},
    ]

    text = recorded(render_citations, citations, False)

    assert text.splitlines() == [
        "",
        "From your notes",
        "  [1] CSE321.pdf · p. 12",
        "      “The first address.”",
        "  [2] networks.md · § Subnet masks",
        "      “Masks [x].”",
        "",
        "From the web",
        "  [3] cloudflare.com · What is DNS",
        "      “DNS maps names.”",
    ]


def test_general_knowledge_label_only_when_flagged():
    cited = [{"kind": "chunk", "label": "a.md", "quote": "q"}]
    assert "General knowledge · not from your notes" in recorded(render_citations, [], True)
    assert recorded(render_citations, [], False) == ""
    assert "General knowledge" not in recorded(render_citations, cited, True)


def test_dispute_lines():
    base = {
        "original_grade": "partial",
        "new_grade": None,
        "explanation": "Because [x].",
        "citations": [],
        "concept": "DNS",
        "stage": 2,
    }
    changed = {**base, "outcome": "changed"}
    assert dispute_line({**changed, "new_grade": "correct"}) == (
        "Re-check: raised to Correct — DNS back to ●●○○○ level 2."
    )
    assert dispute_line({**changed, "new_grade": "incorrect"}) == (
        "Re-check: lowered to Incorrect — DNS now ●●○○○ level 2."
    )
    assert dispute_line({**base, "outcome": "voided"}) == (
        "Re-check: the question was ambiguous — that answer no longer counts."
    )
    assert dispute_line({**base, "outcome": "upheld"}) == (
        "Re-check: the grade stands — Because [x]."
    )
    assert dispute_line({"outcome": "failed"}) == (
        "Couldn't re-check right now — the grade stands. Try again with d."
    )
    assert dispute_line({"outcome": "refused"}) == (
        "Only short-answer grades from this session can be disputed."
    )


@pytest.mark.parametrize("raw", ["d", "D", " d "])
@pytest.mark.parametrize("can_dispute", [True, False])
def test_read_reply_d_requests_a_dispute(raw, can_dispute):
    assert read_reply(raw, [], can_dispute) == {"dispute_request": True}
    assert read_reply(raw, ["a", "b"], can_dispute) == {"dispute_request": True}


def question_payload(question=None, **extra):
    return {
        "concept": "DNS",
        "number": 2,
        "question": {"type": "short", "prompt": "Why?", "options": [], **(question or {})},
        "feedback": {
            "value": "partial",
            "explanation": "Half.",
            "citations": [],
            "general_knowledge": True,
        },
        "lesson": None,
        "notices": [],
        **extra,
    }


def test_render_turn_shows_source_line_hint_and_grade_label():
    payload = question_payload({"source_line": "From your notes · p. 12"}, can_dispute=True)

    text = recorded(render_turn, payload)

    assert "From your notes · p. 12" in text
    assert "Type your answer, d to dispute that grade, or q to stop" in text
    assert "General knowledge · not from your notes" in text
    assert "Type your answer, or q to stop" in recorded(render_turn, question_payload())


def test_render_turn_after_a_dispute_skips_the_old_feedback():
    cited = {"kind": "chunk", "label": "a.md", "quote": "Quote."}
    result = {"outcome": "upheld", "explanation": "Still half.", "citations": [cited]}
    payload = question_payload(dispute_result=result, lesson="Old lesson", notices=["oldnotice"])

    text = recorded(render_turn, payload)

    assert "Re-check: the grade stands — Still half." in text
    assert "“Quote.”" in text
    assert "Half." not in text.replace("Still half.", "")
    assert "Old lesson" not in text
    assert "oldnotice" not in text
    assert "Why?" in text
