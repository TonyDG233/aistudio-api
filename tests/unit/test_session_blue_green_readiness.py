"""Single-page rebind contracts: no readiness gate, no blue-green, bounded drain."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway import session_state
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext


def _page(target_id: str, url: str) -> MagicMock:
    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page.close = AsyncMock()
    page.url = url
    page.target_id = target_id
    return page


def _context(page: MagicMock, *, in_flight: int = 0) -> SessionContext:
    return SessionContext(
        page=page,
        snap_key="snap_old",
        auth_user="0",
        auth_file="/data/acc1.json",
        base_template={"body": '["models/gemini-3.7-flash"]'},
        in_flight=in_flight,
    )


def test_readiness_gate_and_blue_green_state_removed():
    """D03-019/D-10: the readiness gate and retiring-context bookkeeping are gone."""
    assert not hasattr(session_state, "SessionReadinessGate")
    assert not hasattr(session_state, "SessionState")

    session = BrowserSession(port=9222)
    assert not hasattr(session, "_gate")
    assert not hasattr(session, "_retiring_contexts")
    assert not hasattr(session, "_background_tasks")


@pytest.mark.asyncio
async def test_rebind_waits_for_old_page_then_creates_new_page():
    """D-05/D-10/D-11: wait on the old page, close it, only then create the new page."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    old_page = _page("page_tab_1", "https://aistudio.google.com/u/0/prompts/new_chat")
    new_page = _page("page_tab_2", "https://aistudio.google.com/u/1/prompts/new_chat")
    order: list[str] = []

    old_page.close.side_effect = lambda: order.append("close_old")

    async def create_new(*args, **kwargs):
        order.append("create_new")
        return new_page

    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.connect_page = AsyncMock()
    mock_cdp.create_new_page = AsyncMock(side_effect=create_new)
    mock_cdp.close_target = AsyncMock()
    session._cdp_client = mock_cdp

    old_ctx = _context(old_page, in_flight=1)
    session.set_ready_context(old_ctx)
    session._in_flight = 1

    with (
        patch(
            "aistudio_api.infrastructure.gateway.session.save_browser_cookies",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.load_cookies_to_page",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.navigate_to_aistudio",
            AsyncMock(),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.bootstrap_botguard_and_template",
            AsyncMock(
                return_value=("snap_new", {"body": '["models/gemini-3.8-flash"]'})
            ),
        ),
    ):
        task = asyncio.create_task(
            session.transition_to_account("/data/acc2.json", force_refresh=True)
        )
        await asyncio.sleep(0.05)
        # Draining: no replacement page may be created yet.
        assert mock_cdp.create_new_page.await_count == 0
        old_page.close.assert_not_called()

        old_ctx.decr_in_flight()
        session._in_flight = 0
        new_ctx = await asyncio.wait_for(task, timeout=2.0)

    assert new_ctx.page is new_page
    assert session.active_context is new_ctx
    assert order == ["close_old", "create_new"]
    old_page.close.assert_awaited_once()
    mock_cdp.close_target.assert_awaited_once_with("page_tab_1")
    mock_cdp.connect_page.assert_not_awaited()
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("drain_")]


@pytest.mark.asyncio
async def test_rebind_drain_timeout_force_closes_old_page():
    """D-10: one bounded wait; on timeout force-close instead of retrying forever."""
    session = BrowserSession(port=9222)
    session._drain_timeout_s = 0.05
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    old_page = _page("page_tab_1", "https://aistudio.google.com/u/0/")
    new_page = _page("page_tab_2", "https://aistudio.google.com/u/1/")
    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.create_new_page = AsyncMock(return_value=new_page)
    mock_cdp.close_target = AsyncMock()
    session._cdp_client = mock_cdp

    old_ctx = _context(old_page, in_flight=1)
    session.set_ready_context(old_ctx)
    session._in_flight = 1

    with (
        patch(
            "aistudio_api.infrastructure.gateway.session.save_browser_cookies",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.load_cookies_to_page",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.navigate_to_aistudio",
            AsyncMock(),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.bootstrap_botguard_and_template",
            AsyncMock(
                return_value=("snap_new", {"body": '["models/gemini-3.8-flash"]'})
            ),
        ),
    ):
        new_ctx = await asyncio.wait_for(
            session.transition_to_account("/data/acc2.json", force_refresh=True),
            timeout=2.0,
        )

    assert new_ctx.page is new_page
    old_page.close.assert_awaited_once()
    mock_cdp.close_target.assert_awaited_once_with("page_tab_1")
    assert (
        old_ctx.in_flight == 1
    )  # never decremented; forced close still released the page


@pytest.mark.asyncio
async def test_rebind_after_process_death_leaves_exactly_one_page():
    """P-02/D-11: dead-process rebind must not connect a page before the new one."""
    session = BrowserSession(port=9222)
    dead_proc = MagicMock()
    dead_proc.is_alive.return_value = False
    session._proc = dead_proc

    old_page = _page("page_tab_1", "https://aistudio.google.com/u/0/")
    new_page = _page("page_tab_2", "https://aistudio.google.com/u/1/")
    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.create_new_page = AsyncMock(return_value=new_page)
    mock_cdp.connect_page = AsyncMock()
    mock_cdp.close_target = AsyncMock()

    ensure_calls: list[bool] = []

    async def fake_ensure_browser_cdp(*, connect_page: bool = True):
        ensure_calls.append(connect_page)
        session._cdp_client = mock_cdp
        return

    session._ensure_browser_cdp = AsyncMock(side_effect=fake_ensure_browser_cdp)
    session.set_ready_context(_context(old_page))

    with (
        patch(
            "aistudio_api.infrastructure.gateway.session.save_browser_cookies",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.load_cookies_to_page",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.navigate_to_aistudio",
            AsyncMock(),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.session.bootstrap_botguard_and_template",
            AsyncMock(
                return_value=("snap_new", {"body": '["models/gemini-3.8-flash"]'})
            ),
        ),
    ):
        new_ctx = await asyncio.wait_for(
            session.transition_to_account("/data/acc2.json", force_refresh=True),
            timeout=2.0,
        )

    assert ensure_calls == [False]
    mock_cdp.connect_page.assert_not_awaited()
    mock_cdp.create_new_page.assert_awaited_once()
    old_page.close.assert_awaited_once()
    assert new_ctx.page is new_page
    assert session.active_context is new_ctx
    assert mock_cdp.connect_page.await_count + mock_cdp.create_new_page.await_count == 1
