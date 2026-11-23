"""Per-account exit resolver for the external exit pool (env gated).

The resolver talks to a host-side manager that owns account -> exit stickiness:

* ``GET  {endpoint}/v1/assign?account=<hmac>&kind=chat|build``
* ``POST {endpoint}/v1/events``

Only the irreversible ``HMAC-SHA256(hmac_key, account_id)`` identifier ever
leaves the process.  Cookies, emails and tokens for the manager are never sent.

Failures are never fatal: a timeout, 5xx, malformed payload or a proxy URL that
fails the scheme/host/port whitelist makes :meth:`ExitResolver.resolve` return
``None`` so the caller falls back to the global ``settings.proxy_url``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from aistudio_api.infrastructure.exit_pool.config import ExitPoolConfig
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("exit-pool")

ASSIGN_PATH = "/v1/assign"
EVENTS_PATH = "/v1/events"
STATUS_PATH = "/v1/status"
STATUS_TIMEOUT_S = 2.0
STATUS_CACHE_TTL_S = 15.0
ALLOWED_SCHEMES: frozenset[str] = frozenset({"socks5", "socks5h"})

ClientFactory = Callable[[], httpx.AsyncClient]


def hmac_account_id(hmac_key: str, account_id: str) -> str:
    """Stable, irreversible account identifier shared with the manager."""
    return hmac.new(
        hmac_key.encode("utf-8"),
        account_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def parse_socks_url(raw: object, allowed_hosts: frozenset[str]) -> str | None:
    """Return ``raw`` when it is a whitelisted socks5/socks5h proxy URL."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return None
    hostname = (parsed.hostname or "").lower()
    if not hostname or hostname not in allowed_hosts:
        return None
    if port is None or not 0 < port < 65536:
        return None
    return value


@dataclass(frozen=True)
class ExitAssignment:
    """One validated account -> exit binding."""

    exit_id: str
    socks_url: str
    generation: int = 0
    ttl_s: float = 0.0
    healthy: bool = True


@dataclass
class _CacheEntry:
    assignment: ExitAssignment
    expires_at: float


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _status_failure() -> dict[str, object]:
    """Read-only status shape used when the manager is unreachable/invalid."""
    return {
        "manager_ok": False,
        "healthy": 0,
        "total": 0,
        "quarantined": 0,
        "disabled": 0,
        "bound_accounts": 0,
        "last_switch_at": 0,
    }


