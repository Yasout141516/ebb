"""Onboarding drafts and adding sources over HTTP: each file's progress arrives as SSE."""

import json
from datetime import timedelta
from uuid import UUID

from starlette.datastructures import UploadFile

from ebb import notes
from ebb.api.drafts import DRAFT_TTL, Drafts
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
    events = []
    for block in response.text.strip().split("\n\n"):
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


def test_upload_over_20_mb_is_refused_without_reading_it_all(monkeypatch):
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
    big = b"x" * (21 * 1024 * 1024)
    refusal = {
        "status": "refused",
        "message": "big.txt is 21.0 MB — over the 20 MB limit. "
        "Split it into chapters, or upload just the pages you're studying.",
        "retry": False,
    }

    for path in (f"/drafts/{draft}/files", f"/subjects/{subject_id}/sources"):
        read_sizes.clear()
        events = _events(web.post(path, files=_upload("big.txt", big)))
        assert events == [{"status": "reading"}, refusal]
        assert sum(read_sizes) == notes.MAX_BYTES + 1
        assert max(read_sizes) <= 1024 * 1024
    assert tutor.concepts_calls == []
    assert services.store.list_documents(USER, subject_id) == []
