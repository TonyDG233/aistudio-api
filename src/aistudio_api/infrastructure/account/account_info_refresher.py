"""账号信息刷新：无浏览器直连探测 AI Studio 档位（tier）与登录态，并回填邮箱。

逐字对齐 Ark_AIStudioToAPI ``src/core/AccountInfoRefresher.js``（513 行）的传输与判定：
- 探测 RPC：``POST .../MakerSuiteService/GetAiStudioBenefitTier``，body ``"[]"``，
  ``content-type: application/json+protobuf``，携带账号 Cookie、SAPISIDHASH
  Authorization（复用本仓 :func:`calculate_sapisid_hash`）与 AI Studio web API key。
- 档位映射：``[]``/``0`` -> Free；``1`` -> Pro；``2`` -> Ultra；``3`` -> Plus；
  其他 -> ``unknown-N``。
- ``200`` -> 凭据有效；``401`` -> 标记过期；``403`` 且疑为 key 被拒 -> 回收 key
  重试一次；``5xx``/网络故障 -> 非权威失败，不改状态。
- 邮箱回填：apps 页 HTML 的 ``WIZ_global_data`` ``"oPEP7c"`` 正则 + 噪音域排除回退。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import httpx

from aistudio_api.config import settings
from aistudio_api.infrastructure.account.cookie_parser import calculate_sapisid_hash
from aistudio_api.infrastructure.utils.common import mask_email
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.account.account_store import (
        AccountMeta,
        AccountStore,
    )

logger = get_logger("account_refresher")

RPC_HOST = "alkalimakersuite-pa.clients6.google.com"
RPC_BASE = "/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/"
RPC_URL = f"https://{RPC_HOST}{RPC_BASE}GetAiStudioBenefitTier"
PAGE_HOST = "aistudio.google.com"
ORIGIN = "https://aistudio.google.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
)
TIER_BY_CODE: dict[int, str] = {1: "Pro", 2: "Ultra", 3: "Plus"}
# 与 Ark 同款的公网 AI Studio web API key（可用 MAKERSUITE_WEB_API_KEY 覆盖）。
DEFAULT_WEB_API_KEY = "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs"
# Ark ConfigLoader 默认 app id，用于 apps 页取 HTML（邮箱回填 / key 回收）。
DEFAULT_APP_ID = "3b85b4ed-f945-422c-ba25-395d1feef528"
DEFAULT_REFRESH_CONCURRENCY = 3
REQUEST_TIMEOUT_SECONDS = 30.0

_GOOGLE_DOMAIN_RE = re.compile(r"(^|\.)google\.com$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STRICT_EMAIL_RE = re.compile(
    r'"oPEP7c":"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"'
)
_NOISE_EMAIL_DOMAIN_RE = re.compile(
    r"@(?:example\.(?:com|org)|schema\.org|w3\.org|google\.com|gstatic\.com"
    r"|googleusercontent\.com)$",
    re.IGNORECASE,
)
_WEB_API_KEY_RE = re.compile(r"AIzaSy[A-Za-z0-9_-]{25,45}")


class RefreshInProgressError(RuntimeError):
    """已有一次账号信息刷新正在进行（用于端点返回 409 互斥）。"""


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

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.account_id,
            "ok": self.ok,
            "tier": self.tier,
            "expired": self.expired,
            "email": self.email,
            "error": self.error,
            "checked_at": self.checked_at,
        }


@dataclass
class _CookieJar:
    header: str
    jar: dict[str, str]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def resolve_app_id() -> str:
    """解析 apps 页 app id：优先 AISTUDIO_APP_URL 中的 ``/apps/<id>``，否则用默认。"""
    raw = (os.getenv("AISTUDIO_APP_URL") or "").strip()
    if raw:
        match = re.search(r"/apps/([A-Za-z0-9-]+)", raw)
        if match:
            return match.group(1)
    return DEFAULT_APP_ID


class HttpxTransport:
    """默认 HTTP 传输实现（httpx，尊重 ``settings.proxy_url``）。

    每次请求临时建立客户端连接，避免长生命周期客户端的关闭问题；刷新为低频操作，
    连接复用收益有限。测试通过注入替身传输完全绕过网络。
    """

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._proxy = settings.proxy_url

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
    """账号信息刷新器（tier / 过期 / 邮箱），对齐 Ark AccountInfoRefresher 行为。"""

    def __init__(
        self,
        account_store: AccountStore,
        *,
        transport: HttpTransport | None = None,
        web_api_key: str | None = None,
        concurrency: int | None = None,
        app_id: str | None = None,
    ) -> None:
        self._store = account_store
        self._transport: HttpTransport = transport or HttpxTransport()
        override = (web_api_key or "").strip()
        self._web_api_key = (
            override
            or (os.getenv("MAKERSUITE_WEB_API_KEY") or "").strip()
            or DEFAULT_WEB_API_KEY
        )
        if concurrency is None:
            concurrency = int(
                getattr(settings, "account_refresh_concurrency", 0)
                or DEFAULT_REFRESH_CONCURRENCY
            )
        self._concurrency = max(1, concurrency)
        self._app_id = app_id or resolve_app_id()
        self._active_run = False

    def is_refreshing(self) -> bool:
        return self._active_run

    @property
    def concurrency(self) -> int:
        return self._concurrency

    async def refresh_account(
        self, account: AccountMeta, *, refresh_email: bool = True
    ) -> RefreshResult:
        """刷新单账号：凭据有效性 + 档位（可选邮箱回填），并对齐 Ark 的落库语义。"""
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
        response = await self._post_tier_request(cookies)
        envelope = self._parse_envelope(response.body)
        message = self._envelope_message(envelope)

        if response.status == 200:
            tier = self._tier_name(self._tier_code_from_envelope(envelope))
            email: str | None = None
            email_error: str | None = None
            wants_email = refresh_email or not account.email
            if wants_email:
                try:
                    email = await self._fetch_account_email(cookies, account.email)
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
            self._store.update_account_refresh(account.id, expired=True)
            logger.warning(
                "[Refresh] %s credentials invalid (401), marked as expired; "
                "credential expired, re-import cookies",
                account.id,
            )
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
        semaphore = asyncio.Semaphore(self._concurrency)

        async def _run(account: AccountMeta) -> RefreshResult:
            async with semaphore:
                return await self.refresh_account(account, refresh_email=refresh_email)

        try:
            results = await asyncio.gather(*(_run(account) for account in accounts))
        finally:
            self._active_run = False
        duplicated = self._store.refresh_duplicate_flags()
        if duplicated:
            logger.warning(
                "[Refresh] 检测到 %d 个重复账号（同一邮箱多条），已在账号列表标记",
                duplicated,
            )
        return list(results)

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
        response = await self._post_tier_request(cookies)
        envelope = self._parse_envelope(response.body)
        message = self._envelope_message(envelope)

        if response.status == 200:
            tier = self._tier_name(self._tier_code_from_envelope(envelope))
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
        try:
            email = await self._fetch_account_email(cookies, account.email)
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
                domain = str(cookie.get("domain") or "")
                if not _GOOGLE_DOMAIN_RE.search(domain):
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
        return calculate_sapisid_hash(jar, origin=ORIGIN)

    def _rpc_headers(self, cookies: _CookieJar) -> dict[str, str]:
        headers: dict[str, str] = {
            "accept": "*/*",
            "content-type": "application/json+protobuf",
            "origin": ORIGIN,
            "referer": f"{ORIGIN}/",
            "user-agent": USER_AGENT,
            "x-goog-api-key": self._web_api_key,
            "x-goog-authuser": "0",
            "x-user-agent": "grpc-web-javascript/0.1",
        }
        if cookies.header:
            headers["cookie"] = cookies.header
        authorization = self._build_authorization(cookies.jar)
        if authorization:
            headers["authorization"] = authorization
        return headers

    async def _post_tier_request(self, cookies: _CookieJar) -> TransportResponse:
        headers = self._rpc_headers(cookies)
        response = await self._transport.request(
            "POST", RPC_URL, headers=headers, content="[]"
        )
        if response.status == 403 and self._is_key_problem(response.body):
            rotated = await self._recover_web_api_key(cookies)
            if rotated and rotated != self._web_api_key:
                self._web_api_key = rotated
                headers["x-goog-api-key"] = rotated
                logger.warning(
                    "[Refresh] Web API key was rejected; retrying with a "
                    "re-extracted key."
                )
                response = await self._transport.request(
                    "POST", RPC_URL, headers=headers, content="[]"
                )
        return response

    @staticmethod
    def _is_key_problem(body: str) -> bool:
        text = str(body or "")
        return "API_KEY_SERVICE_BLOCKED" in text or "unregistered callers" in text

    async def _recover_web_api_key(self, cookies: _CookieJar) -> str | None:
        """从 apps 页 HTML 回收 web API key（只返回，不在此处改写当前 key）。

        注意：Ark 原实现会在回收时立即写入 ``this.webApiKey``，导致其
        ``rotatedKey !== this.webApiKey`` 判定恒为假、重试分支成为死代码。此处按
        WP3 规格修正为「回收的 key 与当前 key 不同才重试一次」，由调用方写回。
        """
        try:
            html = await self._fetch_app_html(cookies)
        except Exception as e:
            logger.warning("[Refresh] Unable to re-extract the web API key: %s", e)
            return None
        keys = list(dict.fromkeys(_WEB_API_KEY_RE.findall(html)))
        if not keys:
            return None
        return next(
            (candidate for candidate in keys if candidate.startswith("AIzaSyDdP8")),
            keys[0],
        )

    async def _fetch_app_html(self, cookies: _CookieJar) -> str:
        headers = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "user-agent": USER_AGENT,
        }
        if cookies.header:
            headers["cookie"] = cookies.header
        url = f"https://{PAGE_HOST}/apps/{self._app_id}"
        response = await self._transport.request("GET", url, headers=headers)
        if response.status != 200:
            raise RuntimeError(f"page fetch HTTP {response.status}")
        return response.body

    async def _fetch_account_email(
        self, cookies: _CookieJar, existing_name: str | None
    ) -> str | None:
        html = await self._fetch_app_html(cookies)
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

    @staticmethod
    def _parse_envelope(body: str) -> list[object] | None:
        try:
            parsed = json.loads(str(body or "").strip())
        except Exception:
            return None
        return parsed if isinstance(parsed, list) else None

    @staticmethod
    def _tier_code_from_envelope(envelope: list[object] | None) -> int:
        if not envelope:
            return 0
        first = envelope[0]
        return first if isinstance(first, int) else 0

    @staticmethod
    def _tier_name(code: int) -> str:
        return TIER_BY_CODE.get(code) or ("Free" if code == 0 else f"unknown-{code}")

    @staticmethod
    def _envelope_message(envelope: list[object] | None) -> str:
        if envelope and len(envelope) >= 2 and isinstance(envelope[1], str):
            return envelope[1][:300]
        return ""


__all__ = [
    "AccountInfoRefresher",
    "HttpTransport",
    "HttpxTransport",
    "RefreshInProgressError",
    "RefreshResult",
    "TransportResponse",
    "resolve_app_id",
]
