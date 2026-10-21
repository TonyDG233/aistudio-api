"""账号管理路由。"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastApiPath
from pydantic import BaseModel, Field

from aistudio_api.api.dependencies import (
    get_account_service,
    get_runtime_state,
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
    """优先复用 lifespan 创建的共享刷新器，测试可继续依赖覆盖。"""
    global _refresher_singleton
    if runtime_state.account_refresher is not None:
        return runtime_state.account_refresher
    if _refresher_singleton is None:
        _refresher_singleton = AccountInfoRefresher(account_service.store)
    return _refresher_singleton


class AccountResponse(BaseModel):
    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None
    auth_user: str = "0"
    cookie_id: str | None = None
    tier: str | None = None
    expired: bool = False
    duplicate_of: str | None = None
    tags: list[str] = Field(default_factory=list)


def to_account_response(account: AccountMeta) -> AccountResponse:
    cid = getattr(account, "cookie_id", None) or (
        f"cookie_{account.created_at[:16]}"
        if getattr(account, "created_at", None)
        else f"cookie_{account.id}"
    )
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at or "",
        last_used=account.last_used,
        auth_user=getattr(account, "auth_user", "0") or "0",
        cookie_id=cid,
        tier=getattr(account, "tier", None),
        expired=bool(getattr(account, "expired", False)),
        duplicate_of=getattr(account, "duplicate_of", None),
        tags=list(getattr(account, "tags", []) or []),
    )


class UpdateAccountRequest(BaseModel):
    name: str
    tags: list[str] | None = None


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
    tier: str | None = None


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


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class BatchDeleteResponse(BaseModel):
    deleted: int


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


@router.get("", response_model=list[AccountResponse])
@router.get("/", response_model=list[AccountResponse])
async def list_accounts(
    account_service: AccountService = Depends(get_account_service),
) -> list[AccountResponse]:
    """列出所有账号。"""
    accounts = account_service.list_accounts()
    return [to_account_response(a) for a in accounts]


@router.get("/active", response_model=AccountResponse)
async def get_active_account(
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """获取当前活跃账号。"""
    account = account_service.get_active_account()
    if account is None:
        raise HTTPException(status_code=404, detail="没有活跃账号")
    return to_account_response(account)


@router.get("/tags", response_model=list[str])
async def list_account_tags(
    account_service: AccountService = Depends(get_account_service),
) -> list[str]:
    """账号 tag 目录：自定义 tag + tier 自动 tag。"""
    return account_service.list_tags()


@router.post("/refresh", response_model=RefreshAccountsResponse)
async def refresh_accounts(
    req: RefreshAccountsRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
    refresher: AccountInfoRefresher = Depends(get_account_info_refresher),
) -> RefreshAccountsResponse:
    """刷新账号信息（tier/过期/邮箱）。

    - ``all=true`` 刷新全部账号；否则须提供 ``account_ids``（否则 400）。
    - ``refresh_email`` 控制是否强制回填邮箱（默认 true；未记录邮箱时无论如何都会尝试）。
    - 已有刷新任务进行中时返回 409（进程内互斥，单实例并发语义）。
    """
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
    if runtime_state.client is None:
        raise HTTPException(status_code=503, detail="服务未就绪")

    from aistudio_api.application.account_orchestrator import switch_to_account

    ok = await switch_to_account(account_id, require_idle=False)
    account = account_service.get_account(account_id)
    if not ok or account is None:
        raise HTTPException(status_code=404, detail="账号不存在或切换失败")
    if runtime_state.rotator:
        runtime_state.rotator.add_account(account.id)
        runtime_state.rotator.clear_cooldown(account.id)
    log.info("手动激活账号: %s (%s)", account.id, account.name)
    return to_account_response(account)


@router.post("/{account_id}/detect-email", response_model=AccountResponse)
async def detect_account_email(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> AccountResponse:
    """自动提取并更新指定账号的真实邮箱与订阅权益等级（优先无感 HTTP 探测，必要时回退受控浏览器）。"""
    import json

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
        account_service.store.refresh_duplicate_flags()
        log.info("已自动识别并更新账号 %s 邮箱: %s", account_id, mask_email(email))

    if detected_tier:
        account_service.update_account_tier(account_id, detected_tier)
        log.info("已自动识别并更新账号 %s 订阅等级: %s", account_id, detected_tier)

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
    """探测并更新指定账号的订阅权益等级 (Free / Pro / Ultra / Plus)。"""
    import json

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    detected_tier: str | None = None
    target_auth = account_service.get_account_auth_path(account_id)
    browser_session = runtime_state.client._session if runtime_state.client else None
    if (
        browser_session is not None
        and browser_session.page is not None
        and target_auth
        and str(target_auth) == browser_session._auth_file
        and hasattr(browser_session, "extract_account_tier")
    ):
        detected_tier = await browser_session.extract_account_tier()

    if not detected_tier and target_auth and target_auth.is_file():
        try:
            auth_data = json.loads(target_auth.read_text(encoding="utf-8"))
            raw_cookies = auth_data.get("cookies") or []
            cookie_dict = {
                str(c.get("name") or ""): str(c.get("value") or "")
                for c in raw_cookies
                if isinstance(c, dict) and c.get("name")
            }
            if cookie_dict:
                detected_tier = await probe_account_benefit_tier(
                    cookie_dict, auth_user=acc.auth_user or "0"
                )
        except Exception as e:
            log.debug("通过 HTTP 探测账号 %s 订阅等级失败: %s", account_id, e)

    if detected_tier:
        account_service.update_account_tier(account_id, detected_tier)
        log.info("已更新账号 %s 订阅等级: %s", account_id, detected_tier)

    acc = account_service.get_account(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return to_account_response(acc)


class AccountRoutingUpdate(BaseModel):
    surfaces: list[str] | None = None
    weight: float | None = None
    model_surfaces: dict[str, str] | None = None
    quota_pools: list[str] | None = None


class AccountRoutingResponse(BaseModel):
    surfaces: list[str] = Field(default_factory=list)
    weight: float = 1.0
    model_surfaces: dict[str, str] = Field(default_factory=dict)
    quota_pools: list[str] = Field(default_factory=list)


@router.get("/{account_id}/routing", response_model=AccountRoutingResponse)
async def get_account_routing(
    account_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> AccountRoutingResponse:
    if account_service.store.get_account(account_id) is None:
        raise HTTPException(404, detail="账号不存在")
    raw = account_service.store.get_account_routing(account_id)
    from aistudio_api.application.account_routing import normalize_account_routing

    routing = normalize_account_routing(raw)
    return AccountRoutingResponse.model_validate(routing.to_dict())


@router.put("/{account_id}/routing", response_model=AccountRoutingResponse)
async def update_account_routing(
    account_id: str,
    req: AccountRoutingUpdate,
    account_service: AccountService = Depends(get_account_service),
) -> AccountRoutingResponse:
    if account_service.store.get_account(account_id) is None:
        raise HTTPException(404, detail="账号不存在")
    from aistudio_api.application.account_routing import normalize_account_routing

    raw = req.model_dump(exclude_none=True)
    normalized = normalize_account_routing(raw)
    if not account_service.store.update_account_routing(
        account_id, normalized.to_dict()
    ):
        raise HTTPException(404, detail="账号不存在")
    return AccountRoutingResponse.model_validate(normalized.to_dict())


@router.delete("/{account_id}/routing")
async def clear_account_routing(
    account_id: str,
    account_service: AccountService = Depends(get_account_service),
) -> dict[str, bool]:
    if account_service.store.get_account(account_id) is None:
        raise HTTPException(404, detail="账号不存在")
    return {"ok": account_service.store.clear_account_routing(account_id)}


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
    if is_active:
        remaining_accounts = account_service.list_accounts()
        new_active = remaining_accounts[0] if remaining_accounts else None
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None:
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        else:
            if browser_session is not None:
                await browser_session.switch_auth(None)

    log.info("已删除账号: %s", account_id)
    return {"ok": True}


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_accounts(
    req: BatchDeleteRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> BatchDeleteResponse:
    """批量删除账号（用于清理重复项）。"""
    active_account = account_service.get_active_account()
    active_id = active_account.id if active_account is not None else None

    deleted_ids: list[str] = []
    for account_id in dict.fromkeys(req.ids):
        if not account_service.delete_account(account_id):
            continue
        deleted_ids.append(account_id)
        if runtime_state.rotator:
            runtime_state.rotator.remove_account(account_id)

    if active_id is not None and active_id in deleted_ids:
        remaining_accounts = account_service.list_accounts()
        new_active = remaining_accounts[0] if remaining_accounts else None
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None:
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        elif browser_session is not None:
            await browser_session.switch_auth(None)

    log.info("批量删除账号: %d 个", len(deleted_ids))
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
                log.info("已删除组 %s 下的子账号 %s", cookie_id, a.id)

    if active_deleted:
        remaining_accounts = account_service.list_accounts()
        new_active = remaining_accounts[0] if remaining_accounts else None
        browser_session = (
            runtime_state.client._session if runtime_state.client else None
        )
        if new_active is not None:
            account_service.set_active_account(new_active.id)
            if browser_session is not None:
                new_auth_path = account_service.get_account_auth_path(new_active.id)
                await browser_session.switch_auth(
                    str(new_auth_path) if new_auth_path else None
                )
        else:
            if browser_session is not None:
                await browser_session.switch_auth(None)

    return {"deleted": deleted_count}


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str = FastApiPath(..., pattern=r"^[a-zA-Z0-9_\-\.@]+$"),
    req: UpdateAccountRequest = Body(...),
    account_service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """更新账号名称与自定义 tag。"""
    account = account_service.update_account(account_id, req.name, req.tags)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    log.info("账号已更新: %s -> %s", account.id, req.name)
    return to_account_response(account)


@router.post("/import-cookies", response_model=ImportCookiesResponse)
async def import_cookies(
    req: ImportCookiesRequest,
    account_service: AccountService = Depends(get_account_service),
    runtime_state: RuntimeState = Depends(get_runtime_state),
) -> ImportCookiesResponse:
    """从 cookie 导入账号（支持 JSON 数组、Netscape 或 KV）。"""
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
    if not detected_email or not detected_tier:
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
        if browser_session:
            auth_path = account_service.get_account_auth_path(account.id)
            count = await browser_session.import_cookies(
                req.cookies,
                auth_file=str(auth_path) if auth_path else None,
            )
            log.info("已注入 %d 个 Cookie 并为 %s 保存 auth.json", count, account.name)
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
    """单份 Cookie 无限向下探活多账号并一键批量导入。"""
    from aistudio_api.infrastructure.account.cookie_parser import (
        parse_cookie_string,
        probe_google_accounts_infinite,
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

    account_service.store.refresh_duplicate_flags()
    imported_accounts = [
        to_account_response(fresh)
        for item in metas
        if (fresh := account_service.get_account(item.id)) is not None
    ]

    client_obj = getattr(runtime_state, "client", None)
    if not had_active_account and metas and client_obj is not None:
        browser_session = client_obj._session
        if browser_session is not None:
            await account_service.activate_account(metas[0].id, browser_session)
    log.info("探活与导入完成: 成功导入 %d 个账号", len(imported_accounts))
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

    def _extract_items(raw_data: object) -> list[dict[str, object]]:
        if isinstance(raw_data, list):
            return [
                item
                for item in raw_data
                if isinstance(item, dict) and item.get("cookies")
            ]
        if isinstance(raw_data, dict):
            nested = raw_data.get("accounts") or raw_data.get("profiles")
            if isinstance(nested, list):
                return [
                    item
                    for item in nested
                    if isinstance(item, dict) and item.get("cookies")
                ]
            if raw_data.get("cookies"):
                return [raw_data]
        return []

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
        items_to_import.extend(_extract_items(raw_data))
    elif req.file_path:
        fpath = Path(req.file_path).expanduser().resolve()
        if not fpath.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {fpath}")
        paths = sorted(fpath.glob("auth-*.json")) if fpath.is_dir() else [fpath]
        if not paths:
            raise HTTPException(
                status_code=400, detail=f"目录中没有 auth-*.json: {fpath}"
            )
        for path in paths:
            try:
                raw_text = path.read_text(encoding="utf-8")
                raw_data = json.loads(raw_text)
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"读取或解析文件失败: {path}: {e}"
                ) from e
            items_to_import.extend(_extract_items(raw_data))
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
    for idx, item in enumerate(items_to_import):
        raw_cookie = item.get("cookies")
        raw_cookie_str = (
            raw_cookie
            if isinstance(raw_cookie, str)
            else json.dumps(raw_cookie, ensure_ascii=False)
        )
        if not raw_cookie_str:
            continue
        storage_state = parse_cookie_string(raw_cookie_str)
        if not storage_state.get("cookies"):
            continue

        acc_name = str(
            item.get("name") or item.get("accountName") or f"Account {idx + 1}"
        )
        acc_email = str(item["email"]) if item.get("email") else None
        acc_auth_user = str(item.get("auth_user") or item.get("authUser") or "0")
        acc_tier = str(item.get("tier") or "")
        if not acc_email or not acc_tier:
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
        imported_accounts.append(to_account_response(account))
    if not account_service.get_active_account() and imported_accounts:
        account_service.set_active_account(imported_accounts[0].id)
    account_service.store.refresh_duplicate_flags()
    imported_accounts = [
        to_account_response(fresh)
        for item in imported_accounts
        if (fresh := account_service.get_account(item.id)) is not None
    ]
    log.info("批量文件导入完成: 成功入库 %d 个账号", len(imported_accounts))
    return ImportBundleResponse(
        imported_count=len(imported_accounts),
        accounts=imported_accounts,
    )
