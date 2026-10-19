"""账号保活调度器：凭证回写、元数据刷新、定时切号与轻量活动。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from aistudio_api.infrastructure.account.account_info_refresher import (
    RefreshInProgressError,
)
from aistudio_api.infrastructure.gateway.model_defaults import get_features_config
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

log = get_logger("keepalive")

TASKS = ("metadata_refresh", "auth_update", "auto_switch", "active_activity")


def _feature_float(features: dict[str, object], key: str, default: float) -> float:
    value = features.get(key, default)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class KeepaliveSettings:
    auto_switch: bool
    auto_switch_interval_hours: float
    auth_update: bool
    auth_update_hours: float
    metadata_refresh: bool
    metadata_refresh_hours: float
    metadata_refresh_on_startup: bool
    active_activity: bool
    active_activity_seconds: float

    @classmethod
    def from_features(cls, features: dict[str, object]) -> KeepaliveSettings:
        return cls(
            auto_switch=bool(features.get("auto_switch", False)),
            auto_switch_interval_hours=_feature_float(
                features, "auto_switch_interval_hours", 3.0
            ),
            auth_update=bool(features.get("keepalive_auth_update", True)),
            auth_update_hours=_feature_float(
                features, "keepalive_auth_update_hours", 24.0
            ),
            metadata_refresh=bool(features.get("keepalive_metadata_refresh", True)),
            metadata_refresh_hours=_feature_float(
                features, "keepalive_metadata_refresh_hours", 24.0
            ),
            metadata_refresh_on_startup=bool(
                features.get("keepalive_metadata_refresh_on_startup", True)
            ),
            active_activity=bool(features.get("keepalive_active_activity", False)),
            active_activity_seconds=_feature_float(
                features, "keepalive_active_activity_seconds", 60.0
            ),
        )

    @property
    def auto_switch_seconds(self) -> float:
        return self.auto_switch_interval_hours * 3600.0

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
    """单循环保活服务；所有任务通过同一 job 锁串行执行。"""

    def __init__(
        self,
        store: AccountStore,
        account_service: AccountService,
        rotator: AccountRotator,
        client: AIStudioClient,
        refresher: Any,
        *,
        clock: Callable[[], float] = time.time,
        tick_seconds: float = 1.0,
        switch_cb: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._store = store
        self._account_service = account_service
        self._rotator = rotator
        self._client = client
        self._refresher = refresher
        self._clock = clock
        self._tick_seconds = max(0.01, tick_seconds)
        self._switch_cb = switch_cb
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._job_lock = asyncio.Lock()
        self._running: str | None = None
        self._last_results: dict[str, dict[str, object]] = {}
        self._last_error: str | None = None
        self._next_due: dict[str, float] = {}

    @property
    def running(self) -> str | None:
        return self._running

    def _settings(self) -> KeepaliveSettings:
        return KeepaliveSettings.from_features(get_features_config())

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        now = self._clock()
        settings = self._settings()
        self._next_due = {
            "metadata_refresh": (
                now
                if settings.metadata_refresh_on_startup
                else now + settings.metadata_refresh_seconds
            ),
            "auth_update": now,
            "auto_switch": now + settings.auto_switch_seconds,
            "active_activity": now + settings.active_activity_seconds,
        }
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="account_keepalive")
        log.info(
            "[Keepalive] started auth=%s/%.1fh metadata=%s/%.1fh auto_switch=%s/%.1fh",
            settings.auth_update,
            settings.auth_update_hours,
            settings.metadata_refresh,
            settings.metadata_refresh_hours,
            settings.auto_switch,
            settings.auto_switch_interval_hours,
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
                settings = self._settings()
                await self._run_due(settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                log.warning("[Keepalive] loop error: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._tick_seconds
                )

    async def _run_due(self, settings: KeepaliveSettings) -> None:
        now = self._clock()
        jobs: list[tuple[str, bool, float]] = [
            (
                "metadata_refresh",
                settings.metadata_refresh,
                settings.metadata_refresh_seconds,
            ),
            ("auth_update", settings.auth_update, settings.auth_update_seconds),
            ("auto_switch", settings.auto_switch, settings.auto_switch_seconds),
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
            if due is None or due <= now:
                result = await self.run_now(task, trigger="schedule", settings=settings)
                retry_soon = (
                    task == "auth_update" and result.get("reason") == "no_active_page"
                )
                self._next_due[task] = self._clock() + (
                    60.0 if retry_soon else interval
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
        if self._job_lock.locked():
            return {"ok": False, "task": task, "reason": "busy"}

        async with self._job_lock:
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
            return self._last_results[task]

    async def _execute(
        self, task: str, settings: KeepaliveSettings, trigger: str
    ) -> dict[str, object]:
        if task == "metadata_refresh":
            return await self._metadata_refresh(settings, trigger)
        if task == "auth_update":
            return await self._auth_update(trigger)
        if task == "auto_switch":
            return await self._auto_switch(trigger)
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
            results = await self._refresher.refresh_accounts(due, refresh_email=True)
        except RefreshInProgressError:
            log.info("[Keepalive] metadata refresh skipped: already running")
            return {"ok": False, "reason": "refresh_in_progress", "count": 0}

        checked_at = _iso(now)
        for result in results:
            self._store.mark_refresh_attempt(
                result.account_id,
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

    async def _auth_update(self, trigger: str) -> dict[str, object]:
        pool = getattr(self._client, "instance_pool", None)
        if pool is not None:
            save_pool = cast(
                "Callable[[], Awaitable[int]] | None",
                getattr(self._client, "save_pool_cookies", None),
            )
            saved = int(await save_pool()) if save_pool is not None else 0
            saved_accounts: list[str] = []
            for slot in pool.slots():
                account_id = slot.current_account_id
                if not account_id or not slot.chat_ready():
                    continue
                self._store.mark_auth_saved(account_id)
                saved_accounts.append(account_id)
            if saved_accounts:
                log.info(
                    "[Keepalive] auth update trigger=%s slots=%s saved=%d",
                    trigger,
                    saved_accounts,
                    saved,
                )
                return {
                    "ok": True,
                    "reason": "saved",
                    "count": len(saved_accounts),
                    "accounts": saved_accounts,
                }

        account = self._account_service.get_active_account()
        if account is None:
            return {"ok": False, "reason": "no_active_account"}
        if getattr(account, "expired", False):
            return {"ok": False, "reason": "credential_expired", "account": account.id}

        saved = await self._client.save_current_cookies()
        if not saved:
            return {"ok": False, "reason": "no_active_page", "account": account.id}
        self._store.mark_auth_saved(account.id)
        log.info(
            "[Keepalive] auth update trigger=%s account=%s saved=1",
            trigger,
            account.id,
        )
        return {"ok": True, "reason": "saved", "account": account.id}

    async def _auto_switch(self, trigger: str) -> dict[str, object]:
        pool = getattr(self._client, "instance_pool", None)
        if pool is not None:
            from aistudio_api.application.instance_switch import InstanceSwitcher

            switcher = InstanceSwitcher(
                pool,
                self._store,
                self._account_service,
                features_provider=get_features_config,
            )
            result = await switcher.rebalance()
            log.info(
                "[Keepalive] auto switch trigger=%s pool=%s",
                trigger,
                result,
            )
            return result

        if self._client.in_flight_count > 0:
            log.info(
                "[Keepalive] auto switch skipped: in_flight=%d",
                self._client.in_flight_count,
            )
            return {
                "ok": False,
                "reason": "in_flight",
                "in_flight": self._client.in_flight_count,
            }

        current = self._account_service.get_active_account()
        candidate = await self._rotator.get_keepalive_candidate(
            current.id if current is not None else None
        )
        if candidate is None:
            return {"ok": False, "reason": "no_candidate"}

        switch = self._switch_cb
        if switch is None:
            from aistudio_api.application.account_orchestrator import switch_to_account

            switch = switch_to_account
        ok = await switch(candidate.id)
        if ok:
            log.info(
                "[Keepalive] auto switch trigger=%s from=%s to=%s",
                trigger,
                current.id if current is not None else None,
                candidate.id,
            )
            return {
                "ok": True,
                "reason": "switched",
                "from": current.id if current is not None else None,
                "to": candidate.id,
            }
        return {"ok": False, "reason": "switch_failed", "to": candidate.id}

    async def _active_activity(self, trigger: str) -> dict[str, object]:
        if self._client.in_flight_count > 0:
            return {"ok": False, "reason": "in_flight"}
        page = self._client.session.page
        if page is None or page.is_closed():
            return {"ok": False, "reason": "no_active_page"}
        await page.evaluate("() => { window.scrollBy(0, 2); return true; }")
        self._client.session.touch_activity()
        log.info("[Keepalive] active activity trigger=%s", trigger)
        return {"ok": True, "reason": "activity"}

    def status(self) -> dict[str, object]:
        settings = self._settings()
        chat = self._client.session.in_flight_count
        build = self._client.build_in_flight_count
        pool = getattr(self._client, "instance_pool", None)
        pool_in_flight = sum(slot.in_flight for slot in pool.slots()) if pool else 0
        return {
            "enabled": {
                "auto_switch": settings.auto_switch,
                "auth_update": settings.auth_update,
                "metadata_refresh": settings.metadata_refresh,
                "active_activity": settings.active_activity,
            },
            "intervals": {
                "auto_switch_hours": settings.auto_switch_interval_hours,
                "auth_update_hours": settings.auth_update_hours,
                "metadata_refresh_hours": settings.metadata_refresh_hours,
                "active_activity_seconds": settings.active_activity_seconds,
            },
            "next_due": {
                key: _iso(value) for key, value in sorted(self._next_due.items())
            },
            "running": self._running,
            "last_results": dict(self._last_results),
            "last_error": self._last_error,
            "in_flight": {
                "chat": chat,
                "build": build,
                "pool": pool_in_flight,
                "total": chat + build + pool_in_flight,
            },
            "pool_slots": pool.snapshot() if pool is not None else [],
        }
