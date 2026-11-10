"""SSE keepalive plumbing shared by the streaming response builders.

Cloudflare closes idle HTTP connections (and answers 524) when an upstream
generation produces no bytes for too long.  Both streaming faces therefore
need to emit a small SSE comment (``": keepalive\\n\\n"``) while they await the
next upstream event.

The tricky part is that the first event may only appear after a long
``:generateContent`` upload/function-call round trip.  Waiting for it before
returning ``StreamingResponse`` means the headers are not flushed in time, so
the gateway never sees a 200.  :class:`KeepaliveIterator` therefore owns a
single bounded first wait: when the deadline elapses the pending ``__anext__``
task is kept alive and reused, so callers can return the response and then
continue awaiting the same task from the body while periodically yielding
keepalive comments.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from inspect import isawaitable
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")

DEFAULT_KEEPALIVE_INTERVAL_SEC = 15.0
KEEPALIVE_ENV = "AISTUDIO_SSE_KEEPALIVE_SEC"
PREREAD_WINDOW_ENV = "AISTUDIO_SSE_PREREAD_WINDOW_SEC"
KEEPALIVE_FRAME = ": keepalive\n\n"
CLOSE_AWAIT_TIMEOUT_SEC = 1.0
PREREAD_DRAIN_LIMIT = 64

# Timeout marker yielded by ``KeepaliveIterator.__anext__``.  It is a unique
# object rather than a string so a generic ``T = str`` stream can never collide
# with a real payload.
_KEEPALIVE_SENTINEL = object()
KEEPALIVE_SENTINEL: object = _KEEPALIVE_SENTINEL


class KeepaliveTimeout:
    """Sentinel returned by :meth:`KeepaliveIterator.next_event` on timeout."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<keepalive-timeout>"


KEEPALIVE_TIMEOUT = KeepaliveTimeout()


def _positive_env_float(name: str) -> float | None:
    """Read a strictly positive finite float from ``name``, else ``None``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def keepalive_interval_sec() -> float:
    """Return the configured keepalive interval, defaulting to 15 seconds.

    Invalid values (non-numeric, ``NaN``/``inf``, zero or negative) fall back
    to :data:`DEFAULT_KEEPALIVE_INTERVAL_SEC` so a bad environment override can
    never make the stream spin or hang.
    """
    value = _positive_env_float(KEEPALIVE_ENV)
    return DEFAULT_KEEPALIVE_INTERVAL_SEC if value is None else value


def preread_window_sec() -> float:
    """Return the first-event pre-read window in seconds.

    ``AISTUDIO_SSE_PREREAD_WINDOW_SEC`` overrides it independently.  When it is
    unset or invalid the value follows the keepalive interval, which itself
    defaults to 15 seconds.  A non-finite, zero or negative override falls back
    to the keepalive interval rather than risking an unbounded header wait.
    """
    value = _positive_env_float(PREREAD_WINDOW_ENV)
    if value is not None:
        return value
    return keepalive_interval_sec()


def _consume_task_outcome(task: asyncio.Future[Any]) -> None:
    """Retrieve a finished task's result/exception so asyncio never warns."""
    try:
        task.result()
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    except Exception:
        pass


