"""Whole quiz sessions through interrupt/resume, offline (FakeTutor + MemoryStore)."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb.citations import MAX_RETRIES
from ebb.citations import to_dict as citation_to_dict
from ebb.domain import (
    Answer,
    Chunk,
    ChunkDraft,
    Citation,
    Claim,
    Concept,
    Grade,
    GradeValue,
    NewDocument,
    Option,
    Question,
)
from ebb.graph.build import build_graph
from ebb.graph.nodes import EXHAUSTED_NOTICE, FALLBACK_NOTICE, WEB_NOTICE
from ebb.graph.state import (
    concept_to_dict,
    grade_from_dict,
    grade_to_dict,
    question_from_dict,
    question_to_dict,
)
from ebb.llm.tutor import AllProvidersExhausted
from ebb.web import WebPage
from tests.fakes import FakeTutor, FakeWebSearch, MemoryStore, make_subject, seed_concept

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
USER = UUID("00000000-0000-0000-0000-0000000000aa")


def run(
    tutor: FakeTutor,
    store: MemoryStore,
    subject_id: UUID,
    answer: Callable[[dict[str, Any]], dict[str, Any]],
    web: FakeWebSearch | None = None,
    now: Callable[[], datetime] = lambda: NOW,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    graph = build_graph(tutor, store, InMemorySaver(), now=now, web=web)
    config = {"configurable": {"thread_id": str(uuid4())}}
    start = {"user_id": str(USER), "subject_id": str(subject_id), "thread_id": "t-1"}
    result = graph.invoke(start, config)
    payloads = []
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        payloads.append(payload)
        assert len(payloads) < 50, "session did not end"
        result = graph.invoke(Command(resume=answer(payload)), config)
    return result, payloads


def reply(text: str) -> dict[str, Any]:
    return {"answer": text, "attempt_id": str(uuid4())}


def always_right(payload: dict[str, Any]) -> dict[str, Any]:
    return reply("1" if payload["question"]["options"] else "right")


def always_wrong(payload: dict[str, Any]) -> dict[str, Any]:
    return reply("2" if payload["question"]["options"] else "no idea")


def test_all_correct_session_learns_every_concept_two_questions_each():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=4)

    final, payloads = run(tutor, store, subject_id, always_right)

    assert len(payloads) == 8
    assert [p["concept"] for p in payloads] == [f"Concept {n}" for n in (1, 1, 2, 2, 3, 3, 4, 4)]
    assert [p["question"]["difficulty"] for p in payloads[:2]] == [1, 2]  # easiest, then harder
    assert [r["concept"] for r in final["results"]] == [f"Concept {n}" for n in range(1, 5)]
    assert all(r["tally"] == {"correct": 2, "partial": 0, "incorrect": 0} for r in final["results"])
    # first answer moves a new concept to stage 1; the second is early, so no promotion
    assert [r["stage_after"] for r in final["results"]] == [1, 1, 1, 1]
    assert len(store.attempts) == 8
    assert final["caught_up"] is True  # nothing left, and the default tutor extends with nothing
    session = next(iter(store.sessions.values()))
    assert session["ended_at"] == NOW
    assert final["grade"]["value"] == "correct"  # feedback for the last answer is shown


def test_session_runs_past_three_concepts_until_quit():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=5)
    seen = []

    def answer(payload):
        seen.append(payload)
        return {"quit": True} if len(seen) == 9 else always_right(payload)

    final, payloads = run(tutor, store, subject_id, answer)

    assert len(payloads) == 9
    assert payloads[8]["concept"] == "Concept 5"
    assert len(final["results"]) == 4


def test_due_review_comes_first():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=4)
    concept = store.list_concepts(USER, subject_id)[2]
    seed_concept(
        store,
        concept.id,
        stage=2,
        last_reviewed_at=NOW - timedelta(days=4),
        next_review_at=NOW - timedelta(days=1),
    )

    _, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert payloads[0]["concept"] == "Concept 3"


def test_learning_everything_extends_the_subject():
    store = MemoryStore()
    tutor = FakeTutor(extensions=[["Advanced 1", "Advanced 2", "Advanced 3"]])
    subject_id = make_subject(store, USER, concepts=1)
    seen = []

    def answer(payload):
        seen.append(payload)
        return {"quit": True} if len(seen) == 3 else always_right(payload)

    _, payloads = run(tutor, store, subject_id, answer)

    assert [p["concept"] for p in payloads] == ["Concept 1", "Concept 1", "Advanced 1"]
    assert tutor.extend_calls == [("Networks", ["Concept 1"])]
    assert "Added 3 more advanced concepts." not in payloads[1]["notices"]
    assert payloads[2]["notices"] == ["Added 3 more advanced concepts."]


def test_one_added_concept_is_announced_in_the_singular():
    # "Concept 1" already exists, so only one of the two names is new.
    store, tutor = MemoryStore(), FakeTutor(extensions=[["Concept 1", "Advanced 1"]])
    subject_id = make_subject(store, USER, concepts=1)
    seen = []

    def answer(payload):
        seen.append(payload)
        return {"quit": True} if len(seen) == 3 else always_right(payload)

    _, payloads = run(tutor, store, subject_id, answer)

    assert payloads[2]["concept"] == "Advanced 1"
    assert payloads[2]["notices"] == ["Added 1 more advanced concept."]


def test_extension_with_nothing_new_ends_caught_up():
    store, tutor = MemoryStore(), FakeTutor(extensions=[["Concept 1"]])
    subject_id = make_subject(store, USER, concepts=1)

    final, payloads = run(tutor, store, subject_id, always_right)

    assert len(payloads) == 2
    assert final["caught_up"] is True
    assert final["fatal"] is None
    assert len(tutor.extend_calls) == 1


def test_extension_failure_ends_with_the_exhausted_notice():
    store, tutor = MemoryStore(), FakeTutor(fail_extend=True)
    subject_id = make_subject(store, USER, concepts=1)

    final, payloads = run(tutor, store, subject_id, always_right)

    assert len(payloads) == 2
    assert final["fatal"] == EXHAUSTED_NOTICE
    assert EXHAUSTED_NOTICE in final["notices"]
    assert next(iter(store.sessions.values()))["ended_at"] == NOW


def test_avoid_list_includes_earlier_sessions():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("right"), {"quit": True}])
    _, first = run(tutor, store, subject_id, lambda p: next(answers))
    concept = store.list_concepts(USER, subject_id)[0]
    seed_concept(store, concept.id, stage=1, next_review_at=NOW - timedelta(days=1))

    run(tutor, store, subject_id, lambda p: {"quit": True})

    assert tutor.avoid_calls[0] == []
    assert first[0]["question"]["prompt"] in tutor.avoid_calls[1]


def test_wrong_answer_is_taught_then_asked_again_at_same_difficulty():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("no idea"), {"quit": True}])

    final, payloads = run(tutor, store, subject_id, lambda p: next(answers))

    assert payloads[1]["feedback"]["value"] == "incorrect"
    assert payloads[1]["lesson"] == "Here is the idea, with an example."
    assert payloads[1]["question"]["difficulty"] == payloads[0]["question"]["difficulty"]
    assert final["results"][0]["tally"]["incorrect"] == 1


def test_quitting_at_the_first_question_records_nothing():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER)

    final, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert len(payloads) == 1
    assert final["results"] == []
    assert store.attempts == {}
    assert next(iter(store.sessions.values()))["ended_at"] == NOW


def test_regeneration_is_capped_at_two_batches_per_concept():
    store, tutor = MemoryStore(), FakeTutor(batch_size=1)
    subject_id = make_subject(store, USER, concepts=2)

    final, payloads = run(tutor, store, subject_id, always_wrong)

    assert tutor.generate_calls == ["Concept 1", "Concept 1", "Concept 2", "Concept 2"]
    assert [p["concept"] for p in payloads] == ["Concept 1", "Concept 1", "Concept 2", "Concept 2"]
    assert len(final["results"]) == 2


def test_failed_grading_asks_the_same_question_again():
    store, tutor = MemoryStore(), FakeTutor(fail_grade_times=1)
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("right"), reply("right"), {"quit": True}])

    final, payloads = run(tutor, store, subject_id, lambda p: next(answers))

    assert payloads[0]["question"]["type"] == "short"
    assert payloads[1]["question"]["prompt"] == payloads[0]["question"]["prompt"]
    assert EXHAUSTED_NOTICE in payloads[1]["notices"]
    assert payloads[1]["feedback"] is None
    assert payloads[2]["feedback"]["value"] == "correct"
    assert len(store.attempts) == 1


def test_failed_generation_ends_the_session_with_a_notice():
    store, tutor = MemoryStore(), FakeTutor(fail_generate=True)
    subject_id = make_subject(store, USER)

    final, payloads = run(tutor, store, subject_id, always_right)

    assert payloads == []
    assert final["fatal"] == EXHAUSTED_NOTICE
    assert EXHAUSTED_NOTICE in final["notices"]
    assert next(iter(store.sessions.values()))["ended_at"] == NOW


def test_failed_teaching_continues_without_a_lesson():
    store, tutor = MemoryStore(), FakeTutor(fail_teach=True)
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("no idea"), {"quit": True}])

    final, payloads = run(tutor, store, subject_id, lambda p: next(answers))

    assert payloads[1]["lesson"] is None
    assert EXHAUSTED_NOTICE in payloads[1]["notices"]


def test_backup_model_notice_is_shown_once():
    store, tutor = MemoryStore(), FakeTutor(fell_back=True)
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("right"), {"quit": True}])

    final, payloads = run(tutor, store, subject_id, lambda p: next(answers))

    assert payloads[0]["notices"] == [FALLBACK_NOTICE]
    assert payloads[1]["notices"] == [FALLBACK_NOTICE]  # from grading the first answer
    assert final["notices"] == []


def test_subject_without_concepts_ends_immediately():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=0)

    final, payloads = run(tutor, store, subject_id, always_right)

    assert payloads == []
    assert final["results"] == []


def test_mcq_attempts_are_graded_in_code():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=1)

    run(tutor, store, subject_id, always_right)

    models = sorted(a.model_used for a in store.attempts.values())
    assert models == ["code", "fake-model"]  # short answer by the LLM, MCQ in code


class AlreadyRecordedStore(MemoryStore):
    """Simulates a resume after a crash between the answer's commit and its checkpoint:
    the answer was committed once before, so the re-run is a duplicate."""

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept:
        super().record_answer(user_id, answer)  # the commit that happened before the crash
        return super().record_answer(user_id, answer)  # the re-run after resuming


def test_duplicate_answer_still_counts_in_the_session():
    store, tutor = AlreadyRecordedStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("right"), {"quit": True}])

    final, payloads = run(tutor, store, subject_id, lambda p: next(answers))

    assert payloads[1]["number"] == 2
    assert payloads[1]["feedback"]["value"] == "correct"  # the stored answer's feedback
    assert len(store.attempts) == 1
    assert store.list_concepts(USER, subject_id)[0].stage == 1
    [result] = final["results"]
    assert result["tally"] == {"correct": 1, "partial": 0, "incorrect": 0}
    assert (result["stage_before"], result["stage_after"]) == (0, 1)


def test_summary_reports_tide_and_next_review_for_a_new_concept():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=4)

    final, _ = run(tutor, store, subject_id, always_right)

    first = final["results"][0]
    assert first["tide_before"] is None  # never reviewed
    assert first["tide_after"] == pytest.approx(1.0)  # just reviewed
    assert first["next_review_at"] == (NOW + timedelta(days=1)).isoformat()


def test_summary_reports_tide_and_promotion_for_a_due_concept():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=1)
    concept = store.list_concepts(USER, subject_id)[0]
    seed_concept(
        store,
        concept.id,
        stage=2,
        last_reviewed_at=NOW - timedelta(days=4),
        next_review_at=NOW - timedelta(days=1),
    )

    final, _ = run(tutor, store, subject_id, always_right)

    result = final["results"][0]
    assert result["stage_before"] == 2
    assert result["stage_after"] == 3  # the first correct answer, given while due, promotes
    assert result["tide_before"] == pytest.approx(0.7 ** (4 / 3))
    assert result["tide_after"] == pytest.approx(1.0)
    assert result["next_review_at"] == (NOW + timedelta(days=7)).isoformat()


def test_concept_answered_before_a_failed_regeneration_is_still_summarised():
    store, tutor = MemoryStore(), FakeTutor(batch_size=1, fail_generate_on_call=2)
    subject_id = make_subject(store, USER, concepts=1)

    final, _ = run(tutor, store, subject_id, lambda p: reply("no idea"))

    assert final["fatal"] == EXHAUSTED_NOTICE
    assert len(final["results"]) == 1
    assert final["results"][0]["tally"]["incorrect"] == 1
    assert next(iter(store.sessions.values()))["ended_at"] == NOW


# --- notes and citations ---------------------------------------------------------------

NOTE = (
    "A subnet mask splits an address into its network and host parts. "
    "The first address identifies the network and the last is the broadcast address, "
    "so neither can be assigned to a host."
)
QUOTE = "The first address identifies the network"
OTHER_QUOTE = "neither can be assigned to a host"
COVERED = ("Subnet masks", "How a subnet mask splits an address into network and host parts.")
UNCOVERED = ("Routing tables", "Routers pick next hops.")


def notes_subject(store: MemoryStore, *concepts: tuple[str, str]) -> UUID:
    store.ensure_profile(USER)
    document = NewDocument(
        "CSE321.pdf", "pdf", 1000, 20, "sha-pdf", (ChunkDraft(0, NOTE, None, 12, 12, (0,)),)
    )
    return store.create_subject_with_notes(USER, "Networks", [document], concepts or [COVERED])


def stored(store: MemoryStore, prompt: str) -> Question:
    return next(q for q in store.questions.values() if q.prompt == prompt)


def quit_after(
    count: int, answer: Callable[[dict[str, Any]], dict[str, Any]]
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    seen = []

    def reply_to(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return {"quit": True} if len(seen) > count else answer(payload)

    return reply_to


def test_question_and_grade_dicts_round_trip_through_json():
    citation = Citation("chunk", str(uuid4()), "CSE321.pdf · p. 12", 12, QUOTE)
    question = Question(
        uuid4(),
        uuid4(),
        "mcq",
        2,
        "Why can't the first address be used?",
        "It names the network.",
        (Option("It names the network", True, "Yes."),),
        "notes",
        (Claim("N1", QUOTE),),
        (citation,),
    )
    grade = Grade(GradeValue.PARTIAL, "Half.", "Mixed up.", (Claim("N1", QUOTE),), (citation,))

    question_data = json.loads(json.dumps(question_to_dict(question)))
    grade_data = json.loads(json.dumps(grade_to_dict(grade, general_knowledge=True)))

    assert question_from_dict(question_data) == question
    assert grade_from_dict(grade_data) == grade
    assert grade_data["general_knowledge"] is True


def test_covered_concept_gets_cited_questions():
    store, tutor = MemoryStore(), FakeTutor(cite=[[("N1", QUOTE)]])
    subject_id = notes_subject(store)

    final, payloads = run(tutor, store, subject_id, quit_after(2, always_wrong))

    assert len(tutor.generate_calls) == 1
    assert [s.label for s in tutor.sources_calls[0]] == ["N1"]
    asked = payloads[0]["question"]
    assert asked["source"] == "notes"
    assert asked["source_line"] == "From your notes · p. 12"
    question = stored(store, asked["prompt"])
    assert question.source == "notes"
    assert [(c.label, c.page, c.quote) for c in question.citations] == [
        ("CSE321.pdf · p. 12", 12, QUOTE)
    ]
    # the MCQ that follows is graded in code and shows its question's citations
    assert payloads[1]["question"]["type"] == "mcq"
    mcq = stored(store, payloads[1]["question"]["prompt"])
    assert mcq.citations
    assert payloads[2]["feedback"]["citations"] == [citation_to_dict(c) for c in mcq.citations]
    assert payloads[2]["feedback"]["general_knowledge"] is False
    assert all(q["claims"] == [] for q in final["batch"])  # claims don't outlive verification


def test_heading_source_line_for_notes_without_pages():
    store, tutor = MemoryStore(), FakeTutor(cite=[[("N1", QUOTE)]])
    store.ensure_profile(USER)
    chunk = ChunkDraft(0, NOTE, "Subnet masks", None, None, ())
    document = NewDocument("networks.md", "md", 300, None, "sha-md", (chunk,))
    subject_id = store.create_subject_with_notes(USER, "Networks", [document], [COVERED])

    _, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert payloads[0]["question"]["source_line"] == "From your notes · § Subnet masks"


def test_bad_citation_retries_then_passes():
    store, tutor = MemoryStore(), FakeTutor(cite=[[("N9", "x")], [("N1", QUOTE)]])
    subject_id = notes_subject(store)

    final, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert len(tutor.generate_calls) == 2
    assert tutor.failures_calls[0] == []
    assert tutor.failures_calls[1][0] == "question 1: N9 is not one of the sources you were given"
    assert len(tutor.failures_calls[1]) == 5  # one per question in the batch
    assert payloads[0]["question"]["source"] == "notes"
    assert len(store.questions) == 5  # only the verified batch is saved
    assert final["generation_calls_on_concept"] == 1


def test_three_bad_citations_fall_back_to_general():
    store, tutor = MemoryStore(), FakeTutor(cite=[[("N9", "x")]] * 3)
    subject_id = notes_subject(store)

    final, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert len(tutor.generate_calls) == 1 + MAX_RETRIES
    assert payloads[0]["question"]["source"] == "general"
    assert payloads[0]["question"]["source_line"] is None
    assert all(q.source == "general" and q.citations == () for q in store.questions.values())
    assert final["generation_calls_on_concept"] == 1


def test_uncited_notes_batch_is_retried():
    store, tutor = MemoryStore(), FakeTutor(cite=[[], [("N1", QUOTE)]])
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert len(tutor.generate_calls) == 2
    assert "question 1 cites nothing" in tutor.failures_calls[1]
    assert payloads[0]["question"]["source"] == "notes"


def test_failed_citation_retry_keeps_the_batch_as_general():
    store = MemoryStore()
    tutor = FakeTutor(cite=[[("N9", "x")]], fail_generate_on_call=2)
    subject_id = notes_subject(store)

    final, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert tutor.generate_attempts == 2
    assert final["fatal"] is None
    assert EXHAUSTED_NOTICE not in payloads[0]["notices"]
    assert payloads[0]["question"]["source"] == "general"
    assert final["generation_calls_on_concept"] == 1


def test_uncovered_concept_is_general_without_retry():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = notes_subject(store, UNCOVERED)

    _, payloads = run(tutor, store, subject_id, quit_after(1, always_wrong))

    assert tutor.generate_attempts == 1
    assert tutor.sources_calls == [[]]
    assert payloads[0]["question"]["source_line"] is None
    assert payloads[1]["feedback"]["citations"] == []
    assert payloads[1]["feedback"]["general_knowledge"] is True
    assert payloads[1]["lesson_citations"] == []


def test_topic_only_subject_has_no_label():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=2)

    _, payloads = run(tutor, store, subject_id, always_wrong)

    assert all(sources == [] for sources in tutor.sources_calls)
    assert all(p["question"]["source"] == "general" for p in payloads)
    assert all(p["question"]["source_line"] is None for p in payloads)
    feedback = [p["feedback"] for p in payloads if p["feedback"]]
    assert feedback
    assert all(f["general_knowledge"] is False and f["citations"] == [] for f in feedback)
    assert all(p["lesson_citations"] == [] for p in payloads)


def test_grade_without_citations_uses_the_question_citations():
    store, tutor = MemoryStore(), FakeTutor(cite=[[("N1", QUOTE)]])
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, quit_after(1, lambda p: reply("meh")))

    question = stored(store, payloads[0]["question"]["prompt"])
    assert tutor.grade_attempts == 1
    feedback = payloads[1]["feedback"]
    assert feedback["citations"] == [citation_to_dict(c) for c in question.citations]
    assert feedback["general_knowledge"] is False
    [attempt] = store.attempts.values()
    assert attempt.citations == question.citations


def test_grade_with_invalid_citation_retries():
    store = MemoryStore()
    tutor = FakeTutor(cite=[[("N1", QUOTE)]], grade_cites=[[("N9", "x")], [("N1", OTHER_QUOTE)]])
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, quit_after(1, lambda p: reply("meh")))

    assert tutor.grade_attempts == 2
    assert payloads[1]["feedback"]["value"] == "partial"
    assert [c["quote"] for c in payloads[1]["feedback"]["citations"]] == [OTHER_QUOTE]


def test_grade_citation_retries_stop_after_two():
    store = MemoryStore()
    tutor = FakeTutor(cite=[[("N1", QUOTE)]], grade_cites=[[("N9", "x")]] * 4)
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, quit_after(1, lambda p: reply("meh")))

    assert tutor.grade_attempts == 1 + MAX_RETRIES
    assert [c["quote"] for c in payloads[1]["feedback"]["citations"]] == [QUOTE]  # the question's


def test_failed_grade_retry_keeps_the_first_grade():
    class FailingRetry(FakeTutor):
        def grade(self, question, answer, sources=(), failures=()):
            if failures:
                raise AllProvidersExhausted("busy")
            return super().grade(question, answer, sources, failures)

    store = MemoryStore()
    tutor = FailingRetry(cite=[[("N1", QUOTE)]], grade_cites=[[("N9", "x")]])
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, quit_after(1, lambda p: reply("meh")))

    assert payloads[1]["feedback"]["value"] == "partial"
    assert [c["quote"] for c in payloads[1]["feedback"]["citations"]] == [QUOTE]
    assert EXHAUSTED_NOTICE not in payloads[1]["notices"]
    assert len(store.attempts) == 1


def test_lesson_citations_are_in_the_payload():
    store = MemoryStore()
    tutor = FakeTutor(cite=[[("N1", QUOTE)]], teach_cites=[[("N1", OTHER_QUOTE)]])
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, quit_after(1, always_wrong))

    assert payloads[1]["lesson"] == "Here is the idea, with an example."
    [citation] = payloads[1]["lesson_citations"]
    assert (citation["label"], citation["quote"]) == ("CSE321.pdf · p. 12", OTHER_QUOTE)
    assert payloads[1]["feedback"]["general_knowledge"] is False


def test_lesson_without_valid_citations_shows_none_of_its_own():
    store = MemoryStore()
    tutor = FakeTutor(cite=[[("N1", QUOTE)]], teach_cites=[[("N9", "x")]] * 3)
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, quit_after(1, always_wrong))

    assert tutor.teach_attempts == 1 + MAX_RETRIES
    assert payloads[1]["lesson"] == "Here is the idea, with an example."
    assert payloads[1]["lesson_citations"] == []
    assert [c["quote"] for c in payloads[1]["feedback"]["citations"]] == [QUOTE]  # the question's


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.searches = 0

    def search_chunks(
        self, user_id: UUID, subject_id: UUID, text: str, limit: int = 4
    ) -> list[Chunk]:
        self.searches += 1
        return super().search_chunks(user_id, subject_id, text, limit)


def test_resume_keeps_the_retrieved_sources():
    store = CountingStore()
    tutor = FakeTutor(cite=[[("N1", QUOTE)]], grade_cites=[[("N1", OTHER_QUOTE)]])
    subject_id = notes_subject(store)
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "resumed"}}
    start = {"user_id": str(USER), "subject_id": str(subject_id), "thread_id": "resumed"}
    first = build_graph(tutor, store, saver, now=lambda: NOW).invoke(start, config)
    assert first["__interrupt__"][0].value["question"]["type"] == "short"

    graph = build_graph(tutor, store, saver, now=lambda: NOW)  # as after a restart
    result = graph.invoke(Command(resume=reply("meh")), config)

    assert store.searches == 1
    feedback = result["__interrupt__"][0].value["feedback"]
    assert [c["quote"] for c in feedback["citations"]] == [OTHER_QUOTE]  # graded against N1


def test_milestone_2_checkpoint_still_resumes():
    """A session saved before notes existed has none of the new state keys."""
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = notes_subject(store)
    concept = store.list_concepts(USER, subject_id)[0]
    session_id = store.create_session(USER, subject_id, "old")
    question = Question(uuid4(), concept.id, "short", 1, "An old question?", "right")
    store.save_questions(USER, session_id, [question])
    saved = {
        "user_id": str(USER),
        "subject_id": str(subject_id),
        "thread_id": "old",
        "session_id": str(session_id),
        "studied_concept_ids": [str(concept.id)],
        "results": [],
        "quit": False,
        "caught_up": False,
        "fatal": None,
        "notices": [],
        "concept": concept_to_dict(concept),
        "start": {"stage": 0, "tide": None},
        "tally": {"correct": 0, "partial": 0, "incorrect": 0},
        "batch": [
            {
                "id": str(question.id),
                "concept_id": str(concept.id),
                "type": "short",
                "difficulty": 1,
                "prompt": question.prompt,
                "reference_answer": "right",
                "options": [],
            }
        ],
        "asked_prompts": [],
        "asked_on_concept": 0,
        "correct_streak": 0,
        "generation_calls_on_concept": 1,
        "question": None,
        "difficulty": None,
        "retry_question": False,
        "attempt_id": None,
        "answer": None,
        "grade": {"value": "correct", "explanation": "Earlier.", "misconception": None},
        "model_used": None,
        "lesson": None,
    }
    graph = build_graph(tutor, store, InMemorySaver(), now=lambda: NOW)
    config = {"configurable": {"thread_id": "old"}}
    graph.update_state(config, saved, as_node="teach")

    first = graph.invoke(None, config)["__interrupt__"][0].value
    second = graph.invoke(Command(resume=reply("no idea")), config)["__interrupt__"][0].value

    assert first["question"]["prompt"] == "An old question?"
    assert first["question"]["source_line"] is None
    assert first["feedback"]["citations"] == []
    assert first["lesson_citations"] == []
    assert second["feedback"]["value"] == "incorrect"
    assert second["feedback"]["general_knowledge"] is False
    assert second["lesson"] == "Here is the idea, with an example."
    assert tutor.sources_calls == [[]]  # the next batch, generated without retrieval
    assert second["question"]["source"] == "general"


# --- web questions for repeats ---------------------------------------------------------

PAGE = WebPage(
    "https://www.example.org/subnets",
    "Subnetting basics",
    "Subnetting divides one network into smaller networks. "
    "Each subnet keeps its own broadcast address, which no host may use.",
)
PAGE_QUOTE = "Subnetting divides one network into smaller networks"
OTHER_PAGE_QUOTE = "Each subnet keeps its own broadcast address"
REPEAT = "Why can't a host use the broadcast address?"
WEB_PROMPT = "What does subnetting divide a network into?"


def answered_before(store: MemoryStore, subject_id: UUID, prompt: str, index: int = 0) -> None:
    """An earlier session answered `prompt` on the index-th concept, which is now due."""
    concept = store.list_concepts(USER, subject_id)[index]
    session_id = store.create_session(USER, subject_id, f"earlier-{index}")
    question = Question(uuid4(), concept.id, "short", 1, prompt, "right")
    store.save_questions(USER, session_id, [question])
    earlier = NOW - timedelta(days=2)
    store.record_answer(
        USER,
        Answer(uuid4(), question.id, concept.id, "right", GradeValue.CORRECT, "Ok.", "m", earlier),
    )
    store.end_session(USER, session_id, earlier)
    seed_concept(
        store, concept.id, stage=1, last_reviewed_at=earlier, next_review_at=NOW - timedelta(days=1)
    )


def prompts_of(payloads: list[dict[str, Any]]) -> list[str]:
    return [p["question"]["prompt"] for p in payloads]


def test_repeat_triggers_one_search_and_a_web_question():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    answered_before(store, subject_id, REPEAT)
    tutor = FakeTutor(prompts=[REPEAT], web_prompts=[WEB_PROMPT], cite=[[], [("W1", PAGE_QUOTE)]])
    web = FakeWebSearch(pages=[PAGE])

    final, payloads = run(tutor, store, subject_id, quit_after(2, always_wrong), web=web)

    assert web.queries == ["Networks Concept 1"]
    assert tutor.web_count_calls == [None, 1]
    assert [s.label for s in tutor.sources_calls[1]] == ["W1"]
    assert REPEAT not in prompts_of(payloads)
    asked = payloads[1]["question"]  # the easiest left after a wrong answer at difficulty 1
    assert asked["prompt"] == WEB_PROMPT
    assert asked["source"] == "web"
    assert asked["source_line"] == "From the web · example.org"
    question = stored(store, WEB_PROMPT)
    assert question.source == "web"
    assert [(c.kind, c.label, c.quote) for c in question.citations] == [
        ("web", "example.org · Subnetting basics", PAGE_QUOTE)
    ]
    assert len(store.questions) == 1 + 5  # the earlier one, then four kept and one from the web
    assert final["generation_calls_on_concept"] == 1  # the web batch fills in; it isn't another
    assert WEB_NOTICE not in [n for p in payloads for n in p["notices"]]


PARAPHRASE = "Into what does subnetting divide a network?"  # too close to WEB_PROMPT


def test_near_duplicates_within_a_batch_are_dropped():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    tutor = FakeTutor(prompts=[WEB_PROMPT, PARAPHRASE])

    final, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    prompts = [q.prompt for q in store.questions.values()]
    assert WEB_PROMPT in prompts and PARAPHRASE not in prompts
    assert len(prompts) == 4
    assert payloads[0]["notices"] == [WEB_NOTICE]  # the duplicate counts as a repeat


def test_web_questions_avoid_and_skip_the_pending_batch():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    answered_before(store, subject_id, REPEAT)
    tutor = FakeTutor(
        prompts=[REPEAT, WEB_PROMPT], web_prompts=[PARAPHRASE], cite=[[], [("W1", PAGE_QUOTE)]]
    )

    final, _ = run(tutor, store, subject_id, lambda p: {"quit": True}, FakeWebSearch([PAGE]))

    assert tutor.web_count_calls == [None, 1]
    assert WEB_PROMPT in tutor.avoid_calls[1]  # still waiting in the batch
    assert all(q.source != "web" for q in store.questions.values())
    assert len(store.questions) == 1 + 4
    assert final["replacements_needed"] == 0


def test_web_question_with_bad_citations_is_dropped():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    answered_before(store, subject_id, REPEAT)
    bad = [("W1", "not on the page")]
    tutor = FakeTutor(prompts=[REPEAT], web_prompts=[WEB_PROMPT], cite=[[], bad, bad, bad])

    final, payloads = run(
        tutor, store, subject_id, lambda p: {"quit": True}, web=FakeWebSearch(pages=[PAGE])
    )

    assert tutor.web_count_calls == [None, 1, 1, 1]
    assert all(q.prompt != WEB_PROMPT and q.source != "web" for q in store.questions.values())
    assert len(store.questions) == 1 + 4
    assert payloads[0]["question"]["source"] == "general"
    assert final["generation_mode"] == "notes"
    assert final["replacements_needed"] == 0


def test_no_web_key_drops_repeats_with_one_notice():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=2)
    answered_before(store, subject_id, REPEAT, 0)
    answered_before(store, subject_id, REPEAT, 1)
    tutor = FakeTutor(prompts=[REPEAT])

    final, payloads = run(tutor, store, subject_id, always_right)

    assert [p["concept"] for p in payloads] == ["Concept 1"] * 2 + ["Concept 2"] * 2
    assert REPEAT not in prompts_of(payloads)
    assert payloads[0]["notices"] == [WEB_NOTICE]
    assert all(WEB_NOTICE not in p["notices"] for p in payloads[1:])
    assert tutor.web_count_calls == [None, None]
    assert final["web_notice_shown"] is True


def test_search_failure_stops_further_searches():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=2)
    answered_before(store, subject_id, REPEAT, 0)
    answered_before(store, subject_id, REPEAT, 1)
    tutor = FakeTutor(prompts=[REPEAT])
    web = FakeWebSearch(fail=True)

    final, payloads = run(tutor, store, subject_id, always_right, web=web)

    assert web.queries == ["Networks Concept 1"]
    assert [p["concept"] for p in payloads] == ["Concept 1"] * 2 + ["Concept 2"] * 2
    assert payloads[0]["notices"] == [WEB_NOTICE]
    assert all(WEB_NOTICE not in p["notices"] for p in payloads[1:])
    assert tutor.web_count_calls == [None, None]
    assert final["web_failed"] is True


def test_only_one_search_per_concept():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    answered_before(store, subject_id, REPEAT)
    tutor = FakeTutor(
        batch_size=1, prompts=[REPEAT], web_prompts=[WEB_PROMPT], cite=[[], [("W1", PAGE_QUOTE)]]
    )
    web = FakeWebSearch(pages=[PAGE])

    final, payloads = run(tutor, store, subject_id, always_wrong, web=web)

    assert prompts_of(payloads) == [WEB_PROMPT]  # the second batch was all repeats again
    assert web.queries == ["Networks Concept 1"]
    assert tutor.web_count_calls == [None, 1, None]
    assert final["generation_calls_on_concept"] == 2
    assert all(WEB_NOTICE not in p["notices"] for p in payloads)  # search ran; nothing failed


def test_web_question_is_graded_with_its_page():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    answered_before(store, subject_id, REPEAT)
    tutor = FakeTutor(
        batch_size=1,
        prompts=[REPEAT],
        web_prompts=[WEB_PROMPT],
        cite=[[], [("W1", PAGE_QUOTE)]],
        grade_cites=[[("W1", OTHER_PAGE_QUOTE)]],
    )

    final, payloads = run(tutor, store, subject_id, lambda p: reply("meh"), FakeWebSearch([PAGE]))

    assert prompts_of(payloads) == [WEB_PROMPT]  # then the second batch is all repeats
    feedback = final["grade"]
    assert feedback["value"] == "partial"
    assert feedback["citations"] == [
        {
            "kind": "web",
            "ref": PAGE.url,
            "label": "example.org · Subnetting basics",
            "page": None,
            "quote": OTHER_PAGE_QUOTE,
        }
    ]
    assert feedback["general_knowledge"] is False


def test_all_repeats_and_no_web_regenerates_or_moves_on():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=2)
    answered_before(store, subject_id, REPEAT, 0)  # only Concept 1 has answered it
    tutor = FakeTutor(batch_size=1, prompts=[REPEAT])

    _, payloads = run(tutor, store, subject_id, lambda p: {"quit": True})

    assert tutor.generate_calls == ["Concept 1", "Concept 1", "Concept 2"]
    assert payloads[0]["concept"] == "Concept 2"
    assert payloads[0]["question"]["prompt"] == REPEAT  # not a repeat on Concept 2
    assert payloads[0]["notices"] == [WEB_NOTICE]


def test_failed_web_generation_keeps_the_batch():
    store = MemoryStore()
    subject_id = make_subject(store, USER, concepts=1)
    answered_before(store, subject_id, REPEAT)
    tutor = FakeTutor(prompts=[REPEAT], fail_generate_on_call=2)

    final, payloads = run(tutor, store, subject_id, lambda p: {"quit": True}, FakeWebSearch([PAGE]))

    assert tutor.web_count_calls == [None, 1]
    assert final["fatal"] is None
    assert EXHAUSTED_NOTICE not in payloads[0]["notices"]
    assert len(store.questions) == 1 + 4
    assert final["generation_mode"] == "notes"


# --- disputes --------------------------------------------------------------------------


def dispute(reason: str = "correct", explanation: str = "") -> dict[str, Any]:
    return {"dispute": {"id": str(uuid4()), "reason": reason, "explanation": explanation}}


def script(*replies: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The replies in order, then quit."""
    pending = iter(replies)
    return lambda payload: next(pending, {"quit": True})


