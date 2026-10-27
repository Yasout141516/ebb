"""Study sessions over HTTP: each run's steps, then the turn or the summary, arrive as SSE."""

import asyncio
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi.sse import format_sse_event
from langgraph.checkpoint.memory import InMemorySaver

from ebb.api.app import create_app
from ebb.api.services import NoTutor, Services
from ebb.api.sse import event_stream
from ebb.db.pool import DATABASE_DOWN
from ebb.graph.build import build_graph
from ebb.llm.quota import CappedTutor, UserQuotaExceeded
from ebb.llm.tutor import EXHAUSTED_NOTICE, AllProvidersExhausted, Tutor
from ebb.study import BUSY, Step, StudySessions
from ebb.ui import next_review_line
from tests.api_support import NOW, USER, client, make_services
from tests.fakes import FakeTutor, make_subject

type Sse = tuple[str, dict[str, Any]]

BUSY_REPLY = {"error": BUSY}
OTHER = UUID("00000000-0000-0000-0000-000000000002")


class Clock:
    def __init__(self) -> None:
        self.at = NOW

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


def _with(services: Services, tutor: Tutor, clock: Clock | None = None) -> Services:
    """`services` with its sessions run by a graph over `tutor` (and `clock`)."""
    now = clock or (lambda: NOW)
    saver = InMemorySaver()
    graph = build_graph(tutor, services.store, saver, now=now)
    return replace(services, sessions=StudySessions(services.store, graph, saver, now))


def _parse(text: str) -> list[Sse]:
    events = []
    for block in text.strip().split("\n\n"):
        event, data = block.split("\n")
        events.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def _events(response) -> list[Sse]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _parse(response.text)


def _start(web, subject_id: UUID, driver: str = "tab-a") -> tuple[str, list[Sse]]:
    events = _events(web.post(f"/subjects/{subject_id}/session", json={"driver": driver}))
    name, started = events[0]
    assert name == "started"
    return started["thread_id"], events


def _answer(payload: dict[str, Any], text: str | None = None) -> dict[str, Any]:
    """A reply to the question in `payload`: right unless `text` says otherwise."""
    right = "1" if payload["question"]["options"] else "right"
    return {"attempt_id": str(uuid4()), "answer": text or right}


def _reply(web, thread_id: str, reply: dict[str, Any], driver: str = "tab-a"):
    return web.post(f"/sessions/{thread_id}/reply", json={"driver": driver, "reply": reply})


def _turn(events: list[Sse]) -> dict[str, Any]:
    name, data = events[-1]
    assert name == "turn", events
    return data["payload"]


def test_full_session_over_sse():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    assert web.get(f"/subjects/{subject_id}/session?driver=tab-a").json() == {"offer": None}

    thread_id, events = _start(web, subject_id)

    assert events[0][1]["greeting"] == "Nothing is due. Let's learn something new."
    assert ("step", {"label": "Writing questions…"}) in events
    payload = _turn(events)
    assert (payload["concept"], payload["number"]) == ("Concept 1", 1)
    offer = web.get(f"/subjects/{subject_id}/session?driver=tab-a").json()["offer"]
    assert offer["thread_id"] == thread_id
    assert (offer["concept"], offer["number"], offer["busy"]) == ("Concept 1", 1, False)

    for _ in range(3):
        events = _events(_reply(web, thread_id, _answer(payload)))
        assert events[0][0] == "step"
        payload = _turn(events)
        assert payload["feedback"]["value"] == "correct"
    events = _events(_reply(web, thread_id, {"quit": True}))

    name, finished = events[-1]
    assert name == "finished"
    assert finished["results"][0]["concept"] == "Concept 1"
    assert sum(r["tally"]["correct"] for r in finished["results"]) == 3
    assert finished["final"] == {"grade": None, "lesson": None, "notices": []}
    assert finished["caught_up"] is False
    soonest = services.store.next_review_at(USER, subject_id)
    assert soonest is not None and datetime.fromisoformat(finished["next_review_at"]) == soonest
    concepts = services.store.list_concepts(USER, subject_id)
    assert finished["next_review_line"] == next_review_line(concepts, NOW, ZoneInfo("UTC"))
    assert finished["next_review_line"].startswith("Next review: ")
    assert web.get(f"/subjects/{subject_id}/session?driver=tab-a").json() == {"offer": None}


def test_double_reply_counts_once_and_returns_the_same_turn():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id)
    answer = _answer(_turn(events))

    once = _events(_reply(web, thread_id, answer))
    twice = _events(_reply(web, thread_id, answer))

    assert twice == [once[-1]]  # the graph didn't run again
    assert len(services.store.attempts) == 1


