"""The CLI end to end with typed input, using the fakes instead of Postgres and Gemini."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from typer.testing import CliRunner

from ebb import cli
from ebb.config import ConfigError, Settings
from ebb.llm.tutor import AllProvidersExhausted
from ebb.ui import format_day
from tests.fakes import FakeTutor, FakeWebSearch, MemoryStore, seed_concept

runner = CliRunner()
# Captured at import time, before any test patches cli._tutor or cli._ask.
_real_tutor = cli._tutor
_real_ask = cli._ask


@pytest.fixture
def fakes(monkeypatch):
    settings = Settings(_env_file=None, ebb_timezone="UTC", tavily_api_key=None)
    store, tutor = MemoryStore(), FakeTutor()
    store.ensure_profile(settings.ebb_dev_user_id)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, store))
    monkeypatch.setattr(cli, "_tutor", lambda s: tutor)
    # One saver for every run in a test, as the Postgres one outlives a process.
    saver = InMemorySaver()
    monkeypatch.setattr(cli, "_checkpointer", lambda s: saver)
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


def test_study_gives_the_graph_the_configured_web_search(fakes, monkeypatch):
    from ebb import web
    from ebb.graph import build

    search = FakeWebSearch()
    given = []
    real_build = build.build_graph

    def recording_build(*args, **kwargs):
        given.append(kwargs.get("web"))
        return real_build(*args, **kwargs)

    monkeypatch.setattr(web, "web_search", lambda settings: search)
    monkeypatch.setattr(build, "build_graph", recording_build)
    runner.invoke(cli.app, ["new", "Networks"])

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert given == [search]


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


def test_missing_api_key_exits_2(fakes, monkeypatch):
    # Real _tutor, real key check: no key configured means exit 2 before any LLM call.
    store, _ = fakes
    monkeypatch.setattr(cli, "_tutor", _real_tutor)
    settings = Settings(_env_file=None, google_api_key=None)
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, store))

    result = runner.invoke(cli.app, ["new", "Networks"])

    assert result.exit_code == 2
    assert "GOOGLE_API_KEY is not set" in result.output


def test_unreachable_database_exits_2(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql://ebb:ebb@127.0.0.1:1/ebb")
    monkeypatch.setattr(cli, "Settings", lambda: settings)

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 2
    assert "docker compose up -d" in result.output


def test_new_reports_when_the_llm_is_unavailable(fakes, monkeypatch):
    _, tutor = fakes

    def down(topic: str):
        raise AllProvidersExhausted("generation down")

    monkeypatch.setattr(tutor, "extract_concepts", down)

    result = runner.invoke(cli.app, ["new", "Networks"])

    assert result.exit_code == 1
    assert "The tutor couldn't respond" in result.output


def test_new_shows_the_first_line_of_the_cause(fakes, monkeypatch):
    _, tutor = fakes

    def down(topic: str):
        raise AllProvidersExhausted("503 overloaded\nstack detail")

    monkeypatch.setattr(tutor, "extract_concepts", down)

    result = runner.invoke(cli.app, ["new", "Networks"])

    assert result.exit_code == 1
    assert "503 overloaded" in result.output
    assert "stack detail" not in result.output


def test_new_exits_2_on_a_rejected_key(fakes, monkeypatch):
    _, tutor = fakes

    def rejected(topic: str):
        raise ConfigError("Gemini rejected GOOGLE_API_KEY — check the key in .env")

    monkeypatch.setattr(tutor, "extract_concepts", rejected)

    result = runner.invoke(cli.app, ["new", "Networks"])

    assert result.exit_code == 2
    assert "Gemini rejected GOOGLE_API_KEY" in result.output


def test_study_exits_2_on_a_rejected_key(fakes, monkeypatch):
    store, tutor = fakes
    runner.invoke(cli.app, ["new", "Networks"])

    def rejected(*args, **kwargs):
        raise ConfigError("Gemini rejected GOOGLE_API_KEY — check the key in .env")

    monkeypatch.setattr(tutor, "generate_questions", rejected)

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 2
    assert "Gemini rejected GOOGLE_API_KEY" in result.output


@pytest.mark.parametrize("command", ["status", "study"])
def test_a_bad_timezone_exits_2_with_the_message(fakes, monkeypatch, command):
    store, _ = fakes
    settings = Settings(_env_file=None, ebb_timezone="Mars/Olympus")
    monkeypatch.setattr(cli, "_open_store", lambda: (settings, store))

    result = runner.invoke(cli.app, [command])

    assert result.exit_code == 2
    assert "EBB_TIMEZONE 'Mars/Olympus' is not a known time zone" in result.output


def interrupted_study(monkeypatch, typed: str = "right\n", at: int = 2, subject: str | None = None):
    """Run `ebb study` and close the terminal at the `at`-th question; returns that question."""
    asked = []

    def ask(payload):
        asked.append(payload)
        if len(asked) == at:
            raise RuntimeError("terminal closed")
        return _real_ask(payload)

    monkeypatch.setattr(cli, "_ask", ask)
    args = ["study"] if subject is None else ["study", subject]
    result = runner.invoke(cli.app, args, input=typed)
    monkeypatch.setattr(cli, "_ask", _real_ask)
    assert isinstance(result.exception, RuntimeError), result.output
    return asked[-1]


def only_session(store: MemoryStore) -> tuple:
    [(session_id, session)] = store.sessions.items()
    return session_id, session


def resumable(store: MemoryStore, session: dict):
    return store.resumable_session(session["user_id"], session["subject_id"], datetime.now(UTC))


def test_resume_yes_continues_at_the_same_question(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    pending = interrupted_study(monkeypatch)
    assert pending["concept"] == "Networks 1" and pending["number"] == 2

    result = runner.invoke(cli.app, ["study"], input="y\n1\nq\n")

    assert result.exit_code == 0, result.output
    question = "You stopped on Networks 1, question 2, a moment ago. Resume? [Y/n]"
    assert question in result.output
    assert pending["question"]["prompt"] in result.output.split(question, 1)[1]
    assert "Welcome back" not in result.output and "Nothing is due" not in result.output
    assert len(store.sessions) == 1  # the same session carried on
    assert len(store.attempts) == 2  # one answer before the terminal closed, one after
    assert "Session summary" in result.output


def test_resume_no_starts_fresh(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    interrupted_study(monkeypatch)
    old_id, old = only_session(store)

    # "maybe" is not an answer, so the question is asked again.
    result = runner.invoke(cli.app, ["study"], input="maybe\nn\nq\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Resume? [Y/n]") == 2
    assert old["ended_at"] is not None
    assert len(store.sessions) == 2
    assert "Nothing is due. Let's learn something new." in result.output


def test_eof_at_resume_prompt_leaves_session_resumable(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    interrupted_study(monkeypatch)
    old_id, old = only_session(store)

    result = runner.invoke(cli.app, ["study"], input="")

    assert result.exit_code == 0, result.output
    assert "Resume? [Y/n]" in result.output
    assert resumable(store, old).session_id == old_id
    assert len(store.sessions) == 1


def test_ctrl_c_at_resume_prompt_leaves_session_resumable(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    interrupted_study(monkeypatch)
    old_id, old = only_session(store)

    def interrupted(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.console, "input", interrupted)
    result = runner.invoke(cli.app, ["study"])

    assert result.exit_code == 0, result.output
    assert resumable(store, old).session_id == old_id
    assert len(store.sessions) == 1


def test_locked_session_starts_fresh(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    interrupted_study(monkeypatch)
    old_id, old = only_session(store)

    with store.session_lock(old["user_id"], old["thread_id"]) as held:
        assert held is True
        result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "That session is open in another terminal." in result.output
    assert "Resume?" not in result.output
    assert "Nothing is due. Let's learn something new." in result.output
    assert old["ended_at"] is None  # still the other terminal's
    assert len(store.sessions) == 2


def test_other_subjects_session_is_not_offered(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    interrupted_study(monkeypatch, subject="Networks")
    old_id, old = only_session(store)
    runner.invoke(cli.app, ["new", "Biology"])

    result = runner.invoke(cli.app, ["study", "Biology"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Resume?" not in result.output
    assert "Nothing is due. Let's learn something new." in result.output
    assert resumable(store, old).session_id == old_id


def test_q_ends_the_session_and_is_not_offered_again(fakes):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    runner.invoke(cli.app, ["study"], input="right\nq\n")

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Resume?" not in result.output
    assert len(store.sessions) == 2
    assert all(s["ended_at"] is not None for s in store.sessions.values())


def test_greeting_shows_due_count(fakes):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    now = datetime.now(UTC)
    seed_concept(
        store,
        next(iter(store.concepts)),
        stage=1,
        next_review_at=now - timedelta(hours=1),
        last_reviewed_at=now - timedelta(days=1, hours=1),
        review_count=1,
    )

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Welcome back — 1 idea is ebbing. Reviewing those first." in result.output


def test_caught_up_line_uses_the_next_review_date(fakes):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    now = datetime.now(UTC)
    for days, concept_id in enumerate(list(store.concepts), start=3):
        seed_concept(
            store,
            concept_id,
            stage=2,
            next_review_at=now + timedelta(days=days),
            last_reviewed_at=now - timedelta(days=1),
            review_count=2,
        )

    result = runner.invoke(cli.app, ["study"], input="")

    assert result.exit_code == 0, result.output
    assert "You've learned every concept here. Finding more advanced ones…" in result.output
    day = format_day(now + timedelta(days=3), ZoneInfo("UTC"))
    assert f"You're all caught up — next review {day}." in result.output


def test_startup_closes_stale_sessions_and_prunes_old_checkpoints(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    runner.invoke(cli.app, ["study"], input="q\n")
    interrupted_study(monkeypatch)
    (_, ended), (_, stale) = store.sessions.items()
    now = datetime.now(UTC)
    ended["ended_at"] = now - timedelta(days=8)
    stale["last_active_at"] = stale_at = now - timedelta(hours=30)
    saver = cli._checkpointer(store)
    old_thread = {"configurable": {"thread_id": ended["thread_id"]}}
    assert saver.get_tuple(old_thread) is not None

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Resume?" not in result.output
    assert stale["ended_at"] == stale_at
    assert ended["pruned_at"] is not None
    assert saver.get_tuple(old_thread) is None
    assert store.timezones[ended["user_id"]] == "UTC"


def crash_once(monkeypatch, owner, name: str, after: bool = False) -> None:
    """Make owner.name raise RuntimeError on its first call (after doing its work if `after`)."""
    real = getattr(owner, name)
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args)
        if len(calls) > 1:
            return real(*args, **kwargs)
        if after:
            real(*args, **kwargs)
        raise RuntimeError("killed while thinking")

    monkeypatch.setattr(owner, name, flaky)


def test_resume_after_a_crash_while_grading_grades_the_answer_once(fakes, monkeypatch):
    store, tutor = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    crash_once(monkeypatch, tutor, "grade")
    crashed = runner.invoke(cli.app, ["study"], input="right\n")  # Q1 is a short answer
    assert isinstance(crashed.exception, RuntimeError), crashed.output

    result = runner.invoke(cli.app, ["study"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    question = "You stopped on Networks 1, question 1, a moment ago. Resume? [Y/n]"
    assert question in result.output
    assert "● Correct" in result.output.split(question, 1)[1]
    assert len(store.sessions) == 1 and len(store.attempts) == 1
    [row] = [r for r in store.attempts.values()]
    assert row.answer_text == "right"


def test_resume_after_a_crash_once_recorded_does_not_count_twice(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    crash_once(monkeypatch, store, "record_answer", after=True)  # saved, then the process died
    crashed = runner.invoke(cli.app, ["study"], input="right\n")
    assert isinstance(crashed.exception, RuntimeError), crashed.output
    assert len(store.attempts) == 1

    result = runner.invoke(cli.app, ["study"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    question = "You stopped on Networks 1, question 1, a moment ago. Resume? [Y/n]"
    assert question in result.output
    after = result.output.split(question, 1)[1]
    assert "● Correct" in after and "Q2" in after  # the stored answer counts; Q2 is next
    assert "Session summary" in after and "1✓ 0◐ 0✗" in after
    assert len(store.sessions) == 1 and len(store.attempts) == 1


def test_declining_a_resume_after_a_crash_starts_fresh(fakes, monkeypatch):
    store, tutor = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    crash_once(monkeypatch, tutor, "grade")
    runner.invoke(cli.app, ["study"], input="right\n")
    old_id, old = only_session(store)

    result = runner.invoke(cli.app, ["study"], input="n\nq\n")

    assert result.exit_code == 0, result.output
    assert old["ended_at"] is not None and len(store.sessions) == 2
    assert len(store.attempts) == 0  # declined: the ungraded answer is dropped
    assert "Nothing is due. Let's learn something new." in result.output


def test_session_with_nothing_pending_is_closed_and_greeted(fakes):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    [subject] = store.list_subjects(next(iter(store.profiles)))
    # A session whose process died before its first checkpoint was written.
    session_id = store.create_session(next(iter(store.profiles)), subject.id, "never-saved")

    result = runner.invoke(cli.app, ["study"], input="q\n")

    assert result.exit_code == 0, result.output
    assert "Resume?" not in result.output
    assert store.sessions[session_id]["ended_at"] is not None
    assert "Nothing is due. Let's learn something new." in result.output


def held_elsewhere(store: MemoryStore) -> bool:
    """Whether the open session's lock is taken, so another terminal would be refused it."""
    [session] = [s for s in store.sessions.values() if s["ended_at"] is None]
    with store.session_lock(session["user_id"], session["thread_id"]) as got:
        return not got


