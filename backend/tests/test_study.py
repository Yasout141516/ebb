"""The study-session service the CLI and the API share (MemoryStore + InMemorySaver)."""

import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from rich.console import Console

from ebb.db.repo import PgStore
from ebb.graph.build import build_graph
from ebb.study import (
    LEASE,
    Event,
    Finished,
    Offer,
    Pending,
    SessionBusy,
    SessionNotFound,
    Step,
    StudySessions,
)
from ebb.ui import render_turn
from tests.fakes import FakeTutor, MemoryStore, make_subject

USER = UUID("00000000-0000-0000-0000-0000000000aa")


class Clock:
    """Starts at the real time, as MemoryStore stamps new sessions with it."""

    def __init__(self) -> None:
        self.at = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


@dataclass
class Setup:
    store: MemoryStore
    tutor: FakeTutor
    clock: Clock
    subject_id: UUID
    graph: CompiledStateGraph
    saver: InMemorySaver
    sessions: StudySessions

    def another_process(self) -> StudySessions:
        """A second service on the same database, as the API is to the CLI."""
        return StudySessions(self.store, self.graph, self.saver, self.clock)


@pytest.fixture
def s() -> Setup:
    store, tutor, clock = MemoryStore(), FakeTutor(), Clock()
    subject_id = make_subject(store, USER, concepts=2)
    saver = InMemorySaver()
    graph = build_graph(tutor, store, saver, now=clock)
    sessions = StudySessions(store, graph, saver, clock)
    return Setup(store, tutor, clock, subject_id, graph, saver, sessions)


def ending(events: Iterator[Event]) -> Event:
    return list(events)[-1]


def right(pending: Event) -> dict[str, Any]:
    assert isinstance(pending, Pending)
    options = pending.payload["question"]["options"]
    return {"answer": "1" if options else "right", "attempt_id": str(uuid4())}


def started(s: Setup, driver: str = "tab-a") -> tuple[str, Pending]:
    thread_id, _, events = s.sessions.start(USER, s.subject_id, driver)
    first = ending(events)
    assert isinstance(first, Pending)
    return thread_id, first


def session(s: Setup, thread_id: str) -> dict[str, Any]:
    [found] = [row for row in s.store.sessions.values() if row["thread_id"] == thread_id]
    return found


def test_start_yields_steps_then_the_first_question(s):
    thread_id, greeting, events = s.sessions.start(USER, s.subject_id, "tab-a")

    got = list(events)

    assert greeting == "Nothing is due. Let's learn something new."
    assert got[:-1] == [Step("Writing questions…"), Step("Checking citations against your notes…")]
    [last] = got[-1:]
    assert isinstance(last, Pending)
    assert (last.payload["concept"], last.payload["number"]) == ("Concept 1", 1)
    assert session(s, thread_id)["driver"] == "tab-a"
    assert s.store.locks == set()  # released before the question is handed over


def test_start_refuses_another_users_subject(s):
    with pytest.raises(LookupError):
        s.sessions.start(uuid4(), s.subject_id, "tab-a")


def test_reply_counts_an_answer_once_when_sent_twice(s):
    thread_id, first = started(s)
    answer = right(first)

    once = list(s.sessions.reply(USER, thread_id, "tab-a", answer))
    twice = list(s.sessions.reply(USER, thread_id, "tab-a", answer))

    assert len(s.store.attempts) == 1
    assert isinstance(once[-1], Pending) and once[-1].payload["number"] == 2
    assert twice == [once[-1]]  # the graph didn't run again


def test_a_dispute_sent_twice_is_rechecked_once(s):
    thread_id, first = started(s)  # Q1 is a short answer, graded by the model
    second = ending(s.sessions.reply(USER, thread_id, "tab-a", right(first)))
    assert second.payload["can_dispute"] is True
    dispute = {"dispute": {"id": str(uuid4()), "reason": "correct", "explanation": ""}}

    once = ending(s.sessions.reply(USER, thread_id, "tab-a", dispute))
    twice = list(s.sessions.reply(USER, thread_id, "tab-a", dispute))

    assert len(s.tutor.regrade_calls) == 1 and len(s.store.disputes) == 1
    assert once.payload["dispute_result"] is not None
    assert twice == [once]


