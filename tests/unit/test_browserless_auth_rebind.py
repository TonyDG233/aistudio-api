"""Browserless login-state failures must trigger routing rebind, offline only.

Covers the signer 401/403/login-state error mapping, BrowserlessSession's
translation into ``AuthError``/``SessionExpiredError``, and the full routing
chain against the real ``InstanceManager`` + ``InstancePool`` (mock signer, no
browser, no network).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.application.routing_pipeline import RouteAttempt, RoutingPipeline
from aistudio_api.domain.errors import AuthError, SessionExpiredError
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstancePool,
    InstanceSpec,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.waa.browserless_session import BrowserlessSession
from aistudio_api.infrastructure.waa.engine_runtime import BrowserlessEngineRuntime
from aistudio_api.infrastructure.waa.signer_client import (
    MintProofResult,
    RpcResult,
    SignerAuthError,
    SignerClient,
)

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "test_output.json"
_COOKIE_TEXT = json.dumps(
    {"cookies": [{"name": "SAPISID", "value": "v", "domain": ".google.com"}]}
)
_OLD = "acc_old"
_HEALTHY = "acc_healthy"


@pytest.fixture(autouse=True)
def _offline_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the 403 refresh path offline: no /RotateCookies network call."""
    from aistudio_api.infrastructure.waa import browserless_session as module

    async def _skip(cookies: dict[str, str], **kwargs: object) -> dict[str, object]:
        del cookies, kwargs
        return {"ok": False, "status": 0, "updated": []}

    monkeypatch.setattr(module, "rotate_google_cookies", _skip)


# --------------------------------------------------------------------- signer


