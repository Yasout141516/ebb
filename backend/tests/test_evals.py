"""The grading eval: building labelled cases, scoring grades against them, the report."""

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ebb import cli
from ebb.citations import normalise
from ebb.config import ConfigError, Settings
from ebb.domain import Claim, Grade, GradeValue
from ebb.evals import (
    EVAL_TOPICS,
    PACE_SECONDS,
    RETRY_WAITS,
    EvalCase,
    build_cases,
    load_cases,
    report_markdown,
    run_cases,
    save_cases,
    score,
)
from ebb.llm.gateway import NullRecorder, StoreRecorder
from tests.fakes import FakeTutor, MemoryStore

runner = CliRunner()
NOTES = Path(__file__).parent.parent / "evals" / "sample_notes.md"
CORRECT, PARTIAL, INCORRECT = GradeValue.CORRECT, GradeValue.PARTIAL, GradeValue.INCORRECT


def _no_sleep(seconds: float) -> None:
    pass


def _case(label: GradeValue, n: int = 1, sources=(), answer: str = "right") -> EvalCase:
    return EvalCase(
        f"case-{n:03d}", "Topic", f"Question {n}?", "ref", answer, label, "why", sources, "writer"
    )


def _grade(value: GradeValue, claims=()) -> Grade:
    return Grade(value, f"It is {value.value}.", claims=tuple(claims))


def test_there_are_forty_distinct_topics():
    assert len(EVAL_TOPICS) == 40
    assert len(set(EVAL_TOPICS)) == 40


def test_build_cycles_targets_and_grounds_ten():
    writer = FakeTutor()
    pauses = []

    cases = build_cases(writer, EVAL_TOPICS[:6], NOTES, grounded=10, sleep=pauses.append)

    assert len(cases) == 16
    assert [c.label for c in cases[:6]] == [CORRECT, PARTIAL, INCORRECT] * 2
    assert [c.label for c in cases[6:9]] == [CORRECT, PARTIAL, INCORRECT]
    assert [n for _, _, n in writer.eval_calls[:6]] == [None] * 6
    assert all(n for _, _, n in writer.eval_calls[6:])
    plain, grounded = cases[:6], cases[6:]
    assert all(c.sources == () for c in plain)
    assert all(len(c.sources) == 1 and c.sources[0].kind == "chunk" for c in grounded)
    assert {c.sources[0].title for c in grounded} == {"sample_notes.md"}
    assert len({c.sources[0].ref for c in grounded}) > 1  # round-robin over the chunks
    assert len({c.id for c in cases}) == 16
    assert {c.writer_model for c in cases} == {"fake-model"}
    assert pauses and set(pauses) == {PACE_SECONDS}


def test_build_is_stable_across_runs():
    one = build_cases(FakeTutor(), EVAL_TOPICS[:2], NOTES, grounded=3, sleep=_no_sleep)
    two = build_cases(FakeTutor(), EVAL_TOPICS[:2], NOTES, grounded=3, sleep=_no_sleep)
    assert [c.sources for c in one] == [c.sources for c in two]


def test_failed_writer_case_is_skipped():
    writer = FakeTutor(fail_eval_on_calls=[2])

    cases = build_cases(writer, EVAL_TOPICS[:4], NOTES, grounded=0, sleep=_no_sleep)

    assert [c.topic for c in cases] == [EVAL_TOPICS[i] for i in (0, 2, 3)]
    assert [c.label for c in cases] == [CORRECT, INCORRECT, CORRECT]


def test_refused_writer_case_is_skipped():
    writer = FakeTutor(refuse={"write_eval_case"})
    pauses: list[float] = []

    cases = build_cases(writer, EVAL_TOPICS[:3], NOTES, grounded=0, sleep=pauses.append)

    assert cases == []
    assert len(writer.eval_calls) == 3  # one call each, never retried
    assert pauses == [PACE_SECONDS] * 3


def test_cases_round_trip_jsonl(tmp_path):
    cases = build_cases(FakeTutor(), EVAL_TOPICS[:3], NOTES, grounded=2, sleep=_no_sleep)
    path = tmp_path / "out" / "grading.jsonl"

    save_cases(cases, path)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 5
    assert load_cases(path) == cases