def test_pending_is_the_waiting_question_for_its_user_only(s):
    thread_id, first = started(s)

    assert s.sessions.pending(USER, thread_id) == first.payload
    assert s.sessions.pending(uuid4(), thread_id) is None
    assert s.sessions.pending(USER, str(uuid4())) is None
    answer = right(first)
    second = ending(s.sessions.reply(USER, thread_id, "tab-a", answer))
    assert s.sessions.pending(USER, thread_id, right(second)) == second.payload
    assert s.sessions.pending(USER, thread_id, answer) is None  # sent again: already applied
    ending(s.sessions.reply(USER, thread_id, "tab-a", {"quit": True}))
    assert s.sessions.pending(USER, thread_id) is None


def test_quit_finishes_with_the_summary_and_a_repeat_gets_it_again(s):
    thread_id, first = started(s)
    ending(s.sessions.reply(USER, thread_id, "tab-a", right(first)))

    finished = ending(s.sessions.reply(USER, thread_id, "tab-a", {"quit": True}))
    again = list(s.sessions.reply(USER, thread_id, "tab-a", {"quit": True}))

    assert isinstance(finished, Finished)
    assert finished.subject_id == s.subject_id
    assert [r["concept"] for r in finished.results] == ["Concept 1"]
    assert finished.final == {"grade": None, "lesson": None, "notices": []}
    assert finished.caught_up is False
    assert finished.next_review_at == s.store.next_review_at(USER, s.subject_id)
    assert again == [finished]
    assert session(s, thread_id)["ended_at"] is not None


def test_reply_to_another_users_session_is_refused(s):
    thread_id, first = started(s)

    with pytest.raises(SessionNotFound):
        list(s.sessions.reply(uuid4(), thread_id, "tab-a", right(first)))


def test_offer_names_concept_and_question_number(s):
    assert s.sessions.offer(USER, s.subject_id, "tab-a") is None
    thread_id, first = started(s)
    ending(s.sessions.reply(USER, thread_id, "tab-a", right(first)))

    offer = s.sessions.offer(USER, s.subject_id, "tab-a")

    assert offer == Offer(thread_id, "Concept 1", 2, s.clock(), busy=False)


def test_offer_ends_a_session_with_nothing_to_carry_on_with(s):
    # A session whose process died before its first checkpoint was written.
    session_id = s.store.create_session(USER, s.subject_id, "never-saved")

    assert s.sessions.offer(USER, s.subject_id, "tab-a") is None
    assert s.store.sessions[session_id]["ended_at"] is not None


def test_resuming_a_session_with_nothing_saved_is_refused(s):
    s.store.create_session(USER, s.subject_id, "never-saved")

    with pytest.raises(SessionNotFound):
        list(s.sessions.resume(USER, "never-saved", "tab-a"))
    assert s.store.locks == set()