def due_subject(store: MemoryStore, concepts: int = 1) -> tuple[UUID, Concept]:
    """A subject whose first concept is at stage 2 and due."""
    subject_id = make_subject(store, USER, concepts=concepts)
    concept = store.list_concepts(USER, subject_id)[0]
    seed_concept(
        store,
        concept.id,
        stage=2,
        last_reviewed_at=NOW - timedelta(days=4),
        next_review_at=NOW - timedelta(days=1),
    )
    return subject_id, store.concept(concept.id)


def test_dispute_raises_the_grade_and_the_stage():
    store, tutor = MemoryStore(), FakeTutor(regrades=[("correct", False)])
    subject_id, concept = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))

    assert payloads[1]["feedback"]["value"] == "partial"
    assert payloads[1]["can_dispute"] is True
    assert payloads[1]["dispute_result"] is None
    assert payloads[2]["dispute_result"] == {
        "outcome": "changed",
        "original_grade": "partial",
        "new_grade": "correct",
        "explanation": "Regraded correct.",
        "citations": [],
        "concept": "Concept 1",
        "stage": 3,
    }
    assert tutor.regrade_calls == [("meh", "correct", "")]
    assert store.concept(concept.id).stage == 3  # recomputed as if the answer were correct
    [attempt] = store.attempts.values()
    assert attempt.grade is GradeValue.CORRECT
    assert final["concept"]["stage"] == 3
    [result] = final["results"]
    assert result["tally"] == {"correct": 1, "partial": 0, "incorrect": 0}
    assert result["stage_after"] == 3


