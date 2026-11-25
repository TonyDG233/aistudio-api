"""账号管理路由。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path as FastApiPath,
    Query,
)
from pydantic import BaseModel, Field

from aistudio_api.api.dependencies import (
    get_account_service,
    get_runtime_state,
)
from aistudio_api.api.ui_sandbox import UISandbox
from aistudio_api.application.instance_manager import (
    InstanceError,
    InstanceRebindError,
    InstanceRebindTargetError,
)
from aistudio_api.application.verify_account_service import (
    VerifyAccountController,
    VerifyAccountNotFound,
    VerifyBrowserUnavailable,
    VerifySessionConflict,
    VerifySessionNotFound,
    get_verify_account_service,
    verify_capability,
)
from aistudio_api.infrastructure.account.account_info_refresher import (
    AccountInfoRefresher,
    RefreshInProgressError,
    SlotProvider,
    find_account_session,
    probe_tier_over_session,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.account.benefit_tier import (
    parse_benefit_tier_envelope,
)
from aistudio_api.infrastructure.account.cookie_parser import (
    parse_cookie_string,
    probe_account_benefit_tier,
    probe_account_email,
)
from aistudio_api.infrastructure.account.cookie_refresher import UNSET_PROXY
from aistudio_api.infrastructure.exit_pool import (
    get_exit_resolver,
    resolve_credential_exit,
)
from aistudio_api.infrastructure.exit_pool.resolver import (
    lane_for_tier,
    probe_egress_allowed,
    probe_skip_reason,
)
from aistudio_api.infrastructure.utils.common import mask_email

if TYPE_CHECKING:
    from aistudio_api.api.state import RuntimeState
    from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("accounts")

router = APIRouter(prefix="/accounts")

# 账号信息刷新器单例（进程内互斥标志由刷新器自身持有）。
_refresher_singleton: AccountInfoRefresher | None = None

# External exit mode: tiny gap between per-account import probes so a batch does
# not burst through the same exit.
IMPORT_PROBE_PACING_SECONDS = 0.3


def _external_exits_active() -> bool:
    """True when the external exit pool is enabled (resolver constructed)."""
    return get_exit_resolver() is not None


def get_account_info_refresher(
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountInfoRefresher:
    """优先复用 lifespan 创建的共享刷新器，测试可依赖覆盖。"""
    global _refresher_singleton
    shared = runtime_state.account_refresher
    if shared is not None and callable(getattr(shared, "refresh_accounts", None)):
        refresher = cast(AccountInfoRefresher, shared)
    else:
        if _refresher_singleton is None:
            _refresher_singleton = AccountInfoRefresher(account_service.store)
        refresher = _refresher_singleton
    provider = getattr(
        getattr(runtime_state, "instance_manager", None), "pool_slots", None
    )
    if callable(provider):
        refresher.set_slot_provider(cast("SlotProvider", provider))
    return refresher


def get_ui_sandbox(runtime_state: RuntimeState) -> UISandbox | None:
    """Return the active offline sandbox, or None outside ui_debug mode."""
    sandbox = getattr(runtime_state, "ui_sandbox", None)
    if getattr(runtime_state, "ui_debug", False) and isinstance(sandbox, UISandbox):
        return sandbox
    return None


def get_verify_account_controller(
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> VerifyAccountController:
    """Resolve the active verify-account controller (sandbox fake or real)."""
    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        return sandbox.verify_service
    service = get_verify_account_service()
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "验证服务未初始化",
                "hint": "服务尚未完成启动或已进入关闭流程，请稍后重试",
            },
        )
    return service


def _optional_verify_controller(
    runtime_state: RuntimeState,
) -> VerifyAccountController | None:
    """Resolve the verify controller without raising when it is uninitialized."""
    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        return sandbox.verify_service
    return get_verify_account_service()


async def _cancel_verify_for_deleted_account(
    runtime_state: RuntimeState, account_id: str
) -> None:
    controller = _optional_verify_controller(runtime_state)
    if controller is None:
        return
    try:
        await controller.cancel(account_id)
    except Exception as exc:
        log.warning("删除账号 %s 时取消验证会话失败: %s", account_id, exc)


def _reconcile_verify_after_import(rotator: object, account_id: str) -> None:
    """Sync a re-import's store-level verify clear into the rotator memory flag."""
    reconcile = getattr(rotator, "reconcile_verify_cleared", None)
    if callable(reconcile):
        reconcile(account_id)


def _cookie_dict_from_storage(storage_state: dict[str, object]) -> dict[str, str]:
    """Flatten a storage-state cookie list into a name->value map."""
    raw = storage_state.get("cookies")
    if not isinstance(raw, list):
        return {}
    return {
        str(cookie.get("name") or ""): str(cookie.get("value") or "")
        for cookie in raw
        if isinstance(cookie, dict) and cookie.get("name")
    }


async def _post_save_identity_probe(
    account_service: AccountService,
    account: AccountMeta,
    storage_state: dict[str, object],
    *,
    auth_user: str,
) -> AccountMeta:
    """Probe email/tier with the *real* account id after the account is saved.

    The exit manager binds exits to the account id, so probing before the id
    exists would create an orphan binding (P2-1).  This helper is used only in
    external mode after ``save_account``: it resolves the account's own exit and
    writes back any discovered email/tier.  A missing exit or a failed probe
    simply leaves the imported credentials intact but records the reason on the
    account (``last_refresh_error``) so the operator/keepalive can retry.
    """
    if account.email and account.tier and account.tier != "Free":
        return account
    cookie_dict = _cookie_dict_from_storage(storage_state)
    if not cookie_dict:
        return account
    decision = await resolve_credential_exit(
        account.id,
        kind="chat",
        lane=lane_for_tier(account.tier),
        category="maintenance",
    )
    if decision.deferred:
        log.warning(
            "账号 %s 导入后探测延迟：出口调用预算不足 retry_after=%.0fs（账号已保存）",
            account.id,
            decision.retry_after_s,
        )
        _mark_probe_reason(
            account_service,
            account.id,
            f"deferred_no_capacity: retry_after_s={decision.retry_after_s:.0f}",
        )
        return account
    if not probe_egress_allowed(decision):
        reason = probe_skip_reason(decision)
        log.warning(
            "账号 %s 导入后探测跳过：无可用出口 reason=%s（账号已保存）",
            account.id,
            reason,
        )
        _mark_probe_reason(account_service, account.id, f"skipped_no_exit: {reason}")
        return account
    problems: list[str] = []
    if not account.email:
        try:
            probed_email = await probe_account_email(
                cookie_dict,
                auth_user=auth_user,
                account_id=account.id,
                proxy=decision.proxy,
            )
        except Exception as exc:
            problems.append(f"email probe failed: {exc}")
        else:
            if probed_email:
                account_service.store.update_account_email(account.id, probed_email)
            else:
                problems.append("email probe returned no result")
    if not account.tier or account.tier == "Free":
        try:
            probed_tier = await probe_account_benefit_tier(
                cookie_dict,
                auth_user=auth_user,
                account_id=account.id,
                proxy=decision.proxy,
            )
        except Exception as exc:
            problems.append(f"tier probe failed: {exc}")
        else:
            if probed_tier:
                account_service.store.update_account_tier(
                    account.id,
                    probed_tier,
                    checked_at=datetime.now(UTC).isoformat(),
                )
            else:
                problems.append("tier probe returned no result")
    if problems:
        log.warning(
            "账号 %s 导入后探测未完成: %s（账号已保存）",
            account.id,
            "; ".join(problems),
        )
        _mark_probe_reason(account_service, account.id, "; ".join(problems))
    return account_service.get_account(account.id) or account


def _mark_probe_reason(
    account_service: AccountService, account_id: str, reason: str
) -> None:
    """Persist a probe skip/failure reason without touching tier freshness."""
    marker = getattr(account_service.store, "mark_refresh_attempt", None)
    if not callable(marker):
        return
    with contextlib.suppress(Exception):
        marker(account_id, error=reason)


# Deterministic import-anchor prefix.  External probe-import saves one stable
# placeholder per cookie jar so that re-importing the same jar is idempotent
# (P2-A) instead of stacking ``email=None`` duplicates.
IMPORT_ANCHOR_PREFIX = "acc_import_"

# Stable session-identity cookies, most specific first.  Rolling tokens such as
# ``__Secure-1PSIDTS``/``SIDCC`` rotate and are deliberately excluded: hashing
# them would change the anchor id on every import and defeat idempotency.
_ANCHOR_IDENTITY_COOKIES: tuple[str, ...] = ("SAPISID", "APISID", "SID")


def _stable_import_anchor_id(storage_state: dict[str, object]) -> str | None:
    """Derive a deterministic placeholder id from stable identity cookies.

    Uses ``provider + stable cookie value`` so re-importing the same cookie jar
    lands on the same record.  Never hashes ``1PSIDTS``/``SIDCC`` or the whole
    jar, whose values change per import.
    """
    cookie_dict = _cookie_dict_from_storage(storage_state)
    for name in _ANCHOR_IDENTITY_COOKIES:
        value = cookie_dict.get(name)
        if value:
            digest = hashlib.sha256(f"google:{value}".encode()).hexdigest()[:12]
            return f"{IMPORT_ANCHOR_PREFIX}{digest}"
    # No identity cookie: fall back to cookie name/domain names (not values) so
    # a malformed jar still resolves to one placeholder.  Such a jar cannot be
    # probed as a real account anyway.
    raw = storage_state.get("cookies")
    if not isinstance(raw, list):
        return None
    fingerprint = sorted(
        f"{cookie.get('name')}@{cookie.get('domain')}"
        for cookie in raw
        if isinstance(cookie, dict) and cookie.get("name")
    )
    if not fingerprint:
        return None
    digest = hashlib.sha256("|".join(fingerprint).encode()).hexdigest()[:12]
    return f"{IMPORT_ANCHOR_PREFIX}{digest}"


