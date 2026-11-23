"""Route tests for verify-account start/status/cancel endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.routes_accounts import to_account_response
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_service import AccountService
from aistudio_api.application.verify_account_service import (
    PageProbe,
    QrChallenge,
    VerifyAccountService,
    VerifyBrowserUnavailable,
    set_verify_account_service,
)
from aistudio_api.config import settings
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore

_QR = QrChallenge(
    qr_url="https://recaptcha.net/qr/ROUTE123456",
    short_code="ROUTE123456",
)
_VERIFY_URL = "https://aistudio.google.com/verify-account"


class RouteFakeDriver:
    def __init__(self) -> None:
        self.closed = False

    async def start(self) -> QrChallenge:
        return _QR

    async def poll(self) -> PageProbe:
        return PageProbe(_VERIFY_URL, False)

    async def confirm_verified(self, auth_user: str) -> int | None:
        del auth_user
        return 2

    async def close(self) -> None:
        self.closed = True


@dataclass
class RouteEnv:
    service: VerifyAccountService
    store: AccountStore
    drivers: list[RouteFakeDriver]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _add_account(store: AccountStore, account_id: str, *, enabled: bool = True) -> None:
    store.save_account(
        name=account_id,
        email=f"{account_id}@example.com",
        storage_state={"cookies": [], "origins": []},
        account_id=account_id,
        auth_user="0",
        tier="Free",
        enabled=enabled,
    )


@pytest.fixture()
async def verify_route_env(tmp_path: Path) -> AsyncIterator[RouteEnv]:
    saved_service = runtime_state.account_service
    saved_rotator = runtime_state.rotator
    saved_debug = runtime_state.ui_debug
    saved_sandbox = runtime_state.ui_sandbox
    store = AccountStore(tmp_path / "accounts")
    _add_account(store, "acc_1")
    _add_account(store, "acc_2", enabled=False)
    drivers: list[RouteFakeDriver] = []

    def factory(_session: Any, _auth_file: Path) -> RouteFakeDriver:
        driver = RouteFakeDriver()
        drivers.append(driver)
        return driver

    service = VerifyAccountService(store, None, driver_factory=factory)
    runtime_state.account_service = AccountService(store)
    runtime_state.rotator = None
    runtime_state.ui_debug = False
    runtime_state.ui_sandbox = None
    set_verify_account_service(service)
    try:
        yield RouteEnv(service=service, store=store, drivers=drivers)
    finally:
        await service.close()
        set_verify_account_service(None)
        runtime_state.account_service = saved_service
        runtime_state.rotator = saved_rotator
        runtime_state.ui_debug = saved_debug
        runtime_state.ui_sandbox = saved_sandbox


@pytest.mark.asyncio
async def test_verify_start_status_cancel_contract(verify_route_env: RouteEnv) -> None:
    async with _client() as client:
        started = await client.post("/api/accounts/acc_1/verify/start")
        assert started.status_code == 200
        payload = started.json()
        assert payload["state"] == "pending"
        assert payload["qr_url"] == _QR.qr_url
        assert payload["short_code"] == _QR.short_code
        assert payload["session_id"].startswith("verify_")
        assert isinstance(payload["expires_at"], str)

        status = await client.get(
            "/api/accounts/acc_1/verify/status",
            params={"session_id": payload["session_id"]},
        )
        assert status.status_code == 200
        assert status.json()["session_id"] == payload["session_id"]
        assert status.json()["state"] == "pending"

        cancelled = await client.post("/api/accounts/acc_1/verify/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {"state": "cancelled"}
        await verify_route_env.service.wait_for_cleanup()
        assert verify_route_env.drivers[0].closed is True

        again = await client.post("/api/accounts/acc_1/verify/cancel")
        assert again.status_code == 200
        assert again.json() == {"state": "cancelled"}


@pytest.mark.asyncio
async def test_verify_start_conflicts_for_other_account(
    verify_route_env: RouteEnv,
) -> None:
    async with _client() as client:
        first = await client.post("/api/accounts/acc_1/verify/start")
        assert first.status_code == 200
        same = await client.post("/api/accounts/acc_1/verify/start")
        assert same.status_code == 200
        assert same.json()["session_id"] == first.json()["session_id"]

        other = await client.post("/api/accounts/acc_2/verify/start")
        assert other.status_code == 409
        message = other.json()["error"]["message"]
        assert "其它账号" in message


@pytest.mark.asyncio
async def test_verify_start_allows_disabled_account(
    verify_route_env: RouteEnv,
) -> None:
    async with _client() as client:
        resp = await client.post("/api/accounts/acc_2/verify/start")
    assert resp.status_code == 200
    assert resp.json()["state"] == "pending"


@pytest.mark.asyncio
async def test_verify_start_unknown_account_and_session(
    verify_route_env: RouteEnv,
) -> None:
    async with _client() as client:
        missing = await client.post("/api/accounts/acc_missing/verify/start")
        assert missing.status_code == 404

        status = await client.get(
            "/api/accounts/acc_1/verify/status",
            params={"session_id": "verify_missing"},
        )
        assert status.status_code == 404


@pytest.mark.asyncio
async def test_verify_start_returns_503_when_browser_unavailable(
    verify_route_env: RouteEnv,
) -> None:
    def factory(_session: Any, _auth_file: Path) -> RouteFakeDriver:
        raise VerifyBrowserUnavailable("browser missing")

    bad = VerifyAccountService(verify_route_env.store, None, driver_factory=factory)
    set_verify_account_service(bad)
    try:
        async with _client() as client:
            resp = await client.post("/api/accounts/acc_1/verify/start")
        assert resp.status_code == 503
        message = resp.json()["error"]["message"]
        assert "browser missing" in message
        assert "AISTUDIO_VERIFY_CHROME_PATH" in message
    finally:
        await bad.close()
        set_verify_account_service(verify_route_env.service)


@pytest.mark.asyncio
async def test_verify_routes_require_web_auth(
    verify_route_env: RouteEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "web_password", "secret")
    async with _client() as client:
        resp = await client.post("/api/accounts/acc_1/verify/start")
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "管理控制台鉴权失败，请输入正确的管理密码"


def test_account_response_without_status_respects_verify_lock() -> None:
    account = AccountMeta(
        id="acc_verify",
        name="Verify",
        email="v@example.com",
        created_at="2026-11-22 00:00:00",
        verify_required=True,
    )
    response = to_account_response(account, status=None)
    assert response.verify_required is True
    assert response.is_available is False


@pytest.mark.asyncio
async def test_production_status_ignores_simulate(
    verify_route_env: RouteEnv,
) -> None:
    async with _client() as client:
        started = await client.post("/api/accounts/acc_1/verify/start")
        session_id = started.json()["session_id"]
        status = await client.get(
            "/api/accounts/acc_1/verify/status",
            params={"session_id": session_id, "simulate": "passed"},
        )
    assert status.status_code == 200
    assert status.json()["state"] == "pending"


@pytest.mark.asyncio
async def test_delete_account_cancels_active_verify_session(
    verify_route_env: RouteEnv,
) -> None:
    async with _client() as client:
        started = await client.post("/api/accounts/acc_1/verify/start")
        assert started.status_code == 200
        deleted = await client.delete("/api/accounts/acc_1")
    assert deleted.status_code == 200
    await verify_route_env.service.wait_for_cleanup()
    assert verify_route_env.drivers[0].closed is True
    session = verify_route_env.service.session
    assert session is not None
    assert session.state.value == "cancelled"