def test_dispute_payload_carries_the_recheck_notices_only():
    class BackupRecheck(FakeTutor):
        def regrade(self, *args, **kwargs):
            return replace(super().regrade(*args, **kwargs), fell_back=True)

    class BackupGrade(FakeTutor):
        def grade(self, *args, **kwargs):
            return replace(super().grade(*args, **kwargs), fell_back=True)

    store, tutor = MemoryStore(), BackupRecheck(regrades=[("correct", False)])
    subject_id, _ = due_subject(store)
    _, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))
    assert payloads[1]["notices"] == []
    assert payloads[2]["notices"] == [FALLBACK_NOTICE]  # the re-check used the backup

    store, tutor = MemoryStore(), BackupGrade(regrades=[("correct", False)])
    subject_id, _ = due_subject(store)
    _, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))
    assert payloads[1]["notices"] == [FALLBACK_NOTICE]
    assert payloads[2]["notices"] == []  # shown with the grade; the re-check didn't fall back


def test_dispute_can_lower_the_grade():
    store, tutor = MemoryStore(), FakeTutor(regrades=[("incorrect", False)])
    subject_id, concept = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))

    assert payloads[2]["dispute_result"]["outcome"] == "changed"
    assert payloads[2]["dispute_result"]["new_grade"] == "incorrect"
    assert store.concept(concept.id).stage == 1
    assert tutor.teach_attempts == 0  # no lesson for the lowered grade
    assert payloads[2]["lesson"] is None
    assert final["results"][0]["tally"] == {"correct": 0, "partial": 0, "incorrect": 1}


