"""Connection pool. Autocommit is on, so `with conn.transaction():` marks every real transaction."""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ebb.config import ConfigError


def make_pool(url: str, max_size: int = 4, timeout: int = 5) -> ConnectionPool:
    # One direct connection first: fails fast with a clear message instead of the
    # pool retrying in the background until its own timeout.
    try:
        psycopg.connect(url, connect_timeout=timeout).close()
    except psycopg.OperationalError as error:
        raise ConfigError(
            "Can't reach the database. Is it running? Try: docker compose up -d"
        ) from error
    return ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=True,
    )
