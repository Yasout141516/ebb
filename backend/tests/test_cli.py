"""The CLI end to end with typed input, using the fakes instead of Postgres and Gemini."""

import pytest
from typer.testing import CliRunner

from ebb import cli
from ebb.config import Settings
from tests.fakes import FakeTutor, MemoryStore

runner = CliRunner()


@pytest.fixture
def fakes(monkeypatch):
    settings = Settings(_env_file=None)
    store, tutor = MemoryStore(), FakeTutor()
    store.ensure_profile(settings.ebb_dev_user_id)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, store))
    monkeypatch.setattr(cli, "_tutor", lambda s: tutor)
    return store, tutor


def test_new_lists_the_concepts(fakes):
    result = runner.invoke(cli.app, ["new", "Computer Networks"])
    assert result.exit_code == 0
    assert "6 concepts" in result.output
    assert "Computer Networks 1" in result.output
    assert "ebb study" in result.output


def test_new_rejects_a_blank_topic(fakes):
    result = runner.invoke(cli.app, ["new", "   "])
    assert result.exit_code == 1
    assert "Give a topic" in result.output


def test_study_without_subjects_explains_what_to_do(fakes):
    result = runner.invoke(cli.app, ["study"])
    assert result.exit_code == 1
    assert 'ebb new "Computer Networks"' in result.output


def test_study_session_end_to_end(fakes):
    runner.invoke(cli.app, ["new", "Networks"])
    # Q1 short (empty line re-asks, then answer), Q2 mcq ("9" is invalid, re-asked), then quit.
    typed = "\n".join(["", "right", "9", "1", "q"]) + "\n"

    result = runner.invoke(cli.app, ["study"], input=typed)

    assert result.exit_code == 0, result.output
    assert "Short answer" in result.output
    assert "Multiple choice" in result.output
    assert "Type an answer, or q to stop." in result.output
    assert "Type 1-4, or q to stop." in result.output
    assert "● Correct" in result.output
    assert "Session summary" in result.output
    assert "Networks 1" in result.output


def test_study_ends_cleanly_when_input_runs_out(fakes):
    runner.invoke(cli.app, ["new", "Networks"])
    result = runner.invoke(cli.app, ["study"], input="")
    assert result.exit_code == 0
    assert "No answers this session" in result.output


def test_status_shows_tide_and_stage(fakes):
    runner.invoke(cli.app, ["new", "Networks"])
    runner.invoke(cli.app, ["study"], input="right\n1\nq\n")

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "100%" in result.output  # just reviewed
    assert "●○○○○" in result.output  # stage 1
    assert "new" in result.output  # concepts not studied yet


def test_unknown_subject_lists_the_real_ones(fakes):
    runner.invoke(cli.app, ["new", "Networks"])
    result = runner.invoke(cli.app, ["status", "Biology"])
    assert result.exit_code == 1
    assert "You have: Networks" in result.output
