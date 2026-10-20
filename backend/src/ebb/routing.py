"""Every routing decision in the quiz loop, as pure functions over graph state."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from ebb.domain import Concept
from ebb.mastery import is_due, tide

STREAK_TO_FINISH = 2
MAX_QUESTIONS_PER_CONCEPT = 4
MAX_GENERATIONS_PER_CONCEPT = 2

NextStep = Literal["ask_question", "generate_questions", "next_concept", "summarize"]


def pick_next_concept(
    concepts: Sequence[Concept], studied_ids: Sequence[str], now: datetime
) -> Concept | None:
    """Due reviews by lowest tide, then new concepts in list order; never a learned, not-due one."""
    candidates = [c for c in concepts if str(c.id) not in studied_ids]
    due = [c for c in candidates if c.stage >= 1 and is_due(c, now)]
    if due:
        # min() keeps the first of equal keys, so ties stay in list order.
        return min(due, key=lambda c: _tide_or_zero(c, now))
    return next((c for c in candidates if c.stage == 0), None)


def _tide_or_zero(concept: Concept, now: datetime) -> float:
    value = tide(concept, now)
    return 0.0 if value is None else value


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


def decide_next(state: Mapping[str, Any]) -> NextStep:
    if concept_done(state):
        return "next_concept"
    if state["batch"]:
        return "ask_question"
    if state["generation_calls_on_concept"] < MAX_GENERATIONS_PER_CONCEPT:
        return "generate_questions"
    return "next_concept"


def after_ask(state: Mapping[str, Any]) -> Literal["grade_answer", "summarize"]:
    return "summarize" if state["quit"] else "grade_answer"


def after_grade(state: Mapping[str, Any]) -> Literal["update_mastery", "ask_question"]:
    """A failed grading call leaves `grade` empty and re-asks the same question."""
    return "ask_question" if state["grade"] is None else "update_mastery"


def after_mastery(state: Mapping[str, Any]) -> NextStep | Literal["teach"]:
    if state["grade"] is None:
        return decide_next(state)
    if state["grade"]["value"] == "incorrect":
        return "teach"
    return decide_next(state)


def after_generate(state: Mapping[str, Any]) -> Literal["verify_citations", "summarize"]:
    return "summarize" if state["fatal"] else "verify_citations"


def after_verify(state: Mapping[str, Any], web: bool) -> NextStep | Literal["search_web"]:
    """Failures left in state mean the candidates go back for another try. Repeats dropped
    from a notes batch are replaced from the web, if `web` search is set up and this
    session hasn't searched for this concept or seen a search fail."""
    if state["citation_failures"]:
        return "generate_questions"
    if (
        web
        and state.get("generation_mode", "notes") == "notes"
        and state.get("replacements_needed", 0) > 0
        and state["concept"]["id"] not in state.get("web_searched", [])
        and not state.get("web_failed", False)
    ):
        return "search_web"
    if state["batch"]:
        return "ask_question"
    return decide_next(state)


def after_search(state: Mapping[str, Any]) -> NextStep:
    """Pages found switch generation to web mode; otherwise the batch goes on without them."""
    if state.get("generation_mode", "notes") == "web":
        return "generate_questions"
    if state["batch"]:
        return "ask_question"
    return decide_next(state)


def after_pick(
    state: Mapping[str, Any],
) -> Literal["retrieve_notes", "extend_subject", "summarize"]:
    if state["concept"] is not None:
        return "retrieve_notes"
    return "summarize" if state["caught_up"] else "extend_subject"


def after_extend(state: Mapping[str, Any]) -> Literal["pick_concept", "summarize"]:
    return "summarize" if state["caught_up"] or state["fatal"] else "pick_concept"
