"""Behavior contracts for the WP5 v3 unified request instance pool."""

from __future__ import annotations

from typing import cast

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
async def test_channel_page_budget_is_enforced() -> None:
    cdp = FakeCDP()
    pool = _pool(
        cdp,
        features={
            "routing": {"max_contexts": 2, "global_max_pages": 4},
            "channels": {
                "chat": {"enabled": True, "max_pages": 1},
                "build": {"enabled": True, "max_pages": 1},
            },
        },
    )
    first = await pool.acquire(account_id="a", auth_file="a.json", group_name="g")
    with pytest.raises(InstanceCapacityError):
        await pool.acquire(account_id="b", auth_file="b.json", group_name="g")
    await pool.release(first)
    await pool.close()


@pytest.mark.asyncio
async def test_global_page_budget_is_enforced() -> None:
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
    first = await pool.acquire(account_id="a", auth_file="a.json", group_name="g")
    with pytest.raises(InstanceCapacityError):
        await pool.acquire(account_id="b", auth_file="b.json", group_name="g")
    await pool.release(first)
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
