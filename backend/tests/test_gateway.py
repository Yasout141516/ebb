import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, message_chunk_to_message
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import BaseModel

from ebb.config import ConfigError
from ebb.domain import LlmCall
from ebb.llm import prices
from ebb.llm.gateway import Gateway, NullRecorder, StoreRecorder
from ebb.llm.prices import Price
from ebb.llm.providers import Provider
from ebb.llm.request import current, request_scope
from ebb.llm.tutor import AllProvidersExhausted, TutorRefused
from tests.fakes import MemoryStore

ALICE = UUID("00000000-0000-0000-0000-00000000a11c")
OWNER = UUID("00000000-0000-0000-0000-0000000000e0")
NOW = datetime(2026, 10, 28, 9, 30, tzinfo=UTC)
USAGE = {
    "input_tokens": 1200,
    "output_tokens": 300,
    "total_tokens": 1500,
    "output_token_details": {"reasoning": 120},
}
# 1200 × $0.15/M + 300 × $0.60/M
COST = Decimal("0.00036")
REAL_PRICES = prices.PRICES  # before the fixture swaps in test prices


class Clock:
    """A monotonic clock that only moves when a fake model says time has passed."""

    def __init__(self) -> None:
        self.t = 500.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Streamed(BaseChatModel):
    """A streaming chat model on a fake clock. Each call plays the next reply: a list of
    (seconds before it arrives, chunk), or an exception to raise."""

    replies: list[Any]
    clock: Any
    streaming: bool = False  # pass streaming=True, as the real clients are built
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "streamed"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Streamed:
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        """The whole reply at once, as a client that doesn't stream returns it."""
        chunks = [c.message for c in self._stream()]
        merged = chunks[0] if len(chunks) == 1 else chunks[0] + chunks[1:]
        return ChatResult(generations=[ChatGeneration(message=message_chunk_to_message(merged))])

    def _stream(self, *args: Any, **kwargs: Any) -> Any:
        reply = self.replies[self.calls]
        self.calls += 1
        if isinstance(reply, BaseException):
            raise reply
        for item in reply:
            if isinstance(item, BaseException):  # the stream breaks off here
                raise item
            seconds, chunk = item
            self.clock.advance(seconds)
            yield ChatGenerationChunk(message=chunk)


class Pick(BaseModel):
    name: str
    score: int


def tool_reply(args: str, usage: dict | None = USAGE) -> list[tuple[float, AIMessageChunk]]:
    """A structured reply in three pieces, 0.25 s, 0.5 s and 0.25 s apart; usage last."""
    cut = len(args) // 2
    return [
        (0.25, AIMessageChunk(content="", tool_call_chunks=[_tool_chunk(args[:cut], first=True)])),
        (0.5, AIMessageChunk(content="", tool_call_chunks=[_tool_chunk(args[cut:])])),
        (0.25, AIMessageChunk(content="", usage_metadata=usage)),
    ]


def _tool_chunk(args: str, first: bool = False) -> dict:
    return {
        "name": "Pick" if first else None,
        "args": args,
        "id": "call-1" if first else None,
        "index": 0,
        "type": "tool_call_chunk",
    }


VALID = '{"name": "DNS", "score": 3}'
INVALID = '{"name": "DNS", "score": "high"}'


def text_reply(*pieces: str, usage: dict | None = USAGE, **metadata: Any) -> list:
    chunks: list = [(0.1, AIMessageChunk(content=piece)) for piece in pieces]
    chunks.append((0.1, AIMessageChunk(content="", usage_metadata=usage, **metadata)))
    return chunks


class Recording:
    """Keeps every call it is given."""

    def __init__(self, user_id: UUID = OWNER) -> None:
        self.user_id = user_id
        self.calls: list[LlmCall] = []

    def record(self, call: LlmCall) -> None:
        self.calls.append(call)


@pytest.fixture(autouse=True)
def test_prices(monkeypatch):
    price = Price(
        Decimal("0.15"), Decimal("0.60"), "https://example.test/pricing", NOW.date(), True
    )
    table = {("groq", "main"): price, ("groq", "backup"): price, ("mistral", "backup"): price}
    monkeypatch.setattr(prices, "PRICES", table)


def gateway(
    clock: Clock,
    *chain: Provider,
    recorder: Any = None,
    attempts: int = 3,
    sleep: Any = lambda seconds: None,
) -> tuple[Gateway, Recording]:
    recording = recorder or Recording()
    chains = {"quiz": list(chain), "teach": list(chain)}
    return (
        Gateway(chains, recording, attempts, clock, lambda: NOW, sleep, jitter=lambda: 0.5),
        recording,
    )


