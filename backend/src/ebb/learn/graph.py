"""The learn phase: a new concept's lesson, then the learner's questions until Ready.

gather_sources -> write_lesson -> wait
wait --ask--> gather_sources -> answer -> wait
wait --ready--> mark_learned -> END

Only write_lesson and answer call the LLM, each streaming its markdown as {"text": delta}
custom events, and only gather_sources searches the web. Nothing here touches a concept's
stage, tide or review date.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from functools import partial
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from ebb.citations import note_sources, source_from_dict, source_to_dict, to_dict, web_sources
from ebb.clock import utc_now
from ebb.domain import Citation, Concept, Exchange, LearnText, Source, StoredLesson
from ebb.graph.nodes import FALLBACK_NOTICE, with_notice
from ebb.graph.retry import with_citations
from ebb.graph.state import concept_from_dict, concept_to_dict
from ebb.llm.tutor import Generated, Tutor
from ebb.store import Store
from ebb.web import WebSearch, WebSearchFailed

WEB_NOTICE = "Web search unavailable — this lesson uses your notes only."
NOTES_ONLY = "Your notes only"
GENERAL = "General knowledge · not from your notes"


class LearnState(TypedDict):
    user_id: str
    concept: dict[str, Any]  # as concept_to_dict
    subject: str  # its name
    has_notes: bool
    sources: list[dict[str, Any]]  # gathered for the lesson or answer about to be written
    lesson: dict[str, Any] | None  # as lesson_to_dict, once written
    exchanges: list[dict[str, Any]]  # as exchange_to_dict, by position
    suggestions: list[str]  # the follow-ups offered now: the last answer's, or the lesson's
    notices: list[str]  # this turn's
    asking: dict[str, Any] | None  # the question being answered: {ask, kind, ask_id}
    answered: list[str]  # the ask ids answered, so one sent twice is not asked again
    ready: bool


def first_state(user_id: UUID, concept: Concept, subject: str, has_notes: bool) -> LearnState:
    return {
        "user_id": str(user_id),
        "concept": concept_to_dict(concept),
        "subject": subject,
        "has_notes": has_notes,
        "sources": [],
        "lesson": None,
        "exchanges": [],
        "suggestions": [],
        "notices": [],
        "asking": None,
        "answered": [],
        "ready": False,
    }


def lesson_to_dict(lesson: StoredLesson) -> dict[str, Any]:
    return {
        "concept_id": str(lesson.concept_id),
        "markdown": lesson.markdown,
        "citations": [to_dict(c) for c in lesson.citations],
        "web_pages": [dict(page) for page in lesson.web_pages],
        "suggestions": list(lesson.suggestions),
        "checks": list(lesson.checks),
        "source_summary": dict(lesson.source_summary),
        "model": lesson.model,
        "written_at": lesson.written_at.isoformat(),
        "learned_at": None if lesson.learned_at is None else lesson.learned_at.isoformat(),
    }


def exchange_to_dict(exchange: Exchange) -> dict[str, Any]:
    return {
        "position": exchange.position,
        "kind": exchange.kind,
        "question": exchange.question,
        "answer": exchange.answer,
        "citations": [to_dict(c) for c in exchange.citations],
        "suggestions": list(exchange.suggestions),
        "asked_at": exchange.asked_at.isoformat(),
    }


def source_summary(sources: Sequence[Source]) -> dict[str, Any]:
    """What the lesson's header says it was written from: the notes pages and the number of
    web pages; without web pages, a label instead ("Your notes only", or general knowledge
    when no notes were found either)."""
    notes = [s for s in sources if s.kind == "chunk"]
    pages = {
        s.page_start + n
        for s in notes
        if s.page_start is not None
        for n in range(max(len(s.page_offsets), 1))
    }
    web = len(sources) - len(notes)
    label = None if web else (NOTES_ONLY if notes else GENERAL)
    return {"notes": len(notes), "notes_pages": sorted(pages), "web_pages": web, "label": label}


def read_back(store: Store, user_id: UUID, concept_id: UUID) -> dict[str, Any]:
    """The kept lesson, its exchanges and the follow-ups they offer now, as state."""
    lesson = store.get_lesson(user_id, concept_id)
    if lesson is None:
        raise LookupError(f"no lesson kept for concept {concept_id}")
    kept = store.exchanges(user_id, concept_id)
    return {
        "lesson": lesson_to_dict(lesson),
        "exchanges": [exchange_to_dict(e) for e in kept],
        "suggestions": list(kept[-1].suggestions if kept else lesson.suggestions),
    }


def turn(state: LearnState) -> dict[str, Any]:
    """What `wait` shows: the lesson, the questions asked so far and what to ask next."""
    return {
        "kind": "learn",
        "concept_id": state["concept"]["id"],
        "concept": state["concept"]["name"],
        "lesson": state["lesson"],
        "exchanges": state["exchanges"],
        "suggestions": state["suggestions"],
        "notices": state["notices"],
        "has_notes": state["has_notes"],
    }


def _step(name: str, label: str) -> None:
    get_stream_writer()({"step": name, "label": label})


def build_learn_graph(
    tutor: Tutor,
    store: Store,
    checkpointer: BaseCheckpointSaver,
    now: Callable[[], datetime] = utc_now,
    web: WebSearch | None = None,
) -> CompiledStateGraph:
    def ids(state: LearnState) -> tuple[UUID, UUID]:
        return UUID(state["user_id"]), UUID(state["concept"]["id"])

    def gather_sources(state: LearnState) -> dict[str, Any]:
        user_id, concept_id = ids(state)
        asking = state["asking"]
        if asking is None and store.get_lesson(user_id, concept_id) is not None:
            return {"sources": []}  # written before: write_lesson reads it back
        concept = state["concept"]
        about = f"{concept['name']} {concept['description']}"
        question = None if asking is None else asking["ask"]
        sources: list[Source] = []
        if state["has_notes"]:
            subject_id = UUID(concept["subject_id"])
            found = about if question is None else f"{question} {about}"
            sources += note_sources(store.search_chunks(user_id, subject_id, found))
        update: dict[str, Any] = {}
        if web is not None:
            _step("gather_sources", "Searching the web…")
            # A lesson's search is about the concept; a question's, about it and the name.
            if question is None:
                query = f"{about} {state['subject']}"
            else:
                query = f"{question} {concept['name']}"
            try:
                sources += web_sources(web.search(query))
            except WebSearchFailed:
                update["notices"] = with_notice(state, WEB_NOTICE)
        return {**update, "sources": [source_to_dict(s) for s in sources]}

    def compose(
        state: LearnState, call: Callable[..., Generated[LearnText]]
    ) -> tuple[Generated[LearnText], tuple[Citation, ...], dict[str, Any]]:
        """`call`'s text, streamed as it is written, once its citations have had their
        retries; the citations that hold; and the notices update. A first call that fails
        raises: nothing is kept, and the turn can be run again."""
        sources = [source_from_dict(s) for s in state["sources"]]
        write = get_stream_writer()
        first = call(sources, on_text=lambda text: write({"text": text}))
        generated, citations = with_citations(
            first,
            lambda text: text.claims,
            sources,
            lambda last, failures: call(sources, failures=failures, previous=last.value),
        )
        # A retry may name no model (it had nothing to cite from): the first call's is kept.
        generated = Generated(generated.value, first.model, first.fell_back or generated.fell_back)
        update = {"notices": with_notice(state, FALLBACK_NOTICE)} if generated.fell_back else {}
        return generated, citations, update

    def write_lesson(state: LearnState) -> dict[str, Any]:
        user_id, concept_id = ids(state)
        update: dict[str, Any] = {"sources": []}
        if store.get_lesson(user_id, concept_id) is None:
            _step("write_lesson", "Writing the lesson…")
            concept = concept_from_dict(state["concept"])
            call = partial(tutor.write_lesson, concept, state["subject"])
            generated, citations, notices = compose(state, call)
            text = generated.value
            sources = [source_from_dict(s) for s in state["sources"]]
            web_pages = tuple(
                {"url": s.ref, "title": s.page_title, "site": s.site}
                for s in sources
                if s.kind == "web"
            )
            lesson = StoredLesson(
                concept_id,
                text.markdown,
                citations,
                web_pages,
                text.suggestions,
                text.checks,
                source_summary(sources),
                generated.model,
                now(),
            )
            store.save_lesson(user_id, lesson)
            update |= notices
        # Read back: if another run saved it first, theirs is the one kept.
        return {**update, **read_back(store, user_id, concept_id)}

    def answer(state: LearnState) -> dict[str, Any]:
        user_id, concept_id = ids(state)
        asking = state["asking"]
        if asking is None or state["lesson"] is None:
            raise ValueError("answer needs a question and the lesson")
        position = len(state["exchanges"])
        update: dict[str, Any] = {}
        # Resumed after the store kept the answer, it isn't asked of the model again.
        if len(store.exchanges(user_id, concept_id)) <= position:
            _step("answer", "Answering…")
            history = [(e["question"], e["answer"]) for e in state["exchanges"]]
            call = partial(
                tutor.answer,
                concept_from_dict(state["concept"]),
                state["lesson"]["markdown"],
                history,
                asking["ask"],
            )
            generated, citations, update = compose(state, call)
            exchange = Exchange(
                position,
                asking["kind"],
                asking["ask"],
                generated.value.markdown,
                citations,
                generated.value.suggestions,
                now(),
            )
            store.add_exchange(user_id, concept_id, exchange)
        kept = store.exchanges(user_id, concept_id)[position]
        return {
            **update,
            "sources": [],
            "exchanges": [*state["exchanges"], exchange_to_dict(kept)],
            "suggestions": list(kept.suggestions),
            "answered": [*state["answered"], asking["ask_id"]],
            "asking": None,
        }

    def wait(state: LearnState) -> dict[str, Any]:
        # Runs again from the top when resumed, so nothing before interrupt() may have side effects.
        reply = interrupt(turn(state))
        if reply.get("ready"):
            return {"ready": True}
        if reply["ask_id"] in state["answered"]:
            return {}  # asked twice: the turn is shown again
        asking = {"ask": reply["ask"], "kind": reply["kind"], "ask_id": reply["ask_id"]}
        return {"asking": asking, "notices": []}

    def after_wait(state: LearnState) -> str:
        if state["ready"]:
            return "mark_learned"
        return "wait" if state["asking"] is None else "gather_sources"

    def mark_learned(state: LearnState) -> dict[str, Any]:
        user_id, concept_id = ids(state)
        store.mark_learned(user_id, concept_id, now())  # False if already learned: still done
        return {}

    graph = StateGraph(LearnState)
    graph.add_node("gather_sources", gather_sources)
    graph.add_node("write_lesson", write_lesson)
    graph.add_node("answer", answer)
    graph.add_node("wait", wait)
    graph.add_node("mark_learned", mark_learned)
    graph.add_edge(START, "gather_sources")
    graph.add_conditional_edges(
        "gather_sources",
        lambda state: "write_lesson" if state["asking"] is None else "answer",
        ["write_lesson", "answer"],
    )
    graph.add_edge("write_lesson", "wait")
    graph.add_edge("answer", "wait")
    graph.add_conditional_edges("wait", after_wait, ["mark_learned", "gather_sources", "wait"])
    graph.add_edge("mark_learned", END)
    return graph.compile(checkpointer=checkpointer)
