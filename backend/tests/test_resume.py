"""Resuming a session from a new process: real Postgres, real PostgresSaver, fake tutor."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from typer.testing import CliRunner

from ebb import cli
from ebb.config import Settings
from ebb.db.repo import PgStore
from ebb.graph.build import build_graph
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


def test_state_without_a_pending_question_is_not_resumable(store, subject_id, monkeypatch):
    settings = Settings(_env_file=None, ebb_timezone="UTC", ebb_dev_user_id=ALICE)
    crashing = FakeTutor()

    def killed_while_grading(question, answer):
        raise RuntimeError("killed while grading")

    monkeypatch.setattr(crashing, "grade", killed_while_grading)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, store))
    monkeypatch.setattr(cli, "_tutor", lambda s: crashing)
    crashed = runner.invoke(cli.app, ["study"], input="right\n")  # Q1 is a short answer
    assert isinstance(crashed.exception, RuntimeError), crashed.output
    found = store.resumable_session(ALICE, subject_id, datetime.now(UTC))
    assert found is not None
    assert (
        not new_process(store)
        .get_state({"configurable": {"thread_id": found.thread_id}})
        .interrupts
    )

    monkeypatch.setattr(cli, "_tutor", lambda s: FakeTutor())
    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Resume?" not in result.output
    assert "Nothing is due. Let's learn something new." in result.output
    assert store.resumable_session(ALICE, subject_id, datetime.now(UTC)) is None
    with store.pool.connection() as conn:
        ended = conn.execute(
            "select ended_at from study_sessions where id = %s", (found.session_id,)
        ).fetchone()["ended_at"]
    assert ended is not None
