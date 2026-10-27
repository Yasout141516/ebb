from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ebb.domain import Concept, GradeValue
from ebb.mastery import INTERVAL_DAYS, apply_grade, is_due, reviews_due, tide

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
OK, PART, BAD = GradeValue.CORRECT, GradeValue.PARTIAL, GradeValue.INCORRECT


def concept(stage: int, due: bool) -> Concept:
    if stage == 0:
        return Concept(uuid4(), uuid4(), "DNS", "How names resolve.")
    reviewed = NOW - timedelta(days=INTERVAL_DAYS[stage] + (1 if due else -0.5))
    return Concept(
        uuid4(),
        uuid4(),
        "DNS",
        "How names resolve.",
        stage=stage,
        last_reviewed_at=reviewed,
        next_review_at=reviewed + timedelta(days=INTERVAL_DAYS[stage]),
        review_count=3,
    )


# (stage, due, grade, expected stage, schedule changes)
CASES = [
    (0, True, OK, 1, True),
    (0, True, PART, 1, True),
    (0, True, BAD, 1, True),
    *[(s, True, OK, min(s + 1, 5), True) for s in range(1, 6)],
    *[(s, True, PART, s, True) for s in range(1, 6)],
    *[(s, True, BAD, 1, True) for s in range(1, 6)],
    *[(s, False, OK, s, False) for s in range(1, 6)],
    *[(s, False, PART, s, False) for s in range(1, 6)],
    *[(s, False, BAD, 1, True) for s in range(1, 6)],
]


@pytest.mark.parametrize(("stage", "due", "grade", "expected", "changes"), CASES)
def test_leitner_rules(stage, due, grade, expected, changes):
    before = concept(stage, due)
    assert is_due(before, NOW) is due
    after = apply_grade(before, grade, NOW)
    assert after.stage == expected
    if changes:
        assert after.last_reviewed_at == NOW
        assert after.next_review_at == NOW + timedelta(days=INTERVAL_DAYS[expected])
        assert after.review_count == before.review_count + 1
    else:
        assert after == before


@pytest.mark.parametrize(("stage", "due", "grade", "expected", "changes"), CASES)
def test_schedule_invariant_holds(stage, due, grade, expected, changes):
    after = apply_grade(concept(stage, due), grade, NOW)
    assert after.next_review_at == after.last_reviewed_at + timedelta(
        days=INTERVAL_DAYS[after.stage]
    )


def test_tide_is_none_for_new_concepts():
    assert tide(concept(0, True), NOW) is None


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
def test_tide_is_full_after_review_and_70_percent_when_due(stage):
    reviewed = apply_grade(concept(stage, True), OK, NOW)
    assert tide(reviewed, NOW) == pytest.approx(1.0)
    assert tide(reviewed, reviewed.next_review_at) == pytest.approx(0.7)


def test_tide_strictly_decreases():
    reviewed = apply_grade(concept(3, True), OK, NOW)
    levels = [tide(reviewed, NOW + timedelta(days=d)) for d in range(0, 40, 3)]
    assert all(a > b for a, b in zip(levels, levels[1:], strict=False))


def test_tide_never_exceeds_one_with_clock_skew():
    reviewed = apply_grade(concept(2, True), OK, NOW)
    assert tide(reviewed, NOW - timedelta(hours=1)) == pytest.approx(1.0)


def test_reviews_due_includes_exactly_now():
    c = replace(concept(2, due=True), next_review_at=NOW)
    assert reviews_due([c], NOW) == [c]


def test_reviews_due_excludes_new_concepts():
    new, waiting, due = concept(0, due=False), concept(2, due=False), concept(3, due=True)
    assert reviews_due([new, waiting, due], NOW) == [due]
