"""Unit tests for BrowserSession lifecycle, hot switching, and identity verification."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.infrastructure.browser import browser_engine
from aistudio_api.infrastructure.browser.cdp_client import (
    CDPClient,
    CDPConnection,
    CDPPage,
)
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session import (
    DEFAULT_BOOTSTRAP_TEMPLATE,
    BrowserSession,
)
from aistudio_api.infrastructure.gateway.session_auth import verify_account_identity
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport


@pytest.mark.asyncio
async def test_session_hot_cookie_switching(tmp_path):
    """Verify single-process cookie hot switching without browser restart."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    page = MagicMock(spec=CDPPage)
    page.is_closed = MagicMock(return_value=False)
    page.cdp = MagicMock()
    page.cdp.send = AsyncMock()
    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.connect_page = AsyncMock(return_value=page)
    session._cdp_client = mock_cdp

    session._close_internal = AsyncMock()

    target_dir = tmp_path / "target_acc"
    target_dir.mkdir()
    target_auth = target_dir / "auth.json"
    target_auth.write_text(
        json.dumps(
            {
                "cookies": [{"name": "SAPISID", "value": "new_cookie"}],
                "origins": [],
            }
        )
    )
    target_meta = target_dir / "meta.json"
    target_meta.write_text(json.dumps({"id": "t1", "email": "target@gmail.com"}))

    with (
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
            AsyncMock(return_value=("snap_123", {"url": "http://test", "body": "[]"})),
        ),
    ):
        await session.switch_auth(str(target_auth))

    # Must NOT call _close_internal (no process kill)
    session._close_internal.assert_not_called()
    assert session.is_ready
    assert session.active_context is not None
    assert session.active_context.page == page


@pytest.mark.asyncio
async def test_verify_account_identity_does_not_rmtree(tmp_path):
    """Verify identity verification failure does not delete profile directory."""
    session = BrowserSession(port=9222)
    fake_meta_dir = tmp_path / "acc"
    fake_meta_dir.mkdir()
    fake_auth = fake_meta_dir / "auth.json"
    fake_auth.write_text("{}")
    fake_meta = fake_meta_dir / "meta.json"
    fake_meta.write_text('{"id": "acc_1", "email": "expected@gmail.com"}')
    fake_profile = fake_meta_dir / "profile"
    fake_profile.mkdir()
    (fake_profile / "data.txt").write_text("keep me")

    session._auth_file = str(fake_auth)
    session._profile_dir = str(fake_profile)

    page = MagicMock(spec=CDPPage)
    page.evaluate = AsyncMock(return_value=False)
    page.content = AsyncMock(return_value="<html>other page</html>")
    page.get_cookies = AsyncMock(return_value=[])

    assert await verify_account_identity(page, str(fake_auth)) is False

    assert fake_profile.exists()
    assert (fake_profile / "data.txt").exists()


@pytest.mark.asyncio
async def test_session_template_capture_universal_bootstrap():
    """Verify template capture universally uses bootstrap template to prevent DOM GC timeout."""
    session = BrowserSession(port=9222)
    mock_page = MagicMock(spec=CDPPage)
    session.set_ready_context(
        SessionContext(
            page=mock_page,
            snap_key="snap_key",
            auth_user="0",
            auth_file=None,
            base_template={
                "url": "http://bootstrap.com",
                "headers": {},
                "body": '["models/gemini-3.7-flash"]',
            },
        )
    )

    # 3.7-flash uses bootstrap
    tpl1 = await session.capture_template("gemini-3.7-flash")
    assert tpl1["url"] == "http://bootstrap.com"

    # Other models ALSO use bootstrap now, saving 30 seconds
    tpl2 = await session.capture_template("gemini-3.1-pro-preview")
    assert tpl2["url"] == "http://bootstrap.com"


