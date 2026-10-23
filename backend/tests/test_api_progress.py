from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from ebb import mastery, progress
from ebb.api.routes_progress import build_progress
from ebb.domain import Answer, Concept, GradeValue
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
    for name, stage, due in [("A", 1, -1), ("B", 3, 2), ("C", 3, -1), ("D", 3, -1)]:
        concept = _concept(services, name)
        services.store.concepts[concept.id] = (
            USER,
            replace(concept, stage=stage, next_review_at=NOW + timedelta(days=due)),
        )
    body = _get(services, subject)
    assert [s["stage"] for s in body["stages"]] == [1, 2, 3, 4, 5]
    by_stage = {s["stage"]: [c["name"] for c in s["concepts"]] for s in body["stages"]}
    assert by_stage[1] == ["A"]
    assert by_stage[2] == []
    assert by_stage[3] == ["C", "D", "B"]  # soonest first, then by name
    assert sorted(d["name"] for d in body["due"]) == ["A", "C", "D"]


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


def test_build_progress_follows_local_days_in_dhaka():
    dhaka = ZoneInfo("Asia/Dhaka")
    now = datetime(2026, 10, 23, 19, 0, tzinfo=UTC)  # 01:00 on 24 Oct in Dhaka
    concept = Concept(uuid4(), uuid4(), "Cells", "a")
    moments = [
        datetime(2026, 10, 21, 17, 0, tzinfo=UTC),  # 21 Oct 23:00 in Dhaka
        datetime(2026, 10, 21, 19, 30, tzinfo=UTC),  # 22 Oct 01:30
        datetime(2026, 10, 22, 19, 30, tzinfo=UTC),  # 23 Oct 01:30
    ]
    answers = [progress.AnswerPoint(concept.id, m, GradeValue.INCORRECT) for m in moments]
    result = build_progress([concept], answers, moments, now, dhaka)
    # Dhaka days 21, 22, 23 (UTC would see only 21 and 22); today is the 24th.
    assert result.streak.last14 == [False] * 10 + [True, True, True, False]
    assert (result.streak.current, result.streak.best) == (3, 3)
    assert result.chart.enough_days is True
    assert result.average.since == "2026-09-24"  # 30 days before 01:00 on 24 Oct, local
    utc = build_progress([concept], answers, moments, now, ZoneInfo("UTC"))
    assert utc.chart.enough_days is False
    assert utc.average.since == "2026-09-23"


def test_build_progress_stored_state_wins_over_the_replay():
    now = NOW
    concept = Concept(uuid4(), uuid4(), "Cells", "a")
    answers = [progress.AnswerPoint(concept.id, now - timedelta(days=10), GradeValue.INCORRECT)]
    # Stored as if a week had passed since: reviewed more recently, higher stage, due now.
    stored = replace(
        concept,
        stage=2,
        last_reviewed_at=now - timedelta(days=1),
        next_review_at=now - timedelta(hours=1),
        review_count=2,
    )
    replayed = progress.replay(concept, answers)[-1][1]
    assert mastery.tide(replayed, now) != mastery.tide(stored, now)
    result = build_progress([stored], answers, [a.graded_at for a in answers], now, ZoneInfo("UTC"))
    expected = mastery.tide(stored, now)
    line = result.chart.concepts[0]
    assert line.now == expected
    assert line.past[-1] == (now, expected)
    assert line.ahead[0][1] == mastery.tide(stored, now + timedelta(hours=6))
    assert result.average.now == expected
    assert [(d.name, d.tide) for d in result.due] == [("Cells", expected)]
    assert [c.name for c in result.stages[1].concepts] == ["Cells"]
