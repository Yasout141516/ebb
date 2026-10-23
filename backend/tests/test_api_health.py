import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ebb.api.app import create_app
from ebb.config import ConfigError
from ebb.db.pool import DATABASE_DOWN
from ebb.llm.tutor import AllProvidersExhausted
from ebb.store import SubjectExists
from ebb.study import BUSY, SessionBusy
from tests.api_support import client, make_services


def test_health_ok():
    response = client(make_services()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "tutor": "ok"}


def test_health_reports_missing_groq_key():
    text = "GROQ_API_KEY is not set (get one free at console.groq.com)"
    response = client(make_services(tutor=None, config_error=text)).get("/health")
    assert response.json() == {"database": "ok", "tutor": text}


def test_cors_allows_the_web_origin_only():
    web = client(make_services())
    allowed = web.get("/health", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = web.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


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
