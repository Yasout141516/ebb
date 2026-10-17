"""`ebb new`, `ebb study`, `ebb status`.

The LLM and LangGraph stacks take seconds to import, so they are imported inside
the commands that use them; `ebb --help` and `ebb status` never load them.
"""

import atexit
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
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
from ebb.domain import ResumableSession, Subject
from ebb.store import Store
from ebb.ui import (
    caught_up_line,
    format_ago,
    greeting,
    read_reply,
    read_yes_no,
    render_summary,
    render_turn,
    resume_question,
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
console = Console()


def _fail(message: str, code: int = 1) -> NoReturn:
    console.print(f"[red]{escape(message)}[/red]")
    raise typer.Exit(code)


def _open_store() -> tuple[Settings, PgStore]:
    try:
        settings = Settings()
        pool = make_pool(settings.database_url)
    except ConfigError as error:
        _fail(str(error), code=2)
    # Close the pool before interpreter shutdown, or its worker threads print a traceback.
    atexit.register(pool.close)
    with pool.connection() as conn:
        migrate(conn)
    store = PgStore(pool)
    store.ensure_profile(settings.ebb_dev_user_id)
    return settings, store


def _tutor(settings: Settings) -> LangChainTutor:
    from ebb.llm.tutor import LangChainTutor

    try:
        return LangChainTutor.from_settings(settings)
    except ConfigError as error:
        _fail(str(error), code=2)


def _timezone(settings: Settings) -> ZoneInfo:
    try:
        return resolve_timezone(settings.ebb_timezone)
    except ConfigError as error:
        _fail(str(error), code=2)


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


def _ask(payload: dict[str, Any]) -> dict[str, Any]:
    options = payload["question"]["options"]
    while True:
        try:
            raw = console.input("[bold]› [/bold]")
        except KeyboardInterrupt, EOFError:
            return {"quit": True}
        reply = read_reply(raw, options)
        if reply is None:
            console.print(
                "[dim]Type 1-4, or q to stop.[/dim]"
                if options
                else "[dim]Type an answer, or q to stop.[/dim]"
            )
            continue
        if "answer" in reply:
            reply["attempt_id"] = str(uuid4())
        return reply


@app.command()
def new(topic: Annotated[str, typer.Argument(help="What you are studying.")]) -> None:
    """Create a subject from a topic: Ebb lists its core concepts."""
    from ebb.graph.nodes import EXHAUSTED_NOTICE
    from ebb.llm.tutor import AllProvidersExhausted

    topic = topic.strip()
    if not topic:
        _fail('Give a topic, e.g. ebb new "Computer Networks"')
    settings, store = _open_store()
    tutor = _tutor(settings)
    with console.status("Mapping the topic…"):
        try:
            generated = tutor.extract_concepts(topic)
        except AllProvidersExhausted as error:
            console.print(f"[red]{escape(EXHAUSTED_NOTICE)}[/red]")
            cause = str(error).strip()
            if cause:
                console.print(f"[dim]{escape(cause.splitlines()[0])}[/dim]")
            raise typer.Exit(1) from error
        except ConfigError as error:
            _fail(str(error), code=2)
    concepts = [(c.name, c.description) for c in generated.value]
    store.create_subject(settings.ebb_dev_user_id, topic, concepts)
    console.print(f"[bold]{escape(topic)}[/bold] · {len(concepts)} concepts")
    for number, (name, description) in enumerate(concepts, start=1):
        console.print(f"  {number}. [bold]{escape(name)}[/bold] [dim]— {escape(description)}[/dim]")
    console.print("\nStart with: [bold]ebb study[/bold]")


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
    graph = build_graph(tutor, store, saver)
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
            due = store.count_due(user_id, chosen.id, now)
            console.print(escape(greeting(due, store.count_new(user_id, chosen.id))))
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
                with console.status("Thinking…"):
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
