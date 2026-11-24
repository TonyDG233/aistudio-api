"""Per-account exit routing for credential-bearing upstream calls.

Covers the external exit mode (all account-credential requests use the bound
exit, fail-closed otherwise), the off-mode compatibility path and the
emergency direct-fallback valve.  Everything is offline: fake resolvers,
fake transports and fake httpx clients only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aistudio_api.api import routes_accounts
from aistudio_api.api.routes_accounts import (
    IMPORT_ANCHOR_PREFIX,
    ImportCookiesRequest,
    ProbeAndImportRequest,
    RefreshAccountResult,
    RefreshAccountsRequest,
    import_bundle,
    import_cookies,
    probe_and_import,
    refresh_accounts,
)
from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_keepalive import (
    AccountKeepaliveService,
    KeepaliveSettings,
)
from aistudio_api.application.account_service import AccountService
from aistudio_api.application.exit_arbitration import (
    CanaryVerdict,
    ExitArbitration,
)
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.config import settings
from aistudio_api.domain.errors import ExitBudgetWaitError, RoutingError
from aistudio_api.domain.instances import InstanceState
from aistudio_api.infrastructure.account.account_info_refresher import (
    EXTERNAL_BATCH_PACING_SECONDS,
    AccountInfoRefresher,
    RefreshResult,
    TransportResponse,
    reset_skip_log_throttle,
)
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.account.cookie_parser import (
    probe_account_benefit_tier,
    rotate_google_cookies,
)
from aistudio_api.infrastructure.exit_pool import (
    reset_direct_fallback_log,
    reset_exit_resolver,
    resolve_credential_exit,
)
from aistudio_api.infrastructure.exit_pool.resolver import ExitAssignment, LeaseResult
from aistudio_api.infrastructure.gateway.instance_pool import InstancePool, InstanceSpec
from aistudio_api.infrastructure.waa.browserless_session import BrowserlessSession
from aistudio_api.infrastructure.waa.engine_runtime import (
    BrowserlessEngineRuntime,
    build_engine_runtime,
)

EXIT_ONE = "socks5h://127.0.0.1:40001"
EXIT_TWO = "socks5h://127.0.0.1:40002"
SENTINEL_GLOBAL = "socks5://127.0.0.1:59998"


@contextmanager
def _capture_logger(
    caplog: pytest.LogCaptureFixture,
    name: str,
    level: int = logging.WARNING,
):
    """Capture exactly one logger regardless of root/handler state.

    ``caplog.at_level(level)`` only lowers the *root* logger and relies on the
    pytest capture handler still being mounted on root.  A suite that ran
    ``setup_logging`` earlier can leave the ``aistudio`` parent raised (or the
    root handler cleared), so the concrete logger would drop records on Linux
    even though Windows passes.  Attaching the capture handler directly to the
    target logger (with propagation off to avoid duplicate records) is
    independent of that global state.
    """
    target = logging.getLogger(name)
    previous_level = target.level
    previous_propagate = target.propagate
    previous_handler_level = caplog.handler.level
    target.addHandler(caplog.handler)
    target.setLevel(level)
    target.propagate = False
    caplog.handler.setLevel(level)
    try:
        yield
    finally:
        target.removeHandler(caplog.handler)
        target.setLevel(previous_level)
        target.propagate = previous_propagate
        caplog.handler.setLevel(previous_handler_level)


_STORAGE_STATE: dict[str, object] = {
    "cookies": [
        {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
        {"name": "SAPISID", "value": "sap", "domain": ".google.com", "path": "/"},
    ],
    "origins": [],
}


class FakeExitResolver:
    """Manager double: returns a configured exit per account, or nothing."""

    def __init__(
        self,
        mapping: dict[str, str] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.mapping = mapping or {}
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        self.calls.append((account_id, kind))
        if self.fail:
            return None
        return self.mapping.get(account_id)


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        self.requests.append((method, url))
        if method == "POST":
            return TransportResponse(status=200, body="[1]")
        return TransportResponse(
            status=200,
            body='<script>{"oPEP7c":"user@gmail.com"}</script>',
        )


def _make_store(tmp_path: Any, *, count: int = 1) -> tuple[AccountStore, list[Any]]:
    store = AccountStore(tmp_path / "accounts")
    accounts = [
        store.save_account(
            name=f"Acc{i}",
            email=f"acc{i}@example.com",
            storage_state=_STORAGE_STATE,
            account_id=f"acc{i}",
        )
        for i in range(1, count + 1)
    ]
    return store, accounts


def test_external_mode_uses_per_account_exit_for_tier_and_email(tmp_path: Any) -> None:
    store, accounts = _make_store(tmp_path, count=2)
    resolver = FakeExitResolver({"acc1": EXIT_ONE, "acc2": EXIT_TWO})
    proxies: list[str | None] = []
    sleeps: list[float] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    refresher = AccountInfoRefresher(
        store,
        transport_factory=factory,
        exit_resolver=resolver,
        sleep=fake_sleep,
    )

    results = asyncio.run(refresher.refresh_accounts(accounts, refresh_email=True))

    assert all(result.ok for result in results)
    assert resolver.calls == [("acc1", "chat"), ("acc2", "chat")]
    # Each account issues one tier POST and one emails-page GET on its own exit.
    assert proxies == [EXIT_ONE, EXIT_ONE, EXIT_TWO, EXIT_TWO]
    # Serial pacing between the two accounts (injectable sleep).
    assert sleeps == [EXTERNAL_BATCH_PACING_SECONDS]


def test_external_no_exit_skips_with_zero_requests(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    store, accounts = _make_store(tmp_path)
    resolver = FakeExitResolver(fail=True)
    proxies: list[str | None] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    refresher = AccountInfoRefresher(
        store, transport_factory=factory, exit_resolver=resolver
    )
    arbiter = _RecordingArbiter("account_bad")
    refresher.set_region_arbiter(arbiter)

    with _capture_logger(caplog, "aistudio.account_refresher"):
        result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.ok is False
    assert result.skipped is True
    assert result.skip_reason == "no_exit"
    assert result.error is not None and "skipped_no_exit" in result.error
    assert proxies == []  # no request was even attempted
    assert arbiter.calls == []  # no region canary without an exit
    assert any("无可用出口" in record.getMessage() for record in caplog.records)


def test_off_mode_uses_global_proxy_unchanged(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, accounts = _make_store(tmp_path)
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    proxies: list[str | None] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    refresher = AccountInfoRefresher(store, transport_factory=factory)

    result = asyncio.run(refresher.refresh_account(accounts[0], refresh_email=False))

    assert result.ok is True
    assert proxies == [SENTINEL_GLOBAL]
    assert refresher.concurrency >= 1


def test_external_never_uses_global_proxy_sentinel(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, accounts = _make_store(tmp_path)
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    resolver = FakeExitResolver({"acc1": EXIT_ONE})
    captured: list[str | None] = []

    class _FakeClient:
        def __init__(self, *, timeout: float, proxy: str | None) -> None:
            del timeout
            captured.append(proxy)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def request(
            self, method: str, url: str, **kwargs: object
        ) -> SimpleNamespace:
            del url, kwargs
            body = "[1]" if method == "POST" else "<html></html>"
            return SimpleNamespace(status_code=200, text=body, headers={})

    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.account_info_refresher.httpx.AsyncClient",
        _FakeClient,
    )
    refresher = AccountInfoRefresher(store, exit_resolver=resolver)

    result = asyncio.run(refresher.refresh_account(accounts[0], refresh_email=False))

    assert result.ok is True
    assert captured == [EXIT_ONE]
    assert SENTINEL_GLOBAL not in captured


def test_cookie_parser_probe_uses_resolved_exit_not_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    reset_exit_resolver(cast(Any, FakeExitResolver({"acc1": EXIT_ONE})))
    captured: list[str | None] = []

    class _FakeClient:
        def __init__(self, *, timeout: float, proxy: str | None, **kwargs: object):
            del timeout, kwargs
            captured.append(proxy)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object) -> Any:
            del args, kwargs
            return SimpleNamespace(status_code=200, text="[1]")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    tier = asyncio.run(
        probe_account_benefit_tier({"SID": "sid", "SAPISID": "sap"}, account_id="acc1")
    )

    assert tier == "Pro"
    assert captured == [EXIT_ONE]
    assert SENTINEL_GLOBAL not in captured


def test_cookie_parser_probe_fails_closed_without_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    captured: list[str | None] = []

    class _FakeClient:  # pragma: no cover - must never be built
        def __init__(self, **kwargs: object) -> None:
            captured.append(cast("str | None", kwargs.get("proxy")))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    tier = asyncio.run(
        probe_account_benefit_tier({"SID": "sid", "SAPISID": "sap"}, account_id="acc1")
    )

    assert tier is None
    assert captured == []


def test_explicit_none_proxy_does_not_bypass_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q-5: a stray ``proxy=None`` must re-resolve, not claim resolved-direct."""
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    captured: list[str | None] = []

    class _FakeClient:  # pragma: no cover - must never be built
        def __init__(self, **kwargs: object) -> None:
            captured.append(cast("str | None", kwargs.get("proxy")))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    tier = asyncio.run(
        probe_account_benefit_tier(
            {"SID": "sid", "SAPISID": "sap"}, account_id="acc1", proxy=None
        )
    )

    assert tier is None
    assert captured == []


def test_emergency_direct_fallback_is_opt_in_and_log_throttled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    resolver = FakeExitResolver(fail=True)
    reset_direct_fallback_log()

    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    with _capture_logger(caplog, "aistudio.exit-pool"):
        first = asyncio.run(resolve_credential_exit("acc1", resolver=resolver))
        second = asyncio.run(resolve_credential_exit("acc1", resolver=resolver))

    assert first.action == "direct_fallback"
    assert first.proxy is None
    assert second.action == "direct_fallback"
    fallback_logs = [
        record for record in caplog.records if "应急阀" in record.getMessage()
    ]
    assert len(fallback_logs) == 1  # throttled to one warning per window

    monkeypatch.delenv("AISTUDIO_EXIT_DIRECT_FALLBACK", raising=False)
    reset_direct_fallback_log()
    closed = asyncio.run(resolve_credential_exit("acc2", resolver=resolver))
    assert closed.action == "skip"
    assert closed.proxy is None


def test_emergency_direct_fallback_never_raw_probes_in_external_mode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """External maintenance probes never send from the host egress.

    The emergency valve is a generation-path escape hatch; a bare metadata
    probe from the VPS egress gets a region 403 that a canary would then
    misattribute to the account (the 5-false-verify incident).  The probe must
    skip without issuing any request instead.
    """
    store, accounts = _make_store(tmp_path)
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    resolver = FakeExitResolver(fail=True)
    proxies: list[str | None] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    refresher = AccountInfoRefresher(
        store, transport_factory=factory, exit_resolver=resolver
    )
    arbiter = _RecordingArbiter("account_bad")
    refresher.set_region_arbiter(arbiter)

    result = asyncio.run(refresher.refresh_account(accounts[0], refresh_email=False))

    assert result.ok is False
    assert result.skipped is True
    assert result.skip_reason == "no_pooled_exit"
    assert result.error is not None and "skipped_no_exit" in result.error
    assert proxies == []
    assert arbiter.calls == []
    stored = store.get_account(accounts[0].id)
    assert stored is not None
    assert stored.verify_required is False