def _post_and_leave(app, path: str, body: dict[str, Any]) -> list[Sse]:
    """POST `body` straight to the ASGI app and go away once the first `step` arrives, as a
    closed tab does; returns what was sent before that."""
    data = json.dumps(body).encode()
    sent: list[dict] = []

    async def run() -> None:
        stepped = asyncio.Event()
        asked = 0

        async def receive() -> dict:
            nonlocal asked
            asked += 1
            if asked == 1:
                return {"type": "http.request", "body": data, "more_body": False}
            await stepped.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)
            if b"event: step" in message.get("body", b""):
                stepped.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},  # as uvicorn says
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)

    asyncio.run(asyncio.wait_for(run(), 30))  # a regression fails here instead of hanging
    assert sent[0]["status"] == 200
    return _parse(b"".join(m.get("body", b"") for m in sent[1:]).decode())


def test_disconnect_mid_turn_then_resume_finishes_once():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id)
    wrong = _answer(_turn(events), "wrong")  # graded, then a lesson: two labelled steps

    seen = _post_and_leave(
        create_app(services), f"/sessions/{thread_id}/reply", {"driver": "tab-a", "reply": wrong}
    )

    assert seen[0] == ("step", {"label": "Grading…"})
    assert all(name == "step" for name, _ in seen)  # gone before the turn
    offer = web.get(f"/subjects/{subject_id}/session?driver=tab-a").json()["offer"]
    assert offer["thread_id"] == thread_id and offer["busy"] is False
    payload = _turn(_events(web.post(f"/sessions/{thread_id}/resume", json={"driver": "tab-a"})))
    assert (payload["concept"], payload["number"]) == (offer["concept"], offer["number"])
    assert payload["feedback"]["value"] == "incorrect"
    assert payload["lesson"] is not None
    assert len(services.store.attempts) == 1
    assert services.store.locks == set()


def test_a_reader_going_away_closes_the_events_in_a_worker_thread():
    closed_on: list[threading.Thread] = []
    left = threading.Event()

    def events() -> Iterator[str]:
        try:
            yield "a"
            left.wait()  # the next event is still being made when the reader leaves
            yield from ("b", "c")
        except GeneratorExit:
            closed_on.append(threading.current_thread())
            raise

    response = event_stream(events(), lambda e: format_sse_event(event="x", data_str=e))

    async def run() -> None:
        first_sent = asyncio.Event()

        async def receive() -> dict:
            await first_sent.wait()
            left.set()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message.get("body"):
                first_sent.set()

        await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)

    asyncio.run(run())
    # Closed mid-stream, and not on the event loop's thread.
    assert len(closed_on) == 1 and closed_on[0] is not threading.main_thread()


def test_second_tab_gets_409_busy():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id, driver="tab-a")

    replied = _reply(web, thread_id, _answer(_turn(events)), driver="tab-b")
    resumed = web.post(f"/sessions/{thread_id}/resume", json={"driver": "tab-b"})
    beat = web.post(f"/sessions/{thread_id}/heartbeat", json={"driver": "tab-b"})

    for response in (replied, resumed, beat):
        assert (response.status_code, response.json()) == (409, BUSY_REPLY)
    offer = web.get(f"/subjects/{subject_id}/session?driver=tab-b").json()["offer"]
    assert offer["busy"] is True
    assert services.store.attempts == {}


def test_heartbeat_keeps_the_lease():
    clock = Clock()
    services = _with(make_services(), FakeTutor(), clock)
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, _ = _start(web, subject_id, driver="tab-a")

    clock.advance(seconds=60)
    beat = web.post(f"/sessions/{thread_id}/heartbeat", json={"driver": "tab-a"})
    clock.advance(seconds=60)  # 120 s after the turn, 60 s after the heartbeat
    kept = web.post(f"/sessions/{thread_id}/resume", json={"driver": "tab-b"})
    clock.advance(seconds=31)  # the lease ran out
    taken = web.post(f"/sessions/{thread_id}/resume", json={"driver": "tab-b"})

    assert beat.status_code == 204
    assert (kept.status_code, kept.json()) == (409, BUSY_REPLY)
    assert _turn(_events(taken))["number"] == 1


def test_blank_answer_is_422():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id)

    response = _reply(web, thread_id, _answer(_turn(events), "   \n "))

    assert response.status_code == 422
    assert services.store.attempts == {}


def test_mcq_answer_5_is_422():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id)
    payload = _turn(events)
    while not payload["question"]["options"]:
        payload = _turn(_events(_reply(web, thread_id, _answer(payload))))
    answered = len(services.store.attempts)

    response = _reply(web, thread_id, _answer(payload, "5"))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "reply", "answer"]
    assert len(services.store.attempts) == answered
    by_text = _turn(_events(_reply(web, thread_id, _answer(payload, "Right option"))))
    assert by_text["feedback"]["value"] == "correct"


