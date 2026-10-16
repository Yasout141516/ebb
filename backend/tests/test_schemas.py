import pytest
from pydantic import ValidationError

from ebb.llm.schemas import ConceptExtension, ConceptList, QuestionBatch


def mcq(difficulty=1, correct=1, count=4):
    return {
        "type": "mcq",
        "difficulty": difficulty,
        "prompt": "p",
        "reference_answer": "a",
        "options": [{"text": f"o{n}", "is_correct": n < correct, "why": "w"} for n in range(count)],
    }


def short(difficulty=1):
    return {
        "type": "short",
        "difficulty": difficulty,
        "prompt": "p",
        "reference_answer": "a",
        "options": [],
    }


def test_valid_batch():
    batch = QuestionBatch.model_validate(
        {"questions": [mcq(1), mcq(2), short(3), mcq(3), short(2)]}
    )
    assert len(batch.questions) == 5


@pytest.mark.parametrize(
    "questions",
    [
        [mcq(1), mcq(2), short(3), mcq(3)],  # only 4
        [mcq(1), mcq(2), mcq(3), mcq(1), mcq(2)],  # no short answer
        [mcq(1), short(2), short(3), short(1), short(2)],  # one mcq
        [mcq(1), mcq(1), short(1), mcq(2), short(2)],  # no difficulty 3
        [mcq(1, count=3), mcq(2), short(3), mcq(3), short(2)],  # 3 options
        [mcq(1, correct=2), mcq(2), short(3), mcq(3), short(2)],  # two correct
        [mcq(1, correct=0), mcq(2), short(3), mcq(3), short(2)],  # none correct
        [mcq(4), mcq(2), short(3), mcq(3), short(1)],  # difficulty out of range
        [mcq(1), mcq(2), {**short(3), "options": mcq()["options"]}, mcq(3), short(2)],
    ],
)
def test_invalid_batches_are_rejected(questions):
    with pytest.raises(ValidationError):
        QuestionBatch.model_validate({"questions": questions})


def concepts(n, name="DNS"):
    return {"concepts": [{"name": name, "description": "d"} for _ in range(n)]}


def test_concept_list_needs_six_to_ten():
    assert len(ConceptList.model_validate(concepts(6)).concepts) == 6
    for bad in (5, 11):
        with pytest.raises(ValidationError):
            ConceptList.model_validate(concepts(bad))


def test_concept_names_are_short_and_non_empty():
    for bad in ("", "x" * 61):
        with pytest.raises(ValidationError):
            ConceptList.model_validate(concepts(6, name=bad))


def test_concept_extension_needs_three_to_five():
    for good in (3, 5):
        assert len(ConceptExtension.model_validate(concepts(good)).concepts) == good
    for bad in (2, 6):
        with pytest.raises(ValidationError):
            ConceptExtension.model_validate(concepts(bad))
