"""External exit pool resolver/config tests (offline, fake manager only)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from aistudio_api.infrastructure.exit_pool import (
    ExitPoolConfig,
    ExitResolver,
    get_exit_resolver,
    hmac_account_id,
    parse_socks_url,
    reset_exit_resolver,
)
from aistudio_api.infrastructure.waa.engine_runtime import BrowserlessEngineRuntime

_ENV_NAMES = (
    "AISTUDIO_EXIT_MODE",
    "AISTUDIO_EXIT_ENDPOINT",
    "AISTUDIO_EXIT_TOKEN",
    "AISTUDIO_EXIT_HMAC_KEY",
    "AISTUDIO_EXIT_ALLOWED_HOSTS",
    "AISTUDIO_EXIT_HTTP_TIMEOUT",
    "AISTUDIO_EXIT_TTL",
    "AISTUDIO_EXIT_CANARY_ACCOUNT",
)


@pytest.fixture(autouse=True)
def _isolate_exit_pool(monkeypatch: pytest.MonkeyPatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    reset_exit_resolver()
    yield
    reset_exit_resolver()


def _set_external_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "AISTUDIO_EXIT_MODE": "external",
        "AISTUDIO_EXIT_ENDPOINT": "http://172.17.0.1:41999/",
        "AISTUDIO_EXIT_TOKEN": "secret-token",
        "AISTUDIO_EXIT_HMAC_KEY": "hmac-key",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _resolver_with_transport(
    handler: Any,
    *,
    clock: Any = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> ExitResolver:
    config = ExitPoolConfig.from_env()
    assert config is not None, "external env must be set"
    transport = httpx.MockTransport(handler)
    factory = lambda: httpx.AsyncClient(transport=transport)  # noqa: E731
    if clock is None:
        return ExitResolver(config, client_factory=factory)
    return ExitResolver(config, client_factory=factory, clock=clock)


# --------------------------------------------------------------- config / gate


def test_off_mode_builds_no_config_and_no_resolver() -> None:
    assert ExitPoolConfig.from_env() is None
    assert get_exit_resolver() is None


def test_unknown_mode_is_off_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AISTUDIO_EXIT_MODE", "pools")
    with caplog.at_level("WARNING"):
        assert ExitPoolConfig.from_env() is None
        assert get_exit_resolver() is None
    assert any("未知 AISTUDIO_EXIT_MODE" in record.message for record in caplog.records)


def test_partial_external_env_is_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AISTUDIO_EXIT_MODE", "external")
    monkeypatch.setenv("AISTUDIO_EXIT_ENDPOINT", "http://172.17.0.1:41999")
    with caplog.at_level("WARNING"):
        assert ExitPoolConfig.from_env() is None
    assert any("缺少" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://172.17.0.1:41999",
        "172.17.0.1:41999",
        "http://",
        "http:///no-host",
        "socks5h://172.17.0.1:41999",
    ],
)
def test_invalid_endpoint_keeps_feature_off_at_load(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    endpoint: str,
) -> None:
    _set_external_env(monkeypatch, AISTUDIO_EXIT_ENDPOINT=endpoint)
    with caplog.at_level("WARNING"):
        assert ExitPoolConfig.from_env() is None
        assert get_exit_resolver() is None
    assert any(
        "非法 AISTUDIO_EXIT_ENDPOINT" in record.message for record in caplog.records
    )


def test_external_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_external_env(monkeypatch)
    config = ExitPoolConfig.from_env()
    assert config is not None
    assert config.endpoint == "http://172.17.0.1:41999"
    assert config.allowed_hosts == frozenset({"127.0.0.1", "172.17.0.1"})
    assert config.http_timeout_s == 2.0
    assert config.ttl_s == 300.0
    assert config.canary_account == ""


def test_external_config_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_external_env(
        monkeypatch,
        AISTUDIO_EXIT_ALLOWED_HOSTS="127.0.0.1, 10.0.0.5",
        AISTUDIO_EXIT_HTTP_TIMEOUT="1.5",
        AISTUDIO_EXIT_TTL="60",
        AISTUDIO_EXIT_CANARY_ACCOUNT="canary-acc",
    )
    config = ExitPoolConfig.from_env()
    assert config is not None
    assert config.allowed_hosts == frozenset({"127.0.0.1", "10.0.0.5"})
    assert config.http_timeout_s == 1.5
    assert config.ttl_s == 60.0
    assert config.canary_account == "canary-acc"


# ------------------------------------------------------------------ primitives


def test_hmac_account_id_is_stable_and_keyed() -> None:
    expected = hmac.new(b"hmac-key", b"acc-1@example.com", hashlib.sha256).hexdigest()
    assert hmac_account_id("hmac-key", "acc-1@example.com") == expected
    assert hmac_account_id("hmac-key", "acc-1@example.com") == hmac_account_id(
        "hmac-key", "acc-1@example.com"
    )
    assert hmac_account_id("other-key", "acc-1@example.com") != expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("socks5://172.17.0.1:40001", "socks5://172.17.0.1:40001"),
        ("socks5h://127.0.0.1:1080", "socks5h://127.0.0.1:1080"),
        (
            "socks5h://user:pass@172.17.0.1:40001",
            "socks5h://user:pass@172.17.0.1:40001",
        ),
        ("http://172.17.0.1:40001", None),
        ("socks5://10.9.9.9:40001", None),
        ("socks5://172.17.0.1", None),
        ("socks5://172.17.0.1:0", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_socks_url_whitelist(url: object, expected: str | None) -> None:
    allowed = frozenset({"127.0.0.1", "172.17.0.1"})
    assert parse_socks_url(url, allowed) == expected


# ------------------------------------------------------------------- resolving


@pytest.mark.asyncio
async def test_resolve_assigns_caches_and_sends_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    seen: list[httpx.Request] = []
    now = [100.0]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "exit_id": "exit-1",
                "socks_url": "socks5h://172.17.0.1:40001",
                "generation": 3,
                "ttl_s": 30,
                "healthy": True,
            },
        )

    resolver = _resolver_with_transport(handler, clock=lambda: now[0])
    first = await resolver.resolve("acc-1@example.com", kind="chat")
    second = await resolver.resolve("acc-1@example.com", kind="chat")

    assert first == "socks5h://172.17.0.1:40001"
    assert second == first
    assert len(seen) == 1
    request = seen[0]
    assert request.url.path == "/v1/assign"
    assert request.url.params["account"] == hmac_account_id(
        "hmac-key", "acc-1@example.com"
    )
    assert request.url.params["kind"] == "chat"
    assert request.headers["authorization"] == "Bearer secret-token"

    assignment = resolver.assignment_for("acc-1@example.com", kind="chat")
    assert assignment is not None
    assert assignment.exit_id == "exit-1"
    assert assignment.generation == 3

    now[0] += 31.0
    assert await resolver.resolve("acc-1@example.com", kind="chat") == first
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_resolve_503_falls_back_to_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    resolver = _resolver_with_transport(lambda request: httpx.Response(503))
    assert await resolver.resolve("acc-1") is None
    assert resolver.assignment_for("acc-1") is None


@pytest.mark.asyncio
async def test_resolve_transport_timeout_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    resolver = _resolver_with_transport(handler)
    assert await resolver.resolve("acc-1") is None


@pytest.mark.asyncio
async def test_resolve_invalid_url_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_external_env(monkeypatch)
    for payload in (
        {
            "exit_id": "exit-1",
            "socks_url": "http://172.17.0.1:40001",
            "generation": 1,
            "ttl_s": 30,
            "healthy": True,
        },
        {
            "exit_id": "exit-1",
            "socks_url": "socks5://10.9.9.9:40001",
            "generation": 1,
            "ttl_s": 30,
            "healthy": True,
        },
        {"socks_url": "socks5://172.17.0.1:40001"},
        "not-a-dict",
    ):
        resolver = _resolver_with_transport(
            lambda request, body=payload: httpx.Response(200, json=body)
        )
        assert await resolver.resolve("acc-1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("healthy", [False, None])
async def test_resolve_unhealthy_exit_falls_back_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, healthy: object
) -> None:
    _set_external_env(monkeypatch)
    payload: dict[str, object] = {
        "exit_id": "exit-1",
        "socks_url": "socks5h://172.17.0.1:40001",
        "generation": 1,
        "ttl_s": 30,
    }
    if healthy is not None:
        payload["healthy"] = healthy
    resolver = _resolver_with_transport(
        lambda request: httpx.Response(200, json=payload)
    )

    assert await resolver.resolve("acc-1") is None
    assert resolver.assignment_for("acc-1") is None
    assert resolver.assignment_for("acc-1", include_expired=True) is None


@pytest.mark.asyncio
async def test_invalidate_exit_drops_every_account_on_that_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        account = request.url.params["account"]
        exit_id = (
            "exit-2" if account == hmac_account_id("hmac-key", "acc-2") else "exit-1"
        )
        port = 40002 if exit_id == "exit-2" else 40001
        return httpx.Response(
            200,
            json={
                "exit_id": exit_id,
                "socks_url": f"socks5h://172.17.0.1:{port}",
                "generation": 1,
                "ttl_s": 300,
                "healthy": True,
            },
        )

    resolver = _resolver_with_transport(handler)
    assert await resolver.resolve("acc-1") == "socks5h://172.17.0.1:40001"
    assert await resolver.resolve("acc-3") == "socks5h://172.17.0.1:40001"
    assert await resolver.resolve("acc-2") == "socks5h://172.17.0.1:40002"

    resolver.invalidate_exit("exit-1")

    assert resolver.assignment_for("acc-1", include_expired=True) is None
    assert resolver.assignment_for("acc-3", include_expired=True) is None
    survivor = resolver.assignment_for("acc-2", include_expired=True)
    assert survivor is not None
    assert survivor.exit_id == "exit-2"


@pytest.mark.asyncio
async def test_invalidate_forces_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_external_env(monkeypatch)
    urls = iter(
        [
            "socks5h://172.17.0.1:40001",
            "socks5h://172.17.0.1:40002",
        ]
    )
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(
            200,
            json={
                "exit_id": "exit-1",
                "socks_url": next(urls),
                "generation": 1,
                "ttl_s": 300,
                "healthy": True,
            },
        )

    resolver = _resolver_with_transport(handler)
    assert await resolver.resolve("acc-1") == "socks5h://172.17.0.1:40001"
    resolver.invalidate("acc-1")
    assert resolver.assignment_for("acc-1") is None
    assert await resolver.resolve("acc-1") == "socks5h://172.17.0.1:40002"
    assert calls[0] == 2


@pytest.mark.asyncio
async def test_last_known_assignment_survives_ttl_for_arbitration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    now = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "exit_id": "exit-1",
                "socks_url": "socks5h://172.17.0.1:40001",
                "generation": 1,
                "ttl_s": 30,
                "healthy": True,
            },
        )

    resolver = _resolver_with_transport(handler, clock=lambda: now[0])
    assert await resolver.resolve("acc-1") == "socks5h://172.17.0.1:40001"

    now[0] += 300.0
    assert resolver.assignment_for("acc-1") is None
    still_bound = resolver.assignment_for("acc-1", include_expired=True)
    assert still_bound is not None
    assert still_bound.exit_id == "exit-1"

    resolver.invalidate("acc-1")
    assert resolver.assignment_for("acc-1", include_expired=True) is None


# ---------------------------------------------------------------------- events


def test_event_payload_carries_hmac_and_never_raw_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    resolver = _resolver_with_transport(lambda request: httpx.Response(202))
    payload = resolver.event_payload(
        account_id="acc-1@example.com",
        exit_id="exit-1",
        event_kind="region_403",
        canary="exit_bad",
    )
    assert payload["account"] == hmac_account_id("hmac-key", "acc-1@example.com")
    assert payload["exit_id"] == "exit-1"
    assert payload["kind"] == "region_403"
    assert payload["canary"] == "exit_bad"
    assert "acc-1@example.com" not in str(payload)


@pytest.mark.asyncio
async def test_report_event_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_external_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    resolver = _resolver_with_transport(handler)
    ok = await resolver.report_event(
        account_id="acc-1", exit_id="exit-1", event_kind="region_403", canary="exit_bad"
    )
    assert ok is False


@pytest.mark.asyncio
async def test_report_event_nowait_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    resolver = _resolver_with_transport(lambda request: httpx.Response(500))
    resolver.report_event_nowait(
        account_id="acc-1",
        exit_id="exit-1",
        event_kind="region_403",
        canary="inconclusive",
    )
    await resolver.wait_pending()


# ---------------------------------------------------------------- pool status

_STATUS_FAILURE = {
    "manager_ok": False,
    "healthy": 0,
    "total": 0,
    "quarantined": 0,
    "disabled": 0,
    "bound_accounts": 0,
    "last_switch_at": 0,
}


@pytest.mark.asyncio
async def test_pool_status_parses_whitelisted_fields_and_sends_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "manager_ok": True,
                "exits": {
                    "healthy": 2,
                    "total": 3,
                    "quarantined": 1,
                    "disabled": 0,
                    "unknown": 9,
                },
                "bound_accounts": 5,
                "last_switch_at": 1700000000,
                "secret": "must-not-leak",
                "version": "1.2.3",
            },
        )

    resolver = _resolver_with_transport(handler)
    status = await resolver.pool_status()

    assert status == {
        "manager_ok": True,
        "healthy": 2,
        "total": 3,
        "quarantined": 1,
        "disabled": 0,
        "bound_accounts": 5,
        "last_switch_at": 1700000000,
    }
    assert "secret" not in status
    assert "version" not in status
    assert len(seen) == 1
    assert seen[0].url.path == "/v1/status"
    assert seen[0].headers["authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_pool_status_cache_hits_within_15s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    calls = [0]
    now = [100.0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(
            200,
            json={"manager_ok": True, "exits": {"healthy": 1, "total": 1}},
        )

    resolver = _resolver_with_transport(handler, clock=lambda: now[0])

    first = await resolver.pool_status()
    now[0] += 14.9
    second = await resolver.pool_status()
    assert first == second
    assert calls[0] == 1

    now[0] += 0.1
    third = await resolver.pool_status()
    assert third == first
    assert calls[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "content"),
    [
        (503, b""),
        (200, b"not-json"),
    ],
)
async def test_pool_status_http_error_and_invalid_json_report_manager_down(
    monkeypatch: pytest.MonkeyPatch, status_code: int, content: bytes
) -> None:
    _set_external_env(monkeypatch)
    resolver = _resolver_with_transport(
        lambda request: httpx.Response(status_code, content=content)
    )

    assert await resolver.pool_status() == _STATUS_FAILURE


@pytest.mark.asyncio
async def test_pool_status_transport_timeout_reports_manager_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    resolver = _resolver_with_transport(handler)
    assert await resolver.pool_status() == _STATUS_FAILURE


@pytest.mark.asyncio
async def test_pool_status_wait_for_timeout_reports_manager_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    from aistudio_api.infrastructure.exit_pool import resolver as resolver_module

    monkeypatch.setattr(resolver_module, "STATUS_TIMEOUT_S", 0.01)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"manager_ok": True, "exits": {}})

    resolver = _resolver_with_transport(handler)
    assert await resolver.pool_status() == _STATUS_FAILURE


# ---------------------------------------------------------------- engine wiring


class _FakeExitResolver:
    def __init__(self, result: str | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        self.calls.append((account_id, kind))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_off_mode_runtime_keeps_global_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "proxy_url", "socks5://172.17.0.1:40001")
    runtime = BrowserlessEngineRuntime(
        {"mode": "browserless", "browserless": {}},
        signer_client=cast(Any, object()),
    )
    assert runtime._exit_resolver is None  # type: ignore[attr-defined]

    build = await runtime.create_session(
        slot_id="slot-1",
        auth_file=None,
        auth_user="0",
        tier="Free",
        account_id="acc-1",
        realm_id="realm-1",
    )
    assert build.session._proxy == "socks5://172.17.0.1:40001"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_off_mode_never_constructs_an_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx as httpx_module

    def _boom(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("off mode must not build an httpx client")

    monkeypatch.setattr(httpx_module, "AsyncClient", _boom)
    runtime = BrowserlessEngineRuntime(
        {"mode": "browserless", "browserless": {}},
        signer_client=cast(Any, object()),
    )
    assert runtime._exit_resolver is None  # type: ignore[attr-defined]
    build = await runtime.create_session(
        slot_id="slot-1",
        auth_file=None,
        auth_user="0",
        tier="Free",
        account_id="acc-1",
        realm_id="realm-1",
    )
    assert build.session is not None


@pytest.mark.asyncio
async def test_external_runtime_injects_resolved_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "proxy_url", "socks5://172.17.0.1:40001")
    fake = _FakeExitResolver(result="socks5h://172.17.0.1:40002")
    runtime = BrowserlessEngineRuntime(
        {"mode": "browserless", "browserless": {}},
        signer_client=cast(Any, object()),
        exit_resolver=cast(Any, fake),
    )

    build = await runtime.create_session(
        slot_id="slot-1",
        auth_file=None,
        auth_user="0",
        tier="Free",
        account_id="acc-1",
        realm_id="realm-1",
        surface="chat",
    )
    assert fake.calls == [("acc-1", "chat")]
    assert build.session._proxy == "socks5h://172.17.0.1:40002"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_external_runtime_falls_back_on_resolver_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.config import settings

    monkeypatch.setattr(settings, "proxy_url", "socks5://172.17.0.1:40001")
    fake = _FakeExitResolver(error=RuntimeError("manager down"))
    runtime = BrowserlessEngineRuntime(
        {"mode": "browserless", "browserless": {}},
        signer_client=cast(Any, object()),
        exit_resolver=cast(Any, fake),
    )

    build = await runtime.create_session(
        slot_id="slot-1",
        auth_file=None,
        auth_user="0",
        tier="Free",
        account_id="acc-1",
        realm_id="realm-1",
    )
    assert build.session._proxy == "socks5://172.17.0.1:40001"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_shutdown_runtime_closes_and_resets_exit_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.api import app as app_module
    from aistudio_api.infrastructure import exit_pool as exit_pool_module

    closed: list[bool] = []
    resets: list[object] = []

    class _FakeResolver:
        async def aclose(self) -> None:
            closed.append(True)

    fake = _FakeResolver()
    monkeypatch.setattr(exit_pool_module, "get_exit_resolver", lambda: fake)
    monkeypatch.setattr(
        exit_pool_module,
        "reset_exit_resolver",
        lambda resolver=None: resets.append(resolver),
    )

    state = SimpleNamespace(
        keepalive=None,
        instance_manager=None,
        engine_runtime=None,
        client=None,
    )
    await app_module._shutdown_runtime(cast(Any, state))

    assert closed == [True]
    assert resets == [None]


@pytest.mark.asyncio
async def test_pool_status_coalesces_concurrent_cold_cache_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_external_env(monkeypatch)
    calls = [0]

    async def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        await asyncio.sleep(0.02)
        return httpx.Response(
            200,
            json={"manager_ok": True, "exits": {"healthy": 1, "total": 1}},
        )

    resolver = _resolver_with_transport(handler)
    try:
        first, second = await asyncio.gather(
            resolver.pool_status(), resolver.pool_status()
        )
    finally:
        await resolver.aclose()

    assert calls[0] == 1
    assert first == second
    assert first["manager_ok"] is True
