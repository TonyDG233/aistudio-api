"""Behavior tests for account keepalive jobs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.account_keepalive import (
    AccountKeepaliveService,
    KeepaliveSettings,
)
from aistudio_api.infrastructure.account.account_info_refresher import (
    RefreshInProgressError,
)
from aistudio_api.infrastructure.account.account_store import (
    AccountMeta,
    AccountStore,
    write_storage_state,
)


def _settings(**overrides: object) -> KeepaliveSettings:
    base: dict[str, object] = {
        "auto_switch": True,
        "auto_switch_interval_hours": 3.0,
        "auth_update": True,
        "auth_update_hours": 24.0,
        "metadata_refresh": True,
        "metadata_refresh_hours": 24.0,
        "metadata_refresh_on_startup": True,
        "active_activity": False,
        "active_activity_seconds": 60.0,
    }
    base.update(overrides)
    return KeepaliveSettings(**base)  # type: ignore[arg-type]


class FakeAccountService:
    def __init__(self, store: AccountStore, active_id: str) -> None:
        self._store = store
        self._active_id = active_id

    def get_active_account(self) -> AccountMeta | None:
        return self._store.get_account(self._active_id)


class FakeClient:
    def __init__(
        self,
        auth_path: str | None = None,
        *,
        in_flight_count: int = 0,
        save_ok: bool = True,
    ) -> None:
        self.in_flight_count = in_flight_count
        self._auth_path = auth_path
        self._save_ok = save_ok

    async def save_current_cookies(self) -> bool:
        if not self._save_ok or self._auth_path is None:
            return False
        write_storage_state(
            self._auth_path,
            cookies=[{"name": "SID", "value": "refreshed"}],
        )
        return True


class FakeRotator:
    def __init__(self, candidate: AccountMeta | None) -> None:
        self._candidate = candidate

    async def get_keepalive_candidate(
        self, current_account_id: str | None = None
    ) -> AccountMeta | None:
        return self._candidate


class FakeRefresher:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def refresh_accounts(
        self, accounts: list[AccountMeta], *, refresh_email: bool = True
    ) -> list[SimpleNamespace]:
        self.calls.append([account.id for account in accounts])
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
        self, accounts: list[AccountMeta], *, refresh_email: bool = True
    ) -> list[SimpleNamespace]:
        raise RefreshInProgressError("already running")


def _service(
    store: AccountStore,
    active_id: str,
    *,
    client: Any,
    rotator: Any,
    refresher: Any,
    switch_cb: Any = None,
) -> AccountKeepaliveService:
    return AccountKeepaliveService(
        store,
        cast(Any, FakeAccountService(store, active_id)),
        cast(Any, rotator),
        cast(Any, client),
        refresher,
        switch_cb=switch_cb,
    )


@pytest.mark.asyncio
async def test_auth_update_writes_auth_and_marks_meta(tmp_path):
    store = AccountStore(tmp_path)
    account = store.save_account(
        "A",
        "a@example.com",
        {"cookies": [{"name": "SID", "value": "old"}]},
        account_id="acc_a",
    )
    store.set_active_account(account.id)
    store.update_account_keepalive(account.id, auth_saved_at=None)

    auth_path = tmp_path / account.id / "auth.json"
    service = _service(
        store,
        account.id,
        client=FakeClient(str(auth_path)),
        rotator=FakeRotator(None),
        refresher=FakeRefresher(),
    )

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is True
    assert (
        json.loads(auth_path.read_text(encoding="utf-8"))["cookies"][0]["value"]
        == "refreshed"
    )
    updated = store.get_account(account.id)
    assert updated is not None
    assert updated.auth_saved_at is not None
    meta = json.loads((tmp_path / account.id / "meta.json").read_text(encoding="utf-8"))
    assert meta["auth_saved_at"] is not None


@pytest.mark.asyncio
async def test_metadata_refresh_only_targets_stale_healthy_accounts(tmp_path):
    store = AccountStore(tmp_path)
    now = datetime.now(UTC)
    fresh = store.save_account(
        "fresh", "fresh@example.com", {"cookies": []}, account_id="acc_fresh"
    )
    stale = store.save_account(
        "stale", "stale@example.com", {"cookies": []}, account_id="acc_stale"
    )
    expired = store.save_account(
        "expired", "expired@example.com", {"cookies": []}, account_id="acc_expired"
    )
    store.update_account_refresh(fresh.id, tier_checked_at=now.isoformat())
    stale_at = (now - timedelta(days=2)).isoformat()
    store.update_account_refresh(stale.id, tier_checked_at=stale_at)
    store.update_account_refresh(expired.id, tier_checked_at=stale_at, expired=True)
    store.set_active_account(fresh.id)

    refresher = FakeRefresher()
    service = _service(
        store,
        fresh.id,
        client=FakeClient(),
        rotator=FakeRotator(None),
        refresher=refresher,
    )

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is True
    assert refresher.calls == [[stale.id]]
    stale_after = store.get_account(stale.id)
    expired_after = store.get_account(expired.id)
    assert stale_after is not None
    assert expired_after is not None
    assert stale_after.last_refresh_attempt_at is not None
    assert expired_after.last_refresh_attempt_at is None


@pytest.mark.asyncio
async def test_metadata_refresh_reports_manual_refresh_in_progress(tmp_path):
    store = AccountStore(tmp_path)
    account = store.save_account(
        "A", "a@example.com", {"cookies": []}, account_id="acc_a"
    )
    store.set_active_account(account.id)

    service = _service(
        store,
        account.id,
        client=FakeClient(),
        rotator=FakeRotator(None),
        refresher=BusyRefresher(),
    )

    result = await service.run_now("metadata_refresh", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "refresh_in_progress"


@pytest.mark.asyncio
async def test_auto_switch_skips_while_chat_or_build_is_in_flight(tmp_path):
    store = AccountStore(tmp_path)
    active = store.save_account(
        "A", "a@example.com", {"cookies": []}, account_id="acc_a"
    )
    candidate = store.save_account(
        "B", "b@example.com", {"cookies": []}, account_id="acc_b"
    )
    store.set_active_account(active.id)
    switched: list[str] = []

    async def switch_cb(account_id: str) -> bool:
        switched.append(account_id)
        return True

    service = _service(
        store,
        active.id,
        client=FakeClient(in_flight_count=1),
        rotator=FakeRotator(candidate),
        refresher=FakeRefresher(),
        switch_cb=switch_cb,
    )

    result = await service.run_now("auto_switch", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "in_flight"
    assert switched == []


@pytest.mark.asyncio
async def test_auto_switch_uses_lru_candidate_when_idle(tmp_path):
    store = AccountStore(tmp_path)
    active = store.save_account(
        "A", "a@example.com", {"cookies": []}, account_id="acc_a"
    )
    candidate = store.save_account(
        "B", "b@example.com", {"cookies": []}, account_id="acc_b"
    )
    store.set_active_account(active.id)
    switched: list[str] = []

    async def switch_cb(account_id: str) -> bool:
        switched.append(account_id)
        return True

    service = _service(
        store,
        active.id,
        client=FakeClient(in_flight_count=0),
        rotator=FakeRotator(candidate),
        refresher=FakeRefresher(),
        switch_cb=switch_cb,
    )

    result = await service.run_now("auto_switch", settings=_settings())

    assert result["ok"] is True
    assert result["to"] == candidate.id
    assert switched == [candidate.id]


@pytest.mark.asyncio
async def test_auto_switch_single_account_does_not_switch(tmp_path):
    store = AccountStore(tmp_path)
    active = store.save_account(
        "A", "a@example.com", {"cookies": []}, account_id="acc_a"
    )
    store.set_active_account(active.id)
    switched: list[str] = []

    async def switch_cb(account_id: str) -> bool:
        switched.append(account_id)
        return True

    service = _service(
        store,
        active.id,
        client=FakeClient(in_flight_count=0),
        rotator=FakeRotator(None),
        refresher=FakeRefresher(),
        switch_cb=switch_cb,
    )

    result = await service.run_now("auto_switch", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "no_candidate"
    assert switched == []


@pytest.mark.asyncio
async def test_auth_update_without_open_page_does_not_mark_saved(tmp_path):
    store = AccountStore(tmp_path)
    account = store.save_account(
        "A", "a@example.com", {"cookies": []}, account_id="acc_a"
    )
    store.set_active_account(account.id)
    store.update_account_keepalive(account.id, auth_saved_at=None)

    service = _service(
        store,
        account.id,
        client=FakeClient(save_ok=False),
        rotator=FakeRotator(None),
        refresher=FakeRefresher(),
    )

    result = await service.run_now("auth_update", settings=_settings())

    assert result["ok"] is False
    assert result["reason"] == "no_active_page"
    updated = store.get_account(account.id)
    assert updated is not None
    assert updated.auth_saved_at is None
