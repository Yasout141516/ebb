"""Starting, resuming and driving quiz sessions: one service for the CLI and the API.

A session is driven from one place at a time. A browser tab holds a lease on it, renewed by
its turns and heartbeats, and each graph run holds the thread's advisory lock; the CLI holds
that lock for its whole run instead (`hold`).
"""

from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from ebb.graph.state import fill_defaults
from ebb.mastery import reviews_due
from ebb.store import Store
from ebb.ui import greeting

LEASE = timedelta(seconds=90)
BUSY = "That session is open in another tab or terminal."


class SessionBusy(Exception):
    """Another tab or terminal is driving the session."""


class SessionNotFound(LookupError):
    """The user has no open session by that thread id (or nothing in it to carry on with)."""


@dataclass(frozen=True)
class Offer:
    """An open session to resume: where it stopped, and whether someone else is on it."""

    thread_id: str
    concept: str
    number: int
    last_active_at: datetime
    busy: bool


@dataclass(frozen=True)
class Step:
    label: str


@dataclass(frozen=True)
class Pending:
    payload: dict[str, Any]  # the question waiting for a reply, as ask_question shows it


@dataclass(frozen=True)
class Finished:
    subject_id: UUID
    results: list[dict[str, Any]]
    final: dict[str, Any]  # the last turn's grade, lesson and notices
    caught_up: bool
    next_review_at: datetime | None


Event = Step | Pending | Finished
# Yields a run's steps, then returns where it stopped.
type _Run = Generator[Step, None, Pending | Finished]


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _cut_off(saved: StateSnapshot) -> bool:
    """Whether the session stopped mid-turn: a step was due, or one ran but the checkpoint
    after it was never written (its writes are saved, so carrying on applies them)."""
    return bool(saved.next or saved.tasks)


def _stopped_on(saved: StateSnapshot) -> tuple[str, int] | None:
    """The concept and question number a session stopped on; None if there is nothing to
    carry on with."""
    if saved.interrupts:
        payload = saved.interrupts[0].value
        return payload["concept"], payload["number"]
    if _cut_off(saved) and saved.values.get("concept"):
        # Stopped mid-step, e.g. while grading: the answer is in the state but its step
        # never finished. The stored attempt_id makes re-running it count once.
        return saved.values["concept"]["name"], saved.values["asked_on_concept"] + 1
    return None


