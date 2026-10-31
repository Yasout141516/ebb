"""Postgres tests: migrations, the repository, idempotency and per-user isolation."""

import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb import citations, mastery
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
    Exchange,
    GradeValue,
    LlmCall,
    NewDocument,
    Option,
    Question,
    StoredLesson,
)
from ebb.graph.build import build_graph
from ebb.progress import replay
from ebb.store import DocumentRefused, SubjectExists
from ebb.subjects import ensure_name_free
from tests.fakes import FakeTutor, MemoryStore, new_document

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
        "004_web_app.sql",
        "005_gateway_learn.sql",
        "006_lesson_checks.sql",
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


def test_create_subject_refuses_a_name_differing_only_in_case(store):
    store.create_subject(ALICE, "Networks", [])
    with pytest.raises(SubjectExists) as raised:
        store.create_subject(ALICE, "networks", [("DNS", "d")])
    assert raised.value.name == "networks"
    with pytest.raises(SubjectExists):
        store.create_subject_with_notes(ALICE, "NETWORKS", [], [])
    store.create_subject(BOB, "networks", [])  # another user's name is no clash
    assert [s.name for s in store.list_subjects(ALICE)] == ["Networks"]


def test_names_compare_as_postgres_lower_does(store):
    # lower() keeps ß, so "Straße" and "STRASSE" are two names (casefold would make them one).
    for subjects in (store, MemoryStore()):
        subjects.ensure_profile(ALICE)
        subjects.create_subject(ALICE, "Straße", [])

        name = ensure_name_free(subjects, ALICE, "STRASSE")
        subjects.create_subject(ALICE, name, [])

        with pytest.raises(SubjectExists):
            ensure_name_free(subjects, ALICE, "STRAßE")
        with pytest.raises(SubjectExists):
            subjects.create_subject(ALICE, "STRAßE", [])


def test_memory_store_refuses_a_name_differing_only_in_case():
    memory = MemoryStore()
    memory.create_subject(ALICE, "Networks", [])
    with pytest.raises(SubjectExists):
        memory.create_subject(ALICE, "networks", [])
    with pytest.raises(SubjectExists):
        memory.create_subject_with_notes(ALICE, "NETWORKS", [], [])
    memory.create_subject(BOB, "networks", [])


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


def test_next_review(store, pg_pool):
    subject_id = store.create_subject(
        ALICE, "Networks", [("DNS", "d"), ("TCP", "d"), ("UDP", "d"), ("QUIC", "d")]
    )
    dns, tcp, udp, _ = store.list_concepts(ALICE, subject_id)
    seed_stage(pg_pool, dns.id, 2, NOW - timedelta(days=1))
    seed_stage(pg_pool, tcp.id, 1, NOW + timedelta(days=2))
    seed_stage(pg_pool, udp.id, 4, NOW + timedelta(days=5))

    assert store.next_review_at(ALICE, subject_id) == NOW - timedelta(days=1)
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


def test_add_document_saves_chunks_and_new_concepts(store):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names.")])

    added = store.add_document(
        ALICE, subject_id, new_document("a.txt", "one", "two"), [("DNS", "dup"), ("ARP", "Maps.")]
    )

    assert added == 1
    [saved] = store.list_documents(ALICE, subject_id)
    assert (saved.filename, saved.kind, saved.chunk_count) == ("a.txt", "txt", 2)
    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "ARP"]


