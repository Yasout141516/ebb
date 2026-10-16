"""Migration 002: milestone 1 data survives, ownership is enforced by the database."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from ebb.db.migrate import migrate

pytestmark = pytest.mark.db

ALICE = UUID("00000000-0000-0000-0000-00000000000a")
BOB = UUID("00000000-0000-0000-0000-00000000000b")
T = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
TABLES = ["profiles", "subjects", "concepts", "study_sessions", "questions", "attempts"]


def insert_attempt(conn, user_id, question_id, concept_id) -> None:
    conn.execute(
        "insert into attempts (id, user_id, question_id, concept_id, answer_text, grade,"
        " feedback, stage_before, stage_after, model_used, graded_at)"
        " values (%s, %s, %s, %s, 'x', 'correct', 'f', 0, 1, 'm', %s)",
        (uuid4(), user_id, question_id, concept_id, T),
    )


def seed_milestone_1(conn) -> dict[str, UUID]:
    """Raw SQL, valid under the 001 schema: one of each table, two concepts."""
    ids = {name: uuid4() for name in ("subject", "c1", "c2", "session", "question")}
    conn.execute("insert into profiles (id) values (%s)", (ALICE,))
    conn.execute(
        "insert into subjects (id, user_id, name) values (%s, %s, 'Networks')",
        (ids["subject"], ALICE),
    )
    for key in ("c1", "c2"):
        conn.execute(
            "insert into concepts (id, user_id, subject_id, name, description)"
            " values (%s, %s, %s, %s, 'd')",
            (ids[key], ALICE, ids["subject"], key),
        )
    conn.execute(
        "insert into study_sessions (id, user_id, subject_id, thread_id, started_at)"
        " values (%s, %s, %s, 't1', %s)",
        (ids["session"], ALICE, ids["subject"], datetime(2026, 10, 1, tzinfo=UTC)),
    )
    conn.execute(
        "insert into questions (id, user_id, session_id, concept_id, type, difficulty,"
        " prompt, reference_answer) values (%s, %s, %s, %s, 'short', 1, 'q', 'a')",
        (ids["question"], ALICE, ids["session"], ids["c1"]),
    )
    insert_attempt(conn, ALICE, ids["question"], ids["c1"])
    return ids


def row_counts(conn) -> dict[str, int]:
    return {
        table: conn.execute(f"select count(*) as n from {table}").fetchone()["n"]
        for table in TABLES
    }


def seed_alice_tree(conn) -> dict[str, UUID]:
    """Two profiles, and for Alice a subject, concept, session and question."""
    ids = {name: uuid4() for name in ("subject", "concept", "session", "question")}
    conn.execute("insert into profiles (id) values (%s), (%s)", (ALICE, BOB))
    conn.execute(
        "insert into subjects (id, user_id, name) values (%s, %s, 'Networks')",
        (ids["subject"], ALICE),
    )
    conn.execute(
        "insert into concepts (id, user_id, subject_id, name, description)"
        " values (%s, %s, %s, 'DNS', 'd')",
        (ids["concept"], ALICE, ids["subject"]),
    )
    conn.execute(
        "insert into study_sessions (id, user_id, subject_id, thread_id) values (%s, %s, %s, 't')",
        (ids["session"], ALICE, ids["subject"]),
    )
    conn.execute(
        "insert into questions (id, user_id, session_id, concept_id, type, difficulty,"
        " prompt, reference_answer) values (%s, %s, %s, %s, 'short', 1, 'q', 'a')",
        (ids["question"], ALICE, ids["session"], ids["concept"]),
    )
    return ids


def test_002_keeps_milestone_1_data(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        assert migrate(conn, until="001_init.sql") == ["001_init.sql"]
        seed_milestone_1(conn)
        assert migrate(conn) == ["002_spaced_review.sql"]
        counts = row_counts(conn)
        session = conn.execute("select last_active_at, pruned_at from study_sessions").fetchone()
    assert counts == {
        "profiles": 1,
        "subjects": 1,
        "concepts": 2,
        "study_sessions": 1,
        "questions": 1,
        "attempts": 1,
    }
    assert session["last_active_at"] == T
    assert session["pruned_at"] is None


def test_002_backfills_a_session_without_attempts_from_started_at(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        migrate(conn, until="001_init.sql")
        seed_milestone_1(conn)
        conn.execute("delete from attempts")
        migrate(conn)
        row = conn.execute("select last_active_at, started_at from study_sessions").fetchone()
    assert row["last_active_at"] == row["started_at"]


def test_composite_keys_reject_a_child_owned_by_another_user(pg_pool):
    with pg_pool.connection() as conn:
        ids = seed_alice_tree(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "insert into concepts (user_id, subject_id, name, description)"
                " values (%s, %s, 'DNS', 'd')",
                (BOB, ids["subject"]),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "insert into study_sessions (user_id, subject_id, thread_id) values (%s, %s, 't2')",
                (BOB, ids["subject"]),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "insert into questions (user_id, session_id, concept_id, type, difficulty,"
                " prompt, reference_answer) values (%s, %s, %s, 'short', 1, 'q', 'a')",
                (BOB, ids["session"], ids["concept"]),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert_attempt(conn, BOB, ids["question"], ids["concept"])


def test_new_sessions_get_last_active_at(pg_pool):
    with pg_pool.connection() as conn:
        seed_alice_tree(conn)
        row = conn.execute(
            "select last_active_at, pruned_at, now() as now from study_sessions"
        ).fetchone()
    assert row["pruned_at"] is None
    assert abs((row["now"] - row["last_active_at"]).total_seconds()) < 60


def test_deleting_a_subject_still_cascades(pg_pool):
    with pg_pool.connection() as conn:
        ids = seed_alice_tree(conn)
        insert_attempt(conn, ALICE, ids["question"], ids["concept"])
        conn.execute("delete from subjects where id = %s", (ids["subject"],))
        counts = row_counts(conn)
    assert counts == {
        "profiles": 2,
        "subjects": 0,
        "concepts": 0,
        "study_sessions": 0,
        "questions": 0,
        "attempts": 0,
    }
