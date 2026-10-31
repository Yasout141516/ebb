from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

from ebb.domain import ChunkDraft, NewDocument
from tests.api_support import NOW, USER, client, make_services
from tests.fakes import learn

OTHER = UUID("00000000-0000-0000-0000-000000000002")


def _subject(services, name="Biology", concepts=(("Cells", "Units of life"),), owner=USER):
    services.store.ensure_profile(owner)
    return services.store.create_subject(owner, name, list(concepts))


def _set(services, name, **changes):
    for key, (owner, concept) in services.store.concepts.items():
        if concept.name == name:
            services.store.concepts[key] = (owner, replace(concept, **changes))


def test_list_subjects_with_due_counts():
    services = make_services()
    first = _subject(services, "Biology", [("Cells", "a"), ("Genes", "b"), ("New", "c")])
    _subject(services, "Chemistry", [("Atoms", "d")])
    _set(services, "Cells", stage=1, next_review_at=NOW - timedelta(days=1))
    _set(services, "Genes", stage=2, next_review_at=NOW + timedelta(days=1))
    body = client(services).get("/subjects").json()
    assert {s["name"] for s in body} == {"Chemistry", "Biology"}
    biology = next(s for s in body if s["id"] == str(first))
    assert biology["concept_count"] == 3
    assert biology["due_count"] == 1


def test_create_subject_from_topic():
    response = client(make_services()).post("/subjects", json={"topic": "  Photosynthesis "})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Photosynthesis"
    assert body["concepts"]
    assert set(body["concepts"][0]) == {"name", "description"}
    UUID(body["id"])


def test_create_subject_duplicate_is_409_with_copy():
    services = make_services()
    _subject(services, "Biology")
    response = client(services).post("/subjects", json={"topic": "biology"})
    assert response.status_code == 409
    assert response.json() == {
        "error": "Biology already exists — add notes to it from its Sources list."
    }


def test_create_subject_blank_topic_is_422():
    web = client(make_services())
    assert web.post("/subjects", json={"topic": "   "}).status_code == 422
    assert web.post("/subjects", json={"topic": ""}).status_code == 422
    assert web.post("/subjects", json={"topic": "x" * 121}).status_code == 422


def test_sources_detail_line():
    services = make_services()
    subject = _subject(services)
    chunk = ChunkDraft(0, "Cells are small.", None, None, None, ())
    services.store.add_document(
        USER, subject, NewDocument("bio.pdf", "pdf", 4_400_000, 38, "a" * 64, (chunk,)), []
    )
    services.store.add_document(
        USER, subject, NewDocument("n.txt", "txt", 2_000_000, None, "b" * 64, (chunk,)), []
    )
    body = client(services).get(f"/subjects/{subject}/sources").json()
    by_name = {s["filename"]: s for s in body}
    assert by_name["bio.pdf"]["detail"] == "38 pages · 4.2 MB"
    assert by_name["bio.pdf"]["chunk_count"] == 1
    assert by_name["n.txt"]["detail"] == "1.9 MB"
    assert set(by_name["n.txt"]) == {
        "id",
        "filename",
        "kind",
        "page_count",
        "size_bytes",
        "chunk_count",
        "detail",
    }


def test_concepts_have_tide_band_and_projection():
    services = make_services()
    subject = _subject(services, concepts=[("Cells", "a"), ("Genes", "b")])
    _set(
        services,
        "Cells",
        stage=2,
        last_reviewed_at=NOW - timedelta(days=1),
        next_review_at=NOW - timedelta(hours=1),
        review_count=2,
    )
    body = client(services).get(f"/subjects/{subject}/concepts").json()
    cells = next(c for c in body["concepts"] if c["name"] == "Cells")
    genes = next(c for c in body["concepts"] if c["name"] == "Genes")
    assert cells["stage"] == 2 and cells["due"] is True
    assert 0 <= cells["tide"] <= 1
    assert cells["band"] in {"high tide", "ebbing", "low tide"}
    assert cells["projected_tide"] < cells["tide"]
    assert genes["tide"] is None and genes["band"] == "new" and genes["due"] is False
    assert body["due_count"] == 1
    assert body["next_review_at"] is not None
    assert cells["learned"] is False and genes["learned"] is False


def test_concepts_say_which_are_learned():
    services = make_services()
    subject = _subject(services, concepts=[("Cells", "a"), ("Genes", "b")])
    genes = next(c for c in services.store.list_concepts(USER, subject) if c.name == "Genes")
    learn(services.store, USER, genes.id)

    body = client(services).get(f"/subjects/{subject}/concepts").json()

    assert {c["name"]: c["learned"] for c in body["concepts"]} == {"Cells": False, "Genes": True}


def test_another_users_subject_is_404():
    services = make_services()
    theirs = _subject(services, "Secret", owner=OTHER)
    web = client(services)
    assert web.get("/subjects").json() == []
    for path in ("sources", "concepts"):
        assert web.get(f"/subjects/{theirs}/{path}").status_code == 404
        assert web.get(f"/subjects/{uuid4()}/{path}").status_code == 404