def test_add_document_to_another_users_subject_is_refused(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    with pytest.raises(LookupError):
        store.add_document(BOB, subject_id, new_document("a.txt", "one"), [])
    assert store.list_documents(ALICE, subject_id) == []


def test_fifth_document_ok_sixth_refused(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    for n in range(5):
        store.add_document(ALICE, subject_id, new_document(f"{n}.txt", "x"), [])

    with pytest.raises(DocumentRefused) as refused:
        store.add_document(ALICE, subject_id, new_document("6.txt", "x"), [])

    assert refused.value.reason == "full"
    assert len(store.list_documents(ALICE, subject_id)) == 5


def test_same_file_twice_refused(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, new_document("a.txt", "x", sha="same"), [])

    with pytest.raises(DocumentRefused) as refused:
        store.add_document(ALICE, subject_id, new_document("b.txt", "y", sha="same"), [])

    assert refused.value.reason == "duplicate"
    assert store.has_document(ALICE, subject_id, "same")
    assert not store.has_document(ALICE, subject_id, "other")
    assert not store.has_document(BOB, subject_id, "same")


def test_concurrent_adds_respect_the_limit(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    for n in range(4):
        store.add_document(ALICE, subject_id, new_document(f"{n}.txt", "x"), [])
    outcomes: list[str] = []

    def add(name: str) -> None:
        try:
            store.add_document(ALICE, subject_id, new_document(name, "x"), [])
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
        [new_document("a.txt", "one")],
        [("DNS", "Names."), ("dns", "dup"), ("ARP", "Maps.")],
    )

    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "ARP"]
    assert [d.filename for d in store.list_documents(ALICE, subject_id)] == ["a.txt"]


@pytest.mark.parametrize("kind", ["memory", "postgres"])
def test_files_saved_together_keep_their_order(kind, request):
    store = MemoryStore() if kind == "memory" else PgStore(request.getfixturevalue("pg_pool"))
    store.ensure_profile(ALICE)
    names = ["e.txt", "a.txt", "d.txt", "b.txt", "c.txt"]

    subject_id = store.create_subject_with_notes(
        ALICE, "Networks", [new_document(n, "text") for n in names], []
    )

    assert [d.filename for d in store.list_documents(ALICE, subject_id)] == names


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
    store.add_document(ALICE, subject_id, new_document("net.txt", SUBNET, ARP, TCP), [])

    found = store.search_chunks(
        ALICE, subject_id, "Subnetting: dividing a network using subnet masks"
    )

    assert found[0].content == SUBNET
    assert found[0].filename == "net.txt"
    assert found[0].page_offsets == (0,)
    assert TCP not in [c.content for c in found]


def test_search_returns_nothing_for_an_uncovered_concept(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, new_document("net.txt", SUBNET, ARP, TCP), [])

    assert store.search_chunks(ALICE, subject_id, "Quantum entanglement") == []
    assert store.search_chunks(ALICE, subject_id, "a to") == []


def test_documents_are_scoped_to_the_user(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, new_document("net.txt", SUBNET), [])

    assert store.list_documents(BOB, subject_id) == []
    assert store.search_chunks(BOB, subject_id, "subnet masks broadcast") == []
    assert store.search_chunks(ALICE, subject_id, "subnet masks broadcast") != []


def test_count_documents(store):
    subject_id = store.create_subject(ALICE, "Networks", [])
    assert store.count_documents(ALICE, subject_id) == 0
    store.add_document(ALICE, subject_id, new_document("a.txt", "x"), [])
    store.add_document(ALICE, subject_id, new_document("b.txt", "y"), [])

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

    assert (
        result["__interrupt__"][0].value["question"]["prompt"] == "DNS question 1 (part 1a or 1b)"
    )
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


def test_get_dispute_finds_only_the_users_own(store, pg_pool):
    _, _, _, answer = stage_3_then_incorrect(store, pg_pool)
    dispute = dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.CORRECT)
    assert store.get_dispute(ALICE, dispute.id) is None

    store.resolve_dispute(ALICE, dispute, NOW)

    assert store.get_dispute(ALICE, dispute.id) == dispute
    assert store.get_dispute(BOB, dispute.id) is None
    assert store.get_dispute(ALICE, uuid4()) is None


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


# --- the session summary, read from either store -----------------------------------------


@pytest.fixture(params=["memory", "postgres"])
def summary_store(request):
    if request.param == "memory":
        store = MemoryStore()
    else:
        store = PgStore(request.getfixturevalue("pg_pool"))
    store.ensure_profile(ALICE)
    store.ensure_profile(BOB)
    return store


