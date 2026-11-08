"""Shared API runtime state and statistics ledger.

Two ledgers are kept strictly apart:

* **attempt** 鈥?every real upstream execution.  ``RuntimeState.record`` and
  ``RoutingPipeline.record_success``/``_record_error`` write this ledger.
* **client** 鈥?one terminal outcome per client generation request, even when
  the request was rescued by a same-account retry or an account switch.

``countTokens`` requests live in their own ``count_tokens`` ledger and never
enter either generation denominator, nor the token totals.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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
STATS_DAILY_RETENTION_DAYS = 90
STATS_DAILY_WINDOW_DAYS = 90
SHANGHAI_TZ = timezone(timedelta(hours=8))
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
_STATS_ENVELOPE_KEYS = frozenset(
    {"schema_version", "models", "daily", "count_tokens"}
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


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(str(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def success_rate(success: int, total: int) -> float | None:
    """Percentage success rate rounded to 2 decimals, ``None`` on 0/0."""
    if total <= 0:
        return None
    return round(success / total * 100, 2)


@dataclass
class ClientStats:
    """One terminal outcome per client generation request."""

    requests: int = 0
    success: int = 0
    errors: int = 0
    aborted: int = 0
    rate_limited: int = 0
    rescued_requests: int = 0

    @property
    def success_rate(self) -> float | None:
        return success_rate(self.success, self.requests)

    def to_dict(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "success": self.success,
            "errors": self.errors,
            "aborted": self.aborted,
            "rate_limited": self.rate_limited,
            "rescued_requests": self.rescued_requests,
            "success_rate": self.success_rate,
        }

    @classmethod
    def from_dict(cls, raw: object) -> ClientStats:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            requests=_int_value(raw.get("requests")),
            success=_int_value(raw.get("success")),
            errors=_int_value(raw.get("errors")),
            aborted=_int_value(raw.get("aborted")),
            rate_limited=_int_value(raw.get("rate_limited")),
            rescued_requests=_int_value(raw.get("rescued_requests")),
        )

    def add(self, other: ClientStats) -> None:
        self.requests += other.requests
        self.success += other.success
        self.errors += other.errors
        self.aborted += other.aborted
        self.rate_limited += other.rate_limited
        self.rescued_requests += other.rescued_requests

    def record(self, result: str, *, rescued: bool = False) -> None:
        self.requests += 1
        if result == "success":
            self.success += 1
            if rescued:
                self.rescued_requests += 1
        elif result == "rate_limited":
            self.rate_limited += 1
        elif result == "aborted":
            self.aborted += 1
        else:
            self.errors += 1


@dataclass
class AttemptStats:
    """Every real upstream execution attempt."""

    attempts: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0

    @property
    def success_rate(self) -> float | None:
        return success_rate(self.success, self.attempts)

    def to_dict(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "success": self.success,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
            "success_rate": self.success_rate,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AttemptStats:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            attempts=_int_value(raw.get("attempts")),
            success=_int_value(raw.get("success")),
            rate_limited=_int_value(raw.get("rate_limited")),
            errors=_int_value(raw.get("errors")),
        )

    def add(self, other: AttemptStats) -> None:
        self.attempts += other.attempts
        self.success += other.success
        self.rate_limited += other.rate_limited
        self.errors += other.errors


@dataclass
class CountTokensStats:
    """Isolated ``countTokens`` ledger (never a generation denominator)."""

    requests: int = 0
    success: int = 0
    errors: int = 0
    input_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "success": self.success,
            "errors": self.errors,
            "input_tokens": self.input_tokens,
        }

    @classmethod
    def from_dict(cls, raw: object) -> CountTokensStats:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            requests=_int_value(raw.get("requests")),
            success=_int_value(raw.get("success")),
            errors=_int_value(raw.get("errors")),
            input_tokens=_int_value(raw.get("input_tokens")),
        )


@dataclass
class ModelStatsItem:
    """Per-model attempt counters plus the client terminal summary."""

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
    client: ClientStats = field(default_factory=ClientStats)

    @property
    def attempt(self) -> AttemptStats:
        return AttemptStats(
            attempts=self.requests,
            success=self.success,
            rate_limited=self.rate_limited,
            errors=self.errors,
        )


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
        client=ClientStats.from_dict(raw.get("client")),
    )


def _item_to_dict(item: ModelStatsItem) -> dict[str, object]:
    return {
        "requests": item.requests,
        "success": item.success,
        "rate_limited": item.rate_limited,
        "errors": item.errors,
        "prompt_tokens": item.prompt_tokens,
        "completion_tokens": item.completion_tokens,
        "reasoning_tokens": item.reasoning_tokens,
        "cached_tokens": item.cached_tokens,
        "total_tokens": item.total_tokens,
        "finish_reasons": dict(item.finish_reasons),
        "last_used": item.last_used,
        "client": item.client.to_dict(),
    }


def _clone_item(item: ModelStatsItem) -> ModelStatsItem:
    return ModelStatsItem(
        requests=item.requests,
        success=item.success,
        rate_limited=item.rate_limited,
        errors=item.errors,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        reasoning_tokens=item.reasoning_tokens,
        cached_tokens=item.cached_tokens,
        total_tokens=item.total_tokens,
        finish_reasons=dict(item.finish_reasons),
        last_used=item.last_used,
        client=ClientStats(
            requests=item.client.requests,
            success=item.client.success,
            errors=item.client.errors,
            aborted=item.client.aborted,
            rate_limited=item.client.rate_limited,
            rescued_requests=item.client.rescued_requests,
        ),
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
    target.client.add(item.client)
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


def daily_key(moment: datetime | None = None) -> str:
    """Shanghai natural-day key (``YYYY-MM-DD``)."""
    current = moment.astimezone(SHANGHAI_TZ) if moment else datetime.now(SHANGHAI_TZ)
    return current.strftime("%Y-%m-%d")


def daily_keys(days: int, *, today: date | None = None) -> list[str]:
    """Consecutive Shanghai date keys ending today, ascending."""
    window = max(1, min(int(days or 1), STATS_DAILY_WINDOW_DAYS))
    end = today or datetime.now(SHANGHAI_TZ).date()
    return [
        (end - timedelta(days=offset)).isoformat()
        for offset in range(window - 1, -1, -1)
    ]


def prune_daily_map(
    daily: dict[str, Any],
    *,
    today: date | None = None,
) -> bool:
    """Drop daily buckets older than the retention window; True if changed."""
    end = today or datetime.now(SHANGHAI_TZ).date()
    cutoff = end - timedelta(days=STATS_DAILY_RETENTION_DAYS - 1)
    removed = False
    for key in list(daily):
        try:
            moment = date.fromisoformat(str(key))
        except ValueError:
            daily.pop(key, None)
            removed = True
            continue
        if moment < cutoff:
            daily.pop(key, None)
            removed = True
    return removed


def _normalized_models(
    raw: Mapping[str, ModelStatsItem],
) -> dict[str, ModelStatsItem]:
    merged: dict[str, ModelStatsItem] = {}
    for name, item in raw.items():
        normalized = normalize_model_name(name)
        if not normalized or is_reserved_stats_key(normalized):
            continue
        if is_reserved_stats_key(name):
            continue
        existing = merged.get(normalized)
        if existing is None:
            merged[normalized] = _clone_item(item)
        else:
            _add_item(existing, item)
    return merged


def aggregate_clients(items: Iterable[ModelStatsItem]) -> ClientStats:
    total = ClientStats()
    for item in items:
        total.add(item.client)
    return total


def aggregate_attempts(items: Iterable[ModelStatsItem]) -> AttemptStats:
    total = AttemptStats()
    for item in items:
        total.add(item.attempt)
    return total


def _tokens_payload(items: Iterable[ModelStatsItem]) -> dict[str, int]:
    prompt = completion = reasoning = cached = total = 0
    for item in items:
        prompt += item.prompt_tokens
        completion += item.completion_tokens
        reasoning += item.reasoning_tokens
        cached += item.cached_tokens
        total += item.total_tokens
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
    }


def _totals_payload(items: Iterable[ModelStatsItem]) -> dict[str, int]:
    aggregate = {
        "requests": 0,
        "success": 0,
        "rate_limited": 0,
        "errors": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
    }
    for item in items:
        aggregate["requests"] += item.requests
        aggregate["success"] += item.success
        aggregate["rate_limited"] += item.rate_limited
        aggregate["errors"] += item.errors
        aggregate["prompt_tokens"] += item.prompt_tokens
        aggregate["completion_tokens"] += item.completion_tokens
        aggregate["reasoning_tokens"] += item.reasoning_tokens
        aggregate["cached_tokens"] += item.cached_tokens
        aggregate["total_tokens"] += item.total_tokens
    return aggregate


def build_stats_payload(
    *,
    model_stats: Mapping[str, ModelStatsItem],
    daily_stats: Mapping[str, Mapping[str, ModelStatsItem]],
    count_tokens: CountTokensStats,
    days: int | None = None,
    quota_scope: str = "account",
    model_extra: Callable[[str], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Render the frozen ``/stats`` contract from either real or sandbox state.

    ``days=None`` keeps the legacy all-time summary and returns the last 90
    natural days.  ``days=N`` scopes ``totals``/``client``/``attempt`` to that
    window and returns exactly ``N`` consecutive daily points.
    """
    models = _normalized_models(model_stats)
    window = STATS_DAILY_WINDOW_DAYS if days is None else max(
        1, min(int(days), STATS_DAILY_WINDOW_DAYS)
    )
    keys = daily_keys(window)

    if days is None:
        summary_items = list(models.values())
    else:
        summary_items = [
            item
            for key in keys
            for item in _normalized_models(daily_stats.get(key, {})).values()
        ]

    model_payloads: dict[str, dict[str, object]] = {}
    for name, item in models.items():
        row = _item_to_dict(item)
        row["attempt"] = item.attempt.to_dict()
        if callable(model_extra):
            row.update(model_extra(name))
        model_payloads[name] = row

    daily_payload: list[dict[str, object]] = []
    for key in keys:
        bucket = _normalized_models(daily_stats.get(key, {}))
        items = list(bucket.values())
        daily_payload.append(
            {
                "date": key,
                "client": aggregate_clients(items).to_dict(),
                "attempt": aggregate_attempts(items).to_dict(),
                "tokens": _tokens_payload(items),
                "by_model": {
                    name: {
                        "client": item.client.to_dict(),
                        "attempt": item.attempt.to_dict(),
                        "tokens": _tokens_payload([item]),
                    }
                    for name, item in bucket.items()
                },
            }
        )

    return {
        "models": model_payloads,
        "totals": _totals_payload(summary_items),
        "quota_scope": quota_scope,
        "client": aggregate_clients(summary_items).to_dict(),
        "attempt": aggregate_attempts(summary_items).to_dict(),
        "daily": daily_payload,
        "count_tokens": count_tokens.to_dict(),
    }


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
    count_tokens: CountTokensStats = field(default_factory=CountTokensStats)
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
            is_v2 = any(key in data for key in ("schema_version", "models", "daily"))
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
                self.count_tokens = CountTokensStats.from_dict(
                    data.get("count_tokens")
                )
                extras = set(data) - _STATS_ENVELOPE_KEYS
                pruned = prune_daily_map(self.daily_stats)
                needs_migration = bool(
                    needs_migration
                    or extras
                    or models_ghosts
                    or _reserved_daily_ghosts(raw_daily)
                    or pruned
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
            logger.warning("浠?%s 璇诲彇妯″瀷缁熻澶辫触: %s", stats_path, e)

    def _migrate_stats_file(self, stats_path: Path) -> None:
        """Back up the old file, then atomically rewrite a clean v2 envelope."""
        try:
            if stats_path.is_file():
                shutil.copy2(
                    stats_path,
                    stats_path.with_name(f"{stats_path.name}.bak"),
                )
            self.save_stats()
            logger.info("妯″瀷缁熻鏂囦欢宸茶縼绉讳负 v2 骞插噣缁撴瀯: %s", stats_path)
        except Exception as e:
            # Never clear history: in-memory stats are already loaded; the
            # original file (and backup when written) remains untouched.
            logger.warning("妯″瀷缁熻鏂囦欢杩佺Щ澶辫触锛屼繚鐣欏師鏂囦欢涓庡唴瀛樻暟鎹? %s", e)

    def save_stats(self) -> None:
        """Persist statistics as a clean schema v2 envelope (no ghost keys)."""
        if not settings.persist_stats:
            return
        stats_path = resolve_stats_file()
        try:
            prune_daily_map(self.daily_stats)
            payload = {
                "schema_version": STATS_SCHEMA_VERSION,
                "models": {
                    name: _item_to_dict(item)
                    for name, item in self.model_stats.items()
                    if name and not is_reserved_stats_key(name)
                },
                "daily": {
                    day: {
                        name: _item_to_dict(item)
                        for name, item in models.items()
                        if name and not is_reserved_stats_key(name)
                    }
                    for day, models in self.daily_stats.items()
                    if day and not is_reserved_stats_key(day)
                },
                "count_tokens": self.count_tokens.to_dict(),
            }
            atomic_write_json(stats_path, payload)
            import time

            self._last_saved_time = time.time()
        except Exception as e:
            logger.warning("鎸佷箙鍖栨ā鍨嬬粺璁″埌 %s 澶辫触: %s", stats_path, e)

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

    def _record_targets(self, name: str, last_used: str) -> tuple[ModelStatsItem, ...]:
        daily = self.daily_stats.setdefault(daily_key(), {})
        targets = (
            self.model_stats[name],
            daily.setdefault(name, ModelStatsItem()),
        )
        for stats in targets:
            stats.last_used = last_used
        return targets

    def _record_attempt(
        self,
        model: str,
        result: str,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        name = normalize_model_name(model) or model
        now = datetime.now(SHANGHAI_TZ)
        last_used = now.strftime("%Y-%m-%d %H:%M:%S")
        clean_fr = str(finish_reason).strip().upper() if finish_reason else ""
        for stats in self._record_targets(name, last_used):
            stats.requests += 1

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

    def record(
        self,
        model: str,
        result: str,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        """Record one **attempt** (real upstream execution) outcome."""
        self._record_attempt(
            model, result, usage=usage, finish_reason=finish_reason
        )

    def record_client(
        self,
        model: str,
        result: str,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
        rescued: bool = False,
    ) -> None:
        """Record the single terminal outcome of one client request.

        Tokens are intentionally *not* added here: they belong to the attempt
        ledger so daily/model token totals are never double counted.
        """
        del usage, finish_reason
        name = normalize_model_name(model) or model
        now = datetime.now(SHANGHAI_TZ)
        last_used = now.strftime("%Y-%m-%d %H:%M:%S")
        for stats in self._record_targets(name, last_used):
            stats.client.record(result, rescued=rescued and result == "success")

        if result in ("errors", "aborted"):
            self.save_stats()
        else:
            self._schedule_debounced_save()

    def record_count_tokens(
        self,
        model: str | None = None,
        result: str = "success",
        *,
        input_tokens: int = 0,
    ) -> None:
        """Record one isolated ``countTokens`` request terminal."""
        del model
        self.count_tokens.requests += 1
        if result == "success":
            self.count_tokens.success += 1
            self.count_tokens.input_tokens += max(0, int(input_tokens))
            self._schedule_debounced_save()
        else:
            self.count_tokens.errors += 1
            self.save_stats()


runtime_state = RuntimeState()
