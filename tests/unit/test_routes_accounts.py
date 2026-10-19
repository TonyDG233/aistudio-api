"""Tests for accounts routes, cache isolation headers, and /api/accounts prefix."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

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
    mock_svc.store.get_account.return_value = sample_acc
    mock_svc.store.get_account_routing.return_value = {
        "surfaces": ["chat"],
        "weight": 2.5,
        "model_surfaces": {"gemini-3.8-flash": "chat"},
    }
    mock_svc.store.update_account_routing.return_value = True
    mock_svc.store.clear_account_routing.return_value = True

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
async def test_account_routing_get_put_delete():
    """Account-level routing stays in AccountMeta and is normalized on write."""
    async with _client() as client:
        resp = await client.get(
            "/api/accounts/acc_1/routing", headers={"Accept": "application/json"}
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "surfaces": ["chat"],
            "weight": 2.5,
            "model_surfaces": {"gemini-3.8-flash": "chat"},
        }

        resp = await client.put(
            "/api/accounts/acc_1/routing",
            json={
                "surfaces": ["build", "build", "bogus"],
                "weight": 500,
                "model_surfaces": {"models/gemini-3.8-flash": "build"},
            },
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["surfaces"] == ["build"]
        assert payload["weight"] == 100.0
        assert payload["model_surfaces"] == {"gemini-3.8-flash": "build"}

        resp = await client.delete(
            "/api/accounts/acc_1/routing", headers={"Accept": "application/json"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_account_routing_unknown_account_is_404():
    async with _client() as client:
        # Fixture returns an account; force the route's lookup to miss.
        from aistudio_api.api.state import runtime_state

        store = cast(
            MagicMock,
            runtime_state.account_service.store,  # type: ignore[union-attr]
        )
        store.get_account.return_value = None
        resp = await client.get(
            "/api/accounts/missing/routing", headers={"Accept": "application/json"}
        )
        assert resp.status_code == 404