def test_emergency_direct_fallback_skips_email_probe_with_zero_requests(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The email sub-probe must not send from the host egress either."""
    store, accounts = _make_store(tmp_path)
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    resolver = FakeExitResolver(fail=True)
    proxies: list[str | None] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    refresher = AccountInfoRefresher(
        store, transport_factory=factory, exit_resolver=resolver
    )
    arbiter = _RecordingArbiter("account_bad")
    refresher.set_region_arbiter(arbiter)

    result = asyncio.run(refresher.refresh_account(accounts[0], refresh_email=True))

    assert result.ok is False
    assert result.skipped is True
    assert result.skip_reason == "no_pooled_exit"
    assert proxies == []
    assert arbiter.calls == []
    stored = store.get_account(accounts[0].id)
    assert stored is not None
    assert stored.verify_required is False


def test_emergency_direct_fallback_skips_refresh_tier_with_zero_requests(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, accounts = _make_store(tmp_path)
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    resolver = FakeExitResolver(fail=True)
    proxies: list[str | None] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    refresher = AccountInfoRefresher(
        store, transport_factory=factory, exit_resolver=resolver
    )
    arbiter = _RecordingArbiter("account_bad")
    refresher.set_region_arbiter(arbiter)

    result = asyncio.run(refresher.refresh_tier(accounts[0]))

    assert result["skipped"] is True
    assert result["error"] == "skipped_no_exit: no_pooled_exit"
    assert proxies == []
    assert arbiter.calls == []
    stored = store.get_account(accounts[0].id)
    assert stored is not None
    assert stored.verify_required is False


class _EmptyBoundSignerSession:
    """Emergency-valve browserless session: no bound exit, sends host-direct."""

    engine = "browserless"
    _bound_socks = ""

    def __init__(self) -> None:
        self.calls = 0

    def is_alive(self) -> bool:
        return True

    async def send_hooked_request(self, **kwargs: object) -> tuple[int, bytes]:
        del kwargs
        self.calls += 1
        return 403, REGION_403_BODY.encode("utf-8")


def test_pooled_session_without_bound_exit_skips_without_request(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty ``_bound_socks`` session must skip, never fall back to binding."""
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    resolver = _ArbitrationResolver()
    session = _EmptyBoundSignerSession()
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, resolver),
        slot_provider=lambda: (
            SimpleNamespace(account_id=accounts[0].id, session=session),
        ),
        pacing_seconds=0.0,
    )
    arbiter = _RecordingArbiter("account_bad")
    refresher.set_region_arbiter(arbiter)

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.skipped is True
    assert result.skip_reason == "no_pooled_exit"
    assert session.calls == 0  # the session transport was never used
    assert arbiter.calls == []
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is False


def test_cookie_parser_probe_blocks_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_direct_fallback_log()
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    captured: list[str | None] = []

    class _FakeClient:  # pragma: no cover - must never be built
        def __init__(self, **kwargs: object) -> None:
            captured.append(cast("str | None", kwargs.get("proxy")))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    tier = asyncio.run(
        probe_account_benefit_tier({"SID": "sid", "SAPISID": "sap"}, account_id="acc1")
    )

    assert tier is None
    assert captured == []


def test_rotate_cookies_blocks_direct_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_direct_fallback_log()
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    captured: list[str | None] = []

    class _FakeClient:  # pragma: no cover - must never be built
        def __init__(self, **kwargs: object) -> None:
            captured.append(cast("str | None", kwargs.get("proxy")))

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    cookies = {
        "SID": "sid",
        "SAPISID": "sap",
        "LSID": "lsid",
        "__Secure-1PSIDTS": "old",
    }

    result = asyncio.run(rotate_google_cookies(cookies, account_id="acc1"))

    assert result["ok"] is False
    assert result["skipped"] == "no_exit"
    assert result["reason"] == "no_pooled_exit"
    assert cookies["__Secure-1PSIDTS"] == "old"
    assert captured == []


def test_fetch_app_html_external_proxy_none_never_sends(tmp_path: Any) -> None:
    store, accounts = _make_store(tmp_path)
    resolver = FakeExitResolver(fail=True)
    proxies: list[str | None] = []

    def factory(proxy: str | None) -> RecordingTransport:
        proxies.append(proxy)
        return RecordingTransport()

    refresher = AccountInfoRefresher(
        store, transport_factory=factory, exit_resolver=resolver
    )
    cookies = refresher._collect_cookies(  # type: ignore[attr-defined]
        [{"name": "SID", "value": "sid", "domain": ".google.com"}]
    )

    with pytest.raises(RuntimeError, match="skipped_no_exit"):
        asyncio.run(
            refresher._fetch_app_html(  # type: ignore[attr-defined]
                cookies, proxy=None, allow_bare=True
            )
        )

    assert proxies == []
    assert store.get_account(accounts[0].id) is not None


def test_keepalive_probe_exit_id_empty_bound_socks_is_inconclusive(
    tmp_path: Any,
) -> None:
    store, accounts = _make_store(tmp_path)
    resolver = _ArbitrationResolver()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        cast(Any, SimpleNamespace()),
        instance_manager=SimpleNamespace(
            pool_slots=lambda: (
                SimpleNamespace(
                    account_id=accounts[0].id,
                    surface="chat",
                    session=_EmptyBoundSignerSession(),
                ),
            )
        ),
    )

    probe_exit = service._probe_exit_id(  # type: ignore[attr-defined]
        accounts[0].id, SimpleNamespace(resolver=resolver), "chat"
    )

    assert probe_exit == ""


def test_keepalive_skipped_probe_does_not_advance_due(tmp_path: Any) -> None:
    store = AccountStore(tmp_path / "accounts")
    account = store.save_account(
        name="Stale",
        email="stale@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_stale",
    )
    stale_at = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    store.update_account_refresh(
        account.id, tier="Free", tier_checked_at=stale_at, expired=False
    )
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        exit_resolver=FakeExitResolver(fail=True),
    )

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    class _Rotator:
        def is_auth_locked(self, account_id: str) -> bool:
            del account_id
            return False

    clock = _Clock()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        refresher,
        rotator=_Rotator(),
        clock=clock,
        tick_seconds=3600.0,
    )
    service._next_due = {"metadata_refresh": clock.now}
    settings_block = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        active_activity=False,
    )

    asyncio.run(service._run_due(settings_block))

    # A skipped account must retry in the short failure window, never 24h.
    assert service._next_due["metadata_refresh"] == clock.now + 300.0
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier_checked_at == stale_at


def test_manual_refresh_result_reports_skipped() -> None:
    class _SkipRefresher:
        async def refresh_accounts(
            self, accounts: list[Any], **kwargs: object
        ) -> list[RefreshResult]:
            del kwargs
            return [
                RefreshResult(
                    account_id=item.id,
                    ok=False,
                    skipped=True,
                    skip_reason="no_exit",
                    error="skipped_no_exit: no_exit",
                )
                for item in accounts
            ]

    account_service = MagicMock()
    account_service.list_accounts.return_value = [
        SimpleNamespace(id="acc_skip", name="S", email="s@x.com")
    ]

    response = asyncio.run(
        refresh_accounts(
            RefreshAccountsRequest(all=True),
            account_service=account_service,
            refresher=cast(Any, _SkipRefresher()),
        )
    )

    assert len(response.results) == 1
    result: RefreshAccountResult = response.results[0]
    assert result.id == "acc_skip"
    assert result.skipped is True
    assert result.skip_reason == "no_exit"


def test_import_probe_skip_still_imports(tmp_path: Any) -> None:
    from aistudio_api.api.state import RuntimeState
    from aistudio_api.application.account_service import AccountService

    store = AccountStore(tmp_path / "accounts_import")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=MagicMock())
    resolver = FakeExitResolver(fail=True)
    reset_exit_resolver(cast(Any, resolver))

    email_probe = AsyncMock(return_value="probed@example.com")
    tier_probe = AsyncMock(return_value="Pro")
    original_email = routes_accounts.probe_account_email
    original_tier = routes_accounts.probe_account_benefit_tier
    routes_accounts.probe_account_email = email_probe  # type: ignore[assignment]
    routes_accounts.probe_account_benefit_tier = tier_probe  # type: ignore[assignment]
    try:
        response = asyncio.run(
            import_cookies(
                ImportCookiesRequest(cookies="SID=sid; SAPISID=sap"),
                account_service=service,
                runtime_state=state,
            )
        )
    finally:
        routes_accounts.probe_account_email = original_email
        routes_accounts.probe_account_benefit_tier = original_tier

    assert response.account_id
    assert email_probe.await_count == 0
    assert tier_probe.await_count == 0
    stored = store.get_account(response.account_id)
    assert stored is not None
    assert stored.tier == "Free"


def test_probe_and_import_external_without_exit_keeps_real_account(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-R2: no exit must not 503 the batch.

    The jar is saved under a real account id, the exit lookup fails closed, the
    multi-account probe is skipped (zero upstream calls) and the account is kept
    with its reason recorded.
    """
    store = AccountStore(tmp_path / "accounts_probe")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = FakeExitResolver(fail=True)
    reset_exit_resolver(cast(Any, resolver))
    probe = AsyncMock(
        return_value=[{"auth_user": "0", "email": "a@b.com", "tier": "Pro"}]
    )
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        probe,
    )

    response = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert response.imported_count == 1
    account_id = response.accounts[0].id
    assert not account_id.startswith("cookie_")
    assert response.skipped == [account_id]
    assert response.skip_reason == "no_exit"
    assert response.probe_failed[account_id] == "no_exit"
    probe.assert_not_called()
    assert resolver.calls == [(account_id, "chat")]
    stored = store.get_account(account_id)
    assert stored is not None
    assert stored.email is None
    assert stored.last_refresh_error == "no_exit"


def test_probe_and_import_external_splits_multi_account_with_real_ids(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-R2: probe through the anchor exit, then split into real accounts."""
    store = AccountStore(tmp_path / "accounts_probe_multi")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    probe = AsyncMock(
        return_value=[
            {"auth_user": "0", "email": "a@b.com", "tier": "Pro"},
            {"auth_user": "1", "email": "c@d.com", "tier": "Ultra"},
        ]
    )
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        probe,
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_email", AsyncMock(return_value="c@d.com")
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value="Ultra")
    )

    response = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert response.imported_count == 2
    ids = {account.id for account in response.accounts}
    assert len(ids) == 2
    assert all(not account_id.startswith("cookie_") for account_id in ids)
    assert all(account in ids for account, _ in resolver.calls)
    stored = [store.get_account(account_id) for account_id in ids]
    assert {item.tier for item in stored if item is not None} == {"Pro", "Ultra"}
    assert probe.await_count == 1
    assert probe.await_args is not None
    assert probe.await_args.kwargs["account_id"] in ids


