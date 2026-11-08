"""Behavior tests for P6 keepalive jobs (no real browser/Google access)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.account_keepalive import (
    TASKS,
    AccountKeepaliveService,
    KeepaliveSettings,
)
from aistudio_api.infrastructure.account.account_info_refresher import (
    AccountInfoRefresher,
    RefreshInProgressError,
    TransportResponse,
)
from aistudio_api.infrastructure.account.account_store import (
    AccountMeta,
    AccountStore,
)


def _settings(**overrides: object) -> KeepaliveSettings:
    base: dict[str, object] = {
        "auth_update": True,
        "auth_update_hours": 24.0,
        "metadata_refresh": True,
        "metadata_refresh_hours": 24.0,
        "metadata_refresh_on_startup": True,
        "active_activity": False,
        "active_activity_seconds": 60.0,
    }
    base.update(overrides)
    return KeepaliveSettings.from_mapping(base)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeControlSession:
    def __init__(
        self,
        *,
        page_open: bool = False,
        saved: bool = True,
        auth_file: Path | None = None,
    ) -> None:
        self.page = (
            SimpleNamespace(is_closed=lambda: not page_open) if page_open else None
        )
        self._saved = saved
        self._auth_file = auth_file
        self.calls = 0

    async def save_current_cookies(self) -> bool:
        self.calls += 1
        return self._saved


class FakeClient:
    def __init__(self, control: FakeControlSession | None = None) -> None:
        self.session = control or FakeControlSession()


class FakeSlotSession:
    def __init__(self, *, saved: bool = True, raises: bool = False) -> None:
        self._saved = saved
        self._raises = raises
        self.calls = 0

    async def save_current_cookies(self) -> bool:
        self.calls += 1
        if self._raises:
            raise RuntimeError("page closed")
        return self._saved


class FakeSlot:
    def __init__(
        self,
        slot_id: str,
        account_id: str,
        *,
        surface: str = "chat",
        saved: bool = True,
        raises: bool = False,
        session: object | None = None,
        in_flight: int = 0,
        lifecycle_lock: object | None = None,
    ) -> None:
        self.slot_id = slot_id
        self.account_id = account_id
        self.surface = surface
        self.session = (
            session
            if session is not None
            else FakeSlotSession(saved=saved, raises=raises)
        )
        self.active_requests = in_flight
        self.lifecycle_lock = lifecycle_lock


class TrackingLifecycleLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.held = False
        self.acquired = 0

    async def __aenter__(self) -> TrackingLifecycleLock:
        await self._lock.acquire()
        self.held = True
        self.acquired += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.held = False
        self._lock.release()
        return False


class LockCheckingSlotSession:
    def __init__(self, lock: TrackingLifecycleLock) -> None:
        self._lock = lock
        self.calls = 0

    async def save_current_cookies(self) -> bool:
        self.calls += 1
        assert self._lock.held, "save_current_cookies must run inside lifecycle_lock"
        return True


class FakeManager:
    def __init__(self, slots: list[FakeSlot] | None = None) -> None:
        self._slots = list(slots or [])

    def pool_slots(self) -> tuple[FakeSlot, ...]:
        return tuple(self._slots)


class FakeRefresher:
    def __init__(self, *, ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.skip_flags: list[bool] = []
        self._ok = ok

    async def refresh_accounts(
        self,
        accounts: list[AccountMeta],
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
    ) -> list[SimpleNamespace]:
        self.calls.append([account.id for account in accounts])
        self.skip_flags.append(skip_fresh_free_tier)
        return [
            SimpleNamespace(
                account_id=account.id,
                ok=self._ok,
                error=None if self._ok else "HTTP 503",
                checked_at=datetime.now(UTC).isoformat(),
            )
            for account in accounts
        ]


class GateRefresher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def refresh_accounts(
        self,
        accounts: list[AccountMeta],
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
    ) -> list[SimpleNamespace]:
        self.started.set()
        await self.release.wait()
        return [
            SimpleNamespace(
                account_id=account.id,
                ok=True,
                error=None,
                checked_at=datetime.now(UTC).isoformat(),
            )
            for account in accounts
        ]


class BusyRefresher:
    async def refresh_accounts(
        self,
        accounts: list[AccountMeta],
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
    ) -> list[SimpleNamespace]:
        raise RefreshInProgressError("already running")


class StaticTransport:
    def __init__(self, status: int) -> None:
        self.status = status

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        return TransportResponse(status=self.status, body="[]")


class CountingTransport:
    def __init__(self) -> None:
        self.posts = 0
        self.gets = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        if method == "POST":
            self.posts += 1
        else:
            self.gets += 1
        return TransportResponse(status=200, body="[]")


def _account(store: AccountStore, account_id: str, email: str) -> AccountMeta:
    account = store.save_account(
        account_id,
        email,
        {"cookies": [{"name": "SID", "value": "old", "domain": ".google.com"}]},
        account_id=account_id,
    )
    store.update_account_keepalive(account_id, auth_saved_at=None)
    return account


def _service(
    store: AccountStore,
    *,
    client: Any | None = None,
    refresher: Any | None = None,
    manager: Any | None = None,
    clock: FakeClock | None = None,
) -> AccountKeepaliveService:
    client_obj: Any = client or FakeClient()
    return AccountKeepaliveService(
        store,
        client_obj,
        refresher or FakeRefresher(),
        instance_manager=manager,
        clock=clock or FakeClock(),
        tick_seconds=3600.0,
    )


def _status(service: AccountKeepaliveService) -> dict[str, Any]:
    return cast("dict[str, Any]", service.status())


@pytest.mark.asyncio
async def test_start_schedules_metadata_and_auth_immediately(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    clock = FakeClock(500.0)
    service = _service(store, clock=clock)

    await service.start()
    try:
        status = _status(service)
        assert status["next_due"]["metadata_refresh"] == _iso(500.0)
        assert status["next_due"]["auth_update"] == _iso(500.0)
    finally:
        await service.stop()


def test_status_hides_next_due_for_disabled_jobs(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    service = _service(store, clock=FakeClock(2_500.0))
    service._next_due = {
        "auth_update": 2_500.0,
        "metadata_refresh": 2_500.0,
        "active_activity": 2_560.0,
    }

    status = _status(service)

    assert status["enabled"]["active_activity"] is False
    assert status["next_due"]["active_activity"] is None
    assert status["next_due"]["auth_update"] == _iso(2_500.0)
    assert status["next_due"]["metadata_refresh"] == _iso(2_500.0)


@pytest.mark.asyncio
async def test_auth_update_writes_all_pool_slots_including_build_only(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_chat", "chat@example.com")
    _account(store, "acc_build", "build@example.com")
    manager = FakeManager(
        [
            FakeSlot("slot-chat", "acc_chat", surface="chat"),
            FakeSlot("slot-build", "acc_build", surface="build"),
        ]
    )
    service = _service(store, manager=manager)

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is True
    assert result["reason"] == "saved"
    assert result["count"] == 2
    chat = store.get_account("acc_chat")
    build = store.get_account("acc_build")
    assert chat is not None and chat.auth_saved_at is not None
    assert build is not None and build.auth_saved_at is not None


@pytest.mark.asyncio
async def test_auth_update_marks_only_accounts_whose_slots_all_saved(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    _account(store, "acc_b", "b@example.com")
    manager = FakeManager(
        [
            FakeSlot("slot-a", "acc_a", saved=True),
            FakeSlot("slot-b", "acc_b", saved=False),
        ]
    )
    service = _service(store, manager=manager)

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "partial"
    assert result["accounts"] == ["acc_a"]
    assert result["failed_accounts"] == ["acc_b"]
    assert result["failed_slots"] == ["slot-b"]
    saved_a = store.get_account("acc_a")
    saved_b = store.get_account("acc_b")
    assert saved_a is not None and saved_a.auth_saved_at is not None
    assert saved_b is not None and saved_b.auth_saved_at is None


@pytest.mark.asyncio
async def test_auth_update_does_not_let_one_slot_mask_same_account_failure(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    manager = FakeManager(
        [
            FakeSlot("slot-a1", "acc_a", saved=True),
            FakeSlot("slot-a2", "acc_a", raises=True),
        ]
    )
    service = _service(store, manager=manager)

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "partial"
    assert result["failed_accounts"] == ["acc_a"]
    account = store.get_account("acc_a")
    assert account is not None
    assert account.auth_saved_at is None


@pytest.mark.asyncio
async def test_auth_update_saves_pool_slot_under_lifecycle_lock(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    lock = TrackingLifecycleLock()
    session = LockCheckingSlotSession(lock)
    manager = FakeManager(
        [FakeSlot("slot-a", "acc_a", session=session, lifecycle_lock=lock)]
    )
    service = _service(store, manager=manager)

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is True
    assert result["accounts"] == ["acc_a"]
    assert session.calls == 1
    assert lock.acquired == 1
    assert lock.held is False
    account = store.get_account("acc_a")
    assert account is not None and account.auth_saved_at is not None


@pytest.mark.asyncio
async def test_auth_update_marks_control_session_account(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_control", "control@example.com")
    control = FakeControlSession(page_open=True, saved=True)
    service = _service(store, client=FakeClient(control))

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is True
    assert result["reason"] == "saved"
    assert result["accounts"] == ["acc_control"]
    assert control.calls == 1
    account = store.get_account("acc_control")
    assert account is not None and account.auth_saved_at is not None


@pytest.mark.asyncio
async def test_control_session_marks_auth_file_owner_not_active_account(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_owner", "owner@example.com")
    _account(store, "acc_active", "active@example.com")
    auth_file = store.get_auth_path_optional("acc_owner")
    assert auth_file is not None
    store.set_active_account("acc_active")
    control = FakeControlSession(page_open=True, saved=True, auth_file=auth_file)
    service = _service(store, client=FakeClient(control))

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is True
    assert result["reason"] == "saved"
    assert result["accounts"] == ["acc_owner"]
    owner = store.get_account("acc_owner")
    active = store.get_account("acc_active")
    assert owner is not None and owner.auth_saved_at is not None
    assert active is not None and active.auth_saved_at is None


@pytest.mark.asyncio
async def test_auth_update_without_active_page_returns_no_active_page(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    service = _service(store)

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "no_active_page"
    account = store.get_account("acc_a")
    assert account is not None and account.auth_saved_at is None


@pytest.mark.asyncio
async def test_auth_update_no_active_page_retries_after_60_seconds(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    clock = FakeClock(1_000.0)
    service = _service(store, clock=clock)
    service._next_due = {
        "auth_update": 1_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    await service._run_due(_settings(metadata_refresh=False))

    assert _status(service)["next_due"]["auth_update"] == _iso(1_060.0)


@pytest.mark.asyncio
async def test_auth_update_success_reschedules_after_24_hours(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    clock = FakeClock(2_000.0)
    manager = FakeManager([FakeSlot("slot-a", "acc_a")])
    service = _service(store, manager=manager, clock=clock)
    service._next_due = {
        "auth_update": 2_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    await service._run_due(_settings(metadata_refresh=False))

    assert _status(service)["next_due"]["auth_update"] == _iso(2_000.0 + 86_400.0)


@pytest.mark.asyncio
async def test_busy_job_does_not_advance_next_due(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    clock = FakeClock(3_000.0)
    service = _service(store, clock=clock)
    service._next_due = {
        "auth_update": 3_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }
    await service._job_lock.acquire()
    try:
        await service._run_due(_settings(metadata_refresh=False))
    finally:
        service._job_lock.release()

    assert _status(service)["next_due"]["auth_update"] == _iso(3_000.0)
    assert _status(service)["last_results"] == {}


@pytest.mark.asyncio
async def test_single_global_job_lock_rejects_concurrent_job(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    refresher = GateRefresher()
    service = _service(store, refresher=refresher)
    active = asyncio.create_task(
        service.run_now("metadata_refresh", settings=_settings())
    )
    await refresher.started.wait()
    try:
        rejected = await service.run_now("auth_update", settings=_settings())
    finally:
        refresher.release.set()
        await active

    assert rejected["reason"] == "busy"
    assert _status(service)["last_results"]["metadata_refresh"]["ok"] is True


@pytest.mark.asyncio
async def test_metadata_refresh_only_targets_due_healthy_accounts(tmp_path):
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    fresh = _account(store, "acc_fresh", "fresh@example.com")
    stale = _account(store, "acc_stale", "stale@example.com")
    expired = _account(store, "acc_expired", "expired@example.com")
    store.update_account_refresh(fresh.id, tier_checked_at=now.isoformat())
    stale_at = (now - timedelta(days=2)).isoformat()
    store.update_account_refresh(stale.id, tier_checked_at=stale_at)
    store.update_account_refresh(expired.id, tier_checked_at=stale_at, expired=True)

    refresher = FakeRefresher()
    service = _service(store, refresher=refresher, clock=FakeClock(now.timestamp()))

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert refresher.calls == [["acc_stale"]]
    assert refresher.skip_flags == [True]
    stale_after = store.get_account("acc_stale")
    expired_after = store.get_account("acc_expired")
    assert stale_after is not None
    assert expired_after is not None
    assert stale_after.last_refresh_attempt_at is not None
    assert expired_after.last_refresh_attempt_at is None


@pytest.mark.asyncio
async def test_metadata_refresh_reports_manual_refresh_in_progress(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    service = _service(store, refresher=BusyRefresher())

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "refresh_in_progress"


@pytest.mark.asyncio
async def test_metadata_5xx_leaves_state_unchanged_and_retries_short(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    refresher = AccountInfoRefresher(store, transport=StaticTransport(503))
    clock = FakeClock(4_000.0)
    service = _service(store, refresher=refresher, clock=clock)
    service._next_due = {
        "metadata_refresh": 4_000.0,
        "auth_update": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    result = await service.run_now("metadata_refresh", settings=_settings())
    await service._run_due(_settings(auth_update=False))

    account = store.get_account("acc_a")
    assert account is not None
    assert result["ok"] is False
    assert account.tier == "Free"
    assert account.expired is False
    assert account.tier_checked_at is None
    assert account.last_refresh_attempt_at is not None
    assert _status(service)["next_due"]["metadata_refresh"] == _iso(4_300.0)


@pytest.mark.asyncio
async def test_metadata_401_marks_account_expired(tmp_path):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    refresher = AccountInfoRefresher(store, transport=StaticTransport(401))
    service = _service(store, refresher=refresher)

    result = await service.run_now("metadata_refresh", settings=_settings())

    account = store.get_account("acc_a")
    assert account is not None
    assert result["ok"] is False
    assert account.expired is True
    assert account.last_refresh_attempt_at is not None


def test_auto_switch_and_rebalance_are_not_part_of_keepalive():
    assert "auto_switch" not in TASKS
    assert not hasattr(AccountKeepaliveService, "_auto_switch")
    import aistudio_api.application.account_keepalive as module

    assert "InstanceSwitcher" not in vars(module)


@pytest.mark.asyncio
async def test_metadata_refresh_skips_benefit_tier_for_fresh_free(tmp_path):
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    account = _account(store, "acc_free", "free@example.com")
    checked = (now - timedelta(days=2)).isoformat()
    store.update_account_refresh(account.id, tier="Free", tier_checked_at=checked)
    transport = CountingTransport()
    refresher = AccountInfoRefresher(store, transport=transport)
    service = _service(
        store, refresher=refresher, clock=FakeClock(now.timestamp())
    )

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert transport.posts == 0
    after = store.get_account(account.id)
    assert after is not None
    assert after.tier == "Free"
    assert after.tier_checked_at == checked


@pytest.mark.asyncio
async def test_metadata_refresh_probes_free_tier_older_than_three_days(tmp_path):
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    account = _account(store, "acc_free", "free@example.com")
    checked = (now - timedelta(days=4)).isoformat()
    store.update_account_refresh(account.id, tier="Free", tier_checked_at=checked)
    transport = CountingTransport()
    refresher = AccountInfoRefresher(store, transport=transport)
    service = _service(
        store, refresher=refresher, clock=FakeClock(now.timestamp())
    )

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert transport.posts == 1
    after = store.get_account(account.id)
    assert after is not None
    assert after.tier_checked_at != checked
