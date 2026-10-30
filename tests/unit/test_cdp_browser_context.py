"""Behavior contracts for BrowserContext CDP commands and pooled sessions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPClient
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []
        self.fail_dispose = False

    async def send(
        self, method: str, params: dict[str, object] | None = None, **kwargs
    ):
        self.sent.append((method, params or {}))
        if method == "Target.createBrowserContext":
            return {"browserContextId": "ctx-1"}
        if method == "Target.createTarget":
            return {"targetId": "tgt-1"}
        if method == "Target.disposeBrowserContext" and self.fail_dispose:
            raise RuntimeError("context already disposed")
        return {}

    def on(self, event: str, callback):
        return lambda: None

    async def close(self) -> None:
        return None


class FakePageConn(FakeConn):
    def __init__(self, *, fail_connect: bool = False) -> None:
        super().__init__()
        self.fail_connect = fail_connect

    async def connect(self, timeout_s: float = 10.0) -> None:
        if self.fail_connect:
            raise RuntimeError("ws connect failed")


async def _install_browser_connection(monkeypatch, client: CDPClient, conn: FakeConn):
    async def fake_browser_connection():
        return conn

    monkeypatch.setattr(client, "_browser_connection", fake_browser_connection)


@pytest.mark.asyncio
async def test_create_and_dispose_browser_context(monkeypatch) -> None:
    client = CDPClient(0)
    conn = FakeConn()
    await _install_browser_connection(monkeypatch, client, conn)

    context_id = await client.create_browser_context()
    assert context_id == "ctx-1"
    await client.dispose_browser_context(context_id)

    assert [method for method, _ in conn.sent] == [
        "Target.createBrowserContext",
        "Target.disposeBrowserContext",
    ]
    assert conn.sent[0][1] == {"disposeOnDetach": True}
    assert conn.sent[1][1] == {"browserContextId": "ctx-1"}


@pytest.mark.asyncio
async def test_dispose_browser_context_ignores_missing_or_disposed(monkeypatch) -> None:
    client = CDPClient(0)
    conn = FakeConn()
    conn.fail_dispose = True
    await _install_browser_connection(monkeypatch, client, conn)

    await client.dispose_browser_context("")
    assert conn.sent == []

    await client.dispose_browser_context("ctx-gone")
    assert conn.sent[-1][0] == "Target.disposeBrowserContext"


@pytest.mark.asyncio
async def test_create_page_in_context_polls_json_list(monkeypatch) -> None:
    client = CDPClient(0)
    conn = FakeConn()
    page_conn = FakePageConn()
    await _install_browser_connection(monkeypatch, client, conn)
    client.wait_until_ready = AsyncMock()
    client.get_targets = AsyncMock(
        return_value=[
            {
                "id": "tgt-1",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tgt-1",
            }
        ]
    )

    with patch(
        "aistudio_api.infrastructure.browser.cdp_client.CDPConnection",
        return_value=page_conn,
    ):
        page = await client.create_page_in_context("ctx-1", block_assets=True)

    assert page.target_id == "tgt-1"
    assert conn.sent[0][0] == "Target.createTarget"
    assert conn.sent[0][1] == {
        "url": "about:blank",
        "browserContextId": "ctx-1",
    }
    assert any(method == "Network.setBlockedURLs" for method, _ in page_conn.sent)


@pytest.mark.asyncio
async def test_create_page_in_context_closes_target_on_connect_failure(
    monkeypatch,
) -> None:
    client = CDPClient(0)
    conn = FakeConn()
    await _install_browser_connection(monkeypatch, client, conn)
    client.wait_until_ready = AsyncMock()
    client.get_targets = AsyncMock(
        return_value=[
            {
                "id": "tgt-1",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/tgt-1",
            }
        ]
    )

    with (
        patch(
            "aistudio_api.infrastructure.browser.cdp_client.CDPConnection",
            return_value=FakePageConn(fail_connect=True),
        ),
        pytest.raises(RuntimeError, match="ws connect failed"),
    ):
        await client.create_page_in_context("ctx-1")

    assert ("Target.closeTarget", {"targetId": "tgt-1"}) in conn.sent


@pytest.mark.asyncio
async def test_create_page_in_context_closes_target_on_target_timeout(
    monkeypatch,
) -> None:
    client = CDPClient(0)
    conn = FakeConn()
    await _install_browser_connection(monkeypatch, client, conn)
    client.wait_until_ready = AsyncMock()
    client.get_targets = AsyncMock(return_value=[])

    with pytest.raises(TimeoutError, match="did not appear"):
        await client.create_page_in_context("ctx-1", timeout_s=0.05)

    assert ("Target.closeTarget", {"targetId": "tgt-1"}) in conn.sent


@pytest.mark.asyncio
async def test_pooled_session_bootstraps_inside_given_context(monkeypatch) -> None:
    page = MagicMock()
    page.is_closed.return_value = False
    page.close = AsyncMock()
    cdp = MagicMock(spec=CDPClient)
    cdp.create_page_in_context = AsyncMock(return_value=page)

    with (
        patch(
            "aistudio_api.infrastructure.gateway.pooled_session.load_cookies_to_page",
            AsyncMock(return_value=True),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.pooled_session.navigate_to_aistudio",
            AsyncMock(),
        ),
        patch(
            "aistudio_api.infrastructure.gateway.pooled_session.bootstrap_botguard_and_template",
            AsyncMock(return_value=("snap_key", {"url": "https://example.com"})),
        ),
    ):
        session = await PooledSession.bootstrap(
            cdp,
            context_id="ctx-1",
            auth_file="/data/acc/auth.json",
            auth_user="0",
            realm_id="realm-acc",
        )

    cdp.create_page_in_context.assert_awaited_once_with("ctx-1", block_assets=True)
    assert page.set_liveness_checker.called is False
    assert session.active_context.page is page
    assert session.active_context.snap_key == "snap_key"
    assert page.account_auth_file == "/data/acc/auth.json"

    await session.close()
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_pooled_session_generate_snapshot_retries_five_times_with_250ms() -> None:
    """Pooled snapshot failures retry 5 times with a 250ms pause between attempts."""
    page = MagicMock()
    page.is_closed.return_value = False
    page.evaluate = AsyncMock(side_effect=RuntimeError("snapshot not ready"))
    page.wait_for_timeout = AsyncMock()
    context = SessionContext(
        page=page,
        snap_key="snap_key",
        auth_user="0",
        auth_file=None,
        base_template={"url": "https://example.com"},
    )
    session = PooledSession(
        context=context, transport=MagicMock(), realm_id="realm-acc"
    )

    contents = [AistudioContent(role="user", parts=[AistudioPart(text="retry")])]

    with pytest.raises(RuntimeError, match="PooledSession snapshot generation failed"):
        await session.generate_snapshot(contents)

    assert page.evaluate.await_count == 5
    assert page.wait_for_timeout.await_count == 4
    assert [call.args for call in page.wait_for_timeout.await_args_list] == [
        (250,)
    ] * 4
