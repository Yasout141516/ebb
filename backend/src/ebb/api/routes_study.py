"""Study sessions: start or resume one, reply to its questions, and end it.

Each run answers with Server-Sent Events: `started` (a new session only), a `step` as each
labelled step starts, then the `turn` waiting for a reply or the `finished` summary; or an
`error` when the tutor fails outside the graph's own handling, isn't configured, or anything
else goes wrong. A session driven elsewhere (409) or not found (404) is refused before the
stream opens.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.sse import EventSourceResponse, format_sse_event

from ebb.api.drafts import SOMETHING_WRONG
from ebb.api.schemas import AnswerReply, Driver, DriverId, Offer, ReplyBody, SessionOffer
from ebb.api.services import Services, owned_subject, services_of
from ebb.api.sse import close, event_stream
from ebb.config import ConfigError
from ebb.db.pool import DATABASE_DOWN
from ebb.grading import parse_choice
from ebb.llm.tutor import TutorUnavailable
from ebb.study import Event, Finished, Pending, SessionBusy, SessionNotFound, Step
from ebb.ui import next_review_line

router = APIRouter()
log = logging.getLogger(__name__)

type Sse = tuple[str, dict[str, Any]]  # an event's name and data


@router.get("/subjects/{subject_id}/session")
def session_offer(
    subject_id: UUID, driver: Annotated[DriverId, Query()], request: Request
) -> SessionOffer:
    """The subject's session to resume, if it has one."""
    services = services_of(request)
    owned_subject(services, subject_id)
    services.sessions.housekeeping(services.user_id)
    offer = services.sessions.offer(services.user_id, subject_id, driver)
    return SessionOffer(offer=None if offer is None else Offer(**asdict(offer)))


@router.post("/subjects/{subject_id}/session")
def start_session(subject_id: UUID, body: Driver, request: Request) -> EventSourceResponse:
    services = services_of(request)
    services.sessions.housekeeping(services.user_id)
    thread_id, greeting, events = services.sessions.start(services.user_id, subject_id, body.driver)
    started = ("started", {"thread_id": thread_id, "greeting": greeting})
    return _respond(services, events, started)


@router.post("/sessions/{thread_id}/resume")
def resume_session(thread_id: UUID, body: Driver, request: Request) -> EventSourceResponse:
    """The waiting question, or the step a closed tab cut off finished first."""
    services = services_of(request)
    events = services.sessions.resume(services.user_id, str(thread_id), body.driver)
    return _respond(services, events)


@router.post("/sessions/{thread_id}/reply")
def reply(thread_id: UUID, body: ReplyBody, request: Request) -> EventSourceResponse:
    """A reply already applied (the same attempt or dispute id) gets the current turn back."""
    services = services_of(request)
    sent = body.reply.model_dump(mode="json")
    if isinstance(body.reply, AnswerReply):
        _check_choice(services, str(thread_id), sent)
    events = services.sessions.reply(services.user_id, str(thread_id), body.driver, sent)
    return _respond(services, events)


@router.post("/sessions/{thread_id}/discard", status_code=204)
def discard_session(thread_id: UUID, body: Driver, request: Request) -> None:
    """Start fresh: the session ends, unless another tab or terminal is driving it."""
    services = services_of(request)
    services.sessions.discard(services.user_id, str(thread_id), body.driver)


@router.post("/sessions/{thread_id}/release", status_code=204)
def release_session(
    thread_id: UUID, driver: Annotated[DriverId, Query()], request: Request
) -> None:
    """A closing tab lets go of the session. The driver comes in the query: a body would
    make the browser send a preflight first, which a page being closed can't wait for."""
    services = services_of(request)
    services.sessions.release(services.user_id, str(thread_id), driver)


@router.post("/sessions/{thread_id}/heartbeat", status_code=204)
def heartbeat(thread_id: UUID, body: Driver, request: Request) -> None:
    """Keep this tab's hold on the session while its question waits."""
    services = services_of(request)
    if not services.sessions.heartbeat(services.user_id, str(thread_id), body.driver):
        raise SessionBusy(str(thread_id))


def _check_choice(services: Services, thread_id: str, reply: dict[str, Any]) -> None:
    """A multiple-choice answer must pick one of the waiting question's options. An answer
    already applied isn't checked against the question after it: it gets that turn back."""
    pending = services.sessions.pending(services.user_id, thread_id, reply)
    options = [] if pending is None else pending["question"]["options"]
    answer = reply["answer"]
    if options and parse_choice(options, answer) is None:
        error = {
            "type": "value_error",
            "loc": ("body", "reply", "answer"),
            "msg": f"Choose an option from 1 to {len(options)}.",
            "input": answer,
        }
        raise RequestValidationError([error])


def _respond(services: Services, events: Iterator[Event], *before: Sse) -> EventSourceResponse:
    """The run's events as SSE, after `before`. The first is pulled here, so a session
    driven elsewhere or not found answers as plain JSON before any stream opens; any other
    failure is the stream's last event."""
    try:
        first = next(events)
    except SessionBusy, SessionNotFound:
        raise
    except Exception as error:
        return event_stream(iter([*before, _failure(error)]), _encode)
    return event_stream(_items(services, before, first, events), _encode)


def _items(
    services: Services, before: tuple[Sse, ...], first: Event, events: Iterator[Event]
) -> Iterator[Sse]:
    """`before`, then each event as SSE; a failure becomes the last one, so the stream
    always ends."""
    try:
        yield from before
        yield _sse(services, first)
        for event in events:
            yield _sse(services, event)
    except Exception as error:
        yield _failure(error)
    finally:
        close(events)  # a reader gone mid-turn: the turn finishes, and its answer counts


def _failure(error: Exception) -> Sse:
    """The `error` event: {error} only for a configuration problem (nothing to retry);
    {notice} for a failure worth trying again."""
    match error:
        case TutorUnavailable():
            return "error", {"notice": error.notice}
        case ConfigError():
            return "error", {"error": str(error)}
        case psycopg.Error():
            log.error("study run failed: database error", exc_info=error)
            return "error", {"notice": DATABASE_DOWN}
        case _:  # the traceback goes to the log
            log.error("study run failed", exc_info=error)
            return "error", {"notice": SOMETHING_WRONG}


def _sse(services: Services, event: Event) -> Sse:
    match event:
        case Step(label):
            return "step", {"label": label}
        case Pending(payload):
            return "turn", {"payload": payload}
        case Finished():
            concepts = services.store.list_concepts(services.user_id, event.subject_id)
            return "finished", {
                "results": event.results,
                "final": event.final,
                "caught_up": event.caught_up,
                "next_review_at": event.next_review_at,
                "next_review_line": next_review_line(concepts, services.now(), services.tz),
            }


def _encode(item: Sse) -> bytes:
    name, data = item
    return format_sse_event(event=name, data_str=json.dumps(jsonable_encoder(data)))
