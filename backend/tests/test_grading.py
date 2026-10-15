from uuid import uuid4

import pytest

from ebb.domain import GradeValue, Option, Question
from ebb.grading import grade_mcq, parse_choice

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
