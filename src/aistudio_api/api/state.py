"""Shared API runtime state."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from aistudio_api.config import resolve_stats_file, settings
from aistudio_api.infrastructure.utils.common import atomic_write_json

if TYPE_CHECKING:
    from aistudio_api.application.account_keepalive import AccountKeepaliveService
    from aistudio_api.application.account_rotator import AccountRotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.infrastructure.account.account_info_refresher import (
        AccountInfoRefresher,
    )
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("state")

STATS_SCHEMA_VERSION = 2
STATS_RETENTION_DAYS = 90
STATS_TIMEZONE = timezone(timedelta(hours=8))
_CHANNEL_PREFIXES = {"pg", "chat", "playground", "build", "official"}


def normalize_model_name(value: object) -> str:
    """Normalize persisted model names (``models/`` and channel prefixes)."""
    text = str(value or "").strip().removeprefix("models/")
    head, separator, tail = text.partition("/")
    if separator and tail and head.lower() in _CHANNEL_PREFIXES:
        return tail
    return text


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _counter(value: object) -> int:
    return _optional_int(value) or 0


def _add_optional(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    if current is None:
        return value
    return current + value


@dataclass
class StatsCounters:
    """Scalar counters for one aggregate bucket."""

    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    client_requests: int | None = None
    client_success: int | None = None
    client_errors: int | None = None
    client_rate_limited: int | None = None
    client_aborted: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int = 0
    count_requests: int | None = None
    count_success: int | None = None
    count_errors: int | None = None
    count_input_tokens: int | None = None
    usage_missing: int = 0
    last_used: str | None = None

    @property
    def attempts(self) -> int:
        return self.requests

    @property
    def cache_hit_rate(self) -> float | None:
        cached = self.cache_read_tokens
        if cached is None or self.prompt_tokens <= 0:
            return None
        return cached / self.prompt_tokens

    def add(self, other: StatsCounters) -> None:
        self.requests += other.requests
        self.success += other.success
        self.rate_limited += other.rate_limited
        self.errors += other.errors
        for name in (
            "client_requests",
            "client_success",
            "client_errors",
            "client_rate_limited",
            "client_aborted",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "count_requests",
            "count_success",
            "count_errors",
            "count_input_tokens",
        ):
            setattr(
                self,
                name,
                _add_optional(getattr(self, name), getattr(other, name)),
            )
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.usage_missing += other.usage_missing
        if other.last_used and (
            self.last_used is None or other.last_used > self.last_used
        ):
            self.last_used = other.last_used


@dataclass
class ModelStatsItem(StatsCounters):
    formats: dict[str, StatsCounters] = field(default_factory=dict)
    surfaces: dict[str, StatsCounters] = field(default_factory=dict)

    def add(self, other: StatsCounters) -> None:
        super().add(other)
        if isinstance(other, ModelStatsItem):
            for name, bucket in other.formats.items():
                self.formats.setdefault(name, StatsCounters()).add(bucket)
            for name, bucket in other.surfaces.items():
                self.surfaces.setdefault(name, StatsCounters()).add(bucket)


def _load_counters(data: object) -> StatsCounters | None:
    if not isinstance(data, dict):
        return None
    return StatsCounters(
        requests=_counter(data.get("requests")),
        success=_counter(data.get("success")),
        rate_limited=_counter(data.get("rate_limited")),
        errors=_counter(data.get("errors")),
        client_requests=_optional_int(data.get("client_requests")),
        client_success=_optional_int(data.get("client_success")),
        client_errors=_optional_int(data.get("client_errors")),
        client_rate_limited=_optional_int(data.get("client_rate_limited")),
        client_aborted=_optional_int(data.get("client_aborted")),
        prompt_tokens=_counter(data.get("prompt_tokens")),
        completion_tokens=_counter(data.get("completion_tokens")),
        reasoning_tokens=_optional_int(data.get("reasoning_tokens")),
        cache_read_tokens=_optional_int(data.get("cache_read_tokens")),
        cache_write_tokens=_optional_int(data.get("cache_write_tokens")),
        total_tokens=_counter(data.get("total_tokens")),
        count_requests=_optional_int(data.get("count_requests")),
        count_success=_optional_int(data.get("count_success")),
        count_errors=_optional_int(data.get("count_errors")),
        count_input_tokens=_optional_int(data.get("count_input_tokens")),
        usage_missing=_counter(data.get("usage_missing")),
        last_used=str(data["last_used"]) if data.get("last_used") else None,
    )


def _load_breakdown(value: object) -> dict[str, StatsCounters]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, StatsCounters] = {}
    for name, raw in value.items():
        if isinstance(raw, int):
            result[str(name)] = StatsCounters(requests=raw)
            continue
        counters = _load_counters(raw)
        if counters is not None:
            result[str(name)] = counters
    return result


def _load_item(data: object) -> ModelStatsItem | None:
    counters = _load_counters(data)
    if counters is None or not isinstance(data, dict):
        return None
    return ModelStatsItem(
        **asdict(counters),
        formats=_load_breakdown(data.get("formats")),
        surfaces=_load_breakdown(data.get("surfaces")),
    )


def _merge_item(
    target: dict[str, ModelStatsItem], name: str, item: ModelStatsItem
) -> None:
    normalized = normalize_model_name(name)
    if not normalized:
        return
    existing = target.get(normalized)
    if existing is None:
        target[normalized] = item
    else:
        existing.add(item)


def _apply_usage(target: StatsCounters, usage: dict[str, object] | None) -> None:
    if not usage:
        target.usage_missing += 1
        return
    prompt = _optional_int(usage.get("prompt_tokens"))
    completion = _optional_int(usage.get("completion_tokens"))
    total = _optional_int(usage.get("total_tokens"))
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    cached = _optional_int(usage.get("cached_tokens"))
    if cached is None and isinstance(prompt_details, dict):
        cached = _optional_int(prompt_details.get("cached_tokens"))
    reasoning = _optional_int(usage.get("reasoning_tokens"))
    if reasoning is None and isinstance(completion_details, dict):
        reasoning = _optional_int(completion_details.get("reasoning_tokens"))
    cache_write = _optional_int(usage.get("cache_creation_input_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    if prompt is None and completion is None and total is None:
        target.usage_missing += 1
        return
    if prompt is not None:
        target.prompt_tokens += prompt
    if completion is not None:
        target.completion_tokens += completion
    if total is not None:
        target.total_tokens += total
    target.reasoning_tokens = _add_optional(target.reasoning_tokens, reasoning)
    target.cache_read_tokens = _add_optional(target.cache_read_tokens, cached)
    target.cache_write_tokens = _add_optional(target.cache_write_tokens, cache_write)


def _apply_attempt(
    target: StatsCounters,
    *,
    result: str,
    usage: dict[str, object] | None,
    last_used: str,
) -> None:
    target.requests += 1
    target.last_used = last_used
    if result == "success":
        target.success += 1
        _apply_usage(target, usage)
    elif result == "rate_limited":
        target.rate_limited += 1
    elif result == "errors":
        target.errors += 1


def _apply_client(target: StatsCounters, outcome: str, last_used: str) -> None:
    target.client_requests = _add_optional(target.client_requests, 1)
    target.last_used = last_used
    if outcome == "success":
        target.client_success = _add_optional(target.client_success, 1)
    elif outcome == "aborted":
        target.client_aborted = _add_optional(target.client_aborted, 1)
    elif outcome == "rate_limited":
        target.client_errors = _add_optional(target.client_errors, 1)
        target.client_rate_limited = _add_optional(target.client_rate_limited, 1)
    else:
        target.client_errors = _add_optional(target.client_errors, 1)


def _apply_count(
    target: StatsCounters,
    *,
    outcome: str,
    total_tokens: int | None,
    last_used: str,
) -> None:
    target.count_requests = _add_optional(target.count_requests, 1)
    target.last_used = last_used
    if outcome == "success":
        target.count_success = _add_optional(target.count_success, 1)
        target.count_input_tokens = _add_optional(
            target.count_input_tokens, total_tokens or 0
        )
    else:
        target.count_errors = _add_optional(target.count_errors, 1)


def _bucket(bucket_map: dict[str, StatsCounters], name: str) -> StatsCounters:
    return bucket_map.setdefault(name, StatsCounters())


@dataclass
class RuntimeState:
    client: AIStudioClient | None = None
    browser_port: int = 9222
    account_service: AccountService | None = None
    rotator: AccountRotator | None = None
    account_refresher: AccountInfoRefresher | None = None
    keepalive: AccountKeepaliveService | None = None
    model_stats: dict[str, ModelStatsItem] = field(
        default_factory=lambda: defaultdict(ModelStatsItem)
    )
    daily_stats: dict[str, dict[str, ModelStatsItem]] = field(default_factory=dict)
    _last_saved_time: float = field(default=0.0, repr=False)
    _save_task: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.load_stats()

    def load_stats(self) -> None:
        """Load persisted statistics, accepting v1 flat and v2 envelope files."""
        stats_path = resolve_stats_file()
        if not stats_path.is_file():
            return
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            if data.get("schema_version") == STATS_SCHEMA_VERSION:
                models = data.get("models")
                if isinstance(models, dict):
                    for name, raw in models.items():
                        item = _load_item(raw)
                        if item is not None:
                            _merge_item(self.model_stats, str(name), item)
                daily = data.get("daily")
                if isinstance(daily, dict):
                    for day, day_models in daily.items():
                        if not isinstance(day_models, dict):
                            continue
                        bucket: dict[str, ModelStatsItem] = {}
                        for name, raw in day_models.items():
                            item = _load_item(raw)
                            if item is not None:
                                _merge_item(bucket, str(name), item)
                        if bucket:
                            self.daily_stats[str(day)] = bucket
                return
            for name, raw in data.items():
                item = _load_item(raw)
                if item is not None:
                    _merge_item(self.model_stats, str(name), item)
        except Exception as e:
            logger.warning("从 %s 读取模型统计失败: %s", stats_path, e)

    def _prune_daily(self) -> None:
        cutoff = datetime.now(STATS_TIMEZONE).date() - timedelta(
            days=STATS_RETENTION_DAYS - 1
        )
        for day in list(self.daily_stats):
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                continue
            if parsed < cutoff:
                self.daily_stats.pop(day, None)

    def save_stats(self) -> None:
        """Persist all-time and daily aggregates as schema v2."""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        try:
            self._prune_daily()
            payload = {
                "schema_version": STATS_SCHEMA_VERSION,
                "models": {
                    name: asdict(item) for name, item in self.model_stats.items()
                },
                "daily": {
                    day: {name: asdict(item) for name, item in models.items()}
                    for day, models in self.daily_stats.items()
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

    def _targets(self, model: str) -> list[ModelStatsItem]:
        name = normalize_model_name(model)
        today = datetime.now(STATS_TIMEZONE).strftime("%Y-%m-%d")
        daily = self.daily_stats.setdefault(today, {})
        return [self.model_stats[name], daily.setdefault(name, ModelStatsItem())]

    def record(
        self,
        model: str,
        result: str,
        usage: dict[str, object] | None = None,
        api_format: str = "gemini",
        surface: str = "chat",
    ) -> None:
        """Record one upstream attempt (including retries)."""
        last_used = datetime.now(STATS_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        for target in self._targets(model):
            _apply_attempt(target, result=result, usage=usage, last_used=last_used)
            _apply_attempt(
                _bucket(target.formats, api_format),
                result=result,
                usage=usage,
                last_used=last_used,
            )
            if surface:
                _apply_attempt(
                    _bucket(target.surfaces, surface),
                    result=result,
                    usage=usage,
                    last_used=last_used,
                )
        if result in ("rate_limited", "errors"):
            self.save_stats()
        else:
            self._schedule_debounced_save()

    def record_client(
        self,
        model: str,
        outcome: str,
        api_format: str = "gemini",
        surface: str | None = None,
    ) -> None:
        """Record one client-level request outcome."""
        last_used = datetime.now(STATS_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        for target in self._targets(model):
            _apply_client(target, outcome, last_used)
            _apply_client(_bucket(target.formats, api_format), outcome, last_used)
            if surface:
                _apply_client(_bucket(target.surfaces, surface), outcome, last_used)
        self._schedule_debounced_save()

    def record_token_count(
        self,
        model: str,
        api_format: str = "gemini",
        surface: str | None = None,
        outcome: str = "success",
        total_tokens: int | None = None,
    ) -> None:
        """Record one client count_tokens call without touching generation stats."""
        last_used = datetime.now(STATS_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        for target in self._targets(model):
            _apply_count(
                target,
                outcome=outcome,
                total_tokens=total_tokens,
                last_used=last_used,
            )
            _apply_count(
                _bucket(target.formats, api_format),
                outcome=outcome,
                total_tokens=total_tokens,
                last_used=last_used,
            )
            if surface:
                _apply_count(
                    _bucket(target.surfaces, surface),
                    outcome=outcome,
                    total_tokens=total_tokens,
                    last_used=last_used,
                )
        self._schedule_debounced_save()

    def _resolve_range(
        self, range_key: str, start: str | None, end: str | None
    ) -> tuple[date, date, bool]:
        today = datetime.now(STATS_TIMEZONE).date()
        key = range_key or "all"
        if key == "all":
            available = sorted(self.daily_stats)
            first = date.fromisoformat(available[0]) if available else today
            return first, today, False
        if key == "today":
            return today, today, False
        if key in ("7d", "30d"):
            days = 7 if key == "7d" else 30
            return today - timedelta(days=days - 1), today, False
        if key != "custom":
            raise ValueError(f"unsupported stats range: {range_key}")
        if not start or not end:
            raise ValueError("custom range requires start and end")
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date < start_date:
            raise ValueError("end must be on or after start")
        if (end_date - start_date).days > 366:
            raise ValueError("custom range cannot exceed 366 days")
        return start_date, min(end_date, today), False

    def stats_for_range(
        self, range_key: str = "all", start: str | None = None, end: str | None = None
    ) -> tuple[dict[str, ModelStatsItem], dict[str, object]]:
        """Return models and range metadata for a requested window."""
        requested_start, requested_end, _ = self._resolve_range(range_key, start, end)
        available = sorted(self.daily_stats)
        data_start = date.fromisoformat(available[0]) if available else None
        if range_key == "all":
            models: dict[str, ModelStatsItem] = {}
            for name, item in self.model_stats.items():
                loaded = _load_item(asdict(item))
                if loaded is not None:
                    models[name] = loaded
            return models, {
                "key": "all",
                "start": data_start.isoformat() if data_start else None,
                "end": requested_end.isoformat(),
                "timezone": "Asia/Shanghai",
                "data_start": data_start.isoformat() if data_start else None,
                "retention_days": STATS_RETENTION_DAYS,
                "partial": False,
            }

        effective_start = requested_start
        partial = False
        if data_start is not None and requested_start < data_start:
            effective_start = data_start
            partial = True
        elif data_start is None:
            effective_start = requested_start

        models: dict[str, ModelStatsItem] = {}
        cursor = effective_start
        while cursor <= requested_end:
            day_models = self.daily_stats.get(cursor.isoformat())
            if day_models:
                for name, item in day_models.items():
                    target = models.setdefault(
                        normalize_model_name(name), ModelStatsItem()
                    )
                    target.add(item)
            cursor += timedelta(days=1)
        return models, {
            "key": range_key,
            "start": requested_start.isoformat(),
            "end": requested_end.isoformat(),
            "timezone": "Asia/Shanghai",
            "data_start": data_start.isoformat() if data_start else None,
            "retention_days": STATS_RETENTION_DAYS,
            "partial": partial,
        }


runtime_state = RuntimeState()
