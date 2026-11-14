"""Account rotation and sticky dispatch for multi-account management."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aistudio_api.config import resolve_rotator_state_file, settings
from aistudio_api.domain.model_access import (
    instance_lane_for_tier,
    resolve_tier_selector,
    tier_matches_selector,
    tier_rank,
    tier_satisfies_required,
)
from aistudio_api.infrastructure.utils.common import atomic_write_json

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("rotator")


def get_pacific_date_key(ts: float | None = None) -> str:
    """获取美西太平洋时间（America/Los_Angeles）日期键值 YYYY-MM-DD。"""
    try:
        tz = ZoneInfo("America/Los_Angeles")
        dt = datetime.fromtimestamp(ts or time.time(), tz=tz)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        dt = datetime.fromtimestamp((ts or time.time()) - 28800, tz=UTC)
        return dt.strftime("%Y-%m-%d")


def get_seconds_until_pacific_midnight() -> float:
    """获取距离下一次美西太平洋时间午夜 00:00 的秒数。"""
    try:
        tz = ZoneInfo("America/Los_Angeles")
        now_dt = datetime.now(tz)
        tomorrow_dt = (now_dt + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(0.0, (tomorrow_dt - now_dt).total_seconds())
    except Exception:
        # 保底计算
        now = time.time()
        pacific_offset = -28800  # UTC-8
        now_pacific = now + pacific_offset
        seconds_in_day = 86400
        remaining = seconds_in_day - (int(now_pacific) % seconds_in_day)
        return float(remaining)


def normalize_model_key(model: str | None) -> str | None:
    """统一模型标识（去除 models/ 前缀并转小写），确保单模型统计准确隔离。"""
    if not model:
        return None
    return model.removeprefix("models/").lower()


def split_quota_key(key: str) -> tuple[str | None, str]:
    """Split a D-01 accounting key into ``(surface, model)``.

    ``quota_scope=account`` stores plain ``model`` keys; ``account_surface``
    stores ``surface::model``.
    """
    surface, separator, model = key.partition("::")
    if separator and surface in ("chat", "build") and model:
        return surface, model
    return None, key


def quota_key(
    model: str | None, *, surface: str | None = None, scope: str = "account"
) -> str:
    """Build the accounting key selected by ``quota_scope`` (D-01)."""
    norm_model = normalize_model_key(model) or "general"
    if scope == "account_surface" and surface in ("chat", "build"):
        return f"{surface}::{norm_model}"
    return norm_model


def quota_keys(
    model: str | None, *, surface: str | None = None, scope: str = "account"
) -> tuple[str, ...]:
    """Primary key plus the legacy unscoped key for readability/migration."""
    primary = quota_key(model, surface=surface, scope=scope)
    legacy = normalize_model_key(model) or "general"
    if primary == legacy:
        return (primary,)
    return (primary, legacy)


@dataclass(slots=True)
class AccountModelDailyStats:
    """单账号在特定模型当天的统计数据。"""

    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    last_used: float = 0.0
    cooldown_until: float = 0.0
    drip_mode: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountModelDailyStats:
        def _as_int(v: object) -> int:
            try:
                return int(str(v)) if v is not None else 0
            except (ValueError, TypeError):
                return 0

        def _as_float(v: object) -> float:
            try:
                return float(str(v)) if v is not None else 0.0
            except (ValueError, TypeError):
                return 0.0

        return cls(
            requests=_as_int(data.get("requests")),
            success=_as_int(data.get("success")),
            rate_limited=_as_int(data.get("rate_limited")),
            errors=_as_int(data.get("errors")),
            prompt_tokens=_as_int(data.get("prompt_tokens")),
            completion_tokens=_as_int(data.get("completion_tokens")),
            reasoning_tokens=_as_int(data.get("reasoning_tokens")),
            cached_tokens=_as_int(data.get("cached_tokens")),
            total_tokens=_as_int(data.get("total_tokens")),
            last_used=_as_float(data.get("last_used")),
            cooldown_until=_as_float(data.get("cooldown_until")),
            drip_mode=bool(data.get("drip_mode")),
        )


@dataclass
class AccountStats:
    """账号多维透视与运行调度统计（按 Account x Model x Date 原生划分）。"""

    account_id: str
    quota_scope: str = "account"
    total_requests: int = 0
    total_success: int = 0
    total_rate_limited: int = 0
    total_errors: int = 0
    auth_errors: int = 0
    last_used: float = 0.0
    last_rate_limited: float = 0.0
    last_auth_error: float = 0.0
    auth_cooldown: float = 0.0
    session_expired: bool = False
    last_session_expired: float = 0.0
    daily_date_la: str = ""
    daily_models: dict[str, AccountModelDailyStats] = field(default_factory=dict)

    # 快捷属性，保持调用方代码简洁
    @property
    def requests(self) -> int:
        return self.total_requests

    @requests.setter
    def requests(self, val: int) -> None:
        self.total_requests = val

    @property
    def success(self) -> int:
        return self.total_success

    @success.setter
    def success(self, val: int) -> None:
        self.total_success = val

    @property
    def rate_limited(self) -> int:
        return self.total_rate_limited

    @rate_limited.setter
    def rate_limited(self, val: int) -> None:
        self.total_rate_limited = val

    @property
    def errors(self) -> int:
        return self.total_errors

    @errors.setter
    def errors(self, val: int) -> None:
        self.total_errors = val

    def _check_date_reset(self, current_la: str) -> None:
        """若跨入新的一天（美西 00:00 PST），自动清零今日模型统计。"""
        if not self.daily_date_la:
            self.daily_date_la = current_la
            return
        if self.daily_date_la < current_la:
            self.daily_models.clear()
            self.daily_date_la = current_la

    def get_model_daily(
        self, model: str | None, *, surface: str | None = None
    ) -> AccountModelDailyStats:
        """获取或创建指定模型在当天的统计对象。"""
        current_la = get_pacific_date_key()
        self._check_date_reset(current_la)
        key = quota_key(model, surface=surface, scope=self.quota_scope)
        if key not in self.daily_models:
            self.daily_models[key] = AccountModelDailyStats()
        return self.daily_models[key]

    def model_requests_total(self, model: str | None) -> int:
        """Sum today's requests across every quota-scoped key for one model."""
        target = normalize_model_key(model)
        if not target:
            return 0
        return sum(
            daily.requests
            for key, daily in self.daily_models.items()
            if split_quota_key(key)[1] == target
        )

    def is_available(
        self,
        model: str | None = None,
        *,
        surface: str | None = None,
        ignore_auth_cooldown: bool = False,
    ) -> bool:
        """检查账号在指定模型下是否可用（软限额超标不禁用，仅上游真 429 冷却与登录态失效不可用）。"""
        now = time.time()
        if self.session_expired:
            return False
        if not ignore_auth_cooldown and self.auth_cooldown > now:
            return False
        current_la = get_pacific_date_key(now)
        self._check_date_reset(current_la)

        if model:
            for key in quota_keys(model, surface=surface, scope=self.quota_scope):
                m_stats = self.daily_models.get(key)
                if m_stats is not None and m_stats.cooldown_until > now:
                    return False

        return True

    def get_cooldown_remaining(
        self, model: str | None = None, *, surface: str | None = None
    ) -> float:
        """获取距离配额刷新的剩余秒数。"""
        if self.is_available(model, surface=surface):
            return 0.0
        now = time.time()
        if self.auth_cooldown > now:
            return max(0.0, self.auth_cooldown - now)
        if model:
            remaining = 0.0
            for key in quota_keys(model, surface=surface, scope=self.quota_scope):
                m_stats = self.daily_models.get(key)
                if m_stats is not None and m_stats.cooldown_until > now:
                    remaining = max(remaining, m_stats.cooldown_until - now)
            if remaining > 0:
                return remaining
        return get_seconds_until_pacific_midnight()

    def record_success(
        self,
        model: str | None = None,
        usage: dict[str, object] | None = None,
        *,
        surface: str | None = None,
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_success += 1
        self.last_used = now
        self.auth_errors = 0
        self.auth_cooldown = 0.0

        m_stats = self.get_model_daily(model, surface=surface)
        m_stats.requests += 1
        m_stats.success += 1
        m_stats.last_used = now
        m_stats.cooldown_until = 0.0
        if not m_stats.drip_mode:
            m_stats.rate_limited = 0

        if usage:
            pt = int(str(usage.get("prompt_tokens") or 0))
            ct = int(str(usage.get("completion_tokens") or 0))
            tt = int(str(usage.get("total_tokens") or 0))
            details = usage.get("completion_tokens_details")
            rt = 0
            if isinstance(details, dict):
                rt = int(str(details.get("reasoning_tokens") or 0))
            cached = int(str(usage.get("cached_tokens") or 0))
            m_stats.prompt_tokens += pt
            m_stats.completion_tokens += ct
            m_stats.reasoning_tokens += rt
            m_stats.cached_tokens += cached
            m_stats.total_tokens += tt if tt else (pt + ct)

    def record_rate_limited(
        self, model: str | None = None, *, surface: str | None = None
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_rate_limited += 1
        self.last_rate_limited = now
        cooldown_seconds = get_seconds_until_pacific_midnight()

        m_stats = self.get_model_daily(model, surface=surface)
        m_stats.requests += 1
        m_stats.rate_limited += 1
        m_stats.last_used = now

        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_rotator_settings,
        )

        rot_cfg = get_configured_rotator_settings()
        cooldowns = rot_cfg["cooldowns"]
        if not isinstance(cooldowns, dict):
            raise RuntimeError("rotator 配置缺少 cooldowns 配置块")
        cd_short = float(str(cooldowns["rate_limited_short"]))
        cd_drip = float(str(cooldowns["rate_limited_drip"]))
        cd_drip_ext = float(str(cooldowns["rate_limited_drip_extended"]))

        is_drip = m_stats.drip_mode
        limit_count = m_stats.rate_limited
        if is_drip:
            cooldown_duration = cd_drip if limit_count <= 4 else cd_drip_ext
        elif limit_count <= 2:
            cooldown_duration = cd_short
        else:
            m_stats.drip_mode = True
            cooldown_duration = cd_drip

        effective_cooldown = min(cooldown_duration, max(60.0, cooldown_seconds))
        m_stats.cooldown_until = now + effective_cooldown

    def record_error(
        self, model: str | None = None, *, surface: str | None = None
    ) -> None:
        self.total_requests += 1
        self.total_errors += 1
        self.last_used = time.time()
        m_stats = self.get_model_daily(model, surface=surface)
        m_stats.requests += 1
        m_stats.errors += 1
        m_stats.last_used = time.time()

    def record_auth_error(
        self,
        model: str | None = None,
        *,
        surface: str | None = None,
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_errors += 1
        self.auth_errors += 1
        self.last_auth_error = now
        self.last_used = now
        # 403 是浏览器/WAA 会话故障，绝不给账号加冷却锁；仅靠 auth_errors
        # 惩罚分做动态避让（首次 403 原位重签，二次 403 才切号）。
        self.auth_cooldown = 0.0
        m_stats = self.get_model_daily(model, surface=surface)
        m_stats.requests += 1
        m_stats.errors += 1

    def record_session_expired(
        self, model: str | None = None, *, surface: str | None = None
    ) -> None:
        now = time.time()
        self.total_requests += 1
        self.total_errors += 1
        self.auth_errors += 1
        self.session_expired = True
        self.last_session_expired = now
        self.last_auth_error = now
        self.last_used = now
        self.auth_cooldown = now + 86400.0
        m_stats = self.get_model_daily(model, surface=surface)
        m_stats.requests += 1
        m_stats.errors += 1

    def clear_cooldown(
        self, model: str | None = None, *, surface: str | None = None
    ) -> None:
        """清除冷却与 429/403/失效锁定。"""
        self.auth_cooldown = 0.0
        self.auth_errors = 0
        self.session_expired = False
        self.last_session_expired = 0.0
        if model:
            for key in quota_keys(model, surface=surface, scope=self.quota_scope):
                st = self.daily_models.get(key)
                if st is not None:
                    st.cooldown_until = 0.0
                    st.rate_limited = 0
                    st.drip_mode = False
        else:
            for st in self.daily_models.values():
                st.cooldown_until = 0.0
                st.rate_limited = 0
                st.drip_mode = False

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "quota_scope": self.quota_scope,
            "total_requests": self.total_requests,
            "total_success": self.total_success,
            "total_rate_limited": self.total_rate_limited,
            "total_errors": self.total_errors,
            "auth_errors": self.auth_errors,
            "last_used": self.last_used,
            "last_rate_limited": self.last_rate_limited,
            "last_auth_error": self.last_auth_error,
            "auth_cooldown": self.auth_cooldown,
            "session_expired": self.session_expired,
            "last_session_expired": self.last_session_expired,
            "daily_date_la": self.daily_date_la,
            "daily_models": {
                m: m_stats.to_dict() for m, m_stats in self.daily_models.items()
            },
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, object], *, quota_scope: str | None = None
    ) -> AccountStats:
        def _as_int(v: object) -> int:
            try:
                return int(str(v)) if v is not None else 0
            except (ValueError, TypeError):
                return 0

        def _as_float(v: object) -> float:
            try:
                return float(str(v)) if v is not None else 0.0
            except (ValueError, TypeError):
                return 0.0

        raw_daily = data.get("daily_models")
        daily_models: dict[str, AccountModelDailyStats] = {}
        if isinstance(raw_daily, dict):
            for m, m_data in raw_daily.items():
                if isinstance(m_data, dict):
                    daily_models[str(m)] = AccountModelDailyStats.from_dict(m_data)

        # 兼容旧版本保存的扁平字段
        tot_req = _as_int(data.get("total_requests") or data.get("requests"))
        tot_succ = _as_int(data.get("total_success") or data.get("success"))
        tot_limit = _as_int(data.get("total_rate_limited") or data.get("rate_limited"))
        tot_err = _as_int(data.get("total_errors") or data.get("errors"))

        old_model_reqs = data.get("model_requests")
        old_model_cds = data.get("model_cooldowns")
        old_model_rates = data.get("model_rate_limited")
        old_model_drips = data.get("model_drip_mode")
        if not daily_models and (
            isinstance(old_model_reqs, dict)
            or isinstance(old_model_cds, dict)
            or isinstance(old_model_rates, dict)
            or isinstance(old_model_drips, dict)
        ):
            all_keys: set[str] = set()
            for source in (
                old_model_reqs,
                old_model_cds,
                old_model_rates,
                old_model_drips,
            ):
                if isinstance(source, dict):
                    all_keys.update(str(k) for k in source)
            for m_k in all_keys:
                daily_models[str(m_k)] = AccountModelDailyStats(
                    requests=_as_int(old_model_reqs.get(m_k))
                    if isinstance(old_model_reqs, dict)
                    else 0,
                    rate_limited=_as_int(old_model_rates.get(m_k))
                    if isinstance(old_model_rates, dict)
                    else 0,
                    cooldown_until=_as_float(old_model_cds.get(m_k))
                    if isinstance(old_model_cds, dict)
                    else 0.0,
                    drip_mode=bool(old_model_drips.get(m_k))
                    if isinstance(old_model_drips, dict)
                    else False,
                )
        return cls(
            account_id=str(data.get("account_id") or ""),
            quota_scope=(quota_scope or str(data.get("quota_scope") or "account")),
            total_requests=tot_req,
            total_success=tot_succ,
            total_rate_limited=tot_limit,
            total_errors=tot_err,
            auth_errors=_as_int(data.get("auth_errors")),
            last_used=_as_float(data.get("last_used")),
            last_rate_limited=_as_float(data.get("last_rate_limited")),
            last_auth_error=_as_float(data.get("last_auth_error")),
            auth_cooldown=_as_float(data.get("auth_cooldown")),
            session_expired=bool(data.get("session_expired")),
            last_session_expired=_as_float(data.get("last_session_expired")),
            daily_date_la=str(data.get("daily_date_la") or get_pacific_date_key()),
            daily_models=daily_models,
        )


