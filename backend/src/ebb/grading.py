"""Multiple-choice grading in code (review decision E4)."""

from collections.abc import Sequence

from ebb.domain import Grade, GradeValue, Question


def parse_choice(options: Sequence[str], answer: str) -> int | None:
    """Map an answer to a 0-based option index, or None if it matches nothing.

    A number from 1 to len(options) always selects by position (the prompt says
    "Type 1-4"); anything else is matched against the option texts, so "62" still
    selects an option whose text is "62".
    """
    text = answer.strip()
    if text.isdigit() and 1 <= int(text) <= len(options):
        return int(text) - 1
    folded = text.casefold()
    for index, option in enumerate(options):
        if option.strip().casefold() == folded:
            return index
    return None


def grade_mcq(question: Question, answer: str) -> Grade:
    index = parse_choice([o.text for o in question.options], answer)
    if index is None:
        raise ValueError(f"not a valid choice: {answer!r}")
    picked = question.options[index]
    if picked.is_correct:
        return Grade(GradeValue.CORRECT, picked.why)
    correct = next(o for o in question.options if o.is_correct)
    return Grade(
        GradeValue.INCORRECT,
        f"{picked.why} The answer is “{correct.text}”: {correct.why}",
        misconception=picked.why,
    )
