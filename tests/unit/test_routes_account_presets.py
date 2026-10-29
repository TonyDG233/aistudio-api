"""路由级测试：toggle / group / groups / presets 与 D-05 有界 rebind。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.api.state import runtime_state
from aistudio_api.application.account_service import AccountService
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstancePool,
    InstanceSpec,
)


class _Page:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, auth_user: str) -> None:
        self.active_context = SimpleNamespace(page=_Page(), auth_user=auth_user)

    @property
    def page(self) -> _Page:
        return self.active_context.page

    async def close(self) -> None:
        await self.active_context.page.close()


class _CDP:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.disposed: list[str] = []
        self._counter = 0

    async def create_browser_context(self) -> str:
        self._counter += 1
        context_id = f"ctx-{self._counter}"
        self.created.append(context_id)
        return context_id

    async def dispose_browser_context(self, context_id: str) -> None:
        self.disposed.append(context_id)


async def _ready(cdp: _CDP) -> _CDP:
    return cdp


def _real_manager(
    store: AccountStore,
    *,
    specs: list[InstanceSpec],
    max_contexts: int,
    drain_timeout_s: float = 12.0,
) -> tuple[InstanceManager, _CDP]:
    cdp = _CDP()

    async def factory(_cdp: Any, **kwargs: Any) -> _Session:
        return _Session(str(kwargs.get("auth_user") or "0"))

    pool = InstancePool(
        cast(Any, lambda: _ready(cdp)),
        specs=specs,
        max_contexts=max_contexts,
        session_factory=cast(Any, factory),
    )
    return InstanceManager(pool, store, drain_timeout_s=drain_timeout_s), cdp


class _FailingManager:
    async def rebind_account(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("bounded rebind exploded")


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
    orig_manager = runtime_state.instance_manager
    runtime_state.account_service = service
    runtime_state.client = None
    runtime_state.instance_manager = None
    try:
        yield service, store
    finally:
        runtime_state.account_service = orig_service
        runtime_state.client = orig_client
        runtime_state.instance_manager = orig_manager


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
async def test_toggle_without_manager_is_explicit_unsupported(client_env) -> None:
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
        assert body["rebind_status"] == "unsupported"
        assert body["previous_active_account_id"] == active.id
        assert body["active_account_id"] == target.id
        assert body["target_account"]["id"] == target.id

        # fallback 不静默改写 active。
        assert store.get_active_account().id == active.id


@pytest.mark.asyncio
async def test_toggle_rebind_failure_is_409_and_rolls_back_enabled(client_env) -> None:
    service, store = client_env
    accounts = service.list_accounts()
    active = accounts[0]
    runtime_state.instance_manager = _FailingManager()
    async with _client() as client:
        resp = await client.post(
            f"/api/accounts/{active.id}/toggle", json={"enabled": False}
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "instance_rebind_failed"
    assert store.get_active_account().id == active.id
    assert store.get_account(active.id).enabled is True  # type: ignore[union-attr]


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
    assert body["rebind_status"] == "not_required"
    assert body["target_account"] is None


@pytest.mark.asyncio
async def test_toggle_disables_active_and_performs_bounded_rebind(tmp_path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    free1 = store.save_account(
        name="free1", email="f1@example.com", storage_state={"cookies": []}, tier="Free"
    )
    free2 = store.save_account(
        name="free2", email="f2@example.com", storage_state={"cookies": []}, tier="Free"
    )
    store.set_active_account(free1.id)
    service = AccountService(store)
    manager, cdp = _real_manager(
        store,
        specs=[InstanceSpec(count=2, surface="chat", min_tier="Free")],
        max_contexts=2,
        drain_timeout_s=0.02,
    )
    await manager.startup()
    instances = (await manager.list_instances()).instances
    first = next(item for item in instances if item.account_id == free1.id)
    handle = await manager.begin_request(first.id)

    runtime_state.account_service = service
    runtime_state.instance_manager = manager
    try:
        async with _client() as client:
            resp = await client.post(
                f"/api/accounts/{free1.id}/toggle", json={"enabled": False}
            )
    finally:
        runtime_state.account_service = None
        runtime_state.instance_manager = None

    handle.release()
    assert resp.status_code == 200
    body = resp.json()
    assert body["rebind_status"] == "applied"
    assert body["rebind_required"] is False
    assert body["active_account_id"] == free2.id
    active = store.get_active_account()
    assert active is not None
    assert active.id == free2.id
    assert store.get_account(free1.id).enabled is False  # type: ignore[union-attr]
    assert "ctx-1" in cdp.disposed
    assert all(
        item.account_id != free1.id
        for item in (await manager.list_instances()).instances
        if item.state.value == "active"
    )


@pytest.mark.asyncio
async def test_preset_apply_disables_active_and_performs_rebind(tmp_path) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    free = store.save_account(
        name="free",
        email="free@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )
    pro = store.save_account(
        name="pro", email="pro@example.com", storage_state={"cookies": []}, tier="Pro"
    )
    store.set_active_account(free.id)
    service = AccountService(store)
    manager, _cdp = _real_manager(
        store,
        specs=[
            InstanceSpec(count=1, surface="chat", min_tier="Free"),
            InstanceSpec(count=1, surface="chat", min_tier="Pro"),
        ],
        max_contexts=2,
    )
    await manager.startup()

    runtime_state.account_service = service
    runtime_state.instance_manager = manager
    try:
        async with _client() as client:
            created = await client.post(
                "/api/accounts/presets",
                json={
                    "name": "Pro only",
                    "action": "filter_tier",
                    "target_tiers": ["Pro"],
                },
            )
            preset_id = created.json()["id"]
            applied = await client.post(f"/api/accounts/presets/{preset_id}/apply")
            deleted = await client.delete(f"/api/accounts/presets/{preset_id}")
    finally:
        runtime_state.account_service = None
        runtime_state.instance_manager = None

    assert applied.status_code == 200
    body = applied.json()
    assert body["rebind_status"] == "applied"
    assert body["rebind_required"] is False
    assert body["active_account_id"] == pro.id
    active = store.get_active_account()
    assert active is not None
    assert active.id == pro.id
    assert store.get_account(free.id).enabled is False  # type: ignore[union-attr]
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_preset_rebind_failure_restores_enabled_snapshot(client_env) -> None:
    service, store = client_env
    active = service.list_accounts()[0]
    runtime_state.instance_manager = _FailingManager()
    async with _client() as client:
        created = await client.post(
            "/api/accounts/presets",
            json={"name": "Pro only", "action": "filter_tier", "target_tiers": ["Pro"]},
        )
        applied = await client.post(
            f"/api/accounts/presets/{created.json()['id']}/apply"
        )
        await client.delete(f"/api/accounts/presets/{created.json()['id']}")
    assert applied.status_code == 409
    assert applied.json()["detail"]["code"] == "instance_rebind_failed"
    assert store.get_active_account().id == active.id
    assert store.get_account(active.id).enabled is True  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_preset_crud_and_snapshot_keeps_active(client_env) -> None:
    service, store = client_env
    active = service.list_accounts()[0]
    async with _client() as client:
        listed = await client.get("/api/accounts/presets/list")
        assert listed.status_code == 200
        assert any(p["id"] == "preset_all" for p in listed.json())

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
        assert applied.json()["rebind_status"] == "not_required"
        assert store.get_active_account().id == active.id
