"""`ebb new`, `ebb add`, `ebb study`, `ebb status`.

The LLM and LangGraph stacks take seconds to import, so they are imported inside
the commands that use them; `ebb --help` and `ebb status` never load them.
"""

import atexit
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.markup import escape

from ebb.config import ConfigError, Settings, resolve_timezone
from ebb.db.migrate import migrate, migration_lock
from ebb.db.pool import make_pool
from ebb.db.repo import PgStore
from ebb.domain import Concept, NewDocument, Subject
from ebb.store import (
    MAX_DOCUMENTS,
    DocumentRefused,
    SubjectExists,
    merge_note_concepts,
)
from ebb.ui import (
    DISPUTE_REASONS,
    NOT_DISPUTABLE,
    PRIVACY_LINE,
    caught_up_line,
    format_ago,
    notes_report,
    read_reply,
    read_yes_no,
    render_summary,
    render_turn,
    resume_question,
    source_lines,
    status_table,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from ebb.llm.tutor import LangChainTutor, Tutor, TutorUnavailable
    from ebb.study import Event, Finished, Pending, StudySessions

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Ebb: an AI tutor that quizzes you and brings ideas back before you forget them.",
)
eval_app = typer.Typer(no_args_is_help=True, help="Build and run the grading eval.")
app.add_typer(eval_app, name="eval")
console = Console()


def _fail(message: str, code: int = 1) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code)


def _configured[T](make: Callable[[], T]) -> T:
    """`make()`; a ConfigError it raises exits 2 with its message."""
    try:
        return make()
    except ConfigError as error:
        _fail(str(error), code=2)


def _open_store() -> tuple[Settings, PgStore]:
    settings = _configured(Settings)
    pool = _configured(lambda: make_pool(settings.database_url))
    # Close the pool before interpreter shutdown, or its worker threads print a traceback.
    atexit.register(pool.close)
    with pool.connection() as conn:
        migrate(conn)
    store = PgStore(pool)
    store.ensure_profile(settings.ebb_dev_user_id)
    return settings, store


def _tutor(settings: Settings, fallback: bool = True) -> LangChainTutor:
    from ebb.llm.tutor import LangChainTutor

    return _configured(lambda: LangChainTutor.from_settings(settings, fallback))


def _capped(tutor: Tutor, settings: Settings, store: PgStore) -> Tutor:
    """The learner-facing tutor behind the daily cap on AI calls."""
    from ebb.llm.quota import CappedTutor

    return CappedTutor(
        tutor,
        store,
        settings.ebb_dev_user_id,
        settings.ebb_daily_ai_calls,
        _timezone(settings),
        lambda: datetime.now(UTC),
    )


def _timezone(settings: Settings) -> ZoneInfo:
    return _configured(lambda: resolve_timezone(settings.ebb_timezone))


def _subject(store: PgStore, user_id: UUID, name: str | None) -> Subject:
    subjects = store.list_subjects(user_id)
    if not subjects:
        _fail('No subjects yet. Create one with: ebb new "Computer Networks"')
    if name is None:
        return subjects[0]
    for subject in subjects:
        if subject.name.casefold() == name.strip().casefold():
            return subject
    _fail(f"No subject called {name!r}. You have: " + ", ".join(s.name for s in subjects))


def _dispute_dialog() -> dict[str, Any] | None:
    """Ask why the grade is wrong; None if Ctrl+C or end of input cancels it."""
    console.print("Why do you think the grade is wrong?")
    for number, (_, text) in enumerate(DISPUTE_REASONS, start=1):
        console.print(f"  {number}. {text}")
    try:
        while True:
            choice = console.input("[bold]› [/bold]").strip()
            if choice in {"1", "2", "3", "4"}:
                break
        explanation = console.input("Explain (optional, Enter to skip) › ").strip()
    except KeyboardInterrupt, EOFError:
        console.print()
        return None
    reason = DISPUTE_REASONS[int(choice) - 1][0]
    return {"dispute": {"id": str(uuid4()), "reason": reason, "explanation": explanation}}


