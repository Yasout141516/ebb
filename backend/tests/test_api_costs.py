from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from ebb.domain import LlmCall
from tests.api_support import NOW, USER, client, make_services

OTHER = UUID("00000000-0000-0000-0000-000000000002")


def _call(at, **fields):
    base = {
        "id": uuid4(),
        "call_id": uuid4(),
        "request_id": None,
        "user_id": USER,
        "at": at,
        "role": "teach",
        "endpoint": "lesson",
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "attempt": 1,
        "outcome": "ok",
        "input_tokens": 100,
        "output_tokens": 100,
        "reasoning_tokens": None,
        "ttft_ms": 300,
        "total_ms": 1000,
        "cost_usd": Decimal("0.00042"),
        "error_kind": None,
        "backup": False,
    }
    return LlmCall(**{**base, **fields})


def _record(services, *calls):
    for c in calls:
        services.store.record_llm_call(c.user_id, c)


def test_costs_404_when_hidden():
    services = make_services()
    services.settings.ebb_show_costs = False
    web = client(services)
    for path in ("/costs/summary", "/costs/calls", "/costs/prices"):
        response = web.get(path)
        assert response.status_code == 404
        assert response.json() == {"error": "Not found."}


def test_health_reports_costs_flag():
    services = make_services()
    assert client(services).get("/health").json()["costs"] is True
    services.settings.ebb_show_costs = False
    assert client(services).get("/health").json()["costs"] is False


def test_summary_filters_by_endpoint_and_model():
    services = make_services()
    hour = timedelta(hours=1)
    _record(
        services,
        _call(NOW - hour, endpoint="lesson"),
        _call(NOW - 2 * hour, endpoint="grade", cost_usd=Decimal("0.001")),
        _call(NOW - 3 * hour, endpoint="grade", model="other-model", cost_usd=None),
        _call(NOW - 4 * hour, endpoint="grade", user_id=OTHER),
    )
    web = client(services)

    everything = web.get("/costs/summary").json()
    assert everything["summary"]["calls"] == 4
    assert {t["key"] for t in everything["by_tenant"]} == {str(USER), str(OTHER)}

    body = web.get("/costs/summary?endpoint=grade&model=openai/gpt-oss-120b").json()
    assert body["summary"]["calls"] == 2
    assert body["summary"]["cost_usd"] == "0.00142"
    assert [e["key"] for e in body["by_endpoint"]] == ["grade"]
    assert [m["key"] for m in body["by_model"]] == ["groq/openai/gpt-oss-120b"]

    mine = web.get(f"/costs/summary?tenant={USER}").json()
    assert mine["summary"]["calls"] == 3
    # an unpriced call: the total is unknown, so null, not 0
    assert mine["summary"]["cost_usd"] is None
    assert mine["summary"]["unpriced_calls"] == 1
    assert mine["summary"]["unreported_calls"] == 0


def test_summary_cost_is_unknown_when_no_tokens_were_reported():
    services = make_services()
    _record(
        services,
        _call(NOW - timedelta(hours=1), input_tokens=None, output_tokens=None, cost_usd=None),
    )
    body = client(services).get("/costs/summary").json()
    assert body["summary"]["cost_usd"] is None  # not "0"
    assert body["summary"]["unreported_calls"] == 1
    assert body["by_endpoint"][0]["cost_usd"] is None


def test_summary_shape_and_configured_roles_without_calls():
    body = client(make_services()).get("/costs/summary").json()
    assert set(body) == {
        "summary",
        "by_feature",
        "per_day",
        "roles",
        "by_model",
        "by_endpoint",
        "by_tenant",
        "anomalies",
        "failures",
        "daily_calls",
    }
    assert body["summary"]["calls"] == 0
    assert body["summary"]["input_tokens"] is None
    assert body["summary"]["ttft_p95_ms"] is None
    assert body["summary"]["fallback_rate"] is None
    assert [r["role"] for r in body["roles"]] == ["teach", "quiz", "eval_writer"]
    teach = body["roles"][0]
    assert teach["main"] == {"provider": "groq", "model": "openai/gpt-oss-120b"}
    assert teach["backup"] is None
    assert teach["attempts"] == 0
    assert teach["failed_share"] is None
    assert body["anomalies"] == []


