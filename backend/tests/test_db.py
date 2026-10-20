"""Postgres tests: migrations, the repository, idempotency and per-user isolation."""

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb import citations
from ebb.db.migrate import migrate
from ebb.db.repo import PgStore
from ebb.domain import (
    Answer,
    ChunkDraft,
    Citation,
    Claim,
    Concept,
    Dispute,
    DisputeOutcome,
    DisputeReason,
    GradeValue,
    NewDocument,
    Option,
    Question,
)
from ebb.graph.build import build_graph
from ebb.store import DocumentRefused
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
    assert versions == [
        "001_init.sql",
        "002_spaced_review.sql",
        "003_notes_citations_disputes.sql",
    ]


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


def test_duplicate_answer_returns_the_stored_row_and_changes_nothing(store):
    subject_id, _, question = subject_with_question(store)
    answer = answer_for(question, GradeValue.CORRECT)

    first = store.record_answer(ALICE, answer)
    assert first.stage == 1
    assert store.record_answer(ALICE, answer) == first

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


def lock_is_free(pg_pool, thread_id: str) -> bool:
    """Ask from a connection outside the pool, which holds no locks of its own."""
    with psycopg.connect(pg_pool.conninfo, autocommit=True) as other:
        got = other.execute(
            "select pg_try_advisory_lock(hashtextextended(%s, 0))", (thread_id,)
        ).fetchone()[0]
    return got


def test_session_lock_is_released_when_the_body_raises(store, pg_pool):
    with pytest.raises(RuntimeError), store.session_lock(ALICE, "t-1") as got:
        assert got is True
        raise RuntimeError("crash mid-session")
    assert lock_is_free(pg_pool, "t-1")


def test_session_lock_on_a_dead_connection_keeps_the_original_error(store, pg_pool):
    with pytest.raises(RuntimeError, match="crash"), store.session_lock(ALICE, "t-1"):
        with pg_pool.connection() as conn:
            conn.execute(
                "select pg_terminate_backend(pid) from pg_locks where locktype = 'advisory'"
                " and database = (select oid from pg_database where datname = current_database())"
            )
        raise RuntimeError("crash")
    assert lock_is_free(pg_pool, "t-1")


def doc(name: str, *contents: str, sha: str | None = None) -> NewDocument:
    chunks = tuple(ChunkDraft(i, text, f"Part {i}", 1, 1, (0,)) for i, text in enumerate(contents))
    return NewDocument(name, "txt", 100, None, sha or f"sha-{name}", chunks)


def test_add_document_saves_chunks_and_new_concepts(store):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names.")])

    added = store.add_document(
        ALICE, subject_id, doc("a.txt", "one", "two"), [("DNS", "dup"), ("ARP", "Maps.")]
    )

    assert added == 1
    [saved] = store.list_documents(ALICE, subject_id)
    assert (saved.filename, saved.kind, saved.chunk_count) == ("a.txt", "txt", 2)
    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "ARP"]


