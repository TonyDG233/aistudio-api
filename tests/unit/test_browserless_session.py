"""BrowserlessSession unit tests: per-slot isolation, proof, cookies, transport."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.waa.browserless_session import BrowserlessSession
from aistudio_api.infrastructure.waa.signer_client import MintProofResult, RpcResult


@pytest.fixture(autouse=True)
def _offline_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep BrowserlessSession.refresh_auth_state offline by default."""
    from aistudio_api.infrastructure.waa import browserless_session as module

    async def _skip(cookies: dict[str, str], **kwargs: object) -> dict[str, object]:
        del cookies, kwargs
        return {"ok": False, "status": 0, "updated": []}

    monkeypatch.setattr(module, "rotate_google_cookies", _skip)


class FakeSigner:
    def __init__(self) -> None:
        self.mints: list[dict[str, object]] = []
        self.rpc_calls: list[dict[str, object]] = []
        self.released: list[str] = []
        self.events: list[str] = []
        self.proof = "proof-abc"
        self.response_status = 200
        self.response_body = b"[]"
        self.stream_events: list[tuple[str, object]] = [
            ("status", 200),
            ("chunk", b"[]"),
        ]
        self.mint_headers: dict[str, str] = {}
        self.mint_deleted_cookies: list[dict[str, object]] = []
        self.mint_truncated = False
        self.rpc_set_cookies: list[dict[str, object]] = [
            {"name": "SID", "value": "rotated", "domain": ".google.com"}
        ]
        self.rpc_deleted_cookies: list[dict[str, object]] = []
        self.rpc_truncated = False
        self.stream_account_key = "acct"
        self.stream_set_cookies: list[dict[str, object]] = []

    async def mint_proof(self, **kwargs: object) -> MintProofResult:
        self.mints.append(dict(kwargs))
        self.events.append("mint")
        return MintProofResult(
            ok=True,
            proof=self.proof,
            digest=str(kwargs.get("digest") or ""),
            account_key="acct",
            headers=dict(self.mint_headers),
            set_cookies=[
                {"name": "SAPISID", "value": "minted", "domain": ".google.com"}
            ],
            deleted_cookies=list(self.mint_deleted_cookies),
            truncated=self.mint_truncated,
            latency_ms=3.5,
        )

    async def send_rpc(self, **kwargs: object) -> RpcResult:
        self.rpc_calls.append(dict(kwargs))
        return RpcResult(
            status=self.response_status,
            body=self.response_body,
            account_key="acct",
            set_cookies=list(self.rpc_set_cookies),
            deleted_cookies=list(self.rpc_deleted_cookies),
            truncated=self.rpc_truncated,
        )

    async def stream_rpc(self, **kwargs: object):
        self.rpc_calls.append(dict(kwargs))
        on_metadata = kwargs.get("on_metadata")
        if callable(on_metadata):
            on_metadata(
                self.stream_account_key,
                list(self.stream_set_cookies),
                self.rpc_truncated,
            )
        for event in self.stream_events:
            yield event

    async def release_worker(
        self, *, account_key: str | None = None
    ) -> dict[str, object]:
        self.released.append(account_key or "")
        self.events.append("release")
        return {"ok": True}