def test_summary_range_and_validation():
    services = make_services()
    _record(
        services,
        _call(NOW - timedelta(hours=2)),
        _call(NOW - timedelta(days=3)),
        _call(NOW - timedelta(days=20)),
    )
    web = client(services)
    assert web.get("/costs/summary?range=24h").json()["summary"]["calls"] == 1
    assert web.get("/costs/summary").json()["summary"]["calls"] == 2  # 7d by default
    assert web.get("/costs/summary?range=30d").json()["summary"]["calls"] == 3
    assert web.get("/costs/summary?range=1y").status_code == 422


def test_summary_per_day_failures_and_dates():
    services = make_services()
    failed = _call(
        NOW - timedelta(hours=2),
        outcome="error",
        error_kind="timeout",
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )
    _record(services, _call(NOW - timedelta(hours=1)), failed)
    body = client(services).get("/costs/summary").json()
    assert body["per_day"] == [
        {"day": "2026-10-23", "input_tokens": 100, "output_tokens": 100, "tokens": 200, "calls": 2}
    ]
    assert [f["id"] for f in body["failures"]] == [str(failed.id)]
    assert body["failures"][0]["error_kind"] == "timeout"
    assert body["failures"][0]["input_tokens"] is None
    assert body["failures"][0]["cost_usd"] is None
    assert body["failures"][0]["endpoint_label"] == "Lesson"


def test_calls_newest_first_with_anomaly_message():
    services = make_services()
    usual = [_call(NOW - timedelta(hours=30 - i)) for i in range(12)]
    spike = _call(NOW - timedelta(hours=1), output_tokens=1000)
    latest = _call(NOW - timedelta(minutes=10), request_id=uuid4())
    _record(services, *usual, spike, latest)
    web = client(services)

    rows = web.get("/costs/calls?limit=3").json()
    assert [r["id"] for r in rows] == [str(latest.id), str(spike.id), str(usual[-1].id)]
    assert rows[0]["anomaly"] is None
    assert rows[0]["request_id"] == str(latest.request_id)
    assert rows[0]["call_id"] == str(latest.call_id)
    assert rows[0]["endpoint_label"] == "Lesson"
    assert rows[0]["cost_usd"] == "0.00042"
    assert rows[0]["at"] == "2026-10-23T11:50:00Z"
    assert rows[1]["anomaly"].endswith("times the output tokens a lesson call usually uses")
    assert rows[1]["anomaly"].startswith("Lesson at 11:00 used 10.0")
    assert rows[2]["anomaly"] is None
    assert rows[1]["reasoning_tokens"] is None
    assert rows[1]["backup"] is False

    assert len(web.get("/costs/calls").json()) == 14
    assert web.get("/costs/calls?limit=501").status_code == 422
    assert web.get("/costs/calls?limit=0").status_code == 422


def test_anomaly_baseline_reaches_back_before_the_range():
    services = make_services()
    old = [_call(NOW - timedelta(days=10, hours=i)) for i in range(12)]
    spike = _call(NOW - timedelta(hours=1), output_tokens=1000)
    _record(services, *old, spike)
    body = client(services).get("/costs/summary?range=24h").json()
    assert [a["call_id"] for a in body["anomalies"]] == [str(spike.call_id)]
    assert body["anomalies"][0]["kind"] == "tokens"
    assert body["anomalies"][0]["day"] is None


def test_prices_lists_known_models_with_exact_decimals():
    rows = client(make_services()).get("/costs/prices").json()
    row = next(r for r in rows if r["model"] == "openai/gpt-oss-120b")
    assert row["provider"] == "groq"
    assert row["input_per_m"] == "0.15"
    assert row["output_per_m"] == "0.60"
    assert row["free_tier"] is True
    assert row["as_of"] == "2026-10-28"
    assert row["source"].startswith("https://")


def test_summary_daily_calls_is_today_for_the_profile_user_whatever_the_filters():
    services = make_services()
    services.settings.ebb_daily_ai_calls = 300
    for _ in range(3):
        services.store.take_ai_call(USER, NOW.date(), 300)
    services.store.take_ai_call(USER, NOW.date() - timedelta(days=1), 300)  # yesterday: not counted
    services.store.take_ai_call(OTHER, NOW.date(), 300)  # another user: not counted
    web = client(services)

    assert web.get("/costs/summary").json()["daily_calls"] == {"used": 3, "limit": 300}
    narrowed = web.get(f"/costs/summary?tenant={OTHER}&endpoint=grade&range=24h").json()
    assert narrowed["daily_calls"] == {"used": 3, "limit": 300}
