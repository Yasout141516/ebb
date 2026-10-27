import os
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ebb.api.app import create_app
from ebb.api.services import build_services
from ebb.config import ConfigError, Settings
from ebb.db.pool import DATABASE_DOWN
from ebb.llm.tutor import AllProvidersExhausted
from ebb.store import SubjectExists
from ebb.study import BUSY, SessionBusy
from tests.api_support import client, make_services
from tests.conftest import DEFAULT_TEST_URL


def test_health_ok():
    response = client(make_services()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "tutor": "ok", "timezone": "ok"}


def test_health_reports_missing_groq_key():
    text = "GROQ_API_KEY is not set — EBB_TEACH_MODEL uses Groq (get one free at console.groq.com)"
    response = client(make_services(tutor=None, config_error=text)).get("/health")
    assert response.json() == {"database": "ok", "tutor": text, "timezone": "ok"}


def test_a_bad_time_zone_falls_back_to_utc_and_shows_on_health(pg_pool):
    settings = Settings(
        _env_file=None,
        database_url=os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL),
        ebb_timezone="Mars/Olympus_Mons",
        groq_api_key=None,
        tavily_api_key=None,
    )

    services = build_services(settings)  # no crash at startup
    try:
        health = client(services).get("/health").json()
    finally:
        services.store.pool.close()

    assert services.tz == ZoneInfo("UTC")
    assert health["timezone"] == "EBB_TIMEZONE 'Mars/Olympus_Mons' is not a known time zone"


def test_cors_allows_the_web_origin_only():
    web = client(make_services())
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):  # the same website
        allowed = web.get("/health", headers={"Origin": origin})
        assert allowed.headers["access-control-allow-origin"] == origin
    other = web.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_cors_takes_a_list_of_web_origins():
    services = make_services()
    services.settings.ebb_web_origin = " http://pc.lan:3000 , http://localhost:3100"
    web = client(services)

    for origin in ("http://pc.lan:3000", "http://localhost:3100"):
        allowed = web.get("/health", headers={"Origin": origin})
        assert allowed.headers["access-control-allow-origin"] == origin
    default = web.get("/health", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in default.headers


def test_health_reports_a_database_that_is_down(monkeypatch):
    services = make_services()

    def down():
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(services.store, "ping", down)
    response = client(services).get("/health")
    assert response.json()["database"] == DATABASE_DOWN


def test_cors_preflight_for_a_post_from_the_web_origin():
    response = client(make_services()).options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]


class Body(BaseModel):
    count: int


def _raising(error: Exception):
    def route():
        raise error

    return route


@pytest.mark.parametrize(
    ("error", "status", "body"),
    [
        (AllProvidersExhausted(), 503, {"notice": AllProvidersExhausted.notice}),
        (ConfigError("GROQ_API_KEY is not set"), 503, {"error": "GROQ_API_KEY is not set"}),
        (
            SubjectExists("Networks"),
            409,
            {"error": "Networks already exists \u2014 add notes to it from its Sources list."},
        ),
        (SessionBusy(), 409, {"error": BUSY}),
        (LookupError("nope"), 404, {"error": "Not found."}),
    ],
)
def test_errors_map_to_their_status_and_body(error, status, body):
    app = create_app(make_services())
    app.add_api_route("/boom", _raising(error))
    response = TestClient(app).get("/boom")
    assert response.status_code == status
    assert response.json() == body


def test_a_bad_request_body_is_a_422():
    app = create_app(make_services())

    @app.post("/typed")
    def typed(body: Body) -> Body:
        return body

    response = TestClient(app).post("/typed", json={"count": "many"})
    assert response.status_code == 422
