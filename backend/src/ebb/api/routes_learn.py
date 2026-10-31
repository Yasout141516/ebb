"""A concept's learn phase: its lesson, written and streamed, and the learner's questions.

Each run answers with Server-Sent Events: a `step` as each labelled step starts, `text`
pieces of the lesson or answer being written, a `discard` if the text shown must be wiped
(its model call then failed), and last one `learn` (the waiting turn) or `learned`; or an
`error`, as for study sessions. A concept that isn't the user's (404), a lesson not yet
written when a reply comes (409) and a learn thread driven elsewhere (409) are refused
before the stream opens.
"""

from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse

from ebb.api.routes_study import Sse, encode, failure
from ebb.api.schemas import LearnReplyBody, LessonPage
from ebb.api.services import services_of
from ebb.api.sse import close, event_stream
from ebb.learn.graph import exchange_to_dict, lesson_to_dict
from ebb.learn.service import Discard, Learned, LearnEvent, LearnTurn, TextDelta
from ebb.study import SessionBusy, Step

router = APIRouter()


@router.post("/concepts/{concept_id}/learn")
def start_learn(concept_id: UUID, request: Request) -> EventSourceResponse:
    """The lesson: written now, or the kept one. Learned at once if the concept is."""
    services = services_of(request)
    return _respond(services.learn.start(services.user_id, concept_id))


@router.post("/concepts/{concept_id}/learn/reply")
def reply_learn(concept_id: UUID, body: LearnReplyBody, request: Request) -> EventSourceResponse:
    """A question about the lesson ({ask, kind, ask_id}) or {ready: true}. A question
    already answered gets the current turn back."""
    services = services_of(request)
    sent = body.reply.model_dump(mode="json")
    return _respond(services.learn.reply(services.user_id, concept_id, sent))


@router.get("/concepts/{concept_id}/lesson")
def get_lesson(concept_id: UUID, request: Request) -> LessonPage:
    """The kept lesson and its exchanges. Reads the store only: never writes a lesson."""
    services = services_of(request)
    lesson = services.store.get_lesson(services.user_id, concept_id)
    if lesson is None:
        raise LookupError(f"concept {concept_id} has no lesson")
    exchanges = services.store.exchanges(services.user_id, concept_id)
    return LessonPage(
        lesson=lesson_to_dict(lesson), exchanges=[exchange_to_dict(e) for e in exchanges]
    )


def _respond(events: Iterator[LearnEvent]) -> EventSourceResponse:
    """The run's events as SSE. The first is pulled here, so a thread driven elsewhere
    answers as plain JSON before any stream opens; any other failure is the stream's
    last event."""
    try:
        first = next(events)
    except SessionBusy, LookupError:
        raise
    except Exception as error:
        return event_stream(iter([failure(error)]), encode)
    return event_stream(_items(first, events), encode, source=events)


def _items(first: LearnEvent, events: Iterator[LearnEvent]) -> Iterator[Sse]:
    """Each event as SSE; a failure becomes the last one, so the stream always ends."""
    try:
        yield _sse(first)
        for event in events:
            yield _sse(event)
    except Exception as error:
        yield failure(error)
    finally:
        close(events)  # a reader gone mid-turn: the turn finishes, and its text is kept


def _sse(event: LearnEvent) -> Sse:
    match event:
        case Step(label):
            return "step", {"label": label}
        case TextDelta(text):
            return "text", {"delta": text}
        case Discard():
            return "discard", {}
        case LearnTurn(payload):
            return "learn", {"payload": payload}
        case Learned(concept_id):
            return "learned", {"concept_id": concept_id}
