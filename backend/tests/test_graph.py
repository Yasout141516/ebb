"""Whole quiz sessions through interrupt/resume, offline (FakeTutor + MemoryStore)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb.domain import Attempt, Concept
from ebb.graph.build import build_graph
from ebb.graph.nodes import EXHAUSTED_NOTICE, FALLBACK_NOTICE
from tests.fakes import FakeTutor, MemoryStore, make_subject, seed_concept

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
USER = UUID("00000000-0000-0000-0000-0000000000aa")


def run(
    tutor: FakeTutor,
    store: MemoryStore,
    subject_id: UUID,
    answer: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    graph = build_graph(tutor, store, InMemorySaver(), now=lambda: NOW)
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


def test_all_correct_session_covers_three_concepts_two_questions_each():
    store, tutor = MemoryStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=4)

    final, payloads = run(tutor, store, subject_id, always_right)

    assert len(payloads) == 6
    assert [p["concept"] for p in payloads] == ["Concept 1"] * 2 + ["Concept 2"] * 2 + [
        "Concept 3"
    ] * 2
    assert [p["question"]["difficulty"] for p in payloads[:2]] == [1, 2]  # easiest, then harder
    assert [r["concept"] for r in final["results"]] == ["Concept 1", "Concept 2", "Concept 3"]
    assert all(r["tally"] == {"correct": 2, "partial": 0, "incorrect": 0} for r in final["results"])
    # first answer moves a new concept to stage 1; the second is early, so no promotion
    assert [r["stage_after"] for r in final["results"]] == [1, 1, 1]
    assert len(store.attempts) == 6
    session = next(iter(store.sessions.values()))
    assert session["ended_at"] == NOW
    assert final["grade"]["value"] == "correct"  # feedback for the last answer is shown


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
    """Simulates a duplicate resume: the attempt id was already stored, so nothing changes."""

    def record_attempt(self, user_id: UUID, attempt: Attempt, concept_after: Concept) -> bool:
        return False


def test_duplicate_attempt_changes_nothing():
    store, tutor = AlreadyRecordedStore(), FakeTutor()
    subject_id = make_subject(store, USER, concepts=1)
    answers = iter([reply("right"), {"quit": True}])

    final, _ = run(tutor, store, subject_id, lambda p: next(answers))

    assert store.attempts == {}
    assert store.list_concepts(USER, subject_id)[0].stage == 0
    assert final["results"] == []  # the tally never moved, so no result line


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
