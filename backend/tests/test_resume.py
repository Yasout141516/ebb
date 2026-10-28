"""Resuming a session from a new process: real Postgres, real PostgresSaver, fake tutor."""

import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from typer.testing import CliRunner

from ebb import cli
from ebb.config import Settings
from ebb.db.migrate import MIGRATION_LOCK
from ebb.db.repo import PgStore
from ebb.graph.build import build_graph
from ebb.study import Offer, Pending, Step, StudySessions
from tests.fakes import FakeTutor

pytestmark = pytest.mark.db

ALICE = UUID("00000000-0000-0000-0000-00000000000a")
CONFIG = {"configurable": {"thread_id": "t-1"}}
runner = CliRunner()


@pytest.fixture
def store(pg_pool) -> PgStore:
    # The saver's tables live in public, which the fixture's schema reset dropped.
    PostgresSaver(pg_pool).setup()
    store = PgStore(pg_pool)
    store.ensure_profile(ALICE)
    return store


@pytest.fixture
def subject_id(store) -> UUID:
    return store.create_subject(ALICE, "Networks", [("DNS", "d"), ("TCP", "t"), ("UDP", "u")])


def new_process(store: PgStore) -> CompiledStateGraph:
    """A graph as a freshly started `ebb study` builds it: new saver, new tutor."""
    return build_graph(FakeTutor(), store, PostgresSaver(store.pool))


def pending(graph: CompiledStateGraph) -> dict[str, Any]:
    [waiting] = graph.get_state(CONFIG).interrupts
    return waiting.value


def reply(payload: dict[str, Any], correct: bool = True) -> dict[str, Any]:
    if payload["question"]["options"]:
        answer = "1" if correct else "2"  # FakeTutor's option 1 is the right one
    else:
        answer = "right" if correct else "nope"
    return {"answer": answer, "attempt_id": str(uuid4())}


def attempt_count(pg_pool) -> int:
    with pg_pool.connection() as conn:
        return conn.execute("select count(*) as n from attempts").fetchone()["n"]


def start(graph: CompiledStateGraph, subject_id: UUID) -> dict[str, Any]:
    state = {"user_id": str(ALICE), "subject_id": str(subject_id), "thread_id": "t-1"}
    return graph.invoke(state, CONFIG)["__interrupt__"][0].value


def test_resume_from_a_new_process(store, subject_id, pg_pool):
    graph = new_process(store)
    first = start(graph, subject_id)
    graph.invoke(Command(resume=reply(first)), CONFIG)
    del graph  # the terminal closes on question 2

    graph = new_process(store)
    second = pending(graph)
    assert second["concept"] == "DNS" and second["number"] == 2
    graph.invoke(Command(resume=reply(second)), CONFIG)
    final = graph.invoke(Command(resume={"quit": True}), CONFIG)

    assert "__interrupt__" not in final
    assert attempt_count(pg_pool) == 2
    [dns] = [row for row in final["results"] if row["concept"] == "DNS"]
    assert dns["tally"]["correct"] == 2
    assert store.resumable_session(ALICE, subject_id, datetime.now(UTC)) is None


def test_resume_twice_counts_each_answer_once(store, subject_id, pg_pool):
    graph = new_process(store)
    graph.invoke(Command(resume=reply(start(graph, subject_id))), CONFIG)

    graph = new_process(store)  # closed on DNS question 2
    second = pending(graph)
    assert (second["concept"], second["number"]) == ("DNS", 2)
    graph.invoke(Command(resume=reply(second)), CONFIG)

    graph = new_process(store)  # closed again, now on TCP question 1
    third = pending(graph)
    assert (third["concept"], third["number"]) == ("TCP", 1)
    graph.invoke(Command(resume=reply(third, correct=False)), CONFIG)
    final = graph.invoke(Command(resume={"quit": True}), CONFIG)

    assert attempt_count(pg_pool) == 3
    tallies = {row["concept"]: row["tally"] for row in final["results"]}
    assert tallies["DNS"] == {"correct": 2, "partial": 0, "incorrect": 0}
    assert tallies["TCP"] == {"correct": 0, "partial": 0, "incorrect": 1}


@pytest.fixture
def cli_on_postgres(store, monkeypatch) -> None:
    """`ebb study` on the real store and the real `_checkpointer`, with a fake tutor."""
    settings = Settings(_env_file=None, ebb_timezone="UTC", ebb_dev_user_id=ALICE)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, store))
    monkeypatch.setattr(cli, "_tutor", lambda s, r: FakeTutor())


def ended_at(store: PgStore, session_id: UUID) -> datetime | None:
    with store.pool.connection() as conn:
        return conn.execute(
            "select ended_at from study_sessions where id = %s", (session_id,)
        ).fetchone()["ended_at"]


