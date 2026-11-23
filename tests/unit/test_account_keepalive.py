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
from aistudio_api.application.account_service import AccountService
from aistudio_api.infrastructure.account.account_info_refresher import (
    AccountInfoRefresher,
    RefreshInProgressError,
    TransportResponse,
)
from aistudio_api.infrastructure.account.account_store import (
    AccountMeta,
    AccountStore,
)
from aistudio_api.infrastructure.exit_pool.resolver import LeaseResult


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


class FakeRotator:
    """Minimal rotator surface: only auth-lock state matters to keepalive."""

    def __init__(
        self,
        locked: set[str] | None = None,
        verify_locked: set[str] | None = None,
    ) -> None:
        self.locked = set(locked or ())
        self.verify_locked = set(verify_locked or ())

    def get_account_stats(self, account_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            session_expired=account_id in self.locked,
            verify_required=account_id in self.verify_locked,
        )

    def is_auth_locked(self, account_id: str) -> bool:
        return account_id in self.locked or account_id in self.verify_locked


class FakeRefresher:
    def __init__(self, *, ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.skip_flags: list[bool] = []
        self.email_flags: list[bool] = []
        self.backoff_flags: list[bool] = []
        self._ok = ok

    async def refresh_accounts(
        self,
        accounts: list[AccountMeta],
        *,
        refresh_email: bool = True,
        skip_fresh_free_tier: bool = False,
        respect_backoff: bool = False,
    ) -> list[SimpleNamespace]:
        self.calls.append([account.id for account in accounts])
        self.skip_flags.append(skip_fresh_free_tier)
        self.email_flags.append(refresh_email)
        self.backoff_flags.append(respect_backoff)
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
        respect_backoff: bool = False,
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
        respect_backoff: bool = False,
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
    account_service: Any | None = None,
    rotator: Any | None = None,
    clock: FakeClock | None = None,
) -> AccountKeepaliveService:
    client_obj: Any = client or FakeClient()
    return AccountKeepaliveService(
        store,
        client_obj,
        refresher or FakeRefresher(),
        instance_manager=manager,
        account_service=account_service,
        rotator=rotator if rotator is not None else FakeRotator(),
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
async def test_browserless_auth_update_uses_slot_cookies_without_control_page(
    tmp_path,
):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    control = FakeControlSession(page_open=False)
    slot_session = FakeSlotSession(saved=True)
    manager = FakeManager([FakeSlot("slot-a", "acc_a", session=slot_session)])
    service = _service(store, client=FakeClient(control), manager=manager)

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is True
    assert result["accounts"] == ["acc_a"]
    assert slot_session.calls == 1
    # Browserless has no control page; only per-slot signer cookies are written.
    assert control.calls == 0


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
    assert refresher.email_flags == [False]
    assert refresher.backoff_flags == [True]
    stale_after = store.get_account("acc_stale")
    expired_after = store.get_account("acc_expired")
    assert stale_after is not None
    assert expired_after is not None
    assert stale_after.last_refresh_attempt_at is not None
    assert expired_after.last_refresh_attempt_at is None


@pytest.mark.asyncio
async def test_metadata_refresh_skips_auth_locked_and_disabled_accounts(tmp_path):
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    stale_at = (now - timedelta(days=2)).isoformat()
    healthy = _account(store, "acc_ok", "ok@example.com")
    locked = _account(store, "acc_locked", "locked@example.com")
    disabled = _account(store, "acc_disabled", "disabled@example.com")
    for account in (healthy, locked, disabled):
        store.update_account_refresh(account.id, tier_checked_at=stale_at)
    store.toggle_account_enabled(disabled.id, False)

    refresher = FakeRefresher()
    rotator = FakeRotator({"acc_locked"})
    service = _service(
        store,
        refresher=refresher,
        rotator=rotator,
        clock=FakeClock(now.timestamp()),
    )

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert refresher.calls == [["acc_ok"]]
    assert refresher.email_flags == [False]
    locked_after = store.get_account("acc_locked")
    disabled_after = store.get_account("acc_disabled")
    assert locked_after is not None
    assert disabled_after is not None
    assert locked_after.last_refresh_attempt_at is None
    assert disabled_after.last_refresh_attempt_at is None


@pytest.mark.asyncio
async def test_auth_update_skips_auth_locked_accounts(tmp_path, monkeypatch):
    store = AccountStore(tmp_path)
    _account(store, "acc_ok", "ok@example.com")
    _account(store, "acc_locked", "locked@example.com")
    account_service = AccountService(store)
    rotated: list[str] = []

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del session
        rotated.append(account_id)
        return {"ok": True}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    service = _service(
        store,
        account_service=account_service,
        rotator=FakeRotator({"acc_locked"}),
    )

    await service.run_now("auth_update", settings=_settings())

    assert rotated == ["acc_ok"]


@pytest.mark.asyncio
async def test_metadata_refresh_skips_verify_required_accounts(tmp_path):
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    stale_at = (now - timedelta(days=2)).isoformat()
    healthy = _account(store, "acc_ok", "ok@example.com")
    verify = _account(store, "acc_verify", "verify@example.com")
    for account in (healthy, verify):
        store.update_account_refresh(account.id, tier_checked_at=stale_at)

    refresher = FakeRefresher()
    service = _service(
        store,
        refresher=refresher,
        rotator=FakeRotator(verify_locked={"acc_verify"}),
        clock=FakeClock(now.timestamp()),
    )

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert refresher.calls == [["acc_ok"]]
    verify_after = store.get_account("acc_verify")
    assert verify_after is not None
    assert verify_after.last_refresh_attempt_at is None


@pytest.mark.asyncio
async def test_auth_update_skips_verify_required_accounts(tmp_path, monkeypatch):
    store = AccountStore(tmp_path)
    _account(store, "acc_ok", "ok@example.com")
    _account(store, "acc_verify", "verify@example.com")
    account_service = AccountService(store)
    rotated: list[str] = []

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del session
        rotated.append(account_id)
        return {"ok": True}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    service = _service(
        store,
        account_service=account_service,
        rotator=FakeRotator(verify_locked={"acc_verify"}),
    )

    await service.run_now("auth_update", settings=_settings())

    assert rotated == ["acc_ok"]


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
    service = _service(store, refresher=refresher, clock=FakeClock(now.timestamp()))

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
    service = _service(store, refresher=refresher, clock=FakeClock(now.timestamp()))

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert transport.posts == 1
    after = store.get_account(account.id)
    assert after is not None
    assert after.tier_checked_at != checked


@pytest.mark.asyncio
async def test_metadata_refresh_backfills_missing_email_when_tier_is_fresh(tmp_path):
    """启动期裸探测并入保活后，缺邮箱账号即使 tier 新鲜也必须进批次。"""
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    account = _account(store, "acc_no_email", "")
    store.update_account_refresh(
        account.id,
        tier="Free",
        tier_checked_at=(now - timedelta(hours=1)).isoformat(),
    )

    class _EmailTransport:
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
                return TransportResponse(status=200, body="[]")
            self.gets += 1
            return TransportResponse(
                status=200, body='{"oPEP7c":"backfilled@example.com"}'
            )

    transport = _EmailTransport()
    refresher = AccountInfoRefresher(store, transport=transport)
    service = _service(store, refresher=refresher, clock=FakeClock(now.timestamp()))

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert result["accounts"] == [account.id]
    after = store.get_account(account.id)
    assert after is not None
    assert after.email == "backfilled@example.com"
    # 3 天内已验证 Free：只回填邮箱，不重复打 benefit-tier。
    assert transport.posts == 0
    assert transport.gets == 1


@pytest.mark.asyncio
async def test_pause_race_rechecked_after_job_lock(tmp_path) -> None:
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    service = _service(store)
    executed: list[str] = []

    async def _execute(task: str, settings: object, trigger: str) -> dict[str, object]:
        del settings, trigger
        executed.append(task)
        return {"ok": True, "task": task}

    service._execute = _execute  # type: ignore[method-assign]

    class _RecheckLock:
        def __init__(self) -> None:
            self.inner = asyncio.Lock()

        def locked(self) -> bool:
            return False

        async def __aenter__(self) -> _RecheckLock:
            await self.inner.acquire()
            return self

        async def __aexit__(self, *args: object) -> None:
            del args
            self.inner.release()

    fake_lock = _RecheckLock()
    await fake_lock.inner.acquire()
    service._job_lock = fake_lock  # type: ignore[assignment]

    task = asyncio.create_task(
        service.run_now("metadata_refresh", settings=_settings())
    )
    await asyncio.sleep(0)
    service._paused = True
    fake_lock.inner.release()
    result = await task

    assert result["reason"] == "paused"
    assert executed == []
    assert service._job_lock is fake_lock


@pytest.mark.asyncio
async def test_auth_update_slot_not_dirty_but_rotate_ok_succeeds_and_reschedules_24h(
    tmp_path, monkeypatch
):
    store = AccountStore(tmp_path)
    _account(store, "acc_slot", "slot@example.com")
    slot_session = FakeSlotSession(saved=False)
    manager = FakeManager([FakeSlot("inst-1", "acc_slot", session=slot_session)])
    account_service = AccountService(store)
    rotated: list[str] = []

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del session
        rotated.append(account_id)
        return {"ok": True, "status": 200, "updated": ["__Secure-1PSIDTS"]}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    clock = FakeClock(10_000.0)
    service = _service(
        store, manager=manager, account_service=account_service, clock=clock
    )
    service._next_due = {
        "auth_update": 10_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    await service._run_due(_settings(metadata_refresh=False))

    last = _status(service)["last_results"]["auth_update"]
    assert last["ok"] is True
    assert last["reason"] == "saved"
    assert last["accounts"] == ["acc_slot"]
    assert last["failed_slots"] == []
    assert "failed_accounts" not in last
    assert rotated == ["acc_slot"]
    assert _status(service)["next_due"]["auth_update"] == _iso(10_000.0 + 86_400.0)
    saved = store.get_account("acc_slot")
    assert saved is not None and saved.auth_saved_at is not None


@pytest.mark.asyncio
async def test_auth_update_browserless_clean_slot_with_missing_seed_not_marked_failed(
    tmp_path,
):
    store = AccountStore(tmp_path)
    _account(store, "acc_clean", "clean@example.com")

    class _CleanBrowserlessSession(FakeSlotSession):
        engine = "browserless"
        _cookie_dirty = False

    slot_session = _CleanBrowserlessSession(saved=False)
    manager = FakeManager([FakeSlot("inst-1", "acc_clean", session=slot_session)])
    service = _service(store, manager=manager)

    result = await service.run_now(
        "auth_update", trigger="schedule", settings=_settings()
    )

    assert result["ok"] is True
    assert result["reason"] == "saved"
    assert result["accounts"] == ["acc_clean"]
    assert result["failed_slots"] == []
    saved = store.get_account("acc_clean")
    assert saved is not None and saved.auth_saved_at is not None


@pytest.mark.asyncio
async def test_auth_update_partial_retries_after_300s_without_reprocessing_saved_accounts(
    tmp_path, monkeypatch
):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    _account(store, "acc_b", "b@example.com")
    slot_a_session = FakeSlotSession(saved=True)
    slot_b_session = FakeSlotSession(saved=False)
    manager = FakeManager(
        [
            FakeSlot("slot-a", "acc_a", session=slot_a_session),
            FakeSlot("slot-b", "acc_b", session=slot_b_session),
        ]
    )
    account_service = AccountService(store)
    rotated_calls: list[str] = []
    rotate_results: dict[str, dict[str, object]] = {
        "acc_a": {"ok": True},
        "acc_b": {"ok": False, "status": 500},
    }

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del session
        rotated_calls.append(account_id)
        return rotate_results[account_id]

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    clock = FakeClock(20_000.0)
    service = _service(
        store, manager=manager, account_service=account_service, clock=clock
    )
    service._next_due = {
        "auth_update": 20_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    await service._run_due(_settings(metadata_refresh=False))

    last = _status(service)["last_results"]["auth_update"]
    assert last["ok"] is False
    assert last["reason"] == "partial"
    assert last["accounts"] == ["acc_a"]
    assert last["failed_accounts"] == ["acc_b"]
    assert last["failed_slots"] == ["slot-b"]
    assert _status(service)["next_due"]["auth_update"] == _iso(20_300.0)
    assert slot_a_session.calls == 1
    assert slot_b_session.calls == 1
    assert rotated_calls == ["acc_a", "acc_b"]

    rotated_calls.clear()
    slot_b_session._saved = True
    rotate_results["acc_b"] = {"ok": True}
    clock.now = 20_300.0

    await service._run_due(_settings(metadata_refresh=False))

    last_retry = _status(service)["last_results"]["auth_update"]
    assert last_retry["ok"] is True
    assert last_retry["reason"] == "saved"
    assert last_retry["accounts"] == ["acc_b"]
    assert slot_a_session.calls == 1
    assert slot_b_session.calls == 2
    assert rotated_calls == ["acc_b"]
    assert _status(service)["next_due"]["auth_update"] == _iso(20_300.0 + 86_400.0)


@pytest.mark.asyncio
async def test_auth_update_stops_retrying_account_after_three_consecutive_failures_in_cycle(
    tmp_path, monkeypatch
):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    _account(store, "acc_b", "b@example.com")
    slot_a_session = FakeSlotSession(saved=True)
    slot_b_session = FakeSlotSession(saved=False)
    manager = FakeManager(
        [
            FakeSlot("slot-a", "acc_a", session=slot_a_session),
            FakeSlot("slot-b", "acc_b", session=slot_b_session),
        ]
    )
    account_service = AccountService(store)
    rotated_calls: list[str] = []

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del session
        rotated_calls.append(account_id)
        if account_id == "acc_a":
            return {"ok": True}
        return {"ok": False, "status": 500}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    clock = FakeClock(30_000.0)
    service = _service(
        store, manager=manager, account_service=account_service, clock=clock
    )
    service._next_due = {
        "auth_update": 30_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    await service._run_due(_settings(metadata_refresh=False))
    assert _status(service)["next_due"]["auth_update"] == _iso(30_300.0)

    clock.now = 30_300.0
    await service._run_due(_settings(metadata_refresh=False))
    assert _status(service)["next_due"]["auth_update"] == _iso(30_900.0)

    clock.now = 30_900.0
    await service._run_due(_settings(metadata_refresh=False))
    last_third = _status(service)["last_results"]["auth_update"]
    assert last_third["ok"] is False
    assert last_third["reason"] == "partial"
    assert last_third["failed_accounts"] == ["acc_b"]
    assert last_third["cycle_exhausted"] is True
    assert _status(service)["next_due"]["auth_update"] == _iso(30_900.0 + 86_400.0)
    assert service._auth_partial_failures == 0
    assert slot_a_session.calls == 1
    assert slot_b_session.calls == 3

    # At t=32_100 (where a 4th 1200s retry would have occurred), no idle wakeup runs.
    rotated_calls.clear()
    clock.now = 32_100.0
    await service._run_due(_settings(metadata_refresh=False))
    assert slot_a_session.calls == 1
    assert slot_b_session.calls == 3
    assert rotated_calls == []
    assert _status(service)["next_due"]["auth_update"] == _iso(30_900.0 + 86_400.0)

    # Next 24h cycle resumes attempting both accounts.
    clock.now = 30_900.0 + 86_400.0
    await service._run_due(_settings(metadata_refresh=False))
    assert slot_a_session.calls == 2
    assert slot_b_session.calls == 4


@pytest.mark.asyncio
async def test_auth_update_manual_trigger_runs_all_accounts_within_24h_window(
    tmp_path, monkeypatch
):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    _account(store, "acc_b", "b@example.com")
    slot_a_session = FakeSlotSession(saved=True)
    slot_b_session = FakeSlotSession(saved=True)
    manager = FakeManager(
        [
            FakeSlot("slot-a", "acc_a", session=slot_a_session),
            FakeSlot("slot-b", "acc_b", session=slot_b_session),
        ]
    )
    account_service = AccountService(store)
    rotated_calls: list[str] = []

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del session
        rotated_calls.append(account_id)
        return {"ok": True}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    clock = FakeClock(40_000.0)
    service = _service(
        store, manager=manager, account_service=account_service, clock=clock
    )
    service._next_due = {
        "auth_update": 40_000.0,
        "metadata_refresh": 10_000_000.0,
        "active_activity": 10_000_000.0,
    }

    await service._run_due(_settings(metadata_refresh=False))
    assert slot_a_session.calls == 1
    assert slot_b_session.calls == 1
    assert rotated_calls == ["acc_a", "acc_b"]

    # Within the 24h window, schedule trigger skips already-saved accounts,
    # while manual trigger must still run all accounts regardless of _auth_saved_at.
    rotated_calls.clear()
    clock.now = 41_000.0
    scheduled_res = await service.run_now(
        "auth_update", trigger="schedule", settings=_settings(metadata_refresh=False)
    )
    assert scheduled_res["count"] == 0
    assert scheduled_res["accounts"] == []
    assert slot_a_session.calls == 1
    assert slot_b_session.calls == 1
    assert rotated_calls == []

    manual_res = await service.run_now(
        "auth_update", trigger="manual", settings=_settings(metadata_refresh=False)
    )
    assert manual_res["ok"] is True
    assert manual_res["reason"] == "saved"
    assert manual_res["count"] == 2
    assert manual_res["accounts"] == ["acc_a", "acc_b"]
    assert slot_a_session.calls == 2
    assert slot_b_session.calls == 2
    assert rotated_calls == ["acc_a", "acc_b"]


@pytest.mark.asyncio
async def test_auth_update_manual_and_api_failures_do_not_consume_schedule_retries(
    tmp_path, monkeypatch
):
    store = AccountStore(tmp_path)
    _account(store, "acc_a", "a@example.com")
    slot_a_session = FakeSlotSession(saved=False)
    manager = FakeManager([FakeSlot("slot-a", "acc_a", session=slot_a_session)])
    account_service = AccountService(store)

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del account_id, session
        return {"ok": False, "status": 500}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    clock = FakeClock(50_000.0)
    service = _service(
        store, manager=manager, account_service=account_service, clock=clock
    )

    for trig in ("manual", "api", "manual"):
        res = await service.run_now("auth_update", trigger=trig, settings=_settings())
        assert res["ok"] is False
        assert res["reason"] == "partial"
        assert "cycle_exhausted" not in res
    assert service._account_auth_failures == {}
    assert "acc_a" not in service._auth_saved_at

    # Schedule still gets full 3 consecutive attempts before circuit-breaking.
    for expected_failures in (1, 2):
        res = await service.run_now(
            "auth_update", trigger="schedule", settings=_settings()
        )
        assert res["ok"] is False
        assert "cycle_exhausted" not in res
        assert service._account_auth_failures == {"acc_a": expected_failures}

    # A manual success in between clears failure count and records _auth_saved_at.
    slot_a_session._saved = True
    manual_ok = await service.run_now(
        "auth_update", trigger="manual", settings=_settings()
    )
    assert manual_ok["ok"] is True
    assert service._account_auth_failures == {}
    assert service._auth_saved_at["acc_a"] == 50_000.0


def test_retry_delay_caps_exponent_for_auth_partial_failures(tmp_path):
    store = AccountStore(tmp_path)
    service = _service(store)
    service._auth_partial_failures = 1_000
    delay = service._retry_delay(
        "auth_update", {"ok": False, "reason": "partial"}, 86_400.0
    )
    assert delay == 86_400.0
    assert service._auth_partial_failures == 1_001


class StartupCaptureSession(FakeSlotSession):
    def __init__(self, state: dict[str, object]) -> None:
        super().__init__(saved=True)
        self._state = state
        self.fetch_calls = 0

    async def fetch_worker_state(self) -> dict[str, object]:
        self.fetch_calls += 1
        return dict(self._state)


@pytest.mark.asyncio
async def test_auth_update_consumes_signer_startup_capture_once(tmp_path, monkeypatch):
    store = AccountStore(tmp_path)
    _account(store, "acc_cold", "cold@example.com")
    state: dict[str, object] = {
        "accountKey": "acc_cold",
        "visitId": "v1_cold",
        "capturedAt": "2026-11-20T08:00:00Z",
        "startupTierRaw": "[2]",
        "startupModelsRaw": '[["ok"]]',
    }
    session = StartupCaptureSession(state)
    manager = FakeManager([FakeSlot("slot-1", "acc_cold", session=session)])
    account_service = AccountService(store)

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del account_id, session
        return {"ok": True}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    from aistudio_api.infrastructure.gateway import model_discovery as discovery_module

    accepted: list[tuple[object, str]] = []

    def fake_accept(raw: object, source: str = "live_signer") -> bool:
        accepted.append((raw, source))
        return True

    monkeypatch.setattr(
        discovery_module.model_discovery, "accept_startup_models", fake_accept
    )

    service = _service(
        store,
        manager=manager,
        account_service=account_service,
        clock=FakeClock(4_000.0),
    )
    await service.run_now(
        "auth_update",
        trigger="manual",
        settings=_settings(metadata_refresh=False),
    )
    await service.run_now(
        "auth_update",
        trigger="manual",
        settings=_settings(metadata_refresh=False),
    )

    assert session.fetch_calls == 2
    assert accepted == [(state["startupModelsRaw"], "live_signer")]
    account = store.get_account("acc_cold")
    assert account is not None
    assert account.tier == "Ultra"
    assert account.tier_checked_at == _iso(4_000.0)


@pytest.mark.asyncio
async def test_auth_update_ignores_unparseable_startup_tier(tmp_path, monkeypatch):
    store = AccountStore(tmp_path)
    _account(store, "acc_cold", "cold@example.com")
    state: dict[str, object] = {
        "capturedAt": "2026-11-20T09:00:00Z",
        "startupTierRaw": "not-json",
        "startupModelsRaw": "",
    }
    session = StartupCaptureSession(state)
    manager = FakeManager([FakeSlot("slot-1", "acc_cold", session=session)])
    account_service = AccountService(store)

    async def fake_rotate(account_id: str, session: object = None) -> dict[str, object]:
        del account_id, session
        return {"ok": True}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    service = _service(
        store,
        manager=manager,
        account_service=account_service,
        clock=FakeClock(4_000.0),
    )

    await service.run_now(
        "auth_update",
        trigger="manual",
        settings=_settings(metadata_refresh=False),
    )

    account = store.get_account("acc_cold")
    assert account is not None
    assert account.tier == "Free"


class FirstNLeaseResolver:
    """External manager double: ok for the first N leases, then no_capacity."""

    def __init__(self, ok_count: int, *, retry_after_s: float = 30.0) -> None:
        self.ok_count = ok_count
        self.retry_after_s = retry_after_s
        self.calls: list[str] = []

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
        del lane, cls, calls, kind, use_cache
        self.calls.append(account_id)
        if len(self.calls) > self.ok_count:
            return LeaseResult(
                "no_capacity",
                retry_after_s=self.retry_after_s,
                reason="no_capacity",
            )
        return LeaseResult(
            "ok",
            exit_id="exit-1",
            socks_url="socks5h://127.0.0.1:40001",
            generation=1,
            ttl_s=60,
            budget_left=10,
        )

    async def resolve(self, account_id: str, *, kind: str = "chat") -> str | None:
        del account_id, kind
        return "socks5h://127.0.0.1:40001"


class TierOkTransport:
    def __init__(self) -> None:
        self.posts = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: str = "",
    ) -> TransportResponse:
        del url, headers, content
        if method == "POST":
            self.posts += 1
            return TransportResponse(status=200, body="[1]")
        return TransportResponse(status=200, body="[]")


def _stale_accounts(store: AccountStore, count: int) -> list[AccountMeta]:
    return [
        _account(store, f"acc{i:02d}", f"acc{i:02d}@example.com")
        for i in range(1, count + 1)
    ]


@pytest.mark.asyncio
async def test_metadata_refresh_batches_and_stops_on_consecutive_no_capacity(
    tmp_path, caplog
):
    store = AccountStore(tmp_path)
    accounts = _stale_accounts(store, 43)
    resolver = FirstNLeaseResolver(ok_count=3)
    refresher = AccountInfoRefresher(
        store,
        transport=TierOkTransport(),
        exit_resolver=resolver,
        pacing_seconds=0.0,
    )
    service = _service(store, refresher=refresher, clock=FakeClock(4_000.0))

    with caplog.at_level("WARNING"):
        result = await service.run_now("metadata_refresh", settings=_settings())

    # 3 ok then a 3-account no_capacity chunk: the round stops early.
    assert resolver.calls == [f"acc{i:02d}" for i in range(1, 7)]
    assert result["attempted"] == 6
    assert result["succeeded"] == 3
    assert result["deferred"] == 3
    assert result["no_capacity"] == 3

    for account in accounts[:3]:
        after = store.get_account(account.id)
        assert after is not None and after.tier_checked_at is not None
    for account in accounts[3:]:
        after = store.get_account(account.id)
        assert after is not None and after.tier_checked_at is None

    summaries = [
        record
        for record in caplog.records
        if record.levelname == "WARNING"
        and "[Keepalive] metadata refresh capacity summary" in record.getMessage()
    ]
    assert len(summaries) == 1
    assert not any(
        "tier/邮箱探测延迟" in record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING"
    )

    # The next round resumes with the remaining due accounts (acc01..03 are
    # now fresh) instead of restarting from the first account.
    resolver.calls.clear()
    await service.run_now("metadata_refresh", settings=_settings())
    assert resolver.calls[:3] == ["acc04", "acc05", "acc06"]
    assert "acc01" not in resolver.calls


@pytest.mark.asyncio
async def test_off_mode_metadata_refresh_still_attempts_every_account(tmp_path):
    store = AccountStore(tmp_path)
    accounts = _stale_accounts(store, 43)
    refresher = FakeRefresher()
    service = _service(store, refresher=refresher, clock=FakeClock(4_000.0))

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["attempted"] == 43
    assert refresher.calls == [[account.id for account in accounts]]


@pytest.mark.asyncio
async def test_auth_update_batches_and_leaves_remaining_accounts_due(
    tmp_path, monkeypatch
):
    store = AccountStore(tmp_path)
    accounts = _stale_accounts(store, 20)
    refresher = AccountInfoRefresher(
        store,
        transport=TierOkTransport(),
        exit_resolver=FirstNLeaseResolver(ok_count=100),
        pacing_seconds=0.0,
    )
    account_service = AccountService(store)
    rotated: list[str] = []

    async def fake_rotate(
        account_id: str, session: object = None, *, client: object = None
    ) -> dict[str, object]:
        del session, client
        rotated.append(account_id)
        if len(rotated) > 3:
            return {"ok": False, "status": 0, "updated": [], "deferred": True}
        return {"ok": True, "status": 200, "updated": ["SID"]}

    monkeypatch.setattr(account_service, "rotate_account_cookies", fake_rotate)
    service = _service(
        store,
        refresher=refresher,
        account_service=account_service,
        clock=FakeClock(4_000.0),
    )

    result = await service.run_now("auth_update", settings=_settings())

    assert rotated == [f"acc{i:02d}" for i in range(1, 7)]
    assert result["deferred"] is True
    assert result["deferred_accounts"] == ["acc04", "acc05", "acc06"]
    for account in accounts[:3]:
        after = store.get_account(account.id)
        assert after is not None and after.auth_saved_at is not None
    for account in accounts[6:]:
        after = store.get_account(account.id)
        assert after is not None and after.auth_saved_at is None
