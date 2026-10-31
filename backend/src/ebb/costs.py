"""What the Costs page shows: sums, percentiles and anomaly rules over recorded model calls.
Pure functions, like progress and mastery.

A "call" is one call_id; it may hold several attempts (retries, then a backup model); it
was retried if some provider was asked more than once (an attempt number above 1); one that
failed on the main model and then succeeded on the backup fell back, and was not retried. Rates
count calls. Cost and tokens add
up every attempt, since a failed attempt that used tokens cost money too. Percentiles use ok
attempts only and the nearest-rank method."""

import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, median
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from ebb.domain import LlmCall
from ebb.llm.prices import PRICES
from ebb.llm.providers import Role

BASELINE_CALLS = 50  # an endpoint's usual is the median of its previous fifty ok calls
BASELINE_MIN_CALLS = 10
SPIKE_RATIO = 3
TTFT_FLOOR_MS = 2000
TOTAL_FLOOR_MS = 5000
DAY_WINDOW = 7
DAY_MIN_DAYS = 3
DAY_RATIO = 2

# endpoint -> (what the page calls it, what "usual" refers to in a message, feature).
# Dict order is the order by_endpoint lists them in.
ENDPOINTS: dict[str, tuple[str, str, str]] = {
    "lesson": ("Lesson", "lesson call", "lessons"),
    "reteach": ("Re-teach", "re-teaching call", "lessons"),
    "write_questions": ("Write questions", "question-writing call", "questions"),
    "write_web_questions": ("Write web questions", "question-writing call", "questions"),
    "rewrite_questions": ("Rewrite questions", "question-rewriting call", "questions"),
    "grade": ("Grade answer", "grading call", "grading"),
    "regrade": ("Re-grade answer", "re-grading call", "grading"),
    "learn_answer": ("Learn-phase answer", "learn-phase answer call", "learn"),
    "extract_concepts": ("Extract concepts", "concept-extraction call", "concepts"),
    "extend_concepts": ("Extend concepts", "concept-extension call", "concepts"),
    "concepts_from_notes": ("Concepts from notes", "concept-extraction call", "concepts"),
    "write_eval_case": ("Write eval case", "eval-writing call", "evals"),
}
OTHER = ("other", "Other")  # feature of an endpoint this table does not know yet
FEATURES: dict[str, str] = {
    "lessons": "Lessons and re-teaching",
    "questions": "Question writing",
    "grading": "Grading and disputes",
    "learn": "Learn-phase questions",
    "concepts": "Concept extraction",
    "evals": "Evals",
}


@dataclass(frozen=True)
class Summary:
    cost_usd: Decimal | None  # None when some ok call has no price or reported no tokens
    unpriced_calls: int  # ok calls with token counts but no price
    unreported_calls: int  # ok calls whose provider reported no tokens, so no cost
    input_tokens: int | None  # None when no call reported any
    output_tokens: int | None
    calls: int
    retried: int
    fell_back: int
    ttft_p50_ms: int | None
    ttft_p95_ms: int | None
    total_p50_ms: int | None
    total_p95_ms: int | None
    fallback_rate: float | None  # None with no calls
    retry_rate: float | None
    all_free_tier: bool  # every model in the range is on a free plan; False with no calls


@dataclass(frozen=True)
class Breakdown:
    """One row of a by-feature, by-endpoint, by-tenant or by-model table."""

    key: str
    label: str
    calls: int
    cost_usd: Decimal | None
    unpriced_calls: int
    input_tokens: int | None
    output_tokens: int | None
    ttft_p50_ms: int | None
    ttft_p95_ms: int | None
    total_p50_ms: int | None
    total_p95_ms: int | None
    output_tokens_per_second: float | None  # output tokens over total time, ok attempts
    fallbacks: int


@dataclass(frozen=True)
class DayTokens:
    day: date  # local date
    input_tokens: int | None  # None when no call that day reported any
    output_tokens: int | None
    tokens: int | None  # input + output (reasoning is already inside output)
    calls: int


@dataclass(frozen=True)
class RoleHealth:
    role: str
    main: tuple[str, str]  # (provider, model)
    backup: tuple[str, str] | None
    attempts: int
    failed_share: float | None  # None with no attempts
    p95_total_ms: int | None  # None with no ok attempts


