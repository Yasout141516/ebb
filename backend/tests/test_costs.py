import typing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ebb import costs
from ebb.domain import LlmCall
from ebb.llm.gateway import Endpoint

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TODAY = T0.date()
USER = uuid4()
GROQ = ("groq", "openai/gpt-oss-120b")


def call(**fields) -> LlmCall:
    base = {
        "id": uuid4(),
        "call_id": uuid4(),
        "request_id": None,
        "user_id": USER,
        "at": T0,
        "role": "quiz",
        "endpoint": "grade",
        "provider": GROQ[0],
        "model": GROQ[1],
        "attempt": 1,
        "outcome": "ok",
        "input_tokens": 100,
        "output_tokens": 50,
        "reasoning_tokens": None,
        "ttft_ms": 500,
        "total_ms": 1000,
        "cost_usd": Decimal("0.001"),
        "error_kind": None,
        "backup": False,
    }
    return LlmCall(**{**base, **fields})


def series(n, **fields):
    """n ok calls one minute apart, ending just before T0."""
    return [call(at=T0 - timedelta(minutes=n - i), **fields) for i in range(n)]


def kinds(found):
    return [a.kind for a in found]


def test_summary_of_no_calls_is_empty_not_nan():
    s = costs.summary([])
    assert (s.calls, s.retried, s.fell_back) == (0, 0, 0)
    assert s.cost_usd == Decimal(0)
    assert s.input_tokens is None and s.output_tokens is None
    assert s.ttft_p50_ms is None and s.ttft_p95_ms is None
    assert s.total_p50_ms is None and s.total_p95_ms is None
    assert s.fallback_rate is None and s.retry_rate is None
    assert s.all_free_tier is False


def test_summary_of_one_call():
    s = costs.summary([call(ttft_ms=700, total_ms=1900)])
    assert s.calls == 1
    assert (s.ttft_p50_ms, s.ttft_p95_ms) == (700, 700)
    assert (s.total_p50_ms, s.total_p95_ms) == (1900, 1900)
    assert (s.input_tokens, s.output_tokens) == (100, 50)
    assert s.cost_usd == Decimal("0.001")
    assert (s.fallback_rate, s.retry_rate) == (0.0, 0.0)
    assert s.all_free_tier is True


def test_percentiles_are_nearest_rank_over_ok_attempts():
    rows = [call(total_ms=ms) for ms in range(100, 1100, 100)]  # 100..1000
    rows.append(call(outcome="error", total_ms=99999, ttft_ms=None))
    s = costs.summary(rows)
    assert (s.total_p50_ms, s.total_p95_ms) == (500, 1000)


def test_rates_are_over_calls_and_cost_over_all_attempts():
    retried = uuid4()
    fell = uuid4()
    rows = [
        call(call_id=retried, outcome="error", ttft_ms=None, cost_usd=Decimal("0.002")),
        call(call_id=retried, attempt=2),
        call(call_id=fell, outcome="error", ttft_ms=None),
        # Attempt numbers run across the chain: the backup's first try is attempt 2 here.
        call(call_id=fell, provider="openrouter", model="m", backup=True, attempt=2),
        call(),
        call(),
    ]
    s = costs.summary(rows)
    assert (s.calls, s.retried, s.fell_back) == (4, 1, 1)
    assert s.retry_rate == 0.25 and s.fallback_rate == 0.25
    assert s.input_tokens == 600
    assert s.cost_usd == Decimal("0.007")


def test_a_backup_asked_twice_is_retried_too():
    both = uuid4()
    rows = [
        call(call_id=both, outcome="error", ttft_ms=None),
        call(
            call_id=both, provider="openrouter", model="m", backup=True, attempt=2, outcome="error"
        ),
        call(call_id=both, provider="openrouter", model="m", backup=True, attempt=3),
    ]
    s = costs.summary(rows)
    assert (s.calls, s.retried, s.fell_back) == (1, 1, 1)


def test_unpriced_model_makes_cost_unknown():
    unpriced = call(cost_usd=None, provider="mistral", model="mistral-medium-latest")
    s = costs.summary([call(), unpriced])
    assert s.cost_usd is None
    assert s.unpriced_calls == 1
    assert s.all_free_tier is False


def test_tokens_not_reported_stay_none():
    rows = [call(input_tokens=None, output_tokens=None, cost_usd=None)]
    s = costs.summary(rows)
    assert s.input_tokens is None and s.output_tokens is None
    assert s.cost_usd is None  # no usage reported: unknown, not $0.00
    assert (s.unpriced_calls, s.unreported_calls) == (0, 1)
    for table in (costs.by_endpoint(rows), costs.by_feature(rows), costs.by_model(rows)):
        assert all(b.cost_usd is None for b in table if b.calls)