def _signer_client(handler: Any) -> SignerClient:
    transport = httpx.MockTransport(handler)
    return SignerClient(
        "http://127.0.0.1:8081",
        request_timeout_s=5,
        connect_timeout_s=2,
        http_client=httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_signer_mint_login_state_failure_is_auth_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/proof/mint"
        return httpx.Response(
            502,
            json={
                "ok": False,
                "error": "隔离登录态失效 url=https://accounts.google.com",
            },
        )

    client = _signer_client(handler)
    with pytest.raises(SignerAuthError) as excinfo:
        await client.mint_proof(cookies=_COOKIE_TEXT, digest="d")
    assert excinfo.value.status_code == 502
    assert "隔离登录态失效" in excinfo.value.reason
    assert _COOKIE_TEXT not in str(excinfo.value)
    await client.close()


@pytest.mark.asyncio
async def test_signer_mint_403_is_auth_error_even_without_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = _signer_client(handler)
    with pytest.raises(SignerAuthError) as excinfo:
        await client.mint_proof(cookies=_COOKIE_TEXT, digest="d")
    assert excinfo.value.status_code == 403
    await client.close()


@pytest.mark.asyncio
async def test_signer_rpc_login_state_failure_is_auth_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rpc/send"
        return httpx.Response(502, json={"ok": False, "error": "WAA denied"})

    client = _signer_client(handler)
    with pytest.raises(SignerAuthError) as excinfo:
        await client.send_rpc(cookies=_COOKIE_TEXT, body="[]", url="https://x")
    assert excinfo.value.status_code == 502
    assert "WAA denied" in excinfo.value.reason
    await client.close()


@pytest.mark.asyncio
async def test_signer_rpc_real_upstream_403_is_not_rewritten() -> None:
    """A proxied Google 403 keeps its status/body for the normal 4xx mapping."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            content=b'[null,[7,"WAA denied"]]',
            headers={"x-upstream-status": "403"},
        )

    client = _signer_client(handler)
    result = await client.send_rpc(cookies=_COOKIE_TEXT, body="[]", url="https://x")
    assert result.status == 403
    assert result.upstream_status == 403
    assert result.body == b'[null,[7,"WAA denied"]]'
    await client.close()


@pytest.mark.asyncio
async def test_signer_stream_login_state_failure_is_auth_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"ok": False, "error": "credential expired"})

    client = _signer_client(handler)
    with pytest.raises(SignerAuthError) as excinfo:
        _ = [
            event
            async for event in client.stream_rpc(
                cookies=_COOKIE_TEXT, body="[]", url="https://x"
            )
        ]
    assert excinfo.value.status_code == 502
    assert "credential expired" in excinfo.value.reason
    await client.close()


# -------------------------------------------------------------------- session


class _RaisingSigner:
    def __init__(self, error: SignerAuthError) -> None:
        self.error = error
        self.released: list[str] = []

    async def mint_proof(self, **kwargs: object) -> MintProofResult:
        raise self.error

    async def send_rpc(self, **kwargs: object) -> RpcResult:
        raise self.error

    async def stream_rpc(
        self, **kwargs: object
    ) -> AsyncGenerator[tuple[str, object], None]:
        raise self.error
        yield ("status", 0)

    async def release_worker(
        self, *, account_key: str | None = None
    ) -> dict[str, object]:
        self.released.append(account_key or "")
        return {"ok": True}


def _auth_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "SAPISID",
                        "value": f"sap-{name}",
                        "domain": ".google.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _session(signer: Any, auth_file: Path) -> BrowserlessSession:
    return BrowserlessSession(
        signer=signer,
        auth_file=str(auth_file),
        tier="Pro",
        account_id=_OLD,
        realm_id="old-g1",
    )


@pytest.mark.asyncio
async def test_session_maps_403_and_401_login_state_failures(
    tmp_path: Path,
) -> None:
    error_403 = SignerAuthError(
        "waa-signer mint 登录态失效 (status=403)",
        status_code=403,
        reason="invalid authentication",
    )
    session = _session(_RaisingSigner(error_403), _auth_file(tmp_path, "403"))
    with pytest.raises(AuthError) as info:
        await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    assert info.value.__class__ is AuthError
    assert "403" in str(info.value)
    assert "invalid authentication" in str(info.value)

    error_401 = SignerAuthError(
        "waa-signer rpc 登录态失效 (status=401)",
        status_code=401,
        reason="credential expired",
    )
    session = _session(_RaisingSigner(error_401), _auth_file(tmp_path, "401"))
    with pytest.raises(SessionExpiredError) as expired:
        await session.send_hooked_request(body="[]", url="https://x")
    assert "401" in str(expired.value)
    assert "credential expired" in str(expired.value)


@pytest.mark.asyncio
async def test_session_stream_maps_login_state_failure(tmp_path: Path) -> None:
    signer = _RaisingSigner(
        SignerAuthError(
            "waa-signer rpc stream 登录态失效 (status=403)",
            status_code=403,
            reason="WAA denied",
        )
    )
    session = _session(signer, _auth_file(tmp_path, "stream"))
    with pytest.raises(AuthError):
        _ = [
            event
            async for event in session.send_streaming_request(
                body="[]", url="https://x"
            )
        ]


def _isolated_login_state_error() -> SignerAuthError:
    return SignerAuthError(
        "waa-signer mint 登录态失效 (status=502)",
        status_code=502,
        reason=(
            "初始化 SpiderMonkey WAA Worker 失败: 隔离登录态失效 "
            "url=https://accounts.google.com/servicelogin"
        ),
    )


@pytest.mark.asyncio
async def test_session_maps_502_isolated_login_state_to_session_expired(
    tmp_path: Path,
) -> None:
    session = _session(
        _RaisingSigner(_isolated_login_state_error()),
        _auth_file(tmp_path, "isolated"),
    )
    with pytest.raises(SessionExpiredError):
        await session.generate_snapshot([AistudioContent.from_user_prompt("hi")])
    assert session.session_expired is True


@pytest.mark.asyncio
async def test_refresh_auth_state_surfaces_root_session_expired(
    tmp_path: Path,
) -> None:
    session = _session(
        _RaisingSigner(_isolated_login_state_error()),
        _auth_file(tmp_path, "refresh-expired"),
    )
    assert await session.refresh_auth_state() is False
    assert session.session_expired is True


# ------------------------------------------------------- routing + real pool


class _SignerPool:
    """Mock signer keyed by the stable account key forwarded by the session."""

    def __init__(
        self,
        *,
        expired: str = _OLD,
        healthy: str = _HEALTHY,
        expired_mint: str = "auth_error",
        expired_rpc_status: int | None = None,
    ) -> None:
        self.expired = expired
        self.healthy = healthy
        self.expired_mint = expired_mint
        self.expired_rpc_status = expired_rpc_status
        self.mint_calls: list[str] = []
        self.rpc_calls: list[str] = []
        self.released: list[str] = []
        self.rpc_body = _FIXTURE.read_bytes()

    async def mint_proof(self, **kwargs: object) -> MintProofResult:
        key = str(kwargs.get("account_key") or "")
        self.mint_calls.append(key)
        if key == self.expired:
            if self.expired_mint == "status_401":
                raise SignerAuthError(
                    "waa-signer mint 登录态失效 (status=401)",
                    status_code=401,
                    reason="credential expired",
                )
            if self.expired_mint == "auth_error":
                raise SignerAuthError(
                    "waa-signer mint 登录态失效 (status=502)",
                    status_code=502,
                    reason="隔离登录态失效 url=https://accounts.google.com",
                )
        return MintProofResult(
            ok=True,
            proof="!proof",
            account_key=key,
            latency_ms=1.0,
        )

    async def send_rpc(self, **kwargs: object) -> RpcResult:
        key = str(kwargs.get("account_key") or "")
        self.rpc_calls.append(key)
        if key == self.expired and self.expired_rpc_status is not None:
            return RpcResult(
                status=self.expired_rpc_status,
                body=b'[null,[7,"WAA denied"]]',
                account_key=key,
                upstream_status=self.expired_rpc_status,
            )
        return RpcResult(status=200, body=self.rpc_body, account_key=key)

    async def stream_rpc(
        self, **kwargs: object
    ) -> AsyncGenerator[tuple[str, object], None]:
        result = await self.send_rpc(**kwargs)
        yield ("status", result.status)
        yield ("chunk", result.body)

    async def release_worker(
        self, *, account_key: str | None = None, release_all: bool = False
    ) -> dict[str, object]:
        if account_key:
            self.released.append(account_key)
        return {"ok": True, "all": release_all}

    async def close(self) -> None:
        return None


class _Rotator:
    def __init__(self, healthy: str = _HEALTHY) -> None:
        self.healthy = healthy
        self.rebind_exclusions: list[set[str]] = []

    def get_account_stats(self, account_id: str) -> Any:
        del account_id
        return SimpleNamespace(total_requests=0, model_requests_total=lambda _model: 0)

    def is_account_available(
        self, account_id: str, *, model: str | None = None, surface: str | None = None
    ) -> bool:
        del account_id, model, surface
        return True

    def select_rebind_account(self, **kwargs: object) -> Any:
        excluded = set(cast("Any", kwargs.get("exclude_account_ids") or ()))
        self.rebind_exclusions.append(excluded)
        if self.healthy in excluded:
            return None
        return SimpleNamespace(id=self.healthy, tier="Pro")

    def record_success(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def record_auth_error(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def record_session_expired(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def record_rate_limited(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def record_error(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def _storage_state(name: str) -> dict[str, object]:
    return {
        "cookies": [
            {
                "name": "SAPISID",
                "value": f"sap-{name}",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "SID",
                "value": f"sid-{name}",
                "domain": ".google.com",
                "path": "/",
            },
        ],
        "origins": [],
    }


def _store(tmp_path: Path) -> AccountStore:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    store.save_account(
        name="old",
        email="old@example.com",
        storage_state=_storage_state("old"),
        account_id=_OLD,
        tier="Pro",
    )
    store.save_account(
        name="healthy",
        email="healthy@example.com",
        storage_state=_storage_state("healthy"),
        account_id=_HEALTHY,
        tier="Pro",
    )
    store.set_active_account(_OLD)
    return store


async def _no_cdp() -> Any:
    raise AssertionError("browserless engine must never request CDP")


async def _manager(
    tmp_path: Path, signer: _SignerPool
) -> tuple[InstanceManager, RoutingPipeline, _Rotator]:
    store = _store(tmp_path)
    runtime = BrowserlessEngineRuntime(
        {
            "mode": "browserless",
            "signer": {"mode": "remote_http", "endpoint": "http://127.0.0.1:1"},
            "transport": {"mode": "signer_rpc"},
        },
        signer_client=cast("Any", signer),
    )
    pool = InstancePool(
        cast(Any, _no_cdp),
        specs=[InstanceSpec(count=1, surface="chat", min_tier="Pro")],
        max_contexts=2,
        engine_runtime=runtime,
        engine="browserless",
    )
    manager = InstanceManager(pool, store)
    await manager.startup()
    rotator = _Rotator()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast(Any, rotator),
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 0,
        },
    )
    return manager, pipeline, rotator


async def _generate_with_routing(
    pipeline: RoutingPipeline,
) -> tuple[Any, int]:
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt: RouteAttempt | None = None
    switches = 0
    try:
        while True:
            if attempt is None:
                attempt = await pipeline.acquire(context)
                assert attempt is not None
            try:
                output = await attempt.client.generate_content(
                    model="gemini-4-argon",
                    capture_prompt="hi",
                    contents=[AistudioContent.from_user_prompt("hi")],
                )
            except Exception as exc:
                action = await pipeline.handle_error(
                    context, attempt, exc, has_yielded_data=False
                )
                if action == "retry_same":
                    continue
                assert action == "switch"
                switches += 1
                await pipeline.release(attempt)
                attempt = None
                continue
            pipeline.record_success(context, attempt)
            return output, switches
    finally:
        if attempt is not None:
            await pipeline.release(attempt)


def _current_account(manager: InstanceManager) -> str:
    slot = next(iter(manager._pool.slots()))
    return str(slot.account_id)


@pytest.mark.asyncio
async def test_mint_login_state_failure_rebinds_to_healthy_account(
    tmp_path: Path,
) -> None:
    signer = _SignerPool(expired_mint="auth_error")
    manager, pipeline, rotator = await _manager(tmp_path, signer)
    try:
        old_slot = manager.ready_slot_for_account(_OLD)
        assert old_slot is not None
        old_session = old_slot.session
        assert isinstance(old_session, BrowserlessSession)

        output, switches = await _generate_with_routing(pipeline)

        assert output.candidates and output.candidates[0].text
        assert switches == 1
        assert _current_account(manager) == _HEALTHY
        assert old_session.is_alive() is False
        # 502 isolated-login-state is terminal: no same-account refresh attempt.
        assert signer.released == [_OLD]
        assert rotator.rebind_exclusions
        healthy_slot = manager.ready_slot_for_account(_HEALTHY)
        assert healthy_slot is not None
        assert healthy_slot.session is not old_session
        assert signer.mint_calls.count(_OLD) == 1
        assert _HEALTHY in signer.mint_calls
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_mint_401_rebinds_without_reusing_stale_cookie(
    tmp_path: Path,
) -> None:
    signer = _SignerPool(expired_mint="status_401")
    manager, pipeline, _rotator = await _manager(tmp_path, signer)
    try:
        old_slot = manager.ready_slot_for_account(_OLD)
        assert old_slot is not None
        old_session = old_slot.session
        assert isinstance(old_session, BrowserlessSession)

        output, switches = await _generate_with_routing(pipeline)

        assert output.candidates and output.candidates[0].text
        assert switches == 1
        assert _current_account(manager) == _HEALTHY
        assert old_session.is_alive() is False
        assert signer.released == [_OLD]
        # 401 is a definite expired session: no same-account re-mint.
        assert signer.mint_calls.count(_OLD) == 1
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_second_rpc_403_rebinds_after_same_account_refresh(
    tmp_path: Path,
) -> None:
    signer = _SignerPool(expired_mint="ok", expired_rpc_status=403)
    manager, pipeline, rotator = await _manager(tmp_path, signer)
    try:
        old_slot = manager.ready_slot_for_account(_OLD)
        assert old_slot is not None
        old_session = old_slot.session
        assert isinstance(old_session, BrowserlessSession)

        output, switches = await _generate_with_routing(pipeline)

        assert output.candidates and output.candidates[0].text
        assert switches == 1
        assert _current_account(manager) == _HEALTHY
        assert old_session.is_alive() is False
        # Same-account refresh releases the hot worker; the rebind closes it too.
        assert signer.released == [_OLD, _OLD]
        # First 403 re-signed on the same session, second 403 triggered rebind.
        assert signer.rpc_calls.count(_OLD) == 2
        # initial mint + refresh mint + retry mint
        assert signer.mint_calls.count(_OLD) == 3
        assert rotator.rebind_exclusions[0] == {_OLD}
        healthy_slot = manager.ready_slot_for_account(_HEALTHY)
        assert healthy_slot is not None
        assert healthy_slot.session is not old_session
    finally:
        await manager.close_all()


# ------------------------------------------------ same-anchor group isolation

_SHARED_U0 = "acc-shared-u0"
_SHARED_U1 = "acc-shared-u1"
_INDEPENDENT = "acc-independent"


class _GroupSignerPool:
    """Expired root session for every same-anchor sibling, healthy independent."""

    def __init__(self, expired: set[str]) -> None:
        self.expired = expired
        self.mint_calls: list[str] = []
        self.rpc_calls: list[str] = []
        self.released: list[str] = []
        self.rpc_body = _FIXTURE.read_bytes()

    async def mint_proof(self, **kwargs: object) -> MintProofResult:
        key = str(kwargs.get("account_key") or "")
        self.mint_calls.append(key)
        if key in self.expired:
            raise _isolated_login_state_error()
        return MintProofResult(ok=True, proof="!proof", account_key=key, latency_ms=1.0)

    async def send_rpc(self, **kwargs: object) -> RpcResult:
        key = str(kwargs.get("account_key") or "")
        self.rpc_calls.append(key)
        if key in self.expired:
            raise _isolated_login_state_error()
        return RpcResult(status=200, body=self.rpc_body, account_key=key)

    async def stream_rpc(
        self, **kwargs: object
    ) -> AsyncGenerator[tuple[str, object], None]:
        result = await self.send_rpc(**kwargs)
        yield ("status", result.status)
        yield ("chunk", result.body)

    async def release_worker(
        self, *, account_key: str | None = None, release_all: bool = False
    ) -> dict[str, object]:
        if account_key:
            self.released.append(account_key)
        return {"ok": True, "all": release_all}

    async def close(self) -> None:
        return None


def _shared_storage_state(sapisid: str, sid: str) -> dict[str, object]:
    return {
        "cookies": [
            {
                "name": "SAPISID",
                "value": sapisid,
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "SID",
                "value": sid,
                "domain": ".google.com",
                "path": "/",
            },
        ],
        "origins": [],
    }


def _group_store(tmp_path: Path) -> AccountStore:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    store.save_account(
        name="u0",
        email="u0@example.com",
        storage_state=_shared_storage_state("shared-anchor", "shared-sid"),
        account_id=_SHARED_U0,
        auth_user="0",
        tier="Pro",
    )
    store.save_account(
        name="u1",
        email="u1@example.com",
        storage_state=_shared_storage_state("shared-anchor", "shared-sid"),
        account_id=_SHARED_U1,
        auth_user="1",
        tier="Pro",
    )
    store.save_account(
        name="independent",
        email="independent@example.com",
        storage_state=_shared_storage_state("independent-anchor", "independent-sid"),
        account_id=_INDEPENDENT,
        auth_user="0",
        tier="Pro",
    )
    store.set_active_account(_SHARED_U0)
    return store


async def _group_manager(
    tmp_path: Path, signer: _GroupSignerPool
) -> tuple[InstanceManager, RoutingPipeline, AccountRotator]:
    store = _group_store(tmp_path)
    runtime = BrowserlessEngineRuntime(
        {
            "mode": "browserless",
            "signer": {"mode": "remote_http", "endpoint": "http://127.0.0.1:1"},
            "transport": {"mode": "signer_rpc"},
        },
        signer_client=cast("Any", signer),
    )
    pool = InstancePool(
        cast(Any, _no_cdp),
        specs=[InstanceSpec(count=1, surface="chat", min_tier="Pro")],
        max_contexts=2,
        engine_runtime=runtime,
        engine="browserless",
    )
    manager = InstanceManager(pool, store)
    await manager.startup()
    rotator = AccountRotator(store)
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=rotator,
        routing_settings_provider=lambda: {
            "max_switches": 3,
            "retry_same_delay_sec": 0,
        },
    )
    return manager, pipeline, rotator


@pytest.mark.asyncio
async def test_session_expired_isolates_same_anchor_group_and_rebinds_once(
    tmp_path: Path,
) -> None:
    signer = _GroupSignerPool({_SHARED_U0, _SHARED_U1})
    manager, pipeline, rotator = await _group_manager(tmp_path, signer)
    try:
        old_slot = manager.ready_slot_for_account(_SHARED_U0)
        assert old_slot is not None
        old_session = old_slot.session
        assert isinstance(old_session, BrowserlessSession)

        output, switches = await _generate_with_routing(pipeline)

        assert output.candidates and output.candidates[0].text
        # The dead sibling must never get a mint attempt: one rebind, one switch.
        assert switches == 1
        assert _current_account(manager) == _INDEPENDENT
        assert signer.mint_calls.count(_SHARED_U0) == 1
        assert _SHARED_U1 not in signer.mint_calls
        assert _INDEPENDENT in signer.mint_calls
        shared_u0_stats = rotator.get_account_stats(_SHARED_U0)
        shared_u1_stats = rotator.get_account_stats(_SHARED_U1)
        independent_stats = rotator.get_account_stats(_INDEPENDENT)
        assert shared_u0_stats is not None and shared_u0_stats.session_expired is True
        assert shared_u1_stats is not None and shared_u1_stats.session_expired is True
        assert (
            independent_stats is not None and independent_stats.session_expired is False
        )
    finally:
        await manager.close_all()