def test_crash_while_grading_resumes_and_grades_once(
    store, subject_id, pg_pool, cli_on_postgres, monkeypatch
):
    crashing = FakeTutor()

    def killed_while_grading(question, answer, sources=(), failures=(), previous=None):
        raise RuntimeError("killed while grading")

    monkeypatch.setattr(crashing, "grade", killed_while_grading)
    monkeypatch.setattr(cli, "_tutor", lambda s, r: crashing)
    crashed = runner.invoke(cli.app, ["study"], input="right\n")  # Q1 is a short answer
    assert isinstance(crashed.exception, RuntimeError), crashed.output
    found = store.resumable_session(ALICE, subject_id, datetime.now(UTC))
    saved = new_process(store).get_state({"configurable": {"thread_id": found.thread_id}})
    assert not saved.interrupts and saved.next  # mid-step: the answer is in, not graded

    monkeypatch.setattr(cli, "_tutor", lambda s, r: FakeTutor())
    result = runner.invoke(cli.app, ["study"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    assert "You stopped on DNS, question 1, a moment ago. Resume? [Y/n]" in result.output
    assert "● Correct" in result.output.split("Resume? [Y/n]", 1)[1]
    assert attempt_count(pg_pool) == 1
    assert ended_at(store, found.session_id) is not None  # finished by q, same session


def study_service(store: PgStore, tutor: FakeTutor) -> StudySessions:
    """The session service as a freshly started API process builds it."""
    saver = PostgresSaver(store.pool)
    graph = build_graph(tutor, store, saver)
    return StudySessions(store, graph, saver, lambda: datetime.now(UTC))


def test_service_finishes_a_cut_off_step_once_after_a_restart(
    store, subject_id, pg_pool, monkeypatch
):
    crashing = FakeTutor()

    def killed_while_grading(question, answer, sources=(), failures=(), previous=None):
        raise RuntimeError("killed while grading")

    monkeypatch.setattr(crashing, "grade", killed_while_grading)
    sessions = study_service(store, crashing)
    thread_id, _, events = sessions.start(ALICE, subject_id, "tab-a")
    first = list(events)[-1]
    with pytest.raises(RuntimeError):
        list(sessions.reply(ALICE, thread_id, "tab-a", reply(first.payload)))

    sessions = study_service(store, FakeTutor())  # the API restarted; the tab reconnects
    offer = sessions.offer(ALICE, subject_id, "tab-a")
    resumed = list(sessions.resume(ALICE, thread_id, "tab-a"))

    assert offer == Offer(thread_id, "DNS", 1, offer.last_active_at, busy=False)
    last = resumed[-1]
    assert isinstance(last, Pending)
    assert last.payload["number"] == 2 and last.payload["feedback"]["value"] == "correct"
    assert attempt_count(pg_pool) == 1
    assert sessions.offer(ALICE, subject_id, "tab-b").busy is True  # tab-a's lease is live


def test_service_stream_closed_mid_turn_finishes_the_turn(store, subject_id, pg_pool):
    sessions = study_service(store, FakeTutor())
    thread_id, _, events = sessions.start(ALICE, subject_id, "tab-a")
    first = list(events)[-1]
    events = sessions.reply(ALICE, thread_id, "tab-a", reply(first.payload))
    assert next(events) == Step("Grading…")
    events.close()  # the browser went away while grading

    sessions = study_service(store, FakeTutor())
    offer = sessions.offer(ALICE, subject_id, "tab-a")
    resumed = list(sessions.resume(ALICE, thread_id, "tab-a"))

    assert (offer.concept, offer.number, offer.busy) == ("DNS", 2, False)
    [question] = resumed
    assert isinstance(question, Pending)
    assert question.payload["number"] == 2 and question.payload["feedback"]["value"] == "correct"
    assert attempt_count(pg_pool) == 1


def test_service_carries_on_a_step_cut_off_after_it_ran(store, subject_id, pg_pool):
    sessions = study_service(store, FakeTutor())
    thread_id, _, events = sessions.start(ALICE, subject_id, "tab-a")
    first = list(events)[-1]
    # A process that died mid-turn: grading ran, but no checkpoint was written after it.
    graph = new_process(store)
    stream = graph.stream(
        Command(resume=reply(first.payload)),
        {"configurable": {"thread_id": thread_id}},
        stream_mode=["custom", "values"],
    )
    assert ("custom", {"step": "grade_answer", "label": "Grading…"}) in stream
    stream.close()

    sessions = study_service(store, FakeTutor())
    offer = sessions.offer(ALICE, subject_id, "tab-a")
    resumed = list(sessions.resume(ALICE, thread_id, "tab-a"))

    assert (offer.concept, offer.number) == ("DNS", 1)
    last = resumed[-1]
    assert isinstance(last, Pending)
    assert last.payload["number"] == 2 and last.payload["feedback"]["value"] == "correct"
    assert attempt_count(pg_pool) == 1


def test_state_with_nothing_pending_is_closed(store, subject_id, cli_on_postgres):
    # A session whose process died before its first checkpoint was written.
    session_id = store.create_session(ALICE, subject_id, "never-checkpointed")

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Resume?" not in result.output
    assert "Nothing is due. Let's learn something new." in result.output
    assert ended_at(store, session_id) is not None


def waiting_for_the_migration_lock(conn: psycopg.Connection) -> bool:
    # A bigint advisory key shows in pg_locks as classid (high half) and objid (low half).
    row = conn.execute(
        "select count(*) from pg_locks"
        " where locktype = 'advisory' and not granted and classid = 0 and objid::bigint = %s"
        " and database = (select oid from pg_database where datname = current_database())",
        (MIGRATION_LOCK,),
    ).fetchone()
    return row[0] > 0


def saver_tables_exist(conn: psycopg.Connection) -> bool:
    return conn.execute("select to_regclass('checkpoints') is not null").fetchone()[0]


def test_checkpointer_setup_waits_for_the_migration_lock(pg_pool):
    # pg_pool's fresh schema has no saver tables yet, as on a first run.
    store = PgStore(pg_pool)
    savers = []
    with psycopg.connect(pg_pool.conninfo, autocommit=True) as other_start:
        other_start.execute("select pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        worker = threading.Thread(target=lambda: savers.append(cli._checkpointer(store)))
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not waiting_for_the_migration_lock(other_start):
                assert time.monotonic() < deadline, "setup() did not wait for the lock"
                time.sleep(0.02)
            assert savers == [] and not saver_tables_exist(other_start)
        finally:
            other_start.execute("select pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
            worker.join(timeout=10)
        assert not worker.is_alive() and len(savers) == 1
        assert saver_tables_exist(other_start)