def test_score_agreement_and_confusion():
    cases = [_case(CORRECT, 1), _case(CORRECT, 2), _case(PARTIAL, 3), _case(PARTIAL, 4)]
    cases += [_case(INCORRECT, 5), _case(INCORRECT, 6)]
    grades = [
        _grade(CORRECT),  # agrees
        _grade(PARTIAL),  # harsh
        _grade(CORRECT),  # lenient
        _grade(PARTIAL),  # agrees
        _grade(INCORRECT),  # agrees
        _grade(CORRECT),  # lenient
    ]

    result = score(cases, grades)

    assert (result.total, result.agreed) == (6, 3)
    assert result.agreement == 0.5
    assert (result.too_harsh, result.too_lenient) == (1, 2)
    assert result.confusion["correct"] == {"correct": 1, "partial": 1, "incorrect": 0}
    assert result.confusion["partial"] == {"correct": 1, "partial": 1, "incorrect": 0}
    assert result.confusion["incorrect"] == {"correct": 1, "partial": 0, "incorrect": 1}
    questions = [d["question"] for d in result.disagreements]
    assert questions == ["Question 2?", "Question 3?", "Question 6?"]
    assert result.disagreements[0]["grade"] == "partial"
    assert result.disagreements[0]["explanation"] == "It is partial."


def test_a_failed_grade_is_counted_apart_from_disagreements():
    cases = [_case(CORRECT, 1), _case(INCORRECT, 2, answer="wrong")]
    grader = FakeTutor(fail_grade_times=1 + len(RETRY_WAITS))  # every try on case 1

    grades = run_cases(grader, cases, sleep=_no_sleep)
    result = score(cases, grades)

    assert grades[0] is None and grades[1].value is INCORRECT
    assert (result.total, result.failed, result.agreed) == (2, 1, 1)
    assert result.agreement == 1.0  # over the one graded case
    assert result.disagreements == []
    assert result.failures == [{"id": "case-001", "question": "Question 1?"}]
    assert (result.too_harsh, result.too_lenient) == (0, 0)
    assert sum(sum(row.values()) for row in result.confusion.values()) == 1


def test_a_failed_grade_leaves_agreement_and_disagreements_unchanged():
    cases = [_case(CORRECT, 1), _case(PARTIAL, 2), _case(INCORRECT, 3)]
    graded = [_grade(CORRECT), _grade(CORRECT)]

    with_failure = score(cases, [*graded, None])
    without = score(cases[:2], graded)

    assert with_failure.failed == 1 and without.failed == 0
    assert with_failure.agreement == without.agreement == 0.5
    assert with_failure.disagreements == without.disagreements
    assert with_failure.confusion == without.confusion


def test_run_retries_a_case_with_backoff_before_failing():
    cases = [_case(CORRECT, 1), _case(INCORRECT, 2, answer="wrong")]
    grader = FakeTutor(fail_grade_times=2)
    pauses: list[float] = []
    seen: list[tuple[int, int]] = []

    grades = run_cases(
        grader, cases, sleep=pauses.append, progress=lambda n, total: seen.append((n, total))
    )

    assert [g.value if g else None for g in grades] == [CORRECT, INCORRECT]
    assert pauses == [RETRY_WAITS[0], RETRY_WAITS[1], PACE_SECONDS, PACE_SECONDS]
    assert seen == [(1, 2), (2, 2)]


def test_run_gives_up_on_a_case_after_the_last_wait():
    pauses: list[float] = []

    grades = run_cases(FakeTutor(fail_grade_times=99), [_case(CORRECT)], sleep=pauses.append)

    assert grades == [None]
    assert pauses == [*RETRY_WAITS, PACE_SECONDS]


def test_run_skips_a_refused_case_without_waiting():
    grader = FakeTutor(refuse={"grade"})
    pauses: list[float] = []

    grades = run_cases(grader, [_case(CORRECT, 1), _case(CORRECT, 2)], sleep=pauses.append)

    assert grades == [None, None]
    assert len(grader.grade_previous) == 2  # one call per case
    assert pauses == [PACE_SECONDS, PACE_SECONDS]