def test_ambiguous_dispute_voids_the_answer():
    store, tutor = MemoryStore(), FakeTutor(regrades=[("partial", True)])
    subject_id, concept = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute("ambiguous")))

    result = payloads[2]["dispute_result"]
    assert (result["outcome"], result["new_grade"], result["stage"]) == ("voided", None, 2)
    assert store.concept(concept.id) == concept  # restored to its state before the answer
    [attempt_id] = store.attempts
    assert attempt_id in store.voided
    assert final["tally"] == {"correct": 0, "partial": 0, "incorrect": 0}
    assert final["results"] == []  # nothing left to summarise


def test_upheld_dispute_changes_nothing():
    store, tutor = MemoryStore(), FakeTutor(regrades=[("partial", False)])
    subject_id, concept = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))

    assert payloads[2]["dispute_result"]["outcome"] == "upheld"
    assert payloads[2]["dispute_result"]["new_grade"] == "partial"
    [attempt] = store.attempts.values()
    assert attempt.grade is GradeValue.PARTIAL
    assert store.concept(concept.id).stage == 2
    assert len(store.disputes) == 1  # recorded whatever the outcome
    assert final["results"][0]["tally"] == {"correct": 0, "partial": 1, "incorrect": 0}


def test_dispute_reshows_the_same_question():
    store, tutor = MemoryStore(), FakeTutor(regrades=[("correct", False)])
    subject_id, _ = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute(), reply("1")))

    assert payloads[2]["question"] == payloads[1]["question"]
    assert payloads[2]["number"] == payloads[1]["number"] == 2
    assert payloads[2]["feedback"]["value"] == "partial"  # the grade card stays as shown
    # answering it consumes it and clears the dispute
    assert payloads[3]["question"]["prompt"] != payloads[1]["question"]["prompt"]
    assert payloads[3]["dispute_result"] is None
    assert payloads[3]["can_dispute"] is False  # an MCQ answer
    assert final["pending_question_id"] is None
    assert len(store.attempts) == 2


