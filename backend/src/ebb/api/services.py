"""What the routes share, built once at startup (or by a test)."""

import atexit
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Request

from ebb.api.drafts import Drafts
from ebb.clock import utc_now
from ebb.config import ConfigError, Settings, resolve_timezone
from ebb.db.migrate import migrate
from ebb.db.pool import make_pool
from ebb.db.repo import PgStore
from ebb.domain import Subject
from ebb.learn.service import LearnSessions
from ebb.llm.tutor import Tutor
from ebb.store import Store
from ebb.study import StudySessions
from ebb.web import WebSearch


@dataclass
class Services:
    """One instance serves every request. `sessions` is shared by many drivers, so
    the API never calls its `hold`. `tutor` is None when it could not be configured;
    `config_error` then says why. `timezone_error` says why EBB_TIMEZONE was not used
    (`tz` is then UTC)."""

    settings: Settings
    store: Store
    tutor: Tutor | None
    config_error: str | None
    web: WebSearch | None
    sessions: StudySessions
    learn: LearnSessions
    drafts: Drafts
    user_id: UUID
    tz: ZoneInfo
    now: Callable[[], datetime]
    timezone_error: str | None = None

    def require_tutor(self) -> Tutor:
        """The tutor; raises ConfigError, saying why, when it isn't configured."""
        if self.tutor is None:
            raise ConfigError(self.config_error or "The tutor is not configured.")
        return self.tutor


def services_of(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def owned_subject(services: Services, subject_id: UUID) -> Subject:
    """The user's subject; raises LookupError (a 404) for anyone else's or none."""
    subject = services.store.get_subject(services.user_id, subject_id)
    if subject is None:
        raise LookupError(f"subject {subject_id} not found")
    return subject


class NoTutor:
    """Stands in for the tutor when it is not configured: every call says why."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise ConfigError(self.reason)

        return refuse


def build_services(settings: Settings) -> Services:
    from ebb.db.saver import postgres_saver
    from ebb.graph.build import build_graph
    from ebb.learn.graph import build_learn_graph
    from ebb.llm.gateway import StoreRecorder
    from ebb.llm.quota import CappedTutor
    from ebb.llm.tutor import LangChainTutor
    from ebb.web import web_search

    pool = make_pool(settings.database_url)
    atexit.register(pool.close)
    with pool.connection() as conn:
        migrate(conn)
    store = PgStore(pool)
    user_id = settings.ebb_dev_user_id
    store.ensure_profile(user_id)
    timezone_error: str | None = None
    try:
        tz = resolve_timezone(settings.ebb_timezone)
    except ConfigError as error:  # shown on /health; the API still starts
        tz, timezone_error = ZoneInfo("UTC"), str(error)
    store.set_timezone(user_id, tz.key)

    tutor: Tutor | None
    config_error: str | None = None
    try:
        tutor = CappedTutor(
            LangChainTutor.from_settings(settings, recorder=StoreRecorder(store, user_id)),
            store,
            user_id,
            settings.ebb_daily_ai_calls,
            tz,
            utc_now,
        )
    except ConfigError as error:
        tutor, config_error = None, str(error)
    web = web_search(settings)
    saver = postgres_saver(store)
    graph = build_graph(tutor or NoTutor(config_error or ""), store, saver, web=web)
    sessions = StudySessions(store, graph, saver, utc_now)
    learn_graph = build_learn_graph(tutor or NoTutor(config_error or ""), store, saver, web=web)
    learn = LearnSessions(store, learn_graph, saver)
    services = Services(
        settings=settings,
        store=store,
        tutor=tutor,
        config_error=config_error,
        web=web,
        sessions=sessions,
        learn=learn,
        drafts=Drafts(lambda: services.require_tutor(), utc_now),
        user_id=user_id,
        tz=tz,
        now=utc_now,
        timezone_error=timezone_error,
    )
    return services
