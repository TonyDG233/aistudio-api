"""In-memory log broadcast, daily archive sink and SSE formatting helpers.

The ring buffer is intentionally independent from the logging framework so
both synchronous logging threads and asynchronous subscribers can share one
monotonic log sequence without blocking request handling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TextIO

DEFAULT_CAPACITY = 2000
DEFAULT_QUEUE_SIZE = 500
DEFAULT_REPLAY_LIMIT = 100
DEFAULT_SUBSCRIBER_LIMIT = 8
LOG_RETENTION_DAYS = 7

LEVEL_ORDER: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_MASK = "***"

_AUTH_HEADER_RE = re.compile(
    r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?)(bearer\s+)?([^\s,;\"']+)"
)
_SET_COOKIE_RE = re.compile(r"(?i)(\bset-cookie[\"']?\s*[:=]\s*[\"']?)([^\r\n\"']+)")
_COOKIE_HEADER_RE = re.compile(r"(?i)(\bcookie[\"']?\s*[:=]\s*[\"']?)([^\r\n\"']+)")
_COOKIE_PAIR_RE = re.compile(r"([^=;\s]+)\s*=\s*([^;]*)")
_SENSITIVE_COOKIE_NAME_RE = re.compile(
    r"(?i)^(?:sapisid(?:hash)?|sid|hsid|ssid|apisid|__secure-.*|.*(?:auth|session|token).*)$"
)
_COOKIE_TOKEN_RE = re.compile(
    r"(?i)\b(sapisidhash|sapisid|hsid|ssid|apisid|sid)\s*=\s*([^;\s\"',]+)"
)
_API_KEY_RE = re.compile(
    r"(?i)(\b(?:x-goog-api-key|api[_-]?key|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s,;\"'}]+)"
)
_QUERY_KEY_RE = re.compile(r"(?i)([?&]key=)([^&\s]+)")
_BEARER_TOKEN_RE = re.compile(r"(?i)\b(bearer\s+)([a-z0-9\-._~+/=]{8,})")
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")

_ARCHIVE_NAME_RE = re.compile(r"^aistudio-(\d{8})\.log$")


def sanitize_log_message(message: str) -> str:
    """Mask credentials that are commonly present in request log messages."""
    text = str(message)

    text = _AUTH_HEADER_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2) or ''}{_MASK}", text
    )
    text = _SET_COOKIE_RE.sub(lambda match: f"{match.group(1)}{_MASK}", text)
    text = _COOKIE_HEADER_RE.sub(
        lambda match: match.group(1) + _mask_cookie_header(match.group(2)), text
    )
    text = _COOKIE_TOKEN_RE.sub(lambda match: f"{match.group(1)}={_MASK}", text)
    text = _API_KEY_RE.sub(lambda match: f"{match.group(1)}{_MASK}", text)
    text = _QUERY_KEY_RE.sub(lambda match: f"{match.group(1)}{_MASK}", text)
    text = _BEARER_TOKEN_RE.sub(lambda match: f"{match.group(1)}{_MASK}", text)
    return _GOOGLE_API_KEY_RE.sub(_MASK, text)


def _mask_cookie_header(value: str) -> str:
    def _mask_pair(match: re.Match[str]) -> str:
        name = match.group(1)
        if _SENSITIVE_COOKIE_NAME_RE.match(name):
            return f"{name}={_MASK}"
        return match.group(0)

    return _COOKIE_PAIR_RE.sub(_mask_pair, value)


def level_number(level: str) -> int:
    """Resolve a level name to its numeric value, rejecting unknown names."""
    normalized = str(level).strip().upper()
    try:
        return LEVEL_ORDER[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported log level: {level}") from exc


@dataclass(frozen=True, slots=True)
class LogEntry:
    id: int
    ts: str
    level: str
    logger: str
    message: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


@dataclass(slots=True)
class Subscription:
    id: int
    queue: asyncio.Queue[LogEntry]
    loop: asyncio.AbstractEventLoop
    dropped: int = 0
    active: bool = True

    def take_dropped(self) -> int:
        count = self.dropped
        self.dropped = 0
        return count


class SubscriptionLimitError(RuntimeError):
    """Raised when the broadcaster already has the maximum subscribers."""


class LogBroadcaster:
    """Thread-safe monotonic ring buffer with asyncio subscribers."""

    def __init__(
        self,
        capacity: int = DEFAULT_CAPACITY,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        self._capacity = capacity
        self._queue_size = queue_size
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._next_id = 0
        self._next_subscription_id = 0
        self._subscribers: dict[int, Subscription] = {}
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def oldest_id(self) -> int | None:
        with self._lock:
            return self._entries[0].id if self._entries else None

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(
        self,
        level: str,
        logger_name: str,
        message: str,
        *,
        ts: str | None = None,
    ) -> LogEntry:
        """Append one sanitized entry and fan it out without blocking callers."""
        level_name = str(level).strip().upper()
        level_number(level_name)
        timestamp = ts or datetime.now().astimezone().isoformat(timespec="seconds")
        safe_message = sanitize_log_message(message)

        with self._lock:
            self._next_id += 1
            entry = LogEntry(
                id=self._next_id,
                ts=timestamp,
                level=level_name,
                logger=logger_name,
                message=safe_message,
            )
            self._entries.append(entry)
            subscribers = list(self._subscribers.values())

        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(
                    self._deliver, subscription, entry
                )
            except RuntimeError:
                self.unsubscribe(subscription)
        return entry

    def subscribe(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        max_subscribers: int = DEFAULT_SUBSCRIBER_LIMIT,
    ) -> Subscription:
        target_loop = loop or asyncio.get_running_loop()
        with self._lock:
            if len(self._subscribers) >= max_subscribers:
                raise SubscriptionLimitError(
                    f"maximum {max_subscribers} log subscribers reached"
                )
            self._next_subscription_id += 1
            subscription = Subscription(
                id=self._next_subscription_id,
                queue=asyncio.Queue(maxsize=self._queue_size),
                loop=target_loop,
            )
            self._subscribers[subscription.id] = subscription
            return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            subscription.active = False
            self._subscribers.pop(subscription.id, None)

    def replay(
        self,
        *,
        level: str = "DEBUG",
        last_id: int | None = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> tuple[list[LogEntry], dict[str, int] | None]:
        """Return retained entries in chronological order plus an optional gap."""
        threshold = level_number(level)
        with self._lock:
            entries = list(self._entries)

        oldest = entries[0].id if entries else None
        gap: dict[str, int] | None = None
        if last_id is not None and oldest is not None and last_id < oldest - 1:
            gap = {"last_id": last_id, "oldest_id": oldest}

        if last_id is not None:
            entries = [entry for entry in entries if entry.id > last_id]
        entries = [entry for entry in entries if LEVEL_ORDER[entry.level] >= threshold]
        if limit <= 0:
            return [], gap
        return entries[-limit:], gap

    def _deliver(self, subscription: Subscription, entry: LogEntry) -> None:
        if not subscription.active:
            return
        try:
            subscription.queue.put_nowait(entry)
        except asyncio.QueueFull:
            subscription.dropped += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                subscription.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                subscription.queue.put_nowait(entry)


class BroadcastHandler(logging.Handler):
    """Logging handler that feeds records into the shared broadcaster."""

    def __init__(self, broadcaster: LogBroadcaster | None = None) -> None:
        super().__init__()
        self.broadcaster = broadcaster if broadcaster is not None else get_broadcaster()
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = (
                datetime.fromtimestamp(record.created)
                .astimezone()
                .isoformat(timespec="seconds")
            )
            self.broadcaster.publish(
                record.levelname,
                record.name,
                record.getMessage(),
                ts=timestamp,
            )
        except Exception:
            self.handleError(record)


def resolve_log_dir(log_dir: str | Path | None = None) -> Path:
    """Resolve the archive directory, honoring AISTUDIO_LOG_DIR."""
    if log_dir is not None:
        path = Path(log_dir)
    else:
        override = os.getenv("AISTUDIO_LOG_DIR")
        if override and override.strip():
            path = Path(override.strip())
        else:
            from aistudio_api.config import resolve_data_dir

            path = resolve_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def archive_file_name(day: date) -> str:
    return f"aistudio-{day.strftime('%Y%m%d')}.log"


def daily_log_path(day: date, *, log_dir: str | Path | None = None) -> Path:
    return resolve_log_dir(log_dir) / archive_file_name(day)


def cleanup_old_logs(
    log_dir: str | Path | None = None,
    *,
    retention_days: int = LOG_RETENTION_DAYS,
    today: date | None = None,
) -> list[Path]:
    """Delete archive files outside the retention window."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    directory = resolve_log_dir(log_dir)
    current = today or date.today()
    cutoff = current - timedelta(days=retention_days - 1)
    removed: list[Path] = []

    for path in directory.glob("aistudio-*.log"):
        match = _ARCHIVE_NAME_RE.match(path.name)
        if match is None:
            continue
        try:
            file_day = datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if file_day < cutoff:
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path)
    return removed