class Summarised:
    """A subject of two concepts, A answered in an earlier session and then in this one."""

    def __init__(self, store) -> None:
        self.store = store
        subject_id = store.create_subject(ALICE, "Networks", [("A", "a"), ("B", "b")])
        self.a, self.b = store.list_concepts(ALICE, subject_id)
        self.earlier = store.create_session(ALICE, subject_id, "earlier")
        self.session = store.create_session(ALICE, subject_id, "now")
        self.long_ago = NOW - timedelta(days=2)
        self.answer(self.earlier, self.a, GradeValue.CORRECT, self.long_ago)  # A: stage 1

    def answer(self, session_id, concept, grade, at) -> Answer:
        question = Question(uuid4(), concept.id, "short", 1, f"{uuid4()}?", "-")
        self.store.save_questions(ALICE, session_id, [question])
        answer = answer_for(question, grade, at)
        self.store.record_answer(ALICE, answer)
        return answer

    def summary(self, user_id=ALICE, session_id=None):
        return self.store.session_summary(user_id, session_id or self.session, NOW)


def test_summary_counts_this_sessions_answers_by_grade(summary_store):
    s = Summarised(summary_store)
    s.answer(s.session, s.b, GradeValue.PARTIAL, NOW + timedelta(minutes=3))
    s.answer(s.session, s.a, GradeValue.CORRECT, NOW)  # due: stage 2
    s.answer(s.session, s.a, GradeValue.PARTIAL, NOW + timedelta(minutes=1))
    s.answer(s.session, s.a, GradeValue.INCORRECT, NOW + timedelta(minutes=2))  # back to 1

    first, second = s.summary()

    a, b = summary_store.list_concepts(ALICE, s.a.subject_id)
    assert (first.concept_id, first.concept) == (a.id, "A")
    assert (first.correct, first.partial, first.incorrect) == (1, 1, 1)
    assert (first.stage_before, first.stage_after) == (1, 1)
    assert first.tide_before == pytest.approx(0.7**2)  # stage 1, reviewed two days before NOW
    assert first.tide_after == pytest.approx(mastery.tide(a, NOW))
    assert first.next_review_at == a.next_review_at
    assert (second.concept_id, second.concept) == (b.id, "B")  # A's first answer came first
    assert (second.correct, second.partial, second.incorrect) == (0, 1, 0)
    assert (second.stage_before, second.stage_after) == (0, 1)
    assert second.tide_before is None


def test_summary_skips_voided_answers_and_drops_a_concept_left_with_none(summary_store):
    s = Summarised(summary_store)
    s.answer(s.session, s.a, GradeValue.CORRECT, NOW)  # stage 2
    voided = s.answer(s.session, s.a, GradeValue.INCORRECT, NOW + timedelta(minutes=1))
    only = s.answer(s.session, s.b, GradeValue.PARTIAL, NOW + timedelta(minutes=2))
    for answer in (only, voided):
        summary_store.resolve_dispute(ALICE, dispute_for(answer, DisputeOutcome.VOIDED), NOW)

    [row] = s.summary()

    assert row.concept == "A"
    assert (row.correct, row.partial, row.incorrect) == (1, 0, 0)
    assert row.stage_after == 2  # restored by the void


def test_summary_reflects_a_changed_dispute(summary_store):
    s = Summarised(summary_store)
    s.answer(s.session, s.a, GradeValue.CORRECT, NOW)
    wrong = s.answer(s.session, s.a, GradeValue.INCORRECT, NOW + timedelta(minutes=1))
    changed = dispute_for(wrong, DisputeOutcome.CHANGED, GradeValue.CORRECT)
    _, after = summary_store.resolve_dispute(ALICE, changed, NOW)

    [row] = s.summary()

    assert (row.correct, row.partial, row.incorrect) == (2, 0, 0)
    assert row.stage_after == after.stage


def test_summary_ignores_answers_from_other_sessions(summary_store):
    s = Summarised(summary_store)
    s.answer(s.session, s.b, GradeValue.CORRECT, NOW)

    [row] = s.summary()  # A's answer belongs to the earlier session

    assert row.concept == "B"
    assert [r.concept for r in s.summary(session_id=s.earlier)] == ["A"]
    assert s.summary(user_id=BOB) == []
    assert s.summary(session_id=uuid4()) == []


# --- the daily AI-call cap -------------------------------------------------------------


@pytest.fixture(params=["postgres", "memory"])
def usage_store(request, store):
    return store if request.param == "postgres" else MemoryStore()