def test_run_stops_on_a_config_error():
    class BadKey(FakeTutor):
        def grade(self, *args, **kwargs):
            raise ConfigError("Groq rejected the API key — check GROQ_API_KEY.")

    with pytest.raises(ConfigError):
        run_cases(BadKey(), [_case(CORRECT)], sleep=_no_sleep)


def test_report_lists_failed_cases_apart():
    cases = [_case(CORRECT, 1), _case(PARTIAL, 2), _case(INCORRECT, 3)]
    result = score(cases, [_grade(CORRECT), _grade(CORRECT), None])

    text = report_markdown(result, "m", date(2026, 10, 20), None)

    assert "Agreement: 1 of 2 graded (50.0%) · 1 failed — target 90%" in text
    assert "not comparable" in text
    failed = text.split("## Failed", 1)[1]
    assert "case-003" in failed and "Question 3?" in failed
    disagreements = text.split("## Disagreements", 1)[1].split("## Failed", 1)[0]
    assert "Question 2?" in disagreements and "Question 3?" not in disagreements


def test_grounded_cases_count_valid_citations():
    notes = build_cases(FakeTutor(), [], NOTES, grounded=2, sleep=_no_sleep)
    source = notes[0].sources[0]
    quote = source.text.split(".")[0]
    cases = [*notes, _case(CORRECT, 9)]
    grades = [
        _grade(CORRECT, [Claim("N1", quote)]),
        _grade(PARTIAL, [Claim("N1", "words that are not in the notes")]),
        _grade(CORRECT, [Claim("N1", "ignored for a case with no sources")]),
    ]

    result = score(cases, grades)

    assert normalise(quote) in normalise(source.text)
    assert (result.grounded_total, result.grounded_cited) == (2, 1)


def test_report_states_where_labels_come_from():
    cases = [_case(CORRECT, 1), _case(PARTIAL, 2)]
    result = score(cases, [_grade(CORRECT), _grade(CORRECT)])

    text = report_markdown(result, "model-x", date(2026, 10, 20), None)

    assert text.startswith("# Grading eval")
    assert "Agreement: 1 of 2 graded (50.0%) · 0 failed — target 90%" in text
    assert "not comparable" not in text
    assert "## Failed\n\nNone." in text
    assert "Disputes: database not reachable" in text
    assert "too lenient: 1" in text.lower()
    assert "Question 2?" in text
    assert "writer" in text and "a model, not a person" in text
    assert "model-x" in text and "2026-10-20" in text


def test_report_shows_dispute_counts():
    result = score([_case(CORRECT)], [_grade(CORRECT)])
    counts = {"raised": 4, "changed": 2, "voided": 1, "upheld": 1}

    text = report_markdown(result, "m", date(2026, 10, 20), counts)

    assert "Disputes: 4 raised · 2 changed · 1 voided · 1 upheld" in text


@pytest.fixture
def eval_cli(monkeypatch):
    settings = Settings(
        _env_file=None,
        ebb_timezone="UTC",
        groq_api_key="g",
        mistral_api_key="m",
        ebb_primary_model="a/b",
    )
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, MemoryStore()))
    writer = FakeTutor()
    monkeypatch.setattr(cli, "_eval_writer", lambda s, r: writer)
    monkeypatch.setattr(cli, "_eval_grader", lambda s, r: FakeTutor())
    monkeypatch.setattr(cli, "_pace", _no_sleep)
    monkeypatch.setattr(cli, "_today", lambda: date(2026, 10, 20))
    return writer


def test_eval_build_refuses_to_overwrite(eval_cli, tmp_path):
    dataset = tmp_path / "grading.jsonl"
    dataset.write_text("keep me\n", encoding="utf-8")

    result = runner.invoke(
        cli.app, ["eval", "build", "--dataset", str(dataset), "--notes", str(NOTES)]
    )

    assert result.exit_code == 1
    assert "exists" in result.output and "--force to rebuild it." in result.output
    assert dataset.read_text(encoding="utf-8") == "keep me\n"
    assert eval_cli.eval_calls == []


