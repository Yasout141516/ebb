"""Whole quiz sessions through interrupt/resume, offline (FakeTutor + MemoryStore)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ebb.domain import Answer, Concept
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
    the answer is stored and graded, but record_answer reports it as a duplicate."""

    def record_answer(self, user_id: UUID, answer: Answer) -> Concept | None:
        super().record_answer(user_id, answer)
        return None


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
