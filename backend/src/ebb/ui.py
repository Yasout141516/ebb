"""Terminal rendering and input parsing. Formatting helpers are pure and tested."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ebb.domain import Concept, Document
from ebb.grading import GRADE_RANK, parse_choice
from ebb.mastery import MAX_STAGE, is_due, tide
from ebb.units import format_megabytes

GRADE_STYLE = {"correct": "green", "partial": "yellow", "incorrect": "red"}
GRADE_MARK = {"correct": "● Correct", "partial": "◐ Partial", "incorrect": "○ Incorrect"}


def format_tide(value: float | None) -> str:
    return "new" if value is None else f"{round(value * 100)}%"


def stage_dots(stage: int) -> str:
    return "●" * stage + "○" * (MAX_STAGE - stage)


def format_day(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).strftime("%a %d %b")


def due_label(concept: Concept, now: datetime, tz: ZoneInfo) -> str:
    if concept.stage == 0:
        return "new"
    if is_due(concept, now):
        return "due now"
    return format_day(concept.next_review_at, tz)


def read_reply(
    raw: str, options: Sequence[str], can_dispute: bool = False
) -> dict[str, Any] | None:
    """Turn one line of input into a resume value, or None if it should be asked again.

    `d` always gives a dispute request; the caller checks `can_dispute` and may refuse it.
    """
    text = raw.strip()
    if text.casefold() == "q":
        return {"quit": True}
    if text.casefold() == "d":
        return {"dispute_request": True}
    if not text:
        return None
    if options and parse_choice(options, text) is None:
        return None
    return {"answer": text}


def read_yes_no(raw: str) -> bool | None:
    """Blank or y/yes is yes, n/no is no (any case); None means ask again."""
    text = raw.strip().casefold()
    if text in ("", "y", "yes"):
        return True
    if text in ("n", "no"):
        return False
    return None


def format_ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "a moment ago"
    if minutes < 60:
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    hours = minutes // 60
    return "1 hour ago" if hours == 1 else f"{hours} hours ago"


def greeting(due: int, new: int) -> str:
    if due == 1:
        return "Welcome back — 1 idea is ebbing. Reviewing those first."
    if due > 1:
        return f"Welcome back — {due} ideas are ebbing. Reviewing those first."
    if new > 0:
        return "Nothing is due. Let's learn something new."
    return "You've learned every concept here. Finding more advanced ones…"


def resume_question(payload: dict[str, Any], ago: str) -> str:
    return (
        f"You stopped on {payload['concept']}, question {payload['number']}, {ago}. Resume? [Y/n]"
    )


def caught_up_line(next_review: datetime | None, tz: ZoneInfo) -> str:
    if next_review is None:
        return "You're all caught up."
    return f"You're all caught up — next review {format_day(next_review, tz)}."


def next_review_line(concepts: Sequence[Concept], now: datetime, tz: ZoneInfo) -> str | None:
    """D4's line: the local day of the soonest review and how many concepts fall on it; an
    overdue concept counts as today. None when nothing is scheduled."""
    days = [
        max(c.next_review_at, now).astimezone(tz).date()
        for c in concepts
        if c.stage >= 1 and c.next_review_at is not None
    ]
    if not days:
        return None
    soonest = min(days)
    n = days.count(soonest)
    return f"Next review: {soonest.strftime('%A')} · {n} concept{'' if n == 1 else 's'}"


def status_table(concepts: Sequence[Concept], now: datetime, tz: ZoneInfo) -> Table:
    table = Table(title="Memory", title_justify="left")
    table.add_column("Concept")
    table.add_column("Tide", justify="right")
    table.add_column("Stage")
    table.add_column("Next review")
    for concept in concepts:
        table.add_row(
            escape(concept.name),
            format_tide(tide(concept, now)),
            stage_dots(concept.stage),
            due_label(concept, now, tz),
        )
    return table


GRADE_NAME = {"correct": "Correct", "partial": "Partial", "incorrect": "Incorrect"}
DISPUTE_REASONS = [
    ("correct", "My answer is correct"),
    ("ambiguous", "The question is ambiguous"),
    ("notes_differ", "My notes say something different"),
    ("other", "Something else"),
]
NOT_DISPUTABLE = "Only short-answer grades from this session can be disputed."


def render_citations(
    console: Console, citations: Sequence[dict[str, Any]], general_knowledge: bool
) -> None:
    """The quoted sources under a grade or lesson, or the general-knowledge label."""
    if not citations:
        if general_knowledge:
            console.print("[dim]General knowledge · not from your notes[/dim]")
        return
    number = 0
    for heading, web in (("From your notes", False), ("From the web", True)):
        shown = [c for c in citations if (c["kind"] == "web") == web]
        if not shown:
            continue
        console.print(f"\n[bold]{heading}[/bold]")
        for citation in shown:
            number += 1
            console.print(f"  [{number}] {escape(citation['label'])}")
            console.print(f"      “{escape(citation['quote'])}”")


def render_feedback(
    console: Console,
    grade: dict[str, Any] | None,
    lesson: dict[str, Any] | None,
) -> None:
    if grade:
        style = GRADE_STYLE[grade["value"]]
        explanation = escape(grade["explanation"])
        console.print(f"[bold {style}]{GRADE_MARK[grade['value']]}[/]  {explanation}")
        render_citations(console, grade.get("citations", []), grade.get("general_knowledge", False))
    if lesson:
        console.print(Panel(escape(lesson["text"]), title="Let's fix that", border_style="cyan"))
        render_citations(console, lesson["citations"], False)


def render_notices(console: Console, notices: Sequence[str]) -> None:
    for notice in notices:
        console.print(f"[dim]{escape(notice)}[/dim]")


def render_question(console: Console, payload: dict[str, Any]) -> None:
    question = payload["question"]
    kind = "Multiple choice" if question["type"] == "mcq" else "Short answer"
    lines = [escape(question["prompt"]), ""]
    if question["options"]:
        lines += [f"  {n}. {escape(text)}" for n, text in enumerate(question["options"], start=1)]
        lines.append("")
    if question.get("source_line"):
        lines.insert(1, f"[dim]{escape(question['source_line'])}[/dim]")
    hint = "Type 1-4" if question["options"] else "Type your answer"
    if payload.get("can_dispute"):
        hint += ", d to dispute that grade"
    lines.append(f"[dim]{hint}, or q to stop[/dim]")
    console.print(
        Panel(
            "\n".join(lines).rstrip(),
            title=f"{escape(payload['concept'])} · {kind} · Q{payload['number']}",
            title_align="left",
        )
    )


def dispute_line(result: dict[str, Any]) -> str:
    """The one-line outcome of a re-check, as plain text (escape it before printing)."""
    outcome = result["outcome"]
    if outcome == "refused":
        return NOT_DISPUTABLE
    if outcome == "failed":
        return "Couldn't re-check right now — the grade stands. Try again with d."
    if outcome == "upheld":
        return f"Re-check: the grade stands — {result['explanation']}"
    if outcome == "voided":
        return "Re-check: the question was ambiguous — that answer no longer counts."
    new = result["new_grade"]
    raised = GRADE_RANK[new] > GRADE_RANK[result["original_grade"]]
    verb, link = ("raised to", "back to") if raised else ("lowered to", "now")
    level = f"{stage_dots(result['stage'])} level {result['stage']}"
    return f"Re-check: {verb} {GRADE_NAME[new]} — {result['concept']} {link} {level}."


def render_turn(console: Console, payload: dict[str, Any]) -> None:
    result = payload.get("dispute_result")
    if result:
        # The grade and lesson were shown before the dispute; only the verdict is new, and
        # the notices are the re-check's own.
        console.print(escape(dispute_line(result)))
        render_citations(console, result.get("citations", []), False)
    else:
        render_feedback(console, payload["feedback"], payload["lesson"])
    render_notices(console, payload["notices"])
    render_question(console, payload)


def render_summary(console: Console, final: dict[str, Any], tz: ZoneInfo) -> None:
    render_feedback(console, final.get("grade"), final.get("lesson"))
    render_notices(console, final.get("notices", []))
    results = final.get("results", [])
    if not results:
        console.print("No answers this session. Your memory levels are unchanged.")
        return
    table = Table(title="Session summary", title_justify="left")
    table.add_column("Concept")
    table.add_column("Answers")
    table.add_column("Tide", justify="right")
    table.add_column("Stage")
    table.add_column("Next review")
    for row in results:
        tally = row["tally"]
        answers = f"{tally['correct']}✓ {tally['partial']}◐ {tally['incorrect']}✗"
        next_review = format_day(datetime.fromisoformat(row["next_review_at"]), tz)
        table.add_row(
            escape(row["concept"]),
            answers,
            f"{format_tide(row['tide_before'])} → {format_tide(row['tide_after'])}",
            f"{stage_dots(row['stage_before'])} → {stage_dots(row['stage_after'])}",
            next_review,
        )
    console.print(table)


PRIVACY_LINE = (
    "Don't upload sensitive documents. "
    "Files are processed by an AI model to write questions and cite answers."
)


def pages_prefix(page_count: int | None) -> str:
    return "" if page_count is None else f"{page_count} pages · "


def notes_report(
    filename: str,
    page_count: int | None,
    chunk_count: int,
    matched: Sequence[str],
    fresh: Sequence[tuple[str, str]],
) -> list[str]:
    """What adding one file found, as plain lines (escape them before printing)."""
    lines = [f"{filename} · {pages_prefix(page_count)}{chunk_count} passages"]
    summary = f"{len(matched) + len(fresh)} concepts in these notes — "
    if matched:
        summary += f"{len(matched)} already here ({', '.join(matched)}), "
    lines.append(f"{summary}{len(fresh)} new:")
    lines.extend(f"  {n}. {name} — {text}" for n, (name, text) in enumerate(fresh, start=1))
    return lines


def source_lines(documents: Sequence[Document]) -> list[str]:
    """One line per stored file for `ebb status`."""
    return [
        f"{d.filename} · {pages_prefix(d.page_count)}{format_megabytes(d.size_bytes)}"
        for d in documents
    ]
