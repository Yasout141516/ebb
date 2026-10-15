"""`ebb new`, `ebb study`, `ebb status`.

The LLM and LangGraph stacks take seconds to import, so they are imported inside
the commands that use them; `ebb --help` and `ebb status` never load them.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, NoReturn
from uuid import UUID, uuid4

import typer
from rich.console import Console
from rich.markup import escape

from ebb.config import ConfigError, Settings
from ebb.db.migrate import migrate
from ebb.db.pool import make_pool
from ebb.db.repo import PgStore
from ebb.domain import Subject
from ebb.ui import read_reply, render_summary, render_turn, status_table

if TYPE_CHECKING:
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


@app.command()
def study(
    subject: Annotated[str | None, typer.Argument(help="Subject name (default: newest).")] = None,
) -> None:
    """Run a quiz session."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from ebb.graph.build import build_graph

    settings, store = _open_store()
    tutor = _tutor(settings)
    user_id = settings.ebb_dev_user_id
    chosen = _subject(store, user_id, subject)
    graph = build_graph(tutor, store, InMemorySaver())
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    console.print(f"Studying [bold]{escape(chosen.name)}[/bold]. Type q at any question to stop.\n")
    start = {"user_id": str(user_id), "subject_id": str(chosen.id), "thread_id": thread_id}
    try:
        with console.status("Preparing questions…"):
            result = graph.invoke(start, config)
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            render_turn(console, payload)
            reply = _ask(payload)
            with console.status("Thinking…"):
                result = graph.invoke(Command(resume=reply), config)
    except ConfigError as error:
        _fail(str(error), code=2)
    except KeyboardInterrupt:
        _fail("Stopped. Every answer you finished is saved.", code=130)
    render_summary(console, result)


@app.command()
def status(
    subject: Annotated[str | None, typer.Argument(help="Subject name (default: newest).")] = None,
) -> None:
    """Show tide, stage and next review for each concept."""
    settings, store = _open_store()
    chosen = _subject(store, settings.ebb_dev_user_id, subject)
    concepts = store.list_concepts(settings.ebb_dev_user_id, chosen.id)
    console.print(f"[bold]{escape(chosen.name)}[/bold]")
    console.print(status_table(concepts, datetime.now(UTC)))
