"""账号保活调度器：24h Cookie 回写、元数据刷新与可选控制页活动。

P6/D08：保留 auth_update 与 metadata_refresh；删除后台 auto_switch、
InstanceSwitcher.rebalance 与 legacy rotator 兜底。所有 job 共用同一把
串行锁，busy 时立即返回且不推进 next_due；失败按 job 短周期重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from aistudio_api.domain.errors import is_region_restricted_message
from aistudio_api.infrastructure.account.account_info_refresher import (
    REGION_DEFER_RETRY_SECONDS,
    RefreshInProgressError,
    resolve_arbitration_kind,
    skip_log_due,
)
from aistudio_api.infrastructure.account.benefit_tier import (
    parse_benefit_tier_envelope,
)
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_keepalive_settings,
)
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

log = get_logger("keepalive")

TASKS = ("metadata_refresh", "auth_update", "active_activity")
NO_ACTIVE_PAGE_RETRY_SECONDS = 60.0
FAILURE_RETRY_SECONDS = 300.0
REFRESH_IN_PROGRESS_RETRY_SECONDS = 60.0
MAX_AUTH_CONSECUTIVE_FAILURES = 3

# External exit mode: maintenance calls share a small per-exit budget, so one
# keepalive round must not attempt every due account at once.  These caps stop
# a round early and leave the rest due for the next 300s retry.
DEFAULT_METADATA_ATTEMPT_LIMIT = 12
DEFAULT_AUTH_ATTEMPT_LIMIT = 8
CONSECUTIVE_NO_CAPACITY_LIMIT = 3
REGION_403_STATUS = 403

DEFAULT_AUTH_UPDATE_HOURS = 24.0
DEFAULT_METADATA_REFRESH_HOURS = 24.0
DEFAULT_ACTIVE_ACTIVITY_SECONDS = 60.0


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if number > 0 else default


# The refresher reports a deferred email sub-probe (tier itself succeeded) as
# this error prefix, because the tier probe already produced a successful
# result.  Q-10: keepalive must still treat the account as deferred so the
# next metadata pass happens after 300s instead of a full 24h.
_DEFERRED_EMAIL_PREFIX = "deferred_no_capacity"


def _is_deferred_result(result: object) -> bool:
    if bool(getattr(result, "deferred", False)):
        return True
    error = getattr(result, "error", None)
    return isinstance(error, str) and error.startswith(_DEFERRED_EMAIL_PREFIX)


def _is_no_capacity_result(result: object) -> bool:
    """True when a deferred result is an exit-budget ``no_capacity`` deferral."""
    if not _is_deferred_result(result):
        return False
    reason = str(getattr(result, "skip_reason", "") or "")
    if reason == "no_capacity":
        return True
    error = getattr(result, "error", None)
    return isinstance(error, str) and error.startswith(_DEFERRED_EMAIL_PREFIX)


def _rotate_after(
    items: list[Any], last_id: str | None, order_ids: list[str] | None = None
) -> list[Any]:
    """Order ``items`` starting right after the account that ended the last round.

    External maintenance rounds attempt only a bounded batch, so a fixed
    store-order head would let persistently region-deferred accounts occupy
    every round and starve the rest.  The in-memory cursor (last attempted
    account id) rotates each round past that head.  ``order_ids`` is the stable
    store order used as the rotation clock when ``items`` is a due-only subset;
    it defaults to the ids of ``items`` itself.  An unknown/missing cursor
    restarts from the original order.
    """
    if not last_id or not items:
        return items
    if order_ids is None:
        order_ids = [str(getattr(item, "id", "") or "") for item in items]
    positions = {account_id: index for index, account_id in enumerate(order_ids)}
    anchor = positions.get(last_id)
    if anchor is None:
        return items
    total = len(order_ids)

    def rank(item: Any) -> int:
        position = positions.get(str(getattr(item, "id", "") or ""), -1)
        if position < 0:
            return total
        return (position - anchor - 1) % total

    return sorted(items, key=rank)


@dataclass(slots=True)
class KeepaliveSettings:
    """The two 24h jobs plus the optional (default off) control-page activity."""

    auth_update: bool = True
    auth_update_hours: float = DEFAULT_AUTH_UPDATE_HOURS
    metadata_refresh: bool = True
    metadata_refresh_hours: float = DEFAULT_METADATA_REFRESH_HOURS
    metadata_refresh_on_startup: bool = True
    active_activity: bool = False
    active_activity_seconds: float = DEFAULT_ACTIVE_ACTIVITY_SECONDS

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> KeepaliveSettings:
        return cls(
            auth_update=_as_bool(raw.get("auth_update"), True),
            auth_update_hours=_as_float(
                raw.get("auth_update_hours"), DEFAULT_AUTH_UPDATE_HOURS
            ),
            metadata_refresh=_as_bool(raw.get("metadata_refresh"), True),
            metadata_refresh_hours=_as_float(
                raw.get("metadata_refresh_hours"), DEFAULT_METADATA_REFRESH_HOURS
            ),
            metadata_refresh_on_startup=_as_bool(
                raw.get("metadata_refresh_on_startup"), True
            ),
            active_activity=_as_bool(raw.get("active_activity"), False),
            active_activity_seconds=_as_float(
                raw.get("active_activity_seconds"), DEFAULT_ACTIVE_ACTIVITY_SECONDS
            ),
        )

    @classmethod
    def from_config(cls) -> KeepaliveSettings:
        return cls.from_mapping(get_configured_keepalive_settings())

    @property
    def auth_update_seconds(self) -> float:
        return self.auth_update_hours * 3600.0

    @property
    def metadata_refresh_seconds(self) -> float:
        return self.metadata_refresh_hours * 3600.0


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


class AccountKeepaliveService:
    """Single-loop keepalive service; every job runs through one global lock."""

    def __init__(
        self,
        store: AccountStore,
        client: AIStudioClient,
        refresher: Any,
        *,
        instance_manager: Any | None = None,
        account_service: Any | None = None,
        rotator: Any | None = None,
        region_arbiter: Any | None = None,
        metadata_attempt_limit: int | None = None,
        auth_attempt_limit: int | None = None,
        consecutive_no_capacity_limit: int | None = None,
        clock: Callable[[], float] = time.time,
        tick_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._client = client
        self._refresher = refresher
        self._instance_manager = instance_manager
        self._account_service = account_service
        self._rotator = rotator
        self._region_arbiter = region_arbiter
        self._metadata_attempt_limit = max(
            1,
            int(
                metadata_attempt_limit
                if metadata_attempt_limit is not None
                else DEFAULT_METADATA_ATTEMPT_LIMIT
            ),
        )
        self._auth_attempt_limit = max(
            1,
            int(
                auth_attempt_limit
                if auth_attempt_limit is not None
                else DEFAULT_AUTH_ATTEMPT_LIMIT
            ),
        )
        self._consecutive_no_capacity_limit = max(
            1,
            int(
                consecutive_no_capacity_limit
                if consecutive_no_capacity_limit is not None
                else CONSECUTIVE_NO_CAPACITY_LIMIT
            ),
        )
        self._clock = clock
        self._tick_seconds = max(0.01, tick_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._job_lock = asyncio.Lock()
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._running: str | None = None
        self._last_results: dict[str, dict[str, object]] = {}
        self._last_error: str | None = None
        self._next_due: dict[str, float] = {}
        self._auth_saved_at: dict[str, float] = {}
        self._account_auth_failures: dict[str, int] = {}
        self._auth_partial_failures: int = 0
        # account_id -> startup capture marker (capturedAt or raw digest), so a
        # hot-reused worker's identical capture is parsed at most once.
        self._startup_capture_consumed: dict[str, str] = {}
        # One canary arbitration per account per keepalive round.
        self._region_arbitrated_round: set[str] = set()
        # In-memory rotation cursors (last attempted account per channel) so a
        # bounded external batch never restarts from a persistently deferred
        # queue head.  Off mode ignores them and still attempts every account.
        self._metadata_cursor: str | None = None
        self._auth_cursor: str | None = None
        self._paused = False
        self._attach_tier_slot_provider()
        self._attach_region_arbiter()

    def _attach_tier_slot_provider(self) -> None:
        """Let the refresher reuse live pool sessions for engine-correct probes."""
        setter = getattr(self._refresher, "set_slot_provider", None)
        if callable(setter):
            setter(self._pool_slots)

    def _region_arbiter_handle(self) -> Any | None:
        """Injected arbiter first, then the process-wide one (None when off)."""
        if self._region_arbiter is not None:
            return self._region_arbiter
        try:
            from aistudio_api.application.exit_arbitration import (
                get_default_exit_arbitration,
            )

            return get_default_exit_arbitration()
        except Exception:
            return None

    def _attach_region_arbiter(self) -> None:
        """Hand the (possibly process-wide) arbiter to the metadata refresher."""
        setter = getattr(self._refresher, "set_region_arbiter", None)
        if not callable(setter):
            return
        try:
            setter(self._region_arbiter_handle())
        except Exception as exc:
            log.debug("[Keepalive] region arbiter wiring failed: %s", exc)

    def _external_runtime(self) -> bool:
        """True when the refresher runs against the external exit pool."""
        try:
            return bool(getattr(self._refresher, "external_mode", False))
        except Exception:
            return False

    def _slot_surface_for(self, account_id: str) -> str | None:
        """Bound pool surface (chat/build) for an account, when it holds a slot."""
        if not account_id:
            return None
        for slot in self._pool_slots():
            if str(getattr(slot, "account_id", "") or "") != account_id:
                continue
            surface = str(getattr(slot, "surface", "") or "").lower()
            if surface in ("chat", "build"):
                return surface
        return None

    def _arbitration_kind(self, account_id: str, arbiter: object | None) -> str:
        """Pass the account's actually bound kind (chat/build) to the arbiter."""
        surface = self._slot_surface_for(account_id)
        if surface is not None:
            return surface
        resolver = getattr(arbiter, "resolver", None)
        return resolve_arbitration_kind(resolver, account_id)

    async def _arbitrate_region_once(self, account_id: str, source: str) -> str | None:
        """Run at most one region-403 canary per account per keepalive round."""
        if not account_id or account_id in self._region_arbitrated_round:
            return None
        arbiter = self._region_arbiter_handle()
        if arbiter is None:
            return None
        self._region_arbitrated_round.add(account_id)
        try:
            outcome = await arbiter.adjudicate(
                account_id=account_id,
                kind=self._arbitration_kind(account_id, arbiter),
                account_bad_event_kind="verify_account",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "[Keepalive] region 403 canary 仲裁异常 account=%s source=%s error=%s",
                account_id,
                source,
                type(exc).__name__,
            )
            return "inconclusive"
        if outcome == "account_bad":
            self._persist_account_bad(account_id)
        log.info(
            "[Keepalive] region 403 canary 仲裁 account=%s source=%s outcome=%s",
            account_id,
            source,
            outcome,
        )
        return str(outcome)

    def _persist_account_bad(self, account_id: str) -> None:
        """Registry is authoritative: set verify_required, keep ``expired`` as-is.

        ``expired`` is a separate, stronger signal: a real credential expiry must
        never be cleared by a verify-account verdict (Q1), so only
        ``verify_required`` is written here.
        """
        try:
            self._store.update_account_refresh(account_id, verify_required=True)
        except Exception as exc:
            log.warning(
                "[Keepalive] 持久化 verify_required 失败 account=%s: %s",
                account_id,
                exc,
            )
        rotator = self._rotator_handle()
        recorder = getattr(rotator, "record_verify_required", None)
        if callable(recorder):
            try:
                recorder(account_id, reason="region_403_account_bad")
            except Exception as exc:
                log.warning(
                    "[Keepalive] 更新 rotator verify 统计失败 account=%s: %s",
                    account_id,
                    exc,
                )

    @staticmethod
    def _rotation_is_region_403(result: dict[str, object]) -> bool:
        """RotateCookies returned 403: treat as an exit-region suspicion.

        The rotation client does not expose the response body, so any 403 is
        passed to the canary; the arbiter decides whether the exit is bad, the
        account is bad, or the signal is inconclusive.
        """
        if bool(result.get("deferred")):
            return False
        raw_status = result.get("status")
        if isinstance(raw_status, bool) or not isinstance(raw_status, (int, str)):
            return False
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            return False
        if status != REGION_403_STATUS:
            return False
        text = str(result.get("body") or result.get("error") or "")
        return not text or is_region_restricted_message(text)

    @property
    def running(self) -> str | None:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    async def pause_and_wait(self, timeout_s: float = 10.0) -> bool:
        """Pause scheduled jobs and wait (bounded) for any in-flight job.

        Used by the engine hot switch before draining/destroying slot sessions.
        """
        self._paused = True
        if not self._job_lock.locked():
            return True
        try:
            await asyncio.wait_for(
                self._idle_event.wait(), timeout=max(0.1, float(timeout_s))
            )
        except TimeoutError:
            log.warning("[Keepalive] pause wait timed out; job still running")
            return False
        return True

    def resume(self) -> None:
        self._paused = False

    def _rotator_handle(self) -> Any | None:
        """Injected rotator first, then the process-wide instance."""
        if self._rotator is not None:
            return self._rotator
        try:
            from aistudio_api.application import account_rotator as rotator_module

            return getattr(rotator_module, "_rotator", None)
        except Exception:
            return None

    def _is_auth_locked(self, account_id: str) -> bool:
        """True for accounts locked by session expiry / verify-account.

        Background keepalive must not keep probing a verify-account lock: a
        successful ``GetAiStudioBenefitTier`` 200 would otherwise clear the
        local ``expired`` flag and silently wash the lock away.
        """
        rotator = self._rotator_handle()
        if rotator is None:
            return False
        checker = getattr(rotator, "is_auth_locked", None)
        if callable(checker):
            try:
                return bool(checker(account_id))
            except Exception as exc:
                log.debug("[Keepalive] auth lock probe failed %s: %s", account_id, exc)
                return False
        getter = getattr(rotator, "get_account_stats", None)
        if not callable(getter):
            return False
        try:
            stats = getter(account_id)
        except Exception as exc:
            log.debug("[Keepalive] auth lock stats failed %s: %s", account_id, exc)
            return False
        return bool(getattr(stats, "session_expired", False))

    def _settings(self) -> KeepaliveSettings:
        return KeepaliveSettings.from_config()

    def _pool_slots(self) -> tuple[Any, ...]:
        manager = self._instance_manager
        if manager is None:
            return ()
        getter = getattr(manager, "pool_slots", None)
        if not callable(getter):
            return ()
        try:
            slots: Any = getter()
            return tuple(slots)
        except Exception as exc:
            log.warning("[Keepalive] pool slot inspection failed: %s", exc)
            return ()

    async def start(self, *, startup_metadata_done: bool = False) -> None:
        if self._task is not None and not self._task.done():
            return
        now = self._clock()
        settings = self._settings()
        self._next_due = {
            "metadata_refresh": (
                now
                if settings.metadata_refresh_on_startup and not startup_metadata_done
                else now + settings.metadata_refresh_seconds
            ),
            "auth_update": now,
            "active_activity": now + settings.active_activity_seconds,
        }
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="account_keepalive")
        log.info(
            "[Keepalive] started auth=%s/%.1fh metadata=%s/%.1fh activity=%s",
            settings.auth_update,
            settings.auth_update_hours,
            settings.metadata_refresh,
            settings.metadata_refresh_hours,
            settings.active_activity,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        log.info("[Keepalive] stopped")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_due(self._settings())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log.warning("[Keepalive] loop error: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._tick_seconds
                )

    def _retry_delay(
        self, task: str, result: dict[str, object], interval: float
    ) -> float:
        if result.get("deferred"):
            # 429 no_capacity is not a failure: the account stays due and is
            # retried after the manager's Retry-After (at least the short
            # failure window), never after the full 24h interval.
            return max(
                FAILURE_RETRY_SECONDS, _as_float(result.get("retry_after_s"), 0.0)
            )
        if result.get("ok"):
            if task == "auth_update":
                self._auth_partial_failures = 0
            return interval
        if task == "metadata_refresh":
            if result.get("reason") == "refresh_in_progress":
                return REFRESH_IN_PROGRESS_RETRY_SECONDS
            return FAILURE_RETRY_SECONDS
        if task == "auth_update":
            if result.get("reason") == "no_active_page":
                self._auth_partial_failures = 0
                return min(NO_ACTIVE_PAGE_RETRY_SECONDS, interval)
            if bool(result.get("cycle_exhausted")):
                self._auth_partial_failures = 0
                return interval
            failure_count = self._auth_partial_failures
            self._auth_partial_failures = failure_count + 1
            delay = FAILURE_RETRY_SECONDS * (2 ** min(failure_count, 10))
            return min(delay, interval)
        return min(NO_ACTIVE_PAGE_RETRY_SECONDS, interval)

    async def _run_due(self, settings: KeepaliveSettings) -> None:
        if self._paused:
            return
        now = self._clock()
        jobs: list[tuple[str, bool, float]] = [
            (
                "metadata_refresh",
                settings.metadata_refresh,
                settings.metadata_refresh_seconds,
            ),
            ("auth_update", settings.auth_update, settings.auth_update_seconds),
            (
                "active_activity",
                settings.active_activity,
                settings.active_activity_seconds,
            ),
        ]
        for task, enabled, interval in jobs:
            if not enabled:
                continue
            due = self._next_due.get(task)
            if due is not None and due > now:
                continue
            result = await self.run_now(task, trigger="schedule", settings=settings)
            if result.get("reason") == "busy":
                continue
            self._next_due[task] = self._clock() + self._retry_delay(
                task, result, interval
            )

    async def run_now(
        self,
        task: str,
        *,
        trigger: str = "manual",
        settings: KeepaliveSettings | None = None,
    ) -> dict[str, object]:
        if task not in TASKS:
            return {"ok": False, "task": task, "reason": "unknown_task"}
        if self._paused:
            return {"ok": False, "task": task, "reason": "paused"}
        if self._job_lock.locked():
            return {"ok": False, "task": task, "reason": "busy"}

        async with self._job_lock:
            # Re-check after acquiring the lock: a pause may have been requested
            # while this caller waited behind an in-flight job.
            if self._paused:
                return {"ok": False, "task": task, "reason": "paused"}
            self._idle_event.clear()
            self._running = task
            self._region_arbitrated_round.clear()
            current = settings or self._settings()
            try:
                result = await self._execute(task, current, trigger)
            except Exception as exc:
                self._last_error = str(exc)
                result = {
                    "ok": False,
                    "task": task,
                    "reason": "exception",
                    "error": str(exc),
                }
                log.warning("[Keepalive] %s failed: %s", task, exc)
            finally:
                self._running = None
            self._last_results[task] = {
                **result,
                "at": _iso(self._clock()),
                "trigger": trigger,
            }
            self._idle_event.set()
            return self._last_results[task]

    async def run_startup_metadata_refresh(self) -> dict[str, object]:
        """Run the startup metadata refresh, waiting out an in-flight job."""
        while True:
            result = await self.run_now("metadata_refresh", trigger="startup")
            if result.get("reason") != "busy":
                return result
            await self._idle_event.wait()

    async def _execute(
        self, task: str, settings: KeepaliveSettings, trigger: str
    ) -> dict[str, object]:
        if task == "metadata_refresh":
            return await self._metadata_refresh(settings, trigger)
        if task == "auth_update":
            return await self._auth_update(trigger, settings=settings)
        return await self._active_activity(trigger)

    async def _metadata_refresh(
        self, settings: KeepaliveSettings, trigger: str
    ) -> dict[str, object]:
        accounts = [
            account
            for account in self._store.list_accounts()
            if getattr(account, "enabled", True)
            and not getattr(account, "expired", False)
            and not self._is_auth_locked(account.id)
        ]
        now = self._clock()
        # 缺少邮箱的账号即使 tier 刚校验过也必须进入本轮批次：
        # ``refresh_account`` 的 ``wants_email = refresh_email or not
        # account.email`` 才会回填邮箱（启动期裸探测已并入本周期）。
        due = [
            account
            for account in accounts
            if not getattr(account, "email", "")
            or (checked := _parse_ts(account.tier_checked_at)) is None
            or now - checked >= settings.metadata_refresh_seconds
        ]
        if not due:
            return {"ok": True, "reason": "no_due_accounts", "count": 0}

        if self._external_runtime():
            self._attach_region_arbiter()
            try:
                results = await self._refresh_metadata_batched(
                    due, [str(account.id) for account in accounts]
                )
            except RefreshInProgressError:
                log.info("[Keepalive] metadata refresh skipped: already running")
                return {"ok": False, "reason": "refresh_in_progress", "count": 0}
        else:
            try:
                # Background metadata refresh must not re-fetch the apps HTML
                # for accounts that already have an email.  ``refresh_email``
                # =False still backfills a genuinely missing email because the
                # refresher treats ``not account.email`` as an override.
                results = await self._refresher.refresh_accounts(
                    due,
                    refresh_email=False,
                    skip_fresh_free_tier=True,
                    respect_backoff=True,
                )
            except RefreshInProgressError:
                log.info("[Keepalive] metadata refresh skipped: already running")
                return {"ok": False, "reason": "refresh_in_progress", "count": 0}

        checked_at = _iso(now)
        deferred_results = [result for result in results if _is_deferred_result(result)]
        deferred_ids = [str(result.account_id) for result in deferred_results]
        deferred_retry = max(
            (
                float(getattr(result, "retry_after_s", 0.0) or 0.0)
                for result in deferred_results
            ),
            default=0.0,
        )
        previous_errors = {
            str(account.id): getattr(account, "last_refresh_error", None)
            for account in due
        }
        for result in results:
            deferred = _is_deferred_result(result)
            if getattr(result, "region_outcome", None) == "account_bad":
                self._persist_account_bad(str(result.account_id))
            self._store.mark_refresh_attempt(
                str(result.account_id),
                # 429 no_capacity must not be recorded as an account failure, and
                # it must not erase a real error from an earlier attempt either:
                # only the attempt timestamp advances for a deferred account.
                error=(
                    previous_errors.get(str(result.account_id))
                    if deferred
                    else result.error
                ),
                at=result.checked_at or checked_at,
            )
        failed = sum(
            1
            for result in results
            if not result.ok
            and not _is_deferred_result(result)
            and not getattr(result, "skipped", False)
        )
        succeeded = sum(
            1
            for result in results
            if bool(getattr(result, "ok", False)) and not _is_deferred_result(result)
        )
        no_capacity = sum(
            1 for result in deferred_results if _is_no_capacity_result(result)
        )
        # Skipped accounts were not probed at all (no exit): they must stay due
        # so the next keepalive round retries instead of waiting a full 24h.
        skipped_results = [
            result for result in results if getattr(result, "skipped", False)
        ]
        skipped_ids = [str(result.account_id) for result in skipped_results]
        # Reuse the refresher's (account, reason) 60s throttle: a skip that the
        # refresher already warned about must not produce a second line here.
        unlogged = [
            str(result.account_id)
            for result in skipped_results
            if skip_log_due(
                str(result.account_id),
                str(getattr(result, "skip_reason", "") or "no_exit"),
            )
        ]
        if unlogged:
            log.warning(
                "[Keepalive] metadata refresh skipped %d account(s) without exit: %s",
                len(unlogged),
                unlogged,
            )
        log.info(
            "[Keepalive] metadata refresh trigger=%s attempted=%d succeeded=%d "
            "failed=%d skipped=%d deferred=%d no_capacity=%d",
            trigger,
            len(results),
            succeeded,
            failed,
            len(skipped_ids),
            len(deferred_ids),
            no_capacity,
        )
        if no_capacity:
            # One aggregate warning per round; per-account defers stay INFO.
            # Region deferrals are not exit-budget exhaustion and must not be
            # reported as ``no_capacity=0`` capacity warnings (P2-2).
            log.warning(
                "[Keepalive] metadata refresh capacity summary trigger=%s "
                "attempted=%d succeeded=%d deferred=%d no_capacity=%d",
                trigger,
                len(results),
                succeeded,
                len(deferred_ids),
                no_capacity,
            )
        elif deferred_ids:
            log.info(
                "[Keepalive] metadata refresh region defer summary trigger=%s "
                "attempted=%d succeeded=%d deferred=%d",
                trigger,
                len(results),
                succeeded,
                len(deferred_ids),
            )
        return {
            # ``failed`` excludes skipped accounts (they were never probed), but
            # a skip still keeps the round "not ok" so those accounts stay due
            # for the next 300s round instead of waiting a full 24h.
            "ok": failed == 0 and not skipped_ids,
            "reason": "checked",
            "count": len(results),
            "attempted": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": len(skipped_ids),
            "deferred": len(deferred_ids),
            "no_capacity": no_capacity,
            "deferred_accounts": deferred_ids,
            "retry_after_s": deferred_retry,
            "accounts": [result.account_id for result in results],
        }

    async def _refresh_metadata_batched(
        self, due: list[Any], order_ids: list[str]
    ) -> list[Any]:
        """Attempt a bounded metadata batch, stopping on repeated no_capacity.

        External-mode maintenance shares a small per-exit budget.  Accounts are
        processed in small chunks so three *consecutive ``no_capacity``* results
        stop the round immediately; a region-403 deferral is not budget
        exhaustion and never breaks the streak.  Anything not attempted stays
        due for the next 300s round instead of advancing its 24h clock.

        Each round starts right after the previous round's last attempted
        account (in-memory cursor), so a head of persistently region-deferred
        accounts can never occupy every round and starve the rest.
        """
        limit = self._metadata_attempt_limit
        stop_after = self._consecutive_no_capacity_limit
        batch = _rotate_after(due, self._metadata_cursor, order_ids)[:limit]
        chunk_size = max(1, min(stop_after, limit))
        results: list[Any] = []
        streak = 0
        for start in range(0, len(batch), chunk_size):
            chunk = batch[start : start + chunk_size]
            try:
                chunk_results = await self._refresher.refresh_accounts(
                    chunk,
                    refresh_email=False,
                    skip_fresh_free_tier=True,
                    respect_backoff=True,
                )
            except RefreshInProgressError:
                if not results:
                    raise
                log.info(
                    "[Keepalive] metadata refresh stopped after %d account(s): "
                    "another refresh started",
                    len(results),
                )
                break
            results.extend(chunk_results)
            stop = False
            for result in chunk_results:
                if _is_no_capacity_result(result):
                    streak += 1
                    if streak >= stop_after:
                        stop = True
                        break
                else:
                    streak = 0
            if stop:
                log.info(
                    "[Keepalive] metadata refresh stopped early after %d "
                    "account(s): %d consecutive no_capacity",
                    len(results),
                    streak,
                )
                break
        if results:
            self._metadata_cursor = (
                str(getattr(results[-1], "account_id", "") or "") or None
            )
        return results

    async def _save_slot(self, slot: Any) -> tuple[str | None, str, bool, bool]:
        slot_id = str(getattr(slot, "slot_id", "") or "")
        account_id = str(getattr(slot, "account_id", "") or "")
        session = getattr(slot, "session", None)
        if not account_id or session is None:
            return None, slot_id, False, False
        try:
            lifecycle_lock = getattr(slot, "lifecycle_lock", None)
            if lifecycle_lock is not None:
                async with lifecycle_lock:
                    saved = bool(await session.save_current_cookies())
            else:
                saved = bool(await session.save_current_cookies())
        except Exception as exc:
            log.warning(
                "[Keepalive] slot cookie save failed slot=%s account=%s: %s",
                slot_id,
                account_id,
                exc,
            )
            return account_id, slot_id, False, True
        return account_id, slot_id, saved, False

    def _is_account_auth_due(
        self,
        account_id: str,
        *,
        trigger: str,
        now: float,
        interval: float,
    ) -> bool:
        if trigger != "schedule":
            return True
        saved_at = self._auth_saved_at.get(account_id)
        if saved_at is None:
            return True
        return (now - saved_at) >= interval

    async def _save_control_session(self) -> tuple[str | None, bool, bool]:
        session = getattr(self._client, "session", None)
        if session is None:
            return None, False, False
        page = getattr(session, "page", None)
        if page is None or page.is_closed():
            return None, False, False
        try:
            saved = bool(await session.save_current_cookies())
        except Exception as exc:
            log.warning("[Keepalive] control session cookie save failed: %s", exc)
            return None, False, True
        account_id: str | None = None
        auth_file = getattr(session, "_auth_file", None) or getattr(
            session, "auth_file", None
        )
        if auth_file:
            with contextlib.suppress(Exception):
                target = Path(str(auth_file)).resolve()
                for account in self._store.list_accounts():
                    candidate = self._store.get_auth_path_optional(account.id)
                    if candidate is not None and candidate.resolve() == target:
                        account_id = account.id
                        break
        if account_id is None:
            account = self._store.get_active_account()
            account_id = account.id if account is not None else None
        return account_id, saved, True

    async def _harvest_worker_startup(self, account_id: str, session: Any) -> None:
        """Consume the signer's cold-start tier/model capture (zero new requests).

        A hot-reused worker reports the same ``capturedAt`` and is skipped; a
        cold rebuild (boot, rebind, 403 rebuild, slot rotation) yields a new
        stamp and is consumed exactly once. A busy (503) or missing (404)
        worker both skip this cycle without touching the tier/model stores.
        """
        fetch = getattr(session, "fetch_worker_state", None)
        if not callable(fetch):
            return
        try:
            state = await cast("Callable[[], Awaitable[object]]", fetch)()
        except Exception as exc:
            log.debug(
                "[Keepalive] 读取 worker 冷启动状态失败 account=%s: %s",
                account_id,
                exc,
            )
            return
        if not isinstance(state, dict):
            return
        stamp = str(state.get("capturedAt") or "")
        tier_raw = state.get("startupTierRaw")
        models_raw = state.get("startupModelsRaw")
        has_tier = isinstance(tier_raw, str) and bool(tier_raw)
        has_models = isinstance(models_raw, str) and bool(models_raw)
        if not has_tier and not has_models:
            return
        marker = stamp or f"{tier_raw!r}|{models_raw!r}"
        if self._startup_capture_consumed.get(account_id) == marker:
            return
        self._startup_capture_consumed[account_id] = marker
        if has_tier:
            tier = parse_benefit_tier_envelope(tier_raw).strict_tier()
            if tier:
                self._store.update_account_tier(
                    account_id, tier, checked_at=_iso(self._clock())
                )
                log.info(
                    "[Keepalive] 冷启动收获 tier account=%s tier=%s", account_id, tier
                )
        if has_models:
            from aistudio_api.infrastructure.gateway.model_discovery import (
                model_discovery,
            )

            if model_discovery.accept_startup_models(models_raw, source="live_signer"):
                log.info("[Keepalive] 冷启动收获模型目录 account=%s", account_id)

    async def _auth_update(
        self, trigger: str, settings: KeepaliveSettings | None = None
    ) -> dict[str, object]:
        current_settings = settings or self._settings()
        now = self._clock()
        interval = float(current_settings.auth_update_seconds)
        accounts = self._store.list_accounts()
        slots = self._pool_slots()

        candidate_ids = {
            account.id
            for account in accounts
            if account.enabled
            and not account.expired
            and not self._is_auth_locked(account.id)
        }
        for slot in slots:
            slot_acc_id = str(getattr(slot, "account_id", "") or "")
            if slot_acc_id:
                acc = self._store.get_account(slot_acc_id)
                if acc is None or (
                    acc.enabled
                    and not acc.expired
                    and not self._is_auth_locked(slot_acc_id)
                ):
                    candidate_ids.add(slot_acc_id)

        if (
            trigger == "schedule"
            and candidate_ids
            and not any(
                self._is_account_auth_due(
                    acc_id, trigger=trigger, now=now, interval=interval
                )
                for acc_id in candidate_ids
            )
        ):
            return {
                "ok": True,
                "reason": "saved",
                "count": 0,
                "accounts": [],
            }

        account_order: list[str] = []

        def _ensure_acc(acc_id: str) -> None:
            if acc_id not in account_order:
                account_order.append(acc_id)

        slot_results: dict[str, list[bool]] = {}
        slot_errored: set[str] = set()
        slot_failures: list[tuple[str, str]] = []
        browserless_clean_eligible: dict[str, bool] = {}
        control_results: dict[str, list[bool]] = {}
        rotate_results: dict[str, list[bool]] = {}
        browserless_disk_ok: set[str] = set()
        deferred_accounts: dict[str, float] = {}
        region_deferred_accounts: set[str] = set()

        for slot in slots:
            slot_acc_id = str(getattr(slot, "account_id", "") or "")
            if slot_acc_id and not self._is_account_auth_due(
                slot_acc_id, trigger=trigger, now=now, interval=interval
            ):
                continue
            account_id, slot_id, saved, raised = await self._save_slot(slot)
            if account_id is None:
                continue
            _ensure_acc(account_id)
            slot_results.setdefault(account_id, []).append(saved)
            if raised:
                slot_errored.add(account_id)
            if not saved and slot_id:
                slot_failures.append((account_id, slot_id))
            sess = getattr(slot, "session", None)
            is_clean_browserless = (
                not saved
                and not raised
                and getattr(sess, "engine", None) == "browserless"
                and not getattr(sess, "_cookie_dirty", False)
                and not getattr(sess, "_mint_updates", None)
                and not getattr(sess, "_rpc_set_cookies", None)
            )
            if account_id not in browserless_clean_eligible:
                browserless_clean_eligible[account_id] = is_clean_browserless
            elif not is_clean_browserless:
                browserless_clean_eligible[account_id] = False

        (
            control_account,
            control_saved,
            control_attempted,
        ) = await self._save_control_session()
        control_active_saved = False
        if control_attempted:
            if control_account is not None:
                if self._is_account_auth_due(
                    control_account, trigger=trigger, now=now, interval=interval
                ):
                    _ensure_acc(control_account)
                    control_results.setdefault(control_account, []).append(
                        control_saved
                    )
                    control_active_saved = control_saved
            else:
                control_active_saved = control_saved

        # 对启用中的账号（特别是持有轮换种子或活动槽位的账号）尝试主动轮换短效 Cookie
        from aistudio_api.application.account_service import AccountService

        acc_svc = self._account_service
        svc = (
            acc_svc
            if isinstance(acc_svc, AccountService)
            else AccountService(self._store)
        )
        slot_sessions: dict[str, Any] = {}
        for slot in slots:
            acc_id = getattr(slot, "account_id", None)
            sess = getattr(slot, "session", None)
            if acc_id and sess:
                slot_sessions[str(acc_id)] = sess

        external = self._external_runtime()
        if external:
            self._attach_region_arbiter()
        auth_attempts = 0
        no_capacity_streak = 0
        stop_after_account = False
        last_attempted: str | None = None

        # External rounds attempt only a bounded batch: rotate past the previous
        # round's cursor so a head of persistently region-deferred accounts can
        # never block the rest.  Off mode keeps the original full pass.
        iteration = (
            _rotate_after(list(accounts), self._auth_cursor) if external else accounts
        )

        for account in iteration:
            if not account.enabled or account.expired:
                continue
            if not self._is_account_auth_due(
                account.id, trigger=trigger, now=now, interval=interval
            ):
                continue
            if self._is_auth_locked(account.id):
                log.debug(
                    "[Keepalive] 跳过已锁定账号的 Cookie 轮换 account=%s", account.id
                )
                continue
            if external and auth_attempts >= self._auth_attempt_limit:
                log.info(
                    "[Keepalive] auth update stopped at attempt limit %d; "
                    "remaining accounts stay due",
                    self._auth_attempt_limit,
                )
                break
            acc_sess = slot_sessions.get(account.id)
            account_no_capacity = False
            auth_attempts += 1
            last_attempted = account.id
            try:
                rot_res = await svc.rotate_account_cookies(account.id, session=acc_sess)
                if rot_res.get("deferred"):
                    _ensure_acc(account.id)
                    deferred_accounts[account.id] = _as_float(
                        rot_res.get("retry_after_s"), 0.0
                    )
                    account_no_capacity = True
                elif self._rotation_is_region_403(rot_res):
                    outcome = await self._arbitrate_region_once(
                        account.id, "auth_update"
                    )
                    if outcome is None:
                        # No arbiter (off mode): keep the legacy failure path.
                        _ensure_acc(account.id)
                        rotate_results.setdefault(account.id, []).append(False)
                    else:
                        _ensure_acc(account.id)
                        deferred_accounts[account.id] = REGION_DEFER_RETRY_SECONDS
                        region_deferred_accounts.add(account.id)
                elif rot_res.get("ok"):
                    _ensure_acc(account.id)
                    rotate_results.setdefault(account.id, []).append(True)
                elif rot_res.get("skipped") == "missing_seed":
                    if (
                        browserless_clean_eligible.get(account.id, False)
                        and account.id not in slot_errored
                        and self._store.get_auth_path(account.id) is not None
                    ):
                        browserless_disk_ok.add(account.id)
                elif (
                    rot_res.get("status") != 0
                    or "error" in rot_res
                    or "status" not in rot_res
                ):
                    _ensure_acc(account.id)
                    rotate_results.setdefault(account.id, []).append(False)
            except Exception as exc:
                log.debug("[Keepalive] 账号 %s 轮换 Cookie 异常: %s", account.id, exc)
                _ensure_acc(account.id)
                rotate_results.setdefault(account.id, []).append(False)
            if acc_sess is not None:
                await self._harvest_worker_startup(account.id, acc_sess)
            if account_no_capacity:
                no_capacity_streak += 1
                if (
                    external
                    and no_capacity_streak >= self._consecutive_no_capacity_limit
                ):
                    stop_after_account = True
            else:
                # A region-403 deferral (or any real attempt) is not exit-budget
                # exhaustion: it must not contribute to the no_capacity streak.
                no_capacity_streak = 0
            if stop_after_account:
                log.info(
                    "[Keepalive] auth update stopped early after %d account(s): "
                    "%d consecutive no_capacity",
                    auth_attempts,
                    no_capacity_streak,
                )
                break

        if external and last_attempted:
            self._auth_cursor = last_attempted

        attempted_accounts = len(account_order)

        saved_accounts: list[str] = []
        failed_accounts: list[str] = []
        for account_id in account_order:
            if account_id in deferred_accounts:
                # 429 no_capacity: not saved, not failed, not cooled down.
                continue
            channel_outcomes: list[bool] = []
            if account_id in slot_results:
                channel_outcomes.append(all(slot_results[account_id]))
            if account_id in control_results:
                channel_outcomes.append(any(control_results[account_id]))
            if account_id in rotate_results:
                channel_outcomes.append(any(rotate_results[account_id]))
            if any(channel_outcomes) or account_id in browserless_disk_ok:
                saved_accounts.append(account_id)
            else:
                failed_accounts.append(account_id)

        failed_set = set(failed_accounts)
        failed_slots = [
            slot_id for acc_id, slot_id in slot_failures if acc_id in failed_set
        ]
        deferred_retry = max(deferred_accounts.values(), default=0.0)
        no_capacity_deferred = len(deferred_accounts) - len(region_deferred_accounts)

        if no_capacity_deferred:
            # One aggregate warning per round; per-account defers stay INFO.
            log.warning(
                "[Keepalive] auth update capacity summary trigger=%s "
                "attempted=%d succeeded=%d deferred=%d no_capacity=%d",
                trigger,
                attempted_accounts,
                len(saved_accounts),
                len(deferred_accounts),
                no_capacity_deferred,
            )
        elif deferred_accounts:
            # Only region deferrals: not exit-budget exhaustion, so this must
            # never read as a ``no_capacity=0`` capacity warning (P2-2).
            log.info(
                "[Keepalive] auth update region defer summary trigger=%s "
                "attempted=%d succeeded=%d deferred=%d region_deferred=%d",
                trigger,
                attempted_accounts,
                len(saved_accounts),
                len(deferred_accounts),
                len(region_deferred_accounts),
            )

        for account_id in saved_accounts:
            self._store.mark_auth_saved(account_id)
            self._auth_saved_at[account_id] = now
            self._account_auth_failures.pop(account_id, None)

        exhausted_accounts: set[str] = set()
        if trigger == "schedule":
            for account_id in failed_accounts:
                failures = self._account_auth_failures.get(account_id, 0) + 1
                if failures >= MAX_AUTH_CONSECUTIVE_FAILURES:
                    self._auth_saved_at[account_id] = now
                    self._account_auth_failures.pop(account_id, None)
                    exhausted_accounts.add(account_id)
                    log.warning(
                        "[Keepalive] auth update account=%s reached %d consecutive failures; skipping until next cycle",
                        account_id,
                        failures,
                    )
                else:
                    self._account_auth_failures[account_id] = failures

        if failed_accounts:
            cycle_exhausted = trigger == "schedule" and len(exhausted_accounts) == len(
                failed_accounts
            )
            result: dict[str, object] = {
                "ok": False,
                "reason": "partial",
                "count": len(saved_accounts),
                "accounts": saved_accounts,
                "failed_accounts": failed_accounts,
                "failed_slots": failed_slots,
            }
            if cycle_exhausted:
                result["cycle_exhausted"] = True
            if deferred_accounts:
                result["deferred"] = True
                result["deferred_accounts"] = sorted(deferred_accounts)
                result["retry_after_s"] = deferred_retry
            log.warning(
                "[Keepalive] auth update trigger=%s partial saved=%s failed=%s "
                "deferred=%s",
                trigger,
                saved_accounts,
                failed_accounts,
                sorted(deferred_accounts),
            )
            return result

        if not saved_accounts and not control_active_saved:
            if deferred_accounts:
                return {
                    "ok": False,
                    "reason": "deferred",
                    "count": 0,
                    "deferred": True,
                    "deferred_accounts": sorted(deferred_accounts),
                    "retry_after_s": deferred_retry,
                    "failed_slots": failed_slots,
                }
            return {
                "ok": False,
                "reason": "no_active_page",
                "count": 0,
                "failed_slots": failed_slots,
            }

        if not saved_accounts and control_active_saved:
            result = {
                "ok": True,
                "reason": "saved",
                "count": 1,
                "accounts": [],
                "control": True,
            }
        else:
            result = {
                "ok": True,
                "reason": "saved",
                "count": len(saved_accounts),
                "accounts": saved_accounts,
                "failed_slots": [],
            }
        if deferred_accounts:
            # Some accounts were deferred for 429 no_capacity: retry them after
            # the short defer window instead of the full 24h interval.
            result["deferred"] = True
            result["deferred_accounts"] = sorted(deferred_accounts)
            result["retry_after_s"] = deferred_retry
        log.info(
            "[Keepalive] auth update trigger=%s saved=%s deferred=%s",
            trigger,
            saved_accounts or ["control"],
            sorted(deferred_accounts),
        )
        return result

    async def _active_activity(self, trigger: str) -> dict[str, object]:
        """Optional control-page scroll. No idle reclamation is implemented."""
        session = getattr(self._client, "session", None)
        page = getattr(session, "page", None)
        if page is None or page.is_closed():
            return {"ok": False, "reason": "no_active_page"}
        await page.evaluate("() => { window.scrollBy(0, 2); return true; }")
        log.info("[Keepalive] active activity trigger=%s", trigger)
        return {"ok": True, "reason": "activity"}

    def status(self) -> dict[str, object]:
        settings = self._settings()
        slots = self._pool_slots()
        in_flight = sum(int(getattr(slot, "active_requests", 0) or 0) for slot in slots)
        enabled_map = {
            "auth_update": settings.auth_update,
            "metadata_refresh": settings.metadata_refresh,
            "active_activity": settings.active_activity,
        }
        return {
            "enabled": enabled_map,
            "intervals": {
                "auth_update_hours": settings.auth_update_hours,
                "metadata_refresh_hours": settings.metadata_refresh_hours,
                "active_activity_seconds": settings.active_activity_seconds,
            },
            "next_due": {
                key: (_iso(value) if enabled_map.get(key, False) else None)
                for key, value in sorted(self._next_due.items())
            },
            "busy": self._job_lock.locked(),
            "running": self._running,
            "last_run": {
                task: entry.get("at") for task, entry in self._last_results.items()
            },
            "last_results": dict(self._last_results),
            "last_error": self._last_error,
            "in_flight": in_flight,
            "pool_slots": len(slots),
        }


__all__ = [
    "TASKS",
    "AccountKeepaliveService",
    "KeepaliveSettings",
]
