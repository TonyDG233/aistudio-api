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
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.config import settings
from aistudio_api.domain.errors import RoutingError
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
from aistudio_api.infrastructure.gateway.instance_pool import InstancePool, InstanceSpec
from aistudio_api.infrastructure.waa.browserless_session import BrowserlessSession
from aistudio_api.infrastructure.waa.engine_runtime import (
    BrowserlessEngineRuntime,
    build_engine_runtime,
)

EXIT_ONE = "socks5h://127.0.0.1:40001"
EXIT_TWO = "socks5h://127.0.0.1:40002"
SENTINEL_GLOBAL = "socks5://127.0.0.1:59998"

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

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(refresher.refresh_account(accounts[0]))

    assert result.ok is False
    assert result.skipped is True
    assert result.skip_reason == "no_exit"
    assert result.error is not None and "skipped_no_exit" in result.error
    assert proxies == []  # no request was even attempted
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
    with caplog.at_level(logging.WARNING, logger="exit-pool"):
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


def test_emergency_direct_fallback_sends_without_proxy(
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

    result = asyncio.run(refresher.refresh_account(accounts[0], refresh_email=False))

    assert result.ok is True
    assert proxies == [None]


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


def test_external_direct_fallback_session_is_proxy_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "proxy_url", SENTINEL_GLOBAL)
    monkeypatch.setenv("AISTUDIO_EXIT_DIRECT_FALLBACK", "1")
    runtime = _browserless_runtime(FakeExitResolver(fail=True))

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
    assert session._proxy is None
    assert session._external_exits is True

    captured: list[str | None] = []

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.append(cast("str | None", kwargs.get("proxy")))

        async def aclose(self) -> bool:
            return True

    monkeypatch.setattr(
        "aistudio_api.infrastructure.waa.browserless_session.httpx.AsyncClient",
        _FakeClient,
    )
    asyncio.run(session._get_direct_http())

    assert captured == [None]
    assert SENTINEL_GLOBAL not in captured


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

    with caplog.at_level(logging.WARNING):
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

    with caplog.at_level(logging.WARNING):
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
