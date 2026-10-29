"""路由级测试：toggle / group / groups / presets，notes 与批量分组已删除。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_store import AccountStore


@pytest.fixture()
async def client_env(tmp_path) -> AsyncIterator[tuple[AccountService, AccountStore]]:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    service = AccountService(store)
    acc1 = store.save_account(
        name="Free",
        email="free@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )
    store.save_account(
        name="Pro", email="pro@example.com", storage_state={"cookies": []}, tier="Pro"
    )
    store.set_active_account(acc1.id)

    orig_service = runtime_state.account_service
    orig_client = runtime_state.client
    runtime_state.account_service = service
    runtime_state.client = None
    try:
        yield service, store
    finally:
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_notes_and_batch_group_endpoints_removed(client_env) -> None:
    service, _store = client_env
    account_id = service.list_accounts()[0].id
    async with _client() as client:
        notes = await client.put(
            f"/api/accounts/{account_id}/notes", json={"notes": "x"}
        )
        assert notes.status_code in (404, 405)

        batch = await client.post(
            "/api/accounts/groups/vip/toggle", json={"enabled": False}
        )
        assert batch.status_code in (404, 405)


@pytest.mark.asyncio
async def test_group_endpoints_remain(client_env) -> None:
    service, _store = client_env
    account_id = service.list_accounts()[0].id
    async with _client() as client:
        updated = await client.put(
            f"/api/accounts/{account_id}/group", json={"group": "vip"}
        )
        assert updated.status_code == 200
        assert updated.json()["group"] == "vip"

        groups = await client.get("/api/accounts/groups/list")
        assert groups.status_code == 200
        assert "vip" in groups.json()


@pytest.mark.asyncio
async def test_toggle_disabled_active_returns_rebind_and_keeps_active(
    client_env,
) -> None:
    service, store = client_env
    accounts = service.list_accounts()
    active = accounts[0]
    target = accounts[1]
    async with _client() as client:
        resp = await client.post(
            f"/api/accounts/{active.id}/toggle", json={"enabled": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["rebind_required"] is True
        assert body["previous_active_account_id"] == active.id
        assert body["active_account_id"] == target.id
        assert body["target_account"]["id"] == target.id

        # D-05：active 不得在 rebind 前被改写。
        assert store.get_active_account().id == active.id
        active_resp = await client.get("/api/accounts/active")
        assert active_resp.status_code == 200
        assert active_resp.json()["id"] == active.id


@pytest.mark.asyncio
async def test_toggle_non_active_has_no_rebind_signal(client_env) -> None:
    service, _store = client_env
    accounts = service.list_accounts()
    target = accounts[1]
    async with _client() as client:
        resp = await client.post(
            f"/api/accounts/{target.id}/toggle", json={"enabled": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["rebind_required"] is False
        assert body["target_account"] is None


@pytest.mark.asyncio
async def test_preset_crud_and_apply_returns_rebind(client_env) -> None:
    service, store = client_env
    accounts = service.list_accounts()
    active = accounts[0]
    target = accounts[1]
    async with _client() as client:
        listed = await client.get("/api/accounts/presets/list")
        assert listed.status_code == 200
        assert any(p["id"] == "preset_all" for p in listed.json())

        created = await client.post(
            "/api/accounts/presets",
            json={
                "name": "Pro only",
                "action": "filter_tier",
                "target_tiers": ["Pro"],
            },
        )
        assert created.status_code == 200
        preset_id = created.json()["id"]

        applied = await client.post(f"/api/accounts/presets/{preset_id}/apply")
        assert applied.status_code == 200
        body = applied.json()
        assert body["rebind_required"] is True
        assert body["previous_active_account_id"] == active.id
        assert body["active_account_id"] == target.id
        assert body["target_account"]["id"] == target.id
        assert store.get_active_account().id == active.id
        assert store.get_account(active.id).enabled is False  # type: ignore[union-attr]

        deleted = await client.delete(f"/api/accounts/presets/{preset_id}")
        assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_snapshot_preset_keeps_active_without_rebind(client_env) -> None:
    service, store = client_env
    active = service.list_accounts()[0]
    async with _client() as client:
        created = await client.post(
            "/api/accounts/presets/snapshot",
            json={"name": "current", "description": ""},
        )
        assert created.status_code == 200
        applied = await client.post(
            f"/api/accounts/presets/{created.json()['id']}/apply"
        )
        assert applied.status_code == 200
        assert applied.json()["rebind_required"] is False
        assert store.get_active_account().id == active.id