def test_offer_of_a_cut_off_step_and_resume_finishes_it_once(s, monkeypatch):
    thread_id, first = started(s)
    real_grade = s.tutor.grade
    calls = []

    def killed_once(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("killed while grading")
        return real_grade(*args, **kwargs)

    monkeypatch.setattr(s.tutor, "grade", killed_once)
    with pytest.raises(RuntimeError):
        list(s.sessions.reply(USER, thread_id, "tab-a", right(first)))
    assert s.store.locks == set()

    offer = s.sessions.offer(USER, s.subject_id, "tab-a")
    resumed = list(s.sessions.resume(USER, thread_id, "tab-a"))
    again = list(s.sessions.resume(USER, thread_id, "tab-a"))

    assert (offer.concept, offer.number) == ("Concept 1", 1)
    assert resumed[0] == Step("Grading…")
    last = resumed[-1]
    assert isinstance(last, Pending)
    assert last.payload["number"] == 2 and last.payload["feedback"]["value"] == "correct"
    assert again == [last]
    assert len(s.store.attempts) == 1 and len(calls) == 2


def test_second_driver_is_busy_while_the_lease_is_live(s):
    thread_id, first = started(s, "tab-a")

    offer = s.sessions.offer(USER, s.subject_id, "tab-b")

    assert offer is not None and offer.busy is True
    with pytest.raises(SessionBusy):
        list(s.sessions.resume(USER, thread_id, "tab-b"))
    with pytest.raises(SessionBusy):
        list(s.sessions.reply(USER, thread_id, "tab-b", right(first)))
    assert s.sessions.heartbeat(USER, thread_id, "tab-b") is False
    assert len(s.store.attempts) == 0
    assert session(s, thread_id)["driver"] == "tab-a"


def test_a_running_turn_makes_the_session_busy(s):
    thread_id, _ = started(s, "tab-a")
    s.store.locks.add(thread_id)  # a turn is running elsewhere

    assert s.sessions.offer(USER, s.subject_id, "tab-a").busy is True
    with pytest.raises(SessionBusy):
        list(s.sessions.resume(USER, thread_id, "tab-a"))


def test_lease_expires_after_90_seconds_without_heartbeat(s):
    thread_id, first = started(s, "tab-a")

    s.clock.advance(seconds=89)
    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is True
    s.clock.advance(seconds=2)
    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is False
    assert list(s.sessions.resume(USER, thread_id, "tab-b")) == [first]

    assert s.sessions.heartbeat(USER, thread_id, "tab-a") is False  # tab-b drives it now


def test_heartbeat_keeps_the_lease(s):
    thread_id, _ = started(s, "tab-a")

    s.clock.advance(seconds=60)
    assert s.sessions.heartbeat(USER, thread_id, "tab-a") is True
    s.clock.advance(seconds=60)

    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is True


def test_heartbeat_on_a_session_that_isnt_open_is_refused(s):
    thread_id, _ = started(s, "tab-a")
    with pytest.raises(SessionNotFound):
        s.sessions.heartbeat(uuid4(), thread_id, "tab-a")  # another user's
    ending(s.sessions.reply(USER, thread_id, "tab-a", {"quit": True}))

    for thread in (thread_id, str(uuid4())):  # ended, unknown
        with pytest.raises(SessionNotFound):
            s.sessions.heartbeat(USER, thread, "tab-a")


def test_same_driver_reconnects(s):
    thread_id, first = started(s, "tab-a")

    offer = s.sessions.offer(USER, s.subject_id, "tab-a")

    assert offer is not None and offer.busy is False
    assert list(s.sessions.resume(USER, thread_id, "tab-a")) == [first]


def test_discard_ends_the_session(s):
    thread_id, _ = started(s, "tab-a")
    s.clock.advance(seconds=91)  # the tab was closed

    s.sessions.discard(USER, thread_id, "tab-b")

    assert session(s, thread_id)["ended_at"] == s.clock()
    assert s.sessions.offer(USER, s.subject_id, "tab-b") is None


def test_discard_keeps_a_session_another_tab_is_driving(s):
    thread_id, _ = started(s, "tab-a")

    s.sessions.discard(USER, thread_id, "tab-b")

    assert session(s, thread_id)["ended_at"] is None


def test_discard_by_the_tab_driving_it_ends_the_session(s):
    # The tab's own lease, e.g. after a refresh while its turn ran: Start fresh must end
    # that session, not leave it open beside the new one.
    thread_id, _ = started(s, "tab-a")

    s.sessions.discard(USER, thread_id, "tab-a")

    assert session(s, thread_id)["ended_at"] == s.clock()


def test_discard_keeps_a_session_whose_turn_is_running(s):
    thread_id, _ = started(s, "tab-a")
    s.store.locks.add(thread_id)

    s.sessions.discard(USER, thread_id, "tab-a")

    assert session(s, thread_id)["ended_at"] is None


def test_release_lets_another_tab_take_the_session_at_once(s):
    thread_id, first = started(s, "tab-a")

    s.sessions.release(USER, thread_id, "tab-b")  # someone else's lease: kept
    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is True
    s.sessions.release(USER, thread_id, "tab-a")  # the tab closed

    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is False
    assert list(s.sessions.resume(USER, thread_id, "tab-b")) == [first]
    assert session(s, thread_id)["driver"] == "tab-b"


def test_release_of_a_session_that_isnt_open_is_refused(s):
    thread_id, _ = started(s, "tab-a")
    with pytest.raises(SessionNotFound):
        s.sessions.release(uuid4(), thread_id, "tab-a")  # another user's
    ending(s.sessions.reply(USER, thread_id, "tab-a", {"quit": True}))

    for thread in (thread_id, str(uuid4())):  # ended, unknown
        with pytest.raises(SessionNotFound):
            s.sessions.release(USER, thread, "tab-a")


def test_a_held_thread_runs_without_a_lease_and_is_busy_elsewhere(s):
    thread_id, _, events = s.sessions.start(USER, s.subject_id, "cli:1")

    with s.sessions.hold(USER, thread_id) as held:
        first = ending(events)
        busy = s.sessions.offer(USER, s.subject_id, "tab-b").busy

    assert held is True and isinstance(first, Pending)
    assert busy is True
    assert session(s, thread_id)["driver"] is None
    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is False


def test_hold_refuses_a_session_a_tab_is_driving(s):
    thread_id, _ = started(s, "tab-a")

    with s.sessions.hold(USER, thread_id) as held:
        assert held is False


def test_closing_the_events_mid_turn_releases_the_lock(s):
    thread_id, first = started(s)
    events = s.sessions.reply(USER, thread_id, "tab-a", right(first))

    assert next(events) == Step("Grading…")
    assert s.store.locks == {thread_id}
    events.close()  # the browser went away

    assert s.store.locks == set()


def test_a_stream_closed_mid_turn_finishes_the_turn(s):
    thread_id, first = started(s)
    events = s.sessions.reply(USER, thread_id, "tab-a", right(first))
    assert next(events) == Step("Grading…")
    events.close()  # the browser went away while grading

    offer = s.sessions.offer(USER, s.subject_id, "tab-a")
    resumed = list(s.sessions.resume(USER, thread_id, "tab-a"))

    assert (offer.concept, offer.number) == ("Concept 1", 2)
    [question] = resumed  # the turn was finished when the stream closed
    assert isinstance(question, Pending)
    assert question.payload["number"] == 2 and question.payload["feedback"]["value"] == "correct"
    assert len(s.store.attempts) == 1


def test_a_step_cut_off_after_it_ran_is_carried_on_once(s):
    thread_id, first = started(s)
    # A process that died mid-turn: grading ran, but no checkpoint was written after it.
    stream = s.graph.stream(
        Command(resume=right(first)),
        {"configurable": {"thread_id": thread_id}},
        stream_mode=["custom", "values"],
    )
    assert ("custom", {"step": "grade_answer", "label": "Grading…"}) in stream
    stream.close()

    offer = s.sessions.offer(USER, s.subject_id, "tab-a")
    resumed = list(s.sessions.resume(USER, thread_id, "tab-a"))
    again = list(s.sessions.resume(USER, thread_id, "tab-a"))

    assert (offer.concept, offer.number) == ("Concept 1", 1)
    last = resumed[-1]
    assert isinstance(last, Pending)
    assert last.payload["number"] == 2 and last.payload["feedback"]["value"] == "correct"
    assert again == [last]
    assert len(s.store.attempts) == 1


def test_a_call_refused_by_the_lock_claims_nothing(s):
    cli = s.another_process()
    thread_id, _, events = cli.start(USER, s.subject_id, "cli:1")
    with cli.hold(USER, thread_id) as held:
        assert held is True
        ending(events)
        with pytest.raises(SessionBusy):
            list(s.sessions.resume(USER, thread_id, "tab-b"))
        assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is True

    assert session(s, thread_id)["driver"] is None
    assert s.another_process().offer(USER, s.subject_id, "cli:2").busy is False  # the CLI exited


def test_a_tab_closed_mid_turn_leaves_the_session_free_for_another_tab(s):
    thread_id, first = started(s, "tab-a")
    events = s.sessions.reply(USER, thread_id, "tab-a", right(first))
    assert next(events) == Step("Grading…")

    s.sessions.release(USER, thread_id, "tab-a")  # pagehide, while the turn still runs
    events.close()  # the server notices the tab is gone and finishes the turn

    assert session(s, thread_id)["driver"] is None
    assert s.sessions.offer(USER, s.subject_id, "tab-b").busy is False
    [question] = s.sessions.resume(USER, thread_id, "tab-b")
    assert isinstance(question, Pending) and question.payload["number"] == 2


OLD_CITATION = {"kind": "chunk", "ref": "r1", "label": "a.pdf · p. 3", "page": 3, "quote": "Q."}
CITATION = OLD_CITATION | {"name": "a.pdf", "heading": None, "title": None}


class _Milestone3Payload:
    """The graph, with the waiting question as milestone 3 saved it: the lesson a string
    with its citations beside it, and citations without name, heading or title."""

    def __init__(self, graph: CompiledStateGraph) -> None:
        self._graph = graph

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)

    def get_state(self, config: Any) -> Any:
        saved = self._graph.get_state(config)
        if not saved.interrupts:
            return saved
        [waiting] = saved.interrupts
        old = {k: v for k, v in waiting.value.items()} | {
            "feedback": {
                "value": "incorrect",
                "explanation": "No.",
                "misconception": None,
                "citations": [OLD_CITATION],
                "general_knowledge": False,
            },
            "lesson": "An old lesson.",
            "lesson_citations": [OLD_CITATION],
        }
        return saved._replace(interrupts=(Interrupt(value=old, id=waiting.id),))


