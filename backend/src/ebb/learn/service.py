"""Driving the learn graph: a concept's lesson and the learner's questions about it, as
events for the API.

Each concept has one learn thread per user, run from one place at a time: a run holds the
thread's advisory lock, and a second driver gets SessionBusy. The thread is deleted once
the concept is learned; a lesson whose thread is gone is rebuilt from the store without a
model call.
"""

from collections.abc import Generator, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from ebb.learn.graph import LearnState, first_state, read_back
from ebb.llm.request import in_request
from ebb.llm.tutor import TutorUnavailable
from ebb.store import Store
from ebb.study import SessionBusy, Step


class LearnNotStarted(LookupError):
    """A reply to a learn phase whose lesson hasn't been written yet."""


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


_KINDS = ("check", "suggested", "typed")


def _check(reply: Mapping[str, Any]) -> None:
    """Raise ValueError unless `reply` is {ready: True} or {ask, kind, ask_id}."""
    if reply.keys() == {"ready"} and reply["ready"] is True:
        return
    if (
        reply.keys() == {"ask", "kind", "ask_id"}
        and isinstance(reply["ask"], str)
        and reply["ask"].strip()
        and reply["kind"] in _KINDS
        and isinstance(reply["ask_id"], str)
        and reply["ask_id"]
    ):
        return
    raise ValueError("a learn reply is {ready: true} or {ask, kind, ask_id}")


class LearnSessions:
    """Every call is scoped by user_id. `start` and `reply` raise at once, before any event:
    LookupError for a concept that isn't the user's, ValueError for a malformed reply, and
    LearnNotStarted for a reply before the lesson is kept. Their event iterators are lazy,
    and SessionBusy is raised when one is first advanced.

    Each call yields its steps and text, then exactly one LearnTurn or Learned, last: the
    end of the turn. A model call that fails raises its TutorUnavailable instead, after
    Discard if some of its text was streamed, and nothing of it is kept. A failed lesson is
    written again by the next start; a failed answer only by a reply with the same ask_id.
    Anything else drops that question, unanswered and without a model call."""

    def __init__(self, store: Store, graph: CompiledStateGraph, saver: BaseCheckpointSaver) -> None:
        self.store, self.graph, self.saver = store, graph, saver

    def start(self, user_id: UUID, concept_id: UUID) -> Iterator[LearnEvent]:
        """The learn phase up to its waiting turn: the lesson written (or read back), a
        failed lesson written again, or the waiting turn shown again. Learned at once if
        it is."""
        first = self._first_if_new(user_id, concept_id)
        return self._drive(user_id, concept_id, first, None)

    def reply(
        self, user_id: UUID, concept_id: UUID, reply: Mapping[str, Any]
    ) -> Iterator[LearnEvent]:
        """Send {ask, kind, ask_id} or {ready: True}. A question whose ask_id was answered
        already is not asked again; the current turn comes back instead."""
        _check(reply)
        first = self._first_if_new(user_id, concept_id)
        if self.store.get_lesson(user_id, concept_id) is None:
            raise LearnNotStarted(f"concept {concept_id} has no lesson yet")
        return self._drive(user_id, concept_id, first, reply)

    def _first_if_new(self, user_id: UUID, concept_id: UUID) -> LearnState | None:
        """The state a new thread starts from, or None if the thread exists (it is the
        user's: their id is in the thread id)."""
        if self.graph.get_state(_config(user_id, concept_id)).values:
            return None
        return self._first_state(user_id, concept_id)

    def _first_state(self, user_id: UUID, concept_id: UUID) -> LearnState:
        concept = self.store.get_concept(user_id, concept_id)
        subject = None if concept is None else self.store.get_subject(user_id, concept.subject_id)
        if concept is None or subject is None:
            raise LookupError(f"concept {concept_id} not found")
        has_notes = self.store.count_documents(user_id, subject.id) > 0
        return first_state(user_id, concept, subject.name, has_notes)

    def _drive(
        self,
        user_id: UUID,
        concept_id: UUID,
        first: LearnState | None,
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
        first: LearnState | None,
        reply: Mapping[str, Any] | None,
    ) -> _Run:
        """Bring the thread to its waiting turn (or to Learned), then apply `reply`. Only
        the last run's turn is returned: one before it streams nothing (a kept lesson read
        back, a dropped question, or a retried answer that `reply` then finds applied)."""
        saved = self.graph.get_state(_config(user_id, concept_id))
        if saved.interrupts:
            last: LearnTurn | Learned = LearnTurn(saved.interrupts[0].value)
        elif saved.next or saved.tasks:
            # A turn stopped mid-step: a call failed, or the process died. A lesson is
            # written again; an answer only when `reply` retries its question.
            asking = saved.values.get("asking")
            if asking is not None and (reply is None or reply.get("ask_id") != asking["ask_id"]):
                self._drop_question(user_id, concept_id)
            last = yield from self._stream(user_id, concept_id, None)
        elif saved.values:
            last = self._learned(user_id, concept_id)  # learned, but the thread outlived it
        elif self.store.is_learned(user_id, concept_id):
            return Learned(concept_id)
        else:
            # New, or its thread is gone: a kept lesson is read back, not written again.
            first = first or self._first_state(user_id, concept_id)
            last = yield from self._stream(user_id, concept_id, first)
        if reply is None or isinstance(last, Learned) or self._applied(user_id, concept_id, reply):
            return last
        return (yield from self._stream(user_id, concept_id, Command(resume=dict(reply))))

    def _drop_question(self, user_id: UUID, concept_id: UUID) -> None:
        """Take the thread back to its wait, leaving the question it was answering
        unanswered; its exchanges are re-read, in case the answer was kept after all."""
        kept = read_back(self.store, user_id, concept_id)
        update = {"asking": None, "sources": [], "notices": [], **kept}
        self.graph.update_state(_config(user_id, concept_id), update, as_node="wait")

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
