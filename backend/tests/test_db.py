"""Postgres tests: migrations, the repository, idempotency and per-user isolation."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ebb.db.migrate import migrate
from ebb.db.repo import PgStore
from ebb.domain import Answer, GradeValue, Option, Question
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


def answer_for(
    question: Question, grade: GradeValue, at: datetime = NOW, answer_id: UUID | None = None
) -> Answer:
    return Answer(
        answer_id or uuid4(), question.id, question.concept_id, "text", grade, "ok", "code", at
    )


def attempt_row(pg_pool, answer: Answer) -> dict:
    with pg_pool.connection() as conn:
        return conn.execute("select * from attempts where id = %s", (answer.id,)).fetchone()


def seed_stage(pg_pool, concept_id: UUID, stage: int, due: datetime) -> None:
    with pg_pool.connection() as conn:
        conn.execute(
            "update concepts set stage = %s, next_review_at = %s, last_reviewed_at = %s,"
            " review_count = 1 where id = %s",
            (stage, due, due - timedelta(days=7), concept_id),
        )


def test_record_answer_grades_the_current_row_not_a_snapshot(store, pg_pool):
    subject_id, _, question = subject_with_question(store)
    seed_stage(pg_pool, question.concept_id, 3, NOW - timedelta(hours=1))
    answer_a = answer_for(question, GradeValue.INCORRECT)
    answer_b = answer_for(question, GradeValue.CORRECT, NOW + timedelta(minutes=1))

    assert store.record_answer(ALICE, answer_a).stage == 1
    # B's session still "saw" stage 3, but the row is now stage 1 and not due.
    assert store.record_answer(ALICE, answer_b).stage == 1

    row = attempt_row(pg_pool, answer_b)
    assert row["stage_before"] == 1 and row["stage_after"] == 1
    assert attempt_row(pg_pool, answer_a)["stage_before"] == 3
    assert store.list_concepts(ALICE, subject_id)[0].review_count == 2


def test_duplicate_answer_returns_none_and_changes_nothing(store):
    subject_id, _, question = subject_with_question(store)
    answer = answer_for(question, GradeValue.CORRECT)

    assert store.record_answer(ALICE, answer).stage == 1
    assert store.record_answer(ALICE, answer) is None

    concept = store.list_concepts(ALICE, subject_id)[0]
    assert concept.stage == 1 and concept.review_count == 1


def test_record_answer_touches_the_session(store, pg_pool):
    _, session_id, question = subject_with_question(store)
    later = NOW + timedelta(hours=2)
    store.record_answer(ALICE, answer_for(question, GradeValue.CORRECT, later))
    with pg_pool.connection() as conn:
        row = conn.execute(
            "select last_active_at from study_sessions where id = %s", (session_id,)
        ).fetchone()
    assert row["last_active_at"] == later


def test_record_answer_refuses_other_users(store, pg_pool):
    subject_id, _, question = subject_with_question(store)
    with pytest.raises(LookupError):
        store.record_answer(BOB, answer_for(question, GradeValue.CORRECT))
    with pg_pool.connection() as conn:
        bob_attempts = conn.execute(
            "select count(*) as n from attempts where user_id = %s", (BOB,)
        ).fetchone()
    assert bob_attempts["n"] == 0
    assert store.list_concepts(ALICE, subject_id)[0].stage == 0


def test_answered_prompts_only_answered_newest_first(store):
    subject_id, session_id, first = subject_with_question(store)
    concept_id = first.concept_id
    second = Question(uuid4(), concept_id, "short", 1, "Why TTL?", "Expiry.")
    third = Question(uuid4(), concept_id, "short", 1, "Never asked", "-")
    store.save_questions(ALICE, session_id, [second, third])
    store.record_answer(ALICE, answer_for(first, GradeValue.CORRECT, NOW))
    store.record_answer(ALICE, answer_for(second, GradeValue.CORRECT, NOW + timedelta(minutes=1)))

    assert store.answered_prompts(ALICE, concept_id) == ["Why TTL?", "Why cache?"]
    assert store.answered_prompts(ALICE, concept_id, limit=1) == ["Why TTL?"]
    assert store.answered_prompts(BOB, concept_id) == []


def test_add_concepts_drops_duplicates_and_appends(store):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d"), ("TCP", "d")])

    added = store.add_concepts(
        ALICE, subject_id, [("dns ", "dup"), ("UDP", "u"), ("udp", "dup"), ("QUIC", "q")]
    )

    assert added == 2
    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "TCP", "UDP", "QUIC"]
    assert all(c.stage == 0 for c in store.list_concepts(ALICE, subject_id))
    assert store.add_concepts(ALICE, subject_id, [("QUIC", "again")]) == 0
    with pytest.raises(LookupError):
        store.add_concepts(BOB, subject_id, [("Sneaky", "x")])


def test_counts_and_next_review(store, pg_pool):
    subject_id = store.create_subject(
        ALICE, "Networks", [("DNS", "d"), ("TCP", "d"), ("UDP", "d"), ("QUIC", "d")]
    )
    dns, tcp, udp, _ = store.list_concepts(ALICE, subject_id)
    seed_stage(pg_pool, dns.id, 2, NOW - timedelta(days=1))
    seed_stage(pg_pool, tcp.id, 1, NOW + timedelta(days=2))
    seed_stage(pg_pool, udp.id, 4, NOW + timedelta(days=5))

    assert store.count_due(ALICE, subject_id, NOW) == 1
    assert store.count_new(ALICE, subject_id) == 1
    assert store.next_review_at(ALICE, subject_id) == NOW - timedelta(days=1)
    assert store.count_due(BOB, subject_id, NOW) == 0
    assert store.next_review_at(BOB, subject_id) is None


def test_get_subject_is_scoped(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    assert store.get_subject(ALICE, subject_id).name == "Networks"
    assert store.get_subject(BOB, subject_id) is None


def test_set_timezone(store, pg_pool):
    store.set_timezone(ALICE, "Europe/Berlin")
    with pg_pool.connection() as conn:
        row = conn.execute("select timezone from profiles where id = %s", (ALICE,)).fetchone()
    assert row["timezone"] == "Europe/Berlin"


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
    bobs_question = Question(uuid4(), concept.id, "short", 1, "Sneaky?", "No.")
    with pytest.raises(LookupError):
        store.save_questions(BOB, session_id, [bobs_question])
    with pg_pool.connection() as conn:
        ended = conn.execute(
            "select ended_at from study_sessions where id = %s", (session_id,)
        ).fetchone()
        bob_questions = conn.execute(
            "select count(*) as n from questions where user_id = %s", (BOB,)
        ).fetchone()
    assert ended["ended_at"] is None
    assert bob_questions["n"] == 0


def test_a_session_persists_progress_in_postgres(store):
    # Imported here: the graph still imports removed names until it is rewired.
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from ebb.graph.build import build_graph

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


def make_session(store, pg_pool, subject_id, thread_id, active_at, ended_at=None):
    session_id = store.create_session(ALICE, subject_id, thread_id)
    with pg_pool.connection() as conn:
        conn.execute(
            "update study_sessions set last_active_at = %s, ended_at = %s where id = %s",
            (active_at, ended_at, session_id),
        )
    return session_id


def session_row(pg_pool, session_id) -> dict:
    with pg_pool.connection() as conn:
        return conn.execute("select * from study_sessions where id = %s", (session_id,)).fetchone()


def test_resumable_within_24_hours(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    session_id = make_session(
        store, pg_pool, subject_id, "t-1", NOW - timedelta(hours=23, minutes=59)
    )
    found = store.resumable_session(ALICE, subject_id, NOW)
    assert found.session_id == session_id and found.thread_id == "t-1"
    assert store.resumable_session(BOB, subject_id, NOW) is None


def test_exactly_24_hours_is_not_resumable(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    make_session(store, pg_pool, subject_id, "t-1", NOW - timedelta(hours=24))
    assert store.resumable_session(ALICE, subject_id, NOW) is None


def test_resumable_session_is_per_subject(store, pg_pool):
    a = store.create_subject(ALICE, "A", [("x", "x")])
    b = store.create_subject(ALICE, "B", [("y", "y")])
    make_session(store, pg_pool, a, "t-a", NOW - timedelta(hours=1))
    assert store.resumable_session(ALICE, b, NOW) is None
    assert store.resumable_session(ALICE, a, NOW).thread_id == "t-a"


def test_most_recent_resumable_session_wins(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    make_session(store, pg_pool, subject_id, "old", NOW - timedelta(hours=5))
    make_session(store, pg_pool, subject_id, "new", NOW - timedelta(hours=1))
    assert store.resumable_session(ALICE, subject_id, NOW).thread_id == "new"


def test_ended_sessions_are_not_resumable(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    hour_ago = NOW - timedelta(hours=1)
    make_session(store, pg_pool, subject_id, "t-1", hour_ago, ended_at=hour_ago)
    assert store.resumable_session(ALICE, subject_id, NOW) is None


def test_close_stale_sessions_uses_last_activity(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    stale_at = NOW - timedelta(hours=30)
    stale = make_session(store, pg_pool, subject_id, "stale", stale_at)
    fresh = make_session(store, pg_pool, subject_id, "fresh", NOW - timedelta(hours=1))
    assert store.close_stale_sessions(BOB, NOW) == 0
    assert store.close_stale_sessions(ALICE, NOW) == 1
    assert session_row(pg_pool, stale)["ended_at"] == stale_at
    assert session_row(pg_pool, fresh)["ended_at"] is None
    assert store.close_stale_sessions(ALICE, NOW) == 0


def test_threads_to_prune_after_7_days_once(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    ended = NOW - timedelta(days=7)
    make_session(store, pg_pool, subject_id, "old", ended, ended_at=ended)
    make_session(store, pg_pool, subject_id, "recent", NOW, ended_at=NOW - timedelta(days=6))
    assert store.threads_to_prune(ALICE, NOW) == ["old"]
    assert store.threads_to_prune(BOB, NOW) == []
    store.mark_pruned(BOB, ["old"], NOW)
    assert store.threads_to_prune(ALICE, NOW) == ["old"]
    store.mark_pruned(ALICE, ["old"], NOW)
    assert store.threads_to_prune(ALICE, NOW) == []


def test_session_lock_refuses_a_second_holder(store):
    with store.session_lock(ALICE, "t-1") as first:
        with store.session_lock(ALICE, "t-1") as second:
            assert first is True and second is False
    with store.session_lock(ALICE, "t-1") as again:
        assert again is True


def test_session_lock_leaves_a_connection_for_queries_and_other_threads(store):
    with store.session_lock(ALICE, "t-1") as held:
        assert held is True
        assert store.list_subjects(ALICE) == []  # the second pooled connection still serves
    with store.session_lock(ALICE, "t-1") as a, store.session_lock(ALICE, "t-2") as b:
        assert a is True and b is True
