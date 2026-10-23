"""The Postgres checkpointer, shared by the CLI and the API."""

from langgraph.checkpoint.postgres import PostgresSaver

from ebb.db.migrate import migration_lock
from ebb.db.repo import PgStore


def postgres_saver(store: PgStore) -> PostgresSaver:
    saver = PostgresSaver(store.pool)
    # setup() creates or upgrades its own tables; two first runs at once must not race.
    with migration_lock(store.pool):
        saver.setup()
    return saver
