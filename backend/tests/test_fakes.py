"""MemoryStore must keep the Store contract the Postgres tests pin down."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ebb.domain import (
    Answer,
    ChunkDraft,
    Citation,
    Claim,
    Dispute,
    DisputeOutcome,
    DisputeReason,
    GradeValue,
    NewDocument,
    Question,
)
from ebb.llm.tutor import AllProvidersExhausted
from ebb.store import DocumentRefused, merge_note_concepts
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


def doc(name: str, *contents: str, sha: str | None = None) -> NewDocument:
    chunks = tuple(ChunkDraft(i, text, f"Part {i}", 1, 1, (0,)) for i, text in enumerate(contents))
    return NewDocument(name, "txt", 100, None, sha or f"sha-{name}", chunks)


SUBNET = "Subnet masks split a network into subnets; the broadcast address closes each subnet."
ARP = "ARP maps IP addresses to MAC addresses on a local network."
TCP = "The TCP three-way handshake opens a connection with SYN, SYN-ACK and ACK."


def test_memory_store_add_document_saves_chunks_and_new_concepts():
    store = MemoryStore()
    subject_id = store.create_subject(ALICE, "Networks", [("DNS", "Names.")])

    added = store.add_document(
        ALICE, subject_id, doc("a.txt", "one", "two"), [("DNS", "dup"), ("ARP", "Maps.")]
    )

    assert added == 1
    [saved] = store.list_documents(ALICE, subject_id)
    assert (saved.filename, saved.chunk_count) == ("a.txt", 2)
    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "ARP"]
    with pytest.raises(LookupError):
        store.add_document(BOB, subject_id, doc("b.txt", "x"), [])


def test_memory_store_refuses_a_sixth_and_a_repeated_document():
    store = MemoryStore()
    subject_id = store.create_subject(ALICE, "Networks", [])
    for n in range(5):
        store.add_document(ALICE, subject_id, doc(f"{n}.txt", "x"), [])

    with pytest.raises(DocumentRefused) as full:
        store.add_document(ALICE, subject_id, doc("6.txt", "x"), [])
    with pytest.raises(DocumentRefused) as same:
        store.add_document(ALICE, subject_id, doc("7.txt", "x", sha="sha-0.txt"), [])

    assert (full.value.reason, same.value.reason) == ("full", "duplicate")
    assert store.has_document(ALICE, subject_id, "sha-0.txt")
    assert not store.has_document(BOB, subject_id, "sha-0.txt")


def test_memory_store_create_subject_with_notes():
    store = MemoryStore()

    subject_id = store.create_subject_with_notes(
        ALICE, "Networks", [doc("a.txt", "one")], [("DNS", "N."), ("dns", "dup"), ("ARP", "M.")]
    )

    assert [c.name for c in store.list_concepts(ALICE, subject_id)] == ["DNS", "ARP"]
    assert [d.filename for d in store.list_documents(ALICE, subject_id)] == ["a.txt"]


def test_memory_store_search_needs_two_shared_words_and_is_scoped():
    store = MemoryStore()
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, doc("net.txt", SUBNET, ARP, TCP), [])

    found = store.search_chunks(ALICE, subject_id, "Subnetting: a network using subnet masks")

    assert [c.content for c in found] == [SUBNET]
    assert found[0].filename == "net.txt"
    assert store.search_chunks(ALICE, subject_id, "Quantum entanglement") == []
    assert store.search_chunks(BOB, subject_id, "subnet masks network") == []
    assert store.list_documents(BOB, subject_id) == []


def test_merge_note_concepts():
    matched, fresh = merge_note_concepts(
        ["Subnetting", "DNS"],
        [
            ("Subnet masks", "Masks.", "subnetting "),
            ("Ghost", "Links nowhere.", "Nonexistent"),
            ("dns", "Already there.", None),
            ("ARP", "Maps.", None),
            ("arp", "Dup.", None),
        ],
    )

    assert matched == ["Subnetting"]
    assert fresh == [("ARP", "Maps.")]


def test_memory_store_count_documents():
    store = MemoryStore()
    subject_id = store.create_subject(ALICE, "Networks", [])
    store.add_document(ALICE, subject_id, doc("a.txt", "x"), [])
    store.add_document(ALICE, subject_id, doc("b.txt", "y"), [])

    assert store.count_documents(ALICE, subject_id) == 2
    assert store.count_documents(BOB, subject_id) == 0


CITE = Citation("chunk", "3f0c2a5e-0000-0000-0000-000000000001", "N1", 3, "masks split a network")


def dispute_for(
    answer: Answer, outcome: DisputeOutcome, new_grade: GradeValue | None = None
) -> Dispute:
    return Dispute(
        uuid4(),
        answer.id,
        DisputeReason.CORRECT,
        "I said the same thing.",
        answer.grade,
        new_grade,
        outcome,
        "Re-checked.",
        (CITE,),
        "strict-model",
    )


def stage_3_then_incorrect():
    """A due stage-3 concept answered incorrectly at NOW, with the concept before it."""
    store, _, _, question = setup()
    seed_concept(
        store,
        question.concept_id,
        stage=3,
        next_review_at=NOW - timedelta(hours=1),
        last_reviewed_at=NOW - timedelta(days=7),
        review_count=1,
    )
    before = store.concept(question.concept_id)
    answer = answer_for(question, GradeValue.INCORRECT)
    assert store.record_answer(ALICE, answer).stage == 1
    return store, question, before, answer


def test_memory_store_keeps_citations_but_not_claims():
    store, _, session_id, _ = setup()
    concept_id = next(iter(store.concepts))
    question = Question(
        uuid4(),
        concept_id,
        "short",
        1,
        "Why split?",
        "-",
        source="notes",
        claims=(Claim("N1", "q"),),
        citations=(CITE,),
    )
    store.save_questions(ALICE, session_id, [question])
    answer = replace(answer_for(question, GradeValue.CORRECT), citations=(CITE,))
    store.record_answer(ALICE, answer)

    saved = store.questions[question.id]
    assert (saved.source, saved.citations, saved.claims) == ("notes", (CITE,), ())
    assert store.attempts[answer.id].citations == (CITE,)


def test_memory_store_saving_a_question_twice_is_a_no_op():
    store, _, session_id, question = setup()

    store.save_questions(ALICE, session_id, [question])  # a replay after a crash
    store.save_questions(ALICE, session_id, [replace(question, source="notes", citations=(CITE,))])

    assert store.questions[question.id] == question
    assert len(store.questions) == 1


def test_memory_store_changed_dispute_recomputes_from_the_snapshot():
    store, question, before, answer = stage_3_then_incorrect()
    dispute = dispute_for(answer, DisputeOutcome.CHANGED, GradeValue.CORRECT)

    stored, concept = store.resolve_dispute(ALICE, dispute, NOW + timedelta(minutes=5))

    assert stored == dispute and concept == store.concept(question.concept_id)
    assert concept.stage == 4 and concept.next_review_at == NOW + timedelta(days=16)
    assert concept.review_count == before.review_count + 1
    assert store.attempts[answer.id].grade is GradeValue.CORRECT
    assert store.stages[answer.id] == (3, 4)


def test_memory_store_voided_dispute_restores_the_concept_and_keeps_the_prompt():
    store, question, before, answer = stage_3_then_incorrect()

    _, concept = store.resolve_dispute(ALICE, dispute_for(answer, DisputeOutcome.VOIDED), NOW)

    assert concept == before == store.concept(question.concept_id)
    assert store.voided[answer.id] == NOW
    assert store.answered_prompts(ALICE, question.concept_id) == [question.prompt]


def test_memory_store_upheld_dispute_and_idempotent_rerun():
    store, question, _, answer = stage_3_then_incorrect()
    graded = store.concept(question.concept_id)
    dispute = dispute_for(answer, DisputeOutcome.UPHELD, GradeValue.INCORRECT)

    assert store.resolve_dispute(ALICE, dispute, NOW) == (dispute, graded)
    rerun = replace(dispute, outcome=DisputeOutcome.VOIDED, new_grade=None)
    assert store.resolve_dispute(ALICE, rerun, NOW) == (dispute, graded)
    assert store.dispute_counts(ALICE) == {"raised": 1, "changed": 0, "voided": 0, "upheld": 1}
    assert store.dispute_counts(BOB)["raised"] == 0


def test_memory_store_refuses_disputes_it_cannot_apply():
    store, question, _, first = stage_3_then_incorrect()
    second = answer_for(question, GradeValue.CORRECT, NOW + timedelta(days=2))
    store.record_answer(ALICE, second)

    with pytest.raises(LookupError):
        store.resolve_dispute(BOB, dispute_for(second, DisputeOutcome.VOIDED), NOW)
    with pytest.raises(ValueError):  # not the latest
        store.resolve_dispute(ALICE, dispute_for(first, DisputeOutcome.VOIDED), NOW)
    with pytest.raises(ValueError):  # changed needs a new grade
        store.resolve_dispute(ALICE, dispute_for(second, DisputeOutcome.CHANGED), NOW)
    store.resolve_dispute(ALICE, dispute_for(second, DisputeOutcome.VOIDED), NOW)
    with pytest.raises(ValueError):  # already disputed
        store.resolve_dispute(ALICE, dispute_for(second, DisputeOutcome.UPHELD), NOW)
    del store.snapshots[first.id]  # as if recorded before migration 003
    with pytest.raises(ValueError):
        store.resolve_dispute(ALICE, dispute_for(first, DisputeOutcome.VOIDED), NOW)
    assert store.dispute_counts(ALICE)["raised"] == 1
