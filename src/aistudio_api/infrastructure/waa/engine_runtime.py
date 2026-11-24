"""Engine runtime factories for the transactional hot switch.

A runtime owns everything that is not per-slot: the CloakBrowser process/CDP
for ``browser`` and the waa-signer daemon/client for ``browserless``.  Slot
sessions are created through :meth:`create_session` using the same signature
for both engines.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aistudio_api.config import settings
from aistudio_api.domain.errors import ExitBudgetWaitError, RoutingError
from aistudio_api.infrastructure.browser.browser_engine import (
    find_chromium_executable,
)
from aistudio_api.infrastructure.exit_pool import (
    ExitDecision,
    ExitResolver,
    get_exit_resolver,
    resolve_credential_exit,
)
from aistudio_api.infrastructure.exit_pool.resolver import lane_for_tier
from aistudio_api.infrastructure.gateway.browser_engine_session import (
    BrowserEngineSession,
)
from aistudio_api.infrastructure.gateway.session_protocol import EngineSession
from aistudio_api.infrastructure.utils.logger import get_logger
from aistudio_api.infrastructure.waa.browserless_session import BrowserlessSession
from aistudio_api.infrastructure.waa.local_daemon import LocalSignerDaemon
from aistudio_api.infrastructure.waa.signer_client import (
    SignerClient,
    SignerError,
    SignerHealth,
    SignerUnavailableError,
)

log = get_logger("engine-runtime")

# Process-wide once-only guard for the direct_http risk warning.
_direct_http_warned = False

# One warning per account per window when the manager migrates a live session to
# a new exit: the session keeps its bound socks until the slot is rebuilt (the
# existing region-403/D-05 path owns the rebuild), never a hot switch.
_LEASE_MISMATCH_LOG_INTERVAL_S = 60.0
_lease_mismatch_log_at: dict[str, float] = {}


def _warn_lease_mismatch(account_id: str, *, leased_exit: str) -> None:
    import time

    now = time.monotonic()
    last = _lease_mismatch_log_at.get(account_id, 0.0)
    if now - last < _LEASE_MISMATCH_LOG_INTERVAL_S:
        return
    _lease_mismatch_log_at[account_id] = now
    log.warning(
        "[engine] 出口绑定已迁移，当前会话继续使用旧出口直至槽位重建 "
        "account=%s leased_exit=%s",
        account_id,
        leased_exit,
    )


class _LeasedBrowserlessSession:
    """EngineSession wrapper that leases each upstream call with the manager.

    A generation request enters :meth:`request_scope` exactly once, so the lease
    is taken once per request (covers template capture + RPC).  Maintenance
    probes call ``send_hooked_request`` without a scope and lease the
    ``maintenance`` category instead.  Off mode never builds this wrapper.

    The scope flag is a per-task :class:`contextvars.ContextVar`, never a shared
    bool: concurrent requests on the same slot (or a keepalive probe running
    alongside a generation request) must not clear each other's scope.
    """

    def __init__(
        self,
        delegate: EngineSession,
        *,
        resolver: ExitResolver,
        account_id: str,
        lane: str,
        kind: str,
        bound_socks: str | None,
        cold_start_lease: bool = False,
    ) -> None:
        self._delegate = delegate
        self._resolver = resolver
        self._account_id = account_id
        self._lane = lane
        self._kind = kind
        self._bound_socks = bound_socks or ""
        self._scope_leased: contextvars.ContextVar[bool] = contextvars.ContextVar(
            f"leased-scope-{account_id or 'anonymous'}", default=False
        )
        # P2-2: ``create_session`` already leased one generation call to bind
        # the exit.  The first ``request_scope`` consumes that credit instead of
        # leasing a second time; later scopes lease normally.
        self._cold_start_lease = cold_start_lease

    @property
    def engine(self) -> str:
        return self._delegate.engine

    @property
    def active_context(self) -> Any:
        return self._delegate.active_context

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @asynccontextmanager
    async def request_scope(self) -> AsyncGenerator[Any, None]:
        if self._cold_start_lease:
            # Consume the lease taken by ``create_session`` (no await before the
            # flag flips, so a concurrent first request cannot double-consume).
            self._cold_start_lease = False
        else:
            await self._lease("generation")
        token = self._scope_leased.set(True)
        try:
            async with self._delegate.request_scope() as ctx:
                yield ctx
        finally:
            self._scope_leased.reset(token)

    async def _lease(self, cls: str) -> None:
        lease_fn = getattr(self._resolver, "lease", None)
        if not callable(lease_fn):
            # Legacy/fake resolver surface: still fail closed when no exit.
            proxy = await self._resolver.resolve(self._account_id, kind=self._kind)
            if not proxy:
                raise RoutingError(
                    "账号出口不可用，已拒绝本次请求（fail-closed）。",
                    code="exit_unavailable",
                    surface=self._kind,
                )
            return
        result = await cast(Callable[..., Awaitable[Any]], lease_fn)(
            self._account_id,
            lane=self._lane,
            cls=cls,
            calls=1,
            kind=self._kind,
        )
        if result.no_capacity:
            raise ExitBudgetWaitError(
                "出口池调用预算不足（no_capacity），请按 Retry-After 等待后重试。",
                retry_after=result.retry_after_s or 30.0,
                surface=self._kind,
            )
        if not result.ok:
            raise RoutingError(
                "账号出口租约不可用，已拒绝本次请求（fail-closed）；"
                "请检查出口池管理器或稍后重试。",
                code="exit_unavailable",
                surface=self._kind,
            )
        if self._bound_socks and result.socks_url != self._bound_socks:
            _warn_lease_mismatch(
                self._account_id,
                leased_exit=result.exit_id,
            )

    async def send_hooked_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        if not self._scope_leased.get():
            await self._lease("maintenance")
        return await self._delegate.send_hooked_request(
            body=body, url=url, headers=headers
        )

    async def send_streaming_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        if not self._scope_leased.get():
            await self._lease("maintenance")
        async for event in self._delegate.send_streaming_request(
            body=body, url=url, headers=headers
        ):
            yield event

    async def rotate_cookies_via_signer(self) -> dict[str, object]:
        lease_fn = getattr(self._resolver, "lease", None)
        if callable(lease_fn):
            result = await cast(Callable[..., Awaitable[Any]], lease_fn)(
                self._account_id,
                lane=self._lane,
                cls="maintenance",
                calls=1,
                kind=self._kind,
            )
            if result.no_capacity:
                # account_service maps signer exceptions to a generic failure;
                # return an explicit deferred dict so keepalive never counts a
                # 429 no_capacity as an account failure or cooldown.
                return {
                    "ok": False,
                    "status": 0,
                    "updated": [],
                    "skipped": "no_exit",
                    "deferred": True,
                    "retry_after_s": result.retry_after_s or 30.0,
                    "reason": result.reason or "no_capacity",
                }
            if not result.ok:
                return {
                    "ok": False,
                    "status": 0,
                    "updated": [],
                    "error": "exit_unavailable",
                }
        else:
            proxy = await self._resolver.resolve(self._account_id, kind=self._kind)
            if not proxy:
                return {
                    "ok": False,
                    "status": 0,
                    "updated": [],
                    "error": "exit_unavailable",
                }
        method = cast(Any, self._delegate).rotate_cookies_via_signer
        result_dict: dict[str, object] = await method()
        return result_dict


@dataclass
class SessionBuild:
    session: EngineSession
    context_id: str = ""
    cdp: Any = None


def _float(value: object, default: float) -> float:
    """Best-effort float parse for YAML/env-provided engine settings."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


