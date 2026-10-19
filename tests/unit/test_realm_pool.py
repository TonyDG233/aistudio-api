"""Behavior contracts for lazy cross-account Realm capacity/reuse."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aistudio_api.infrastructure.gateway.realm_pool import (
    RealmCapacityError,
    RealmPool,
    RealmSession,
)


class FakePage:
    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class FakeSession:
    def __init__(self, realm_id: str) -> None:
        self.page = FakePage()
        self._in_flight = 0
        self.active_context = SimpleNamespace(auth_user="0")
        self._realm_id = realm_id

    @property
    def in_flight_count(self) -> int:
        return self._in_flight

    async def close(self) -> None:
        return None


class FakeCDP:
    def __init__(self) -> None:
        self.contexts = 0
        self.disposed: list[str] = []

    async def create_browser_context(self) -> str:
        self.contexts += 1
        return f"ctx-{self.contexts}"

    async def dispose_browser_context(self, context_id: str) -> None:
        self.disposed.append(context_id)


def features(max_realms: int = 1, realm_mode: bool = True) -> dict[str, object]:
    return {
        "routing": {
            "realm_mode": realm_mode,
            "max_realms": max_realms,
        }
    }


async def test_realm_is_reused_per_account(monkeypatch) -> None:
    cdp = FakeCDP()
    created: list[str] = []

    async def fake_bootstrap(
        cls, cdp_client, *, context_id, auth_file, auth_user, realm_id, timeout_s=90.0
    ):
        created.append(realm_id)
        return FakeSession(realm_id)

    monkeypatch.setattr(RealmSession, "bootstrap", classmethod(fake_bootstrap))
    pool = RealmPool(lambda: _return(cdp), features_provider=lambda: features())
    first = await pool.acquire(account_id="acc_a", auth_file="a.json")
    second = await pool.acquire(account_id="acc_a", auth_file="a.json")
    assert first is second
    assert cdp.contexts == 1
    assert created == [first.realm_id]


async def test_capacity_rejects_when_no_idle_realm(monkeypatch) -> None:
    cdp = FakeCDP()

    async def fake_bootstrap(
        cls, cdp_client, *, context_id, auth_file, auth_user, realm_id, timeout_s=90.0
    ):
        return FakeSession(realm_id)

    monkeypatch.setattr(RealmSession, "bootstrap", classmethod(fake_bootstrap))
    pool = RealmPool(lambda: _return(cdp), features_provider=lambda: features(1))
    first = await pool.acquire(account_id="acc_a", auth_file="a.json")
    first.session._in_flight = 1
    with pytest.raises(RealmCapacityError):
        await pool.acquire(account_id="acc_b", auth_file="b.json")


async def test_idle_realm_is_retired_to_make_room(monkeypatch) -> None:
    cdp = FakeCDP()

    async def fake_bootstrap(
        cls, cdp_client, *, context_id, auth_file, auth_user, realm_id, timeout_s=90.0
    ):
        return FakeSession(realm_id)

    monkeypatch.setattr(RealmSession, "bootstrap", classmethod(fake_bootstrap))
    pool = RealmPool(lambda: _return(cdp), features_provider=lambda: features(1))
    first = await pool.acquire(account_id="acc_a", auth_file="a.json")
    second = await pool.acquire(account_id="acc_b", auth_file="b.json")
    assert second.account_id == "acc_b"
    assert first.context_id in cdp.disposed
    assert pool.live_realms == 1


async def test_realm_mode_off_forces_single_realm(monkeypatch) -> None:
    cdp = FakeCDP()

    async def fake_bootstrap(
        cls, cdp_client, *, context_id, auth_file, auth_user, realm_id, timeout_s=90.0
    ):
        return FakeSession(realm_id)

    monkeypatch.setattr(RealmSession, "bootstrap", classmethod(fake_bootstrap))
    pool = RealmPool(
        lambda: _return(cdp),
        features_provider=lambda: features(8, realm_mode=False),
    )
    await pool.acquire(account_id="acc_a", auth_file="a.json")
    second = await pool.acquire(account_id="acc_b", auth_file="b.json")
    assert second.account_id == "acc_b"
    assert pool.live_realms == 1


async def _return(value):
    return value
