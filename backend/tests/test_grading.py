from uuid import uuid4

import pytest

from ebb.domain import DisputeOutcome, DisputeReason, GradeValue, Option, Question
from ebb.grading import dispute_outcome, grade_mcq, parse_choice

OPTIONS = ["64", "62", "30", "126"]
QUESTION = Question(
    uuid4(),
    uuid4(),
    "mcq",
    2,
    "Usable hosts in a /26?",
    "62",
    (
        Option("64", False, "64 counts the network and broadcast addresses."),
        Option("62", True, "2^6 - 2 = 62 usable hosts."),
        Option("30", False, "30 is a /27."),
        Option("126", False, "126 is a /25."),
    ),
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("1", 0),
        (" 4 ", 3),
        ("62", 1),
        ("126", 3),
        ("0", None),
        ("5", None),
        ("", None),
        ("abc", None),
        ("²", None),
    ],
)
def test_parse_choice(answer, expected):
    assert parse_choice(OPTIONS, answer) == expected


def test_parse_choice_matches_text_case_insensitively():
    assert parse_choice(["TCP", "UDP"], "udp") == 1


def test_correct_choice_by_number():
    grade = grade_mcq(QUESTION, "2")
    assert grade.value is GradeValue.CORRECT
    assert grade.misconception is None


def test_wrong_choice_explains_and_names_the_right_answer():
    grade = grade_mcq(QUESTION, "1")
    assert grade.value is GradeValue.INCORRECT
    assert "network and broadcast" in grade.explanation
    assert "“62”" in grade.explanation
    assert grade.misconception == "64 counts the network and broadcast addresses."


def test_invalid_choice_raises():
    with pytest.raises(ValueError):
        grade_mcq(QUESTION, "7")


_R, _G, _O = DisputeReason, GradeValue, DisputeOutcome


@pytest.mark.parametrize(
    ("reason", "orig", "new", "amb", "outcome"),
    [
        (_R.AMBIGUOUS, _G.INCORRECT, _G.INCORRECT, True, _O.VOIDED),
        (_R.AMBIGUOUS, _G.INCORRECT, _G.INCORRECT, False, _O.UPHELD),
        (_R.CORRECT, _G.PARTIAL, _G.CORRECT, False, _O.CHANGED),
        (_R.OTHER, _G.CORRECT, _G.PARTIAL, False, _O.CHANGED),
        (_R.NOTES_DIFFER, _G.PARTIAL, _G.PARTIAL, True, _O.UPHELD),
    ],
)
def test_dispute_outcome(reason, orig, new, amb, outcome):
    assert dispute_outcome(reason, orig, new, amb) == outcome
