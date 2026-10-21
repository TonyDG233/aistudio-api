"""Behavior contracts for the WP5 v4 user-facing instance manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from aistudio_api.application.instance_manager import (
    InstanceAlreadyExistsError,
    InstanceBusyError,
    InstanceCapacityExhaustedError,
    InstanceInvalidAccountError,
    InstanceManager,
    InstanceNotReadyError,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstanceCapacityError,
    SlotState,
)


@dataclass
class FakeSlot:
    slot_id: str
    current_account_id: str
    state: SlotState = SlotState.READY
    in_flight: int = 0
    has_chat: bool = True
    has_build: bool = False
    chat_counted: bool = True
    build_counted: bool = False
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
        self.fail_accounts: set[str] = set()
        self.build_budget_full = False

    def slots(self) -> tuple[FakeSlot, ...]:
        return tuple(self._slots.values())

    def get(self, slot_id: str) -> FakeSlot | None:
        return self._slots.get(slot_id)

    async def check_create_budget(
        self,
        channel: str,
        *,
        account_id: str | None = None,
        group_name: str | None = None,
        group_max_pages: int | None = None,
    ) -> None:
        existing = (
            next(
                (
                    slot
                    for slot in self._slots.values()
                    if slot.current_account_id == account_id
                ),
                None,
            )
            if account_id
            else None
        )
        if existing is None and len(self._slots) >= self.max_contexts:
            raise InstanceCapacityError("capacity")
        if channel == "build" and self.build_budget_full:
            raise InstanceCapacityError("build page budget exhausted")

    async def create_instance(self, **kwargs) -> FakeSlot:
        account_id = str(kwargs["account_id"])
        channel = str(kwargs.get("channel") or "chat")
        if account_id in self.fail_accounts:
            raise RuntimeError("startup failed")
        existing = next(
            (
                slot
                for slot in self._slots.values()
                if slot.current_account_id == account_id
            ),
            None,
        )
        if existing is not None:
            if channel == "build":
                if self.build_budget_full:
                    raise InstanceCapacityError("build page budget exhausted")
                existing.has_build = True
                existing.build_counted = True
            else:
                existing.has_chat = True
                existing.chat_counted = True
            return existing
        if len(self._slots) >= self.max_contexts:
            raise InstanceCapacityError("capacity")
        if channel == "build" and self.build_budget_full:
            raise InstanceCapacityError("build page budget exhausted")
        self._counter += 1
        slot = FakeSlot(
            slot_id=f"slot-{self._counter}",
            current_account_id=account_id,
            has_chat=True,
            has_build=channel == "build",
            chat_counted=channel == "chat",
            build_counted=channel == "build",
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
            raise InstanceCapacityError("busy")
        self._slots.pop(slot_id, None)
        self.closed.append(slot_id)

    async def ensure_channel(self, slot_id: str, channel: str) -> None:
        slot = self._slots[slot_id]
        if channel == "build":
            if self.build_budget_full:
                raise InstanceCapacityError("build page budget exhausted")
            slot.has_build = True
            slot.build_counted = True
        else:
            slot.has_chat = True
            slot.chat_counted = True

    async def close_channel(self, slot_id: str, channel: str) -> None:
        slot = self._slots[slot_id]
        if slot.in_flight > 0:
            raise InstanceCapacityError("busy")
        if channel == "build":
            slot.has_build = False
            slot.build_counted = False
        else:
            slot.has_chat = False
            slot.chat_counted = False

    async def reassign_instance(self, slot_id: str, **kwargs) -> FakeSlot:
        slot = self._slots[slot_id]
        slot.current_account_id = str(kwargs["account_id"])
        return slot


def _store(
    *accounts: AccountMeta,
    active_id: str | None = None,
    missing_auth: set[str] | None = None,
) -> object:
    mapping = {account.id: account for account in accounts}
    missing = missing_auth or set()

    class Store:
        def get_account(self, account_id: str) -> AccountMeta | None:
            return mapping.get(account_id)

        def get_auth_path_optional(
            self, account_id: str, *, require_exists: bool = False
        ) -> str | None:
            if account_id not in mapping or (require_exists and account_id in missing):
                return None
            return f"{account_id}.json"

        def list_accounts(self) -> list[AccountMeta]:
            return list(mapping.values())

        def get_active_account(self) -> AccountMeta | None:
            if active_id is not None:
                return mapping.get(active_id)
            return next(iter(mapping.values()), None)

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
    pool: FakePool | None = None,
    *,
    accounts: tuple[AccountMeta, ...] | None = None,
    active_id: str | None = None,
    missing_auth: set[str] | None = None,
) -> tuple[InstanceManager, FakePool]:
    fake_pool = pool or FakePool()
    manager = InstanceManager(
        fake_pool,  # type: ignore[arg-type]
        _store(  # type: ignore[arg-type]
            *(accounts or (_account("a"), _account("b"), _account("c"))),
            active_id=active_id,
            missing_auth=missing_auth,
        ),
    )
    return manager, fake_pool


async def _settle(manager: InstanceManager) -> None:
    await manager.wait_idle()


class SlowPool(FakePool):
    def __init__(self, max_contexts: int = 2) -> None:
        super().__init__(max_contexts)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def create_instance(self, **kwargs) -> FakeSlot:
        self.started.set()
        await self.release.wait()
        return await super().create_instance(**kwargs)


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
    assert listed.current_instance_id == first.id
    assert first.id != second.id
    assert all(item.state.value == "active" for item in listed.instances)
    assert listed.instances[0].channel == "pg"
    assert all(slot.pinned for slot in pool.slots())


@pytest.mark.asyncio
async def test_create_with_build_channel_does_not_report_hidden_chat_page() -> None:
    manager, pool = _manager()
    item = await manager.create("a", "build")
    await _settle(manager)

    listed = await manager.list_instances()
    assert listed.instances[0].channel == "build"
    assert len(pool.slots()) == 1
    assert pool.slots()[0].has_chat is True
    assert pool.slots()[0].chat_counted is False
    assert item.id == listed.instances[0].id


@pytest.mark.asyncio
async def test_startup_starts_max_contexts_accounts_active_first() -> None:
    manager, pool = _manager(
        FakePool(max_contexts=2),
        accounts=(_account("a"), _account("b"), _account("c")),
        active_id="b",
    )
    await manager.startup()

    listed = await manager.list_instances()
    assert len(listed.instances) == 2
    assert [item.account_id for item in listed.instances] == ["b", "a"]
    assert listed.current_instance_id == listed.instances[0].id
    assert listed.latest_instance_id == listed.instances[1].id
    assert len(pool.slots()) == 2


@pytest.mark.asyncio
async def test_startup_starts_fewer_when_accounts_are_unavailable() -> None:
    manager, pool = _manager(
        FakePool(max_contexts=3),
        accounts=(
            _account("a"),
            _account("expired", expired=True),
            _account("missing"),
        ),
        missing_auth={"missing"},
    )
    await manager.startup()

    listed = await manager.list_instances()
    assert [item.account_id for item in listed.instances] == ["a"]
    assert len(pool.slots()) == 1


@pytest.mark.asyncio
async def test_startup_does_not_duplicate_accounts_and_isolates_failures() -> None:
    pool = FakePool(max_contexts=2)
    pool.fail_accounts.add("b")
    manager, _ = _manager(
        pool,
        accounts=(_account("a"), _account("b"), _account("c")),
        active_id="a",
    )
    await manager.startup()

    listed = await manager.list_instances()
    assert [item.account_id for item in listed.instances] == ["a", "c"]
    assert listed.current_instance_id == listed.instances[0].id
    assert listed.latest_instance_id == listed.instances[1].id


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
async def test_build_budget_is_checked_before_creating_a_half_instance() -> None:
    pool = FakePool()
    pool.build_budget_full = True
    manager, _ = _manager(pool)

    with pytest.raises(InstanceCapacityExhaustedError):
        await manager.create("a", "build")
    assert (await manager.list_instances()).instances == ()
    assert pool.created == []


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
    assert listed.current_instance_id == first.id


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


@pytest.mark.asyncio
async def test_listing_while_starting_does_not_adopt_the_same_slot() -> None:
    slow_pool = SlowPool()
    manager, _ = _manager(slow_pool)
    await manager.create("a")
    await slow_pool.started.wait()

    listed = await manager.list_instances()
    assert len(listed.instances) == 1
    assert listed.instances[0].state.value == "starting"

    slow_pool.release.set()
    await _settle(manager)
    listed = await manager.list_instances()
    assert len(listed.instances) == 1
    assert listed.instances[0].state.value == "active"


@pytest.mark.asyncio
async def test_extra_pool_slot_for_same_account_is_not_adopted() -> None:
    manager, pool = _manager()
    await manager.create("a")
    await _settle(manager)
    pool._slots["slot-extra"] = FakeSlot(
        slot_id="slot-extra", current_account_id="a", pinned=False
    )

    listed = await manager.list_instances()
    assert len(listed.instances) == 1


@pytest.mark.asyncio
async def test_switch_active_instance_does_not_close_other_instances() -> None:
    pool = FakePool()
    calls: list[str] = []

    async def switch_account(account_id: str) -> bool:
        calls.append(account_id)
        return True

    manager = InstanceManager(
        pool,  # type: ignore[arg-type]
        _store(_account("a"), _account("b")),  # type: ignore[arg-type]
        switch_account=switch_account,
    )
    first = await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)

    switched = await manager.switch_to(second.id)
    listed = await manager.list_instances()

    assert switched.id == second.id
    assert listed.current_instance_id == second.id
    assert calls == ["b"]
    assert len(listed.instances) == 2
    assert pool.closed == []
    assert first.id != second.id


@pytest.mark.asyncio
async def test_failed_account_switch_keeps_current_unchanged() -> None:
    pool = FakePool()

    async def switch_account(_account_id: str) -> bool:
        return False

    manager = InstanceManager(
        pool,  # type: ignore[arg-type]
        _store(_account("a"), _account("b")),  # type: ignore[arg-type]
        switch_account=switch_account,
    )
    first = await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)
    before = (await manager.list_instances()).current_instance_id

    with pytest.raises(InstanceNotReadyError):
        await manager.switch_to(second.id)

    listed = await manager.list_instances()
    assert listed.current_instance_id == before
    assert listed.current_instance_id == first.id


@pytest.mark.asyncio
async def test_switch_active_target_can_start_another_channel_without_new_instance() -> (
    None
):
    manager, pool = _manager()
    item = await manager.create("a")
    await _settle(manager)
    await manager.switch_to(item.id, "build")
    listed = await manager.list_instances()

    assert len(listed.instances) == 2
    assert {item.channel for item in listed.instances} == {"pg", "build"}
    assert len(pool.slots()) == 1
    assert pool.slots()[0].has_build is True


@pytest.mark.asyncio
async def test_switch_channel_budget_failure_returns_409_without_page() -> None:
    pool = FakePool()
    manager, _ = _manager(pool)
    item = await manager.create("a")
    await _settle(manager)
    pool.build_budget_full = True

    with pytest.raises(InstanceCapacityExhaustedError):
        await manager.switch_to(item.id, "build")

    listed = await manager.list_instances()
    assert listed.instances[0].channel == "pg"
    assert pool.slots()[0].has_build is False


@pytest.mark.asyncio
async def test_switch_inactive_target_restarts_without_closing_when_capacity_free() -> (
    None
):
    manager, pool = _manager(FakePool(max_contexts=2))
    await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)
    slot_id = next(
        item.runtime_ref for item in manager._records.values() if item.id == second.id
    )
    await pool.close_instance(slot_id)
    listed = await manager.list_instances()
    assert (
        next(item for item in listed.instances if item.id == second.id).state.value
        == "invalid"
    )

    switched = await manager.switch_to(second.id)
    listed = await manager.list_instances()

    assert switched.state.value == "active"
    assert len(listed.instances) == 2
    assert listed.current_instance_id == second.id


@pytest.mark.asyncio
async def test_switch_inactive_target_closes_current_when_capacity_full() -> None:
    manager, pool = _manager(FakePool(max_contexts=2))
    first = await manager.create("a")
    await _settle(manager)
    second = await manager.create("b")
    await _settle(manager)
    slot_id = next(
        item.runtime_ref for item in manager._records.values() if item.id == second.id
    )
    await pool.close_instance(slot_id)
    await manager.list_instances()
    pool.max_contexts = 1

    switched = await manager.switch_to(second.id)
    listed = await manager.list_instances()

    assert switched.state.value == "active"
    assert [item.id for item in listed.instances] == [second.id]
    assert listed.current_instance_id == second.id
    assert first.id not in {item.id for item in listed.instances}
