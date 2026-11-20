"""REV-2.1.5.3: engine-aware ``GetAiStudioBenefitTier`` probing + failure backoff.

All transports are in-memory fakes: no real network, no real cookies.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from aistudio_api.api import routes_accounts
from aistudio_api.api.routes_accounts import detect_account_tier
from aistudio_api.api.state import RuntimeState
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_info_refresher import (
    ACCEPT_LANGUAGE,
    TIER_PROBE_BACKOFF_CAP_SECONDS,
    TIER_PROBE_FAILURE_BASE_SECONDS,
    AccountInfoRefresher,
    TransportResponse,
)
from aistudio_api.infrastructure.account.account_store import (
    AccountMeta,
    AccountStore,
)
from aistudio_api.infrastructure.gateway.public_web_constants import BENEFIT_TIER_URL

_STORAGE_STATE: dict[str, object] = {
    "cookies": [
        {"name": "SAPISID", "value": "sapisid", "domain": ".google.com", "path": "/"},
        {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
    ],
    "origins": [],
}


@dataclass
class _RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    content: str


class _RecordingTransport:
    """Scripted raw-HTTP transport; records every call so tests can prove non-use."""

    def __init__(self, *responses: TransportResponse) -> None:
        self.responses = list(responses) or [TransportResponse(status=200, body="[1]")]
        self.requests: list[_RecordedRequest] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        self.requests.append(_RecordedRequest(method, url, dict(headers), content))
        if not self.responses:
            raise AssertionError("_RecordingTransport: no scripted response left")
        return self.responses.pop(0)


@dataclass
class _Slot:
    account_id: str
    session: object | None


@dataclass
class _SlotProvider:
    slots: list[_Slot] = field(default_factory=list)

    def __call__(self) -> tuple[_Slot, ...]:
        return tuple(self.slots)


class _SignerSession:
    """Browserless session double exposing ``send_hooked_request``."""

    engine = "browserless"

    def __init__(self, *, status: int = 200, body: bytes = b"[1]") -> None:
        self._status = status
        self._body = body
        self.calls: list[tuple[str | None, str, dict[str, str]]] = []

    def is_alive(self) -> bool:
        return True

    async def send_hooked_request(
        self,
        *,
        body: str,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        self.calls.append((url, body, dict(headers or {})))
        return self._status, self._body


class _Page:
    def __init__(self, *, status: int = 200, text: str = "[2]") -> None:
        self._status = status
        self._text = text
        self.calls: list[dict[str, object]] = []

    def is_closed(self) -> bool:
        return False

    async def evaluate(
        self,
        script: str,
        args: dict[str, object] | None = None,
        timeout_s: float = 0.0,
    ) -> dict[str, object]:
        self.calls.append(
            {"script": script, "args": dict(args or {}), "timeout_s": timeout_s}
        )
        return {"status": self._status, "text": self._text}


class _BrowserSession:
    engine = "browser"

    def __init__(self, page: object | None) -> None:
        self.page = page

    def is_alive(self) -> bool:
        return True


def _make_store(tmp_path: Path) -> tuple[AccountStore, AccountMeta]:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Acc",
        email="user@example.com",
        storage_state=_STORAGE_STATE,
    )
    return store, account


def _signer_refresher(
    store: AccountStore,
    account: AccountMeta,
    session: object,
    transport: _RecordingTransport,
) -> AccountInfoRefresher:
    provider = _SlotProvider([_Slot(account.id, session)])
    return AccountInfoRefresher(store, transport=transport, slot_provider=provider)


def test_browserless_tier_probe_uses_signer_transport(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    session = _SignerSession()
    transport = _RecordingTransport()

    refresher = _signer_refresher(store, account, session, transport)
    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.tier == "Pro"
    assert transport.requests == []
    assert len(session.calls) == 1
    url, body, headers = session.calls[0]
    assert url == BENEFIT_TIER_URL
    assert body == "[]"
    # The signer attaches the account's complete cookie jar, fingerprint and
    # server-issued visit id itself; no per-request Cookie/Authorization leaks in.
    assert headers == {"accept": "*/*", "accept-language": ACCEPT_LANGUAGE}
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier == "Pro"
    assert stored.tier_checked_at is not None


def test_browser_tier_probe_uses_page_xhr_when_page_available(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    page = _Page(status=200, text="[2]")
    transport = _RecordingTransport()

    refresher = _signer_refresher(store, account, _BrowserSession(page), transport)
    result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.tier == "Ultra"
    assert transport.requests == []
    assert len(page.calls) == 1
    args = page.calls[0]["args"]
    assert isinstance(args, dict)
    assert args["url"] == BENEFIT_TIER_URL
    assert args["authUser"] == "0"
    assert str(args["authorization"]).startswith("SAPISIDHASH ")


def test_browser_tier_probe_without_page_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, account = _make_store(tmp_path)
    transport = _RecordingTransport(TransportResponse(status=200, body="[1]"))
    refresher = _signer_refresher(store, account, _BrowserSession(None), transport)

    with caplog.at_level(logging.WARNING, logger="aistudio.account_refresher"):
        result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.tier == "Pro"
    assert len(transport.requests) == 1
    assert transport.requests[0].url == BENEFIT_TIER_URL
    assert any(
        "no live page" in record.getMessage() and "raw httpx" in record.getMessage()
        for record in caplog.records
    )


def test_missing_pooled_session_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, account = _make_store(tmp_path)
    transport = _RecordingTransport(TransportResponse(status=200, body="[]"))
    refresher = AccountInfoRefresher(
        store, transport=transport, slot_provider=_SlotProvider([])
    )

    with caplog.at_level(logging.WARNING, logger="aistudio.account_refresher"):
        result = asyncio.run(refresher.refresh_account(account, refresh_email=False))

    assert result.ok is True
    assert result.tier == "Free"
    assert len(transport.requests) == 1
    assert any(
        "no pooled engine session" in record.getMessage() for record in caplog.records
    )


def test_tier_failure_backoff_sequence_and_cap(tmp_path: Path) -> None:
    store, account = _make_store(tmp_path)
    refresher = AccountInfoRefresher(store, transport=_RecordingTransport())

    expected = [300.0, 600.0, 1200.0, 2400.0, 4800.0]
    for index, delay in enumerate(expected, start=1):
        refresher._record_tier_probe(account.id, ok=False, now=1_000.0)
        assert refresher.tier_failure_count(account.id) == index
        assert refresher.tier_backoff_seconds(account.id, now=1_000.0) == delay

    for _ in range(10):
        refresher._record_tier_probe(account.id, ok=False, now=1_000.0)
    assert (
        refresher.tier_backoff_seconds(account.id, now=1_000.0)
        == TIER_PROBE_BACKOFF_CAP_SECONDS
    )
    assert TIER_PROBE_BACKOFF_CAP_SECONDS == 21_600.0 != TIER_PROBE_FAILURE_BASE_SECONDS

    refresher._record_tier_probe(account.id, ok=True)
    assert refresher.tier_failure_count(account.id) == 0
    assert refresher.tier_backoff_seconds(account.id, now=1_000.0) == 0.0


def test_scheduled_refresh_respects_backoff_but_manual_refresh_does_not(
    tmp_path: Path,
) -> None:
    store, account = _make_store(tmp_path)
    transport = _RecordingTransport(
        TransportResponse(status=403, body='[7,"Region not supported."]'),
        TransportResponse(status=200, body="[1]"),
    )
    refresher = AccountInfoRefresher(store, transport=transport)

    first = asyncio.run(refresher.refresh_account(account, refresh_email=False))
    assert first.ok is False
    assert first.error is not None and "403" in first.error
    assert refresher.tier_failure_count(account.id) == 1
    assert len(transport.requests) == 1

    scheduled = asyncio.run(
        refresher.refresh_account(account, refresh_email=False, respect_backoff=True)
    )
    assert scheduled.ok is False
    assert scheduled.error is not None and "backoff" in scheduled.error
    assert len(transport.requests) == 1

    manual = asyncio.run(refresher.refresh_account(account, refresh_email=False))
    assert manual.ok is True
    assert manual.tier == "Pro"
    assert len(transport.requests) == 2
    assert refresher.tier_failure_count(account.id) == 0


def test_refresh_accounts_preserves_per_account_ok_and_error(tmp_path: Path) -> None:
    store, first = _make_store(tmp_path)
    second = store.save_account(
        name="Second",
        email="second@example.com",
        storage_state=_STORAGE_STATE,
    )
    transport = _RecordingTransport(
        TransportResponse(status=200, body="[1]"),
        TransportResponse(status=403, body='[7,"Region not supported."]'),
    )
    refresher = AccountInfoRefresher(store, transport=transport)

    results = asyncio.run(
        refresher.refresh_accounts([first, second], refresh_email=False)
    )

    assert [item.account_id for item in results] == [first.id, second.id]
    assert results[0].ok is True
    assert results[0].tier == "Pro"
    assert results[0].error is None
    assert results[1].ok is False
    assert results[1].tier is None
    assert results[1].expired is None
    assert results[1].error is not None
    assert "403" in results[1].error
    assert "Region not supported." in results[1].error


class _DetectTierManager:
    def __init__(self, slots: list[_Slot]) -> None:
        self._slots = list(slots)
        self.synced: list[tuple[str, str]] = []

    def ready_slot_for_account(self, account_id: str) -> object | None:
        del account_id
        return None

    def pool_slots(self) -> tuple[_Slot, ...]:
        return tuple(self._slots)

    def sync_account_tier(self, account_id: str, tier: str) -> int:
        self.synced.append((account_id, tier))
        return 1


@pytest.mark.asyncio
async def test_detect_tier_endpoint_uses_signer_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Paid",
        email="paid@example.com",
        storage_state=_STORAGE_STATE,
        tier="Pro",
    )
    service = AccountService(store)
    session = _SignerSession(status=200, body=b"[1]")
    manager = _DetectTierManager([_Slot(account.id, session)])
    state = RuntimeState(
        account_service=service,
        client=None,
        instance_manager=manager,
    )

    def _forbidden_probe(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("detect-tier must not fall back to raw httpx")

    monkeypatch.setattr(routes_accounts, "probe_account_benefit_tier", _forbidden_probe)

    response = await detect_account_tier(
        account_id=account.id, account_service=service, runtime_state=state
    )

    assert response.tier == "Pro"
    assert len(session.calls) == 1
    assert session.calls[0][0] == BENEFIT_TIER_URL
    assert manager.synced == [(account.id, "Pro")]
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier == "Pro"


@pytest.mark.asyncio
async def test_detect_tier_engine_failure_blocks_raw_probe_and_returns_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An engine failure is terminal: no second raw httpx attempt, 502 carries it."""
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Paid",
        email="paid@example.com",
        storage_state=_STORAGE_STATE,
        tier="Pro",
    )
    service = AccountService(store)
    session = _SignerSession(status=403, body=b'[7,"Region not supported."]')
    manager = _DetectTierManager([_Slot(account.id, session)])
    state = RuntimeState(
        account_service=service,
        client=None,
        instance_manager=manager,
    )

    def _forbidden_probe(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("engine failure must not fall back to raw httpx")

    monkeypatch.setattr(routes_accounts, "probe_account_benefit_tier", _forbidden_probe)

    with pytest.raises(HTTPException) as exc:
        await detect_account_tier(
            account_id=account.id, account_service=service, runtime_state=state
        )

    assert exc.value.status_code == 502
    detail = str(exc.value.detail)
    assert "HTTP 403" in detail
    assert "Region not supported." in detail
    assert len(session.calls) == 1
    assert manager.synced == []
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier == "Pro"


@pytest.mark.asyncio
async def test_detect_tier_without_pool_session_falls_back_to_raw_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No pooled session is a degraded path: raw probe allowed, WARNING emitted."""
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Unbound",
        email="unbound@example.com",
        storage_state=_STORAGE_STATE,
        tier="Free",
    )
    service = AccountService(store)
    manager = _DetectTierManager([])
    state = RuntimeState(
        account_service=service,
        client=None,
        instance_manager=manager,
    )

    engine_probe = AsyncMock(return_value=None)
    raw_probe = AsyncMock(return_value="Pro")
    monkeypatch.setattr(routes_accounts, "probe_tier_over_session", engine_probe)
    monkeypatch.setattr(routes_accounts, "probe_account_benefit_tier", raw_probe)

    with caplog.at_level(logging.WARNING, logger="aistudio.accounts"):
        response = await detect_account_tier(
            account_id=account.id, account_service=service, runtime_state=state
        )

    assert response.tier == "Pro"
    engine_probe.assert_not_awaited()
    raw_probe.assert_awaited_once()
    assert manager.synced == [(account.id, "Pro")]
    assert any("回退裸 httpx" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_keepalive_metadata_refresh_probes_via_signer_session(
    tmp_path: Path,
) -> None:
    """Scheduled refresh reuses the pool session; a failure arms the backoff."""
    from aistudio_api.application.account_keepalive import (
        AccountKeepaliveService,
        KeepaliveSettings,
    )

    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Keepalive",
        email="keep@example.com",
        storage_state=_STORAGE_STATE,
    )
    stale_at = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    store.update_account_refresh(account.id, tier_checked_at=stale_at)
    account = store.get_account(account.id)
    assert account is not None

    session = _SignerSession(status=200, body=b"[1]")
    transport = _RecordingTransport()
    refresher = AccountInfoRefresher(
        store,
        transport=transport,
        slot_provider=_SlotProvider([_Slot(account.id, session)]),
    )

    class _Manager:
        def pool_slots(self) -> tuple[_Slot, ...]:
            return (_Slot(account.id, session),)

    class _Client:
        session = SimpleNamespace(page=None)

    client: Any = _Client()
    service = AccountKeepaliveService(
        store,
        client,
        refresher,
        instance_manager=_Manager(),
        rotator=None,
        tick_seconds=3600.0,
    )
    settings = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        metadata_refresh_on_startup=True,
        active_activity=False,
    )

    result = await service.run_now("metadata_refresh", settings=settings)

    assert result["ok"] is True
    assert len(session.calls) == 1
    assert transport.requests == []
    stored = store.get_account(account.id)
    assert stored is not None
    assert stored.tier == "Pro"
    assert refresher.tier_failure_count(account.id) == 0


@pytest.mark.asyncio
async def test_keepalive_scheduled_refresh_stops_hitting_after_failure(
    tmp_path: Path,
) -> None:
    """A failed scheduled probe arms the backoff and the next round skips upstream."""
    from aistudio_api.application.account_keepalive import (
        AccountKeepaliveService,
        KeepaliveSettings,
    )

    store = AccountStore(accounts_dir=tmp_path / "accounts")
    account = store.save_account(
        name="Keepalive",
        email="keep@example.com",
        storage_state=_STORAGE_STATE,
    )
    stale_at = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    store.update_account_refresh(account.id, tier_checked_at=stale_at)

    session = _SignerSession(status=403, body=b'[7,"Region not supported."]')
    transport = _RecordingTransport()
    refresher = AccountInfoRefresher(
        store,
        transport=transport,
        slot_provider=_SlotProvider([_Slot(account.id, session)]),
    )

    class _Manager:
        def pool_slots(self) -> tuple[_Slot, ...]:
            return (_Slot(account.id, session),)

    class _Client:
        session = SimpleNamespace(page=None)

    client: Any = _Client()
    service = AccountKeepaliveService(
        store,
        client,
        refresher,
        instance_manager=_Manager(),
        rotator=None,
        tick_seconds=3600.0,
    )
    settings = KeepaliveSettings(
        auth_update=False,
        metadata_refresh=True,
        metadata_refresh_on_startup=True,
        active_activity=False,
    )

    first = await service.run_now("metadata_refresh", settings=settings)
    assert first["ok"] is False
    assert len(session.calls) == 1
    assert refresher.tier_failure_count(account.id) == 1

    second = await service.run_now("metadata_refresh", settings=settings)
    assert second["ok"] is False
    assert len(session.calls) == 1
    assert transport.requests == []
