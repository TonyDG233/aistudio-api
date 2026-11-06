"""C2a pure-offline UI debug sandbox.

When ``diagnostics.ui_debug`` is enabled the lifespan must not construct a real
``AIStudioClient``/browser, start pooled instances, warm up control sessions,
run model discovery, keepalive or account metadata network refreshers.  The API
routes keep their normal REST contract but read/write this in-memory sandbox
backed by a throw-away ``AccountStore`` in a temp directory.

Everything here is deterministic and performs no network or browser work.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.application.account_service import AccountService
from aistudio_api.config import resolve_default_config_template
from aistudio_api.domain.instances import Instance, InstanceList, InstanceState
from aistudio_api.domain.model_access import (
    instance_lane_for_tier,
    normalize_tier,
    resolve_tier_selector,
    tier_matches_selector,
)
from aistudio_api.infrastructure.account.account_info_refresher import RefreshResult
from aistudio_api.infrastructure.account.account_store import AccountMeta, AccountStore
from aistudio_api.infrastructure.browser.fingerprint import get_account_fingerprint
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_keepalive_settings,
)
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("ui-debug")

SCENARIOS = ("all_states", "mass_accounts", "narrow_edge", "empty")
_SANDBOX_DRAIN_TIMEOUT_S = 0.05
ACTIONS = (
    "reset",
    "instance_inflight",
    "account_expired",
    "instance_cooldown",
    "keepalive_partial",
)
_SANDBOX_MAX_CONTEXTS = 10

# Built-in, deterministic model catalog for ui_debug.  It intentionally mirrors
# the cold-start fallback shape (Free + Paid) but lives here so the model routes
# never import ``model_discovery`` (which owns a real snapshot read) or construct
# an ``AccountStore`` while ui_debug is on.
_SANDBOX_MODEL_CATALOG: tuple[dict[str, object], ...] = (
    {
        "id": "gemini-3.7-flash",
        "displayName": "Gemini 3.7 Flash",
        "description": "Gemini 3.7 Flash default text model",
        "category": "flash",
        "paid": False,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-3.8-flash",
        "displayName": "Gemini 3.8 Flash",
        "description": "Latest Gemini 3.8 Flash model",
        "category": "flash",
        "paid": False,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-3.5-flash-lite",
        "displayName": "Gemini 3.5 Flash Lite",
        "description": "Ultra fast and lightweight model",
        "category": "flash",
        "paid": False,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-3.1-pro-preview",
        "displayName": "Gemini 3.1 Pro Preview",
        "description": "Pro capabilities with complex reasoning",
        "category": "flagship",
        "paid": False,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemma-4-31b-it",
        "displayName": "Gemma 4 31B IT",
        "description": "Open weights flagship text model",
        "category": "gemma",
        "paid": False,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-pro-latest",
        "displayName": "Gemini Pro Latest",
        "description": "Latest stable Gemini Pro alias",
        "category": "flagship",
        "paid": False,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-4-argon",
        "displayName": "Gemini 4 Argon",
        "description": "Our most capable model",
        "category": "flagship",
        "paid": True,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-3.6-flash",
        "displayName": "Gemini 3.6 Flash",
        "description": "High throughput flash model",
        "category": "flash",
        "paid": True,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "gemini-3.1-flash-image",
        "displayName": "Nano Banana 2",
        "description": "Image generation and multimodal editing",
        "category": "image",
        "paid": True,
        "is_image_model": True,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
    {
        "id": "veo-3.1-generate-preview",
        "displayName": "Veo 3.1",
        "description": "Video generation preview model",
        "category": "video",
        "paid": True,
        "is_video_model": True,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    },
)


def sandbox_model_catalog() -> list[dict[str, object]]:
    """Return the built-in sandbox model list (Free + Paid), no disk/network."""
    return [dict(item) for item in _SANDBOX_MODEL_CATALOG]


def sandbox_model_by_id(model_id: str) -> dict[str, object] | None:
    """Look up one sandbox model by bare or ``models/``-prefixed id."""
    clean = model_id.removeprefix("models/").strip().lower()
    for item in _SANDBOX_MODEL_CATALOG:
        if str(item.get("id") or "").lower() == clean:
            return dict(item)
    return None


class UiDebugIsolation:
    """Redirect every persisted path touched by config/log/key routes to a temp dir.

    ui_debug must be strictly offline and must never rewrite the operator's real
    ``config.yaml``, accounts, stats, model cache or log archives.  The lifespan
    enters this before ``validate_config()`` (which may migrate/write config) and
    closes it after the sandbox, restoring the original environment and settings.
    """

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="aistudio-ui-debug-paths-"))
        self.config_file = self.root / "config.yaml"
        self.data_dir = self.root / "data"
        self.accounts_dir = self.data_dir / "accounts"
        self.logs_dir = self.data_dir / "logs"
        self.dumps_dir = self.data_dir / "dumps"
        self._saved_env: dict[str, str | None] = {}
        self._saved_settings: dict[str, object] = {}
        self._entered = False

    def enter(self) -> UiDebugIsolation:
        try:
            for directory in (
                self.data_dir,
                self.accounts_dir,
                self.logs_dir,
                self.dumps_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(
                resolve_default_config_template().read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            overrides = {
                "AISTUDIO_CONFIG_PATH": str(self.config_file),
                "AISTUDIO_DATA_DIR": str(self.data_dir),
                "AISTUDIO_ACCOUNTS_DIR": str(self.accounts_dir),
                "AISTUDIO_STATS_FILE": str(self.data_dir / "stats.json"),
                "AISTUDIO_ROTATOR_STATE_FILE": str(
                    self.data_dir / "rotator_state.json"
                ),
                "AISTUDIO_MODEL_CACHE_FILE": str(
                    self.data_dir / "models_cache.json"
                ),
                "AISTUDIO_LOG_DIR": str(self.logs_dir),
                "AISTUDIO_DUMP_DIR": str(self.dumps_dir),
                "AISTUDIO_DUMP_RAW_RESPONSE_DIR": str(self.dumps_dir / "raw"),
            }
            for key, value in overrides.items():
                self._saved_env[key] = os.environ.get(key)
                os.environ[key] = value

            from aistudio_api.config import settings

            for attr, value in (
                ("config_file", str(self.config_file)),
                ("data_dir", str(self.data_dir)),
                ("accounts_dir", str(self.accounts_dir)),
                ("stats_file", str(self.data_dir / "stats.json")),
                ("rotator_state_file", str(self.data_dir / "rotator_state.json")),
                ("model_cache_file", str(self.data_dir / "models_cache.json")),
                ("dump_dir", str(self.dumps_dir)),
                ("dump_raw_response_dir", str(self.dumps_dir / "raw")),
            ):
                self._saved_settings[attr] = getattr(settings, attr)
                setattr(settings, attr, value)
            self._entered = True
            return self
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        from aistudio_api.config import settings

        for attr, value in self._saved_settings.items():
            setattr(settings, attr, value)
        self._saved_settings.clear()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._saved_env.clear()
        if self._entered:
            self._entered = False
        shutil.rmtree(self.root, ignore_errors=True)


@dataclass
class _AccountSeed:
    id: str
    name: str
    email: str | None
    tier: str = "Free"
    enabled: bool = True
    expired: bool = False
    group: str = "default"
    cookie_id: str | None = None
    session_expired: bool = False
    auth_cooldown: int = 0
    quota_available: bool = True


@dataclass
class _InstanceSeed:
    id: str
    account_id: str
    state: InstanceState
    surface: str = "chat"
    only_tier: str = ""
    min_tier: str = ""
    max_tier: str = ""
    in_flight: int = 0


@dataclass
class _SandboxInstance:
    id: str
    number: int
    account_id: str
    state: InstanceState
    surface: str
    only_tier: str = ""
    min_tier: str = ""
    max_tier: str = ""
    in_flight: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def selector(self) -> Any:
        if self.only_tier:
            return resolve_tier_selector(only_tier=self.only_tier)
        return resolve_tier_selector(
            min_tier=self.min_tier or None,
            max_tier=self.max_tier or None,
        )

    def accepts_tier(self, tier: object) -> bool:
        return tier_matches_selector(tier, self.selector)


def _sandbox_cookie_state() -> dict[str, object]:
    return {
        "cookies": [
            {
                "name": "SID",
                "value": "ui-debug-sandbox",
                "domain": ".google.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
            }
        ],
        "origins": [],
    }


class SandboxInstanceManager:
    """In-memory instance manager with the same public REST semantics."""

    def __init__(self, account_store: AccountStore) -> None:
        self._accounts = account_store
        self._records: dict[str, _SandboxInstance] = {}
        self._next_number = 1
        self._latest_id: str | None = None
        self._current_id: str | None = None
        self._drain_timeout_s = _SANDBOX_DRAIN_TIMEOUT_S

    @property
    def max_instances(self) -> int:
        return _SANDBOX_MAX_CONTEXTS

    @property
    def current_instance_id(self) -> str | None:
        return self._current_id

    def reset(self) -> None:
        self._records.clear()
        self._next_number = 1
        self._latest_id = None
        self._current_id = None

    def seed(self, seeds: tuple[_InstanceSeed, ...]) -> None:
        self.reset()
        for seed in seeds:
            number = self._next_number
            self._next_number += 1
            record = _SandboxInstance(
                id=seed.id,
                number=number,
                account_id=seed.account_id,
                state=seed.state,
                surface=seed.surface,
                only_tier=seed.only_tier,
                min_tier=seed.min_tier,
                max_tier=seed.max_tier,
                in_flight=seed.in_flight,
            )
            self._records[record.id] = record
            if record.state is InstanceState.ACTIVE:
                self._latest_id = record.id
                if self._current_id is None:
                    self._current_id = record.id

    async def startup(self) -> None:
        return None

    async def list_instances(self) -> InstanceList:
        return InstanceList(
            max_instances=self.max_instances,
            latest_instance_id=self._latest_id,
            current_instance_id=self._current_id,
            instances=self.views(),
        )

    def views(self) -> tuple[Instance, ...]:
        return tuple(
            self._view(record)
            for record in sorted(self._records.values(), key=lambda item: item.number)
        )

    async def create(
        self,
        account_id: str,
        surface: str | None = None,
        *,
        only_tier: str | None = None,
        min_tier: str | None = None,
        max_tier: str | None = None,
    ) -> Instance:
        account = self._validate_account(account_id)
        target_surface = self._normalize_surface(surface)
        if self._has_record_for_account(account.id):
            from aistudio_api.application.instance_manager import (
                InstanceAlreadyExistsError,
            )

            raise InstanceAlreadyExistsError(
                f"账号 {account_id} 已存在实例（同账号 chat/build 不并存）"
            )
        if len(self._records) >= self.max_instances:
            from aistudio_api.application.instance_manager import (
                InstanceCapacityExhaustedError,
            )

            raise InstanceCapacityExhaustedError("实例上限已满")
        selector = self._resolve_selector(
            account, only_tier=only_tier, min_tier=min_tier, max_tier=max_tier
        )
        if not tier_matches_selector(account.tier, selector):
            from aistudio_api.application.instance_manager import (
                InstanceInvalidAccountError,
            )

            raise InstanceInvalidAccountError(
                f"账号 tier={account.tier} 不满足实例 tier 区间"
            )
        number = self._next_number
        self._next_number += 1
        record = _SandboxInstance(
            id=f"inst-{number}",
            number=number,
            account_id=account.id,
            state=InstanceState.ACTIVE,
            surface=target_surface,
            only_tier=selector.only_tier or "",
            min_tier=selector.min_tier,
            max_tier=selector.max_tier,
        )
        self._records[record.id] = record
        self._latest_id = record.id
        if self._current_id is None:
            self._current_id = record.id
        if self._accounts.get_active_account() is None:
            self._accounts.set_active_account(account.id)
        return self._view(record)

    async def close(self, instance_id: str | None = None) -> Instance | None:
        if instance_id is None:
            self.reset()
            return None
        record = self._record(instance_id)
        if record.in_flight > 0:
            from aistudio_api.application.instance_manager import InstanceBusyError

            raise InstanceBusyError("该实例仍有在途请求")
        view = self._view(record, state=InstanceState.RECLAIMING)
        self._records.pop(record.id, None)
        self._repair_pointers()
        return view

    async def switch_to(self, instance_id: str, surface: str | None = None) -> Instance:
        record = self._record(instance_id)
        record.surface = self._normalize_surface(surface)
        self._current_id = record.id
        self._accounts.set_active_account(record.account_id)
        return self._view(record)

    async def restart(self, instance_id: str) -> Instance:
        record = self._record(instance_id)
        self._require_bound_account(record)
        if record.in_flight > 0:
            from aistudio_api.application.instance_manager import InstanceBusyError

            raise InstanceBusyError("该实例仍有在途请求")
        record.state = InstanceState.ACTIVE
        self._latest_id = record.id
        if self._current_id is None:
            self._current_id = record.id
        return self._view(record)

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        record = self._record(instance_id)
        account = self._validate_account(account_id)
        if record.account_id == account.id:
            return self._view(record)
        if instance_lane_for_tier(account.tier) != self._lane(record):
            from aistudio_api.application.instance_manager import (
                InstanceInvalidAccountError,
            )

            raise InstanceInvalidAccountError(
                "Free/paid 实例池互相隔离，不能跨池改绑账号"
            )
        if not record.accepts_tier(account.tier):
            from aistudio_api.application.instance_manager import (
                InstanceInvalidAccountError,
            )

            raise InstanceInvalidAccountError(
                f"账号 tier={account.tier} 不满足实例 tier 区间"
            )
        other = self._record_for_account(account.id, exclude_id=record.id)
        if other is not None:
            from aistudio_api.application.instance_manager import (
                InstanceAlreadyExistsError,
            )

            raise InstanceAlreadyExistsError(
                f"账号 {account_id} 已绑定实例 #{other.number}，"
                f"请先在实例 #{other.number} 换绑其他账号或关闭该实例后"
                "再绑定此槽位"
            )
        if record.in_flight > 0:
            # Bounded safe drain: stop accepting new work, wait once, then
            # force-close the simulated old runtime before switching accounts.
            await self._drain(record)
        record.account_id = account.id
        record.state = InstanceState.ACTIVE
        if self._current_id == record.id:
            self._accounts.set_active_account(account.id)
        return self._view(record)

    async def wake(self, instance_id: str) -> Instance:
        record = self._record(instance_id)
        if record.state is InstanceState.ACTIVE:
            return self._view(record)
        if record.state not in (InstanceState.UNSTARTED, InstanceState.SLEEPING):
            from aistudio_api.application.instance_manager import InstanceNotReadyError

            raise InstanceNotReadyError("仅 UNSTARTED/SLEEPING 实例可唤醒")
        self._require_bound_account(record)
        record.state = InstanceState.ACTIVE
        self._latest_id = record.id
        return self._view(record)

    async def rebind_account(
        self,
        target_account_id: str,
        *,
        previous_account_id: str | None = None,
        reason: str = "d-05",
    ) -> Instance:
        target = self._validate_account(target_account_id)
        target_lane = instance_lane_for_tier(target.tier)
        previous_id = previous_account_id
        if previous_id is None and self._current_id:
            current = self._records.get(self._current_id)
            if current is not None:
                previous_id = current.account_id

        affected = [
            record
            for record in self._records.values()
            if record.account_id
            and (
                record.account_id == previous_id
                or not self._is_runnable(record.account_id)
            )
        ]
        target_record = self._find_target_record(target, target_lane)
        if target_record is None:
            from aistudio_api.application.instance_manager import InstanceNotReadyError

            raise InstanceNotReadyError(
                f"没有可用于账号 {target_account_id} 的 {target_lane} 实例"
            )

        for record in affected:
            if record.id == target_record.id:
                continue
            self._retire(record)
        if target_record in affected or target_record.account_id != target.id:
            self._retire(target_record)

        target_record.account_id = target.id
        target_record.state = InstanceState.ACTIVE
        target_record.in_flight = 0
        self._current_id = target_record.id
        self._latest_id = target_record.id
        self._accounts.set_active_account(target.id)
        log.info(
            "[ui-debug] %s rebind complete %s -> %s",
            reason,
            previous_id,
            target.id,
        )
        return self._view(target_record)

    def ready_slot_for_account(self, account_id: str) -> object | None:
        if not account_id:
            return None
        for record in self._records.values():
            if record.account_id == account_id and record.state is InstanceState.ACTIVE:
                self._current_id = record.id
                return record
        return None

    def instance_account_ids(self) -> set[str]:
        return {
            record.account_id for record in self._records.values() if record.account_id
        }

    def snapshot(self) -> dict[str, object]:
        return {
            "max_instances": self.max_instances,
            "latest_instance_id": self._latest_id,
            "current_instance_id": self._current_id,
            "instances": [self.payload(record) for record in self._records.values()],
        }

    def payload(self, record: _SandboxInstance) -> dict[str, object]:
        account = (
            self._accounts.get_account(record.account_id) if record.account_id else None
        )
        return {
            "id": record.id,
            "number": record.number,
            "account_id": record.account_id,
            "state": record.state.value,
            "surface": record.surface,
            "only_tier": record.only_tier,
            "min_tier": record.min_tier,
            "max_tier": record.max_tier,
            "tier_selector": {
                "only_tier": record.only_tier,
                "min_tier": record.min_tier or self._selector_min(record),
                "max_tier": record.max_tier or self._selector_max(record),
            },
            "in_flight": record.in_flight,
            "latest": record.id == self._latest_id,
            "account_email": account.email if account is not None else None,
            "account_name": account.name if account is not None else "",
            "account_tier": account.tier if account is not None else "",
            "tier": account.tier if account is not None else "",
            "fingerprint": get_account_fingerprint(record.account_id),
            "fingerprint_summary": get_account_fingerprint(record.account_id),
            "last_used": record.started_at,
        }

    def records(self) -> tuple[_SandboxInstance, ...]:
        return tuple(self._records.values())

    def first_instance(self) -> _SandboxInstance | None:
        return next(iter(self._records.values()), None)

    def get_record(self, instance_id: str) -> _SandboxInstance | None:
        return self._records.get(instance_id)

    def _view(
        self, record: _SandboxInstance, *, state: InstanceState | None = None
    ) -> Instance:
        account = (
            self._accounts.get_account(record.account_id) if record.account_id else None
        )
        return Instance(
            id=record.id,
            number=record.number,
            account_id=record.account_id,
            state=state or record.state,
            surface=record.surface,
            min_tier=record.min_tier or self._selector_min(record),
            only_tier=record.only_tier,
            max_tier=record.max_tier or self._selector_max(record),
            in_flight=record.in_flight,
            latest=record.id == self._latest_id,
            tier=account.tier if account is not None else "",
            last_used=record.started_at,
        )

    def _selector_min(self, record: _SandboxInstance) -> str:
        return record.selector.min_tier

    def _selector_max(self, record: _SandboxInstance) -> str:
        return record.selector.max_tier

    def _lane(self, record: _SandboxInstance) -> str:
        return record.selector.lane

    def _retire(self, record: _SandboxInstance) -> None:
        record.in_flight = 0
        record.account_id = ""
        record.state = InstanceState.SLEEPING

    async def _drain(self, record: _SandboxInstance) -> bool:
        """Bounded drain for the synthesized in-flight counter.

        The sandbox has no real request handles, so a non-zero counter can only
        drain by timeout. After the bound expires the simulated Page/Context is
        considered force-closed and the slot becomes reusable.
        """
        if record.in_flight <= 0:
            return False
        await asyncio.sleep(self._drain_timeout_s)
        timed_out = record.in_flight > 0
        record.in_flight = 0
        return timed_out

    def _record_for_account(
        self, account_id: str, *, exclude_id: str | None = None
    ) -> _SandboxInstance | None:
        for record in self._records.values():
            if (
                record.account_id == account_id
                and record.id != exclude_id
                and record.state
                not in (
                    InstanceState.RECLAIMING,
                    InstanceState.RETIRING,
                    InstanceState.INVALID,
                )
            ):
                return record
        return None

    def _find_target_record(
        self, target: AccountMeta, target_lane: str
    ) -> _SandboxInstance | None:
        for record in self._records.values():
            if record.account_id == target.id and record.accepts_tier(target.tier):
                return record
        for record in self._records.values():
            if (
                record.selector.lane == target_lane
                and not record.account_id
                and record.accepts_tier(target.tier)
            ):
                return record
        if len(self._records) < self.max_instances:
            number = self._next_number
            self._next_number += 1
            selector = resolve_tier_selector(only_tier=normalize_tier(target.tier))
            record = _SandboxInstance(
                id=f"inst-{number}",
                number=number,
                account_id="",
                state=InstanceState.UNSTARTED,
                surface="chat",
                only_tier=selector.only_tier or "",
                min_tier=selector.min_tier,
                max_tier=selector.max_tier,
            )
            self._records[record.id] = record
            return record
        return None

    def _is_runnable(self, account_id: str) -> bool:
        account = self._accounts.get_account(account_id)
        return account is not None and account.enabled and not account.expired

    def _require_bound_account(self, record: _SandboxInstance) -> AccountMeta:
        from aistudio_api.application.instance_manager import (
            InstanceInvalidAccountError,
        )

        if not record.account_id:
            raise InstanceInvalidAccountError("实例账号不可用，请重新选择账号")
        return self._validate_account(record.account_id)

    def _validate_account(self, account_id: str) -> AccountMeta:
        from aistudio_api.application.instance_manager import (
            InstanceAccountNotFoundError,
            InstanceInvalidAccountError,
        )

        account = self._accounts.get_account(account_id)
        if account is None:
            raise InstanceAccountNotFoundError("账号不存在")
        if not account.enabled:
            raise InstanceInvalidAccountError("账号已停用")
        if account.expired:
            raise InstanceInvalidAccountError("账号已失效，请重新登录")
        if (
            self._accounts.get_auth_path_optional(account.id, require_exists=True)
            is None
        ):
            raise InstanceInvalidAccountError("账号凭据缺失，请重新登录")
        return account

    def _resolve_selector(
        self,
        account: AccountMeta,
        *,
        only_tier: str | None,
        min_tier: str | None,
        max_tier: str | None,
    ) -> Any:
        if any(value for value in (only_tier, min_tier, max_tier)):
            return resolve_tier_selector(
                only_tier=only_tier or None,
                min_tier=min_tier or None,
                max_tier=max_tier or None,
            )
        return resolve_tier_selector(only_tier=normalize_tier(account.tier))

    def _record(self, instance_id: str) -> _SandboxInstance:
        record = self._records.get(instance_id)
        if record is None:
            from aistudio_api.application.instance_manager import (
                InstanceNotFoundError,
            )

            raise InstanceNotFoundError("实例不存在")
        return record

    def _has_record_for_account(
        self, account_id: str, *, exclude_id: str | None = None
    ) -> bool:
        return self._record_for_account(account_id, exclude_id=exclude_id) is not None

    def _repair_pointers(self) -> None:
        if self._latest_id not in self._records:
            candidates = [
                record
                for record in self._records.values()
                if record.state is not InstanceState.RECLAIMING
            ]
            self._latest_id = (
                max(candidates, key=lambda item: item.started_at).id
                if candidates
                else None
            )
        current = self._records.get(self._current_id or "")
        if current is not None and current.state is InstanceState.ACTIVE:
            return
        active = [
            record
            for record in self._records.values()
            if record.state is InstanceState.ACTIVE
        ]
        self._current_id = (
            max(active, key=lambda item: item.started_at).id if active else None
        )

    @staticmethod
    def _normalize_surface(surface: str | None) -> str:
        if surface in (None, "", "chat"):
            return "chat"
        if surface == "build":
            return "build"
        from aistudio_api.application.instance_manager import InstanceNotReadyError

        raise InstanceNotReadyError(f"未知 surface: {surface}")


class SandboxRotator:
    """In-memory rotator used by /rotation and /stats while ui_debug is on."""

    quota_scope = "account"

    def __init__(self, sandbox: UISandbox) -> None:
        self._sandbox = sandbox
        self._stats: dict[str, AccountStats] = {}

    def get_account_stats(self, account_id: str) -> AccountStats | None:
        if self._sandbox.account_service.get_account(account_id) is None:
            return None
        return self._stats_for(account_id)

    def get_all_stats(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for account in self._sandbox.account_service.list_accounts():
            flags = self._sandbox.flags(account.id)
            cooldown = int(flags["auth_cooldown"])
            stats = self._stats_for(account.id)
            result[account.id] = {
                "name": account.name,
                "email": account.email,
                "auth_user": account.auth_user,
                "tier": account.tier,
                "quota_scope": self.quota_scope,
                "enabled": account.enabled,
                "group": account.group,
                "requests": stats.total_requests,
                "success": stats.total_success,
                "rate_limited": stats.total_rate_limited,
                "errors": stats.total_errors,
                "today_total_requests": 0,
                "today_total_tokens": 0,
                "last_used": None,
                "last_rate_limited": None,
                "is_available": self._sandbox.is_available(account.id),
                "session_expired": bool(flags["session_expired"]),
                "auth_cooldown": cooldown,
                "auth_errors": stats.auth_errors,
                "cooldown_remaining": cooldown,
                "model_cooldowns": {},
                "today_models": {},
                "model_requests": {},
                "model_rate_limited": {},
                "model_drip_mode": {},
            }
        return result

    def clear_cooldown(
        self, account_id: str | None = None, model: str | None = None
    ) -> None:
        del model
        if account_id is None:
            self.clear_all_cooldowns()
            return
        self._sandbox.clear_cooldown(account_id)

    def clear_all_cooldowns(self) -> None:
        for account in self._sandbox.account_service.list_accounts():
            self._sandbox.clear_cooldown(account.id)

    def cleanup_ghost_accounts(self) -> list[str]:
        live = {account.id for account in self._sandbox.account_service.list_accounts()}
        ghosts = [account_id for account_id in self._stats if account_id not in live]
        for account_id in ghosts:
            self._stats.pop(account_id, None)
        return ghosts

    def reset(self) -> None:
        self._stats.clear()

    def add_account(self, account_id: str) -> None:
        self._stats_for(account_id)

    def remove_account(self, account_id: str) -> None:
        self._stats.pop(account_id, None)

    async def get_next_account(self) -> AccountMeta | None:
        for account in self._sandbox.account_service.list_accounts():
            if self._sandbox.is_available(account.id):
                return account
        return None

    def _stats_for(self, account_id: str) -> AccountStats:
        stats = self._stats.get(account_id)
        if stats is None:
            stats = AccountStats(account_id=account_id, quota_scope=self.quota_scope)
            self._stats[account_id] = stats
        flags = self._sandbox.flags(account_id)
        stats.session_expired = bool(flags["session_expired"])
        stats.auth_cooldown = time.time() + int(flags["auth_cooldown"])
        return stats


class SandboxAccountRefresher:
    """Offline account refresher: reports current sandbox state, no network."""

    def __init__(self, sandbox: UISandbox) -> None:
        self._sandbox = sandbox

    async def refresh_accounts(
        self, accounts: list[AccountMeta], *, refresh_email: bool = True
    ) -> list[RefreshResult]:
        results: list[RefreshResult] = []
        checked_at = datetime.now(UTC).isoformat()
        for account in accounts:
            current = self._sandbox.account_service.get_account(account.id)
            if current is None:
                results.append(
                    RefreshResult(
                        account_id=account.id,
                        ok=False,
                        error="账号不存在",
                        checked_at=checked_at,
                    )
                )
                continue
            if refresh_email and not current.email:
                self._sandbox.account_service.update_account_email(
                    current.id, f"ui-debug.{current.id}@example.com"
                )
                current = (
                    self._sandbox.account_service.get_account(current.id) or current
                )
            self._sandbox.account_service.update_account_tier(
                current.id,
                normalize_tier(current.tier),
                checked_at=checked_at,
            )
            self._sandbox._clear_recovery_state(current.id)
            current = self._sandbox.account_service.get_account(current.id) or current
            results.append(
                RefreshResult(
                    account_id=current.id,
                    ok=True,
                    tier=current.tier,
                    expired=bool(current.expired),
                    email=current.email,
                    checked_at=checked_at,
                )
            )
        return results


def _setting_int(settings: dict[str, object], key: str, default: int) -> int:
    value = settings.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


class SandboxKeepalive:
    """Offline keepalive status/manual runner."""

    def __init__(self, sandbox: UISandbox) -> None:
        self._sandbox = sandbox
        self._last_results: dict[str, dict[str, object]] = {}
        self._last_error: str | None = None

    async def start(self, startup_metadata_done: bool = False) -> None:
        del startup_metadata_done

    async def stop(self) -> None:
        return None

    def reset(self) -> None:
        self._last_results.clear()
        self._last_error = None

    async def run_now(
        self, task: str, *, trigger: str = "api", settings: object | None = None
    ) -> dict[str, object]:
        del settings
        if task not in ("metadata_refresh", "auth_update", "active_activity"):
            return {"ok": False, "task": task, "reason": "unknown_task"}
        at = datetime.now(UTC).isoformat()
        if self._sandbox.keepalive_partial:
            result: dict[str, object] = {
                "ok": False,
                "task": task,
                "reason": "sandbox_partial",
                "error": "sandbox scenario: partial keepalive",
                "at": at,
                "trigger": trigger,
            }
        else:
            result = {
                "ok": True,
                "task": task,
                "reason": "sandbox",
                "accounts": [
                    account.id
                    for account in self._sandbox.account_service.list_accounts()
                ],
                "at": at,
                "trigger": trigger,
            }
        self._last_results[task] = result
        return result

    def status(self) -> dict[str, object]:
        settings = get_configured_keepalive_settings()
        in_flight = sum(record.in_flight for record in self._sandbox.manager.records())
        return {
            "settings": settings,
            "tasks": ["metadata_refresh", "auth_update", "active_activity"],
            "enabled": {
                "auth_update": bool(settings.get("auth_update", True)),
                "metadata_refresh": bool(settings.get("metadata_refresh", True)),
                "active_activity": bool(settings.get("active_activity", False)),
            },
            "intervals": {
                "auth_update_hours": _setting_int(settings, "auth_update_hours", 24),
                "metadata_refresh_hours": _setting_int(
                    settings, "metadata_refresh_hours", 24
                ),
                "active_activity_seconds": _setting_int(
                    settings, "active_activity_seconds", 60
                ),
            },
            "next_due": {},
            "busy": False,
            "running": None,
            "last_run": {
                task: entry.get("at") for task, entry in self._last_results.items()
            },
            "last_results": dict(self._last_results),
            "last_error": self._last_error,
            "in_flight": in_flight,
            "pool_slots": len(self._sandbox.manager.records()),
        }


class UISandbox:
    """Stateful offline runtime used by all UI-facing routes in ui_debug mode."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="aistudio-ui-debug-"))
        try:
            accounts_dir = self.root / "accounts"
            accounts_dir.mkdir(parents=True, exist_ok=True)
            (accounts_dir / "registry.json").write_text(
                '{"accounts":{},"active_account_id":null}', encoding="utf-8"
            )
            self._store = AccountStore(accounts_dir)
            self._store.refresh_duplicate_flags()
            self.account_service = AccountService(self._store)
            self.manager = SandboxInstanceManager(self._store)
            self.rotator = SandboxRotator(self)
            self.refresher = SandboxAccountRefresher(self)
            self.keepalive = SandboxKeepalive(self)
            self.scenario = "all_states"
            self.keepalive_partial = False
            self._flags: dict[str, dict[str, Any]] = {}
            self._closed = False
            self.reset("all_states")
        except Exception:
            shutil.rmtree(self.root, ignore_errors=True)
            raise

    # ------------------------------------------------------------ lifecycle
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.manager.reset()
        shutil.rmtree(self.root, ignore_errors=True)

    # -------------------------------------------------------------- scenario
    def reset(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario: {scenario}")
        for account in list(self._store.list_accounts()):
            self._store.delete_account(account.id)
        self._flags.clear()
        self.scenario = scenario
        self.keepalive_partial = False
        self.manager.reset()
        self.rotator.reset()
        self.keepalive.reset()
        if scenario == "empty":
            return
        if scenario == "mass_accounts":
            accounts, instances, active_id = _mass_scenario()
        elif scenario == "narrow_edge":
            accounts, instances, active_id = _narrow_edge_scenario()
        else:
            accounts, instances, active_id = _all_states_scenario()
        self._seed(accounts, instances, active_id)

    def apply(
        self,
        *,
        scenario: str | None = None,
        action: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, object]:
        if scenario is not None:
            self.reset(scenario)
        if action is None:
            return self.snapshot()
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")
        if action == "reset":
            self.reset(scenario or self.scenario)
        elif action == "instance_inflight":
            self._action_instance_inflight(target_id)
        elif action == "account_expired":
            self._action_account_expired(target_id)
        elif action == "instance_cooldown":
            self._action_instance_cooldown(target_id)
        elif action == "keepalive_partial":
            self.keepalive_partial = True
            self.keepalive._last_error = "sandbox scenario: partial keepalive"
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        return {
            "ui_debug": True,
            "ui_debug_scenario": self.scenario,
            "enabled": True,
            "scenario": self.scenario,
            "scenarios": list(SCENARIOS),
            "quick_actions": [action for action in ACTIONS if action != "reset"],
            "accounts": [
                self.account_payload(account)
                for account in self.account_service.list_accounts()
            ],
            "instances": self.manager.snapshot(),
            "rotation": self.rotator.get_all_stats(),
            "keepalive": {"available": True, **self.keepalive.status()},
        }

    # --------------------------------------------------------------- account
    def flags(self, account_id: str) -> dict[str, Any]:
        flags = self._flags.get(account_id)
        if flags is None:
            flags = {
                "session_expired": False,
                "auth_cooldown": 0,
                "quota_available": True,
                "_cooldown_until": 0.0,
            }
            self._flags[account_id] = flags
        remaining = int(flags.get("_cooldown_until", 0.0) - time.time())
        flags["auth_cooldown"] = max(0, remaining)
        return flags

    def is_available(self, account_id: str) -> bool:
        account = self.account_service.get_account(account_id)
        if account is None:
            return False
        flags = self.flags(account_id)
        return bool(
            account.enabled
            and not account.expired
            and not flags["session_expired"]
            and int(flags["auth_cooldown"]) == 0
            and flags["quota_available"]
        )

    def quota_exhausted(self, account_id: str) -> bool:
        """True only when quota is the sole blocker (cooldown/expiry is separate)."""
        account = self.account_service.get_account(account_id)
        if account is None:
            return False
        flags = self.flags(account_id)
        return bool(
            account.enabled
            and not account.expired
            and not flags["session_expired"]
            and int(flags["auth_cooldown"]) == 0
            and not flags["quota_available"]
        )

    def account_payload(self, account: AccountMeta) -> dict[str, object]:
        flags = self.flags(account.id)
        if account.cookie_id:
            cookie_id = account.cookie_id
        elif account.created_at:
            cookie_id = f"cookie_{account.created_at[:16]}"
        else:
            cookie_id = f"cookie_{account.id}"
        return {
            "id": account.id,
            "name": account.name,
            "email": account.email,
            "created_at": account.created_at,
            "last_used": account.last_used,
            "auth_user": account.auth_user,
            "cookie_id": cookie_id,
            "tier": account.tier,
            "tier_checked_at": account.tier_checked_at,
            "expired": bool(account.expired),
            "duplicate_of": account.duplicate_of,
            "enabled": account.enabled,
            "group": account.group,
            "session_expired": bool(flags["session_expired"]),
            "auth_cooldown": int(flags["auth_cooldown"]),
            "is_available": self.is_available(account.id),
            "quota_exhausted": self.quota_exhausted(account.id),
            "rebind_required": False,
            "rebind_status": "not_required",
            "previous_active_account_id": None,
            "active_account_id": None,
            "target_account": None,
        }

    def activate_account(self, account_id: str) -> AccountMeta | None:
        account = self.account_service.get_account(account_id)
        if account is None or not account.enabled or account.expired:
            return None
        self.account_service.set_active_account(account_id)
        record = self.manager.ready_slot_for_account(account_id)
        if record is None:
            target = self.manager._find_target_record(
                account, instance_lane_for_tier(account.tier)
            )
            if target is not None:
                target.account_id = account_id
                target.state = InstanceState.ACTIVE
                self.manager._latest_id = target.id
                self.manager._current_id = target.id
        return account

    def detect_email(self, account_id: str) -> AccountMeta | None:
        account = self.account_service.get_account(account_id)
        if account is None:
            return None
        if not account.email:
            self.account_service.update_account_email(
                account_id, f"detected.{account_id}@example.com"
            )
        self._clear_recovery_state(account_id)
        return self.account_service.get_account(account_id)

    def detect_tier(self, account_id: str) -> AccountMeta | None:
        account = self.account_service.get_account(account_id)
        if account is None:
            return None
        self.account_service.update_account_tier(
            account_id,
            normalize_tier(account.tier),
            checked_at=datetime.now(UTC).isoformat(),
        )
        self._clear_recovery_state(account_id)
        return self.account_service.get_account(account_id)

    def clear_cooldown(self, account_id: str) -> None:
        self._clear_recovery_state(account_id)
        for record in self.manager.records():
            if (
                record.account_id == account_id
                and record.state is InstanceState.COOLDOWN
            ):
                record.state = InstanceState.ACTIVE

    def _clear_recovery_state(self, account_id: str) -> None:
        """A successful recover/detect clears expired + session/cooldown locks."""
        flags = self.flags(account_id)
        flags["_cooldown_until"] = 0.0
        flags["auth_cooldown"] = 0
        flags["session_expired"] = False
        self._store.update_account_refresh(account_id, expired=False)

    def next_account(self) -> AccountMeta | None:
        for account in self.account_service.list_accounts():
            if self.is_available(account.id):
                self.account_service.set_active_account(account.id)
                return account
        return None

    def on_account_deleted(self, account_id: str) -> None:
        """Unbind any sandbox slot that referenced a deleted account."""
        self._flags.pop(account_id, None)
        for record in self.manager.records():
            if record.account_id != account_id:
                continue
            record.account_id = ""
            record.in_flight = 0
            record.state = InstanceState.UNSTARTED
        self.manager._repair_pointers()

    # ---------------------------------------------------------------- helpers
    def _seed(
        self,
        accounts: tuple[_AccountSeed, ...],
        instances: tuple[_InstanceSeed, ...],
        active_id: str | None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        for seed in accounts:
            account = self._store.save_account(
                name=seed.name,
                email=seed.email,
                storage_state=_sandbox_cookie_state(),
                account_id=seed.id,
                auth_user="0",
                cookie_id=seed.cookie_id,
                tier=normalize_tier(seed.tier),
                enabled=seed.enabled,
                group=seed.group,
            )
            self._store.update_account_refresh(
                account.id,
                expired=seed.expired,
                tier_checked_at=now,
            )
            self._flags[account.id] = {
                "session_expired": seed.session_expired,
                "auth_cooldown": seed.auth_cooldown,
                "quota_available": seed.quota_available,
                "_cooldown_until": (
                    time.time() + seed.auth_cooldown if seed.auth_cooldown else 0.0
                ),
            }
        if active_id is not None:
            self._store.set_active_account(active_id)
        self.manager.seed(
            tuple(
                seed
                for seed in instances
                if seed.account_id == "" or self._store.get_account(seed.account_id)
            )
        )

    def _action_instance_inflight(self, target_id: str | None) -> None:
        record = (
            self.manager.get_record(target_id)
            if target_id is not None
            else self.manager.first_instance()
        )
        if record is None:
            raise ValueError("no instance available for instance_inflight")
        record.in_flight += 1

    def _action_account_expired(self, target_id: str | None) -> None:
        account_id = target_id
        if account_id is None:
            account = self.account_service.get_active_account()
            account_id = account.id if account is not None else None
        if account_id is None:
            raise ValueError("no account available for account_expired")
        if not self._store.update_account_refresh(account_id, expired=True):
            account = self._store.get_account(account_id)
            if account is None:
                raise ValueError(f"unknown account: {account_id}")
        self.flags(account_id)["session_expired"] = False

    def _action_instance_cooldown(self, target_id: str | None) -> None:
        record = (
            self.manager.get_record(target_id)
            if target_id is not None
            else self.manager.first_instance()
        )
        if record is None:
            raise ValueError("no instance available for instance_cooldown")
        record.state = InstanceState.COOLDOWN
        if record.account_id:
            self.flags(record.account_id)["_cooldown_until"] = time.time() + 300
            self.flags(record.account_id)["auth_cooldown"] = 300


def _all_states_scenario() -> tuple[
    tuple[_AccountSeed, ...], tuple[_InstanceSeed, ...], str | None
]:
    accounts = (
        _AccountSeed(
            "acc_free_ready",
            "Free Ready",
            "free.ready@example.com",
            "Free",
        ),
        _AccountSeed("acc_free_nomail", "Free No Email", None, "Free"),
        _AccountSeed(
            "acc_plus_prod",
            "Plus Production",
            "plus.production.team+long-alias@example.com",
            "Plus",
            group="production",
        ),
        _AccountSeed(
            "acc_pro_prod",
            "Pro Production",
            "pro.production@example.com",
            "Pro",
            group="production",
        ),
        _AccountSeed(
            "acc_pro_expired",
            "Pro Expired",
            "pro.expired@example.com",
            "Pro",
            expired=True,
            group="legacy",
        ),
        _AccountSeed(
            "acc_pro_session",
            "Pro Session Expired",
            "pro.session@example.com",
            "Pro",
            group="legacy",
            session_expired=True,
        ),
        _AccountSeed(
            "acc_ultra_cooldown",
            "Ultra Cooldown",
            "ultra.cooldown@example.com",
            "Ultra",
            group="vip",
            auth_cooldown=120,
        ),
        _AccountSeed(
            "acc_ultra20x_disabled",
            "Ultra 20X Disabled",
            "ultra20x.disabled@example.com",
            "Ultra_20X",
            enabled=False,
            group="vip",
        ),
        _AccountSeed(
            "acc_free_quota",
            "Free Quota Exhausted",
            "free.quota@example.com",
            "Free",
            quota_available=False,
        ),
        _AccountSeed(
            "acc_free_quota_idle",
            "Free Quota Idle",
            "free.quota.idle@example.com",
            "Free",
            quota_available=False,
        ),
        _AccountSeed(
            "acc_cookie_shared_a",
            "Shared Cookie A",
            "cookie.shared.a@example.com",
            "Free",
            cookie_id="cookie_shared",
        ),
        _AccountSeed(
            "acc_cookie_shared_b",
            "Shared Cookie B",
            "cookie.shared.b@example.com",
            "Pro",
            group="production",
            cookie_id="cookie_shared",
        ),
    )
    instances = (
        _InstanceSeed(
            "inst-1",
            "acc_pro_prod",
            InstanceState.ACTIVE,
            min_tier="Pro",
            max_tier="Ultra_20X",
            in_flight=1,
        ),
        _InstanceSeed(
            "inst-2",
            "",
            InstanceState.UNSTARTED,
            only_tier="Free",
            min_tier="Free",
            max_tier="Free",
        ),
        _InstanceSeed(
            "inst-3",
            "acc_ultra_cooldown",
            InstanceState.COOLDOWN,
            only_tier="Ultra",
            min_tier="Ultra",
            max_tier="Ultra_20X",
        ),
        _InstanceSeed(
            "inst-4",
            "acc_pro_expired",
            InstanceState.INVALID,
            only_tier="Pro",
        ),
        _InstanceSeed(
            "inst-5",
            "acc_plus_prod",
            InstanceState.STARTING,
            only_tier="Plus",
        ),
        _InstanceSeed(
            "inst-6",
            "acc_free_ready",
            InstanceState.RETIRING,
            only_tier="Free",
            in_flight=2,
        ),
        _InstanceSeed(
            "inst-7",
            "acc_free_quota",
            InstanceState.SLEEPING,
            only_tier="Free",
        ),
        _InstanceSeed(
            "inst-8",
            "acc_ultra20x_disabled",
            InstanceState.RECLAIMING,
            only_tier="Ultra_20X",
        ),
        _InstanceSeed(
            "inst-9",
            "",
            InstanceState.UNSTARTED,
            only_tier="Pro",
            min_tier="Pro",
            max_tier="Pro",
        ),
    )
    return accounts, instances, "acc_pro_prod"


def _narrow_edge_scenario() -> tuple[
    tuple[_AccountSeed, ...], tuple[_InstanceSeed, ...], str | None
]:
    accounts = (
        _AccountSeed(
            "acc_edge_long",
            "Extremely Long Narrow Edge Account Name That Should Ellipsize In Tables",
            "a.very.long.email.address.for.narrow.viewports+edge-case@example.com",
            "Free",
            group="production",
        ),
        _AccountSeed(
            "acc_edge_plus",
            "Plus Edge",
            "plus.edge@example.com",
            "Plus",
            group="vip",
        ),
        _AccountSeed(
            "acc_edge_ultra20x",
            "Ultra 20X Disabled Edge",
            "ultra20x.edge@example.com",
            "Ultra_20X",
            enabled=False,
            group="legacy",
        ),
    )
    instances = (
        _InstanceSeed(
            "inst-1",
            "acc_edge_long",
            InstanceState.ACTIVE,
            only_tier="Free",
            in_flight=1,
        ),
        _InstanceSeed(
            "inst-2",
            "",
            InstanceState.UNSTARTED,
            min_tier="Pro",
        ),
    )
    return accounts, instances, "acc_edge_long"


def _mass_scenario() -> tuple[
    tuple[_AccountSeed, ...], tuple[_InstanceSeed, ...], str | None
]:
    tiers = ("Free", "Plus", "Pro", "Ultra", "Ultra_20X")
    groups = ("default", "production", "vip", "legacy")
    accounts: list[_AccountSeed] = []
    for index in range(48):
        tier = tiers[index % len(tiers)]
        accounts.append(
            _AccountSeed(
                f"acc_mass_{index:03d}",
                f"Mass Account {index:03d}",
                None if index % 7 == 0 else f"mass.{index:03d}@example.com",
                tier,
                enabled=index % 19 != 0,
                expired=index % 11 == 0,
                group=groups[index % len(groups)],
                session_expired=index % 13 == 0,
                auth_cooldown=90 if index % 17 == 0 else 0,
                quota_available=index % 23 != 0,
            )
        )
    states = (
        InstanceState.ACTIVE,
        InstanceState.UNSTARTED,
        InstanceState.COOLDOWN,
        InstanceState.INVALID,
        InstanceState.STARTING,
        InstanceState.RETIRING,
    )
    active_seed = next(
        account for account in accounts if account.enabled and not account.expired
    )
    instance_accounts = [
        active_seed,
        *[account for account in accounts if account is not active_seed][:5],
    ]
    instances = tuple(
        _InstanceSeed(
            f"inst-{index + 1}",
            instance_accounts[index].id,
            states[index],
            only_tier=instance_accounts[index].tier,
            in_flight=index % 4,
        )
        for index in range(6)
    )
    return tuple(accounts), instances, active_seed.id


def create_ui_sandbox() -> UISandbox:
    return UISandbox()


__all__ = [
    "ACTIONS",
    "SCENARIOS",
    "SandboxAccountRefresher",
    "SandboxInstanceManager",
    "SandboxKeepalive",
    "SandboxRotator",
    "UISandbox",
    "UiDebugIsolation",
    "create_ui_sandbox",
    "sandbox_model_by_id",
    "sandbox_model_catalog",
]
