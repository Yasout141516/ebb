"""The twelve quiz-loop nodes. Only generate_questions, extend_subject, grade_answer (short
answers) and teach call the LLM; everything else is plain code."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from ebb.citations import (
    MAX_RETRIES,
    note_sources,
    source_from_dict,
    source_to_dict,
    to_dict,
    verify,
)
from ebb.domain import Answer, Citation, Claim, Grade, GradeValue, Question, Source
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


# Keys added after milestone 2 are read with a default: a session saved before them may resume.


def sources_for(state: QuizState, question: Question) -> list[Source]:
    """The sources `question` was written from, for grading and teaching it."""
    return [source_from_dict(s) for s in state.get("sources", [])]


def source_line(question: dict[str, Any]) -> str | None:
    """Where a question came from, shown under it: "From your notes · p. 12" and the like."""
    citations = question.get("citations", [])
    if question.get("source", "general") == "general" or not citations:
        return None
    first = citations[0]
    if first["kind"] == "web":
        return f"From the web · {first['label'].split(' · ')[0]}"  # "domain · page title"
    if first["page"] is not None:
        return f"From your notes · p. {first['page']}"
    _, heading_mark, heading = first["label"].partition(" · § ")
    return f"From your notes · § {heading}" if heading_mark else "From your notes"


def _verified[T](
    call: Callable[[tuple[str, ...]], Generated[T]],
    claims_of: Callable[[T], Sequence[Claim]],
    sources: Sequence[Source],
) -> tuple[Generated[T], tuple[Citation, ...]]:
    """Call with no failures, then again with them while any claim is invalid, at most
    MAX_RETRIES more times. A retry that fails keeps the last result. Missing claims
    aren't retried, and nothing counts as cited when there are no sources."""
    generated = call(())
    if not sources:
        return generated, ()
    citations, failures = verify(claims_of(generated.value), sources)
    for _ in range(MAX_RETRIES):
        if not failures:
            break
        try:
            generated = call(failures)
        except AllProvidersExhausted:
            break
        citations, failures = verify(claims_of(generated.value), sources)
    return generated, citations