def test_a_fresh_session_holds_its_lock_while_it_runs(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    seen = []

    def ask(payload):
        seen.append(held_elsewhere(store))
        return {"quit": True}

    monkeypatch.setattr(cli, "_ask", ask)
    result = runner.invoke(cli.app, ["study"])

    assert result.exit_code == 0, result.output
    assert seen == [True]
    assert store.locks == set()  # released when the command ends


def test_a_resumed_session_holds_its_lock_while_it_runs(fakes, monkeypatch):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    interrupted_study(monkeypatch)
    seen = []

    def ask(payload):
        seen.append(held_elsewhere(store))
        return {"quit": True}

    monkeypatch.setattr(cli, "_ask", ask)
    result = runner.invoke(cli.app, ["study"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "Resume? [Y/n]" in result.output
    assert len(store.sessions) == 1  # the resumed session, not a fresh one
    assert seen == [True]
    assert store.locks == set()


# --- adding notes ---

PASSAGE = "A subnet splits one network into smaller ones. Routers forward packets between them. "


def note_file(tmp_path, name="wk1.md", body=None):
    path = tmp_path / name
    path.write_text(body or f"# Subnets\n\n{PASSAGE * 5}\n", encoding="utf-8")
    return path


def one_subject(store):
    [subject] = store.list_subjects(next(iter(store.profiles)))
    return subject


def flat(output: str) -> str:
    return " ".join(output.split())


def test_add_reports_matched_and_new_concepts(fakes, tmp_path):
    store, tutor = fakes
    user = next(iter(store.profiles))
    runner.invoke(cli.app, ["new", "Networks"])  # concepts "Networks 1" ... "Networks 6"
    tutor.note_concepts = [[("Networks 1", "Networks 1"), ("ARP", None)]]

    result = runner.invoke(cli.app, ["add", str(note_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "wk1.md · 1 passages" in result.output
    assert "2 concepts in these notes — 1 already here (Networks 1), 1 new:" in result.output
    assert "1. ARP — About ARP." in result.output
    assert store.count_documents(user, one_subject(store).id) == 1
    assert len(store.list_concepts(user, one_subject(store).id)) == 7


def test_add_prints_the_privacy_line_once(fakes, tmp_path):
    runner.invoke(cli.app, ["new", "Networks"])
    result = runner.invoke(cli.app, ["add", str(note_file(tmp_path))])
    assert result.exit_code == 0, result.output
    assert flat(result.output).count("Don't upload sensitive documents.") == 1
    assert "Files are processed by an AI model to write questions and cite answers." in flat(
        result.output
    )


@pytest.mark.parametrize(
    "case", ["scanned", "too_big", "wrong_type", "missing", "duplicate", "full"]
)
def test_add_refusals_store_nothing_and_never_call_the_llm(fakes, tmp_path, case):
    store, tutor = fakes
    user = next(iter(store.profiles))
    runner.invoke(cli.app, ["new", "Networks"])
    subject = one_subject(store)
    path = note_file(tmp_path)
    expected = ""
    if case == "scanned":
        from tests.pdfs import build_pdf

        path = build_pdf(tmp_path / "scan.pdf", ["image", "image"])
        expected = "No text found — this looks like a scanned PDF."
    elif case == "too_big":
        path = tmp_path / "big.txt"
        with path.open("wb") as handle:
            handle.truncate(21 * 1024 * 1024)
        expected = "over the 20 MB limit"
    elif case == "wrong_type":
        path = note_file(tmp_path, "notes.docx")
        expected = "Ebb reads PDF, TXT or MD files."
    elif case == "missing":
        path = tmp_path / "nope.md"
        expected = "Can't open"
    elif case == "duplicate":
        assert runner.invoke(cli.app, ["add", str(path)]).exit_code == 0
        tutor.concepts_calls.clear()
        expected = "wk1.md is already in Networks."
    elif case == "full":
        for n in range(5):
            other = note_file(tmp_path, f"f{n}.md", f"# H{n}\n\n{PASSAGE * 4}{n}\n")
            assert runner.invoke(cli.app, ["add", str(other)]).exit_code == 0
        tutor.concepts_calls.clear()
        path = note_file(tmp_path, "sixth.md", f"# H6\n\n{PASSAGE * 4}six\n")
        expected = "Networks already has 5 of 5 files."
    before = store.count_documents(user, subject.id)

    result = runner.invoke(cli.app, ["add", str(path)])

    assert result.exit_code == 1
    assert expected in flat(result.output)
    assert store.count_documents(user, subject.id) == before
    assert tutor.concepts_calls == []
    assert "Don't upload" not in result.output


def test_add_a_missing_file_in_docker_says_where_to_put_it(fakes, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_in_docker", lambda: True)
    runner.invoke(cli.app, ["new", "Networks"])
    result = runner.invoke(cli.app, ["add", str(tmp_path / "nope.md")])
    assert result.exit_code == 1
    assert "In Docker, put files in the notes/ folder and use notes/<file>." in flat(result.output)


def test_add_a_missing_file_outside_docker_has_no_hint(fakes, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_in_docker", lambda: False)
    runner.invoke(cli.app, ["new", "Networks"])
    result = runner.invoke(cli.app, ["add", str(tmp_path / "nope.md")])
    assert result.exit_code == 1
    assert "In Docker" not in result.output


def test_add_llm_unavailable_stores_nothing(fakes, tmp_path):
    store, tutor = fakes
    runner.invoke(cli.app, ["new", "Networks"])
    tutor.fail_concepts = True

    result = runner.invoke(cli.app, ["add", str(note_file(tmp_path))])

    assert result.exit_code == 1
    assert "The tutor couldn't respond" in result.output
    assert store.documents == {}


def test_add_exits_2_on_a_rejected_key(fakes, tmp_path, monkeypatch):
    store, tutor = fakes
    runner.invoke(cli.app, ["new", "Networks"])

    def rejected(*args, **kwargs):
        raise ConfigError("Gemini rejected GOOGLE_API_KEY — check the key in .env")

    monkeypatch.setattr(tutor, "concepts_from_notes", rejected)
    result = runner.invoke(cli.app, ["add", str(note_file(tmp_path))])
    assert result.exit_code == 2
    assert store.documents == {}


def test_add_race_refusal_uses_the_same_copy(fakes, tmp_path, monkeypatch):
    from ebb.store import DocumentRefused

    store, _ = fakes
    runner.invoke(cli.app, ["new", "Networks"])

    def refused(*args, **kwargs):
        raise DocumentRefused("duplicate")

    monkeypatch.setattr(store, "add_document", refused)
    result = runner.invoke(cli.app, ["add", str(note_file(tmp_path))])
    assert result.exit_code == 1
    assert "wk1.md is already in Networks." in flat(result.output)


def test_new_with_notes_uses_only_note_concepts(fakes, tmp_path, monkeypatch):
    store, tutor = fakes
    tutor.note_concepts = [[("ARP", None), ("NAT", None), ("DNS", None)]]
    calls = []
    monkeypatch.setattr(tutor, "extract_concepts", lambda topic: calls.append(topic))

    result = runner.invoke(cli.app, ["new", "Networks", "--notes", str(note_file(tmp_path))])

    assert result.exit_code == 0, result.output
    assert calls == []
    user = next(iter(store.profiles))
    names = [c.name for c in store.list_concepts(user, one_subject(store).id)]
    assert names == ["ARP", "NAT", "DNS"]
    assert flat(result.output).count("Don't upload sensitive documents.") == 1
    assert "3 concepts in these notes — 3 new:" in result.output


def test_new_with_two_files_shares_concepts_between_calls(fakes, tmp_path):
    store, tutor = fakes
    tutor.note_concepts = [[("ARP", None)], [("arp", "ARP"), ("NAT", None)]]
    one = note_file(tmp_path, "a.md")
    two = note_file(tmp_path, "b.md", f"# Other\n\n{PASSAGE * 4}two\n")

    result = runner.invoke(cli.app, ["new", "Networks", "--notes", str(one), "--notes", str(two)])

    assert result.exit_code == 0, result.output
    assert tutor.concepts_calls[1][1] == ["ARP"]
    assert "1 already here (ARP), 1 new:" in result.output
    user = next(iter(store.profiles))
    assert store.count_documents(user, one_subject(store).id) == 2
    assert len(store.list_concepts(user, one_subject(store).id)) == 2


def test_new_with_two_files_is_all_or_nothing(fakes, tmp_path, monkeypatch):
    store, tutor = fakes
    real = tutor.concepts_from_notes

    def second_fails(subject, existing, digest):
        if tutor.concepts_calls:
            raise AllProvidersExhausted("down")
        return real(subject, existing, digest)

    monkeypatch.setattr(tutor, "concepts_from_notes", second_fails)
    tutor.note_concepts = [[("ARP", None)]]
    one = note_file(tmp_path, "a.md")
    two = note_file(tmp_path, "b.md", f"# Other\n\n{PASSAGE * 4}two\n")

    result = runner.invoke(cli.app, ["new", "Networks", "--notes", str(one), "--notes", str(two)])

    assert result.exit_code == 1
    assert "The tutor couldn't respond" in result.output
    assert store.subjects == {} and store.documents == {} and store.concepts == {}


def test_new_with_a_bad_second_file_calls_no_llm_and_creates_nothing(fakes, tmp_path):
    store, tutor = fakes
    good, bad = note_file(tmp_path), tmp_path / "x.docx"

    result = runner.invoke(cli.app, ["new", "Networks", "--notes", str(good), "--notes", str(bad)])

    assert result.exit_code == 1
    assert tutor.concepts_calls == [] and store.subjects == {}


def test_new_refuses_an_existing_name(fakes):
    store, _ = fakes
    runner.invoke(cli.app, ["new", "Computer Networks"])

    result = runner.invoke(cli.app, ["new", "computer networks"])

    assert result.exit_code == 1
    expected = "Computer Networks already exists — add notes to it with: ebb add <file>"
    assert expected in flat(result.output)
    assert len(store.subjects) == 1


def test_status_lists_sources(fakes, tmp_path):
    runner.invoke(cli.app, ["new", "Networks"])
    runner.invoke(cli.app, ["add", str(note_file(tmp_path))])

    result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == 0, result.output
    assert "Sources" in result.output
    assert "wk1.md · 0.0 MB" in result.output


def test_status_without_sources_says_how_to_add_some(fakes):
    runner.invoke(cli.app, ["new", "Networks"])
    result = runner.invoke(cli.app, ["status"])
    assert "No sources yet. Add notes with: ebb add <file>" in flat(result.output)
