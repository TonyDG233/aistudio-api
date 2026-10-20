"""Behavior contracts for the WP5 v4 user-facing instance manager."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aistudio_api.application.instance_manager import (
    InstanceAlreadyExistsError,
    InstanceBusyError,
    InstanceCapacityExhaustedError,
    InstanceInvalidAccountError,
    InstanceManager,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.instance_pool import SlotState


@dataclass
class FakeSlot:
    slot_id: str
    current_account_id: str
    state: SlotState = SlotState.READY
    in_flight: int = 0
    has_chat: bool = True
    has_build: bool = False
    pinned: bool = False
    last_used: float = 0.0

    def chat_ready(self) -> bool:
        return self.has_chat

    def build_ready(self) -> bool:
        return self.has_build


class FakePool:
    def __init__(self, max_contexts: int = 2) -> None:
        self.max_contexts = max_contexts
        self._slots: dict[str, FakeSlot] = {}
        self._counter = 0
        self.created: list[str] = []
        self.closed: list[str] = []

    def slots(self) -> tuple[FakeSlot, ...]:
        return tuple(self._slots.values())

    def get(self, slot_id: str) -> FakeSlot | None:
        return self._slots.get(slot_id)

    async def create_instance(self, **kwargs) -> FakeSlot:
        account_id = str(kwargs["account_id"])
        if len(self._slots) >= self.max_contexts:
            raise RuntimeError("capacity")
        self._counter += 1
        slot = FakeSlot(
            slot_id=f"slot-{self._counter}",
            current_account_id=account_id,
            pinned=True,
        )
        self._slots[slot.slot_id] = slot
        self.created.append(slot.slot_id)
        return slot

    async def close_instance(self, slot_id: str) -> None:
        slot = self._slots.get(slot_id)
        if slot is None:
            return
        if slot.in_flight > 0:
            raise RuntimeError("busy")
        self._slots.pop(slot_id, None)
        self.closed.append(slot_id)

    async def reassign_instance(self, slot_id: str, **kwargs) -> FakeSlot:
        slot = self._slots[slot_id]
        slot.current_account_id = str(kwargs["account_id"])
        return slot


def _store(*accounts: AccountMeta) -> object:
    mapping = {account.id: account for account in accounts}

    class Store:
        def get_account(self, account_id: str) -> AccountMeta | None:
            return mapping.get(account_id)

        def get_auth_path_optional(self, account_id: str) -> str | None:
            return f"{account_id}.json" if account_id in mapping else None

    return Store()


def _account(account_id: str, *, expired: bool = False) -> AccountMeta:
    return AccountMeta(
        id=account_id,
        name=account_id,
        email=f"{account_id}@gmail.com",
        created_at="2026-10-20T00:00:00Z",
        expired=expired,
    )


def _manager(
    pool: FakePool | None = None, *, accounts: tuple[AccountMeta, ...] | None = None
) -> tuple[InstanceManager, FakePool]:
    fake_pool = pool or FakePool()
    manager = InstanceManager(
        fake_pool,  # type: ignore[arg-type]
        _store(*(accounts or (_account("a"), _account("b"), _account("c")))),  # type: ignore[arg-type]
    )
    return manager, fake_pool


async def _settle(manager: InstanceManager) -> None:
    await manager.wait_idle()


@pytest.mark.asyncio
async def test_create_two_instances_and_latest() -> None:
    manager, pool = _manager()
    first = await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)

    listed = await manager.list_instances()
    assert listed.max_instances == 2
    assert [item.number for item in listed.instances] == [1, 2]
    assert [item.account_id for item in listed.instances] == ["a", "b"]
    assert listed.latest_instance_id == second.id
    assert first.id != second.id
    assert all(item.state.value == "active" for item in listed.instances)
    assert listed.instances[0].channels == ("pg",)
    assert all(slot.pinned for slot in pool.slots())


@pytest.mark.asyncio
async def test_capacity_and_duplicate_account_are_explicit() -> None:
    manager, _ = _manager(FakePool(max_contexts=1))
    await manager.create("a")
    await _settle(manager)

    with pytest.raises(InstanceAlreadyExistsError):
        await manager.create("a")
    with pytest.raises(InstanceCapacityExhaustedError):
        await manager.create("b")


@pytest.mark.asyncio
async def test_expired_account_is_rejected() -> None:
    manager, _ = _manager(accounts=(_account("a", expired=True),))
    with pytest.raises(InstanceInvalidAccountError):
        await manager.create("a")


@pytest.mark.asyncio
async def test_close_refuses_in_flight_then_reclaims() -> None:
    manager, pool = _manager()
    item = await manager.create("a")
    await _settle(manager)
    slot = pool.slots()[0]
    slot.in_flight = 1

    with pytest.raises(InstanceBusyError):
        await manager.close(item.id)

    slot.in_flight = 0
    closing = await manager.close(item.id)
    assert closing.state.value == "reclaiming"
    await _settle(manager)
    assert (await manager.list_instances()).instances == ()
    assert pool.closed == [slot.slot_id]


@pytest.mark.asyncio
async def test_reassign_preserves_number_and_does_not_change_latest() -> None:
    manager, _pool = _manager()
    first = await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)

    updated = await manager.reassign(first.id, "c")
    await _settle(manager)
    listed = await manager.list_instances()

    assert updated.id == first.id
    assert updated.number == first.number
    changed = next(item for item in listed.instances if item.id == first.id)
    assert changed.account_id == "c"
    assert changed.number == first.number
    assert listed.latest_instance_id == second.id


@pytest.mark.asyncio
async def test_latest_falls_back_when_latest_closes() -> None:
    manager, _ = _manager()
    first = await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)

    await manager.close(second.id)
    await _settle(manager)
    listed = await manager.list_instances()
    assert listed.latest_instance_id == first.id
    assert [item.latest for item in listed.instances] == [True]
