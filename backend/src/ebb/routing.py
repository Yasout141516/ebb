"""Every routing decision in the quiz loop, as pure functions over graph state."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from ebb.domain import Concept
from ebb.mastery import is_due

STREAK_TO_FINISH = 2
MAX_QUESTIONS_PER_CONCEPT = 4
CONCEPTS_PER_SESSION = 3
MAX_GENERATIONS_PER_CONCEPT = 2

NextStep = Literal["ask_question", "generate_questions", "next_concept", "summarize"]

_NEVER = datetime.min.replace(tzinfo=UTC)


def pick_next_concept(
    concepts: Sequence[Concept], studied_ids: Sequence[str], now: datetime
) -> Concept | None:
    """Due first, then lowest stage, then least recently reviewed (never-reviewed first)."""
    candidates = [c for c in concepts if str(c.id) not in studied_ids]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (not is_due(c, now), c.stage, c.last_reviewed_at or _NEVER),
    )


def choose_question(
    batch: Sequence[Mapping[str, Any]],
    difficulty: int | None,
    last_grade: str | None,
    first: bool,
) -> int:
    """Index into `batch` of the next question to ask. `batch` must not be empty."""
    levels = [q["difficulty"] for q in batch]
    if first or difficulty is None:
        return levels.index(min(levels))
    if last_grade == "correct":
        harder = [d for d in levels if d > difficulty]
        if harder:
            return levels.index(min(harder))
    if difficulty in levels:
        return levels.index(difficulty)
    return min(range(len(levels)), key=lambda i: abs(levels[i] - difficulty))


def concept_done(state: Mapping[str, Any]) -> bool:
    return (
        state["correct_streak"] >= STREAK_TO_FINISH
        or state["asked_on_concept"] >= MAX_QUESTIONS_PER_CONCEPT
    )


def _finish_concept(state: Mapping[str, Any]) -> NextStep:
    last_allowed = state["concepts_done"] + 1 >= CONCEPTS_PER_SESSION
    none_left = len(state["studied_concept_ids"]) >= state["concept_count"]
    return "summarize" if last_allowed or none_left else "next_concept"


def decide_next(state: Mapping[str, Any]) -> NextStep:
    if concept_done(state):
        return _finish_concept(state)
    if state["batch"]:
        return "ask_question"
    if state["generation_calls_on_concept"] < MAX_GENERATIONS_PER_CONCEPT:
        return "generate_questions"
    return _finish_concept(state)


def after_ask(state: Mapping[str, Any]) -> Literal["grade_answer", "summarize"]:
    return "summarize" if state["quit"] else "grade_answer"


def after_grade(state: Mapping[str, Any]) -> Literal["update_mastery", "ask_question"]:
    """A failed grading call leaves `grade` empty and re-asks the same question."""
    return "ask_question" if state["grade"] is None else "update_mastery"


def after_mastery(state: Mapping[str, Any]) -> NextStep | Literal["teach"]:
    if state["grade"]["value"] == "incorrect":
        return "teach"
    return decide_next(state)


def after_generate(state: Mapping[str, Any]) -> Literal["ask_question", "summarize"]:
    return "summarize" if state["fatal"] else "ask_question"


def after_pick(state: Mapping[str, Any]) -> Literal["generate_questions", "summarize"]:
    return "summarize" if state["concept"] is None else "generate_questions"