def provider(name: str, model: Streamed, kind: str = "groq") -> Provider:
    return Provider(name, model, model, provider=kind)


def test_records_tokens_ttft_and_total_for_an_ok_call():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model))

    out = gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p.name)

    assert (out.value, out.model, out.fell_back) == ("DNS", "main", False)
    [row] = recording.calls
    assert row == LlmCall(
        id=row.id,
        call_id=row.call_id,
        request_id=None,
        user_id=OWNER,
        at=NOW,
        role="quiz",
        endpoint="grade",
        provider="groq",
        model="main",
        attempt=1,
        outcome="ok",
        input_tokens=1200,
        output_tokens=300,
        reasoning_tokens=120,
        ttft_ms=250,
        total_ms=1000,
        cost_usd=COST,
        error_kind=None,
        backup=False,
    )


def test_retry_and_fallback_each_write_a_row_with_one_call_id():
    clock = Clock()
    main = Streamed(replies=[tool_reply(INVALID)] * 3, clock=clock, streaming=True)
    backup = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    gw, recording = gateway(
        clock, provider("main", main), provider("backup", backup, "mistral"), attempts=3
    )

    out = gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p.name)

    assert (out.value, out.model, out.fell_back) == ("DNS", "backup", True)
    rows = recording.calls
    assert [r.outcome for r in rows] == ["invalid", "invalid", "invalid", "ok"]
    assert [r.attempt for r in rows] == [1, 2, 3, 4]
    assert [(r.provider, r.model, r.backup) for r in rows] == [
        ("groq", "main", False),
        ("groq", "main", False),
        ("groq", "main", False),
        ("mistral", "backup", True),
    ]
    assert len({r.call_id for r in rows}) == 1
    assert len({r.id for r in rows}) == 4
    assert [r.error_kind for r in rows] == ["ValidationError"] * 3 + [None]
    # An invalid reply still used tokens: they and its cost are recorded.
    assert (rows[0].input_tokens, rows[0].cost_usd, rows[0].total_ms) == (1200, COST, 1000)


def test_provider_errors_record_error_and_exhaust():
    clock = Clock()
    down = Streamed(replies=[httpx.ConnectError("down")] * 2, clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", down), attempts=2)

    with pytest.raises(AllProvidersExhausted):
        gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    assert [(r.outcome, r.error_kind) for r in recording.calls] == [("error", "ConnectError")] * 2
    first = recording.calls[0]
    assert (first.input_tokens, first.ttft_ms, first.total_ms, first.cost_usd) == (
        None,
        None,
        0,
        None,
    )


def test_a_rejected_key_is_recorded_then_raised_as_a_config_error():
    clock = Clock()
    response = httpx.Response(401, request=httpx.Request("POST", "https://x.test"))
    error = httpx.HTTPStatusError("401", request=response.request, response=response)
    backup = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    bad = Streamed(replies=[error], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", bad), provider("backup", backup))

    with pytest.raises(ConfigError):
        gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    assert [(r.outcome, r.error_kind) for r in recording.calls] == [("error", "HTTPStatusError")]
    assert backup.calls == 0


def test_refusal_records_refused_and_stops():
    clock = Clock()
    refusing = Streamed(
        replies=[text_reply("No.", response_metadata={"finish_reason": "content_filter"})],
        clock=clock,
        streaming=True,
    )
    backup = Streamed(replies=[text_reply("Fine.")], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", refusing), provider("backup", backup))

    with pytest.raises(TutorRefused):
        gw.call("teach", "reteach", None, "prompt", creative=True, parse=lambda m: m.text)

    [row] = recording.calls
    assert (row.outcome, row.error_kind, row.input_tokens, row.ttft_ms) == (
        "refused",
        "TutorRefused",
        1200,
        100,
    )
    assert (refusing.calls, backup.calls) == (1, 0)


def test_missing_usage_records_none():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID, usage=None)], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model))

    gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    [row] = recording.calls
    assert (row.input_tokens, row.output_tokens, row.reasoning_tokens) == (None, None, None)
    assert row.cost_usd is None  # a price is known, the tokens are not: no guess
    assert (row.ttft_ms, row.total_ms) == (250, 1000)


def test_usage_without_reasoning_details_records_no_reasoning_tokens():
    clock = Clock()
    usage = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    model = Streamed(replies=[tool_reply(VALID, usage=usage)], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model))

    gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    [row] = recording.calls
    assert (row.input_tokens, row.output_tokens, row.reasoning_tokens) == (10, 4, None)


