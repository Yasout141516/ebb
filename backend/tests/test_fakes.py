"""MemoryStore must keep the Store contract the Postgres tests pin down."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ebb.domain import Answer, GradeValue, Question
from ebb.llm.tutor import AllProvidersExhausted
from tests.fakes import FakeTutor, MemoryStore, make_subject, seed_concept

ALICE = UUID("00000000-0000-0000-0000-00000000000a")
BOB = UUID("00000000-0000-0000-0000-00000000000b")
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def setup() -> tuple[MemoryStore, UUID, UUID, Question]:
    store = MemoryStore()
    subject_id = make_subject(store, ALICE, concepts=1)
    concept = store.list_concepts(ALICE, subject_id)[0]
    session_id = store.create_session(ALICE, subject_id, "t")
    question = Question(uuid4(), concept.id, "short", 1, "Why cache?", "Speed.")
    store.save_questions(ALICE, session_id, [question])
    return store, subject_id, session_id, question


def answer_for(question: Question, grade: GradeValue, at: datetime = NOW) -> Answer:
    return Answer(uuid4(), question.id, question.concept_id, "x", grade, "ok", "fake", at)


def test_record_answer_grades_the_current_row_and_touches_the_session():
    store, _, session_id, question = setup()
    seed_concept(store, question.concept_id, stage=3, next_review_at=NOW - timedelta(hours=1))
    first = answer_for(question, GradeValue.INCORRECT)
    second = answer_for(question, GradeValue.CORRECT, NOW + timedelta(minutes=1))

    assert store.record_answer(ALICE, first).stage == 1
    assert store.record_answer(ALICE, second).stage == 1  # row stage 1 is not due
    assert store.stages[second.id] == (1, 1)
    assert store.sessions[session_id]["last_active_at"] == second.graded_at


def test_duplicate_answer_and_other_users():
    store, _, _, question = setup()
    answer = answer_for(question, GradeValue.CORRECT)
    first = store.record_answer(ALICE, answer)
    assert first.stage == 1
    assert store.record_answer(ALICE, answer) == first
    assert store.concept(question.concept_id).review_count == 1
    with pytest.raises(LookupError):
        store.record_answer(BOB, answer_for(question, GradeValue.CORRECT))


def test_answered_prompts_only_answered_newest_first():
    store, _, session_id, first = setup()
    second = Question(uuid4(), first.concept_id, "short", 1, "Why TTL?", "Expiry.")
    unanswered = Question(uuid4(), first.concept_id, "short", 1, "Never asked", "-")
    store.save_questions(ALICE, session_id, [second, unanswered])
    store.record_answer(ALICE, answer_for(first, GradeValue.CORRECT))
    store.record_answer(ALICE, answer_for(second, GradeValue.CORRECT, NOW + timedelta(minutes=1)))

    assert store.answered_prompts(ALICE, first.concept_id) == ["Why TTL?", "Why cache?"]
    assert store.answered_prompts(ALICE, first.concept_id, limit=1) == ["Why TTL?"]
    assert store.answered_prompts(BOB, first.concept_id) == []


def test_add_concepts_drops_duplicates_and_appends():
    store, subject_id, _, _ = setup()  # one concept: "Concept 1"
    added = store.add_concepts(
        ALICE, subject_id, [("concept 1 ", "dup"), ("UDP", "u"), ("udp", "dup"), ("QUIC", "q")]
    )
    assert added == 2
    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == [
        "Concept 1",
        "UDP",
        "QUIC",
    ]
    with pytest.raises(LookupError):
        store.add_concepts(BOB, subject_id, [("Sneaky", "x")])


def test_counts_subject_and_timezone():
    store, subject_id, _, question = setup()
    store.add_concepts(ALICE, subject_id, [("UDP", "u")])
    seed_concept(store, question.concept_id, stage=2, next_review_at=NOW - timedelta(days=1))
    assert store.count_due(ALICE, subject_id, NOW) == 1
    assert store.count_new(ALICE, subject_id) == 1
    assert store.next_review_at(ALICE, subject_id) == NOW - timedelta(days=1)
    assert store.count_due(BOB, subject_id, NOW) == 0
    assert store.get_subject(ALICE, subject_id).name == "Networks"
    assert store.get_subject(BOB, subject_id) is None
    store.set_timezone(ALICE, "Europe/Berlin")
    assert store.timezones[ALICE] == "Europe/Berlin"


def test_resumable_window_and_subject():
    store, subject_id, session_id, _ = setup()
    other = make_subject(store, ALICE, concepts=1)
    store.sessions[session_id]["last_active_at"] = NOW - timedelta(hours=23, minutes=59)
    assert store.resumable_session(ALICE, subject_id, NOW).session_id == session_id
    assert store.resumable_session(ALICE, other, NOW) is None
    assert store.resumable_session(BOB, subject_id, NOW) is None
    store.sessions[session_id]["last_active_at"] = NOW - timedelta(hours=24)
    assert store.resumable_session(ALICE, subject_id, NOW) is None


def test_close_stale_and_prune_bookkeeping():
    store, _, session_id, _ = setup()
    stale_at = NOW - timedelta(days=8)
    store.sessions[session_id]["last_active_at"] = stale_at
    assert store.close_stale_sessions(ALICE, NOW) == 1
    assert store.sessions[session_id]["ended_at"] == stale_at
    assert store.threads_to_prune(ALICE, NOW) == ["t"]
    store.mark_pruned(ALICE, ["t"], NOW)
    assert store.threads_to_prune(ALICE, NOW) == []


def test_session_lock_refuses_a_second_holder():
    store = MemoryStore()
    with store.session_lock(ALICE, "t-1") as first:
        with store.session_lock(ALICE, "t-1") as second:
            assert first is True and second is False
    with store.session_lock(ALICE, "t-1") as again:
        assert again is True


def test_fake_tutor_extensions_run_out_cleanly():
    store = MemoryStore()
    subject_id = make_subject(store, ALICE, concepts=2)
    existing = store.list_concepts(ALICE, subject_id)
    tutor = FakeTutor(extensions=[["A", "B"], ["C"]])

    names = [[d.name for d in tutor.extend_concepts("Networks", existing).value] for _ in range(3)]

    assert names == [["A", "B"], ["C"], []]
    assert tutor.extend_calls == [("Networks", ["Concept 1", "Concept 2"])] * 3
    assert FakeTutor().extend_concepts("Networks", existing).value == []


def test_fake_tutor_can_fail_to_extend():
    tutor = FakeTutor(fail_extend=True)
    with pytest.raises(AllProvidersExhausted):
        tutor.extend_concepts("Networks", [])
    assert len(tutor.extend_calls) == 1