def test_add_document_to_another_users_subject_is_refused(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    with pytest.raises(LookupError):
        store.add_document(BOB, subject_id, doc("a.txt", "one"), [])
    assert store.list_documents(ALICE, subject_id) == []


def test_fifth_document_ok_sixth_refused(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    for n in range(5):
        store.add_document(ALICE, subject_id, doc(f"{n}.txt", "x"), [])

    with pytest.raises(DocumentRefused) as refused:
        store.add_document(ALICE, subject_id, doc("6.txt", "x"), [])

    assert refused.value.reason == "full"
    assert len(store.list_documents(ALICE, subject_id)) == 5


def test_same_file_twice_refused(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, doc("a.txt", "x", sha="same"), [])

    with pytest.raises(DocumentRefused) as refused:
        store.add_document(ALICE, subject_id, doc("b.txt", "y", sha="same"), [])

    assert refused.value.reason == "duplicate"
    assert store.has_document(ALICE, subject_id, "same")
    assert not store.has_document(ALICE, subject_id, "other")
    assert not store.has_document(BOB, subject_id, "same")


def test_concurrent_adds_respect_the_limit(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    for n in range(4):
        store.add_document(ALICE, subject_id, doc(f"{n}.txt", "x"), [])
    outcomes: list[str] = []

    def add(name: str) -> None:
        try:
            store.add_document(ALICE, subject_id, doc(name, "x"), [])
            outcomes.append("ok")
        except DocumentRefused as refused:
            outcomes.append(refused.reason)

    threads = [threading.Thread(target=add, args=(f"c{n}.txt",)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["full", "ok"]
    assert len(store.list_documents(ALICE, subject_id)) == 5


def test_create_subject_with_notes_stores_everything_in_order(store):
    subject_id = store.create_subject_with_notes(
        ALICE,
        "Networks",
        [doc("a.txt", "one")],
        [("DNS", "Names."), ("dns", "dup"), ("ARP", "Maps.")],
    )

    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "ARP"]
    assert [d.filename for d in store.list_documents(ALICE, subject_id)] == ["a.txt"]


def test_create_subject_with_notes_is_one_transaction(store):
    bad = NewDocument("a.txt", "bad-kind", 1, None, "s", (ChunkDraft(0, "x", None, 1, 1, ()),))

    with pytest.raises(psycopg.errors.CheckViolation):
        store.create_subject_with_notes(ALICE, "Networks", [bad], [("DNS", "Names.")])

    assert store.list_subjects(ALICE) == []


SUBNET = (
    "Subnet masks split a network into subnets. The mask decides the network part and "
    "the host part of an address, and the broadcast address closes each subnet."
)
ARP = "ARP maps IP addresses to MAC addresses on a local network."
TCP = "The TCP three-way handshake opens a connection with SYN, SYN-ACK and ACK."


def test_search_finds_the_matching_chunk_and_ignores_others(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, doc("net.txt", SUBNET, ARP, TCP), [])

    found = store.search_chunks(
        ALICE, subject_id, "Subnetting: dividing a network using subnet masks"
    )

    assert found[0].content == SUBNET
    assert found[0].filename == "net.txt"
    assert found[0].page_offsets == (0,)
    assert TCP not in [c.content for c in found]


def test_search_returns_nothing_for_an_uncovered_concept(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, doc("net.txt", SUBNET, ARP, TCP), [])

    assert store.search_chunks(ALICE, subject_id, "Quantum entanglement") == []
    assert store.search_chunks(ALICE, subject_id, "a to") == []


def test_documents_are_scoped_to_the_user(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, doc("net.txt", SUBNET), [])

    assert store.list_documents(BOB, subject_id) == []
    assert store.search_chunks(BOB, subject_id, "subnet masks broadcast") == []
    assert store.search_chunks(ALICE, subject_id, "subnet masks broadcast") != []


def test_count_documents(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    assert store.count_documents(ALICE, subject_id) == 0
    store.add_document(ALICE, subject_id, doc("a.txt", "x"), [])
    store.add_document(ALICE, subject_id, doc("b.txt", "y"), [])

    assert store.count_documents(ALICE, subject_id) == 2
    assert store.count_documents(BOB, subject_id) == 0


CITE = Citation("chunk", "3f0c2a5e-0000-0000-0000-000000000001", "N1", 3, "masks split a network")
DUE = NOW - timedelta(hours=1)


def dispute_for(
    answer: Answer,
    outcome: DisputeOutcome,
    new_grade: GradeValue | None = None,
    dispute_id: UUID | None = None,
) -> Dispute:
    return Dispute(
        dispute_id or uuid4(),
        answer.id,
        DisputeReason.CORRECT,
        "I said the same thing.",
        answer.grade,
        new_grade,
        outcome,
        "Re-checked against the notes.",
        (CITE,),
        "strict-model",
    )


def stage_3_then_incorrect(store, pg_pool) -> tuple[UUID, Question, Concept, Answer]:
    """A due stage-3 concept answered incorrectly at NOW; returns the concept before it."""
    subject_id, _, question = subject_with_question(store)
    seed_stage(pg_pool, question.concept_id, 3, DUE)
    [before] = store.list_concepts(ALICE, subject_id)
    answer = answer_for(question, GradeValue.INCORRECT)
    assert store.record_answer(ALICE, answer).stage == 1
    return subject_id, question, before, answer


def test_citations_round_trip_on_questions_and_attempts(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names.")])
    concept = store.list_concepts(ALICE, subject_id)[0]
    session_id = store.create_session(ALICE, subject_id, "t")
    question = Question(
        uuid4(),
        concept.id,
        "short",
        1,
        "Why split networks?",
        "Smaller broadcast domains.",
        source="notes",
        claims=(Claim("N1", "masks split a network"),),
        citations=(CITE,),
    )
    store.save_questions(ALICE, session_id, [question])
    answer = replace(answer_for(question, GradeValue.CORRECT), citations=(CITE,))
    store.record_answer(ALICE, answer)

    with pg_pool.connection() as conn:
        saved = conn.execute(
            "select source, citations from questions where id = %s", (question.id,)
        ).fetchone()
    assert saved["source"] == "notes"
    assert [citations.from_dict(c) for c in saved["citations"]] == [CITE]
    assert [citations.from_dict(c) for c in attempt_row(pg_pool, answer)["citations"]] == [CITE]


def test_saving_a_question_batch_twice_is_a_no_op(store, pg_pool):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names.")])
    concept = store.list_concepts(ALICE, subject_id)[0]
    session_id = store.create_session(ALICE, subject_id, "t")
    question = Question(
        uuid4(), concept.id, "short", 1, "Why split?", "-", source="notes", citations=(CITE,)
    )
    store.save_questions(ALICE, session_id, [question])

    store.save_questions(ALICE, session_id, [question])  # a replay after a crash
    store.save_questions(ALICE, session_id, [replace(question, source="general", citations=())])

    with pg_pool.connection() as conn:
        rows = conn.execute(
            "select source, citations from questions where id = %s", (question.id,)
        ).fetchall()
    assert [r["source"] for r in rows] == ["notes"]
    assert [citations.from_dict(c) for c in rows[0]["citations"]] == [CITE]


class CrashAfterSavingQuestions(PgStore):
    """The process dies after the batch commits but before verify_citations checkpoints."""

    crashed = False

    def save_questions(self, user_id, session_id, questions):
        super().save_questions(user_id, session_id, questions)
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("killed after saving the batch")


def test_resume_after_a_crash_while_saving_questions(pg_pool):
    store = CrashAfterSavingQuestions(pg_pool)
    store.ensure_profile(ALICE)
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    graph = build_graph(FakeTutor(), store, InMemorySaver(), now=lambda: NOW)
    config = {"configurable": {"thread_id": "pg-crash"}}
    start = {"user_id": str(ALICE), "subject_id": str(subject_id), "thread_id": "pg-crash"}
    with pytest.raises(RuntimeError):
        graph.invoke(start, config)

    result = graph.invoke(None, config)  # verify_citations runs again with the same ids

    assert result["__interrupt__"][0].value["question"]["prompt"] == "DNS question 1"
    with pg_pool.connection() as conn:
        saved = conn.execute(
            "select count(*) as n from questions where user_id = %s", (ALICE,)
        ).fetchone()
    assert saved["n"] == 5


def test_record_answer_snapshots_the_concept_before(store, pg_pool):
    _, _, before, answer = stage_3_then_incorrect(store, pg_pool)

    snapshot = attempt_row(pg_pool, answer)["concept_before"]

    assert snapshot["stage"] == 3 and snapshot["review_count"] == 1
    assert datetime.fromisoformat(snapshot["next_review_at"]) == before.next_review_at
    assert datetime.fromisoformat(snapshot["last_reviewed_at"]) == before.last_reviewed_at


def test_record_answer_snapshots_a_new_concept_with_nulls(store, pg_pool):
    _, _, question = subject_with_question(store)
    answer = answer_for(question, GradeValue.CORRECT)
    store.record_answer(ALICE, answer)

    assert attempt_row(pg_pool, answer)["concept_before"] == {
        "stage": 0,
        "next_review_at": None,
        "last_reviewed_at": None,
        "review_count": 0,
    }


def test_changed_dispute_recomputes_from_the_snapshot(store, pg_pool):
    subject_id, _, before, answer = stage_3_then_incorrect(store, pg_pool)
    dispute = dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.CORRECT)

    stored, concept = store.resolve_dispute(ALICE, dispute, NOW + timedelta(minutes=5))

    assert stored == dispute
    assert concept == store.list_concepts(ALICE, subject_id)[0]
    assert concept.stage == 4
    assert concept.next_review_at == answer.graded_at + timedelta(days=16)
    assert concept.last_reviewed_at == answer.graded_at
    assert concept.review_count == before.review_count + 1
    row = attempt_row(pg_pool, answer)
    assert (row["grade"], row["stage_before"], row["stage_after"]) == ("correct", 3, 4)
    assert row["feedback"] == "ok" and row["voided_at"] is None


def test_changed_dispute_can_lower_the_grade(store, pg_pool):
    _, _, question = subject_with_question(store)
    seed_stage(pg_pool, question.concept_id, 3, DUE)
    answer = answer_for(question, GradeValue.CORRECT)
    assert store.record_answer(ALICE, answer).stage == 4

    _, concept = store.resolve_dispute(
        ALICE, dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.INCORRECT), NOW
    )

    assert concept.stage == 1 and concept.next_review_at == NOW + timedelta(days=1)
    assert attempt_row(pg_pool, answer)["stage_after"] == 1


def test_voided_dispute_restores_the_concept_exactly(store, pg_pool):
    subject_id, _, before, answer = stage_3_then_incorrect(store, pg_pool)
    voided_at = NOW + timedelta(minutes=5)

    stored, concept = store.resolve_dispute(
        ALICE, dispute_for(answer, DisputeOutcome.VOIDED), voided_at
    )

    assert stored.outcome is DisputeOutcome.VOIDED and stored.new_grade is None
    assert concept == before == store.list_concepts(ALICE, subject_id)[0]
    row = attempt_row(pg_pool, answer)
    assert row["voided_at"] == voided_at and row["grade"] == "incorrect"


def test_voided_question_stays_in_answered_prompts(store, pg_pool):
    _, question, _, answer = stage_3_then_incorrect(store, pg_pool)
    store.resolve_dispute(ALICE, dispute_for(answer, DisputeOutcome.VOIDED), NOW)

    assert store.answered_prompts(ALICE, question.concept_id) == [question.prompt]


def test_upheld_dispute_changes_nothing_but_is_recorded(store, pg_pool):
    subject_id, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    [graded] = store.list_concepts(ALICE, subject_id)
    row_before = attempt_row(pg_pool, answer)
    dispute = dispute_for(answer, DisputeOutcome.UPHELD, GradeValue.INCORRECT)

    stored, concept = store.resolve_dispute(ALICE, dispute, NOW)

    assert stored == dispute
    assert concept == graded == store.list_concepts(ALICE, subject_id)[0]
    assert attempt_row(pg_pool, answer) == row_before
    with pg_pool.connection() as conn:
        row = conn.execute("select * from grade_disputes where id = %s", (dispute.id,)).fetchone()
    assert (row["user_id"], row["attempt_id"], row["outcome"]) == (ALICE, answer.id, "upheld")
    assert [citations.from_dict(c) for c in row["citations"]] == [CITE]


def test_same_dispute_id_twice_returns_the_stored_result(store, pg_pool):
    subject_id, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    first = dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.CORRECT)
    _, concept = store.resolve_dispute(ALICE, first, NOW)
    # A re-run after a crash may carry a different re-check; the stored one wins.
    rerun = replace(first, outcome=DisputeOutcome.VOIDED, new_grade=None)

    assert store.resolve_dispute(ALICE, rerun, NOW + timedelta(minutes=1)) == (first, concept)
    assert store.list_concepts(ALICE, subject_id)[0] == concept
    assert store.dispute_counts(ALICE)["raised"] == 1


def test_second_dispute_on_an_attempt_is_refused(store, pg_pool):
    subject_id, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    upheld = dispute_for(answer, DisputeOutcome.UPHELD, GradeValue.INCORRECT)
    store.resolve_dispute(ALICE, upheld, NOW)
    [graded] = store.list_concepts(ALICE, subject_id)

    with pytest.raises(ValueError):
        store.resolve_dispute(
            ALICE, dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.CORRECT), NOW
        )

    assert store.list_concepts(ALICE, subject_id)[0] == graded
    assert store.dispute_counts(ALICE)["raised"] == 1


def test_only_the_latest_attempt_can_be_disputed(store, pg_pool):
    _, question, _, first = stage_3_then_incorrect(store, pg_pool)
    second = answer_for(question, GradeValue.CORRECT, NOW + timedelta(days=2))
    store.record_answer(ALICE, second)

    with pytest.raises(ValueError):
        store.resolve_dispute(
            ALICE, dispute_for(first, DisputeOutcome.CHANGED, GradeValue.CORRECT), NOW
        )

    # Once the later answer is voided, the first is the latest that counts again.
    store.resolve_dispute(ALICE, dispute_for(second, DisputeOutcome.VOIDED), NOW)
    _, concept = store.resolve_dispute(
        ALICE, dispute_for(first, DisputeOutcome.CHANGED, GradeValue.CORRECT), NOW
    )
    assert concept.stage == 4


def test_attempts_from_before_003_cannot_be_disputed(store, pg_pool):
    _, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    with pg_pool.connection() as conn:
        conn.execute("update attempts set concept_before = null where id = %s", (answer.id,))

    with pytest.raises(ValueError):
        store.resolve_dispute(ALICE, dispute_for(answer, DisputeOutcome.VOIDED), NOW)

    assert store.dispute_counts(ALICE)["raised"] == 0


def test_voided_attempts_cannot_be_disputed(store, pg_pool):
    _, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    with pg_pool.connection() as conn:
        conn.execute("update attempts set voided_at = %s where id = %s", (NOW, answer.id))

    with pytest.raises(ValueError):
        store.resolve_dispute(ALICE, dispute_for(answer, DisputeOutcome.VOIDED), NOW)


def test_dispute_must_match_the_attempt(store, pg_pool):
    _, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    claims_correct = replace(
        dispute_for(answer, DisputeOutcome.UPHELD), original_grade=GradeValue.CORRECT
    )

    with pytest.raises(ValueError):  # the attempt was graded incorrect, not correct
        store.resolve_dispute(ALICE, claims_correct, NOW)
    with pytest.raises(ValueError):  # changed needs a new grade
        store.resolve_dispute(ALICE, dispute_for(answer, DisputeOutcome.CHANGED), NOW)


def test_concurrent_disputes_apply_once(store, pg_pool):
    subject_id, _, before, answer = stage_3_then_incorrect(store, pg_pool)
    outcomes: list[str] = []

    def resolve() -> None:
        dispute = dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.CORRECT)
        try:
            store.resolve_dispute(ALICE, dispute, NOW)
            outcomes.append("ok")
        except ValueError:
            outcomes.append("refused")

    threads = [threading.Thread(target=resolve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["ok", "refused"]
    concept = store.list_concepts(ALICE, subject_id)[0]
    assert (concept.stage, concept.review_count) == (4, before.review_count + 1)


def test_other_users_cannot_dispute_alices_attempts(store, pg_pool):
    subject_id, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    [graded] = store.list_concepts(ALICE, subject_id)

    with pytest.raises(LookupError):
        store.resolve_dispute(BOB, dispute_for(answer, DisputeOutcome.VOIDED), NOW)

    assert store.list_concepts(ALICE, subject_id)[0] == graded
    assert attempt_row(pg_pool, answer)["voided_at"] is None


def test_dispute_counts(store):
    assert store.dispute_counts(ALICE) == {"raised": 0, "changed": 0, "voided": 0, "upheld": 0}
    subject_id = store.create_subject(ALICE, "Networks", [("A", "a"), ("B", "b"), ("C", "c")])
    session_id = store.create_session(ALICE, subject_id, "t")
    outcomes = [
        (DisputeOutcome.CHANGED, GradeValue.CORRECT),
        (DisputeOutcome.VOIDED, None),
        (DisputeOutcome.UPHELD, GradeValue.INCORRECT),
    ]
    for concept, (outcome, new_grade) in zip(
        store.list_concepts(ALICE, subject_id), outcomes, strict=True
    ):
        question = Question(uuid4(), concept.id, "short", 1, f"About {concept.name}?", "-")
        store.save_questions(ALICE, session_id, [question])
        answer = answer_for(question, GradeValue.INCORRECT)
        store.record_answer(ALICE, answer)
        store.resolve_dispute(ALICE, dispute_for(answer, outcome, new_grade), NOW)

    assert store.dispute_counts(ALICE) == {"raised": 3, "changed": 1, "voided": 1, "upheld": 1}
    assert store.dispute_counts(BOB) == {"raised": 0, "changed": 0, "voided": 0, "upheld": 0}