@dataclass(frozen=True)
class Anomaly:
    kind: Literal["tokens", "ttft", "total", "day"]
    call_id: UUID | None
    day: date | None
    endpoint: str | None
    ratio: float
    message: str


def percentile(values: Iterable[int], pct: int) -> int | None:
    """Nearest rank: the smallest value with at least pct% of values at or below it."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = -(-pct * len(ordered) // 100)  # ceil
    return ordered[max(rank, 1) - 1]


def _ok(calls: Iterable[LlmCall]) -> list[LlmCall]:
    return [c for c in calls if c.outcome == "ok"]


def _sum_known(values: Iterable[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _cost(calls: Sequence[LlmCall]) -> tuple[Decimal | None, int, int]:
    """Estimated cost over every attempt, how many calls had an ok attempt with token counts
    but no price, and how many had one whose provider reported no tokens. Either makes the
    total unknown: a missing cost is not zero."""
    unpriced = set()
    unreported = set()
    for c in calls:
        if c.outcome != "ok" or c.cost_usd is not None:
            continue
        if c.input_tokens is None or c.output_tokens is None:
            unreported.add(c.call_id)
        else:
            unpriced.add(c.call_id)
    total = sum((c.cost_usd for c in calls if c.cost_usd is not None), Decimal(0))
    return (None if unpriced or unreported else total), len(unpriced), len(unreported)


def _by_call(calls: Iterable[LlmCall]) -> dict[UUID, list[LlmCall]]:
    grouped: dict[UUID, list[LlmCall]] = defaultdict(list)
    for c in calls:
        grouped[c.call_id].append(c)
    return grouped


def _tokens_per_second(ok: Sequence[LlmCall]) -> float | None:
    timed = [c for c in ok if c.output_tokens is not None and c.total_ms > 0]
    if not timed:
        return None
    return sum(c.output_tokens or 0 for c in timed) * 1000 / sum(c.total_ms for c in timed)


def _rate(count: int, calls: int) -> float | None:
    return count / calls if calls else None


def _is_free(provider: str, model: str) -> bool:
    price = PRICES.get((provider, model))
    return price is not None and price.free_tier


def summary(calls: Sequence[LlmCall]) -> Summary:
    grouped = _by_call(calls)
    retried = sum(1 for attempts in grouped.values() if any(a.attempt > 1 for a in attempts))
    fell_back = sum(1 for attempts in grouped.values() if any(a.backup for a in attempts))
    ok = _ok(calls)
    ttfts = [c.ttft_ms for c in ok if c.ttft_ms is not None]
    totals = [c.total_ms for c in ok]
    cost, unpriced, unreported = _cost(calls)
    return Summary(
        cost_usd=cost,
        unpriced_calls=unpriced,
        unreported_calls=unreported,
        input_tokens=_sum_known(c.input_tokens for c in calls),
        output_tokens=_sum_known(c.output_tokens for c in calls),
        calls=len(grouped),
        retried=retried,
        fell_back=fell_back,
        ttft_p50_ms=percentile(ttfts, 50),
        ttft_p95_ms=percentile(ttfts, 95),
        total_p50_ms=percentile(totals, 50),
        total_p95_ms=percentile(totals, 95),
        fallback_rate=_rate(fell_back, len(grouped)),
        retry_rate=_rate(retried, len(grouped)),
        all_free_tier=bool(calls) and all(_is_free(c.provider, c.model) for c in calls),
    )


def _breakdown(key: str, label: str, calls: Sequence[LlmCall]) -> Breakdown:
    ok = _ok(calls)
    ttfts = [c.ttft_ms for c in ok if c.ttft_ms is not None]
    totals = [c.total_ms for c in ok]
    cost, unpriced, _ = _cost(calls)
    return Breakdown(
        key=key,
        label=label,
        calls=len({c.call_id for c in calls}),
        cost_usd=cost,
        unpriced_calls=unpriced,
        input_tokens=_sum_known(c.input_tokens for c in calls),
        output_tokens=_sum_known(c.output_tokens for c in calls),
        ttft_p50_ms=percentile(ttfts, 50),
        ttft_p95_ms=percentile(ttfts, 95),
        total_p50_ms=percentile(totals, 50),
        total_p95_ms=percentile(totals, 95),
        output_tokens_per_second=_tokens_per_second(ok),
        fallbacks=len({c.call_id for c in calls if c.backup}),
    )


def _grouped_rows(
    calls: Sequence[LlmCall],
    key: Callable[[LlmCall], str],
    label: Callable[[str], str],
    order: Sequence[str] | None = None,
) -> list[Breakdown]:
    groups: dict[str, list[LlmCall]] = defaultdict(list)
    for c in calls:
        groups[key(c)].append(c)
    if order is not None:  # fixed order; groups with no calls still get a row
        return [_breakdown(k, label(k), groups.get(k, [])) for k in order]
    rows = [_breakdown(k, label(k), v) for k, v in groups.items()]
    return sorted(rows, key=lambda r: -r.calls)  # stable: ties keep first-seen order


def _endpoint_info(endpoint: str) -> tuple[str, str, str]:
    """(label, noun, feature); an endpoint missing from the table still gets a row."""
    return ENDPOINTS.get(endpoint, (endpoint, "call", OTHER[0]))


def _feature_of(call: LlmCall) -> str:
    return _endpoint_info(call.endpoint)[2]


def endpoint_label(endpoint: str) -> str:
    return _endpoint_info(endpoint)[0]


def _floor1(ratio: float) -> str:
    """One decimal, rounded down, so a flagged call never reads as exactly the threshold."""
    return f"{math.floor(ratio * 10) / 10:.1f}"


def _merged(history: Sequence[LlmCall], calls: Sequence[LlmCall]) -> list[LlmCall]:
    """History then range, each record once (the two may overlap)."""
    return list({c.id: c for c in [*history, *calls]}.values())


def by_feature(calls: Sequence[LlmCall]) -> list[Breakdown]:
    order = list(FEATURES)
    if any(_feature_of(c) == OTHER[0] for c in calls):
        order.append(OTHER[0])
    return _grouped_rows(calls, _feature_of, lambda k: FEATURES.get(k, OTHER[1]), order=order)


def by_endpoint(calls: Sequence[LlmCall]) -> list[Breakdown]:
    present = {c.endpoint for c in calls}
    order = [e for e in ENDPOINTS if e in present] + sorted(present - set(ENDPOINTS))
    return _grouped_rows(calls, lambda c: c.endpoint, endpoint_label, order=order)


def by_tenant(calls: Sequence[LlmCall]) -> list[Breakdown]:
    return _grouped_rows(calls, lambda c: str(c.user_id), str)


def by_model(calls: Sequence[LlmCall]) -> list[Breakdown]:
    return _grouped_rows(calls, lambda c: f"{c.provider}/{c.model}", str)


def per_day(calls: Sequence[LlmCall], tz: ZoneInfo) -> list[DayTokens]:
    """Tokens per local day over every attempt, oldest first; days with no calls are left out."""
    days: dict[date, list[LlmCall]] = defaultdict(list)
    for c in calls:
        days[c.at.astimezone(tz).date()].append(c)
    rows = []
    for day, rows_of_day in sorted(days.items()):
        tokens_in = _sum_known(c.input_tokens for c in rows_of_day)
        tokens_out = _sum_known(c.output_tokens for c in rows_of_day)
        known = [t for t in (tokens_in, tokens_out) if t is not None]
        total = sum(known) if known else None
        calls_in_day = len({c.call_id for c in rows_of_day})
        rows.append(DayTokens(day, tokens_in, tokens_out, total, calls_in_day))
    return rows


def role_health(
    calls: Sequence[LlmCall], roles: Mapping[Role, Sequence[tuple[str, str]]]
) -> list[RoleHealth]:
    """Each role's chain (main, then backup) with its failed-attempt share and p95 time."""
    rows = []
    for role, chain in roles.items():
        attempts = [c for c in calls if c.role == role]
        ok = _ok(attempts)
        rows.append(
            RoleHealth(
                role=role,
                main=chain[0],
                backup=chain[1] if len(chain) > 1 else None,
                attempts=len(attempts),
                failed_share=_rate(len(attempts) - len(ok), len(attempts)),
                p95_total_ms=percentile((c.total_ms for c in ok), 95),
            )
        )
    return rows


