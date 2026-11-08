"""User-facing instance lifecycle (one account + one Context + one Page).

The manager maps configured ``instances`` specs (count/surface/min_tier) to
stable numbered records. Free and paid lanes are derived from the account tier,
never from the model name; a Free account can never occupy a Pro instance quota.

There is no background rebind/rebalance and no idle reclamation. A failed or
stale instance is only restarted explicitly. The one exception to "busy means
refuse" is the D-05 control-plane rebind: disabling the active account or
applying a preset retires the old instance with one bounded drain, force-closes
the old Page/Context when the timeout expires, and only then switches account.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aistudio_api.domain.instances import Instance, InstanceList, InstanceState
from aistudio_api.domain.model_access import (
    canonical_tier,
    instance_lane_for_tier,
    resolve_tier_selector,
    tier_matches_selector,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.gateway.instance_pool import (
    ActiveRequest,
    InstanceBusyError as InstancePoolBusyError,
    InstanceCapacityError,
    InstanceSpec,
    InstanceTierError,
    SlotState,
)
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("instances")

DEFAULT_DRAIN_TIMEOUT_S = 12.0


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


class InstanceRebindError(InstanceError):
    code = "instance_rebind_failed"
    status_code = 409


class InstanceRebindTargetError(InstanceError):
    code = "instance_rebind_no_target"
    status_code = 409


class InstanceStore(Protocol):
    def get_account(self, account_id: str) -> AccountMeta | None: ...

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ) -> Path | None: ...

    def list_accounts(self) -> list[AccountMeta]: ...

    def get_active_account(self) -> AccountMeta | None: ...


@dataclass
class _LifecycleTxn:
    """Shared reentrancy token inherited by shielded/child tasks."""

    depth: int = 0


@dataclass
class _Record:
    id: str
    number: int
    account_id: str
    state: InstanceState
    slot_id: str
    surface: str
    min_tier: str
    lane: str
    only_tier: str = ""
    max_tier: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    last_error: str | None = None

    @property
    def spec(self) -> InstanceSpec:
        return InstanceSpec(
            count=1,
            surface=self.surface,
            only_tier=self.only_tier,
            min_tier=self.min_tier,
            max_tier=self.max_tier,
        )

    def accepts_tier(self, tier: object) -> bool:
        return self.spec.accepts_tier(tier)


class InstanceManager:
    """Owns user-visible instances and their stable numbering."""

    def __init__(
        self,
        pool: Any,
        account_store: InstanceStore,
        *,
        clock: Any = time.time,
        on_active_changed: Any = None,
        drain_timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S,
    ) -> None:
        self._pool = pool
        self._accounts = account_store
        self._clock = clock
        self._on_active_changed = on_active_changed
        self._drain_timeout_s = max(0.0, float(drain_timeout_s))
        self._records: dict[str, _Record] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._lifecycle_txn: ContextVar[_LifecycleTxn | None] = ContextVar(
            f"instance-lifecycle-{id(self)}", default=None
        )
        self._lifecycle_depth = 0
        self._next_number = 1
        self._latest_id: str | None = None
        self._current_id: str | None = None

    @contextlib.asynccontextmanager
    async def lifecycle_scope(self) -> AsyncIterator[None]:
        """Hold the whole lifecycle transaction, re-entrant per transaction.

        Reentrancy is tracked with a ``ContextVar`` transaction token rather
        than ``asyncio.current_task()``: a shielded rollback runs in a *new*
        task but inherits the context of the switch task, so it must be able to
        reuse the scope instead of deadlocking on ``_lifecycle_lock``.

        Tasks created outside the scope (normal request/rebind tasks) do not
        carry the token and still block on the lock until the transaction ends.
        """
        txn = self._lifecycle_txn.get()
        if txn is not None:
            txn.depth += 1
            self._lifecycle_depth = txn.depth
            try:
                yield
            finally:
                txn.depth -= 1
                self._lifecycle_depth = txn.depth
            return

        txn = _LifecycleTxn()
        await self._lifecycle_lock.acquire()
        token = self._lifecycle_txn.set(txn)
        txn.depth = 1
        self._lifecycle_depth = 1
        try:
            yield
        finally:
            txn.depth = 0
            self._lifecycle_depth = 0
            self._lifecycle_txn.reset(token)
            self._lifecycle_lock.release()

    # ------------------------------------------------------------- public API

    @property
    def max_instances(self) -> int:
        return max(1, int(self._pool.max_contexts))

    @property
    def requests_open(self) -> bool:
        """False while a transactional engine switch is draining old slots."""
        return bool(getattr(self._pool, "requests_open", True))

    @property
    def engine_phase(self) -> str:
        return str(getattr(self._pool, "engine_phase", "idle"))

    async def startup(self) -> None:
        """Create one record per configured spec and start eligible accounts."""
        async with self._lifecycle_lock:
            if self._records:
                return
            active = self._accounts.get_active_account()
            accounts = self._ordered_accounts(active)
            used: set[str] = set()
            for spec in self._pool.specs:
                lane = spec.lane
                for _ in range(max(0, int(spec.count))):
                    try:
                        slot = await self._pool.add_slot(
                            InstanceSpec(
                                count=1,
                                surface=spec.surface,
                                only_tier=spec.only_tier,
                                min_tier=spec.min_tier,
                                max_tier=spec.max_tier,
                            ),
                            lane=lane,
                        )
                    except InstanceCapacityError:
                        log.info(
                            "[instances] startup stops at max_contexts=%d",
                            self.max_instances,
                        )
                        break
                    record = self._new_record(
                        slot_id=slot.slot_id,
                        surface=spec.surface,
                        only_tier=spec.only_tier,
                        min_tier=spec.min_tier,
                        max_tier=spec.max_tier,
                        lane=lane,
                    )
                    self._records[record.id] = record
                    account = self._pick_account(spec, accounts, used)
                    if account is None:
                        record.state = InstanceState.UNSTARTED
                        continue
                    used.add(account.id)
                    record.account_id = account.id
                    try:
                        await self._activate(record, account)
                    except InstanceError as exc:
                        log.warning(
                            "[instances] startup start failed account=%s slot=%s: %s",
                            account.id,
                            slot.slot_id,
                            exc,
                        )
                        record.state = InstanceState.INVALID
                        record.last_error = str(exc)
                else:
                    continue
                break
            self._select_current_for_active_account(active)
            self._repair_latest()
            self._repair_current()
            log.info(
                "[instances] startup complete records=%d active=%d",
                len(self._records),
                sum(
                    1
                    for record in self._records.values()
                    if record.state is InstanceState.ACTIVE
                ),
            )

    async def list_instances(self) -> InstanceList:
        self._sync_records()
        self._repair_latest()
        self._repair_current()
        instances = tuple(
            self._view(record)
            for record in sorted(self._records.values(), key=lambda item: item.number)
        )
        return InstanceList(
            max_instances=self.max_instances,
            latest_instance_id=self._latest_id,
            current_instance_id=self._current_id,
            instances=instances,
        )

    def eligible_instances(
        self, *, paid: bool, surface: str | None = None
    ) -> tuple[Instance, ...]:
        """Candidates for a model lane; sleeping/unstarted instances included."""
        self._sync_records()
        lane = "paid" if paid else "free"
        return tuple(
            self._view(record)
            for record in sorted(self._records.values(), key=lambda item: item.number)
            if record.lane == lane
            and (surface is None or record.surface == surface)
            and record.state
            in (
                InstanceState.UNSTARTED,
                InstanceState.SLEEPING,
                InstanceState.STARTING,
                InstanceState.ACTIVE,
            )
        )

    def lane_instances(self, *, paid: bool) -> tuple[Instance, ...]:
        """All records in one lane, including invalid/retiring ones."""
        self._sync_records()
        lane = "paid" if paid else "free"
        return tuple(
            self._view(record)
            for record in sorted(self._records.values(), key=lambda item: item.number)
            if record.lane == lane
        )

    @property
    def configured_surfaces(self) -> frozenset[str]:
        return frozenset(record.surface for record in self._records.values())

    @property
    def current_instance_id(self) -> str | None:
        return self._current_id

    async def create(
        self,
        account_id: str,
        surface: str | None = None,
        *,
        only_tier: str | None = None,
        min_tier: str | None = None,
        max_tier: str | None = None,
    ) -> Instance:
        account = self._validate_account(account_id)
        normalized = self._normalize_surface(surface)
        custom_selector = any(value for value in (only_tier, min_tier, max_tier))
        async with self._lifecycle_lock:
            if self._has_record_for_account(account.id):
                raise InstanceAlreadyExistsError(
                    f"账号 {account_id} 已存在实例（同账号 chat/build 不并存）"
                )
            if custom_selector:
                selector = resolve_tier_selector(
                    only_tier=only_tier or None,
                    min_tier=min_tier or None,
                    max_tier=max_tier or None,
                )
                if not tier_matches_selector(account.tier, selector):
                    raise InstanceInvalidAccountError(
                        f"账号 tier={account.tier} 不满足实例 tier 区间 "
                        f"only={selector.only_tier or '-'} "
                        f"min={selector.min_tier or '-'} "
                        f"max={selector.max_tier or '-'}"
                    )
                spec = InstanceSpec(
                    count=1,
                    surface=normalized,
                    only_tier=selector.only_tier or "",
                    min_tier=selector.min_tier,
                    max_tier=selector.max_tier,
                )
                lane = selector.lane
            else:
                spec = self._matching_spec(account, normalized)
                if spec is None:
                    raise InstanceNotReadyError(
                        f"没有匹配账号 tier={account.tier} surface={normalized} 的实例配置"
                    )
                lane = instance_lane_for_tier(account.tier)
            try:
                slot = await self._pool.add_slot(spec, lane=lane)
            except InstanceCapacityError as exc:
                raise InstanceCapacityExhaustedError(str(exc)) from exc
            record = self._new_record(
                slot_id=slot.slot_id,
                surface=spec.surface,
                only_tier=spec.only_tier,
                min_tier=spec.min_tier,
                max_tier=spec.max_tier,
                lane=lane,
            )
            record.account_id = account.id
            record.state = InstanceState.STARTING
            self._records[record.id] = record
            self._schedule(record.id, self._start_created(record, account))
            return self._view(record)

    async def close(self, instance_id: str | None = None) -> Instance | None:
        if instance_id is None:
            await self.close_all()
            return None
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            if record.state is InstanceState.RECLAIMING:
                return self._view(record)
            if record.state is InstanceState.STARTING:
                task = self._tasks.pop(record.id, None)
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                record.state = InstanceState.RECLAIMING
                view = self._view(record)
                with self._suppress_errors():
                    await self._pool.close_slot(record.slot_id)
                self._records.pop(record.id, None)
                self._repair_latest()
                self._repair_current()
                return view
            slot = self._pool.get(record.slot_id)
            if slot is not None and int(slot.active_requests) > 0:
                raise InstanceBusyError("该实例仍有在途请求")
            record.state = InstanceState.RECLAIMING
            try:
                await self._pool.close_slot(record.slot_id)
            except InstanceCapacityError as exc:
                record.state = InstanceState.ACTIVE
                raise InstanceBusyError(str(exc)) from exc
            view = self._view(record)
            self._records.pop(record.id, None)
            self._repair_latest()
            self._repair_current()
            return view

    async def switch_to(self, instance_id: str, surface: str | None = None) -> Instance:
        """Manual in-place surface switch; close old Page/Context first."""
        target = self._normalize_surface(surface)
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            account = self._require_account_for_record(record)
            if target == record.surface:
                if record.state is not InstanceState.ACTIVE:
                    await self._activate(record, account)
                self._current_id = record.id
                self._set_active_account(account.id)
                return self._view(record)
            if record.state not in (InstanceState.ACTIVE, InstanceState.INVALID):
                raise InstanceNotReadyError("实例尚未就绪，不能切换 surface")
            slot = self._pool.get(record.slot_id)
            if slot is not None and int(slot.active_requests) > 0:
                raise InstanceBusyError("该实例仍有在途请求")
            if not any(spec.surface == target for spec in self._pool.specs):
                raise InstanceNotReadyError(f"没有配置 surface={target} 的实例")
            old_surface = record.surface
            try:
                await self._activate(record, account, surface=target)
            except InstanceError as exc:
                try:
                    await self._activate(record, account, surface=old_surface)
                except InstanceError as rollback_exc:
                    record.state = InstanceState.INVALID
                    record.last_error = f"切换失败且回滚失败: {exc}; {rollback_exc}"
                    raise InstanceNotReadyError(record.last_error) from exc
                raise InstanceNotReadyError(f"切换失败，已回滚: {exc}") from exc
            self._current_id = record.id
            self._set_active_account(account.id)
            return self._view(record)

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        account = self._validate_account(account_id)
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            if record.account_id == account.id:
                return self._view(record)
            if instance_lane_for_tier(account.tier) != record.lane:
                raise InstanceInvalidAccountError(
                    "Free/paid 实例池互相隔离，不能跨池改绑账号"
                )
            if not record.accepts_tier(account.tier):
                raise InstanceInvalidAccountError(
                    f"账号 tier={account.tier} 不满足实例 tier 区间 "
                    f"only={record.only_tier or '-'} min={record.min_tier or '-'} "
                    f"max={record.max_tier or '-'}"
                )
            other = self._record_for_account(account.id, exclude_id=record.id)
            if other is not None:
                raise InstanceAlreadyExistsError(
                    f"账号 {account_id} 已绑定实例 #{other.number}，"
                    f"请先在实例 #{other.number} 换绑其他账号或关闭该实例后"
                    "再绑定此槽位"
                )
            previous = (
                self._accounts.get_account(record.account_id)
                if record.account_id
                else None
            )
            was_current = self._current_id == record.id
            slot = self._pool.get(record.slot_id)
            if slot is not None and int(slot.active_requests) > 0:
                # Bounded safe drain/retire: stop new work, drain once, then
                # force-close the old Page/Context before the new account starts.
                await self._retire_record(record)
            try:
                await self._activate(record, account)
            except InstanceError as exc:
                if previous is None:
                    record.account_id = ""
                    record.state = InstanceState.UNSTARTED
                    record.last_error = str(exc)
                    self._repair_latest()
                    self._repair_current()
                    raise InstanceNotReadyError(f"绑定失败: {exc}") from exc
                try:
                    await self._activate(record, previous)
                except InstanceError as rollback_exc:
                    record.state = InstanceState.INVALID
                    record.last_error = f"改绑失败且回滚失败: {exc}; {rollback_exc}"
                    raise InstanceNotReadyError(record.last_error) from exc
                raise InstanceNotReadyError(f"改绑失败，已回滚: {exc}") from exc
            if was_current:
                self._current_id = record.id
                self._set_active_account(account.id)
            return self._view(record)

    async def restart(self, instance_id: str) -> Instance:
        """Explicit manual restart for INVALID/stale instances."""
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            account = self._require_account_for_record(record)
            slot = self._pool.get(record.slot_id)
            if slot is not None and int(slot.active_requests) > 0:
                raise InstanceBusyError("该实例仍有在途请求")
            await self._activate(record, account, force=True)
            if self._current_id is None:
                self._current_id = record.id
            return self._view(record)

    async def sleep(self, instance_id: str) -> Instance:
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            try:
                await self._pool.sleep(record.slot_id)
            except InstanceCapacityError as exc:
                raise InstanceBusyError(str(exc)) from exc
            record.state = InstanceState.SLEEPING
            record.last_error = None
            return self._view(record)

    async def wake(self, instance_id: str) -> Instance:
        async with self._lifecycle_lock:
            record = self._record(instance_id)
            if record.state is InstanceState.ACTIVE:
                return self._view(record)
            if record.state not in (
                InstanceState.UNSTARTED,
                InstanceState.SLEEPING,
            ):
                raise InstanceNotReadyError("仅 UNSTARTED/SLEEPING 实例可唤醒")
            account = self._require_account_for_record(record, allow_disabled=False)
            await self._activate(record, account)
            return self._view(record)

    async def wake_eligible(
        self, *, paid: bool | None = None, surface: str | None = None
    ) -> list[Instance]:
        """Message arrival wakes every eligible sleeping/unstarted instance."""
        started: list[Instance] = []
        async with self._lifecycle_lock:
            used = {
                record.account_id
                for record in self._records.values()
                if record.account_id
            }
            for record in sorted(self._records.values(), key=lambda item: item.number):
                if record.state not in (
                    InstanceState.UNSTARTED,
                    InstanceState.SLEEPING,
                ):
                    continue
                if paid is not None and (record.lane == "paid") != paid:
                    continue
                if surface is not None and record.surface != surface:
                    continue
                account = self._account_for_record(
                    record, allow_disabled=False, raise_if_missing=False
                )
                if account is None:
                    account = self._pick_account(
                        record.spec,
                        self._ordered_accounts(self._accounts.get_active_account()),
                        used,
                    )
                if account is None:
                    continue
                used.add(account.id)
                try:
                    await self._activate(record, account)
                except InstanceError as exc:
                    record.state = InstanceState.INVALID
                    record.last_error = str(exc)
                    continue
                started.append(self._view(record))
        return started

    async def rebind_account(
        self,
        target_account_id: str,
        *,
        previous_account_id: str | None = None,
        reason: str = "d-05",
    ) -> Instance:
        """D-05 control-plane rebind to the next enabled account.

        The old instance enters RETIRING, rejects new requests, drains once with
        a bounded wait, force-closes Page/Context on timeout, and only then
        activates the target account. The persisted active account is rewritten
        only after the browser switch succeeds.
        """
        target = self._validate_account(target_account_id)
        target_lane = instance_lane_for_tier(target.tier)
        async with self._lifecycle_lock:
            previous_id = previous_account_id
            if previous_id is None and self._current_id:
                current = self._records.get(self._current_id)
                if current is not None:
                    previous_id = current.account_id
            affected: list[_Record] = []
            for record in self._records.values():
                if not record.account_id:
                    continue
                account = self._accounts.get_account(record.account_id)
                disabled = account is None or not account.enabled or account.expired
                if record.account_id == previous_id or disabled:
                    affected.append(record)

            target_record = self._find_target_record(target, target_lane)
            if target_record is None:
                raise InstanceNotReadyError(
                    f"没有可用于账号 {target_account_id} 的 {target_lane} 实例"
                )

            retired: list[tuple[_Record, AccountMeta | None]] = []
            for record in affected:
                if record.id == target_record.id:
                    continue
                previous_account = (
                    self._accounts.get_account(record.account_id)
                    if record.account_id
                    else None
                )
                await self._retire_record(record)
                retired.append((record, previous_account))

            if target_record in affected or target_record.account_id != target.id:
                previous_account = (
                    self._accounts.get_account(target_record.account_id)
                    if target_record.account_id
                    else None
                )
                if target_record.account_id:
                    retired.append((target_record, previous_account))
                await self._retire_record(target_record)

            try:
                await self._activate(target_record, target)
            except InstanceError as exc:
                log.error(
                    "[instances] %s rebind failed %s -> %s: %s",
                    reason,
                    previous_id,
                    target.id,
                    exc,
                )
                restored = False
                target_restored = False
                for record, previous_account in retired:
                    if previous_account is None:
                        continue
                    try:
                        await self._activate(record, previous_account)
                        restored = True
                        if record.id == target_record.id:
                            target_restored = True
                    except InstanceError as restore_exc:
                        record.state = InstanceState.INVALID
                        record.last_error = f"rebind 失败且恢复失败: {restore_exc}"
                if not target_restored:
                    target_record.account_id = ""
                    target_record.state = InstanceState.UNSTARTED
                self._repair_current()
                suffix = "；已恢复旧账号运行态" if restored else ""
                raise InstanceRebindError(
                    f"账号 rebind 到 {target.id} 失败: {exc}{suffix}"
                ) from exc

            self._current_id = target_record.id
            self._repair_latest()
            self._set_active_account(target.id)
            log.info(
                "[instances] %s rebind complete %s -> %s slot=%s",
                reason,
                previous_id,
                target.id,
                target_record.slot_id,
            )
            return self._view(target_record)

    async def begin_request(self, instance_id: str) -> ActiveRequest:
        record = self._record(instance_id)
        if record.state is not InstanceState.ACTIVE:
            raise InstanceNotReadyError("实例尚未就绪")
        try:
            return await self._pool.begin_request(record.slot_id)
        except InstanceCapacityError as exc:
            raise InstanceBusyError(str(exc)) from exc

    def current_account_id(self) -> str | None:
        record = self._records.get(self._current_id or "")
        return record.account_id if record is not None else None

    def pool_slots(self) -> tuple[Any, ...]:
        """Expose pooled runtime slots for keepalive cookie write-back."""
        self._sync_records()
        return tuple(self._pool.slots())

    def discovery_sessions(self) -> tuple[Any, ...]:
        """Return READY pool sessions ordered for read-only model discovery.

        Paid lane first, then Free; within each lane idle slots
        (``active_requests == 0``) come before busy ones.  Discovery only
        performs a standalone ListModels XHR on an existing page: it never
        acquires ``active_requests`` and never closes, retires, or re-lanes a
        slot.
        """
        self._sync_records()
        ready = [
            slot
            for slot in self._pool.slots()
            if slot.state is SlotState.READY
            and slot.session is not None
            and not slot.page_closed()
        ]
        ready.sort(
            key=lambda slot: (
                0 if slot.lane == "paid" else 1,
                0 if int(getattr(slot, "active_requests", 0) or 0) == 0 else 1,
            )
        )
        return tuple(slot.session for slot in ready)

    def ready_slot_for_account(self, account_id: str) -> Any | None:
        """Return the READY pool slot already bound to ``account_id``, if any.

        Used by manual account activation to reuse the existing single page
        instead of switching/opening the control-plane page for the same
        account (same-account double-page guard).  It only syncs the current
        instance pointer; it never acquires, closes, or retires a slot.
        """
        if not account_id:
            return None
        self._sync_records()
        found: Any | None = None
        for slot in self._pool.slots():
            if (
                slot.state is SlotState.READY
                and getattr(slot, "account_id", "") == account_id
                and slot.session is not None
                and not slot.page_closed()
            ):
                found = slot
                break
        if found is None:
            return None
        for record in self._records.values():
            if record.account_id == account_id and record.state is InstanceState.ACTIVE:
                self._current_id = record.id
                break
        return found

    async def sync_account_tier(self, account_id: str, tier: str) -> int:
        """Synchronize a manually detected tier into pool and instance records.

        Same-lane tier changes are mirrored in place (slot + session context)
        without restarting a page. A cross-lane change (free slot now holding a
        paid account, or the reverse) retires and unbinds the old slot so it can
        never serve an ineligible account, refills that slot with another
        account from its configured lane when one is available, and binds the
        restored account to an idle slot in its new lane. Returns the number of
        slots the pool touched.
        """
        if not account_id:
            return 0
        canonical = canonical_tier(tier) or str(tier or "")
        target_lane = instance_lane_for_tier(canonical)
        async with self._lifecycle_lock:
            self._sync_records()
            affected = [
                record
                for record in self._records.values()
                if record.account_id == account_id
            ]
            sync = getattr(self._pool, "sync_account_tier", None)
            if not callable(sync):
                return 0
            result = sync(
                account_id,
                canonical,
                drain_timeout_s=self._drain_timeout_s,
            )
            if inspect.isawaitable(result):
                result = await result
            updated = result if isinstance(result, int) else 0

            evicted: list[_Record] = []
            for record in affected:
                slot = self._pool.get(record.slot_id)
                if slot is None:
                    record.account_id = ""
                    record.state = InstanceState.INVALID
                    record.last_error = "runtime missing"
                    continue
                if getattr(slot, "account_id", "") == account_id:
                    record.account_id = account_id
                    record.state = self._state_from_slot(slot)
                else:
                    record.account_id = ""
                    record.state = self._state_from_slot(slot)
                    record.last_error = None
                    evicted.append(record)

            account = self._accounts.get_account(account_id)
            already_bound = any(
                record.account_id == account_id for record in self._records.values()
            )
            if (
                not already_bound
                and account is not None
                and account.enabled
                and not account.expired
            ):
                target = self._find_bindable_record(target_lane, canonical)
                if target is not None:
                    try:
                        await self._activate(target, account)
                    except InstanceError as exc:
                        target.state = InstanceState.INVALID
                        target.last_error = str(exc)
                        log.warning(
                            "[instances] tier sync could not bind account=%s "
                            "slot=%s tier=%s: %s",
                            account_id,
                            target.slot_id,
                            canonical,
                            exc,
                        )

            for record in evicted:
                if record.account_id:
                    continue
                used = {
                    item.account_id
                    for item in self._records.values()
                    if item.account_id
                }
                used.add(account_id)
                replacement = self._pick_account(
                    record.spec,
                    self._ordered_accounts(self._accounts.get_active_account()),
                    used,
                )
                if replacement is None:
                    log.info(
                        "[instances] tier sync left slot=%s unstarted: no "
                        "eligible replacement in lane=%s",
                        record.slot_id,
                        record.lane,
                    )
                    continue
                try:
                    await self._activate(record, replacement)
                except InstanceError as exc:
                    record.state = InstanceState.INVALID
                    record.last_error = str(exc)
                    log.warning(
                        "[instances] tier sync replacement failed slot=%s "
                        "account=%s: %s",
                        record.slot_id,
                        replacement.id,
                        exc,
                    )

            self._repair_latest()
            self._repair_current()
            log.info(
                "[instances] tier sync account=%s tier=%s lane=%s slots=%d evicted=%d",
                account_id,
                canonical,
                target_lane,
                updated,
                len(evicted),
            )
            return updated

    def discovery_session(self) -> Any | None:
        """Return the preferred READY pool session for discovery."""
        sessions = self.discovery_sessions()
        return sessions[0] if sessions else None

    def records(self) -> tuple[Any, ...]:
        """Read-only record view for status endpoints."""
        return tuple(sorted(self._records.values(), key=lambda item: item.number))

    async def destroy_runtimes(self) -> int:
        """Close every slot runtime after an engine drain; keep slot topology."""
        async with self.lifecycle_scope():
            return await self._destroy_runtimes_locked()

    async def _destroy_runtimes_locked(self) -> int:
        self._sync_records()
        closed = 0
        for record in list(self._records.values()):
            slot = self._pool.get(record.slot_id)
            if slot is None:
                continue
            try:
                await self._pool.retire_runtime(record.slot_id, timeout_s=0.0)
                closed += 1
            except Exception as exc:
                log.warning(
                    "[instances] destroy runtime failed slot=%s: %s",
                    record.slot_id,
                    exc,
                )
            record.state = InstanceState.UNSTARTED
            record.last_error = None
        return closed

    async def rebuild_runtimes(
        self, *, concurrency: int = 2, timeout_s: float = 120.0
    ) -> tuple[int, int]:
        """Rebuild every slot runtime under the lifecycle lock.

        Account binding is preserved; a disabled/deleted bound account leaves
        the slot UNSTARTED instead of silently rebinding another account (Q-05).
        """
        async with self.lifecycle_scope():
            return await self._rebuild_runtimes_locked(
                concurrency=concurrency, timeout_s=timeout_s
            )

    async def _rebuild_runtimes_locked(
        self, *, concurrency: int = 2, timeout_s: float = 120.0
    ) -> tuple[int, int]:
        self._sync_records()
        records = sorted(self._records.values(), key=lambda item: item.number)
        total = len(records)
        if not records:
            return 0, 0
        semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        rebuilt = 0
        errors: list[str] = []
        used: set[str] = {record.account_id for record in records if record.account_id}

        async def _rebuild(record: _Record) -> None:
            nonlocal rebuilt
            async with semaphore:
                bound_account_id = record.account_id
                if bound_account_id:
                    # Preserve the binding. A disabled/deleted account leaves
                    # the slot UNSTARTED; never silently pick another account.
                    account = self._account_for_record(
                        record, allow_disabled=False, raise_if_missing=False
                    )
                    if account is None or (
                        self._accounts.get_auth_path_optional(
                            account.id, require_exists=True
                        )
                        is None
                    ):
                        record.state = InstanceState.UNSTARTED
                        record.last_error = (
                            f"bound account unavailable: {bound_account_id}"
                        )
                        return
                else:
                    account = self._pick_account(
                        record.spec,
                        self._ordered_accounts(self._accounts.get_active_account()),
                        used,
                    )
                    if account is None:
                        record.state = InstanceState.UNSTARTED
                        record.last_error = None
                        return
                used.add(account.id)
                try:
                    await asyncio.wait_for(
                        self._activate(record, account, force=True),
                        timeout=max(1.0, float(timeout_s)),
                    )
                except Exception as exc:
                    record.account_id = account.id
                    record.state = InstanceState.INVALID
                    record.last_error = str(exc)
                    errors.append(f"{record.id}: {exc}")
                    return
                rebuilt += 1

        await asyncio.gather(*(_rebuild(record) for record in records))
        self._repair_latest()
        self._repair_current()
        if errors:
            raise InstanceRebindError(
                "engine rebuild partially failed: " + "; ".join(errors[:3])
            )
        return rebuilt, total

    def active_account_ids(self) -> set[str]:
        return {
            record.account_id
            for record in self._records.values()
            if record.account_id and record.state is InstanceState.ACTIVE
        }

    def instance_account_ids(self) -> set[str]:
        """Every account currently bound to a slot, in any lifecycle state.

        UNSTARTED/RECLAIMING slots can still hold a bound account id while a
        rebind or startup is in flight; excluding them lets rebind selection
        pick the same account twice.
        """
        return {
            record.account_id for record in self._records.values() if record.account_id
        }

    def instance_active_requests(self, instance_id: str) -> int:
        """In-flight request count for one instance (0 when unknown)."""
        record = self._records.get(instance_id)
        if record is None:
            return 0
        slot = self._pool.get(record.slot_id)
        return int(slot.active_requests) if slot is not None else 0

    async def close_all(self) -> None:
        async with self._lifecycle_lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._pool.close()
            self._records.clear()
            self._latest_id = None
            self._current_id = None

    async def wait_idle(self) -> None:
        """Test/shutdown helper: wait for STARTING lifecycle tasks."""
        while self._tasks:
            tasks = list(self._tasks.values())
            await asyncio.gather(*tasks, return_exceptions=True)
            for key, task in list(self._tasks.items()):
                if task.done():
                    self._tasks.pop(key, None)

    # ---------------------------------------------------------------- helpers

    async def _start_created(self, record: _Record, account: AccountMeta) -> None:
        try:
            await self._activate(record, account)
        except asyncio.CancelledError:
            raise
        except InstanceError:
            with self._suppress_errors():
                await self._pool.close_slot(record.slot_id)
            self._records.pop(record.id, None)
            self._repair_latest()
            self._repair_current()

    def _schedule(self, key: str, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks[key] = task

        def _drop(done: asyncio.Task[None]) -> None:
            if self._tasks.get(key) is done:
                self._tasks.pop(key, None)

        task.add_done_callback(_drop)

    def _ordered_accounts(self, active: AccountMeta | None) -> list[AccountMeta]:
        accounts = list(self._accounts.list_accounts())
        if active is None:
            return accounts
        return [account for account in accounts if account.id == active.id] + [
            account for account in accounts if account.id != active.id
        ]

    def _pick_account(
        self,
        spec: InstanceSpec,
        accounts: list[AccountMeta],
        used: set[str],
    ) -> AccountMeta | None:
        for account in accounts:
            if account.id in used:
                continue
            if not account.enabled or account.expired:
                continue
            if not spec.accepts_tier(account.tier):
                continue
            if (
                self._accounts.get_auth_path_optional(account.id, require_exists=True)
                is None
            ):
                continue
            return account
        return None

    def _require_account_for_record(
        self, record: _Record, *, allow_disabled: bool = False
    ) -> AccountMeta:
        account = self._account_for_record(
            record, allow_disabled=allow_disabled, raise_if_missing=False
        )
        if account is None:
            raise InstanceInvalidAccountError("实例账号不可用，请重新选择账号")
        return account

    def _account_for_record(
        self,
        record: _Record,
        *,
        allow_disabled: bool = False,
        raise_if_missing: bool = True,
    ) -> AccountMeta | None:
        account = (
            self._accounts.get_account(record.account_id) if record.account_id else None
        )
        if account is not None and (allow_disabled or account.enabled):
            return account
        if raise_if_missing:
            raise InstanceInvalidAccountError("实例账号不可用，请重新选择账号")
        return None

    def _validate_account(self, account_id: str) -> AccountMeta:
        account = self._accounts.get_account(account_id)
        if account is None:
            raise InstanceAccountNotFoundError("账号不存在")
        if not account.enabled:
            raise InstanceInvalidAccountError("账号已停用")
        if account.expired:
            raise InstanceInvalidAccountError("账号已失效，请重新登录")
        if (
            self._accounts.get_auth_path_optional(account.id, require_exists=True)
            is None
        ):
            raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
        return account

    def _matching_spec(self, account: AccountMeta, surface: str) -> InstanceSpec | None:
        for spec in self._pool.specs:
            if spec.surface != surface:
                continue
            if spec.accepts_tier(account.tier):
                return spec
        return None

    async def _activate(
        self,
        record: _Record,
        account: AccountMeta,
        *,
        surface: str | None = None,
        force: bool = False,
    ) -> _Record:
        target_surface = surface or record.surface
        if not record.accepts_tier(account.tier):
            raise InstanceInvalidAccountError(
                f"账号 tier={account.tier} 不满足实例 tier 区间 "
                f"only={record.only_tier or '-'} min={record.min_tier or '-'} "
                f"max={record.max_tier or '-'}"
            )
        auth_file = self._accounts.get_auth_path_optional(
            account.id, require_exists=True
        )
        if auth_file is None:
            raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
        previous_state = record.state
        record.state = InstanceState.STARTING
        record.last_error = None
        try:
            await self._pool.activate(
                record.slot_id,
                account_id=account.id,
                auth_file=str(auth_file),
                auth_user=account.auth_user,
                tier=account.tier,
                account_lane=instance_lane_for_tier(account.tier),
                surface=target_surface,
                force=force,
            )
        except InstanceTierError as exc:
            record.state = InstanceState.INVALID
            record.last_error = str(exc)
            raise InstanceInvalidAccountError(str(exc)) from exc
        except InstancePoolBusyError as exc:
            record.state = previous_state
            record.last_error = str(exc)
            raise InstanceBusyError(str(exc)) from exc
        except InstanceCapacityError as exc:
            record.state = InstanceState.INVALID
            record.last_error = str(exc)
            raise InstanceNotReadyError(str(exc)) from exc
        except Exception as exc:
            record.state = InstanceState.INVALID
            record.last_error = str(exc)
            raise InstanceNotReadyError(str(exc)) from exc
        record.account_id = account.id
        record.surface = target_surface
        record.state = InstanceState.ACTIVE
        record.started_at = self._clock()
        self._latest_id = record.id
        if self._current_id is None:
            self._current_id = record.id
        return record

    async def _retire_record(self, record: _Record) -> bool:
        timed_out = False
        if record.slot_id:
            try:
                timed_out = await self._pool.retire_runtime(
                    record.slot_id, timeout_s=self._drain_timeout_s
                )
            except InstanceCapacityError as exc:
                log.warning(
                    "[instances] retire skipped slot=%s: %s",
                    record.slot_id,
                    exc,
                )
        record.state = InstanceState.SLEEPING
        record.account_id = ""
        record.last_error = None
        return timed_out

    def _find_bindable_record(self, lane: str, tier: object) -> _Record | None:
        """Return an unbound idle record in ``lane`` that accepts ``tier``."""
        for record in sorted(self._records.values(), key=lambda item: item.number):
            if record.lane != lane or record.account_id:
                continue
            if record.state not in (
                InstanceState.UNSTARTED,
                InstanceState.SLEEPING,
            ):
                continue
            if not record.accepts_tier(tier):
                continue
            return record
        return None

    def _find_target_record(
        self, target: AccountMeta, target_lane: str
    ) -> _Record | None:
        for record in self._records.values():
            if record.account_id == target.id and record.accepts_tier(target.tier):
                return record
        for record in self._records.values():
            if (
                record.lane == target_lane
                and not record.account_id
                and record.accepts_tier(target.tier)
                and record.state in (InstanceState.UNSTARTED, InstanceState.SLEEPING)
            ):
                return record
        for record in self._records.values():
            if record.lane != target_lane or not record.account_id:
                continue
            if not record.accepts_tier(target.tier):
                continue
            account = self._accounts.get_account(record.account_id)
            if account is None or not account.enabled:
                return record
        return None

    def _set_active_account(self, account_id: str) -> None:
        callback = self._on_active_changed
        if callback is None:
            callback = getattr(self._accounts, "set_active_account", None)
        if callable(callback):
            callback(account_id)

    def _new_record(
        self,
        *,
        slot_id: str,
        surface: str,
        only_tier: str,
        min_tier: str,
        max_tier: str,
        lane: str,
    ) -> _Record:
        number = self._next_number
        self._next_number += 1
        return _Record(
            id=f"inst-{number}",
            number=number,
            account_id="",
            state=InstanceState.UNSTARTED,
            slot_id=slot_id,
            surface=surface,
            min_tier=min_tier,
            lane=lane,
            only_tier=only_tier,
            max_tier=max_tier,
            created_at=self._clock(),
        )

    def _record(self, instance_id: str) -> _Record:
        record = self._records.get(instance_id)
        if record is None:
            raise InstanceNotFoundError("实例不存在")
        return record

    def _record_for_account(
        self, account_id: str, *, exclude_id: str | None = None
    ) -> _Record | None:
        for record in self._records.values():
            if (
                record.account_id == account_id
                and record.id != exclude_id
                and record.state
                not in (
                    InstanceState.RECLAIMING,
                    InstanceState.RETIRING,
                    InstanceState.INVALID,
                )
            ):
                return record
        return None

    def _has_record_for_account(
        self, account_id: str, *, exclude_id: str | None = None
    ) -> bool:
        return self._record_for_account(account_id, exclude_id=exclude_id) is not None

    @staticmethod
    def _normalize_surface(surface: str | None) -> str:
        if surface in (None, "", "chat"):
            return "chat"
        if surface == "build":
            return "build"
        raise InstanceNotReadyError(f"未知 surface: {surface}")

    def _sync_records(self) -> None:
        for record in self._records.values():
            slot = self._pool.get(record.slot_id)
            if slot is None:
                if record.state is not InstanceState.RECLAIMING:
                    record.state = InstanceState.INVALID
                    record.last_error = "runtime missing"
                continue
            if slot.state is SlotState.READY and slot.page_closed():
                slot.state = SlotState.INVALID
                slot.last_error = "instance page closed"
            if record.state is InstanceState.RECLAIMING:
                continue
            record.state = self._state_from_slot(slot)
            if slot.last_error:
                record.last_error = slot.last_error

    @staticmethod
    def _state_from_slot(slot: Any) -> InstanceState:
        state = slot.state
        if state is SlotState.STARTING:
            return InstanceState.STARTING
        if state is SlotState.READY:
            return InstanceState.ACTIVE
        if state is SlotState.SLEEPING:
            return InstanceState.SLEEPING
        if state is SlotState.RETIRING:
            return InstanceState.RECLAIMING
        if state is SlotState.INVALID:
            return InstanceState.INVALID
        return InstanceState.UNSTARTED

    def _view(self, record: _Record) -> Instance:
        slot = self._pool.get(record.slot_id)
        in_flight = int(slot.active_requests) if slot is not None else 0
        return Instance(
            id=record.id,
            number=record.number,
            account_id=record.account_id,
            state=record.state,
            surface=record.surface,
            min_tier=record.spec.selector.min_tier,
            only_tier=record.only_tier,
            max_tier=record.max_tier,
            in_flight=in_flight,
            latest=record.id == self._latest_id,
            tier=slot.tier if slot is not None else "",
            last_used=slot.last_used if slot is not None else 0.0,
        )

    def _select_current_for_active_account(self, active: AccountMeta | None) -> None:
        if active is None:
            return
        for record in self._records.values():
            if record.account_id == active.id and record.state is InstanceState.ACTIVE:
                self._current_id = record.id
                return

    def _repair_current(self) -> None:
        current = self._records.get(self._current_id or "")
        if current is not None and current.state is InstanceState.ACTIVE:
            return
        active = [
            record
            for record in self._records.values()
            if record.state is InstanceState.ACTIVE
        ]
        self._current_id = (
            max(active, key=lambda item: item.started_at).id if active else None
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
            max(candidates, key=lambda item: item.started_at).id if candidates else None
        )

    @staticmethod
    def _suppress_errors():
        import contextlib

        return contextlib.suppress(Exception)


__all__ = [
    "DEFAULT_DRAIN_TIMEOUT_S",
    "InstanceAccountNotFoundError",
    "InstanceAlreadyExistsError",
    "InstanceBusyError",
    "InstanceCapacityExhaustedError",
    "InstanceError",
    "InstanceInvalidAccountError",
    "InstanceManager",
    "InstanceNotFoundError",
    "InstanceNotReadyError",
    "InstanceRebindError",
    "InstanceRebindTargetError",
]
