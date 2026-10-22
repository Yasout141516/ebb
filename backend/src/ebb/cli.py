"""`ebb new`, `ebb add`, `ebb study`, `ebb status`.

The LLM and LangGraph stacks take seconds to import, so they are imported inside
the commands that use them; `ebb --help` and `ebb status` never load them.
"""

import atexit
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
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
from ebb.domain import Concept, NewDocument, ResumableSession, Subject
from ebb.mastery import reviews_due
from ebb.store import MAX_DOCUMENTS, DocumentRefused, Store, merge_note_concepts
from ebb.ui import (
    DISPUTE_REASONS,
    NOT_DISPUTABLE,
    PRIVACY_LINE,
    caught_up_line,
    format_ago,
    greeting,
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
    from langgraph.graph.state import CompiledStateGraph

    from ebb.llm.tutor import LangChainTutor

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


def _exhausted(error: Exception) -> NoReturn:
    from ebb.graph.nodes import EXHAUSTED_NOTICE

    console.print(f"[red]{escape(EXHAUSTED_NOTICE)}[/red]")
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
    tutor: LangChainTutor,
    subject: str,
    known: list[tuple[str, str]],
    document: NewDocument,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Ask for the concepts in one file; returns (matched names, new (name, description))."""
    from ebb.chunking import digest
    from ebb.llm.tutor import AllProvidersExhausted

    # Placeholders: the prompt only uses names and descriptions, so these ids are never stored.
    placeholder = uuid4()
    existing = [Concept(uuid4(), placeholder, name, text) for name, text in known]
    with console.status("Finding the concepts in your notes…"):
        try:
            generated = tutor.concepts_from_notes(subject, existing, digest(document.chunks))
        except AllProvidersExhausted as error:
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
    from ebb.llm.tutor import AllProvidersExhausted

    topic = topic.strip()
    if not topic:
        _fail('Give a topic, e.g. ebb new "Computer Networks"')
    settings, store = _open_store()
    user_id = settings.ebb_dev_user_id
    for existing in store.list_subjects(user_id):
        if existing.name.casefold() == topic.casefold():
            _fail(f"{existing.name} already exists — add notes to it with: ebb add <file>")
    if notes:
        _new_from_notes(settings, store, topic, notes)
        return
    tutor = _tutor(settings)
    with console.status("Mapping the topic…"):
        try:
            generated = tutor.extract_concepts(topic)
        except AllProvidersExhausted as error:
            _exhausted(error)
        except ConfigError as error:
            _fail(str(error), code=2)
    concepts = [(c.name, c.description) for c in generated.value]
    store.create_subject(user_id, topic, concepts)
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
    tutor = _tutor(settings)
    console.print(f"[dim]{escape(PRIVACY_LINE)}[/dim]")
    known: list[tuple[str, str]] = []
    reports: list[tuple[NewDocument, list[str], list[tuple[str, str]]]] = []
    for document in documents:
        matched, fresh = _find_concepts(tutor, topic, known, document)
        known += fresh
        reports.append((document, matched, fresh))
    store.create_subject_with_notes(settings.ebb_dev_user_id, topic, documents, known)
    console.print(f"[bold]{escape(topic)}[/bold]")
    for document, matched, fresh in reports:
        _print_report(document, matched, fresh)
    console.print("\nStart with: [bold]ebb study[/bold]")


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
    tutor = _tutor(settings)
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


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _pending(result: dict[str, Any]) -> dict[str, Any] | None:
    return result["__interrupt__"][0].value if "__interrupt__" in result else None


@dataclass(frozen=True)
class _Resume:
    thread_id: str
    payload: dict[str, Any] | None  # the pending question, or None to finish the cut-off step


def _offer_resume(
    store: Store,
    graph: CompiledStateGraph,
    user_id: UUID,
    found: ResumableSession,
    held: ExitStack,
    now: datetime,
) -> _Resume | None:
    """How to carry on with `found` if the learner resumes it, else None (start fresh).

    On resume the session's lock moves into `held`, so it lasts until the command exits.
    """
    with ExitStack() as trying:
        if not trying.enter_context(store.session_lock(user_id, found.thread_id)):
            console.print("That session is open in another terminal.")
            return None
        saved = graph.get_state(_config(found.thread_id))
        payload = saved.interrupts[0].value if saved.interrupts else None
        if payload is not None:
            stopped_on = payload
        elif saved.next and saved.values.get("concept"):
            # Stopped mid-step, e.g. while grading: the answer is in the state but its
            # step never finished. The stored attempt_id makes re-running it count once.
            stopped_on = {
                "concept": saved.values["concept"]["name"],
                "number": saved.values["asked_on_concept"] + 1,
            }
        else:
            stopped_on = None  # nothing to carry on with; recorded answers stay
        if stopped_on is not None:
            ago = format_ago(now - found.last_active_at)
            answer = _confirm(resume_question(stopped_on, ago))
            if answer is None:
                raise typer.Exit(0)  # leaves the session as it was, still resumable
            if answer:
                held.enter_context(trying.pop_all())
                return _Resume(found.thread_id, payload)
        store.end_session(user_id, found.session_id, now)
        return None


@app.command()
def study(
    subject: Annotated[str | None, typer.Argument(help="Subject name (default: newest).")] = None,
) -> None:
    """Run a quiz session."""
    from langgraph.types import Command

    from ebb.graph.build import build_graph
    from ebb.web import web_search

    settings, store = _open_store()
    user_id = settings.ebb_dev_user_id
    tz = _timezone(settings)
    tutor = _tutor(settings)

    store.set_timezone(user_id, tz.key)
    now = datetime.now(UTC)
    store.close_stale_sessions(user_id, now)
    saver = _checkpointer(store)
    # Learning state lives in Ebb's own tables; old checkpoints are only resume data.
    old_threads = store.threads_to_prune(user_id, now)
    for old_thread in old_threads:
        saver.delete_thread(old_thread)
    if old_threads:
        store.mark_pruned(user_id, old_threads, now)

    chosen = _subject(store, user_id, subject)
    graph = build_graph(tutor, store, saver, web=web_search(settings))
    with ExitStack() as held:
        found = store.resumable_session(user_id, chosen.id, now)
        resume = None if found is None else _offer_resume(store, graph, user_id, found, held, now)
        if resume is not None:
            config = _config(resume.thread_id)
            payload = resume.payload
        else:
            thread_id = str(uuid4())
            config = _config(thread_id)
            held.enter_context(store.session_lock(user_id, thread_id))  # a new id: always ours
            concepts = store.list_concepts(user_id, chosen.id)
            new = sum(1 for c in concepts if c.stage == 0)
            console.print(escape(greeting(len(reviews_due(concepts, now)), new)))
            payload = None
        console.print(
            f"Studying [bold]{escape(chosen.name)}[/bold]. Type q at any question to stop.\n"
        )
        try:
            if resume is not None and payload is None:
                with console.status("Thinking…"):
                    result = graph.invoke(None, config)  # finish the step that was cut off
                payload = _pending(result)
            elif resume is None:
                start = {
                    "user_id": str(user_id),
                    "subject_id": str(chosen.id),
                    "thread_id": thread_id,
                }
                with console.status("Preparing questions…"):
                    result = graph.invoke(start, config)
                payload = _pending(result)
            while payload is not None:
                render_turn(console, payload)
                reply = _ask(payload)
                with console.status("Re-checking…" if "dispute" in reply else "Thinking…"):
                    result = graph.invoke(Command(resume=reply), config)
                payload = _pending(result)
        except ConfigError as error:
            _fail(str(error), code=2)
        except KeyboardInterrupt:
            _fail("Stopped. Every answer you finished is saved.", code=130)
        render_summary(console, result, tz)
        if result.get("caught_up"):
            soonest = store.next_review_at(user_id, chosen.id)
            console.print(escape(caught_up_line(soonest, tz)))


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