def test_probe_and_import_partial_probe_failure_keeps_account(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-R2: a failed identity probe keeps the account and records the reason."""
    store = AccountStore(tmp_path / "accounts_probe_partial")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        AsyncMock(
            return_value=[
                {"auth_user": "0", "email": "a@b.com", "tier": "Pro"},
                {"auth_user": "1", "email": None, "tier": None},
            ]
        ),
    )
    monkeypatch.setattr(
        routes_accounts,
        "probe_account_email",
        AsyncMock(side_effect=RuntimeError("email boom")),
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value=None)
    )

    response = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert response.imported_count == 2
    failed_id = next(
        account_id
        for account_id in response.probe_failed
        if account_id not in response.skipped
    )
    reason = response.probe_failed[failed_id]
    assert "email probe failed" in reason
    stored = store.get_account(failed_id)
    assert stored is not None
    assert stored.last_refresh_error is not None
    assert "email probe failed" in stored.last_refresh_error


def test_probe_and_import_external_is_idempotent_for_same_cookie(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-A: re-importing the same jar must not add duplicate accounts."""
    store = AccountStore(tmp_path / "accounts_probe_idempotent")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        AsyncMock(return_value=[{"auth_user": "0", "email": "a@b.com", "tier": "Pro"}]),
    )

    cookies = "SID=sid; SAPISID=sap"
    first = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies=cookies),
            account_service=service,
            runtime_state=state,
        )
    )
    second = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies=cookies),
            account_service=service,
            runtime_state=state,
        )
    )

    assert first.imported_count == 1
    assert second.imported_count == 1
    assert first.accounts[0].id == second.accounts[0].id
    assert first.accounts[0].id.startswith(IMPORT_ANCHOR_PREFIX)
    assert len(service.list_accounts()) == 1
    # The manager is never keyed by a synthetic ``cookie_<hex>`` id.
    assert all(not account_id.startswith("cookie_") for account_id, _ in resolver.calls)


def test_probe_and_import_external_u0_leaves_no_orphan_anchor(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-A: a u/0 probe result claims the anchor; no email=None orphan stays."""
    store = AccountStore(tmp_path / "accounts_probe_u0")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        AsyncMock(return_value=[{"auth_user": "0", "email": "a@b.com", "tier": "Pro"}]),
    )

    response = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    accounts = service.list_accounts()
    assert response.imported_count == 1
    assert len(accounts) == 1
    only = accounts[0]
    assert only.id == response.accounts[0].id
    assert only.email == "a@b.com"
    assert only.tier == "Pro"
    assert resolver.calls == [(only.id, "chat")]


def test_probe_and_import_external_without_u0_drops_placeholder(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-A: probe success without u/0 must not leave the anchor behind."""
    store = AccountStore(tmp_path / "accounts_probe_no_u0")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        AsyncMock(
            return_value=[{"auth_user": "1", "email": "c@d.com", "tier": "Ultra"}]
        ),
    )

    response = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    accounts = service.list_accounts()
    assert response.imported_count == 1
    assert len(accounts) == 1
    assert accounts[0].email == "c@d.com"
    assert not any(account.id.startswith(IMPORT_ANCHOR_PREFIX) for account in accounts)
    assert all(not account_id.startswith("cookie_") for account_id, _ in resolver.calls)


