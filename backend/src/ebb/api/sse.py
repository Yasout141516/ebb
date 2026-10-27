"""Server-Sent Events from a blocking iterator, pulled off the event loop."""

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import anyio
from fastapi.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send


def close(events: Iterator[Any]) -> None:
    """Close `events` if it can be closed (a generator runs its cleanup)."""
    method = getattr(events, "close", None)
    if method is not None:
        method()


async def _pull(events: Iterator[Any], encode: Callable[[Any], bytes]) -> AsyncIterator[bytes]:
    while (event := await run_in_threadpool(next, events, None)) is not None:
        yield encode(event)


class _EventStream(EventSourceResponse):
    def __init__(
        self, events: Iterator[Any], encode: Callable[[Any], bytes], source: Iterator[Any] | None
    ) -> None:
        self._events, self._source = events, source
        self._chunks = _pull(events, encode)
        super().__init__(self._chunks, headers={"Cache-Control": "no-cache"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # However the response ended, a reader gone mid-stream included: close the
            # events here, in a worker thread. Closing a generator runs its cleanup, which
            # may block; left to garbage collection, it would run on the event loop.
            with anyio.CancelScope(shield=True):
                await self._chunks.aclose()
                await run_in_threadpool(self._close)

    def _close(self) -> None:
        close(self._events)
        if self._source is not None:
            close(self._source)


def event_stream[T](
    events: Iterator[T], encode: Callable[[T], bytes], source: Iterator[Any] | None = None
) -> EventSourceResponse:
    """Each event, encoded. A reader that goes away stops the pulling, and `events` is
    closed, if it can be, once the response is over; so is `source`, an iterator `events`
    reads from: if the response never starts, `events` was never advanced, and closing it
    alone would leave `source` open."""
    return _EventStream(events, encode, source)
