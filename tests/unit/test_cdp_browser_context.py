"""Behavior contracts for browser-context CDP commands and Realm WS demux."""

from __future__ import annotations

from aistudio_api.infrastructure.browser.cdp_client import CDPClient
from aistudio_api.infrastructure.gateway.build_surface import (
    _realm_connections,
    _ws_realm_id,
)


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def send(
        self, method: str, params: dict[str, object] | None = None, **kwargs
    ):
        self.sent.append((method, params or {}))
        if method == "Target.createBrowserContext":
            return {"browserContextId": "ctx-1"}
        return {}

    async def close(self) -> None:
        return None


async def test_create_and_dispose_browser_context(monkeypatch) -> None:
    client = CDPClient(0)
    conn = FakeConn()

    async def fake_browser_connection():
        return conn

    monkeypatch.setattr(client, "_browser_connection", fake_browser_connection)
    context_id = await client.create_browser_context()
    assert context_id == "ctx-1"
    await client.dispose_browser_context(context_id)
    assert [method for method, _ in conn.sent] == [
        "Target.createBrowserContext",
        "Target.disposeBrowserContext",
    ]
    assert conn.sent[0][1] == {"disposeOnDetach": True}
    assert conn.sent[1][1] == {"browserContextId": "ctx-1"}


def test_ws_realm_id_parses_query_and_defaults_to_empty() -> None:
    class Request:
        def __init__(self, path: str):
            self.path = path

    class WS:
        def __init__(self, path: str):
            self.request = Request(path)

    assert _ws_realm_id(WS("/?realm=realm-acc_a-1")) == "realm-acc_a-1"
    assert _ws_realm_id(WS("/")) == ""
    assert _ws_realm_id(WS("/?foo=1&realm=abc")) == "abc"


def test_realm_connection_buckets_are_isolated() -> None:
    default = _realm_connections(None)
    first = _realm_connections("realm-a")
    second = _realm_connections("realm-b")
    assert default is not first
    assert first is not second
    default.append(object())
    first.append(object())
    assert len(default) == 1
    assert len(first) == 1
    assert len(second) == 0
