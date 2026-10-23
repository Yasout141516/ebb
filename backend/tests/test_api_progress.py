from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from ebb import mastery, progress
from ebb.domain import Answer, GradeValue
from tests.api_support import NOW, USER, client, make_services


def _subject(services, name="Biology", concepts=(("Cells", "a"), ("Genes", "b"))):
    return services.store.create_subject(USER, name, list(concepts))


def _concept(services, name):
    return next(c for _, c in services.store.concepts.values() if c.name == name)


def _answer(services, name, ago, grade=GradeValue.INCORRECT):
    """Record an answer `ago` before NOW and set the concept's stored state to the replay."""
    concept = _concept(services, name)
    answer = Answer(uuid4(), uuid4(), concept.id, "x", grade, "", "fake", NOW - ago)
    services.store.attempts[answer.id] = answer
    points = [
        progress.AnswerPoint(a.concept_id, a.graded_at, a.grade)
        for a in sorted(services.store.attempts.values(), key=lambda a: a.graded_at)
        if a.concept_id == concept.id
    ]
    stored = progress.replay(concept, points)[-1][1]
    services.store.concepts[concept.id] = (USER, replace(concept, **_state(stored)))


def _state(c):
    return {
        "stage": c.stage,
        "next_review_at": c.next_review_at,
        "last_reviewed_at": c.last_reviewed_at,
        "review_count": c.review_count,
    }


def _get(services, subject_id):
    response = client(services).get(f"/subjects/{subject_id}/progress")
    assert response.status_code == 200
    return response.json()


def test_progress_new_user_has_no_chart():
    services = make_services()
    subject = _subject(services)
    _answer(services, "Cells", timedelta(days=2))
    _answer(services, "Genes", timedelta(days=1))
    _answer(services, "Genes", timedelta(hours=30))  # the same local day as the one above
    assert _get(services, subject)["chart"]["enough_days"] is False
    _answer(services, "Cells", timedelta(hours=1))
    assert _get(services, subject)["chart"]["enough_days"] is True


def test_progress_streak_counts_other_subjects():
    services = make_services()
    biology = _subject(services)
    _subject(services, "Chemistry", [("Atoms", "c")])
    _answer(services, "Atoms", timedelta(days=1))
    _answer(services, "Atoms", timedelta(hours=1))
    body = _get(services, biology)
    assert body["streak"]["current"] == 2
    assert body["streak"]["best"] == 2
    assert body["streak"]["last14"] == [False] * 12 + [True, True]
    assert body["chart"]["enough_days"] is False  # none of the answers are in Biology


def test_progress_average_and_change():
    services = make_services()
    subject = _subject(services, concepts=[("Cells", "a"), ("Genes", "b"), ("New", "c")])
    _answer(services, "Cells", timedelta(days=40))
    _answer(services, "Cells", timedelta(days=1))
    _answer(services, "Genes", timedelta(days=5))  # not answered 30 days ago: not counted then
    body = _get(services, subject)["average"]
    cells, genes = _concept(services, "Cells"), _concept(services, "Genes")
    then = progress.replay(
        cells, [progress.AnswerPoint(cells.id, NOW - timedelta(days=40), GradeValue.INCORRECT)]
    )[-1][1]
    average_then = mastery.tide(then, NOW - timedelta(days=30))
    average_now = (mastery.tide(cells, NOW) + mastery.tide(genes, NOW)) / 2
    assert body["now"] == average_now
    assert body["change"] == average_now - average_then
    assert body["since"] == "2026-09-23"
    assert body["counted"] == 2
    assert body["not_counted"] == ["New"]


def test_progress_average_is_null_with_nothing_studied():
    services = make_services()
    body = _get(services, _subject(services))["average"]
    assert body["now"] is None
    assert body["change"] is None
    assert body["counted"] == 0
    assert body["not_counted"] == ["Cells", "Genes"]


def test_progress_stages_group_concepts():
    services = make_services()
    subject = _subject(services, concepts=[("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")])
    for name, stage in [("A", 1), ("B", 3), ("C", 3)]:
        concept = _concept(services, name)
        services.store.concepts[concept.id] = (
            USER,
            replace(concept, stage=stage, next_review_at=NOW - timedelta(days=1)),
        )
    body = _get(services, subject)
    assert [s["stage"] for s in body["stages"]] == [1, 2, 3, 4, 5]
    by_stage = {s["stage"]: [c["name"] for c in s["concepts"]] for s in body["stages"]}
    assert by_stage[1] == ["A"]
    assert by_stage[2] == []
    assert sorted(by_stage[3]) == ["B", "C"]
    assert sorted(d["name"] for d in body["due"]) == ["A", "B", "C"]


def test_progress_chart_now_point_matches_concepts_route():
    services = make_services()
    subject = _subject(services)
    _answer(services, "Cells", timedelta(days=3))
    http = client(services)
    chart = http.get(f"/subjects/{subject}/progress").json()["chart"]["concepts"]
    states = {c["name"]: c for c in http.get(f"/subjects/{subject}/concepts").json()["concepts"]}
    for line in chart:
        assert line["now"] == states[line["name"]]["tide"]
        assert line["past"][-1][1] == line["now"]
    cells = next(line for line in chart if line["name"] == "Cells")
    genes = next(line for line in chart if line["name"] == "Genes")
    assert cells["studied"] is True
    assert cells["ahead"]
    assert genes["studied"] is False
    assert genes["ahead"] == []
    assert all(tide is None for _, tide in genes["past"])
    assert cells["past"][0][1] is None  # nothing answered 30 days ago


def test_progress_of_another_users_subject_is_not_found():
    services = make_services()
    other = services.store.create_subject(USER, "Mine", [])
    response = client(replace(services, user_id=uuid4())).get(f"/subjects/{other}/progress")
    assert response.status_code == 404