def test_dispute_after_moving_on_corrects_the_previous_concept():
    store, tutor = MemoryStore(), FakeTutor(batch_size=1, regrades=[("incorrect", False)])
    subject_id, concept = due_subject(store, concepts=2)
    replies = script(reply("meh"), reply("meh"), dispute(), reply("meh"))
    # Concept 1's two answers must be graded at different times for one to be the latest.
    ticks = count()

    final, payloads = run(
        tutor, store, subject_id, replies, now=lambda: NOW + timedelta(seconds=next(ticks))
    )

    assert [p["concept"] for p in payloads] == ["Concept 1"] * 2 + ["Concept 2"] * 3
    assert payloads[2]["number"] == 1 and payloads[2]["can_dispute"] is True
    result = payloads[3]["dispute_result"]
    assert (result["outcome"], result["concept"], result["stage"]) == ("changed", "Concept 1", 1)
    assert payloads[3]["question"] == payloads[2]["question"]
    assert store.concept(concept.id).stage == 1
    first, second = final["results"]
    assert first["concept_id"] == str(concept.id)
    assert first["tally"] == {"correct": 0, "partial": 1, "incorrect": 1}
    assert first["stage_after"] == 1
    assert first["next_review_at"] == store.concept(concept.id).next_review_at.isoformat()
    assert first["tide_after"] == pytest.approx(1.0, abs=1e-3)  # seconds after the answer
    assert second["tally"] == {"correct": 0, "partial": 1, "incorrect": 0}  # Concept 2 untouched


