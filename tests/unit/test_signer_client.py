"""Offline WAA signer HTTP client contract tests (MockTransport only)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from aistudio_api.infrastructure.waa.signer_client import (
    SignerClient,
    SignerError,
    SignerProtocolError,
    SignerTimeoutError,
    SignerUnavailableError,
)

_COOKIE_TEXT = json.dumps(
    {"cookies": [{"name": "SAPISID", "value": "v", "domain": ".google.com"}]}
)


def _set_cookies_header(cookies: list[dict[str, object]]) -> str:
    payload = json.dumps(cookies).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _client(handler: object) -> tuple[SignerClient, list[str]]:
    calls: list[str] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        handler_fn = handler
        return await handler_fn(request)  # type: ignore[operator]

    transport = httpx.MockTransport(wrapped)
    http = httpx.AsyncClient(transport=transport)
    return (
        SignerClient(
            "http://127.0.0.1:8081",
            request_timeout_s=5,
            connect_timeout_s=2,
            http_client=http,
        ),
        calls,
    )


@pytest.mark.asyncio
async def test_health_round_trip() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "workers": 2,
                "maxWorkers": 8,
                "version": "1.2.3",
                "ready": True,
                "engine": "firefox152",
            },
        )

    client, calls = _client(handler)
    health = await client.health()
    assert health.ok and health.ready
    assert health.workers == 2 and health.max_workers == 8
    assert health.version == "1.2.3"
    assert calls == ["http://127.0.0.1:8081/health"]


@pytest.mark.asyncio
async def test_mint_and_rpc_and_release() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/proof/mint":
            body = json.loads(request.content)
            assert body["cookies"]
            assert body["digest"] == "abc"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "proof": "!proof-123",
                    "digest": "abc",
                    "accountKey": "acct-key",
                    "setCookies": [{"name": "SAPISID", "value": "new"}],
                    "latencyMs": 12,
                },
            )
        if request.url.path == "/v1/rpc/send":
            return httpx.Response(
                200,
                content=b"wire-body",
                headers={
                    "content-type": "application/json+protobuf",
                    "x-waa-account-key": "acct-key",
                    "x-waa-set-cookies": _set_cookies_header(
                        [{"name": "SID", "value": "rotated"}]
                    ),
                },
            )
        if request.url.path == "/v1/worker/release":
            return httpx.Response(200, json={"ok": True, "released": 1})
        raise AssertionError(request.url.path)

    client, _ = _client(handler)
    mint = await client.mint_proof(
        cookies=_COOKIE_TEXT, prompt="hi", page_prompt="hi", digest="abc"
    )
    assert mint.ok and mint.proof == "!proof-123"
    assert mint.account_key == "acct-key"
    assert mint.set_cookies == [{"name": "SAPISID", "value": "new"}]

    rpc = await client.send_rpc(cookies=_COOKIE_TEXT, body="[]", url="https://x")
    assert rpc.status == 200 and rpc.body == b"wire-body"
    assert rpc.account_key == "acct-key"
    assert rpc.set_cookies == [{"name": "SID", "value": "rotated"}]

    released = await client.release_worker(account_key="acct-key")
    assert released["ok"] is True


@pytest.mark.asyncio
async def test_stream_rpc_yields_status_and_chunks() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"chunk-1")

    client, _ = _client(handler)
    events = [
        event
        async for event in client.stream_rpc(
            cookies=_COOKIE_TEXT, body="[]", url="https://x"
        )
    ]
    assert events[0] == ("status", 200)
    assert events[1] == ("chunk", b"chunk-1")


@pytest.mark.asyncio
async def test_connect_error_maps_to_unavailable_without_remote_fallback() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, calls = _client(handler)
    with pytest.raises(SignerUnavailableError):
        await client.health()
    assert len(calls) == 1
    assert all("127.0.0.1:8081" in url for url in calls)


@pytest.mark.asyncio
async def test_read_timeout_maps_to_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, _ = _client(handler)
    with pytest.raises(SignerTimeoutError):
        await client.health()


@pytest.mark.asyncio
async def test_server_error_maps_to_signer_error_without_secret_echo() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "worker acquire failed"})

    client, _ = _client(handler)
    with pytest.raises(SignerError) as excinfo:
        await client.mint_proof(cookies="SECRET-COOKIE", digest="x")
    assert excinfo.value.status_code == 502
    assert "SECRET-COOKIE" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_invalid_mint_payload_is_protocol_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _ = _client(handler)
    with pytest.raises(SignerProtocolError):
        await client.mint_proof(cookies=_COOKIE_TEXT, digest="x")


@pytest.mark.asyncio
async def test_mint_parses_deleted_and_truncated_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["accountKey"] == "acct-1"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "proof": "!p",
                "accountKey": "acct-1",
                "setCookies": [{"name": "A", "value": "1"}],
                "deletedCookies": [{"name": "B", "value": "2"}],
                "setCookiesTruncated": True,
            },
        )

    client, _ = _client(handler)
    result = await client.mint_proof(
        cookies=_COOKIE_TEXT, digest="d", account_key="acct-1"
    )
    assert result.deleted_cookies == [{"name": "B", "value": "2"}]
    assert result.truncated is True
    assert result.set_cookies == [{"name": "A", "value": "1"}]


@pytest.mark.asyncio
async def test_rpc_reads_deleted_and_truncated_headers() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["accountKey"] == "acct-2"
        return httpx.Response(
            200,
            content=b"ok",
            headers={
                "x-waa-set-cookies": _set_cookies_header([{"name": "A", "value": "1"}]),
                "x-waa-set-cookies-truncated": "1",
                "x-waa-deleted-cookies": _set_cookies_header(
                    [{"name": "B", "value": "2"}]
                ),
            },
        )

    client, _ = _client(handler)
    result = await client.send_rpc(
        cookies=_COOKIE_TEXT, body="[]", url="https://x", account_key="acct-2"
    )
    assert result.truncated is True
    assert result.deleted_cookies == [{"name": "B", "value": "2"}]
    assert result.set_cookies == [{"name": "A", "value": "1"}]


@pytest.mark.asyncio
async def test_stream_metadata_includes_truncated_flag() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"body",
            headers={"x-waa-set-cookies-truncated": "true"},
        )

    client, _ = _client(handler)
    seen: list[tuple[str, list[dict[str, object]], bool]] = []
    _ = [
        event
        async for event in client.stream_rpc(
            cookies=_COOKIE_TEXT,
            body="[]",
            url="https://x",
            on_metadata=lambda key, cookies, truncated: seen.append(
                (key, cookies, truncated)
            ),
        )
    ]
    assert seen == [("", [], True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cookies",
    ["", "{}", '{"cookies": []}', "null", "[]"],
)
async def test_empty_cookies_are_rejected(cookies: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("empty cookies must not reach the signer")

    client, _ = _client(handler)
    with pytest.raises(SignerProtocolError):
        await client.mint_proof(cookies=cookies, digest="d")
    with pytest.raises(SignerProtocolError):
        await client.send_rpc(cookies=cookies, body="[]", url="https://x")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"workers": "abc"}, {"maxWorkers": -1}])
async def test_health_rejects_bad_numbers(payload: dict[str, object]) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**payload, "ready": True, "ok": True})

    client, _ = _client(handler)
    with pytest.raises(SignerProtocolError):
        await client.health()


@pytest.mark.asyncio
async def test_request_and_read_timeouts_are_applied() -> None:
    client = SignerClient(
        "http://127.0.0.1:8081",
        request_timeout_s=7,
        read_timeout_s=11,
        connect_timeout_s=3,
    )
    assert client._client.timeout.read == 11
    assert client._client.timeout.connect == 3
    await client.close()
