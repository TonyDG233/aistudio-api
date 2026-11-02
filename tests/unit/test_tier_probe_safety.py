"""Regression tests: failed tier probes must never fabricate Free.

These cover the VPS bug where a SOCKS/proxy/network failure or an unparseable
upstream body was downgraded to Free, overwriting a real Pro/Ultra tier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from aistudio_api.api import routes_accounts
from aistudio_api.api.routes_accounts import (
    ImportCookiesRequest,
    detect_account_tier,
    import_cookies,
)
from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.account.cookie_parser import (
    parse_benefit_tier_strict,
    probe_account_benefit_tier,
    probe_google_accounts_infinite,
)

_STORAGE_STATE = {
    "cookies": [
        {"name": "SID", "value": "sid-value", "domain": ".google.com"},
        {"name": "SAPISID", "value": "sapisid-value", "domain": ".google.com"},
    ],
    "origins": [],
}


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """Minimal ``httpx.AsyncClient`` replacement for probe tests."""

    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        post_responses: list[_FakeResponse] | None = None,
        get_response: _FakeResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._response = response
        self._post_responses = list(post_responses or [])
        self._get_response = get_response or _FakeResponse(500)
        self._error = error

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        if self._post_responses:
            return self._post_responses.pop(0)
        assert self._response is not None
        return self._response

    async def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        return self._get_response


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeAsyncClient) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)


class _FakeInstanceManager:
    def __init__(self) -> None:
        self.synced: list[tuple[str, str]] = []

    def ready_slot_for_account(self, account_id: str) -> None:
        return None

    def sync_account_tier(self, account_id: str, tier: str) -> int:
        self.synced.append((account_id, tier))
        return 1


class _FailingSyncInstanceManager(_FakeInstanceManager):
    def sync_account_tier(self, account_id: str, tier: str) -> int:
        raise RuntimeError("instance pool unavailable")


def _service(tmp_path: Any) -> tuple[AccountService, AccountStore]:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    return AccountService(store), store


def _account_with_auth(
    store: AccountStore,
    *,
    tier: str,
    email: str = "user@example.com",
) -> Any:
    account = store.save_account(
        name="Tier Test",
        email=email,
        storage_state=_STORAGE_STATE,
        tier=tier,
    )
    if tier != "Free":
        store.update_account_tier(
            account.id,
            tier,
            checked_at=datetime.now(UTC).isoformat(),
        )
    return store.get_account(account.id)


@pytest.mark.asyncio
async def test_proxy_exception_probe_returns_none(monkeypatch):
    _patch_client(
        monkeypatch,
        _FakeAsyncClient(error=httpx.ProxyError("socks proxy refused")),
    )

    assert (
        await probe_account_benefit_tier(
            {"SID": "sid", "SAPISID": "sapisid"}, auth_user="0"
        )
        is None
    )


@pytest.mark.asyncio
async def test_probe_non_200_and_malformed_200_return_none(monkeypatch):
    _patch_client(monkeypatch, _FakeAsyncClient(response=_FakeResponse(500, "err")))
    assert await probe_account_benefit_tier({"SID": "s", "SAPISID": "a"}) is None

    _patch_client(
        monkeypatch,
        _FakeAsyncClient(response=_FakeResponse(200, "not-json")),
    )
    assert await probe_account_benefit_tier({"SID": "s", "SAPISID": "a"}) is None

    assert await probe_account_benefit_tier({}) is None


def test_explicit_free_payload_is_valid_but_unknown_is_not():
    assert parse_benefit_tier_strict("[]") == "Free"
    assert parse_benefit_tier_strict("[0]") == "Free"
    assert parse_benefit_tier_strict("[null]") == "Free"
    assert parse_benefit_tier_strict('[null, ""]') == "Free"
    assert parse_benefit_tier_strict('[null, "error message"]') is None
    assert parse_benefit_tier_strict("[1]") == "Pro"
    assert parse_benefit_tier_strict("[2]") == "Ultra"
    assert parse_benefit_tier_strict("[3]") == "Plus"
    assert parse_benefit_tier_strict("[9]") is None
    assert parse_benefit_tier_strict("[true]") is None
    assert parse_benefit_tier_strict("not-json") is None
    assert parse_benefit_tier_strict("null") is None


@pytest.mark.asyncio
async def test_detect_tier_route_returns_502_and_keeps_pro_on_probe_failure(
    tmp_path, monkeypatch
):
    service, store = _service(tmp_path)
    account = _account_with_auth(store, tier="Pro")
    update_mock = MagicMock(wraps=service.update_account_tier)
    service.update_account_tier = update_mock  # type: ignore[method-assign]
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_benefit_tier",
        AsyncMock(return_value=None),
    )
    manager = _FakeInstanceManager()
    state = RuntimeState(account_service=service, client=None, instance_manager=manager)

    with pytest.raises(HTTPException) as exc:
        await detect_account_tier(
            account_id=account.id, account_service=service, runtime_state=state
        )

    assert exc.value.status_code == 502
    update_mock.assert_not_called()
    assert store.get_account(account.id).tier == "Pro"  # type: ignore[union-attr]
    assert manager.synced == []


@pytest.mark.asyncio
async def test_detect_tier_http_502_body_status_is_unavailable(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    account = _account_with_auth(store, tier="Pro")
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_benefit_tier",
        AsyncMock(return_value=None),
    )
    from aistudio_api.api.app import app
    from aistudio_api.api.state import runtime_state

    saved_service = runtime_state.account_service
    saved_client = runtime_state.client
    saved_manager = runtime_state.instance_manager
    runtime_state.account_service = service
    runtime_state.client = None
    runtime_state.instance_manager = _FakeInstanceManager()  # type: ignore[assignment]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/accounts/{account.id}/detect-tier")
    finally:
        runtime_state.account_service = saved_service
        runtime_state.client = saved_client
        runtime_state.instance_manager = saved_manager

    assert resp.status_code == 502
    assert resp.json()["error"]["status"] == "UNAVAILABLE"
    assert store.get_account(account.id).tier == "Pro"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_detect_tier_route_400_when_no_cookies(tmp_path):
    service, store = _service(tmp_path)
    account = store.save_account(
        name="No Auth",
        email="noauth@example.com",
        storage_state={"cookies": [], "origins": []},
        tier="Ultra",
    )
    state = RuntimeState(
        account_service=service, client=None, instance_manager=_FakeInstanceManager()
    )

    with pytest.raises(HTTPException) as exc:
        await detect_account_tier(
            account_id=account.id, account_service=service, runtime_state=state
        )

    assert exc.value.status_code == 400
    assert store.get_account(account.id).tier == "Ultra"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_detect_tier_route_writes_explicit_free_and_syncs_instance(
    tmp_path, monkeypatch
):
    service, store = _service(tmp_path)
    account = _account_with_auth(store, tier="Pro")
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_benefit_tier",
        AsyncMock(return_value="Free"),
    )
    manager = _FakeInstanceManager()
    state = RuntimeState(account_service=service, client=None, instance_manager=manager)

    response = await detect_account_tier(
        account_id=account.id, account_service=service, runtime_state=state
    )

    assert response.tier == "Free"
    assert store.get_account(account.id).tier == "Free"  # type: ignore[union-attr]
    assert manager.synced == [(account.id, "Free")]


@pytest.mark.asyncio
async def test_manual_detect_bypasses_fresh_free_skip_and_updates_pool(
    tmp_path, monkeypatch
):
    service, store = _service(tmp_path)
    account = _account_with_auth(store, tier="Free")
    store.update_account_tier(
        account.id, "Free", checked_at=datetime.now(UTC).isoformat()
    )
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_benefit_tier",
        AsyncMock(return_value="Pro"),
    )
    manager = _FakeInstanceManager()
    state = RuntimeState(account_service=service, client=None, instance_manager=manager)

    response = await detect_account_tier(
        account_id=account.id, account_service=service, runtime_state=state
    )

    assert response.tier == "Pro"
    assert store.get_account(account.id).tier == "Pro"  # type: ignore[union-attr]
    assert manager.synced == [(account.id, "Pro")]


@pytest.mark.asyncio
async def test_detect_tier_pool_sync_failure_still_returns_updated_account(
    tmp_path, monkeypatch
):
    service, store = _service(tmp_path)
    account = _account_with_auth(store, tier="Pro")
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_benefit_tier",
        AsyncMock(return_value="Ultra"),
    )
    state = RuntimeState(
        account_service=service,
        client=None,
        instance_manager=_FailingSyncInstanceManager(),
    )

    response = await detect_account_tier(
        account_id=account.id, account_service=service, runtime_state=state
    )

    assert response.tier == "Ultra"
    assert store.get_account(account.id).tier == "Ultra"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_import_fallback_creates_free_for_new_account(tmp_path, monkeypatch):
    service, store = _service(tmp_path)
    monkeypatch.setattr(
        routes_accounts, "probe_account_email", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value=None)
    )
    state = RuntimeState(account_service=service, client=None, rotator=None)

    response = await import_cookies(
        ImportCookiesRequest(cookies="SID=sid; SAPISID=sapisid"),
        account_service=service,
        runtime_state=state,
    )

    assert response.tier == "Free"
    assert store.get_account(response.account_id).tier == "Free"  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_tier", ["Pro", "Ultra"])
async def test_import_probe_failure_does_not_downgrade_existing_account(
    tmp_path, monkeypatch, existing_tier
):
    service, store = _service(tmp_path)
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_email",
        AsyncMock(return_value="user@example.com"),
    )
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_benefit_tier",
        AsyncMock(return_value="Free"),
    )
    state = RuntimeState(account_service=service, client=None, rotator=None)
    request = ImportCookiesRequest(cookies="SID=sid; SAPISID=sapisid")
    first = await import_cookies(request, account_service=service, runtime_state=state)
    store.update_account_tier(first.account_id, existing_tier)

    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value=None)
    )
    second = await import_cookies(request, account_service=service, runtime_state=state)

    assert second.account_id == first.account_id
    assert store.get_account(first.account_id).tier == existing_tier  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_probe_google_accounts_infinite_does_not_fabricate_free(monkeypatch):
    client = _FakeAsyncClient(
        post_responses=[
            _FakeResponse(200, "[]"),  # ListModels: authenticated
            _FakeResponse(500, "rpc down"),  # BenefitTier: failed
            _FakeResponse(401, ""),  # next ListModels: no such user
        ]
    )
    _patch_client(monkeypatch, client)

    accounts = await probe_google_accounts_infinite(_STORAGE_STATE)

    assert len(accounts) == 1
    assert accounts[0]["tier"] is None


@pytest.mark.asyncio
async def test_startup_probe_failure_keeps_existing_tier(tmp_path, monkeypatch):
    from datetime import timedelta

    from aistudio_api.api import app as app_module
    from aistudio_api.infrastructure.account import cookie_parser

    _, store = _service(tmp_path)
    account = _account_with_auth(store, tier="Pro")
    store.update_account_refresh(
        account.id,
        tier="Pro",
        tier_checked_at=(datetime.now(UTC) - timedelta(days=4)).isoformat(),
    )
    monkeypatch.setattr(
        cookie_parser,
        "probe_account_benefit_tier",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        cookie_parser, "probe_account_email", AsyncMock(return_value=None)
    )

    await app_module._refresh_startup_account_metadata(store)

    assert store.get_account(account.id).tier == "Pro"  # type: ignore[union-attr]


def test_manifests_declare_socks_dependency_without_requiring_lockfile():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")

    assert "httpx[socks]" in pyproject
    assert "httpx[socks]" in requirements

    # ``uv.lock`` is git-ignored, so a clean checkout must still pass. Verify the
    # resolved extra only when a local lockfile happens to exist.
    lock = root / "uv.lock"
    if lock.exists():
        assert 'name = "socksio"' in lock.read_text(encoding="utf-8")