def test_take_ai_call_stops_at_the_limit(usage_store):
    day = date(2026, 10, 15)

    assert [usage_store.take_ai_call(ALICE, day, 2) for _ in range(3)] == [True, True, False]
    assert usage_store.take_ai_call(BOB, day, 2) is True  # counted per user


def test_take_ai_call_with_limit_zero_refuses_without_writing(usage_store):
    assert usage_store.take_ai_call(ALICE, date(2026, 10, 15), 0) is False
    assert usage_store.take_ai_call(ALICE, date(2026, 10, 15), 1) is True  # nothing was counted


def test_ai_calls_used_counts_per_user_and_day(usage_store):
    day = date(2026, 10, 15)

    assert usage_store.ai_calls_used(ALICE, day) == 0  # no row yet
    usage_store.take_ai_call(ALICE, day, 5)
    usage_store.take_ai_call(ALICE, day, 5)
    usage_store.take_ai_call(BOB, day, 5)
    usage_store.take_ai_call(ALICE, date(2026, 10, 16), 5)

    assert usage_store.ai_calls_used(ALICE, day) == 2
    assert usage_store.ai_calls_used(BOB, day) == 1


def test_take_ai_call_leaves_no_row_when_the_limit_is_zero(store, pg_pool):
    store.take_ai_call(ALICE, date(2026, 10, 15), 0)

    with pg_pool.connection() as conn:
        assert conn.execute("select count(*) as n from llm_usage").fetchone()["n"] == 0


def test_take_ai_call_counts_per_day_in_the_users_zone(usage_store):
    zone = ZoneInfo("Asia/Dhaka")
    before_midnight = datetime(2026, 10, 15, 23, 30, tzinfo=zone).astimezone(zone).date()
    after_midnight = datetime(2026, 10, 16, 0, 30, tzinfo=zone).astimezone(zone).date()

    assert before_midnight != after_midnight
    assert usage_store.take_ai_call(ALICE, before_midnight, 1) is True
    assert usage_store.take_ai_call(ALICE, before_midnight, 1) is False
    assert usage_store.take_ai_call(ALICE, after_midnight, 1) is True


# --- per-attempt LLM call records -------------------------------------------------------


def llm_call(at: datetime, **fields) -> LlmCall:
    base = dict(
        id=uuid4(),
        call_id=uuid4(),
        request_id=None,
        user_id=ALICE,
        at=at,
        role="teach",
        endpoint="lesson",
        provider="openrouter",
        model="m",
        attempt=1,
        outcome="error",
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        ttft_ms=None,
        total_ms=120,
        cost_usd=None,
        error_kind="timeout",
        backup=False,
    )
    return LlmCall(**{**base, **fields})


def test_record_and_list_llm_calls_round_trip(usage_store):
    t = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    failed = llm_call(t)
    ok = llm_call(
        t + timedelta(seconds=1),
        request_id=uuid4(),
        attempt=2,
        outcome="ok",
        input_tokens=100,
        output_tokens=50,
        reasoning_tokens=7,
        ttft_ms=300,
        cost_usd=Decimal("0.00012345"),
        error_kind=None,
        backup=True,
    )
    usage_store.record_llm_call(ALICE, ok)
    usage_store.record_llm_call(ALICE, failed)

    got = usage_store.llm_calls(ALICE, t - timedelta(hours=1), t + timedelta(hours=1))

    assert got == [failed, ok]  # ordered by time; nullable fields stay None


def test_llm_calls_filters_by_user_and_range(usage_store):
    t = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    early, inside, late = (llm_call(t + timedelta(hours=h)) for h in (-2, 0, 2))
    theirs = llm_call(t, user_id=BOB)
    for call in (early, inside, late):
        usage_store.record_llm_call(ALICE, call)
    usage_store.record_llm_call(BOB, theirs)
    since, until = t - timedelta(hours=1), t + timedelta(hours=1)

    assert usage_store.llm_calls(ALICE, since, until) == [inside]
    assert usage_store.llm_calls(BOB, since, until) == [theirs]
    everyone = usage_store.llm_calls(ALICE, since, until, all_tenants=True)
    assert {(c.id, c.user_id) for c in everyone} == {(inside.id, ALICE), (theirs.id, BOB)}


