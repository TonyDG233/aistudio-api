"""账号信息刷新：无浏览器直连探测 AI Studio 档位（tier）与登录态，并回填邮箱。

传输与判定对齐上游：
- 探测 RPC：``POST .../MakerSuite service/GetAiStudioBenefitTier``，body ``"[]"``，
  ``content-type: application/json+protobuf``，携带账号 Cookie、SAPISIDHASH
  Authorization 与 AI Studio web API key。
- 档位映射：``[]``/``0`` -> Free；``1`` -> Pro；``2`` -> Ultra；``3`` -> Plus；
  未知 code 或不可解析的 ``200`` 响应不产生权威档位，``tier`` 保持原值。
- ``200`` -> 凭据有效且档位权威才写入；``401`` -> 标记过期；``403`` 且疑为 key
  被拒 -> 回收 key 重试一次；``5xx``/网络故障 -> 非权威失败，不改状态。
- 邮箱回填：apps 页 HTML 的 ``WIZ_global_data`` ``"oPEP7c"`` 正则 + 噪音域排除回退。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import httpx

from aistudio_api.config import settings
from aistudio_api.domain.errors import ExitBudgetWaitError
from aistudio_api.infrastructure.account.benefit_tier import (
    parse_benefit_tier_envelope,
)
from aistudio_api.infrastructure.account.cookie_parser import (
    calculate_sapisid_hash,
    filter_minimal_cookies,
)
from aistudio_api.infrastructure.exit_pool import get_exit_resolver
from aistudio_api.infrastructure.exit_pool.resolver import (
    ExitAssignmentResolver,
    ExitDecision,
    lane_for_tier,
    resolve_credential_exit,
)
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
    AI_STUDIO_ORIGIN,
    BENEFIT_TIER_URL,
    DEFAULT_APP_ID,
    EXT_BIN,
    GRPC_WEB_USER_AGENT,
    WEB_API_KEY,
    WEB_API_KEY_PREFIX,
    WEB_API_KEY_RE,
    aistudio_app_url,
)
from aistudio_api.infrastructure.utils.common import mask_email
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )

logger = get_logger("account_refresher")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
)
ACCEPT_LANGUAGE = "en-US,en;q=0.9"
DEFAULT_REFRESH_CONCURRENCY = 3
REQUEST_TIMEOUT_SECONDS = 30.0

# External exit mode probes accounts one at a time with a tiny gap so a batch
# never bursts through the same exit (each account still resolves its own exit).
EXTERNAL_BATCH_PACING_SECONDS = 0.3

# Injected transport factory: builds the HTTP transport for one resolved proxy
# (``None`` means direct).  Tests use it to assert which proxy every bare
# credential request would use.
TransportFactory = Callable[[str | None], "HttpTransport"]
SleepFunc = Callable[[float], Awaitable[None]]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STRICT_EMAIL_RE = re.compile(
    r'"oPEP7c":"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"'
)
_NOISE_EMAIL_DOMAIN_RE = re.compile(
    r"@(?:example\.(?:com|org)|schema\.org|w3\.org|google\.com|gstatic\.com"
    r"|googleusercontent\.com)$",
    re.IGNORECASE,
)


class RefreshInProgressError(RuntimeError):
    """已有一次账号信息刷新正在进行（用于端点返回 409 互斥）。"""


# Fail-closed skip logging is throttled per (account, reason) so a sustained
# manager outage does not emit two warnings per account per refresh round.
SKIP_LOG_INTERVAL_SECONDS = 60.0
_skip_log_at: dict[tuple[str, str], float] = {}


def reset_skip_log_throttle() -> None:
    """Clear the per-account skip-log throttle (tests/teardown)."""
    _skip_log_at.clear()


def skip_log_due(account_id: str, reason: str, *, now: float | None = None) -> bool:
    """True when this (account, reason) skip may be logged now.

    Records the attempt so the caller can log exactly once per window; keepalive
    reuses the same throttle so a single skip never produces two warnings.
    """
    key = (account_id or "<none>", reason or "no_exit")
    current = time.monotonic() if now is None else now
    last = _skip_log_at.get(key, 0.0)
    if current - last < SKIP_LOG_INTERVAL_SECONDS:
        return False
    _skip_log_at[key] = current
    return True


@dataclass
class TransportResponse:
    """注入式传输的最小响应契约。"""

    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    """可注入的异步 HTTP 传输协议（测试用替身实现）。"""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse: ...


@dataclass
class RefreshResult:
    """单账号刷新结果摘要。"""

    account_id: str
    ok: bool
    tier: str | None = None
    expired: bool | None = None
    email: str | None = None
    error: str | None = None
    checked_at: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    # Exit manager returned 429 no_capacity: retry this account after the delay;
    # never a completed refresh, a skip or an account failure/cooldown.
    deferred: bool = False
    retry_after_s: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.account_id,
            "ok": self.ok,
            "tier": self.tier,
            "expired": self.expired,
            "email": self.email,
            "error": self.error,
            "checked_at": self.checked_at,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "deferred": self.deferred,
            "retry_after_s": self.retry_after_s,
        }


@dataclass
class _CookieJar:
    header: str
    jar: dict[str, str]


TIER_PROBE_FRESHNESS_SECONDS = 3 * 86400

# 单次 signer/page tier 探测的有界超时，避免一次挂起的传输拖住整轮调度。
TIER_PROBE_SIGNER_TIMEOUT_SECONDS = 30.0

# 周期 tier 探测失败退避：以 300s 为基数，连续失败 ×2（300→600→1200→2400→4800），
# 封顶 21600s（6h）。Google 对裸请求打 403 时绝不允许同一账号每约 5 分钟重撞。
TIER_PROBE_FAILURE_BASE_SECONDS = 300.0
TIER_PROBE_BACKOFF_CAP_SECONDS = 6 * 3600.0

# 池槽位提供者：返回带 ``account_id`` / ``session`` 的槽位序列（鸭子类型）。
SlotProvider = Callable[[], Sequence[object]]


class PageEvaluator(Protocol):
    """Minimal CDP page surface used by the in-page tier XHR."""

    async def evaluate(
        self,
        script: str,
        args: dict[str, object] | None = None,
        timeout_s: float = 0.0,
    ) -> object: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_checked_at(value: object) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _session_engine(session: object) -> str:
    return str(getattr(session, "engine", "") or "")


def _session_is_browserless(session: object) -> bool:
    """Browserless sessions own a signer transport and never a Chromium Page."""
    return (
        _session_engine(session) == "browserless"
        or type(session).__name__ == "BrowserlessSession"
    )


def _session_page(session: object) -> object | None:
    return getattr(session, "page", None)


def _page_is_open(page: object) -> bool:
    checker = getattr(page, "is_closed", None)
    if not callable(checker):
        return True
    try:
        return not bool(checker())
    except Exception:
        return False


def find_account_session(slots: Sequence[object], account_id: str) -> object | None:
    """Return the live pooled session bound to ``account_id`` (read-only)."""
    if not account_id:
        return None
    for slot in slots or ():
        if str(getattr(slot, "account_id", "") or "") != account_id:
            continue
        session = getattr(slot, "session", None)
        if session is None:
            continue
        checker = getattr(session, "is_alive", None)
        if callable(checker):
            try:
                if not bool(checker()):
                    continue
            except Exception:
                continue
        return session
    return None


def _probe_failure_response(summary: str) -> TransportResponse:
    """Engine-transport failure as a bounded, credential-free envelope body."""
    message = " ".join(str(summary or "").split())[:120]
    return TransportResponse(status=502, body=json.dumps([None, message]))


def _normalize_cookie_jar(cookies: _CookieJar | dict[str, str]) -> _CookieJar:
    if isinstance(cookies, _CookieJar):
        return cookies
    jar = {str(name): str(value) for name, value in cookies.items()}
    header = "; ".join(f"{name}={value}" for name, value in jar.items())
    return _CookieJar(header=header, jar=jar)


TIER_PAGE_XHR_SCRIPT = """
(opts) => {
    return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', opts.url);
        xhr.setRequestHeader('Content-Type', 'application/json+protobuf');
        xhr.setRequestHeader('X-User-Agent', opts.userAgent);
        xhr.setRequestHeader('X-Goog-Api-Key', opts.apiKey);
        xhr.setRequestHeader('X-Goog-Ext-519733851-bin', opts.extBin);
        if (opts.authUser) {
            xhr.setRequestHeader('X-Goog-AuthUser', String(opts.authUser));
        }
        if (opts.authorization) {
            xhr.setRequestHeader('Authorization', String(opts.authorization));
        }
        xhr.withCredentials = true;
        xhr.timeout = 10000;
        xhr.onload = () => resolve({status: xhr.status, text: xhr.responseText || ''});
        xhr.onerror = () => resolve({status: 0, text: 'network_error'});
        xhr.ontimeout = () => resolve({status: 0, text: 'timeout'});
        xhr.send('[]');
    });
}
"""


async def probe_tier_over_session(
    session: object | None,
    *,
    cookies: _CookieJar | dict[str, str],
    auth_user: str = "0",
    web_api_key: str | None = None,
) -> TransportResponse | None:
    """Probe ``GetAiStudioBenefitTier`` over a live engine session.

    Browserless sessions go through ``send_hooked_request`` (signer transport:
    complete cookie jar + Firefox 152 fingerprint + server-issued visit id).
    Browser sessions use an in-page XHR carrying the page's own credentials.
    ``None`` means no engine transport is usable and the caller must decide on
    its documented degraded fallback.
    """
    if session is None:
        return None
    jar = _normalize_cookie_jar(cookies)
    api_key = web_api_key or WEB_API_KEY
    if _session_is_browserless(session):
        sender = getattr(session, "send_hooked_request", None)
        if not callable(sender):
            logger.warning(
                "[Refresh] browserless session exposes no signer transport; "
                "tier probe cannot use the signer channel"
            )
            return None
        headers = {"accept": "*/*", "accept-language": ACCEPT_LANGUAGE}
        try:
            pending = sender(body="[]", url=BENEFIT_TIER_URL, headers=headers)
            status, body = await asyncio.wait_for(
                cast("Awaitable[tuple[int, bytes]]", pending),
                timeout=TIER_PROBE_SIGNER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "[Refresh] signer tier probe timed out (%.0fs)",
                TIER_PROBE_SIGNER_TIMEOUT_SECONDS,
            )
            return _probe_failure_response(
                f"signer tier probe timed out after "
                f"{TIER_PROBE_SIGNER_TIMEOUT_SECONDS:.0f}s"
            )
        except ExitBudgetWaitError:
            # 429 no_capacity from the session lease must reach the caller so it
            # can defer (retry after Retry-After), never degrade into a 502 skip.
            raise
        except Exception as exc:
            logger.warning("[Refresh] signer tier probe failed: %s", exc)
            return _probe_failure_response(f"signer tier probe failed: {exc}")
        text = (
            body.decode("utf-8", errors="replace")
            if isinstance(body, (bytes, bytearray))
            else str(body or "")
        )
        return TransportResponse(status=int(status), body=text)

    page = _session_page(session)
    if page is None or not _page_is_open(page):
        logger.warning(
            "[Refresh] tier probe session has no live page (engine=%s); "
            "falling back to raw httpx which Google may reject as "
            "region-restricted",
            _session_engine(session) or "browser",
        )
        return None
    authorization = calculate_sapisid_hash(
        filter_minimal_cookies(jar.jar), origin=AI_STUDIO_ORIGIN
    )
    evaluator = cast("PageEvaluator", page)
    try:
        result = await evaluator.evaluate(
            TIER_PAGE_XHR_SCRIPT,
            args={
                "url": BENEFIT_TIER_URL,
                "apiKey": api_key,
                "userAgent": GRPC_WEB_USER_AGENT,
                "extBin": EXT_BIN,
                "authUser": str(auth_user or "0"),
                "authorization": authorization,
            },
            timeout_s=12.0,
        )
    except Exception as exc:
        logger.warning("[Refresh] tier probe page XHR failed: %s", exc)
        return _probe_failure_response(f"tier probe page XHR failed: {exc}")
    if not isinstance(result, dict):
        return _probe_failure_response("tier probe page XHR returned non-object")
    raw_status = result.get("status")
    status = int(raw_status) if isinstance(raw_status, (int, float)) else 0
    return TransportResponse(status=status, body=str(result.get("text") or ""))


def is_verified_free_tier_fresh(account: object, *, now: float | None = None) -> bool:
    """True when a verified Free tier was checked within the last three days."""
    if str(getattr(account, "tier", "") or "") != "Free":
        return False
    checked = _parse_checked_at(getattr(account, "tier_checked_at", None))
    if checked is None:
        return False
    current = time.time() if now is None else now
    return current - checked < TIER_PROBE_FRESHNESS_SECONDS


def resolve_app_id() -> str:
    """解析 apps 页 app id：优先 AISTUDIO_APP_URL 中的 ``/apps/<id>``，否则用默认。"""
    raw = (os.getenv("AISTUDIO_APP_URL") or "").strip()
    if raw:
        match = re.search(r"/apps/([A-Za-z0-9-]+)", raw)
        if match:
            return match.group(1)
    return DEFAULT_APP_ID


class HttpxTransport:
    """默认 HTTP 传输实现（httpx）。

    ``use_global_proxy=True`` keeps the legacy behaviour (read
    ``settings.proxy_url``).  The per-account exit path always passes an explicit
    ``proxy`` with ``use_global_proxy=False`` so it can never silently fall back
    to the global proxy.
    """

    def __init__(
        self,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        proxy: str | None = None,
        use_global_proxy: bool = True,
    ) -> None:
        self._timeout = timeout
        self._proxy = settings.proxy_url if use_global_proxy else proxy

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        async with httpx.AsyncClient(
            timeout=self._timeout, proxy=self._proxy
        ) as client:
            resp = await client.request(method, url, headers=headers, content=content)
            return TransportResponse(
                status=resp.status_code,
                body=resp.text,
                headers={k.lower(): v for k, v in resp.headers.items()},
            )


class AccountInfoRefresher:
    """账号信息刷新器（tier / 过期 / 邮箱）。"""

    def __init__(
        self,
        account_store: AccountStore,
        *,
        transport: HttpTransport | None = None,
        transport_factory: TransportFactory | None = None,
        exit_resolver: ExitAssignmentResolver | None = None,
        pacing_seconds: float = EXTERNAL_BATCH_PACING_SECONDS,
        sleep: SleepFunc | None = None,
        web_api_key: str | None = None,
        concurrency: int | None = None,
        app_id: str | None = None,
        slot_provider: SlotProvider | None = None,
    ) -> None:
        self._store = account_store
        # ``transport`` is kept for callers/tests that inspect it, but the actual
        # request path always goes through ``_transport_factory``.  No transport
        # is built eagerly, so an external run never constructs one bound to the
        # global proxy.
        self._transport = transport
        self._transport_factory = self._build_transport_factory(
            transport, transport_factory
        )
        self._exit_resolver = exit_resolver
        self._pacing_seconds = max(0.0, float(pacing_seconds))
        self._sleep: SleepFunc = sleep or asyncio.sleep
        override = (web_api_key or "").strip()
        self._web_api_key = (
            override
            or (os.getenv("MAKERSUITE_WEB_API_KEY") or "").strip()
            or WEB_API_KEY
        )
        if concurrency is None:
            concurrency = int(
                getattr(settings, "account_refresh_concurrency", 0)
                or DEFAULT_REFRESH_CONCURRENCY
            )
        self._concurrency = max(1, concurrency)
        self._app_id = app_id or resolve_app_id()
        self._slot_provider: SlotProvider | None = slot_provider
        self._active_run = False
        # 仅内存的逐账号 tier 探测失败退避状态：键为账号 id，不动持久化 schema。
        self._tier_failure_counts: dict[str, int] = {}
        self._tier_retry_at: dict[str, float] = {}

    @staticmethod
    def _build_transport_factory(
        transport: HttpTransport | None,
        transport_factory: TransportFactory | None,
    ) -> TransportFactory:
        if transport_factory is not None:
            return transport_factory
        if transport is not None:
            injected = transport

            def _fixed(_proxy: str | None) -> HttpTransport:
                return injected

            return _fixed

        def _per_account(proxy: str | None) -> HttpTransport:
            return HttpxTransport(proxy=proxy, use_global_proxy=False)

        return _per_account

    def _make_transport(self, proxy: str | None) -> HttpTransport:
        return self._transport_factory(proxy)

    def _active_exit_resolver(self) -> ExitAssignmentResolver | None:
        """Injected resolver (tests) or the process-wide env-gated one."""
        if self._exit_resolver is not None:
            return self._exit_resolver
        return get_exit_resolver()

    async def _credential_exit(
        self, account_id: str, tier: object = ""
    ) -> ExitDecision:
        """Resolve the exit for one account's bare credential requests."""
        return await resolve_credential_exit(
            account_id,
            kind="chat",
            lane=lane_for_tier(tier),
            category="maintenance",
            resolver=self._active_exit_resolver(),
        )

    def _deferred_result(
        self,
        account: AccountMeta,
        decision: ExitDecision,
        checked_at: str | None,
    ) -> RefreshResult:
        reason = decision.reason or "no_capacity"
        if skip_log_due(account.id, reason):
            logger.warning(
                "[Refresh] %s tier/邮箱探测延迟（出口调用预算不足，稍后重试）"
                "reason=%s retry_after=%.0fs email=%s",
                account.id,
                reason,
                decision.retry_after_s,
                mask_email(account.email) or "-",
            )
        return RefreshResult(
            account_id=account.id,
            ok=False,
            error=None,
            checked_at=checked_at,
            deferred=True,
            retry_after_s=decision.retry_after_s,
            skip_reason=reason,
        )

    def _skipped_result(
        self,
        account: AccountMeta,
        decision: ExitDecision,
        checked_at: str | None,
    ) -> RefreshResult:
        reason = decision.reason or "no_exit"
        if skip_log_due(account.id, reason):
            logger.warning(
                "[Refresh] %s 跳过 tier/邮箱探测（无可用出口，fail-closed）"
                "reason=%s has_cookies=true email=%s",
                account.id,
                reason,
                mask_email(account.email) or "-",
            )
        return RefreshResult(
            account_id=account.id,
            ok=False,
            error=f"skipped_no_exit: {reason}",
            checked_at=checked_at,
            skipped=True,
            skip_reason=reason,
        )

    def set_slot_provider(self, provider: SlotProvider | None) -> None:
        """Attach a pool-slot provider so tier probes reuse live engine sessions."""
        self._slot_provider = provider

    def tier_failure_count(self, account_id: str) -> int:
        return self._tier_failure_counts.get(account_id, 0)

    def tier_backoff_seconds(
        self, account_id: str, *, now: float | None = None
    ) -> float:
        """Remaining scheduled-probe backoff for one account (0.0 when due)."""
        retry_at = self._tier_retry_at.get(account_id)
        if retry_at is None:
            return 0.0
        current = time.time() if now is None else now
        return max(0.0, retry_at - current)

    def _record_tier_probe(
        self, account_id: str, *, ok: bool, now: float | None = None
    ) -> None:
        if not account_id:
            return
        if ok:
            self._tier_failure_counts.pop(account_id, None)
            self._tier_retry_at.pop(account_id, None)
            return
        count = self._tier_failure_counts.get(account_id, 0) + 1
        exponent = min(count - 1, 10)
        delay = min(
            TIER_PROBE_FAILURE_BASE_SECONDS * (2**exponent),
            TIER_PROBE_BACKOFF_CAP_SECONDS,
        )
        current = time.time() if now is None else now
        self._tier_failure_counts[account_id] = count
        self._tier_retry_at[account_id] = current + delay

    def _resolve_account_session(self, account_id: str) -> object | None:
        provider = self._slot_provider
        if provider is None or not account_id:
            return None
        try:
            slots = provider()
        except Exception as exc:
            logger.debug("[Refresh] pool slot inspection failed: %s", exc)
            return None
        return find_account_session(slots, account_id)

    def is_refreshing(self) -> bool:
        return self._active_run

    @property
    def concurrency(self) -> int:
        return self._concurrency

    async def refresh_account(
        self,
        account: AccountMeta,
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
        respect_backoff: bool = False,
    ) -> RefreshResult:
        """刷新单账号：凭据有效性 + 档位（可选邮箱回填）。

        ``respect_backoff`` 仅由周期调度传入：失败探测过的账号在其指数退避窗口
        内直接跳过，不再重撞上游。手动刷新（用户主动操作）保持 ``False``。
        """
        cookies = self._load_cookies(account)
        if cookies is None:
            return RefreshResult(
                account_id=account.id,
                ok=False,
                error=(
                    f"auth.json for account {account.id} is missing or unreadable; "
                    "re-import cookies"
                ),
            )

        checked_at = _now_iso()
        if respect_backoff:
            remaining = self.tier_backoff_seconds(account.id)
            if remaining > 0:
                logger.info(
                    "[Refresh] %s tier probe skipped: failure backoff %.0fs remaining",
                    account.id,
                    remaining,
                )
                return RefreshResult(
                    account_id=account.id,
                    ok=False,
                    error=f"tier probe backoff active ({remaining:.0f}s remaining)",
                    checked_at=checked_at,
                )
        if skip_fresh_free_tier and is_verified_free_tier_fresh(account):
            email: str | None = None
            email_error: str | None = None
            email_skip_reason: str | None = None
            wants_email = refresh_email or not account.email
            if wants_email:
                decision = await self._credential_exit(account.id, account.tier)
                if decision.deferred:
                    return self._deferred_result(account, decision, checked_at)
                if decision.allowed:
                    try:
                        email = await self._fetch_account_email(
                            cookies,
                            account.email,
                            auth_user=account.auth_user,
                            proxy=decision.proxy,
                            allow_bare=True,
                        )
                    except Exception as e:
                        email_error = str(e)
                else:
                    # P2-2: a skipped email probe is NOT a completed refresh.
                    # Mark it skipped/not-ok so keepalive retries in 300s instead
                    # of pushing the next metadata refresh out to 24h.
                    email_skip_reason = decision.reason or "no_exit"
                    email_error = f"skipped_no_exit: {email_skip_reason}"
            if email:
                self._store.update_account_refresh(account.id, email=email)
            logger.info(
                "[Refresh] %s verified Free within 3 days; tier probe skipped",
                account.id,
            )
            if email_skip_reason is not None:
                if skip_log_due(account.id, email_skip_reason):
                    logger.warning(
                        "[Refresh] %s free-fresh 邮箱探测跳过（无可用出口，"
                        "fail-closed）reason=%s",
                        account.id,
                        email_skip_reason,
                    )
                return RefreshResult(
                    account_id=account.id,
                    ok=False,
                    tier=str(account.tier or "Free"),
                    expired=False,
                    email=account.email,
                    error=email_error,
                    checked_at=checked_at,
                    skipped=True,
                    skip_reason=email_skip_reason,
                )
            return RefreshResult(
                account_id=account.id,
                ok=True,
                tier=str(account.tier or "Free"),
                expired=False,
                email=email or account.email,
                error=email_error,
                checked_at=checked_at,
            )

        session = self._resolve_account_session(account.id)
        # P2-1: on a pooled/slotted session the lease is taken once by the
        # session's own ``send_hooked_request`` (maintenance).  Resolving the
        # exit here as well would charge two maintenance calls for one probe.
        # A deferred 429 still surfaces as ``ExitBudgetWaitError`` from the
        # session and is caught below.
        decision: ExitDecision | None = None
        if session is None or not _session_is_browserless(session):
            decision = await self._credential_exit(account.id, account.tier)
            if decision.deferred:
                return self._deferred_result(account, decision, checked_at)
            if not decision.allowed:
                return self._skipped_result(account, decision, checked_at)

        try:
            response = await self._post_tier_request(
                cookies,
                auth_user=account.auth_user,
                account_id=account.id,
                session=session,
                proxy=decision.proxy if decision is not None else None,
                allow_bare=decision.allowed if decision is not None else False,
            )
        except ExitBudgetWaitError as exc:
            return self._deferred_result(
                account,
                ExitDecision(
                    "defer",
                    reason=exc.code,
                    retry_after_s=float(exc.retry_after),
                    external=True,
                ),
                checked_at,
            )
        if response is None:
            if decision is None:
                decision = ExitDecision(
                    "skip", reason="probe_unavailable", external=True
                )
            return self._skipped_result(account, decision, checked_at)
        envelope = parse_benefit_tier_envelope(response.body)
        message = envelope.message
        self._record_tier_probe(
            account.id,
            ok=response.status == 200 and envelope.strict_tier() is not None,
        )

        if response.status == 200:
            tier = envelope.strict_tier()
            if tier is None:
                logger.warning(
                    "[Refresh] %s returned HTTP 200 without an authoritative "
                    "tier (account tier unchanged): code=%r message=%r",
                    account.id,
                    envelope.code,
                    message,
                )
                return RefreshResult(
                    account_id=account.id,
                    ok=False,
                    tier=None,
                    expired=None,
                    error=(
                        "probe returned HTTP 200 but no parseable canonical "
                        "tier; account tier unchanged"
                    ),
                    checked_at=checked_at,
                )
            email: str | None = None
            email_error: str | None = None
            wants_email = refresh_email or not account.email
            if wants_email:
                # The email probe is a separate credential-bearing call, so it
                # resolves/leases its own exit when the tier probe reused a
                # pooled session (``decision`` is None there).
                email_decision = decision
                if email_decision is None:
                    email_decision = await self._credential_exit(
                        account.id, account.tier
                    )
                if email_decision.deferred:
                    email_error = (
                        "deferred_no_capacity: "
                        f"{email_decision.reason or 'no_capacity'}"
                    )
                elif not email_decision.allowed:
                    email_error = (
                        f"skipped_no_exit: {email_decision.reason or 'no_exit'}"
                    )
                else:
                    try:
                        email = await self._fetch_account_email(
                            cookies,
                            account.email,
                            auth_user=account.auth_user,
                            proxy=email_decision.proxy,
                            allow_bare=email_decision.allowed,
                        )
                    except Exception as e:
                        email_error = str(e)

            if email:
                self._store.update_account_refresh(
                    account.id,
                    tier=tier,
                    tier_checked_at=checked_at,
                    expired=False,
                    email=email,
                )
            else:
                self._store.update_account_refresh(
                    account.id,
                    tier=tier,
                    tier_checked_at=checked_at,
                    expired=False,
                )
            masked = mask_email(email) if email else ""
            logger.info(
                "[Refresh] %s valid, tier=%s%s",
                account.id,
                tier,
                f", email={masked}" if masked else "",
            )
            return RefreshResult(
                account_id=account.id,
                ok=True,
                tier=tier,
                expired=False,
                email=email or account.email,
                error=email_error,
                checked_at=checked_at,
            )

        error_text = f"HTTP {response.status}{f': {message}' if message else ''}"
        if response.status == 401:
            logger.warning(
                "[Refresh] %s credentials invalid (401), marked as expired; "
                "credential expired, re-import cookies "
                "(authuser=%s, cookies=%d, sapisid=%s, authorization=%s, body=%s)",
                account.id,
                account.auth_user,
                len(cookies.jar),
                bool(
                    cookies.jar.get("SAPISID") or cookies.jar.get("__Secure-1PAPISID")
                ),
                bool(self._build_authorization(cookies.jar)),
                message or "-",
            )
            self._store.update_account_refresh(account.id, expired=True)
            return RefreshResult(
                account_id=account.id,
                ok=False,
                expired=True,
                error=error_text,
                checked_at=checked_at,
            )

        # 非权威失败（地域封锁、key 异常、上游瞬时错误）：不改动账号状态。
        logger.warning(
            "[Refresh] %s check failed (state unchanged): %s", account.id, error_text
        )
        return RefreshResult(
            account_id=account.id,
            ok=False,
            expired=None,
            error=error_text,
            checked_at=checked_at,
        )

    async def refresh_accounts(
        self,
        accounts: list[AccountMeta],
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
        respect_backoff: bool = False,
    ) -> list[RefreshResult]:
        """并发刷新一批账号（``Semaphore`` 限流，结果顺序与输入一致）。

        进程内互斥：已有刷新任务进行中时抛出 :class:`RefreshInProgressError`，
        由路由翻译为 HTTP 409。
        """
        if self._active_run:
            raise RefreshInProgressError(
                "an account info refresh run is already in progress"
            )
        if not accounts:
            return []

        self._active_run = True
        try:
            if self._active_exit_resolver() is not None:
                # External exit mode: probe accounts one at a time with a small
                # gap so a batch never bursts through the same exit.  Each call
                # still resolves the account's own exit.
                results: list[RefreshResult] = []
                for index, account in enumerate(accounts):
                    if index > 0 and self._pacing_seconds > 0:
                        await self._sleep(self._pacing_seconds)
                    results.append(
                        await self.refresh_account(
                            account,
                            refresh_email=refresh_email,
                            skip_fresh_free_tier=skip_fresh_free_tier,
                            respect_backoff=respect_backoff,
                        )
                    )
            else:
                semaphore = asyncio.Semaphore(self._concurrency)

                async def _run(account: AccountMeta) -> RefreshResult:
                    async with semaphore:
                        return await self.refresh_account(
                            account,
                            refresh_email=refresh_email,
                            skip_fresh_free_tier=skip_fresh_free_tier,
                            respect_backoff=respect_backoff,
                        )

                results = list(
                    await asyncio.gather(*(_run(account) for account in accounts))
                )
        finally:
            self._active_run = False
        duplicated = self._store.refresh_duplicate_flags()
        if duplicated:
            logger.warning(
                "[Refresh] 检测到 %d 个重复账号（同一邮箱多条），已在账号列表标记",
                duplicated,
            )
        return results

    async def refresh_tier(self, account: AccountMeta) -> dict[str, object]:
        """仅探测档位与凭据有效性并落库，返回 ``{tier, expired, error, checked_at}``。"""
        cookies = self._load_cookies(account)
        if cookies is None:
            return {
                "tier": None,
                "expired": None,
                "error": "auth.json missing or unreadable; re-import cookies",
                "checked_at": None,
            }

        checked_at = _now_iso()
        session = self._resolve_account_session(account.id)
        # P2-1: a pooled session leases this probe once through its own
        # ``send_hooked_request``; only the raw-httpx path needs a pre-lease.
        decision: ExitDecision | None = None
        if session is None or not _session_is_browserless(session):
            decision = await self._credential_exit(account.id, account.tier)
            if decision.deferred:
                reason = decision.reason or "no_capacity"
                if skip_log_due(account.id, reason):
                    logger.warning(
                        "[Refresh] %s tier probe deferred (exit budget) reason=%s "
                        "retry_after=%.0fs",
                        account.id,
                        reason,
                        decision.retry_after_s,
                    )
                return {
                    "tier": None,
                    "expired": None,
                    "error": None,
                    "checked_at": None,
                    "deferred": True,
                    "retry_after_s": decision.retry_after_s,
                    "skip_reason": reason,
                }
            if not decision.allowed:
                skip_reason = decision.reason or "no_exit"
                if skip_log_due(account.id, skip_reason):
                    logger.warning(
                        "[Refresh] %s tier probe skipped (no exit, fail-closed) "
                        "reason=%s",
                        account.id,
                        skip_reason,
                    )
                return {
                    "tier": None,
                    "expired": None,
                    "error": f"skipped_no_exit: {decision.reason or 'no_exit'}",
                    "checked_at": None,
                    "skipped": True,
                }
        try:
            response = await self._post_tier_request(
                cookies,
                auth_user=account.auth_user,
                account_id=account.id,
                session=session,
                proxy=decision.proxy if decision is not None else None,
                allow_bare=decision.allowed if decision is not None else False,
            )
        except ExitBudgetWaitError as exc:
            return {
                "tier": None,
                "expired": None,
                "error": None,
                "checked_at": None,
                "deferred": True,
                "retry_after_s": float(exc.retry_after),
                "skip_reason": exc.code,
            }
        if response is None:
            return {
                "tier": None,
                "expired": None,
                "error": (
                    f"skipped_no_exit: {decision.reason or 'no_exit'}"
                    if decision is not None
                    else "skipped_no_exit: probe_unavailable"
                ),
                "checked_at": None,
                "skipped": True,
            }
        envelope = parse_benefit_tier_envelope(response.body)
        message = envelope.message
        self._record_tier_probe(
            account.id,
            ok=response.status == 200 and envelope.strict_tier() is not None,
        )

        if response.status == 200:
            tier = envelope.strict_tier()
            if tier is None:
                logger.warning(
                    "[Refresh] %s probe returned HTTP 200 without an "
                    "authoritative tier (account tier unchanged): "
                    "code=%r message=%r",
                    account.id,
                    envelope.code,
                    message,
                )
                return {
                    "tier": None,
                    "expired": None,
                    "error": (
                        "probe returned HTTP 200 but no parseable canonical "
                        "tier; account tier unchanged"
                    ),
                    "checked_at": checked_at,
                }
            self._store.update_account_refresh(
                account.id, tier=tier, tier_checked_at=checked_at, expired=False
            )
            return {
                "tier": tier,
                "expired": False,
                "error": None,
                "checked_at": checked_at,
            }

        if response.status == 401:
            self._store.update_account_refresh(account.id, expired=True)
            return {
                "tier": None,
                "expired": True,
                "error": f"HTTP 401{f': {message}' if message else ''}",
                "checked_at": checked_at,
            }

        return {
            "tier": None,
            "expired": None,
            "error": (f"HTTP {response.status}{f': {message}' if message else ''}"),
            "checked_at": checked_at,
        }

    async def refresh_email(self, account: AccountMeta) -> str | None:
        """仅从 apps 页回填邮箱，成功则落库；失败返回 None（非权威失败）。"""
        cookies = self._load_cookies(account)
        if cookies is None:
            return None
        decision = await self._credential_exit(account.id, account.tier)
        if decision.deferred:
            reason = decision.reason or "no_capacity"
            if skip_log_due(account.id, reason):
                logger.warning(
                    "[Refresh] %s 邮箱探测延迟（出口调用预算不足，稍后重试）"
                    "reason=%s retry_after=%.0fs",
                    account.id,
                    reason,
                    decision.retry_after_s,
                )
            return None
        if not decision.allowed:
            reason = decision.reason or "no_exit"
            if skip_log_due(account.id, reason):
                logger.warning(
                    "[Refresh] %s 邮箱探测跳过（无可用出口，fail-closed）reason=%s",
                    account.id,
                    reason,
                )
            return None
        try:
            email = await self._fetch_account_email(
                cookies,
                account.email,
                auth_user=account.auth_user,
                proxy=decision.proxy,
                allow_bare=True,
            )
        except Exception as e:
            logger.warning("账号 %s 邮箱回填失败: %s", account.id, e)
            return None
        if email:
            self._store.update_account_refresh(account.id, email=email)
            logger.info("账号 %s 邮箱已回填: %s", account.id, mask_email(email))
        return email

    def _load_cookies(self, account: AccountMeta) -> _CookieJar | None:
        auth_path = self._store.get_auth_path_optional(account.id, require_exists=True)
        if auth_path is None or not auth_path.is_file():
            return None
        try:
            data = json.loads(auth_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("读取账号 %s auth.json 失败: %s", account.id, e)
            return None
        return self._collect_cookies(data.get("cookies"))

    def _collect_cookies(self, raw_cookies: object) -> _CookieJar:
        now = int(time.time())
        jar: dict[str, str] = {}
        header_parts: list[str] = []
        if isinstance(raw_cookies, list):
            for cookie in raw_cookies:
                if not isinstance(cookie, dict):
                    continue
                domain = str(cookie.get("domain") or "").lower()
                # clients6.google.com 只接受 google.com/.google.com 的 cookie；
                # accounts.google.com 等同名值覆盖会导致 401。
                if domain not in ("google.com", ".google.com"):
                    continue
                expires = cookie.get("expires")
                if isinstance(expires, (int, float)) and 0 < expires < now:
                    continue
                name = str(cookie.get("name") or "")
                value = str(cookie.get("value") or "")
                if not name:
                    continue
                jar[name] = value
                header_parts.append(f"{name}={value}")
        return _CookieJar(header="; ".join(header_parts), jar=jar)

    def _build_authorization(self, jar: dict[str, str]) -> str:
        return calculate_sapisid_hash(
            filter_minimal_cookies(jar), origin=AI_STUDIO_ORIGIN
        )

    def _rpc_headers(
        self, cookies: _CookieJar, *, auth_user: str = "0"
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            "accept": "*/*",
            "accept-language": ACCEPT_LANGUAGE,
            "content-type": "application/json+protobuf",
            "origin": AI_STUDIO_ORIGIN,
            "referer": AI_STUDIO_HOME_URL,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": USER_AGENT,
            "x-goog-api-key": self._web_api_key,
            "x-goog-authuser": str(auth_user or "0"),
            "x-goog-ext-519733851-bin": EXT_BIN,
            "x-ai-studio-visit-id": f"v1_{uuid.uuid4()}",
            "x-user-agent": GRPC_WEB_USER_AGENT,
        }
        if cookies.header:
            headers["cookie"] = cookies.header
        authorization = self._build_authorization(cookies.jar)
        if authorization:
            headers["authorization"] = authorization
        return headers

    async def _post_tier_request(
        self,
        cookies: _CookieJar,
        *,
        auth_user: str = "0",
        account_id: str = "",
        session: object | None = None,
        proxy: str | None = None,
        allow_bare: bool = True,
    ) -> TransportResponse | None:
        """Route the tier RPC by engine: signer → page XHR → raw httpx fallback.

        ``allow_bare=False`` (external mode with no account exit) forbids the
        raw httpx fallback and returns ``None`` so the caller reports a skip.
        """
        if session is None:
            session = self._resolve_account_session(account_id)
        if session is not None:
            response = await probe_tier_over_session(
                session,
                cookies=cookies,
                auth_user=auth_user,
                web_api_key=self._web_api_key,
            )
            if response is not None:
                return response
        elif account_id and self._slot_provider is not None and allow_bare:
            logger.warning(
                "[Refresh] %s tier probe has no pooled engine session; "
                "falling back to raw httpx which Google may reject as "
                "region-restricted",
                account_id,
            )
        if not allow_bare:
            return None
        return await self._post_tier_via_http(cookies, auth_user=auth_user, proxy=proxy)

    async def _post_tier_via_http(
        self, cookies: _CookieJar, *, auth_user: str = "0", proxy: str | None = None
    ) -> TransportResponse:
        transport = self._make_transport(proxy)
        headers = self._rpc_headers(cookies, auth_user=auth_user)
        response = await transport.request(
            "POST", BENEFIT_TIER_URL, headers=headers, content="[]"
        )
        if response.status == 403 and self._is_key_problem(response.body):
            rotated = await self._recover_web_api_key(
                cookies, auth_user=auth_user, proxy=proxy
            )
            if rotated and rotated != self._web_api_key:
                self._web_api_key = rotated
                headers["x-goog-api-key"] = rotated
                logger.warning(
                    "[Refresh] Web API key was rejected; retrying with a "
                    "re-extracted key."
                )
                response = await transport.request(
                    "POST", BENEFIT_TIER_URL, headers=headers, content="[]"
                )
        return response

    @staticmethod
    def _is_key_problem(body: str) -> bool:
        text = str(body or "")
        return "API_KEY_SERVICE_BLOCKED" in text or "unregistered callers" in text

    async def _recover_web_api_key(
        self,
        cookies: _CookieJar,
        *,
        auth_user: str = "0",
        proxy: str | None = None,
    ) -> str | None:
        """从 apps 页 HTML 回收 web API key（只返回，不在此处改写当前 key）。"""
        try:
            html = await self._fetch_app_html(
                cookies, auth_user=auth_user, proxy=proxy, allow_bare=True
            )
        except Exception as e:
            logger.warning("[Refresh] Unable to re-extract the web API key: %s", e)
            return None
        keys = list(dict.fromkeys(WEB_API_KEY_RE.findall(html)))
        if not keys:
            return None
        return next(
            (
                candidate
                for candidate in keys
                if candidate.startswith(WEB_API_KEY_PREFIX)
            ),
            keys[0],
        )

    async def _fetch_app_html(
        self,
        cookies: _CookieJar,
        *,
        auth_user: str = "0",
        proxy: str | None = None,
        allow_bare: bool = True,
    ) -> str:
        """裸 httpx 路径抓取 AI Studio apps 页 HTML。

        UA / accept-language 已与全站一致的 Firefox 152 身份对齐（``USER_AGENT``
        / ``ACCEPT_LANGUAGE``），但 TLS 指纹仍是 Python httpx，风控残留已知；
        迁移依赖 signer GET 页面通道（留档，不在本批范围）。

        ``allow_bare=False`` forbids the request entirely (external mode with no
        account exit) so this credential path never falls back to the global
        proxy.
        """
        if not allow_bare:
            raise RuntimeError("skipped_no_exit")
        headers = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "accept-language": ACCEPT_LANGUAGE,
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "user-agent": USER_AGENT,
            "x-goog-authuser": str(auth_user or "0"),
        }
        if cookies.header:
            headers["cookie"] = cookies.header
        transport = self._make_transport(proxy)
        url = aistudio_app_url(self._app_id, auth_user=auth_user)
        response = await transport.request("GET", url, headers=headers)
        if response.status != 200:
            raise RuntimeError(f"page fetch HTTP {response.status}")
        return response.body

    async def _fetch_account_email(
        self,
        cookies: _CookieJar,
        existing_name: str | None,
        *,
        auth_user: str = "0",
        proxy: str | None = None,
        allow_bare: bool = True,
    ) -> str | None:
        if not allow_bare:
            raise RuntimeError("skipped_no_exit")
        html = await self._fetch_app_html(
            cookies, auth_user=auth_user, proxy=proxy, allow_bare=True
        )
        # WIZ_global_data 的 "oPEP7c" 字段携带已登录账号邮箱。
        config_match = _STRICT_EMAIL_RE.search(html)
        if config_match:
            return config_match.group(1)
        found = list(dict.fromkeys(_EMAIL_RE.findall(html)))
        found = [email for email in found if not _NOISE_EMAIL_DOMAIN_RE.search(email)]
        if not found:
            return None
        if existing_name:
            target = existing_name.lower()
            exact = next((email for email in found if email.lower() == target), None)
            if exact:
                return exact
        return found[0]


__all__ = [
    "EXTERNAL_BATCH_PACING_SECONDS",
    "TIER_PROBE_BACKOFF_CAP_SECONDS",
    "TIER_PROBE_FAILURE_BASE_SECONDS",
    "TIER_PROBE_FRESHNESS_SECONDS",
    "TIER_PROBE_SIGNER_TIMEOUT_SECONDS",
    "AccountInfoRefresher",
    "HttpTransport",
    "HttpxTransport",
    "RefreshInProgressError",
    "RefreshResult",
    "SleepFunc",
    "SlotProvider",
    "TransportFactory",
    "TransportResponse",
    "find_account_session",
    "probe_tier_over_session",
    "resolve_app_id",
]
