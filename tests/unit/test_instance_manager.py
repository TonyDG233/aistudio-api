"""Instance manager contracts: lanes, wake, busy guard, D-05 bounded rebind."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.instance_manager import (
    InstanceAlreadyExistsError,
    InstanceBusyError,
    InstanceInvalidAccountError,
    InstanceManager,
    InstanceRebindError,
)
from aistudio_api.domain.instances import InstanceState
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstancePool,
    InstanceSpec,
    SessionFactory,
    SlotState,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec",
    [
        InstanceSpec(count=1, surface="chat", only_tier="Pro"),
        InstanceSpec(count=1, surface="chat", min_tier="Pro", max_tier="Pro"),
    ],
)
async def test_paid_interval_excludes_ultra_at_startup(tmp_path, spec) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("ultra", "ultra@example.com", "Ultra")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[spec], max_contexts=1)

    await manager.startup()
    record = (await manager.list_instances()).instances[0]

    assert record.state is InstanceState.UNSTARTED
    assert record.account_id == ""
    assert cdp.created == []


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
async def test_restart_ready_same_account_forces_close_and_rebuild(
    tmp_path, monkeypatch
) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    slot = manager._pool.slots()[0]
    assert slot.state is SlotState.READY
    first_context = slot.context_id
    first_realm = slot.realm_id
    first_session = slot.session
    assert first_session is not None

    calls: list[dict[str, Any]] = []
    original = manager._pool.activate

    async def spy(slot_id: str, **kwargs: Any) -> Any:
        calls.append({"slot_id": slot_id, **kwargs})
        return await original(slot_id, **kwargs)

    monkeypatch.setattr(manager._pool, "activate", spy)

    restarted = await manager.restart(record.id)

    assert calls
    assert calls[-1]["slot_id"] == slot.slot_id
    assert calls[-1]["force"] is True
    assert restarted.state is InstanceState.ACTIVE
    assert slot.state is SlotState.READY
    assert slot.generation == 2
    assert slot.realm_id != first_realm
    assert slot.context_id != first_context
    assert slot.session is not first_session
    assert cdp.created == ["ctx-1", "ctx-2"]
    assert cdp.disposed == [first_context]
    assert "page.close" in cdp.events
    assert cdp.events.index("page.close") < cdp.events.index(
        f"context.dispose:{first_context}"
    )


@pytest.mark.asyncio
async def test_restart_ready_same_account_with_inflight_request_stays_busy(
    tmp_path,
) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    slot = manager._pool.slots()[0]
    handle = await manager._pool.begin_request(slot.slot_id)

    try:
        with pytest.raises(InstanceBusyError):
            await manager.restart(record.id)
    finally:
        handle.release()

    assert slot.state is SlotState.READY
    assert slot.generation == 1
    assert cdp.created == ["ctx-1"]
    assert cdp.disposed == []


@pytest.mark.asyncio
async def test_reassign_drains_inflight_then_switches_same_slot(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(
        store, cdp, specs=[_free_spec()], max_contexts=1, drain_timeout_s=1.0
    )
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    handle = await manager.begin_request(record.id)

    async def _release_soon() -> None:
        await asyncio.sleep(0.02)
        handle.release()

    task = asyncio.create_task(_release_soon())
    updated = await manager.reassign(record.id, free2.id)
    await task

    assert updated.account_id == free2.id
    assert updated.state is InstanceState.ACTIVE
    assert updated.in_flight == 0
    assert cdp.events.index("page.close") < cdp.events.index("context.dispose:ctx-1")
    assert cdp.events.index("context.dispose:ctx-1") < cdp.events.index(
        "context.create:ctx-2"
    )
    assert accounts["free1"].id != updated.account_id


@pytest.mark.asyncio
async def test_reassign_timeout_force_closes_and_switches_same_slot(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(
        store, cdp, specs=[_free_spec()], max_contexts=1, drain_timeout_s=0.02
    )
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    handle = await manager.begin_request(record.id)
    started = time.monotonic()
    updated = await manager.reassign(record.id, free2.id)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert updated.account_id == free2.id
    assert updated.state is InstanceState.ACTIVE
    assert updated.in_flight == 0
    assert "context.dispose:ctx-1" in cdp.events
    assert cdp.events.index("context.dispose:ctx-1") < cdp.events.index(
        "context.create:ctx-2"
    )
    handle.release()


@pytest.mark.asyncio
async def test_reassign_unbound_slot_binds_account(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(count=2)], max_contexts=2)
    await manager.startup()
    unbound = next(
        item
        for item in (await manager.list_instances()).instances
        if not item.account_id
    )
    assert unbound.state is InstanceState.UNSTARTED
    free2 = store.save_account(
        name="free2",
        email="free2@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )

    bound = await manager.reassign(unbound.id, free2.id)

    assert bound.account_id == free2.id
    assert bound.state is InstanceState.ACTIVE
    assert len(cdp.created) == 2


@pytest.mark.asyncio
async def test_reassign_rejects_account_bound_to_other_instance(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path,
        ("free1", "free1@example.com", "Free"),
        ("free2", "free2@example.com", "Free"),
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(count=2)], max_contexts=2)
    await manager.startup()
    first = next(
        item
        for item in (await manager.list_instances()).instances
        if item.account_id == accounts["free1"].id
    )

    with pytest.raises(InstanceAlreadyExistsError, match="已绑定实例 #2"):
        await manager.reassign(first.id, accounts["free2"].id)


@pytest.mark.asyncio
async def test_reassign_recovers_account_from_invalid_stale_slot(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free1", "free1@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(count=2)], max_contexts=2)
    await manager.startup()
    records = (await manager.list_instances()).instances
    bound = next(item for item in records if item.account_id)
    unbound = next(item for item in records if not item.account_id)
    bound_slot = next(
        slot
        for slot in manager._pool.slots()
        if slot.account_id == accounts["free1"].id
    )
    assert bound_slot.page is not None
    bound_slot.page.closed = True

    stale = (await manager.list_instances()).instances
    stale_bound = next(item for item in stale if item.id == bound.id)
    assert stale_bound.state is InstanceState.INVALID
    assert stale_bound.account_id == accounts["free1"].id

    rebound = await manager.reassign(unbound.id, accounts["free1"].id)

    assert rebound.account_id == accounts["free1"].id
    assert rebound.state is InstanceState.ACTIVE


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
async def test_create_accepts_explicit_tier_selector_without_configured_spec(
    tmp_path,
) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[], max_contexts=2)

    created = await manager.create(accounts["free"].id, only_tier="Free")

    assert created.state is InstanceState.STARTING
    assert created.only_tier == "Free"
    assert created.min_tier == "Free"
    assert created.max_tier == "Free"
    await manager.wait_idle()
    listed = (await manager.list_instances()).instances
    assert listed[0].state is InstanceState.ACTIVE
    assert listed[0].account_id == accounts["free"].id


@pytest.mark.asyncio
async def test_create_rejects_account_already_bound_to_instance(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=2)
    await manager.startup()

    with pytest.raises(InstanceAlreadyExistsError):
        await manager.create(accounts["free"].id)


@pytest.mark.asyncio
async def test_unbound_restart_and_wake_raise_clear_account_error(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(), _free_spec()], max_contexts=2)
    await manager.startup()

    unbound = next(
        item
        for item in (await manager.list_instances()).instances
        if not item.account_id
    )
    with pytest.raises(InstanceInvalidAccountError, match="账号"):
        await manager.restart(unbound.id)
    with pytest.raises(InstanceInvalidAccountError, match="账号"):
        await manager.wake(unbound.id)


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
async def test_sync_account_tier_moves_promoted_account_to_paid_and_refills_free(
    tmp_path,
) -> None:
    store, accounts = _store_with_accounts(
        tmp_path,
        ("promoted", "promoted@example.com", "Free"),
        ("spare", "spare@example.com", "Free"),
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(), _paid_spec()], max_contexts=2)
    await manager.startup()

    before = (await manager.list_instances()).instances
    free_before = next(item for item in before if item.min_tier == "Free")
    assert free_before.account_id == accounts["promoted"].id
    assert next(item for item in before if item.min_tier == "Pro").account_id == ""

    store.update_account_tier(accounts["promoted"].id, "Pro")
    updated = await manager.sync_account_tier(accounts["promoted"].id, "Pro")

    assert updated >= 1
    after = (await manager.list_instances()).instances
    promoted = next(
        item for item in after if item.account_id == accounts["promoted"].id
    )
    spare = next(item for item in after if item.account_id == accounts["spare"].id)
    assert promoted.min_tier == "Pro"
    assert promoted.state is InstanceState.ACTIVE
    assert promoted.tier == "Pro"
    assert spare.min_tier == "Free"
    assert spare.state is InstanceState.ACTIVE
    assert spare.tier == "Free"
    assert sum(1 for item in after if item.account_id == accounts["promoted"].id) == 1

    slots = manager._pool.slots()
    promoted_slot = next(
        slot for slot in slots if slot.account_id == accounts["promoted"].id
    )
    spare_slot = next(slot for slot in slots if slot.account_id == accounts["spare"].id)
    assert promoted_slot.lane == "paid"
    assert promoted_slot.session is not None
    assert promoted_slot.session.active_context.tier == "Pro"
    assert spare_slot.lane == "free"
    assert spare_slot.session is not None
    assert spare_slot.session.active_context.tier == "Free"

    await manager.sync_account_tier(accounts["promoted"].id, "Pro")
    account_ids = [slot.account_id for slot in manager._pool.slots() if slot.account_id]
    assert len(account_ids) == len(set(account_ids))
    assert (
        next(
            item
            for item in (await manager.list_instances()).instances
            if item.account_id == accounts["promoted"].id
        ).min_tier
        == "Pro"
    )


@pytest.mark.asyncio
async def test_sync_account_tier_without_free_replacement_unbinds_free_slot(
    tmp_path,
) -> None:
    store, accounts = _store_with_accounts(
        tmp_path, ("promoted", "promoted@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(), _paid_spec()], max_contexts=2)
    await manager.startup()

    store.update_account_tier(accounts["promoted"].id, "Pro")
    await manager.sync_account_tier(accounts["promoted"].id, "Pro")

    after = (await manager.list_instances()).instances
    promoted = next(
        item for item in after if item.account_id == accounts["promoted"].id
    )
    free_item = next(item for item in after if item.min_tier == "Free")
    assert promoted.min_tier == "Pro"
    assert promoted.state is InstanceState.ACTIVE
    assert free_item.account_id == ""
    assert free_item.state is InstanceState.UNSTARTED

    free_slot = next(slot for slot in manager._pool.slots() if slot.lane == "free")
    assert free_slot.account_id == ""
    assert free_slot.state is SlotState.UNSTARTED
    assert free_slot.session is None

    spare = store.save_account(
        name="spare",
        email="spare@example.com",
        storage_state={"cookies": []},
        tier="Free",
    )
    awoken = await manager.wake_eligible(paid=False)

    assert len(awoken) == 1
    assert awoken[0].account_id == spare.id
    assert awoken[0].state is InstanceState.ACTIVE
    assert free_slot.account_id == spare.id


@pytest.mark.asyncio
async def test_sync_account_tier_binds_restored_pro_to_unstarted_paid_slot(
    tmp_path,
) -> None:
    store, accounts = _store_with_accounts(
        tmp_path,
        ("free", "free@example.com", "Free"),
        ("dormant", "dormant@example.com", "Free"),
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(), _paid_spec()], max_contexts=2)
    await manager.startup()

    before = (await manager.list_instances()).instances
    assert (
        next(item for item in before if item.min_tier == "Pro").state
        is InstanceState.UNSTARTED
    )

    store.update_account_tier(accounts["dormant"].id, "Pro")
    await manager.sync_account_tier(accounts["dormant"].id, "Pro")

    after = (await manager.list_instances()).instances
    dormant = next(item for item in after if item.account_id == accounts["dormant"].id)
    assert dormant.min_tier == "Pro"
    assert dormant.state is InstanceState.ACTIVE
    assert dormant.tier == "Pro"
    paid_slot = next(slot for slot in manager._pool.slots() if slot.lane == "paid")
    assert paid_slot.account_id == accounts["dormant"].id
    assert paid_slot.state is SlotState.READY
    assert paid_slot.session is not None
    assert paid_slot.session.active_context.tier == "Pro"


@pytest.mark.asyncio
async def test_sync_account_tier_same_lane_updates_without_restart(tmp_path) -> None:
    store, accounts = _store_with_accounts(tmp_path, ("pro", "pro@example.com", "Pro"))
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_paid_spec()], max_contexts=1)
    await manager.startup()

    slot = manager._pool.slots()[0]
    context_id = slot.context_id
    assert slot.account_id == accounts["pro"].id
    assert slot.tier == "Pro"

    store.update_account_tier(accounts["pro"].id, "Ultra")
    updated = await manager.sync_account_tier(accounts["pro"].id, "Ultra")

    assert updated == 1
    assert slot.account_id == accounts["pro"].id
    assert slot.context_id == context_id
    assert slot.tier == "Ultra"
    assert slot.session is not None
    assert slot.session.active_context.tier == "Ultra"
    assert len(cdp.created) == 1
    listed = (await manager.list_instances()).instances
    assert len(listed) == 1
    assert listed[0].account_id == accounts["pro"].id
    assert listed[0].tier == "Ultra"


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


@pytest.mark.asyncio
async def test_rebuild_keeps_disabled_bound_account_unstarted(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path,
        ("free", "free@example.com", "Free"),
        ("spare", "spare@example.com", "Free"),
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()
    record = (await manager.list_instances()).instances[0]
    bound_account = record.account_id
    assert bound_account == accounts["free"].id

    store.toggle_account_enabled(bound_account, False)
    await manager.destroy_runtimes()
    rebuilt, total = await manager.rebuild_runtimes()

    assert (rebuilt, total) == (0, 1)
    listed = (await manager.list_instances()).instances[0]
    assert listed.state is InstanceState.UNSTARTED
    assert listed.account_id == bound_account  # Q-05: no silent rebind
    slot = manager._pool.slots()[0]
    assert slot.account_id == bound_account
    assert slot.session is None


@pytest.mark.asyncio
async def test_destroy_and_rebuild_take_lifecycle_lock(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()

    await manager._lifecycle_lock.acquire()
    destroy = asyncio.create_task(manager.destroy_runtimes())
    await asyncio.sleep(0)
    assert not destroy.done()
    manager._lifecycle_lock.release()
    assert await destroy == 1

    await manager._lifecycle_lock.acquire()
    rebuild = asyncio.create_task(manager.rebuild_runtimes())
    await asyncio.sleep(0)
    assert not rebuild.done()
    manager._lifecycle_lock.release()
    assert await rebuild == (1, 1)


@pytest.mark.asyncio
async def test_lifecycle_scope_blocks_manual_rebind(tmp_path) -> None:
    store, accounts = _store_with_accounts(
        tmp_path,
        ("free1", "free1@example.com", "Free"),
        ("free2", "free2@example.com", "Free"),
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec(count=2)], max_contexts=2)
    await manager.startup()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_scope() -> None:
        async with manager.lifecycle_scope():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold_scope())
    await entered.wait()
    manual = asyncio.create_task(
        manager.rebind_account(
            accounts["free2"].id,
            previous_account_id=accounts["free1"].id,
            reason="concurrency-test",
        )
    )
    await asyncio.sleep(0)
    assert not manual.done()

    release.set()
    await holder
    rebound = await manual
    assert rebound.account_id == accounts["free2"].id


@pytest.mark.asyncio
async def test_lifecycle_scope_is_reentrant_for_destroy_and_rebuild(tmp_path) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    manager = _manager(store, cdp, specs=[_free_spec()], max_contexts=1)
    await manager.startup()

    async with manager.lifecycle_scope():
        assert await manager.destroy_runtimes() == 1
        assert await manager.rebuild_runtimes() == (1, 1)
    assert manager._lifecycle_depth == 0
