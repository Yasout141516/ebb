"""The fourteen quiz-loop nodes. Only generate_questions, extend_subject, grade_answer (short
answers), teach and regrade call the LLM, and only search_web searches the web; everything
else is plain code."""

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
    web_sources,
)
from ebb.domain import (
    Answer,
    Citation,
    Claim,
    Concept,
    Dispute,
    DisputeOutcome,
    DisputeReason,
    Grade,
    GradeValue,
    Question,
    Source,
)
from ebb.grading import dispute_outcome, grade_mcq
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
from ebb.repeats import too_close
from ebb.routing import choose_question, pick_next_concept
from ebb.store import Store
from ebb.web import WebSearch, WebSearchFailed

FALLBACK_NOTICE = "Switched to a backup model while the main one is busy."
EXHAUSTED_NOTICE = "The tutor couldn't respond — your progress is saved. Try again in a minute."
WEB_NOTICE = "Web search unavailable — skipped repeated questions."

Node = Callable[[QuizState], dict[str, Any]]


def _with_notice(state: QuizState, notice: str) -> list[str]:
    notices = list(state["notices"])
    if notice not in notices:
        notices.append(notice)
    return notices


def _notices(state: QuizState, generated: Generated[Any]) -> list[str]:
    return _with_notice(state, FALLBACK_NOTICE) if generated.fell_back else list(state["notices"])


def _web_notice(state: QuizState) -> dict[str, Any]:
    """The notice that repeats were skipped without replacements, once per session."""
    if state.get("web_notice_shown", False):
        return {}
    return {"notices": _with_notice(state, WEB_NOTICE), "web_notice_shown": True}


# Keys added after milestone 2 are read with a default: a session saved before them may resume.


def _sources(state: QuizState, key: str) -> list[Source]:
    return [source_from_dict(s) for s in state.get(key, [])]


def _web_mode(state: QuizState) -> bool:
    return state.get("generation_mode", "notes") == "web"


def _candidate_sources(state: QuizState) -> list[Source]:
    """What the candidates are written from: the notes, or the web pages in web mode."""
    return _sources(state, "web_sources" if _web_mode(state) else "sources")


def sources_for(state: QuizState, question: Question) -> list[Source]:
    """The sources `question` was written from, for grading and teaching it."""
    return _sources(state, "web_sources" if question.source == "web" else "sources")


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


def _corrected_tally(tally: dict[str, int], dispute: Dispute) -> dict[str, int]:
    """`tally` with the disputed answer moved to its new grade, or taken out if voided."""
    corrected = dict(tally)
    if dispute.outcome is DisputeOutcome.UPHELD:
        return corrected
    corrected[dispute.original_grade.value] -= 1
    if dispute.new_grade is not None:
        corrected[dispute.new_grade.value] += 1
    return corrected


def _dispute_result(dispute: Dispute, concept: Concept) -> dict[str, Any]:
    return {
        "outcome": dispute.outcome.value,
        "original_grade": dispute.original_grade.value,
        "new_grade": None if dispute.new_grade is None else dispute.new_grade.value,
        "explanation": dispute.regrade_explanation,
        "citations": [to_dict(c) for c in dispute.citations],
        "concept": concept.name,
        "stage": concept.stage,
    }