class BrowserEngineRuntime:
    """Traditional CloakBrowser engine runtime (control browser + pool pages)."""

    mode = "browser"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def preflight(self) -> None:
        # Resolve the executable without launching a process so the old engine
        # keeps serving while the target engine is validated. The CDP port is
        # only bound later by ensure_cdp_client; a busy port therefore pops as a
        # rebuild failure and triggers rollback, not a silent fallback.
        executable = find_chromium_executable()
        path = Path(executable)
        if not path.is_file():
            raise RuntimeError(f"CloakBrowser 可执行文件不存在: {path}")
        if os.name != "nt" and not os.access(path, os.X_OK):
            raise RuntimeError(f"CloakBrowser 可执行文件不可执行（权限）: {path}")

    async def start(self) -> None:
        await self.preflight()

    async def create_session(
        self,
        *,
        slot_id: str,
        auth_file: str | None,
        auth_user: str,
        tier: str,
        account_id: str,
        realm_id: str,
        surface: str = "chat",
    ) -> SessionBuild:
        del slot_id, tier, surface
        cdp = await self._client.session.ensure_cdp_client()
        context_id = await cdp.create_browser_context()
        try:
            session = await BrowserEngineSession.bootstrap(
                cdp,
                context_id=context_id,
                auth_file=auth_file,
                auth_user=auth_user,
                realm_id=realm_id,
                account_id=account_id,
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await cdp.dispose_browser_context(context_id)
            raise
        return SessionBuild(session=session, context_id=context_id, cdp=cdp)

    async def close(self) -> None:
        await self._client.close()

    def status(self) -> dict[str, object]:
        return {
            "engine": self.mode,
            "transport_state": "cdp",
            "worker_state": "browser",
        }


class BrowserlessEngineRuntime:
    """WAA signer engine runtime; never launches Chromium."""

    mode = "browserless"

    def __init__(
        self,
        engine_settings: dict[str, object],
        *,
        account_store: Any | None = None,
        signer_client: SignerClient | None = None,
        daemon: LocalSignerDaemon | None = None,
        exit_resolver: ExitResolver | None = None,
    ) -> None:
        browserless = engine_settings.get("browserless")
        block = dict(browserless) if isinstance(browserless, dict) else {}
        signer = block.get("signer")
        transport = block.get("transport")
        self._signer_settings = dict(signer) if isinstance(signer, dict) else {}
        self._transport_settings = (
            dict(transport) if isinstance(transport, dict) else {}
        )
        self._account_store = account_store
        self._signer = signer_client
        self._owns_client = signer_client is None
        self._daemon = daemon
        self._started = False
        self._health = SignerHealth(ok=False)
        # External exit pool is strictly env gated: ``None`` keeps the legacy
        # single global proxy and constructs no control-endpoint client.
        self._exit_resolver = (
            exit_resolver if exit_resolver is not None else get_exit_resolver()
        )

    # ------------------------------------------------------------------ setup

    def _signer_mode(self) -> str:
        return str(self._signer_settings.get("mode") or "local_subprocess")

    def _endpoint(self) -> str:
        return str(self._signer_settings.get("endpoint") or "http://127.0.0.1:8081")

    def _ensure_client(self) -> SignerClient:
        if self._signer is None:
            req_timeout = _float(self._signer_settings.get("request_timeout_s"), 300.0)
            raw_read_timeout = _float(
                self._transport_settings.get("read_timeout_s"), req_timeout
            )
            read_timeout = max(req_timeout, raw_read_timeout)
            self._signer = SignerClient(
                self._endpoint(),
                request_timeout_s=req_timeout,
                read_timeout_s=read_timeout,
                connect_timeout_s=_float(
                    self._transport_settings.get("connect_timeout_s"), 15.0
                ),
                max_connections=int(
                    self._transport_settings.get("max_connections", 6) or 6
                ),
            )
            self._owns_client = True
        return self._signer

    def _ensure_daemon(self) -> LocalSignerDaemon | None:
        if self._daemon is not None:
            return self._daemon
        if self._signer_mode() != "local_subprocess":
            return None
        if not bool(self._signer_settings.get("auto_start", True)):
            return None
        self._daemon = LocalSignerDaemon(
            endpoint=self._endpoint(),
            binary_path=str(
                self._signer_settings.get("binary_path") or "waa-signer/bin/waa-signer"
            ),
            state_dir=str(self._signer_settings.get("state_dir") or "data/waa/workers"),
            startup_timeout_s=float(
                self._signer_settings.get("startup_timeout_s", 30) or 30
            ),
            locale=str(self._signer_settings.get("locale") or settings.locale),
            timezone=str(self._signer_settings.get("timezone") or settings.timezone),
            max_workers=int(self._signer_settings.get("max_workers", 8) or 8),
            idle_ttl_s=float(self._signer_settings.get("idle_ttl_s", 3600) or 3600),
            # External exit mode must never hand the daemon the global socks
            # proxy: requests without a per-account exit would silently fall
            # back to WARP.  Per-request exits are still sent by the session.
            proxy=(None if self._exit_resolver is not None else settings.proxy_url),
            external_exits=self._exit_resolver is not None,
        )
        return self._daemon

    async def preflight(self) -> SignerHealth:
        """Validate the signer without touching the currently running engine."""
        client = self._ensure_client()
        daemon = self._ensure_daemon()
        if self._signer_mode() == "remote_http":
            log.warning(
                "[engine] 使用远程 waa-signer endpoint=%s；本地进程不会作为静默回退",
                self._endpoint(),
            )
        if str(self._transport_settings.get("mode") or "signer_rpc") == "direct_http":
            global _direct_http_warned
            if not _direct_http_warned:
                _direct_http_warned = True
                log.warning(
                    "[engine] transport.mode=direct_http：使用 Python TLS 裸直连降级，"
                    "存在浏览器指纹暴露与上游风控风险，建议改用 signer_rpc"
                )
        try:
            health = await client.health()
        except SignerError as exc:
            if daemon is None:
                raise
            log.info("[engine] signer 未就绪，尝试拉起本地进程: %s", exc)
            await daemon.start(health_client=client)
            health = await client.health()
        if not health.ok:
            raise SignerUnavailableError("waa-signer 不可达")
        if not health.ready:
            # /health.ready is false while the worker pool is saturated.  The
            # endpoint/signer process is still alive, so the switch must proceed
            # and let rebuild acquire workers after the old engine is drained.
            log.warning(
                "[engine] waa-signer health ok but ready=false "
                "(workers=%d/%d); 继续切换",
                health.workers,
                health.max_workers,
            )
        self._health = health
        return health

    async def start(self) -> None:
        await self.preflight()
        self._started = True

    # --------------------------------------------------------------- sessions

    async def create_session(
        self,
        *,
        slot_id: str,
        auth_file: str | None,
        auth_user: str,
        tier: str,
        account_id: str,
        realm_id: str,
        surface: str = "chat",
    ) -> SessionBuild:
        del slot_id
        client = self._ensure_client()
        external = self._exit_resolver is not None
        lane = lane_for_tier(tier)
        kind = surface if surface in ("chat", "build") else "chat"
        decision = (
            await self._resolve_exit_proxy(account_id, surface, tier)
            if external
            else None
        )
        proxy = decision.proxy if decision is not None else settings.proxy_url
        session: Any = BrowserlessSession(
            signer=client,
            auth_file=auth_file,
            auth_user=auth_user,
            tier=tier,
            account_id=account_id,
            realm_id=realm_id,
            transport_mode=str(self._transport_settings.get("mode") or "signer_rpc"),
            proxy=proxy,
            external_exits=external,
            locale=str(self._signer_settings.get("locale") or settings.locale),
            timezone=str(self._signer_settings.get("timezone") or settings.timezone),
            http2=bool(self._transport_settings.get("http2", True)),
            read_timeout_s=max(
                _float(self._signer_settings.get("request_timeout_s"), 300.0),
                _float(self._transport_settings.get("read_timeout_s"), 300.0),
            ),
        )
        if external and self._exit_resolver is not None:
            cold_start_lease = (
                decision is not None
                and decision.action == "proxy"
                and callable(getattr(self._exit_resolver, "lease", None))
            )
            session = cast(
                EngineSession,
                _LeasedBrowserlessSession(
                    session,
                    resolver=self._exit_resolver,
                    account_id=account_id,
                    lane=lane,
                    kind=kind,
                    bound_socks=proxy,
                    cold_start_lease=cold_start_lease,
                ),
            )
        return SessionBuild(session=session)

    async def _resolve_exit_proxy(
        self, account_id: str, surface: str, tier: str = ""
    ) -> ExitDecision:
        """Resolve one account's exit, or fail closed (never the global proxy).

        Returns the lease decision whose ``proxy`` is the bound socks URL.
        Raises :class:`ExitBudgetWaitError` (HTTP 429 ``no_capacity`` +
        Retry-After) when the manager has no call budget, and
        :class:`RoutingError` (HTTP 503) when no exit is available **or when the
        only decision is the emergency direct valve** (generation never leaves
        from the host egress), so a credential-bearing generation request never
        leaks onto WARP/direct and never masks a 429 as 503.
        """
        kind = surface if surface in ("chat", "build") else "chat"
        decision = await resolve_credential_exit(
            account_id or "",
            kind=kind,
            lane=lane_for_tier(tier),
            category="generation",
            resolver=self._exit_resolver,
        )
        if decision.deferred:
            raise ExitBudgetWaitError(
                "出口池调用预算不足（no_capacity），请按 Retry-After 等待后重试。",
                retry_after=decision.retry_after_s,
                surface=kind,
            )
        if not decision.allowed:
            raise RoutingError(
                "账号出口不可用，已拒绝本次生成请求（fail-closed）；"
                "请检查出口池管理器或稍后重试。",
                code="exit_unavailable",
                surface=kind,
            )
        if decision.action == "direct_fallback":
            # Generation must never leave from the host egress, even when the
            # operator opened the emergency valve for maintenance probes.  The
            # valve is intentionally not consulted here: this is a code-level
            # invariant, not an environment switch.
            log.warning(
                "[engine] 生成请求拒绝直连应急阀（class=generation fail-closed）"
                "account=%s surface=%s",
                account_id or "-",
                kind,
            )
            raise RoutingError(
                "生成请求禁止使用直连应急出口（fail-closed）；"
                "请等待出口池恢复或检查管理器。",
                code="exit_unavailable",
                surface=kind,
            )
        if decision.action == "proxy" and decision.proxy:
            # Never log the socks URL itself: it may carry credentials.
            log.info("[engine] 使用外部出口 surface=%s", kind)
        return decision

    async def create_canary_session(
        self,
        *,
        account_id: str,
        auth_file: str | None,
        proxy: str,
        auth_user: str = "0",
        tier: str = "Free",
    ) -> BrowserlessSession:
        """Build a throwaway probe session for region canary arbitration.

        It shares the signer/transport settings with real slots but is created
        outside the pool: it never takes ``active_requests`` and never becomes
        an instance.  The caller owns ``close()`` and the auth file path.
        """
        return BrowserlessSession(
            signer=self._ensure_client(),
            auth_file=auth_file,
            auth_user=auth_user,
            tier=tier,
            account_id=account_id,
            realm_id=f"exit-canary-{account_id}",
            transport_mode=str(self._transport_settings.get("mode") or "signer_rpc"),
            proxy=proxy,
            external_exits=self._exit_resolver is not None,
            locale=str(self._signer_settings.get("locale") or settings.locale),
            timezone=str(self._signer_settings.get("timezone") or settings.timezone),
            http2=bool(self._transport_settings.get("http2", True)),
            read_timeout_s=max(
                _float(self._signer_settings.get("request_timeout_s"), 300.0),
                _float(self._transport_settings.get("read_timeout_s"), 300.0),
            ),
        )

    async def health(self) -> SignerHealth:
        try:
            health = await self._ensure_client().health()
        except SignerError as exc:
            return SignerHealth(ok=False, error=str(exc))
        self._health = health
        return health

    async def close(self) -> None:
        client = self._signer
        if client is not None:
            try:
                await client.release_worker(release_all=True)
            except SignerError as exc:
                log.warning("[engine] 释放全部 WAA worker 失败: %s", exc)
        daemon = self._daemon
        if daemon is not None:
            await daemon.close()
        if client is not None and self._owns_client:
            await client.close()
        self._started = False

    def status(self) -> dict[str, object]:
        return {
            "engine": self.mode,
            "transport_state": str(
                self._transport_settings.get("mode") or "signer_rpc"
            ),
            "worker_state": (
                f"{self._health.workers}/{self._health.max_workers}"
                if self._health.max_workers
                else ("ready" if self._health.ready else "unknown")
            ),
        }


def build_engine_runtime(
    engine_settings: dict[str, object],
    *,
    client: Any,
    account_store: Any | None = None,
) -> BrowserEngineRuntime | BrowserlessEngineRuntime:
    mode = str(engine_settings.get("mode") or "browser")
    if mode == "browserless":
        return BrowserlessEngineRuntime(engine_settings, account_store=account_store)
    if get_exit_resolver() is not None:
        # P2-4: per-account exits require per-session proxies; Chrome's proxy is
        # a process-level flag.  Continuing with the browser engine would route
        # every browser/credential request through the global (WARP) proxy, so
        # external mode fails fast instead of silently violating the invariant.
        raise RuntimeError(
            "AISTUDIO_EXIT_MODE=external 仅支持 browserless 引擎；"
            "browser 引擎使用进程级全局代理，已拒绝启动（fail-fast）"
        )
    return BrowserEngineRuntime(client)


__all__ = [
    "BrowserEngineRuntime",
    "BrowserlessEngineRuntime",
    "SessionBuild",
    "build_engine_runtime",
]
