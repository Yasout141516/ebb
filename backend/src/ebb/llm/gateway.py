"""Every model call goes through here: the role's chain, retries and the backup, and one
record per attempt (tokens, time to first token, total time, estimated cost)."""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import groq
import httpx
import openai
import psycopg
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import (
    ModelAuthenticationError,
    ModelNotFoundError,
    ModelPermissionDeniedError,
    OutputParserException,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, ValidationError

from ebb.clock import utc_now
from ebb.config import ConfigError
from ebb.domain import LlmCall
from ebb.llm import prices, request
from ebb.llm.providers import Provider, Role
from ebb.store import Store

log = logging.getLogger(__name__)

# Errors a retry or a backup model cannot fix: the key or the model name is wrong.
_AUTH_ERRORS: tuple[type[BaseException], ...] = (
    ModelAuthenticationError,
    ModelPermissionDeniedError,
    groq.AuthenticationError,
    groq.PermissionDeniedError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
)
_NOT_FOUND_ERRORS: tuple[type[BaseException], ...] = (
    ModelNotFoundError,
    groq.NotFoundError,
    openai.NotFoundError,
)
# A reply that came back but failed validation.
_INVALID_ERRORS: tuple[type[BaseException], ...] = (OutputParserException, ValidationError)

# ChatMistralAI raises httpx.HTTPStatusError for any error response; transport errors
# (timeouts, connection failures) subclass httpx.TransportError.
PROVIDER_ERRORS: tuple[type[BaseException], ...] = (
    groq.APIError,
    openai.APIError,
    httpx.HTTPStatusError,
    httpx.TransportError,
    *_INVALID_ERRORS,
    *_AUTH_ERRORS,
    *_NOT_FOUND_ERRORS,
)

Failure = Literal["retry", "bad_key", "no_model"]

# The kind of work a call does, as the Costs page groups it.
Endpoint = Literal[
    "lesson",
    "learn_answer",
    "write_questions",
    "write_web_questions",
    "rewrite_questions",
    "grade",
    "regrade",
    "reteach",
    "extract_concepts",
    "extend_concepts",
    "concepts_from_notes",
    "write_eval_case",
]


def _classify(error: BaseException) -> Failure:
    """What a provider error means: worth retrying, or a key/model the user must fix."""
    if isinstance(error, _AUTH_ERRORS):
        return "bad_key"
    if isinstance(error, _NOT_FOUND_ERRORS):
        return "no_model"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return "bad_key"
        if status == 404:
            return "no_model"
    return "retry"


def _retryable(error: BaseException) -> bool:
    return isinstance(error, PROVIDER_ERRORS) and _classify(error) == "retry"


EXHAUSTED_NOTICE = "The tutor couldn't respond — your progress is saved. Try again in a minute."
REFUSED_NOTICE = "The tutor won't help with that topic."


class TutorUnavailable(Exception):
    """The tutor can't answer this call; `notice` is what the learner is told."""

    notice: str = EXHAUSTED_NOTICE


class AllProvidersExhausted(TutorUnavailable):
    """Every provider failed or returned invalid output after retries."""


class TutorRefused(TutorUnavailable):
    """A provider refused the request for safety: not retried, and no other provider asked."""

    notice = REFUSED_NOTICE


def _is_refusal(raw: BaseMessage) -> bool:
    """Groq's API ends a reply its content filters withheld with finish_reason
    "content_filter"; OpenAI-style APIs put a model's refusal in `refusal`, which
    langchain_groq doesn't copy into `additional_kwargs` today (checked for other providers
    and later versions)."""
    return (
        bool(raw.additional_kwargs.get("refusal"))
        or raw.response_metadata.get("finish_reason") == "content_filter"
    )


def _unless_refused(raw: BaseMessage) -> BaseMessage:
    if _is_refusal(raw):
        raise TutorRefused()
    return raw


def _parsed(output: dict[str, Any]) -> Any:
    """The structured value from an `include_raw=True` reply. A refusal raises TutorRefused;
    a reply that didn't parse raises as it would without `include_raw`, so it is retried."""
    _unless_refused(output["raw"])
    if output["parsing_error"] is not None:
        raise output["parsing_error"]
    if output["parsed"] is None:
        raise OutputParserException("the reply had no structured output")
    return output["parsed"]


def _chain(model: BaseChatModel, schema: type[BaseModel] | None) -> Runnable:
    """The model, structured as `schema` when given (plain text otherwise)."""
    if schema is None:
        return model | RunnableLambda(_unless_refused)
    return model.with_structured_output(schema, include_raw=True) | RunnableLambda(_parsed)


@dataclass(frozen=True)
class Generated[T]:
    value: T
    model: str
    fell_back: bool = False


class Recorder(Protocol):
    user_id: UUID  # whose calls these are when no request scope names a user

    def record(self, call: LlmCall) -> None: ...


class NullRecorder:
    """Records nothing."""

    user_id = UUID(int=0)

    def record(self, call: LlmCall) -> None:
        pass


class StoreRecorder:
    """Writes each attempt to the store. A write that fails is logged, never raised: the
    learner's call has already succeeded or failed on its own."""

    def __init__(self, store: Store, user_id: UUID) -> None:
        self.store = store
        self.user_id = user_id

    def record(self, call: LlmCall) -> None:
        try:
            self.store.record_llm_call(call.user_id, call)
        except psycopg.Error as error:
            log.warning("could not record the %s call (%s): %s", call.endpoint, call.model, error)


class _Meter(BaseCallbackHandler):
    """Watches one attempt's model stream: when its first chunk came and the usage the
    provider reported. For plain replies, passes each chunk's text on."""

    raise_error = True  # a failing `on_text` fails the call, as if called directly

    def __init__(self, clock: Callable[[], float], on_text: Callable[[str], None] | None):
        self.clock = clock
        self.on_text = on_text
        self.first_chunk: float | None = None
        self.usage: dict[str, Any] | None = None
        self.sent_text = False  # some of this reply has reached `on_text`

    def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
        if self.first_chunk is None:
            self.first_chunk = self.clock()
        if self.on_text is not None and chunk is not None and chunk.message.text:
            self.sent_text = True
            self.on_text(chunk.message.text)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        message = getattr(response.generations[0][0], "message", None)
        if isinstance(message, AIMessage) and message.usage_metadata is not None:
            self.usage = dict(message.usage_metadata)


def _outcome(error: BaseException | None) -> str:
    if error is None:
        return "ok"
    if isinstance(error, TutorRefused):
        return "refused"
    if isinstance(error, _INVALID_ERRORS):
        return "invalid"
    return "error"


def _config_error(error: BaseException, provider: Provider, role: Role, index: int) -> ConfigError:
    if _classify(error) == "bad_key":
        return ConfigError(
            f"{provider.service} rejected {provider.key_name} — check the key in .env"
        )
    setting = f"EBB_{role.upper()}_{'MODEL' if index == 0 else 'BACKUP'}"
    return ConfigError(
        f"Model {provider.name!r} was not found on {provider.service} — check {setting} in .env"
    )


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _backoff(failed: int, jitter: float) -> float:
    """Seconds to wait after a provider's `failed`-th failed attempt: 1 s, 2 s, 4 s... plus
    the jitter (under a second), as LangChain's `with_retry` waited."""
    return 2 ** (failed - 1) + jitter


class Gateway:
    def __init__(
        self,
        chains: dict[Role, list[Provider]],
        recorder: Recorder,
        attempts: int = 3,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """Each role's providers, main first; each is tried `attempts` times, waiting longer
        (`sleep`, plus `jitter()` in [0, 1)) between tries of the same provider."""
        self.chains = chains
        self.recorder = recorder
        self.attempts = attempts
        self.clock = clock
        self.now = now
        self.sleep = sleep
        self.jitter = jitter

    def call[T](
        self,
        role: Role,
        endpoint: Endpoint,
        schema: type[BaseModel] | None,
        prompt: str,
        creative: bool,
        parse: Callable[[Any], T],
        on_text: Callable[[str], None] | None = None,
    ) -> Generated[T]:
        """Each provider in turn, retried; a refusal (TutorRefused) ends the call at once, and
        a rejected key or unknown model is a ConfigError. A reply `parse` rejects with
        OutputParserException or ValidationError is invalid, like one that failed `schema`.
        Every attempt is recorded under one call id. `on_text` receives a plain reply's text
        as it streams; once some has reached it, a failure ends the call, so no second reply
        is spliced onto the first."""
        call_id, attempt = uuid4(), 0

        def attempt_once(provider: Provider, index: int, meter: _Meter) -> T:
            """One attempt, recorded however it ends."""
            model = provider.creative if creative else provider.precise
            at, start = self.now(), self.clock()
            error: BaseException | None = None
            try:
                # The clients stream (built with streaming=True); invoking the chain keeps
                # validation on the whole reply while the meter watches the chunks arrive.
                return parse(_chain(model, schema).invoke(prompt, config={"callbacks": [meter]}))
            except Exception as caught:
                error = caught
                raise
            finally:
                end = self.clock()
                self._record(
                    call_id, role, endpoint, provider, index, attempt, at, meter, start, end, error
                )

        last_error: BaseException | None = None
        for index, provider in enumerate(self.chains[role]):
            for tried in range(1, self.attempts + 1):
                attempt += 1
                meter = _Meter(self.clock, on_text if schema is None else None)
                try:
                    value = attempt_once(provider, index, meter)
                except PROVIDER_ERRORS as error:
                    if not _retryable(error):
                        raise _config_error(error, provider, role, index) from error
                    if meter.sent_text:
                        raise AllProvidersExhausted(str(error)) from error
                    last_error = error
                    if tried < self.attempts:
                        self.sleep(_backoff(tried, self.jitter()))
                    continue
                return Generated(value, provider.name, fell_back=index > 0)
        raise AllProvidersExhausted(str(last_error)) from last_error

    def _record(
        self,
        call_id: UUID,
        role: Role,
        endpoint: Endpoint,
        provider: Provider,
        index: int,
        attempt: int,
        at: datetime,
        meter: _Meter,
        start: float,
        end: float,
        error: BaseException | None,
    ) -> None:
        """Records the attempt. Nothing from recording may replace the call's result or
        error: a recorder that fails is logged, never raised."""
        request_id, user_id = request.current()
        usage = meter.usage or {}
        input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        call = LlmCall(
            id=uuid4(),
            call_id=call_id,
            request_id=request_id,
            user_id=user_id or self.recorder.user_id,
            at=at,
            role=role,
            endpoint=endpoint,
            provider=provider.provider,
            model=provider.name,
            attempt=attempt,
            outcome=_outcome(error),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=(usage.get("output_token_details") or {}).get("reasoning"),
            ttft_ms=None if meter.first_chunk is None else _ms(meter.first_chunk - start),
            total_ms=_ms(end - start),
            cost_usd=prices.cost(provider.provider, provider.name, input_tokens, output_tokens),
            error_kind=None if error is None else type(error).__name__,
            backup=index > 0,
        )
        try:
            self.recorder.record(call)
        except Exception:  # the recording boundary: see the docstring
            log.exception("could not record the %s call (%s)", endpoint, provider.name)