def _parse_status(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return _status_failure()
    exits = payload.get("exits")
    if not isinstance(exits, dict):
        return _status_failure()
    return {
        "manager_ok": payload.get("manager_ok") is True,
        "healthy": _as_int(exits.get("healthy"), 0),
        "total": _as_int(exits.get("total"), 0),
        "quarantined": _as_int(exits.get("quarantined"), 0),
        "disabled": _as_int(exits.get("disabled"), 0),
        "bound_accounts": _as_int(payload.get("bound_accounts"), 0),
        "last_switch_at": _as_int(payload.get("last_switch_at"), 0),
    }


class ExitResolver:
    """TTL-cached exit assignment client (never raises at call sites)."""

    def __init__(
        self,
        config: ExitPoolConfig,
        *,
        client_factory: ClientFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._clock = clock
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        # Last resolved binding regardless of TTL: a long-lived session keeps
        # running on that exit, so region-403 arbitration must still know it.
        self._last_known: dict[tuple[str, str], ExitAssignment] = {}
        self._fetch_lock = asyncio.Lock()
        self._status_lock = asyncio.Lock()
        self._pending: set[asyncio.Task[bool]] = set()
        self._status_cache: dict[str, object] | None = None
        self._status_cached_at = 0.0

    @property
    def config(self) -> ExitPoolConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return True

    # ------------------------------------------------------------------ client

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                timeout = httpx.Timeout(self._config.http_timeout_s)
                self._client = httpx.AsyncClient(
                    timeout=timeout,
                    trust_env=False,
                )
        return self._client

    # --------------------------------------------------------------- resolving

    def cache_key(self, account_id: str, kind: str) -> tuple[str, str]:
        return (account_id, kind)

    def assignment_for(
        self,
        account_id: str,
        *,
        kind: str = "chat",
        include_expired: bool = False,
    ) -> ExitAssignment | None:
        """Return the cached assignment, or the last known one when asked.

        ``include_expired=True`` is for canary arbitration: a session that is
        still alive was created with that exit even if the cache TTL elapsed.
        """
        key = self.cache_key(account_id, kind)
        entry = self._cache.get(key)
        if entry is not None and (include_expired or entry.expires_at > self._clock()):
            return entry.assignment
        if include_expired:
            return self._last_known.get(key)
        return None

    def invalidate(self, account_id: str, *, kind: str | None = None) -> None:
        """Drop the cached binding so the next ``create_session`` re-resolves."""
        if kind is None:
            stale = [key for key in self._cache if key[0] == account_id]
            stale.extend(key for key in self._last_known if key[0] == account_id)
            for key in stale:
                self._cache.pop(key, None)
                self._last_known.pop(key, None)
            return
        key = self.cache_key(account_id, kind)
        self._cache.pop(key, None)
        self._last_known.pop(key, None)

    def invalidate_exit(self, exit_id: str) -> None:
        """Drop every cached/last-known binding on one quarantined exit.

        Quarantining is an exit-level event: the manager migrates all accounts
        bound to that exit, so keeping other accounts' TTL cache would let them
        keep using the known-bad exit until it expires.
        """
        if not exit_id:
            return
        stale = {
            key
            for key, entry in self._cache.items()
            if entry.assignment.exit_id == exit_id
        }
        stale.update(
            key
            for key, assignment in self._last_known.items()
            if assignment.exit_id == exit_id
        )
        for key in stale:
            self._cache.pop(key, None)
            self._last_known.pop(key, None)

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        """Resolve the socks URL for one account, or ``None`` to use global."""
        if not account_id:
            return None
        cached = self.assignment_for(account_id, kind=kind)
        if cached is not None:
            return cached.socks_url
        async with self._fetch_lock:
            cached = self.assignment_for(account_id, kind=kind)
            if cached is not None:
                return cached.socks_url
            try:
                assignment = await self._fetch_assignment(account_id, kind)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "[exit-pool] 出口分配异常，回退全局代理 kind=%s error=%s",
                    kind,
                    type(exc).__name__,
                )
                return None
            if assignment is None:
                return None
            self._store(account_id, kind, assignment)
            return assignment.socks_url

    async def pool_status(self) -> dict[str, object]:
        """Read-only manager status, cached for 15s; never raises.

        The manager is a best-effort observability source: any timeout, HTTP
        failure or malformed payload yields ``manager_ok=False`` with zeroed
        counters so ``GET /config`` stays responsive and the app keeps using the
        global proxy.

        A cold cache only ever triggers one in-flight ``/v1/status`` probe:
        concurrent callers wait on ``_status_lock`` and then reuse the result
        the winner cached, instead of each firing an independent request.
        """
        now = self._clock()
        cached = self._status_cache
        if cached is not None and now - self._status_cached_at < STATUS_CACHE_TTL_S:
            return dict(cached)
        async with self._status_lock:
            now = self._clock()
            cached = self._status_cache
            if cached is not None and now - self._status_cached_at < STATUS_CACHE_TTL_S:
                return dict(cached)
            try:
                status = await asyncio.wait_for(
                    self._fetch_status(), timeout=STATUS_TIMEOUT_S
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "[exit-pool] 管理器状态查询失败（管理器不可达）error=%s",
                    type(exc).__name__,
                )
                status = _status_failure()
            self._status_cache = status
            self._status_cached_at = self._clock()
            return dict(status)

    async def _fetch_status(self) -> dict[str, object]:
        client = self._ensure_client()
        try:
            response = await client.get(
                f"{self._config.endpoint}{STATUS_PATH}",
                headers={"Authorization": f"Bearer {self._config.token}"},
            )
        except httpx.HTTPError:
            return _status_failure()
        if response.status_code != 200:
            log.debug("[exit-pool] 管理器状态不可用 status=%s", response.status_code)
            return _status_failure()
        try:
            payload: Any = response.json()
        except ValueError:
            return _status_failure()
        return _parse_status(payload)

    def _store(self, account_id: str, kind: str, assignment: ExitAssignment) -> None:
        ttl = assignment.ttl_s if assignment.ttl_s > 0 else self._config.ttl_s
        ttl = max(1.0, min(ttl, self._config.ttl_s))
        key = self.cache_key(account_id, kind)
        self._last_known[key] = assignment
        self._cache[key] = _CacheEntry(
            assignment=assignment,
            expires_at=self._clock() + ttl,
        )

    async def _fetch_assignment(
        self, account_id: str, kind: str
    ) -> ExitAssignment | None:
        client = self._ensure_client()
        account_hmac = hmac_account_id(self._config.hmac_key, account_id)
        try:
            response = await client.get(
                f"{self._config.endpoint}{ASSIGN_PATH}",
                params={"account": account_hmac, "kind": kind},
                headers={"Authorization": f"Bearer {self._config.token}"},
            )
        except httpx.HTTPError as exc:
            log.warning(
                "[exit-pool] 出口分配请求失败（degraded 回退全局代理）kind=%s error=%s",
                kind,
                type(exc).__name__,
            )
            return None
        if response.status_code != 200:
            log.warning(
                "[exit-pool] 出口分配不可用（degraded 回退全局代理）kind=%s status=%s",
                kind,
                response.status_code,
            )
            return None
        try:
            payload: Any = response.json()
        except ValueError:
            log.warning("[exit-pool] 出口分配响应非 JSON，回退全局代理 kind=%s", kind)
            return None
        assignment = self._parse_assignment(payload, kind)
        if assignment is None:
            log.warning(
                "[exit-pool] 出口分配响应非法（socks_url 未通过白名单），"
                "回退全局代理 kind=%s",
                kind,
            )
        return assignment

    def _parse_assignment(self, payload: object, kind: str) -> ExitAssignment | None:
        if not isinstance(payload, dict):
            return None
        exit_id = payload.get("exit_id")
        if not isinstance(exit_id, str) or not exit_id.strip():
            return None
        socks_url = parse_socks_url(
            payload.get("socks_url"), self._config.allowed_hosts
        )
        if socks_url is None:
            return None
        if payload.get("healthy") is not True:
            # Treat anything but an explicit ``true`` as unusable: the manager
            # contract always carries ``healthy``, and a missing/false flag means
            # the exit is known (or assumed) bad.  Returning ``None`` makes the
            # caller fall back to the global proxy and never caches it.
            log.warning(
                "[exit-pool] 分配到的出口未标记健康（回退全局代理）exit=%s kind=%s",
                exit_id,
                kind,
            )
            return None
        return ExitAssignment(
            exit_id=exit_id.strip(),
            socks_url=socks_url,
            generation=_as_int(payload.get("generation"), 0),
            ttl_s=_as_float(payload.get("ttl_s"), 0.0),
            healthy=True,
        )

    # ------------------------------------------------------------------ events

    def report_event_nowait(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> None:
        """Fire-and-forget event report; never blocks or raises."""
        payload = self.event_payload(
            account_id=account_id,
            exit_id=exit_id,
            event_kind=event_kind,
            canary=canary,
        )
        task = asyncio.create_task(self._post_event(payload))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def event_payload(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> dict[str, object]:
        return {
            "account": hmac_account_id(self._config.hmac_key, account_id),
            "exit_id": exit_id,
            "kind": event_kind,
            "canary": canary,
            "ts": int(time.time()),
        }

    async def report_event(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> bool:
        """Bounded event report; returns ``False`` on any failure."""
        payload = self.event_payload(
            account_id=account_id,
            exit_id=exit_id,
            event_kind=event_kind,
            canary=canary,
        )
        try:
            return await asyncio.wait_for(
                self._post_event(payload),
                timeout=self._config.http_timeout_s,
            )
        except TimeoutError:
            log.warning(
                "[exit-pool] 出口事件上报超时 kind=%s exit=%s", event_kind, exit_id
            )
            return False
        except Exception as exc:
            log.warning(
                "[exit-pool] 出口事件上报失败 kind=%s exit=%s error=%s",
                event_kind,
                exit_id,
                type(exc).__name__,
            )
            return False

    async def _post_event(self, payload: dict[str, object]) -> bool:
        try:
            client = self._ensure_client()
            response = await client.post(
                f"{self._config.endpoint}{EVENTS_PATH}",
                json=payload,
                headers={"Authorization": f"Bearer {self._config.token}"},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("[exit-pool] 出口事件上报失败: %s", type(exc).__name__)
            return False
        if response.status_code >= 400:
            log.debug("[exit-pool] 出口事件上报被拒 status=%s", response.status_code)
            return False
        return True

    async def wait_pending(self) -> None:
        """Await background event reports (tests/shutdown only)."""
        pending = list(self._pending)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ----------------------------------------------------------------- teardown

    async def aclose(self) -> None:
        await self.wait_pending()
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                log.debug("[exit-pool] 关闭控制端点客户端失败: %s", type(exc).__name__)


_resolver: ExitResolver | None = None


def get_exit_resolver() -> ExitResolver | None:
    """Process-wide resolver, or ``None`` when the external mode is off.

    Reading the env is cheap and never touches the network; the underlying
    ``httpx.AsyncClient`` is only created on the first real assignment request.
    """
    global _resolver
    if _resolver is not None:
        return _resolver
    config = ExitPoolConfig.from_env()
    if config is None:
        return None
    _resolver = ExitResolver(config)
    return _resolver


def reset_exit_resolver(resolver: ExitResolver | None = None) -> None:
    """Replace/clear the process-wide resolver (tests and teardown)."""
    global _resolver
    _resolver = resolver


__all__ = [
    "ASSIGN_PATH",
    "EVENTS_PATH",
    "STATUS_CACHE_TTL_S",
    "STATUS_PATH",
    "STATUS_TIMEOUT_S",
    "ExitAssignment",
    "ExitResolver",
    "get_exit_resolver",
    "hmac_account_id",
    "parse_socks_url",
    "reset_exit_resolver",
]