def _write_auth(tmp_path: Path, name: str, value: str) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": value,
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {
                        "name": "SID",
                        "value": f"sid-{value}",
                        "domain": ".google.com",
                        "path": "/",
                    },
                ],
                "origins": [{"origin": "https://aistudio.google.com"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _session(
    signer: FakeSigner,
    auth_file: Path,
    *,
    transport_mode: str = "signer_rpc",
    account_id: str = "a",
    http_client: httpx.AsyncClient | None = None,
) -> BrowserlessSession:
    return BrowserlessSession(
        signer=signer,  # type: ignore[arg-type]
        auth_file=str(auth_file),
        auth_user="0",
        tier="Free",
        account_id=account_id,
        realm_id=f"{account_id}-g1",
        transport_mode=transport_mode,
        direct_http_client=http_client,
    )


@pytest.mark.asyncio
async def test_single_slot_proof_and_no_page(tmp_path: Path) -> None:
    signer = FakeSigner()
    session = _session(signer, _write_auth(tmp_path, "a", "cookie-a"))
    assert session.page is None
    assert session.engine == "browserless"
    contents = [AistudioContent.from_user_prompt("hello world")]

    proof = await session.generate_snapshot(contents)
    assert proof == "!proof-abc"
    assert signer.mints[0]["prompt"] == "hello world"
    assert signer.mints[0]["g1_tier"] == ""
    assert signer.mints[0]["auth_user"] == "0"
    assert session.last_proof_latency_ms == 3.5
    template = await session.capture_template("models/gemini-3.7-flash")
    assert template["body"] == '["models/gemini-3.7-flash"]'


@pytest.mark.asyncio
async def test_two_slots_are_fully_isolated(tmp_path: Path) -> None:
    signer_a = FakeSigner()
    signer_b = FakeSigner()
    session_a = _session(
        signer_a, _write_auth(tmp_path, "a", "cookie-a"), account_id="a"
    )
    session_b = _session(
        signer_b, _write_auth(tmp_path, "b", "cookie-b"), account_id="b"
    )
    contents = [AistudioContent.from_user_prompt("hi")]

    await session_a.generate_snapshot(contents)
    await session_b.generate_snapshot(contents)

    assert "cookie-a" in str(signer_a.mints[0]["cookies"])
    assert "cookie-b" in str(signer_b.mints[0]["cookies"])
    assert "cookie-b" not in str(signer_a.mints[0]["cookies"])
    assert "cookie-a" not in str(signer_b.mints[0]["cookies"])
    assert session_a.account_key == "acct"
    assert session_a.active_context is not session_b.active_context


@pytest.mark.asyncio
async def test_signer_rpc_request_and_stream(tmp_path: Path) -> None:
    signer = FakeSigner()
    signer.response_body = b"rpc-body"
    session = _session(signer, _write_auth(tmp_path, "a", "cookie-a"))

    status, body = await session.send_hooked_request(body="[]", url="https://x")
    assert (status, body) == (200, b"rpc-body")
    assert signer.rpc_calls[0]["cookies"]
    assert "cookie-a" in str(signer.rpc_calls[0]["cookies"])

    events = [
        event
        async for event in session.send_streaming_request(body="[]", url="https://x")
    ]
    assert events == [("status", 200), ("chunk", b"[]")]


@pytest.mark.asyncio
async def test_direct_http_transport_uses_own_cookie_header(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["cookie"] = request.headers.get("cookie", "")
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, content=b"direct-body")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    signer = FakeSigner()
    session = _session(
        signer,
        _write_auth(tmp_path, "a", "cookie-a"),
        transport_mode="direct_http",
        http_client=http,
    )
    status, body = await session.send_hooked_request(
        body='["models/x"]', url="https://generativelanguage.googleapis.com/v1beta/x"
    )
    assert status == 200 and body == b"direct-body"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/x"
    assert "SAPISID=cookie-a" in str(captured["cookie"])
    assert str(captured["auth"]).startswith("SAPISIDHASH ")
    assert captured["body"] == '["models/x"]'
    assert signer.rpc_calls == []
    await http.aclose()


@pytest.mark.asyncio
async def test_save_current_cookies_merges_and_preserves_origins(
    tmp_path: Path,
) -> None:
    signer = FakeSigner()
    auth = _write_auth(tmp_path, "a", "cookie-a")
    session = _session(signer, auth)
    await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])

    assert await session.save_current_cookies() is True
    data = json.loads(auth.read_text(encoding="utf-8"))
    names = {cookie["name"]: cookie["value"] for cookie in data["cookies"]}
    assert names["SAPISID"] == "minted"
    assert names["SID"] == "sid-cookie-a"
    assert data["origins"] == [{"origin": "https://aistudio.google.com"}]


@pytest.mark.asyncio
async def test_refresh_auth_state_reloads_and_remints(tmp_path: Path) -> None:
    signer = FakeSigner()
    auth = _write_auth(tmp_path, "a", "cookie-a")
    session = _session(signer, auth)
    assert await session.refresh_auth_state() is True
    assert len(signer.mints) == 1

    auth.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": "cookie-new",
                        "domain": ".google.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    assert await session.refresh_auth_state() is True
    assert "cookie-new" in str(signer.mints[-1]["cookies"])