def test_malformed_dispute_id_is_422():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id)
    events = _events(_reply(web, thread_id, _answer(_turn(events), "meh")))
    assert _turn(events)["can_dispute"] is True

    malformed = {"id": "not-a-uuid", "reason": "correct", "explanation": ""}
    unknown_reason = {"id": str(uuid4()), "reason": "unfair", "explanation": ""}
    for dispute in (malformed, unknown_reason):
        assert _reply(web, thread_id, {"dispute": dispute}).status_code == 422
    assert _reply(web, thread_id, {"quit": False}).status_code == 422
    assert services.store.disputes == {}


def test_the_daily_cap_reaches_the_stream_as_notices():
    """A refused generation ends the session with the notice; a refused grade comes back
    on the same question with it."""
    services = make_services()
    capped = CappedTutor(FakeTutor(), services.store, USER, 0, ZoneInfo("UTC"), lambda: NOW)
    services = _with(services, capped)
    first_subject = make_subject(services.store, USER, concepts=2)
    web = client(services)

    _, events = _start(web, first_subject)
    name, finished = events[-1]
    assert name == "finished"
    assert finished["final"]["notices"] == [UserQuotaExceeded.notice]

    capped.limit = 300
    thread_id, events = _start(web, make_subject(services.store, USER, name="Biology"))
    payload = _turn(events)
    capped.limit = sum(services.store.ai_calls.values())  # spent
    again = _turn(_events(_reply(web, thread_id, _answer(payload))))

    assert again["notices"] == [UserQuotaExceeded.notice]
    assert again["question"]["prompt"] == payload["question"]["prompt"]
    assert services.store.attempts == {}


def test_a_config_error_mid_stream_sends_an_error_event():
    services = _with(make_services(), NoTutor("GROQ_API_KEY is not set."))
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)

    _, events = _start(web, subject_id)

    assert events[1:] == [
        ("step", {"label": "Writing questions…"}),
        ("error", {"error": "GROQ_API_KEY is not set."}),
    ]


class _Failing:
    """Sessions whose run fails outside the graph's own handling: after its first step, or
    before it when `at_once`."""

    def __init__(self, error: Exception, at_once: bool = False) -> None:
        self.error, self.at_once = error, at_once

    def housekeeping(self, user_id: UUID) -> None:
        pass

    def start(self, user_id: UUID, subject_id: UUID, driver: str) -> tuple[str, str, Any]:
        def events() -> Iterator[Step]:
            if not self.at_once:
                yield Step("Writing questions…")
            raise self.error

        return "thread-1", "Hello.", events()


def test_a_tutor_failure_escaping_the_graph_sends_an_error_event_with_its_notice():
    services = replace(make_services(), sessions=_Failing(AllProvidersExhausted("down")))
    subject_id = make_subject(services.store, USER, concepts=2)

    _, events = _start(client(services), subject_id)

    assert events[-1] == ("error", {"notice": EXHAUSTED_NOTICE})


def test_a_database_failure_is_a_notice_the_learner_can_retry():
    services = replace(make_services(), sessions=_Failing(psycopg.OperationalError("gone")))
    subject_id = make_subject(services.store, USER, concepts=2)

    _, events = _start(client(services), subject_id)

    assert events[-1] == ("error", {"notice": DATABASE_DOWN})


def test_an_unexpected_failure_still_ends_the_stream():
    services = replace(make_services(), sessions=_Failing(RuntimeError("bug")))
    subject_id = make_subject(services.store, USER, concepts=2)

    _, events = _start(client(services), subject_id)

    assert events[-1] == ("error", {"notice": "Something went wrong — try again."})


def test_a_key_error_is_a_failure_not_a_404_before_or_during_the_stream():
    for at_once in (True, False):
        services = replace(make_services(), sessions=_Failing(KeyError("concept"), at_once))
        subject_id = make_subject(services.store, USER, concepts=2)

        _, events = _start(client(services), subject_id)

        assert events[-1] == ("error", {"notice": "Something went wrong — try again."})
        assert len(events) == (2 if at_once else 3)  # started, [step], error


def test_heartbeat_on_a_session_that_isnt_open_is_404():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, _ = _start(web, subject_id)
    _events(_reply(web, thread_id, {"quit": True}))

    ended = web.post(f"/sessions/{thread_id}/heartbeat", json={"driver": "tab-a"})
    unknown = web.post(f"/sessions/{uuid4()}/heartbeat", json={"driver": "tab-a"})

    for response in (ended, unknown):
        assert (response.status_code, response.json()) == (404, {"error": "Not found."})