def test_mcq_grades_cannot_be_disputed():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id, _ = due_subject(store)

    # a wrong MCQ answer, so the concept isn't finished by two correct ones
    _, payloads = run(tutor, store, subject_id, script(reply("right"), reply("2")))

    assert payloads[0]["can_dispute"] is False  # nothing graded yet
    assert payloads[1]["can_dispute"] is True
    assert payloads[1]["question"]["type"] == "mcq"
    assert payloads[2]["can_dispute"] is False


def test_each_answer_disputed_once():
    store, tutor = MemoryStore(), FakeTutor(regrades=[("correct", False)])
    subject_id, _ = due_subject(store)

    _, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute(), dispute()))

    assert payloads[2]["can_dispute"] is False
    assert payloads[3]["dispute_result"] == {"outcome": "refused"}
    assert payloads[3]["question"] == payloads[1]["question"]
    assert len(tutor.regrade_calls) == 1
    assert len(store.disputes) == 1


def test_dispute_the_store_refuses_is_not_offered_again():
    class RefusingStore(MemoryStore):
        def resolve_dispute(self, user_id, dispute, now):
            raise ValueError("not the concept's latest attempt")

    store, tutor = RefusingStore(), FakeTutor(regrades=[("correct", False)])
    subject_id, _ = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))

    assert payloads[2]["dispute_result"] == {"outcome": "refused"}
    assert payloads[2]["can_dispute"] is False
    assert payloads[2]["question"] == payloads[1]["question"]
    assert final["results"][0]["tally"] == {"correct": 0, "partial": 1, "incorrect": 0}


