import time

import pytest

from ebb.config import ConfigError
from ebb.db.pool import make_pool


def test_unreachable_database_fails_fast_with_a_clear_message():
    started = time.monotonic()
    with pytest.raises(ConfigError, match="docker compose up -d"):
        make_pool("postgresql://ebb:ebb@127.0.0.1:1/ebb", timeout=2)
    assert time.monotonic() - started < 5