def test_record_llm_call_refuses_a_call_owned_by_someone_else(usage_store):
    call = llm_call(datetime(2026, 10, 20, 9, 0, tzinfo=UTC), user_id=BOB)

    with pytest.raises(ValueError):
        usage_store.record_llm_call(ALICE, call)


# --- who drives a session, in either store ------------------------------------------------

LEASE = timedelta(seconds=90)


@pytest.fixture(params=["postgres", "memory"])
def session_store(request, store):
    if request.param == "postgres":
        return store
    memory = MemoryStore()
    memory.ensure_profile(ALICE)
    memory.ensure_profile(BOB)
    return memory


def open_session(store, thread_id: str = "t-1") -> UUID:
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "d")])
    return store.create_session(ALICE, subject_id, thread_id)


def later(seconds: int) -> datetime:
    return NOW + timedelta(seconds=seconds)


def test_claim_session_takes_a_free_lease_and_renews_its_own(session_store):
    open_session(session_store)

    assert session_store.session_driver(ALICE, "t-1", NOW, LEASE) is None
    assert session_store.claim_session(ALICE, "t-1", "tab-a", NOW, LEASE) is True
    assert session_store.session_driver(ALICE, "t-1", later(89), LEASE) == "tab-a"
    assert session_store.claim_session(ALICE, "t-1", "tab-b", later(89), LEASE) is False
    assert session_store.claim_session(ALICE, "t-1", "tab-a", later(60), LEASE) is True
    assert session_store.claim_session(ALICE, "t-1", "tab-b", later(149), LEASE) is False
    assert session_store.session_driver(ALICE, "t-1", later(150), LEASE) is None  # expired
    assert session_store.claim_session(ALICE, "t-1", "tab-b", later(150), LEASE) is True
    assert session_store.session_driver(ALICE, "t-1", later(150), LEASE) == "tab-b"
    assert session_store.get_session(ALICE, "t-1").last_active_at == later(150)


def test_claim_session_refuses_ended_and_other_users_sessions(session_store):
    session_id = open_session(session_store)

    assert session_store.claim_session(BOB, "t-1", "tab-a", NOW, LEASE) is False
    assert session_store.get_session(BOB, "t-1") is None
    assert session_store.get_session(ALICE, "t-1").session_id == session_id
    session_store.end_session(ALICE, session_id, NOW)
    assert session_store.claim_session(ALICE, "t-1", "tab-a", NOW, LEASE) is False
    assert session_store.get_session(ALICE, "t-1") is None
    assert session_store.session_driver(ALICE, "t-1", NOW, LEASE) is None


def test_release_session_frees_only_its_own_drivers_lease(session_store):
    open_session(session_store)
    session_store.claim_session(ALICE, "t-1", "tab-a", NOW, LEASE)

    session_store.release_session(ALICE, "t-1", "tab-b")  # not tab-b's to free
    session_store.release_session(BOB, "t-1", "tab-a")  # nor another user's
    assert session_store.session_driver(ALICE, "t-1", later(1), LEASE) == "tab-a"
    session_store.release_session(ALICE, "t-1", "tab-a")

    assert session_store.session_driver(ALICE, "t-1", later(1), LEASE) is None
    assert session_store.claim_session(ALICE, "t-1", "tab-b", later(1), LEASE) is True
    assert session_store.get_session(ALICE, "t-1") is not None  # still open


def test_touch_session_moves_the_last_activity(session_store):
    session_id = open_session(session_store)

    session_store.touch_session(ALICE, "t-1", NOW)
    session_store.touch_session(BOB, "t-1", later(60))

    found = session_store.get_session(ALICE, "t-1")
    assert (found.session_id, found.thread_id, found.last_active_at) == (session_id, "t-1", NOW)


def test_close_stale_sessions_skips_a_session_whose_lock_is_held(session_store):
    open_session(session_store)
    session_store.touch_session(ALICE, "t-1", NOW - timedelta(hours=25))

    with session_store.session_lock(ALICE, "t-1") as held:
        assert held is True
        assert session_store.close_stale_sessions(ALICE, NOW) == 0

    assert session_store.close_stale_sessions(ALICE, NOW) == 1
    assert session_store.get_session(ALICE, "t-1") is None


