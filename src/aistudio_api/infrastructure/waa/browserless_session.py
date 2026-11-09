"""Browserless WAA request session.

One instance owns exactly one cookie jar, one deterministic fingerprint record,
one proof minter state and one transport path.  Nothing is shared between slots:
no global headers, no global session, no module-level cookie cache.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from aistudio_api.config import settings
from aistudio_api.domain.errors import (
    AuthError,
    BrowserTransportError,
    SessionExpiredError,
)
from aistudio_api.domain.model_access import tier_header_value
from aistudio_api.infrastructure.account.account_store import write_storage_state
from aistudio_api.infrastructure.account.cookie_parser import (
    calculate_sapisid_hash,
    filter_minimal_cookies,
)
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.public_web_constants import (
    AI_STUDIO_HOME_URL,
    DEFAULT_CHAT_MODEL,
    GENERATE_CONTENT_URL,
    GRPC_WEB_USER_AGENT,
    WEB_API_KEY,
)
from aistudio_api.infrastructure.gateway.session import build_snapshot_hash_parts
from aistudio_api.infrastructure.gateway.session_state import (
    SessionContext,
    should_warm_snapshot,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger
from aistudio_api.infrastructure.waa.signer_client import (
    MintProofResult,
    SignerAuthError,
    SignerClient,
    SignerError,
)

log = get_logger("browserless-session")

ENGINE_NAME = "browserless"

_BASE_HEADERS: dict[str, str] = {
    "Content-Type": "application/json+protobuf",
    "X-User-Agent": GRPC_WEB_USER_AGENT,
    "X-Goog-Api-Key": WEB_API_KEY,
    "X-Goog-AuthUser": "0",
    "Referer": AI_STUDIO_HOME_URL,
}

_COOKIE_DROP_HEADERS = frozenset({"host", "content-length", "cookie", "authorization"})
_ACCOUNT_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _map_signer_auth_error(exc: SignerAuthError) -> AuthError:
    """Translate a signer login-state denial into a routing-visible error.

    The original signer status and bounded server reason are preserved in the
    message so routing logs/rotator events stay actionable. 401 is a definite
    expired session; a 403/WAA denial keeps the first-attempt same-account
    re-sign path.
    """
    reason = exc.reason or str(exc)
    message = (
        f"browserless 登录态失效 (status={exc.status_code or 'unknown'}): {reason}"
    )
    if exc.status_code == 401:
        return SessionExpiredError(message)
    return AuthError(message)


def _base_template(model: str = DEFAULT_CHAT_MODEL) -> dict[str, object]:
    normalized = model if model.startswith("models/") else f"models/{model}"
    return {
        "url": GENERATE_CONTENT_URL,
        "headers": dict(_BASE_HEADERS),
        "body": f'["{normalized}"]',
    }


def _cookie_map(cookies: list[dict[str, object]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        if name:
            result[name] = str(cookie.get("value") or "")
    return result


def _cookie_key(cookie: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(cookie.get("name") or "").lower(),
        str(cookie.get("domain") or "").lower(),
        str(cookie.get("path") or "/"),
    )


def _is_cookie_tombstone(cookie: dict[str, object]) -> bool:
    """True for WAA deletion tombstones.

    The Go signer encodes deletions inside ``X-WAA-Set-Cookies`` as
    ``value=""`` and/or ``expires=0``.  ``expires=-1`` is a Playwright session
    cookie, not a deletion, so negative expiry must never tombstone a cookie.
    """
    value = cookie.get("value")
    if value is None or (isinstance(value, str) and value == ""):
        return True
    raw_expires = cookie.get("expires")
    if isinstance(raw_expires, bool) or not isinstance(raw_expires, (int, float)):
        return False
    return float(raw_expires) == 0.0


def _split_cookie_updates(
    set_cookies: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    updates: list[dict[str, object]] = []
    tombstones: list[dict[str, object]] = []
    for cookie in set_cookies:
        if _is_cookie_tombstone(cookie):
            tombstones.append(cookie)
        else:
            updates.append(cookie)
    return updates, tombstones


def _cookie_expired(cookie: dict[str, object], now: float) -> bool:
    raw = cookie.get("expires")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return False
    # Playwright storage-state uses -1 for session cookies.
    return float(raw) > 0 and float(raw) <= now


def _merge_cookies(
    current: list[dict[str, object]], updates: list[dict[str, object]]
) -> list[dict[str, object]]:
    if not updates:
        return current
    merged = [dict(item) for item in current]
    index = {
        (
            str(item.get("name") or "").lower(),
            str(item.get("domain") or "").lower(),
            str(item.get("path") or "/"),
        ): position
        for position, item in enumerate(merged)
    }
    for update in updates:
        key = (
            str(update.get("name") or "").lower(),
            str(update.get("domain") or "").lower(),
            str(update.get("path") or "/"),
        )
        position = index.get(key)
        if position is None:
            index[key] = len(merged)
            merged.append(dict(update))
        else:
            merged[position] = dict(update)
    return merged


class BrowserlessSession:
    """``EngineSession`` implementation backed by waa-signer (no Chromium)."""

    engine = ENGINE_NAME

    def __init__(
        self,
        *,
        signer: SignerClient,
        auth_file: str | None,
        auth_user: str = "0",
        tier: str = "Free",
        account_id: str = "",
        realm_id: str = "",
        transport_mode: str = "signer_rpc",
        proxy: str | None = None,
        locale: str = "en-US",
        timezone: str = "America/Los_Angeles",
        http2: bool = True,
        read_timeout_s: float = 120.0,
        direct_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._signer = signer
        self._auth_file = auth_file
        self._auth_user = str(auth_user or "0")
        self._tier = str(tier or "Free")
        self._account_id = account_id
        self._realm_id = realm_id or account_id or "browserless"
        self._transport_mode = (
            transport_mode
            if transport_mode in ("signer_rpc", "direct_http")
            else "signer_rpc"
        )
        self._proxy = proxy
        self._locale = locale
        self._timezone = timezone
        self._http2 = bool(http2)
        self._read_timeout_s = max(1.0, float(read_timeout_s or 120.0))
        self._current_model: str = DEFAULT_CHAT_MODEL
        self._cookies: list[dict[str, object]] = []
        self._origins: list[dict[str, object]] = []
        self._mint: MintProofResult | None = None
        self._mint_updates: list[dict[str, object]] = []
        self._rpc_set_cookies: list[dict[str, object]] = []
        self._cookie_dirty = False
        self._rpc_account_key = ""
        self._last_rpc_at: float | None = None
        self._direct_http = direct_http_client
        self._owns_direct_http = direct_http_client is None
        self._snapshot_lock = asyncio.Lock()
        self._last_snapshot_at: float | None = None
        self._force_snapshot_warmup = False
        self.last_snapshot_warmup = False
        self.last_proof_latency_ms: float | None = None
        self._in_flight = 0
        self._closed = False
        self._storage_state: dict[str, object] = {
            "cookies": [],
            "origins": [],
        }
        self._context = SessionContext(
            page=None,
            snap_key="waa",
            auth_user=self._auth_user,
            auth_file=auth_file,
            base_template=_base_template(),
            tier=self._tier,
        )
        self._capture_service = RequestCaptureService(self)
        self._load_storage_state()

    # ----------------------------------------------------------- introspection

    @property
    def capture_service(self) -> RequestCaptureService:
        return self._capture_service

    @property
    def active_context(self) -> SessionContext:
        return self._context

    @property
    def page(self) -> None:
        return None

    @property
    def in_flight_count(self) -> int:
        return self._in_flight

    @property
    def account_key(self) -> str:
        if self._mint is not None and self._mint.account_key:
            return self._mint.account_key
        return self._rpc_account_key

    @property
    def fingerprint(self) -> dict[str, object]:
        # The signer's Firefox 152 profile is authoritative; browserless never
        # persists a local fingerprint JSON shell.
        return {
            "profile": "Firefox 152",
            "engine": "waa",
            "summary": "Firefox 152 (WAA)",
        }

    @property
    def stable_account_key(self) -> str:
        """Go-contract safe account key (``[A-Za-z0-9][A-Za-z0-9._-]{0,127}``).

        Routes may use ids such as ``a@b`` or ``.foo``; those cannot be sent to
        the signer verbatim.  Invalid ids hash deterministically so mint, rpc,
        release and close always agree on the same key.
        """
        if not self._account_id:
            return sha256(self._cookie_text().encode("utf-8")).hexdigest()
        if _ACCOUNT_KEY_RE.fullmatch(self._account_id):
            return self._account_id
        return sha256(self._account_id.encode("utf-8")).hexdigest()

    @property
    def transport_state(self) -> str:
        return self._transport_mode

    def is_alive(self) -> bool:
        if self._closed:
            return False
        return self._has_sapisid()

    def get_current_auth_user(self) -> str:
        return self._auth_user

    def get_current_tier(self) -> str:
        return self._tier

    # -------------------------------------------------------------- lifecycle

    def _load_storage_state(self) -> None:
        if not self._auth_file:
            return
        path = Path(self._auth_file)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        self._storage_state = data
        raw_cookies = data.get("cookies")
        self._cookies = (
            [dict(item) for item in raw_cookies if isinstance(item, dict)]
            if isinstance(raw_cookies, list)
            else []
        )
        raw_origins = data.get("origins")
        self._origins = (
            [dict(item) for item in raw_origins if isinstance(item, dict)]
            if isinstance(raw_origins, list)
            else []
        )
        now = time.time()
        self._cookies = [
            cookie for cookie in self._cookies if not _cookie_expired(cookie, now)
        ]

    def _cookie_text(self) -> str:
        return json.dumps(
            {"cookies": self._cookies, "origins": self._origins},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _has_sapisid(self) -> bool:
        return any(
            "SAPISID" in str(cookie.get("name") or "") for cookie in self._cookies
        )

    def _delete_cookies(self, deleted: list[dict[str, object]]) -> None:
        if not deleted:
            return
        keys = {_cookie_key(cookie) for cookie in deleted}
        self._cookies = [
            cookie for cookie in self._cookies if _cookie_key(cookie) not in keys
        ]
        # A deletion must be persisted even if the cookie was already absent.
        self._cookie_dirty = True

    def _apply_cookie_payload(
        self,
        set_cookies: list[dict[str, object]],
        deleted_cookies: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        """Apply tombstones + explicit deletions, return only live updates."""
        updates, tombstones = _split_cookie_updates(set_cookies)
        deletions = [*list(deleted_cookies or []), *tombstones]
        if deletions:
            self._delete_cookies(deletions)
            self._prune_pending_updates(deletions)
        if updates:
            self._cookie_dirty = True
        return updates

    def _prune_pending_updates(self, deletions: list[dict[str, object]]) -> None:
        if not deletions:
            return
        keys = {_cookie_key(cookie) for cookie in deletions}
        self._mint_updates = [
            cookie for cookie in self._mint_updates if _cookie_key(cookie) not in keys
        ]
        self._rpc_set_cookies = [
            cookie
            for cookie in self._rpc_set_cookies
            if _cookie_key(cookie) not in keys
        ]

    # --------------------------------------------------------- EngineSession

    @asynccontextmanager
    async def request_scope(self):
        self._in_flight += 1
        self._context.incr_in_flight()
        try:
            yield self._context
        finally:
            self._in_flight = max(0, self._in_flight - 1)
            self._context.decr_in_flight()

    async def ensure_ready(self, timeout_s: float = 120.0) -> SessionContext:
        del timeout_s
        if self._closed:
            raise RuntimeError(f"browserless session {self._realm_id} is closed")
        if not self._has_sapisid():
            raise RuntimeError(
                f"browserless session {self._realm_id} has no SAPISID cookie"
            )
        return self._context

    async def capture_template(self, model: str) -> dict[str, object]:
        self._current_model = str(model or DEFAULT_CHAT_MODEL)
        return _base_template(model)

    async def generate_snapshot(
        self, contents: list[AistudioContent], *, payload_size: int | None = None
    ) -> str:
        prompt = " ".join(build_snapshot_hash_parts(contents))
        content_hash = sha256(prompt.encode("utf-8")).hexdigest()
        page_prompt = _last_text_part(contents) or prompt
        async with self._snapshot_lock:
            now = time.monotonic()
            warmup = should_warm_snapshot(
                last_snapshot_at=self._last_snapshot_at,
                now=now,
                prompt_bytes=len(prompt.encode("utf-8")),
                payload_bytes=payload_size or 0,
                force=self._force_snapshot_warmup,
            )
            try:
                result = await self._signer.mint_proof(
                    cookies=self._cookie_text(),
                    auth_user=self._auth_user,
                    g1_tier=tier_header_value(self._tier) or "",
                    model=self._current_model or DEFAULT_CHAT_MODEL,
                    prompt=prompt,
                    page_prompt=page_prompt,
                    digest=content_hash,
                    wire_contents=[content.to_wire() for content in contents],
                    proxy=self._proxy,
                    account_key=self.stable_account_key,
                )
            except SignerAuthError as exc:
                raise _map_signer_auth_error(exc) from exc
            except SignerError as exc:
                raise RuntimeError(f"browserless proof mint failed: {exc}") from exc
            if not result.proof:
                raise RuntimeError("browserless proof mint returned empty proof")
            self._mint = result
            mint_updates = self._apply_cookie_payload(
                result.set_cookies, result.deleted_cookies
            )
            self._mint_updates = _merge_cookies(self._mint_updates, mint_updates)
            self._cookies = _merge_cookies(self._cookies, mint_updates)
            if result.truncated:
                log.warning(
                    "browserless mint Set-Cookie 已截断，realm=%s（仅应用前 %d 项）",
                    self._realm_id,
                    len(result.set_cookies),
                )
            self._last_snapshot_at = time.monotonic()
            self._force_snapshot_warmup = False
            self.last_snapshot_warmup = warmup
            self.last_proof_latency_ms = result.latency_ms
            proof = result.proof
            return proof if proof.startswith("!") else f"!{proof}"

    async def refresh_auth_state(self) -> bool:
        """Reload auth.json, drop minter/transport state, then re-mint."""
        self._force_snapshot_warmup = True
        self._mint = None
        self._mint_updates = []
        self._rpc_set_cookies = []
        self._cookie_dirty = False
        self._rpc_account_key = ""
        self._load_storage_state()
        if not self._has_sapisid():
            log.warning(
                "browserless 刷新鉴权失败: realm=%s 缺少 SAPISID", self._realm_id
            )
            return False
        try:
            result = await self._signer.mint_proof(
                cookies=self._cookie_text(),
                auth_user=self._auth_user,
                g1_tier=tier_header_value(self._tier) or "",
                prompt="",
                page_prompt="",
                digest=sha256(b"").hexdigest(),
                proxy=self._proxy,
                account_key=self.stable_account_key,
            )
        except SignerError as exc:
            log.warning("browserless 重新 mint 失败: realm=%s: %s", self._realm_id, exc)
            return False
        if not result.ok:
            return False
        self._mint = result
        refresh_updates = self._apply_cookie_payload(
            result.set_cookies, result.deleted_cookies
        )
        self._mint_updates = _merge_cookies(self._mint_updates, refresh_updates)
        self._cookies = _merge_cookies(self._cookies, refresh_updates)
        return True

    async def send_hooked_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        ctx = await self.ensure_ready()
        target_url = url or str(ctx.base_template.get("url") or "")
        merged = self._merged_headers(headers)
        if self._transport_mode == "signer_rpc":
            try:
                result = await self._signer.send_rpc(
                    cookies=self._cookie_text(),
                    body=body,
                    url=target_url,
                    headers=merged,
                    auth_user=self._auth_user,
                    g1_tier=tier_header_value(self._tier) or "",
                    page_prompt=_first_text(body),
                    proxy=self._proxy,
                    account_key=self.stable_account_key,
                )
            except SignerAuthError as exc:
                raise _map_signer_auth_error(exc) from exc
            except SignerError as exc:
                raise BrowserTransportError(f"browserless rpc failed: {exc}") from exc
            self._apply_rpc_metadata(
                result.account_key,
                result.set_cookies,
                result.truncated,
                result.deleted_cookies,
            )
            return result.status, result.body
        return await self._direct_http_request(target_url, merged, body)

    async def send_streaming_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        ctx = await self.ensure_ready()
        target_url = url or str(ctx.base_template.get("url") or "")
        merged = self._merged_headers(headers)
        if self._transport_mode == "signer_rpc":
            try:
                async for event in self._signer.stream_rpc(
                    cookies=self._cookie_text(),
                    body=body,
                    url=target_url,
                    headers=merged,
                    auth_user=self._auth_user,
                    g1_tier=tier_header_value(self._tier) or "",
                    page_prompt=_first_text(body),
                    proxy=self._proxy,
                    account_key=self.stable_account_key,
                    on_metadata=self._apply_rpc_metadata,
                ):
                    yield event
            except SignerAuthError as exc:
                raise _map_signer_auth_error(exc) from exc
            except SignerError as exc:
                raise BrowserTransportError(
                    f"browserless rpc stream failed: {exc}"
                ) from exc
            return

        async for event in self._direct_http_stream(target_url, merged, body):
            yield event

    async def save_current_cookies(self) -> bool:
        if not self._auth_file:
            return False
        updates: list[dict[str, object]] = [
            *self._mint_updates,
            *self._rpc_set_cookies,
        ]
        if not updates and not self._cookie_dirty:
            return False
        self._cookies = _merge_cookies(self._cookies, updates)
        try:
            write_storage_state(
                self._auth_file,
                cookies=self._cookies,
                origins=self._origins,
            )
        except OSError as exc:
            log.warning("browserless cookie 回写失败 realm=%s: %s", self._realm_id, exc)
            return False
        self._cookie_dirty = False
        return True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._capture_service.clear_templates(invalidate_base=True)
        with suppress(Exception):
            await self.save_current_cookies()
        key = self.stable_account_key
        if key:
            with suppress(Exception):
                await self._signer.release_worker(account_key=key)
        if self._owns_direct_http and self._direct_http is not None:
            with suppress(Exception):
                await self._direct_http.aclose()
            self._direct_http = None

    # ---------------------------------------------------------------- helpers

    def _merged_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        base = dict(_BASE_HEADERS)
        base["X-Goog-AuthUser"] = self._auth_user
        tier_header = tier_header_value(self._tier)
        if tier_header:
            base["X-AIStudio-G1-Tier"] = tier_header
        for key, value in (headers or {}).items():
            if key.lower() in _COOKIE_DROP_HEADERS:
                continue
            base[key] = value
        # The proof is minted against the exact UA / visit-id / ext-bin headers
        # returned by the signer; they must win over the static template or the
        # upstream WAA check will reject the mismatched request.
        if self._mint is not None:
            for key, value in self._mint.headers.items():
                if key.lower() in _COOKIE_DROP_HEADERS:
                    continue
                base[key] = value
        return base

    def _apply_rpc_metadata(
        self,
        account_key: str,
        set_cookies: list[dict[str, object]],
        truncated: bool = False,
        deleted_cookies: list[dict[str, object]] | None = None,
    ) -> None:
        if account_key:
            self._rpc_account_key = account_key
        updates = self._apply_cookie_payload(
            list(set_cookies), list(deleted_cookies or [])
        )
        if updates:
            self._rpc_set_cookies = _merge_cookies(self._rpc_set_cookies, updates)
            self._cookies = _merge_cookies(self._cookies, updates)
        if truncated:
            log.warning(
                "browserless rpc Set-Cookie 已截断，realm=%s（仅应用前 %d 项）",
                self._realm_id,
                len(set_cookies),
            )
        self._last_rpc_at = time.monotonic()

    def _direct_headers(self, headers: dict[str, str]) -> dict[str, str]:
        cookie_map = _cookie_map(self._cookies)
        minimal = filter_minimal_cookies(cookie_map)
        result = {
            key: value
            for key, value in headers.items()
            if key.lower() not in _COOKIE_DROP_HEADERS
        }
        if minimal:
            result["Cookie"] = "; ".join(f"{k}={v}" for k, v in minimal.items())
            auth = calculate_sapisid_hash(minimal)
            if auth:
                result["Authorization"] = auth
        return result

    async def _get_direct_http(self) -> httpx.AsyncClient:
        if self._direct_http is None:
            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(
                    self._read_timeout_s,
                    read=self._read_timeout_s,
                    connect=15.0,
                ),
                "proxy": self._proxy or settings.proxy_url,
                "trust_env": False,
            }
            if self._http2:
                client_kwargs["http2"] = True
            try:
                self._direct_http = httpx.AsyncClient(**client_kwargs)
            except ImportError:
                if not self._http2:
                    raise
                log.warning(
                    "browserless direct_http 配置了 HTTP/2 但缺少 h2 依赖，回退 HTTP/1.1"
                )
                client_kwargs.pop("http2", None)
                self._direct_http = httpx.AsyncClient(**client_kwargs)
            self._owns_direct_http = True
        return self._direct_http

    async def _direct_http_request(
        self, url: str, headers: dict[str, str], body: str
    ) -> tuple[int, bytes]:
        client = await self._get_direct_http()
        try:
            resp = await client.post(
                url, content=body.encode("utf-8"), headers=self._direct_headers(headers)
            )
        except httpx.TimeoutException as exc:
            raise BrowserTransportError("browserless direct http timeout") from exc
        except httpx.HTTPError as exc:
            raise BrowserTransportError("browserless direct http failed") from exc
        return resp.status_code, resp.content

    async def _direct_http_stream(
        self, url: str, headers: dict[str, str], body: str
    ) -> AsyncGenerator[tuple[str, object], None]:
        client = await self._get_direct_http()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            async with client.stream(
                "POST",
                url,
                content=body.encode("utf-8"),
                headers=self._direct_headers(headers),
            ) as resp:
                yield ("status", int(resp.status_code))
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    text = decoder.decode(chunk)
                    if text:
                        yield ("chunk", text.encode("utf-8"))
                tail = decoder.decode(b"", final=True)
                if tail:
                    yield ("chunk", tail.encode("utf-8"))
        except httpx.TimeoutException as exc:
            raise BrowserTransportError(
                "browserless direct http stream timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise BrowserTransportError(
                "browserless direct http stream failed"
            ) from exc


def _first_text(body: str) -> str:
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return ""
    if not isinstance(parsed, list) or len(parsed) <= 1:
        return ""
    contents = parsed[1]
    if not isinstance(contents, list):
        return ""
    for content in reversed(contents):
        if not isinstance(content, list) or not content:
            continue
        parts = content[0]
        if not isinstance(parts, list):
            continue
        for part in parts:
            if (
                isinstance(part, list)
                and len(part) > 1
                and isinstance(part[1], str)
                and part[1]
            ):
                return part[1]
    return ""


def _last_text_part(contents: list[AistudioContent]) -> str:
    for content in reversed(contents):
        for part in reversed(content.parts):
            if part.text:
                return str(part.text)
    return ""


__all__ = ["ENGINE_NAME", "BrowserlessSession"]
