"""Driving the learn graph: a concept's lesson and the learner's questions about it, as
events for the API.

Each concept has one learn thread per user, run from one place at a time: a run holds the
thread's advisory lock, and a second driver gets SessionBusy. The thread is deleted once
the concept is learned; a lesson whose thread is gone is rebuilt from the store without a
model call.
"""

from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from ebb.domain import Concept, Subject
from ebb.learn.graph import LearnState, first_state
from ebb.llm.request import in_request
from ebb.llm.tutor import TutorUnavailable
from ebb.store import Store
from ebb.study import SessionBusy, Step


@dataclass(frozen=True)
class TextDelta:
    text: str  # the next piece of the lesson or answer being written


@dataclass(frozen=True)
class Discard:
    """Wipe the text streamed this turn: its call failed after the text was shown."""


@dataclass(frozen=True)
class LearnTurn:
    payload: dict[str, Any]  # what the learn graph's wait shows; its markdown is the kept one


@dataclass(frozen=True)
class Learned:
    concept_id: UUID


LearnEvent = Step | TextDelta | Discard | LearnTurn | Learned
# Yields a run's events, then returns where it stopped.
type _Run = Generator[LearnEvent, None, LearnTurn | Learned]


def thread_id(user_id: UUID, concept_id: UUID) -> str:
    return f"learn:{user_id}:{concept_id}"


def _config(user_id: UUID, concept_id: UUID) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id(user_id, concept_id)}}


class LearnSessions:
    """Every call is scoped by user_id. `start` and `reply` raise LookupError at once for a
    concept that isn't the user's; their event iterators are lazy, and SessionBusy is raised
    when one is first advanced. A model call that fails raises its TutorUnavailable from the
    iterator, after Discard if some of its text was streamed; nothing of it is kept, and
    calling again runs the turn again."""

    def __init__(
        self,
        store: Store,
        graph: CompiledStateGraph,
        saver: BaseCheckpointSaver,
        now: Callable[[], datetime],
    ) -> None:
        self.store, self.graph, self.saver, self.now = store, graph, saver, now

    def start(self, user_id: UUID, concept_id: UUID) -> Iterator[LearnEvent]:
        """The learn phase up to its waiting turn: the lesson written (or read back), a turn
        cut off finished, or the waiting turn shown again. Learned at once if it is."""
        first = self._first_state(user_id, concept_id)
        return self._drive(user_id, concept_id, first, None)

    def reply(
        self, user_id: UUID, concept_id: UUID, reply: Mapping[str, Any]
    ) -> Iterator[LearnEvent]:
        """Send {ask, kind, ask_id} or {ready: True}. A question whose ask_id was answered
        already is not asked again; the current turn comes back instead."""
        first = self._first_state(user_id, concept_id)
        return self._drive(user_id, concept_id, first, reply)

    def _first_state(self, user_id: UUID, concept_id: UUID) -> LearnState:
        found = self._find(user_id, concept_id)
        if found is None:
            raise LookupError(f"concept {concept_id} not found")
        subject, concept = found
        has_notes = self.store.count_documents(user_id, subject.id) > 0
        return first_state(user_id, concept, subject.name, has_notes)

    def _find(self, user_id: UUID, concept_id: UUID) -> tuple[Subject, Concept] | None:
        for subject in self.store.list_subjects(user_id):
            for concept in self.store.list_concepts(user_id, subject.id):
                if concept.id == concept_id:
                    return subject, concept
        return None

    def _drive(
        self,
        user_id: UUID,
        concept_id: UUID,
        first: LearnState,
        reply: Mapping[str, Any] | None,
    ) -> Iterator[LearnEvent]:
        """The turn under the thread's lock. The last event is handed over after the lock
        is released, so a caller may stop reading there."""
        with self.store.session_lock(user_id, thread_id(user_id, concept_id)) as free:
            if not free:
                raise SessionBusy(thread_id(user_id, concept_id))
            last = yield from self._turn(user_id, concept_id, first, reply)
        yield last

    def _turn(
        self,
        user_id: UUID,
        concept_id: UUID,
        first: LearnState,
        reply: Mapping[str, Any] | None,
    ) -> _Run:
        """Bring the thread to its waiting turn (or to Learned), then apply `reply`."""
        saved = self.graph.get_state(_config(user_id, concept_id))
        ran = True
        if saved.interrupts:
            last: LearnTurn | Learned = LearnTurn(saved.interrupts[0].value)
            ran = False
        elif saved.next or saved.tasks:
            # A turn stopped mid-step (a call failed, or the process died): finish it.
            last = yield from self._stream(user_id, concept_id, None)
        elif saved.values:
            last = self._learned(user_id, concept_id)  # learned, but the thread outlived it
        elif self.store.is_learned(user_id, concept_id):
            return Learned(concept_id)
        else:
            # New, or its thread is gone: a kept lesson is read back, not written again.
            last = yield from self._stream(user_id, concept_id, first)
        if reply is None or isinstance(last, Learned) or self._applied(user_id, concept_id, reply):
            return last
        if ran:
            yield last
        return (yield from self._stream(user_id, concept_id, Command(resume=dict(reply))))

    def _applied(self, user_id: UUID, concept_id: UUID, reply: Mapping[str, Any]) -> bool:
        """Whether `reply` is a question already answered (a double click, or a retry of
        a turn that has since finished)."""
        if "ask_id" not in reply:
            return False
        values = self.graph.get_state(_config(user_id, concept_id)).values
        return reply["ask_id"] in values.get("answered", [])

    def _stream(self, user_id: UUID, concept_id: UUID, graph_input: Any) -> _Run:
        """The graph's run on `graph_input`, as steps and text, ending where it stopped. All
        its model calls share one request id."""
        stream = in_request(
            user_id,
            self.graph.stream(
                graph_input, _config(user_id, concept_id), stream_mode=["custom", "values"]
            ),
        )
        values: dict[str, Any] = {}
        shown = False  # whether this run has handed the reader any text
        try:
            for mode, chunk in stream:
                if mode == "values":
                    values = chunk
                elif "text" in chunk:
                    shown = True
                    yield TextDelta(chunk["text"])
                else:
                    yield Step(chunk["label"])
        except GeneratorExit:
            # The reader went away mid-turn (a tab closed or refreshed). Finish the turn,
            # still under the lock, so the lesson or answer is kept for the next visit. A
            # call that fails now leaves the turn cut off, to be run again then.
            with suppress(TutorUnavailable):
                for _ in stream:
                    pass
            raise
        except TutorUnavailable:
            # Decided by what this run streamed, not by the error: a call can fail with its
            # text held back from the screen, and then there is nothing to wipe.
            if shown:
                yield Discard()
            raise
        if "__interrupt__" in values:
            return LearnTurn(values["__interrupt__"][0].value)
        return self._learned(user_id, concept_id)

    def _learned(self, user_id: UUID, concept_id: UUID) -> Learned:
        """The thread reached its end: the concept is learned, and the thread is dropped."""
        self.saver.delete_thread(thread_id(user_id, concept_id))
        return Learned(concept_id)