def test_probe_and_import_external_failure_keeps_single_placeholder(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-A: failed/no-exit re-imports keep exactly one retryable placeholder."""
    store = AccountStore(tmp_path / "accounts_probe_failure")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    resolver = FakeExitResolver(fail=True)
    reset_exit_resolver(cast(Any, resolver))
    probe = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        probe,
    )

    cookies = "SID=sid; SAPISID=sap"
    first = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies=cookies),
            account_service=service,
            runtime_state=state,
        )
    )
    second = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies=cookies),
            account_service=service,
            runtime_state=state,
        )
    )

    accounts = service.list_accounts()
    assert len(accounts) == 1
    placeholder = accounts[0]
    assert placeholder.id.startswith(IMPORT_ANCHOR_PREFIX)
    assert first.accounts[0].id == second.accounts[0].id == placeholder.id
    assert placeholder.email is None
    assert placeholder.last_refresh_error == "no_exit"
    assert first.skip_reason == second.skip_reason == "no_exit"
    assert first.skipped == second.skipped == [placeholder.id]
    probe.assert_not_called()


def test_import_bundle_external_paces_probes(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q-4: the 0.3s inter-account pacing is observable and only between items."""
    from aistudio_api.api.routes_accounts import (
        IMPORT_PROBE_PACING_SECONDS,
        BundleAccountItem,
        ImportBundleRequest,
    )

    store = AccountStore(tmp_path / "accounts_bundle_pacing")
    service = AccountService(store)
    reset_exit_resolver(cast(Any, _AlwaysExitResolver(EXIT_ONE)))
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(routes_accounts.asyncio, "sleep", fake_sleep)

    response = asyncio.run(
        import_bundle(
            ImportBundleRequest(
                accounts=[
                    BundleAccountItem(
                        cookies="SID=sid1; SAPISID=sap1",
                        email="a@b.com",
                        tier="Pro",
                    ),
                    BundleAccountItem(
                        cookies="SID=sid2; SAPISID=sap2",
                        email="c@d.com",
                        tier="Pro",
                    ),
                ]
            ),
            account_service=service,
        )
    )

    assert response.imported_count == 2
    assert sleeps == [IMPORT_PROBE_PACING_SECONDS]


def test_rotate_cookies_uses_resolved_exit_not_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    reset_exit_resolver(cast(Any, FakeExitResolver({"acc1": EXIT_ONE})))
    captured: list[str | None] = []

    class _FakeClient:
        def __init__(self, *, timeout: float, proxy: str | None, **kwargs: object):
            del timeout, kwargs
            captured.append(proxy)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            del args, kwargs
            return httpx.Response(
                200,
                headers=[
                    (
                        "set-cookie",
                        "__Secure-1PSIDTS=rotated; Domain=.google.com; Path=/",
                    )
                ],
            )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    cookies = {
        "SID": "sid",
        "SAPISID": "sap",
        "LSID": "lsid",
        "__Secure-1PSIDTS": "old",
    }

    result = asyncio.run(rotate_google_cookies(cookies, account_id="acc1"))

    assert result["ok"] is True
    assert captured == [EXIT_ONE]
    assert SENTINEL_GLOBAL not in captured


def test_rotate_cookies_fails_closed_without_exit() -> None:
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    cookies = {
        "SID": "sid",
        "SAPISID": "sap",
        "LSID": "lsid",
        "__Secure-1PSIDTS": "old",
    }

    result = asyncio.run(rotate_google_cookies(cookies, account_id="acc1"))

    assert result["ok"] is False
    assert result["skipped"] == "no_exit"
    assert cookies["__Secure-1PSIDTS"] == "old"


def test_resolve_credential_exit_off_mode_returns_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    decision = asyncio.run(resolve_credential_exit("acc1", resolver=None))
    assert decision.action == "proxy"
    assert decision.proxy == SENTINEL_GLOBAL
    assert decision.external is False


# --------------------------------------------------------------------------- #
# P1-1: verify_account_identity passes the real account id to the bare tier probe
# --------------------------------------------------------------------------- #


def test_verify_account_identity_passes_account_id_to_tier_probe(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.account import cookie_parser
    from aistudio_api.infrastructure.gateway.session_auth import (
        verify_account_identity,
    )

    acc_dir = tmp_path / "acc"
    acc_dir.mkdir()
    auth_file = acc_dir / "auth.json"
    auth_file.write_text(
        json.dumps({"cookies": [{"name": "SID", "value": "sid"}], "origins": []}),
        encoding="utf-8",
    )
    (acc_dir / "meta.json").write_text(
        json.dumps(
            {"id": "acc_1", "email": "a@b.com", "tier": "Free", "auth_user": "0"}
        ),
        encoding="utf-8",
    )

    page = MagicMock()
    page.url = "https://aistudio.google.com/"
    page.evaluate = AsyncMock(return_value=True)
    page.get_cookies = AsyncMock(return_value=[])

    calls: list[str | None] = []

    async def fake_probe(
        cookie_dict: dict[str, str],
        auth_user: str = "0",
        *,
        account_id: str | None = None,
    ) -> str | None:
        del cookie_dict, auth_user
        calls.append(account_id)
        return "Pro"

    monkeypatch.setattr(cookie_parser, "probe_account_benefit_tier", fake_probe)
    # Avoid touching a real store: the unknown id update is a no-op anyway.

    ok = asyncio.run(verify_account_identity(page, str(auth_file)))

    assert ok is True
    assert calls == ["acc_1"]


# --------------------------------------------------------------------------- #
# P1-2: import-time cookie pre-refresh uses the account exit (external) / global (off)
# --------------------------------------------------------------------------- #


class _RecordingBrowserSession:
    engine = "browser"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def import_cookies(
        self,
        cookie_string: str,
        auth_file: str | None = None,
        *,
        proxy: object = None,
    ) -> int:
        self.calls.append(
            {"cookies": cookie_string, "auth_file": auth_file, "proxy": proxy}
        )
        return 2


class _AlwaysExitResolver:
    def __init__(self, exit_url: str) -> None:
        self.exit_url = exit_url
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        self.calls.append((account_id, kind))
        return self.exit_url


def test_import_prerefresh_uses_account_exit_in_external_mode(
    tmp_path: Any,
) -> None:
    store = AccountStore(tmp_path / "accounts_prerefresh")
    service = AccountService(store)
    session = _RecordingBrowserSession()
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    state = RuntimeState(
        account_service=service,
        client=cast(Any, SimpleNamespace(_session=session)),
        rotator=MagicMock(),
        engine_settings={"mode": "browser"},
    )

    response = asyncio.run(
        import_cookies(
            ImportCookiesRequest(
                cookies="SID=sid; SAPISID=sap", email="a@b.com", tier="Pro"
            ),
            account_service=service,
            runtime_state=state,
        )
    )

    assert session.calls, "the browser pre-refresh must not be skipped"
    assert session.calls[-1]["proxy"] == EXIT_ONE
    assert resolver.calls == [(response.account_id, "chat")]
    assert not response.account_id.startswith("cookie_")


def test_import_prerefresh_skips_when_external_has_no_exit(
    tmp_path: Any,
) -> None:
    store = AccountStore(tmp_path / "accounts_prerefresh_skip")
    service = AccountService(store)
    session = _RecordingBrowserSession()
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    state = RuntimeState(
        account_service=service,
        client=cast(Any, SimpleNamespace(_session=session)),
        rotator=MagicMock(),
        engine_settings={"mode": "browser"},
    )

    response = asyncio.run(
        import_cookies(
            ImportCookiesRequest(
                cookies="SID=sid; SAPISID=sap", email="a@b.com", tier="Pro"
            ),
            account_service=service,
            runtime_state=state,
        )
    )

    # Fail closed: no pre-refresh, but the account import still completes.
    assert session.calls == []
    assert store.get_account(response.account_id) is not None


def test_import_prerefresh_off_mode_keeps_global_proxy(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    store = AccountStore(tmp_path / "accounts_prerefresh_off")
    service = AccountService(store)
    session = _RecordingBrowserSession()
    state = RuntimeState(
        account_service=service,
        client=cast(Any, SimpleNamespace(_session=session)),
        rotator=MagicMock(),
        engine_settings={"mode": "browser"},
    )

    asyncio.run(
        import_cookies(
            ImportCookiesRequest(
                cookies="SID=sid; SAPISID=sap", email="a@b.com", tier="Pro"
            ),
            account_service=service,
            runtime_state=state,
        )
    )

    assert session.calls[-1]["proxy"] is routes_accounts.UNSET_PROXY


# --------------------------------------------------------------------------- #
# P1-3: generation session main path is fail-closed (never the global proxy)
# --------------------------------------------------------------------------- #


def _browserless_runtime(
    resolver: object,
    *,
    signer: object | None = None,
) -> BrowserlessEngineRuntime:
    return BrowserlessEngineRuntime(
        {"mode": "browserless", "browserless": {"signer": {}, "transport": {}}},
        signer_client=cast(Any, signer if signer is not None else MagicMock()),
        exit_resolver=cast(Any, resolver),
    )


def test_external_no_exit_rejects_session_without_global_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    signer = MagicMock()
    runtime = _browserless_runtime(FakeExitResolver(fail=True), signer=signer)

    with pytest.raises(RoutingError) as excinfo:
        asyncio.run(
            runtime.create_session(
                slot_id="s1",
                auth_file=None,
                auth_user="0",
                tier="Pro",
                account_id="acc1",
                realm_id="r1",
                surface="chat",
            )
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "exit_unavailable"
    # Zero upstream calls: the session was never built / never signed.
    assert signer.method_calls == []


def test_external_direct_fallback_rejected_for_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """class=generation must fail closed even when the emergency valve is on."""
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    signer = MagicMock()
    runtime = _browserless_runtime(FakeExitResolver(fail=True), signer=signer)

    with pytest.raises(RoutingError) as excinfo:
        asyncio.run(
            runtime.create_session(
                slot_id="s1",
                auth_file=None,
                auth_user="0",
                tier="Pro",
                account_id="acc1",
                realm_id="r1",
                surface="chat",
            )
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "exit_unavailable"
    # Zero upstream calls and no proxy-free session was ever built.
    assert signer.method_calls == []


def test_direct_fallback_still_allowed_for_maintenance_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The emergency valve stays available for maintenance/probe callers."""
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")

    decision = asyncio.run(
        resolve_credential_exit(
            "acc1",
            category="maintenance",
            resolver=FakeExitResolver(fail=True),
        )
    )

    assert decision.action == "direct_fallback"
    assert decision.proxy is None


def test_off_mode_session_uses_global_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    runtime = _browserless_runtime(None)

    build = asyncio.run(
        runtime.create_session(
            slot_id="s1",
            auth_file=None,
            auth_user="0",
            tier="Pro",
            account_id="acc1",
            realm_id="r1",
            surface="chat",
        )
    )
    session = cast(BrowserlessSession, build.session)
    assert session._proxy == SENTINEL_GLOBAL
    assert session._external_exits is False


def test_signer_daemon_omits_global_proxy_in_external_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aistudio_api.infrastructure.waa.engine_runtime import BrowserlessEngineRuntime

    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    runtime = BrowserlessEngineRuntime(
        {
            "mode": "browserless",
            "browserless": {
                "signer": {"auto_start": True, "mode": "local_subprocess"},
                "transport": {},
            },
        },
        signer_client=MagicMock(),
        exit_resolver=cast(Any, FakeExitResolver({"acc1": EXIT_ONE})),
    )
    daemon = runtime._ensure_daemon()
    assert daemon is not None
    assert "-proxy" not in daemon._command()


# --------------------------------------------------------------------------- #
# P2-4: external + browser engine fails fast at build time
# --------------------------------------------------------------------------- #


def test_external_mode_browser_engine_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aistudio_api.infrastructure.waa.engine_runtime.get_exit_resolver",
        lambda: FakeExitResolver({"acc1": EXIT_ONE}),
    )

    with pytest.raises(RuntimeError, match="external"):
        build_engine_runtime({"mode": "browser"}, client=MagicMock())


# --------------------------------------------------------------------------- #
# P2-1: no synthetic ``cookie_<hex>`` exit binding
# --------------------------------------------------------------------------- #


def test_import_cookies_external_binds_real_account_id(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AccountStore(tmp_path / "accounts_real_bind")
    service = AccountService(store)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        routes_accounts, "probe_account_email", AsyncMock(return_value="a@b.com")
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value="Pro")
    )
    state = RuntimeState(account_service=service, client=None, rotator=MagicMock())

    response = asyncio.run(
        import_cookies(
            ImportCookiesRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert resolver.calls, "the post-save probe must resolve an exit"
    assert all(account == response.account_id for account, _ in resolver.calls)
    assert all(not account.startswith("cookie_") for account, _ in resolver.calls)
    stored = store.get_account(response.account_id)
    assert stored is not None and stored.email == "a@b.com" and stored.tier == "Pro"
    # N-3: the post-save probe records freshness instead of leaving it empty.
    assert stored.tier_checked_at is not None


def test_import_cookies_external_uses_explicit_real_id_before_save(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AccountStore(tmp_path / "accounts_explicit_id")
    service = AccountService(store)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        routes_accounts, "probe_account_email", AsyncMock(return_value="a@b.com")
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value="Pro")
    )
    state = RuntimeState(account_service=service, client=None, rotator=MagicMock())

    response = asyncio.run(
        import_cookies(
            ImportCookiesRequest(cookies="SID=sid; SAPISID=sap", account_id="acc_real"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert response.account_id == "acc_real"
    assert resolver.calls[0] == ("acc_real", "chat")
    assert all(not account.startswith("cookie_") for account, _ in resolver.calls)


def test_import_bundle_external_binds_real_account_id(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.api.routes_accounts import (
        BundleAccountItem,
        ImportBundleRequest,
    )

    store = AccountStore(tmp_path / "accounts_bundle_bind")
    service = AccountService(store)
    resolver = _AlwaysExitResolver(EXIT_ONE)
    reset_exit_resolver(cast(Any, resolver))
    monkeypatch.setattr(
        routes_accounts, "probe_account_email", AsyncMock(return_value="a@b.com")
    )
    monkeypatch.setattr(
        routes_accounts, "probe_account_benefit_tier", AsyncMock(return_value="Pro")
    )

    response = asyncio.run(
        import_bundle(
            ImportBundleRequest(
                accounts=[
                    BundleAccountItem(cookies="SID=sid; SAPISID=sap", auth_user="0")
                ]
            ),
            account_service=service,
        )
    )

    assert response.imported_count == 1
    account_id = response.accounts[0].id
    assert resolver.calls, "the post-save probe must resolve an exit"
    assert all(account == account_id for account, _ in resolver.calls)
    assert all(not account.startswith("cookie_") for account, _ in resolver.calls)


# --------------------------------------------------------------------------- #
# P2-2: Free-fresh email skip is not a completed refresh
# --------------------------------------------------------------------------- #


def test_free_fresh_email_skip_is_not_ok_and_retries_300s(tmp_path: Any) -> None:
    store = AccountStore(tmp_path / "accounts_free_fresh")
    account = store.save_account(
        name="FreeFresh",
        email=None,
        storage_state=_STORAGE_STATE,
        account_id="acc_free",
    )
    fresh_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    store.update_account_refresh(account.id, tier="Free", tier_checked_at=fresh_at)
    account = store.get_account(account.id)
    assert account is not None

    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        exit_resolver=FakeExitResolver(fail=True),
    )

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    class _Rotator:
        def is_auth_locked(self, account_id: str) -> bool:
            del account_id
            return False

    clock = _Clock()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        refresher,
        rotator=_Rotator(),
        clock=clock,
        tick_seconds=3600.0,
    )
    service._next_due = {"metadata_refresh": clock.now}
    settings_block = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        active_activity=False,
    )

    asyncio.run(service._run_due(settings_block))

    # The email probe was skipped (no exit), so keepalive must retry in 300s.
    assert service._next_due["metadata_refresh"] == clock.now + 300.0
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier_checked_at == fresh_at


# --------------------------------------------------------------------------- #
# N-1: the synthetic "unknown" account id must not reach the exit manager
# --------------------------------------------------------------------------- #


def test_verify_account_identity_does_not_forward_unknown_id(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.account import cookie_parser
    from aistudio_api.infrastructure.gateway.session_auth import (
        verify_account_identity,
    )

    acc_dir = tmp_path / "acc_unknown"
    acc_dir.mkdir()
    auth_file = acc_dir / "auth.json"
    auth_file.write_text(
        json.dumps({"cookies": [{"name": "SID", "value": "sid"}], "origins": []}),
        encoding="utf-8",
    )
    (acc_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": "unknown",
                "email": "a@b.com",
                "tier": "Free",
                "auth_user": "0",
            }
        ),
        encoding="utf-8",
    )
    page = MagicMock()
    page.url = "https://aistudio.google.com/"
    page.evaluate = AsyncMock(return_value=True)
    page.get_cookies = AsyncMock(return_value=[])

    calls: list[str | None] = []

    async def fake_probe(
        cookie_dict: dict[str, str],
        auth_user: str = "0",
        *,
        account_id: str | None = None,
    ) -> str | None:
        del cookie_dict, auth_user
        calls.append(account_id)
        return "Pro"

    monkeypatch.setattr(cookie_parser, "probe_account_benefit_tier", fake_probe)

    ok = asyncio.run(verify_account_identity(page, str(auth_file)))

    assert ok is True
    assert calls == [None]


# --------------------------------------------------------------------------- #
# P1-R1: exit_unavailable reaches the protocol layer and never poisons a slot
# --------------------------------------------------------------------------- #


class _ToggleExitResolver:
    """Manager double that can go down and recover mid-test."""

    def __init__(self, exit_url: str) -> None:
        self.exit_url = exit_url
        self.fail = True
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        self.calls.append((account_id, kind))
        if self.fail:
            return None
        return self.exit_url


def test_external_missing_exit_surfaces_503_and_slot_stays_usable(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: engine -> pool -> manager -> routing -> 503 exit_unavailable."""
    from aistudio_api.application.routing_pipeline import RoutingPipeline
    from aistudio_api.infrastructure.waa.engine_runtime import (
        BrowserlessEngineRuntime,
    )

    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    store = AccountStore(tmp_path / "accounts_e2e")
    account = store.save_account(
        name="Paid",
        email="paid@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_pro",
        tier="Pro",
    )
    store.set_active_account(account.id)
    resolver = _ToggleExitResolver(EXIT_ONE)
    signer = MagicMock()
    runtime = BrowserlessEngineRuntime(
        {
            "mode": "browserless",
            "browserless": {
                "signer": {"mode": "remote_http", "endpoint": "http://127.0.0.1:1"},
                "transport": {"mode": "signer_rpc"},
            },
        },
        signer_client=cast(Any, signer),
        exit_resolver=cast(Any, resolver),
    )

    async def _no_cdp() -> Any:
        raise AssertionError("browserless engine must never request CDP")

    pool = InstancePool(
        cast(Any, _no_cdp),
        specs=[InstanceSpec(count=1, surface="chat", min_tier="Pro")],
        max_contexts=2,
        engine_runtime=runtime,
        engine="browserless",
        load_attempts=3,
    )
    manager = InstanceManager(pool, store)
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 0,
        },
    )

    async def _run() -> None:
        await manager.startup()
        listed = (await manager.list_instances()).instances
        assert [item.state for item in listed] == [InstanceState.UNSTARTED]
        assert listed[0].account_id == account.id

        with pytest.raises(RoutingError) as excinfo:
            context = await pipeline.begin(model_path="gemini-4-argon")
            await pipeline.acquire(context)

        assert excinfo.value.status_code == 503
        assert excinfo.value.code == "exit_unavailable"

        assert signer.method_calls == []  # zero upstream calls

        # The protocol adapter renders the same 503/code to the client.
        from aistudio_api.api.error_mapping import error_payload

        status, body = error_payload(excinfo.value, "openai")
        assert status == 503
        openai_error = body["error"]
        assert isinstance(openai_error, dict)
        assert openai_error["code"] == "exit_unavailable"
        gemini_status, gemini_body = error_payload(excinfo.value, "gemini")
        assert gemini_status == 503
        gemini_error = gemini_body["error"]
        assert isinstance(gemini_error, dict)
        assert gemini_error["type"] == "exit_unavailable"

        listed = (await manager.list_instances()).instances
        assert listed[0].state is InstanceState.UNSTARTED  # not sticky INVALID
        assert listed[0].account_id == account.id

        # Manager recovers: the same slot must build a session and serve.
        resolver.fail = False
        context = await pipeline.begin(model_path="gemini-4-argon")

        attempt = await pipeline.acquire(context)
        assert attempt is not None
        assert attempt.handle.slot.session is not None
        await pipeline.release(attempt)
        listed = (await manager.list_instances()).instances
        assert listed[0].state is InstanceState.ACTIVE

    asyncio.run(_run())


def test_exit_unavailable_does_not_advance_to_next_candidate() -> None:
    """Once exit_unavailable is raised, acquire must not mask it by switching."""
    from aistudio_api.application.routing_pipeline import RoutingPipeline
    from aistudio_api.domain.instances import Instance

    class _Manager:
        configured_surfaces = frozenset({"chat"})

        def __init__(self) -> None:
            self.begin_calls: list[str] = []

        def eligible_instances(
            self, *, paid: bool, surface: str | None = None
        ) -> tuple[Instance, ...]:
            del paid, surface
            return (
                Instance(
                    id="inst-1",
                    number=1,
                    account_id="acc-1",
                    state=InstanceState.UNSTARTED,
                    surface="chat",
                    min_tier="Pro",
                    tier="Pro",
                ),
                Instance(
                    id="inst-2",
                    number=2,
                    account_id="acc-2",
                    state=InstanceState.UNSTARTED,
                    surface="chat",
                    min_tier="Pro",
                    tier="Pro",
                ),
            )

        def lane_instances(self, *, paid: bool) -> tuple[Instance, ...]:
            return self.eligible_instances(paid=paid)

        async def wake_eligible(
            self, *, paid: bool | None = None, surface: str | None = None
        ) -> list[Instance]:
            del paid, surface
            return list(self.eligible_instances(paid=True))

        async def wake(self, instance_id: str) -> object:
            del instance_id
            raise RoutingError("no exit", code="exit_unavailable", surface="chat")

        async def begin_request(self, instance_id: str) -> object:
            self.begin_calls.append(instance_id)
            raise AssertionError("must never acquire after exit_unavailable")

    manager = _Manager()
    pipeline = RoutingPipeline(
        instance_manager=cast(Any, manager),
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    async def _run() -> None:
        context = await pipeline.begin(model_path="gemini-4-argon")
        with pytest.raises(RoutingError) as excinfo:
            await pipeline.acquire(context)
        assert excinfo.value.code == "exit_unavailable"

    asyncio.run(_run())
    assert manager.begin_calls == []


# --------------------------------------------------------------------------- #
# P2-R3: skip logs are throttled per (account, reason) in refresher + keepalive
# --------------------------------------------------------------------------- #


def test_refresher_skip_log_is_throttled_per_account_and_reason(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    store, accounts = _make_store(tmp_path)
    reset_skip_log_throttle()
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        exit_resolver=FakeExitResolver(fail=True),
    )

    with _capture_logger(caplog, "aistudio.account_refresher"):
        asyncio.run(refresher.refresh_account(accounts[0]))
        asyncio.run(refresher.refresh_account(accounts[0]))

    lines = [
        record
        for record in caplog.records
        if record.name == "aistudio.account_refresher"
        and "无可用出口" in record.getMessage()
        and str(accounts[0].id) in record.getMessage()
    ]
    assert len(lines) == 1


def test_keepalive_skip_log_is_throttled_across_rounds(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    store = AccountStore(tmp_path / "accounts_keepalive_throttle")
    account = store.save_account(
        name="Stale",
        email=None,
        storage_state=_STORAGE_STATE,
        account_id="acc_throttle",
    )
    stale_at = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    store.update_account_refresh(account.id, tier="Free", tier_checked_at=stale_at)

    class _SkipRefresher:
        async def refresh_accounts(
            self, accounts: list[Any], **kwargs: object
        ) -> list[RefreshResult]:
            del kwargs
            return [
                RefreshResult(
                    account_id=item.id,
                    ok=False,
                    skipped=True,
                    skip_reason="no_exit",
                    error="skipped_no_exit: no_exit",
                )
                for item in accounts
            ]

    class _Rotator:
        def is_auth_locked(self, account_id: str) -> bool:
            del account_id
            return False

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    reset_skip_log_throttle()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        cast(Any, _SkipRefresher()),
        rotator=_Rotator(),
        clock=_Clock(),
        tick_seconds=3600.0,
    )
    settings_block = KeepaliveSettings(
        auth_update=False, metadata_refresh=True, active_activity=False
    )

    with _capture_logger(caplog, "aistudio.keepalive"):
        asyncio.run(service.run_now("metadata_refresh", settings=settings_block))
        asyncio.run(service.run_now("metadata_refresh", settings=settings_block))

    lines = [
        record
        for record in caplog.records
        if "metadata refresh skipped" in record.getMessage()
    ]
    assert len(lines) == 1


# --------------------------------------------------------------------------- #
# N-2: detect-tier no-exit uses the same 503 as the generation path
# --------------------------------------------------------------------------- #


def test_detect_account_tier_no_exit_returns_503(tmp_path: Any) -> None:
    from fastapi import HTTPException

    from aistudio_api.api.routes_accounts import detect_account_tier

    store = AccountStore(tmp_path / "accounts_detect_tier")
    service = AccountService(store)
    account = store.save_account(
        name="Acc",
        email="acc@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_detect",
        tier="Pro",
    )
    reset_exit_resolver(cast(Any, FakeExitResolver(fail=True)))
    state = RuntimeState(account_service=service, client=None, instance_manager=None)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            detect_account_tier(
                account_id=account.id,
                account_service=service,
                runtime_state=state,
            )
        )

    assert excinfo.value.status_code == 503


# --------------------------------------------------------------------------- #
# Lease budget: per-request counting, 429 no_capacity and deferred semantics
# --------------------------------------------------------------------------- #


class _LeaseResolver:
    """Manager double with the ``/v1/lease`` surface only."""

    def __init__(
        self, *, no_capacity: bool = False, retry_after_s: float = 0.0
    ) -> None:
        self.no_capacity = no_capacity
        self.retry_after_s = retry_after_s
        self.calls: list[tuple[str, str, str, int, str]] = []

    async def lease(
        self,
        account_id: str,
        *,
        lane: str = "free",
        cls: str = "generation",
        calls: int = 1,
        kind: str = "chat",
        use_cache: bool = False,
    ) -> LeaseResult:
        del use_cache
        self.calls.append((account_id, lane, cls, calls, kind))
        if self.no_capacity:
            return LeaseResult(
                "no_capacity",
                retry_after_s=self.retry_after_s,
                reason="no_capacity",
            )
        return LeaseResult(
            "ok",
            exit_id="exit-1",
            socks_url=EXIT_ONE,
            generation=1,
            ttl_s=120,
            budget_left=20,
        )

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        del account_id, kind
        return None if self.no_capacity else EXIT_ONE


class _SequencedLeaseResolver(_LeaseResolver):
    """Lease double that returns queued outcomes, then a healthy default.

    Used to model one account where the tier probe succeeds but the follow-up
    email probe hits ``no_capacity`` (Q-10), or a failed creation lease followed
    by a successful generation lease (Q-11).
    """

    def __init__(self, results: list[LeaseResult]) -> None:
        super().__init__()
        self._results = list(results)

    async def lease(
        self,
        account_id: str,
        *,
        lane: str = "free",
        cls: str = "generation",
        calls: int = 1,
        kind: str = "chat",
        use_cache: bool = False,
    ) -> LeaseResult:
        del use_cache
        self.calls.append((account_id, lane, cls, calls, kind))
        if self._results:
            return self._results.pop(0)
        return LeaseResult(
            "ok",
            exit_id="exit-1",
            socks_url=EXIT_ONE,
            generation=1,
            ttl_s=120,
            budget_left=20,
        )


def test_resolve_credential_exit_returns_defer_on_no_capacity() -> None:
    resolver = _LeaseResolver(no_capacity=True, retry_after_s=17.0)
    decision = asyncio.run(
        resolve_credential_exit(
            "acc1",
            lane="free",
            category="maintenance",
            resolver=cast(Any, resolver),
        )
    )

    assert decision.action == "defer"
    assert decision.deferred is True
    assert decision.allowed is False
    assert decision.retry_after_s == pytest.approx(17.0)
    assert resolver.calls == [("acc1", "free", "maintenance", 1, "chat")]


def test_leased_session_counts_once_per_request_scope() -> None:
    from contextlib import asynccontextmanager

    from aistudio_api.infrastructure.waa.engine_runtime import (
        _LeasedBrowserlessSession,
    )

    class _Delegate:
        engine = "browserless"
        active_context = object()

        @asynccontextmanager
        async def request_scope(self):
            yield self.active_context

        async def send_hooked_request(self, **kwargs: object):
            del kwargs
            return 200, b"[]"

        async def send_streaming_request(self, **kwargs: object):
            del kwargs
            yield "data", b"[]"

        async def rotate_cookies_via_signer(self) -> dict[str, object]:
            return {"ok": True}

    resolver = _LeaseResolver()
    session = _LeasedBrowserlessSession(
        cast(Any, _Delegate()),
        resolver=cast(Any, resolver),
        account_id="acc_pro",
        lane="paid",
        kind="chat",
        bound_socks=EXIT_ONE,
    )

    async def _run() -> None:
        async with session.request_scope():
            await session.send_hooked_request(body="x")
            await session.send_hooked_request(body="x")
        await session.send_hooked_request(body="x")
        _ = [event async for event in session.send_streaming_request(body="x")]
        await session.rotate_cookies_via_signer()

    asyncio.run(_run())

    # One generation lease for the whole request scope (capture + RPC), then
    # each unscoped maintenance send/rotate leases its own maintenance call.
    assert resolver.calls == [
        ("acc_pro", "paid", "generation", 1, "chat"),
        ("acc_pro", "paid", "maintenance", 1, "chat"),
        ("acc_pro", "paid", "maintenance", 1, "chat"),
        ("acc_pro", "paid", "maintenance", 1, "chat"),
    ]


def test_leased_session_raises_429_budget_wait() -> None:
    from contextlib import asynccontextmanager

    from aistudio_api.infrastructure.waa.engine_runtime import (
        _LeasedBrowserlessSession,
    )

    class _Delegate:
        engine = "browserless"
        active_context = object()

        @asynccontextmanager
        async def request_scope(self):
            yield self.active_context

        async def send_hooked_request(self, **kwargs: object):
            del kwargs
            return 200, b"[]"

    resolver = _LeaseResolver(no_capacity=True, retry_after_s=12.0)
    session = _LeasedBrowserlessSession(
        cast(Any, _Delegate()),
        resolver=cast(Any, resolver),
        account_id="acc1",
        lane="free",
        kind="chat",
        bound_socks=EXIT_ONE,
    )

    with pytest.raises(ExitBudgetWaitError) as excinfo:
        asyncio.run(session.send_hooked_request(body="x"))

    assert excinfo.value.status_code == 429
    assert excinfo.value.code == "no_capacity"
    assert excinfo.value.retry_after == 12

    rotate = asyncio.run(session.rotate_cookies_via_signer())
    assert rotate["ok"] is False
    assert rotate["deferred"] is True
    assert rotate["retry_after_s"] == pytest.approx(12.0)
    assert rotate["skipped"] == "no_exit"


def _leased_probe_session(
    resolver: _LeaseResolver, *, account_id: str, body: bytes = b"[1]"
) -> Any:
    from aistudio_api.infrastructure.waa.engine_runtime import (
        _LeasedBrowserlessSession,
    )

    class _Delegate:
        engine = "browserless"

        def is_alive(self) -> bool:
            return True

        async def send_hooked_request(self, **kwargs: object):
            del kwargs
            return 200, body

    return _LeasedBrowserlessSession(
        cast(Any, _Delegate()),
        resolver=cast(Any, resolver),
        account_id=account_id,
        lane="free",
        kind="chat",
        bound_socks=EXIT_ONE,
    )


def test_slotted_session_tier_probe_leases_maintenance_once(tmp_path: Any) -> None:
    """P2-1: a pooled-session tier probe must not pre-lease + session-lease twice."""
    store = AccountStore(tmp_path / "accounts_slotted_lease")
    account = store.save_account(
        name="Slotted",
        email="slotted@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_slotted",
        tier="Free",
    )
    resolver = _LeaseResolver()
    session = _leased_probe_session(resolver, account_id=account.id)
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        slot_provider=lambda: (
            SimpleNamespace(account_id=account.id, session=session),
        ),
    )

    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.tier == "Pro"
    # Exactly one maintenance lease for the whole probe: the pre-session
    # ``_credential_exit`` lease is gone and only the session counts.
    assert resolver.calls == [(account.id, "free", "maintenance", 1, "chat")]


def test_slotted_session_tier_probe_defer_comes_from_session(tmp_path: Any) -> None:
    store = AccountStore(tmp_path / "accounts_slotted_defer")
    account = store.save_account(
        name="Slotted defer",
        email="slotted-defer@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_slotted_defer",
        tier="Free",
    )
    resolver = _LeaseResolver(no_capacity=True, retry_after_s=33.0)
    session = _leased_probe_session(resolver, account_id=account.id)
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        slot_provider=lambda: (
            SimpleNamespace(account_id=account.id, session=session),
        ),
    )

    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.deferred is True
    assert result.retry_after_s == pytest.approx(33.0)
    assert resolver.calls == [(account.id, "free", "maintenance", 1, "chat")]


def test_slotted_session_refresh_tier_leases_maintenance_once(
    tmp_path: Any,
) -> None:
    """P2-1: ``refresh_tier`` also counts a pooled probe exactly once."""
    store = AccountStore(tmp_path / "accounts_slotted_tier")
    account = store.save_account(
        name="Slotted tier",
        email="slotted-tier@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_slotted_tier",
        tier="Free",
    )
    resolver = _LeaseResolver()
    session = _leased_probe_session(resolver, account_id=account.id)
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        slot_provider=lambda: (
            SimpleNamespace(account_id=account.id, session=session),
        ),
    )

    result = asyncio.run(refresher.refresh_tier(account))

    assert result["error"] is None
    assert result["tier"] == "Pro"
    assert resolver.calls == [(account.id, "free", "maintenance", 1, "chat")]


