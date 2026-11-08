"""Dashboard UI debug route, scenarios and live action tests."""

from __future__ import annotations

import httpx
import pytest

import aistudio_api.api.app as app_module
from aistudio_api.api.state import runtime_state


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.app), base_url="http://test"
    )


@pytest.fixture
def ui_debug(monkeypatch):
    monkeypatch.setattr(app_module, "resolve_ui_debug", lambda: True)
    return app_module.app.router.lifespan_context(app_module.app)


@pytest.mark.asyncio
async def test_dashboard_debug_404_when_ui_debug_off(monkeypatch):
    from aistudio_api.api import dependencies

    monkeypatch.setattr(dependencies.settings, "web_password", "secret")
    saved_debug = runtime_state.ui_debug
    saved_sandbox = runtime_state.ui_sandbox
    runtime_state.ui_debug = False
    runtime_state.ui_sandbox = None
    try:
        async with _client() as client:
            state = await client.get("/api/debug/dashboard/state")
            apply = await client.post(
                "/api/debug/dashboard/apply", json={"scenario": "empty"}
            )
        assert state.status_code == 404
        assert apply.status_code == 404
    finally:
        runtime_state.ui_debug = saved_debug
        runtime_state.ui_sandbox = saved_sandbox


@pytest.mark.asyncio
async def test_dashboard_state_scenarios_and_stats(ui_debug):
    async with ui_debug, _client() as client:
        state = (await client.get("/api/debug/dashboard/state")).json()
        assert state["ok"] is True
        assert state["ui_debug_scenario"] == "all_states"
        assert "gemini-4-argon" in state["stats"]["models"]
        assert "daily" not in state["stats"]["models"]
        assert state["stats"]["totals"]["requests"] > 0
        assert len(state["instances"]["instances"]) >= 6

        mass = (
            await client.post(
                "/api/debug/dashboard/apply", json={"scenario": "mass_accounts"}
            )
        ).json()
        assert mass["ui_debug_scenario"] == "mass_accounts"
        assert len(mass["accounts"]) == 48
        assert len(mass["instances"]["instances"]) >= 6

        all_429 = (
            await client.post(
                "/api/debug/dashboard/apply", json={"scenario": "all_429"}
            )
        ).json()
        assert all_429["ui_debug_scenario"] == "all_429"
        assert all(
            account["auth_cooldown"] > 0
            for account in all_429["accounts"]
            if account["enabled"] and not account["expired"]
        )
        assert all_429["stats"]["totals"]["rate_limited"] > 0
        assert all_429["stats"]["totals"]["success"] == 0

        empty = (
            await client.post(
                "/api/debug/dashboard/apply", json={"scenario": "empty"}
            )
        ).json()
        assert empty["accounts"] == []
        assert empty["instances"]["instances"] == []
        assert empty["stats"]["models"] == {}
        assert empty["stats"]["totals"]["requests"] == 0


@pytest.mark.asyncio
async def test_dashboard_quick_actions_update_memory(ui_debug):
    async with ui_debug, _client() as client:
        before = (await client.get("/api/debug/dashboard/state")).json()
        total_before = before["stats"]["totals"]

        injected = (
            await client.post(
                "/api/debug/dashboard/apply", json={"action": "simulate_requests"}
            )
        ).json()
        total_after = injected["stats"]["totals"]
        assert total_after["requests"] == total_before["requests"] + 10
        assert total_after["total_tokens"] == total_before["total_tokens"] + 50_000

        limited = (
            await client.post(
                "/api/debug/dashboard/apply", json={"action": "simulate_429"}
            )
        ).json()
        assert limited["stats"]["totals"]["rate_limited"] == (
            total_after["rate_limited"] + 1
        )
        assert any(account["auth_cooldown"] > 0 for account in limited["accounts"])

        stats = (await client.get("/stats")).json()
        assert "models" not in stats["models"]
        assert "daily" not in stats["models"]
        assert stats["totals"]["requests"] == limited["stats"]["totals"]["requests"]


@pytest.mark.asyncio
async def test_dashboard_toggle_formats_and_status(ui_debug):
    async with ui_debug, _client() as client:
        status = (await client.get("/api/system/status")).json()
        assert status["formats"]["gemini"] is True
        assert status["default_text_model"]
        assert status["formats"]["openai"] is True
        assert status["formats"]["claude"] is True
        assert status["formats"]["responses"] is True

        closed = (
            await client.post(
                "/api/debug/dashboard/apply", json={"action": "toggle_formats"}
            )
        ).json()
        assert closed["formats"] == {
            "gemini": True,
            "openai": False,
            "claude": False,
            "responses": False,
        }
        live_status = (await client.get("/api/system/status")).json()
        assert live_status["formats"]["claude"] is False
        assert live_status["formats"]["responses"] is False

        all_on = (
            await client.post(
                "/api/debug/dashboard/apply", json={"action": "toggle_formats"}
            )
        ).json()
        assert all_on["formats"] == {
            "gemini": True,
            "openai": True,
            "claude": True,
            "responses": True,
        }
