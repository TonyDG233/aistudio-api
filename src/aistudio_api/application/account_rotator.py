"""Account rotation and sticky dispatch for multi-account management."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aistudio_api.config import resolve_rotator_state_file, settings
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


# WP5：冷却/限流维度从 (account, model) 扩展为 (account, surface, model)。
DEFAULT_SURFACE = "chat"

# 内部存储约定：chat 面沿用裸 model 键（"gemini-2.5-pro"），以保持
# data/rotator_state.json 的持久化格式与既有消费方（含既有单测）完全兼容；
# 其它面统一命名空间化为 "surface::model"。API 输出统一用 surface_model_key()
# 给出的 "surface::model" 新格式。
_SURFACE_SEP = "::"


def surface_model_key(
    surface: str = DEFAULT_SURFACE, model: str | None = None
) -> str | None:
    """构造对外/展示用的 "surface::model" 键（默认 surface 为 chat）。"""
    normalized = normalize_model_key(model)
    if normalized is None:
        return None
    surface_key = (surface or DEFAULT_SURFACE).strip().lower() or DEFAULT_SURFACE
    return f"{surface_key}{_SURFACE_SEP}{normalized}"


def _storage_key(surface: str, model: str | None) -> str | None:
    """计算内部存储键：chat 面为裸 model（向后兼容），其它面为 "surface::model"。"""
    normalized = normalize_model_key(model)
    if normalized is None:
        return None
    surface_key = (surface or DEFAULT_SURFACE).strip().lower() or DEFAULT_SURFACE
    if surface_key == DEFAULT_SURFACE:
        return normalized
    return f"{surface_key}{_SURFACE_SEP}{normalized}"


def split_surface_key(key: str) -> tuple[str, str]:
    """把内部存储键拆成 (surface, model)；裸键视为 chat 面。"""
    if _SURFACE_SEP in key:
        surface, _, model = key.partition(_SURFACE_SEP)
        return surface, model
    return DEFAULT_SURFACE, key


def canonicalize_surface_key(key: str) -> str:
    """把持久化键归一为内部存储键。

    旧 state 里的裸 model 键一律视为 chat 面；"chat::model" 收敛回裸键以保持
    chat 面内部表示的唯一性（既有的裸键单测与落盘格式因此仍然成立）。
    """
    surface, model = split_surface_key(key)
    if surface == DEFAULT_SURFACE:
        return model
    return f"{surface}{_SURFACE_SEP}{model}"


@dataclass
class AccountStats:
    """单账号的运行与配额统计。"""

    account_id: str
    requests: int = 0
    success: int = 0
    rate_limited: int = 0
    errors: int = 0
    auth_errors: int = 0
    last_used: float = 0.0
    last_rate_limited: float = 0.0
    last_auth_error: float = 0.0
    auth_cooldown: float = 0.0
    session_expired: bool = False
    last_session_expired: float = 0.0
    rate_limited_date_la: str | None = None
    model_cooldowns: dict[str, float] = field(default_factory=dict)
    model_rate_limited_dates: dict[str, str] = field(default_factory=dict)
    model_requests: dict[str, int] = field(default_factory=dict)
    model_requests_dates: dict[str, str] = field(default_factory=dict)
    model_rate_limited: dict[str, int] = field(default_factory=dict)
    model_drip_mode: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "requests": self.requests,
            "success": self.success,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
            "auth_errors": self.auth_errors,
            "last_used": self.last_used,
            "last_rate_limited": self.last_rate_limited,
            "last_auth_error": self.last_auth_error,
            "auth_cooldown": self.auth_cooldown,
            "session_expired": self.session_expired,
            "last_session_expired": self.last_session_expired,
            "rate_limited_date_la": self.rate_limited_date_la,
            "model_cooldowns": dict(self.model_cooldowns),
            "model_rate_limited_dates": dict(self.model_rate_limited_dates),
            "model_requests": dict(self.model_requests),
            "model_requests_dates": dict(self.model_requests_dates),
            "model_rate_limited": dict(self.model_rate_limited),
            "model_drip_mode": dict(self.model_drip_mode),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountStats:
        raw_cd = data.get("model_cooldowns")
        model_cooldowns: dict[str, float] = {}
        if isinstance(raw_cd, dict):
            for k, v in raw_cd.items():
                with contextlib.suppress(ValueError, TypeError):
                    model_cooldowns[canonicalize_surface_key(str(k))] = float(str(v))

        raw_dates = data.get("model_rate_limited_dates")
        model_rate_limited_dates: dict[str, str] = {}
        if isinstance(raw_dates, dict):
            for k, v in raw_dates.items():
                model_rate_limited_dates[canonicalize_surface_key(str(k))] = str(v)

        raw_reqs = data.get("model_requests")
        model_requests: dict[str, int] = {}
        if isinstance(raw_reqs, dict):
            for k, v in raw_reqs.items():
                with contextlib.suppress(ValueError, TypeError):
                    model_requests[canonicalize_surface_key(str(k))] = int(str(v))

        raw_req_dates = data.get("model_requests_dates")
        model_requests_dates: dict[str, str] = {}
        if isinstance(raw_req_dates, dict):
            for k, v in raw_req_dates.items():
                model_requests_dates[canonicalize_surface_key(str(k))] = str(v)

        raw_limits = data.get("model_rate_limited")
        model_rate_limited: dict[str, int] = {}
        if isinstance(raw_limits, dict):
            for k, v in raw_limits.items():
                with contextlib.suppress(ValueError, TypeError):
                    model_rate_limited[canonicalize_surface_key(str(k))] = int(str(v))

        raw_drip = data.get("model_drip_mode")
        model_drip_mode: dict[str, bool] = {}
        if isinstance(raw_drip, dict):
            for k, v in raw_drip.items():
                model_drip_mode[canonicalize_surface_key(str(k))] = bool(v)

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
            account_id=str(data.get("account_id") or ""),
            requests=_as_int(data.get("requests")),
            success=_as_int(data.get("success")),
            rate_limited=_as_int(data.get("rate_limited")),
            errors=_as_int(data.get("errors")),
            auth_errors=_as_int(data.get("auth_errors")),
            last_used=_as_float(data.get("last_used")),
            last_rate_limited=_as_float(data.get("last_rate_limited")),
            last_auth_error=_as_float(data.get("last_auth_error")),
            auth_cooldown=_as_float(data.get("auth_cooldown")),
            session_expired=bool(data.get("session_expired")),
            last_session_expired=_as_float(data.get("last_session_expired")),
            rate_limited_date_la=str(data["rate_limited_date_la"])
            if data.get("rate_limited_date_la")
            else None,
            model_cooldowns=model_cooldowns,
            model_rate_limited_dates=model_rate_limited_dates,
            model_requests=model_requests,
            model_requests_dates=model_requests_dates,
            model_rate_limited=model_rate_limited,
            model_drip_mode=model_drip_mode,
        )

    def _check_date_reset(self, model: str | None, current_la: str) -> None:
        """检查美西午夜跨天，自动重置单日请求计数与限流锁定。"""
        model = normalize_model_key(model)
        if self.rate_limited_date_la and self.rate_limited_date_la < current_la:
            self.rate_limited_date_la = None

        if model:
            req_date = self.model_requests_dates.get(model)
            if req_date and req_date < current_la:
                self.model_requests[model] = 0
                self.model_requests_dates[model] = current_la

            limit_date = self.model_rate_limited_dates.get(model)
            if limit_date and limit_date < current_la:
                self.model_rate_limited_dates.pop(model, None)
                self.model_cooldowns.pop(model, None)
                self.model_rate_limited.pop(model, None)
                self.model_drip_mode.pop(model, None)

    def is_available(
        self,
        model: str | None = None,
        *,
        ignore_auth_cooldown: bool = False,
        surface: str = DEFAULT_SURFACE,
    ) -> bool:
        """检查账号在指定面/模型下是否可用（美西 0 点自动刷新）。"""
        now = time.time()
        if self.session_expired:
            return False
        if not ignore_auth_cooldown and self.auth_cooldown > now:
            return False
        key = _storage_key(surface, model)
        current_la = get_pacific_date_key(now)
        self._check_date_reset(key, current_la)

        # 检查全局 429 标记
        if self.rate_limited_date_la:
            return False

        if key:
            cd = self.model_cooldowns.get(key, 0.0)
            return now >= cd

        return True

    def get_cooldown_remaining(
        self, model: str | None = None, *, surface: str = DEFAULT_SURFACE
    ) -> float:
        """获取指定面/模型距离配额刷新的剩余秒数。"""
        key = _storage_key(surface, model)
        if self.is_available(model, surface=surface):
            return 0.0
        now = time.time()
        if self.auth_cooldown > now:
            return max(0.0, self.auth_cooldown - now)
        if key:
            cd = self.model_cooldowns.get(key, 0.0)
            if cd > now:
                return max(0.0, cd - now)
        return get_seconds_until_pacific_midnight()

    def tracked_surface_models(self, current_la: str) -> list[tuple[str, str, str]]:
        """枚举所有被跟踪的 (存储键, surface, model)，供 get_all_stats 汇总。"""
        keys: set[str] = set(self.model_cooldowns.keys())
        keys |= {
            k for k, dt in self.model_rate_limited_dates.items() if dt == current_la
        }
        tracked: list[tuple[str, str, str]] = []
        for storage_key in sorted(keys):
            surface, model = split_surface_key(storage_key)
            tracked.append((storage_key, surface, model))
        return tracked

    def record_success(
        self, model: str | None = None, *, surface: str = DEFAULT_SURFACE
    ) -> None:
        now = time.time()
        self.requests += 1
        self.success += 1
        self.last_used = now
        self.auth_errors = 0
        self.auth_cooldown = 0.0
        key = _storage_key(surface, model)
        if key:
            la_date = get_pacific_date_key(now)
            self._check_date_reset(key, la_date)
            self.model_requests[key] = self.model_requests.get(key, 0) + 1
            self.model_requests_dates[key] = la_date
            self.model_cooldowns.pop(key, None)
            self.model_rate_limited_dates.pop(key, None)
            if not self.model_drip_mode.get(key):
                self.model_rate_limited.pop(key, None)

    def record_rate_limited(
        self, model: str | None = None, *, surface: str = DEFAULT_SURFACE
    ) -> None:
        now = time.time()
        self.requests += 1
        self.rate_limited += 1
        self.last_rate_limited = now
        la_date = get_pacific_date_key(now)
        cooldown_seconds = get_seconds_until_pacific_midnight()
        key = _storage_key(surface, model)

        if key:
            self._check_date_reset(key, la_date)

            is_drip = bool(self.model_drip_mode.get(key))
            limit_count = self.model_rate_limited.get(key, 0) + 1
            self.model_requests[key] = self.model_requests.get(key, 0) + 1
            self.model_requests_dates[key] = la_date
            self.model_rate_limited[key] = limit_count

            if is_drip:
                cooldown_duration = 300.0 if limit_count <= 4 else 600.0
            elif limit_count <= 2:
                cooldown_duration = 60.0
            else:
                self.model_drip_mode[key] = True
                cooldown_duration = 300.0

            effective_cooldown = min(cooldown_duration, max(60.0, cooldown_seconds))
            self.model_cooldowns[key] = now + effective_cooldown
            self.model_rate_limited_dates[key] = la_date
        else:
            self.rate_limited_date_la = la_date

    def record_error(
        self, model: str | None = None, *, surface: str = DEFAULT_SURFACE
    ) -> None:
        self.requests += 1
        self.errors += 1
        self.last_used = time.time()
        key = _storage_key(surface, model)
        if key:
            self.model_requests[key] = self.model_requests.get(key, 0) + 1

    def record_auth_error(
        self,
        model: str | None = None,
        cooldown_seconds: float = 300.0,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        """记录鉴权/权限错误（403/401），立即冷却该账号以便快速故障转移。"""
        now = time.time()
        self.requests += 1
        self.errors += 1
        self.auth_errors += 1
        self.last_auth_error = now
        self.last_used = now
        self.auth_cooldown = now + cooldown_seconds
        key = _storage_key(surface, model)
        if key:
            self.model_requests[key] = self.model_requests.get(key, 0) + 1

    def record_session_expired(
        self, model: str | None = None, *, surface: str = DEFAULT_SURFACE
    ) -> None:
        """记录账号登录态失效（被重定向到登录页），将该账号隔离并在未更新前禁止使用。"""
        now = time.time()
        self.requests += 1
        self.errors += 1
        self.auth_errors += 1
        self.session_expired = True
        self.last_session_expired = now
        self.last_auth_error = now
        self.last_used = now
        # 隔离 24 小时或直至用户重新导入/手动重置
        self.auth_cooldown = now + 86400.0
        key = _storage_key(surface, model)
        if key:
            self.model_requests[key] = self.model_requests.get(key, 0) + 1

    def clear_cooldown(
        self, model: str | None = None, *, surface: str = DEFAULT_SURFACE
    ) -> None:
        """手动清除冷却与 429/403/失效锁定。"""
        self.auth_cooldown = 0.0
        self.auth_errors = 0
        self.session_expired = False
        self.last_session_expired = 0.0
        key = _storage_key(surface, model)
        if key:
            self.model_cooldowns.pop(key, None)
            self.model_rate_limited_dates.pop(key, None)
            self.model_rate_limited.pop(key, None)
            self.model_drip_mode.pop(key, None)
            self.model_requests.pop(key, None)
            self.model_requests_dates.pop(key, None)
        else:
            self.rate_limited_date_la = None
            self.model_cooldowns.clear()
            self.model_rate_limited_dates.clear()
            self.model_rate_limited.clear()
            self.model_drip_mode.clear()
            self.model_requests.clear()
            self.model_requests_dates.clear()


def _parse_iso_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


class AccountRotator:
    """黏性账号调度管理器。

    默认保持当前激活账号，直到特定模型遇到 429 配额耗尽时，
    自动切换到该模型今日仍有额度的下一个健康账号。
    """

    def __init__(self, account_store: AccountStore) -> None:
        self._store = account_store
        self._stats: dict[str, AccountStats] = {}
        self._lock = asyncio.Lock()
        self._last_saved_time: float = 0.0
        self._save_task: asyncio.Task | None = None
        # WP5：调度排除钩子。orchestrator 会注入「allowed_surfaces 为空的组不参与调度」。
        self._surface_eligibility: Callable[[AccountMeta], bool] | None = None
        self.load_state()
        self.cleanup_ghost_accounts()
        for account in self._store.list_accounts():
            if account.id not in self._stats:
                self._stats[account.id] = AccountStats(account_id=account.id)

    def set_surface_eligibility(self, fn: Callable[[AccountMeta], bool] | None) -> None:
        """注入面策略调度排除钩子：fn(account) 返回 False 的账号跳过调度。

        默认 None 表示不施加任何面策略过滤（保持旧调度行为）。主代理会把
        orchestrator 的「allowed_surfaces 为空的组不参与调度」接上来。
        """
        self._surface_eligibility = fn

    def load_state(self) -> None:
        """从持久化文件恢复账号统计与 429 锁定状态。"""
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
                        self._stats[str(acc_id)] = AccountStats.from_dict(item)
                logger.info(
                    "已从 %s 恢复 %d 个账号的运行与限额状态",
                    state_file,
                    len(self._stats),
                )
        except Exception as e:
            logger.warning("从 %s 读取账号调度状态失败: %s", state_file, e)

    def save_state(self) -> None:
        """将账号调度统计与 429 冷却状态持久化到文件。"""
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

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        """获取所有账号的运行与配额状态。

        ``model_cooldowns`` 的键改为 ``"surface::model"`` 新格式（surface 内嵌），
        并新增 ``surface_cooldowns`` 汇总（所有被跟踪的面/模型键 →剩余冷却秒）。
        既有字段一律保留。
        """
        result: dict[str, dict[str, object]] = {}
        current_la = get_pacific_date_key()
        for account in self._store.list_accounts():
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            model_cooldowns: dict[str, int] = {}
            surface_cooldowns: dict[str, int] = {}
            for _storage, surface, model in stats.tracked_surface_models(current_la):
                remaining = int(stats.get_cooldown_remaining(model, surface=surface))
                display_key = surface_model_key(surface, model) or _storage
                surface_cooldowns[display_key] = remaining
                if not stats.is_available(model, surface=surface) and remaining > 0:
                    model_cooldowns[display_key] = remaining
            result[account.id] = {
                "name": account.name,
                "email": account.email,
                "requests": stats.requests,
                "success": stats.success,
                "rate_limited": stats.rate_limited,
                "errors": stats.errors,
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
                "is_available": stats.is_available(),
                "session_expired": stats.session_expired,
                "auth_cooldown": max(0, int(stats.auth_cooldown - time.time()))
                if stats.auth_cooldown > time.time()
                else 0,
                "auth_errors": stats.auth_errors,
                "cooldown_remaining": int(stats.get_cooldown_remaining()),
                "model_cooldowns": model_cooldowns,
                "surface_cooldowns": surface_cooldowns,
                "model_rate_limited_dates": dict(stats.model_rate_limited_dates),
                "model_requests": dict(stats.model_requests),
                "model_rate_limited": dict(stats.model_rate_limited),
                "model_drip_mode": dict(stats.model_drip_mode),
            }
        return result

    def get_account_stats(self, account_id: str) -> AccountStats | None:
        """获取指定账号的统计对象。"""
        return self._stats.get(account_id)

    def is_account_available(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> bool:
        """检查指定账号在特定面/模型下是否健康可用（凭据过期账号一律不可用）。"""
        account = self._store.get_account(account_id)
        if account is not None and getattr(account, "expired", False):
            return False
        stats = self.get_account_stats(account_id)
        if stats is None:
            return True
        return stats.is_available(model, surface=surface)

    def _warn_if_all_expired(self) -> None:
        """当全部账号都因凭据过期而不可调度时，显式报错（不静默回落）。"""
        accounts = self._store.list_accounts()
        if accounts and all(getattr(a, "expired", False) for a in accounts):
            logger.error(
                "所有账号凭据均已过期(credential expired)，已无可调度账号；"
                "请重新导入 cookies (re-import cookies)"
            )

    def _get_available_accounts(
        self,
        model: str | None = None,
        *,
        ignore_auth_cooldown: bool = False,
        surface: str = DEFAULT_SURFACE,
    ) -> list[tuple[AccountMeta, AccountStats]]:
        """获取在指定面/模型下尚未耗尽当日额度且未被鉴权锁定的账号列表。

        凭据过期（``AccountMeta.expired``）的账号在此统一排除出所有调度选择；
        若注入了 ``set_surface_eligibility`` 钩子，钩子返回 False 的账号同样跳过。
        """
        accounts = self._store.list_accounts()
        available: list[tuple[AccountMeta, AccountStats]] = []
        for account in accounts:
            if getattr(account, "expired", False):
                logger.debug(
                    "账号 %s 凭据已过期(credential expired)，已排除出调度；"
                    "请重新导入 cookies (re-import cookies)",
                    account.id,
                )
                continue
            if self._surface_eligibility is not None and not self._surface_eligibility(
                account
            ):
                logger.debug(
                    "账号 %s 被面策略排除出调度(allowed_surfaces 为空)",
                    account.id,
                )
                continue
            stats = self._stats.get(account.id, AccountStats(account_id=account.id))
            if stats.is_available(
                model, ignore_auth_cooldown=ignore_auth_cooldown, surface=surface
            ):
                available.append((account, stats))
        return available

    async def get_next_account(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
        failed_account_id: str | None = None,
        *,
        is_session_expired: bool = False,
        surface: str = DEFAULT_SURFACE,
    ) -> AccountMeta | None:
        """获取下一个可用账号（排除失败账号，优先平滑故障转移至健康账号）。"""
        async with self._lock:
            available = self._get_available_accounts(model, surface=surface)
            if not available:
                # 保底：若所有账号都处于 auth_cooldown 中，允许 fallback 尝试恢复
                available = self._get_available_accounts(
                    model, ignore_auth_cooldown=True, surface=surface
                )
                if not available:
                    self._warn_if_all_expired()
                    return None

            # 优先在排除失败账号的候选集中挑选
            candidates = [
                (a, s)
                for a, s in available
                if not (failed_account_id and a.id == failed_account_id)
            ]
            if candidates:
                # 黏性策略：如果当前账号未失败且在候选列表中，继续复用
                if current_account_id and not (
                    failed_account_id and current_account_id == failed_account_id
                ):
                    for a, _ in candidates:
                        if a.id == current_account_id:
                            return a
                # 故障转移：按最少 auth_errors、最少 errors、最久未用挑选
                # 故障转移：优先用该模型与总使用次数少、非 drip_mode 的充沛额度健康账号
                norm_m = normalize_model_key(model)
                account, _ = min(
                    candidates,
                    key=lambda x: (
                        1 if (norm_m and x[1].model_drip_mode.get(norm_m)) else 0,
                        x[1].model_requests.get(norm_m, 0) if norm_m else 0,
                        x[1].requests,
                        x[1].auth_errors,
                        x[1].errors,
                        x[1].last_used,
                    ),
                )
                if current_account_id and account.id != current_account_id:
                    logger.info("账号故障转移切换: %s (model=%s)", account.name, model)
                return account
            if is_session_expired:
                return None
            if failed_account_id:
                for a, _ in available:
                    if a.id == failed_account_id:
                        return a

            return available[0][0]

    async def get_keepalive_candidate(
        self, current_account_id: str | None = None
    ) -> AccountMeta | None:
        """选“最久未回写 auth”的可用账号，供定时切号保活使用。"""
        async with self._lock:
            available = self._get_available_accounts(None)
            if not available:
                available = self._get_available_accounts(
                    None, ignore_auth_cooldown=True
                )
            candidates = [
                account
                for account, _stats in available
                if account.id != current_account_id
            ]
            if not candidates:
                return None
            chosen = min(
                candidates,
                key=lambda account: (
                    _parse_iso_ts(account.auth_saved_at),
                    _parse_iso_ts(account.last_used),
                    account.id,
                ),
            )
            logger.info(
                "保活切号候选: %s (auth_saved_at=%s)",
                chosen.name,
                chosen.auth_saved_at,
            )
            return chosen

    async def get_next_account_with_stats(
        self,
        model: str | None = None,
        current_account_id: str | None = None,
        failed_account_id: str | None = None,
        *,
        is_session_expired: bool = False,
        surface: str = DEFAULT_SURFACE,
    ) -> tuple[AccountMeta, AccountStats] | None:
        """获取下一个可用账号及其统计。"""
        async with self._lock:
            available = self._get_available_accounts(model, surface=surface)
            if not available:
                available = self._get_available_accounts(
                    model, ignore_auth_cooldown=True, surface=surface
                )
                if not available:
                    self._warn_if_all_expired()
                    return None

            candidates = [
                (a, s)
                for a, s in available
                if not (failed_account_id and a.id == failed_account_id)
            ]
            if candidates:
                if current_account_id and not (
                    failed_account_id and current_account_id == failed_account_id
                ):
                    for a, s in candidates:
                        if a.id == current_account_id:
                            return a, s
                norm_m = normalize_model_key(model)
                return min(
                    candidates,
                    key=lambda x: (
                        1 if (norm_m and x[1].model_drip_mode.get(norm_m)) else 0,
                        x[1].model_requests.get(norm_m, 0) if norm_m else 0,
                        x[1].requests,
                        x[1].auth_errors,
                        x[1].errors,
                        x[1].last_used,
                    ),
                )
            if is_session_expired:
                return None
            if failed_account_id:
                for a, s in available:
                    if a.id == failed_account_id:
                        return a, s

            return available[0]

    def record_success(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_success(model, surface=surface)
        self._schedule_debounced_save()

    def record_rate_limited(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_rate_limited(model, surface=surface)
        logger.warning(
            "账号 %s 在面 %s 模型 %s 触发当日限额",
            account_id,
            surface,
            model or "all",
        )

        self.save_state()

    def record_error(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_error(model, surface=surface)
        self.save_state()

    def record_auth_error(
        self,
        account_id: str,
        model: str | None = None,
        cooldown_seconds: float = 300.0,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_auth_error(
            model, cooldown_seconds=cooldown_seconds, surface=surface
        )
        logger.warning(
            "账号 %s 遇到 403 权限拒绝，暂停使用 %ds 并切换备用账号",
            account_id,
            int(cooldown_seconds),
        )
        self.save_state()

    def record_session_expired(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)
        self._stats[account_id].record_session_expired(model, surface=surface)
        logger.error("账号 %s 登录态失效，已被永久禁用直至重新导入凭据", account_id)
        self.save_state()

    def clear_cooldown(
        self,
        account_id: str,
        model: str | None = None,
        *,
        surface: str = DEFAULT_SURFACE,
    ) -> None:
        """清除指定账号的 429 锁定。"""
        if account_id in self._stats:
            self._stats[account_id].clear_cooldown(model, surface=surface)
            logger.info(
                "已手动清除账号 %s 锁定 (surface=%s, model=%s)",
                account_id,
                surface,
                model,
            )

        self.save_state()

    def clear_all_cooldowns(self) -> None:
        """清除全部账号的所有模型 429 锁定。"""
        for stats in self._stats.values():
            stats.clear_cooldown()
        logger.info("已手动清除全部账号的 429 锁定状态")

        self.save_state()

    def add_account(self, account_id: str) -> None:
        if account_id not in self._stats:
            self._stats[account_id] = AccountStats(account_id=account_id)

        self.save_state()

    def remove_account(self, account_id: str) -> None:
        self._stats.pop(account_id, None)

        self.save_state()

    def cleanup_ghost_accounts(self) -> list[str]:
        """清理已从 AccountStore 中删除但仍残留在 rotator_state 中的幽灵账号/Cookie。"""
        existing_ids = {a.id for a in self._store.list_accounts()}
        ghost_ids = [
            acc_id for acc_id in list(self._stats.keys()) if acc_id not in existing_ids
        ]
        for gid in ghost_ids:
            self._stats.pop(gid, None)
        if ghost_ids:
            logger.info(
                "已清理 %d 个幽灵账号/Cookie 残留状态: %s", len(ghost_ids), ghost_ids
            )
            self.save_state()
        return ghost_ids


_rotator: AccountRotator | None = None


def init_rotator(account_store: AccountStore) -> AccountRotator:
    global _rotator
    _rotator = AccountRotator(account_store)
    return _rotator