def _find_identity_account(
    account_service: AccountService,
    *,
    email: str | None,
    auth_user: str,
    exclude_id: str | None = None,
) -> AccountMeta | None:
    """Find an existing account by ``email + auth_user`` (excluding the anchor)."""
    if not email:
        return None
    for account in account_service.list_accounts():
        if exclude_id is not None and account.id == exclude_id:
            continue
        if account.email == email and (account.auth_user or "0") == auth_user:
            return account
    return None


def _browserless_engine(runtime_state: RuntimeState) -> bool:
    """True when the active engine has no browser control page.

    Browserless mode must never touch the control-plane BrowserSession: cookies
    are already persisted by AccountService, so import/delete/toggle paths only
    update registry/rotator/pool state.
    """
    settings_block = getattr(runtime_state, "engine_settings", None)
    if (
        isinstance(settings_block, dict)
        and str(settings_block.get("mode") or "") == "browserless"
    ):
        return True
    client = getattr(runtime_state, "client", None)
    session = getattr(client, "_session", None) if client is not None else None
    return str(getattr(session, "engine", "browser")) == "browserless"


def _account_status(
    runtime_state: RuntimeState, account_id: str
) -> dict[str, object] | None:
    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        flags = sandbox.flags(account_id)
        return {
            "session_expired": bool(flags["session_expired"]),
            "verify_required": bool(flags["verify_required"]),
            "auth_cooldown": int(flags["auth_cooldown"]),
            "is_available": sandbox.is_available(account_id),
            "quota_exhausted": sandbox.quota_exhausted(account_id),
        }
    rotator = getattr(runtime_state, "rotator", None)
    service = getattr(runtime_state, "account_service", None)
    if rotator is None or service is None:
        return None
    account = service.get_account(account_id)
    stats = rotator.get_account_stats(account_id)
    if account is None or stats is None:
        return None
    now = datetime.now(UTC).timestamp()
    cooldown = (
        max(0, int(stats.auth_cooldown - now)) if stats.auth_cooldown > now else 0
    )
    session_expired = bool(stats.session_expired)
    verify_required = bool(
        stats.verify_required or getattr(account, "verify_required", False)
    )
    is_available = bool(
        account.enabled
        and not account.expired
        and not session_expired
        and not verify_required
        and cooldown == 0
    )
    return {
        "session_expired": session_expired,
        "verify_required": verify_required,
        "auth_cooldown": cooldown,
        "is_available": is_available,
        # Real quota is a soft limit; only sandbox models an exhausted quota state.
        "quota_exhausted": False,
    }


class AccountResponse(BaseModel):
    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None
    auth_user: str = "0"
    cookie_id: str | None = None
    tier: str = "Free"
    tier_checked_at: str | None = None
    expired: bool = False
    duplicate_of: str | None = None
    enabled: bool = True
    group: str = "default"
    rebind_required: bool = False
    rebind_status: str = "not_required"
    previous_active_account_id: str | None = None
    active_account_id: str | None = None
    target_account: AccountResponse | None = None
    session_expired: bool = False
    verify_required: bool = False
    auth_cooldown: int = 0
    is_available: bool = True
    quota_exhausted: bool = False


async def _run_control_rebind(
    runtime_state: RuntimeState,
    *,
    previous_account_id: str,
    target_account_id: str | None,
    reason: str,
) -> tuple[bool, str]:
    """D-05：把 registry rebind 信号接到实例控制面。

    返回 ``(rebind_required, rebind_status)``。实例池未接线时显式返回
    ``unsupported``，不得静默假装已切换；切换失败必须抛错并由调用方回滚
    enabled 状态，active 只在浏览器切换成功后改写。
    """
    if target_account_id is None:
        raise InstanceRebindTargetError(
            "没有可切换的 enabled 账号，账号已停用但 rebind 目标为空"
        )
    manager = runtime_state.instance_manager
    if manager is None:
        log.warning(
            "实例池未初始化，D-05 rebind 未执行（显式 fallback）: %s -> %s",
            previous_account_id,
            target_account_id,
        )
        return True, "unsupported"
    try:
        await manager.rebind_account(  # type: ignore[attr-defined]
            target_account_id,
            previous_account_id=previous_account_id,
            reason=reason,
        )
    except Exception as exc:
        log.error(
            "D-05 rebind 失败 %s -> %s: %s",
            previous_account_id,
            target_account_id,
            exc,
        )
        raise InstanceRebindError(f"账号 rebind 失败: {exc}") from exc
    return False, "applied"


def _clear_account_failure_state(runtime_state: RuntimeState, account_id: str) -> None:
    """Clear persisted expiry and rotator session/cooldown locks after success."""
    service = runtime_state.account_service
    store = getattr(service, "store", None)
    if store is not None:
        store.update_account_refresh(account_id, expired=False)
    rotator = runtime_state.rotator
    clear = getattr(rotator, "clear_cooldown", None)
    if callable(clear):
        clear(account_id)


def to_account_response(
    account: AccountMeta,
    *,
    status: dict[str, object] | None = None,
) -> AccountResponse:
    cid = getattr(account, "cookie_id", None) or (
        f"cookie_{account.created_at[:16]}"
        if getattr(account, "created_at", None)
        else f"cookie_{account.id}"
    )
    expired = bool(getattr(account, "expired", False))
    enabled = getattr(account, "enabled", True)
    if status is None:
        session_expired = False
        verify_required = bool(getattr(account, "verify_required", False))
        # No rotator status yet (early startup): a verify lock still makes the
        # account unavailable, otherwise the response contradicts itself.
        is_available = bool(enabled and not expired and not verify_required)
        auth_cooldown = 0
        quota_exhausted = False
    else:
        is_available = bool(status.get("is_available", True))
        session_expired = bool(status.get("session_expired", False))
        verify_required = bool(status.get("verify_required", False))
        auth_cooldown = int(str(status.get("auth_cooldown") or 0))
        quota_exhausted = bool(status.get("quota_exhausted", False))
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at or "",
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0") or "0",
        cookie_id=cid,
        tier=getattr(account, "tier", "Free") or "Free",
        tier_checked_at=getattr(account, "tier_checked_at", None),
        expired=expired,
        duplicate_of=getattr(account, "duplicate_of", None),
        enabled=enabled,
        group=getattr(account, "group", "default") or "default",
        session_expired=session_expired,
        verify_required=verify_required,
        auth_cooldown=auth_cooldown,
        is_available=is_available,
        quota_exhausted=quota_exhausted,
    )


class UpdateAccountRequest(BaseModel):
    name: str


class UpdateGroupRequest(BaseModel):
    group: str


class ToggleAccountRequest(BaseModel):
    enabled: bool | None = None


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class BatchDeleteResponse(BaseModel):
    deleted: int


class BatchToggleRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    enabled: bool


class BatchToggleResult(BaseModel):
    id: str
    ok: bool
    error: str | None = None


class BatchToggleResponse(BaseModel):
    results: list[BatchToggleResult]
    toggled_count: int
    error_count: int


class VerifySessionResponse(BaseModel):
    session_id: str
    state: str
    qr_url: str | None = None
    short_code: str | None = None
    expires_at: str
    message: str | None = None


class VerifyCancelResponse(BaseModel):
    state: str


class VerifyCheckResponse(BaseModel):
    state: str
    message: str
    upstream_status: int | None = None
    verify_required: bool = False


class CredentialExportResponse(BaseModel):
    version: int = 1
    account_id: str
    email: str | None = None
    exported_at: str
    cookies: list[dict[str, Any]]


class RefreshAccountsRequest(BaseModel):
    account_ids: list[str] | None = None
    all: bool = False
    refresh_email: bool = True


class RefreshAccountResult(BaseModel):
    id: str
    ok: bool
    tier: str | None = None
    expired: bool | None = None
    email: str | None = None
    error: str | None = None
    checked_at: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    # 429 no_capacity: retry this account after retry_after_s; not a failure.
    deferred: bool = False
    retry_after_s: float = 0.0


class RefreshAccountsResponse(BaseModel):
    results: list[RefreshAccountResult]
    refreshed_count: int
    expired_count: int


class PresetResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    action: str = "snapshot"
    account_ids: list[str] = []
    target_tiers: list[str] = []
    target_groups: list[str] = []
    is_system: bool = False
    created_at: str = ""
    updated_at: str = ""


class CreatePresetRequest(BaseModel):
    name: str
    description: str = ""
    action: str = "snapshot"
    account_ids: list[str] = []
    target_tiers: list[str] = []
    target_groups: list[str] = []


class SnapshotPresetRequest(BaseModel):
    name: str
    description: str = ""


class ApplyPresetResponse(BaseModel):
    ok: bool = True
    preset_id: str
    preset_name: str
    enabled_count: int
    disabled_count: int
    enabled_account_ids: list[str]
    disabled_account_ids: list[str]
    rebind_required: bool = False
    rebind_status: str = "not_required"
    previous_active_account_id: str | None = None
    active_account_id: str | None = None
    target_account: AccountResponse | None = None


class ImportCookiesRequest(BaseModel):
    cookies: str
    name: str | None = None
    email: str | None = None
    account_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_\-\.@]+$")
    auth_user: str = "0"
    tier: str | None = None