def test_another_users_subject_and_session_are_404():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    thread_id, events = _start(client(services), subject_id)
    services.store.ensure_profile(OTHER)
    other = client(replace(services, user_id=OTHER))
    driver = {"driver": "tab-a"}

    responses = [
        other.get(f"/subjects/{subject_id}/session?driver=tab-a"),
        other.post(f"/subjects/{subject_id}/session", json=driver),
        other.post(f"/sessions/{thread_id}/resume", json=driver),
        _reply(other, thread_id, _answer(_turn(events))),
        other.post(f"/sessions/{thread_id}/heartbeat", json=driver),
    ]

    for response in responses:
        assert (response.status_code, response.json()) == (404, {"error": "Not found."})
    assert services.store.attempts == {}


def test_dispute_round_trip_shows_result_on_next_turn():
    services = _with(make_services(), FakeTutor(regrades=[("correct", False)]))
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, events = _start(web, subject_id)
    graded = _turn(_events(_reply(web, thread_id, _answer(_turn(events), "meh"))))
    assert graded["feedback"]["value"] == "partial" and graded["can_dispute"] is True
    dispute = {"id": str(uuid4()), "reason": "correct", "explanation": "  It is right.  "}

    events = _events(_reply(web, thread_id, {"dispute": dispute}))

    assert ("step", {"label": "Re-checking…"}) in events
    shown = _turn(events)
    assert shown["dispute_result"]["outcome"] == "changed"
    assert shown["dispute_result"]["new_grade"] == "correct"
    assert shown["question"] == graded["question"]  # the waiting question, shown again
    assert shown["can_dispute"] is False
    [recorded] = [d for _, d in services.store.disputes.values()]
    assert str(recorded.id) == dispute["id"] and recorded.explanation == "It is right."


def test_discard_ends_a_session_nobody_drives():
    clock = Clock()
    services = _with(make_services(), FakeTutor(), clock)
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, _ = _start(web, subject_id, driver="tab-a")

    kept = web.post(f"/sessions/{thread_id}/discard", json={"driver": "tab-b"})  # tab-a's
    offered = web.get(f"/subjects/{subject_id}/session?driver=tab-b").json()["offer"]
    clock.advance(seconds=91)
    ended = web.post(f"/sessions/{thread_id}/discard", json={"driver": "tab-b"})

    assert kept.status_code == ended.status_code == 204
    assert offered["thread_id"] == thread_id and offered["busy"] is True
    assert web.get(f"/subjects/{subject_id}/session?driver=tab-b").json() == {"offer": None}


def test_discard_by_the_tab_driving_it_ends_the_session():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, _ = _start(web, subject_id, driver="tab-a")

    ended = web.post(f"/sessions/{thread_id}/discard", json={"driver": "tab-a"})

    assert ended.status_code == 204
    assert web.get(f"/subjects/{subject_id}/session?driver=tab-a").json() == {"offer": None}


def test_release_frees_the_tabs_lease_and_is_404_when_not_open():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)
    thread_id, _ = _start(web, subject_id, driver="tab-a")
    services.store.ensure_profile(OTHER)
    other = client(replace(services, user_id=OTHER))

    # As a closing tab sends it: a plain POST, so the browser needs no preflight.
    released = web.post(f"/sessions/{thread_id}/release?driver=tab-a")
    offer = web.get(f"/subjects/{subject_id}/session?driver=tab-b").json()["offer"]
    foreign = other.post(f"/sessions/{thread_id}/release?driver=tab-a")
    unknown = web.post(f"/sessions/{uuid4()}/release?driver=tab-a")

    assert released.status_code == 204
    assert offer["thread_id"] == thread_id and offer["busy"] is False
    for response in (foreign, unknown):
        assert (response.status_code, response.json()) == (404, {"error": "Not found."})


def test_unknown_sessions_are_404_and_malformed_ids_422():
    services = make_services()
    subject_id = make_subject(services.store, USER, concepts=2)
    web = client(services)

    unknown = str(uuid4())
    assert web.post(f"/sessions/{unknown}/resume", json={"driver": "tab-a"}).status_code == 404
    assert web.post(f"/subjects/{uuid4()}/session", json={"driver": "tab-a"}).status_code == 404
    assert web.get(f"/subjects/{uuid4()}/session?driver=tab-a").status_code == 404
    assert web.post("/sessions/not-a-uuid/resume", json={"driver": "tab-a"}).status_code == 422
    assert web.post(f"/subjects/{subject_id}/session", json={"driver": ""}).status_code == 422
