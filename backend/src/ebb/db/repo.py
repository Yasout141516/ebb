"""Postgres implementation of `Store`. Every query filters on user_id (review decision E1)."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ebb import citations, mastery
from ebb.db.locks import advisory_lock
from ebb.domain import (
    Answer,
    Chunk,
    Citation,
    Concept,
    ConceptResult,
    Dispute,
    DisputeOutcome,
    DisputeReason,
    Document,
    GradeValue,
    NewDocument,
    Question,
    ResumableSession,
    Subject,
)
from ebb.store import (
    MAX_DOCUMENTS,
    PRUNE_AFTER,
    RESUME_WINDOW,
    DocumentRefused,
    SubjectExists,
    concept_after_dispute,
    concept_result,
    new_concepts,
    restore,
    search_words,
    snapshot,
)

# Chunks ranking below this (ts_rank_cd) are not about the searched concept.
MIN_RANK = 0.05

_CONCEPT_COLUMNS = (
    "id, subject_id, name, description, stage, next_review_at, last_reviewed_at, review_count"
)


def _concept(row: dict[str, Any]) -> Concept:
    return Concept(**row)


def _citations_json(cited: Sequence[Citation]) -> Jsonb:
    return Jsonb([citations.to_dict(c) for c in cited])


def _dispute(row: dict[str, Any]) -> Dispute:
    return Dispute(
        row["id"],
        row["attempt_id"],
        DisputeReason(row["reason"]),
        row["explanation"],
        GradeValue(row["original_grade"]),
        None if row["new_grade"] is None else GradeValue(row["new_grade"]),
        DisputeOutcome(row["outcome"]),
        row["regrade_explanation"],
        tuple(citations.from_dict(c) for c in row["citations"]),
        row["model_used"],
    )


def _update_concept(conn: Connection, user_id: UUID, concept: Concept) -> None:
    conn.execute(
        "update concepts set stage = %s, next_review_at = %s, last_reviewed_at = %s,"
        " review_count = %s where id = %s and user_id = %s",
        (
            concept.stage,
            concept.next_review_at,
            concept.last_reviewed_at,
            concept.review_count,
            concept.id,
            user_id,
        ),
    )


def _insert_concepts(
    conn: Connection, user_id: UUID, subject_id: UUID, concepts: Sequence[tuple[str, str]]
) -> int:
    """Add the concepts the subject lacks after its last position, on an open connection that
    holds the subject row lock. Returns how many were added."""
    existing = conn.execute(
        "select name from concepts where user_id = %s and subject_id = %s",
        (user_id, subject_id),
    ).fetchall()
    fresh = new_concepts([row["name"] for row in existing], concepts)
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


def _insert_document(
    conn: Connection, user_id: UUID, subject_id: UUID, document: NewDocument
) -> None:
    row = conn.execute(
        "insert into documents (user_id, subject_id, filename, kind, size_bytes, page_count,"
        " sha256) values (%s, %s, %s, %s, %s, %s, %s) returning id",
        (
            user_id,
            subject_id,
            document.filename,
            document.kind,
            document.size_bytes,
            document.page_count,
            document.sha256,
        ),
    ).fetchone()
    with conn.cursor() as cur:
        cur.executemany(
            "insert into document_chunks (user_id, document_id, subject_id, ordinal, page_start,"
            " page_end, page_offsets, heading, content)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    user_id,
                    row["id"],
                    subject_id,
                    chunk.ordinal,
                    chunk.page_start,
                    chunk.page_end,
                    Jsonb(list(chunk.page_offsets)),
                    chunk.heading,
                    chunk.content,
                )
                for chunk in document.chunks
            ],
        )


def _insert_subject(conn: Connection, user_id: UUID, name: str) -> UUID:
    try:
        return conn.execute(
            "insert into subjects (user_id, name) values (%s, %s) returning id", (user_id, name)
        ).fetchone()["id"]
    except UniqueViolation as error:
        if error.diag.constraint_name != "subjects_user_name_key":
            raise
        raise SubjectExists(name) from error


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
            subject_id = _insert_subject(conn, user_id, name)
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
                    " prompt, options, reference_answer, source, citations)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    # verify_citations re-runs with the same ids if it dies before checkpointing.
                    " on conflict (id) do nothing",
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
                            q.source,
                            _citations_json(q.citations),
                        )
                        for q in questions
                    ],
                )

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept:
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
                " feedback, stage_before, stage_after, model_used, graded_at, citations,"
                " concept_before)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
                    _citations_json(answer.citations),
                    Jsonb(snapshot(before)),
                ),
            ).fetchone()
            if inserted is None:
                return before  # already recorded: the row already holds this answer's grade
            _update_concept(conn, user_id, after)
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

    def resolve_dispute(
        self, user_id: UUID, dispute: Dispute, now: datetime
    ) -> tuple[Dispute, Concept]:
        with self.pool.connection() as conn, conn.transaction():
            # Every write to a concept's attempts holds this row lock, so the checks below
            # read a history nothing else is changing.
            row = conn.execute(
                f"select {_CONCEPT_COLUMNS} from concepts where user_id = %s and id ="
                " (select concept_id from attempts where id = %s and user_id = %s) for update",
                (user_id, dispute.attempt_id, user_id),
            ).fetchone()
            if row is None:
                raise LookupError(f"attempt {dispute.attempt_id} not found")
            current = _concept(row)
            stored = conn.execute(
                "select * from grade_disputes where id = %s and user_id = %s",
                (dispute.id, user_id),
            ).fetchone()
            if stored is not None:
                if stored["attempt_id"] != dispute.attempt_id:
                    raise ValueError(f"dispute {dispute.id} is about another attempt")
                return _dispute(stored), current  # a re-run: already applied
            attempt = conn.execute(
                "select a.grade, a.graded_at, a.concept_before, a.voided_at,"
                " exists (select 1 from grade_disputes d"
                " where d.attempt_id = a.id and d.user_id = a.user_id) as disputed,"
                " (select l.id from attempts l"
                " where l.user_id = a.user_id and l.concept_id = a.concept_id"
                " and l.voided_at is null order by l.graded_at desc, l.id desc limit 1) as latest"
                " from attempts a where a.id = %s and a.user_id = %s",
                (dispute.attempt_id, user_id),
            ).fetchone()
            if attempt["disputed"]:
                raise ValueError(f"attempt {dispute.attempt_id} was already disputed")
            if attempt["voided_at"] is not None:
                raise ValueError(f"attempt {dispute.attempt_id} is voided")
            if attempt["concept_before"] is None:
                raise ValueError(f"attempt {dispute.attempt_id} has no concept snapshot")
            if attempt["latest"] != dispute.attempt_id:
                raise ValueError(f"attempt {dispute.attempt_id} is not the concept's latest")
            before = restore(current, attempt["concept_before"])
            after = concept_after_dispute(
                current, before, dispute, GradeValue(attempt["grade"]), attempt["graded_at"]
            )
            if dispute.outcome is DisputeOutcome.VOIDED:
                conn.execute(
                    "update attempts set voided_at = %s where id = %s and user_id = %s",
                    (now, dispute.attempt_id, user_id),
                )
            elif dispute.outcome is DisputeOutcome.CHANGED:
                conn.execute(
                    "update attempts set grade = %s, stage_after = %s"
                    " where id = %s and user_id = %s",
                    (dispute.new_grade.value, after.stage, dispute.attempt_id, user_id),
                )
            _update_concept(conn, user_id, after)
            conn.execute(
                "insert into grade_disputes (id, user_id, attempt_id, reason, explanation,"
                " original_grade, new_grade, outcome, regrade_explanation, citations,"
                " model_used) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    dispute.id,
                    user_id,
                    dispute.attempt_id,
                    dispute.reason.value,
                    dispute.explanation,
                    dispute.original_grade.value,
                    None if dispute.new_grade is None else dispute.new_grade.value,
                    dispute.outcome.value,
                    dispute.regrade_explanation,
                    _citations_json(dispute.citations),
                    dispute.model_used,
                ),
            )
        return dispute, after

    def session_summary(
        self, user_id: UUID, session_id: UUID, now: datetime
    ) -> list[ConceptResult]:
        first = "order by a.graded_at, a.id"
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select c.id, c.subject_id, c.name, c.description, c.stage, c.next_review_at,"
                " c.last_reviewed_at, c.review_count,"
                " count(*) filter (where a.grade = 'correct') as correct,"
                " count(*) filter (where a.grade = 'partial') as partial,"
                " count(*) filter (where a.grade = 'incorrect') as incorrect,"
                f" (array_agg(a.stage_before {first}))[1] as first_stage,"
                f" (array_agg(a.concept_before {first}))[1] as first_before,"
                " min(a.graded_at) as first_at"
                " from attempts a"
                " join questions q on q.id = a.question_id and q.user_id = a.user_id"
                " join concepts c on c.id = a.concept_id and c.user_id = a.user_id"
                " where a.user_id = %s and q.session_id = %s and a.voided_at is null"
                " group by c.id order by first_at, c.position",
                (user_id, session_id),
            ).fetchall()
        return [
            concept_result(
                Concept(**{k: row[k] for k in Concept.__dataclass_fields__}),
                {
                    GradeValue.CORRECT: row["correct"],
                    GradeValue.PARTIAL: row["partial"],
                    GradeValue.INCORRECT: row["incorrect"],
                },
                (row["first_stage"], row["first_before"], row["first_at"]),
                now,
            )
            for row in rows
        ]

    def get_dispute(self, user_id: UUID, dispute_id: UUID) -> Dispute | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select * from grade_disputes where id = %s and user_id = %s",
                (dispute_id, user_id),
            ).fetchone()
        return None if row is None else _dispute(row)

    def dispute_counts(self, user_id: UUID) -> dict[str, int]:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select count(*) as raised,"
                " count(*) filter (where outcome = 'changed') as changed,"
                " count(*) filter (where outcome = 'voided') as voided,"
                " count(*) filter (where outcome = 'upheld') as upheld"
                " from grade_disputes where user_id = %s",
                (user_id,),
            ).fetchone()
        return dict(row)

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
            return _insert_concepts(conn, user_id, subject_id, concepts)

    def create_subject_with_notes(
        self,
        user_id: UUID,
        name: str,
        documents: Sequence[NewDocument],
        concepts: Sequence[tuple[str, str]],
    ) -> UUID:
        with self.pool.connection() as conn, conn.transaction():
            subject_id = _insert_subject(conn, user_id, name)
            for document in documents:
                _insert_document(conn, user_id, subject_id, document)
            _insert_concepts(conn, user_id, subject_id, concepts)
        return subject_id

    def add_document(
        self,
        user_id: UUID,
        subject_id: UUID,
        document: NewDocument,
        concepts: Sequence[tuple[str, str]],
    ) -> int:
        with self.pool.connection() as conn, conn.transaction():
            # The subject row lock makes concurrent uploads count and insert one after the other.
            owned = conn.execute(
                "select 1 from subjects where id = %s and user_id = %s for update",
                (subject_id, user_id),
            ).fetchone()
            if owned is None:
                raise LookupError(f"subject {subject_id} not found")
            stored = conn.execute(
                "select count(*) as n, coalesce(bool_or(sha256 = %s), false) as same"
                " from documents where user_id = %s and subject_id = %s",
                (document.sha256, user_id, subject_id),
            ).fetchone()
            if stored["same"]:
                raise DocumentRefused("duplicate")
            if stored["n"] >= MAX_DOCUMENTS:
                raise DocumentRefused("full")
            _insert_document(conn, user_id, subject_id, document)
            return _insert_concepts(conn, user_id, subject_id, concepts)

    def list_documents(self, user_id: UUID, subject_id: UUID) -> list[Document]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select d.id, d.filename, d.kind, d.size_bytes, d.page_count, d.created_at,"
                " (select count(*) from document_chunks c where c.document_id = d.id"
                " and c.user_id = d.user_id)::int as chunk_count"
                " from documents d where d.user_id = %s and d.subject_id = %s"
                " order by d.created_at, d.id",
                (user_id, subject_id),
            ).fetchall()
        return [Document(**row) for row in rows]

    def has_document(self, user_id: UUID, subject_id: UUID, sha256: str) -> bool:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select 1 from documents where user_id = %s and subject_id = %s and sha256 = %s",
                (user_id, subject_id, sha256),
            ).fetchone()
        return row is not None

    def count_documents(self, user_id: UUID, subject_id: UUID) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "select count(*) as n from documents where user_id = %s and subject_id = %s",
                (user_id, subject_id),
            ).fetchone()
        return row["n"]

    def search_chunks(
        self, user_id: UUID, subject_id: UUID, text: str, limit: int = 4
    ) -> list[Chunk]:
        words = search_words(text)
        if not words:
            return []
        with self.pool.connection() as conn:
            # Materialized, so each row's rank is computed once for the filter and the order.
            rows = conn.execute(
                "with ranked as materialized ("
                " select c.id, d.filename, c.content, c.heading, c.page_start, c.page_offsets,"
                " d.created_at, c.ordinal, ts_rank_cd(c.search_vector, q) as rank"
                " from document_chunks c join documents d"
                " on d.id = c.document_id and d.user_id = c.user_id"
                " cross join websearch_to_tsquery('english', %s) q"
                " where c.user_id = %s and c.subject_id = %s and c.search_vector @@ q)"
                " select id, filename, content, heading, page_start, page_offsets from ranked"
                " where rank >= %s order by rank desc, created_at, ordinal limit %s",
                (" or ".join(words), user_id, subject_id, MIN_RANK, limit),
            ).fetchall()
        return [Chunk(**{**row, "page_offsets": tuple(row["page_offsets"])}) for row in rows]

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

    def take_ai_call(self, user_id: UUID, day: date, limit: int) -> bool:
        if limit < 1:
            return False
        with self.pool.connection() as conn:
            row = conn.execute(
                "insert into llm_usage (user_id, day, calls) values (%s, %s, 1)"
                " on conflict (user_id, day) do update set calls = llm_usage.calls + 1"
                " where llm_usage.calls < %s returning calls",
                (user_id, day, limit),
            ).fetchone()
        return row is not None

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

    def session_lock(self, user_id: UUID, thread_id: str) -> AbstractContextManager[bool]:
        # Thread ids are uuid4s, so the key is already unique without the user id.
        return advisory_lock(self.pool, thread_id, wait=False)