class DailyLogFileHandler(logging.Handler):
    """Append sanitized records to data/logs/aistudio-YYYYMMDD.log."""

    def __init__(
        self,
        log_dir: str | Path | None = None,
        *,
        retention_days: int = LOG_RETENTION_DAYS,
        date_factory: Callable[[], date] = date.today,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__()
        self._log_dir = Path(log_dir) if log_dir is not None else None
        self._retention_days = retention_days
        self._date_factory = date_factory
        self._clock = clock
        self._stream: TextIO | None = None
        self._current_day: date | None = None
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self._date_factory()
            if self._stream is None or day != self._current_day:
                self._open_for_day(day)
            safe_message = sanitize_log_message(record.getMessage())
            timestamp = self._clock().strftime("%Y-%m-%d %H:%M:%S")
            assert self._stream is not None
            self._stream.write(
                f"{timestamp} {record.levelname} {record.name} {safe_message}\n"
            )
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self._close_stream()
        super().close()

    def _open_for_day(self, day: date) -> None:
        self._close_stream()
        directory = resolve_log_dir(self._log_dir)
        cleanup_old_logs(
            directory,
            retention_days=self._retention_days,
            today=day,
        )
        self._stream = (directory / archive_file_name(day)).open("a", encoding="utf-8")
        self._current_day = day

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None
        self._current_day = None


_shared_broadcaster = LogBroadcaster()


def get_broadcaster() -> LogBroadcaster:
    return _shared_broadcaster
