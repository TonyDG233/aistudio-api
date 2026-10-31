"""Instance manager contracts: lanes, wake, busy guard, D-05 bounded rebind."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.instance_manager import (
    InstanceBusyError,
    InstanceManager,
    InstanceRebindError,
)
from aistudio_api.domain.instances import InstanceState
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstancePool,
    InstanceSpec,
    SessionFactory,
)


class FakePage:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.events.append("page.close")
        self.closed = True


class FakeSession:
    def __init__(self, events: list[str], auth_user: str) -> None:
        self.events = events
        self.active_context = SimpleNamespace(
            page=FakePage(events), auth_user=auth_user
        )

    @property
    def page(self) -> FakePage:
        return self.active_context.page

    async def close(self) -> None:
        await self.active_context.page.close()


class FakeCDP:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.created: list[str] = []
        self.disposed: list[str] = []
        self._counter = 0

    async def create_browser_context(self) -> str:
        self._counter += 1
        context_id = f"ctx-{self._counter}"
        self.created.append(context_id)
        self.events.append(f"context.create:{context_id}")
        return context_id

    async def dispose_browser_context(self, context_id: str) -> None:
        self.disposed.append(context_id)
        self.events.append(f"context.dispose:{context_id}")


async def _ready(value: FakeCDP) -> FakeCDP:
    return value


def _factory(cdp: FakeCDP, *, fail_contexts: set[str] | None = None) -> SessionFactory:
    async def factory(_cdp: Any, **kwargs: Any) -> FakeSession:
        context_id = str(kwargs["context_id"])
        cdp.events.append(f"session.create:{context_id}")
        if fail_contexts and context_id in fail_contexts:
            raise RuntimeError(f"bootstrap failed for {context_id}")
        return FakeSession(cdp.events, str(kwargs.get("auth_user") or "0"))

    return cast("SessionFactory", factory)


def _manager(
    store: AccountStore,
    cdp: FakeCDP,
    *,
    specs: list[InstanceSpec],
    max_contexts: int,
    fail_contexts: set[str] | None = None,
    load_attempts: int = 1,
    drain_timeout_s: float = 12.0,
    retired: list[str] | None = None,
) -> InstanceManager:
    pool = InstancePool(
        cast(Any, lambda: _ready(cdp)),
        specs=specs,
        max_contexts=max_contexts,
        session_factory=_factory(cdp, fail_contexts=fail_contexts),
        load_attempts=load_attempts,
        on_slot_retired=(retired.append if retired is not None else None),
    )
    return InstanceManager(pool, store, drain_timeout_s=drain_timeout_s)


def _store_with_accounts(
    tmp_path: Any,
    *accounts: tuple[str, str, str],
) -> tuple[AccountStore, dict[str, Any]]:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    by_name: dict[str, Any] = {}
    for name, email, tier in accounts:
        account = store.save_account(
            name=name,
            email=email,
            storage_state={"cookies": []},
            tier=tier,
        )
        by_name[name] = account
    if by_name:
        store.set_active_account(next(iter(by_name.values())).id)
    return store, by_name


def _free_spec(count: int = 1, surface: str = "chat") -> InstanceSpec:
    return InstanceSpec(count=count, surface=surface, min_tier="Free")


def _paid_spec(count: int = 1, surface: str = "chat") -> InstanceSpec:
    return InstanceSpec(count=count, surface=surface, min_tier="Pro")


def _by_account(items: tuple[Any, ...], account_id: str) -> Any:
    return next(item for item in items if item.account_id == account_id)


@pytest.mark.asyncio
async def test_startup_runs_free_and_paid_lanes_isolated(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path,
        ("free", "free@example.com", "Free"),
        ("pro", "pro@example.com", "Pro"),
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(), _paid_spec()], max_contexts=2)

    await manager.startup()
    result = await manager.list_instances()

    assert result.max_instances == 2
    assert len(result.instances) == 2
    assert all(item.state is InstanceState.ACTIVE for item in result.instances)
    free_item = _by_account(result.instances, accounts["free"].id)
    paid_item = _by_account(result.instances, accounts["pro"].id)
    assert free_item.min_tier == "Free"
    assert paid_item.min_tier == "Pro"
    assert [item.id for item in manager.eligible_instances(paid=False)] == [
        free_item.id
    ]
    assert [item.id for item in manager.eligible_instances(paid=True)] == [paid_item.id]


@pytest.mark.asyncio
async def test_free_account_never_occupies_paid_instance(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(), _paid_spec()], max_contexts=2)

    await manager.startup()
    result = await manager.list_instances()

    assert len(result.instances) == 2
    assert (
        _by_account(result.instances, accounts["free"].id).state is InstanceState.ACTIVE
    )
    paid = next(item for item in result.instances if item.min_tier == "Pro")
    assert paid.state is InstanceState.UNSTARTED
    assert paid.account_id == ""
    assert len(cdp.created) == 1

    store.save_account(
        name="pro2", email="pro2@example.com", storage_state={"cookies": []}, tier="Pro"
    )
    awoken = await manager.wake_eligible(paid=True)
    assert len(awoken) == 1
    assert awoken[0].state is InstanceState.ACTIVE
    assert len(cdp.created) == 2


@pytest.mark.asyncio
async def test_same_account_does_not_get_a_second_instance(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(count=2)], max_contexts=2)

    await manager.startup()
    result = await manager.list_instances()

    assert len(result.instances) == 2
    assert (
        sum(1 for item in result.instances if item.state is InstanceState.ACTIVE) == 1
    )
    assert (
        sum(1 for item in result.instances if item.state is InstanceState.UNSTARTED)
        == 1
    )
    assert len(cdp.created) == 1


@pytest.mark.asyncio
async def test_sleeping_instance_is_woken_on_message_arrival(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]

    slept = await manager.sleep(record.id)
    assert slept.state is InstanceState.SLEEPING
    assert len(cdp.disposed) == 1

    awoken = await manager.wake_eligible(paid=False)
    assert len(awoken) == 1
    assert awoken[0].state is InstanceState.ACTIVE
    assert len(cdp.created) == 2


@pytest.mark.asyncio
async def test_manual_close_refuses_active_requests(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]

    handle = await manager.begin_request(record.id)
    with pytest.raises(InstanceBusyError):
        await manager.close(record.id)

    handle.release()
    closed = await manager.close(record.id)
    assert closed is not None
    assert closed.state is InstanceState.RECLAIMING
    assert (await manager.list_instances()).instances == ()


@pytest.mark.asyncio
async def test_d05_rebind_waits_for_active_request_then_switches(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(
        store, cdp, specs=[_free_spec(count=2)], max_contexts=2, drain_timeout_s=1.0
    )
    await manager.startup()
    first = (await manager.list_instances()).instances[0]
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    handle = await manager.begin_request(first.id)

    async def _release_soon() -> None:
        await asyncio.sleep(0.02)
        handle.release()

    task = asyncio.create_task(_release_soon())
    switched = await manager.rebind_account(
        free2.id, previous_account_id=accounts["free1"].id, reason="test"
    )
    await task

    assert switched.account_id == free2.id
    assert switched.state is InstanceState.ACTIVE
    active = store.get_active_account()
    assert active is not None
    assert active.id == free2.id
    assert first.id not in {
        item.id
        for item in (await manager.list_instances()).instances
        if item.account_id == accounts["free1"].id
        and item.state is InstanceState.ACTIVE
    }

    page_close = cdp.events.index("page.close")
    old_dispose = cdp.events.index("context.dispose:ctx-1")
    new_create = cdp.events.index("context.create:ctx-2")
    assert page_close < old_dispose < new_create


@pytest.mark.asyncio
async def test_d05_rebind_timeout_force_closes_and_creates_page_after(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(
        store, cdp, specs=[_free_spec(count=2)], max_contexts=2, drain_timeout_s=0.02
    )
    await manager.startup()
    first = (await manager.list_instances()).instances[0]
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    handle = await manager.begin_request(first.id)
    started = time.monotonic()
    switched = await manager.rebind_account(
        free2.id, previous_account_id=accounts["free1"].id, reason="test"
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert switched.account_id == free2.id
    active = store.get_active_account()
    assert active is not None
    assert active.id == free2.id
    assert "context.dispose:ctx-1" in cdp.events
    assert cdp.events.index("page.close") < cdp.events.index("context.dispose:ctx-1")
    assert cdp.events.index("context.dispose:ctx-1") < cdp.events.index(
        "context.create:ctx-2"
    )
    handle.release()


@pytest.mark.asyncio
async def test_d05_rebind_failure_is_explicit_and_restores_old_account(
    tmp_path,
) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(
        store,
        cdp,
        specs=[_free_spec(count=2)],
        max_contexts=2,
        fail_contexts={"ctx-2"},
        drain_timeout_s=0.5,
    )
    await manager.startup()
    first = (await manager.list_instances()).instances[0]
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    with pytest.raises(InstanceRebindError):
        await manager.rebind_account(
            free2.id, previous_account_id=accounts["free1"].id, reason="test"
        )

    active = store.get_active_account()
    assert active is not None
    assert active.id == accounts["free1"].id
    restored = next(
        item
        for item in (await manager.list_instances()).instances
        if item.account_id == accounts["free1"].id
    )
    assert restored.state is InstanceState.ACTIVE
    target = next(
        item
        for item in (await manager.list_instances()).instances
        if item.id == first.id
    )
    assert target.id == restored.id


@pytest.mark.asyncio
async def test_d05_rebind_failure_single_slot_keeps_restored_account(
    tmp_path,
) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(
        store,
        cdp,
        specs=[_free_spec(count=1)],
        max_contexts=1,
        fail_contexts={"ctx-2"},
        drain_timeout_s=0.5,
    )
    await manager.startup()
    first = (await manager.list_instances()).instances[0]
    store.toggle_account_enabled(accounts["free1"].id, False)
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    with pytest.raises(InstanceRebindError):
        await manager.rebind_account(
            free2.id, previous_account_id=accounts["free1"].id, reason="test"
        )

    active = store.get_active_account()
    assert active is not None
    assert active.id == accounts["free1"].id
    records = (await manager.list_instances()).instances
    assert len(records) == 1
    assert records[0].id == first.id
    assert records[0].account_id == accounts["free1"].id
    assert records[0].state is InstanceState.ACTIVE


@pytest.mark.asyncio
async def test_switch_surface_reuses_slot_and_rolls_back_on_failure(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    specs = [_free_spec(surface="chat"), _free_spec(surface="build")]
    manager = _manager(
        store,
        cdp,
        specs=specs,
        max_contexts=2,
        fail_contexts={"ctx-2"},
        load_attempts=1,
    )
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    assert record.surface == "chat"

    from aistudio_api.application.instance_manager import InstanceNotReadyError

    with pytest.raises(InstanceNotReadyError):
        await manager.switch_to(record.id, "build")

    after = (await manager.list_instances()).instances[0]
    assert after.surface == "chat"
    assert after.state is InstanceState.ACTIVE
    assert len(manager._pool.slots()) == 2
    assert "context.dispose:ctx-1" in cdp.events
    assert "context.create:ctx-3" in cdp.events


@pytest.mark.asyncio
async def test_switch_surface_when_capacity_full_keeps_slot_count(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    specs = [_free_spec(surface="chat"), _free_spec(surface="build")]
    manager = _manager(store, cdp, specs=specs, max_contexts=2)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]

    switched = await manager.switch_to(record.id, "build")

    assert switched.surface == "build"
    assert len(manager._pool.slots()) == 2
    assert sum(1 for item in manager._pool.slots() if item.context_id) == 1


@pytest.mark.asyncio
async def test_stale_instance_waits_for_manual_restart(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    slot = manager._pool.slots()[0]
    assert slot is not None
    assert slot.page is not None
    slot.page.closed = True

    listed = (await manager.list_instances()).instances[0]
    assert listed.state is InstanceState.INVALID
    assert len(cdp.created) == 1  # no automatic stale revival

    restarted = await manager.restart(record.id)
    assert restarted.state is InstanceState.ACTIVE
    assert len(cdp.created) == 2


@pytest.mark.asyncio
async def test_create_is_async_starting_then_active(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)

    created = await manager.create(accounts["free"].id)

    assert created.state is InstanceState.STARTING
    await manager.wait_idle()
    listed = (await manager.list_instances()).instances
    assert len(listed) == 1
    assert listed[0].state is InstanceState.ACTIVE
    assert listed[0].account_id == accounts["free"].id


@pytest.mark.asyncio
async def test_close_without_instance_id_closes_pool(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()

    result = await manager.close()

    assert result is None
    assert manager._pool.slots() == ()
    assert (await manager.list_instances()).instances == ()


@pytest.mark.asyncio
async def test_ready_slot_for_account_reuses_existing_page(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    created_before = len(cdp.created)

    slot = manager.ready_slot_for_account(accounts["free"].id)

    assert slot is not None
    assert slot.account_id == accounts["free"].id
    assert slot.session is not None
    # Lookup must not create another Context/Page or retire the slot.
    assert len(cdp.created) == created_before
    assert manager.ready_slot_for_account("missing-account") is None
    assert len(cdp.created) == created_before
