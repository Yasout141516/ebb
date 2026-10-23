"""Uploading notes: onboarding drafts, and adding a source to a subject.

Each upload answers with Server-Sent Events, `event: file`, one per step of that file:
`reading`, `finding`, then `ready` (a draft) or `saved` (a subject), or `refused`.
"""

import json
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

from fastapi import APIRouter, Request, UploadFile
from fastapi.sse import EventSourceResponse, format_sse_event
from starlette.concurrency import run_in_threadpool

from ebb import notes
from ebb.api.drafts import Event, Say, refused, run_detached, tutor_refusal
from ebb.api.routes_subjects import _owned, _services
from ebb.api.schemas import CommittedDraft, CreatedDraft, NewDraft
from ebb.api.services import Services
from ebb.config import ConfigError
from ebb.domain import NewDocument, Subject
from ebb.llm.tutor import TutorUnavailable
from ebb.store import MAX_DOCUMENTS, DocumentRefused
from ebb.subjects import add_notes, ensure_name_free, ensure_room
from ebb.ui import pages_prefix
from ebb.units import format_megabytes

router = APIRouter()

READ_CHUNK = 1024 * 1024
READING: Event = {"status": "reading"}


def _stream(events: Iterator[Event]) -> EventSourceResponse:
    """Each event as `event: file`. The events are pulled off the event loop; a reader
    that goes away only stops pulling (the work behind them carries on)."""

    async def body() -> AsyncIterator[bytes]:
        while (event := await run_in_threadpool(next, events, None)) is not None:
            yield format_sse_event(event="file", data_str=json.dumps(event))

    return EventSourceResponse(body(), headers={"Cache-Control": "no-cache"})


async def _read_upload(file: UploadFile) -> bytes:
    """At most MAX_BYTES + 1 bytes: one byte past the limit is enough to refuse the file."""
    data = bytearray()
    while len(data) <= notes.MAX_BYTES:
        chunk = await file.read(min(READ_CHUNK, notes.MAX_BYTES + 1 - len(data)))
        if not chunk:
            break
        data += chunk
    return bytes(data)


async def _upload(file: UploadFile) -> tuple[str, bytes] | EventSourceResponse:
    """The file's name and bytes, or the stream refusing it when its type or size is wrong
    (a file over the limit is refused with the size the client sent, unread past the limit)."""
    filename = file.filename or ""
    data = await _read_upload(file)
    try:
        notes.check_upload(filename, max(file.size or 0, len(data)))
    except notes.NotesError as error:
        return _stream(iter([READING, refused(str(error))]))
    return filename, data


@router.post("/drafts", status_code=201)
def create_draft(body: NewDraft, request: Request) -> CreatedDraft:
    services = _services(request)
    name = ensure_name_free(services.store, services.user_id, body.name)
    return CreatedDraft(id=services.drafts.create(name, services.user_id))


@router.post("/drafts/{draft_id}/files")
async def add_draft_file(draft_id: str, file: UploadFile, request: Request) -> EventSourceResponse:
    services = _services(request)
    services.require_tutor()
    upload = await _upload(file)
    if isinstance(upload, EventSourceResponse):
        return upload
    filename, data = upload
    return _stream(services.drafts.add_file(draft_id, filename, data, services.user_id))


@router.delete("/drafts/{draft_id}/files/{file_id}", status_code=204)
def remove_draft_file(draft_id: str, file_id: str, request: Request) -> None:
    services = _services(request)
    services.drafts.remove_file(draft_id, file_id, services.user_id)


@router.post("/drafts/{draft_id}/commit", status_code=201)
def commit_draft(draft_id: str, request: Request) -> CommittedDraft:
    services = _services(request)
    subject_id = services.drafts.commit(draft_id, services.store, services.user_id)
    return CommittedDraft(subject_id=subject_id)


@router.post("/subjects/{subject_id}/sources")
async def add_source(subject_id: UUID, file: UploadFile, request: Request) -> EventSourceResponse:
    services = _services(request)
    subject = await run_in_threadpool(_owned, services, subject_id)
    services.require_tutor()
    upload = await _upload(file)
    if isinstance(upload, EventSourceResponse):
        return upload
    filename, data = upload
    return _stream(run_detached(lambda say: _save_source(services, subject, filename, data, say)))


def _save_source(
    services: Services, subject: Subject, filename: str, data: bytes, say: Say
) -> None:
    """What `ebb add` does: check, find the concepts, save the file with them."""
    say(READING)
    try:
        document = notes.prepare_bytes(filename, data)
        ensure_room(services.store, services.user_id, subject, document)
    except notes.NotesError as error:
        say(refused(str(error)))
        return
    except DocumentRefused as error:
        say(refused(_room_copy(error, subject, filename)))
        return
    say({"status": "finding"})
    try:
        report = add_notes(
            services.require_tutor(), services.store, services.user_id, subject, document
        )
    except DocumentRefused as error:  # another upload got there first
        say(refused(_room_copy(error, subject, filename)))
        return
    except (TutorUnavailable, ConfigError) as error:
        say(tutor_refusal(error))
        return
    say(
        {
            "status": "saved",
            "detail": _detail(document),
            "matched": report.matched,
            "fresh": [{"name": n, "description": d} for n, d in report.fresh],
        }
    )


def _room_copy(error: DocumentRefused, subject: Subject, filename: str) -> str:
    """The CLI's copy for a full subject or a file it already has."""
    if error.reason == "full":
        return f"{subject.name} already has {MAX_DOCUMENTS} of {MAX_DOCUMENTS} files."
    return f"{filename} is already in {subject.name}."


def _detail(document: NewDocument) -> str:
    return f"{pages_prefix(document.page_count)}{format_megabytes(document.size_bytes)}"
