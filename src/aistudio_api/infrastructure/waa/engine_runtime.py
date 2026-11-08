"""Engine runtime factories for the transactional hot switch.

A runtime owns everything that is not per-slot: the CloakBrowser process/CDP
for ``browser`` and the waa-signer daemon/client for ``browserless``.  Slot
sessions are created through :meth:`create_session` using the same signature
for both engines.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aistudio_api.config import settings
from aistudio_api.infrastructure.browser.browser_engine import (
    find_chromium_executable,
)
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


@dataclass
class SessionBuild:
    session: EngineSession
    context_id: str = ""
    cdp: Any = None


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
    ) -> SessionBuild:
        del slot_id, tier
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
    ) -> None:
        signer = engine_settings.get("signer")
        transport = engine_settings.get("transport")
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

    # ------------------------------------------------------------------ setup

    def _signer_mode(self) -> str:
        return str(self._signer_settings.get("mode") or "local_subprocess")

    def _endpoint(self) -> str:
        return str(self._signer_settings.get("endpoint") or "http://127.0.0.1:8081")

    def _ensure_client(self) -> SignerClient:
        if self._signer is None:
            self._signer = SignerClient(
                self._endpoint(),
                request_timeout_s=float(
                    self._signer_settings.get("request_timeout_s", 90) or 90
                ),
                read_timeout_s=float(
                    self._transport_settings.get("read_timeout_s", 120) or 120
                ),
                connect_timeout_s=float(
                    self._transport_settings.get("connect_timeout_s", 15) or 15
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
            idle_ttl_s=float(self._signer_settings.get("idle_ttl_s", 900) or 900),
            proxy=settings.proxy_url,
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
    ) -> SessionBuild:
        del slot_id
        client = self._ensure_client()
        session = BrowserlessSession(
            signer=client,
            auth_file=auth_file,
            auth_user=auth_user,
            tier=tier,
            account_id=account_id,
            realm_id=realm_id,
            transport_mode=str(self._transport_settings.get("mode") or "signer_rpc"),
            proxy=settings.proxy_url,
            locale=str(self._signer_settings.get("locale") or settings.locale),
            timezone=str(self._signer_settings.get("timezone") or settings.timezone),
            http2=bool(self._transport_settings.get("http2", True)),
            read_timeout_s=float(
                self._transport_settings.get("read_timeout_s", 120) or 120
            ),
        )
        return SessionBuild(session=session)

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
    return BrowserEngineRuntime(client)


__all__ = [
    "BrowserEngineRuntime",
    "BrowserlessEngineRuntime",
    "SessionBuild",
    "build_engine_runtime",
]
