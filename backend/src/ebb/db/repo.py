"""Postgres implementation of `Store`. Every query filters on user_id (review decision E1)."""

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ebb.domain import Attempt, Concept, Question, Subject

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

    def record_attempt(self, user_id: UUID, attempt: Attempt, concept_after: Concept) -> bool:
        with self.pool.connection() as conn, conn.transaction():
            owned_question = conn.execute(
                "select 1 from questions where id = %s and user_id = %s and concept_id = %s",
                (attempt.question_id, user_id, attempt.concept_id),
            ).fetchone()
            owned_concept = conn.execute(
                "select 1 from concepts where id = %s and user_id = %s",
                (attempt.concept_id, user_id),
            ).fetchone()
            if owned_question is None or owned_concept is None:
                raise LookupError(f"question {attempt.question_id} not found")
            inserted = conn.execute(
                "insert into attempts (id, user_id, question_id, concept_id, answer_text, grade,"
                " feedback, stage_before, stage_after, model_used, graded_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " on conflict (id) do nothing returning id",
                (
                    attempt.id,
                    user_id,
                    attempt.question_id,
                    attempt.concept_id,
                    attempt.answer_text,
                    attempt.grade.value,
                    attempt.feedback,
                    attempt.stage_before,
                    attempt.stage_after,
                    attempt.model_used,
                    attempt.graded_at,
                ),
            ).fetchone()
            if inserted is None:
                return False
            conn.execute(
                "update concepts set stage = %s, next_review_at = %s, last_reviewed_at = %s,"
                " review_count = %s where id = %s and user_id = %s",
                (
                    concept_after.stage,
                    concept_after.next_review_at,
                    concept_after.last_reviewed_at,
                    concept_after.review_count,
                    concept_after.id,
                    user_id,
                ),
            )
        return True
