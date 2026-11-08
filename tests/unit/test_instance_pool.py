"""Instance pool contracts: one account + one Context + one Page + one surface."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.infrastructure.gateway.instance_pool import (
    InstanceBusyError,
    InstanceCapacityError,
    InstancePool,
    InstanceSpec,
    InstanceTierError,
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


def _factory(cdp: FakeCDP, *, fail_contexts: set[str] | None = None) -> SessionFactory:
    async def factory(_cdp: Any, **kwargs: Any) -> FakeSession:
        context_id = str(kwargs["context_id"])
        cdp.events.append(f"session.create:{context_id}")
        if fail_contexts and context_id in fail_contexts:
            raise RuntimeError(f"bootstrap failed for {context_id}")
        return FakeSession(cdp.events, str(kwargs.get("auth_user") or "0"))

    return cast("SessionFactory", factory)


async def _ready(value: FakeCDP) -> FakeCDP:
    return value


def _pool(
    cdp: FakeCDP,
    *,
    specs: list[InstanceSpec] | None = None,
    max_contexts: int = 2,
    on_slot_retired: Any = None,
    fail_contexts: set[str] | None = None,
    load_attempts: int = 3,
) -> InstancePool:
    return InstancePool(
        cast(Any, lambda: _ready(cdp)),
        specs=specs
        or [
            InstanceSpec(count=1, surface="chat", min_tier="Free"),
            InstanceSpec(count=1, surface="chat", min_tier="Pro"),
        ],
        max_contexts=max_contexts,
        session_factory=_factory(cdp, fail_contexts=fail_contexts),
        on_slot_retired=on_slot_retired,
        load_attempts=load_attempts,
    )


async def _activate(
    pool: InstancePool,
    slot_id: str,
    *,
    account_id: str = "acc",
    tier: str = "Free",
    lane: str = "free",
    surface: str = "chat",
    force: bool = False,
) -> None:
    await pool.activate(
        slot_id,
        account_id=account_id,
        auth_file=f"{account_id}.json",
        auth_user="0",
        tier=tier,
        account_lane=lane,
        surface=surface,
        force=force,
    )


@pytest.mark.asyncio
async def test_free_and_paid_instances_are_isolated_and_bounded() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    free = await pool.add_slot(pool.specs[0], lane="free")
    paid = await pool.add_slot(pool.specs[1], lane="paid")

    await _activate(pool, free.slot_id, account_id="free-acc", tier="Free")
    await _activate(pool, paid.slot_id, account_id="pro-acc", tier="Pro", lane="paid")

    assert pool.live_contexts == 2
    assert free.context_id != paid.context_id
    assert free.realm_id != paid.realm_id
    assert [slot.slot_id for slot in pool.eligible_slots(paid=False)] == [free.slot_id]
    assert [slot.slot_id for slot in pool.eligible_slots(paid=True)] == [paid.slot_id]

    third = InstanceSpec(count=1, surface="chat", min_tier="Free")
    with pytest.raises(InstanceCapacityError):
        await pool.add_slot(third, lane="free")

    with pytest.raises(InstanceTierError):
        await _activate(pool, free.slot_id, account_id="pro", tier="Pro", lane="paid")


@pytest.mark.asyncio
async def test_one_page_per_instance_and_page_closes_before_context() -> None:
    cdp = FakeCDP()
    retired: list[str] = []
    pool = _pool(cdp, on_slot_retired=retired.append, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)

    assert slot.page is not None
    assert slot.session is not None
    assert sum(1 for event in cdp.events if event.startswith("session.create")) == 1
    assert pool.snapshot()[0]["pages"] == 1

    old_realm = slot.realm_id
    await pool.close_slot(slot.slot_id)

    assert slot.context_id == ""
    assert slot.session is None
    assert cdp.disposed == ["ctx-1"]
    assert cdp.events.index("page.close") < cdp.events.index("context.dispose:ctx-1")
    assert retired == [old_realm]
    assert pool.slots() == ()


@pytest.mark.asyncio
async def test_active_requests_release_and_block_manual_close() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)

    handle = await pool.begin_request(slot.slot_id)
    assert slot.active_requests == 1

    slot.state = SlotState.RETIRING
    with pytest.raises(InstanceCapacityError):
        await pool.begin_request(slot.slot_id)
    slot.state = SlotState.READY

    with pytest.raises(InstanceCapacityError):
        await pool.close_slot(slot.slot_id)

    handle.release()
    handle.release()
    assert slot.active_requests == 0

    await pool.close_slot(slot.slot_id)
    assert pool.slots() == ()


@pytest.mark.asyncio
async def test_activate_with_inflight_request_raises_busy() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id, account_id="a")
    handle = await pool.begin_request(slot.slot_id)

    with pytest.raises(InstanceBusyError):
        await _activate(pool, slot.slot_id, account_id="b")

    handle.release()


@pytest.mark.asyncio
async def test_retire_runtime_waits_once_then_force_closes() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)

    handle = await pool.begin_request(slot.slot_id)

    async def _release_soon() -> None:
        await asyncio.sleep(0.02)
        handle.release()

    task = asyncio.create_task(_release_soon())
    timed_out = await pool.retire_runtime(slot.slot_id, timeout_s=1.0)
    await task

    assert timed_out is False
    assert slot.state is SlotState.UNSTARTED
    assert slot.context_id == ""
    assert cdp.disposed == ["ctx-1"]

    await _activate(pool, slot.slot_id)
    assert slot.generation == 2

    blocked = await pool.begin_request(slot.slot_id)
    timed_out = await pool.retire_runtime(slot.slot_id, timeout_s=0.01)
    blocked.release()

    assert timed_out is True
    assert slot.state is SlotState.UNSTARTED
    assert cdp.disposed == ["ctx-1", "ctx-2"]


@pytest.mark.asyncio
async def test_sleep_releases_context_and_wake_creates_new_generation() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)
    first_realm = slot.realm_id

    await pool.sleep(slot.slot_id)
    assert slot.state is SlotState.SLEEPING
    assert slot.context_id == ""
    assert slot in pool.slots()

    await _activate(pool, slot.slot_id)
    assert slot.state is SlotState.READY
    assert slot.generation == 2
    assert slot.realm_id != first_realm
    assert pool.live_contexts == 1


@pytest.mark.asyncio
async def test_activate_same_account_ready_without_force_reuses_runtime() -> None:
    cdp = FakeCDP()
    retired: list[str] = []
    pool = _pool(cdp, on_slot_retired=retired.append, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)
    first_context = slot.context_id
    first_realm = slot.realm_id
    first_session = slot.session

    await _activate(pool, slot.slot_id)

    assert slot.state is SlotState.READY
    assert slot.generation == 1
    assert slot.context_id == first_context
    assert slot.realm_id == first_realm
    assert slot.session is first_session
    assert cdp.created == ["ctx-1"]
    assert cdp.disposed == []
    assert retired == []


@pytest.mark.asyncio
async def test_activate_same_account_ready_with_force_rebuilds_runtime() -> None:
    cdp = FakeCDP()
    retired: list[str] = []
    pool = _pool(cdp, on_slot_retired=retired.append, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)
    first_context = slot.context_id
    first_realm = slot.realm_id
    first_session = slot.session
    assert first_session is not None

    await _activate(pool, slot.slot_id, force=True)

    assert slot.state is SlotState.READY
    assert slot.generation == 2
    assert slot.realm_id == f"{slot.slot_id}-g2"
    assert slot.realm_id != first_realm
    assert slot.context_id == "ctx-2"
    assert slot.context_id != first_context
    assert slot.session is not first_session
    assert "page.close" in cdp.events
    assert cdp.created == ["ctx-1", "ctx-2"]
    assert cdp.disposed == ["ctx-1"]
    assert cdp.events.index("page.close") < cdp.events.index("context.dispose:ctx-1")
    assert retired == [first_realm]
    assert pool.live_contexts == 1


@pytest.mark.asyncio
async def test_bootstrap_retries_then_marks_invalid() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1, fail_contexts={"ctx-1", "ctx-2"})
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)

    assert slot.state is SlotState.READY
    assert len(cdp.created) == 3
    assert cdp.disposed == ["ctx-1", "ctx-2"]

    await pool.close_slot(slot.slot_id)

    always_fail = _pool(
        cdp,
        max_contexts=1,
        fail_contexts={"ctx-4", "ctx-5", "ctx-6"},
    )
    failed = await always_fail.add_slot(always_fail.specs[0], lane="free")
    with pytest.raises(RuntimeError):
        await _activate(always_fail, failed.slot_id)
    assert failed.state is SlotState.INVALID
    assert failed.context_id == ""


def test_legacy_lease_and_pinned_surface_is_gone() -> None:
    import aistudio_api.infrastructure.gateway.instance_pool as pool_module

    assert not hasattr(pool_module, "InstanceLease")
    assert "COOLDOWN" not in SlotState.__members__
    for legacy in ("acquire", "release", "rebind", "retire_idle", "borrow"):
        assert not hasattr(InstancePool, legacy)

    slot = pool_module.InstanceSlot(
        slot_id="slot-x", surface="chat", min_tier="Free", lane="free"
    )
    assert not hasattr(slot, "reservations")
    assert not hasattr(slot, "pinned")
    assert not hasattr(slot, "pending_pages")


@pytest.mark.asyncio
async def test_sync_account_tier_cross_lane_unbinds_free_slot_and_keeps_same_lane() -> (
    None
):
    cdp = FakeCDP()
    retired: list[str] = []
    pool = _pool(cdp, on_slot_retired=retired.append)
    free = await pool.add_slot(pool.specs[0], lane="free")
    paid = await pool.add_slot(pool.specs[1], lane="paid")
    await _activate(pool, free.slot_id, account_id="restored", tier="Free")
    await _activate(pool, paid.slot_id, account_id="paid-acc", tier="Pro", lane="paid")
    free_context = free.context_id
    paid_context = paid.context_id

    updated = await pool.sync_account_tier("restored", "Pro", drain_timeout_s=0.1)

    assert updated == 1
    assert free.state is SlotState.UNSTARTED
    assert free.account_id == ""
    assert free.session is None
    assert free.context_id == ""
    assert free_context in cdp.disposed
    assert retired == [f"{free.slot_id}-g1"]
    assert paid.state is SlotState.READY
    assert paid.account_id == "paid-acc"
    assert paid.context_id == paid_context

    updated = await pool.sync_account_tier("paid-acc", "Ultra")
    assert updated == 1
    assert paid.tier == "Ultra"
    assert paid.session is not None
    assert paid.session.active_context.tier == "Ultra"
    assert paid.context_id == paid_context
    assert paid.account_id == "paid-acc"


@pytest.mark.asyncio
async def test_snapshot_has_no_lease_or_page_budget_fields() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, slot.slot_id)

    item = pool.snapshot()[0]
    assert set(item) == {
        "slot_id",
        "realm_id",
        "account_id",
        "surface",
        "min_tier",
        "lane",
        "state",
        "generation",
        "context_id",
        "pages",
        "active_requests",
        "last_error",
    }
    assert item["active_requests"] == 0


@pytest.mark.asyncio
async def test_pause_requests_rejects_begin_with_503_retry_after() -> None:
    from aistudio_api.domain.errors import EngineSwitchingError

    cdp = FakeCDP()
    pool = _pool(cdp)
    free = await pool.add_slot(pool.specs[0], lane="free")
    await _activate(pool, free.slot_id, account_id="free-acc", tier="Free")

    pool.pause_requests()
    with pytest.raises(EngineSwitchingError) as excinfo:
        await pool.begin_request(free.slot_id)
    assert excinfo.value.status_code == 503
    assert excinfo.value.retry_after == 1
    assert pool.total_active_requests() == 0

    pool.resume_requests()
    handle = await pool.begin_request(free.slot_id)
    assert handle.generation == free.generation
    assert handle.epoch == pool.engine_epoch
    handle.release()
    assert pool.total_active_requests() == 0