def test_anomaly_names_the_date_unless_it_is_today():
    old = call(at=T0 - timedelta(days=2), total_ms=9000)
    history = [replace(c, at=c.at - timedelta(days=2)) for c in series(10)]
    found = costs.anomalies([old], history, UTC, TODAY)
    assert found[0].message.startswith("Grade answer at 18 Oct 12:00 took")
    found = costs.anomalies([old], history, UTC, TODAY - timedelta(days=2))
    assert found[0].message.startswith("Grade answer at 12:00 took")


def test_token_anomaly_needs_ten_previous_calls():
    big = call(input_tokens=1000, output_tokens=500)
    assert costs.anomalies([big], series(9), UTC, TODAY) == []
    assert kinds(costs.anomalies([big], series(10), UTC, TODAY)) == [
        "tokens",
        "tokens",
    ]  # in and out


def test_token_anomaly_over_three_times_median():
    history = series(10)  # 150 tokens each
    assert costs.anomalies([call(input_tokens=300, output_tokens=150)], history, UTC, TODAY) == []
    found = costs.anomalies([call(input_tokens=300, output_tokens=151)], history, UTC, TODAY)
    assert kinds(found) == ["tokens"]  # only the output count is over
    a = found[0]
    assert a.endpoint == "grade" and a.ratio == pytest.approx(151 / 50)
    assert a.message == (
        "Grade answer at 12:00 used 3.0 times the output tokens a grading call usually uses"
    )


def test_token_anomaly_names_input_when_only_input_spikes():
    found = costs.anomalies([call(input_tokens=410, output_tokens=50)], series(10), UTC, TODAY)
    assert kinds(found) == ["tokens"]
    assert found[0].ratio == pytest.approx(4.1)
    assert found[0].message == (
        "Grade answer at 12:00 used 4.1 times the input tokens a grading call usually uses"
    )


def test_token_anomaly_checks_the_one_count_a_call_reported():
    found = costs.anomalies([call(input_tokens=None, output_tokens=500)], series(10), UTC, TODAY)
    assert kinds(found) == ["tokens"] and "output tokens" in found[0].message
    unknown_before = series(10, input_tokens=None)  # baseline for input has no known values
    assert costs.anomalies([call(input_tokens=5000)], unknown_before, UTC, TODAY) == []


def test_ratio_in_messages_rounds_down():
    found = costs.anomalies([call(total_ms=5990)], series(10, total_ms=1000), UTC, TODAY)
    assert "took 5.9 times" in found[0].message  # 5.99, never rounded up


def test_baseline_is_built_once_when_history_and_range_overlap():
    seen = series(5)
    big = call(input_tokens=5000)
    assert costs.anomalies([*seen, big], seen, UTC, TODAY) == []  # five distinct calls, not ten


def test_baseline_is_the_previous_fifty_ok_calls_of_that_endpoint():
    old = [replace(c, at=c.at - timedelta(hours=2)) for c in series(50, input_tokens=1000)]
    history = old + series(50)  # the last fifty are 150 tokens each
    found = costs.anomalies([call(input_tokens=500, output_tokens=0)], history, UTC, TODAY)
    assert kinds(found) == ["tokens"]
    # another endpoint's calls are no baseline
    other = series(10, endpoint="lesson", input_tokens=1000)
    assert costs.anomalies([call(input_tokens=500)], series(9) + other, UTC, TODAY) == []


def test_baseline_only_uses_ok_calls_strictly_before():
    failed = [call(outcome="error", at=T0 - timedelta(minutes=i + 1)) for i in range(10)]
    assert costs.anomalies([call(input_tokens=5000)], failed, UTC, TODAY) == []
    # inside one range, calls count only once they are behind the call being judged
    inside = series(10)
    big = call(at=T0 - timedelta(hours=1), input_tokens=5000)
    assert costs.anomalies([big, *inside], [], UTC, TODAY) == []


def test_ttft_anomaly_needs_both_ratio_and_two_seconds():
    history = series(10, ttft_ms=500)
    assert costs.anomalies([call(ttft_ms=1900)], history, UTC, TODAY) == []  # 3.8x but under 2 s
    assert costs.anomalies([call(ttft_ms=2000)], history, UTC, TODAY) == []  # not over 2 s
    found = costs.anomalies([call(ttft_ms=2001)], history, UTC, TODAY)
    assert kinds(found) == ["ttft"]
    assert found[0].message == (
        "Grade answer at 12:00 took 4.0 times as long to start replying "
        "as a grading call usually does"
    )
    slow = series(10, ttft_ms=1000)
    assert costs.anomalies([call(ttft_ms=2500)], slow, UTC, TODAY) == []  # over 2 s, only 2.5x


