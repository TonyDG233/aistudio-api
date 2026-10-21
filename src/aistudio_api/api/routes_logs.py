"""P5 log streaming (SSE) and archive export routes."""

from __future__ import annotations

import asyncio
import json
import tempfile
import zipfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from aistudio_api.infrastructure.utils.log_broadcast import (
    DEFAULT_REPLAY_LIMIT,
    LogBroadcaster,
    LogEntry,
    Subscription,
    SubscriptionLimitError,
    archive_file_name,
    get_broadcaster,
    level_number,
    resolve_log_dir,
)

protected_router = APIRouter()

HEARTBEAT_SECONDS = 15.0
MAX_EXPORT_DAYS = 7


def prepare_stream(
    broadcaster: LogBroadcaster,
    *,
    level: str,
    last_id: int | None,
) -> tuple[Subscription, list[LogEntry], dict[str, int] | None]:
    """Validate the level, subscribe first, then snapshot replay entries."""
    level_number(level)
    subscription = broadcaster.subscribe()
    replay, gap = broadcaster.replay(
        level=level, last_id=last_id, limit=DEFAULT_REPLAY_LIMIT
    )
    return subscription, replay, gap


def _format_event(
    event: str, data: dict[str, object], event_id: int | None = None
) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    prefix = f"event: {event}\n"
    if event_id is not None:
        prefix += f"id: {event_id}\n"
    return f"{prefix}data: {payload}\n\n"


def format_log_event(entry: LogEntry) -> str:
    return _format_event("log", entry.as_dict(), entry.id)


def format_gap_event(gap: dict[str, int]) -> str:
    return _format_event("gap", dict(gap))


def format_dropped_event(count: int) -> str:
    return _format_event("dropped", {"dropped": count})


async def stream_subscription(
    broadcaster: LogBroadcaster,
    subscription: Subscription,
    replay: list[LogEntry],
    gap: dict[str, int] | None,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncGenerator[str, None]:
    """Yield replay, live entries, dropped notices and periodic heartbeats."""
    last_sent = replay[-1].id if replay else 0
    try:
        if gap is not None:
            yield format_gap_event(gap)
        for entry in replay:
            yield format_log_event(entry)

        while True:
            if is_disconnected is not None and await is_disconnected():
                break

            dropped = subscription.take_dropped()
            if dropped:
                yield format_dropped_event(dropped)

            try:
                entry = await asyncio.wait_for(
                    subscription.queue.get(), timeout=heartbeat_seconds
                )
            except TimeoutError:
                yield ": ping\n\n"
                continue

            if entry.id <= last_sent:
                continue
            last_sent = entry.id
            yield format_log_event(entry)
    finally:
        broadcaster.unsubscribe(subscription)


@protected_router.get("/logs/stream")
async def logs_stream(
    request: Request,
    level: str = Query("DEBUG"),
    last_id: int | None = Query(None),
) -> StreamingResponse:
    """Stream logs as SSE, replaying the retained history on connect."""
    header_last_id = request.headers.get("last-event-id")
    if last_id is None and header_last_id:
        try:
            last_id = int(header_last_id)
        except ValueError as exc:
            raise HTTPException(400, detail="Last-Event-ID 必须为整数") from exc

    broadcaster = get_broadcaster()
    try:
        subscription, replay, gap = prepare_stream(
            broadcaster, level=level, last_id=last_id
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except SubscriptionLimitError as exc:
        raise HTTPException(503, detail=str(exc)) from exc

    async def _is_disconnected() -> bool:
        return await request.is_disconnected()

    return StreamingResponse(
        stream_subscription(
            broadcaster,
            subscription,
            replay,
            gap,
            is_disconnected=_is_disconnected,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _parse_iso_datetime(value: str, field: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, detail=f"{field} 必须为 ISO8601 时间") from exc


def resolve_export_range(
    *,
    start: str | None,
    end: str | None,
    days: int | None,
    today: date | None = None,
) -> list[date]:
    """Resolve an export request into a chronological list of archive days."""
    current = today or date.today()
    earliest = current - timedelta(days=MAX_EXPORT_DAYS - 1)

    if days is not None:
        if start is not None or end is not None:
            raise HTTPException(400, detail="days 与 start/end 不能同时使用")
        if days < 1 or days > MAX_EXPORT_DAYS:
            raise HTTPException(400, detail="days 必须在 1 到 7 之间")
        first = current - timedelta(days=days - 1)
        return [first + timedelta(days=offset) for offset in range(days)]

    if start is None or end is None:
        raise HTTPException(400, detail="必须提供 start/end 或 days")

    start_dt = _parse_iso_datetime(start, "start")
    end_dt = _parse_iso_datetime(end, "end")
    if start_dt > end_dt:
        raise HTTPException(400, detail="start 不能晚于 end")

    first = start_dt.date()
    last = end_dt.date()
    if (last - first).days + 1 > MAX_EXPORT_DAYS:
        raise HTTPException(400, detail="单次导出最多 7 天")
    if first < earliest or last > current:
        raise HTTPException(400, detail="导出范围必须在最近 7 天内")

    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


async def _iter_file(path: Path) -> AsyncGenerator[bytes, None]:
    if not path.is_file():
        return
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            yield chunk


async def _iter_zip(paths: list[Path]) -> AsyncGenerator[bytes, None]:
    with tempfile.TemporaryFile() as buffer:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                if not path.is_file():
                    continue
                with (
                    archive.open(path.name, "w") as destination,
                    path.open("rb") as source,
                ):
                    while chunk := source.read(64 * 1024):
                        destination.write(chunk)
        buffer.seek(0)
        while chunk := buffer.read(64 * 1024):
            yield chunk


def _attachment_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@protected_router.get("/logs/export")
async def export_logs(
    start: str | None = Query(None),
    end: str | None = Query(None),
    days: int | None = Query(None),
) -> StreamingResponse:
    """Export one archive day as .log, or multiple days as a ZIP."""
    selected_days = resolve_export_range(start=start, end=end, days=days)
    log_dir = resolve_log_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = [log_dir / archive_file_name(day) for day in selected_days]

    if len(selected_days) == 1:
        filename = f"aistudio-logs-{timestamp}.log"
        return StreamingResponse(
            _iter_file(paths[0]),
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(filename),
        )

    if not any(path.is_file() for path in paths):
        raise HTTPException(404, detail="指定范围内没有日志归档")

    filename = f"aistudio-logs-{timestamp}.zip"
    return StreamingResponse(
        _iter_zip(paths),
        media_type="application/zip",
        headers=_attachment_headers(filename),
    )