def test_eval_build_writes_the_dataset(eval_cli, tmp_path):
    dataset = tmp_path / "grading.jsonl"

    result = runner.invoke(
        cli.app, ["eval", "build", "--dataset", str(dataset), "--notes", str(NOTES), "--force"]
    )

    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 50
    assert sum(1 for r in rows if r["sources"]) == 10


def test_eval_build_without_mistral_exits_2(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, mistral_api_key=None)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, MemoryStore()))

    result = runner.invoke(
        cli.app,
        ["eval", "build", "--dataset", str(tmp_path / "g.jsonl"), "--notes", str(NOTES)],
    )

    assert result.exit_code == 2
    assert "MISTRAL_API_KEY is not set" in result.output


def test_eval_run_writes_a_dated_report(eval_cli, tmp_path):
    dataset = tmp_path / "grading.jsonl"
    save_cases(build_cases(FakeTutor(), EVAL_TOPICS[:6], NOTES, 0, _no_sleep), dataset)
    results = tmp_path / "results"

    result = runner.invoke(
        cli.app, ["eval", "run", "--dataset", str(dataset), "--results", str(results)]
    )

    assert result.exit_code == 0, result.output
    assert "Grading 1/6…" in result.output and "Grading 6/6…" in result.output
    assert "Agreement: 6 of 6 graded (100.0%) · 0 failed" in result.output
    report = (results / "2026-10-20-a-b.md").read_text(encoding="utf-8")
    assert "Agreement: 6 of 6 graded (100.0%) · 0 failed — target 90%" in report
    assert "Disputes: 0 raised" in report


def test_eval_run_with_a_bad_key_exits_2(eval_cli, tmp_path, monkeypatch):
    dataset = tmp_path / "grading.jsonl"
    save_cases(build_cases(FakeTutor(), EVAL_TOPICS[:2], NOTES, 0, _no_sleep), dataset)

    class BadKey(FakeTutor):
        def grade(self, *args, **kwargs):
            raise ConfigError("Groq rejected GROQ_API_KEY — check the key in .env")

    monkeypatch.setattr(cli, "_eval_grader", lambda s, r: BadKey())

    result = runner.invoke(
        cli.app, ["eval", "run", "--dataset", str(dataset), "--results", str(tmp_path / "r")]
    )

    assert result.exit_code == 2
    assert "Groq rejected GROQ_API_KEY — check the key in .env" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert not (tmp_path / "r").exists()


def test_eval_run_survives_an_unreachable_database(eval_cli, tmp_path, monkeypatch):
    dataset = tmp_path / "grading.jsonl"
    save_cases(build_cases(FakeTutor(), EVAL_TOPICS[:3], NOTES, 0, _no_sleep), dataset)

    def unreachable():
        cli._fail("Can't reach the database.", code=2)

    monkeypatch.setattr(cli, "_open_store", unreachable)

    result = runner.invoke(
        cli.app, ["eval", "run", "--dataset", str(dataset), "--results", str(tmp_path / "r")]
    )

    assert result.exit_code == 0, result.output
    report = (tmp_path / "r" / "2026-10-20-a-b.md").read_text(encoding="utf-8")
    assert "Disputes: database not reachable" in report


@pytest.mark.parametrize("database_up", [True, False])
def test_eval_model_calls_are_recorded_when_the_database_is_up(
    eval_cli, tmp_path, monkeypatch, database_up
):
    dataset = tmp_path / "grading.jsonl"
    save_cases(build_cases(FakeTutor(), EVAL_TOPICS[:1], NOTES, 0, _no_sleep), dataset)
    if not database_up:
        monkeypatch.setattr(cli, "_open_store", lambda: cli._fail("down", code=2))
    recorders = []

    def grader(settings, recorder):
        recorders.append(recorder)
        return FakeTutor()

    monkeypatch.setattr(cli, "_eval_grader", grader)

    result = runner.invoke(
        cli.app, ["eval", "run", "--dataset", str(dataset), "--results", str(tmp_path / "r")]
    )

    assert result.exit_code == 0, result.output
    [recorder] = recorders
    assert type(recorder) is (StoreRecorder if database_up else NullRecorder)
