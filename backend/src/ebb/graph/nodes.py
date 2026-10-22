"""The fourteen quiz-loop nodes. Only generate_questions, verify_citations (rewriting the
questions whose citations fail), extend_subject, grade_answer (short answers), teach and
regrade call the LLM, and only search_web searches the web; everything else is plain code."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from langgraph.types import interrupt

from ebb.citations import (
    from_dict as citation_from_dict,
)
from ebb.citations import (
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
    Regrade,
    Source,
)
from ebb.grading import dispute_outcome, grade_mcq
from ebb.graph.retry import fix_citations
from ebb.graph.state import (
    QuizState,
    concept_from_dict,
    concept_to_dict,
    grade_from_dict,
    grade_to_dict,
    question_from_dict,
    question_to_dict,
    result_to_dict,
)
from ebb.llm.tutor import AllProvidersExhausted, Generated, Tutor
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
    if state["web_notice_shown"]:
        return {}
    return {"notices": _with_notice(state, WEB_NOTICE), "web_notice_shown": True}


# Every node gets its state through fill_defaults (see build._wrap), so keys added after
# milestone 1 can be indexed here even when a session saved before them resumes.


def _sources(state: QuizState, web: bool) -> list[Source]:
    """The concept's web pages if `web`, else its retrieved notes. Candidates are written
    from the web pages in web mode; a question is graded and taught from what it came from."""
    return [source_from_dict(s) for s in state["web_sources" if web else "sources"]]


def _web_mode(state: QuizState) -> bool:
    return state["run"]["mode"] == "web"


def _fresh_run() -> dict[str, Any]:
    return {"mode": "notes", "candidates": [], "replacing": 0}


def source_line(question: dict[str, Any]) -> str | None:
    """Where a question came from, shown under it: "From your notes · p. 12" and the like."""
    citations = question.get("citations", [])
    if question.get("source", "general") == "general" or not citations:
        return None
    first = citation_from_dict(citations[0])  # fills the fields a milestone 3 dict lacks
    if first.kind == "web":
        return f"From the web · {first.name}"
    if first.page is not None:
        return f"From your notes · p. {first.page}"
    return f"From your notes · § {first.heading}" if first.heading else "From your notes"


def _with_citations[T](
    generated: Generated[T],
    claims_of: Callable[[T], Sequence[Claim]],
    sources: Sequence[Source],
    fix: Callable[[Generated[T], tuple[str, ...]], Generated[T]],
) -> tuple[Generated[T], tuple[Citation, ...]]:
    """`generated` once `fix` has had its tries at any invalid claim (see fix_citations),
    with the citations that hold. Missing claims aren't retried, and nothing counts as
    cited when there are no sources."""
    if not sources:
        return generated, ()
    fixed = fix_citations(generated, lambda value: verify(claims_of(value), sources)[1], fix)
    return fixed, verify(claims_of(fixed.value), sources)[0]


def _failing(
    questions: Sequence[Question], sources: Sequence[Source]
) -> tuple[list[int], tuple[str, ...]]:
    """The indexes of the questions whose citations fail, and why, each numbered as a
    rewrite lists the failing questions. With sources, a question citing nothing fails."""
    if not sources:
        return [], ()
    failing: list[int] = []
    failures: list[str] = []
    for index, question in enumerate(questions):
        _, failed = verify(question.claims, sources)
        n = len(failing) + 1
        reasons = [f"question {n}: {reason}" for reason in failed]
        if not question.claims:
            reasons.append(f"question {n} cites nothing")
        if reasons:
            failures += reasons
            failing.append(index)
    return failing, tuple(failures)


def _keep_grade(first: Grade, retried: Grade) -> Grade:
    """A retry that changed the grade only lends the first grade its citations."""
    return retried if retried.value is first.value else replace(first, claims=retried.claims)


def _grade_dict(
    state: QuizState, grade: Grade, own: Sequence[Citation], question: Question
) -> dict[str, Any]:
    """A grade with no citations of its own shows the question's."""
    final = replace(grade, claims=(), citations=tuple(own) or question.citations)
    return grade_to_dict(final, state["has_notes"] and not final.citations)