def test_cold_start_session_reuses_creation_lease_for_first_request() -> None:
    """P2-2: create_session + first request leases generation exactly once."""
    resolver = _LeaseResolver()
    runtime = BrowserlessEngineRuntime(
        {
            "mode": "browserless",
            "browserless": {
                "signer": {"mode": "remote_http", "endpoint": "http://127.0.0.1:1"},
                "transport": {"mode": "signer_rpc"},
            },
        },
        signer_client=cast(Any, MagicMock()),
        exit_resolver=cast(Any, resolver),
    )

    async def _run() -> None:
        build = await runtime.create_session(
            slot_id="slot-1",
            auth_file=None,
            auth_user="0",
            tier="Free",
            account_id="acc_cold",
            realm_id="realm-cold",
            surface="chat",
        )
        generation = [call for call in resolver.calls if call[2] == "generation"]
        assert generation == [("acc_cold", "free", "generation", 1, "chat")]

        async with build.session.request_scope():
            pass
        # The first request consumed the creation lease; nothing new was leased.
        generation = [call for call in resolver.calls if call[2] == "generation"]
        assert len(generation) == 1

        async with build.session.request_scope():
            pass
        generation = [call for call in resolver.calls if call[2] == "generation"]
        assert len(generation) == 2

    asyncio.run(_run())


