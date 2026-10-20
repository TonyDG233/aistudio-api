"""User-facing instance lifecycle for WP5 v4.

This layer maps accounts to stable, numbered instances. It intentionally does
not expose Context/Page/Slot concepts; those stay inside ``InstancePool``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aistudio_api.domain.instances import Instance, InstanceList, InstanceState
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.instance_pool import SlotState
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

    def get_auth_path_optional(self, account_id: str) -> Any: ...


@dataclass
class _Record:
    id: str
    number: int
    account_id: str
    state: InstanceState
    runtime_ref: str = ""
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
    ) -> None:
        self._pool = pool
        self._accounts = account_store
        self._clock = clock
        self._records: dict[str, _Record] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._next_number = 1
        self._latest_id: str | None = None

    # ------------------------------------------------------------- public API

    async def list_instances(self) -> InstanceList:
        self._adopt_orphans()
        self._refresh_records()
        self._repair_latest()
        instances = tuple(
            self._view(record)
            for record in sorted(self._records.values(), key=lambda r: r.number)
        )
        latest = self._latest_id
        return InstanceList(
            max_instances=self._max_instances(),
            latest_instance_id=latest,
            instances=instances,
        )

    async def create(self, account_id: str) -> Instance:
        account = self._validate_account(account_id)
        self._adopt_orphans()
        if any(
            record.account_id == account_id
            and record.state is not InstanceState.RECLAIMING
            for record in self._records.values()
        ):
            raise InstanceAlreadyExistsError(f"账号 {account_id} 已有实例")
        if self._occupying_count() >= self._max_instances():
            raise InstanceCapacityExhaustedError(
                f"实例上限已满（{self._max_instances()}），请先关闭一个实例"
            )

        number = self._next_number
        self._next_number += 1
        record = _Record(
            id=f"inst-{number}",
            number=number,
            account_id=account.id,
            state=InstanceState.STARTING,
            created_at=self._clock(),
        )
        self._records[record.id] = record
        self._schedule(record.id, self._start(record, account))
        return self._view(record)

    async def close(self, instance_id: str) -> Instance:
        record = self._record(instance_id)
        if record.state is InstanceState.STARTING and not record.runtime_ref:
            self._cancel(record.id)
            view = Instance(
                id=record.id,
                number=record.number,
                account_id=record.account_id,
                state=InstanceState.RECLAIMING,
            )
            self._records.pop(record.id, None)
            self._repair_latest()
            return view
        if record.runtime_ref:
            slot = self._pool.get(record.runtime_ref)
            if slot is not None and int(getattr(slot, "in_flight", 0)) > 0:
                raise InstanceBusyError("该实例仍有在途请求")
        record.state = InstanceState.RECLAIMING
        self._schedule(record.id, self._reclaim(record))
        return self._view(record)

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        record = self._record(instance_id)
        account = self._validate_account(account_id)
        if record.state is not InstanceState.ACTIVE:
            raise InstanceNotReadyError("实例尚未就绪，不能换账号")
        if record.account_id == account.id:
            return self._view(record)
        if any(
            other.id != record.id
            and other.account_id == account.id
            and other.state is not InstanceState.RECLAIMING
            for other in self._records.values()
        ):
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

    async def _start(self, record: _Record, account: AccountMeta) -> None:
        try:
            auth_file = self._accounts.get_auth_path_optional(account.id)
            slot = await self._pool.create_instance(
                account_id=account.id,
                auth_file=str(auth_file) if auth_file is not None else None,
                auth_user=account.auth_user,
                channel="chat",
            )
            record.runtime_ref = str(getattr(slot, "slot_id", ""))
            record.state = InstanceState.ACTIVE
            record.started_at = self._clock()
            self._latest_id = record.id
            log.info(
                "[instances] started id=%s number=%s account=%s",
                record.id,
                record.number,
                account.id,
            )
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
        self._records.pop(record.id, None)
        self._repair_latest()
        log.info("[instances] closed id=%s number=%s", record.id, record.number)

    async def _reassign(self, record: _Record, account: AccountMeta) -> None:
        try:
            auth_file = self._accounts.get_auth_path_optional(account.id)
            if record.runtime_ref:
                await self._pool.reassign_instance(
                    record.runtime_ref,
                    account_id=account.id,
                    auth_file=str(auth_file) if auth_file is not None else None,
                    auth_user=account.auth_user,
                    channel="chat",
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
        if self._accounts.get_auth_path_optional(account.id) is None:
            raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
        return account

    def _max_instances(self) -> int:
        return max(1, int(self._pool.max_contexts))

    def _occupying_count(self) -> int:
        return sum(
            1
            for record in self._records.values()
            if record.state is not InstanceState.RECLAIMING
        )

    def _adopt_orphans(self) -> None:
        known = {
            record.runtime_ref
            for record in self._records.values()
            if record.runtime_ref
        }
        for slot in self._pool.slots():
            slot_id = str(getattr(slot, "slot_id", ""))
            account_id = str(getattr(slot, "current_account_id", ""))
            if not slot_id or not account_id or slot_id in known:
                continue
            if getattr(slot, "state", None) is SlotState.RETIRING:
                continue
            slot.pinned = True
            number = self._next_number
            self._next_number += 1
            record = _Record(
                id=f"inst-{number}",
                number=number,
                account_id=account_id,
                state=self._state_from_slot(slot),
                runtime_ref=slot_id,
                created_at=self._clock(),
                started_at=self._clock(),
            )
            self._records[record.id] = record
            known.add(slot_id)
            if self._latest_id is None:
                self._latest_id = record.id

    def _refresh_records(self) -> None:
        for record in list(self._records.values()):
            if not record.runtime_ref:
                continue
            slot = self._pool.get(record.runtime_ref)
            if slot is None:
                if record.state is InstanceState.ACTIVE:
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
            if self._ready(slot, "chat"):
                channels = (*channels, "pg")
            if self._ready(slot, "build"):
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

    @staticmethod
    def _ready(slot: Any, surface: str) -> bool:
        probe = getattr(slot, "chat_ready" if surface == "chat" else "build_ready")
        try:
            return bool(probe())
        except Exception:
            return False

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
