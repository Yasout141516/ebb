"""The API for the browser test: memory store, fake tutor, fixed clock. No database, no model.

Run from backend/: `uv run python -m tests.e2e_server` (port 8001; Playwright starts it).
"""

import random
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import uvicorn
from langgraph.checkpoint.memory import InMemorySaver

from ebb.api.app import create_app
from ebb.domain import LlmCall
from ebb.graph.build import build_graph
from ebb.learn.graph import build_learn_graph
from ebb.learn.service import LearnSessions
from ebb.study import StudySessions
from tests.api_support import NOW, USER, make_services
from tests.fakes import FakeTutor

PORT = 8001
WEB_ORIGIN = "http://localhost:3100"  # the website the browser test starts


class E2ETutor(FakeTutor):
    """The fake tutor, with prompts that share no words, so none is dropped as a repeat of
    another and each concept keeps its whole batch (a short question, then multiple choice).

    It bypasses the LLM gateway, so it records one representative `LlmCall` (through the
    store's `record_llm_call`, as the gateway does) for each call it answers: the Costs page
    then has real rows to show."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.recorder: Any = None  # the store, set by main()
        self.recorded = 0

    def _record(self, role: str, endpoint: str) -> None:
        if self.recorder is None:
            return
        self.recorded += 1
        self.recorder.record_llm_call(
            USER,
            LlmCall(
                id=uuid4(),
                call_id=uuid4(),
                request_id=None,
                user_id=USER,
                at=NOW - timedelta(minutes=self.recorded),
                role=role,
                endpoint=endpoint,
                provider="groq",
                model="openai/gpt-oss-120b",
                attempt=1,
                outcome="ok",
                input_tokens=1200,
                output_tokens=300,
                reasoning_tokens=None,
                ttft_ms=400,
                total_ms=1500,
                cost_usd=Decimal("0.0003"),
                error_kind=None,
            ),
        )

    def generate_questions(self, *args: Any, **kwargs: Any):
        call = self.generate_attempts + 1
        self.prompts = [
            "Explain " + " ".join(f"topic{call}q{q}w{w}" for w in range(7)) for q in range(5)
        ]
        out = super().generate_questions(*args, **kwargs)
        self._record("quiz", "write_questions")
        return out

    def write_lesson(self, *args: Any, **kwargs: Any):
        out = super().write_lesson(*args, **kwargs)
        self._record("teach", "lesson")
        return out

    def answer(self, *args: Any, **kwargs: Any):
        out = super().answer(*args, **kwargs)
        self._record("teach", "learn_answer")
        return out

    def grade(self, *args: Any, **kwargs: Any):
        out = super().grade(*args, **kwargs)
        self._record("quiz", "grade")
        return out


def main() -> None:
    base = make_services()
    tutor, saver = E2ETutor(rng=random.Random(7)), InMemorySaver()
    tutor.recorder = base.store
    graph = build_graph(tutor, base.store, saver, now=lambda: NOW)
    sessions = StudySessions(base.store, graph, saver, lambda: NOW)
    learn = LearnSessions(
        base.store, build_learn_graph(tutor, base.store, saver, now=lambda: NOW), saver
    )
    services = replace(base, tutor=tutor, sessions=sessions, learn=learn)
    services.settings.ebb_web_origin = WEB_ORIGIN
    uvicorn.run(create_app(services), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
