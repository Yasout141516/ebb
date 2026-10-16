"""Postgres implementation of `Store`. Every query filters on user_id (review decision E1)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ebb import mastery
from ebb.domain import Answer, Concept, Question, ResumableSession, Subject
from ebb.store import PRUNE_AFTER, RESUME_WINDOW

_CONCEPT_COLUMNS = (
    "id, subject_id, name, description, stage, next_review_at, last_reviewed_at, review_count"
)


def _concept(row: dict[str, Any]) -> Concept:
    return Concept(**row)


class PgStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def ensure_profile(self, user_id: UUID) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "insert into profiles (id) values (%s) on conflict (id) do nothing", (user_id,)
            )

    def create_subject(self, user_id: UUID, name: str, concepts: Sequence[tuple[str, str]]) -> UUID:
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "insert into subjects (user_id, name) values (%s, %s) returning id",
                (user_id, name),
            ).fetchone()
            subject_id = row["id"]
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into concepts (user_id, subject_id, name, description, position)"
                    " values (%s, %s, %s, %s, %s)",
                    [
                        (user_id, subject_id, concept_name, description, position)
                        for position, (concept_name, description) in enumerate(concepts)
                    ],
                )
        return subject_id

    def list_subjects(self, user_id: UUID) -> list[Subject]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select id, name, created_at from subjects where user_id = %s"
                " order by created_at desc",
                (user_id,),
            ).fetchall()
        return [Subject(**row) for row in rows]

    def list_concepts(self, user_id: UUID, subject_id: UUID) -> list[Concept]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"select {_CONCEPT_COLUMNS} from concepts"
                " where user_id = %s and subject_id = %s order by position",
                (user_id, subject_id),
            ).fetchall()
        return [_concept(row) for row in rows]

    def create_session(self, user_id: UUID, subject_id: UUID, thread_id: str) -> UUID:
        with self.pool.connection() as conn:
            row = conn.execute(
                "insert into study_sessions (user_id, subject_id, thread_id)"
                " select %s, id, %s from subjects where id = %s and user_id = %s"
                " returning id",
                (user_id, thread_id, subject_id, user_id),
            ).fetchone()
        if row is None:
            raise LookupError(f"subject {subject_id} not found")
        return row["id"]

    def end_session(self, user_id: UUID, session_id: UUID, now: datetime) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "update study_sessions set ended_at = %s where id = %s and user_id = %s",
                (now, session_id, user_id),
            )

    def save_questions(
        self, user_id: UUID, session_id: UUID, questions: Sequence[Question]
    ) -> None:
        with self.pool.connection() as conn, conn.transaction():
            owned_session = conn.execute(
                "select 1 from study_sessions where id = %s and user_id = %s",
                (session_id, user_id),
            ).fetchone()
            if owned_session is None:
                raise LookupError(f"session {session_id} not found")
            concept_ids = list({q.concept_id for q in questions})
            owned_concepts = conn.execute(
                "select count(*) as n from concepts where user_id = %s and id = any(%s::uuid[])",
                (user_id, concept_ids),
            ).fetchone()
            if owned_concepts["n"] != len(concept_ids):
                raise LookupError("concept not found")
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into questions (id, user_id, session_id, concept_id, type, difficulty,"
                    " prompt, options, reference_answer)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            q.id,
                            user_id,
                            session_id,
                            q.concept_id,
                            q.type,
                            q.difficulty,
                            q.prompt,
                            Jsonb([asdict(o) for o in q.options]) if q.options else None,
                            q.reference_answer,
                        )
                        for q in questions
                    ],
                )

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept | None:
        with self.pool.connection() as conn, conn.transaction():
            owned_question = conn.execute(
                "select session_id from questions"
                " where id = %s and user_id = %s and concept_id = %s",
                (answer.question_id, user_id, answer.concept_id),
            ).fetchone()
            # The row lock makes concurrent answers for one concept grade one after the other.
            row = conn.execute(
                f"select {_CONCEPT_COLUMNS} from concepts where id = %s and user_id = %s"
                " for update",
                (answer.concept_id, user_id),
            ).fetchone()
            if owned_question is None or row is None:
                raise LookupError(f"question {answer.question_id} not found")
            before = _concept(row)
            after = mastery.apply_grade(before, answer.grade, answer.graded_at)
            inserted = conn.execute(
                "insert into attempts (id, user_id, question_id, concept_id, answer_text, grade,"
                " feedback, stage_before, stage_after, model_used, graded_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " on conflict (id) do nothing returning id",
                (
                    answer.id,
                    user_id,
                    answer.question_id,
                    answer.concept_id,
                    answer.answer_text,
                    answer.grade.value,
                    answer.feedback,
                    before.stage,
                    after.stage,
                    answer.model_used,
                    answer.graded_at,
                ),
            ).fetchone()
            if inserted is None:
                return None
            conn.execute(
                "update concepts set stage = %s, next_review_at = %s, last_reviewed_at = %s,"
                " review_count = %s where id = %s and user_id = %s",
                (
                    after.stage,
                    after.next_review_at,
                    after.last_reviewed_at,
                    after.review_count,
                    after.id,
                    user_id,
                ),
            )
            conn.execute(
                "update study_sessions set last_active_at = %s where id = %s and user_id = %s",
                (answer.graded_at, owned_question["session_id"], user_id),
            )
        return after

    def answered_prompts(self, user_id: UUID, concept_id: UUID, limit: int = 40) -> list[str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select prompt from ("
                " select q.prompt, max(a.graded_at) as last_answered"
                " from questions q join attempts a on a.question_id = q.id"
                " where q.user_id = %s and a.user_id = %s and q.concept_id = %s"
                " group by q.prompt) answered"
                " order by last_answered desc limit %s",
                (user_id, user_id, concept_id, limit),
            ).fetchall()
        return [row["prompt"] for row in rows]

    def get_subject(self, user_id: UUID, subject_id: UUID) -> Subject | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select id, name, created_at from subjects where id = %s and user_id = %s",
                (subject_id, user_id),
            ).fetchone()
        return None if row is None else Subject(**row)

    def add_concepts(
        self, user_id: UUID, subject_id: UUID, concepts: Sequence[tuple[str, str]]
    ) -> int:
        with self.pool.connection() as conn, conn.transaction():
            # Locking the subject row serialises concurrent additions to its concept list.
            owned = conn.execute(
                "select 1 from subjects where id = %s and user_id = %s for update",
                (subject_id, user_id),
            ).fetchone()
            if owned is None:
                raise LookupError(f"subject {subject_id} not found")
            existing = conn.execute(
                "select name from concepts where user_id = %s and subject_id = %s",
                (user_id, subject_id),
            ).fetchall()
            seen = {row["name"].strip().casefold() for row in existing}
            fresh = []
            for name, description in concepts:
                key = name.strip().casefold()
                if key not in seen:
                    seen.add(key)
                    fresh.append((name, description))
            if not fresh:
                return 0
            top = conn.execute(
                "select coalesce(max(position), -1) as top from concepts"
                " where user_id = %s and subject_id = %s",
                (user_id, subject_id),
            ).fetchone()["top"]
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into concepts (user_id, subject_id, name, description, position)"
                    " values (%s, %s, %s, %s, %s)",
                    [
                        (user_id, subject_id, name, description, top + 1 + offset)
                        for offset, (name, description) in enumerate(fresh)
                    ],
                )
        return len(fresh)

    def count_due(self, user_id: UUID, subject_id: UUID, now: datetime) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select count(*) as n from concepts where user_id = %s and subject_id = %s"
                " and stage >= 1 and next_review_at <= %s",
                (user_id, subject_id, now),
            ).fetchone()
        return row["n"]

    def count_new(self, user_id: UUID, subject_id: UUID) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select count(*) as n from concepts where user_id = %s and subject_id = %s"
                " and stage = 0",
                (user_id, subject_id),
            ).fetchone()
        return row["n"]

    def next_review_at(self, user_id: UUID, subject_id: UUID) -> datetime | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select min(next_review_at) as soonest from concepts"
                " where user_id = %s and subject_id = %s and stage >= 1",
                (user_id, subject_id),
            ).fetchone()
        return row["soonest"]

    def set_timezone(self, user_id: UUID, name: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("update profiles set timezone = %s where id = %s", (name, user_id))

    def resumable_session(
        self, user_id: UUID, subject_id: UUID, now: datetime
    ) -> ResumableSession | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select id, thread_id, last_active_at from study_sessions"
                " where user_id = %s and subject_id = %s and ended_at is null"
                " and last_active_at > %s order by last_active_at desc limit 1",
                (user_id, subject_id, now - RESUME_WINDOW),
            ).fetchone()
        if row is None:
            return None
        return ResumableSession(row["id"], row["thread_id"], row["last_active_at"])

    def close_stale_sessions(self, user_id: UUID, now: datetime) -> int:
        with self.pool.connection() as conn:
            return conn.execute(
                "update study_sessions set ended_at = last_active_at"
                " where user_id = %s and ended_at is null and last_active_at <= %s",
                (user_id, now - RESUME_WINDOW),
            ).rowcount

    def threads_to_prune(self, user_id: UUID, now: datetime) -> list[str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select thread_id from study_sessions"
                " where user_id = %s and ended_at <= %s and pruned_at is null"
                " order by ended_at",
                (user_id, now - PRUNE_AFTER),
            ).fetchall()
        return [row["thread_id"] for row in rows]

    def mark_pruned(self, user_id: UUID, thread_ids: Sequence[str], now: datetime) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "update study_sessions set pruned_at = %s"
                " where user_id = %s and thread_id = any(%s)",
                (now, user_id, list(thread_ids)),
            )

    @contextmanager
    def session_lock(self, user_id: UUID, thread_id: str) -> Iterator[bool]:
        # A session-level advisory lock belongs to one connection, so hold one for the
        # whole block. hashtextextended gives a stable signed 64-bit key from the thread id.
        with self.pool.connection() as conn:
            got = conn.execute(
                "select pg_try_advisory_lock(hashtextextended(%s, 0)) as got", (thread_id,)
            ).fetchone()["got"]
            try:
                yield got
            finally:
                if got:
                    conn.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (thread_id,))