def failures(calls: Sequence[LlmCall]) -> list[LlmCall]:
    """Attempts that errored or were refused, and every attempt of a call that never got an
    ok one (a call whose replies were all invalid, say). Oldest first."""
    answered = {c.call_id for c in calls if c.outcome == "ok"}
    failed = [c for c in calls if c.outcome in ("error", "refused") or c.call_id not in answered]
    return sorted(failed, key=lambda c: c.at)


def _call_anomalies(
    calls: Sequence[LlmCall], history: Sequence[LlmCall], tz: ZoneInfo, today: date
) -> list[Anomaly]:
    ok_by_endpoint: dict[str, list[LlmCall]] = defaultdict(list)
    for c in sorted(_ok(_merged(history, calls)), key=lambda c: c.at):
        ok_by_endpoint[c.endpoint].append(c)
    times = {e: [c.at for c in rows] for e, rows in ok_by_endpoint.items()}
    in_range = {c.id for c in calls}
    found = []
    for endpoint, rows in ok_by_endpoint.items():
        label, noun, _ = _endpoint_info(endpoint)
        for c in rows:
            if c.id not in in_range:
                continue
            end = bisect_left(times[endpoint], c.at)  # strictly before c
            previous = rows[max(0, end - BASELINE_CALLS) : end]
            local = c.at.astimezone(tz)
            # The date only when it is not today there, so a 7-day range reads unambiguously.
            stamp = f"{local:%H:%M}" if local.date() == today else f"{local:%d %b %H:%M}"
            when = f"{label} at {stamp}"
            for kind, value, usual, floor, text in (
                (
                    "tokens",
                    c.input_tokens,
                    lambda p: p.input_tokens,
                    0,
                    f"used {{}} times the input tokens a {noun} usually uses",
                ),
                (
                    "tokens",
                    c.output_tokens,
                    lambda p: p.output_tokens,
                    0,
                    f"used {{}} times the output tokens a {noun} usually uses",
                ),
                (
                    "ttft",
                    c.ttft_ms,
                    lambda p: p.ttft_ms,
                    TTFT_FLOOR_MS,
                    f"took {{}} times as long to start replying as a {noun} usually does",
                ),
                (
                    "total",
                    c.total_ms,
                    lambda p: p.total_ms,
                    TOTAL_FLOOR_MS,
                    f"took {{}} times as long to finish as a {noun} usually does",
                ),
            ):
                known = [v for v in map(usual, previous) if v is not None]
                if value is None or len(known) < BASELINE_MIN_CALLS:
                    continue
                typical = median(known)
                if typical > 0 and value > SPIKE_RATIO * typical and value > floor:
                    ratio = value / typical
                    message = f"{when} {text.format(_floor1(ratio))}"
                    found.append(Anomaly(kind, c.call_id, None, endpoint, ratio, message))
    return found