class AccountRotator:
    """账号调度管理器。

    核心流程：
    1. 候选过滤（使能开关、登录态、订阅要求、429 冷却）；
    2. 当前健康账号优先（sticky），否则按最少使用与 LRU 选择；
    3. D-01 quota_scope 记账口径（account / account_surface）。
    """

    quota_scope: str = "account"

    def __init__(self, account_store: AccountStore) -> None:
        self._store = account_store
        from aistudio_api.infrastructure.gateway.model_defaults import (
            resolve_quota_scope,
        )

        self.quota_scope = resolve_quota_scope()
        self._stats: dict[str, AccountStats] = {}
        self._lock = asyncio.Lock()
        self._last_saved_time: float = 0.0
        self._save_task: asyncio.Task[None] | None = None
        self.load_state()
        self.cleanup_ghost_accounts()
        for account in self._store.list_accounts():
            if account.id not in self._stats:
                self._stats[account.id] = AccountStats(
                    account_id=account.id, quota_scope=self.quota_scope
                )

    def load_state(self) -> None:
        """从持久化文件恢复账号统计状态。"""
        if not settings.persist_rotator:
            return
        state_file = resolve_rotator_state_file()
        if not state_file.is_file():
            return
        try:
            raw = state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                for acc_id, item in data.items():
                    if isinstance(item, dict):
                        self._stats[str(acc_id)] = AccountStats.from_dict(
                            item, quota_scope=self.quota_scope
                        )
                logger.info(
                    "已从 %s 恢复 %d 个账号的统计状态",
                    state_file,
                    len(self._stats),
                )
        except Exception as e:
            logger.warning("从 %s 读取账号调度状态失败: %s", state_file, e)

    def save_state(self) -> None:
        """将账号调度与统计状态落盘。"""
        if not settings.persist_rotator:
            return
        state_file = resolve_rotator_state_file()
        try:
            payload = {acc_id: stats.to_dict() for acc_id, stats in self._stats.items()}
            atomic_write_json(state_file, payload)
            self._last_saved_time = time.time()
        except Exception as e:
            logger.warning("持久化账号调度状态到 %s 失败: %s", state_file, e)

    def _schedule_debounced_save(self, delay: float = 2.0) -> None:
        if not settings.persist_rotator:
            return
        now = time.time()
        if now - self._last_saved_time >= 5.0:
            self.save_state()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save_state()
            return

        if self._save_task is not None and not self._save_task.done():
            return

        async def _delayed():
            await asyncio.sleep(delay)
            self.save_state()

        self._save_task = loop.create_task(_delayed())

    @property
    def account_store(self) -> AccountStore:
        """Expose the backing store for sibling/session-expiry coordination."""
        return self._store

    def get_account_stats(self, account_id: str) -> AccountStats | None:
        """获取指定账号的统计对象。"""
        stats = self._stats.get(account_id)
        if stats is None and any(
            a.id == account_id for a in self._store.list_accounts()
        ):
            stats = AccountStats(account_id=account_id, quota_scope=self.quota_scope)
            self._stats[account_id] = stats
        if stats is not None:
            account = self._store.get_account(account_id)
            if account is not None and getattr(account, "expired", False) is True:
                # Store-level expiry (credential import / refresh / root login
                # death) is authoritative for admission; mirror it into the
                # rotator view so stats and selection stay consistent.
                stats.session_expired = True
        return stats

    def is_account_available(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str | None = None,
    ) -> bool:
        """检查指定账号在特定模型下是否可用（使能、登录态与真 429 冷却）。"""
        account = self._store.get_account(account_id)
        if not account or not account.enabled or getattr(account, "expired", False):
            return False
        stats = self.get_account_stats(account_id)
        return bool(stats and stats.is_available(model, surface=surface))

    def is_auth_locked(self, account_id: str) -> bool:
        """True when the account is locked by a dead login / verify-account.

        ``get_account_stats`` mirrors the store-level ``expired`` flag into
        ``session_expired``, so this covers both a rotator-recorded lock and a
        credential death detected by the account store.  Keepalive and model
        discovery use it to stop probing a locked account.
        """
        stats = self.get_account_stats(account_id)
        return bool(stats is not None and stats.session_expired)

    def select_rebind_account(
        self,
        *,
        lane: str,
        model: str | None = None,
        surface: str | None = None,
        exclude_account_ids: set[str] | None = None,
        only_tier: str = "",
        min_tier: str = "",
        max_tier: str = "",
        required_tiers: tuple[str, ...] | list[str] | None = None,
    ) -> AccountMeta | None:
        """Pick a healthy same-lane account for a request-triggered slot rebind.

        Excludes failed/cooldown accounts supplied by the caller plus accounts
        that are disabled, expired or cannot satisfy the instance tier interval
        and the route's rank-based tier requirement.  ``min_tier`` keeps the
        legacy call shape (``min_tier: Free`` means exactly Free).
        ``exclude_account_ids`` also carries accounts already attempted by the
        request and accounts bound to other instances; no background rebind is
        implied — this is only a read-only selection helper.
        """
        excluded = set(exclude_account_ids or ())
        required = tuple(required_tiers or ())
        selector = None
        if only_tier:
            selector = resolve_tier_selector(only_tier=only_tier)
        elif min_tier or max_tier:
            selector = resolve_tier_selector(
                min_tier=min_tier or None,
                max_tier=max_tier or None,
            )
        candidates: list[tuple[AccountMeta, AccountStats]] = []
        for account in self._store.list_accounts():
            if account.id in excluded:
                continue
            if not account.enabled or getattr(account, "expired", False):
                continue
            tier = getattr(account, "tier", "Free") or "Free"
            if instance_lane_for_tier(tier) != lane:
                continue
            if selector is not None and not tier_matches_selector(tier, selector):
                continue
            if required and not tier_satisfies_required(tier, required):
                continue
            stats = self.get_account_stats(account.id)
            if stats is None or not stats.is_available(model, surface=surface):
                continue
            resolver = getattr(self._store, "get_auth_path_optional", None)
            if callable(resolver) and resolver(account.id, require_exists=True) is None:
                continue
            candidates.append((account, stats))
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                tier_rank(getattr(item[0], "tier", "Free")),
                item[1].total_requests,
                item[1].model_requests_total(model),
                item[1].last_used,
            ),
        )[0]

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        """获取所有账号的运行透视与配额状态。"""
        from aistudio_api.infrastructure.gateway.model_defaults import (
            resolve_tier_quota,
        )

        result: dict[str, dict[str, object]] = {}
        current_la = get_pacific_date_key()

        for account in self._store.list_accounts():
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            stats._check_date_reset(current_la)
            tier_str = getattr(account, "tier", "Free") or "Free"

            today_models_data: dict[str, dict[str, object]] = {}
            model_cooldowns_dict: dict[str, int] = {}
            today_total_requests = 0
            today_total_tokens = 0

            for m_key, m_stats in stats.daily_models.items():
                today_total_requests += m_stats.requests
                today_total_tokens += m_stats.total_tokens
                _surface, clean_model = split_quota_key(m_key)
                quota = resolve_tier_quota(tier_str, clean_model)
                rem_cd = (
                    max(0, int(m_stats.cooldown_until - time.time()))
                    if m_stats.cooldown_until > time.time()
                    else 0
                )
                if rem_cd > 0:
                    model_cooldowns_dict[m_key] = rem_cd

                today_models_data[m_key] = {
                    "requests": m_stats.requests,
                    "success": m_stats.success,
                    "rate_limited": m_stats.rate_limited,
                    "errors": m_stats.errors,
                    "prompt_tokens": m_stats.prompt_tokens,
                    "completion_tokens": m_stats.completion_tokens,
                    "reasoning_tokens": m_stats.reasoning_tokens,
                    "cached_tokens": m_stats.cached_tokens,
                    "total_tokens": m_stats.total_tokens,
                    "configured_quota": quota,
                    "quota_used_percent": round(
                        (m_stats.requests / max(1, quota)) * 100, 1
                    ),
                    "cooldown_remaining": rem_cd,
                    "drip_mode": m_stats.drip_mode,
                    "is_available": rem_cd == 0,
                }

            result[account.id] = {
                "name": account.name,
                "email": account.email,
                "auth_user": account.auth_user,
                "tier": tier_str,
                "quota_scope": self.quota_scope,
                "enabled": account.enabled,
                "group": account.group,
                "requests": stats.total_requests,
                "success": stats.total_success,
                "rate_limited": stats.total_rate_limited,
                "errors": stats.total_errors,
                "today_total_requests": today_total_requests,
                "today_total_tokens": today_total_tokens,
                "last_used": (
                    datetime.fromtimestamp(stats.last_used, tz=UTC).isoformat()
                    if stats.last_used
                    else None
                ),
                "last_rate_limited": (
                    datetime.fromtimestamp(stats.last_rate_limited, tz=UTC).isoformat()
                    if stats.last_rate_limited
                    else None
                ),
                "is_available": (
                    account.enabled
                    and not getattr(account, "expired", False)
                    and stats.is_available()
                ),
                "session_expired": stats.session_expired,
                "auth_cooldown": max(0, int(stats.auth_cooldown - time.time()))
                if stats.auth_cooldown > time.time()
                else 0,
                "auth_errors": stats.auth_errors,
                "cooldown_remaining": int(stats.get_cooldown_remaining()),
                "model_cooldowns": model_cooldowns_dict,
                "today_models": today_models_data,
                # 保持前端现有组件向下读取容错
                "model_requests": {
                    m: s.requests for m, s in stats.daily_models.items()
                },
                "model_rate_limited": {
                    m: s.rate_limited for m, s in stats.daily_models.items()
                },
                "model_drip_mode": {
                    m: s.drip_mode for m, s in stats.daily_models.items()
                },
            }
        return result

    def _get_eligible_candidates(
        self,
        model: str | None = None,
        required_tiers: tuple[str, ...] | list[str] | None = None,
        surface: str | None = None,
    ) -> list[tuple[AccountMeta, AccountStats]]:
        """Filter usable accounts (enabled, session, required tiers, cooldowns).

        There is no group filter, no quota-pool narrowing and no model-discovery
        fail-open here: P2 admission lives in ``routing_eligibility``.
        """
        accounts = self._store.list_accounts()
        candidates: list[tuple[AccountMeta, AccountStats]] = []

        for account in accounts:
            if not account.enabled or getattr(account, "expired", False):
                continue
            if required_tiers and not tier_satisfies_required(
                account.tier, required_tiers
            ):
                continue

            stats = self._stats.setdefault(
                account.id,
                AccountStats(account_id=account.id, quota_scope=self.quota_scope),
            )
            if not stats.is_available(model, surface=surface):
                continue

            candidates.append((account, stats))

        return candidates

    def _least_used_key(
        self,
        item: tuple[AccountMeta, AccountStats],
        model: str | None,
    ) -> tuple[int, int, float]:
        """最少总请求数 → 该模型今日请求数 → last_used 最久（LRU/FIFO）。"""
        _account, stats = item
        return (
            stats.total_requests,
            stats.model_requests_total(model),
            stats.last_used,
        )

    async def get_next_account(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
        failed_account_id: str | None = None,
        required_tiers: tuple[str, ...] | list[str] | None = None,
        surface: str | None = None,
    ) -> AccountMeta | None:
        """Pick the best ready account: sticky, then least-used/LRU, then FIFO.

        Request-time switching is owned by the routing pipeline; this method is
        kept for the manual ``/rotation/next`` control-plane action.
        """
        async with self._lock:
            candidates = self._get_eligible_candidates(
                model=model,
                required_tiers=required_tiers,
                surface=surface,
            )
            valid_candidates = [
                (a, s)
                for a, s in candidates
                if not (failed_account_id and a.id == failed_account_id)
            ]
            if not valid_candidates:
                return None

            if current_account_id:
                for account, _stats in valid_candidates:
                    if account.id == current_account_id:
                        return account

            best_account, _ = min(
                valid_candidates,
                key=lambda item: self._least_used_key(item, model),
            )
            if current_account_id and best_account.id != current_account_id:
                logger.info(
                    "账号切换: %s -> %s (model=%s, tier=%s)",
                    current_account_id,
                    best_account.id,
                    model,
                    best_account.tier,
                )
            return best_account

    def record_success(
        self,
        account_id: str,
        model: str | None = None,
        usage: dict[str, object] | None = None,
        *,
        surface: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self._stats[account_id].record_success(model, usage=usage, surface=surface)
        self._schedule_debounced_save()

    def record_rate_limited(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str | None = None,
        reason: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self._stats[account_id].record_rate_limited(model, surface=surface)
        logger.warning(
            "账号 %s 在模型 %s (surface=%s) 触发真实上游 429: %s",
            account_id,
            model or "all",
            surface or "-",
            reason or "-",
        )
        self.save_state()

    def record_error(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self._stats[account_id].record_error(model, surface=surface)
        self.save_state()

    def record_auth_error(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str | None = None,
        reason: str | None = None,
    ) -> None:
        account = self._store.get_account(account_id)
        if account is not None and getattr(account, "expired", False) is True:
            # The 403 refresh path may have discovered that the root login
            # state is dead and marked the account (and siblings) expired in
            # the store.  Promote this attempt to the terminal event so routing
            # skips the whole same-anchor group instead of ping-ponging.
            self.record_session_expired(
                account_id, model, surface=surface, reason=reason
            )
            return
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self._stats[account_id].record_auth_error(model, surface=surface)
        logger.warning(
            "账号 %s 权限被拒绝 (HTTP 403) model=%s surface=%s reason=%s",
            account_id,
            model or "all",
            surface or "-",
            reason or "-",
        )
        self.save_state()

    def record_session_expired(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str | None = None,
        reason: str | None = None,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self._stats[account_id].record_session_expired(model, surface=surface)
        logger.error(
            "账号 %s 登录态失效，已被永久禁用直至重新导入凭据 "
            "(model=%s surface=%s reason=%s)",
            account_id,
            model or "all",
            surface or "-",
            reason or "-",
        )
        self.save_state()

    def clear_cooldown(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str | None = None,
    ) -> None:
        """清除指定账号的 429 锁定。"""
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self._stats[account_id].clear_cooldown(model, surface=surface)
        logger.info("已手动清除账号 %s 锁定 (model=%s)", account_id, model)
        self.save_state()

    def clear_all_cooldowns(self) -> None:
        """清除全部账号的所有模型 429 锁定。"""
        for stats in self._stats.values():
            stats.clear_cooldown()
        logger.info("已手动清除全部账号的 429 锁定状态")
        self.save_state()

    def add_account(self, account_id: str) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(
                account_id=account_id, quota_scope=self.quota_scope
            )
        self.save_state()

    def remove_account(self, account_id: str) -> None:
        self._stats.pop(account_id, None)
        self.save_state()

    def cleanup_ghost_accounts(self) -> list[str]:
        """清理已从 AccountStore 中删除但仍残留在 rotator_state 中的幽灵账号。"""
        existing_ids = {a.id for a in self._store.list_accounts()}
        ghost_ids = [
            acc_id for acc_id in list(self._stats.keys()) if acc_id not in existing_ids
        ]
        for gid in ghost_ids:
            self._stats.pop(gid, None)
        if ghost_ids:
            logger.info("已清理 %d 个幽灵账号残留状态: %s", len(ghost_ids), ghost_ids)
            self.save_state()
        return ghost_ids


_rotator: AccountRotator | None = None


def init_rotator(account_store: AccountStore) -> AccountRotator:
    global _rotator
    _rotator = AccountRotator(account_store)
    return _rotator
