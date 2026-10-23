from uuid import uuid4

import pytest

from ebb.store import DocumentRefused, SubjectExists
from ebb.subjects import add_notes, find_note_concepts, subject_from_topic
from tests.fakes import FakeTutor, MemoryStore, make_subject, new_document


def test_subject_from_topic_creates_the_subject_with_stripped_name():
    store, user = MemoryStore(), uuid4()
    store.ensure_profile(user)
    subject_id, concepts = subject_from_topic(FakeTutor(), store, user, "  Networks \n")
    assert [s.name for s in store.list_subjects(user)] == ["Networks"]
    assert len(concepts) == 6
    assert [c.name for c in store.list_concepts(user, subject_id)] == [n for n, _ in concepts]


def test_subject_from_topic_refuses_existing_name_without_calling_the_tutor():
    store, user = MemoryStore(), uuid4()
    make_subject(store, user, name="Networks")
    tutor = FakeTutor(refuse={"extract_concepts"})  # would raise TutorRefused if it were called
    with pytest.raises(SubjectExists):
        subject_from_topic(tutor, store, user, " networks ")


def test_find_note_concepts_splits_matched_from_fresh():
    tutor = FakeTutor(note_concepts=[[("Subnets", "Subnetting"), ("Routing", None)]])
    known = [("Subnetting", "Splitting networks.")]
    report = find_note_concepts(tutor, "Networks", known, new_document("a.md", "text"))
    assert report.matched == ["Subnetting"]
    assert [name for name, _ in report.fresh] == ["Routing"]


def test_add_notes_reports_matched_and_fresh():
    store, user = MemoryStore(), uuid4()
    subject_id = make_subject(store, user, concepts=1)
    subject = store.list_subjects(user)[0]
    old = store.list_concepts(user, subject_id)[0].name
    tutor = FakeTutor(note_concepts=[[("Gateways", old), ("Routing", None)]])
    report = add_notes(tutor, store, user, subject, new_document("a.md", "text"))
    assert report.matched == [old]
    assert [name for name, _ in report.fresh] == ["Routing"]
    assert store.count_documents(user, subject_id) == 1
    assert "Routing" in [c.name for c in store.list_concepts(user, subject_id)]


def test_add_notes_refuses_a_duplicate_file():
    store, user = MemoryStore(), uuid4()
    make_subject(store, user)
    subject = store.list_subjects(user)[0]
    document = new_document("a.md", "text")
    add_notes(FakeTutor(), store, user, subject, document)
    with pytest.raises(DocumentRefused):
        add_notes(FakeTutor(), store, user, subject, document)
