"""Onboarding drafts: a new subject's files, read and checked one at a time and held in
memory until the learner commits them as one subject. Nothing is saved before the commit.

Each file is processed on its own thread, so it finishes even if nobody reads its events
(a closed browser tab leaves the row's result in the draft), and closing the event iterator
never runs any of the work.
"""

import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ebb.config import ConfigError
from ebb.domain import NewDocument
from ebb.llm.tutor import NOTES_REFUSED_NOTICE, Tutor, TutorRefused, TutorUnavailable
from ebb.notes import NotesError, prepare_bytes
from ebb.store import MAX_DOCUMENTS, Store
from ebb.subjects import find_note_concepts

DRAFT_TTL = timedelta(hours=1)
EXPIRED = "This upload expired — add the files again."

type Event = dict[str, Any]
type Say = Callable[[Event], None]


class DraftExpired(Exception):
    """The draft is unknown, unused for DRAFT_TTL, already committed, or not the user's."""


class EmptyDraft(Exception):
    """A commit was asked of a draft with no ready file."""


def refused(message: str, retry: bool = False) -> Event:
    """A file's refusal; `retry` is true when sending the same file again may work."""
    return {"status": "refused", "message": message, "retry": retry}


def tutor_refusal(error: TutorUnavailable | ConfigError) -> Event:
    """A failed concept call, as the file's refusal."""
    if isinstance(error, TutorRefused):
        return refused(NOTES_REFUSED_NOTICE)
    if isinstance(error, TutorUnavailable):
        return refused(error.notice, retry=True)
    return refused(str(error))


class _Failed:
    """An unexpected error in the work, handed to whoever reads its events."""

    def __init__(self, error: Exception) -> None:
        self.error = error


def run_detached(work: Callable[[Say], None]) -> Iterator[Event]:
    """Start `work` on its own thread and return its events as they come."""
    events: queue.SimpleQueue[Event | _Failed | None] = queue.SimpleQueue()

    def run() -> None:
        try:
            work(events.put)
        except Exception as error:  # not swallowed: the reader raises it
            events.put(_Failed(error))
        finally:
            events.put(None)

    threading.Thread(target=run, name="ebb-upload", daemon=True).start()
    return _read(events)


def _read(events: queue.SimpleQueue[Event | _Failed | None]) -> Iterator[Event]:
    while (event := events.get()) is not None:
        if isinstance(event, _Failed):
            raise event.error
        yield event


@dataclass(frozen=True)
class _File:
    id: str
    document: NewDocument
    fresh: list[tuple[str, str]]  # the concepts this file added to the draft


@dataclass
class _Draft:
    name: str
    user_id: UUID
    used_at: datetime
    lock: threading.Lock = field(default_factory=threading.Lock)
    files: dict[str, _File] = field(default_factory=dict)  # ready files, in arrival order
    last_done: threading.Event = field(default_factory=threading.Event)
    closed: bool = False

    def __post_init__(self) -> None:
        self.last_done.set()  # no file before the first one


class Drafts:
    """Every draft in this process. Files of one draft are processed one at a time, in the
    order they arrived, so each concept call sees the concepts of the files before it."""

    def __init__(self, tutor_of: Callable[[], Tutor], now: Callable[[], datetime]) -> None:
        self._tutor_of = tutor_of
        self._now = now
        self._lock = threading.Lock()
        self._drafts: dict[str, _Draft] = {}

    def create(self, name: str, user_id: UUID) -> str:
        """A new empty draft for a subject called `name` (checked free by the caller)."""
        draft_id = str(uuid4())
        with self._lock:
            self._expire()
            self._drafts[draft_id] = _Draft(name.strip(), user_id, self._now())
        return draft_id

    def add_file(self, draft_id: str, filename: str, data: bytes, user_id: UUID) -> Iterator[Event]:
        """Read and check the file, then find its concepts. Raises DraftExpired at once;
        the rest arrives as events: reading, finding, then ready or refused."""
        with self._lock:
            draft = self._find(draft_id, user_id)
            turn, done = draft.last_done, threading.Event()
            draft.last_done = done

        def work(say: Say) -> None:
            try:
                self._process(draft, filename, data, turn, say)
            finally:
                done.set()

        return run_detached(work)

    def remove_file(self, draft_id: str, file_id: str, user_id: UUID) -> None:
        """Drop a ready file and the concepts it added. Raises LookupError if it isn't there."""
        with self._lock:
            draft = self._find(draft_id, user_id)
        with draft.lock:
            if draft.files.pop(file_id, None) is None:
                raise LookupError(f"file {file_id} not in draft")

    def commit(self, draft_id: str, store: Store, user_id: UUID) -> UUID:
        """Save the subject with every ready file and their concepts in one transaction.
        Raises SubjectExists if the name was taken meanwhile; the draft then stays."""
        with self._lock:
            draft = self._find(draft_id, user_id)
        with draft.lock:
            if draft.closed:
                raise DraftExpired(draft_id)
            files = list(draft.files.values())
            if not files:
                raise EmptyDraft(draft_id)
            subject_id = store.create_subject_with_notes(
                user_id,
                draft.name,
                [f.document for f in files],
                [concept for f in files for concept in f.fresh],
            )
            draft.closed = True
        with self._lock:
            self._drafts.pop(draft_id, None)
        return subject_id

    def _find(self, draft_id: str, user_id: UUID) -> _Draft:
        """The draft, renewed; the caller holds self._lock."""
        self._expire()
        draft = self._drafts.get(draft_id)
        if draft is None or draft.user_id != user_id:
            raise DraftExpired(draft_id)
        draft.used_at = self._now()
        return draft

    def _expire(self) -> None:
        cutoff = self._now() - DRAFT_TTL
        for draft_id, draft in list(self._drafts.items()):
            if draft.used_at < cutoff:
                draft.closed = True
                del self._drafts[draft_id]

    def _process(
        self, draft: _Draft, filename: str, data: bytes, turn: threading.Event, say: Say
    ) -> None:
        say({"status": "reading"})
        try:
            document = prepare_bytes(filename, data)
        except NotesError as error:
            say(refused(str(error)))
            return
        turn.wait()  # the files before this one are done
        with draft.lock:
            refusal = self._check(draft, document)
            known = [concept for f in draft.files.values() for concept in f.fresh]
        if refusal is not None:
            say(refused(refusal))
            return
        say({"status": "finding"})
        try:
            report = find_note_concepts(self._tutor_of(), draft.name, known, document)
        except (TutorUnavailable, ConfigError) as error:
            say(tutor_refusal(error))
            return
        ready = _File(str(uuid4()), document, report.fresh)
        with draft.lock:
            if draft.closed:
                say(refused(EXPIRED))
                return
            draft.files[ready.id] = ready
        say(
            {
                "status": "ready",
                "file_id": ready.id,
                "filename": document.filename,
                "page_count": document.page_count,
                "passages": len(document.chunks),
                "concepts": [name for name, _ in report.fresh],
            }
        )

    @staticmethod
    def _check(draft: _Draft, document: NewDocument) -> str | None:
        """Why the file can't join the draft, or None; the caller holds draft.lock."""
        if draft.closed:
            return EXPIRED
        if len(draft.files) >= MAX_DOCUMENTS:
            return f"{draft.name} can have at most {MAX_DOCUMENTS} files."
        if any(f.document.sha256 == document.sha256 for f in draft.files.values()):
            return f"{document.filename} is already in {draft.name}."
        return None
