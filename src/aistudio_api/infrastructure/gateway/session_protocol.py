"""Engine-neutral session surface shared by every request engine.

``EngineSession`` is implemented by the traditional browser engine
(``BrowserEngineSession`` wrapping ``PooledSession``) and the browserless WAA
engine (``BrowserlessSession``).  Method names are intentionally unchanged from
the original ``RequestSession`` protocol so capture/replay/streaming code does
not have to care which engine produced the session.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent

ENGINE_BROWSER = "browser"
ENGINE_BROWSERLESS = "browserless"
VALID_ENGINES = frozenset({ENGINE_BROWSER, ENGINE_BROWSERLESS})


class EngineSession(Protocol):
    """The subset of a request engine used by capture/replay/streaming."""

    @property
    def engine(self) -> str: ...

    @property
    def active_context(self) -> SessionContext: ...

    def request_scope(self) -> AbstractAsyncContextManager[object]: ...

    async def ensure_ready(self, timeout_s: float = 120.0) -> SessionContext: ...

    async def capture_template(self, model: str) -> dict[str, object]: ...

    async def generate_snapshot(
        self, contents: list[AistudioContent], *, payload_size: int | None = None
    ) -> str: ...

    async def refresh_auth_state(self) -> bool:
        """Invalidate cached cookies and refetch the live auth material."""
        ...

    async def send_hooked_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]: ...

    def send_streaming_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[tuple[str, object], None]: ...

    async def save_current_cookies(self) -> bool: ...

    async def close(self) -> None: ...


# Backwards-compatible alias: existing imports keep working unchanged.
RequestSession = EngineSession


__all__ = [
    "ENGINE_BROWSER",
    "ENGINE_BROWSERLESS",
    "VALID_ENGINES",
    "EngineSession",
    "RequestSession",
]