def test_direct_fallback_cold_start_is_rejected_for_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even the create-time lease may not fall back to direct for generation."""
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    resolver = _SequencedLeaseResolver(
        [LeaseResult("error", reason="manager_unreachable")]
    )
    runtime = _browserless_runtime(resolver)

    async def _run() -> None:
        with pytest.raises(RoutingError) as excinfo:
            await runtime.create_session(
                slot_id="slot-df",
                auth_file=None,
                auth_user="0",
                tier="Free",
                account_id="acc_direct",
                realm_id="realm-df",
                surface="chat",
            )
        assert excinfo.value.status_code == 503
        generation = [call for call in resolver.calls if call[2] == "generation"]
        # Exactly the refused create-time probe: no session credit was granted.
        assert len(generation) == 1

    asyncio.run(_run())


def test_skip_cold_start_does_not_grant_generation_credit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q-11: a fail-closed skip creates no session and consumes no credit."""
    monkeypatch.delenv("AISTUDIO_EXIT_DIRECT_FALLBACK", raising=False)
    resolver = _SequencedLeaseResolver(
        [LeaseResult("error", reason="manager_unreachable")]
    )
    runtime = _browserless_runtime(resolver)

    async def _run() -> None:
        with pytest.raises(RoutingError):
            await runtime.create_session(
                slot_id="slot-skip",
                auth_file=None,
                auth_user="0",
                tier="Free",
                account_id="acc_skip",
                realm_id="realm-skip",
                surface="chat",
            )
        generation = [call for call in resolver.calls if call[2] == "generation"]
        # Exactly the refused create-time probe: nothing was granted to a
        # request scope because no session exists.
        assert len(generation) == 1

    asyncio.run(_run())


def test_request_scope_is_per_task_not_a_shared_session_flag() -> None:
    """P2-3: A leaving its scope must not drop B's still-open scope."""
    from contextlib import asynccontextmanager

    from aistudio_api.infrastructure.waa.engine_runtime import (
        _LeasedBrowserlessSession,
    )

    class _Delegate:
        engine = "browserless"
        active_context = object()

        @asynccontextmanager
        async def request_scope(self):
            yield self.active_context

        async def send_hooked_request(self, **kwargs: object):
            del kwargs
            return 200, b"[]"

    resolver = _LeaseResolver()
    session = _LeasedBrowserlessSession(
        cast(Any, _Delegate()),
        resolver=cast(Any, resolver),
        account_id="acc_concurrent",
        lane="free",
        kind="chat",
        bound_socks=EXIT_ONE,
    )

    async def _run() -> None:
        a_entered = asyncio.Event()
        b_entered = asyncio.Event()
        a_may_exit = asyncio.Event()
        b_may_send = asyncio.Event()

        async def _request_a() -> None:
            async with session.request_scope():
                a_entered.set()
                await a_may_exit.wait()

        async def _request_b() -> None:
            await a_entered.wait()
            async with session.request_scope():
                b_entered.set()
                await b_may_send.wait()
                # B is still inside its own scope: this must not lease
                # maintenance just because A already exited.
                await session.send_hooked_request(body="b")

        task_a = asyncio.create_task(_request_a())
        task_b = asyncio.create_task(_request_b())
        await a_entered.wait()
        await b_entered.wait()
        a_may_exit.set()
        await task_a
        b_may_send.set()
        await task_b

    asyncio.run(_run())

    calls = resolver.calls
    assert [call[2] for call in calls] == ["generation", "generation"]
    assert all(call[2] != "maintenance" for call in calls)


