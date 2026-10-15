"""Wire the quiz-loop nodes into a LangGraph graph."""

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
    after_generate,
    after_grade,
    after_mastery,
    after_pick,
    decide_next,
)
from ebb.store import Store

_NEXT = ["ask_question", "generate_questions", "next_concept", "summarize"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_graph(
    tutor: Tutor,
    store: Store,
    checkpointer: BaseCheckpointSaver,
    now: Callable[[], datetime] = _utc_now,
) -> CompiledStateGraph:
    graph = StateGraph(QuizState)
    for name, node in make_nodes(tutor, store, now).items():
        graph.add_node(name, node)

    graph.add_edge(START, "load_learner")
    graph.add_edge("load_learner", "pick_concept")
    graph.add_conditional_edges("pick_concept", after_pick, ["generate_questions", "summarize"])
    graph.add_conditional_edges("generate_questions", after_generate, ["ask_question", "summarize"])
    graph.add_conditional_edges("ask_question", after_ask, ["grade_answer", "summarize"])
    graph.add_conditional_edges("grade_answer", after_grade, ["update_mastery", "ask_question"])
    graph.add_conditional_edges("update_mastery", after_mastery, ["teach", *_NEXT])
    graph.add_conditional_edges("teach", decide_next, _NEXT)
    graph.add_edge("next_concept", "pick_concept")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=checkpointer)
