"""Unit tests for the log SSE stream and archive export endpoints."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from collections.abc import AsyncGenerator
from datetime import date, timedelta, timezone
from typing import cast

import httpx
import pytest
from fastapi import HTTPException, Request

from aistudio_api.api import routes_logs
from aistudio_api.api.app import app
from aistudio_api.config import settings
from aistudio_api.infrastructure.utils.log_broadcast import LogBroadcaster


class _FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}

    async def is_disconnected(self) -> bool:
        return False


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _decode_event(chunk: str) -> tuple[str, int | None, dict[str, object]]:
    lines = chunk.strip().splitlines()
    event = next(line[7:] for line in lines if line.startswith("event: "))
    event_id = next((int(line[4:]) for line in lines if line.startswith("id: ")), None)
    payload = next(line[6:] for line in lines if line.startswith("data: "))
    return event, event_id, json.loads(payload)


async def test_prepare_and_stream_replays_filtered_entries():
    broadcaster = LogBroadcaster(capacity=100)
    broadcaster.publish("DEBUG", "aistudio.test", "debug-line")
    broadcaster.publish("INFO", "aistudio.test", "info-line")
    broadcaster.publish("ERROR", "aistudio.test", "error-line")

    subscription, replay, gap = routes_logs.prepare_stream(
        broadcaster, level="INFO", last_id=None
    )
    assert gap is None
    assert [entry.message for entry in replay] == ["info-line", "error-line"]

    stream = routes_logs.stream_subscription(
        broadcaster,
        subscription,
        replay,
        gap,
        heartbeat_seconds=0.05,
    )
    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    event, event_id, payload = _decode_event(first)
    assert event == "log"
    assert event_id == 2
    assert payload["id"] == 2
    assert payload["level"] == "INFO"
    assert payload["logger"] == "aistudio.test"
    assert payload["message"] == "info-line"
    assert set(payload) == {"id", "ts", "level", "logger", "message"}

    event, event_id, payload = _decode_event(second)
    assert event == "log"
    assert event_id == 3
    assert payload["message"] == "error-line"
    assert broadcaster.subscriber_count == 0


async def test_stream_sends_gap_event_with_missed_count():
    broadcaster = LogBroadcaster(capacity=3)
    for index in range(6):
        broadcaster.publish("INFO", "aistudio.test", f"line-{index}")

    subscription, replay, gap = routes_logs.prepare_stream(
        broadcaster, level="DEBUG", last_id=1
    )
    assert gap == {"last_id": 1, "oldest_id": 4, "missed": 2}

    stream = routes_logs.stream_subscription(
        broadcaster,
        subscription,
        replay,
        gap,
        heartbeat_seconds=0.05,
    )
    first = await anext(stream)
    await stream.aclose()

    event, event_id, payload = _decode_event(first)
    assert event == "gap"
    assert event_id is None
    assert payload == {"last_id": 1, "oldest_id": 4, "missed": 2}


async def test_stream_emits_dropped_event_on_queue_overflow():
    broadcaster = LogBroadcaster(capacity=10, queue_size=1)
    subscription, replay, gap = routes_logs.prepare_stream(
        broadcaster, level="DEBUG", last_id=None
    )
    broadcaster.publish("INFO", "aistudio.test", "first")
    broadcaster.publish("INFO", "aistudio.test", "second")
    await asyncio.sleep(0.01)

    stream = routes_logs.stream_subscription(
        broadcaster,
        subscription,
        replay,
        gap,
        heartbeat_seconds=0.05,
    )
    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    event, event_id, payload = _decode_event(first)
    assert event == "dropped"
    assert event_id is None
    assert payload == {"dropped": 1}

    event, _event_id, payload = _decode_event(second)
    assert event == "log"
    assert payload["message"] == "second"


async def test_stream_heartbeat_and_unsubscribe():
    broadcaster = LogBroadcaster(capacity=10)
    subscription, replay, gap = routes_logs.prepare_stream(
        broadcaster, level="DEBUG", last_id=None
    )
    stream = routes_logs.stream_subscription(
        broadcaster,
        subscription,
        replay,
        gap,
        heartbeat_seconds=0.01,
    )

    assert await anext(stream) == ": ping\n\n"
    await stream.aclose()
    assert broadcaster.subscriber_count == 0


async def test_stream_route_headers_and_spa_bypass(monkeypatch):
    broadcaster = LogBroadcaster(capacity=10)
    broadcaster.publish("INFO", "aistudio.test", "route-line")
    monkeypatch.setattr(routes_logs, "get_broadcaster", lambda: broadcaster)

    response = await routes_logs.logs_stream(
        cast(Request, _FakeRequest({"accept": "text/html"})),
        level="DEBUG",
        last_id=None,
    )
    assert response.media_type == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["connection"] == "keep-alive"

    iterator = cast(AsyncGenerator[str, None], response.body_iterator)
    first = await anext(iterator)
    assert "event: log" in first
    await iterator.aclose()
    assert broadcaster.subscriber_count == 0


def test_stream_and_export_registered_at_both_paths():
    paths = set(app.openapi()["paths"])
    assert {
        "/logs/stream",
        "/api/logs/stream",
        "/logs/export",
        "/api/logs/export",
    } <= paths


async def test_stream_route_honors_last_event_id_header(monkeypatch):
    broadcaster = LogBroadcaster(capacity=3)
    for index in range(6):
        broadcaster.publish("INFO", "aistudio.test", f"header-line-{index}")
    monkeypatch.setattr(routes_logs, "get_broadcaster", lambda: broadcaster)

    response = await routes_logs.logs_stream(
        cast(Request, _FakeRequest({"last-event-id": "1"})),
        level="DEBUG",
        last_id=None,
    )
    iterator = cast(AsyncGenerator[str, None], response.body_iterator)
    first = await anext(iterator)
    await iterator.aclose()

    event, event_id, payload = _decode_event(first)
    assert event == "gap"
    assert event_id is None
    assert payload["last_id"] == 1
    assert payload["missed"] == 2
    assert broadcaster.subscriber_count == 0


async def test_stream_route_rejects_unknown_level(monkeypatch):
    broadcaster = LogBroadcaster(capacity=10)
    monkeypatch.setattr(routes_logs, "get_broadcaster", lambda: broadcaster)

    with pytest.raises(HTTPException) as exc_info:
        await routes_logs.logs_stream(
            cast(Request, _FakeRequest()), level="TRACE", last_id=None
        )
    assert exc_info.value.status_code == 400
    assert broadcaster.subscriber_count == 0


async def test_stream_route_returns_503_above_subscriber_limit(monkeypatch):
    broadcaster = LogBroadcaster(capacity=10)
    monkeypatch.setattr(routes_logs, "get_broadcaster", lambda: broadcaster)
    subscriptions = [broadcaster.subscribe() for _ in range(8)]

    with pytest.raises(HTTPException) as exc_info:
        await routes_logs.logs_stream(
            cast(Request, _FakeRequest()), level="DEBUG", last_id=None
        )
    assert exc_info.value.status_code == 503

    for subscription in subscriptions:
        broadcaster.unsubscribe(subscription)


@pytest.mark.parametrize("path", ["/logs/stream", "/api/logs/stream"])
async def test_stream_route_requires_web_auth(monkeypatch, path):
    monkeypatch.setattr(settings, "web_password", "super-secret")
    async with _client() as client:
        response = await client.get(path)
        assert response.status_code == 401


def _write_archive(log_dir, day: date, content: str) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    name = f"aistudio-{day.strftime('%Y%m%d')}.log"
    (log_dir / name).write_bytes(content.encode("utf-8"))
    return name


async def test_export_single_day_returns_log(monkeypatch, tmp_path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    _write_archive(tmp_path, date.today(), "single-day-line\n")

    async with _client() as client:
        response = await client.get("/logs/export", params={"days": 1})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    disposition = response.headers["content-disposition"]
    assert "aistudio-logs-" in disposition
    assert disposition.endswith('.log"')
    assert "single-day-line" in response.text


async def test_export_sanitizes_historical_secrets_and_keeps_markers(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    _write_archive(
        tmp_path,
        date.today(),
        "Authorization: Bearer historical-secret\n"
        "Cookie: SAPISID=historical-cookie; theme=dark\n"
        "x-goog-api-key: AIzaSyHistoricalValue1234567890\n"
        "已删除 API Key: sk-b6-deadbeefcafebabe\n"
        "HTTP DELETE /api-keys/sk-b6-deadbeefcafebabe -> 200\n"
        "[toolshape] preserved\n"
        "pg08 preserved\n",
    )

    async with _client() as client:
        response = await client.get("/logs/export", params={"days": 1})

    assert response.status_code == 200
    body = response.text
    assert "historical-secret" not in body
    assert "historical-cookie" not in body
    assert "theme=dark" not in body
    assert "AIzaSyHistoricalValue1234567890" not in body
    assert "sk-b6-deadbeefcafebabe" not in body
    assert "Authorization: Bearer ***" in body
    assert "Cookie: ***" in body
    assert "[toolshape] preserved" in body
    assert "pg08 preserved" in body


async def test_export_zip_sanitizes_historical_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    today = date.today()
    yesterday = today - timedelta(days=1)
    first_name = _write_archive(
        tmp_path, yesterday, "Cookie: SID=zip-cookie-value\n[toolshape] zip-keep\n"
    )
    second_name = _write_archive(
        tmp_path, today, "Authorization: Bearer zip-secret\npg08 zip-keep\n"
    )

    async with _client() as client:
        response = await client.get("/logs/export", params={"days": 2})

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        first = archive.read(first_name).decode("utf-8")
        second = archive.read(second_name).decode("utf-8")

    assert "zip-cookie-value" not in first
    assert "[toolshape] zip-keep" in first
    assert "zip-secret" not in second
    assert "pg08 zip-keep" in second


@pytest.mark.parametrize("path", ["/logs/export", "/api/logs/export"])
async def test_export_api_alias_serves_same_archive(monkeypatch, tmp_path, path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    _write_archive(tmp_path, date.today(), "alias-line\n")

    async with _client() as client:
        response = await client.get(path, params={"days": 1})

    assert response.status_code == 200
    assert "alias-line" in response.text


async def test_export_cross_day_returns_zip(monkeypatch, tmp_path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    today = date.today()
    yesterday = today - timedelta(days=1)
    first_name = _write_archive(tmp_path, yesterday, "yesterday-line\n")
    second_name = _write_archive(tmp_path, today, "today-line\n")

    async with _client() as client:
        response = await client.get("/logs/export", params={"days": 2})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    disposition = response.headers["content-disposition"]
    assert "aistudio-logs-" in disposition
    assert disposition.endswith('.zip"')

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == sorted([first_name, second_name])
        assert archive.read(first_name) == b"yesterday-line\n"
        assert archive.read(second_name) == b"today-line\n"


async def test_export_accepts_iso_range(monkeypatch, tmp_path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    today = date.today()
    _write_archive(tmp_path, today, "iso-range-line\n")

    start = f"{today.isoformat()}T00:00:00"
    end = f"{today.isoformat()}T23:59:59"
    async with _client() as client:
        response = await client.get("/logs/export", params={"start": start, "end": end})

    assert response.status_code == 200
    assert "iso-range-line" in response.text


@pytest.mark.parametrize("path", ["/logs/export", "/api/logs/export"])
async def test_export_single_missing_day_returns_404(monkeypatch, tmp_path, path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))

    async with _client() as client:
        response = await client.get(path, params={"days": 1})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {"days": 0},
        {"days": 8},
        {"start": "2026-10-01T00:00:00"},
        {"days": 1, "start": "2026-10-01T00:00:00"},
        {"start": "not-a-date", "end": "2026-10-20T00:00:00"},
    ],
)
async def test_export_rejects_invalid_ranges(params):
    async with _client() as client:
        response = await client.get("/logs/export", params=params)
    assert response.status_code == 400


async def test_export_rejects_ranges_outside_seven_days(monkeypatch, tmp_path):
    monkeypatch.setenv("AISTUDIO_LOG_DIR", str(tmp_path))
    today = date.today()

    too_old = today - timedelta(days=8)
    too_wide = today - timedelta(days=7)
    async with _client() as client:
        too_old_response = await client.get(
            "/logs/export",
            params={
                "start": f"{too_old.isoformat()}T00:00:00",
                "end": f"{too_old.isoformat()}T23:59:59",
            },
        )
        too_wide_response = await client.get(
            "/logs/export",
            params={
                "start": f"{too_wide.isoformat()}T00:00:00",
                "end": f"{today.isoformat()}T23:59:59",
            },
        )

    assert too_old_response.status_code == 400
    assert too_wide_response.status_code == 400


async def test_export_rejects_mixed_aware_and_naive_range():
    today = date.today()
    async with _client() as client:
        response = await client.get(
            "/logs/export",
            params={
                "start": f"{today.isoformat()}T00:00:00Z",
                "end": f"{today.isoformat()}T23:59:59",
            },
        )

    assert response.status_code == 400


@pytest.mark.parametrize("days", [1, 3, 7])
def test_resolve_export_range_days_shortcuts(days):
    today = date(2026, 10, 30)
    selected = routes_logs.resolve_export_range(
        start=None, end=None, days=days, today=today
    )

    assert len(selected) == days
    assert selected[-1] == today


def test_resolve_export_range_utc_payload_maps_to_local_calendar_day():
    # UTC+8: local 2026-10-30 00:00..23:59:59 becomes UTC 10-29 16:00..10-30 15:59.
    selected = routes_logs.resolve_export_range(
        start="2026-10-29T16:00:00Z",
        end="2026-10-30T15:59:59.999Z",
        days=None,
        today=date(2026, 10, 30),
        local_tz=timezone(timedelta(hours=8)),
    )

    assert selected == [date(2026, 10, 30)]


def test_resolve_export_range_utc_plus_8_seven_days_is_not_eight():
    selected = routes_logs.resolve_export_range(
        start="2026-10-23T16:00:00Z",
        end="2026-10-30T15:59:59.999Z",
        days=None,
        today=date(2026, 10, 30),
        local_tz=timezone(timedelta(hours=8)),
    )

    assert selected == [date(2026, 10, 24) + timedelta(days=index) for index in range(7)]


def test_resolve_export_range_naive_local_dates_are_inclusive():
    selected = routes_logs.resolve_export_range(
        start="2026-10-24T00:00:00",
        end="2026-10-30T23:59:59.999",
        days=None,
        today=date(2026, 10, 30),
    )

    assert len(selected) == 7
    assert selected[0] == date(2026, 10, 24)
    assert selected[-1] == date(2026, 10, 30)


@pytest.mark.parametrize("path", ["/logs/export", "/api/logs/export"])
async def test_export_requires_web_auth(monkeypatch, path):
    monkeypatch.setattr(settings, "web_password", "super-secret")
    async with _client() as client:
        response = await client.get(path, params={"days": 1})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/logs", "/logs/"])
async def test_logs_spa_navigation_serves_index(path):
    async with _client() as client:
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"].startswith("no-store")
    assert "Accept" in response.headers.get("vary", "")
