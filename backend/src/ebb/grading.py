"""Multiple-choice grading in code (review decision E4)."""

from collections.abc import Sequence
from dataclasses import replace

from ebb.domain import DisputeOutcome, DisputeReason, Grade, GradeValue, Question

# Grades from worst to best. GradeValue is a StrEnum, so "correct" looks up CORRECT too.
GRADE_RANK = {GradeValue.INCORRECT: 0, GradeValue.PARTIAL: 1, GradeValue.CORRECT: 2}


def parse_choice(options: Sequence[str], answer: str) -> int | None:
    """Map an answer to a 0-based option index, or None if it matches nothing.

    A number from 1 to len(options) always selects by position (the prompt says
    "Type 1-4"); anything else is matched against the option texts, so "62" still
    selects an option whose text is "62".
    """
    text = answer.strip()
    if text.isascii() and text.isdigit() and 1 <= int(text) <= len(options):
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


def dispute_outcome(
    reason: DisputeReason,
    original: GradeValue,
    regraded: GradeValue,
    question_ambiguous: bool,
) -> DisputeOutcome:
    """The spec's outcome table: voided only for an ambiguous question the model agrees is."""
    if reason is DisputeReason.AMBIGUOUS and question_ambiguous:
        return DisputeOutcome.VOIDED
    if regraded != original:
        return DisputeOutcome.CHANGED
    return DisputeOutcome.UPHELD


# What models write in place of JSON null when they report no misconception.
_NO_MISCONCEPTION = {"none", "null", "n/a", "no misconception"}


def cap_for_misconception(grade: Grade) -> Grade:
    """A "correct" grade that names a misconception is capped at "partial"."""
    named = grade.misconception is not None and (
        grade.misconception.strip().casefold() not in {"", *_NO_MISCONCEPTION}
    )
    if grade.value is GradeValue.CORRECT and named:
        return replace(grade, value=GradeValue.PARTIAL)
    return grade
