"""Shared API runtime state."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from aistudio_api.config import resolve_stats_file, settings
from aistudio_api.infrastructure.utils.common import atomic_write_json

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_info_refresher import (
        AccountInfoRefresher,
    )
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("state")


def discovery_sessions_for(state: object) -> tuple[Any, ...]:
    """Resolve ordered discovery sessions (ready pool lane first)."""
    manager = getattr(state, "instance_manager", None)
    getter = getattr(manager, "discovery_sessions", None)
    if callable(getter):
        try:
            sessions: Any = getter()
        except Exception as exc:
            logger.debug("discovery sessions resolution failed: %s", exc)
            sessions = ()
        if sessions:
            return tuple(session for session in sessions if session is not None)
    single = discovery_session_for(state)
    return (single,) if single is not None else ()


def discovery_session_for(state: object) -> Any | None:
    """Resolve the session used by read-only model discovery.

    A ready pool session always wins over the control session; the control
    session is only a cold-start fallback before any instance is READY.
    Duck-typed so route tests can pass a lightweight state object.
    """
    manager = getattr(state, "instance_manager", None)
    getter = getattr(manager, "discovery_session", None)
    if callable(getter):
        try:
            session = getter()
        except Exception as exc:
            logger.debug("discovery session resolution failed: %s", exc)
            session = None
        if session is not None:
            return session
    client = getattr(state, "client", None)
    if client is None:
        return None
    return getattr(client, "_session", None)


@dataclass
class ModelStatsItem:
    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    finish_reasons: dict[str, int] = field(default_factory=dict)
    last_used: str | None = None


@dataclass
class RuntimeState:
    client: AIStudioClient | None = None
    browser_port: int = 9222
    account_service: AccountService | None = None
    account_refresher: AccountInfoRefresher | None = None
    rotator: AccountRotator | None = None
    instance_manager: object | None = None
    routing_pipeline: object | None = None
    keepalive: object | None = None
    ui_debug: bool = False
    ui_sandbox: object | None = None
    model_stats: dict[str, ModelStatsItem] = field(
        default_factory=lambda: defaultdict(ModelStatsItem)
    )
    _last_saved_time: float = field(default=0.0, repr=False)
    _save_task: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.load_stats()

    def discovery_session(self) -> Any | None:
        """Resolve the session used by read-only model discovery."""
        return discovery_session_for(self)

    def discovery_sessions(self) -> tuple[Any, ...]:
        """READY pool sessions for startup discovery; never the control session."""
        manager = getattr(self, "instance_manager", None)
        getter = getattr(manager, "discovery_sessions", None)
        if not callable(getter):
            return ()
        try:
            sessions: Any = getter()
        except Exception as exc:
            logger.debug("discovery sessions resolution failed: %s", exc)
            return ()
        return tuple(session for session in sessions if session is not None)

    def load_stats(self) -> None:
        """从持久化文件加载历史调用统计。"""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        if not stats_path.is_file():
            return
        try:
            raw = stats_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                for model, item in data.items():
                    if isinstance(item, dict):
                        raw_finish = item.get("finish_reasons")
                        finish_reasons = (
                            {str(k): int(str(v)) for k, v in raw_finish.items()}
                            if isinstance(raw_finish, dict)
                            else {}
                        )
                        self.model_stats[model] = ModelStatsItem(
                            requests=int(item.get("requests", 0)),
                            success=int(item.get("success", 0)),
                            rate_limited=int(item.get("rate_limited", 0)),
                            errors=int(item.get("errors", 0)),
                            prompt_tokens=int(item.get("prompt_tokens", 0)),
                            completion_tokens=int(item.get("completion_tokens", 0)),
                            reasoning_tokens=int(item.get("reasoning_tokens", 0)),
                            cached_tokens=int(item.get("cached_tokens", 0)),
                            total_tokens=int(item.get("total_tokens", 0)),
                            finish_reasons=finish_reasons,
                            last_used=str(item["last_used"])
                            if item.get("last_used")
                            else None,
                        )
        except Exception as e:
            logger.warning("从 %s 读取模型统计失败: %s", stats_path, e)

    def save_stats(self) -> None:
        """将模型调用与 Token 统计持久化到文件。"""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        try:
            payload = {model: asdict(item) for model, item in self.model_stats.items()}
            atomic_write_json(stats_path, payload)
            import time

            self._last_saved_time = time.time()
        except Exception as e:
            logger.warning("持久化模型统计到 %s 失败: %s", stats_path, e)

    def _schedule_debounced_save(self, delay: float = 2.0) -> None:
        if not settings.persist_stats:
            return
        import asyncio
        import time

        now = time.time()
        if now - self._last_saved_time >= 5.0:
            self.save_stats()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save_stats()
            return

        task = getattr(self, "_save_task", None)
        if task is not None and not task.done():
            return

        async def _delayed():
            await asyncio.sleep(delay)
            self.save_stats()

        self._save_task = loop.create_task(_delayed())

    def record(
        self,
        model: str,
        result: str,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        stats = self.model_stats[model]
        stats.requests += 1
        tz = timezone(timedelta(hours=8))
        stats.last_used = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        if result == "success":
            stats.success += 1
        elif result == "rate_limited":
            stats.rate_limited += 1
        elif result == "errors":
            stats.errors += 1

        if finish_reason:
            clean_fr = str(finish_reason).strip().upper()
            stats.finish_reasons[clean_fr] = stats.finish_reasons.get(clean_fr, 0) + 1

        if usage:
            pt = int(str(usage.get("prompt_tokens") or 0))
            ct = int(str(usage.get("completion_tokens") or 0))
            tt = int(str(usage.get("total_tokens") or 0))
            details = usage.get("completion_tokens_details")
            rt = 0
            if isinstance(details, dict):
                rt = int(str(details.get("reasoning_tokens") or 0))
            cached = int(str(usage.get("cached_tokens") or 0))

            stats.prompt_tokens += pt
            stats.completion_tokens += ct
            stats.reasoning_tokens += rt
            stats.cached_tokens += cached
            stats.total_tokens += tt if tt else (pt + ct)

        if result in ("rate_limited", "errors"):
            self.save_stats()
        else:
            self._schedule_debounced_save()


runtime_state = RuntimeState()
