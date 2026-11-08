"""Shared API runtime state."""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

STATS_SCHEMA_VERSION = 2
# Top-level v2 envelope keys and legacy ghost counters that must never be
# rendered as model rows.
STATS_RESERVED_KEYS = frozenset(
    {
        "models",
        "daily",
        "schema_version",
        "totals",
        "version",
        "finish_reasons",
        "count_tokens",
        "client",
        "attempt",
        "range",
        "events",
        "quota_scope",
        "updated_at",
        "account_breakdown",
    }
)
_CHANNEL_PREFIXES = {"pg", "chat", "playground", "build", "official"}


def is_reserved_stats_key(value: object) -> bool:
    """True for v2 envelope / legacy aggregate keys masquerading as models."""
    text = str(value or "").strip()
    if text.startswith("models/"):
        text = text[len("models/") :]
    return text.strip().lower() in STATS_RESERVED_KEYS


def normalize_model_name(value: object) -> str:
    """Normalize persisted model names (``models/`` and channel prefixes)."""
    text = str(value or "").strip().removeprefix("models/")
    head, separator, tail = text.partition("/")
    if separator and tail and head.lower() in _CHANNEL_PREFIXES:
        return tail
    return text


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


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(str(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _load_item(raw: object) -> ModelStatsItem | None:
    if not isinstance(raw, dict):
        return None
    raw_finish = raw.get("finish_reasons")
    finish_reasons = (
        {str(k): _int_value(v) for k, v in raw_finish.items()}
        if isinstance(raw_finish, dict)
        else {}
    )
    last_used = raw.get("last_used")
    return ModelStatsItem(
        requests=_int_value(raw.get("requests")),
        success=_int_value(raw.get("success")),
        rate_limited=_int_value(raw.get("rate_limited")),
        errors=_int_value(raw.get("errors")),
        prompt_tokens=_int_value(raw.get("prompt_tokens")),
        completion_tokens=_int_value(raw.get("completion_tokens")),
        reasoning_tokens=_int_value(raw.get("reasoning_tokens")),
        cached_tokens=_int_value(raw.get("cached_tokens")),
        total_tokens=_int_value(raw.get("total_tokens")),
        finish_reasons=finish_reasons,
        last_used=str(last_used) if last_used else None,
    )


def _add_item(target: ModelStatsItem, item: ModelStatsItem) -> None:
    target.requests += item.requests
    target.success += item.success
    target.rate_limited += item.rate_limited
    target.errors += item.errors
    target.prompt_tokens += item.prompt_tokens
    target.completion_tokens += item.completion_tokens
    target.reasoning_tokens += item.reasoning_tokens
    target.cached_tokens += item.cached_tokens
    target.total_tokens += item.total_tokens
    for reason, count in item.finish_reasons.items():
        target.finish_reasons[reason] = target.finish_reasons.get(reason, 0) + count
    if item.last_used and (
        target.last_used is None or item.last_used > target.last_used
    ):
        target.last_used = item.last_used


def _merge_item(
    target: dict[str, ModelStatsItem], name: object, item: ModelStatsItem
) -> None:
    normalized = normalize_model_name(name)
    if not normalized or is_reserved_stats_key(normalized):
        return
    existing = target.get(normalized)
    if existing is None:
        target[normalized] = item
    else:
        _add_item(existing, item)


def _reserved_daily_ghosts(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return any(is_reserved_stats_key(day) for day in value)


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
    daily_stats: dict[str, dict[str, ModelStatsItem]] = field(default_factory=dict)
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
        """Load persisted statistics from schema v2 or the legacy flat file.

        The historical bug treated every top-level key (``models``/``daily``)
        as a model, producing ghost rows with zero requests.  v2 envelopes are
        now unwrapped and any old/ghost structure is backed up and rewritten as
        a clean v2 document without dropping successfully parsed history.
        """
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        if not stats_path.is_file():
            return
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            needs_migration = False
            raw_models = data.get("models")
            raw_daily = data.get("daily")
            # A v2 envelope is identified by any envelope key.  Its payload
            # lives strictly under ``models``/``daily``; the top level must
            # never be iterated as model rows (that produced ghost rows such
            # as ``finish_reasons`` with zero requests).
            is_v2 = any(
                key in data for key in ("schema_version", "models", "daily")
            )
            if is_v2:
                models_map = raw_models if isinstance(raw_models, dict) else {}
                models_ghosts = any(
                    is_reserved_stats_key(name) for name in models_map
                )
                if "models" in data and not isinstance(raw_models, dict):
                    needs_migration = True
                for name, raw in models_map.items():
                    if is_reserved_stats_key(name):
                        continue
                    item = _load_item(raw)
                    if item is not None:
                        _merge_item(self.model_stats, name, item)
                if isinstance(raw_daily, dict):
                    for day, day_models in raw_daily.items():
                        if is_reserved_stats_key(day) or not isinstance(
                            day_models, dict
                        ):
                            needs_migration = True
                            continue
                        bucket: dict[str, ModelStatsItem] = {}
                        for name, raw in day_models.items():
                            if is_reserved_stats_key(name):
                                needs_migration = True
                                continue
                            item = _load_item(raw)
                            if item is not None:
                                _merge_item(bucket, name, item)
                        if bucket:
                            self.daily_stats[str(day)] = bucket
                extras = set(data) - {"schema_version", "models", "daily"}
                needs_migration = bool(
                    needs_migration
                    or extras
                    or models_ghosts
                    or _reserved_daily_ghosts(raw_daily)
                    or data.get("schema_version") != STATS_SCHEMA_VERSION
                )
            else:
                # Legacy v1: top-level model -> counters.  Reserved aggregate
                # keys are skipped strictly, then the file is migrated to v2.
                for name, raw in data.items():
                    if is_reserved_stats_key(name):
                        continue
                    item = _load_item(raw)
                    if item is not None:
                        _merge_item(self.model_stats, name, item)
                needs_migration = True

            if needs_migration:
                self._migrate_stats_file(stats_path)
        except Exception as e:
            logger.warning("从 %s 读取模型统计失败: %s", stats_path, e)

    def _migrate_stats_file(self, stats_path: Path) -> None:
        """Back up the old file, then atomically rewrite a clean v2 envelope."""
        try:
            if stats_path.is_file():
                shutil.copy2(
                    stats_path,
                    stats_path.with_name(f"{stats_path.name}.bak"),
                )
            self.save_stats()
            logger.info("模型统计文件已迁移为 v2 干净结构: %s", stats_path)
        except Exception as e:
            # Never clear history: in-memory stats are already loaded; the
            # original file (and backup when written) remains untouched.
            logger.warning("模型统计文件迁移失败，保留原文件与内存数据: %s", e)

    def save_stats(self) -> None:
        """Persist statistics as a clean schema v2 envelope (no ghost keys)."""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        try:
            payload = {
                "schema_version": STATS_SCHEMA_VERSION,
                "models": {
                    name: asdict(item)
                    for name, item in self.model_stats.items()
                    if name and not is_reserved_stats_key(name)
                },
                "daily": {
                    day: {
                        name: asdict(item)
                        for name, item in models.items()
                        if name and not is_reserved_stats_key(name)
                    }
                    for day, models in self.daily_stats.items()
                    if day and not is_reserved_stats_key(day)
                },
            }
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
        name = normalize_model_name(model) or model
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        last_used = now.strftime("%Y-%m-%d %H:%M:%S")
        daily = self.daily_stats.setdefault(now.strftime("%Y-%m-%d"), {})
        targets = (self.model_stats[name], daily.setdefault(name, ModelStatsItem()))
        clean_fr = str(finish_reason).strip().upper() if finish_reason else ""
        for stats in targets:
            stats.requests += 1
            stats.last_used = last_used

            if result == "success":
                stats.success += 1
            elif result == "rate_limited":
                stats.rate_limited += 1
            elif result == "errors":
                stats.errors += 1

            if clean_fr:
                stats.finish_reasons[clean_fr] = (
                    stats.finish_reasons.get(clean_fr, 0) + 1
                )

            if usage:
                pt = _int_value(usage.get("prompt_tokens"))
                ct = _int_value(usage.get("completion_tokens"))
                tt = _int_value(usage.get("total_tokens"))
                details = usage.get("completion_tokens_details")
                rt = (
                    _int_value(details.get("reasoning_tokens"))
                    if isinstance(details, dict)
                    else 0
                )
                cached = _int_value(usage.get("cached_tokens"))

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
