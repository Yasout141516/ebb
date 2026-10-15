"""The only place that talks to an LLM. Graph nodes see the `Tutor` protocol."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from string import Template
from typing import Any, Protocol
from uuid import uuid4

import google.genai.errors
import groq
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_google_genai._common import GoogleGenerativeAIError
from pydantic import ValidationError

from ebb.config import ConfigError, Settings
from ebb.domain import Concept, Grade, GradeValue, Option, Question
from ebb.llm.schemas import ConceptDraft, ConceptList, GradeResult, QuestionBatch

PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    GoogleGenerativeAIError,
    google.genai.errors.APIError,
    groq.APIError,
    OutputParserException,
    ValidationError,
)


class AllProvidersExhausted(Exception):
    """Every provider failed or returned invalid output after retries."""


@dataclass(frozen=True)
class Generated[T]:
    value: T
    model: str
    fell_back: bool = False


class Tutor(Protocol):
    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]: ...

    def generate_questions(
        self, concept: Concept, avoid: list[str]
    ) -> Generated[list[Question]]: ...

    def grade(self, question: Question, answer: str) -> Generated[Grade]: ...

    def teach(self, question: Question, answer: str, grade: Grade) -> Generated[str]: ...


@dataclass(frozen=True)
class Provider:
    name: str
    precise: BaseChatModel  # low temperature: grading
    creative: BaseChatModel  # higher temperature: generating and teaching


def _prompt(template: str, /, **values: str) -> str:
    text = files("ebb.llm").joinpath("prompts", f"{template}.md").read_text(encoding="utf-8")
    return Template(text).substitute(values)


def correct_text(question: Question) -> str:
    if question.type == "mcq":
        return next(o.text for o in question.options if o.is_correct)
    return question.reference_answer


class LangChainTutor:
    def __init__(self, providers: list[Provider], attempts: int = 3) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = providers
        self.attempts = attempts

    @classmethod
    def from_settings(cls, settings: Settings) -> LangChainTutor:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_groq import ChatGroq

        if not settings.google_api_key:
            raise ConfigError("GOOGLE_API_KEY is not set (get one free at aistudio.google.com)")

        def gemini(temperature: float) -> BaseChatModel:
            return ChatGoogleGenerativeAI(
                model=settings.ebb_primary_model,
                google_api_key=settings.google_api_key,
                temperature=temperature,
                max_retries=1,
            )

        providers = [Provider(settings.ebb_primary_model, gemini(0.2), gemini(0.7))]
        if settings.groq_api_key:

            def groq_model(temperature: float) -> BaseChatModel:
                return ChatGroq(
                    model_name=settings.ebb_fallback_model,
                    groq_api_key=settings.groq_api_key,
                    temperature=temperature,
                    max_retries=1,
                )

            providers.append(
                Provider(settings.ebb_fallback_model, groq_model(0.2), groq_model(0.7))
            )
        return cls(providers)

    def _run(
        self, build: Callable[[BaseChatModel], Runnable], prompt: str, creative: bool
    ) -> Generated[Any]:
        last_error: BaseException | None = None
        for index, provider in enumerate(self.providers):
            model = provider.creative if creative else provider.precise
            chain = build(model).with_retry(
                retry_if_exception_type=PROVIDER_ERRORS, stop_after_attempt=self.attempts
            )
            try:
                return Generated(chain.invoke(prompt), provider.name, fell_back=index > 0)
            except PROVIDER_ERRORS as error:
                last_error = error
        raise AllProvidersExhausted(str(last_error)) from last_error

    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]:
        result = self._run(
            lambda m: m.with_structured_output(ConceptList),
            _prompt("extract_concepts", topic=topic),
            creative=True,
        )
        return Generated(result.value.concepts, result.model, result.fell_back)

    def generate_questions(self, concept: Concept, avoid: list[str]) -> Generated[list[Question]]:
        avoid_text = "\n".join(f"- {p}" for p in avoid) or "(none)"
        result = self._run(
            lambda m: m.with_structured_output(QuestionBatch),
            _prompt(
                "generate_questions",
                name=concept.name,
                description=concept.description,
                avoid=avoid_text,
            ),
            creative=True,
        )
        questions = [
            Question(
                id=uuid4(),
                concept_id=concept.id,
                type=draft.type,
                difficulty=draft.difficulty,
                prompt=draft.prompt,
                reference_answer=draft.reference_answer,
                options=tuple(Option(o.text, o.is_correct, o.why) for o in draft.options),
            )
            for draft in result.value.questions
        ]
        return Generated(questions, result.model, result.fell_back)

    def grade(self, question: Question, answer: str) -> Generated[Grade]:
        result = self._run(
            lambda m: m.with_structured_output(GradeResult),
            _prompt(
                "grade",
                prompt=question.prompt,
                reference=question.reference_answer,
                answer=answer,
            ),
            creative=False,
        )
        value: GradeResult = result.value
        grade = Grade(GradeValue(value.grade), value.explanation, value.misconception)
        return Generated(grade, result.model, result.fell_back)

    def teach(self, question: Question, answer: str, grade: Grade) -> Generated[str]:
        result = self._run(
            lambda m: m,
            _prompt(
                "teach",
                prompt=question.prompt,
                reference=correct_text(question),
                answer=answer,
                explanation=grade.explanation,
            ),
            creative=True,
        )
        return Generated(result.value.text.strip(), result.model, result.fell_back)