def test_generation_no_capacity_surfaces_429_and_keeps_slot_retryable(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: engine lease 429 -> pool UNSTARTED -> routing 429 no_capacity."""
    from aistudio_api.application.routing_pipeline import RoutingPipeline

    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    store = AccountStore(tmp_path / "accounts_e2e_429")
    account = store.save_account(
        name="Free",
        email="free@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_free",
        tier="Free",
    )
    store.set_active_account(account.id)
    resolver = _LeaseResolver(no_capacity=True, retry_after_s=23.0)
    runtime = BrowserlessEngineRuntime(
        {
            "mode": "browserless",
            "browserless": {
                "signer": {"mode": "remote_http", "endpoint": "http://127.0.0.1:1"},
                "transport": {"mode": "signer_rpc"},
            },
        },
        signer_client=cast(Any, MagicMock()),
        exit_resolver=cast(Any, resolver),
    )

    async def _no_cdp() -> Any:
        raise AssertionError("browserless engine must never request CDP")

    pool = InstancePool(
        cast(Any, _no_cdp),
        specs=[InstanceSpec(count=1, surface="chat", only_tier="Free")],
        max_contexts=2,
        engine_runtime=runtime,
        engine="browserless",
        load_attempts=1,
    )
    manager = InstanceManager(pool, store)
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 0,
        },
    )

    async def _run() -> None:
        await manager.startup()
        listed = (await manager.list_instances()).instances
        assert [item.state for item in listed] == [InstanceState.UNSTARTED]

        with pytest.raises(ExitBudgetWaitError) as excinfo:
            context = await pipeline.begin(model_path="gemini-3.7-flash")
            await pipeline.acquire(context)

        assert excinfo.value.status_code == 429
        assert excinfo.value.code == "no_capacity"
        assert excinfo.value.retry_after == 23
        listed_after = (await manager.list_instances()).instances
        assert [item.state for item in listed_after] == [InstanceState.UNSTARTED]

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# Maintenance defer: no_capacity delays the account, never skips/fails it
# --------------------------------------------------------------------------- #


def test_keepalive_metadata_deferred_does_not_advance_due(tmp_path: Any) -> None:
    store = AccountStore(tmp_path / "accounts_metadata_defer")
    account = store.save_account(
        name="Deferred",
        email="deferred@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_deferred",
    )
    stale_at = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    store.update_account_refresh(
        account.id, tier="Free", tier_checked_at=stale_at, expired=False
    )
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        exit_resolver=cast(Any, _LeaseResolver(no_capacity=True, retry_after_s=45.0)),
    )

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        refresher,
        clock=clock,
        tick_seconds=3600.0,
    )
    service._next_due = {"metadata_refresh": clock.now}
    settings_block = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        active_activity=False,
    )

    asyncio.run(service._run_due(settings_block))

    # 429 no_capacity is a defer, not a 24h completion or an account failure.
    assert service._next_due["metadata_refresh"] == clock.now + 300.0
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier_checked_at == stale_at
    assert stored.last_refresh_error is None
    last = service._last_results["metadata_refresh"]
    assert last["deferred"] == 1


def test_keepalive_deferred_preserves_previous_refresh_error(tmp_path: Any) -> None:
    """Q-9: a deferred 429 advances only the attempt time, not last_refresh_error."""
    store = AccountStore(tmp_path / "accounts_metadata_defer_error")
    account = store.save_account(
        name="Deferred with error",
        email="deferred-error@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_deferred_error",
    )
    stale_at = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    store.update_account_refresh(
        account.id, tier="Free", tier_checked_at=stale_at, expired=False
    )
    store.mark_refresh_attempt(account.id, error="earlier upstream 403")

    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        exit_resolver=cast(Any, _LeaseResolver(no_capacity=True, retry_after_s=45.0)),
    )

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        refresher,
        clock=clock,
        tick_seconds=3600.0,
    )
    service._next_due = {"metadata_refresh": clock.now}
    settings_block = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        active_activity=False,
    )

    asyncio.run(service._run_due(settings_block))

    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier_checked_at == stale_at
    # The deferred probe must not clear a real error from an earlier attempt.
    assert stored.last_refresh_error == "earlier upstream 403"
    assert stored.last_refresh_attempt_at is not None


def test_manual_refresh_reports_per_account_deferred() -> None:
    class _MixedRefresher:
        async def refresh_accounts(
            self, accounts: list[Any], **kwargs: object
        ) -> list[RefreshResult]:
            del kwargs
            return [
                RefreshResult(
                    account_id="acc_deferred",
                    ok=False,
                    deferred=True,
                    retry_after_s=42.0,
                    skip_reason="no_capacity",
                ),
                RefreshResult(account_id="acc_ok", ok=True, tier="Pro"),
            ]

    account_service = MagicMock()
    account_service.list_accounts.return_value = [
        SimpleNamespace(id="acc_deferred", name="D", email="d@x.com"),
        SimpleNamespace(id="acc_ok", name="O", email="o@x.com"),
    ]

    response = asyncio.run(
        refresh_accounts(
            RefreshAccountsRequest(all=True),
            account_service=account_service,
            refresher=cast(Any, _MixedRefresher()),
        )
    )

    assert len(response.results) == 2
    deferred, ok = response.results
    assert deferred.id == "acc_deferred"
    assert deferred.deferred is True
    assert deferred.retry_after_s == pytest.approx(42.0)
    assert deferred.ok is False
    assert ok.id == "acc_ok"
    assert ok.ok is True
    assert ok.deferred is False


def test_import_probe_deferred_still_imports(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.application.account_service import AccountService

    store = AccountStore(tmp_path / "accounts_import_defer")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=MagicMock())
    reset_exit_resolver(cast(Any, _LeaseResolver(no_capacity=True, retry_after_s=33.0)))

    email_probe = AsyncMock(return_value="probed@example.com")
    tier_probe = AsyncMock(return_value="Pro")
    monkeypatch.setattr(routes_accounts, "probe_account_email", email_probe)
    monkeypatch.setattr(routes_accounts, "probe_account_benefit_tier", tier_probe)

    response = asyncio.run(
        import_cookies(
            ImportCookiesRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert response.account_id
    email_probe.assert_not_called()
    tier_probe.assert_not_called()
    stored = store.get_account(response.account_id)
    assert stored is not None
    assert stored.tier == "Free"
    assert stored.last_refresh_error is not None
    assert stored.last_refresh_error.startswith("deferred_no_capacity")


def test_probe_and_import_deferred_keeps_account(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.application.account_service import AccountService

    store = AccountStore(tmp_path / "accounts_probe_defer")
    service = AccountService(store)
    state = RuntimeState(account_service=service, client=None, rotator=None)
    reset_exit_resolver(cast(Any, _LeaseResolver(no_capacity=True, retry_after_s=21.0)))
    probe = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "aistudio_api.infrastructure.account.cookie_parser.probe_google_accounts_infinite",
        probe,
    )

    response = asyncio.run(
        probe_and_import(
            ProbeAndImportRequest(cookies="SID=sid; SAPISID=sap"),
            account_service=service,
            runtime_state=state,
        )
    )

    assert response.imported_count == 1
    account_id = response.accounts[0].id
    assert response.skipped == [account_id]
    assert response.skip_reason is not None
    assert response.skip_reason.startswith("deferred_no_capacity")
    probe.assert_not_called()
    stored = store.get_account(account_id)
    assert stored is not None
    assert stored.last_refresh_error is not None
    assert stored.last_refresh_error.startswith("deferred_no_capacity")


def test_auth_update_deferred_is_not_an_account_failure(tmp_path: Any) -> None:
    from aistudio_api.application.account_service import AccountService

    store = AccountStore(tmp_path / "accounts_auth_defer")
    account = store.save_account(
        name="AuthDeferred",
        email="authdefer@example.com",
        storage_state=_STORAGE_STATE,
        account_id="acc_auth_defer",
    )
    service = AccountService(store)

    async def _deferred_rotate(account_id: str, session: object = None):
        del session
        assert account_id == account.id
        return {
            "ok": False,
            "status": 0,
            "updated": [],
            "skipped": "no_exit",
            "deferred": True,
            "retry_after_s": 50.0,
            "reason": "no_capacity",
        }

    service.rotate_account_cookies = _deferred_rotate  # type: ignore[method-assign]

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    keepalive = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        cast(Any, SimpleNamespace()),
        account_service=service,
        clock=clock,
        tick_seconds=3600.0,
    )
    keepalive._next_due = {"auth_update": clock.now}
    settings_block = KeepaliveSettings(
        auth_update=True,
        metadata_refresh=False,
        active_activity=False,
    )

    asyncio.run(keepalive._run_due(settings_block))

    assert keepalive._next_due["auth_update"] == clock.now + 300.0
    assert keepalive._account_auth_failures == {}
    assert account.id not in keepalive._auth_saved_at
    last = keepalive._last_results["auth_update"]
    assert last["reason"] == "deferred"
    assert last["deferred"] is True


def test_keepalive_defers_when_email_subprobe_hits_no_capacity(
    tmp_path: Any,
) -> None:
    """Q-10: a successful tier probe with a deferred email probe keeps 300s retry.

    The tier result itself is ``ok=True``; the email sub-probe is reported as
    ``deferred_no_capacity``.  Keepalive must treat the account as deferred and
    not push the next metadata pass out to the full 24h interval.
    """
    store = AccountStore(tmp_path / "accounts_metadata_email_defer")
    account = store.save_account(
        name="Email deferred",
        email=None,
        storage_state=_STORAGE_STATE,
        account_id="acc_email_defer",
        tier="Free",
    )
    resolver = _SequencedLeaseResolver(
        [
            LeaseResult(
                "ok",
                exit_id="exit-1",
                socks_url=EXIT_ONE,
                generation=1,
                ttl_s=120,
                budget_left=20,
            ),
            LeaseResult("no_capacity", retry_after_s=45.0, reason="no_capacity"),
        ]
    )
    session = _leased_probe_session(resolver, account_id=account.id)
    refresher = AccountInfoRefresher(
        store,
        transport_factory=lambda _proxy: RecordingTransport(),
        exit_resolver=cast(Any, resolver),
    )

    class _Clock:
        now = datetime.now(UTC).timestamp()

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    service = AccountKeepaliveService(
        store,
        cast(Any, SimpleNamespace(session=None)),
        refresher,
        instance_manager=SimpleNamespace(
            pool_slots=lambda: (
                SimpleNamespace(account_id=account.id, session=session),
            )
        ),
        clock=clock,
        tick_seconds=3600.0,
    )
    service._next_due = {"metadata_refresh": clock.now}
    settings_block = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        active_activity=False,
    )

    asyncio.run(service._run_due(settings_block))

    last = service._last_results["metadata_refresh"]
    assert last["deferred"] == 1
    assert service._next_due["metadata_refresh"] == pytest.approx(clock.now + 300.0)
    stored = store.get_account(account.id)
    assert stored is not None
    # Tier data was authoritative and saved; the missing email still keeps the
    # account due for the 300s retry.
    assert stored.tier == "Pro"
    assert stored.tier_checked_at is not None
    assert stored.email is None


# --------------------------------------------------------------------------- #
# Maintenance region 403 -> canary arbitration (metadata / cookie rotation)
# --------------------------------------------------------------------------- #

REGION_403_BODY = '{"error":{"message":"Region not supported."}}'


class _RegionTransport:
    """Bare httpx double: the tier probe always returns a region 403."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        del method, url, headers, content
        return TransportResponse(status=403, body=REGION_403_BODY)


class _FixedCanary:
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome

    async def probe(self, *, socks_url: str, exit_id: str) -> CanaryVerdict:
        del socks_url, exit_id
        return CanaryVerdict(cast(Any, self.outcome))


class _ArbitrationResolver(_LeaseResolver):
    """Manager double exposing the arbitration surface plus lease."""

    def __init__(self) -> None:
        super().__init__()
        self.assignment = ExitAssignment(exit_id="exit-1", socks_url=EXIT_ONE, ttl_s=60)
        self.events: list[tuple[str, str, str, str]] = []
        self.invalidated: list[str] = []

    def assignment_for(
        self, account_id: str, *, kind: str = "chat", include_expired: bool = False
    ) -> ExitAssignment | None:
        del account_id, kind, include_expired
        return self.assignment

    async def report_event(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> bool:
        self.events.append((account_id, exit_id, event_kind, canary))
        return True

    def report_event_nowait(
        self,
        *,
        account_id: str,
        exit_id: str,
        event_kind: str,
        canary: str,
    ) -> None:
        self.events.append((account_id, exit_id, event_kind, canary))

    def invalidate_exit(self, exit_id: str) -> None:
        self.invalidated.append(exit_id)


class _RecordingArbiter:
    def __init__(self, outcome: str = "inconclusive") -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def adjudicate(
        self,
        *,
        account_id: str,
        kind: str,
        probe_exit_id: str | None = None,
    ) -> str:
        del kind, probe_exit_id
        self.calls.append(account_id)
        return self.outcome


def _region_refresher(
    store: AccountStore,
    resolver: _ArbitrationResolver,
    canary: _FixedCanary,
    *,
    session: object | None = None,
) -> tuple[AccountInfoRefresher, _ArbitrationResolver]:
    slot_provider = (
        (lambda: (SimpleNamespace(account_id="acc1", session=session),))
        if session is not None
        else None
    )
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, resolver),
        slot_provider=slot_provider,
        pacing_seconds=0.0,
    )
    refresher.set_region_arbiter(
        ExitArbitration(
            resolver=cast(Any, resolver), canary_probe=canary, probe_cooldown_s=0.0
        )
    )
    return refresher, resolver


