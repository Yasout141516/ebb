import time

import psycopg
import pytest

from ebb.config import ConfigError
from ebb.db.pool import make_pool


def test_unreachable_database_fails_fast_with_a_clear_message():
    started = time.monotonic()
    with pytest.raises(ConfigError, match="docker compose up -d"):
        make_pool("postgresql://ebb:ebb@127.0.0.1:1/ebb", timeout=2)
    assert time.monotonic() - started < 5


@pytest.mark.db
def test_a_returned_connection_drops_leaked_advisory_locks(pg_session_pool):
    # As if an interrupt landed between taking a session lock and releasing it.
    with pg_session_pool.connection() as conn:
        assert conn.prepare_threshold == 0  # as PostgresSaver requires
        conn.execute("select pg_advisory_lock(424242)")
    # The pool resets returned connections in a worker thread, so wait for it.
    deadline = time.monotonic() + 5
    with psycopg.connect(pg_session_pool.conninfo, autocommit=True) as other:
        while not other.execute("select pg_try_advisory_lock(424242)").fetchone()[0]:
            assert time.monotonic() < deadline, "the leaked lock was never released"
            time.sleep(0.05)
