"""Structural session surface shared by control and pooled request sessions."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent


class RequestSession(Protocol):
    """The subset of BrowserSession/PooledSession used by capture/replay."""

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


__all__ = ["RequestSession"]