def test_single_chunk_reply_ttft_equals_that_chunk():
    clock = Clock()
    whole = AIMessageChunk(
        content="",
        tool_call_chunks=[_tool_chunk(VALID, first=True)],
        usage_metadata=USAGE,
    )
    model = Streamed(replies=[[(0.75, whole)]], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model))

    out = gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p.score)

    assert out.value == 3
    [row] = recording.calls
    assert (row.ttft_ms, row.total_ms, row.input_tokens) == (750, 750, 1200)


def test_a_model_that_does_not_stream_records_no_ttft():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)], clock=clock)  # streaming not switched on
    gw, recording = gateway(clock, provider("main", model))

    out = gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p.name)

    assert out.value == "DNS"
    [row] = recording.calls
    assert (row.outcome, row.ttft_ms, row.total_ms, row.input_tokens) == ("ok", None, 1000, 1200)


def test_unknown_price_records_null_cost():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("unpriced/model", model))

    gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    [row] = recording.calls
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (1200, 300, None)


class FailingStore(MemoryStore):
    def record_llm_call(self, user_id: UUID, call: LlmCall) -> None:
        raise psycopg.OperationalError("the database went away")


def test_recorder_failure_does_not_fail_the_call(caplog):
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    gw, _ = gateway(clock, provider("main", model), recorder=StoreRecorder(FailingStore(), OWNER))

    with caplog.at_level(logging.WARNING, logger="ebb.llm.gateway"):
        out = gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p.name)

    assert out.value == "DNS"
    [warning] = caplog.records
    assert warning.levelno == logging.WARNING
    assert "the database went away" in warning.getMessage()


def test_store_recorder_records_under_the_scope_user_or_its_own():
    store, clock = MemoryStore(), Clock()
    model = Streamed(replies=[tool_reply(VALID)] * 2, clock=clock, streaming=True)
    gw, _ = gateway(clock, provider("main", model), recorder=StoreRecorder(store, OWNER))

    gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)
    with request_scope(ALICE) as request_id:
        gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    assert [(c.user_id, c.request_id) for c in store.llm_call_log] == [
        (OWNER, None),
        (ALICE, request_id),
    ]


def test_null_recorder_records_nothing():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    gw, _ = gateway(clock, provider("main", model), recorder=NullRecorder())
    assert (
        gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p.name).value == "DNS"
    )


def test_on_text_receives_streamed_text_for_plain_calls():
    clock = Clock()
    model = Streamed(
        replies=[text_reply("Caching", " saves", " lookups."), tool_reply(VALID)],
        clock=clock,
        streaming=True,
    )
    gw, _ = gateway(clock, provider("main", model))
    pieces: list[str] = []

    out = gw.call(
        "teach", "reteach", None, "p", creative=True, parse=lambda m: m.text, on_text=pieces.append
    )
    assert pieces == ["Caching", " saves", " lookups."]
    assert out.value == "Caching saves lookups."

    pieces.clear()
    gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p, on_text=pieces.append)
    assert pieces == []  # structured replies are not text


def test_request_scope_tags_rows():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)] * 3, clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model))

    assert current() == (None, None)
    with request_scope(ALICE) as request_id:
        assert current() == (request_id, ALICE)
        gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p)
        with request_scope(uuid4()) as inner:
            assert inner == request_id  # a nested scope keeps the outer request
            gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p)
    assert current() == (None, None)
    gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p)

    first, second, outside = recording.calls
    assert (first.request_id, first.user_id) == (request_id, ALICE)
    assert (second.request_id, second.user_id) == (request_id, ALICE)
    assert first.call_id != second.call_id
    assert (outside.request_id, outside.user_id) == (None, OWNER)


def test_cost_is_tokens_times_the_price_per_million():
    assert prices.cost("groq", "main", 1200, 300) == COST
    assert prices.cost("groq", "main", None, 300) is None
    assert prices.cost("groq", "main", 1200, None) is None
    assert prices.cost("groq", "nobody/knows", 1200, 300) is None


def test_a_price_belongs_to_one_provider(monkeypatch):
    monkeypatch.setattr(prices, "PRICES", REAL_PRICES)
    model = "openai/gpt-oss-120b"
    assert prices.cost("groq", model, 1_000_000, 1_000_000) == Decimal("0.75")
    assert prices.cost("openrouter", model, 1_000_000, 1_000_000) is None


def test_the_same_model_on_another_provider_records_no_cost():
    clock = Clock()
    model = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model, "openrouter"))

    gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    [row] = recording.calls
    assert (row.provider, row.input_tokens, row.cost_usd) == ("openrouter", 1200, None)


