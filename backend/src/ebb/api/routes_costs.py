"""The dev Costs page's numbers. Every route is a 404 while EBB_SHOW_COSTS is off."""

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ebb import costs
from ebb.api.schemas import (
    CostAnomaly,
    CostBreakdown,
    CostCall,
    CostDay,
    CostPrice,
    CostRole,
    CostSummary,
    CostSummaryPage,
    DailyCalls,
    ModelRef,
)
from ebb.api.services import Services, services_of
from ebb.domain import LlmCall
from ebb.llm.prices import PRICES

RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
HISTORY = timedelta(days=30)  # how far back an anomaly looks for what is usual


def _shown(request: Request) -> Services:
    services = services_of(request)
    if not services.settings.ebb_show_costs:
        raise LookupError("the costs page is off")
    return services


router = APIRouter(prefix="/costs")
Shown = Annotated[Services, Depends(_shown)]


class Filters:
    """The query the summary and the call list share. No `tenant` means every tenant."""

    def __init__(
        self,
        range: Literal["24h", "7d", "30d"] = "7d",  # noqa: A002 - the query parameter's name
        tenant: UUID | None = None,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> None:
        self.range = range
        self.tenant = tenant
        self.endpoint = endpoint
        self.model = model


Filtered = Annotated[Filters, Depends()]


def _records(services: Services, f: Filters) -> tuple[list[LlmCall], list[LlmCall]]:
    """(the range's attempts, the 30 days before them), both narrowed by the filters."""
    until = services.now()
    since = until - RANGES[f.range]
    user = f.tenant or services.user_id
    everyone = f.tenant is None

    def narrowed(rows: Sequence[LlmCall]) -> list[LlmCall]:
        return [
            c
            for c in rows
            if (f.endpoint is None or c.endpoint == f.endpoint)
            and (f.model is None or c.model == f.model)
        ]

    return (
        narrowed(services.store.llm_calls(user, since, until, all_tenants=everyone)),
        narrowed(services.store.llm_calls(user, since - HISTORY, since, all_tenants=everyone)),
    )


def _today(services: Services) -> date:
    return services.now().astimezone(services.tz).date()


def _row(call: LlmCall, anomaly: str | None = None) -> CostCall:
    return CostCall(
        **asdict(call), endpoint_label=costs.endpoint_label(call.endpoint), anomaly=anomaly
    )


@router.get("/summary")
def cost_summary(services: Shown, f: Filtered) -> CostSummaryPage:
    calls, history = _records(services, f)
    roles = {
        role: [(m.provider, m.model) for m in chain if m is not None]
        for role, chain in services.settings.role_models().items()
    }
    return CostSummaryPage(
        summary=CostSummary(**asdict(costs.summary(calls))),
        by_feature=[CostBreakdown(**asdict(b)) for b in costs.by_feature(calls)],
        per_day=[CostDay(**asdict(d)) for d in costs.per_day(calls, services.tz)],
        roles=[
            CostRole(
                role=r.role,
                main=ModelRef(provider=r.main[0], model=r.main[1]),
                backup=ModelRef(provider=r.backup[0], model=r.backup[1]) if r.backup else None,
                attempts=r.attempts,
                failed_share=r.failed_share,
                p95_total_ms=r.p95_total_ms,
            )
            for r in costs.role_health(calls, roles)
        ],
        by_model=[CostBreakdown(**asdict(b)) for b in costs.by_model(calls)],
        by_endpoint=[CostBreakdown(**asdict(b)) for b in costs.by_endpoint(calls)],
        by_tenant=[CostBreakdown(**asdict(b)) for b in costs.by_tenant(calls)],
        anomalies=[
            CostAnomaly(**asdict(a))
            for a in costs.anomalies(calls, history, services.tz, _today(services))
        ],
        failures=[_row(c) for c in costs.failures(calls)],
        daily_calls=DailyCalls(
            used=services.store.ai_calls_used(services.user_id, _today(services)),
            limit=services.settings.ebb_daily_ai_calls,
        ),
    )


@router.get("/calls")
def cost_calls(
    services: Shown, f: Filtered, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[CostCall]:
    calls, history = _records(services, f)
    first: dict[UUID, str] = {}  # call_id -> the message of its first anomaly
    for a in costs.anomalies(calls, history, services.tz, _today(services)):
        if a.call_id is not None:
            first.setdefault(a.call_id, a.message)
    newest = sorted(calls, key=lambda c: (c.at, c.id), reverse=True)[:limit]
    return [_row(c, first.get(c.call_id)) for c in newest]


@router.get("/prices")
def cost_prices(services: Shown) -> list[CostPrice]:
    return [
        CostPrice(provider=provider, model=model, **asdict(price))
        for (provider, model), price in PRICES.items()
    ]
