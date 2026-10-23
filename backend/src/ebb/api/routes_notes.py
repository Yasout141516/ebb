"""Uploading notes: onboarding drafts, and adding a source to a subject.

Each upload answers with Server-Sent Events, `event: file`, one per step of that file:
`reading`, `finding`, then `ready` (a draft) or `saved` (a subject), or `refused`.
"""

import json
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.sse import EventSourceResponse, format_sse_event
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from ebb import notes
from ebb.api.drafts import Event, Say, refused, run_detached, tutor_refusal
from ebb.api.schemas import CommittedDraft, CreatedDraft, NewDraft
from ebb.api.services import Services, owned_subject, services_of
from ebb.config import ConfigError
from ebb.domain import NewDocument, Subject
from ebb.llm.tutor import TutorUnavailable
from ebb.store import DocumentRefused
from ebb.subjects import add_notes, ensure_name_free, ensure_room, room_copy
from ebb.ui import pages_prefix
from ebb.units import format_megabytes

router = APIRouter()

READ_CHUNK = 1024 * 1024
MULTIPART_ALLOWANCE = 64 * 1024  # room for the multipart framing around an allowed file
UNNAMED = "That file"
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


def _content_length(request: Request) -> int:
    length = request.headers.get("content-length", "")
    return int(length) if length.isdigit() else 0


async def _read_form(request: Request) -> tuple[str, bytes, int]:
    """The `file` field's name, its first MAX_BYTES + 1 bytes and its full size."""
    async with request.form() as form:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            missing = {"type": "missing", "loc": ("body", "file"), "msg": "Field required"}
            raise RequestValidationError([missing])
        data = await _read_upload(file)
        return file.filename or "", data, max(file.size or 0, len(data))


async def _upload(request: Request, name: str | None) -> tuple[str, bytes] | EventSourceResponse:
    """The file's name and bytes, or the stream refusing it when its type or size is wrong.
    A request too big to hold an allowed file is refused from its Content-Length before the
    body is parsed, named by `name` (the frontend sends the file's name in the query)."""
    try:
        if (length := _content_length(request)) > notes.MAX_BYTES + MULTIPART_ALLOWANCE:
            if name:
                notes.check_upload(name, length)  # its type first, as for any file
            else:
                notes.check_size(UNNAMED, length)
        filename, data, size = await _read_form(request)
        notes.check_upload(filename, size)
    except notes.NotesError as error:
        return _stream(iter([READING, refused(str(error))]))
    return filename, data


@router.post("/drafts", status_code=201)
def create_draft(body: NewDraft, request: Request) -> CreatedDraft:
    services = services_of(request)
    name = ensure_name_free(services.store, services.user_id, body.name)
    return CreatedDraft(id=services.drafts.create(name, services.user_id))


@router.post("/drafts/{draft_id}/files")
async def add_draft_file(
    draft_id: str, request: Request, name: str | None = None
) -> EventSourceResponse:
    """Multipart field `file`."""
    services = services_of(request)
    services.require_tutor()
    upload = await _upload(request, name)
    if isinstance(upload, EventSourceResponse):
        return upload
    filename, data = upload
    return _stream(services.drafts.add_file(draft_id, filename, data, services.user_id))


@router.delete("/drafts/{draft_id}/files/{file_id}", status_code=204)
def remove_draft_file(draft_id: str, file_id: str, request: Request) -> None:
    services = services_of(request)
    services.drafts.remove_file(draft_id, file_id, services.user_id)


@router.post("/drafts/{draft_id}/commit", status_code=201)
def commit_draft(draft_id: str, request: Request) -> CommittedDraft:
    services = services_of(request)
    subject_id = services.drafts.commit(draft_id, services.store, services.user_id)
    return CommittedDraft(subject_id=subject_id)


@router.post("/subjects/{subject_id}/sources")
async def add_source(
    subject_id: UUID, request: Request, name: str | None = None
) -> EventSourceResponse:
    """Multipart field `file`."""
    services = services_of(request)
    subject = await run_in_threadpool(owned_subject, services, subject_id)
    services.require_tutor()
    upload = await _upload(request, name)
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
        say(refused(room_copy(error, subject.name, filename)))
        return
    say({"status": "finding"})
    try:
        report = add_notes(
            services.require_tutor(), services.store, services.user_id, subject, document
        )
    except DocumentRefused as error:  # another upload got there first
        say(refused(room_copy(error, subject.name, filename)))
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


def _detail(document: NewDocument) -> str:
    return f"{pages_prefix(document.page_count)}{format_megabytes(document.size_bytes)}"