def test_total_anomaly_needs_both_ratio_and_five_seconds():
    history = series(10, total_ms=1000)
    assert costs.anomalies([call(total_ms=4900)], history, UTC, TODAY) == []
    assert costs.anomalies([call(total_ms=5000)], history, UTC, TODAY) == []
    found = costs.anomalies([call(total_ms=5001)], history, UTC, TODAY)
    assert kinds(found) == ["total"]
    assert found[0].message == (
        "Grade answer at 12:00 took 5.0 times as long to finish as a grading call usually does"
    )
    slow = series(10, total_ms=3000)
    assert costs.anomalies([call(total_ms=8000)], slow, UTC, TODAY) == []


def test_anomaly_time_is_in_the_given_zone():
    zone = ZoneInfo("Asia/Karachi")  # UTC+5
    found = costs.anomalies([call(total_ms=9000)], series(10), zone, TODAY)
    assert "at 17:00 " in found[0].message


def day_calls(day: date, tokens: int, n: int = 1):
    at = datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)
    return [
        call(at=at + timedelta(minutes=i), input_tokens=tokens, output_tokens=0) for i in range(n)
    ]


def day_found(found):
    return [a for a in found if a.kind == "day"]


def test_day_anomaly_over_twice_the_seven_day_mean():
    history = [c for d in range(1, 8) for c in day_calls(date(2026, 10, d), 100)]
    found = day_found(costs.anomalies(day_calls(date(2026, 10, 8), 201), history, UTC, TODAY))
    assert len(found) == 1
    a = found[0]
    assert a.day == date(2026, 10, 8) and a.call_id is None and a.endpoint is None
    assert a.ratio == pytest.approx(2.01)
    assert a.message == "Tokens on 2026-10-08 were 2.0 times the daily average of the week before"
    assert day_found(costs.anomalies(day_calls(date(2026, 10, 8), 200), history, UTC, TODAY)) == []


def test_day_anomaly_needs_three_days_and_uses_only_the_last_seven():
    two = [c for d in (6, 7) for c in day_calls(date(2026, 10, d), 100)]
    assert day_found(costs.anomalies(day_calls(date(2026, 10, 8), 900), two, UTC, TODAY)) == []
    far = day_calls(date(2026, 10, 1), 100000)  # eight days back: outside the window
    near = [c for d in (2, 3, 4) for c in day_calls(date(2026, 10, d), 100)]
    found = day_found(costs.anomalies(day_calls(date(2026, 10, 9), 250), far + near, UTC, TODAY))
    assert len(found) == 1


def test_days_group_by_local_date():
    zone = ZoneInfo("Asia/Karachi")
    late = call(at=datetime(2026, 10, 20, 20, 0, tzinfo=UTC), input_tokens=10, output_tokens=5)
    assert costs.per_day([late], zone) == [costs.DayTokens(date(2026, 10, 21), 10, 5, 15, 1)]


def test_per_day_sums_input_plus_output_over_all_attempts():
    rows = day_calls(date(2026, 10, 8), 100, n=2) + [
        call(at=datetime(2026, 10, 7, 9, tzinfo=UTC), outcome="error", output_tokens=7)
    ]
    days = costs.per_day(rows, UTC)
    assert [(d.day, d.tokens, d.calls) for d in days] == [
        (date(2026, 10, 7), 107, 1),
        (date(2026, 10, 8), 200, 2),
    ]


def test_per_day_with_no_reported_tokens_is_none_not_zero():
    quiet = call(at=datetime(2026, 10, 7, 9, tzinfo=UTC), input_tokens=None, output_tokens=None)
    half = call(at=datetime(2026, 10, 8, 9, tzinfo=UTC), input_tokens=None, output_tokens=7)
    days = costs.per_day([quiet, half], UTC)
    assert (days[0].input_tokens, days[0].output_tokens, days[0].tokens) == (None, None, None)
    assert days[0].calls == 1
    assert (days[1].input_tokens, days[1].output_tokens, days[1].tokens) == (None, 7, 7)


