"""The ten quiz-loop nodes. Only generate_questions, extend_subject, grade_answer (short
answers) and teach call the LLM; everything else is plain code."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from ebb.domain import Answer, GradeValue
from ebb.grading import grade_mcq
from ebb.graph.state import (
    QuizState,
    concept_from_dict,
    concept_to_dict,
    grade_from_dict,
    grade_to_dict,
    question_from_dict,
    question_to_dict,
)
from ebb.llm.tutor import AllProvidersExhausted, Generated, Tutor
from ebb.mastery import tide
from ebb.routing import choose_question, pick_next_concept
from ebb.store import Store

FALLBACK_NOTICE = "Switched to a backup model while the main one is busy."
EXHAUSTED_NOTICE = "The tutor couldn't respond — your progress is saved. Try again in a minute."

Node = Callable[[QuizState], dict[str, Any]]


def _with_notice(state: QuizState, notice: str) -> list[str]:
    notices = list(state["notices"])
    if notice not in notices:
        notices.append(notice)
    return notices


def _notices(state: QuizState, generated: Generated[Any]) -> list[str]:
    return _with_notice(state, FALLBACK_NOTICE) if generated.fell_back else list(state["notices"])


def make_nodes(tutor: Tutor, store: Store, now: Callable[[], datetime]) -> dict[str, Node]:
    def ids(state: QuizState) -> tuple[UUID, UUID]:
        return UUID(state["user_id"]), UUID(state["subject_id"])

    def record_result(state: QuizState) -> list[dict[str, Any]]:
        results = list(state["results"])
        concept = state["concept"]
        if concept is None or sum(state["tally"].values()) == 0:
            return results
        current = concept_from_dict(concept)
        results.append(
            {
                "concept": current.name,
                "tally": dict(state["tally"]),
                "stage_before": state["start"]["stage"],
                "stage_after": current.stage,
                "tide_before": state["start"]["tide"],
                "tide_after": tide(current, now()),
                "next_review_at": concept["next_review_at"],
            }
        )
        return results

    def load_learner(state: QuizState) -> dict[str, Any]:
        user_id, subject_id = ids(state)
        session_id = store.create_session(user_id, subject_id, state["thread_id"])
        return {
            "session_id": str(session_id),
            "studied_concept_ids": [],
            "results": [],
            "quit": False,
            "caught_up": False,
            "fatal": None,
            "notices": [],
            "concept": None,
            "tally": {},
            "grade": None,
            "lesson": None,
            "retry_question": False,
        }

    def pick_concept(state: QuizState) -> dict[str, Any]:
        user_id, subject_id = ids(state)
        moment = now()
        concept = pick_next_concept(
            store.list_concepts(user_id, subject_id), state["studied_concept_ids"], moment
        )
        if concept is None:
            return {"concept": None}
        return {
            "concept": concept_to_dict(concept),
            "start": {"stage": concept.stage, "tide": tide(concept, moment)},
            "tally": {"correct": 0, "partial": 0, "incorrect": 0},
            "studied_concept_ids": [*state["studied_concept_ids"], str(concept.id)],
            "batch": [],
            "asked_prompts": [],
            "asked_on_concept": 0,
            "correct_streak": 0,
            "generation_calls_on_concept": 0,
            "difficulty": None,
            "retry_question": False,
        }

    def extend_subject(state: QuizState) -> dict[str, Any]:
        user_id, subject_id = ids(state)
        subject = store.get_subject(user_id, subject_id)
        if subject is None:
            raise LookupError(f"subject {subject_id} not found")
        try:
            generated = tutor.extend_concepts(
                subject.name, store.list_concepts(user_id, subject_id)
            )
        except AllProvidersExhausted:
            return {"fatal": EXHAUSTED_NOTICE, "notices": _with_notice(state, EXHAUSTED_NOTICE)}
        added = store.add_concepts(
            user_id, subject_id, [(d.name, d.description) for d in generated.value]
        )
        notices = _notices(state, generated)
        if added > 0:
            noun = "concept" if added == 1 else "concepts"
            notices.append(f"Added {added} more advanced {noun}.")
        return {"caught_up": added == 0, "notices": notices}

    def generate_questions(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        concept = concept_from_dict(state["concept"])
        calls = state["generation_calls_on_concept"] + 1
        # Earlier sessions' questions first, then this session's, without repeats.
        avoid = list(
            dict.fromkeys(
                [*store.answered_prompts(user_id, concept.id, 40), *state["asked_prompts"]]
            )
        )
        try:
            generated = tutor.generate_questions(concept, avoid)
        except AllProvidersExhausted:
            return {
                "fatal": EXHAUSTED_NOTICE,
                "generation_calls_on_concept": calls,
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        store.save_questions(user_id, UUID(state["session_id"]), generated.value)
        return {
            "batch": [question_to_dict(q) for q in generated.value],
            "generation_calls_on_concept": calls,
            "notices": _notices(state, generated),
        }

    def ask_question(state: QuizState) -> dict[str, Any]:
        # Runs again from the top when resumed, so nothing before interrupt() may have side effects.
        batch = state["batch"]
        if state["retry_question"]:
            index = 0
        else:
            last = state["grade"]["value"] if state["grade"] else None
            index = choose_question(
                batch, state["difficulty"], last, first=state["asked_on_concept"] == 0
            )
        question = batch[index]
        reply = interrupt(
            {
                "concept": state["concept"]["name"],
                "number": state["asked_on_concept"] + 1,
                "question": {
                    "type": question["type"],
                    "difficulty": question["difficulty"],
                    "prompt": question["prompt"],
                    "options": [o["text"] for o in question["options"]],
                },
                "feedback": state["grade"],
                "lesson": state["lesson"],
                "notices": state["notices"],
            }
        )
        update: dict[str, Any] = {
            "question": question,
            "batch": batch[:index] + batch[index + 1 :],
            "retry_question": False,
            "grade": None,
            "lesson": None,
            "notices": [],
        }
        if reply.get("quit"):
            return {**update, "quit": True}
        return {
            **update,
            "answer": reply["answer"],
            "attempt_id": reply["attempt_id"],
            "difficulty": question["difficulty"],
            "asked_prompts": [*state["asked_prompts"], question["prompt"]],
        }

    def grade_answer(state: QuizState) -> dict[str, Any]:
        question = question_from_dict(state["question"])
        if question.type == "mcq":
            return {
                "grade": grade_to_dict(grade_mcq(question, state["answer"])),
                "model_used": "code",
            }
        try:
            generated = tutor.grade(question, state["answer"])
        except AllProvidersExhausted:
            return {
                "grade": None,
                "batch": [state["question"], *state["batch"]],
                "retry_question": True,
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        return {
            "grade": grade_to_dict(generated.value),
            "model_used": generated.model,
            "notices": _notices(state, generated),
        }

    def update_mastery(state: QuizState) -> dict[str, Any]:
        user_id, subject_id = ids(state)
        concept = concept_from_dict(state["concept"])
        grade = grade_from_dict(state["grade"])
        answer = Answer(
            id=UUID(state["attempt_id"]),
            question_id=UUID(state["question"]["id"]),
            concept_id=concept.id,
            answer_text=state["answer"],
            grade=grade.value,
            feedback=grade.explanation,
            model_used=state["model_used"],
            graded_at=now(),
        )
        after = store.record_answer(user_id, answer)
        if after is None:
            # Already recorded: the process died after the commit but before this step's
            # checkpoint. The answer still counts here, against the concept's stored row.
            current = {c.id: c for c in store.list_concepts(user_id, subject_id)}
            after = current[concept.id]
        correct = grade.value is GradeValue.CORRECT
        return {
            "concept": concept_to_dict(after),
            "tally": {**state["tally"], grade.value.value: state["tally"][grade.value.value] + 1},
            "correct_streak": state["correct_streak"] + 1 if correct else 0,
            "asked_on_concept": state["asked_on_concept"] + 1,
        }

    def teach(state: QuizState) -> dict[str, Any]:
        try:
            generated = tutor.teach(
                question_from_dict(state["question"]),
                state["answer"],
                grade_from_dict(state["grade"]),
            )
        except AllProvidersExhausted:
            return {"lesson": None, "notices": _with_notice(state, EXHAUSTED_NOTICE)}
        return {"lesson": generated.value, "notices": _notices(state, generated)}

    def next_concept(state: QuizState) -> dict[str, Any]:
        return {"results": record_result(state)}

    def summarize(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        store.end_session(user_id, UUID(state["session_id"]), now())
        return {"results": record_result(state)}

    return {
        "load_learner": load_learner,
        "pick_concept": pick_concept,
        "extend_subject": extend_subject,
        "generate_questions": generate_questions,
        "ask_question": ask_question,
        "grade_answer": grade_answer,
        "update_mastery": update_mastery,
        "teach": teach,
        "next_concept": next_concept,
        "summarize": summarize,
    }
