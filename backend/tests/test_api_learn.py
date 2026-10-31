"""The learn phase over HTTP: streamed lesson text, questions, Ready, and the kept lesson."""

import json
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver

from ebb.api.services import NoTutor, Services
from ebb.domain import ChunkDraft
from ebb.learn.graph import build_learn_graph
from ebb.learn.service import LearnSessions, thread_id
from ebb.llm.tutor import Tutor
from ebb.study import BUSY
from tests.api_support import NOW, USER, client, make_services
from tests.fakes import FakeTutor, make_subject, new_document

type Sse = tuple[str, dict[str, Any]]

PIECES = [
    "# Concept 1\n\n",
    "**In one sentence:** About concept 1.\n\n",
    "| Part | What it does |\n|---|---|\n| Concept 1 | About concept 1. |",
]
NOTE = "Concept 1 is about how a mask splits an address into network and host parts."
CITATION_KEYS = {"kind", "ref", "label", "page", "quote", "name", "heading", "title"}


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


def _names(events: list[Sse]) -> list[str]:
    return [name for name, _ in events]


def _text(events: list[Sse]) -> list[str]:
    return [data["delta"] for name, data in events if name == "text"]


def _concept(services: Services) -> UUID:
    subject_id = make_subject(services.store, USER, concepts=1)
    [concept] = services.store.list_concepts(USER, subject_id)
    return concept.id


def _concept_with_notes(services: Services) -> UUID:
    """A concept whose subject has notes, so its lesson cites them."""
    note = new_document("CSE321.pdf", ChunkDraft(0, NOTE, None, 12, 12, (0,)))
    subject_id = services.store.create_subject_with_notes(
        USER, "Networks", [note], [("Concept 1", "About concept 1.")]
    )
    [concept] = services.store.list_concepts(USER, subject_id)
    return concept.id


def _ask(text: str) -> dict[str, Any]:
    return {"reply": {"ask": text, "kind": "typed", "ask_id": str(uuid4())}}


def _over(services: Services, tutor: Tutor) -> Services:
    """`services` whose learn sessions run over `tutor`."""
    saver = InMemorySaver()
    graph = build_learn_graph(tutor, services.store, saver, now=lambda: NOW)
    return replace(services, learn=LearnSessions(services.store, graph, saver))


def test_learn_stream_event_order():
    services = make_services()
    concept_id = _concept_with_notes(services)

    events = _events(client(services).post(f"/concepts/{concept_id}/learn"))

    names = _names(events)
    assert names[0] == "step" and names[-1] == "learn"
    assert names.count("learn") == 1 and "discard" not in names
    assert names == sorted(names, key={"step": 0, "text": 1, "learn": 2}.__getitem__)
    assert _text(events) == PIECES
    payload = events[-1][1]["payload"]
    assert payload["kind"] == "learn" and payload["concept_id"] == str(concept_id)
    assert payload["lesson"]["markdown"] == "".join(PIECES)
    assert payload["exchanges"] == []
    assert payload["has_notes"] is True
    [citation] = payload["lesson"]["citations"]  # the shape the quiz's citation chips read
    assert set(citation) == CITATION_KEYS


def test_learn_turn_says_whether_the_subject_has_notes():
    services = make_services()
    concept_id = _concept(services)  # a subject with no notes

    events = _events(client(services).post(f"/concepts/{concept_id}/learn"))

    assert events[-1][1]["payload"]["has_notes"] is False


def test_ask_streams_an_answer_and_a_new_turn():
    services = make_services()
    concept_id = _concept(services)
    web = client(services)
    web.post(f"/concepts/{concept_id}/learn")

    events = _events(web.post(f"/concepts/{concept_id}/learn/reply", json=_ask("What is it?")))

    assert _names(events)[-1] == "learn" and "text" in _names(events)
    [exchange] = events[-1][1]["payload"]["exchanges"]
    assert exchange["question"] == "What is it?"
    assert exchange["answer"] == "".join(_text(events))
    assert all(set(c) == CITATION_KEYS for c in exchange["citations"])


def test_ready_emits_learned():
    services = make_services()
    concept_id = _concept(services)
    web = client(services)
    web.post(f"/concepts/{concept_id}/learn")

    body = {"reply": {"ready": True}}
    events = _events(web.post(f"/concepts/{concept_id}/learn/reply", json=body))

    assert events == [("learned", {"concept_id": str(concept_id)})]
    assert services.store.is_learned(USER, concept_id)
    assert _events(web.post(f"/concepts/{concept_id}/learn")) == events  # it stays learned


