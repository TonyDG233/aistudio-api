"""SSE keepalive plumbing shared by the streaming response builders.

Cloudflare closes idle HTTP connections (and answers 524) when an upstream
generation produces no bytes for too long.  Both streaming faces therefore
need to emit a small SSE comment (``": keepalive\\n\\n"``) while they await the
next upstream event.

The tricky part is that the first event may only appear after a long
``:generateContent`` upload/function-call round trip.  Waiting for it before
returning ``StreamingResponse`` means the headers are not flushed in time, so
the gateway never sees a 200.  The helpers here allow a *bounded* first wait:
when the deadline elapses the pending ``__anext__`` task is kept alive and the
caller returns the response, then the body continues awaiting the same task
while periodically yielding keepalive comments.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import AsyncIterator, Awaitable, Coroutine
from inspect import isawaitable
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")

DEFAULT_KEEPALIVE_INTERVAL_SEC = 15.0
KEEPALIVE_ENV = "AISTUDIO_SSE_KEEPALIVE_SEC"
KEEPALIVE_FRAME = ": keepalive\n\n"


class KeepaliveTimeout:
    """Sentinel returned by :func:`next_with_timeout` when the wait elapsed."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<keepalive-timeout>"


KEEPALIVE_TIMEOUT = KeepaliveTimeout()


def keepalive_interval_sec() -> float:
    """Return the configured keepalive interval, defaulting to 15 seconds.

    Invalid values (non-numeric, ``NaN``/``inf``, zero or negative) fall back
    to :data:`DEFAULT_KEEPALIVE_INTERVAL_SEC` so a bad environment override can
    never make the stream spin or hang.
    """
    raw = os.environ.get(KEEPALIVE_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_KEEPALIVE_INTERVAL_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_KEEPALIVE_INTERVAL_SEC
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_KEEPALIVE_INTERVAL_SEC
    return value


async def next_with_timeout(
    iterator: AsyncIterator[T],
    timeout: float,
    *,
    pending: list[asyncio.Task[T]] | None = None,
) -> T | KeepaliveTimeout:
    """Await the next item for at most ``timeout`` seconds.

    A task is created for ``iterator.__anext__()`` and awaited with
    :func:`asyncio.wait`.  When the timeout elapses the task is **not**
    cancelled: the sentinel :data:`KEEPALIVE_TIMEOUT` is returned and, when a
    ``pending`` slot is supplied, the task stays in it so the next call can
    continue awaiting the same ``__anext__`` instead of losing work.

    On completion the task result is returned (or its exception raised,
    including ``StopAsyncIteration``).
    """
    if pending:
        task = pending[0]
    else:
        coroutine = cast("Coroutine[Any, Any, T]", iterator.__anext__())
        task = asyncio.create_task(coroutine)
        if pending is not None:
            pending.append(task)
    done, _still_pending = await asyncio.wait({task}, timeout=timeout)
    if not done:
        return KEEPALIVE_TIMEOUT
    if pending is not None:
        pending.clear()
    return task.result()


class KeepaliveIterator(Generic[T]):
    """Wrap an async iterator, yielding SSE keepalive comments while waiting.

    Only one ``__anext__`` task is ever in flight.  When it exceeds the
    keepalive interval a ``": keepalive\\n\\n"`` string is yielded and the task
    is preserved for the next call, so slow upstream events are never lost.
    ``aclose()`` cancels the pending task and closes the underlying iterator.
    """

    def __init__(
        self,
        iterator: AsyncIterator[T],
        *,
        interval: float | None = None,
    ) -> None:
        self._iterator = iterator
        self._interval = keepalive_interval_sec() if interval is None else interval
        self._pending: list[asyncio.Task[T]] = []
        self._closed = False
        self._done = False

    @property
    def interval(self) -> float:
        return self._interval

    def __aiter__(self) -> KeepaliveIterator[T]:
        return self

    async def next_event(
        self,
        timeout: float | None = None,
    ) -> T | KeepaliveTimeout:
        """Return the next item or :data:`KEEPALIVE_TIMEOUT` after ``timeout``.

        ``timeout`` defaults to the keepalive interval.  A pending ``__anext__``
        task is reused across calls, which lets a first-window deadline stop
        consuming routing frames without discarding the upstream wait.
        """
        if self._closed or self._done:
            raise StopAsyncIteration
        effective = self._interval if timeout is None else timeout
        try:
            result = await next_with_timeout(
                self._iterator, effective, pending=self._pending
            )
        except StopAsyncIteration:
            self._done = True
            raise
        except asyncio.CancelledError:
            await self.aclose()
            raise
        return result

    async def __anext__(self) -> T | str:
        result = await self.next_event()
        if isinstance(result, KeepaliveTimeout):
            return KEEPALIVE_FRAME
        return result

    async def aclose(self) -> None:
        """Cancel any pending ``__anext__`` and close the underlying iterator."""
        if self._closed:
            return
        self._closed = True
        await self._cancel_pending()
        aclose = getattr(self._iterator, "aclose", None)
        if callable(aclose):
            result = aclose()
            if isawaitable(result):
                await cast("Awaitable[None]", result)

    async def _cancel_pending(self) -> None:
        if not self._pending:
            return
        task = self._pending.pop()
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except StopAsyncIteration:
            pass
        except Exception:
            # A pending anext may have failed just after the timeout; close()
            # must stay quiet and let the exception be observed elsewhere.
            pass


__all__ = [
    "DEFAULT_KEEPALIVE_INTERVAL_SEC",
    "KEEPALIVE_ENV",
    "KEEPALIVE_FRAME",
    "KEEPALIVE_TIMEOUT",
    "KeepaliveIterator",
    "KeepaliveTimeout",
    "keepalive_interval_sec",
    "next_with_timeout",
]
