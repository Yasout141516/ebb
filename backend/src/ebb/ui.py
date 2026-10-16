"""Terminal rendering and input parsing. Formatting helpers are pure and tested."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ebb.domain import Concept
from ebb.grading import parse_choice
from ebb.mastery import MAX_STAGE, is_due, tide

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


def read_reply(raw: str, options: Sequence[str]) -> dict[str, Any] | None:
    """Turn one line of input into a resume value, or None if it should be asked again."""
    text = raw.strip()
    if text.casefold() == "q":
        return {"quit": True}
    if not text:
        return None
    if options and parse_choice(options, text) is None:
        return None
    return {"answer": text}


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


def render_feedback(console: Console, grade: dict[str, Any] | None, lesson: str | None) -> None:
    if grade:
        style = GRADE_STYLE[grade["value"]]
        explanation = escape(grade["explanation"])
        console.print(f"[bold {style}]{GRADE_MARK[grade['value']]}[/]  {explanation}")
    if lesson:
        console.print(Panel(escape(lesson), title="Let's fix that", border_style="cyan"))


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
    hint = "Type 1-4" if question["options"] else "Type your answer"
    lines.append(f"[dim]{hint}, or q to stop[/dim]")
    console.print(
        Panel(
            "\n".join(lines).rstrip(),
            title=f"{escape(payload['concept'])} · {kind} · Q{payload['number']}",
            title_align="left",
        )
    )


def render_turn(console: Console, payload: dict[str, Any]) -> None:
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