@pytest.mark.asyncio
async def test_refresh_auth_state_persists_pending_rpc_cookies(
    tmp_path: Path,
) -> None:
    signer = FakeSigner()
    auth = _write_auth(tmp_path, "a", "cookie-a")
    session = _session(signer, auth)
    await session.send_hooked_request(body="[]", url="https://x")

    assert await session.refresh_auth_state() is True
    # The RPC rotation received before the refresh must survive the disk reload.
    assert _auth_cookies(auth)["SID"] == "rotated"


@pytest.mark.asyncio
async def test_refresh_auth_state_rotates_persists_and_rebuilds_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.waa import browserless_session as module

    seen: list[dict[str, str]] = []

    async def rotate(cookies: dict[str, str], **kwargs: object) -> dict[str, object]:
        del kwargs
        seen.append(dict(cookies))
        cookies["__Secure-1PSIDTS"] = "fresh-ts"
        return {"ok": True, "status": 200, "updated": ["__Secure-1PSIDTS"]}

    monkeypatch.setattr(module, "rotate_google_cookies", rotate)
    signer = FakeSigner()
    auth = _write_auth(tmp_path, "a", "cookie-a")
    session = _session(signer, auth)

    assert await session.refresh_auth_state() is True

    assert seen and seen[0]["SAPISID"] == "cookie-a"
    assert _auth_cookies(auth)["__Secure-1PSIDTS"] == "fresh-ts"
    # The Go worker is released before the fresh proof is minted.
    assert signer.events[:2] == ["release", "mint"]
    assert signer.released == ["a"]
    assert "fresh-ts" in str(signer.mints[-1]["cookies"])


@pytest.mark.asyncio
async def test_close_releases_worker(tmp_path: Path) -> None:
    signer = FakeSigner()
    session = _session(signer, _write_auth(tmp_path, "a", "cookie-a"))
    await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    await session.close()
    assert signer.released == ["a"]
    assert session.is_alive() is False


@pytest.mark.asyncio
async def test_capture_model_and_mint_headers_flow_into_rpc(tmp_path: Path) -> None:
    signer = FakeSigner()
    signer.mint_headers = {
        "User-Agent": "WAA/9.9",
        "X-AIStudio-Visit-Id": "visit-123",
        "X-Goog-Ext-777": "ext-bin",
    }
    session = _session(signer, _write_auth(tmp_path, "a", "cookie-a"))
    await session.capture_template("models/gemini-3.8-pro")
    await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    assert signer.mints[0]["model"] == "models/gemini-3.8-pro"

    await session.send_hooked_request(body="[]", url="https://x")
    sent_headers = signer.rpc_calls[0]["headers"]
    assert isinstance(sent_headers, dict)
    assert sent_headers["User-Agent"] == "WAA/9.9"
    assert sent_headers["X-AIStudio-Visit-Id"] == "visit-123"
    assert sent_headers["X-Goog-Ext-777"] == "ext-bin"


@pytest.mark.asyncio
async def test_direct_http_uses_mint_headers_and_http2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.waa import browserless_session as module

    captured: dict[str, object] = {}
    request_headers: dict[str, str] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            del url
            raw_headers = kwargs.get("headers")
            if isinstance(raw_headers, dict):
                request_headers.update({str(k): str(v) for k, v in raw_headers.items()})
            return httpx.Response(200, content=b"direct-body")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    signer = FakeSigner()
    signer.mint_headers = {
        "User-Agent": "WAA/9.9",
        "X-AIStudio-Visit-Id": "visit-direct",
    }
    session = _session(
        signer,
        _write_auth(tmp_path, "a", "cookie-a"),
        transport_mode="direct_http",
    )
    await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    status, body = await session.send_hooked_request(body="[]", url="https://x")
    assert status == 200 and body == b"direct-body"
    assert captured["http2"] is True
    assert request_headers["User-Agent"] == "WAA/9.9"
    assert request_headers["X-AIStudio-Visit-Id"] == "visit-direct"


