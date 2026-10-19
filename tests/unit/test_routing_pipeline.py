"""Behavior contracts for the WP5 v3 request routing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.application.surface_orchestrator import SurfacePolicyError
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstanceCapacityError,
    InstanceLease,
)


@dataclass
class Account:
    id: str
    name: str = ""
    tier: str | None = "free"
    email: str | None = None
    expired: bool = False
    routing: dict[str, object] = field(default_factory=dict)


class FakeStore:
    def __init__(self, accounts: list[Account], active_id: str | None = None):
        self.accounts = accounts
        self.active_id = active_id or accounts[0].id

    def list_accounts(self) -> list[Account]:
        return list(self.accounts)

    def get_active_account(self) -> Account | None:
        return next((a for a in self.accounts if a.id == self.active_id), None)

    def get_account_routing(self, account_id: str) -> dict[str, object]:
        account = next((a for a in self.accounts if a.id == account_id), None)
        return dict(account.routing) if account is not None else {}


class FakeRotator:
    def __init__(self) -> None:
        self.unavailable: set[str] = set()

    def is_account_available(self, account_id, model=None, *, surface="chat"):
        del model, surface
        return account_id not in self.unavailable

    def get_account_stats(self, account_id) -> Any:
        del account_id
        return None


class FakePool:
    def __init__(
        self,
        *,
        capacity_error: bool = False,
        capacity_until_borrow: bool = False,
    ) -> None:
        self.capacity_error = capacity_error
        self.capacity_until_borrow = capacity_until_borrow
        self.acquired: list[dict[str, object]] = []
        self.released: list[str] = []

    def slots(self) -> tuple[Any, ...]:
        return ()

    def page_counts(self):
        return {"chat": 0, "build": 0}

    @property
    def max_contexts(self) -> int:
        return 2

    def snapshot(self):
        return []

    async def acquire(self, **kwargs) -> InstanceLease:
        self.acquired.append(dict(kwargs))
        borrow_accounts = kwargs.get("borrow_accounts")
        if self.capacity_error or (self.capacity_until_borrow and not borrow_accounts):
            raise InstanceCapacityError("pool full")
        account_id = str(kwargs["account_id"])
        if borrow_accounts:
            account_id = sorted(borrow_accounts)[0]
        return InstanceLease(
            account_id=account_id,
            channel=str(kwargs.get("channel") or "chat"),
            surface=str(kwargs.get("surface") or "chat"),
            slot_id="slot-1",
            realm_id="slot-1-g1",
            context_id="ctx-1",
            adapter=cast("Any", SimpleNamespace()),
            is_borrow=bool(borrow_accounts),
            slot_index=0,
        )

    async def release(self, lease: InstanceLease) -> None:
        self.released.append(lease.realm_id)


def _features(
    *,
    groups: list[dict[str, object]] | None = None,
    borrow: bool = False,
) -> dict[str, object]:
    return {
        "build_surface": True,
        "route_by_tier": False,
        "channels": {
            "chat": {"enabled": True, "max_pages": 1, "quota_pool": None},
            "build": {"enabled": True, "max_pages": 1, "quota_pool": None},
            "official": {"enabled": False, "max_pages": 0},
        },
        "routing": {"max_switches": 2, "borrow": borrow},
        "slots": [],
        "groups": groups or [],
    }


def _pipeline(
    monkeypatch,
    accounts: list[Account],
    *,
    features: dict[str, object],
    active_id: str | None = None,
    pool: FakePool | None = None,
) -> tuple[RoutingPipeline, FakePool]:
    fake_pool = pool or FakePool()
    client = SimpleNamespace(instance_pool=fake_pool)
    pipe = RoutingPipeline(
        client,
        account_store=FakeStore(accounts, active_id=active_id),
        features_provider=lambda: features,
        rotator=FakeRotator(),
    )

    def auth_for(account_id: str) -> tuple[str, str]:
        return f"{account_id}.json", "0"

    monkeypatch.setattr(pipe, "_auth_for", auth_for)
    return pipe, fake_pool


def _native_groups() -> list[dict[str, object]]:
    return [
        {
            "name": "pro",
            "match_tier": ["pro", "ultra", "plus"],
            "allowed_surfaces": ["chat"],
        },
        {
            "name": "free",
            "match_tier": ["free"],
            "allowed_surfaces": ["build"],
        },
    ]


def test_pro_request_finds_pro_account_even_when_active_is_free(monkeypatch) -> None:
    accounts = [
        Account("free-1", tier="free"),
        Account("pro-1", tier="pro"),
    ]
    pipe, _ = _pipeline(
        monkeypatch,
        accounts,
        features=_features(groups=_native_groups()),
        active_id="free-1",
    )

    ctx = pipe.begin(model_path="pg/argon", api_format="gemini")
    assert ctx.channel == "chat"
    assert [candidate.account_id for candidate in ctx.candidates] == ["pro-1"]
    assert ctx.candidates[0].group.name == "pro"


def test_bare_model_channel_is_not_decided_by_active_account(monkeypatch) -> None:
    accounts = [
        Account("free-1", tier="free"),
        Account("pro-1", tier="pro"),
    ]
    pipe, _ = _pipeline(
        monkeypatch,
        accounts,
        features=_features(groups=_native_groups()),
        active_id="pro-1",
    )

    ctx = pipe.begin(model_path="models/flash", api_format="gemini")
    assert ctx.channel == "build"
    assert [candidate.account_id for candidate in ctx.candidates] == ["free-1"]


def test_no_pro_account_raises_explicit_403(monkeypatch) -> None:
    pipe, _ = _pipeline(
        monkeypatch,
        [Account("free-1", tier="free")],
        features=_features(groups=_native_groups()),
    )

    with pytest.raises(SurfacePolicyError) as exc:
        pipe.begin(model_path="pg/argon", api_format="gemini")
    assert exc.value.code == "surface_no_capable_account"


def test_groups_are_per_candidate_in_snapshot(monkeypatch) -> None:
    accounts = [
        Account("free-1", tier="free"),
        Account("pro-1", tier="pro"),
    ]
    groups = [
        {
            "name": "pro",
            "match_tier": ["pro"],
            "allowed_surfaces": ["chat", "build"],
        },
        {
            "name": "free",
            "match_tier": ["free"],
            "allowed_surfaces": ["chat", "build"],
        },
    ]
    pipe, _ = _pipeline(
        monkeypatch,
        accounts,
        features=_features(groups=groups),
    )

    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    snapshot = ctx.snapshot()
    assert snapshot["groups"] == ["free", "pro"]
    candidates = cast("list[dict[str, object]]", snapshot["candidates"])
    assert {candidate["group"] for candidate in candidates} == {"free", "pro"}


def test_lifetime_requests_do_not_starve_older_account(monkeypatch) -> None:
    class StatsRotator(FakeRotator):
        def get_account_stats(self, account_id):
            if account_id == "free-1":
                return SimpleNamespace(requests=100, last_used=0)
            return SimpleNamespace(requests=1, last_used=50)

    accounts = [Account("free-1", tier="free"), Account("free-2", tier="free")]
    features = _features(
        groups=[
            {
                "name": "all",
                "match_tier": ["free"],
                "allowed_surfaces": ["chat"],
            }
        ]
    )
    pipe = RoutingPipeline(
        SimpleNamespace(instance_pool=FakePool()),
        account_store=FakeStore(accounts),
        features_provider=lambda: features,
        rotator=StatsRotator(),
    )
    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    assert [candidate.account_id for candidate in ctx.candidates] == [
        "free-1",
        "free-2",
    ]


def test_rotation_fairness_beats_warm_slot_stickiness(monkeypatch) -> None:
    class StatsRotator(FakeRotator):
        def get_account_stats(self, account_id):
            return SimpleNamespace(
                requests=3 if account_id == "free-1" else 0,
                last_used=0,
            )

    accounts = [Account("free-1", tier="free"), Account("free-2", tier="free")]
    features = _features(
        groups=[
            {
                "name": "all",
                "match_tier": ["free"],
                "allowed_surfaces": ["chat"],
            }
        ]
    )
    client = SimpleNamespace(instance_pool=FakePool())

    class WarmPool(FakePool):
        def slots(self):
            return (
                SimpleNamespace(
                    current_account_id="free-1",
                    in_flight=0,
                    chat_ready=lambda: True,
                    build_ready=lambda: False,
                ),
            )

    client.instance_pool = WarmPool()
    pipe = RoutingPipeline(
        client,
        account_store=FakeStore(accounts),
        features_provider=lambda: features,
        rotator=StatsRotator(),
    )
    monkeypatch.setattr(pipe, "_auth_for", lambda account_id: ("a.json", "0"))

    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    assert [candidate.account_id for candidate in ctx.candidates] == [
        "free-2",
        "free-1",
    ]


def test_equal_fairness_prefers_warm_slot(monkeypatch) -> None:
    class StatsRotator(FakeRotator):
        def get_account_stats(self, account_id) -> Any:
            return SimpleNamespace(requests=0, last_used=0)

    accounts = [Account("free-1", tier="free"), Account("free-2", tier="free")]
    features = _features(
        groups=[
            {
                "name": "all",
                "match_tier": ["free"],
                "allowed_surfaces": ["chat"],
            }
        ]
    )
    client = SimpleNamespace(instance_pool=FakePool())

    class WarmPool(FakePool):
        def slots(self):
            return (
                SimpleNamespace(
                    current_account_id="free-2",
                    in_flight=0,
                    chat_ready=lambda: True,
                    build_ready=lambda: False,
                ),
            )

    client.instance_pool = WarmPool()
    pipe = RoutingPipeline(
        client,
        account_store=FakeStore(accounts),
        features_provider=lambda: features,
        rotator=StatsRotator(),
    )
    monkeypatch.setattr(pipe, "_auth_for", lambda account_id: ("a.json", "0"))

    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    assert [candidate.account_id for candidate in ctx.candidates] == [
        "free-2",
        "free-1",
    ]


@pytest.mark.asyncio
async def test_acquire_loads_candidate_account_and_releases(monkeypatch) -> None:
    accounts = [Account("pro-1", tier="pro"), Account("pro-2", tier="pro")]
    pipe, pool = _pipeline(
        monkeypatch,
        accounts,
        features=_features(groups=_native_groups()),
    )
    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    candidate = ctx.next_candidate()
    assert candidate is not None

    lease = await pipe.acquire(ctx, candidate)
    assert pool.acquired[0]["account_id"] == candidate.account_id
    assert lease.account_id == candidate.account_id
    await pipe.release(ctx, lease)
    assert pool.released == [lease.realm_id]


@pytest.mark.asyncio
async def test_capacity_error_maps_to_explicit_429(monkeypatch) -> None:
    pipe, _ = _pipeline(
        monkeypatch,
        [Account("pro-1", tier="pro")],
        features=_features(groups=_native_groups()),
        pool=FakePool(capacity_error=True),
    )
    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    candidate = ctx.next_candidate()
    assert candidate is not None

    with pytest.raises(SurfacePolicyError) as exc:
        await pipe.acquire(ctx, candidate)
    assert exc.value.code == "surface_quota_exceeded"


@pytest.mark.asyncio
async def test_borrow_runs_only_after_primary_capacity_failure(monkeypatch) -> None:
    accounts = [Account("pro-1", tier="pro"), Account("pro-2", tier="pro")]
    groups = [{"name": "pro", "match_tier": ["pro"], "allowed_surfaces": ["chat"]}]
    pipe, pool = _pipeline(
        monkeypatch,
        accounts,
        features=_features(groups=groups, borrow=True),
        pool=FakePool(capacity_until_borrow=True),
    )
    ctx = pipe.begin(model_path="pg/flash", api_format="gemini")
    candidate = ctx.next_candidate()
    assert candidate is not None

    lease = await pipe.acquire(ctx, candidate)
    assert lease.is_borrow is True
    assert len(pool.acquired) == 2
    assert pool.acquired[1]["borrow_accounts"]