class StudySessions:
    """Every call is scoped by user_id. The event iterators are lazy: SessionBusy and
    SessionNotFound are raised when one is first advanced."""

    def __init__(
        self,
        store: Store,
        graph: CompiledStateGraph,
        saver: BaseCheckpointSaver,
        now: Callable[[], datetime],
    ) -> None:
        self.store, self.graph, self.saver, self.now = store, graph, saver, now
        # Threads this instance holds the lock of (see `hold`), for every caller of it.
        self._held: set[str] = set()

    def housekeeping(self, user_id: UUID) -> None:
        """Close sessions idle too long to resume, and drop old checkpoints."""
        now = self.now()
        self.store.close_stale_sessions(user_id, now)
        # Learning state lives in Ebb's own tables; old checkpoints are only resume data.
        old_threads = self.store.threads_to_prune(user_id, now)
        for thread_id in old_threads:
            self.saver.delete_thread(thread_id)
        if old_threads:
            self.store.mark_pruned(user_id, old_threads, now)

    def offer(self, user_id: UUID, subject_id: UUID, driver: str) -> Offer | None:
        """The subject's resumable session, or None. One with nothing to carry on with is
        ended, unless someone else is on it."""
        now = self.now()
        found = self.store.resumable_session(user_id, subject_id, now)
        if found is None:
            return None
        leased_to = self.store.session_driver(user_id, found.thread_id, now, LEASE)
        with self.store.session_lock(user_id, found.thread_id) as free:
            busy = not free or leased_to not in (None, driver)
            stopped_on = _stopped_on(self.graph.get_state(_config(found.thread_id)))
            if stopped_on is None:
                if not busy:
                    self.store.end_session(user_id, found.session_id, now)  # answers stay
                return None
        concept, number = stopped_on
        return Offer(found.thread_id, concept, number, found.last_active_at, busy)

    def start(
        self, user_id: UUID, subject_id: UUID, driver: str
    ) -> tuple[str, str, Iterator[Event]]:
        """A new session: its thread id, the greeting, and its events up to the first
        question (or the summary). Raises LookupError if the subject isn't the user's."""
        if self.store.get_subject(user_id, subject_id) is None:
            raise LookupError(f"subject {subject_id} not found")
        concepts = self.store.list_concepts(user_id, subject_id)
        new = sum(1 for c in concepts if c.stage == 0)
        text = greeting(len(reviews_due(concepts, self.now())), new)
        thread_id = str(uuid4())
        first = {"user_id": str(user_id), "subject_id": str(subject_id), "thread_id": thread_id}
        # The session row is made by the run, so its lease is first claimed at the question.
        run = self._stream(user_id, thread_id, driver, first)
        return thread_id, text, self._drive(user_id, thread_id, driver, run, claim=False)

    def resume(self, user_id: UUID, thread_id: str, driver: str) -> Iterator[Event]:
        """The pending question at once, or the cut-off step finished first."""
        return self._carry_on(user_id, thread_id, driver, None)

    def reply(
        self, user_id: UUID, thread_id: str, driver: str, reply: Mapping[str, Any]
    ) -> Iterator[Event]:
        """Send the learner's reply: {attempt_id, answer}, {dispute: {id, reason,
        explanation}} or {quit: True}. A reply already applied is not applied again; the
        session's current turn comes back instead."""
        return self._carry_on(user_id, thread_id, driver, reply)

    def pending(
        self, user_id: UUID, thread_id: str, reply: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """The question waiting for `reply` (for any reply if None), as the last turn showed
        it; None if there is none, or if `reply` was already applied: sent again, it is not
        answering this question but gets the current turn back."""
        saved = self.graph.get_state(_config(thread_id))
        if saved.values.get("user_id") != str(user_id) or not saved.interrupts:
            return None
        if reply is not None and self._applied(user_id, saved.values, reply):
            return None
        payload: dict[str, Any] = saved.interrupts[0].value
        return payload

    def discard(self, user_id: UUID, thread_id: str, driver: str) -> None:
        """Start fresh: end the session, unless another tab or terminal is driving it or a
        turn of it is running. `driver`'s own lease doesn't keep it open."""
        found = self.store.get_session(user_id, thread_id)
        if found is None:
            return
        now = self.now()
        with self.store.session_lock(user_id, thread_id) as free:
            if free and self.store.session_driver(user_id, thread_id, now, LEASE) in (None, driver):
                self.store.end_session(user_id, found.session_id, now)

    def release(self, user_id: UUID, thread_id: str, driver: str) -> None:
        """`driver` is gone (its tab closed): free its lease, so another tab may carry on at
        once. Raises SessionNotFound if the session isn't open."""
        if self.store.get_session(user_id, thread_id) is None:
            raise SessionNotFound(thread_id)
        self.store.release_session(user_id, thread_id, driver)

    def heartbeat(self, user_id: UUID, thread_id: str, driver: str) -> bool:
        """Renew `driver`'s lease; False if someone else drives the session. Raises
        SessionNotFound if it isn't open."""
        if self.store.get_session(user_id, thread_id) is None:
            raise SessionNotFound(thread_id)
        return self.store.claim_session(user_id, thread_id, driver, self.now(), LEASE)

    @contextmanager
    def hold(self, user_id: UUID, thread_id: str) -> Iterator[bool]:
        """Hold the thread's lock for the block, as the CLI does for its whole run; yields
        False if another tab or terminal is driving it. While it is held, every call on the
        thread through this instance runs without a lease or a lock of its own, whoever
        makes it; so an instance shared by many drivers, as the API's is, must never hold."""
        with self.store.session_lock(user_id, thread_id) as free:
            leased_to = self.store.session_driver(user_id, thread_id, self.now(), LEASE)
            if not free or leased_to is not None:
                yield False
                return
            self._held.add(thread_id)
            try:
                yield True
            finally:
                self._held.discard(thread_id)

    def _carry_on(
        self, user_id: UUID, thread_id: str, driver: str, reply: Mapping[str, Any] | None
    ) -> Iterator[Event]:
        if self.store.get_session(user_id, thread_id) is None:
            # Ended: a repeated last reply gets the summary again.
            saved = self.graph.get_state(_config(thread_id))
            if saved.values.get("user_id") != str(user_id) or _cut_off(saved):
                raise SessionNotFound(thread_id)
            yield self._finished(user_id, saved.values)
            return
        run = self._next(user_id, thread_id, driver, reply)
        yield from self._drive(user_id, thread_id, driver, run)

    def _drive(
        self, user_id: UUID, thread_id: str, driver: str, run: _Run, claim: bool = True
    ) -> Iterator[Event]:
        """`run` as `driver`, under the thread's lock and then its lease (claimed unless
        `claim` is False); either taken raises SessionBusy, and a call refused the lock
        claims nothing. The last event is handed over after the lock is released, so a
        caller may stop reading there."""
        if thread_id in self._held:
            last = yield from run
        else:
            with self.store.session_lock(user_id, thread_id) as free:
                if not free or (
                    claim
                    and not self.store.claim_session(user_id, thread_id, driver, self.now(), LEASE)
                ):
                    raise SessionBusy(thread_id)
                last = yield from run
        yield last

    def _next(
        self, user_id: UUID, thread_id: str, driver: str, reply: Mapping[str, Any] | None
    ) -> _Run:
        """The one decision on what the graph gets next."""
        saved = self.graph.get_state(_config(thread_id))
        if saved.interrupts:
            payload = saved.interrupts[0].value
            if reply is None or self._applied(user_id, saved.values, reply):
                return self._pending(user_id, thread_id, driver, payload)
            graph_input: Any = Command(resume=dict(reply))
        elif _cut_off(saved):
            graph_input = None  # finish the turn that was cut off
        elif saved.values:
            return self._finished(user_id, saved.values)
        else:
            raise SessionNotFound(thread_id)
        return (yield from self._stream(user_id, thread_id, driver, graph_input))

    def _applied(self, user_id: UUID, values: Mapping[str, Any], reply: Mapping[str, Any]) -> bool:
        """Whether `reply` was already applied: resuming with it again would answer the next
        question with an old attempt id, or re-check a dispute twice."""
        if "attempt_id" in reply:
            return reply["attempt_id"] == values.get("attempt_id")
        if "dispute" in reply:
            dispute_id = reply["dispute"]["id"]
            waiting = values.get("dispute")
            return (waiting is not None and waiting["id"] == dispute_id) or (
                self.store.get_dispute(user_id, UUID(dispute_id)) is not None
            )
        return False

    def _stream(self, user_id: UUID, thread_id: str, driver: str, graph_input: Any) -> _Run:
        stream = self.graph.stream(
            graph_input, _config(thread_id), stream_mode=["custom", "values"]
        )
        values: dict[str, Any] = {}
        try:
            for mode, chunk in stream:
                if mode == "custom":
                    yield Step(chunk["label"])
                else:
                    values = chunk
        except GeneratorExit:
            # The reader went away mid-turn (a tab closed or refreshed). Finish the turn,
            # still under the lock, so the answer counts and the next question is waiting.
            for mode, chunk in stream:
                if mode == "values":
                    values = chunk
            self._stopped(user_id, thread_id, driver, values)
            raise
        return self._stopped(user_id, thread_id, driver, values)

    def _stopped(
        self, user_id: UUID, thread_id: str, driver: str, values: dict[str, Any]
    ) -> Pending | Finished:
        """Where a run stopped, from its last values."""
        if "__interrupt__" in values:
            return self._pending(user_id, thread_id, driver, values["__interrupt__"][0].value)
        return self._finished(user_id, values)

    def _pending(
        self, user_id: UUID, thread_id: str, driver: str, payload: dict[str, Any]
    ) -> Pending:
        now = self.now()
        # A question left open for hours keeps its session from being closed as idle.
        self.store.touch_session(user_id, thread_id, now)
        if thread_id not in self._held:
            # Names the driver of a new session; renews the lease of a running one.
            self.store.claim_session(user_id, thread_id, driver, now, LEASE)
        return Pending(payload)

    def _finished(self, user_id: UUID, values: Mapping[str, Any]) -> Finished:
        filled = fill_defaults(values)
        subject_id = UUID(filled["subject_id"])
        return Finished(
            subject_id=subject_id,
            results=filled.get("results", []),
            final={
                "grade": filled.get("grade"),
                "lesson": filled.get("lesson"),
                "notices": filled.get("notices", []),
            },
            caught_up=bool(filled.get("caught_up")),
            next_review_at=self.store.next_review_at(user_id, subject_id),
        )
