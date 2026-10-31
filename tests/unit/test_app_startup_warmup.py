"""Startup warmup isolation: pool sessions never double-warm the control Page."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from aistudio_api.api import app as app_module
from aistudio_api.api.state import RuntimeState
from aistudio_api.infrastructure.gateway import model_discovery as discovery_module
from aistudio_api.infrastructure.gateway.session import BrowserSession


@pytest.mark.asyncio
async def test_pool_enabled_startup_never_double_warms_control_and_pooled_sessions():
    """Regression: the exact bug that opened a control Page plus a pooled Page."""
    client = SimpleNamespace(
        warmup=AsyncMock(),
        session=SimpleNamespace(ensure_cdp_client=AsyncMock()),
    )
    manager = SimpleNamespace(startup=AsyncMock())

    warmup_task = app_module._start_control_warmup(
        cast(Any, client), pool_enabled=True
    )
    await app_module._startup_instances(
        cast(Any, SimpleNamespace(instance_manager=manager)), warmup_task
    )

    assert warmup_task is None
    assert client.warmup.await_count == 0
    assert client.session.ensure_cdp_client.await_count == 0
    assert manager.startup.await_count == 1


@pytest.mark.asyncio
async def test_pool_disabled_keeps_legacy_control_warmup_allowed():
    client = SimpleNamespace(warmup=AsyncMock())
    manager = SimpleNamespace(startup=AsyncMock())

    warmup_task = app_module._start_control_warmup(
        cast(Any, client), pool_enabled=False
    )
    assert warmup_task is not None
    await app_module._startup_instances(
        cast(Any, SimpleNamespace(instance_manager=manager)), warmup_task
    )

    assert client.warmup.await_count == 1
    assert manager.startup.await_count == 1


@pytest.mark.asyncio
async def test_ensure_cdp_client_does_not_open_or_warm_a_control_page(monkeypatch):
    session = BrowserSession(port=9222)
    sentinel = cast(Any, object())
    session._cdp_client = sentinel
    ensure = AsyncMock(return_value=None)
    monkeypatch.setattr(session, "_ensure_browser_cdp", ensure)

    assert await session.ensure_cdp_client() is sentinel
    ensure.assert_awaited_once_with(connect_page=False)
    assert session.page is None


@pytest.mark.asyncio
async def test_model_catalog_refresh_uses_pool_sessions_only(monkeypatch):
    pool_session = object()
    control_session = object()
    seen: list[tuple[object, ...]] = []

    async def _get_models(**kwargs):
        seen.append(kwargs["sessions"])
        return [{"id": "pool-model"}]

    monkeypatch.setattr(
        discovery_module.model_discovery, "get_models", _get_models
    )
    state = RuntimeState()
    state.instance_manager = cast(
        Any, SimpleNamespace(discovery_sessions=lambda: (pool_session,))
    )
    state.client = cast(Any, SimpleNamespace(_session=control_session))
    try:
        assert await app_module._refresh_model_catalog_once(state) == 1
    finally:
        state.instance_manager = None
        state.client = None

    assert seen == [(pool_session,)]
    assert control_session not in seen[0]


def test_runtime_state_discovery_sessions_never_falls_back_to_control():
    control_session = object()
    state = RuntimeState()
    state.instance_manager = cast(
        Any, SimpleNamespace(discovery_sessions=lambda: ())
    )
    state.client = cast(Any, SimpleNamespace(_session=control_session))
    try:
        assert state.discovery_sessions() == ()
    finally:
        state.instance_manager = None
        state.client = None


@pytest.mark.asyncio
async def test_shutdown_ignores_absent_control_warmup_tasks():
    state = SimpleNamespace(
        keepalive=None,
        instance_manager=None,
        client=None,
        account_service=None,
        rotator=None,
    )

    await app_module._shutdown_runtime(cast(Any, state), (None, None, None))

    assert state.client is None


@pytest.mark.asyncio
async def test_shutdown_cancels_and_gathers_unfinished_background_tasks():
    """N5: _shutdown_runtime cancels live tasks and waits for cancellation."""

    async def _hang() -> None:
        await asyncio.sleep(3600)

    pending = asyncio.create_task(_hang())
    pending_other = asyncio.create_task(_hang())
    already_done = asyncio.create_task(asyncio.sleep(0))
    await already_done

    state = SimpleNamespace(
        keepalive=None,
        instance_manager=None,
        client=None,
        account_service=None,
        rotator=None,
    )

    await app_module._shutdown_runtime(
        cast(Any, state), (pending, None, pending_other, already_done)
    )

    assert pending.cancelled()
    assert pending_other.cancelled()
    assert pending.done()
    assert pending_other.done()
    assert not already_done.cancelled()