def test_a_question_saved_by_milestone_3_is_handed_out_in_the_current_shape(s):
    thread_id, _ = started(s, "tab-a")
    old = StudySessions(s.store, _Milestone3Payload(s.graph), s.saver, s.clock)

    [resumed] = old.resume(USER, thread_id, "tab-a")
    waiting = old.pending(USER, thread_id)

    assert isinstance(resumed, Pending)
    for payload in (resumed.payload, waiting):
        assert payload["lesson"] == {"text": "An old lesson.", "citations": [CITATION]}
        assert "lesson_citations" not in payload
        assert payload["feedback"]["citations"] == [CITATION]
    render_turn(Console(file=io.StringIO()), resumed.payload)  # the CLI shows it


def test_pending_question_touches_the_session(s):
    thread_id, _ = started(s, "tab-a")
    s.clock.advance(hours=23)
    ending(s.sessions.resume(USER, thread_id, "tab-a"))  # the question is shown again

    s.clock.advance(hours=2)
    s.sessions.housekeeping(USER)

    assert session(s, thread_id)["ended_at"] is None
    assert s.sessions.offer(USER, s.subject_id, "tab-a") is not None


def test_housekeeping_closes_an_idle_session(s):
    thread_id, _ = started(s, "tab-a")
    s.clock.advance(hours=25)

    s.sessions.housekeeping(USER)

    assert session(s, thread_id)["ended_at"] is not None