class KeepaliveIterator(Generic[T]):
    """Wrap an async iterator, yielding keepalive markers while waiting.

    Only one ``__anext__`` task is ever in flight, and it is always owned by
    ``self._pending``: there is no code path that creates an untracked task.
    When it exceeds the keepalive interval :data:`KEEPALIVE_SENTINEL` is
    yielded and the task is preserved for the next call, so slow upstream
    events are never lost.  ``aclose()`` is idempotent, cancels the pending
    task with a bounded await, and then closes the underlying iterator.
    """

    def __init__(
        self,
        iterator: AsyncIterator[T],
        *,
        interval: float | None = None,
    ) -> None:
        self._iterator = iterator
        self._interval = keepalive_interval_sec() if interval is None else interval
        self._pending: asyncio.Task[T] | None = None
        self._closed = False
        self._done = False

    @property
    def interval(self) -> float:
        return self._interval

    def __aiter__(self) -> KeepaliveIterator[T]:
        return self

    def _ensure_pending(self) -> asyncio.Task[T]:
        task = self._pending
        if task is None:
            coroutine = cast("Coroutine[Any, Any, T]", self._iterator.__anext__())
            task = asyncio.create_task(coroutine)
            self._pending = task
        return task

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
        task = self._ensure_pending()
        try:
            done, _still_pending = await asyncio.wait({task}, timeout=effective)
        except asyncio.CancelledError:
            await self.aclose()
            raise
        if not done:
            # ``asyncio.wait`` does not cancel on timeout; keep the same task
            # in ``_pending`` so no upstream work is lost or leaked.
            return KEEPALIVE_TIMEOUT
        self._pending = None
        try:
            return task.result()
        except StopAsyncIteration:
            self._done = True
            raise

    async def __anext__(self) -> T | object:
        result = await self.next_event()
        if isinstance(result, KeepaliveTimeout):
            return _KEEPALIVE_SENTINEL
        return result

    async def aclose(self) -> None:
        """Cancel any pending ``__anext__`` and close the underlying iterator."""
        if self._closed:
            return
        self._closed = True
        settled = await self._cancel_pending()
        if not settled:
            # The cancelled ``__anext__`` is still unwinding.  Calling
            # ``aclose()`` now would raise "asynchronous generator is already
            # running"; the cancellation path closes it once it yields control.
            return
        aclose = getattr(self._iterator, "aclose", None)
        if callable(aclose):
            result = aclose()
            if isawaitable(result):
                await cast("Awaitable[None]", result)

    async def _cancel_pending(self) -> bool:
        """Cancel the in-flight task; return False if it outlived the bound."""
        task = self._pending
        self._pending = None
        if task is None:
            return True
        if not task.done():
            task.cancel()
        # Bound the post-cancel await so an uncooperative iterator cannot hang
        # shutdown forever.  This never cancels a normal pending ``__anext__``.
        done, _still_pending = await asyncio.wait(
            {task}, timeout=CLOSE_AWAIT_TIMEOUT_SEC
        )
        if not done:
            task.add_done_callback(_consume_task_outcome)
            return False
        _consume_task_outcome(task)
        return True


async def preread_first_rendered(
    events: KeepaliveIterator[T],
    render: Callable[[T], R | None],
    *,
    window: float,
) -> R | None:
    """Pre-read up to ``window`` seconds for the first renderable value.

    ``render`` returns ``None`` for progress-only frames (``routing_wait`` /
    ``usage``) and for events that produce no wire content; those never end the
    pre-read.  ``None`` is returned once the window is exhausted so callers can
    flush headers and keep the stream alive with keepalive comments.

    A non-positive window disables the pre-read entirely: callers must switch to
    HTTP 200 + keepalive without polling the upstream (zero-window contract).

    When the deadline expires the loop still drains frames that are already
    available (``timeout=0``) up to :data:`PREREAD_DRAIN_LIMIT`.  A stalled
    event loop can therefore never turn an already-queued terminal error into
    a "200 + keepalive" response, while a progress-only stream stays bounded.
    """
    if window <= 0:
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window
    drain_budget = PREREAD_DRAIN_LIMIT
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            if drain_budget <= 0:
                return None
            drain_budget -= 1
        item = await events.next_event(max(0.0, remaining))
        if isinstance(item, KeepaliveTimeout):
            return None
        rendered = render(item)
        if rendered is not None:
            return rendered


__all__ = [
    "CLOSE_AWAIT_TIMEOUT_SEC",
    "DEFAULT_KEEPALIVE_INTERVAL_SEC",
    "KEEPALIVE_ENV",
    "KEEPALIVE_FRAME",
    "KEEPALIVE_SENTINEL",
    "KEEPALIVE_TIMEOUT",
    "PREREAD_DRAIN_LIMIT",
    "PREREAD_WINDOW_ENV",
    "KeepaliveIterator",
    "KeepaliveTimeout",
    "keepalive_interval_sec",
    "preread_first_rendered",
    "preread_window_sec",
]
