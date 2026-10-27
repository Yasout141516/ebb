"""Onboarding drafts and adding sources over HTTP: each file's progress arrives as SSE."""

import asyncio
import json
from datetime import timedelta
from uuid import UUID

import psycopg
from starlette.datastructures import UploadFile

from ebb import notes
from ebb.api.app import create_app
from ebb.api.drafts import DRAFT_TTL, Drafts
from ebb.db.pool import DATABASE_DOWN
from ebb.units import format_megabytes
from tests.api_support import NOW, USER, client, make_services
from tests.fakes import FakeTutor, new_document

OTHER = UUID("00000000-0000-0000-0000-000000000002")

EXPIRED = {"error": "This upload expired — add the files again."}


def _text(topic: str) -> bytes:
    return f"{topic} notes. {topic} are explained here in a few plain sentences.".encode()


def _upload(name: str, data: bytes) -> dict:
    return {"file": (name, data, "application/octet-stream")}


def _events(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _parse(response.text)


def _parse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        event, data = block.split("\n")
        assert event == "event: file"
        events.append(json.loads(data.removeprefix("data: ")))
    return events


def test_draft_over_http_from_name_to_subject():
    tutor = FakeTutor(note_concepts=[[("Cells", None)], [("Genes", None)]])
    services = make_services(tutor=tutor)
    web = client(services)

    created = web.post("/drafts", json={"name": "  Biology "})
    assert created.status_code == 201
    draft = created.json()["id"]
    first = _events(web.post(f"/drafts/{draft}/files", files=_upload("a.txt", _text("Cells"))))
    second = _events(web.post(f"/drafts/{draft}/files", files=_upload("b.txt", _text("Genes"))))
    assert [e["status"] for e in first] == ["reading", "finding", "ready"]
    assert services.store.list_subjects(USER) == []  # nothing saved before the commit

    removed = web.delete(f"/drafts/{draft}/files/{second[-1]['file_id']}")
    assert removed.status_code == 204
    committed = web.post(f"/drafts/{draft}/commit")

    assert committed.status_code == 201
    subject_id = committed.json()["subject_id"]
    [subject] = services.store.list_subjects(USER)
    assert str(subject.id) == subject_id and subject.name == "Biology"
    assert [c.name for c in services.store.list_concepts(USER, subject.id)] == ["Cells"]
    sources = web.get(f"/subjects/{subject_id}/sources").json()
    assert [s["filename"] for s in sources] == ["a.txt"]


def test_draft_name_taken_is_409_with_copy():
    services = make_services()
    services.store.create_subject(USER, "Biology", [])
    web = client(services)

    response = web.post("/drafts", json={"name": "biology"})

    assert response.status_code == 409
    assert response.json() == {
        "error": "Biology already exists — add notes to it from its Sources list."
    }
    assert web.post("/drafts", json={"name": "   "}).status_code == 422


def test_commit_after_the_name_was_taken_meanwhile_is_409():
    services = make_services(tutor=FakeTutor(note_concepts=[[("Cells", None)]]))
    web = client(services)
    draft = web.post("/drafts", json={"name": "Biology"}).json()["id"]
    _events(web.post(f"/drafts/{draft}/files", files=_upload("a.txt", _text("Cells"))))
    services.store.create_subject(USER, "Biology", [])  # the CLI got there first

    response = web.post(f"/drafts/{draft}/commit")

    assert response.status_code == 409
    assert response.json() == {
        "error": "Biology already exists — add notes to it from its Sources list."
    }


def test_commit_with_no_ready_file_is_422():
    web = client(make_services())
    draft = web.post("/drafts", json={"name": "Biology"}).json()["id"]
    response = web.post(f"/drafts/{draft}/commit")
    assert response.status_code == 422
    assert response.json() == {"error": "Add a file first."}


def test_expired_draft_is_410():
    clock = [NOW]
    services = make_services()
    services.drafts = Drafts(lambda: services.tutor, lambda: clock[0])
    web = client(services)
    draft = web.post("/drafts", json={"name": "Biology"}).json()["id"]
    clock[0] += DRAFT_TTL + timedelta(seconds=1)

    upload = web.post(f"/drafts/{draft}/files", files=_upload("a.txt", _text("Cells")))
    assert (upload.status_code, upload.json()) == (410, EXPIRED)
    removed = web.delete(f"/drafts/{draft}/files/whatever")
    assert (removed.status_code, removed.json()) == (410, EXPIRED)
    committed = web.post(f"/drafts/{draft}/commit")
    assert (committed.status_code, committed.json()) == (410, EXPIRED)
    unknown = web.post("/drafts/no-such-draft/commit")
    assert (unknown.status_code, unknown.json()) == (410, EXPIRED)


def test_upload_without_a_tutor_is_503_before_streaming():
    services = make_services(tutor=None, config_error="GROQ_API_KEY is not set.")
    web = client(services)
    draft = web.post("/drafts", json={"name": "Biology"}).json()["id"]

    response = web.post(f"/drafts/{draft}/files", files=_upload("a.txt", _text("Cells")))

    assert (response.status_code, response.json()) == (503, {"error": "GROQ_API_KEY is not set."})


def test_add_source_saves_and_reports():
    tutor = FakeTutor(note_concepts=[[("Cells", "Cells"), ("Genes", None)]])
    services = make_services(tutor=tutor)
    subject_id = services.store.create_subject(USER, "Biology", [("Cells", "Units of life.")])
    data = _text("Genes")

    events = _events(
        client(services).post(f"/subjects/{subject_id}/sources", files=_upload("g.txt", data))
    )

    assert events == [
        {"status": "reading"},
        {"status": "finding"},
        {
            "status": "saved",
            "detail": format_megabytes(len(data)),
            "matched": ["Cells"],
            "fresh": [{"name": "Genes", "description": "About Genes."}],
        },
    ]
    names = [c.name for c in services.store.list_concepts(USER, subject_id)]
    assert names == ["Cells", "Genes"]
    assert [d.filename for d in services.store.list_documents(USER, subject_id)] == ["g.txt"]


def test_add_source_refuses_sixth_file_with_copy():
    tutor = FakeTutor()
    services = make_services(tutor=tutor)
    subject_id = services.store.create_subject(USER, "Biology", [])
    for n in range(5):
        services.store.add_document(USER, subject_id, new_document(f"{n}.txt", "x"), [])
    web = client(services)

    events = _events(
        web.post(f"/subjects/{subject_id}/sources", files=_upload("a.txt", _text("Cells")))
    )

    assert events[-1] == {
        "status": "refused",
        "message": "Biology already has 5 of 5 files.",
        "retry": False,
    }
    assert tutor.concepts_calls == []


def test_add_source_refuses_a_file_it_already_has():
    services = make_services()
    subject_id = services.store.create_subject(USER, "Biology", [])
    web = client(services)
    _events(web.post(f"/subjects/{subject_id}/sources", files=_upload("a.txt", _text("Cells"))))

    events = _events(
        web.post(f"/subjects/{subject_id}/sources", files=_upload("again.txt", _text("Cells")))
    )

    assert events[-1] == {
        "status": "refused",
        "message": "again.txt is already in Biology.",
        "retry": False,
    }


def test_add_source_to_someone_elses_subject_is_404():
    services = make_services()
    services.store.ensure_profile(OTHER)
    other = services.store.create_subject(OTHER, "Theirs", [])

    response = client(services).post(
        f"/subjects/{other}/sources", files=_upload("a.txt", _text("Cells"))
    )

    assert response.status_code == 404


def _too_big(name: str, megabytes: str) -> dict:
    return {
        "status": "refused",
        "message": f"{name} is {megabytes} MB — over the 20 MB limit. "
        "Split it into chapters, or upload just the pages you're studying.",
        "retry": False,
    }


def _post_raw(app, path: str, query: bytes, length: int) -> tuple[list[dict], int]:
    """POST a body of `length` bytes straight to the ASGI app, handing it out in 1 MB pieces
    only when asked; returns the events sent back and how many body bytes the app pulled."""
    pulled = 0
    piece = b"x" * (1024 * 1024)
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal pulled
        more = pulled + len(piece) < length
        pulled += len(piece)
        return {"type": "http.request", "body": piece, "more_body": more}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"multipart/form-data; boundary=edge"),
            (b"content-length", str(length).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 200
    text = b"".join(m.get("body", b"") for m in sent[1:]).decode()
    return _parse(text), pulled


def test_upload_over_20_mb_is_refused_without_reading_it_all():
    tutor = FakeTutor()
    services = make_services(tutor=tutor)
    app = create_app(services)
    draft = services.drafts.create("Biology", USER)
    subject_id = services.store.create_subject(USER, "Chemistry", [])
    length = 25 * 1024 * 1024

    for path in (f"/drafts/{draft}/files", f"/subjects/{subject_id}/sources"):
        events, pulled = _post_raw(app, path, b"name=big.pdf", length)
        assert events == [{"status": "reading"}, _too_big("big.pdf", "25.0")]
        assert pulled == 0  # refused from Content-Length, before the body was parsed
    events, pulled = _post_raw(app, f"/drafts/{draft}/files", b"", length)
    assert events == [{"status": "reading"}, _too_big("That file", "25.0")]
    assert tutor.concepts_calls == []
    assert services.store.list_documents(USER, subject_id) == []


def test_upload_just_over_the_limit_is_read_no_further_than_the_limit(monkeypatch):
    read_sizes: list[int] = []
    real_read = UploadFile.read

    async def counting_read(self, size: int = -1) -> bytes:
        chunk = await real_read(self, size)
        read_sizes.append(len(chunk))
        return chunk

    monkeypatch.setattr(UploadFile, "read", counting_read)
    tutor = FakeTutor()
    services = make_services(tutor=tutor)
    web = client(services)
    draft = web.post("/drafts", json={"name": "Biology"}).json()["id"]
    subject_id = services.store.create_subject(USER, "Chemistry", [])
    big = b"x" * (notes.MAX_BYTES + 1000)  # within the multipart allowance

    for path in (f"/drafts/{draft}/files", f"/subjects/{subject_id}/sources"):
        read_sizes.clear()
        events = _events(web.post(path, files=_upload("big.txt", big)))
        assert events == [{"status": "reading"}, _too_big("big.txt", "20.0")]
        assert sum(read_sizes) == notes.MAX_BYTES + 1
        assert max(read_sizes) <= 1024 * 1024
    assert tutor.concepts_calls == []
    assert services.store.list_documents(USER, subject_id) == []


def test_upload_without_a_file_field_is_422():
    services = make_services()
    subject_id = services.store.create_subject(USER, "Chemistry", [])
    response = client(services).post(f"/subjects/{subject_id}/sources", data={"other": "x"})
    assert response.status_code == 422


def test_database_down_mid_stream_ends_the_row_with_the_notice(monkeypatch):
    services = make_services()
    subject_id = services.store.create_subject(USER, "Biology", [])

    def down(*args, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(services.store, "count_documents", down)
    events = _events(
        client(services).post(f"/subjects/{subject_id}/sources", files=_upload("a.txt", _text("x")))
    )

    assert events == [
        {"status": "reading"},
        {"status": "refused", "message": DATABASE_DOWN, "retry": True},
    ]


def test_unexpected_error_mid_stream_still_ends_the_row(monkeypatch):
    services = make_services()
    subject_id = services.store.create_subject(USER, "Biology", [])

    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(services.store, "add_document", broken)
    events = _events(
        client(services).post(f"/subjects/{subject_id}/sources", files=_upload("a.txt", _text("x")))
    )

    assert events[-1] == {
        "status": "refused",
        "message": "Something went wrong — try again.",
        "retry": True,
    }