def _ask(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload["question"]["options"]
    can_dispute = payload.get("can_dispute", False)
    while True:
        try:
            raw = console.input("[bold]› [/bold]")
        except KeyboardInterrupt, EOFError:
            return {"quit": True}
        reply = read_reply(raw, options, can_dispute)
        if reply is None:
            console.print(
                "[dim]Type 1-4, or q to stop.[/dim]"
                if options
                else "[dim]Type an answer, or q to stop.[/dim]"
            )
            continue
        if "dispute_request" in reply:
            if not can_dispute:
                console.print(f"[dim]{escape(NOT_DISPUTABLE)}[/dim]")
                continue
            dispute = _dispute_dialog()
            if dispute is None:
                continue
            return dispute
        if "answer" in reply:
            reply["attempt_id"] = str(uuid4())
        return reply


def _exhausted(error: TutorUnavailable, notice: str | None = None) -> NoReturn:
    """A tutor call outside the graph failed or was refused: the notice (the error's own
    unless given), then exit 1."""
    console.print(f"[red]{escape(notice or error.notice)}[/red]")
    cause = str(error).strip()
    if cause:
        console.print(f"[dim]{escape(cause.splitlines()[0])}[/dim]")
    raise typer.Exit(1) from error


def _in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _prepare(path: Path) -> NewDocument:
    """Run every check on a notes file; a refusal exits 1 with its message."""
    from ebb.notes import NotesError, prepare

    try:
        return prepare(path)
    except NotesError as error:
        message = str(error)
        if message.startswith("Can't open") and _in_docker():
            message += "\nIn Docker, put files in the notes/ folder and use notes/<file>."
        _fail(message)


def _find_concepts(
    tutor: Tutor,
    subject: str,
    known: list[tuple[str, str]],
    document: NewDocument,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Ask for the concepts in one file; returns (matched names, new (name, description))."""
    from ebb.chunking import digest
    from ebb.llm.tutor import NOTES_REFUSED_NOTICE, TutorRefused, TutorUnavailable

    # Placeholders: the prompt only uses names and descriptions, so these ids are never stored.
    placeholder = uuid4()
    existing = [Concept(uuid4(), placeholder, name, text) for name, text in known]
    with console.status("Finding the concepts in your notes…"):
        try:
            generated = tutor.concepts_from_notes(subject, existing, digest(document.chunks))
        except TutorRefused as error:
            _exhausted(error, NOTES_REFUSED_NOTICE)
        except TutorUnavailable as error:
            _exhausted(error)
        except ConfigError as error:
            _fail(str(error), code=2)
    found = [(c.name, c.description, c.same_as) for c in generated.value]
    return merge_note_concepts([name for name, _ in known], found)


def _print_report(document: NewDocument, matched: list[str], fresh: list[tuple[str, str]]) -> None:
    pages, chunks = document.page_count, len(document.chunks)
    for line in notes_report(document.filename, pages, chunks, matched, fresh):
        console.print(escape(line))


@app.command()
def new(
    topic: Annotated[str, typer.Argument(help="What you are studying.")],
    notes: Annotated[
        list[Path] | None,
        typer.Option("--notes", help="A PDF, TXT or MD file to build the concepts from."),
    ] = None,
) -> None:
    """Create a subject from a topic, or from your notes: Ebb lists its core concepts."""
    from ebb.llm.tutor import TutorUnavailable

    topic = topic.strip()
    if not topic:
        _fail('Give a topic, e.g. ebb new "Computer Networks"')
    settings, store = _open_store()
    user_id = settings.ebb_dev_user_id
    for existing in store.list_subjects(user_id):
        if existing.name.casefold() == topic.casefold():
            _fail(_exists_copy(existing.name))
    if notes:
        _new_from_notes(settings, store, topic, notes)
        return
    tutor = _capped(_tutor(settings), settings, store)
    with console.status("Mapping the topic…"):
        try:
            generated = tutor.extract_concepts(topic)
        except TutorUnavailable as error:
            _exhausted(error)
        except ConfigError as error:
            _fail(str(error), code=2)
    concepts = [(c.name, c.description) for c in generated.value]
    try:
        store.create_subject(user_id, topic, concepts)
    except SubjectExists:  # another terminal got there first
        _fail(_exists_copy(topic))
    console.print(f"[bold]{escape(topic)}[/bold] · {len(concepts)} concepts")
    for number, (name, description) in enumerate(concepts, start=1):
        console.print(f"  {number}. [bold]{escape(name)}[/bold] [dim]— {escape(description)}[/dim]")
    console.print("\nStart with: [bold]ebb study[/bold]")


def _new_from_notes(settings: Settings, store: PgStore, topic: str, paths: list[Path]) -> None:
    """All or nothing: every file is checked, then every concept call made, before any save."""
    if len(paths) > MAX_DOCUMENTS:
        _fail(f"{topic} can have at most {MAX_DOCUMENTS} files.")
    documents: list[NewDocument] = []
    for path in paths:
        document = _prepare(path)
        if any(d.sha256 == document.sha256 for d in documents):
            _fail(f"{document.filename} is already in {topic}.")
        documents.append(document)
    tutor = _capped(_tutor(settings), settings, store)
    console.print(f"[dim]{escape(PRIVACY_LINE)}[/dim]")
    known: list[tuple[str, str]] = []
    reports: list[tuple[NewDocument, list[str], list[tuple[str, str]]]] = []
    for document in documents:
        matched, fresh = _find_concepts(tutor, topic, known, document)
        known += fresh
        reports.append((document, matched, fresh))
    try:
        store.create_subject_with_notes(settings.ebb_dev_user_id, topic, documents, known)
    except SubjectExists:  # another terminal got there first
        _fail(_exists_copy(topic))
    console.print(f"[bold]{escape(topic)}[/bold]")
    for document, matched, fresh in reports:
        _print_report(document, matched, fresh)
    console.print("\nStart with: [bold]ebb study[/bold]")


def _exists_copy(name: str) -> str:
    return f"{name} already exists — add notes to it with: ebb add <file>"


def _full_copy(subject: Subject) -> str:
    return f"{subject.name} already has {MAX_DOCUMENTS} of {MAX_DOCUMENTS} files."


def _duplicate_copy(subject: Subject, document: NewDocument) -> str:
    return f"{document.filename} is already in {subject.name}."


@app.command()
def add(
    path: Annotated[Path, typer.Argument(help="A PDF, TXT or MD file of your notes.")],
    subject: Annotated[str | None, typer.Argument(help="Subject name (default: newest).")] = None,
) -> None:
    """Add a notes file to a subject: Ebb finds the concepts in it and cites it."""
    settings, store = _open_store()
    user_id = settings.ebb_dev_user_id
    chosen = _subject(store, user_id, subject)
    document = _prepare(path)
    if store.count_documents(user_id, chosen.id) >= MAX_DOCUMENTS:
        _fail(_full_copy(chosen))
    if store.has_document(user_id, chosen.id, document.sha256):
        _fail(_duplicate_copy(chosen, document))
    tutor = _capped(_tutor(settings), settings, store)
    console.print(f"[dim]{escape(PRIVACY_LINE)}[/dim]")
    known = [(c.name, c.description) for c in store.list_concepts(user_id, chosen.id)]
    matched, fresh = _find_concepts(tutor, chosen.name, known, document)
    try:
        store.add_document(user_id, chosen.id, document, fresh)
    except DocumentRefused as error:  # another terminal got there first
        _fail(_full_copy(chosen) if error.reason == "full" else _duplicate_copy(chosen, document))
    _print_report(document, matched, fresh)


def _checkpointer(store: PgStore) -> BaseCheckpointSaver:
    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver(store.pool)
    # setup() creates or upgrades its own tables; two first runs at once must not race.
    with migration_lock(store.pool):
        saver.setup()
    return saver


def _confirm(question: str) -> bool | None:
    """Ask until the answer is yes or no; None on end of input or Ctrl+C."""
    while True:
        try:
            raw = console.input(f"{escape(question)} ")
        except KeyboardInterrupt, EOFError:
            console.print()
            return None
        answer = read_yes_no(raw)
        if answer is not None:
            return answer


def _resumed(
    sessions: StudySessions, user_id: UUID, subject: Subject, driver: str, held: ExitStack
) -> str | None:
    """The thread of the subject's open session if the learner resumes it, else None (start
    fresh). A resumed session stays held in `held` until the command exits."""
    from ebb.study import BUSY

    offer = sessions.offer(user_id, subject.id, driver)
    if offer is None:
        return None
    if offer.busy:
        console.print(escape(BUSY))
        return None
    stopped_on = {"concept": offer.concept, "number": offer.number}
    ago = format_ago(sessions.now() - offer.last_active_at)
    answer = _confirm(resume_question(stopped_on, ago))
    if answer is None:
        raise typer.Exit(0)  # leaves the session as it was, still resumable
    if not answer:
        sessions.discard(user_id, offer.thread_id)
        return None
    if not held.enter_context(sessions.hold(user_id, offer.thread_id)):
        console.print(escape(BUSY))  # taken while the learner was deciding
        return None
    return offer.thread_id


def _until_turn(events: Iterator[Event], doing: str) -> Pending | Finished:
    """Show each step of a graph run until it stops at a question or the summary."""
    from ebb.study import Step

    with console.status(doing) as status:
        for event in events:
            if not isinstance(event, Step):
                return event
            status.update(escape(event.label))
    raise RuntimeError("the session stopped without a question or a summary")


@app.command()
def study(
    subject: Annotated[str | None, typer.Argument(help="Subject name (default: newest).")] = None,
) -> None:
    """Run a quiz session."""
    from ebb.graph.build import build_graph
    from ebb.study import Finished, StudySessions
    from ebb.web import web_search

    settings, store = _open_store()
    user_id = settings.ebb_dev_user_id
    tz = _timezone(settings)
    tutor = _capped(_tutor(settings), settings, store)

    store.set_timezone(user_id, tz.key)
    saver = _checkpointer(store)
    graph = build_graph(tutor, store, saver, web=web_search(settings))
    sessions = StudySessions(store, graph, saver, lambda: datetime.now(UTC))
    sessions.housekeeping(user_id)

    chosen = _subject(store, user_id, subject)
    driver = f"cli:{uuid4()}"
    with ExitStack() as held:
        thread_id = _resumed(sessions, user_id, chosen, driver, held)
        if thread_id is not None:
            events = sessions.resume(user_id, thread_id, driver)
            doing = "Thinking…"  # finishing a cut-off step, if there is one
        else:
            thread_id, text, events = sessions.start(user_id, chosen.id, driver)
            held.enter_context(sessions.hold(user_id, thread_id))  # a new id: always ours
            console.print(escape(text))
            doing = "Preparing questions…"
        console.print(
            f"Studying [bold]{escape(chosen.name)}[/bold]. Type q at any question to stop.\n"
        )
        try:
            turn = _until_turn(events, doing)
            while not isinstance(turn, Finished):
                render_turn(console, turn.payload)
                reply = _ask(turn.payload)
                events = sessions.reply(user_id, thread_id, driver, reply)
                turn = _until_turn(events, "Re-checking…" if "dispute" in reply else "Thinking…")
        except ConfigError as error:
            _fail(str(error), code=2)
        except KeyboardInterrupt:
            _fail("Stopped. Every answer you finished is saved.", code=130)
        render_summary(console, {**turn.final, "results": turn.results}, tz)
        if turn.caught_up:
            console.print(escape(caught_up_line(turn.next_review_at, tz)))


@app.command()
def status(
    subject: Annotated[str | None, typer.Argument(help="Subject name (default: newest).")] = None,
) -> None:
    """Show tide, stage and next review for each concept."""
    settings, store = _open_store()
    tz = _timezone(settings)
    chosen = _subject(store, settings.ebb_dev_user_id, subject)
    concepts = store.list_concepts(settings.ebb_dev_user_id, chosen.id)
    console.print(f"[bold]{escape(chosen.name)}[/bold]")
    console.print(status_table(concepts, datetime.now(UTC), tz))
    documents = store.list_documents(settings.ebb_dev_user_id, chosen.id)
    console.print("\n[bold]Sources[/bold]")
    if documents:
        for line in source_lines(documents):
            console.print(f"  {escape(line)}")
    else:
        console.print("  No sources yet. Add notes with: ebb add <file>")


def _eval_writer(settings: Settings) -> LangChainTutor:
    from ebb.llm.tutor import LangChainTutor

    return _configured(lambda: LangChainTutor.writer(settings))


def _eval_grader(settings: Settings) -> LangChainTutor:
    """The primary model alone, so one run measures one model."""
    return _tutor(settings, fallback=False)


def _pace(seconds: float) -> None:
    time.sleep(seconds)


def _today() -> date:
    return date.today()


def _dispute_counts() -> dict[str, int] | None:
    """Disputes raised, changed, voided and upheld; None when the database is down."""
    try:
        settings, store = _open_store()
    except typer.Exit:
        return None
    return store.dispute_counts(settings.ebb_dev_user_id)


@eval_app.command("build")
def eval_build(
    dataset: Annotated[Path, typer.Option(help="Where to write the cases.")] = Path(
        "evals/grading.jsonl"
    ),
    notes: Annotated[Path, typer.Option(help="Notes for the grounded cases.")] = Path(
        "evals/sample_notes.md"
    ),
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing set.")] = False,
) -> None:
    """Have the writer model generate the labelled cases (needs MISTRAL_API_KEY)."""
    from ebb.evals import EVAL_TOPICS, GROUNDED_CASES, build_cases, save_cases
    from ebb.notes import NotesError

    if dataset.exists() and not force:
        _fail(f"{dataset} exists — pass --force to rebuild it.")
    writer = _eval_writer(_configured(Settings))
    expected = len(EVAL_TOPICS) + GROUNDED_CASES
    console.print(f"Writing {expected} cases; this takes a few minutes.")
    try:
        cases = build_cases(writer, EVAL_TOPICS, notes, GROUNDED_CASES, sleep=_pace)
    except NotesError as error:
        _fail(str(error))
    save_cases(cases, dataset)
    console.print(f"Wrote {len(cases)} cases to {escape(str(dataset))}.")
    if len(cases) < expected:
        console.print(
            f"[yellow]{expected - len(cases)} skipped: the writer failed on them.[/yellow]"
        )


@eval_app.command("run")
def eval_run(
    dataset: Annotated[Path, typer.Option(help="The cases to grade.")] = Path(
        "evals/grading.jsonl"
    ),
    results: Annotated[Path, typer.Option(help="Folder for the dated report.")] = Path(
        "evals/results"
    ),
) -> None:
    """Grade every case with the primary model and write a dated report."""
    from ebb.evals import agreement_line, load_cases, report_markdown, run_cases, score

    if not dataset.is_file():
        _fail(f"{dataset} not found. Build it first with: ebb eval build")
    cases = load_cases(dataset)
    settings = _configured(Settings)
    model = settings.ebb_primary_model
    grader = _eval_grader(settings)

    def progress(number: int, total: int) -> None:
        console.print(f"Grading {number}/{total}…")

    grades = _configured(lambda: run_cases(grader, cases, sleep=_pace, progress=progress))
    result = score(cases, grades)
    writer = cases[0].writer_model if cases else "the writer model"
    text = report_markdown(result, model, _today(), _dispute_counts(), writer)
    results.mkdir(parents=True, exist_ok=True)
    path = results / f"{_today().isoformat()}-{model.replace('/', '-')}.md"
    path.write_text(text, encoding="utf-8")
    console.print(escape(agreement_line(result)))
    console.print(f"Report: {escape(str(path))}")