def test_price_table_names_its_sources():
    for key, price in REAL_PRICES.items():
        assert price.source.startswith("https://"), key
        assert price.as_of <= date.today()
        assert price.input_per_m > 0 and price.output_per_m > 0


def test_retries_wait_with_exponential_backoff_on_the_same_provider_only():
    clock, waits = Clock(), []
    main = Streamed(replies=[tool_reply(INVALID)] * 3, clock=clock, streaming=True)
    backup = Streamed(replies=[tool_reply(INVALID), tool_reply(VALID)], clock=clock, streaming=True)
    gw, recording = gateway(
        clock, provider("main", main), provider("backup", backup), sleep=waits.append
    )

    gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    # 1 s then 2 s, each plus the jitter (0.5 here); none before switching to the backup.
    assert waits == [1.5, 2.5, 1.5]
    assert len(recording.calls) == 5


def test_backoff_jitter_stays_within_a_second(monkeypatch):
    clock, waits = Clock(), []
    main = Streamed(replies=[tool_reply(INVALID)] * 3, clock=clock, streaming=True)
    gw = Gateway({"quiz": [provider("main", main)]}, Recording(), 3, clock, sleep=waits.append)

    with pytest.raises(AllProvidersExhausted):
        gw.call("quiz", "grade", Pick, "prompt", creative=False, parse=lambda p: p)

    first, second = waits
    assert 1 <= first < 2 and 2 <= second < 3


def test_text_already_streamed_is_not_retried_or_fallen_back():
    clock = Clock()
    broken = text_reply("Caching", " saves")[:2] + [httpx.ReadTimeout("stalled")]
    main = Streamed(replies=[broken, text_reply("Again.")], clock=clock, streaming=True)
    backup = Streamed(replies=[text_reply("Backup.")], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", main), provider("backup", backup))
    pieces: list[str] = []

    with pytest.raises(AllProvidersExhausted):
        gw.call("teach", "reteach", None, "p", creative=True, parse=str, on_text=pieces.append)

    assert pieces == ["Caching", " saves"]  # the learner never sees a second reply spliced on
    assert [(r.outcome, r.error_kind) for r in recording.calls] == [("error", "ReadTimeout")]
    assert (main.calls, backup.calls) == (1, 0)


def test_failure_before_any_text_retries_and_falls_back_as_usual():
    clock = Clock()
    error = httpx.ReadTimeout("slow")
    main = Streamed(replies=[[error], [error]], clock=clock, streaming=True)
    backup = Streamed(replies=[text_reply("Backup", " lesson.")], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", main), provider("backup", backup), attempts=2)
    pieces: list[str] = []

    out = gw.call(
        "teach", "reteach", None, "p", creative=True, parse=lambda m: m.text, on_text=pieces.append
    )

    assert (out.value, out.fell_back) == ("Backup lesson.", True)
    assert pieces == ["Backup", " lesson."]
    assert [r.outcome for r in recording.calls] == ["error", "error", "ok"]


def test_a_reply_that_parse_rejects_is_invalid_and_retried():
    clock = Clock()
    model = Streamed(replies=[text_reply("bad"), text_reply("good")], clock=clock, streaming=True)
    gw, recording = gateway(clock, provider("main", model))

    def parse(message: Any) -> str:
        if message.text == "bad":
            raise OutputParserException("no trailer")
        return message.text

    out = gw.call("teach", "lesson", None, "p", creative=True, parse=parse)

    assert out.value == "good"
    assert [(r.outcome, r.error_kind) for r in recording.calls] == [
        ("invalid", "OutputParserException"),
        ("ok", None),
    ]


class Exploding(Recording):
    def record(self, call: LlmCall) -> None:
        raise RuntimeError("recorder bug")


def test_a_failing_recorder_never_replaces_the_result_or_the_error(caplog):
    clock = Clock()
    ok = Streamed(replies=[tool_reply(VALID)], clock=clock, streaming=True)
    down = Streamed(replies=[httpx.ConnectError("down")], clock=clock, streaming=True)

    with caplog.at_level(logging.ERROR, logger="ebb.llm.gateway"):
        gw, _ = gateway(clock, provider("main", ok), recorder=Exploding())
        assert gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p.name).value
        gw, _ = gateway(clock, provider("main", down), recorder=Exploding(), attempts=1)
        with pytest.raises(AllProvidersExhausted):
            gw.call("quiz", "grade", Pick, "p", creative=False, parse=lambda p: p)

    assert len(caplog.records) == 2
    assert all(isinstance(r.exc_info[1], RuntimeError) for r in caplog.records)
