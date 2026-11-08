"""账号存储层，管理多 Google 账号的注册表和 storage state。"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from os.path import basename, join, realpath
from pathlib import Path
from typing import ClassVar

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("account_store")

_UNSET: object = object()


def _atomic_write_json(path: Path, data: object) -> None:
    """写入 JSON 文件，通过临时文件加原子替换防止文件损坏。"""
    target_path = path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(f".tmp.{os.getpid()}_{time.time_ns()}")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp_path.replace(target_path)
    except Exception:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


_AUTH_FILE_WRITE_LOCK = threading.Lock()


def write_storage_state(
    auth_file: str | Path,
    *,
    cookies: list[dict[str, object]],
    origins: list[dict[str, object]] | None = None,
) -> None:
    """原子合并写入 auth.json，保留 cookies/origins 之外的字段。"""
    target = Path(auth_file).resolve()
    with _AUTH_FILE_WRITE_LOCK:
        existing: dict[str, object] = {}
        if target.is_file():
            with contextlib.suppress(Exception):
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
        existing["cookies"] = list(cookies)
        if origins is not None:
            existing["origins"] = list(origins)
        else:
            existing.setdefault("origins", [])
        _atomic_write_json(target, existing)


# 默认搜索路径（与 config.py 保持一致）
_SEARCH_ROOTS: list[Path] = [
    Path.cwd(),
    Path(__file__)
    .resolve()
    .parents[4],  # src/aistudio_api/infrastructure/account -> 项目根
]


def _safe_account_dir(accounts_dir: Path, account_id: str) -> Path:
    """验证并返回安全的账号子目录路径，防止路径遍历攻击。"""
    raw = str(account_id or "").strip()
    clean_id = "".join(c for c in raw if c.isalnum() or c in ("-", "_", ".", "@"))
    safe_name = basename(clean_id)
    if (
        not safe_name
        or safe_name != account_id
        or safe_name in (".", "..")
        or ".." in safe_name
    ):
        raise ValueError(f"Invalid or unsafe account_id: {account_id!r}")
    base_str = realpath(str(accounts_dir))
    target_str = realpath(join(base_str, safe_name))
    prefix = base_str if base_str.endswith(os.sep) else base_str + os.sep
    if not target_str.startswith(prefix):
        raise ValueError(f"Account path escapes base directory: {account_id!r}")
    return Path(target_str)


def _resolve_accounts_dir() -> Path:
    """发现 accounts 目录，默认为 data/accounts。"""
    env = os.getenv("AISTUDIO_ACCOUNTS_DIR")
    if env:
        return Path(env).resolve()
    for root in _SEARCH_ROOTS:
        candidate = root / "data" / "accounts"
        if candidate.is_dir():
            return candidate
    # 默认在第一个搜索根下创建
    return (_SEARCH_ROOTS[0] / "data" / "accounts").resolve()


def _resolve_legacy_auth_file() -> Path | None:
    """查找遗留的 data/auth.json 文件。"""
    for root in _SEARCH_ROOTS:
        candidate = root / "data" / "auth.json"
        if candidate.is_file():
            return candidate
    return None


def _generate_account_id() -> str:
    """生成 acc_ 前缀的随机 ID。"""
    import secrets

    return f"acc_{secrets.token_hex(4)}"


@dataclass
class AccountMeta:
    """账号元数据。"""

    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None = None
    auth_user: str = "0"
    cookie_id: str | None = None
    tier: str = "Free"
    tier_checked_at: str | None = None
    expired: bool = False
    duplicate_of: str | None = None
    auth_saved_at: str | None = None
    last_refresh_attempt_at: str | None = None
    last_refresh_error: str | None = None
    enabled: bool = True
    group: str = "default"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountMeta:
        raw_enabled = data.get("enabled")
        enabled = True if raw_enabled is None else bool(raw_enabled)
        raw_group = str(data.get("group") or "default").strip()
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            email=str(data["email"]) if data.get("email") is not None else None,
            created_at=str(data.get("created_at") or ""),
            last_used=str(data["last_used"])
            if data.get("last_used") is not None
            else None,
            auth_user=str(data.get("auth_user") or "0"),
            cookie_id=str(data["cookie_id"])
            if data.get("cookie_id") is not None
            else None,
            tier=str(data.get("tier") or "Free"),
            tier_checked_at=str(data["tier_checked_at"])
            if data.get("tier_checked_at") is not None
            else None,
            expired=bool(data.get("expired", False)),
            duplicate_of=str(data["duplicate_of"])
            if data.get("duplicate_of") is not None
            else None,
            auth_saved_at=str(data["auth_saved_at"])
            if data.get("auth_saved_at") is not None
            else None,
            last_refresh_attempt_at=str(data["last_refresh_attempt_at"])
            if data.get("last_refresh_attempt_at") is not None
            else None,
            last_refresh_error=str(data["last_refresh_error"])
            if data.get("last_refresh_error") is not None
            else None,
            enabled=enabled,
            group=raw_group or "default",
        )


@dataclass
class AccountPreset:
    """账号调度预设方案。"""

    id: str
    name: str
    description: str = ""
    action: str = "snapshot"
    account_ids: list[str] = field(default_factory=list)
    target_tiers: list[str] = field(default_factory=list)
    target_groups: list[str] = field(default_factory=list)
    is_system: bool = False
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AccountPreset:
        raw_ids = data.get("account_ids")
        ids_list = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else []
        raw_tiers = data.get("target_tiers")
        tiers_list = [str(x) for x in raw_tiers] if isinstance(raw_tiers, list) else []
        raw_groups = data.get("target_groups")
        groups_list = (
            [str(x) for x in raw_groups] if isinstance(raw_groups, list) else []
        )
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "未命名预设"),
            description=str(data.get("description") or ""),
            action=str(data.get("action") or "snapshot"),
            account_ids=ids_list,
            target_tiers=tiers_list,
            target_groups=groups_list,
            is_system=bool(data.get("is_system")),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass
class PresetApplyResult:
    """应用预设的结果，显式携带 rebind 信号而不提前改写 active。"""

    preset: AccountPreset
    enabled_account_ids: list[str]
    disabled_account_ids: list[str]
    previous_active_account_id: str | None = None
    rebind_required: bool = False
    active_account_id: str | None = None
    target_account: AccountMeta | None = None


def _load_system_presets_from_config() -> list[AccountPreset]:
    """从 config.yaml 读取系统预设方案。"""
    from aistudio_api.infrastructure.gateway.model_defaults import (
        get_configured_presets,
    )

    raw_list = get_configured_presets()
    if not raw_list:
        return []
    now = datetime.now(UTC).isoformat()
    presets = []
    for item in raw_list:
        p = AccountPreset.from_dict(item)
        p.is_system = True
        p.created_at = p.created_at or now
        p.updated_at = now
        presets.append(p)
    return presets


@dataclass
class Registry:
    """账号注册表。"""

    accounts: dict[str, AccountMeta]
    active_account_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "accounts": {k: v.to_dict() for k, v in self.accounts.items()},
            "active_account_id": self.active_account_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Registry:
        raw_accounts = data.get("accounts")
        accounts_dict: dict[str, object] = (
            raw_accounts if isinstance(raw_accounts, dict) else {}
        )
        accounts = {
            k: AccountMeta.from_dict(v)
            for k, v in accounts_dict.items()
            if isinstance(v, dict)
        }
        raw_active_id = data.get("active_account_id")
        return cls(
            accounts=accounts,
            active_account_id=str(raw_active_id) if raw_active_id is not None else None,
        )


class AccountStore:
    """账号存储管理器（线程安全单例，支持原子文件替换）。"""

    _instances: ClassVar[dict[Path, AccountStore]] = {}
    _singleton_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, accounts_dir: Path | None = None) -> AccountStore:
        resolved_dir = (accounts_dir or _resolve_accounts_dir()).resolve()
        with cls._singleton_lock:
            if resolved_dir not in cls._instances:
                instance = super().__new__(cls)
                cls._instances[resolved_dir] = instance
            return cls._instances[resolved_dir]

    def __init__(self, accounts_dir: Path | None = None) -> None:
        if getattr(self, "_initialized", False):
            return
        self._accounts_dir = (accounts_dir or _resolve_accounts_dir()).resolve()
        self._registry_path = self._accounts_dir / "registry.json"
        self._presets_path = self._accounts_dir / "presets.json"
        self._registry: Registry | None = None
        self._presets: dict[str, AccountPreset] | None = None
        self._lock = threading.RLock()
        self._ensure_dirs()
        self._migrate_legacy_if_needed()
        self._ensure_default_presets()
        self._initialized = True

    def _ensure_dirs(self) -> None:
        """确保目录存在。"""
        self._accounts_dir.mkdir(parents=True, exist_ok=True)

    def _migrate_legacy_if_needed(self) -> None:
        """如果 accounts 目录为空且存在 data/auth.json，自动迁移。"""
        if self._registry_path.exists():
            return  # 已有注册表，无需迁移
        legacy = _resolve_legacy_auth_file()
        if legacy is None:
            return
        # 创建一个迁移账号
        account_id = "acc_migrated"
        now = datetime.now(UTC).isoformat()
        meta = AccountMeta(
            id=account_id,
            name="迁移的账号",
            email=None,
            created_at=now,
            last_used=now,
        )
        account_dir = self._accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=True)
        # 复制 auth.json
        shutil.copy2(legacy, account_dir / "auth.json")
        # 写入 meta.json
        _atomic_write_json(account_dir / "meta.json", meta.to_dict())
        # 创建注册表
        registry = Registry(
            accounts={account_id: meta},
            active_account_id=account_id,
        )
        self._save_registry(registry)

    def _load_registry(self) -> Registry:
        """加载注册表（线程安全）。"""
        with self._lock:
            if self._registry is not None:
                return self._registry
            if not self._registry_path.exists():
                self._registry = Registry(accounts={})
                return self._registry
            try:
                data = json.loads(self._registry_path.read_text(encoding="utf-8"))
                self._registry = Registry.from_dict(data)
                reconciled = False
                for acc_id, acc in self._registry.accounts.items():
                    account_dir = self._accounts_dir / acc_id
                    meta_path = account_dir / "meta.json"
                    if meta_path.is_file():
                        try:
                            mdata = json.loads(meta_path.read_text(encoding="utf-8"))
                            if isinstance(mdata, dict):
                                if not acc.email and mdata.get("email"):
                                    acc.email = str(mdata["email"])
                                    reconciled = True
                                if (
                                    (not acc.tier or acc.tier == "Free")
                                    and mdata.get("tier")
                                    and mdata.get("tier") != "Free"
                                ):
                                    acc.tier = str(mdata["tier"])
                                    reconciled = True
                        except Exception:
                            pass
                if reconciled:
                    _atomic_write_json(self._registry_path, self._registry.to_dict())
            except Exception:
                self._registry = Registry(accounts={})
            return self._registry

    def _save_registry(self, registry: Registry) -> None:
        """保存注册表（线程安全+原子写入）。"""
        with self._lock:
            self._registry = registry
            _atomic_write_json(self._registry_path, registry.to_dict())

    def list_accounts(self) -> list[AccountMeta]:
        """列出所有账号。"""
        registry = self._load_registry()
        return list(registry.accounts.values())

    def refresh_duplicate_flags(self) -> int:
        """按邮箱重算重复标记（忽略大小写）。

        同一邮箱保留最早创建的账号，其余标记 ``duplicate_of=<保留账号 id>``；
        无邮箱或已不再重复的账号清除标记。返回被标记的重复账号数。
        """
        registry = self._load_registry()
        groups: dict[str, list[AccountMeta]] = {}
        for acc in registry.accounts.values():
            email = (acc.email or "").strip().lower()
            if email:
                groups.setdefault(email, []).append(acc)

        targets: dict[str, str | None] = {
            acc.id: None for acc in registry.accounts.values()
        }
        for group in groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda a: (a.created_at or "", a.id))
            keeper = group[0]
            for duplicate in group[1:]:
                targets[duplicate.id] = keeper.id

        changed = 0
        for acc in registry.accounts.values():
            target = targets.get(acc.id)
            if acc.duplicate_of == target:
                continue
            acc.duplicate_of = target
            changed += 1
            meta_path = _safe_account_dir(self._accounts_dir, acc.id) / "meta.json"
            if meta_path.exists():
                _atomic_write_json(meta_path, acc.to_dict())
        if changed:
            self._save_registry(registry)
        return sum(1 for value in targets.values() if value)

    def select_active_candidate(
        self, *, exclude: set[str] | None = None
    ) -> AccountMeta | None:
        """选择首个 enabled 且未过期的账号作为 rebind 目标（不修改 active）。"""
        registry = self._load_registry()
        excluded = exclude or set()
        for account in registry.accounts.values():
            if account.id in excluded:
                continue
            if account.enabled and not account.expired:
                return account
        return None

    def _find_registered_account(
        self, registry: Registry, account_id: str
    ) -> AccountMeta | None:
        return registry.accounts.get(account_id)

    def get_account(self, account_id: str) -> AccountMeta | None:
        """获取单个账号。"""
        registry = self._load_registry()
        return self._find_registered_account(registry, account_id)

    def get_active_account(self) -> AccountMeta | None:
        """读取当前活跃账号。

        active 已存在时原样返回（即使已被禁用），由控制面显式 rebind；
        不得在 rebind 前用 enabled 兜底改写 active。仅在 active 缺失时才
        从 enabled 且未过期账号中选择兜底。
        """
        registry = self._load_registry()
        if (
            registry.active_account_id
            and registry.active_account_id in registry.accounts
        ):
            return registry.accounts[registry.active_account_id]
        fallback = next(
            (
                acc
                for acc in registry.accounts.values()
                if acc.enabled and not acc.expired
            ),
            None,
        )
        if fallback is not None:
            registry.active_account_id = fallback.id
            self._save_registry(registry)
        return fallback

    def get_active_auth_path(self) -> Path | None:
        """获取当前活跃账号的 auth.json 路径。"""
        account = self.get_active_account()
        if account is None:
            return None
        return self._accounts_dir / account.id / "auth.json"

    def set_active_account(self, account_id: str) -> AccountMeta | None:
        """设置活跃账号，返回账号元数据或 None（如果不存在）。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return None
        changed = registry.active_account_id != acc.id
        registry.active_account_id = acc.id
        now = datetime.now(UTC).isoformat()
        acc.last_used = now
        if changed:
            self._save_registry(registry)
        return acc

    def save_account(
        self,
        name: str,
        email: str | None,
        storage_state: dict[str, object],
        account_id: str | None = None,
        auth_user: str = "0",
        cookie_id: str | None = None,
        tier: str = "Free",
        enabled: bool = True,
        group: str = "default",
    ) -> AccountMeta:
        """保存新账号。"""
        registry = self._load_registry()
        now = datetime.now(UTC).isoformat()
        created_at = now
        resolved_tier = tier or "Free"
        resolved_enabled = enabled
        resolved_group = group or "default"

        if account_id is None and email:
            for acc in registry.accounts.values():
                if acc.email == email and acc.auth_user == auth_user:
                    account_id = acc.id
                    created_at = acc.created_at
                    if resolved_tier == "Free" and acc.tier and acc.tier != "Free":
                        resolved_tier = acc.tier
                    if resolved_group == "default" and acc.group:
                        resolved_group = acc.group
                    if not acc.enabled and enabled is True:
                        resolved_enabled = acc.enabled
                    break

        if account_id is not None:
            existing = self._find_registered_account(registry, account_id)
            if existing is not None:
                account_id = existing.id
                created_at = existing.created_at
                if (
                    resolved_tier == "Free"
                    and existing.tier
                    and existing.tier != "Free"
                ):
                    resolved_tier = existing.tier
                if resolved_group == "default" and existing.group:
                    resolved_group = existing.group
                if not existing.enabled and enabled is True:
                    resolved_enabled = existing.enabled
        else:
            account_id = _generate_account_id()

        account_dir = _safe_account_dir(self._accounts_dir, account_id)
        safe_id = account_dir.name
        meta = AccountMeta(
            id=safe_id,
            name=name,
            email=email,
            created_at=created_at,
            last_used=now,
            auth_user=auth_user,
            cookie_id=cookie_id,
            tier=resolved_tier,
            auth_saved_at=now,
            enabled=resolved_enabled,
            group=resolved_group,
        )
        account_dir.mkdir(parents=True, exist_ok=True)
        # 原子写入 auth.json 与 meta.json
        _atomic_write_json(account_dir / "auth.json", storage_state)
        _atomic_write_json(account_dir / "meta.json", meta.to_dict())
        # 更新注册表
        registry.accounts[safe_id] = meta
        if registry.active_account_id is None:
            registry.active_account_id = safe_id
        self._save_registry(registry)
        self.refresh_duplicate_flags()
        return meta

    def delete_account(self, account_id: str) -> bool:
        """删除账号，返回是否成功。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return False
        safe_id = acc.id
        # 删除目录
        account_dir = _safe_account_dir(self._accounts_dir, safe_id)
        if account_dir.is_dir():
            shutil.rmtree(account_dir, ignore_errors=True)
        # 从注册表移除
        del registry.accounts[safe_id]
        if registry.active_account_id == safe_id:
            registry.active_account_id = next(
                (
                    acc.id
                    for acc in registry.accounts.values()
                    if acc.enabled and not acc.expired
                ),
                None,
            )
        self._save_registry(registry)
        self.refresh_duplicate_flags()
        return True

    def update_account(self, account_id: str, name: str) -> AccountMeta | None:
        """更新账号名称。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return None
        acc.name = name
        # 同步更新 meta.json
        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return acc

    def update_account_email(self, account_id: str, email: str) -> bool:
        """更新账号的登录邮箱，并持久化 meta.json 与 registry.json。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None or acc.email == email:
            return False
        acc.email = email
        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return True

    def update_account_tier(
        self, account_id: str, tier: str, checked_at: str | None = None
    ) -> bool:
        """更新账号的订阅权益等级 (Free / Pro / Ultra / Ultra_20X / Plus)，并持久化 meta.json 与 registry.json。"""
        from aistudio_api.domain.model_access import normalize_tier

        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return False
        clean_tier = normalize_tier(tier)
        changed = acc.tier != clean_tier
        acc.tier = clean_tier
        if checked_at is not None:
            acc.tier_checked_at = checked_at
        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return changed

    def update_account_refresh(
        self,
        account_id: str,
        *,
        tier: object = _UNSET,
        tier_checked_at: object = _UNSET,
        expired: object = _UNSET,
        email: object = _UNSET,
    ) -> bool:
        """更新账号刷新字段（tier/tier_checked_at/expired/email），返回是否有改动。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return False

        changed = False
        if tier is not _UNSET:
            new_tier = None if tier is None else str(tier)
            if acc.tier != new_tier:
                acc.tier = new_tier or "Free"
                changed = True
        if tier_checked_at is not _UNSET:
            new_checked = None if tier_checked_at is None else str(tier_checked_at)
            if acc.tier_checked_at != new_checked:
                acc.tier_checked_at = new_checked
                changed = True
        if expired is not _UNSET:
            new_expired = bool(expired)
            if acc.expired != new_expired:
                acc.expired = new_expired
                changed = True
        if email is not _UNSET:
            new_email = None if email is None else str(email)
            if acc.email != new_email:
                acc.email = new_email
                changed = True

        if not changed:
            return False

        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return True

    def update_account_keepalive(
        self,
        account_id: str,
        *,
        auth_saved_at: object = _UNSET,
        last_refresh_attempt_at: object = _UNSET,
        last_refresh_error: object = _UNSET,
    ) -> bool:
        """更新保活字段（auth 回写时间/刷新尝试/错误），返回是否有改动。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return False

        changed = False
        if auth_saved_at is not _UNSET:
            new_saved = None if auth_saved_at is None else str(auth_saved_at)
            if acc.auth_saved_at != new_saved:
                acc.auth_saved_at = new_saved
                changed = True
        if last_refresh_attempt_at is not _UNSET:
            new_attempt = (
                None
                if last_refresh_attempt_at is None
                else str(last_refresh_attempt_at)
            )
            if acc.last_refresh_attempt_at != new_attempt:
                acc.last_refresh_attempt_at = new_attempt
                changed = True
        if last_refresh_error is not _UNSET:
            new_error = None if last_refresh_error is None else str(last_refresh_error)
            if acc.last_refresh_error != new_error:
                acc.last_refresh_error = new_error
                changed = True

        if not changed:
            return False

        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return True

    def mark_auth_saved(self, account_id: str, *, at: str | None = None) -> bool:
        """记录凭证回写成功时间。"""
        return self.update_account_keepalive(
            account_id,
            auth_saved_at=at or datetime.now(UTC).isoformat(),
        )

    def mark_refresh_attempt(
        self, account_id: str, *, error: str | None = None, at: str | None = None
    ) -> bool:
        """记录一次账号信息刷新尝试及其错误。"""
        return self.update_account_keepalive(
            account_id,
            last_refresh_attempt_at=at or datetime.now(UTC).isoformat(),
            last_refresh_error=error,
        )

    def get_auth_path(self, account_id: str) -> Path | None:
        """获取指定账号的 auth.json 路径。"""
        return self.get_auth_path_optional(account_id, require_exists=True)

    def get_auth_path_optional(
        self, account_id: str, *, require_exists: bool = False
    ) -> Path | None:
        """获取指定账号的 auth.json 路径，可选是否要求文件已存在。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return None
        path = _safe_account_dir(self._accounts_dir, acc.id) / "auth.json"
        if require_exists and not path.exists():
            return None
        return path

    def get_profile_path(self, account_id: str) -> Path | None:
        """获取指定账号的 profile 目录路径。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return None
        return _safe_account_dir(self._accounts_dir, acc.id) / "profile"

    def toggle_account_enabled(
        self, account_id: str, enabled: bool | None = None
    ) -> AccountMeta | None:
        """切换账号的启用/禁用状态。若未指定 enabled 则取反。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return None
        acc.enabled = not acc.enabled if enabled is None else bool(enabled)
        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return acc

    def update_account_group(self, account_id: str, group: str) -> AccountMeta | None:
        """更新账号所属业务分组。"""
        registry = self._load_registry()
        acc = self._find_registered_account(registry, account_id)
        if acc is None:
            return None
        clean_group = str(group or "default").strip() or "default"
        acc.group = clean_group
        account_dir = _safe_account_dir(self._accounts_dir, acc.id)
        meta_path = account_dir / "meta.json"
        if meta_path.exists():
            _atomic_write_json(meta_path, acc.to_dict())
        self._save_registry(registry)
        return acc

    def list_groups(self) -> list[str]:
        """获取当前所有账号中存在的不重复分组列表。"""
        registry = self._load_registry()
        groups = {acc.group for acc in registry.accounts.values() if acc.group}
        if "default" not in groups:
            groups.add("default")
        return sorted(groups)

    # ─── 预设方案 (Presets) 管理 ───
    def _ensure_default_presets(self) -> None:
        if self._presets_path.exists():
            return
        defaults = {p.id: p for p in _load_system_presets_from_config()}
        if defaults:
            self._save_presets(defaults)

    def _load_presets(self) -> dict[str, AccountPreset]:
        with self._lock:
            if self._presets is not None:
                return self._presets
            sys_presets = _load_system_presets_from_config()
            if not self._presets_path.exists():
                defaults = {p.id: p for p in sys_presets}
                if defaults:
                    self._save_presets(defaults)
                self._presets = defaults
                return self._presets
            try:
                raw = json.loads(self._presets_path.read_text(encoding="utf-8"))
                presets: dict[str, AccountPreset] = {}
                if isinstance(raw, dict):
                    for pid, pdata in raw.items():
                        if isinstance(pdata, dict):
                            presets[str(pid)] = AccountPreset.from_dict(pdata)
                for sys_p in sys_presets:
                    if sys_p.id not in presets or presets[sys_p.id].is_system:
                        presets[sys_p.id] = sys_p
                self._presets = presets
            except Exception as exc:
                logger.warning("读取预设文件失败，回退到系统预设: %s", exc)
                self._presets = {p.id: p for p in sys_presets}
            return self._presets

    def _save_presets(self, presets: dict[str, AccountPreset]) -> None:
        """先原子落盘再更新缓存；写失败向上抛出且不留下半写文件。"""
        payload = {pid: p.to_dict() for pid, p in presets.items()}
        with self._lock:
            _atomic_write_json(self._presets_path, payload)
            self._presets = presets

    def list_presets(self) -> list[AccountPreset]:
        """列出所有预设方案。"""
        presets = self._load_presets()
        return list(presets.values())

    def get_preset(self, preset_id: str) -> AccountPreset | None:
        """获取单个预设方案。"""
        presets = self._load_presets()
        return presets.get(preset_id)

    def save_preset(self, preset: AccountPreset) -> AccountPreset:
        """创建或更新预设方案。"""
        presets = self._load_presets()
        now = datetime.now(UTC).isoformat()
        if not preset.created_at:
            preset.created_at = now
        preset.updated_at = now
        presets[preset.id] = preset
        self._save_presets(presets)
        return preset

    def delete_preset(self, preset_id: str) -> bool:
        """删除自定义预设方案（内置方案不可删除）。"""
        presets = self._load_presets()
        if preset_id not in presets:
            return False
        if presets[preset_id].is_system:
            raise ValueError("系统内置预设不允许删除")
        del presets[preset_id]
        self._save_presets(presets)
        return True

    def create_snapshot_preset(self, name: str, description: str = "") -> AccountPreset:
        """保存当前所有已启用账号的 ID 列表为预设方案。"""
        import secrets

        registry = self._load_registry()
        active_ids = [acc.id for acc in registry.accounts.values() if acc.enabled]
        now = datetime.now(UTC).isoformat()
        pid = f"preset_{secrets.token_hex(4)}"
        preset = AccountPreset(
            id=pid,
            name=name.strip() or "自定义快照方案",
            description=description.strip(),
            action="snapshot",
            account_ids=active_ids,
            is_system=False,
            created_at=now,
            updated_at=now,
        )
        return self.save_preset(preset)

    def apply_preset(self, preset_id: str) -> PresetApplyResult:
        """应用预设：更新 enabled 状态并返回显式 rebind 信号。

        D-05：在改写 enabled 前捕获旧 active；若旧 active 被本次预设禁用，
        不得静默改写/清空 active，也不得用 enabled 自动回退。由调用方
        （B2-3 的 manager/pool）执行有界 rebind 后再切换 active。
        """
        preset = self.get_preset(preset_id)
        if preset is None:
            raise ValueError(f"预设方案不存在: {preset_id}")

        registry = self._load_registry()
        previous_active_id = registry.active_account_id
        enabled_ids: list[str] = []
        disabled_ids: list[str] = []

        for acc in registry.accounts.values():
            should_enable = False
            if preset.action == "enable_all":
                should_enable = True
            elif preset.action == "disable_all":
                should_enable = False
            elif preset.action == "snapshot":
                should_enable = acc.id in preset.account_ids
            elif preset.action == "filter_tier":
                should_enable = acc.tier in preset.target_tiers
            elif preset.action == "filter_group":
                should_enable = acc.group in preset.target_groups
            else:
                should_enable = acc.id in preset.account_ids

            acc.enabled = should_enable
            account_dir = _safe_account_dir(self._accounts_dir, acc.id)
            meta_path = account_dir / "meta.json"
            if meta_path.exists():
                _atomic_write_json(meta_path, acc.to_dict())

            if should_enable:
                enabled_ids.append(acc.id)
            else:
                disabled_ids.append(acc.id)

        self._save_registry(registry)

        rebind_required = bool(
            previous_active_id and previous_active_id in disabled_ids
        )
        target = (
            self.select_active_candidate(exclude=set(disabled_ids))
            if rebind_required
            else None
        )
        return PresetApplyResult(
            preset=preset,
            enabled_account_ids=enabled_ids,
            disabled_account_ids=disabled_ids,
            previous_active_account_id=previous_active_id,
            rebind_required=rebind_required,
            active_account_id=target.id if target is not None else None,
            target_account=target,
        )
