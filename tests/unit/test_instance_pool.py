"""Behavior contracts for the WP5 v3 unified request instance pool."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from aistudio_api.infrastructure.gateway.instance_pool import (
    InstanceCapacityError,
    InstancePool,
    InstanceSlot,
    SessionFactory,
    SlotState,
)


class FakePage:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class FakeSessionContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.auth_user = "0"
        self.in_flight = 0


class FakeSession:
    def __init__(self) -> None:
        self.active_context = FakeSessionContext()
        self.in_flight_count = 0
        self.closed = False

    @property
    def page(self) -> FakePage:
        return self.active_context.page

    async def close(self) -> None:
        self.closed = True
        self.active_context.page.closed = True


class FakeBuildSurface:
    def __init__(self, *, context_id: str, realm_id: str) -> None:
        self.context_id = context_id
        self.realm_id = realm_id
        self.state = type("State", (), {"page": FakePage()})()

    async def ensure_ready(self, *_args, **_kwargs) -> None:
        return None


class FakeCDP:
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


def _features(max_contexts: int = 2) -> dict[str, object]:
    return {
        "routing": {"max_contexts": max_contexts, "global_max_pages": 4},
        "channels": {
            "chat": {"enabled": True, "max_pages": 2},
            "build": {"enabled": True, "max_pages": 2},
            "official": {"enabled": False, "max_pages": 0},
        },
    }


def _pool(
    cdp: FakeCDP,
    *,
    max_contexts: int = 2,
    features: dict[str, object] | None = None,
    on_slot_retired=None,
) -> InstancePool:
    async def factory(_cdp, **kwargs):
        session = FakeSession()
        session.active_context.auth_user = str(kwargs.get("auth_user") or "0")
        return session

    configured = features or _features(max_contexts)
    return InstancePool(
        lambda: _ready(cdp),
        features_provider=lambda: configured,
        session_factory=cast("SessionFactory", factory),
        on_slot_retired=on_slot_retired,
        wait_timeout_s=0.0,
    )


async def _ready(value):
    return value


@pytest.mark.asyncio
async def test_pool_reuses_same_account_and_caps_contexts() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)

    first = await pool.acquire(account_id="a", auth_file="a.json", auth_user="1")
    second = await pool.acquire(account_id="b", auth_file="b.json", auth_user="2")
    assert pool.live_contexts == 2
    assert len(cdp.created) == 2

    again = await pool.acquire(account_id="a", auth_file="a.json", auth_user="1")
    assert again.slot_id == first.slot_id
    assert again.realm_id == first.realm_id
    assert pool.live_contexts == 2
    assert len(cdp.created) == 2

    await pool.release(first)
    await pool.release(second)
    await pool.release(again)
    await pool.close()


@pytest.mark.asyncio
async def test_idle_slot_is_rebound_to_third_account() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    first = await pool.acquire(account_id="a", auth_file="a.json")
    second = await pool.acquire(account_id="b", auth_file="b.json")
    await pool.release(first)

    third = await pool.acquire(account_id="c", auth_file="c.json")
    assert third.slot_id == first.slot_id
    assert third.adapter.current_account_id == "c"
    assert third.adapter.generation == 2
    assert pool.live_contexts == 2
    assert len(cdp.created) == 3
    assert len(cdp.disposed) == 1

    await pool.release(second)
    await pool.release(third)
    await pool.close()


@pytest.mark.asyncio
async def test_fifty_account_rotation_keeps_context_count_stable() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    for index in range(50):
        lease = await pool.acquire(
            account_id=f"account-{index}",
            auth_file=f"account-{index}.json",
            timeout_s=0.0,
        )
        assert pool.live_contexts <= 2
        await pool.release(lease)
    assert pool.live_contexts == 2
    assert len(cdp.created) == 50
    await pool.close()


@pytest.mark.asyncio
async def test_busy_pool_waits_and_raises_capacity_error() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    first = await pool.acquire(account_id="a", auth_file="a.json")
    second = await pool.acquire(account_id="b", auth_file="b.json")

    with pytest.raises(InstanceCapacityError):
        await pool.acquire(account_id="c", auth_file="c.json", timeout_s=0.0)

    await pool.release(first)
    third = await pool.acquire(account_id="c", auth_file="c.json", timeout_s=0.0)
    assert third.slot_id == first.slot_id

    await pool.release(second)
    await pool.release(third)
    await pool.close()


@pytest.mark.asyncio
async def test_build_surface_reuses_same_slot_context(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)

    async def fake_ensure(self: InstanceSlot, cdp_client):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)

    chat = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    build = await pool.acquire(account_id="a", auth_file="a.json", channel="build")
    assert build.slot_id == chat.slot_id
    assert build.context_id == chat.context_id
    assert build.adapter.build_ready() is True
    assert pool.page_counts() == {"chat": 1, "build": 1}

    await pool.release(chat)
    await pool.release(build)
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_build_creation_counts_one_page(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_ensure(self: InstanceSlot, cdp_client):
        if self.build_surface is None:
            started.set()
            await release.wait()
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)

    chat = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    first_task = asyncio.create_task(
        pool.acquire(account_id="a", auth_file="a.json", channel="build")
    )
    await started.wait()
    second_task = asyncio.create_task(
        pool.acquire(account_id="a", auth_file="a.json", channel="build")
    )
    await asyncio.sleep(0)
    assert pool.page_counts() == {"chat": 1, "build": 1}

    release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first.slot_id == second.slot_id == chat.slot_id
    assert pool.page_counts() == {"chat": 1, "build": 1}

    await pool.release(chat)
    await pool.release(first)
    await pool.release(second)
    await pool.close()


@pytest.mark.asyncio
async def test_hidden_bootstrap_chat_page_obeys_chat_budget(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 2, "global_max_pages": 2},
            "channels": {
                "chat": {"enabled": True, "max_pages": 1},
                "build": {"enabled": True, "max_pages": 1},
            },
        },
    )

    async def fake_ensure(self: InstanceSlot, cdp_client):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)

    chat = await pool.acquire(account_id="b", auth_file="b.json", channel="chat")
    await pool.release(chat)
    build = await pool.acquire(account_id="a", auth_file="a.json", channel="build")
    await pool.release(build)

    rebound = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    assert rebound.slot_id == chat.slot_id
    assert rebound.slot_id != build.slot_id
    assert pool.page_counts() == {"chat": 1, "build": 1}

    await pool.release(rebound)
    await pool.close()


@pytest.mark.asyncio
async def test_new_slot_is_closed_when_build_surface_creation_fails(
    monkeypatch,
) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)

    async def fail_ensure(self: InstanceSlot, cdp_client):
        raise RuntimeError("build surface failed")

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fail_ensure)

    with pytest.raises(RuntimeError, match="build surface failed"):
        await pool.acquire(account_id="a", auth_file="a.json", channel="build")

    assert pool.slots() == ()
    assert pool.live_contexts == 0
    assert cdp.disposed == ["ctx-1"]
    await pool.close()


@pytest.mark.asyncio
async def test_concurrent_chat_waits_for_loading_slot() -> None:
    cdp = FakeCDP()
    started = asyncio.Event()
    release = asyncio.Event()

    async def factory(_cdp, **_kwargs):
        started.set()
        await release.wait()
        return FakeSession()

    pool = InstancePool(
        lambda: _ready(cdp),
        features_provider=lambda: _features(1),
        session_factory=cast("SessionFactory", factory),
        wait_timeout_s=5.0,
    )
    first_task = asyncio.create_task(
        pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    )
    await started.wait()
    second_task = asyncio.create_task(
        pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    )
    await asyncio.sleep(0.05)
    assert not second_task.done()

    release.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert first.slot_id == second.slot_id
    assert pool.live_contexts == 1
    assert pool.page_counts() == {"chat": 1, "build": 0}

    await pool.release(first)
    await pool.release(second)
    await pool.close()


@pytest.mark.asyncio
async def test_cancelled_slot_load_disposes_context_and_removes_slot() -> None:
    cdp = FakeCDP()
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def factory(_cdp, **_kwargs):
        started.set()
        await blocker.wait()
        return FakeSession()

    pool = InstancePool(
        lambda: _ready(cdp),
        features_provider=lambda: _features(1),
        session_factory=cast("SessionFactory", factory),
        wait_timeout_s=0.0,
    )
    task = asyncio.create_task(pool.acquire(account_id="a", auth_file="a.json"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pool.slots() == ()
    assert pool.live_contexts == 0
    assert cdp.disposed == ["ctx-1"]
    await pool.close()


@pytest.mark.asyncio
async def test_ensure_channel_times_out_without_leaking_page(
    monkeypatch,
) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)

    async def slow_ensure(self: InstanceSlot, cdp_client):
        await asyncio.sleep(5)
        return FakeBuildSurface(context_id=self.context_id, realm_id=self.realm_id)

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", slow_ensure)
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.instance_pool.BUILD_CHANNEL_READY_TIMEOUT_S",
        0.01,
    )
    chat = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    await pool.release(chat)

    with pytest.raises(InstanceCapacityError, match="渠道启动超时"):
        await pool.ensure_channel(chat.slot_id, "build")

    assert pool.page_counts() == {"chat": 1, "build": 0}
    await pool.close()


@pytest.mark.asyncio
async def test_rebind_to_build_reserves_page_while_surface_loads(
    monkeypatch,
) -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 2, "global_max_pages": 2},
            "channels": {
                "chat": {"enabled": True, "max_pages": 1, "max_instances": 2},
                "build": {"enabled": True, "max_pages": 1, "max_instances": 2},
            },
        },
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_ensure(self: InstanceSlot, cdp_client):
        if self.build_surface is None:
            started.set()
            await release.wait()
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)
    first = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    second = await pool.acquire(account_id="b", auth_file="b.json", channel="chat")
    await pool.release(first)
    await pool.release(second)

    task = asyncio.create_task(
        pool.rebind(
            first.slot_id,
            account_id="a",
            auth_file="a.json",
            auth_user="0",
            channel="build",
            group_name="g",
            group_max_pages=1,
        )
    )
    await started.wait()
    with pytest.raises(InstanceCapacityError):
        await pool.acquire(
            account_id="b",
            auth_file="b.json",
            channel="build",
            group_name="g",
            group_max_pages=1,
            timeout_s=0.0,
        )

    release.set()
    await task
    assert pool.page_counts() == {"chat": 1, "build": 1}
    await pool.close()


@pytest.mark.asyncio
async def test_rebind_to_build_creates_build_surface(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)

    async def fake_ensure(self: InstanceSlot, cdp_client):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)

    lease = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    await pool.release(lease)
    await pool.rebind(
        lease.slot_id,
        account_id="b",
        auth_file="b.json",
        auth_user="0",
        channel="build",
        group_name="free",
        group_max_pages=2,
    )
    slot = pool.get(lease.slot_id)
    assert slot is not None
    assert slot.state is SlotState.READY
    assert slot.channel_role == "build"
    assert slot.build_ready() is True
    assert pool.page_counts() == {"chat": 0, "build": 1}
    await pool.close()


@pytest.mark.asyncio
async def test_idle_closed_page_releases_page_budget() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    lease = await pool.acquire(account_id="a", auth_file="a.json", channel="chat")
    slot = pool.get(lease.slot_id)
    assert slot is not None
    await pool.release(lease)
    assert slot.session is not None
    cast("Any", slot.session).active_context.page.closed = True
    assert pool.page_counts() == {"chat": 0, "build": 0}
    await pool.close()


def test_pending_build_page_is_counted_once() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    slot = InstanceSlot(
        slot_id="slot-test",
        build_counted=True,
        pending_pages=1,
        channel_role="build",
    )
    assert pool._slot_page_counts(slot) == {"chat": 0, "build": 1}


@pytest.mark.asyncio
async def test_borrow_uses_idle_other_account_slot() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    first = await pool.acquire(account_id="a", auth_file="a.json")
    await pool.release(first)

    borrowed = await pool.acquire(
        account_id="b",
        auth_file="b.json",
        borrow_accounts={"a"},
        timeout_s=0.0,
    )
    assert borrowed.is_borrow is True
    assert borrowed.slot_id == first.slot_id
    assert borrowed.account_id == "a"

    await pool.release(borrowed)
    await pool.close()


@pytest.mark.asyncio
async def test_retire_idle_closes_context_and_reports_realm() -> None:
    cdp = FakeCDP()
    retired: list[str] = []
    pool = _pool(cdp, on_slot_retired=retired.append)
    lease = await pool.acquire(account_id="a", auth_file="a.json")
    await pool.release(lease)

    assert await pool.retire_idle() == 1
    assert pool.live_contexts == 0
    assert retired == [lease.realm_id]
    assert cdp.disposed == [lease.context_id]
    await pool.close()


@pytest.mark.asyncio
async def test_snapshot_does_not_expose_credentials() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    lease = await pool.acquire(account_id="a", auth_file="secret.json")
    snapshot = pool.snapshot()[0]
    assert "auth_file" not in snapshot
    assert "secret" not in str(snapshot)
    assert snapshot["state"] == SlotState.BUSY.value
    await pool.release(lease)
    await pool.close()


@pytest.mark.asyncio
async def test_channel_page_budget_scales_with_instance_capacity() -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 2, "global_max_pages": 4},
            "channels": {
                "chat": {"enabled": True, "max_pages": 1, "max_instances": 2},
                "build": {"enabled": True, "max_pages": 1, "max_instances": 2},
            },
        },
    )
    first = await pool.acquire(account_id="a", auth_file="a.json", group_name="g")
    second = await pool.acquire(account_id="b", auth_file="b.json", group_name="g")
    assert first.slot_id != second.slot_id
    assert pool.live_contexts == 2
    channel_budget = cast("dict[str, int]", pool.budget_snapshot()["channel_max_pages"])
    assert channel_budget["chat"] == 2
    await pool.release(first)
    await pool.release(second)
    await pool.close()


@pytest.mark.asyncio
async def test_channel_instance_cap_blocks_second_page_for_429_acquire() -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 3, "global_max_pages": 3},
            "channels": {
                "chat": {"enabled": True, "max_pages": 1, "max_instances": 1},
                "build": {"enabled": True, "max_pages": 1, "max_instances": 1},
            },
        },
    )
    first = await pool.acquire(account_id="a", auth_file="a.json", group_name="g")
    with pytest.raises(InstanceCapacityError):
        await pool.acquire(account_id="b", auth_file="b.json", group_name="g")
    assert pool.page_counts().get("chat") == 1
    await pool.release(first)
    await pool.close()


@pytest.mark.asyncio
async def test_full_page_budget_rebinds_idle_slot_instead_of_failing() -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 1, "global_max_pages": 1},
            "channels": {
                "chat": {"enabled": True, "max_pages": 1},
                "build": {"enabled": True, "max_pages": 1},
            },
        },
    )
    first = await pool.acquire(account_id="a", auth_file="a.json", group_name="g")
    await pool.release(first)

    second = await pool.acquire(account_id="b", auth_file="b.json", group_name="g")
    assert second.slot_id == first.slot_id
    assert second.account_id == "b"
    assert pool.live_contexts == 1
    await pool.release(second)
    await pool.close()


@pytest.mark.asyncio
async def test_global_page_budget_is_at_least_instance_capacity() -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 2, "global_max_pages": 1},
            "channels": {
                "chat": {"enabled": True, "max_pages": 2},
                "build": {"enabled": True, "max_pages": 2},
            },
        },
    )
    assert pool.budget_snapshot()["global_max_pages"] == 2
    await pool.close()


@pytest.mark.asyncio
async def test_group_page_budget_is_enforced() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    first = await pool.acquire(
        account_id="a",
        auth_file="a.json",
        group_name="pro",
        group_max_pages=1,
    )
    with pytest.raises(InstanceCapacityError):
        await pool.acquire(
            account_id="b",
            auth_file="b.json",
            group_name="pro",
            group_max_pages=1,
        )
    other = await pool.acquire(
        account_id="c",
        auth_file="c.json",
        group_name="free",
        group_max_pages=1,
    )
    await pool.release(first)
    await pool.release(other)
    await pool.close()


@pytest.mark.asyncio
async def test_rebind_idle_slot_changes_account_without_new_context() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    lease = await pool.acquire(account_id="a", auth_file="a.json", group_name="pro")
    await pool.release(lease)

    await pool.rebind(
        lease.slot_id,
        account_id="b",
        auth_file="b.json",
        auth_user="0",
        channel="chat",
        group_name="free",
    )
    slot = pool.get(lease.slot_id)
    assert slot is not None
    assert slot.current_account_id == "b"
    assert slot.group_name == "free"
    assert slot.generation == 2
    assert len(cdp.disposed) == 1
    await pool.close()


@pytest.mark.asyncio
async def test_rebind_busy_slot_is_rejected() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp)
    lease = await pool.acquire(account_id="a", auth_file="a.json")
    with pytest.raises(InstanceCapacityError):
        await pool.rebind(
            lease.slot_id,
            account_id="b",
            auth_file="b.json",
            auth_user="0",
            channel="chat",
        )
    await pool.release(lease)
    await pool.close()


@pytest.mark.asyncio
async def test_explicit_instance_is_pinned_and_not_rebound() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.create_instance(account_id="a", auth_file="a.json", auth_user="1")
    assert slot.pinned is True
    assert pool.live_contexts == 1
    assert slot.state is SlotState.READY

    with pytest.raises(InstanceCapacityError):
        await pool.acquire(account_id="b", auth_file="b.json", timeout_s=0.0)

    await pool.close_instance(slot.slot_id)
    assert pool.live_contexts == 0
    await pool.close()


@pytest.mark.asyncio
async def test_close_explicit_instance_refuses_in_flight() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.create_instance(account_id="a", auth_file="a.json")
    lease = await pool.acquire(account_id="a", auth_file="a.json")

    with pytest.raises(InstanceCapacityError):
        await pool.close_instance(slot.slot_id)

    await pool.release(lease)
    await pool.close_instance(slot.slot_id)
    assert pool.slots() == ()
    await pool.close()


@pytest.mark.asyncio
async def test_reassign_explicit_instance_keeps_identity_and_pin() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    slot = await pool.create_instance(account_id="a", auth_file="a.json")
    await pool.reassign_instance(
        slot.slot_id,
        account_id="c",
        auth_file="c.json",
        auth_user="0",
        channel="chat",
    )
    rebound = pool.get(slot.slot_id)
    assert rebound is not None
    assert rebound.current_account_id == "c"
    assert rebound.pinned is True
    assert rebound.state is SlotState.READY
    await pool.close()


@pytest.mark.asyncio
async def test_explicit_instance_same_channel_reuses_slot() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=2)
    first = await pool.create_instance(account_id="a", auth_file="a.json")
    again = await pool.create_instance(account_id="a", auth_file="a.json")
    assert again.slot_id == first.slot_id
    assert len(pool.slots()) == 1
    assert len(cdp.created) == 1
    await pool.close()


@pytest.mark.asyncio
async def test_explicit_instance_second_channel_reuses_context(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=2)

    async def fake_ensure(self, _cdp):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)
    chat = await pool.create_instance(account_id="a", auth_file="a.json")
    build = await pool.create_instance(
        account_id="a", auth_file="a.json", channel="build"
    )
    assert build.slot_id == chat.slot_id
    assert build.context_id == chat.context_id
    assert len(pool.slots()) == 1
    assert len(cdp.created) == 1
    assert pool.page_counts() == {"chat": 1, "build": 1}
    await pool.close()


@pytest.mark.asyncio
async def test_close_channel_only_closes_one_page(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=2)

    async def fake_ensure(self, _cdp):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)
    chat = await pool.create_instance(account_id="a", auth_file="a.json")
    await pool.create_instance(account_id="a", auth_file="a.json", channel="build")
    await pool.close_channel(chat.slot_id, "build")
    assert chat.chat_ready() is True
    assert chat.build_ready() is False
    assert pool.page_counts() == {"chat": 1, "build": 0}
    await pool.ensure_channel(chat.slot_id, "build")
    assert chat.build_ready() is True
    assert pool.page_counts() == {"chat": 1, "build": 1}
    await pool.close()


@pytest.mark.asyncio
async def test_build_only_slot_closes_bootstrap_chat_page(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=2)

    async def fake_ensure(self, _cdp):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)
    slot = await pool.create_instance(
        account_id="a", auth_file="a.json", channel="build"
    )
    assert slot.session is None
    assert slot.chat_ready() is False
    assert slot.build_ready() is True
    assert pool.page_counts() == {"chat": 0, "build": 1}
    await pool.close()


@pytest.mark.asyncio
async def test_ensure_channel_reopens_chat_in_existing_context(monkeypatch) -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=2)

    async def fake_ensure(self, _cdp):
        if self.build_surface is None:
            self.build_surface = FakeBuildSurface(
                context_id=self.context_id, realm_id=self.realm_id
            )
        return self.build_surface

    monkeypatch.setattr(InstanceSlot, "ensure_build_surface", fake_ensure)
    slot = await pool.create_instance(
        account_id="a", auth_file="a.json", channel="build"
    )
    await pool.ensure_channel(slot.slot_id, "chat")
    assert slot.session is not None
    assert slot.chat_ready() is True
    assert slot.build_ready() is True
    assert len(cdp.created) == 1
    assert pool.page_counts() == {"chat": 1, "build": 1}
    await pool.close()


@pytest.mark.asyncio
async def test_single_instance_cannot_open_second_page_in_parallel() -> None:
    features = {
        "routing": {"max_contexts": 1, "global_max_pages": 2},
        "channels": {
            "chat": {"enabled": True, "max_pages": 2},
            "build": {"enabled": True, "max_pages": 2},
        },
    }
    pool = _pool(FakeCDP(), max_contexts=1, features=features)

    lease = await pool.acquire(account_id="a", auth_file="a.json")
    assert pool.budget_snapshot()["global_max_pages"] == 1
    assert pool.page_counts()["chat"] == 1
    assert sum(pool.page_counts().values()) == 1

    with pytest.raises(InstanceCapacityError):
        await pool.acquire(
            account_id="a",
            auth_file="a.json",
            channel="build",
            surface="build",
        )

    await pool.release(lease)
    await pool.close()


@pytest.mark.asyncio
async def test_single_instance_ensure_channel_rejects_second_page() -> None:
    pool = _pool(FakeCDP(), max_contexts=1)
    lease = await pool.acquire(account_id="a", auth_file="a.json")

    with pytest.raises(InstanceCapacityError):
        await pool.ensure_channel(lease.slot_id, "build")

    assert pool.page_counts() == {"chat": 1, "build": 0}
    assert len(pool.slots()) == 1
    await pool.release(lease)
    await pool.close()


@pytest.mark.asyncio
async def test_single_instance_check_create_budget_rejects_second_channel() -> None:
    pool = _pool(FakeCDP(), max_contexts=1)
    lease = await pool.acquire(account_id="a", auth_file="a.json")

    with pytest.raises(InstanceCapacityError):
        await pool.check_create_budget("build", account_id="a")

    assert pool.page_counts() == {"chat": 1, "build": 0}
    await pool.release(lease)
    await pool.close()


@pytest.mark.asyncio
async def test_single_instance_borrow_does_not_add_second_page() -> None:
    pool = _pool(FakeCDP(), max_contexts=1)
    lease = await pool.acquire(account_id="a", auth_file="a.json")

    with pytest.raises(InstanceCapacityError):
        await pool.acquire(
            account_id="b",
            auth_file="b.json",
            channel="build",
            surface="build",
            borrow_accounts={"a"},
        )

    assert pool.page_counts() == {"chat": 1, "build": 0}
    assert len(pool.slots()) == 1
    await pool.release(lease)
    await pool.close()


@pytest.mark.asyncio
async def test_single_instance_rebind_replaces_page_sequentially() -> None:
    cdp = FakeCDP()
    pool = _pool(cdp, max_contexts=1)
    lease = await pool.acquire(account_id="a", auth_file="a.json")
    slot_id = lease.slot_id
    await pool.release(lease)

    await pool.rebind(
        slot_id,
        account_id="b",
        auth_file="b.json",
        auth_user="2",
        channel="chat",
    )

    assert pool.page_counts() == {"chat": 1, "build": 0}
    assert len(pool.slots()) == 1
    assert cdp.disposed == ["ctx-1"]
    await pool.close()
