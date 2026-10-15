"""The only place that talks to an LLM. Graph nodes see the `Tutor` protocol."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from string import Template
from typing import Any, Literal, Protocol
from uuid import uuid4

import google.genai.errors
import groq
import httpx
from langchain_core.exceptions import (
    ModelAuthenticationError,
    ModelNotFoundError,
    ModelPermissionDeniedError,
    OutputParserException,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_google_genai._common import GoogleGenerativeAIError
from langchain_google_genai.chat_models import GoogleInvalidRequestError
from pydantic import ValidationError

from ebb.config import ConfigError, Settings
from ebb.domain import Concept, Grade, GradeValue, Option, Question
from ebb.llm.schemas import ConceptDraft, ConceptList, GradeResult, QuestionBatch

try:  # google-genai uses httpx2 when it is installed (it arrives via langsmith today)
    import httpx2

    _TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
        httpx.TransportError,
        httpx2.TransportError,
    )
except ImportError:
    _TRANSPORT_ERRORS = (httpx.TransportError,)

# Errors a retry or a backup model cannot fix: the key or the model name is wrong.
_AUTH_ERRORS: tuple[type[BaseException], ...] = (
    ModelAuthenticationError,
    ModelPermissionDeniedError,
    groq.AuthenticationError,
    groq.PermissionDeniedError,
)
_NOT_FOUND_ERRORS: tuple[type[BaseException], ...] = (ModelNotFoundError, groq.NotFoundError)
# Gemini reports a malformed key as 400 INVALID_ARGUMENT rather than 401.
_BAD_KEY_MARKERS = ("api key not valid", "api_key_invalid")

PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    GoogleGenerativeAIError,
    google.genai.errors.APIError,
    groq.APIError,
    OutputParserException,
    ValidationError,
    *_TRANSPORT_ERRORS,
    *_AUTH_ERRORS,
    *_NOT_FOUND_ERRORS,
)

REQUEST_TIMEOUT_SECONDS = 60

Failure = Literal["retry", "bad_key", "no_model"]


def _classify(error: BaseException) -> Failure:
    """What a provider error means: worth retrying, or a key/model the user must fix."""
    if isinstance(error, _AUTH_ERRORS):
        return "bad_key"
    if isinstance(error, GoogleInvalidRequestError) and any(
        marker in str(error).lower() for marker in _BAD_KEY_MARKERS
    ):
        return "bad_key"
    if isinstance(error, _NOT_FOUND_ERRORS):
        return "no_model"
    return "retry"


def _retryable(error: BaseException) -> bool:
    return isinstance(error, PROVIDER_ERRORS) and _classify(error) == "retry"


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
    service: str = "Gemini"
    key_name: str = "GOOGLE_API_KEY"


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
                request_timeout=REQUEST_TIMEOUT_SECONDS,
            )

        providers = [Provider(settings.ebb_primary_model, gemini(0.2), gemini(0.7))]
        if settings.groq_api_key:

            def groq_model(temperature: float) -> BaseChatModel:
                return ChatGroq(
                    model_name=settings.ebb_fallback_model,
                    groq_api_key=settings.groq_api_key,
                    temperature=temperature,
                    max_retries=1,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )

            providers.append(
                Provider(
                    settings.ebb_fallback_model,
                    groq_model(0.2),
                    groq_model(0.7),
                    service="Groq",
                    key_name="GROQ_API_KEY",
                )
            )
        return cls(providers)

    def _run[T](
        self,
        build: Callable[[BaseChatModel], Runnable],
        prompt: str,
        creative: bool,
        parse: Callable[[Any], T],
    ) -> Generated[T]:
        last_error: BaseException | None = None
        for index, provider in enumerate(self.providers):
            model = provider.creative if creative else provider.precise
            chain = build(model).with_retry(
                retry_if_exception_type=_retryable, stop_after_attempt=self.attempts
            )
            try:
                output = chain.invoke(prompt)
            except PROVIDER_ERRORS as error:
                match _classify(error):
                    case "bad_key":
                        raise ConfigError(
                            f"{provider.service} rejected {provider.key_name} — "
                            "check the key in .env"
                        ) from error
                    case "no_model":
                        raise ConfigError(
                            f"Model {provider.name!r} was not found — set EBB_PRIMARY_MODEL "
                            "(or EBB_FALLBACK_MODEL) in .env"
                        ) from error
                last_error = error
                continue
            return Generated(parse(output), provider.name, fell_back=index > 0)
        raise AllProvidersExhausted(str(last_error)) from last_error

    def extract_concepts(self, topic: str) -> Generated[list[ConceptDraft]]:
        return self._run(
            lambda m: m.with_structured_output(ConceptList),
            _prompt("extract_concepts", topic=topic),
            creative=True,
            parse=lambda out: out.concepts,
        )

    def generate_questions(self, concept: Concept, avoid: list[str]) -> Generated[list[Question]]:
        avoid_text = "\n".join(f"- {p}" for p in avoid) or "(none)"

        def to_questions(batch: QuestionBatch) -> list[Question]:
            return [
                Question(
                    id=uuid4(),
                    concept_id=concept.id,
                    type=draft.type,
                    difficulty=draft.difficulty,
                    prompt=draft.prompt,
                    reference_answer=draft.reference_answer,
                    options=tuple(Option(**o.model_dump()) for o in draft.options),
                )
                for draft in batch.questions
            ]

        return self._run(
            lambda m: m.with_structured_output(QuestionBatch),
            _prompt(
                "generate_questions",
                name=concept.name,
                description=concept.description,
                avoid=avoid_text,
            ),
            creative=True,
            parse=to_questions,
        )

    def grade(self, question: Question, answer: str) -> Generated[Grade]:
        return self._run(
            lambda m: m.with_structured_output(GradeResult),
            _prompt(
                "grade",
                prompt=question.prompt,
                reference=question.reference_answer,
                answer=answer,
            ),
            creative=False,
            parse=lambda out: Grade(GradeValue(out.grade), out.explanation, out.misconception),
        )

    def teach(self, question: Question, answer: str, grade: Grade) -> Generated[str]:
        return self._run(
            lambda m: m,
            _prompt(
                "teach",
                prompt=question.prompt,
                reference=correct_text(question),
                answer=answer,
                explanation=grade.explanation,
            ),
            creative=True,
            parse=lambda out: out.text.strip(),
        )
