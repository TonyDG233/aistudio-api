"""Keepalive API behavior tests."""

from __future__ import annotations

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class FakeKeepalive:
    def __init__(self, run_result: dict[str, object]) -> None:
        self._run_result = run_result

    def status(self) -> dict[str, object]:
        return {
            "enabled": {"auth_update": True},
            "last_results": {"auth_update": {"ok": True}},
        }

    async def run_now(self, task: str, *, trigger: str) -> dict[str, object]:
        return {**self._run_result, "task": task, "trigger": trigger}


@pytest.mark.asyncio
async def test_keepalive_status_reports_service_state():
    original = runtime_state.keepalive
    runtime_state.keepalive = FakeKeepalive({"ok": True})  # type: ignore[assignment]
    try:
        async with _client() as client:
            resp = await client.get("/api/keepalive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["enabled"]["auth_update"] is True
    finally:
        runtime_state.keepalive = original


@pytest.mark.asyncio
async def test_keepalive_run_rejects_unknown_task():
    async with _client() as client:
        resp = await client.post("/api/keepalive/run", json={"task": "not-a-task"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_keepalive_run_maps_busy_to_409():
    original = runtime_state.keepalive
    runtime_state.keepalive = FakeKeepalive({"ok": False, "reason": "busy"})  # type: ignore[assignment]
    try:
        async with _client() as client:
            resp = await client.post("/api/keepalive/run", json={"task": "auth_update"})
        assert resp.status_code == 409
    finally:
        runtime_state.keepalive = original


@pytest.mark.asyncio
async def test_keepalive_run_returns_result():
    original = runtime_state.keepalive
    runtime_state.keepalive = FakeKeepalive({"ok": True, "reason": "saved"})  # type: ignore[assignment]
    try:
        async with _client() as client:
            resp = await client.post("/api/keepalive/run", json={"task": "auth_update"})
        assert resp.status_code == 200
        assert resp.json()["reason"] == "saved"
    finally:
        runtime_state.keepalive = original