def test_replay_skips_voided_and_uses_changed_grades(store):
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names."), ("TCP", "Streams.")])
    dns, tcp = store.list_concepts(ALICE, subject_id)
    session_id = store.create_session(ALICE, subject_id, "thread-1")
    questions = [Question(uuid4(), c.id, "short", 1, "Why?", "Because.") for c in (dns, tcp)]
    store.save_questions(ALICE, session_id, questions)
    dns_first = answer_for(questions[0], GradeValue.INCORRECT, NOW)
    dns_second = answer_for(questions[0], GradeValue.CORRECT, NOW + timedelta(days=2))
    tcp_only = answer_for(questions[1], GradeValue.CORRECT, NOW + timedelta(days=1))
    for answer in (dns_first, tcp_only, dns_second):
        store.record_answer(ALICE, answer)
    store.resolve_dispute(
        ALICE, dispute_for(dns_second, DisputeOutcome.CHANGED, GradeValue.PARTIAL), NOW
    )
    store.resolve_dispute(ALICE, dispute_for(tcp_only, DisputeOutcome.VOIDED), NOW)

    answers = store.subject_answers(ALICE, subject_id)

    assert [(a.concept_id, a.grade) for a in answers] == [
        (dns.id, GradeValue.INCORRECT),
        (dns.id, GradeValue.PARTIAL),
    ]
    stored = store.list_concepts(ALICE, subject_id)[0]
    assert replay(stored, answers)[-1][1] == stored
    assert store.answer_times(ALICE) == [dns_first.graded_at, dns_second.graded_at]


def test_answer_history_is_scoped_to_the_user_and_subject(store):
    subject_id, _, question = subject_with_question(store)
    store.record_answer(ALICE, answer_for(question, GradeValue.CORRECT))
    other = store.create_subject(ALICE, "Maths", [("Sets", "Collections.")])

    assert len(store.subject_answers(ALICE, subject_id)) == 1
    assert store.subject_answers(ALICE, other) == []
    assert store.subject_answers(BOB, subject_id) == []
    assert store.answer_times(BOB) == []
    assert len(store.answer_times(ALICE)) == 1


def test_ping_reaches_the_database(store):
    store.ping()


# --- lessons and learn-phase exchanges ----------------------------------------------------


@pytest.fixture(params=["postgres", "memory"])
def lesson_store(request, store):
    return store if request.param == "postgres" else MemoryStore()


def a_concept(store) -> UUID:
    subject_id = store.create_subject(ALICE, "Networks", [("Frames", "Layer 2 units.")])
    return store.list_concepts(ALICE, subject_id)[0].id


def a_lesson(concept_id: UUID, **fields) -> StoredLesson:
    base = dict(
        concept_id=concept_id,
        markdown="A frame is a layer 2 unit.",
        citations=(Citation("chunk", "c1", "notes.md", 3, "frame"),),
        web_pages=({"url": "https://example.com", "title": "Frames"},),
        suggestions=("What is a packet?",),
        checks=("Why frame at all?",),
        source_summary={"chunks": 2, "web": 1},
        model="m1",
        written_at=datetime(2026, 10, 27, 9, 0, tzinfo=UTC),
        learned_at=None,
    )
    return StoredLesson(**{**base, **fields})


def an_exchange(position: int, **fields) -> Exchange:
    base = dict(
        position=position,
        kind="typed",
        question=f"Question {position}?",
        answer=f"Answer {position}.",
        citations=(Citation("web", "https://example.com", "example.com", None, "a quote"),),
        suggestions=("And then?",),
        asked_at=datetime(2026, 10, 27, 9, position, tzinfo=UTC),
    )
    return Exchange(**{**base, **fields})


def test_get_concept_is_scoped_to_the_user(lesson_store):
    concept_id = a_concept(lesson_store)

    found = lesson_store.get_concept(ALICE, concept_id)

    assert found is not None and (found.id, found.name) == (concept_id, "Frames")
    assert lesson_store.get_concept(BOB, concept_id) is None
    assert lesson_store.get_concept(ALICE, uuid4()) is None


