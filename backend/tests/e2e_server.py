"""The API for the browser test: memory store, fake tutor, fixed clock. No database, no model.

Run from backend/: `uv run python -m tests.e2e_server` (port 8001; Playwright starts it).
"""

import random
from dataclasses import replace
from typing import Any

import uvicorn
from langgraph.checkpoint.memory import InMemorySaver

from ebb.api.app import create_app
from ebb.graph.build import build_graph
from ebb.study import StudySessions
from tests.api_support import NOW, make_services
from tests.fakes import FakeTutor

PORT = 8001
WEB_ORIGIN = "http://localhost:3100"  # the website the browser test starts


class E2ETutor(FakeTutor):
    """The fake tutor, with prompts that share no words, so none is dropped as a repeat of
    another and each concept keeps its whole batch (a short question, then multiple choice)."""

    def generate_questions(self, *args: Any, **kwargs: Any):
        call = self.generate_attempts + 1
        self.prompts = [
            "Explain " + " ".join(f"topic{call}q{q}w{w}" for w in range(7)) for q in range(5)
        ]
        return super().generate_questions(*args, **kwargs)


def main() -> None:
    base = make_services()
    # Every concept counts as learned, so a session goes straight to its questions, as this
    # browser test expects; the learn phase gets its own browser test.
    base.store.is_learned = lambda user_id, concept_id: True  # type: ignore[method-assign]
    tutor, saver = E2ETutor(rng=random.Random(7)), InMemorySaver()
    graph = build_graph(tutor, base.store, saver, now=lambda: NOW)
    sessions = StudySessions(base.store, graph, saver, lambda: NOW)
    services = replace(base, tutor=tutor, sessions=sessions)
    services.settings.ebb_web_origin = WEB_ORIGIN
    uvicorn.run(create_app(services), host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
