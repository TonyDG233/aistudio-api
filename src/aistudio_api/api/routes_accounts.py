"""账号管理路由。"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastApiPath
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
from aistudio_api.infrastructure.account.account_info_refresher import (
    AccountInfoRefresher,
    RefreshInProgressError,
)
from aistudio_api.infrastructure.account.account_store import AccountMeta
from aistudio_api.infrastructure.account.cookie_parser import (
    parse_cookie_string,
    probe_account_benefit_tier,
    probe_account_email,
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


def get_account_info_refresher(
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountInfoRefresher:
    """优先复用 lifespan 创建的共享刷新器，测试可依赖覆盖。"""
    global _refresher_singleton
    shared = runtime_state.account_refresher
    if shared is not None and callable(getattr(shared, "refresh_accounts", None)):
        return cast(AccountInfoRefresher, shared)
    if _refresher_singleton is None:
        _refresher_singleton = AccountInfoRefresher(account_service.store)
    return _refresher_singleton


def get_ui_sandbox(runtime_state: RuntimeState) -> UISandbox | None:
    """Return the active offline sandbox, or None outside ui_debug mode."""
    sandbox = getattr(runtime_state, "ui_sandbox", None)
    if getattr(runtime_state, "ui_debug", False) and isinstance(sandbox, UISandbox):
        return sandbox
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
    is_available = bool(
        account.enabled
        and not account.expired
        and not session_expired
        and cooldown == 0
    )
    return {
        "session_expired": session_expired,
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
        is_available = bool(enabled and not expired)
        session_expired = False
        auth_cooldown = 0
        quota_exhausted = False
    else:
        is_available = bool(status.get("is_available", True))
        session_expired = bool(status.get("session_expired", False))
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


class BundleAccountItem(BaseModel):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    cookies: str
    auth_user: str = "0"
    tier: str | None = None


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
        try:
            auth_data = json.loads(target_auth.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict:
                if not email:
                    email = await probe_account_email(
                        cookie_dict, auth_user=acc.auth_user or "0"
                    )
                if not detected_tier or detected_tier == "Free":
                    http_tier = await probe_account_benefit_tier(
                        cookie_dict, auth_user=acc.auth_user or "0"
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
    只有上游明确给出可解析等级时才写库；探测失败返回 400（缺少 Cookie）或
    502（网络/代理/上游异常、响应不可解析），绝不把已有 Pro/Ultra 降级为 Free。
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

    if detected_tier is None and cookie_dict:
        detected_tier = await probe_account_benefit_tier(
            cookie_dict, auth_user=auth_user
        )

    if detected_tier is None:
        if not cookie_dict:
            raise HTTPException(
                status_code=400,
                detail="缺少可探测该账号的 Cookie，请先导入或刷新 cookies",
            )
        raise HTTPException(
            status_code=502,
            detail=(
                "订阅等级探测失败：网络/代理异常、上游非 200 或响应不可解析；"
                "账号原等级保持不变"
            ),
        )

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


@router.delete("/{account_id}")
async def delete_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> dict[str, bool]:
    """删除账号。"""
    active_account = account_service.get_active_account()
    is_active = active_account is not None and active_account.id == account_id

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
    if sandbox is None and (not detected_email or not detected_tier):
        raw_cookies_list = storage_state.get("cookies")
        if isinstance(raw_cookies_list, list):
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies_list
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict:
                if not detected_email:
                    with contextlib.suppress(Exception):
                        detected_email = await probe_account_email(
                            cookie_dict, auth_user=req.auth_user or "0"
                        )
                if not detected_tier:
                    with contextlib.suppress(Exception):
                        probed = await probe_account_benefit_tier(
                            cookie_dict, auth_user=req.auth_user or "0"
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
    if runtime_state.rotator:
        runtime_state.rotator.add_account(account.id)
        runtime_state.rotator.clear_cooldown(account.id)
    try:
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if browser_session and not _browserless_engine(runtime_state):
            auth_path = account_service.get_account_auth_path(account.id)
            count = await browser_session.import_cookies(
                req.cookies,
                auth_file=str(auth_path) if auth_path else None,
            )
            log.info("已导入 Cookie 并保存文件: 数量=%d, 账号=%s", count, account.name)
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
    probed = await probe_google_accounts_infinite(req.cookies)
    if not probed:
        raise HTTPException(status_code=400, detail="未探测到有效已登录 Google 账号")

    storage_state = parse_cookie_string(req.cookies)
    prefix = req.name_prefix.strip() if req.name_prefix else "Google Account"
    import secrets

    cid = f"cookie_{secrets.token_hex(4)}"
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

    from aistudio_api.infrastructure.account.cookie_parser import parse_cookie_string

    items_to_import: list[dict[str, object]] = []

    if req.accounts:
        for a in req.accounts:
            items_to_import.append(
                {
                    "name": a.name or "Google Account",
                    "email": a.email,
                    "cookies": a.cookies,
                    "auth_user": a.auth_user or "0",
                    "tier": a.tier or "Free",
                }
            )
    elif req.content:
        try:
            raw_data = json.loads(req.content)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"JSON 内容解析失败: {e}"
            ) from e
        if isinstance(raw_data, list):
            raw_list = raw_data
        elif isinstance(raw_data, dict):
            raw_list = raw_data.get("accounts") or raw_data.get("profiles") or []
        else:
            raw_list = []
        for item in raw_list:
            if isinstance(item, dict) and item.get("cookies"):
                items_to_import.append(item)
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
        if isinstance(raw_data, list):
            raw_list = raw_data
        elif isinstance(raw_data, dict):
            raw_list = raw_data.get("accounts") or raw_data.get("profiles") or []
        else:
            raw_list = []
        for item in raw_list:
            if isinstance(item, dict) and item.get("cookies"):
                items_to_import.append(item)
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
    for idx, item in enumerate(items_to_import):
        raw_cookie_str = str(item.get("cookies") or "")
        if not raw_cookie_str:
            continue
        storage_state = parse_cookie_string(raw_cookie_str)
        if not storage_state.get("cookies"):
            continue

        acc_name = str(item.get("name") or f"Account {idx + 1}")
        acc_email = str(item["email"]) if item.get("email") else None
        acc_auth_user = str(item.get("auth_user") or "0")
        acc_tier = str(item.get("tier") or "")
        if sandbox is None and (not acc_email or not acc_tier):
            raw_cookies_list = storage_state.get("cookies")
            if isinstance(raw_cookies_list, list):
                cookie_dict = {
                    str(c.get("name") or ""): str(c.get("value") or "")
                    for c in raw_cookies_list
                    if isinstance(c, dict) and c.get("name")
                }
                if cookie_dict:
                    if not acc_email:
                        with contextlib.suppress(Exception):
                            acc_email = await probe_account_email(
                                cookie_dict, auth_user=acc_auth_user
                            )
                    if not acc_tier:
                        with contextlib.suppress(Exception):
                            probed = await probe_account_benefit_tier(
                                cookie_dict, auth_user=acc_auth_user
                            )
                            if probed and probed != "Free":
                                acc_tier = probed
        acc_tier = acc_tier or "Free"
        cid = f"cookie_{secrets.token_hex(4)}"

        account = account_service.save_account_from_cookies(
            name=acc_name,
            email=acc_email,
            storage_state=storage_state,
            auth_user=acc_auth_user,
            cookie_id=cid,
            tier=acc_tier,
        )
        rotator = getattr(runtime_state, "rotator", None)
        if rotator:
            rotator.add_account(account.id)
            rotator.clear_cooldown(account.id)
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