def test_lesson_round_trips(lesson_store):
    concept_id = a_concept(lesson_store)
    lesson = a_lesson(concept_id)

    assert lesson_store.get_lesson(ALICE, concept_id) is None
    lesson_store.save_lesson(ALICE, lesson)

    assert lesson_store.get_lesson(ALICE, concept_id) == lesson
    assert lesson_store.get_lesson(BOB, concept_id) is None  # scoped to the user


def test_save_lesson_first_writer_wins(lesson_store):
    concept_id = a_concept(lesson_store)
    first = a_lesson(concept_id, markdown="first", model="m1")

    lesson_store.save_lesson(ALICE, first)
    lesson_store.save_lesson(ALICE, a_lesson(concept_id, markdown="second", model="m2"))

    assert lesson_store.get_lesson(ALICE, concept_id) == first


def test_mark_learned_once(lesson_store):
    concept_id = a_concept(lesson_store)
    first = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)

    assert lesson_store.mark_learned(ALICE, concept_id, first) is False  # no lesson yet
    assert lesson_store.is_learned(ALICE, concept_id) is False
    lesson_store.save_lesson(ALICE, a_lesson(concept_id))
    assert lesson_store.is_learned(ALICE, concept_id) is False

    assert lesson_store.mark_learned(BOB, concept_id, first) is False  # not Bob's
    assert lesson_store.mark_learned(ALICE, concept_id, first) is True
    assert lesson_store.mark_learned(ALICE, concept_id, first + timedelta(hours=1)) is False

    assert lesson_store.is_learned(ALICE, concept_id) is True
    assert lesson_store.is_learned(BOB, concept_id) is False
    assert lesson_store.get_lesson(ALICE, concept_id).learned_at == first  # the first stamp stays


def test_learned_concepts_by_subject(lesson_store):
    subject_id = lesson_store.create_subject(
        ALICE, "Networks", [("Frames", "a"), ("Packets", "b"), ("Routes", "c")]
    )
    other = lesson_store.create_subject(ALICE, "Biology", [("Cells", "d")])
    frames, packets, routes = (c.id for c in lesson_store.list_concepts(ALICE, subject_id))
    [cells] = (c.id for c in lesson_store.list_concepts(ALICE, other))
    at = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    for concept_id in (frames, packets, cells):
        lesson_store.save_lesson(ALICE, a_lesson(concept_id))
    lesson_store.mark_learned(ALICE, frames, at)
    lesson_store.mark_learned(ALICE, cells, at)

    assert lesson_store.learned_concepts(ALICE, subject_id) == {frames}  # packets: not yet
    assert lesson_store.learned_concepts(ALICE, other) == {cells}
    assert lesson_store.learned_concepts(BOB, subject_id) == set()
    assert routes not in lesson_store.learned_concepts(ALICE, subject_id)


def test_exchanges_in_order(lesson_store):
    concept_id = a_concept(lesson_store)
    lesson_store.save_lesson(ALICE, a_lesson(concept_id))
    third, first, second = an_exchange(3), an_exchange(1, kind="check"), an_exchange(2)

    for exchange in (third, first, second):
        lesson_store.add_exchange(ALICE, concept_id, exchange)

    assert lesson_store.exchanges(ALICE, concept_id) == [first, second, third]
    assert lesson_store.exchanges(BOB, concept_id) == []


def test_add_exchange_at_a_kept_position_is_ignored(lesson_store):
    concept_id = a_concept(lesson_store)
    lesson_store.save_lesson(ALICE, a_lesson(concept_id))
    kept = an_exchange(1)

    lesson_store.add_exchange(ALICE, concept_id, kept)
    lesson_store.add_exchange(ALICE, concept_id, an_exchange(1, answer="a retry"))

    assert lesson_store.exchanges(ALICE, concept_id) == [kept]


def test_lesson_deleted_with_concept(store, pg_pool):
    concept_id = a_concept(store)
    store.save_lesson(ALICE, a_lesson(concept_id))
    store.add_exchange(ALICE, concept_id, an_exchange(1))

    with pg_pool.connection() as conn:
        conn.execute("delete from concepts where id = %s", (concept_id,))

    assert store.get_lesson(ALICE, concept_id) is None
    assert store.exchanges(ALICE, concept_id) == []
