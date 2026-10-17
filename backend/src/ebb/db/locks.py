"""Session-level advisory locks. Each belongs to one connection, held for the whole block."""

from collections.abc import Iterator
from contextlib import contextmanager, suppress

import psycopg
from psycopg_pool import ConnectionPool


@contextmanager
def advisory_lock(pool: ConnectionPool, key: int | str, *, wait: bool) -> Iterator[bool]:
    """Hold the advisory lock `key` for the block and yield whether it is held.

    A text key is turned into a stable signed 64-bit one with hashtextextended. With
    wait=True this blocks until the lock is free (and yields True); otherwise it yields
    False at once if another connection holds it.
    """
    expression = "%s" if isinstance(key, int) else "hashtextextended(%s, 0)"
    with pool.connection() as conn:
        if wait:
            conn.execute(f"select pg_advisory_lock({expression})", (key,))
            held = True
        else:
            held = conn.execute(
                f"select pg_try_advisory_lock({expression}) as held", (key,)
            ).fetchone()["held"]
        try:
            yield held
        finally:
            # A dead connection holds no lock; don't let it hide the error that got here.
            if held:
                with suppress(psycopg.OperationalError):
                    conn.execute(f"select pg_advisory_unlock({expression})", (key,))