def _day_anomalies(
    calls: Sequence[LlmCall], history: Sequence[LlmCall], tz: ZoneInfo
) -> list[Anomaly]:
    totals = {d.day: d.tokens for d in per_day(_merged(history, calls), tz) if d.tokens is not None}
    found = []
    for day in sorted({c.at.astimezone(tz).date() for c in calls} & totals.keys()):
        window = [
            totals[d] for k in range(1, DAY_WINDOW + 1) if (d := day - timedelta(days=k)) in totals
        ]
        if len(window) < DAY_MIN_DAYS:
            continue
        usual = mean(window)
        if usual > 0 and totals[day] > DAY_RATIO * usual:
            ratio = totals[day] / usual
            message = (
                f"Tokens on {day:%Y-%m-%d} were {_floor1(ratio)} times "
                "the daily average of the week before"
            )
            found.append(Anomaly("day", None, day, None, ratio, message))
    return found


def anomalies(
    calls: Sequence[LlmCall], history: Sequence[LlmCall], tz: ZoneInfo, today: date
) -> list[Anomaly]:
    """Calls that were unusually big or slow for their endpoint, then days that were unusually
    big. `history` is what came before the range, so the first calls have a baseline. `today`
    is the local date, which decides whether a call's message names its date."""
    by_call_time = {c.call_id: c.at for c in calls}
    per_call = sorted(
        _call_anomalies(calls, history, tz, today), key=lambda a: by_call_time[a.call_id]
    )
    return [*per_call, *_day_anomalies(calls, history, tz)]