def test_regrade_unavailable_keeps_the_grade():
    store, tutor = MemoryStore(), FakeTutor(fail_regrade=True)
    subject_id, _ = due_subject(store)

    final, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute()))

    assert payloads[2]["dispute_result"] == {"outcome": "failed"}
    assert payloads[2]["can_dispute"] is True  # try again with d
    assert payloads[2]["question"] == payloads[1]["question"]
    assert store.disputes == {}
    [attempt] = store.attempts.values()
    assert attempt.grade is GradeValue.PARTIAL
    assert final["results"][0]["tally"] == {"correct": 0, "partial": 1, "incorrect": 0}


def test_regrade_checks_the_grades_sources_and_cites_them():
    class CitingTutor(FakeTutor):
        def regrade(self, question, answer, grade, reason, explanation, sources=(), failures=()):
            self.regrade_sources = [s.label for s in sources]
            generated = super().regrade(question, answer, grade, reason, explanation)
            new, ambiguous = generated.value
            cited = replace(new, claims=(Claim("N1", OTHER_QUOTE),))
            return replace(generated, value=(cited, ambiguous))

    store = MemoryStore()
    tutor = CitingTutor(cite=[[("N1", QUOTE)]], regrades=[("correct", False)])
    subject_id = notes_subject(store)

    _, payloads = run(tutor, store, subject_id, script(reply("meh"), dispute("notes_differ")))

    assert tutor.regrade_sources == ["N1"]
    [citation] = payloads[2]["dispute_result"]["citations"]
    assert (citation["label"], citation["quote"]) == ("CSE321.pdf · p. 12", OTHER_QUOTE)
    [(_, recorded)] = store.disputes.values()
    assert [c.quote for c in recorded.citations] == [OTHER_QUOTE]


