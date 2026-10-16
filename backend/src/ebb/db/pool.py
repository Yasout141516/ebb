"""Connection pool. Autocommit is on, so `with conn.transaction():` marks every real transaction."""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ebb.config import ConfigError


def _drop_advisory_locks(conn: psycopg.Connection) -> None:
    # Runs when a connection comes back to the pool: a session lock leaked by an
    # interrupt between taking and releasing it must not outlive its holder.
    conn.execute("select pg_advisory_unlock_all()")


def make_pool(url: str, max_size: int = 8, timeout: int = 5) -> ConnectionPool:
    # One direct connection first: fails fast with a clear message instead of the
    # pool retrying in the background until its own timeout.
    try:
        psycopg.connect(url, connect_timeout=timeout).close()
    except psycopg.OperationalError as error:
        raise ConfigError(
            "Can't reach the database. Is it running? Try: docker compose up -d"
        ) from error
    # A study session holds one connection for its terminal lock the whole time, while
    # the checkpointer and the store take others. PostgresSaver needs prepare_threshold 0.
    return ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        reset=_drop_advisory_locks,
        open=True,
    )