@pytest.mark.db
def test_housekeeping_skips_a_session_whose_lock_is_held(pg_pool):
    store = PgStore(pg_pool)
    store.ensure_profile(USER)
    subject_id = store.create_subject(USER, "Networks", [("DNS", "d")])
    session_id = store.create_session(USER, subject_id, "t-1")
    now = datetime.now(UTC)
    with pg_pool.connection() as conn:
        conn.execute(
            "update study_sessions set last_active_at = %s where id = %s",
            (now - timedelta(hours=25), session_id),
        )
    saver = InMemorySaver()
    sessions = StudySessions(store, build_graph(FakeTutor(), store, saver), saver, lambda: now)

    with store.session_lock(USER, "t-1") as held:
        assert held is True
        sessions.housekeeping(USER)
        assert store.get_session(USER, "t-1") is not None

    sessions.housekeeping(USER)
    assert store.get_session(USER, "t-1") is None


def test_a_long_turn_renews_its_lease_when_it_ends(s):
    thread_id, first = started(s)
    events = s.sessions.reply(USER, thread_id, "tab-a", right(first))
    next(events)  # the run has begun, the lease was claimed at the start
    s.clock.advance(seconds=LEASE.total_seconds() - 30 + 1)  # the turn runs long
    last = list(events)[-1]

    assert isinstance(last, Pending)
    assert s.store.session_driver(USER, thread_id, s.clock(), LEASE) == "tab-a"
    s.clock.advance(seconds=45)  # past the start's lease, inside the renewed one
    assert s.store.session_driver(USER, thread_id, s.clock(), LEASE) == "tab-a"