class Crash(Exception):
    pass


class CrashAfterCommitStore(MemoryStore):
    """The process dies after resolve_dispute commits, before regrade's checkpoint."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    def resolve_dispute(self, user_id, dispute, now):
        resolved = super().resolve_dispute(user_id, dispute, now)
        if not self.crashed:
            self.crashed = True
            raise Crash
        return resolved


def crash_while_regrading(
    store: CrashAfterCommitStore, tutor: FakeTutor
) -> tuple[Any, dict[str, Any], Concept]:
    """Answer "meh", dispute it, and crash once the store has committed the dispute;
    returns a graph built as after a restart, its config, and the disputed concept."""
    subject_id, concept = due_subject(store)
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "regrading"}}
    start = {"user_id": str(USER), "subject_id": str(subject_id), "thread_id": "regrading"}
    graph = build_graph(tutor, store, saver, now=lambda: NOW)
    graph.invoke(start, config)
    graph.invoke(Command(resume=reply("meh")), config)
    with pytest.raises(Crash):
        graph.invoke(Command(resume=dispute()), config)
    return build_graph(tutor, store, saver, now=lambda: NOW), config, concept


def test_resume_while_regrading_resolves_once():
    store = CrashAfterCommitStore()
    # a second call would lower the grade; the stored outcome must win without one
    tutor = FakeTutor(regrades=[("correct", False), ("incorrect", False)])
    graph, config, concept = crash_while_regrading(store, tutor)

    payload = graph.invoke(None, config)["__interrupt__"][0].value

    assert len(tutor.regrade_calls) == 1  # the model isn't asked again
    result = payload["dispute_result"]
    assert (result["outcome"], result["new_grade"], result["stage"]) == ("changed", "correct", 3)
    assert result["explanation"] == "Regraded correct."  # the stored dispute's
    assert len(store.disputes) == 1
    assert store.concept(concept.id).stage == 3
    state = graph.get_state(config).values
    assert state["tally"] == {"correct": 1, "partial": 0, "incorrect": 0}
    assert state["last_graded"]["disputed"] is True
    assert state["dispute"] is None


def test_resume_while_regrading_needs_no_model():
    store, tutor = CrashAfterCommitStore(), FakeTutor(regrades=[("correct", False)])
    graph, config, concept = crash_while_regrading(store, tutor)
    tutor.fail_regrade = True  # every provider is busy after the restart

    payload = graph.invoke(None, config)["__interrupt__"][0].value

    result = payload["dispute_result"]
    assert (result["outcome"], result["new_grade"], result["stage"]) == ("changed", "correct", 3)
    assert payload["can_dispute"] is False
    assert len(tutor.regrade_calls) == 1
    assert store.concept(concept.id).stage == 3
    state = graph.get_state(config).values
    assert state["tally"] == {"correct": 1, "partial": 0, "incorrect": 0}  # corrected once
    assert state["concept"]["stage"] == 3
