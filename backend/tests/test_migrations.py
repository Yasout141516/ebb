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
        assert migrate(conn, until="002_spaced_review.sql") == ["002_spaced_review.sql"]
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


def seed_document(conn, user_id, subject_id) -> UUID:
    doc_id = uuid4()
    conn.execute(
        "insert into documents (id, user_id, subject_id, filename, kind, size_bytes, sha256)"
        " values (%s, %s, %s, 'n.md', 'md', 10, %s)",
        (doc_id, user_id, subject_id, uuid4().hex),
    )
    return doc_id


def insert_chunk(conn, user_id, document_id, subject_id, heading="Subnet masks") -> None:
    conn.execute(
        "insert into document_chunks (id, user_id, document_id, subject_id, ordinal, heading,"
        " content) values (%s, %s, %s, %s, 0, %s, 'A mask splits an address.')",
        (uuid4(), user_id, document_id, subject_id, heading),
    )


def insert_dispute(conn, user_id, attempt_id) -> None:
    conn.execute(
        "insert into grade_disputes (id, user_id, attempt_id, reason, explanation,"
        " original_grade, new_grade, outcome, regrade_explanation, model_used)"
        " values (%s, %s, %s, 'correct', 'e', 'incorrect', 'correct', 'changed', 'r', 'm')",
        (uuid4(), user_id, attempt_id),
    )


