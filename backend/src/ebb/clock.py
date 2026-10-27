"""The wall clock, as one injectable function."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