def _grade_dict(
    state: QuizState, grade: Grade, own: Sequence[Citation], question: Question
) -> dict[str, Any]:
    """A grade with no citations of its own shows the question's."""
    final = replace(grade, claims=(), citations=tuple(own) or question.citations)
    return grade_to_dict(final, state.get("has_notes", False) and not final.citations)


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
            "has_notes": store.count_documents(user_id, subject_id) > 0,
            "sources": [],
            "candidates": [],
            "citation_retries": 0,
            "citation_failures": [],
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
            "lesson_citations": [],
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

    def retrieve_notes(state: QuizState) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        if state.get("has_notes", False):
            user_id, subject_id = ids(state)
            concept = state["concept"]
            chunks = store.search_chunks(
                user_id, subject_id, f"{concept['name']} {concept['description']}"
            )
            sources = [source_to_dict(s) for s in note_sources(chunks)]
        return {"sources": sources, "citation_retries": 0, "citation_failures": []}

    def generate_questions(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        concept = concept_from_dict(state["concept"])
        failures = state.get("citation_failures", [])
        # A citation retry rewrites the same batch, so it isn't another generation call.
        calls = state["generation_calls_on_concept"] + (0 if failures else 1)
        # Earlier sessions' questions first, then this session's, without repeats.
        avoid = list(
            dict.fromkeys(
                [*store.answered_prompts(user_id, concept.id, 40), *state["asked_prompts"]]
            )
        )
        sources = [source_from_dict(s) for s in state.get("sources", [])]
        try:
            generated = tutor.generate_questions(concept, avoid, sources, failures)
        except AllProvidersExhausted:
            if failures:
                # Keep the candidates we have; verify_citations drops their bad citations.
                return {"citation_retries": MAX_RETRIES}
            return {
                "fatal": EXHAUSTED_NOTICE,
                "generation_calls_on_concept": calls,
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        return {
            "candidates": [question_to_dict(q) for q in generated.value],
            "generation_calls_on_concept": calls,
            "notices": _notices(state, generated),
        }

    def verify_citations(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        sources = [source_from_dict(s) for s in state.get("sources", [])]
        retries = state.get("citation_retries", 0)
        checked: list[tuple[Question, tuple[Citation, ...]]] = []
        failures: list[str] = []
        for n, candidate in enumerate(state.get("candidates", []), 1):
            question = question_from_dict(candidate)
            citations, failed = verify(question.claims, sources) if sources else ((), ())
            failures += [f"question {n}: {reason}" for reason in failed]
            if sources and not question.claims:
                failures.append(f"question {n} cites nothing")
            checked.append((question, citations))
        if failures and retries < MAX_RETRIES:
            return {"citation_retries": retries + 1, "citation_failures": failures}
        # Out of retries: bad citations are dropped, and a question left with none is general.
        kept = [
            replace(q, source="notes" if cited else "general", claims=(), citations=cited)
            for q, cited in checked
        ]
        store.save_questions(user_id, UUID(state["session_id"]), kept)
        return {
            "batch": [*state["batch"], *(question_to_dict(q) for q in kept)],
            "candidates": [],
            "citation_retries": 0,
            "citation_failures": [],
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
        grade = state["grade"]
        feedback = None if grade is None else {"citations": [], "general_knowledge": False, **grade}
        reply = interrupt(
            {
                "concept": state["concept"]["name"],
                "number": state["asked_on_concept"] + 1,
                "question": {
                    "type": question["type"],
                    "difficulty": question["difficulty"],
                    "prompt": question["prompt"],
                    "options": [o["text"] for o in question["options"]],
                    "source": question.get("source", "general"),
                    "source_line": source_line(question),
                },
                "feedback": feedback,
                "lesson": state["lesson"],
                "lesson_citations": state.get("lesson_citations", []),
                "notices": state["notices"],
            }
        )
        update: dict[str, Any] = {
            "question": question,
            "batch": batch[:index] + batch[index + 1 :],
            "retry_question": False,
            "grade": None,
            "lesson": None,
            "lesson_citations": [],
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
            graded = grade_mcq(question, state["answer"])
            return {"grade": _grade_dict(state, graded, (), question), "model_used": "code"}
        sources = sources_for(state, question)
        try:
            generated, citations = _verified(
                lambda failures: tutor.grade(question, state["answer"], sources, failures),
                lambda grade: grade.claims,
                sources,
            )
        except AllProvidersExhausted:
            return {
                "grade": None,
                "batch": [state["question"], *state["batch"]],
                "retry_question": True,
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        return {
            "grade": _grade_dict(state, generated.value, citations, question),
            "model_used": generated.model,
            "notices": _notices(state, generated),
        }

    def update_mastery(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
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
            citations=grade.citations,
        )
        # If the process died after recording this answer but before this step's checkpoint,
        # record_answer returns the stored row, so the answer still counts here exactly once.
        after = store.record_answer(user_id, answer)
        correct = grade.value is GradeValue.CORRECT
        return {
            "concept": concept_to_dict(after),
            "tally": {**state["tally"], grade.value.value: state["tally"][grade.value.value] + 1},
            "correct_streak": state["correct_streak"] + 1 if correct else 0,
            "asked_on_concept": state["asked_on_concept"] + 1,
        }

    def teach(state: QuizState) -> dict[str, Any]:
        question = question_from_dict(state["question"])
        grade = grade_from_dict(state["grade"])
        sources = sources_for(state, question)
        try:
            generated, citations = _verified(
                lambda failures: tutor.teach(question, state["answer"], grade, sources, failures),
                lambda lesson: lesson.claims,
                sources,
            )
        except AllProvidersExhausted:
            return {
                "lesson": None,
                "lesson_citations": [],
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        return {
            "lesson": generated.value.text,
            # Only the lesson's own citations; the grade card already shows the question's.
            "lesson_citations": [to_dict(c) for c in citations],
            "notices": _notices(state, generated),
        }

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
        "retrieve_notes": retrieve_notes,
        "generate_questions": generate_questions,
        "verify_citations": verify_citations,
        "ask_question": ask_question,
        "grade_answer": grade_answer,
        "update_mastery": update_mastery,
        "teach": teach,
        "next_concept": next_concept,
        "summarize": summarize,
    }
