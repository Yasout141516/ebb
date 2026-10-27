import time

import pytest

from ebb.config import ConfigError
from ebb.db.locks import advisory_lock
from ebb.db.pool import make_pool


def test_unreachable_database_fails_fast_with_a_clear_message():
    started = time.monotonic()
    with pytest.raises(ConfigError, match="docker compose up -d"):
        make_pool("postgresql://ebb:ebb@127.0.0.1:1/ebb", timeout=2)
    assert time.monotonic() - started < 5


@pytest.mark.db
def test_pool_has_no_reset_callback(pg_session_pool):
    # Locks are released by advisory_lock itself, not by a worker on every return.
    assert pg_session_pool._reset is None
    with pg_session_pool.connection() as conn:
        assert conn.prepare_threshold == 0  # as PostgresSaver requires


@pytest.mark.db
def test_lock_is_released_after_an_exception_in_the_block(pg_session_pool):
    with pytest.raises(RuntimeError):
        with advisory_lock(pg_session_pool, 424242, wait=True):
            raise RuntimeError("boom")
    with advisory_lock(pg_session_pool, 424242, wait=False) as held:
        assert held


@pytest.mark.db
def test_lock_is_released_after_keyboard_interrupt_in_the_block(pg_session_pool):
    with pytest.raises(KeyboardInterrupt):
        with advisory_lock(pg_session_pool, 424242, wait=True):
            raise KeyboardInterrupt
    with advisory_lock(pg_session_pool, "a text key", wait=True):
        pass
    with advisory_lock(pg_session_pool, 424242, wait=False) as held:
        assert held


@pytest.mark.db
def test_a_lock_not_held_returns_its_connection_after_an_exception_in_the_block(
    pg_session_pool,
):
    with advisory_lock(pg_session_pool, 424242, wait=True):
        bad_before = pg_session_pool.get_stats().get("returns_bad", 0)
        with pytest.raises(RuntimeError):
            with advisory_lock(pg_session_pool, 424242, wait=False) as held:
                assert held is False
                raise RuntimeError("busy")  # as _drive raises SessionBusy

        assert pg_session_pool.get_stats().get("returns_bad", 0) == bad_before


@pytest.mark.db
def test_lock_is_released_when_the_unlock_itself_fails(pg_session_pool):
    with advisory_lock(pg_session_pool, 424242, wait=True) as held:
        assert held
        # Break the connection under the block: the unlock then fails.
        with pg_session_pool.connection() as other:
            other.execute(
                "select pg_terminate_backend(pid) from pg_locks"
                " where locktype = 'advisory' and objid = 424242 and granted"
            )
    with advisory_lock(pg_session_pool, 424242, wait=False) as held:
        assert held