class ImportCookiesResponse(BaseModel):
    account_id: str
    name: str
    cookie_count: int
    domain_summary: dict[str, int]
    auth_user: str = "0"
    tier: str = "Free"


_TAMPERMONKEY_TIER_MAP = {
    "TIER0": "Free",
    "TIER1": "Pro",
    "TIER2": "Ultra",
}


def _apply_tampermonkey_import_metadata(req: ImportCookiesRequest) -> None:
    """Detect authUser / g1Tier from a Tampermonkey grabber JSON export.

    Only fills values the caller left at their defaults: an explicit auth_user
    other than ``"0"`` and an explicit tier are never overwritten. Malformed
    JSON is ignored so the normal cookie parsers still get a chance to handle
    the payload.
    """
    raw_text = req.cookies.strip()
    if not (raw_text.startswith("{") and raw_text.endswith("}")):
        return
    with contextlib.suppress(Exception):
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            return
        raw_auth_user = parsed.get("authUser", parsed.get("auth_user"))
        if (
            raw_auth_user is not None
            and (not req.auth_user or req.auth_user == "0")
            and str(raw_auth_user).strip()
        ):
            req.auth_user = str(raw_auth_user).strip()
        if not req.tier:
            raw_tier = parsed.get("g1Tier", parsed.get("g1_tier"))
            if isinstance(raw_tier, str):
                req.tier = _TAMPERMONKEY_TIER_MAP.get(raw_tier.strip().upper())


class ProbeAndImportRequest(BaseModel):
    cookies: str
    name_prefix: str | None = None


class ProbeAndImportResponse(BaseModel):
    imported_count: int
    accounts: list[AccountResponse]
    # External exit mode may import accounts whose identity probe was skipped
    # (no exit yet) or failed; they are kept and their reason is persisted on
    # the account.  These fields surface that outcome without failing the batch.
    skipped: list[str] = Field(default_factory=list)
    skip_reason: str | None = None
    probe_failed: dict[str, str] = Field(default_factory=dict)


class BundleAccountItem(BaseModel):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    cookies: object = None
    cookieObjects: list[dict[str, object]] | None = None
    auth_user: str | None = None
    authUser: int | str | None = None
    tier: str | None = None
    g1Tier: str | None = None
    g1_tier: str | None = None
    accountName: str | None = None


class RotateCookiesResponse(BaseModel):
    ok: bool
    status: int = 200
    updated: list[str] = Field(default_factory=list)
    account: AccountResponse | None = None


class ImportBundleRequest(BaseModel):
    file_path: str | None = None
    content: str | None = None
    accounts: list[BundleAccountItem] | None = None


class ImportBundleResponse(BaseModel):
    imported_count: int
    accounts: list[AccountResponse]


