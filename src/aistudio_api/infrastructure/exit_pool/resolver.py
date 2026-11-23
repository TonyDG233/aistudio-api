"""Per-account exit resolver for the external exit pool (env gated).

The resolver talks to a host-side manager that owns account -> exit stickiness:

* ``POST {endpoint}/v1/lease`` (JSON: account/lane/class/calls)
* ``POST {endpoint}/v1/events``

Only the irreversible ``HMAC-SHA256(hmac_key, account_id)`` identifier ever
leaves the process.  Cookies, emails and tokens for the manager are never sent.

Failures are never fatal: a timeout, 5xx, malformed payload or a proxy URL that
fails the scheme/host/port whitelist makes :meth:`ExitResolver.resolve` return
``None``.  Callers must treat ``None`` as "no exit" and fail closed (or use the
explicit emergency direct valve in :func:`resolve_credential_exit`); they must
never fall back to the global ``settings.proxy_url``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import httpx

from aistudio_api.domain.model_access import account_is_paid
from aistudio_api.infrastructure.exit_pool.config import (
    ExitPoolConfig,
    direct_fallback_enabled,
)
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("exit-pool")

LEASE_PATH = "/v1/lease"
EVENTS_PATH = "/v1/events"
STATUS_PATH = "/v1/status"
STATUS_TIMEOUT_S = 2.0
STATUS_CACHE_TTL_S = 15.0
ALLOWED_SCHEMES: frozenset[str] = frozenset({"socks5", "socks5h"})

# App-side lease/binding cache: the manager remains the authority for sticky
# bindings, but a resolved exit is reused for a short window so session creation
# and canary arbitration do not hammer the control endpoint.
LEASE_CACHE_MIN_S = 30.0
LEASE_CACHE_MAX_S = 60.0

# Fallback wait when the manager's 429 payload/header omits retry_after_s.
DEFAULT_DEFER_RETRY_S = 30.0

LANE_FREE = "free"
LANE_PAID = "paid"


def lane_for_tier(tier: object) -> str:
    """Exit lane derived from an account tier (unknown/empty is always free).

    Only paid tiers (Plus/Pro/Ultra/Paid) may use the paid lane.  An empty or
    unrecognised tier must never be sent to paid exits.
    """
    return LANE_PAID if account_is_paid(tier) else LANE_FREE


# Emergency-valve log throttle: one WARNING per account per window so a
# sustained manager outage never floods the log.
DIRECT_FALLBACK_LOG_INTERVAL_S = 60.0

# Ordinary fail-closed skip throttle: one WARNING per account+reason per window.
# During a manager-wide outage every account skips on every refresh round; this
# keeps the log actionable instead of emitting two lines per account per round.
SKIP_LOG_INTERVAL_S = 60.0

ClientFactory = Callable[[], httpx.AsyncClient]


class ExitAssignmentResolver(Protocol):
    """Minimal resolver surface shared by the real resolver and test doubles."""

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None: ...


@dataclass(frozen=True)
class LeaseResult:
    """Outcome of one ``POST /v1/lease`` call.

    ``status`` is one of ``"ok"``, ``"no_capacity"`` or ``"error"``.  A
    ``no_capacity`` result carries ``retry_after_s`` and must never be treated
    as a skip: the caller defers and retries after that delay.
    """

    status: str
    exit_id: str = ""
    socks_url: str = ""
    generation: int = 0
    ttl_s: float = 0.0
    budget_left: int = 0
    retry_after_s: float = 0.0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def no_capacity(self) -> bool:
        return self.status == "no_capacity"


@dataclass(frozen=True)
class ExitDecision:
    """Explicit outbound-proxy decision for one credential-bearing call.

    ``action`` is one of:

    * ``"proxy"`` — send through ``proxy`` (which may be ``None`` = direct when
      the global mode is off or the emergency valve produced it).
    * ``"defer"`` — the exit manager returned 429 no_capacity: do not send now,
      retry after ``retry_after_s`` (never a skip, never a failure).
    * ``"skip"`` — do not send the request at all (fail-closed).
    * ``"direct_fallback"`` — emergency valve: send without any proxy.
    """

    action: str
    proxy: str | None = None
    reason: str | None = None
    external: bool = False
    retry_after_s: float = 0.0
    exit_id: str = ""
    generation: int = 0

    @property
    def deferred(self) -> bool:
        return self.action == "defer"

    @property
    def allowed(self) -> bool:
        """True when the caller may issue the upstream request."""
        return self.action in ("proxy", "direct_fallback")


_direct_fallback_log_at: dict[str, float] = {}
_skip_log_at: dict[tuple[str, str], float] = {}
_defer_log_at: dict[tuple[str, str], float] = {}


def reset_direct_fallback_log() -> None:
    """Clear the emergency-valve log throttle (tests/teardown)."""
    _direct_fallback_log_at.clear()


def reset_skip_log() -> None:
    """Clear the fail-closed skip log throttle (tests/teardown)."""
    _skip_log_at.clear()
    _defer_log_at.clear()


def _log_deferred(
    account_id: str, *, kind: str, reason: str, retry_after_s: float
) -> None:
    """Throttled no-capacity defer warning (one per account+reason per window)."""
    key = (account_id or "<none>", reason)
    now = time.monotonic()
    last = _defer_log_at.get(key, 0.0)
    if now - last < SKIP_LOG_INTERVAL_S:
        return
    _defer_log_at[key] = now
    log.warning(
        "[exit-pool] 出口调用预算不足（no_capacity），账号 %s 本轮延迟 %.0fs 重试"
        "reason=%s kind=%s",
        account_id or "<none>",
        retry_after_s,
        reason,
        kind,
    )


def _log_skip(account_id: str, *, kind: str, reason: str) -> None:
    """Throttled fail-closed skip warning (one per account+reason per window)."""
    key = (account_id or "<none>", reason)
    now = time.monotonic()
    last = _skip_log_at.get(key, 0.0)
    if now - last < SKIP_LOG_INTERVAL_S:
        return
    _skip_log_at[key] = now
    log.warning(
        "[exit-pool] 账号 %s 无可用出口，跳过带凭据请求（fail-closed）"
        "reason=%s kind=%s",
        account_id or "<none>",
        reason,
        kind,
    )


def _global_proxy() -> str | None:
    from aistudio_api.config import settings

    return settings.proxy_url


def _log_direct_fallback(account_id: str, *, kind: str) -> None:
    now = time.monotonic()
    last = _direct_fallback_log_at.get(account_id, 0.0)
    if now - last < DIRECT_FALLBACK_LOG_INTERVAL_S:
        return
    _direct_fallback_log_at[account_id] = now
    log.warning(
        "[exit-pool] 应急阀已开启：账号 %s 无可用出口，本次带凭据请求改为直连"
        "（不走全局代理）kind=%s",
        account_id,
        kind,
    )


async def resolve_credential_exit(
    account_id: str,
    *,
    kind: str = "chat",
    lane: str = LANE_FREE,
    category: str = "generation",
    resolver: ExitAssignmentResolver | None = None,
) -> ExitDecision:
    """Decide the outbound proxy for one credential-bearing upstream call.

    * off mode (no resolver): return the existing global proxy, which may be
      ``None`` to keep the current direct behaviour.
    * external mode: ask the manager for this account's exit; a 429
      ``no_capacity`` yields ``defer`` with ``retry_after_s`` (never skip /
      failure), a missing exit or an unreachable manager yields ``skip``
      (fail-closed) unless the operator explicitly enabled
      ``AISTUDIO_EXIT_DIRECT_FALLBACK``.
    """
    active = resolver if resolver is not None else get_exit_resolver()
    if active is None:
        return ExitDecision("proxy", proxy=_global_proxy(), external=False)
    if not account_id:
        _log_skip(account_id, kind=kind, reason="no_account")
        return ExitDecision("skip", reason="no_account", external=True)
    try:
        lease_fn = getattr(active, "lease", None)
        if callable(lease_fn):
            lease_call = cast(Callable[..., Awaitable[LeaseResult]], lease_fn)
            result = await lease_call(
                account_id, lane=lane, cls=category, calls=1, kind=kind
            )
            if result.ok:
                return ExitDecision(
                    "proxy",
                    proxy=result.socks_url,
                    external=True,
                    exit_id=result.exit_id,
                    generation=result.generation,
                )
            if result.no_capacity:
                reason = result.reason or "no_capacity"
                retry_after = result.retry_after_s or DEFAULT_DEFER_RETRY_S
                _log_deferred(
                    account_id,
                    kind=kind,
                    reason=reason,
                    retry_after_s=retry_after,
                )
                return ExitDecision(
                    "defer",
                    reason=reason,
                    external=True,
                    retry_after_s=retry_after,
                    exit_id=result.exit_id,
                )
            proxy = None
        else:  # legacy/fake resolver implementing only ``resolve``
            proxy = await active.resolve(account_id, kind=kind)
    except Exception as exc:  # pragma: no cover - resolver never raises
        log.warning(
            "[exit-pool] 出口解析异常（fail-closed）account=%s kind=%s error=%s",
            account_id,
            kind,
            type(exc).__name__,
        )
        proxy = None
    if proxy:
        return ExitDecision("proxy", proxy=proxy, external=True)
    if direct_fallback_enabled():
        _log_direct_fallback(account_id, kind=kind)
        return ExitDecision(
            "direct_fallback", proxy=None, reason="no_exit", external=True
        )
    _log_skip(account_id, kind=kind, reason="no_exit")
    return ExitDecision("skip", reason="no_exit", external=True)


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

    async def lease(
        self,
        account_id: str,
        *,
        lane: str = LANE_FREE,
        cls: str = "generation",
        calls: int = 1,
        kind: str = "chat",
        use_cache: bool = False,
    ) -> LeaseResult:
        """Lease ``calls`` upstream calls on this account's bound exit.

        Never raises: transport/protocol failures return ``LeaseResult("error")``
        and a manager 429 returns ``LeaseResult("no_capacity", retry_after_s=...)``
        so callers can defer without treating it as a failure.  Counting is
        authoritative: only ``use_cache=True`` (session resolution) may reuse the
        short-lived binding cache and skip the manager round trip.
        """
        if not account_id:
            return LeaseResult("error", reason="no_account")
        if use_cache:
            cached = self.assignment_for(account_id, kind=kind)
            if cached is not None:
                return LeaseResult(
                    "ok",
                    exit_id=cached.exit_id,
                    socks_url=cached.socks_url,
                    generation=cached.generation,
                    ttl_s=cached.ttl_s,
                )
        safe_calls = calls if isinstance(calls, int) and calls >= 1 else 1
        account_hmac = hmac_account_id(self._config.hmac_key, account_id)
        payload = {
            "account": account_hmac,
            "lane": lane,
            "class": cls,
            "calls": safe_calls,
        }
        try:
            client = self._ensure_client()
            response = await client.post(
                f"{self._config.endpoint}{LEASE_PATH}",
                json=payload,
                headers={"Authorization": f"Bearer {self._config.token}"},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "[exit-pool] 租约请求失败 kind=%s class=%s error=%s",
                kind,
                cls,
                type(exc).__name__,
            )
            return LeaseResult("error", reason="manager_unreachable")

        if response.status_code == 429:
            return self._parse_no_capacity(response)
        if response.status_code != 200:
            log.warning(
                "[exit-pool] 租约不可用 kind=%s class=%s status=%s",
                kind,
                cls,
                response.status_code,
            )
            return LeaseResult("error", reason=f"http_{response.status_code}")
        try:
            body: Any = response.json()
        except ValueError:
            log.warning("[exit-pool] 租约响应非 JSON kind=%s class=%s", kind, cls)
            return LeaseResult("error", reason="invalid_json")
        result = self._parse_lease(body)
        if result.ok:
            self._store(
                account_id,
                kind,
                ExitAssignment(
                    exit_id=result.exit_id,
                    socks_url=result.socks_url,
                    generation=result.generation,
                    ttl_s=result.ttl_s,
                    healthy=True,
                ),
            )
        else:
            log.warning(
                "[exit-pool] 租约响应非法（socks_url 未通过白名单）"
                "kind=%s class=%s reason=%s",
                kind,
                cls,
                result.reason,
            )
        return result

    def _parse_no_capacity(self, response: httpx.Response) -> LeaseResult:
        retry_after = 0.0
        exit_id = ""
        reason = "no_capacity"
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            retry_after = _as_float(body.get("retry_after_s"), 0.0)
            if isinstance(body.get("exit_id"), str):
                exit_id = str(body["exit_id"])
            if isinstance(body.get("error"), str) and body["error"]:
                reason = str(body["error"])
        if retry_after <= 0.0:
            raw_header = response.headers.get("Retry-After")
            if isinstance(raw_header, str):
                try:
                    retry_after = float(raw_header.strip())
                except ValueError:
                    retry_after = 0.0
        if retry_after <= 0.0:
            retry_after = DEFAULT_DEFER_RETRY_S
        return LeaseResult(
            "no_capacity",
            retry_after_s=retry_after,
            reason=reason,
            exit_id=exit_id,
        )

    def _parse_lease(self, payload: object) -> LeaseResult:
        if not isinstance(payload, dict):
            return LeaseResult("error", reason="invalid_payload")
        exit_id = payload.get("exit_id")
        if not isinstance(exit_id, str) or not exit_id.strip():
            return LeaseResult("error", reason="missing_exit_id")
        socks_url = parse_socks_url(
            payload.get("socks_url"), self._config.allowed_hosts
        )
        if socks_url is None:
            return LeaseResult("error", reason="invalid_socks_url", exit_id=exit_id)
        return LeaseResult(
            "ok",
            exit_id=exit_id.strip(),
            socks_url=socks_url,
            generation=_as_int(payload.get("generation"), 0),
            ttl_s=_as_float(payload.get("ttl_s"), 0.0),
            budget_left=_as_int(payload.get("budget_left"), 0),
        )

    async def resolve(
        self, account_id: str, *, kind: str = "chat", lane: str = LANE_FREE
    ) -> str | None:
        """Resolve the socks URL for one account, or ``None`` when unavailable.

        ``None`` means "no usable exit": callers must fail closed, never fall
        back to the global proxy.  This legacy entry point reuses the short
        binding cache; generation/maintenance callers use :meth:`lease` so every
        counted call reaches the manager.
        """
        if not account_id:
            return None
        cached = self.assignment_for(account_id, kind=kind)
        if cached is not None:
            return cached.socks_url
        async with self._fetch_lock:
            cached = self.assignment_for(account_id, kind=kind)
            if cached is not None:
                return cached.socks_url
            result = await self.lease(
                account_id, lane=lane, cls="generation", calls=1, kind=kind
            )
            return result.socks_url if result.ok else None

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
        ttl = max(LEASE_CACHE_MIN_S, min(ttl, LEASE_CACHE_MAX_S))
        key = self.cache_key(account_id, kind)
        self._last_known[key] = assignment
        self._cache[key] = _CacheEntry(
            assignment=assignment,
            expires_at=self._clock() + ttl,
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
    """Replace/clear the process-wide resolver (tests and teardown).

    Also clears the per-account log throttles so a fresh resolver never inherits
    a previous run's suppression window.
    """
    global _resolver
    _resolver = resolver
    reset_direct_fallback_log()
    reset_skip_log()


__all__ = [
    "DIRECT_FALLBACK_LOG_INTERVAL_S",
    "EVENTS_PATH",
    "SKIP_LOG_INTERVAL_S",
    "STATUS_CACHE_TTL_S",
    "STATUS_PATH",
    "STATUS_TIMEOUT_S",
    "ExitAssignment",
    "ExitAssignmentResolver",
    "ExitDecision",
    "ExitResolver",
    "get_exit_resolver",
    "hmac_account_id",
    "parse_socks_url",
    "reset_direct_fallback_log",
    "reset_exit_resolver",
    "reset_skip_log",
    "resolve_credential_exit",
]