def test_unknown_endpoint_is_listed_not_a_crash():
    rows = [call(endpoint="brand_new", total_ms=9000), call(endpoint="grade")]
    ends = costs.by_endpoint(rows)
    assert [(b.key, b.label) for b in ends] == [
        ("grade", "Grade answer"),
        ("brand_new", "brand_new"),
    ]
    feats = costs.by_feature(rows)
    assert [b.key for b in feats][-2:] == ["evals", "other"]
    assert feats[-1].label == "Other" and feats[-1].calls == 1
    assert [b.key for b in costs.by_feature([call()])][-1] == "evals"  # no Other row without need
    history = series(10, endpoint="brand_new", total_ms=1000)
    found = costs.anomalies([call(endpoint="brand_new", total_ms=9000)], history, UTC, TODAY)
    assert kinds(found) == ["total"] and "brand_new at 12:00" in found[0].message


def test_endpoint_table_covers_every_gateway_endpoint():
    assert set(typing.get_args(Endpoint)) == set(costs.ENDPOINTS)


def test_failures_listed_not_anomalies():
    err = call(outcome="error", error_kind="timeout", ttft_ms=None, total_ms=90000)
    refused = call(outcome="refused")
    invalid_only = call(outcome="invalid")
    retried = uuid4()
    recovered_bad = call(call_id=retried, outcome="invalid")
    recovered_ok = call(call_id=retried, attempt=2)
    rows = [call(), err, refused, invalid_only, recovered_bad, recovered_ok]
    assert {f.id for f in costs.failures(rows)} == {err.id, refused.id, invalid_only.id}
    assert costs.anomalies([err], series(10), UTC, TODAY) == []


def test_by_feature_groups():
    rows = [
        call(endpoint="lesson"),
        call(endpoint="reteach"),
        call(endpoint="write_web_questions"),
        call(endpoint="regrade", total_ms=3000),
        call(endpoint="grade"),
        call(endpoint="learn_answer"),
        call(endpoint="extend_concepts"),
        call(endpoint="write_eval_case"),
    ]
    got = {b.key: b.calls for b in costs.by_feature(rows)}
    assert got == {
        "lessons": 2,
        "questions": 1,
        "grading": 2,
        "learn": 1,
        "concepts": 1,
        "evals": 1,
    }
    empty = costs.by_feature([])
    assert [b.key for b in empty] == [
        "lessons",
        "questions",
        "grading",
        "learn",
        "concepts",
        "evals",
    ]
    assert empty[0].calls == 0 and empty[0].cost_usd == Decimal(0) and empty[0].total_p95_ms is None


def test_by_endpoint_by_tenant_by_model():
    other = uuid4()
    rows = [
        call(endpoint="grade", output_tokens=100, total_ms=2000),
        call(endpoint="grade", output_tokens=100, total_ms=2000, backup=True, user_id=other),
        call(endpoint="lesson", provider="openrouter", model="m", cost_usd=None),
    ]
    ends = costs.by_endpoint(rows)
    assert [(b.key, b.label, b.calls) for b in ends] == [
        ("lesson", "Lesson", 1),
        ("grade", "Grade answer", 2),
    ]
    grade = ends[1]
    assert grade.output_tokens_per_second == pytest.approx(50.0)
    assert grade.fallbacks == 1
    assert grade.ttft_p50_ms == 500
    assert [(b.key, b.calls) for b in costs.by_tenant(rows)] == [(str(USER), 2), (str(other), 1)]
    models = costs.by_model(rows)
    assert [(b.key, b.calls) for b in models] == [
        ("groq/openai/gpt-oss-120b", 2),
        ("openrouter/m", 1),
    ]
    assert models[1].cost_usd is None and models[1].unpriced_calls == 1


def test_role_health():
    rows = [
        call(role="quiz", total_ms=1000),
        call(role="quiz", total_ms=3000),
        call(role="quiz", outcome="error", total_ms=60000, ttft_ms=None),
        call(role="quiz", outcome="invalid"),
        call(role="teach"),
    ]
    chains = {"quiz": [GROQ, ("openrouter", "m")], "teach": [GROQ], "eval_writer": [GROQ]}
    got = {h.role: h for h in costs.role_health(rows, chains)}
    quiz = got["quiz"]
    assert quiz.main == GROQ and quiz.backup == ("openrouter", "m")
    assert quiz.attempts == 4 and quiz.failed_share == 0.5
    assert quiz.p95_total_ms == 3000
    assert got["teach"].backup is None and got["teach"].failed_share == 0.0
    idle = got["eval_writer"]
    assert idle.attempts == 0 and idle.failed_share is None and idle.p95_total_ms is None
