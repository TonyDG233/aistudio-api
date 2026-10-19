"""Behavior contracts for the WP5 phase-2 routing pipeline."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.application.routing_safety import CircuitBreaker
from aistudio_api.application.surface_orchestrator import SurfacePolicyError


@dataclass
class Account:
    id: str
    name: str = ""
    tier: str | None = "free"
    email: str | None = None
    expired: bool = False


class FakeStore:
    def __init__(self, accounts: list[Account], active_id: str | None = None):
        self.accounts = accounts
        self.active_id = active_id or accounts[0].id
        self.routing: dict[str, dict[str, object]] = {}

    def list_accounts(self) -> list[Account]:
        return list(self.accounts)

    def get_active_account(self) -> Account | None:
        return next((a for a in self.accounts if a.id == self.active_id), None)

    def get_account(self, account_id: str) -> Account | None:
        return next((a for a in self.accounts if a.id == account_id), None)

    def get_account_routing(self, account_id: str) -> dict[str, object]:
        return dict(self.routing.get(account_id, {}))


class _Stats:
    def __init__(self, requests: int = 0, last_used: float = 0.0):
        self.requests = requests
        self.last_used = last_used


class FakeRotator:
    def __init__(self):
        self.unavailable: set[str] = set()
        self.stats: dict[str, _Stats] = {}

    def is_account_available(self, account_id, model=None, *, surface="chat"):
        return account_id not in self.unavailable

    def get_account_stats(self, account_id):
        return self.stats.get(account_id)


class _Page:
    def is_closed(self) -> bool:
        return False


class _Context:
    page = _Page()


class _Session:
    active_context = _Context()
    in_flight_count = 0


class FakeClient:
    session = _Session()
    in_flight_count = 0

    def _build_surface_page_alive(self) -> bool:
        return False


class _EmptySession:
    active_context = None
    in_flight_count = 0


class FakeClientNoPage(FakeClient):
    session = _EmptySession()


def features(**overrides):
    base: dict[str, object] = {
        "build_surface": False,
        "route_by_tier": False,
        "channels": {
            "chat": {"enabled": True, "max_instances": 2},
            "build": {"enabled": False, "max_instances": 2},
            "official": {"enabled": False, "max_instances": 0},
        },
        "routing": {
            "max_switches": 2,
            "global_max_instances": 2,
            "max_realms": 2,
            "realm_mode": True,
            "borrow": False,
            "prewarm": False,
            "circuit_breaker": {
                "window_seconds": 600,
                "failure_threshold": 5,
                "cooldown_seconds": 300,
            },
        },
        "slots": [],
        "groups": [],
    }
    base.update(overrides)
    return base


def pipeline(
    store: FakeStore,
    cfg: dict[str, object],
    rotator=None,
    client=None,
):
    return RoutingPipeline(
        client or FakeClient(),
        account_store=store,
        features_provider=lambda: cfg,
        rotator=rotator or FakeRotator(),
        breaker=CircuitBreaker(clock=lambda: 1000.0),
        clock=lambda: 1000.0,
    )


def test_zero_config_single_account_routes_chat() -> None:
    store = FakeStore([Account("acc_a")])
    ctx = pipeline(store, features()).begin(
        model_path="gemini-3.8-flash", api_format="gemini"
    )
    assert ctx.channel == "chat"
    assert ctx.surface == "chat"
    assert ctx.group.source == "implicit-global"
    assert [c.account_id for c in ctx.candidates] == ["acc_a"]
    assert ctx.multi_mode is False


def test_explicit_group_limits_candidate_pool() -> None:
    groups = [
        {
            "name": "pro",
            "enabled": True,
            "match_tier": ["pro"],
            "allowed_surfaces": ["chat"],
            "max_page_instances": 2,
        },
        {
            "name": "free",
            "enabled": True,
            "match_tier": ["free"],
            "allowed_surfaces": ["build"],
            "max_page_instances": 2,
        },
    ]
    store = FakeStore(
        [Account("acc_pro", "pro", tier="pro"), Account("acc_free", "free")],
        active_id="acc_pro",
    )
    cfg = features(
        route_by_tier=True,
        build_surface=True,
        groups=groups,
        channels={
            "chat": {"enabled": True, "max_instances": 2},
            "build": {"enabled": True, "max_instances": 2},
            "official": {"enabled": False, "max_instances": 0},
        },
    )
    ctx = pipeline(store, cfg).begin(model_path="gemini-3.8-flash", api_format="gemini")
    assert ctx.surface == "chat"
    assert ctx.group.name == "pro"
    assert [c.account_id for c in ctx.candidates] == ["acc_pro"]


def test_chat_only_prefix_to_build_is_explicit_model_guard() -> None:
    store = FakeStore([Account("acc_a")])
    cfg = features(
        build_surface=True,
        channels={
            "chat": {"enabled": True, "max_instances": 2},
            "build": {"enabled": True, "max_instances": 2},
            "official": {"enabled": False, "max_instances": 0},
        },
    )
    with pytest.raises(SurfacePolicyError) as caught:
        pipeline(store, cfg).begin(
            model_path="build/gemini-4-argon", api_format="gemini"
        )
    assert caught.value.code == "surface_model_guard"
    assert caught.value.http_status == 403


def test_account_routing_narrows_candidate_pool() -> None:
    store = FakeStore([Account("acc_a", "a"), Account("acc_b", "b")], active_id="acc_a")
    store.routing["acc_b"] = {"surfaces": ["build"]}
    ctx = pipeline(store, features()).begin(
        model_path="chat/gemini-3.8-flash", api_format="openai"
    )
    assert [c.account_id for c in ctx.candidates] == ["acc_a"]


def test_slot_rows_are_preferred_after_active_account() -> None:
    store = FakeStore(
        [Account("acc_a", "a"), Account("acc_b", "b"), Account("acc_c", "c")],
        active_id="acc_a",
    )
    cfg = features(
        slots=[
            {"account_id": "acc_b", "channel": "chat", "enabled": True},
            {"account_id": "acc_c", "channel": "chat", "enabled": True},
        ]
    )
    ctx = pipeline(store, cfg).begin(model_path="gemini-3.8-flash", api_format="gemini")
    assert [c.account_id for c in ctx.candidates] == ["acc_a", "acc_b", "acc_c"]
    assert ctx.candidates[1].slot_index == 0


def test_slot_rows_trigger_multi_mode() -> None:
    store = FakeStore([Account("acc_a")])
    cfg = features(
        slots=[
            {"account_id": "acc_a", "channel": "chat", "enabled": True},
            {"account_id": "acc_a", "channel": "build", "enabled": True},
        ]
    )
    ctx = pipeline(store, cfg).begin(model_path="gemini-3.8-flash", api_format="gemini")
    assert ctx.multi_mode is True


def test_budget_exhaustion_raises_quota_after_two_defers() -> None:
    store = FakeStore(
        [Account("acc_a", "a"), Account("acc_b", "b"), Account("acc_c", "c")],
        active_id="acc_a",
    )
    cfg = features(
        channels={
            "chat": {"enabled": True, "max_instances": 0},
            "build": {"enabled": False, "max_instances": 2},
            "official": {"enabled": False, "max_instances": 0},
        }
    )
    pipe = pipeline(store, cfg, client=FakeClientNoPage())
    ctx = pipe.begin(model_path="gemini-3.8-flash", api_format="gemini")
    with pytest.raises(SurfacePolicyError) as caught:
        pipe.next_candidate(ctx)
    assert caught.value.code == "surface_quota_exceeded"
    assert ctx.budget.snapshot()["defers"] == 2


def test_switch_budget_caps_account_switches() -> None:
    store = FakeStore([Account("acc_a")])
    pipe = pipeline(store, features())
    ctx = pipe.begin(model_path="gemini-3.8-flash", api_format="gemini")
    pipe.record_switch(ctx, "auth")
    pipe.record_switch(ctx, "rate_limited")
    with pytest.raises(SurfacePolicyError) as caught:
        pipe.record_switch(ctx, "again")
    assert caught.value.code == "surface_switch_exhausted"
    assert caught.value.http_status == 503


def test_retry_does_not_consume_switch_budget() -> None:
    store = FakeStore([Account("acc_a")])
    pipe = pipeline(store, features())
    ctx = pipe.begin(model_path="gemini-3.8-flash", api_format="gemini")
    pipe.record_retry(ctx, "transient")
    pipe.record_retry(ctx, "empty")
    snapshot = ctx.budget.snapshot()
    assert snapshot["switches"] == 0
    assert snapshot["retries"] == 2


def test_open_circuit_rejects_switch_before_budget() -> None:
    breaker = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=300, clock=lambda: 1000.0
    )
    store = FakeStore([Account("acc_a")])
    pipe = RoutingPipeline(
        FakeClient(),
        account_store=store,
        features_provider=lambda: features(),
        rotator=FakeRotator(),
        breaker=breaker,
        clock=lambda: 1000.0,
    )
    ctx = pipe.begin(model_path="gemini-3.8-flash", api_format="gemini")
    pipe.record_switch_failure(ctx)
    pipe.record_switch_failure(ctx)
    with pytest.raises(SurfacePolicyError) as caught:
        pipe.record_switch(ctx, "auth")
    assert caught.value.code == "surface_switch_exhausted"


def test_no_capable_account_is_explicit_403() -> None:
    store = FakeStore([Account("acc_a", "a", tier="free")])
    cfg = features(route_by_tier=True, build_surface=False)
    # free default-tier group only allows build, but build channel is disabled.
    with pytest.raises(SurfacePolicyError) as caught:
        pipeline(store, cfg).begin(model_path="gemini-3.8-flash", api_format="gemini")
    assert caught.value.code == "surface_group_disabled"
    assert caught.value.http_status == 403


def test_active_account_is_first_and_cooldown_accounts_sort_last() -> None:
    rotator = FakeRotator()
    rotator.unavailable.add("acc_b")
    rotator.stats["acc_b"] = _Stats(requests=0)
    rotator.stats["acc_c"] = _Stats(requests=100)
    store = FakeStore(
        [Account("acc_a", "a"), Account("acc_b", "b"), Account("acc_c", "c")],
        active_id="acc_a",
    )
    ctx = pipeline(store, features(), rotator).begin(
        model_path="gemini-3.8-flash", api_format="gemini"
    )
    assert [c.account_id for c in ctx.candidates] == ["acc_a", "acc_c", "acc_b"]
