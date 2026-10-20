"""User-facing instance lifecycle for WP5 v4.

This layer maps accounts to stable, numbered instances. It intentionally does
not expose Context/Page/Slot concepts; those stay inside ``InstancePool``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aistudio_api.domain.instances import Instance, InstanceList, InstanceState
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstanceCapacityError,
    SlotState,
)
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("instances")


class InstanceError(RuntimeError):
    code = "instance_error"
    status_code = 409


class InstanceNotFoundError(InstanceError):
    code = "instance_not_found"
    status_code = 404


class InstanceAccountNotFoundError(InstanceError):
    code = "instance_account_not_found"
    status_code = 404


class InstanceInvalidAccountError(InstanceError):
    code = "instance_account_invalid"
    status_code = 409


class InstanceAlreadyExistsError(InstanceError):
    code = "instance_already_exists"
    status_code = 409


class InstanceCapacityExhaustedError(InstanceError):
    code = "instance_capacity_exhausted"
    status_code = 409


class InstanceBusyError(InstanceError):
    code = "instance_busy"
    status_code = 409


class InstanceNotReadyError(InstanceError):
    code = "instance_not_ready"
    status_code = 409


class InstanceStore(Protocol):
    def get_account(self, account_id: str) -> AccountMeta | None: ...

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ) -> Any: ...

    def list_accounts(self) -> list[AccountMeta]: ...

    def get_active_account(self) -> AccountMeta | None: ...


@dataclass
class _Record:
    id: str
    number: int
    account_id: str
    state: InstanceState
    runtime_ref: str = ""
    channel: str = "chat"
    created_at: float = 0.0
    started_at: float = 0.0
    last_error: str | None = None


class InstanceManager:
    """Owns user-visible instances and their stable numbering."""

    def __init__(
        self,
        pool: Any,
        account_store: InstanceStore,
        *,
        clock: Callable[[], float] = time.time,
        switch_account: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._pool = pool
        self._accounts = account_store
        self._clock = clock
        self._switch_account = switch_account
        self._records: dict[str, _Record] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._next_number = 1
        self._latest_id: str | None = None
        self._current_id: str | None = None

    # ------------------------------------------------------------- public API

    async def startup(self) -> None:
        """Start up to ``max_contexts`` available account instances.

        The active account is tried first, then the remaining accounts in
        registry (rotation) order. Invalid/credential-missing accounts and
        duplicate instances are skipped; one failed account never blocks the
        others.
        """
        async with self._lifecycle_lock:
            self._adopt_orphans()
            active = self._accounts.get_active_account()
            accounts = list(self._accounts.list_accounts())
            ordered = [
                account
                for account in accounts
                if active is not None and account.id == active.id
            ] + [
                account
                for account in accounts
                if active is None or account.id != active.id
            ]
            target = self._max_instances()
            log.info(
                "[instances] startup begin max_contexts=%d accounts=%d",
                target,
                len(ordered),
            )
            for account in ordered:
                if self._occupying_count() >= target:
                    break
                if self._has_record_for_account(account.id):
                    continue
                try:
                    self._validate_account(account.id)
                except InstanceError as exc:
                    log.warning(
                        "[instances] startup skip account=%s reason=%s",
                        account.id,
                        exc,
                    )
                    continue
                record = self._new_record(
                    account.id, InstanceState.STARTING, self._default_channel()
                )
                self._records[record.id] = record
                if await self._start(record, account, record.channel):
                    log.info(
                        "[instances] startup started id=%s account=%s",
                        record.id,
                        account.id,
                    )
                else:
                    self._records.pop(record.id, None)
                    self._repair_latest()
                    self._repair_current()

            if active is not None:
                for record in self._records.values():
                    if (
                        record.account_id == active.id
                        and record.state is InstanceState.ACTIVE
                    ):
                        self._current_id = record.id
                        break
            self._repair_latest()
            self._repair_current()
            log.info(
                "[instances] startup complete active=%d latest=%s current=%s",
                sum(
                    1
                    for record in self._records.values()
                    if record.state is InstanceState.ACTIVE
                ),
                self._latest_id,
                self._current_id,
            )

    async def list_instances(self) -> InstanceList:
        self._adopt_orphans()
        self._refresh_records()
        self._repair_latest()
        self._repair_current()
        instances = tuple(
            self._view(record)
            for record in sorted(self._records.values(), key=lambda r: r.number)
        )
        latest = self._latest_id
        return InstanceList(
            max_instances=self._max_instances(),
            latest_instance_id=latest,
            current_instance_id=self._current_id,
            instances=instances,
        )

    async def create(self, account_id: str, channel: str | None = None) -> Instance:
        account = self._validate_account(account_id)
        normalized = self._normalize_channel(channel)
        async with self._lifecycle_lock:
            self._adopt_orphans()
            if self._has_record_for_account(account_id):
                raise InstanceAlreadyExistsError(f"账号 {account_id} 已有实例")
            if self._occupying_count() >= self._max_instances():
                raise InstanceCapacityExhaustedError(
                    f"实例上限已满（{self._max_instances()}），请先关闭一个实例"
                )
            await self._preflight_channel(normalized)

            record = self._new_record(account.id, InstanceState.STARTING, normalized)
            self._records[record.id] = record
            self._schedule(record.id, self._start_created(record, account))
            return self._view(record)

    async def close(self, instance_id: str) -> Instance:
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            if record.state is InstanceState.RECLAIMING:
                return self._view(record)
            if record.state is InstanceState.STARTING and not record.runtime_ref:
                self._cancel(record.id)
                record.state = InstanceState.RECLAIMING
                view = self._view(record)
                self._records.pop(record.id, None)
                self._repair_latest()
                self._repair_current()
                return view
            if record.runtime_ref:
                slot = self._pool.get(record.runtime_ref)
                if slot is not None and int(getattr(slot, "in_flight", 0)) > 0:
                    raise InstanceBusyError("该实例仍有在途请求")
            record.state = InstanceState.RECLAIMING
            if self._current_id == record.id:
                self._current_id = None
                self._repair_current()
            self._schedule(record.id, self._reclaim(record))
            return self._view(record)

    async def switch_to(self, instance_id: str, channel: str | None = None) -> Instance:
        """Switch the current account state to an instance.

        An already active target is switched in place. An inactive target is
        (re)started; if runtime capacity is full, the current instance is closed
        first so ``max_contexts=1`` behaves as close-old-then-open-new.
        """
        normalized = self._normalize_channel(channel) if channel is not None else None
        async with self._lifecycle_lock:
            self._adopt_orphans()
            target = self._record(instance_id)
            account = self._validate_account(target.account_id)

            if target.state is InstanceState.STARTING:
                task = self._tasks.get(target.id)
                if task is not None:
                    await asyncio.shield(task)
                if target.id not in self._records:
                    raise InstanceNotFoundError("实例启动失败")
                self._refresh_records()

            if target.state is InstanceState.RECLAIMING:
                raise InstanceNotReadyError("实例正在回收，不能切换")
            if target.state is InstanceState.INVALID:
                await self._start_inactive_target(target, account, normalized)
            elif target.state is not InstanceState.ACTIVE:
                raise InstanceNotReadyError("实例尚未就绪，不能切换")

            if normalized is not None:
                try:
                    await self._pool.ensure_channel(target.runtime_ref, normalized)
                except InstanceCapacityError as exc:
                    raise InstanceCapacityExhaustedError(str(exc)) from exc

            if self._switch_account is not None:
                switched = await self._switch_account(account.id)
                if not switched:
                    raise InstanceNotReadyError("账号状态切换失败")
            self._current_id = target.id
            return self._view(target)

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        account = self._validate_account(account_id)
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            if record.state is not InstanceState.ACTIVE:
                raise InstanceNotReadyError("实例尚未就绪，不能换账号")
            if record.account_id == account.id:
                return self._view(record)
            if self._has_record_for_account(account.id, exclude_id=record.id):
                raise InstanceAlreadyExistsError(f"账号 {account_id} 已有实例")
            if record.runtime_ref:
                slot = self._pool.get(record.runtime_ref)
                if slot is not None and int(getattr(slot, "in_flight", 0)) > 0:
                    raise InstanceBusyError("该实例仍有在途请求")
            record.account_id = account.id
            record.state = InstanceState.STARTING
            record.last_error = None
            self._schedule(record.id, self._reassign(record, account))
            return self._view(record)

    def has_instances(self) -> bool:
        return bool(self._records)

    def current_account_id(self) -> str | None:
        record = self._records.get(self._current_id or "")
        return record.account_id if record is not None else None

    def active_account_ids(self) -> set[str]:
        return {
            record.account_id
            for record in self._records.values()
            if record.state is InstanceState.ACTIVE
        }

    async def wait_idle(self) -> None:
        """Test/shutdown helper: wait for all background lifecycle operations."""
        while self._tasks:
            tasks = list(self._tasks.values())
            await asyncio.gather(*tasks, return_exceptions=True)
            for key, task in list(self._tasks.items()):
                if task.done():
                    self._tasks.pop(key, None)

    # ---------------------------------------------------------------- lifecycle

    async def _start_created(self, record: _Record, account: AccountMeta) -> None:
        ok = await self._start(record, account, record.channel)
        if ok:
            return
        self._records.pop(record.id, None)
        self._repair_latest()
        self._repair_current()

    async def _start(self, record: _Record, account: AccountMeta, channel: str) -> bool:
        record.state = InstanceState.STARTING
        record.channel = channel
        record.last_error = None
        if record.runtime_ref:
            slot = self._pool.get(record.runtime_ref)
            if slot is not None:
                if int(getattr(slot, "in_flight", 0)) > 0:
                    record.state = InstanceState.INVALID
                    record.last_error = "该实例仍有在途请求"
                    return False
                try:
                    await self._pool.close_instance(record.runtime_ref)
                except Exception as exc:
                    record.state = InstanceState.INVALID
                    record.last_error = str(exc)
                    log.warning(
                        "[instances] restart cleanup failed id=%s error=%s",
                        record.id,
                        exc,
                    )
                    return False
            record.runtime_ref = ""
        try:
            auth_file = self._accounts.get_auth_path_optional(
                account.id, require_exists=True
            )
            if auth_file is None:
                raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
            slot = await self._pool.create_instance(
                account_id=account.id,
                auth_file=str(auth_file),
                auth_user=account.auth_user,
                channel=channel,
            )
            record.runtime_ref = str(getattr(slot, "slot_id", ""))
            record.state = InstanceState.ACTIVE
            record.started_at = self._clock()
            self._latest_id = record.id
            if self._current_id is None:
                self._current_id = record.id
            log.info(
                "[instances] started id=%s number=%s account=%s channel=%s",
                record.id,
                record.number,
                account.id,
                channel,
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.state = InstanceState.INVALID
            record.last_error = str(exc)
            log.warning(
                "[instances] start failed id=%s account=%s error=%s",
                record.id,
                account.id,
                exc,
            )
            return False

    async def _start_inactive_target(
        self, target: _Record, account: AccountMeta, channel: str | None
    ) -> None:
        target_slot = self._pool.get(target.runtime_ref) if target.runtime_ref else None
        if target_slot is not None and int(getattr(target_slot, "in_flight", 0)) > 0:
            raise InstanceBusyError("该实例仍有在途请求")
        if target_slot is None and self._runtime_occupancy() >= self._max_instances():
            victim = self._current_instance()
            if victim is None or victim.id == target.id:
                victim = self._most_recent_active(exclude_id=target.id)
            if victim is not None:
                await self._close_now(victim)
        if not await self._start(
            target, account, channel or target.channel or self._default_channel()
        ):
            raise InstanceNotReadyError(target.last_error or "实例启动失败")

    async def _reclaim(self, record: _Record) -> None:
        try:
            if record.runtime_ref:
                await self._pool.close_instance(record.runtime_ref)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.state = InstanceState.ACTIVE
            record.last_error = str(exc)
            log.warning("[instances] close failed id=%s error=%s", record.id, exc)
            return
        record.runtime_ref = ""
        self._records.pop(record.id, None)
        self._repair_latest()
        self._repair_current()
        log.info("[instances] closed id=%s number=%s", record.id, record.number)

    async def _close_now(self, record: _Record) -> None:
        if record.runtime_ref:
            slot = self._pool.get(record.runtime_ref)
            if slot is not None and int(getattr(slot, "in_flight", 0)) > 0:
                raise InstanceBusyError("该实例仍有在途请求")
            try:
                await self._pool.close_instance(record.runtime_ref)
            except InstanceCapacityError as exc:
                raise InstanceBusyError(str(exc)) from exc
        else:
            self._cancel(record.id)
        record.runtime_ref = ""
        record.state = InstanceState.RECLAIMING
        if self._current_id == record.id:
            self._current_id = None
        self._records.pop(record.id, None)
        self._repair_latest()
        self._repair_current()
        log.info("[instances] closed now id=%s number=%s", record.id, record.number)

    async def _reassign(self, record: _Record, account: AccountMeta) -> None:
        try:
            auth_file = self._accounts.get_auth_path_optional(
                account.id, require_exists=True
            )
            if auth_file is None:
                raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
            if record.runtime_ref:
                await self._pool.reassign_instance(
                    record.runtime_ref,
                    account_id=account.id,
                    auth_file=str(auth_file),
                    auth_user=account.auth_user,
                    channel=record.channel or self._default_channel(),
                )
            record.state = InstanceState.ACTIVE
            record.last_error = None
            log.info(
                "[instances] reassigned id=%s number=%s account=%s",
                record.id,
                record.number,
                account.id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.state = InstanceState.INVALID
            record.last_error = str(exc)
            log.warning("[instances] reassign failed id=%s error=%s", record.id, exc)

    # ------------------------------------------------------------------ helpers

    async def _preflight_channel(self, channel: str) -> None:
        checker = getattr(self._pool, "check_create_budget", None)
        if checker is None:
            return
        try:
            await checker(channel)
        except InstanceCapacityError as exc:
            raise InstanceCapacityExhaustedError(str(exc)) from exc

    def _schedule(self, key: str, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks[key] = task
        task.add_done_callback(
            lambda done: self._tasks.pop(key, None) if done.done() else None
        )

    def _cancel(self, key: str) -> None:
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _record(self, instance_id: str) -> _Record:
        record = self._records.get(instance_id)
        if record is None:
            raise InstanceNotFoundError("实例不存在")
        return record

    def _validate_account(self, account_id: str) -> AccountMeta:
        account = self._accounts.get_account(account_id)
        if account is None:
            raise InstanceAccountNotFoundError("账号不存在")
        if account.expired:
            raise InstanceInvalidAccountError("账号已失效，请重新登录")
        if (
            self._accounts.get_auth_path_optional(account.id, require_exists=True)
            is None
        ):
            raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
        return account

    def _new_record(
        self, account_id: str, state: InstanceState, channel: str
    ) -> _Record:
        number = self._next_number
        self._next_number += 1
        return _Record(
            id=f"inst-{number}",
            number=number,
            account_id=account_id,
            state=state,
            channel=channel,
            created_at=self._clock(),
        )

    @staticmethod
    def _normalize_channel(channel: str | None) -> str:
        if channel in (None, "", "pg", "chat"):
            return "chat"
        if channel == "build":
            return "build"
        raise InstanceNotReadyError("未知渠道")

    @staticmethod
    def _default_channel() -> str:
        return "chat"

    def _has_record_for_account(
        self, account_id: str, *, exclude_id: str | None = None
    ) -> bool:
        return any(
            record.account_id == account_id
            and record.id != exclude_id
            and record.state is not InstanceState.RECLAIMING
            for record in self._records.values()
        )

    def _max_instances(self) -> int:
        return max(1, int(self._pool.max_contexts))

    def _occupying_count(self) -> int:
        return sum(
            1
            for record in self._records.values()
            if record.state is not InstanceState.RECLAIMING
        )

    def _runtime_occupancy(self) -> int:
        return sum(
            1
            for record in self._records.values()
            if record.runtime_ref
            and record.state is not InstanceState.RECLAIMING
            and self._pool.get(record.runtime_ref) is not None
        )

    def _current_instance(self) -> _Record | None:
        return self._records.get(self._current_id or "")

    def _most_recent_active(self, *, exclude_id: str | None = None) -> _Record | None:
        candidates = [
            record
            for record in self._records.values()
            if record.id != exclude_id and record.state is InstanceState.ACTIVE
        ]
        return (
            max(candidates, key=lambda record: record.started_at)
            if candidates
            else None
        )

    def _adopt_orphans(self) -> None:
        known = {
            record.runtime_ref
            for record in self._records.values()
            if record.runtime_ref
        }
        known_accounts = {record.account_id for record in self._records.values()}
        for slot in self._pool.slots():
            slot_id = str(getattr(slot, "slot_id", ""))
            account_id = str(getattr(slot, "current_account_id", ""))
            if not slot_id or not account_id or slot_id in known:
                continue
            if account_id in known_accounts:
                # A record already owns this account (possibly still starting).
                # The pool slot is its runtime, not a second user instance.
                continue
            if getattr(slot, "state", None) is SlotState.RETIRING:
                continue
            slot.pinned = True
            record = self._new_record(
                account_id,
                self._state_from_slot(slot),
                str(getattr(slot, "channel_role", "") or self._default_channel()),
            )
            record.runtime_ref = slot_id
            record.started_at = self._clock()
            self._records[record.id] = record
            known.add(slot_id)
            known_accounts.add(account_id)
            if self._latest_id is None:
                self._latest_id = record.id
            if self._current_id is None:
                self._current_id = record.id

    def _refresh_records(self) -> None:
        for record in list(self._records.values()):
            if not record.runtime_ref:
                continue
            slot = self._pool.get(record.runtime_ref)
            if slot is None:
                if record.state is not InstanceState.RECLAIMING:
                    record.state = InstanceState.INVALID
                    record.last_error = "runtime missing"
                continue
            if record.state is not InstanceState.RECLAIMING:
                record.state = self._state_from_slot(slot)

    @staticmethod
    def _state_from_slot(slot: Any) -> InstanceState:
        state = getattr(slot, "state", None)
        if state in (SlotState.LOADING,):
            return InstanceState.STARTING
        if state is SlotState.RETIRING:
            return InstanceState.RECLAIMING
        if state is SlotState.EMPTY:
            return InstanceState.INVALID
        return InstanceState.ACTIVE

    def _view(self, record: _Record) -> Instance:
        slot = self._pool.get(record.runtime_ref) if record.runtime_ref else None
        channels: tuple[str, ...] = ()
        in_flight = 0
        if slot is not None:
            if self._surface_active(slot, "chat"):
                channels = (*channels, "pg")
            if self._surface_active(slot, "build"):
                channels = (*channels, "build")
            in_flight = int(getattr(slot, "in_flight", 0))
        return Instance(
            id=record.id,
            number=record.number,
            account_id=record.account_id,
            state=record.state,
            channels=channels,
            in_flight=in_flight,
            latest=record.id == self._latest_id,
        )

    @classmethod
    def _surface_active(cls, slot: Any, surface: str) -> bool:
        counted_name = "chat_counted" if surface == "chat" else "build_counted"
        counted = getattr(slot, counted_name, True)
        return bool(counted) and cls._ready(slot, surface)

    @staticmethod
    def _ready(slot: Any, surface: str) -> bool:
        probe = getattr(slot, "chat_ready" if surface == "chat" else "build_ready")
        try:
            return bool(probe())
        except Exception:
            return False

    def _repair_current(self) -> None:
        if self._current_id in self._records:
            return
        active = [
            record
            for record in self._records.values()
            if record.state is InstanceState.ACTIVE
        ]
        self._current_id = (
            max(active, key=lambda record: record.started_at).id if active else None
        )

    def _repair_latest(self) -> None:
        if self._latest_id in self._records:
            return
        candidates = [
            record
            for record in self._records.values()
            if record.state is not InstanceState.RECLAIMING
        ]
        self._latest_id = (
            max(candidates, key=lambda record: record.started_at).id
            if candidates
            else None
        )


__all__ = [
    "InstanceAccountNotFoundError",
    "InstanceAlreadyExistsError",
    "InstanceBusyError",
    "InstanceCapacityExhaustedError",
    "InstanceError",
    "InstanceInvalidAccountError",
    "InstanceManager",
    "InstanceNotFoundError",
    "InstanceNotReadyError",
]