@router.get("", response_model=list[AccountResponse])
@router.get("/", response_model=list[AccountResponse])
async def list_accounts(
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> list[AccountResponse]:
    """列出所有账号。"""
    accounts = account_service.list_accounts()
    return [
        to_account_response(a, status=_account_status(runtime_state, a.id))
        for a in accounts
    ]


@router.get("/active", response_model=AccountResponse)
async def get_active_account(
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """获取当前活跃账号。"""
    account = account_service.get_active_account()
    if account is None:
        raise HTTPException(status_code=404, detail="没有活跃账号")
    return to_account_response(
        account, status=_account_status(runtime_state, account.id)
    )


@router.post("/refresh", response_model=RefreshAccountsResponse)
async def refresh_accounts(
    req: RefreshAccountsRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
    refresher: AccountInfoRefresher = Depends(get_account_info_refresher),
) -> RefreshAccountsResponse:
    """刷新账号信息（tier/过期/邮箱）。"""
    requested = [str(a) for a in (req.account_ids or []) if str(a).strip()]
    if not req.all and not requested:
        raise HTTPException(
            status_code=400,
            detail="必须提供 account_ids 或设置 all=true",
        )

    if req.all:
        targets = account_service.list_accounts()
        ordered_ids = [account.id for account in targets]
    else:
        ordered_ids = requested
        targets = []
        for account_id in ordered_ids:
            account = account_service.get_account(account_id)
            if account is not None:
                targets.append(account)

    try:
        refreshed = await refresher.refresh_accounts(
            targets, refresh_email=req.refresh_email
        )
    except RefreshInProgressError as e:
        raise HTTPException(
            status_code=409,
            detail=f"账号信息刷新正在进行中，请稍后再试: {e}",
        ) from e

    by_id = {result.account_id: result for result in refreshed}
    results: list[RefreshAccountResult] = []
    for account_id in ordered_ids:
        result = by_id.get(account_id)
        if result is None:
            results.append(
                RefreshAccountResult(id=account_id, ok=False, error="账号不存在")
            )
        else:
            results.append(
                RefreshAccountResult(
                    id=result.account_id,
                    ok=result.ok,
                    tier=result.tier,
                    expired=result.expired,
                    email=result.email,
                    error=result.error,
                    checked_at=result.checked_at,
                    skipped=result.skipped,
                    skip_reason=result.skip_reason,
                    deferred=getattr(result, "deferred", False),
                    retry_after_s=float(getattr(result, "retry_after_s", 0.0) or 0.0),
                )
            )

    expired_count = sum(1 for r in results if r.expired is True)
    log.info("账号信息刷新完成: 目标 %d 个, 过期 %d 个", len(results), expired_count)
    return RefreshAccountsResponse(
        results=results,
        refreshed_count=len(results),
        expired_count=expired_count,
    )


@router.post("/{account_id}/activate", response_model=AccountResponse)
async def activate_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """切换到指定账号。"""
    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        account = sandbox.activate_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在或切换失败")
        log.info("UI 调试沙箱激活账号: id=%s, name=%s", account.id, account.name)
        return to_account_response(
            account, status=_account_status(runtime_state, account.id)
        )

    manager = runtime_state.instance_manager
    ready_slot = None
    ready_lookup = getattr(manager, "ready_slot_for_account", None)
    if callable(ready_lookup):
        with contextlib.suppress(Exception):
            ready_slot = ready_lookup(account_id)

    if ready_slot is not None:
        # Same-account guard: a READY pool slot already owns this account's
        # single page.  Only sync registry/rotator/pool-current state; never
        # switch or open the control-plane page (no BotGuard re-probe).
        account = account_service.get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在或切换失败")
        account_service.set_active_account(account_id)
        if runtime_state.rotator:
            runtime_state.rotator.add_account(account.id)
            runtime_state.rotator.clear_cooldown(account.id)
        log.info(
            "手动激活账号（复用已就绪实例页，未切控制面）: %s (%s)",
            account.id,
            account.name,
        )
        return to_account_response(account)

    browser_session = runtime_state.client._session if runtime_state.client else None

    if browser_session is None:
        raise HTTPException(status_code=503, detail="服务未就绪")

    account = await account_service.activate_account(account_id, browser_session)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在或切换失败")
    if runtime_state.rotator:
        runtime_state.rotator.add_account(account.id)
        runtime_state.rotator.clear_cooldown(account.id)
    log.info("激活账号: id=%s, name=%s", account.id, account.name)
    return to_account_response(account)


@router.post("/{account_id}/detect-email", response_model=AccountResponse)
async def detect_account_email(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """自动获取并更新指定账号的真实邮箱与订阅等级。"""
    import json

    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        account = sandbox.detect_email(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        return to_account_response(
            account, status=_account_status(runtime_state, account.id)
        )

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    target_auth = account_service.get_account_auth_path(account_id)
    browser_session = runtime_state.client._session if runtime_state.client else None
    email: str | None = None
    detected_tier: str | None = None

    # 1. 若受控浏览器当前正好处于该账号上下文，优先直接从当前页面提取（免切号）
    is_current_session = bool(
        browser_session is not None
        and browser_session.page is not None
        and (
            not target_auth
            or not getattr(browser_session, "_auth_file", None)
            or str(target_auth) == browser_session._auth_file
        )
    )
    if is_current_session and browser_session is not None:
        with contextlib.suppress(Exception):
            email = await browser_session.extract_account_email()
        if hasattr(browser_session, "extract_account_tier"):
            with contextlib.suppress(Exception):
                detected_tier = await browser_session.extract_account_tier()

    # 2. 通过 auth.json 凭据执行高速 HTTP 探测（无需切号，即使浏览器休眠也可用）
    if (
        (not email or not detected_tier or detected_tier == "Free")
        and target_auth
        and target_auth.is_file()
    ):
        decision = await resolve_credential_exit(
            account_id,
            kind="chat",
            lane=lane_for_tier(acc.tier),
            category="maintenance",
        )
        if decision.deferred:
            raise HTTPException(
                status_code=429,
                detail={
                    "deferred": True,
                    "reason": decision.reason or "no_capacity",
                    "retry_after_s": decision.retry_after_s,
                    "message": "出口调用预算不足，本轮探测已延迟，账号信息保持不变",
                },
                headers={
                    "Retry-After": str(max(1, int(decision.retry_after_s + 0.999)))
                },
            )
        try:
            auth_data = json.loads(target_auth.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict and not probe_egress_allowed(decision):
                log.warning(
                    "账号 %s 邮箱/等级 HTTP 探测跳过：无可用出口 reason=%s",
                    account_id,
                    probe_skip_reason(decision),
                )
            if cookie_dict and probe_egress_allowed(decision):
                if not email:
                    email = await probe_account_email(
                        cookie_dict,
                        auth_user=acc.auth_user or "0",
                        account_id=account_id,
                        proxy=decision.proxy,
                    )
                if not detected_tier or detected_tier == "Free":
                    http_tier = await probe_account_benefit_tier(
                        cookie_dict,
                        auth_user=acc.auth_user or "0",
                        account_id=account_id,
                        proxy=decision.proxy,
                    )
                    if (http_tier and http_tier != "Free") or not detected_tier:
                        detected_tier = http_tier
        except Exception as e:
            log.debug("通过 HTTP 探测账号 %s 邮箱/等级失败: %s", account_id, e)

    # 3. 若 HTTP 仍未取到邮箱且受控浏览器可用，回退切换浏览器会话提取
    if not email and browser_session is not None and browser_session.page is not None:
        if target_auth and str(target_auth) != browser_session._auth_file:
            await browser_session.switch_auth(str(target_auth))
        email = await browser_session.extract_account_email()
        if (not detected_tier or detected_tier == "Free") and hasattr(
            browser_session, "extract_account_tier"
        ):
            with contextlib.suppress(Exception):
                detected_tier = await browser_session.extract_account_tier()

    if email:
        account_service.update_account_email(account_id, email)
        log.info("已更新账号邮箱: id=%s, email=%s", account_id, mask_email(email))

    if detected_tier:
        account_service.update_account_tier(
            account_id,
            detected_tier,
            checked_at=datetime.now(UTC).isoformat(),
        )
        log.info("已更新账号订阅等级: id=%s, tier=%s", account_id, detected_tier)

    if email or detected_tier:
        _clear_account_failure_state(runtime_state, account_id)

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return to_account_response(acc)


@router.post("/{account_id}/detect-tier", response_model=AccountResponse)
async def detect_account_tier(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """强制探测并更新指定账号的订阅权益等级 (Free / Pro / Ultra / Plus)。

    这是人工/支持侧动作，必须绕过启动期“3 天内已验证 Free 则跳过”的缓存策略。
    只有上游明确给出可解析等级时才写库；探测失败返回 400（缺少 Cookie）、
    503（该账号无可用出口，fail-closed，与生成侧 ``exit_unavailable`` 一致）
    或 502（网络/代理/上游异常、响应不可解析），绝不把已有 Pro/Ultra 降级为 Free。
    """
    import inspect
    import json

    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        account = sandbox.detect_tier(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        return to_account_response(
            account, status=_account_status(runtime_state, account.id)
        )

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    auth_user = str(acc.auth_user or "0")
    target_auth = account_service.get_account_auth_path(account_id)
    detected_tier: str | None = None
    cookie_dict: dict[str, str] = {}

    # 1. 优先使用该账号已 READY 的池化页面（最新 Cookie + 页面内 XHR 探测）。
    instance_manager = getattr(runtime_state, "instance_manager", None)
    slot: object | None = None
    ready_getter = getattr(instance_manager, "ready_slot_for_account", None)
    if callable(ready_getter):
        with contextlib.suppress(Exception):
            slot = ready_getter(account_id)
    session = getattr(slot, "session", None) if slot is not None else None
    if session is None and instance_manager is not None:
        provider = getattr(instance_manager, "pool_slots", None)
        if callable(provider):
            with contextlib.suppress(Exception):
                session = find_account_session(
                    cast("Sequence[object]", provider()), account_id
                )
    page = getattr(session, "page", None) if session is not None else None
    session_extractor = getattr(session, "extract_account_tier", None)
    if callable(session_extractor):
        extractor = cast(Callable[..., Awaitable[object]], session_extractor)
        with contextlib.suppress(Exception):
            extracted = await extractor()
            if isinstance(extracted, str):
                detected_tier = extracted
        if detected_tier is None:
            with contextlib.suppress(Exception):
                extracted = await extractor(auth_user=auth_user)
                if isinstance(extracted, str):
                    detected_tier = extracted

    browser_session = runtime_state.client._session if runtime_state.client else None
    if (
        page is None
        and browser_session is not None
        and browser_session.page is not None
    ):
        current_auth = getattr(browser_session, "_auth_file", None)
        if not target_auth or not current_auth or str(target_auth) == str(current_auth):
            page = browser_session.page

    if page is not None:
        from aistudio_api.infrastructure.gateway.session_auth import (
            extract_account_tier,
        )

        if detected_tier is None:
            with contextlib.suppress(Exception):
                detected_tier = await extract_account_tier(page, auth_user=auth_user)
        with contextlib.suppress(Exception):
            raw_page_cookies = await page.get_cookies()
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_page_cookies
                if isinstance(c, dict) and c.get("name")
            }

    # 2. 页面不可用或页面探测失败时，用 auth.json 的凭据走直连 HTTP 探测。
    if not cookie_dict and target_auth and target_auth.is_file():
        with contextlib.suppress(Exception):
            auth_data = json.loads(target_auth.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies
                if isinstance(c, dict) and c.get("name")
            }

    probe_error: str | None = None
    engine_attempted = False
    if detected_tier is None and session is not None:
        session_response = await probe_tier_over_session(
            session, cookies=cookie_dict, auth_user=auth_user
        )
        if session_response is not None:
            engine_attempted = True
            envelope = parse_benefit_tier_envelope(session_response.body)
            if session_response.status == 200:
                detected_tier = envelope.strict_tier()
            if detected_tier is None:
                probe_error = f"HTTP {session_response.status}" + (
                    f": {envelope.message}" if envelope.message else ""
                )
                log.warning(
                    "账号 %s 引擎通道 tier 探测失败: %s", account_id, probe_error
                )

    if detected_tier is None and cookie_dict and not engine_attempted:
        decision = await resolve_credential_exit(
            account_id,
            kind="chat",
            lane=lane_for_tier(acc.tier),
            category="maintenance",
        )
        if decision.deferred:
            raise HTTPException(
                status_code=429,
                detail={
                    "deferred": True,
                    "reason": decision.reason or "no_capacity",
                    "retry_after_s": decision.retry_after_s,
                    "message": "出口调用预算不足，本轮探测已延迟，账号原等级保持不变",
                },
                headers={
                    "Retry-After": str(max(1, int(decision.retry_after_s + 0.999)))
                },
            )
        if not probe_egress_allowed(decision):
            raise HTTPException(
                status_code=503,
                detail=(
                    "订阅等级探测跳过：该账号当前无可用出口"
                    f"（{probe_skip_reason(decision)}），已按 fail-closed 处理；"
                    "账号原等级保持不变"
                ),
            )
        log.warning(
            "账号 %s 无可用引擎会话，tier 探测回退裸 httpx"
            "（可能被上游判 region not supported）",
            account_id,
        )
        detected_tier = await probe_account_benefit_tier(
            cookie_dict,
            auth_user=auth_user,
            account_id=account_id,
            proxy=decision.proxy,
        )

    if detected_tier is None:
        if not cookie_dict:
            raise HTTPException(
                status_code=400,
                detail="缺少可探测该账号的 Cookie，请先导入或刷新 cookies",
            )
        detail = (
            "订阅等级探测失败：网络/代理异常、上游非 200 或响应不可解析；"
            "账号原等级保持不变"
        )
        if probe_error:
            detail = f"{detail} ({probe_error})"
        raise HTTPException(status_code=502, detail=detail)

    account_service.update_account_tier(
        account_id,
        detected_tier,
        checked_at=datetime.now(UTC).isoformat(),
    )
    log.info("已更新账号订阅等级: id=%s, tier=%s", account_id, detected_tier)
    _clear_account_failure_state(runtime_state, account_id)

    # 3. 同步已存在实例/池记录中的 tier，避免视图继续展示旧等级。
    sync_tier = getattr(instance_manager, "sync_account_tier", None)
    if callable(sync_tier):
        try:
            result = sync_tier(account_id, detected_tier)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            log.warning(
                "账号 %s tier 已更新为 %s，但实例池同步失败: %s",
                account_id,
                detected_tier,
                exc,
            )

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return to_account_response(acc)


@router.post("/{account_id}/verify/start", response_model=VerifySessionResponse)
async def start_account_verification(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    controller: VerifyAccountController = Depends(get_verify_account_controller),
) -> VerifySessionResponse:
    """启动单账号扫码验证会话（全局同一时间只允许一个）。"""
    # The ui-debug sandbox fakes the whole browser flow, so the host capability
    # gate must not apply there (CI/UI review runs on Linux).
    if get_ui_sandbox(runtime_state) is None:
        supported, _reason = verify_capability()
        if not supported:
            raise HTTPException(
                status_code=501,
                detail=(
                    "服务所在环境无法完成扫码验证（没有可用的桌面浏览器）。"
                    "请在电脑上运行本服务扫码；或在账号菜单导出扫码凭证，"
                    "在电脑端用 verify-helper 扫码后回来点「直接验证账号」复查。"
                ),
            )
    if account_service.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        payload = await controller.start(account_id)
    except VerifyAccountNotFound as exc:
        raise HTTPException(status_code=404, detail="账号不存在") from exc
    except VerifySessionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "已有其它账号的验证会话正在进行，请先取消或等待结束",
                "account_id": exc.account_id,
            },
        ) from exc
    except VerifyBrowserUnavailable as exc:
        hint = (
            "可按三种布局之一提供验证浏览器：仓库默认 "
            ".cloakbrowser/chromium-*/chrome；Termux 包装脚本 "
            "scripts/cloakbrowser_termux/run-chrome.sh；或设置 "
            "AISTUDIO_VERIFY_CHROME_PATH 指向可执行文件。"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"{exc}；{hint}",
                "hint": hint,
            },
        ) from exc
    log.info("账号验证会话已启动: id=%s, state=%s", account_id, payload.get("state"))
    return VerifySessionResponse.model_validate(payload)


@router.get("/{account_id}/verify/status", response_model=VerifySessionResponse)
async def get_account_verification_status(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    session_id: str = Query(..., min_length=1),
    simulate: str | None = Query(default=None),
    force: bool = Query(default=False),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    controller: VerifyAccountController = Depends(get_verify_account_controller),
) -> VerifySessionResponse:
    """查询验证会话状态；通过判定只在 status 被调用时推进。

    ``simulate`` 是 ui-debug 沙盒专用的确定性驱动参数，生产路由忽略它，
    避免测试参数进入真实契约。``force=true`` 表示手动检查：即使页面尚未
    success 也立即查询上游验证状态。
    """
    if account_service.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    sandbox_simulate = simulate if get_ui_sandbox(runtime_state) is not None else None
    try:
        payload = await controller.status(
            account_id,
            session_id,
            simulate=sandbox_simulate,
            force=force,
        )
    except VerifySessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VerifySessionResponse.model_validate(payload)


@router.post("/{account_id}/verify/cancel", response_model=VerifyCancelResponse)
async def cancel_account_verification(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    controller: VerifyAccountController = Depends(get_verify_account_controller),
) -> VerifyCancelResponse:
    """取消当前账号的验证会话；重复调用幂等。"""
    if account_service.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    payload = await controller.cancel(account_id)
    state = str(payload.get("state") or "")
    log.info("账号验证会话取消: id=%s, state=%s", account_id, state)
    return VerifyCancelResponse(state=state)


@router.post("/{account_id}/verify/check", response_model=VerifyCheckResponse)
async def check_account_verification(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    simulate: str | None = Query(default=None),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    controller: VerifyAccountController = Depends(get_verify_account_controller),
) -> VerifyCheckResponse:
    """独立查询账号验证状态（不依赖二维码会话/浏览器/实例）。

    实际登录账号扫码通过后若面板仍停在“待验证”，此接口只读取该账号
    auth.json 并调用上游 ``GetUserVerificationStatus``：2/3 立即清
    ``verify_required`` 并返回 ``passed``，1/None 保持 ``pending``。
    ``simulate`` 仅用于 ui-debug 沙盒的确定性 passed/pending/error 演示，
    生产路径不会把它交给真实服务。
    """
    if account_service.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    sandbox = get_ui_sandbox(runtime_state)
    try:
        if sandbox is not None:
            payload = await sandbox.verify_service.check(account_id, simulate=simulate)
        else:
            payload = await controller.check(account_id)
    except VerifyAccountNotFound as exc:
        raise HTTPException(status_code=404, detail="账号不存在") from exc
    log.info(
        "账号验证状态独立检查: id=%s, state=%s, upstream_status=%s",
        account_id,
        payload.get("state"),
        payload.get("upstream_status"),
    )
    return VerifyCheckResponse.model_validate(payload)


@router.post("/{account_id}/credential-export", response_model=CredentialExportResponse)
async def export_account_credential(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
) -> CredentialExportResponse:
    """导出该账号的扫码凭证（只读透传 auth.json 的 cookies 数组）。"""
    from aistudio_api.config import settings

    export_opt_in = (
        os.environ.get("AISTUDIO_ALLOW_CREDENTIAL_EXPORT", "").strip().lower()
    )
    if not settings.auth_enabled and export_opt_in not in {"1", "true", "yes"}:
        raise HTTPException(
            status_code=403,
            detail=(
                "导出扫码凭证需要先设置管理密码（AISTUDIO_WEB_PASSWORD）；"
                "否则无鉴权端口可被直接读取登录凭据"
            ),
        )
    account = account_service.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    auth_path = account_service.get_account_auth_path(account_id)
    if auth_path is None or not auth_path.is_file():
        raise HTTPException(status_code=404, detail="账号凭据文件不存在")
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=404, detail="账号凭据文件不存在") from exc
    except json.JSONDecodeError as exc:
        log.warning("账号凭据文件格式损坏: id=%s", account_id)
        raise HTTPException(status_code=500, detail="账号凭据文件格式损坏") from exc
    raw_cookies = raw.get("cookies") if isinstance(raw, dict) else None
    cookies = (
        [item for item in raw_cookies if isinstance(item, dict)]
        if isinstance(raw_cookies, list)
        else []
    )
    if not cookies:
        raise HTTPException(status_code=409, detail="账号凭据不含可用 Cookie")
    log.info("账号凭证导出: id=%s, cookies=%d", account_id, len(cookies))
    return CredentialExportResponse(
        version=1,
        account_id=account.id,
        email=account.email,
        exported_at=datetime.now(UTC).isoformat(),
        cookies=cookies,
    )


@router.delete("/{account_id}")
async def delete_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, bool]:
    """删除账号。"""
    active_account = account_service.get_active_account()
    is_active = active_account is not None and active_account.id == account_id

    await _cancel_verify_for_deleted_account(runtime_state, account_id)
    success = account_service.delete_account(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="账号不存在")

    if runtime_state.rotator:
        runtime_state.rotator.remove_account(account_id)
    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        sandbox.on_account_deleted(account_id)
    if is_active:
        new_active = account_service.select_active_candidate()
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None and not _browserless_engine(runtime_state):
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        else:
            if browser_session is not None and not _browserless_engine(runtime_state):
                await browser_session.switch_auth(None)

    log.info("已删除账号: id=%s", account_id)
    return {"ok": True}


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_accounts(
    req: BatchDeleteRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> BatchDeleteResponse:
    """批量删除账号（去重），并在删除活跃账号后显式迁移。"""
    active = account_service.get_active_account()
    active_id = active.id if active is not None else None
    deleted_ids: list[str] = []
    for account_id in dict.fromkeys(str(i) for i in req.ids if str(i).strip()):
        await _cancel_verify_for_deleted_account(runtime_state, account_id)
        if account_service.delete_account(account_id):
            deleted_ids.append(account_id)
            if runtime_state.rotator:
                runtime_state.rotator.remove_account(account_id)
            sandbox = get_ui_sandbox(runtime_state)
            if sandbox is not None:
                sandbox.on_account_deleted(account_id)
    if active_id in deleted_ids:
        new_active = account_service.select_active_candidate()
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None and not _browserless_engine(runtime_state):
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        elif browser_session is not None and not _browserless_engine(runtime_state):
            await browser_session.switch_auth(None)
    log.info("批量删除账号完成: %d 个", len(deleted_ids))
    return BatchDeleteResponse(deleted=len(deleted_ids))


@router.delete("/group/{cookie_id}")
async def delete_cookie_group(
    cookie_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, int]:
    """按 Cookie 组批量删除该 Cookie 下的所有子账号。"""
    active_account = account_service.get_active_account()
    active_id = active_account.id if active_account is not None else None
    active_deleted = False

    accounts = account_service.list_accounts()
    deleted_count = 0
    rotator = runtime_state.rotator
    for a in accounts:
        acc_cid = getattr(a, "cookie_id", None) or f"cookie_{a.created_at[:16]}"
        if acc_cid == cookie_id:
            if a.id == active_id:
                active_deleted = True
            await _cancel_verify_for_deleted_account(runtime_state, a.id)
            if account_service.delete_account(a.id):
                deleted_count += 1
                if rotator:
                    rotator.remove_account(a.id)
                sandbox = get_ui_sandbox(runtime_state)
                if sandbox is not None:
                    sandbox.on_account_deleted(a.id)
                log.info("已删除子账号: cookie_id=%s, id=%s", cookie_id, a.id)

    if active_deleted:
        new_active = account_service.select_active_candidate()
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None and not _browserless_engine(runtime_state):
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        else:
            if browser_session is not None and not _browserless_engine(runtime_state):
                await browser_session.switch_auth(None)

    return {"deleted": deleted_count}


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: UpdateAccountRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号名称。"""
    account = account_service.update_account(account_id, req.name)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    log.info("账号名称已更新: id=%s, name=%s", account.id, req.name)
    return to_account_response(account)


@router.post("/{account_id}/toggle", response_model=AccountResponse)
async def toggle_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: ToggleAccountRequest = Body(default_factory=ToggleAccountRequest),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """切换单个账号的使能状态 (Enabled/Disabled)。

    D-05：禁用当前 active 时进入有界 RETIRING rebind；先 drain/关旧页，再切到
    新 enabled 账号，最后才改写 active。失败显式 409 并回滚 enabled。
    """
    previous_active = account_service.get_active_account()
    before = account_service.get_account(account_id)
    previous_enabled = bool(before.enabled) if before is not None else True
    account = account_service.toggle_account_enabled(account_id, req.enabled)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    response = to_account_response(
        account, status=_account_status(runtime_state, account.id)
    )
    if (
        previous_active is not None
        and previous_active.id == account.id
        and not account.enabled
    ):
        target = account_service.select_active_candidate(exclude={account.id})
        try:
            required, status = await _run_control_rebind(
                runtime_state,
                previous_account_id=previous_active.id,
                target_account_id=target.id if target is not None else None,
                reason="toggle",
            )
        except InstanceError:
            account_service.toggle_account_enabled(account.id, previous_enabled)
            raise
        response.rebind_required = required
        response.rebind_status = status
        response.previous_active_account_id = previous_active.id
        response.active_account_id = target.id if target is not None else None
        response.target_account = (
            to_account_response(target) if target is not None else None
        )
        log.info(
            "账号 %s 已停用，有界 rebind %s -> %s (%s)",
            account.id,
            previous_active.id,
            target.id if target is not None else None,
            status,
        )

    log.info("账号状态已切换: id=%s, enabled=%s", account.id, account.enabled)
    return response


@router.post("/batch-toggle", response_model=BatchToggleResponse)
async def batch_toggle_accounts(
    req: BatchToggleRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> BatchToggleResponse:
    """批量切换账号使能状态；禁用 active/running 实例时走 D-05 有界 rebind。"""
    manager = runtime_state.instance_manager
    running_ids: set[str] = set()
    account_ids_getter = getattr(manager, "instance_account_ids", None)
    if callable(account_ids_getter):
        with contextlib.suppress(Exception):
            raw_ids = account_ids_getter()
            if isinstance(raw_ids, (set, frozenset, list, tuple)):
                running_ids = {str(item) for item in raw_ids}

    results: list[BatchToggleResult] = []
    for account_id in dict.fromkeys(str(i) for i in req.ids if str(i).strip()):
        account = account_service.get_account(account_id)
        if account is None:
            results.append(
                BatchToggleResult(id=account_id, ok=False, error="账号不存在")
            )
            continue
        previous_enabled = bool(account.enabled)
        previous_active = account_service.get_active_account()
        toggled = account_service.toggle_account_enabled(account_id, req.enabled)
        if toggled is None:
            results.append(
                BatchToggleResult(id=account_id, ok=False, error="账号不存在")
            )
            continue
        needs_rebind = (not req.enabled) and (
            (previous_active is not None and previous_active.id == account_id)
            or account_id in running_ids
        )
        if not needs_rebind:
            results.append(BatchToggleResult(id=account_id, ok=True))
            continue
        target = account_service.select_active_candidate(exclude={account_id})
        try:
            await _run_control_rebind(
                runtime_state,
                previous_account_id=account_id,
                target_account_id=target.id if target is not None else None,
                reason="batch_toggle",
            )
        except InstanceError as exc:
            account_service.toggle_account_enabled(account_id, previous_enabled)
            results.append(BatchToggleResult(id=account_id, ok=False, error=str(exc)))
        else:
            results.append(BatchToggleResult(id=account_id, ok=True))

    toggled_count = sum(1 for result in results if result.ok)
    error_count = len(results) - toggled_count
    log.info("批量账号状态切换完成: toggled=%d, errors=%d", toggled_count, error_count)
    return BatchToggleResponse(
        results=results,
        toggled_count=toggled_count,
        error_count=error_count,
    )


@router.put("/{account_id}/group", response_model=AccountResponse)
async def update_account_group(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: UpdateGroupRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号所属业务分组。"""
    account = account_service.update_account_group(account_id, req.group)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    log.info("账号分组已更新: id=%s, group=%s", account.id, account.group)
    return to_account_response(account)


@router.get("/groups/list", response_model=list[str])
async def list_groups(
    account_service: AccountService = Depends(get_account_service),
) -> list[str]:
    """列出当前存在的所有分组名称。"""
    return account_service.list_groups()


# ─── 预设方案 (Presets) REST 路由 ───
@router.get("/presets/list", response_model=list[PresetResponse])
async def list_presets(
    account_service: AccountService = Depends(get_account_service),
) -> list[PresetResponse]:
    """获取所有可用预设方案列表。"""
    presets = account_service.list_presets()
    return [PresetResponse.model_validate(p.to_dict()) for p in presets]


@router.post("/presets", response_model=PresetResponse)
async def create_preset(
    req: CreatePresetRequest,
    account_service: AccountService = Depends(get_account_service),
) -> PresetResponse:
    """创建自定义预设方案。"""
    import secrets

    from aistudio_api.infrastructure.account.account_store import AccountPreset

    pid = f"preset_{secrets.token_hex(4)}"
    preset = AccountPreset(
        id=pid,
        name=req.name.strip() or "自定义方案",
        description=req.description.strip(),
        action=req.action,
        account_ids=req.account_ids,
        target_tiers=req.target_tiers,
        target_groups=req.target_groups,
        is_system=False,
    )
    saved = account_service.save_preset(preset)
    return PresetResponse.model_validate(saved.to_dict())


@router.post("/presets/snapshot", response_model=PresetResponse)
async def create_snapshot_preset(
    req: SnapshotPresetRequest,
    account_service: AccountService = Depends(get_account_service),
) -> PresetResponse:
    """将当前所有启用账号一键快照保存为新预设方案。"""
    saved = account_service.create_snapshot_preset(req.name, req.description)
    log.info("已创建预设快照: name=%s, id=%s", saved.name, saved.id)
    return PresetResponse.model_validate(saved.to_dict())


@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> dict[str, bool]:
    """删除自定义预设方案。"""
    try:
        ok = account_service.delete_preset(preset_id)
        if not ok:
            raise HTTPException(status_code=404, detail="预设方案不存在")
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/presets/{preset_id}/apply", response_model=ApplyPresetResponse)
async def apply_preset(
    preset_id: str,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ApplyPresetResponse:
    """一键应用预设方案。

    D-05：若预设禁用当前 active，进入有界 RETIRING rebind；失败时恢复整份
    enabled 快照并显式 409，不在 rebind 前改写 active。
    """
    enabled_before = {
        account.id: account.enabled for account in account_service.list_accounts()
    }
    try:
        result = account_service.apply_preset(preset_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    rebind_required = result.rebind_required
    rebind_status = "not_required"
    target = result.target_account
    if result.rebind_required:
        try:
            rebind_required, rebind_status = await _run_control_rebind(
                runtime_state,
                previous_account_id=result.previous_active_account_id or "",
                target_account_id=result.active_account_id,
                reason="preset",
            )
        except InstanceError:
            for account_id, enabled in enabled_before.items():
                account_service.toggle_account_enabled(account_id, enabled)
            raise

    log.info(
        "已应用预设方案 '%s': 启用 %d 个, 停用 %d 个, rebind=%s (%s)",
        result.preset.name,
        len(result.enabled_account_ids),
        len(result.disabled_account_ids),
        rebind_required,
        rebind_status,
    )
    return ApplyPresetResponse(
        ok=True,
        preset_id=result.preset.id,
        preset_name=result.preset.name,
        enabled_count=len(result.enabled_account_ids),
        disabled_count=len(result.disabled_account_ids),
        enabled_account_ids=result.enabled_account_ids,
        disabled_account_ids=result.disabled_account_ids,
        rebind_required=rebind_required,
        rebind_status=rebind_status,
        previous_active_account_id=result.previous_active_account_id,
        active_account_id=result.active_account_id,
        target_account=to_account_response(target) if target is not None else None,
    )


@router.post("/import-cookies", response_model=ImportCookiesResponse)
async def import_cookies(
    req: ImportCookiesRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ImportCookiesResponse:
    """从 cookie 导入账号（支持 JSON 数组、Netscape、KV 或油猴导出的 JSON 对象）。"""
    _apply_tampermonkey_import_metadata(req)
    storage_state = parse_cookie_string(req.cookies)
    raw_cookies = storage_state.get("cookies")
    cookie_list: list[dict[str, object]] = (
        raw_cookies if isinstance(raw_cookies, list) else []
    )
    cookie_count = len(cookie_list)

    if cookie_count == 0:
        raise HTTPException(status_code=400, detail="未解析到有效 cookie")

    domain_summary: dict[str, int] = {}
    for cookie in cookie_list:
        domain = str(cookie.get("domain", ""))
        domain_summary[domain] = domain_summary.get(domain, 0) + 1
    name = req.name or (
        f"Google Account (u/{req.auth_user})" if req.auth_user != "0" else "导入的账号"
    )

    import secrets

    cid = f"cookie_{secrets.token_hex(4)}"
    detected_email = req.email
    detected_tier = req.tier
    sandbox = get_ui_sandbox(runtime_state)
    external = _external_exits_active()
    cookie_dict = _cookie_dict_from_storage(storage_state)

    # P2-1: never key the exit manager by a synthetic ``cookie_<hex>`` id.  In
    # external mode a pre-save probe is only allowed when the caller supplied a
    # real account id; otherwise it is deferred until after save, so the real
    # id receives the long-term binding and no orphan binding is created.
    if (
        sandbox is None
        and cookie_dict
        and (not detected_email or not detected_tier)
        and (not external or req.account_id)
    ):
        decision = await resolve_credential_exit(
            req.account_id or "",
            kind="chat",
            lane=lane_for_tier(detected_tier or req.tier),
            category="maintenance",
        )
        if decision.deferred:
            log.warning(
                "导入探测延迟 account=%s：出口调用预算不足 retry_after=%.0fs"
                "（仍继续导入账号）",
                req.account_id or cid,
                decision.retry_after_s,
            )
        elif not probe_egress_allowed(decision):
            log.warning(
                "导入探测跳过 account=%s：无可用出口 reason=%s（仍继续导入账号）",
                req.account_id or cid,
                probe_skip_reason(decision),
            )
        else:
            if not detected_email:
                with contextlib.suppress(Exception):
                    detected_email = await probe_account_email(
                        cookie_dict,
                        auth_user=req.auth_user or "0",
                        account_id=req.account_id,
                        proxy=decision.proxy,
                    )
            if not detected_tier:
                with contextlib.suppress(Exception):
                    probed = await probe_account_benefit_tier(
                        cookie_dict,
                        auth_user=req.auth_user or "0",
                        account_id=req.account_id,
                        proxy=decision.proxy,
                    )
                    if probed and probed != "Free":
                        detected_tier = probed
    detected_tier = detected_tier or "Free"
    account = account_service.save_account_from_cookies(
        name=name,
        email=detected_email,
        storage_state=storage_state,
        account_id=req.account_id,
        auth_user=req.auth_user or "0",
        cookie_id=cid,
        tier=detected_tier,
    )
    if sandbox is None and external and not req.account_id and cookie_dict:
        account = await _post_save_identity_probe(
            account_service, account, storage_state, auth_user=req.auth_user or "0"
        )
    if runtime_state.rotator:
        runtime_state.rotator.add_account(account.id)
        runtime_state.rotator.clear_cooldown(account.id)
        _reconcile_verify_after_import(runtime_state.rotator, account.id)
    try:
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if browser_session and not _browserless_engine(runtime_state):
            auth_path = account_service.get_account_auth_path(account.id)
            do_refresh = True
            proxy_arg: object = UNSET_PROXY
            if external:
                # P1-2: the import-time credential refresh must use the
                # account's own exit.  Without one, skip the refresh (the
                # account is already saved) instead of leaking to WARP.
                decision = await resolve_credential_exit(
                    account.id,
                    kind="chat",
                    lane=lane_for_tier(account.tier),
                    category="maintenance",
                )
                if decision.deferred:
                    do_refresh = False
                    log.warning(
                        "导入预刷新延迟 account=%s：出口调用预算不足 "
                        "retry_after=%.0fs（仍继续导入）",
                        account.id,
                        decision.retry_after_s,
                    )
                elif probe_egress_allowed(decision):
                    proxy_arg = decision.proxy
                else:
                    do_refresh = False
                    log.warning(
                        "导入预刷新跳过 account=%s：无可用出口 reason=%s（仍继续导入）",
                        account.id,
                        probe_skip_reason(decision),
                    )
            if do_refresh:
                count = await browser_session.import_cookies(
                    req.cookies,
                    auth_file=str(auth_path) if auth_path else None,
                    proxy=proxy_arg,
                )
                log.info(
                    "已导入 Cookie 并保存文件: 数量=%d, 账号=%s", count, account.name
                )
    except Exception as e:
        log.warning("为 %s 注入浏览器 Cookie 失败: %s", account.name, e)
    return ImportCookiesResponse(
        account_id=account.id,
        name=account.name,
        cookie_count=cookie_count,
        domain_summary=domain_summary,
        auth_user=account.auth_user,
        tier=account.tier,
    )


@router.post("/probe-import", response_model=ProbeAndImportResponse)
async def probe_and_import(
    req: ProbeAndImportRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ProbeAndImportResponse:
    """探测 Cookie 中的多个已登录账号并批量导入。"""
    from aistudio_api.infrastructure.account.cookie_parser import (
        parse_cookie_string,
        probe_google_accounts_infinite,
    )

    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        storage_state = parse_cookie_string(req.cookies)
        raw_cookies = storage_state.get("cookies")
        if not isinstance(raw_cookies, list) or not raw_cookies:
            raise HTTPException(status_code=400, detail="未解析到有效 cookie")
        prefix = req.name_prefix.strip() if req.name_prefix else "Google Account"
        account = account_service.save_account_from_cookies(
            name=prefix,
            email=None,
            storage_state=storage_state,
            auth_user="0",
            cookie_id="cookie_ui_debug",
            tier="Free",
        )
        if account_service.get_active_account() is None:
            account_service.set_active_account(account.id)
        return ProbeAndImportResponse(
            imported_count=1,
            accounts=[
                to_account_response(
                    account, status=_account_status(runtime_state, account.id)
                )
            ],
        )

    had_active_account = account_service.get_active_account() is not None
    storage_state = parse_cookie_string(req.cookies)
    prefix = req.name_prefix.strip() if req.name_prefix else "Google Account"
    import secrets

    cid = f"cookie_{secrets.token_hex(4)}"
    external = _external_exits_active()
    skipped_ids: list[str] = []
    skip_reason: str | None = None
    probe_failed: dict[str, str] = {}

    if external:
        # P2-R2/P2-A: never 503 the whole batch and never stack duplicate
        # anchors.  The anchor id is deterministic (stable identity cookie) so
        # re-importing the same jar reuses one record; the exit manager binds
        # ``HMAC(real anchor id)`` (never a synthetic ``cookie_<hex>``), the
        # multi-account probe runs through the anchor's exit, and the result is
        # merged/split into real accounts.
        anchor_id = _stable_import_anchor_id(storage_state) or (
            f"{IMPORT_ANCHOR_PREFIX}{secrets.token_hex(6)}"
        )
        existing_anchor = account_service.get_account(anchor_id)
        anchor = account_service.save_account_from_cookies(
            name=prefix,
            # Preserve a previously discovered identity when re-running a probe
            # that may fail again (do not wipe email on a retry).
            email=existing_anchor.email if existing_anchor is not None else None,
            storage_state=storage_state,
            account_id=anchor_id,
            auth_user="0",
            cookie_id=cid,
            tier="Free",
        )
        decision = await resolve_credential_exit(
            anchor.id,
            kind="chat",
            lane=lane_for_tier(anchor.tier),
            category="maintenance",
        )
        probed: list[dict[str, object]] = []
        if decision.deferred:
            skip_reason = (
                f"deferred_no_capacity: retry_after_s={decision.retry_after_s:.0f}"
            )
        elif probe_egress_allowed(decision):
            try:
                probed = await probe_google_accounts_infinite(
                    req.cookies, account_id=anchor.id, proxy=decision.proxy
                )
            except Exception as exc:
                log.warning("多账号探活失败 account=%s: %s", anchor.id, exc)
                skip_reason = f"probe_error: {exc}"
        else:
            skip_reason = probe_skip_reason(decision)

        metas: list[AccountMeta] = []
        if not probed:
            # No exit (or probe failed): keep exactly one retryable placeholder,
            # record the reason on it and return 200 instead of failing the
            # batch.  The deterministic id means retries never accumulate.
            metas = [anchor]
            skipped_ids.append(anchor.id)
            skip_reason = skip_reason or "probe_failed"
            _mark_probe_reason(account_service, anchor.id, skip_reason)
            probe_failed[anchor.id] = skip_reason
        else:
            checked_at = datetime.now(UTC).isoformat()
            anchor_claimed = False
            for entry in probed:
                u_idx = str(entry.get("auth_user") or "0")
                email_val = str(entry["email"]) if entry.get("email") else None
                tier_val = str(entry.get("tier") or "Free")
                if u_idx == "0":
                    anchor_claimed = True
                    existing = _find_identity_account(
                        account_service,
                        email=email_val,
                        auth_user="0",
                        exclude_id=anchor.id,
                    )
                    if existing is not None:
                        # The same account already exists under its real id:
                        # merge this re-import into it and drop the placeholder.
                        meta = account_service.save_account_from_cookies(
                            name=existing.name or prefix,
                            email=email_val,
                            storage_state=storage_state,
                            account_id=existing.id,
                            auth_user="0",
                            cookie_id=cid,
                            tier=tier_val,
                        )
                        if meta.tier == tier_val:
                            account_service.update_account_tier(
                                meta.id, tier_val, checked_at=checked_at
                            )
                        account_service.delete_account(anchor.id)
                    else:
                        # u/0 claims the deterministic anchor as its real record.
                        if email_val:
                            account_service.store.update_account_email(
                                anchor.id, email_val
                            )
                        account_service.store.update_account_tier(
                            anchor.id, tier_val, checked_at=checked_at
                        )
                        meta = account_service.get_account(anchor.id) or anchor
                else:
                    meta = account_service.save_account_from_cookies(
                        name=f"{prefix} (u/{u_idx})",
                        email=email_val,
                        storage_state=storage_state,
                        auth_user=u_idx,
                        cookie_id=cid,
                        tier=tier_val,
                    )
                if not meta.email or not meta.tier or meta.tier == "Free":
                    # Per-account identity probe keyed by the real account id.
                    meta = await _post_save_identity_probe(
                        account_service, meta, storage_state, auth_user=u_idx
                    )
                if meta.last_refresh_error:
                    probe_failed[meta.id] = meta.last_refresh_error
                metas.append(meta)
            if not anchor_claimed:
                # Probe succeeded without a u/0 owner: the placeholder is an
                # orphan, so remove it instead of leaving a half-baked account.
                account_service.delete_account(anchor.id)

        rotator = getattr(runtime_state, "rotator", None)
        if rotator:
            for meta in metas:
                rotator.add_account(meta.id)
                rotator.clear_cooldown(meta.id)
                _reconcile_verify_after_import(rotator, meta.id)
        imported_accounts = [
            to_account_response(meta, status=_account_status(runtime_state, meta.id))
            for meta in metas
        ]
        client_obj = getattr(runtime_state, "client", None)
        if not had_active_account and metas and client_obj is not None:
            browser_session = client_obj._session
            if browser_session is not None:
                await account_service.activate_account(metas[0].id, browser_session)
        log.info(
            "Cookie 探活完成: 导入账号数=%d skipped=%d probe_failed=%d",
            len(imported_accounts),
            len(skipped_ids),
            len(probe_failed),
        )
        return ProbeAndImportResponse(
            imported_count=len(imported_accounts),
            accounts=imported_accounts,
            skipped=skipped_ids,
            skip_reason=skip_reason,
            probe_failed=probe_failed,
        )

    probed = await probe_google_accounts_infinite(req.cookies)
    if not probed:
        raise HTTPException(status_code=400, detail="未探测到有效已登录 Google 账号")

    metas = account_service.batch_import_accounts(
        probed_list=probed,
        storage_state=storage_state,
        prefix=prefix,
        cookie_id=cid,
    )
    rotator = getattr(runtime_state, "rotator", None)
    if rotator:
        for meta in metas:
            rotator.add_account(meta.id)
            rotator.clear_cooldown(meta.id)
            _reconcile_verify_after_import(rotator, meta.id)

    imported_accounts = [to_account_response(account) for account in metas]

    client_obj = getattr(runtime_state, "client", None)
    if not had_active_account and metas and client_obj is not None:
        browser_session = client_obj._session
        if browser_session is not None:
            await account_service.activate_account(metas[0].id, browser_session)
    log.info("Cookie 探活完成: 导入账号数=%d", len(imported_accounts))
    return ProbeAndImportResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )


def _extract_bundle_items(raw_data: object) -> list[dict[str, object]]:
    """从任意输入数据中提取账号条目列表，兼容 4 种常见 JSON 结构：
    1. 多账号 Bundle 字典（含 accounts 或 profiles 列表）
    2. 单账号 Playwright storageState 字典（如 auth-49.json，顶层含 cookies）
    3. 多账号对象数组（元素包含 cookies 或 cookieObjects）
    4. Cookie-Editor 导出的纯 Cookie 数组（元素含 name 与 value 但不含 cookies）
    """
    if isinstance(raw_data, dict):
        accounts = raw_data.get("accounts")
        if isinstance(accounts, list):
            return [item for item in accounts if isinstance(item, dict)]
        profiles = raw_data.get("profiles")
        if isinstance(profiles, list):
            return [item for item in profiles if isinstance(item, dict)]
        if "cookies" in raw_data and isinstance(raw_data["cookies"], (list, str, dict)):
            return [raw_data]
        if "cookieObjects" in raw_data and isinstance(raw_data["cookieObjects"], list):
            return [raw_data]
        return []

    if isinstance(raw_data, list):
        account_items = [
            item
            for item in raw_data
            if isinstance(item, dict) and ("cookies" in item or "cookieObjects" in item)
        ]
        if account_items:
            return account_items
        # 检查是否为 Cookie-Editor 导出的单条 Cookie 列表
        is_cookie_array = bool(
            raw_data
            and all(
                isinstance(item, dict) and "name" in item and "value" in item
                for item in raw_data
                if isinstance(item, dict)
            )
        )
        if is_cookie_array:
            return [{"cookies": raw_data}]

    return []


@router.post("/import-bundle", response_model=ImportBundleResponse)
async def import_bundle(
    req: ImportBundleRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ImportBundleResponse:
    """批量导入多个账号的凭据 Bundle（支持传入 JSON 文本、直接解析列表或服务器本地文件路径）。"""
    import json
    import secrets
    from pathlib import Path

    from aistudio_api.infrastructure.account.cookie_parser import (
        parse_cookie_string,
        parse_storage_state,
    )

    items_to_import: list[dict[str, object]] = []

    if req.accounts:
        for a in req.accounts:
            a_dict = a.model_dump(exclude_unset=True)
            if a_dict:
                items_to_import.append(a_dict)
    elif req.content:
        try:
            raw_data = json.loads(req.content)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"JSON 内容解析失败: {e}"
            ) from e
        items_to_import = _extract_bundle_items(raw_data)
    elif req.file_path:
        fpath = Path(req.file_path).expanduser().resolve()
        if not fpath.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {fpath}")
        try:
            raw_text = fpath.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"读取或解析文件失败: {e}"
            ) from e
        items_to_import = _extract_bundle_items(raw_data)
    else:
        raise HTTPException(
            status_code=400,
            detail="请提供 accounts 列表、content JSON 文本或 file_path 本地文件路径",
        )

    if not items_to_import:
        raise HTTPException(
            status_code=400, detail="未解析到任何包含有效 cookies 的账号条目"
        )

    imported_accounts: list[AccountResponse] = []
    sandbox = get_ui_sandbox(runtime_state)
    external_exits = _external_exits_active()
    for idx, item in enumerate(items_to_import):
        if external_exits and idx > 0 and IMPORT_PROBE_PACING_SECONDS > 0:
            await asyncio.sleep(IMPORT_PROBE_PACING_SECONDS)
        storage_state: dict[str, object] | None = None
        cookie_objects = item.get("cookieObjects")
        cookies_val = item.get("cookies")

        if isinstance(cookie_objects, list) and cookie_objects:
            storage_state = parse_storage_state({"cookies": cookie_objects})
            if storage_state is None:
                storage_state = parse_cookie_string(cookie_objects)
        elif isinstance(cookies_val, list):
            storage_state = parse_storage_state(
                {"cookies": cookies_val, "origins": item.get("origins", [])}
            )
            if storage_state is None:
                storage_state = parse_cookie_string(cookies_val)
        elif isinstance(cookies_val, dict):
            storage_state = parse_storage_state(cookies_val)
            if storage_state is None:
                storage_state = parse_cookie_string(cookies_val)
        elif isinstance(cookies_val, str) and cookies_val.strip():
            storage_state = parse_cookie_string(cookies_val)

        if not storage_state or not storage_state.get("cookies"):
            continue
        cid = f"cookie_{secrets.token_hex(4)}"

        acc_email = str(item["email"]) if item.get("email") else None
        account_name_val = str(item.get("accountName") or "").strip()
        if not acc_email and "@" in account_name_val:
            acc_email = account_name_val

        acc_name = str(item.get("name") or "")
        if not acc_name:
            if account_name_val:
                acc_name = account_name_val
            elif acc_email:
                acc_name = f"Google ({acc_email.split('@')[0]})"
            else:
                acc_name = f"Account {idx + 1}"

        acc_auth_user = str(item.get("auth_user") or item.get("authUser") or "0")

        acc_tier = ""
        g1_raw = item.get("g1Tier") or item.get("g1_tier")
        if g1_raw:
            acc_tier = _TAMPERMONKEY_TIER_MAP.get(str(g1_raw).strip().upper()) or str(
                g1_raw
            )
        if not acc_tier and item.get("tier"):
            acc_tier = str(item["tier"])

        if sandbox is None and not external_exits and (not acc_email or not acc_tier):
            # P2-1: external mode never keys the manager by a synthetic cookie
            # id; it probes after save with the real account id.  Off mode keeps
            # the legacy pre-save probe (the global proxy involves no binding).
            decision = await resolve_credential_exit("", kind="chat")
            cookie_dict = _cookie_dict_from_storage(storage_state)
            if cookie_dict and probe_egress_allowed(decision):
                if not acc_email:
                    with contextlib.suppress(Exception):
                        acc_email = await probe_account_email(
                            cookie_dict,
                            auth_user=acc_auth_user,
                            account_id=None,
                            proxy=decision.proxy,
                        )
                if not acc_tier:
                    with contextlib.suppress(Exception):
                        probed = await probe_account_benefit_tier(
                            cookie_dict,
                            auth_user=acc_auth_user,
                            account_id=None,
                            proxy=decision.proxy,
                        )
                        if probed and probed != "Free":
                            acc_tier = probed
        acc_tier = acc_tier or "Free"

        account = account_service.save_account_from_cookies(
            name=acc_name,
            email=acc_email,
            storage_state=storage_state,
            auth_user=acc_auth_user,
            cookie_id=cid,
            tier=acc_tier,
        )
        if (
            sandbox is None
            and external_exits
            and (not account.email or not account.tier or account.tier == "Free")
        ):
            account = await _post_save_identity_probe(
                account_service, account, storage_state, auth_user=acc_auth_user
            )
        rotator = getattr(runtime_state, "rotator", None)
        if rotator:
            rotator.add_account(account.id)
            rotator.clear_cooldown(account.id)
            _reconcile_verify_after_import(rotator, account.id)
        imported_accounts.append(
            to_account_response(
                account, status=_account_status(runtime_state, account.id)
            )
        )
    if not account_service.get_active_account() and imported_accounts:
        account_service.set_active_account(imported_accounts[0].id)
    log.info("批量导入完成: 导入账号数=%d", len(imported_accounts))
    return ImportBundleResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )


@router.post("/{account_id}/rotate-cookies", response_model=RotateCookiesResponse)
async def rotate_account_cookies_endpoint(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> RotateCookiesResponse:
    """手动调用 Google RotateCookies 刷新指定账号的短效滚动凭据。"""
    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    sandbox = get_ui_sandbox(runtime_state)
    if sandbox is not None:
        return RotateCookiesResponse(
            ok=True,
            status=200,
            updated=["__Secure-1PSIDTS", "SIDCC"],
            account=to_account_response(
                acc, status=_account_status(runtime_state, acc.id)
            ),
        )

    session: Any = None
    im: Any = getattr(runtime_state, "instance_manager", None)
    if im is not None:
        getter: Any = getattr(im, "pool_slots", None)
        if callable(getter):
            slots: Any = getter()
            if isinstance(slots, (list, tuple)):
                for slot in slots:
                    if getattr(slot, "account_id", None) == account_id:
                        session = getattr(slot, "session", None)
                        break

    res = await account_service.rotate_account_cookies(account_id, session=session)
    updated_acc = account_service.get_account(account_id) or acc
    raw_status = res.get("status")
    status_code: int = (
        int(raw_status)
        if isinstance(raw_status, (int, float))
        else (200 if res.get("ok") else 0)
    )
    raw_updated = res.get("updated")
    updated_list = (
        [str(u) for u in raw_updated if isinstance(u, str)]
        if isinstance(raw_updated, (list, tuple))
        else []
    )
    return RotateCookiesResponse(
        ok=bool(res.get("ok")),
        status=status_code,
        updated=updated_list,
        account=to_account_response(
            updated_acc, status=_account_status(runtime_state, updated_acc.id)
        ),
    )
