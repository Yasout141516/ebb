"""Onboarding drafts: files read and checked one at a time, nothing saved until commit."""

from datetime import timedelta
from uuid import UUID

import psycopg
import pytest

from ebb.api.drafts import DRAFT_TTL, DraftExpired, Drafts
from ebb.db.repo import PgStore
from ebb.llm.quota import UserQuotaExceeded
from tests.api_support import NOW, USER
from tests.fakes import FakeTutor, MemoryStore
from tests.pdfs import build_pdf

OTHER = UUID("00000000-0000-0000-0000-000000000002")


def _text(topic: str) -> bytes:
    return f"{topic} notes. {topic} are explained here in a few plain sentences.".encode()


def _drafts(tutor: FakeTutor, clock=lambda: NOW) -> Drafts:
    return Drafts(lambda: tutor, clock)


def _store() -> MemoryStore:
    store = MemoryStore()
    store.ensure_profile(USER)
    return store


def test_draft_files_see_earlier_concepts():
    tutor = FakeTutor(
        note_concepts=[[("Cells", None), ("Genes", None)], [("Cells", "Cells"), ("Proteins", None)]]
    )
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)

    first = list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))
    second = list(drafts.add_file(draft, "b.txt", _text("Proteins"), USER))

    assert [e["status"] for e in first] == ["reading", "finding", "ready"]
    assert first[-1]["concepts"] == ["Cells", "Genes"]
    assert first[-1]["new"] == ["Cells", "Genes"]
    assert first[-1]["filename"] == "a.txt" and first[-1]["page_count"] is None
    assert first[-1]["passages"] >= 1
    assert tutor.concepts_calls[1][0] == "Biology"
    assert tutor.concepts_calls[1][1] == ["Cells", "Genes"]  # the second call sees the first
    assert second[-1]["concepts"] == ["Cells", "Proteins"]  # all it found, matched first
    assert second[-1]["new"] == ["Proteins"]

    store = _store()
    subject_id = drafts.commit(draft, store, USER)
    names = [c.name for c in store.list_concepts(USER, subject_id)]
    assert names == ["Cells", "Genes", "Proteins"]
    assert [d.filename for d in store.list_documents(USER, subject_id)] == ["a.txt", "b.txt"]


def test_draft_refuses_a_scanned_pdf_on_its_row(tmp_path):
    tutor = FakeTutor()
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)
    data = build_pdf(tmp_path / "scan.pdf", ["image", "image"]).read_bytes()

    events = list(drafts.add_file(draft, "scan.pdf", data, USER))

    assert events == [
        {"status": "reading"},
        {
            "status": "refused",
            "message": "No text found — this looks like a scanned PDF. Export it as text, "
            "or upload the PDF your slides were made from.",
            "retry": False,
        },
    ]
    assert tutor.concepts_calls == []


def test_draft_refuses_sixth_file():
    tutor = FakeTutor()
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)
    for n in range(5):
        last = list(drafts.add_file(draft, f"{n}.txt", _text(f"Part{n}"), USER))[-1]
        assert last["status"] == "ready"

    events = list(drafts.add_file(draft, "6.txt", _text("Extra"), USER))

    assert events[-1] == {
        "status": "refused",
        "message": "Biology can have at most 5 files.",
        "retry": False,
    }
    assert len(tutor.concepts_calls) == 5


def test_draft_refuses_same_file_twice():
    tutor = FakeTutor()
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)
    list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))

    events = list(drafts.add_file(draft, "copy.txt", _text("Cells"), USER))

    assert events[-1] == {
        "status": "refused",
        "message": "copy.txt is already in Biology.",
        "retry": False,
    }
    assert len(tutor.concepts_calls) == 1


def test_failed_concept_call_saves_nothing_and_can_be_retried():
    tutor = FakeTutor(fail_concepts=True, note_concepts=[[], [("Cells", None)]])
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)

    failed = list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))

    assert [e["status"] for e in failed] == ["reading", "finding", "refused"]
    assert failed[-1] == {
        "status": "refused",
        "message": "The tutor couldn't respond — your progress is saved. Try again in a minute.",
        "retry": True,
    }
    tutor.fail_concepts = False
    retried = list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))
    assert retried[-1]["status"] == "ready"  # not refused as a duplicate
    assert retried[-1]["concepts"] == ["Cells"]
    assert retried[-1]["new"] == ["Cells"]


def test_refused_notes_give_the_notes_copy():
    drafts = _drafts(FakeTutor(refuse={"concepts_from_notes"}))
    draft = drafts.create("Biology", USER)

    events = list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))

    assert events[-1] == {
        "status": "refused",
        "message": "The tutor won't help with these notes.",
        "retry": False,
    }


class _OverQuotaTutor(FakeTutor):
    def concepts_from_notes(self, subject, existing, digest):
        raise UserQuotaExceeded()