def _dedupe(questions: Sequence[Question], known: Sequence[str]) -> list[Question]:
    """`questions` without those repeating a known prompt or a question kept before them."""
    seen = list(known)
    kept: list[Question] = []
    for question in questions:
        if not too_close(question.prompt, seen):
            kept.append(question)
            seen.append(question.prompt)
    return kept


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

    def answered_prompts(state: QuizState) -> list[str]:
        user_id, _ = ids(state)
        return store.answered_prompts(user_id, UUID(state["concept"]["id"]), 40)

    def known_prompts(state: QuizState) -> list[str]:
        """Earlier sessions' prompts on this concept first, then this session's, then those
        still waiting in the batch, without repeats. A session saved before the earlier
        prompts were kept in state asks the store for them."""
        earlier = state["earlier_prompts"]
        if earlier is None:
            earlier = answered_prompts(state)
        pending = [q["prompt"] for q in state["batch"]]
        return list(dict.fromkeys([*earlier, *state["asked_prompts"], *pending]))

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
            "run": _fresh_run(),
            "studied_concept_ids": [],
            "results": [],
            "quit": False,
            "caught_up": False,
            "fatal": None,
            "notices": [],
            "concept": None,
            "grade": None,
            "lesson": None,
            "current_question_id": None,
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
            "studied_concept_ids": [*state["studied_concept_ids"], str(concept.id)],
            "batch": [],
            "asked_prompts": [],
            "asked_on_concept": 0,
            "correct_streak": 0,
            "generation_calls_on_concept": 0,
            "difficulty": None,
            "current_question_id": None,
            "run": _fresh_run(),
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
        if state["has_notes"]:
            user_id, subject_id = ids(state)
            concept = state["concept"]
            chunks = store.search_chunks(
                user_id, subject_id, f"{concept['name']} {concept['description']}"
            )
            sources = [source_to_dict(s) for s in note_sources(chunks)]
        # Fetched once per concept; this session's answers are in asked_prompts.
        return {"sources": sources, "earlier_prompts": answered_prompts(state)}

    def generate_questions(state: QuizState) -> dict[str, Any]:
        concept = concept_from_dict(state["concept"])
        web_mode = _web_mode(state)
        # Web questions fill in the last batch, so they aren't another generation call.
        calls = state["generation_calls_on_concept"] + (0 if web_mode else 1)
        try:
            generated = tutor.generate_questions(
                concept,
                # Web questions join the batch, so they must not repeat what is waiting in it.
                known_prompts(state),
                _sources(state, web_mode),
                web_count=state["run"]["replacing"] if web_mode else None,
            )
        except AllProvidersExhausted:
            if web_mode:
                # The batch goes on without replacements, as when the search finds nothing.
                return {"run": {**state["run"], "candidates": []}}
            return {
                "fatal": EXHAUSTED_NOTICE,
                "generation_calls_on_concept": calls,
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        return {
            "run": {**state["run"], "candidates": [question_to_dict(q) for q in generated.value]},
            "generation_calls_on_concept": calls,
            "notices": _notices(state, generated),
        }

    def verify_citations(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        web_mode = _web_mode(state)
        sources = _sources(state, web_mode)

        def rewrite(
            last: Generated[list[Question]], failures: tuple[str, ...]
        ) -> Generated[list[Question]]:
            """Rewrites only the questions whose citations failed; the rest are kept as they
            are. Each rewrite takes its question's place; one not rewritten fails again."""
            failing, _ = _failing(last.value, sources)
            rewritten = tutor.generate_questions(
                concept_from_dict(state["concept"]),
                known_prompts(state),
                sources,
                failures,
                web_count=state["run"]["replacing"] if web_mode else None,
                rewrite=[last.value[i] for i in failing],
            )
            merged = list(last.value)
            for index, question in zip(failing, rewritten.value, strict=False):
                merged[index] = question
            return Generated(merged, rewritten.model, last.fell_back or rewritten.fell_back)

        # generate_questions already noted whether the candidates came from a backup model.
        candidates = Generated([question_from_dict(c) for c in state["run"]["candidates"]], "")
        fixed = fix_citations(
            candidates, lambda questions: _failing(questions, sources)[1], rewrite
        )
        checked = [(q, verify(q.claims, sources)[0] if sources else ()) for q in fixed.value]
        # After the retries bad citations are dropped. A notes question left with none is
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
        # A repeat of an earlier question, of one still waiting in the batch, or of one
        # kept just before it from these candidates.
        kept = _dedupe(final, known_prompts(state))
        store.save_questions(user_id, UUID(state["session_id"]), kept)
        # Only repeats from a notes batch are replaced; web ones are simply dropped.
        replacing = 0 if web_mode else len(final) - len(kept)
        update: dict[str, Any] = {
            "batch": [*state["batch"], *(question_to_dict(q) for q in kept)],
            "run": {"mode": "notes", "candidates": [], "replacing": replacing},
            "notices": _notices(state, fixed),
        }
        if replacing and web is None:
            update |= _web_notice(cast(QuizState, {**state, **update}))
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
        nothing = {**state["run"], "replacing": 0}  # no replacements to write
        searched = {"web_searched": [*state["web_searched"], concept["id"]]}
        try:
            # Only these two names leave the machine.
            pages = web.search(f"{subject.name} {concept['name']}")
        except WebSearchFailed:
            return {**searched, "web_failed": True, "run": nothing, **_web_notice(state)}
        if not pages:
            return {**searched, "run": nothing}
        return {
            **searched,
            "web_sources": [source_to_dict(s) for s in web_sources(pages)],
            "run": {**state["run"], "mode": "web"},
        }

    def ask_question(state: QuizState) -> dict[str, Any]:
        # Runs again from the top when resumed, so nothing before interrupt() may have side effects.
        batch = state["batch"]
        # After a dispute the question it interrupted is shown again, whatever the grade now
        # is; so is one whose grading failed.
        current = state["current_question_id"]
        index = next((i for i, q in enumerate(batch) if q["id"] == current), None)
        if index is None:
            last = state["grade"]["value"] if state["grade"] else None
            index = choose_question(
                batch, state["difficulty"], last, first=state["asked_on_concept"] == 0
            )
        question = batch[index]
        grade = state["grade"]
        feedback = None if grade is None else {"citations": [], "general_knowledge": False, **grade}
        graded = state["last_graded"]
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
                "notices": state["notices"],
                "can_dispute": graded is not None and not graded["disputed"],
                "dispute_result": state["dispute_result"],
            }
        )
        if "dispute" in reply:
            # The question stays in the batch, to be shown again once regrade is done.
            return {"dispute": reply["dispute"], "current_question_id": question["id"]}
        update: dict[str, Any] = {
            "question": question,
            "batch": batch[:index] + batch[index + 1 :],
            "current_question_id": None,
            "grade": None,
            "lesson": None,
            "notices": [],
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
        sources = _sources(state, question.source == "web")
        try:
            first = tutor.grade(question, state["answer"], sources)
        except AllProvidersExhausted:
            return {
                "grade": None,
                "batch": [state["question"], *state["batch"]],
                "current_question_id": state["question"]["id"],
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }

        def fix(last: Generated[Grade], failures: tuple[str, ...]) -> Generated[Grade]:
            retried = tutor.grade(question, state["answer"], sources, failures, previous=last.value)
            return replace(retried, value=_keep_grade(first.value, retried.value))

        generated, citations = _with_citations(first, lambda grade: grade.claims, sources, fix)
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
            "correct_streak": state["correct_streak"] + 1 if correct else 0,
            "asked_on_concept": state["asked_on_concept"] + 1,
            "last_graded": None if state["model_used"] == "code" else disputable(state),
        }

    def disputable(state: QuizState) -> dict[str, Any]:
        """What regrade needs of the answer just graded. Its sources are kept here, so a
        dispute raised after moving on to another concept still has them."""
        sources = _sources(state, state["question"].get("source") == "web")
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
        sources = _sources(state, question.source == "web")
        try:
            first = tutor.teach(question, state["answer"], grade, sources)
        except AllProvidersExhausted:
            return {
                "lesson": None,
                "notices": _with_notice(state, EXHAUSTED_NOTICE),
            }
        generated, citations = _with_citations(
            first,
            lambda lesson: lesson.claims,
            sources,
            lambda last, failures: tutor.teach(
                question, state["answer"], grade, sources, failures, previous=last.value
            ),
        )
        return {
            "lesson": {
                "text": generated.value.text,
                # Only the lesson's own citations; the grade card already shows the question's.
                "citations": [to_dict(c) for c in citations],
            },
            "notices": _notices(state, generated),
        }

    def recheck(
        graded: dict[str, Any], request: dict[str, Any]
    ) -> tuple[Dispute, Generated[Regrade]]:
        """Ask the model to re-check the graded answer; code decides the outcome. Raises
        AllProvidersExhausted if the first call fails."""
        question = question_from_dict(graded["question"])
        first = grade_from_dict(graded["grade"])
        reason = DisputeReason(request["reason"])
        sources = [source_from_dict(s) for s in graded["sources"]]

        def call(
            failures: tuple[str, ...] = (), previous: Grade | None = None
        ) -> Generated[Regrade]:
            return tutor.regrade(
                question,
                graded["answer"],
                first,
                reason,
                request["explanation"],
                sources,
                failures,
                previous=previous,
            )

        initial = call()

        def fix(last: Generated[Regrade], failures: tuple[str, ...]) -> Generated[Regrade]:
            retried = call(failures, last.value.grade)
            # The ambiguity verdict stays the first call's too.
            kept = initial.value
            return replace(
                retried, value=Regrade(_keep_grade(kept.grade, retried.value.grade), kept.ambiguous)
            )

        generated, citations = _with_citations(
            initial, lambda regraded: regraded.grade.claims, sources, fix
        )
        new = generated.value.grade
        outcome = dispute_outcome(reason, first.value, new.value, generated.value.ambiguous)
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
        graded = state["last_graded"]
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
        return update

    def next_concept(state: QuizState) -> dict[str, Any]:
        return {}

    def summarize(state: QuizState) -> dict[str, Any]:
        user_id, _ = ids(state)
        session_id, moment = UUID(state["session_id"]), now()
        store.end_session(user_id, session_id, moment)
        summary = store.session_summary(user_id, session_id, moment)
        return {"results": [result_to_dict(r) for r in summary]}

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