@pytest.mark.asyncio
async def test_concurrent_streaming_metadata_is_per_slot(tmp_path: Path) -> None:
    signer_a = FakeSigner()
    signer_b = FakeSigner()
    signer_a.stream_account_key = "key-a"
    signer_a.stream_set_cookies = [
        {"name": "SAPISID", "value": "stream-a", "domain": ".google.com", "path": "/"}
    ]
    signer_b.stream_account_key = "key-b"
    signer_b.stream_set_cookies = [
        {"name": "SAPISID", "value": "stream-b", "domain": ".google.com", "path": "/"}
    ]
    auth_a = _write_auth(tmp_path, "a", "cookie-a")
    auth_b = _write_auth(tmp_path, "b", "cookie-b")
    session_a = _session(signer_a, auth_a, account_id="a")
    session_b = _session(signer_b, auth_b, account_id="b")

    async def consume(session: BrowserlessSession) -> None:
        async for _event in session.send_streaming_request(body="[]", url="https://x"):
            pass

    await asyncio.gather(consume(session_a), consume(session_b))
    assert session_a.account_key == "key-a"
    assert session_b.account_key == "key-b"
    assert await session_a.save_current_cookies() is True
    assert await session_b.save_current_cookies() is True

    data_a = json.loads(auth_a.read_text(encoding="utf-8"))
    data_b = json.loads(auth_b.read_text(encoding="utf-8"))
    cookies_a = {c["name"]: c["value"] for c in data_a["cookies"]}
    cookies_b = {c["name"]: c["value"] for c in data_b["cookies"]}
    assert cookies_a["SAPISID"] == "stream-a"
    assert cookies_b["SAPISID"] == "stream-b"
    assert "stream-b" not in cookies_a.values()
    assert "stream-a" not in cookies_b.values()


