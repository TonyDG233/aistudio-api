"""账号保活调度器：24h Cookie 回写、元数据刷新与可选控制页活动。

P6/D08：保留 auth_update 与 metadata_refresh；删除后台 auto_switch、
InstanceSwitcher.rebalance 与 legacy rotator 兜底。所有 job 共用同一把
串行锁，busy 时立即返回且不推进 next_due；失败按 job 短周期重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aistudio_api.infrastructure.account.account_info_refresher import (
    RefreshInProgressError,
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
        clock: Callable[[], float] = time.time,
        tick_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._client = client
        self._refresher = refresher
        self._instance_manager = instance_manager
        self._account_service = account_service
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
        self._paused = False

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
        if result.get("ok"):
            return interval
        if task == "metadata_refresh":
            if result.get("reason") == "refresh_in_progress":
                return REFRESH_IN_PROGRESS_RETRY_SECONDS
            return FAILURE_RETRY_SECONDS
        if task == "auth_update":
            return NO_ACTIVE_PAGE_RETRY_SECONDS
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
            return await self._auth_update(trigger)
        return await self._active_activity(trigger)

    async def _metadata_refresh(
        self, settings: KeepaliveSettings, trigger: str
    ) -> dict[str, object]:
        accounts = [
            account
            for account in self._store.list_accounts()
            if not getattr(account, "expired", False)
        ]
        now = self._clock()
        due = [
            account
            for account in accounts
            if (checked := _parse_ts(account.tier_checked_at)) is None
            or now - checked >= settings.metadata_refresh_seconds
        ]
        if not due:
            return {"ok": True, "reason": "no_due_accounts", "count": 0}

        try:
            results = await self._refresher.refresh_accounts(
                due,
                refresh_email=True,
                skip_fresh_free_tier=True,
            )
        except RefreshInProgressError:
            log.info("[Keepalive] metadata refresh skipped: already running")
            return {"ok": False, "reason": "refresh_in_progress", "count": 0}

        checked_at = _iso(now)
        for result in results:
            self._store.mark_refresh_attempt(
                str(result.account_id),
                error=result.error,
                at=result.checked_at or checked_at,
            )
        failed = sum(1 for result in results if not result.ok)
        log.info(
            "[Keepalive] metadata refresh trigger=%s checked=%d failed=%d",
            trigger,
            len(results),
            failed,
        )
        return {
            "ok": failed == 0,
            "reason": "checked",
            "count": len(results),
            "failed": failed,
            "accounts": [result.account_id for result in results],
        }

    async def _save_slot(self, slot: Any) -> tuple[str | None, str, bool]:
        slot_id = str(getattr(slot, "slot_id", "") or "")
        account_id = str(getattr(slot, "account_id", "") or "")
        session = getattr(slot, "session", None)
        if not account_id or session is None:
            return None, slot_id, False
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
            return account_id, slot_id, False
        return account_id, slot_id, saved

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

    async def _auth_update(self, trigger: str) -> dict[str, object]:
        attempts: dict[str, list[bool]] = {}
        failed_slots: list[str] = []
        for slot in self._pool_slots():
            account_id, slot_id, saved = await self._save_slot(slot)
            if account_id is None:
                continue
            attempts.setdefault(account_id, []).append(saved)
            if not saved and slot_id:
                failed_slots.append(slot_id)

        (
            control_account,
            control_saved,
            control_attempted,
        ) = await self._save_control_session()
        if control_attempted and control_account is not None:
            attempts.setdefault(control_account, []).append(control_saved)

        # 对启用中的账号（特别是持有轮换种子或活动槽位的账号）尝试主动轮换短效 Cookie
        from aistudio_api.application.account_service import AccountService

        acc_svc = self._account_service
        svc = (
            acc_svc
            if isinstance(acc_svc, AccountService)
            else AccountService(self._store)
        )
        slot_sessions: dict[str, Any] = {}
        for slot in self._pool_slots():
            acc_id = getattr(slot, "account_id", None)
            sess = getattr(slot, "session", None)
            if acc_id and sess:
                slot_sessions[str(acc_id)] = sess

        rotated_accounts: list[str] = []
        for account in self._store.list_accounts():
            if not account.enabled or account.expired:
                continue
            acc_sess = slot_sessions.get(account.id)
            try:
                rot_res = await svc.rotate_account_cookies(account.id, session=acc_sess)
                if rot_res.get("ok"):
                    rotated_accounts.append(account.id)
                    attempts.setdefault(account.id, []).append(True)
            except Exception as exc:
                log.debug("[Keepalive] 账号 %s 轮换 Cookie 异常: %s", account.id, exc)

        saved_accounts = [
            account_id
            for account_id, results in attempts.items()
            if results and all(results)
        ]
        failed_accounts = [
            account_id
            for account_id, results in attempts.items()
            if not results or not all(results)
        ]
        for account_id in saved_accounts:
            self._store.mark_auth_saved(account_id)

        if not saved_accounts and not (control_attempted and control_saved):
            any_slot_saved = any(any(values) for values in attempts.values())
            if failed_accounts and any_slot_saved:
                result = {
                    "ok": False,
                    "reason": "partial",
                    "count": 0,
                    "accounts": [],
                    "failed_accounts": failed_accounts,
                    "failed_slots": failed_slots,
                }
                log.warning(
                    "[Keepalive] auth update trigger=%s partial saved=[] failed=%s",
                    trigger,
                    failed_accounts,
                )
                return result
            return {
                "ok": False,
                "reason": "no_active_page",
                "count": 0,
                "failed_slots": failed_slots,
            }

        if failed_accounts:
            result: dict[str, object] = {
                "ok": False,
                "reason": "partial",
                "count": len(saved_accounts),
                "accounts": saved_accounts,
                "failed_accounts": failed_accounts,
                "failed_slots": failed_slots,
            }
            log.warning(
                "[Keepalive] auth update trigger=%s partial saved=%s failed=%s",
                trigger,
                saved_accounts,
                failed_accounts,
            )
            return result

        if not saved_accounts and control_saved:
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
        log.info(
            "[Keepalive] auth update trigger=%s saved=%s",
            trigger,
            saved_accounts or ["control"],
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
