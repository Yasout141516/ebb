import os
from collections.abc import Iterator

import pytest
from psycopg_pool import ConnectionPool

from ebb.config import ConfigError
from ebb.db.migrate import migrate
from ebb.db.pool import make_pool

DEFAULT_TEST_URL = "postgresql://ebb:ebb@localhost:5432/ebb_test"


@pytest.fixture(scope="session")
def pg_session_pool() -> Iterator[ConnectionPool]:
    """One pool for the whole run; the reachability check is paid once.

    Skips when no database is reachable, unless EBB_REQUIRE_DB is set (as in CI),
    where a missing database must fail rather than silently skip.
    """
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)
    try:
        pool = make_pool(url, max_size=2, timeout=2)
    except ConfigError:
        if os.environ.get("EBB_REQUIRE_DB"):
            pytest.fail(f"EBB_REQUIRE_DB is set but {url} is unreachable")
        pytest.skip("Postgres not reachable; start it with: docker compose up -d")
    yield pool
    pool.close()


@pytest.fixture
def pg_pool(pg_session_pool: ConnectionPool) -> ConnectionPool:
    """The session pool, on an empty and freshly migrated schema."""
    with pg_session_pool.connection() as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
        migrate(conn)
    return pg_session_pool
