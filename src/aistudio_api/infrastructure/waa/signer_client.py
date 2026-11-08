"""HTTP client for the local/remote ``waa-signer`` service.

Security rules:
* Cookie / Authorization material is never logged, echoed in errors, or stored
  on the client; only status codes and bounded server error strings survive.
* There is no automatic remote fallback: a failed local signer raises, and the
  caller must surface an explicit 503 rather than silently calling a third
  party.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("waa-signer-client")

MINT_PATH = "/v1/proof/mint"
RPC_PATH = "/v1/rpc/send"
RELEASE_PATH = "/v1/worker/release"
HEALTH_PATH = "/health"


class SignerError(RuntimeError):
    """Base signer transport/protocol error (never includes secrets)."""

    status_code: int | None = None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class SignerUnavailableError(SignerError):
    """The signer process/endpoint cannot be reached."""


class SignerTimeoutError(SignerError):
    """The signer request exceeded its bounded timeout."""


class SignerProtocolError(SignerError):
    """The signer returned an unusable/invalid response."""


@dataclass(frozen=True)
class SignerHealth:
    ok: bool
    workers: int = 0
    max_workers: int = 0
    version: str = ""
    ready: bool = False
    engine: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "workers": self.workers,
            "maxWorkers": self.max_workers,
            "version": self.version,
            "ready": self.ready,
            "engine": self.engine,
            "error": self.error,
        }


@dataclass(frozen=True)
class MintProofResult:
    ok: bool
    proof: str = ""
    digest: str = ""
    account_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    set_cookies: list[dict[str, object]] = field(default_factory=list)
    deleted_cookies: list[dict[str, object]] = field(default_factory=list)
    truncated: bool = False
    pool_state: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class RpcResult:
    status: int
    body: bytes
    content_type: str = ""
    account_key: str = ""
    set_cookies: list[dict[str, object]] = field(default_factory=list)
    deleted_cookies: list[dict[str, object]] = field(default_factory=list)
    truncated: bool = False


def _decode_set_cookies(value: str | None) -> list[dict[str, object]]:
    if not value:
        return []
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _truthy_header(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _cookie_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _cookies_empty(cookies: str) -> bool:
    text = str(cookies or "").strip()
    if not text or text in {"{}", "null", "[]"}:
        return True
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    if isinstance(parsed, dict):
        raw = parsed.get("cookies")
        return isinstance(raw, list) and not raw
    return False


def _safe_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise SignerProtocolError(f"waa-signer health.{field_name} 必须为整数")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SignerProtocolError(f"waa-signer health.{field_name} 必须为整数") from exc
    if number < 0:
        raise SignerProtocolError(f"waa-signer health.{field_name} 不能为负数")
    return number


def _error_text(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        raw = payload.get("error")
        if isinstance(raw, str) and raw:
            return raw[:300]
    return fallback[:300]


class SignerClient:
    """Strict, timeout-bounded client for one signer endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        request_timeout_s: float = 90.0,
        read_timeout_s: float | None = None,
        connect_timeout_s: float = 15.0,
        max_connections: int = 6,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._owns_client = http_client is None
        read_timeout = max(0.1, float(read_timeout_s or request_timeout_s))
        timeout = httpx.Timeout(
            max(0.1, float(request_timeout_s)),
            read=read_timeout,
            connect=max(0.1, float(connect_timeout_s)),
        )
        self._client = http_client or httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max(1, int(max_connections)),
                max_keepalive_connections=max(1, int(max_connections)),
            ),
            trust_env=False,
        )

    def _url(self, path: str) -> str:
        return f"{self.endpoint}{path}"

    @staticmethod
    def _map_exception(exc: Exception) -> SignerError:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return SignerUnavailableError("waa-signer 不可达")
        if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return SignerTimeoutError("waa-signer 请求超时")
        if isinstance(exc, httpx.HTTPError):
            return SignerError("waa-signer 传输失败")
        return SignerError("waa-signer 调用异常")

    async def health(self) -> SignerHealth:
        try:
            resp = await self._client.get(self._url(HEALTH_PATH))
        except Exception as exc:
            raise self._map_exception(exc) from exc
        payload: object = {}
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code != 200:
            return SignerHealth(
                ok=False,
                error=f"health status {resp.status_code}",
            )
        if not isinstance(payload, dict):
            raise SignerProtocolError("waa-signer health 返回非对象")
        workers = _safe_int(payload.get("workers", 0), "workers")
        max_workers = _safe_int(payload.get("maxWorkers", 0), "maxWorkers")
        return SignerHealth(
            ok=bool(payload.get("ok", True)),
            workers=workers,
            max_workers=max_workers,
            version=str(payload.get("version") or ""),
            ready=bool(payload.get("ready", False)),
            engine=str(payload.get("engine") or ""),
        )

    async def mint_proof(
        self,
        *,
        cookies: str,
        auth_user: str = "0",
        g1_tier: str = "",
        model: str = "",
        prompt: str = "",
        page_prompt: str = "",
        digest: str = "",
        wire_contents: object | None = None,
        proxy: str | None = None,
        account_key: str | None = None,
    ) -> MintProofResult:
        if _cookies_empty(cookies):
            raise SignerProtocolError("waa-signer mint cookies 为空")
        body: dict[str, object] = {
            "cookies": cookies,
            "authUser": auth_user,
            "g1Tier": g1_tier,
            "model": model,
            "prompt": prompt,
            "pagePrompt": page_prompt,
            "digest": digest,
        }
        if wire_contents is not None:
            body["wireContents"] = wire_contents
        if proxy:
            body["proxy"] = proxy
        if account_key:
            body["accountKey"] = account_key
        started = _monotonic()
        try:
            resp = await self._client.post(self._url(MINT_PATH), json=body)
        except Exception as exc:
            raise self._map_exception(exc) from exc
        latency_ms = (_monotonic() - started) * 1000.0
        if resp.status_code != 200:
            raise SignerError(
                f"waa-signer mint 失败 (status={resp.status_code})",
                status_code=resp.status_code,
            )
        payload = _json_object(resp, "mint")
        if not payload.get("ok") or not isinstance(payload.get("proof"), str):
            raise SignerProtocolError(
                _error_text(payload, "waa-signer mint 未返回 proof")
            )
        headers_raw = payload.get("headers")
        return MintProofResult(
            ok=True,
            proof=str(payload.get("proof") or ""),
            digest=str(payload.get("digest") or ""),
            account_key=str(payload.get("accountKey") or ""),
            headers={
                str(k): str(v)
                for k, v in (
                    headers_raw.items() if isinstance(headers_raw, dict) else []
                )
            },
            set_cookies=_cookie_list(payload.get("setCookies")),
            deleted_cookies=_cookie_list(payload.get("deletedCookies")),
            truncated=bool(
                payload.get("setCookiesTruncated") or payload.get("truncated")
            ),
            pool_state=str(payload.get("poolState") or ""),
            latency_ms=latency_ms,
        )

    async def send_rpc(
        self,
        *,
        cookies: str,
        body: str,
        url: str,
        headers: dict[str, str] | None = None,
        auth_user: str = "0",
        g1_tier: str = "",
        model: str = "",
        page_prompt: str = "",
        proxy: str | None = None,
        account_key: str | None = None,
    ) -> RpcResult:
        if _cookies_empty(cookies):
            raise SignerProtocolError("waa-signer rpc cookies 为空")
        payload = self._rpc_payload(
            cookies=cookies,
            body=body,
            url=url,
            headers=headers,
            auth_user=auth_user,
            g1_tier=g1_tier,
            model=model,
            page_prompt=page_prompt,
            proxy=proxy,
            account_key=account_key,
        )
        try:
            resp = await self._client.post(self._url(RPC_PATH), json=payload)
        except Exception as exc:
            raise self._map_exception(exc) from exc
        return RpcResult(
            status=resp.status_code,
            body=resp.content,
            content_type=resp.headers.get("content-type", ""),
            account_key=resp.headers.get("x-waa-account-key", ""),
            set_cookies=_decode_set_cookies(resp.headers.get("x-waa-set-cookies")),
            deleted_cookies=_decode_set_cookies(
                resp.headers.get("x-waa-deleted-cookies")
            ),
            truncated=_truthy_header(resp.headers.get("x-waa-set-cookies-truncated")),
        )

    async def stream_rpc(
        self,
        *,
        cookies: str,
        body: str,
        url: str,
        headers: dict[str, str] | None = None,
        auth_user: str = "0",
        g1_tier: str = "",
        model: str = "",
        page_prompt: str = "",
        proxy: str | None = None,
        account_key: str | None = None,
        on_metadata: Callable[[str, list[dict[str, object]], bool], None] | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Yield ``("status", int)`` then ``("chunk", bytes)`` events.

        ``on_metadata`` is called once per request with the streamed
        ``X-WAA-Account-Key`` / ``X-WAA-Set-Cookies`` values, so the session can
        write them back without any shared mutable state.
        """
        payload = self._rpc_payload(
            cookies=cookies,
            body=body,
            url=url,
            headers=headers,
            auth_user=auth_user,
            g1_tier=g1_tier,
            model=model,
            page_prompt=page_prompt,
            proxy=proxy,
            account_key=account_key,
        )
        try:
            async with self._client.stream(
                "POST", self._url(RPC_PATH), json=payload
            ) as resp:
                if on_metadata is not None:
                    on_metadata(
                        resp.headers.get("x-waa-account-key", ""),
                        _decode_set_cookies(resp.headers.get("x-waa-set-cookies")),
                        _truthy_header(resp.headers.get("x-waa-set-cookies-truncated")),
                    )
                yield ("status", int(resp.status_code))
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield ("chunk", chunk)
        except Exception as exc:
            raise self._map_exception(exc) from exc

    async def release_worker(
        self, *, account_key: str | None = None, release_all: bool = False
    ) -> dict[str, object]:
        payload: dict[str, object] = {"all": bool(release_all)}
        if account_key:
            payload["accountKey"] = account_key
        try:
            resp = await self._client.post(self._url(RELEASE_PATH), json=payload)
        except Exception as exc:
            raise self._map_exception(exc) from exc
        if resp.status_code != 200:
            raise SignerError(
                f"waa-signer release 失败 (status={resp.status_code})",
                status_code=resp.status_code,
            )
        return _json_object(resp, "release")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _rpc_payload(
        *,
        cookies: str,
        body: str,
        url: str,
        headers: dict[str, str] | None,
        auth_user: str,
        g1_tier: str,
        model: str,
        page_prompt: str,
        proxy: str | None,
        account_key: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "cookies": cookies,
            "body": body,
            "url": url,
            "headers": dict(headers or {}),
            "authUser": auth_user,
            "g1Tier": g1_tier,
            "model": model,
            "pagePrompt": page_prompt,
        }
        if proxy:
            payload["proxy"] = proxy
        if account_key:
            payload["accountKey"] = account_key
        return payload


def _json_object(resp: httpx.Response, operation: str) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SignerProtocolError(f"waa-signer {operation} 返回非 JSON") from exc
    if not isinstance(payload, dict):
        raise SignerProtocolError(f"waa-signer {operation} 返回非对象")
    return dict(payload)


def _monotonic() -> float:
    import time

    return time.monotonic()


__all__ = [
    "HEALTH_PATH",
    "MintProofResult",
    "RpcResult",
    "SignerClient",
    "SignerError",
    "SignerHealth",
    "SignerProtocolError",
    "SignerTimeoutError",
    "SignerUnavailableError",
]