@pytest.mark.asyncio
async def test_mint_deleted_and_truncated_cookies_are_applied(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    signer = FakeSigner()
    signer.mint_deleted_cookies = [
        {"name": "SID", "value": "old", "domain": ".google.com", "path": "/"}
    ]
    signer.mint_truncated = True
    session = _session(signer, _write_auth(tmp_path, "a", "cookie-a"))
    with caplog.at_level("WARNING"):
        await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    names = {str(cookie.get("name")) for cookie in session._cookies}
    assert "SID" not in names
    assert any("截断" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_expired_cookies_are_dropped_on_load(tmp_path: Path) -> None:
    auth = _write_auth(tmp_path, "a", "cookie-a")
    data = json.loads(auth.read_text(encoding="utf-8"))
    data["cookies"].append(
        {
            "name": "OLD",
            "value": "expired",
            "domain": ".google.com",
            "path": "/",
            "expires": 1,
        }
    )
    auth.write_text(json.dumps(data), encoding="utf-8")
    session = _session(FakeSigner(), auth)
    names = {str(cookie.get("name")) for cookie in session._cookies}
    assert "OLD" not in names


@pytest.mark.asyncio
async def test_stable_account_key_passed_to_signer(tmp_path: Path) -> None:
    signer = FakeSigner()
    session = _session(signer, _write_auth(tmp_path, "a", "cookie-a"), account_id="a")
    await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    await session.send_hooked_request(body="[]", url="https://x")
    events = [
        event
        async for event in session.send_streaming_request(body="[]", url="https://x")
    ]
    assert events
    assert signer.mints[0]["account_key"] == "a"
    assert signer.rpc_calls[0]["account_key"] == "a"
    assert signer.rpc_calls[1]["account_key"] == "a"
    await session.close()
    assert signer.released == ["a"]


@pytest.mark.asyncio
async def test_fallback_account_key_is_cookie_hash(tmp_path: Path) -> None:
    session = _session(
        FakeSigner(), _write_auth(tmp_path, "a", "cookie-a"), account_id=""
    )
    key = session.stable_account_key
    assert len(key) == 64
    assert all(char in "0123456789abcdef" for char in key)


def test_stable_account_key_normalizes_go_invalid_ids(tmp_path: Path) -> None:
    import re

    pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    auth = _write_auth(tmp_path, "a", "cookie-a")
    for account_id in ("a@b.com", ".foo", "-foo", "foo bar"):
        session = _session(FakeSigner(), auth, account_id=account_id)
        key = session.stable_account_key
        assert pattern.fullmatch(key)
        assert key == session.stable_account_key
    valid = _session(FakeSigner(), auth, account_id="acct_1.2-3")
    assert valid.stable_account_key == "acct_1.2-3"


def test_browserless_fingerprint_is_signer_authoritative(tmp_path: Path) -> None:
    session = _session(FakeSigner(), _write_auth(tmp_path, "a", "cookie-a"))
    assert session.fingerprint["summary"] == "Firefox 152 (WAA)"
    assert not (tmp_path / "fingerprints").exists()
    assert not (tmp_path / "data" / "waa" / "fingerprints").exists()


def _append_cookie(auth: Path, name: str, value: str, expires: int = -1) -> None:
    data = json.loads(auth.read_text(encoding="utf-8"))
    data["cookies"].append(
        {
            "name": name,
            "value": value,
            "domain": ".google.com",
            "path": "/",
            "expires": expires,
        }
    )
    auth.write_text(json.dumps(data), encoding="utf-8")


def _auth_cookies(auth: Path) -> dict[str, str]:
    data = json.loads(auth.read_text(encoding="utf-8"))
    return {str(item["name"]): str(item["value"]) for item in data["cookies"]}


@pytest.mark.asyncio
async def test_rpc_tombstone_deletes_cookie_and_persists_updates(
    tmp_path: Path,
) -> None:
    signer = FakeSigner()
    signer.rpc_set_cookies = [
        {
            "name": "SID",
            "value": "",
            "domain": ".google.com",
            "path": "/",
            "expires": 0,
        },
        {
            "name": "NEW",
            "value": "fresh",
            "domain": ".google.com",
            "path": "/",
            "expires": -1,
        },
    ]
    auth = _write_auth(tmp_path, "a", "cookie-a")
    session = _session(signer, auth)

    await session.send_hooked_request(body="[]", url="https://x")
    assert await session.save_current_cookies() is True

    cookies = _auth_cookies(auth)
    assert "SID" not in cookies
    assert cookies["NEW"] == "fresh"
    assert cookies["SAPISID"] == "cookie-a"


@pytest.mark.asyncio
async def test_rpc_tombstone_only_deletion_still_persists(tmp_path: Path) -> None:
    signer = FakeSigner()
    signer.rpc_set_cookies = [
        {
            "name": "SID",
            "value": "",
            "domain": ".google.com",
            "path": "/",
            "expires": 0,
        }
    ]
    auth = _write_auth(tmp_path, "a", "cookie-a")
    session = _session(signer, auth)

    await session.send_hooked_request(body="[]", url="https://x")
    assert await session.save_current_cookies() is True
    assert "SID" not in _auth_cookies(auth)


@pytest.mark.asyncio
async def test_stream_tombstones_are_isolated_per_slot(tmp_path: Path) -> None:
    signer_a = FakeSigner()
    signer_b = FakeSigner()
    signer_a.stream_set_cookies = [
        {
            "name": "ONLY_A",
            "value": "",
            "domain": ".google.com",
            "path": "/",
            "expires": 0,
        }
    ]
    signer_b.stream_set_cookies = [
        {
            "name": "ONLY_B",
            "value": "",
            "domain": ".google.com",
            "path": "/",
            "expires": 0,
        }
    ]
    auth_a = _write_auth(tmp_path, "a", "cookie-a")
    auth_b = _write_auth(tmp_path, "b", "cookie-b")
    _append_cookie(auth_a, "ONLY_A", "a")
    _append_cookie(auth_b, "ONLY_B", "b")
    session_a = _session(signer_a, auth_a, account_id="a")
    session_b = _session(signer_b, auth_b, account_id="b")

    async def consume(session: BrowserlessSession) -> None:
        async for _event in session.send_streaming_request(body="[]", url="https://x"):
            pass

    await asyncio.gather(consume(session_a), consume(session_b))
    assert await session_a.save_current_cookies() is True
    assert await session_b.save_current_cookies() is True

    cookies_a = _auth_cookies(auth_a)
    cookies_b = _auth_cookies(auth_b)
    assert "ONLY_A" not in cookies_a
    assert cookies_a["SID"] == "sid-cookie-a"
    assert "ONLY_B" not in cookies_b
    assert cookies_b["SID"] == "sid-cookie-b"


@pytest.mark.asyncio
async def test_expires_minus_one_is_session_cookie_not_tombstone(
    tmp_path: Path,
) -> None:
    signer = FakeSigner()
    signer.rpc_set_cookies = [
        {
            "name": "SESSION",
            "value": "new-value",
            "domain": ".google.com",
            "path": "/",
            "expires": -1,
        }
    ]
    auth = _write_auth(tmp_path, "a", "cookie-a")
    _append_cookie(auth, "SESSION", "old-value", expires=-1)
    session = _session(signer, auth)

    await session.send_hooked_request(body="[]", url="https://x")
    assert await session.save_current_cookies() is True
    assert _auth_cookies(auth)["SESSION"] == "new-value"
