"""Traditional browser engine adapter around :class:`PooledSession`.

The wrapper exists so :class:`InstancePool` can treat both engines through one
``EngineSession`` surface without changing any browser behaviour or tests.  All
attribute access and lifecycle calls delegate to the wrapped ``PooledSession``.
"""

from __future__ import annotations

from typing import Any

from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext


class BrowserEngineSession:
    """``EngineSession`` implementation for CloakBrowser/CDP request slots."""

    engine = "browser"

    def __init__(self, inner: PooledSession) -> None:
        self._inner = inner

    @classmethod
    async def bootstrap(cls, cdp: Any, **kwargs: Any) -> BrowserEngineSession:
        inner = await PooledSession.bootstrap(cdp, **kwargs)
        return cls(inner)

    @property
    def inner(self) -> PooledSession:
        return self._inner

    @property
    def active_context(self) -> SessionContext:
        return self._inner.active_context

    @property
    def capture_service(self) -> Any:
        return self._inner.capture_service

    @property
    def page(self) -> Any:
        return self._inner.page

    @property
    def in_flight_count(self) -> int:
        return self._inner.in_flight_count

    def is_alive(self) -> bool:
        return self._inner.is_alive()

    def request_scope(self) -> Any:
        return self._inner.request_scope()

    async def ensure_ready(self, timeout_s: float = 120.0) -> SessionContext:
        return await self._inner.ensure_ready(timeout_s)

    async def capture_template(self, model: str) -> dict[str, object]:
        return await self._inner.capture_template(model)

    async def generate_snapshot(
        self, contents: Any, *, payload_size: int | None = None
    ) -> str:
        return await self._inner.generate_snapshot(contents, payload_size=payload_size)

    async def refresh_auth_state(self) -> bool:
        return await self._inner.refresh_auth_state()

    async def send_hooked_request(self, **kwargs: Any) -> tuple[int, bytes]:
        return await self._inner.send_hooked_request(**kwargs)

    def send_streaming_request(self, **kwargs: Any) -> Any:
        return self._inner.send_streaming_request(**kwargs)

    async def save_current_cookies(self) -> bool:
        return await self._inner.save_current_cookies()

    async def close(self) -> None:
        await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        # Fallback for optional browser-only attributes used by routes/keepalive
        # (for example ``_auth_file`` or ``extract_account_tier``).
        return getattr(self._inner, name)


__all__ = ["BrowserEngineSession"]
