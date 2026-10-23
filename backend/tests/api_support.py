"""Services and a client for API tests: memory store, fake tutor, fixed clock."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ebb.api.app import create_app
from ebb.api.services import Services
from ebb.config import Settings
from ebb.graph.build import build_graph
from ebb.study import StudySessions
from tests.fakes import FakeTutor, MemoryStore

USER = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def make_services(**overrides: Any) -> Services:
    settings = Settings(_env_file=None, ebb_timezone="UTC", tavily_api_key=None)
    store, tutor, saver = MemoryStore(), FakeTutor(), InMemorySaver()
    store.ensure_profile(USER)
    graph = build_graph(tutor, store, saver, now=lambda: NOW)
    services = Services(
        settings=settings,
        store=store,
        tutor=tutor,
        config_error=None,
        web=None,
        sessions=StudySessions(store, graph, saver, lambda: NOW),
        drafts=None,
        user_id=USER,
        tz=ZoneInfo("UTC"),
        now=lambda: NOW,
    )
    return replace(services, **overrides)


def client(services: Services) -> TestClient:
    return TestClient(create_app(services))
