"""The request a model call serves: one user action, which may cause several calls."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID, uuid4

_current: ContextVar[tuple[UUID, UUID] | None] = ContextVar("ebb_request", default=None)


@contextmanager
def request_scope(user_id: UUID, request_id: UUID | None = None) -> Iterator[UUID]:
    """Every model call inside is tagged with one request id (fresh unless given) and this
    user. A nested scope keeps the outer request (and its user).

    Pass `request_id` to re-enter one request from a context that doesn't carry it: a lazy
    generator advanced step by step on different threads loses a contextvar between steps."""
    outer = _current.get()
    if outer is not None:
        yield outer[0]
        return
    request_id = request_id or uuid4()
    token = _current.set((request_id, user_id))
    try:
        yield request_id
    finally:
        _current.reset(token)


def current() -> tuple[UUID | None, UUID | None]:
    """The request id and user of the innermost scope, or (None, None) outside any."""
    scope = _current.get()
    return scope if scope is not None else (None, None)


def in_request[T](user_id: UUID, stream: Iterator[T]) -> Iterator[T]:
    """`stream` with every model call it makes tagged with one request id for the turn.
    The API advances a turn's generator one next() at a time, each on a worker thread with
    its own copy of the context, so the scope is entered afresh around each advance of the
    graph rather than held across the yields."""
    request_id = uuid4()
    while True:
        with request_scope(user_id, request_id):
            try:
                item = next(stream)
            except StopIteration:
                return
        yield item
