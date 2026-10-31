"""Tests for accounts routes, cache isolation headers, and /api/accounts prefix."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountMeta


@pytest.fixture(autouse=True)
def setup_mock_account_service():
    mock_svc = MagicMock(spec=AccountService)
    sample_acc = AccountMeta(
        id="acc_1",
        name="Test Account 1",
        email="test1@gmail.com",
        created_at="2026-10-17T00:00:00Z",
        last_used=None,
        auth_user="0",
        cookie_id="cookie_1",
    )
    mock_svc.list_accounts.return_value = [sample_acc]
    mock_svc.get_active_account.return_value = sample_acc
    mock_svc.get_account.return_value = sample_acc
    mock_svc.activate_account = AsyncMock(return_value=sample_acc)

    orig = runtime_state.account_service
    runtime_state.account_service = mock_svc
    try:
        yield mock_svc
    finally:
        runtime_state.account_service = orig


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_accounts_html_navigation_headers():
    """Verify that visiting /accounts as HTML returns the SPA HTML with cache isolation headers."""
    async with _client() as client:
        for path in ("/accounts", "/accounts/"):
            resp = await client.get(
                path, headers={"Accept": "text/html,application/xhtml+xml"}
            )
            assert resp.status_code == 200
            assert "<!DOCTYPE html>" in resp.text
            assert "text/html" in resp.headers.get("Content-Type", "")
            assert "Accept" in resp.headers.get("Vary", "")
            assert "no-store" in resp.headers.get("Cache-Control", "")


@pytest.mark.asyncio
async def test_accounts_json_api_headers():
    """Verify that calling /accounts with Accept: application/json returns JSON with cache isolation headers."""
    async with _client() as client:
        resp = await client.get("/accounts", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        assert "Accept" in resp.headers.get("Vary", "")
        assert "no-store" in resp.headers.get("Cache-Control", "")
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "acc_1"
        assert "notes" not in data[0]
        assert data[0]["expired"] is False
        assert data[0]["duplicate_of"] is None


@pytest.mark.asyncio
async def test_api_accounts_endpoint_headers():
    """Verify that the dedicated /api/accounts endpoint returns JSON with cache isolation headers."""
    async with _client() as client:
        for path in ("/api/accounts", "/api/accounts/"):
            resp = await client.get(path, headers={"Accept": "application/json"})
            assert resp.status_code == 200
            assert "application/json" in resp.headers.get("Content-Type", "")
            assert "Accept" in resp.headers.get("Vary", "")
            assert "no-store" in resp.headers.get("Cache-Control", "")
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == "acc_1"


@pytest.mark.asyncio
async def test_api_accounts_and_legacy_accounts_parity():
    """Verify that /api/accounts and /accounts return identical payload for JSON clients."""
    async with _client() as client:
        resp_legacy = await client.get(
            "/accounts", headers={"Accept": "application/json"}
        )
        resp_api = await client.get(
            "/api/accounts", headers={"Accept": "application/json"}
        )
        assert resp_legacy.status_code == 200
        assert resp_api.status_code == 200
        assert resp_legacy.json() == resp_api.json()


@pytest.mark.asyncio
async def test_issue_5_cache_isolation_regression():
    """Regression test for Issue #5:
    Ensure that a client requesting HTML followed by JSON (or vice-versa) receives distinct
    content types and both responses carry Vary: Accept and Cache-Control: no-store headers.
    """
    async with _client() as client:
        html_resp = await client.get("/accounts", headers={"Accept": "text/html"})
        assert html_resp.status_code == 200
        assert "<!DOCTYPE html>" in html_resp.text
        assert "Accept" in html_resp.headers.get("Vary", "")
        assert "no-store" in html_resp.headers.get("Cache-Control", "")

        json_resp = await client.get(
            "/accounts", headers={"Accept": "application/json"}
        )
        assert json_resp.status_code == 200
        assert "application/json" in json_resp.headers.get("Content-Type", "")
        assert "Accept" in json_resp.headers.get("Vary", "")
        assert "no-store" in json_resp.headers.get("Cache-Control", "")
        assert isinstance(json_resp.json(), list)


@pytest.mark.asyncio
async def test_api_accounts_active_endpoint():
    """Verify subroutes under /api/accounts like /api/accounts/active work."""
    async with _client() as client:
        resp = await client.get(
            "/api/accounts/active", headers={"Accept": "application/json"}
        )
        assert resp.status_code == 200
        assert "Accept" in resp.headers.get("Vary", "")
        assert "no-store" in resp.headers.get("Cache-Control", "")
        data = resp.json()
        assert data["id"] == "acc_1"


@pytest.mark.asyncio
async def test_activate_reuses_ready_pool_slot_without_control_switch(
    setup_mock_account_service,
):
    """Q5/N12: same-account activation must not open a second control Page."""
    control_session = SimpleNamespace(
        switch_auth=AsyncMock(), ensure_botguard_service=AsyncMock()
    )
    saved_manager = runtime_state.instance_manager
    saved_client = runtime_state.client
    saved_rotator = runtime_state.rotator
    runtime_state.instance_manager = cast(
        Any,
        SimpleNamespace(
            ready_slot_for_account=MagicMock(return_value=object())
        ),
    )
    runtime_state.client = cast(Any, SimpleNamespace(_session=control_session))
    runtime_state.rotator = None
    try:
        async with _client() as client:
            resp = await client.post("/accounts/acc_1/activate")
    finally:
        runtime_state.instance_manager = saved_manager
        runtime_state.client = saved_client
        runtime_state.rotator = saved_rotator

    assert resp.status_code == 200
    setup_mock_account_service.set_active_account.assert_called_once_with("acc_1")
    control_session.switch_auth.assert_not_awaited()
    control_session.ensure_botguard_service.assert_not_awaited()
    setup_mock_account_service.activate_account.assert_not_awaited()


@pytest.mark.asyncio
async def test_activate_without_ready_pool_keeps_control_switch_fallback(
    setup_mock_account_service,
):
    """The no-pool / no-ready-slot path keeps the existing control switch."""
    control_session = SimpleNamespace(
        switch_auth=AsyncMock(), ensure_botguard_service=AsyncMock()
    )
    saved_manager = runtime_state.instance_manager
    saved_client = runtime_state.client
    saved_rotator = runtime_state.rotator
    runtime_state.instance_manager = cast(
        Any,
        SimpleNamespace(ready_slot_for_account=MagicMock(return_value=None)),
    )
    runtime_state.client = cast(Any, SimpleNamespace(_session=control_session))
    runtime_state.rotator = None
    try:
        async with _client() as client:
            resp = await client.post("/accounts/acc_1/activate")
    finally:
        runtime_state.instance_manager = saved_manager
        runtime_state.client = saved_client
        runtime_state.rotator = saved_rotator

    assert resp.status_code == 200
    setup_mock_account_service.activate_account.assert_awaited_once_with(
        "acc_1", control_session
    )