@pytest.mark.asyncio
async def test_pooled_session_capture_template_does_not_mutate_base_template():
    """Model adaptation returns a copy: the cached realm template stays intact."""
    base_template: dict[str, object] = {
        "url": "http://bootstrap.com",
        "headers": {},
        "body": '["models/gemini-3.7-flash",null,null]',
    }
    context = SessionContext(
        page=MagicMock(spec=CDPPage),
        snap_key="snap_key",
        auth_user="0",
        auth_file=None,
        base_template=base_template,
    )
    session = PooledSession(
        context=context,
        transport=XHRStreamTransport(),
        realm_id="realm",
    )

    adapted = await session.capture_template("gemini-3.1-pro-preview")

    assert adapted["body"] == '["models/gemini-3.1-pro-preview",null,null]'
    assert context.base_template["body"] == '["models/gemini-3.7-flash",null,null]'
    assert base_template["body"] == '["models/gemini-3.7-flash",null,null]'


def test_session_has_no_idle_recovery_or_activity_timestamp():
    """D03-035/D-02: no idle auto-recycle and no activity timer bookkeeping."""
    session = BrowserSession(port=9222)
    assert not hasattr(session, "check_idle_timeout")
    assert not hasattr(session, "touch_activity")
    assert not hasattr(session, "_last_activity_time")
    assert not hasattr(BrowserSession, "check_idle_timeout")


def test_lifecycle_prune_watchdog_and_ping_wiring():
    """D-02/D-03/D-11: watchdog exists, prune closes spares, CDP uses WS ping."""
    assert hasattr(browser_engine, "_spawn_process_watchdog")
    assert not hasattr(CDPConnection, "set_liveness_checker")
    assert "_liveness_checker" not in CDPConnection.__dict__

    import inspect

    source = inspect.getsource(CDPConnection.connect)
    assert "ping_interval=5" in source
    assert "ping_timeout=5" in source


@pytest.mark.asyncio
async def test_switch_auth_rebinds_single_page_in_order():
    """D-05: rebind closes the old page before creating the replacement page."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = True
    session._proc = mock_proc

    old_page = MagicMock(spec=CDPPage)
    old_page.is_closed = MagicMock(return_value=False)
    old_page.close = AsyncMock()
    old_page.target_id = "old-tab"
    new_page = MagicMock(spec=CDPPage)
    new_page.is_closed = MagicMock(return_value=False)
    new_page.close = AsyncMock()
    new_page.target_id = "new-tab"
    new_page.cdp = MagicMock()
    new_page.cdp.send = AsyncMock()

    order: list[str] = []
    old_page.close.side_effect = lambda: order.append("close_old")

    async def create_new(*args, **kwargs):
        order.append("create_new")
        return new_page

    mock_cdp = MagicMock(spec=CDPClient)
    mock_cdp.create_new_page = AsyncMock(side_effect=create_new)
    mock_cdp.close_target = AsyncMock()
    session._cdp_client = mock_cdp
    session.set_ready_context(
        SessionContext(
            page=old_page,
            snap_key="old",
            auth_user="0",
            auth_file="/data/old.json",
            base_template=dict(DEFAULT_BOOTSTRAP_TEMPLATE),
        )
    )

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
            AsyncMock(return_value=("new", {"body": "[]"})),
        ),
    ):
        await session.switch_auth("/data/new.json")

    assert order == ["close_old", "create_new"]
    assert session.active_context is not None
    assert session.active_context.page is new_page


@pytest.mark.asyncio
async def test_session_capture_template_breaks_on_dead_proc():
    """Verify capture_template_flow immediately raises on dead process without 30s delay."""
    session = BrowserSession(port=9222)
    mock_proc = MagicMock()
    mock_proc.is_alive.return_value = False
    session._proc = mock_proc

    session._close_internal = AsyncMock()
    with pytest.raises(
        RuntimeError, match="Browser process died during template capture"
    ):
        await session.capture_template("models/gemini-custom-uncached")
