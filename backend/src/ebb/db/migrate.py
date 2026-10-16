"""Apply db/migrations/*.sql in filename order, once each."""

from importlib.resources import files

from psycopg import Connection

MIGRATION_LOCK = 7_231_001  # arbitrary constant for pg_advisory_xact_lock


def migrate(conn: Connection, until: str | None = None) -> list[str]:
    """Apply pending migrations (up to file name `until`, if given); return the names applied.

    Safe to run concurrently.
    """
    folder = files("ebb.db").joinpath("migrations")
    scripts = sorted(
        (entry for entry in folder.iterdir() if entry.name.endswith(".sql")),
        key=lambda entry: entry.name,
    )
    applied: list[str] = []
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        conn.execute(
            "create table if not exists schema_migrations ("
            " version text primary key,"
            " applied_at timestamptz not null default now())"
        )
        done = {row["version"] for row in conn.execute("select version from schema_migrations")}
        for script in scripts:
            if script.name in done:
                continue
            if until is not None and script.name > until:
                break
            conn.execute(script.read_text(encoding="utf-8"))
            conn.execute("insert into schema_migrations (version) values (%s)", (script.name,))
            applied.append(script.name)
    return applied