def test_get_lesson_makes_no_model_call():
    tutor = FakeTutor()
    services = make_services(tutor=tutor)
    services = _over(services, tutor)
    concept_id = _concept_with_notes(services)
    web = client(services)
    web.post(f"/concepts/{concept_id}/learn")
    web.post(f"/concepts/{concept_id}/learn/reply", json=_ask("What is it?"))
    calls = len(tutor.lesson_calls)

    response = web.get(f"/concepts/{concept_id}/lesson")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"lesson", "exchanges", "has_notes"}
    assert body["has_notes"] is True
    assert body["lesson"]["markdown"] == "".join(PIECES)
    assert body["lesson"]["written_at"] == NOW.isoformat()
    assert set(body["lesson"]["citations"][0]) == CITATION_KEYS
    [exchange] = body["exchanges"]
    assert exchange["question"] == "What is it?" and exchange["asked_at"] == NOW.isoformat()
    assert len(tutor.lesson_calls) == calls  # read from the store
    assert web.get(f"/concepts/{uuid4()}/lesson").status_code == 404


def test_get_lesson_is_404_before_one_is_written():
    tutor = FakeTutor()
    services = _over(make_services(tutor=tutor), tutor)
    concept_id = _concept(services)

    response = client(services).get(f"/concepts/{concept_id}/lesson")

    assert response.status_code == 404
    assert tutor.lesson_calls == []  # and nothing was written for it


def test_discard_then_error_on_mid_stream_failure():
    services = make_services()
    services = _over(services, FakeTutor(break_learn_times=1))
    concept_id = _concept(services)

    events = _events(client(services).post(f"/concepts/{concept_id}/learn"))

    names = _names(events)
    assert names[-2:] == ["discard", "error"]
    assert "text" in names and "learn" not in names
    assert events[-2][1] == {} and list(events[-1][1]) == ["notice"]


def test_failure_before_any_text_is_an_error_without_discard():
    services = _over(make_services(), FakeTutor(fail_learn_times=1))
    concept_id = _concept(services)

    events = _events(client(services).post(f"/concepts/{concept_id}/learn"))

    assert "discard" not in _names(events) and "text" not in _names(events)
    assert _names(events)[-1] == "error" and list(events[-1][1]) == ["notice"]


def test_an_unconfigured_tutor_is_an_error_event():
    services = _over(make_services(), NoTutor("GROQ_API_KEY is not set."))
    concept_id = _concept(services)

    events = _events(client(services).post(f"/concepts/{concept_id}/learn"))

    assert events[-1] == ("error", {"error": "GROQ_API_KEY is not set."})
    assert "learn" not in _names(events)


def test_reply_before_the_lesson_is_409():
    services = make_services()
    concept_id = _concept(services)

    response = client(services).post(f"/concepts/{concept_id}/learn/reply", json=_ask("Why?"))

    assert response.status_code == 409
    assert response.json() == {"error": "Start the lesson first."}


def test_malformed_replies_are_422():
    services = make_services()
    concept_id = _concept(services)
    web = client(services)
    web.post(f"/concepts/{concept_id}/learn")
    bad = [
        {"reply": {"ask": "  ", "kind": "typed", "ask_id": "a"}},
        {"reply": {"ask": "Why?", "kind": "shouted", "ask_id": "a"}},
        {"reply": {"ask": "Why?", "kind": "typed"}},
        {"reply": {"ready": False}},
        {"reply": {"ready": True, "ask": "Why?"}},
    ]

    for body in bad:
        assert web.post(f"/concepts/{concept_id}/learn/reply", json=body).status_code == 422


def test_another_users_or_unknown_concept_is_404():
    services = make_services()
    other = UUID(int=2)
    [theirs] = services.store.list_concepts(other, make_subject(services.store, other, concepts=1))
    web = client(services)
    ready = {"reply": {"ready": True}}

    assert web.post(f"/concepts/{theirs.id}/learn").status_code == 404
    assert web.post(f"/concepts/{uuid4()}/learn").status_code == 404
    assert web.post(f"/concepts/{theirs.id}/learn/reply", json=ready).status_code == 404
    assert web.get(f"/concepts/{theirs.id}/lesson").status_code == 404


def test_a_second_driver_gets_the_busy_answer():
    services = make_services()
    concept_id = _concept(services)

    with services.store.session_lock(USER, thread_id(USER, concept_id)) as free:
        assert free
        response = client(services).post(f"/concepts/{concept_id}/learn")

    assert response.status_code == 409
    assert response.json() == {"error": BUSY}