def make_nodes(
    tutor: Tutor, store: Store, now: Callable[[], datetime], web: WebSearch | None = None
) -> dict[str, Node]:
    def ids(state: QuizState) -> tuple[UUID, UUID]:
        return UUID(state["user_id"]), UUID(state["subject_id"])

    def earlier_prompts(state: QuizState) -> list[str]:
        """Earlier sessions' prompts on this concept first, then this session's, without repeats."""
        user_id, _ = ids(state)
        answered = store.answered_prompts(user_id, UUID(state["concept"]["id"]), 40)
        return list(dict.fromkeys([*answered, *state["asked_prompts"]]))

    def record_result(state: QuizState) -> list[dict[str, Any]]:
        results = list(state["results"])
        concept = state["concept"]
        if concept is None or sum(state["tally"].values()) == 0:
            return results
        current = concept_from_dict(concept)
        results.append(
            {
                "concept": current.name,
                "concept_id": concept["id"],
                "tally": dict(state["tally"]),
                "stage_before": state["start"]["stage"],
                "stage_after": current.stage,
                "tide_before": state["start"]["tide"],
                "tide_after": tide(current, now()),
                "next_review_at": concept["next_review_at"],
            }
        )
        return results

    def corrected_results(
        results: list[dict[str, Any]], concept: Concept, dispute: Dispute
    ) -> list[dict[str, Any]]:
        """`results` with the last entry for `concept` corrected by `dispute`. Entries saved
        before results carried concept ids are never matched."""
        results = list(results)
        index = next(
            (
                i
                for i in reversed(range(len(results)))
                if results[i].get("concept_id") == str(concept.id)
            ),
            None,
        )
        if index is None:
            return results
        tally = _corrected_tally(results[index]["tally"], dispute)
        if sum(tally.values()) == 0:
            del results[index]  # its only answer was voided; record_result skips such concepts
            return results
        results[index] = {
            **results[index],
            "tally": tally,
            "stage_after": concept.stage,
            "tide_after": tide(concept, now()),
            "next_review_at": concept_to_dict(concept)["next_review_at"],
        }
        return results

    def load_learner(state: QuizState) -> dict[str, Any]:
        user_id, subject_id = ids(state)
        session_id = store.create_session(user_id, subject_id, state["thread_id"])
        return {
            "session_id": str(session_id),
            "has_notes": store.count_documents(user_id, subject_id) > 0,
            "web_searched": [],
            "web_failed": False,
            "web_notice_shown": False,
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
            "generation_mode": "notes",
            "replacements_needed": 0,
            "web_sources": [],
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
        concept = concept_from_dict(state["concept"])
        failures = state.get("citation_failures", [])
        web_mode = _web_mode(state)
        # A citation retry rewrites the same batch, and web questions fill in the last one,
        # so neither is another generation call.
        calls = state["generation_calls_on_concept"] + (0 if failures or web_mode else 1)
        web_count = state.get("replacements_needed", 0) if web_mode else None
        try:
            generated = tutor.generate_questions(
                concept,
                earlier_prompts(state),
                _candidate_sources(state),
                failures,
                web_count=web_count,
            )
        except AllProvidersExhausted:
            if failures:
                # Keep the candidates we have; verify_citations drops their bad citations.
                return {"citation_retries": MAX_RETRIES}
            if web_mode:
                # The batch goes on without replacements, as when the search finds nothing.
                return {"candidates": []}
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
        web_mode = _web_mode(state)
        sources = _candidate_sources(state)
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
        # Out of retries: bad citations are dropped. A notes question left with none is
        # general; a web question left with none is dropped.
        if web_mode:
            final = [
                replace(q, source="web", claims=(), citations=cited)
                for q, cited in checked
                if cited
            ]
        else:
            final = [
                replace(q, source="notes" if cited else "general", claims=(), citations=cited)
                for q, cited in checked
            ]
        earlier = earlier_prompts(state)
        kept = [q for q in final if not too_close(q.prompt, earlier)]
        store.save_questions(user_id, UUID(state["session_id"]), kept)
        update: dict[str, Any] = {
            "batch": [*state["batch"], *(question_to_dict(q) for q in kept)],
            "candidates": [],
            "citation_retries": 0,
            "citation_failures": [],
            "generation_mode": "notes",
            # Only repeats from a notes batch are replaced; web ones are simply dropped.
            "replacements_needed": 0 if web_mode else len(final) - len(kept),
        }
        if update["replacements_needed"] and web is None:
            update |= _web_notice(state)
        return update

    def search_web(state: QuizState) -> dict[str, Any]:
        # Routing sends the graph here only when web search is set up.
        if web is None:
            raise RuntimeError("search_web needs a web search client")
        user_id, subject_id = ids(state)
        subject = store.get_subject(user_id, subject_id)
        if subject is None:
            raise LookupError(f"subject {subject_id} not found")
        concept = state["concept"]
        searched = {"web_searched": [*state.get("web_searched", []), concept["id"]]}
        try:
            # Only these two names leave the machine.
            pages = web.search(f"{subject.name} {concept['name']}")
        except WebSearchFailed:
            return {**searched, "web_failed": True, "replacements_needed": 0, **_web_notice(state)}
        if not pages:
            return {**searched, "replacements_needed": 0}
        return {
            **searched,
            "web_sources": [source_to_dict(s) for s in web_sources(pages)],
            "generation_mode": "web",
        }

    def ask_question(state: QuizState) -> dict[str, Any]:
        # Runs again from the top when resumed, so nothing before interrupt() may have side effects.
        batch = state["batch"]
        # After a dispute the question it interrupted is shown again, whatever the grade now is.
        pending = state.get("pending_question_id")
        index = next((i for i, q in enumerate(batch) if q["id"] == pending), None)
        if index is None and state["retry_question"]:
            index = 0
        elif index is None:
            last = state["grade"]["value"] if state["grade"] else None
            index = choose_question(
                batch, state["difficulty"], last, first=state["asked_on_concept"] == 0
            )
        question = batch[index]
        grade = state["grade"]
        feedback = None if grade is None else {"citations": [], "general_knowledge": False, **grade}
        graded = state.get("last_graded")
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
                "can_dispute": graded is not None and not graded["disputed"],
                "dispute_result": state.get("dispute_result"),
            }
        )
        if "dispute" in reply:
            # The question stays in the batch, to be shown again once regrade is done.
            return {"dispute": reply["dispute"], "pending_question_id": question["id"]}
        update: dict[str, Any] = {
            "question": question,
            "batch": batch[:index] + batch[index + 1 :],
            "retry_question": False,
            "grade": None,
            "lesson": None,
            "lesson_citations": [],
            "notices": [],
            "pending_question_id": None,
            "dispute_result": None,
            "last_graded": None,
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
            "last_graded": None if state["model_used"] == "code" else disputable(state),
        }

    def disputable(state: QuizState) -> dict[str, Any]:
        """What regrade needs of the answer just graded. Its sources are kept here, so a
        dispute raised after moving on to another concept still has them."""
        sources = sources_for(state, question_from_dict(state["question"]))
        return {
            "attempt_id": state["attempt_id"],
            "concept_id": state["concept"]["id"],
            "question": state["question"],
            "answer": state["answer"],
            "grade": state["grade"],
            "sources": [source_to_dict(s) for s in sources],
            "disputed": False,
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

    def recheck(
        graded: dict[str, Any], request: dict[str, Any]
    ) -> tuple[Dispute, Generated[tuple[Grade, bool]]]:
        """Ask the model to re-check the graded answer; code decides the outcome. Raises
        AllProvidersExhausted if the first call fails."""
        question = question_from_dict(graded["question"])
        first = grade_from_dict(graded["grade"])
        reason = DisputeReason(request["reason"])
        sources = [source_from_dict(s) for s in graded["sources"]]
        generated, citations = _verified(
            lambda failures: tutor.regrade(
                question,
                graded["answer"],
                first,
                reason,
                request["explanation"],
                sources,
                failures,
            ),
            lambda regraded: regraded[0].claims,
            sources,
        )
        new, ambiguous = generated.value
        outcome = dispute_outcome(reason, first.value, new.value, ambiguous)
        dispute = Dispute(
            id=UUID(request["id"]),
            attempt_id=UUID(graded["attempt_id"]),
            reason=reason,
            explanation=request["explanation"],
            original_grade=first.value,
            new_grade=None if outcome is DisputeOutcome.VOIDED else new.value,
            outcome=outcome,
            regrade_explanation=new.explanation,
            citations=citations,
            model_used=generated.model,
        )
        return dispute, generated

    def regrade(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        graded = state.get("last_graded")
        request = state["dispute"]
        # The turn's notices were shown with its grade; from here on they are the re-check's.
        refused = {"dispute": None, "dispute_result": {"outcome": "refused"}, "notices": []}
        if graded is None or graded["disputed"] or request["reason"] not in DisputeReason:
            return refused
        # Resumed after the store committed, the stored dispute is applied as it was: the
        # model isn't asked again, so a busy or different answer can't undo it.
        dispute = store.get_dispute(user_id, UUID(request["id"]))
        notices: list[str] = []
        if dispute is None:
            try:
                dispute, generated = recheck(graded, request)
            except AllProvidersExhausted:
                return {**refused, "dispute_result": {"outcome": "failed"}}
            notices = [FALLBACK_NOTICE] if generated.fell_back else []
        try:
            # A stored dispute comes back unchanged, with the concept as it is now.
            recorded, concept = store.resolve_dispute(user_id, dispute, now())
        except ValueError, LookupError:
            # Nothing the store refuses can succeed later, so d isn't offered again.
            return {**refused, "last_graded": {**graded, "disputed": True}}
        update: dict[str, Any] = {
            "dispute": None,
            "dispute_result": _dispute_result(recorded, concept),
            "last_graded": {**graded, "disputed": True},
            "notices": notices,
        }
        if graded["concept_id"] == state["concept"]["id"]:
            update["concept"] = concept_to_dict(concept)
            update["tally"] = _corrected_tally(state["tally"], recorded)
        else:
            update["results"] = corrected_results(state["results"], concept, recorded)
        return update

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
        "search_web": search_web,
        "ask_question": ask_question,
        "grade_answer": grade_answer,
        "update_mastery": update_mastery,
        "teach": teach,
        "regrade": regrade,
        "next_concept": next_concept,
        "summarize": summarize,
    }