@pytest.mark.parametrize(
    ("outcome", "event", "invalidated", "verify"),
    [
        ("exit_bad", ("acc1", "exit-1", "region_403", "exit_bad"), ["exit-1"], False),
        (
            "region_suspect",
            ("acc1", "exit-1", "other_403", "account_bad"),
            [],
            False,
        ),
        (
            "inconclusive",
            ("acc1", "exit-1", "region_403", "inconclusive"),
            [],
            False,
        ),
    ],
)
def test_metadata_region_403_arbitration_outcomes(
    tmp_path: Any,
    outcome: str,
    event: tuple[str, str, str, str],
    invalidated: list[str],
    verify: bool,
) -> None:
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    canary = _FixedCanary(outcome)
    # A trusted probe transport (pooled signer session) is what makes the canary
    # verdict attributable to the account; a bare httpx probe is covered by
    # ``test_bare_tier_probe_*`` and never reaches the canary.
    refresher, resolver = _region_refresher(
        store, _ArbitrationResolver(), canary, session=_BoundSignerSession()
    )

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.retry_after_s == pytest.approx(300.0)
    assert result.region_outcome == outcome
    assert resolver.events == [event]
    assert resolver.invalidated == invalidated
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is verify
    assert stored.expired is False

    if verify:
        # Registry is authoritative and meta.json must agree.
        accounts_dir = tmp_path / "accounts"
        registry = json.loads((accounts_dir / "registry.json").read_text("utf-8"))
        meta = json.loads((accounts_dir / "acc1" / "meta.json").read_text("utf-8"))
        assert registry["accounts"]["acc1"]["verify_required"] is True
        assert meta["verify_required"] is True
        reloaded = AccountStore(accounts_dir).get_account("acc1")
        assert reloaded is not None and reloaded.verify_required is True


def test_bare_tier_probe_account_bad_stays_inconclusive(tmp_path: Any) -> None:
    """A bare region 403 on the matching exit still must not write verify.

    The bare httpx probe has no signer shape, so a canary ``account_bad`` result
    proves nothing about the account.  The refresh defers inconclusively and the
    registry keeps ``verify_required`` False.
    """
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    canary = _FixedCanary("account_bad")
    refresher, resolver = _region_refresher(store, _ArbitrationResolver(), canary)

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.region_outcome == "inconclusive"
    assert resolver.events == []
    assert resolver.invalidated == []
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is False
    assert stored.expired is False


def test_bare_tier_probe_exit_bad_does_not_burn_exit(tmp_path: Any) -> None:
    """A bare region 403 must not run the canary, so no exit is invalidated."""
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    canary = _FixedCanary("exit_bad")
    refresher, resolver = _region_refresher(store, _ArbitrationResolver(), canary)

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.region_outcome == "inconclusive"
    assert resolver.events == []
    assert resolver.invalidated == []
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is False


def test_off_mode_region_403_does_not_arbitrate(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_skip_log_throttle()
    for name in (
        "AISTUDIO_EXIT_MODE",
        "AISTUDIO_EXIT_ENDPOINT",
        "AISTUDIO_EXIT_TOKEN",
        "AISTUDIO_EXIT_HMAC_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_exit_resolver()
    store, accounts = _make_store(tmp_path)
    arbiter = _RecordingArbiter("exit_bad")
    refresher = AccountInfoRefresher(store, transport=_RegionTransport())
    refresher.set_region_arbiter(arbiter)

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert arbiter.calls == []
    assert result.deferred is False
    assert result.region_outcome is None
    assert result.error is not None and "HTTP 403" in result.error
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.expired is False


def test_region_canary_fires_once_per_account_per_round(tmp_path: Any) -> None:
    reset_skip_log_throttle()
    store, _accounts = _make_store(tmp_path)
    arbiter = _RecordingArbiter("inconclusive")
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, _ArbitrationResolver()),
        pacing_seconds=0.0,
    )
    refresher.set_region_arbiter(arbiter)
    once: set[str] = set()

    first = asyncio.run(refresher._arbitrate_region_403("acc1", once=once))
    second = asyncio.run(refresher._arbitrate_region_403("acc1", once=once))

    assert first == "inconclusive"
    assert second is None
    assert arbiter.calls == ["acc1"]


class _MismatchedExitResolver(_ArbitrationResolver):
    """Probe lease says exit-1, but the live binding already moved to exit-2."""

    def __init__(self) -> None:
        super().__init__()
        self.assignment = ExitAssignment(exit_id="exit-2", socks_url=EXIT_TWO, ttl_s=60)


def test_region_403_probe_exit_mismatch_never_writes_verify(tmp_path: Any) -> None:
    """A host-egress/migrated probe exit must not become an account_bad lock."""
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    resolver = _MismatchedExitResolver()
    canary = _FixedCanary("account_bad")
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, resolver),
        pacing_seconds=0.0,
    )
    refresher.set_region_arbiter(
        ExitArbitration(
            resolver=cast(Any, resolver), canary_probe=canary, probe_cooldown_s=0.0
        )
    )

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.region_outcome == "inconclusive"
    # The probe used exit-1 (its own lease), the binding is exit-2: no canary,
    # no event, no verify_required write.
    assert resolver.events == []
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is False
    assert stored.expired is False


class _BoundSignerSession:
    """Browserless session still running on its original (migrated-away) exit."""

    engine = "browserless"
    _bound_socks = EXIT_ONE

    def is_alive(self) -> bool:
        return True

    async def send_hooked_request(self, **kwargs: object) -> tuple[int, bytes]:
        del kwargs
        return 403, REGION_403_BODY.encode("utf-8")


def test_pooled_session_probe_exit_mismatch_never_writes_verify(tmp_path: Any) -> None:
    """A live session on an old exit must not be blamed via the new binding."""
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    resolver = _MismatchedExitResolver()
    canary = _FixedCanary("account_bad")
    session = _BoundSignerSession()
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, resolver),
        slot_provider=lambda: (
            SimpleNamespace(account_id=accounts[0].id, session=session),
        ),
        pacing_seconds=0.0,
    )
    refresher.set_region_arbiter(
        ExitArbitration(
            resolver=cast(Any, resolver), canary_probe=canary, probe_cooldown_s=0.0
        )
    )

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.region_outcome == "inconclusive"
    assert resolver.events == []
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is False


def test_pooled_session_probe_exit_match_reports_region_suspect(tmp_path: Any) -> None:
    """A proven exit still must not turn a region 403 into verify_required."""
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    resolver = _ArbitrationResolver()
    canary = _FixedCanary("region_suspect")
    session = _BoundSignerSession()
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, resolver),
        slot_provider=lambda: (
            SimpleNamespace(account_id=accounts[0].id, session=session),
        ),
        pacing_seconds=0.0,
    )
    refresher.set_region_arbiter(
        ExitArbitration(
            resolver=cast(Any, resolver), canary_probe=canary, probe_cooldown_s=0.0
        )
    )

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.region_outcome == "region_suspect"
    # The exit is proven, but that only makes the account suspect: no verify
    # lock and no verify_account event (which would burn the exit).
    assert resolver.events == [("acc1", "exit-1", "other_403", "account_bad")]
    stored = store.get_account("acc1")
    assert stored is not None
    assert stored.verify_required is False


class _BuildOnlyArbitrationResolver(_ArbitrationResolver):
    """Manager double whose only live binding is on the build surface."""

    def assignment_for(
        self, account_id: str, *, kind: str = "chat", include_expired: bool = False
    ) -> ExitAssignment | None:
        del account_id, include_expired
        return self.assignment if kind == "build" else None


class _KindRecordingArbiter:
    def __init__(self, outcome: str = "exit_bad") -> None:
        self.outcome = outcome
        self.kinds: list[str] = []

    async def adjudicate(
        self,
        *,
        account_id: str,
        kind: str,
        probe_exit_id: str | None = None,
    ) -> str:
        del account_id, probe_exit_id
        self.kinds.append(kind)
        return self.outcome


def test_region_arbitration_uses_bound_build_surface_kind(tmp_path: Any) -> None:
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    arbiter = _KindRecordingArbiter("exit_bad")
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, _BuildOnlyArbitrationResolver()),
        pacing_seconds=0.0,
    )
    refresher.set_region_arbiter(arbiter)

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.deferred is True
    assert result.region_outcome == "exit_bad"
    # P2-1: the maintenance arbitration must carry the account's bound kind
    # (build), not a hard-coded "chat".
    assert arbiter.kinds == ["build"]


VERIFY_ACCOUNT_BODY = (
    '{"error":{"message":"Account must be verified before it can continue '
    'using the Gemini API. Visit aistudio.google.com/verify-account"}}'
)


class _BoundVerifySession(_BoundSignerSession):
    """Browserless session returned by upstream with an explicit verify body."""

    async def send_hooked_request(self, **kwargs: object) -> tuple[int, bytes]:
        del kwargs
        return 403, VERIFY_ACCOUNT_BODY.encode("utf-8")


def test_explicit_verify_body_writes_verify_and_keeps_expiry(tmp_path: Any) -> None:
    """Only an explicit upstream verify-account body may lock the account."""
    reset_skip_log_throttle()
    store, accounts = _make_store(tmp_path)
    store.update_account_refresh("acc1", expired=True)
    resolver = _ArbitrationResolver()
    refresher = AccountInfoRefresher(
        store,
        transport=_RegionTransport(),
        exit_resolver=cast(Any, resolver),
        slot_provider=lambda: (
            SimpleNamespace(account_id=accounts[0].id, session=_BoundVerifySession()),
        ),
        pacing_seconds=0.0,
    )

    result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.region_outcome == "verify_account"
    assert result.deferred is False
    assert resolver.events == [("acc1", "exit-1", "verify_account", "inconclusive")]
    stored = store.get_account("acc1")
    assert stored is not None
    # Q1: verify_required is written independently; the stronger real expiry is
    # never cleared by a verify verdict.
    assert stored.verify_required is True
    assert stored.expired is True