def test_daily_limit_is_refused_without_retry():
    drafts = _drafts(_OverQuotaTutor())
    draft = drafts.create("Biology", USER)

    events = list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))

    assert events[-1] == {
        "status": "refused",
        "message": "Daily limit reached — your progress is saved. It resets at midnight.",
        "retry": False,
    }


def test_removed_file_drops_its_fresh_concepts():
    tutor = FakeTutor(
        note_concepts=[[("Cells", None)], [("Genes", None), ("Cells", "Cells")], [("DNA", None)]]
    )
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)
    list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))
    second = list(drafts.add_file(draft, "b.txt", _text("Genes"), USER))[-1]
    list(drafts.add_file(draft, "c.txt", _text("DNA"), USER))

    drafts.remove_file(draft, second["file_id"], USER)

    store = _store()
    subject_id = drafts.commit(draft, store, USER)
    assert [c.name for c in store.list_concepts(USER, subject_id)] == ["Cells", "DNA"]
    assert [d.filename for d in store.list_documents(USER, subject_id)] == ["a.txt", "c.txt"]


def test_a_file_finishes_when_nobody_reads_its_events():
    tutor = FakeTutor(note_concepts=[[("Cells", None)], [("Genes", None)]])
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)
    events = drafts.add_file(draft, "a.txt", _text("Cells"), USER)
    assert next(events) == {"status": "reading"}
    events.close()  # the browser tab went away

    list(drafts.add_file(draft, "b.txt", _text("Genes"), USER))  # waits for a.txt's turn

    store = _store()
    subject_id = drafts.commit(draft, store, USER)
    assert [d.filename for d in store.list_documents(USER, subject_id)] == ["a.txt", "b.txt"]
    assert [c.name for c in store.list_concepts(USER, subject_id)] == ["Cells", "Genes"]


def test_removing_an_unknown_file_is_a_lookup_error():
    drafts = _drafts(FakeTutor())
    draft = drafts.create("Biology", USER)
    with pytest.raises(LookupError):
        drafts.remove_file(draft, "nope", USER)


def test_draft_expires_an_hour_after_last_use():
    clock = [NOW]
    drafts = _drafts(FakeTutor(note_concepts=[[("Cells", None)]]), lambda: clock[0])
    kept, lapsed = drafts.create("Biology", USER), drafts.create("Chemistry", USER)
    clock[0] += DRAFT_TTL - timedelta(minutes=1)
    list(drafts.add_file(kept, "a.txt", _text("Cells"), USER))  # using a draft renews it
    clock[0] += timedelta(minutes=2)

    with pytest.raises(DraftExpired):
        drafts.add_file(lapsed, "b.txt", _text("Genes"), USER)
    with pytest.raises(DraftExpired):
        drafts.commit(lapsed, _store(), USER)
    drafts.commit(kept, _store(), USER)


def test_unknown_or_someone_elses_draft_is_expired():
    drafts = _drafts(FakeTutor())
    draft = drafts.create("Biology", USER)
    with pytest.raises(DraftExpired):
        drafts.add_file("unknown", "a.txt", _text("Cells"), USER)
    with pytest.raises(DraftExpired):
        drafts.add_file(draft, "a.txt", _text("Cells"), OTHER)
    with pytest.raises(DraftExpired):
        drafts.commit(draft, _store(), OTHER)


def test_committed_draft_is_gone():
    drafts = _drafts(FakeTutor(note_concepts=[[("Cells", None)]]))
    draft = drafts.create("Biology", USER)
    list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))
    drafts.commit(draft, _store(), USER)
    with pytest.raises(DraftExpired):
        drafts.commit(draft, _store(), USER)


@pytest.mark.db
def test_commit_saves_subject_files_concepts_in_one_transaction(pg_pool, monkeypatch):
    store = PgStore(pg_pool)
    store.ensure_profile(USER)
    tutor = FakeTutor(note_concepts=[[("Cells", None)], [("Genes", None)]])
    drafts = _drafts(tutor)
    draft = drafts.create("Biology", USER)
    list(drafts.add_file(draft, "a.txt", _text("Cells"), USER))
    list(drafts.add_file(draft, "b.txt", _text("Genes"), USER))

    def broken(*args, **kwargs):
        raise psycopg.errors.CheckViolation("injected")

    with monkeypatch.context() as patch:
        patch.setattr("ebb.db.repo._insert_concepts", broken)
        with pytest.raises(psycopg.errors.CheckViolation):
            drafts.commit(draft, store, USER)
    assert store.list_subjects(USER) == []

    subject_id = drafts.commit(draft, store, USER)  # the draft survives a failed commit
    assert [s.name for s in store.list_subjects(USER)] == ["Biology"]
    # One transaction gives both rows the same created_at, so their order isn't fixed here.
    assert sorted(d.filename for d in store.list_documents(USER, subject_id)) == ["a.txt", "b.txt"]
    assert [c.name for c in store.list_concepts(USER, subject_id)] == ["Cells", "Genes"]
