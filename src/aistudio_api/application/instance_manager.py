"""User-facing instance lifecycle for WP5 v4.

This layer maps accounts to stable, numbered instances. It intentionally does
not expose Context/Page/Slot concepts; those stay inside ``InstancePool``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
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
        features_provider: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self._pool = pool
        self._accounts = account_store
        self._clock = clock
        self._switch_account = switch_account
        self._features_provider = features_provider
        self._records: dict[str, _Record] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._next_number = 1
        self._latest_id: str | None = None
        self._current_id: str | None = None

    # ------------------------------------------------------------- public API

    async def startup(self) -> None:
        """Start enabled channels within channel caps and the context limit.

        Account selection goes through the same eligibility graph as the request
        path (channel registry -> account tag -> group/quota pool -> affinity).
        There is no tier-based branch in this method.
        """
        async with self._lifecycle_lock:
            self._adopt_orphans()
            features = self._features()
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
            max_contexts = max(1, int(self._pool.max_contexts))
            slots = self._startup_slots()
            used_channels: dict[str, set[str]] = {}
            started_accounts: set[str] = set()
            log.info(
                "[instances] startup begin max_contexts=%d slots=%s accounts=%d",
                max_contexts,
                slots,
                len(ordered),
            )
            failed_accounts: set[str] = set()
            for channel in slots:
                ranked = self._startup_candidates(
                    [
                        account
                        for account in ordered
                        if account.id not in failed_accounts
                    ],
                    channel,
                    features,
                )
                if not ranked:
                    log.warning(
                        "[instances] startup no eligible account channel=%s",
                        channel,
                    )
                    continue
                if len(started_accounts) < max_contexts:
                    unused = [
                        account
                        for account in ranked
                        if account.id not in started_accounts
                    ]
                    reused = [
                        account
                        for account in ranked
                        if account.id in started_accounts
                    ]
                    ranked = unused + reused
                else:
                    ranked = [
                        account
                        for account in ranked
                        if account.id in started_accounts
                    ]
                    if not ranked:
                        log.warning(
                            "[instances] startup context limit reached channel=%s",
                            channel,
                        )
                        continue
                started = False
                for account in ranked:
                    if channel in used_channels.get(account.id, set()):
                        continue
                    try:
                        self._validate_account(account.id)
                    except InstanceError as exc:
                        log.warning(
                            "[instances] startup skip account=%s reason=%s",
                            account.id,
                            exc,
                        )
                        failed_accounts.add(account.id)
                        continue
                    record = self._new_record(
                        account.id, InstanceState.STARTING, channel
                    )
                    self._records[record.id] = record
                    if await self._start(record, account, channel):
                        used_channels.setdefault(account.id, set()).add(channel)
                        started_accounts.add(account.id)
                        started = True
                        log.info(
                            "[instances] startup started id=%s account=%s channel=%s",
                            record.id,
                            account.id,
                            channel,
                        )
                        break
                    self._records.pop(record.id, None)
                    self._repair_latest()
                    self._repair_current()
                    failed_accounts.add(account.id)
                if not started:
                    log.warning(
                        "[instances] startup could not start channel=%s", channel
                    )

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
            if self._has_record_for(account_id, normalized):
                raise InstanceAlreadyExistsError(
                    f"账号 {account_id} 已存在 {normalized} 实例"
                )
            if self._occupying_count() >= self._max_instances():
                raise InstanceCapacityExhaustedError(
                    f"实例上限已满（{self._max_instances()}），请先关闭一个实例"
                )
            cap = self._channel_cap(normalized)
            if cap is not None and self._channel_count(normalized) >= cap:
                raise InstanceCapacityExhaustedError(
                    f"{normalized} 渠道实例已达上限（{cap}），请先关闭该渠道实例"
                )
            await self._preflight_channel(normalized, account_id)

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
        """Switch current instance, or replace one instance's channel.

        An active target is switched in place. If a different channel is
        requested for the target's account, an existing sibling instance is
        reused. When the instance cap is full, the target instance's channel is
        closed and the requested channel is opened in the same account context
        (2c1p); otherwise a second instance is created (1c2p).
        """
        requested = self._normalize_channel(channel) if channel is not None else None
        async with self._lifecycle_lock:
            self._adopt_orphans()
            target = self._record(instance_id)
            account = self._validate_account(target.account_id)
            await self._settle_starting(target)

            if target.state is InstanceState.RECLAIMING:
                raise InstanceNotReadyError("实例正在回收，不能切换")

            if requested is None or requested == target.channel:
                await self._ensure_record_active(target, account)
                switched = await self._switch_current_account(account)
                if not switched:
                    raise InstanceNotReadyError("账号状态切换失败")
                self._current_id = target.id
                return self._view(target)

            existing = self._find_record_for(
                account.id, requested, exclude_id=target.id
            )
            if existing is not None:
                await self._settle_starting(existing)
                await self._ensure_record_active(existing, account)
                switched = await self._switch_current_account(account)
                if not switched:
                    raise InstanceNotReadyError("账号状态切换失败")
                self._current_id = existing.id
                return self._view(existing)

            cap = self._channel_cap(requested)
            if cap is not None and self._channel_count(requested) >= cap:
                raise InstanceCapacityExhaustedError(
                    f"{requested} 渠道实例已达上限（{cap}），请先关闭该渠道实例"
                )

            old_current = self._current_id
            if self._occupying_count() >= self._max_instances():
                # Replace this instance's channel; keep its context for reuse.
                if target.state is not InstanceState.ACTIVE:
                    raise InstanceNotReadyError("实例尚未就绪，不能换渠道")
                if target.runtime_ref:
                    slot = self._pool.get(target.runtime_ref)
                    if slot is not None and int(getattr(slot, "in_flight", 0)) > 0:
                        raise InstanceBusyError("该实例仍有在途请求")
                    try:
                        await self._pool.close_channel(
                            target.runtime_ref, target.channel
                        )
                    except InstanceCapacityError as exc:
                        raise InstanceBusyError(str(exc)) from exc
                self._cancel(target.id)
                self._records.pop(target.id, None)
                if self._current_id == target.id:
                    self._current_id = None
                self._repair_latest()

            record = self._new_record(account.id, InstanceState.STARTING, requested)
            self._records[record.id] = record
            if not await self._start(record, account, requested):
                self._records.pop(record.id, None)
                self._repair_latest()
                self._repair_current()
                error = record.last_error or "实例启动失败"
                if "budget" in error.lower() or "capacity" in error.lower():
                    raise InstanceCapacityExhaustedError(error)
                raise InstanceNotReadyError(error)
            switched = await self._switch_current_account(account)
            if not switched:
                self._current_id = old_current if old_current in self._records else None
                self._repair_current()
                raise InstanceNotReadyError("账号状态切换失败")
            self._current_id = record.id
            return self._view(record)

    async def _settle_starting(self, record: _Record) -> None:
        if record.state is not InstanceState.STARTING:
            return
        task = self._tasks.get(record.id)
        if task is not None:
            await asyncio.shield(task)
        if record.id not in self._records:
            raise InstanceNotFoundError("实例启动失败")
        self._refresh_records()

    async def _ensure_record_active(
        self, record: _Record, account: AccountMeta
    ) -> None:
        if record.state is InstanceState.INVALID:
            await self._start_inactive_target(record, account, record.channel)
            return
        if record.state is not InstanceState.ACTIVE:
            raise InstanceNotReadyError("实例尚未就绪，不能切换")
        if record.runtime_ref:
            try:
                await self._pool.ensure_channel(record.runtime_ref, record.channel)
            except InstanceCapacityError as exc:
                raise InstanceCapacityExhaustedError(str(exc)) from exc

    async def _switch_current_account(self, account: AccountMeta) -> bool:
        if self._switch_account is None:
            return True
        return await self._switch_account(account.id)

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        account = self._validate_account(account_id)
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            if record.state is not InstanceState.ACTIVE:
                raise InstanceNotReadyError("实例尚未就绪，不能换账号")
            if record.account_id == account.id:
                return self._view(record)
            if self._siblings(record):
                raise InstanceAlreadyExistsError(
                    "同一账号有多个渠道实例时，不能单独迁移其中一个实例"
                )
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
                    if self._siblings(record):
                        # Shared account context: only replace this instance's page.
                        await self._pool.close_channel(
                            record.runtime_ref, record.channel
                        )
                    else:
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
        if target_slot is None and len(self._pool.slots()) >= self._pool.max_contexts:
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
                if self._siblings(record):
                    await self._pool.close_channel(record.runtime_ref, record.channel)
                else:
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
                if self._siblings(record):
                    await self._pool.close_channel(record.runtime_ref, record.channel)
                else:
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

    async def _preflight_channel(
        self, channel: str, account_id: str | None = None
    ) -> None:
        checker = getattr(self._pool, "check_create_budget", None)
        if checker is None:
            return
        try:
            await checker(channel, account_id=account_id)
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

    def _has_record_for(
        self, account_id: str, channel: str, *, exclude_id: str | None = None
    ) -> bool:
        return any(
            record.account_id == account_id
            and record.channel == channel
            and record.id != exclude_id
            and record.state is not InstanceState.RECLAIMING
            for record in self._records.values()
        )

    def _find_record_for(
        self, account_id: str, channel: str, *, exclude_id: str | None = None
    ) -> _Record | None:
        for record in self._records.values():
            if (
                record.account_id == account_id
                and record.channel == channel
                and record.id != exclude_id
                and record.state is not InstanceState.RECLAIMING
            ):
                return record
        return None

    def _siblings(self, record: _Record) -> list[_Record]:
        if not record.runtime_ref:
            return []
        return [
            other
            for other in self._records.values()
            if other.id != record.id
            and other.runtime_ref == record.runtime_ref
            and other.state is not InstanceState.RECLAIMING
        ]

    def _max_instances(self) -> int:
        return max(2, int(self._pool.max_contexts))

    def _channel_cap(self, channel: str) -> int | None:
        getter = getattr(self._pool, "channel_max_instances", None)
        if not callable(getter):
            return None
        getter_fn: Any = getter
        result = getter_fn(channel)
        return int(result) if result is not None else None

    def _channel_enabled(self, channel: str) -> bool:
        getter = getattr(self._pool, "channel_enabled", None)
        if not callable(getter):
            return True
        return bool(getter(channel))

    def _channel_count(self, channel: str) -> int:
        return sum(
            1
            for record in self._records.values()
            if record.channel == channel
            and record.state is not InstanceState.RECLAIMING
        )

    def _startup_slots(self) -> list[str]:
        """One slot per enabled channel, then fill up to the remaining budget."""
        total = max(0, self._max_instances() - self._occupying_count())
        if total == 0:
            return []
        enabled = [
            channel for channel in ("chat", "build") if self._channel_enabled(channel)
        ]
        if not enabled:
            return []
        existing = {channel: self._channel_count(channel) for channel in enabled}
        slots: list[str] = []
        for channel in enabled:
            if len(slots) >= total:
                break
            cap = self._channel_cap(channel)
            if cap is None or existing[channel] + slots.count(channel) < cap:
                slots.append(channel)
        while len(slots) < total:
            added = False
            for channel in enabled:
                cap = self._channel_cap(channel)
                used = existing[channel] + slots.count(channel)
                if cap is not None and used >= cap:
                    continue
                slots.append(channel)
                added = True
                break
            if not added:
                break
        return slots[:total]

    def _features(self) -> Mapping[str, object]:
        if self._features_provider is not None:
            return self._features_provider() or {}
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_features_config,
        )

        return get_features_config()

    def _startup_candidates(
        self,
        accounts: list[AccountMeta],
        channel: str,
        features: Mapping[str, object],
    ) -> list[AccountMeta]:
        from aistudio_api.application.routing_eligibility import (
            AccountView,
            eligible_accounts,
        )

        views = [AccountView.from_account(account) for account in accounts]
        eligible = eligible_accounts(
            views,
            channel,
            "",
            features=features,
            groups=features.get("groups") or [],
        )
        by_id = {account.id: account for account in accounts}
        order = {account.id: index for index, account in enumerate(accounts)}
        return [
            by_id[item.account_id]
            for item in sorted(
                eligible,
                key=lambda item: (item.affinity, order.get(item.account_id, 10**9)),
            )
            if item.account_id in by_id
        ]

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
                state = self._state_from_slot(slot)
                if state is InstanceState.ACTIVE and not self._surface_active(
                    slot, record.channel
                ):
                    state = InstanceState.INVALID
                    record.last_error = "渠道页面缺失"
                record.state = state

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
        in_flight = 0
        if slot is not None:
            in_flight = int(getattr(slot, "in_flight", 0))
        channel = "build" if record.channel == "build" else "pg"
        return Instance(
            id=record.id,
            number=record.number,
            account_id=record.account_id,
            state=record.state,
            channel=channel,
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
