"""Wire the fourteen quiz-loop nodes into a LangGraph graph.

pick_concept -> retrieve_notes | extend_subject | summarize
extend_subject -> pick_concept | summarize
retrieve_notes -> generate_questions <-> verify_citations -> ask_question
verify_citations -> search_web -> generate_questions (web mode) | ask_question
ask_question -> regrade -> ask_question (a disputed grade)
"""

from collections.abc import Callable
from datetime import UTC, datetime

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ebb.graph.nodes import make_nodes
from ebb.graph.state import QuizState
from ebb.llm.tutor import Tutor
from ebb.routing import (
    after_ask,
    after_extend,
    after_generate,
    after_grade,
    after_mastery,
    after_pick,
    after_search,
    after_verify,
    decide_next,
)
from ebb.store import Store
from ebb.web import WebSearch

_NEXT = ["ask_question", "generate_questions", "next_concept", "summarize"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_graph(
    tutor: Tutor,
    store: Store,
    checkpointer: BaseCheckpointSaver,
    now: Callable[[], datetime] = _utc_now,
    web: WebSearch | None = None,
) -> CompiledStateGraph:
    graph = StateGraph(QuizState)
    for name, node in make_nodes(tutor, store, now, web).items():
        graph.add_node(name, node)

    graph.add_edge(START, "load_learner")
    graph.add_edge("load_learner", "pick_concept")
    graph.add_conditional_edges(
        "pick_concept", after_pick, ["retrieve_notes", "extend_subject", "summarize"]
    )
    graph.add_conditional_edges("extend_subject", after_extend, ["pick_concept", "summarize"])
    graph.add_edge("retrieve_notes", "generate_questions")
    graph.add_conditional_edges(
        "generate_questions", after_generate, ["verify_citations", "summarize"]
    )
    graph.add_conditional_edges(
        "verify_citations",
        lambda state: after_verify(state, web=web is not None),
        [*_NEXT, "search_web"],
    )
    graph.add_conditional_edges("search_web", after_search, _NEXT)
    graph.add_conditional_edges("ask_question", after_ask, ["grade_answer", "regrade", "summarize"])
    graph.add_edge("regrade", "ask_question")
    graph.add_conditional_edges("grade_answer", after_grade, ["update_mastery", "ask_question"])
    graph.add_conditional_edges("update_mastery", after_mastery, ["teach", *_NEXT])
    graph.add_conditional_edges("teach", decide_next, _NEXT)
    graph.add_edge("next_concept", "pick_concept")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=checkpointer)
