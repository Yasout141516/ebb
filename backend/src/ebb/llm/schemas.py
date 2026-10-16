"""Structured-output schemas for LLM calls.

Limits are enforced by validators rather than Field constraints so the JSON schema
sent to providers stays simple; a validation failure triggers a retry.
"""

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class ConceptDraft(BaseModel):
    name: str
    description: str

    @field_validator("name")
    @classmethod
    def short_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 60:
            raise ValueError("concept name must be 1-60 characters")
        return value


class ConceptList(BaseModel):
    concepts: list[ConceptDraft]

    @field_validator("concepts")
    @classmethod
    def six_to_ten(cls, value: list[ConceptDraft]) -> list[ConceptDraft]:
        if not 6 <= len(value) <= 10:
            raise ValueError(f"expected 6-10 concepts, got {len(value)}")
        return value


class ConceptExtension(BaseModel):
    concepts: list[ConceptDraft]

    @field_validator("concepts")
    @classmethod
    def three_to_five(cls, value: list[ConceptDraft]) -> list[ConceptDraft]:
        if not 3 <= len(value) <= 5:
            raise ValueError(f"expected 3-5 concepts, got {len(value)}")
        return value


class OptionDraft(BaseModel):
    text: str
    is_correct: bool
    why: str


class QuestionDraft(BaseModel):
    type: Literal["mcq", "short"]
    difficulty: int
    prompt: str
    reference_answer: str
    options: list[OptionDraft]

    @model_validator(mode="after")
    def well_formed(self) -> QuestionDraft:
        if self.difficulty not in (1, 2, 3):
            raise ValueError("difficulty must be 1, 2 or 3")
        if self.type == "mcq":
            if len(self.options) != 4:
                raise ValueError("a multiple-choice question needs exactly 4 options")
            if sum(o.is_correct for o in self.options) != 1:
                raise ValueError("a multiple-choice question needs exactly one correct option")
        elif self.options:
            raise ValueError("a short-answer question has no options")
        return self


class QuestionBatch(BaseModel):
    questions: list[QuestionDraft]

    @model_validator(mode="after")
    def balanced(self) -> QuestionBatch:
        kinds = [q.type for q in self.questions]
        if len(kinds) != 5:
            raise ValueError(f"expected 5 questions, got {len(kinds)}")
        if kinds.count("mcq") < 2 or kinds.count("short") < 1:
            raise ValueError("need at least 2 multiple-choice and 1 short-answer question")
        if {q.difficulty for q in self.questions} != {1, 2, 3}:
            raise ValueError("difficulties must cover 1, 2 and 3")
        return self


class GradeResult(BaseModel):
    grade: Literal["correct", "partial", "incorrect"]
    explanation: str
    misconception: str | None = None
