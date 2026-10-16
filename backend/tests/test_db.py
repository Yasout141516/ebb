"""Postgres tests: migrations, the repository, idempotency and per-user isolation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb.db.migrate import migrate
from ebb.db.repo import PgStore
from ebb.domain import Attempt, GradeValue, Option, Question
from ebb.graph.build import build_graph
from tests.fakes import FakeTutor

pytestmark = pytest.mark.db

ALICE = UUID("00000000-0000-0000-0000-00000000000a")
BOB = UUID("00000000-0000-0000-0000-00000000000b")
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


@pytest.fixture
def store(pg_pool):
    store = PgStore(pg_pool)
    store.ensure_profile(ALICE)
    store.ensure_profile(BOB)
    return store


def subject_with_question(store: PgStore) -> tuple[UUID, UUID, Question]:
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names to addresses.")])
    concept = store.list_concepts(ALICE, subject_id)[0]
    session_id = store.create_session(ALICE, subject_id, "thread-1")
    question = Question(uuid4(), concept.id, "short", 1, "Why cache?", "Speed.")
    store.save_questions(ALICE, session_id, [question])
    return subject_id, session_id, question


def test_migrations_apply_once(pg_pool):
    with pg_pool.connection() as conn:
        assert migrate(conn) == []  # the fixture already applied them
        versions = [r["version"] for r in conn.execute("select version from schema_migrations")]
    assert versions == ["001_init.sql", "002_spaced_review.sql"]


def test_concepts_keep_their_order(store):
    names = [("Frames", "a"), ("Packets", "b"), ("Segments", "c")]
    subject_id = store.create_subject(ALICE, "Networks", names)
    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == [
        "Frames",
        "Packets",
        "Segments",
    ]
    assert all(
        c.stage == 0 and c.next_review_at is None for c in store.list_concepts(ALICE, subject_id)
    )


def test_newest_subject_first(store):
    store.create_subject(ALICE, "Older", [])
    store.create_subject(ALICE, "Newer", [])
    assert [s.name for s in store.list_subjects(ALICE)][0] == "Newer"


def test_mcq_options_round_trip_as_json(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("UDP", "d")])
    concept = store.list_concepts(ALICE, subject_id)[0]
    session_id = store.create_session(ALICE, subject_id, "t")
    question = Question(
        uuid4(),
        concept.id,
        "mcq",
        2,
        "Which is connectionless?",
        "UDP",
        (Option("TCP", False, "Handshake."), Option("UDP", True, "No handshake.")),
    )
    store.save_questions(ALICE, session_id, [question])
    with pg_pool.connection() as conn:
        row = conn.execute("select options from questions where id = %s", (question.id,)).fetchone()
    assert row["options"][1] == {"text": "UDP", "is_correct": True, "why": "No handshake."}


def test_duplicate_attempt_is_written_once_and_moves_the_stage_once(store):
    subject_id, _, question = subject_with_question(store)
    concept = store.list_concepts(ALICE, subject_id)[0]
    first = replace(
        concept,
        stage=1,
        last_reviewed_at=NOW,
        next_review_at=NOW + timedelta(days=1),
        review_count=1,
    )
    attempt = Attempt(
        uuid4(), question.id, concept.id, "speed", GradeValue.CORRECT, "ok", 0, 1, "code", NOW
    )

    assert store.record_attempt(ALICE, attempt, first) is True
    assert store.record_attempt(ALICE, attempt, replace(first, stage=2)) is False

    assert store.list_concepts(ALICE, subject_id)[0].stage == 1


def test_other_users_see_nothing(store):
    subject_id, _, _ = subject_with_question(store)
    assert store.list_subjects(BOB) == []
    assert store.list_concepts(BOB, subject_id) == []
    with pytest.raises(LookupError):
        store.create_session(BOB, subject_id, "bob-thread")


def test_other_users_cannot_change_alices_rows(store, pg_pool):
    subject_id, session_id, question = subject_with_question(store)
    concept = store.list_concepts(ALICE, subject_id)[0]
    store.end_session(BOB, session_id, NOW)
    with pytest.raises(LookupError):
        store.record_attempt(
            BOB,
            Attempt(
                uuid4(), question.id, concept.id, "x", GradeValue.CORRECT, "ok", 0, 5, "code", NOW
            ),
            replace(concept, stage=5),
        )
    bobs_question = Question(uuid4(), concept.id, "short", 1, "Sneaky?", "No.")
    with pytest.raises(LookupError):
        store.save_questions(BOB, session_id, [bobs_question])
    with pg_pool.connection() as conn:
        ended = conn.execute(
            "select ended_at from study_sessions where id = %s", (session_id,)
        ).fetchone()
        bob_attempts = conn.execute(
            "select count(*) as n from attempts where user_id = %s", (BOB,)
        ).fetchone()
        bob_questions = conn.execute(
            "select count(*) as n from questions where user_id = %s", (BOB,)
        ).fetchone()
    assert ended["ended_at"] is None
    assert bob_attempts["n"] == 0
    assert bob_questions["n"] == 0
    assert store.list_concepts(ALICE, subject_id)[0].stage == 0


def test_a_session_persists_progress_in_postgres(store):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d"), ("TCP", "d")])
    graph = build_graph(FakeTutor(), store, InMemorySaver(), now=lambda: NOW)
    config = {"configurable": {"thread_id": "pg-run"}}
    result = graph.invoke(
        {"user_id": str(ALICE), "subject_id": str(subject_id), "thread_id": "pg-run"}, config
    )
    while "__interrupt__" in result:
        options = result["__interrupt__"][0].value["question"]["options"]
        answer = {"answer": "1" if options else "right", "attempt_id": str(uuid4())}
        result = graph.invoke(Command(resume=answer), config)

    assert [c.stage for c in store.list_concepts(ALICE, subject_id)] == [1, 1]
    assert len(result["results"]) == 2
