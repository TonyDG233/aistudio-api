"""Browserless account routes must never touch the control-plane browser."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.api.routes_accounts import (
    BatchDeleteRequest,
    BatchToggleRequest,
    ImportCookiesRequest,
    batch_delete_accounts,
    batch_toggle_accounts,
    delete_account,
    delete_cookie_group,
    import_cookies,
)
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_info_refresher import (
    AccountInfoRefresher,
    TransportResponse,
)
from aistudio_api.infrastructure.account.account_store import AccountStore


class _RaisingSession:
    engine = "browserless"
    page = None
    _auth_file = None

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def import_cookies(self, *args: object, **kwargs: object) -> int:
        self.calls.append("import_cookies")
        raise AssertionError("browserless must not import cookies into a page")

    async def switch_auth(self, *args: object, **kwargs: object) -> None:
        self.calls.append("switch_auth")
        raise AssertionError("browserless must not switch a browser session")

    async def ensure_botguard_service(self, *args: object, **kwargs: object) -> None:
        self.calls.append("ensure_botguard_service")
        raise AssertionError("browserless must not run BotGuard")


def _state(session: _RaisingSession) -> Any:
    return SimpleNamespace(
        client=SimpleNamespace(_session=session),
        engine_settings={"mode": "browserless"},
        instance_manager=None,
        rotator=None,
        ui_sandbox=None,
        ui_debug=False,
    )


def _service(tmp_path: Path) -> AccountService:
    return AccountService(AccountStore(accounts_dir=tmp_path))


def _cookie_json(value: str = "v") -> str:
    return json.dumps(
        [
            {
                "name": "SAPISID",
                "value": value,
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "SID",
                "value": f"sid-{value}",
                "domain": ".google.com",
                "path": "/",
            },
        ]
    )


async def _import(service: AccountService, state: Any, name: str, value: str) -> str:
    response = await import_cookies(
        ImportCookiesRequest(
            cookies=_cookie_json(value),
            name=name,
            email=f"{name}@example.com",
            tier="Free",
        ),
        service,
        state,
    )
    return response.account_id


@pytest.mark.asyncio
async def test_browserless_import_skips_browser_injection(tmp_path: Path) -> None:
    session = _RaisingSession()
    state = _state(session)
    service = _service(tmp_path)

    account_id = await _import(service, state, "one", "v1")

    assert service.get_account(account_id) is not None
    assert session.calls == []


@pytest.mark.asyncio
async def test_browserless_delete_active_skips_switch_auth(tmp_path: Path) -> None:
    session = _RaisingSession()
    state = _state(session)
    service = _service(tmp_path)
    account_id = await _import(service, state, "one", "v1")
    service.set_active_account(account_id)

    result = await delete_account(account_id, service, state)

    assert result == {"ok": True}
    assert service.get_account(account_id) is None
    assert session.calls == []


@pytest.mark.asyncio
async def test_browserless_batch_delete_skips_switch_auth(tmp_path: Path) -> None:
    session = _RaisingSession()
    state = _state(session)
    service = _service(tmp_path)
    first = await _import(service, state, "one", "v1")
    second = await _import(service, state, "two", "v2")
    service.set_active_account(first)

    result = await batch_delete_accounts(
        BatchDeleteRequest(ids=[first, second]), service, state
    )

    assert result.deleted == 2
    assert session.calls == []


@pytest.mark.asyncio
async def test_browserless_group_delete_skips_switch_auth(tmp_path: Path) -> None:
    session = _RaisingSession()
    state = _state(session)
    service = _service(tmp_path)
    service.save_account_from_cookies(
        name="one",
        email="one@example.com",
        storage_state={"cookies": json.loads(_cookie_json("v1")), "origins": []},
        auth_user="0",
        cookie_id="cookie_group",
        tier="Free",
    )
    second = service.save_account_from_cookies(
        name="two",
        email="two@example.com",
        storage_state={"cookies": json.loads(_cookie_json("v2")), "origins": []},
        auth_user="0",
        cookie_id="cookie_group",
        tier="Free",
    )
    service.set_active_account(second.id)

    result = await delete_cookie_group("cookie_group", service, state)

    assert result["deleted"] == 2
    assert session.calls == []


@pytest.mark.asyncio
async def test_browserless_batch_toggle_never_switches_browser(tmp_path: Path) -> None:
    session = _RaisingSession()
    state = _state(session)
    service = _service(tmp_path)
    active = await _import(service, state, "one", "v1")
    fallback = await _import(service, state, "two", "v2")
    service.set_active_account(active)

    response = await batch_toggle_accounts(
        BatchToggleRequest(ids=[active], enabled=False), service, state
    )

    assert response.results[0].ok is True
    assert {account.id for account in service.list_accounts()} == {active, fallback}
    assert session.calls == []


@pytest.mark.asyncio
async def test_activate_account_browserless_skips_switch(tmp_path: Path) -> None:
    session = _RaisingSession()
    service = _service(tmp_path)
    account_id = await _import(service, _state(session), "one", "v1")

    account = await service.activate_account(account_id, cast(Any, session))

    assert account is not None
    assert account.id == account_id
    active = service.get_active_account()
    assert active is not None and active.id == account_id
    assert session.calls == []


@pytest.mark.asyncio
async def test_account_info_refresher_is_http_only(tmp_path: Path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "acc")
    account = store.save_account(
        name="one",
        email="one@example.com",
        storage_state={"cookies": json.loads(_cookie_json("v1")), "origins": []},
    )

    class _Transport:
        async def request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str],
            content: str = "",
        ) -> TransportResponse:
            del method, url, headers, content
            return TransportResponse(status=200, body="[1]")

    refresher = AccountInfoRefresher(store, transport=_Transport())
    result = await refresher.refresh_account(account, refresh_email=False)

    assert result.ok is True
    assert result.tier == "Pro"