def test_003_keeps_milestone_2_data(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        migrate(conn, until="002_spaced_review.sql")
        seed_milestone_1(conn)
        assert migrate(conn, until="003_notes_citations_disputes.sql") == [
            "003_notes_citations_disputes.sql"
        ]
        counts = row_counts(conn)
        question = conn.execute("select source, citations from questions").fetchone()
        attempt = conn.execute(
            "select concept_before, voided_at, citations from attempts"
        ).fetchone()
    assert counts["questions"] == 1 and counts["attempts"] == 1 and counts["concepts"] == 2
    assert question == {"source": "general", "citations": []}
    assert attempt == {"concept_before": None, "voided_at": None, "citations": []}


def test_004_renames_a_duplicate_subject_and_keeps_its_data(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        migrate(conn, until="003_notes_citations_disputes.sql")
        ids = seed_milestone_1(conn)
        newer, other, bobs = uuid4(), uuid4(), uuid4()
        conn.execute("insert into profiles (id) values (%s)", (BOB,))
        for sid, user, name, day in [
            (newer, ALICE, "networks", 20),
            (other, ALICE, "NETWORKS", 21),
            (bobs, BOB, "Networks", 22),
        ]:
            conn.execute(
                "insert into subjects (id, user_id, name, created_at) values (%s, %s, %s, %s)",
                (sid, user, name, datetime(2026, 10, day, tzinfo=UTC)),
            )
        conn.execute(
            "insert into concepts (user_id, subject_id, name, description)"
            " values (%s, %s, 'kept', 'd')",
            (ALICE, newer),
        )
        conn.execute("update subjects set created_at = %s where id = %s", (T, ids["subject"]))
        assert migrate(conn, until="004_web_app.sql") == ["004_web_app.sql"]
        names = {r["id"]: r["name"] for r in conn.execute("select id, name from subjects")}
        kept = conn.execute(
            "select count(*) as n from concepts where subject_id = %s", (newer,)
        ).fetchone()["n"]
        counts = row_counts(conn)
    assert names[ids["subject"]] == "Networks"
    assert names[newer] == "networks (2)"
    assert names[other] == "NETWORKS (3)"
    assert names[bobs] == "Networks"
    assert kept == 1
    assert counts["concepts"] == 3 and counts["attempts"] == 1


def test_004_rename_skips_a_name_already_taken(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        migrate(conn, until="003_notes_citations_disputes.sql")
        conn.execute("insert into profiles (id) values (%s)", (ALICE,))
        for name, day in [("Networks", 1), ("networks", 2), ("Networks (2)", 3)]:
            conn.execute(
                "insert into subjects (user_id, name, created_at) values (%s, %s, %s)",
                (ALICE, name, datetime(2026, 10, day, tzinfo=UTC)),
            )
        migrate(conn)
        names = sorted(r["name"] for r in conn.execute("select name from subjects"))
    assert names == ["Networks", "Networks (2)", "networks (3)"]


def test_004_adds_driver_columns_and_llm_usage(pg_pool):
    with pg_pool.connection() as conn:
        conn.execute("insert into profiles (id) values (%s)", (ALICE,))
        conn.execute("insert into llm_usage (user_id, day) values (%s, '2026-10-23')", (ALICE,))
        row = conn.execute("select calls from llm_usage").fetchone()
        cols = {
            r["column_name"]
            for r in conn.execute(
                "select column_name from information_schema.columns"
                " where table_name = 'study_sessions'"
            )
        }
    assert row["calls"] == 0
    assert {"driver", "driver_seen_at"} <= cols


def test_migration_005_applies_on_004(pg_empty_pool):
    with pg_empty_pool.connection() as conn:
        migrate(conn, until="004_web_app.sql")
        ids = seed_milestone_1(conn)
        assert migrate(conn, until="005_gateway_learn.sql") == ["005_gateway_learn.sql"]
        conn.execute(
            "insert into llm_calls (call_id, user_id, at, role, endpoint, provider, model,"
            " attempt, outcome, total_ms) values (%s, %s, %s, 'teach', 'lesson', 'p', 'm',"
            " 1, 'ok', 5)",
            (uuid4(), ALICE, T),
        )
        row = conn.execute("select backup, cost_usd, request_id from llm_calls").fetchone()
        conn.execute(
            "insert into concept_lessons (user_id, concept_id, markdown, model, written_at)"
            " values (%s, %s, '# x', 'm', %s)",
            (ALICE, ids["c1"], T),
        )
        lesson = conn.execute("select citations, learned_at from concept_lessons").fetchone()
        insert_exchange(conn, ids["c1"], 1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_exchange(conn, ids["c1"], 1)
        conn.execute("insert into profiles (id) values (%s)", (BOB,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "insert into concept_lessons (user_id, concept_id, markdown, model, written_at)"
                " values (%s, %s, '# x', 'm', %s)",
                (BOB, ids["c2"], T),
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert_exchange(conn, ids["c1"], 2, user_id=BOB)
        conn.execute("delete from concepts where id = %s", (ids["c1"],))
        left = conn.execute("select count(*) as n from concept_lessons").fetchone()["n"]
    assert row == {"backup": False, "cost_usd": None, "request_id": None}
    assert lesson == {"citations": [], "learned_at": None}
    assert left == 0


def insert_exchange(conn, concept_id, position, user_id=ALICE) -> None:
    conn.execute(
        "insert into lesson_exchanges (user_id, concept_id, position, kind, question, answer,"
        " asked_at) values (%s, %s, %s, 'typed', 'q', 'a', %s)",
        (user_id, concept_id, position, T),
    )


def test_new_tables_reject_cross_user_rows(pg_pool):
    with pg_pool.connection() as conn:
        ids = seed_alice_tree(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            seed_document(conn, BOB, ids["subject"])
        doc_id = seed_document(conn, ALICE, ids["subject"])
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert_chunk(conn, BOB, doc_id, ids["subject"])
        insert_attempt(conn, ALICE, ids["question"], ids["concept"])
        attempt_id = conn.execute("select id from attempts").fetchone()["id"]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            insert_dispute(conn, BOB, attempt_id)


def test_search_vector_is_generated_and_indexed(pg_pool):
    with pg_pool.connection() as conn:
        ids = seed_alice_tree(conn)
        doc_id = seed_document(conn, ALICE, ids["subject"])
        insert_chunk(conn, ALICE, doc_id, ids["subject"])
        hit = conn.execute(
            "select search_vector @@ to_tsquery('english', 'subnet') as hit from document_chunks"
        ).fetchone()
        index = conn.execute(
            "select indexdef from pg_indexes where indexname = 'document_chunks_search_idx'"
        ).fetchone()
    assert hit["hit"] is True
    assert "gin" in index["indexdef"].lower()


def test_one_dispute_per_attempt(pg_pool):
    with pg_pool.connection() as conn:
        ids = seed_alice_tree(conn)
        insert_attempt(conn, ALICE, ids["question"], ids["concept"])
        attempt_id = conn.execute("select id from attempts").fetchone()["id"]
        insert_dispute(conn, ALICE, attempt_id)
        with pytest.raises(psycopg.errors.UniqueViolation):
            insert_dispute(conn, ALICE, attempt_id)
