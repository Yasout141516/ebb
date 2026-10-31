"""The learn graph and the service that drives it (FakeTutor, MemoryStore, InMemorySaver)."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ebb.domain import ChunkDraft, LearnText
from ebb.learn.graph import build_learn_graph
from ebb.learn.service import (
    Discard,
    Learned,
    LearnEvent,
    LearnSessions,
    LearnTurn,
    TextDelta,
    thread_id,
)
from ebb.llm import request
from ebb.llm.tutor import AllProvidersExhausted, Generated, TutorUnavailable
from ebb.study import SessionBusy, Step
from ebb.web import WebPage
from tests.fakes import FakeTutor, FakeWebSearch, MemoryStore, new_document, seed_concept

USER = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")
NOTE = (
    "A subnet mask splits an address into network and host parts. "
    "The first address identifies the network and the last is the broadcast address."
)
PAGE = WebPage(
    "https://www.example.org/subnets",
    "Subnetting basics",
    "Subnetting divides one network into smaller networks.",
)
LESSON_PIECES = [
    "# Subnet masks\n\n",
    "**In one sentence:** How a mask splits an address.\n\n",
    "| Part | What it does |\n|---|---|\n| Subnet masks | How a mask splits an address. |",
]
WEB_NOTICE = "Web search unavailable — this lesson uses your notes only."


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


@dataclass
class Setup:
    store: MemoryStore
    tutor: Any
    web: FakeWebSearch | None
    clock: Clock
    saver: InMemorySaver
    sessions: LearnSessions
    concept_id: UUID


def setup(
    tutor: Any = None, web: FakeWebSearch | None = None, notes: bool = True, with_web: bool = True
) -> Setup:
    store, clock, saver = MemoryStore(), Clock(), InMemorySaver()
    tutor = tutor or FakeTutor()
    if web is None and with_web:
        web = FakeWebSearch([PAGE])
    store.ensure_profile(USER)
    concept = ("Subnet masks", "How a mask splits an address.")
    if notes:
        document = new_document("CSE321.pdf", ChunkDraft(0, NOTE, None, 12, 12, (0,)))
        subject_id = store.create_subject_with_notes(USER, "Networks", [document], [concept])
    else:
        subject_id = store.create_subject(USER, "Networks", [concept])
    [found] = store.list_concepts(USER, subject_id)
    graph = build_learn_graph(tutor, store, saver, now=clock, web=web)
    sessions = LearnSessions(store, graph, saver, clock)
    return Setup(store, tutor, web, clock, saver, sessions, found.id)


@pytest.fixture
def s() -> Setup:
    return setup()


def turn(events: list[LearnEvent]) -> dict[str, Any]:
    last = events[-1]
    assert isinstance(last, LearnTurn)
    return last.payload


def texts(events: list[LearnEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, TextDelta)]


def ask(text: str, ask_id: str, kind: str = "typed") -> dict[str, Any]:
    return {"ask": text, "kind": kind, "ask_id": ask_id}


def checkpoint(s: Setup) -> Any:
    return s.saver.get_tuple({"configurable": {"thread_id": thread_id(USER, s.concept_id)}})


def test_start_streams_text_then_waits_with_the_lesson(s):
    events = list(s.sessions.start(USER, s.concept_id))

    assert events[:2] == [Step("Searching the web…"), Step("Writing the lesson…")]
    assert texts(events) == LESSON_PIECES
    assert [type(e) for e in events[2:]] == [TextDelta, TextDelta, TextDelta, LearnTurn]
    payload = turn(events)
    assert payload["kind"] == "learn"
    assert payload["concept_id"] == str(s.concept_id)
    assert payload["concept"] == "Subnet masks"
    assert payload["exchanges"] == [] and payload["notices"] == []
    lesson = payload["lesson"]
    assert lesson["markdown"] == "".join(LESSON_PIECES)
    assert lesson["checks"] == [
        "Why does Subnet masks matter?",
        "Where would you see Subnet masks at work?",
        "What would break without Subnet masks?",
    ]
    assert payload["suggestions"] == lesson["suggestions"]
    [citation] = lesson["citations"]  # the fake cites its first source: the notes
    assert citation["kind"] == "chunk" and citation["quote"] == NOTE
    assert lesson["web_pages"] == [
        {"url": PAGE.url, "title": "Subnetting basics", "site": "example.org"}
    ]
    assert lesson["source_summary"] == {
        "notes": 1,
        "notes_pages": [12],
        "web_pages": 1,
        "label": None,
    }
    assert lesson["written_at"] == s.clock().isoformat() and lesson["learned_at"] is None
    assert lesson["model"] == "fake-model"
    # One search, from the concept and its subject.
    [query] = s.web.queries
    assert "Subnet masks" in query and "How a mask splits" in query and "Networks" in query
    stored = s.store.get_lesson(USER, s.concept_id)
    assert stored.markdown == lesson["markdown"] and stored.model == "fake-model"
    assert stored.source_summary == lesson["source_summary"]
    assert s.store.locks == set()


def test_second_start_uses_stored_lesson_without_a_model_call(s):
    first = turn(list(s.sessions.start(USER, s.concept_id)))
    list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))

    again = list(s.sessions.start(USER, s.concept_id))
    # The checkpoint lost (pruned, or another server): rebuilt from the store.
    s.saver.delete_thread(thread_id(USER, s.concept_id))
    rebuilt = list(s.sessions.start(USER, s.concept_id))

    assert len(s.tutor.lesson_calls) == 1 and len(s.tutor.answer_calls) == 1
    assert len(s.web.queries) == 2  # the lesson's and the answer's; none on reopening
    assert len(again) == 1 and turn(again)["lesson"] == first["lesson"]
    assert len(turn(again)["exchanges"]) == 1
    assert len(rebuilt) == 1  # no steps, no text: nothing was written
    assert turn(rebuilt)["lesson"] == first["lesson"]
    assert turn(rebuilt)["exchanges"] == turn(again)["exchanges"]
    assert turn(rebuilt)["suggestions"] == turn(again)["suggestions"]


def test_ask_answers_and_saves_an_exchange(s):
    lesson = turn(list(s.sessions.start(USER, s.concept_id)))["lesson"]
    s.clock.advance(minutes=1)

    events = list(s.sessions.reply(USER, s.concept_id, ask("Why masks?", "a1", "check")))
    second = list(s.sessions.reply(USER, s.concept_id, ask("And hosts?", "a2", "suggested")))

    assert events[:2] == [Step("Searching the web…"), Step("Answering…")]
    assert texts(events) == ["You asked: Why masks? ", "Here is how Subnet masks ", "answers it."]
    [exchange] = turn(events)["exchanges"]
    assert exchange == {
        "position": 0,
        "kind": "check",
        "question": "Why masks?",
        "answer": "You asked: Why masks? Here is how Subnet masks answers it.",
        "citations": exchange["citations"],
        "suggestions": ["How is this used in practice?", "What is it often confused with?"],
        "asked_at": s.clock().isoformat(),
    }
    assert [c["quote"] for c in exchange["citations"]] == [NOTE]
    assert turn(events)["suggestions"] == exchange["suggestions"]
    assert turn(events)["lesson"] == lesson
    first_call, second_call = s.tutor.answer_calls
    assert first_call["lesson"] == lesson["markdown"] and first_call["history"] == []
    assert second_call["history"] == [("Why masks?", exchange["answer"])]
    assert "Why masks?" in s.web.queries[1] and "Subnet masks" in s.web.queries[1]
    kept = s.store.exchanges(USER, s.concept_id)
    assert [(e.position, e.kind, e.question) for e in kept] == [
        (0, "check", "Why masks?"),
        (1, "suggested", "And hosts?"),
    ]
    assert kept[0].answer == exchange["answer"]
    assert len(turn(second)["exchanges"]) == 2


def test_same_ask_id_is_not_answered_twice(s):
    list(s.sessions.start(USER, s.concept_id))
    first = list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))

    again = list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))

    assert again == [first[-1]]
    assert len(s.tutor.answer_calls) == 1
    assert len(s.store.exchanges(USER, s.concept_id)) == 1


def test_ready_marks_learned_once_and_deletes_the_thread(s):
    list(s.sessions.start(USER, s.concept_id))
    learned_at = s.clock()

    events = list(s.sessions.reply(USER, s.concept_id, {"ready": True}))
    s.clock.advance(hours=1)
    again = list(s.sessions.reply(USER, s.concept_id, {"ready": True}))
    reopened = list(s.sessions.start(USER, s.concept_id))

    assert events == [Learned(s.concept_id)]
    assert again == [Learned(s.concept_id)] and reopened == [Learned(s.concept_id)]
    assert s.store.is_learned(USER, s.concept_id)
    assert s.store.get_lesson(USER, s.concept_id).learned_at == learned_at
    assert checkpoint(s) is None
    assert len(s.tutor.lesson_calls) == 1


def test_no_web_key_labels_notes_only():
    s = setup(with_web=False)

    events = list(s.sessions.start(USER, s.concept_id))

    assert Step("Searching the web…") not in events
    lesson = turn(events)["lesson"]
    assert lesson["source_summary"] == {
        "notes": 1,
        "notes_pages": [12],
        "web_pages": 0,
        "label": "Your notes only",
    }
    assert lesson["web_pages"] == [] and turn(events)["notices"] == []


def test_no_notes_and_no_web_key_labels_general_knowledge():
    s = setup(notes=False, with_web=False)

    lesson = turn(list(s.sessions.start(USER, s.concept_id)))["lesson"]

    assert lesson["source_summary"] == {
        "notes": 0,
        "notes_pages": [],
        "web_pages": 0,
        "label": "General knowledge · not from your notes",
    }
    assert lesson["citations"] == []
    assert s.tutor.lesson_calls[0]["sources"] == []


def test_failed_search_is_a_notice():
    s = setup(web=FakeWebSearch(fail=True))

    events = list(s.sessions.start(USER, s.concept_id))

    assert turn(events)["notices"] == [WEB_NOTICE]
    assert turn(events)["lesson"]["source_summary"]["label"] == "Your notes only"
    assert [src.kind for src in s.tutor.lesson_calls[0]["sources"]] == ["chunk"]
    answered = list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))
    assert turn(answered)["notices"] == [WEB_NOTICE]  # its own search failed too


def test_failure_after_text_yields_discard_then_raises():
    s = setup(tutor=FakeTutor(break_learn_times=1))
    got: list[LearnEvent] = []

    with pytest.raises(AllProvidersExhausted):
        for event in s.sessions.start(USER, s.concept_id):
            got.append(event)

    assert got == [
        Step("Searching the web…"),
        Step("Writing the lesson…"),
        TextDelta(LESSON_PIECES[0]),
        Discard(),
    ]
    assert s.store.get_lesson(USER, s.concept_id) is None  # nothing partial is kept
    assert s.store.locks == set()
    # Retry carries on from the gathered sources: no second search.
    retried = list(s.sessions.start(USER, s.concept_id))
    assert texts(retried) == LESSON_PIECES
    assert turn(retried)["lesson"]["markdown"] == "".join(LESSON_PIECES)
    assert len(s.web.queries) == 1


def test_failure_before_any_text_raises_without_discard():
    s = setup(tutor=FakeTutor(fail_learn_times=1))
    got: list[LearnEvent] = []

    with pytest.raises(TutorUnavailable):
        for event in s.sessions.start(USER, s.concept_id):
            got.append(event)

    assert got == [Step("Searching the web…"), Step("Writing the lesson…")]
    assert s.store.get_lesson(USER, s.concept_id) is None


def test_answer_broken_after_text_is_discarded_and_retried_once(s):
    list(s.sessions.start(USER, s.concept_id))
    s.tutor.break_learn_times = 1
    got: list[LearnEvent] = []

    with pytest.raises(AllProvidersExhausted):
        for event in s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")):
            got.append(event)
    retried = list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))
    again = list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))

    assert got[-2:] == [TextDelta("You asked: Why? "), Discard()]
    assert len(turn(retried)["exchanges"]) == 1
    assert again == [retried[-1]]
    assert len(s.store.exchanges(USER, s.concept_id)) == 1
    assert len(s.tutor.answer_calls) == 2  # the broken call and its retry


def test_reader_gone_mid_lesson_still_stores_it(s):
    events = s.sessions.start(USER, s.concept_id)
    while not isinstance(next(events), TextDelta):
        pass
    events.close()  # the tab closed while the lesson streamed

    assert s.store.get_lesson(USER, s.concept_id).markdown == "".join(LESSON_PIECES)
    assert s.store.locks == set()
    config = {"configurable": {"thread_id": thread_id(USER, s.concept_id)}}
    assert s.sessions.graph.get_state(config).interrupts  # the turn ran on to its wait
    resumed = list(s.sessions.start(USER, s.concept_id))
    assert len(resumed) == 1 and turn(resumed)["lesson"]["markdown"] == "".join(LESSON_PIECES)
    assert len(s.tutor.lesson_calls) == 1


def test_learning_never_changes_stage_or_tide(s):
    seen = s.clock() - timedelta(days=3)
    seed_concept(
        s.store,
        s.concept_id,
        stage=0,
        next_review_at=seen,
        last_reviewed_at=None,
        review_count=0,
    )
    before = s.store.concept(s.concept_id)

    list(s.sessions.start(USER, s.concept_id))
    list(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))
    list(s.sessions.reply(USER, s.concept_id, {"ready": True}))

    assert s.store.concept(s.concept_id) == before
    assert s.store.attempts == {}


def test_another_users_concept_is_not_found(s):
    s.store.ensure_profile(OTHER)

    with pytest.raises(LookupError):
        s.sessions.start(OTHER, s.concept_id)
    with pytest.raises(LookupError):
        s.sessions.reply(OTHER, s.concept_id, {"ready": True})


def test_a_second_driver_is_busy(s):
    events = s.sessions.start(USER, s.concept_id)
    next(events)  # the first tab's run holds the thread

    with pytest.raises(SessionBusy):
        list(s.sessions.start(USER, s.concept_id))
    list(events)
    assert len(s.tutor.lesson_calls) == 1


def test_uncited_claims_lose_their_chip_and_keep_the_text():
    bad = [("N1", "a quote that is nowhere in the notes")]
    s = setup(tutor=FakeTutor(learn_cites=[bad, bad, bad]))

    lesson = turn(list(s.sessions.start(USER, s.concept_id)))["lesson"]

    assert lesson["citations"] == []
    assert lesson["markdown"] == "".join(LESSON_PIECES)
    assert len(s.tutor.lesson_calls) == 3  # the lesson and its two citation retries


class BlankRetryTutor(FakeTutor):
    """A citation retry that names no model, as a retry with nothing to cite does."""

    def write_lesson(self, *args: Any, **kwargs: Any) -> Generated[LearnText]:
        generated = super().write_lesson(*args, **kwargs)
        return replace(generated, model="") if kwargs.get("failures") else generated


def test_the_lesson_keeps_the_first_calls_model():
    bad = [("N1", "a quote that is nowhere in the notes")]
    s = setup(tutor=BlankRetryTutor(learn_cites=[bad, [("N1", NOTE)]]))

    lesson = turn(list(s.sessions.start(USER, s.concept_id)))["lesson"]

    assert len(s.tutor.lesson_calls) == 2
    assert [c["quote"] for c in lesson["citations"]] == [NOTE]
    assert lesson["model"] == "fake-model"


def _advance_in_workers(events: Iterator[LearnEvent]) -> list[LearnEvent]:
    """Every next() on its own worker thread, as the API's run_in_threadpool does."""
    got: list[LearnEvent] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            try:
                got.append(pool.submit(copy_context().run, next, events).result())
            except StopIteration:
                return got


def test_one_learn_turn_shares_one_request_id():
    bad = [("N1", "a quote that is nowhere in the notes")]
    s = setup(tutor=FakeTutor(learn_cites=[bad, [("N1", NOTE)], bad, [("N1", NOTE)]]))
    seen: list[tuple[UUID | None, UUID | None]] = []
    for name in ("write_lesson", "answer"):
        original = getattr(s.tutor, name)

        def wrapped(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
            seen.append(request.current())
            return _original(*args, **kwargs)

        setattr(s.tutor, name, wrapped)

    _advance_in_workers(s.sessions.start(USER, s.concept_id))
    lesson_calls = list(seen)
    seen.clear()
    _advance_in_workers(s.sessions.reply(USER, s.concept_id, ask("Why?", "a1")))

    assert len(lesson_calls) == 2 and len(seen) == 2  # each with its citation retry
    assert len(set(lesson_calls)) == 1 and len(set(seen)) == 1
    assert lesson_calls[0][1] == USER and lesson_calls[0][0] is not None
    assert lesson_calls[0] != seen[0]
