"""C2c sandbox log injection/clear endpoints and real-pipeline assertions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
import pytest

import aistudio_api.api.app as app_module
from aistudio_api.api.state import runtime_state
from aistudio_api.config import resolve_data_dir
from aistudio_api.infrastructure.utils.log_broadcast import (
    LogEntry,
    archive_file_name,
    get_broadcaster,
    resolve_log_dir,
)
from aistudio_api.infrastructure.utils.logger import refresh_log_sinks

_RAW_SECRETS = (
    "sk-1234567890abcdefSECRET",
    "abc123SECRET",
    "def456SECRET",
    "AIzaSyFAKE1234567890SECRETVALUE",
    "sk-json-SECRET-123456",
    "standaloneSAPISIDSECRET",
)

_INJECT = "/api/debug/logs/inject"
_CLEAR = "/api/debug/logs/clear"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.app), base_url="http://test"
    )


@asynccontextmanager
async def _ui_debug_runtime(monkeypatch):
    monkeypatch.setattr(app_module, "resolve_ui_debug", lambda: True)
    async with app_module.app.router.lifespan_context(app_module.app):
        yield


@pytest.fixture
def ui_debug(monkeypatch):
    return _ui_debug_runtime(monkeypatch)


@pytest.fixture(autouse=True)
def _reset_log_state():
    get_broadcaster().clear()
    yield
    get_broadcaster().clear()
    refresh_log_sinks()


def _sample_entries() -> list[LogEntry]:
    entries, _gap = get_broadcaster().replay(level="DEBUG")
    return [
        entry for entry in entries if entry.logger == "aistudio.ui-debug.sample"
    ]


def _snapshot_archives(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {
        path.name: path.read_bytes() for path in directory.glob("aistudio-*.log")
    }


def _assert_masked(text: str) -> None:
    for secret in _RAW_SECRETS:
        assert secret not in text
    assert "Authorization: Bearer ***" in text
    assert "Cookie: ***" in text
    assert "x-goog-api-key: ***" in text


@pytest.mark.asyncio
async def test_log_endpoints_404_when_ui_debug_is_off():
    saved_debug = runtime_state.ui_debug
    saved_sandbox = runtime_state.ui_sandbox
    runtime_state.ui_debug = False
    runtime_state.ui_sandbox = None
    try:
        async with _client() as client:
            for kind in ("all_levels", "sanitize_test", "long_payload"):
                response = await client.post(_INJECT, json={"kind": kind})
                assert response.status_code == 404
                assert response.json()["error"]["message"] == "Not Found"
            clear = await client.post(_CLEAR)
            assert clear.status_code == 404
            assert clear.json()["error"]["message"] == "Not Found"
    finally:
        runtime_state.ui_debug = saved_debug
        runtime_state.ui_sandbox = saved_sandbox


@pytest.mark.asyncio
async def test_all_levels_injection_clear_keeps_ids_and_subscribers(ui_debug):
    broadcaster = get_broadcaster()
    async with ui_debug, _client() as client:
        await client.post(_CLEAR)
        subscription = broadcaster.subscribe()
        try:
            response = await client.post(_INJECT, json={"kind": "all_levels"})
            assert response.status_code == 200, response.text
            assert response.json() == {
                "ok": True,
                "kind": "all_levels",
                "injected": 5,
            }

            samples = _sample_entries()
            assert [entry.level for entry in samples[-5:]] == [
                "DEBUG",
                "INFO",
                "WARNING",
                "ERROR",
                "CRITICAL",
            ]
            assert "429" in samples[-3].message
            assert "熔断器" in samples[-1].message

            latest_id = broadcaster.latest_id
            retained = broadcaster.replay(level="DEBUG")[0]
            cleared = (await client.post(_CLEAR)).json()
            assert cleared == {"ok": True, "cleared": len(retained)}
            # The request middleware records the clear call itself afterwards,
            # but every injected sample must be gone from the ring.
            assert _sample_entries() == []
            assert broadcaster.latest_id >= latest_id
            assert broadcaster.subscriber_count == 1
            assert subscription.active is True

            again = await client.post(_INJECT, json={"kind": "long_payload"})
            assert again.json()["injected"] == 1
            assert broadcaster.latest_id > latest_id
            assert _sample_entries()[-1].id > latest_id
        finally:
            broadcaster.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_sanitize_injection_is_masked_in_ring_archive_and_export(ui_debug):
    real_log_dir = resolve_data_dir() / "logs"
    refresh_log_sinks()
    before_archives = _snapshot_archives(real_log_dir)

    async with ui_debug, _client() as client:
        await client.post(_CLEAR)
        response = await client.post(_INJECT, json={"kind": "sanitize_test"})
        assert response.status_code == 200, response.text
        assert response.json()["injected"] == 1

        ring_message = _sample_entries()[-1].message
        _assert_masked(ring_message)
        assert "keep-me-ui-debug-archive" in ring_message

        sandbox_data = resolve_data_dir()
        archive_path = resolve_log_dir() / archive_file_name(date.today())
        assert archive_path.is_file()
        assert archive_path == sandbox_data / "logs" / archive_file_name(date.today())
        assert archive_path.parent != real_log_dir

        archive_text = archive_path.read_text(encoding="utf-8")
        assert "keep-me-ui-debug-archive" in archive_text
        _assert_masked(archive_text)

        export = await client.get("/api/logs/export", params={"days": 1})
        assert export.status_code == 200, export.text
        assert "keep-me-ui-debug-archive" in export.text
        _assert_masked(export.text)

    assert _snapshot_archives(real_log_dir) == before_archives


@pytest.mark.asyncio
async def test_long_payload_injection_is_multi_kilobyte_and_clear_keeps_archive(
    ui_debug,
):
    async with ui_debug, _client() as client:
        await client.post(_CLEAR)
        response = await client.post(_INJECT, json={"kind": "long_payload"})
        assert response.status_code == 200, response.text
        assert response.json()["injected"] == 1

        message = _sample_entries()[-1].message
        assert 4096 <= len(message) <= 8192
        assert "\n" in message
        assert '"stack"' in message

        archive_path = resolve_log_dir() / archive_file_name(date.today())
        assert archive_path.is_file()
        archive_text = archive_path.read_text(encoding="utf-8")
        assert "ui-debug-long-payload" in archive_text

        latest_id = get_broadcaster().latest_id
        cleared = (await client.post(_CLEAR)).json()
        assert cleared["cleared"] >= 1
        assert _sample_entries() == []
        # Archives are append-only: clearing the ring must never truncate them.
        after_clear = archive_path.read_text(encoding="utf-8")
        assert after_clear.startswith(archive_text)
        assert "ui-debug-long-payload" in after_clear

        again = await client.post(_INJECT, json={"kind": "sanitize_test"})
        assert again.json()["injected"] == 1
        assert _sample_entries()[-1].id > latest_id
