"""Behavior contracts for four-layer instance budgets and slot planning."""

from __future__ import annotations

from typing import cast

from aistudio_api.application.surface_orchestrator import GroupPolicy
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.instance_manager import (
    BudgetLimits,
    BudgetUsage,
    InstanceManager,
    evaluate_budget,
)


class _Page:
    def __init__(self, closed: bool = False) -> None:
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class _Context:
    def __init__(self, alive: bool = True) -> None:
        self.page = _Page(not alive)


class _Session:
    def __init__(self, alive: bool = True) -> None:
        self.active_context = _Context(alive) if alive else None
        self.in_flight_count = 0


class FakeClient:
    def __init__(self, *, chat_alive: bool = True, build_alive: bool = False):
        self.session = _Session(chat_alive)
        self.in_flight_count = 2
        self._build_alive = build_alive

    def _build_surface_page_alive(self) -> bool:
        return self._build_alive


GROUP = GroupPolicy(
    name="pro",
    source="explicit",
    allowed_surfaces=("chat",),
    max_pages=2,
    weight=1.0,
    enabled=True,
)


def _features(**overrides):
    data: dict[str, object] = {
        "channels": {
            "chat": {"enabled": True, "max_instances": 2},
            "build": {"enabled": True, "max_instances": 2},
            "official": {"enabled": False, "max_instances": 0},
        },
        "routing": {
            "global_max_instances": 2,
            "max_realms": 2,
            "realm_mode": False,
        },
        "slots": [],
    }
    data.update(overrides)
    return data


def manager(client=None, features=None):
    cfg = features or _features()
    return InstanceManager(
        cast("AIStudioClient", client or FakeClient()),
        features_provider=lambda: cfg,
        account_store=object(),
    )


def test_budget_passes_when_no_new_instance_is_needed() -> None:
    assert (
        evaluate_budget(
            BudgetLimits(global_max=0, channel_max=0, group_max=0, realm_max=0),
            BudgetUsage(9, 9, 9, 9),
            need_new=False,
            need_realm=True,
        )
        is None
    )


def test_budget_picks_the_first_exhausted_layer() -> None:
    limits = BudgetLimits(global_max=2, channel_max=1, group_max=4, realm_max=2)
    usage = BudgetUsage(global_live=2, channel_live=1, group_live=4, realm_live=1)
    assert evaluate_budget(limits, usage, need_new=True, need_realm=False) == "global"

    limits = BudgetLimits(global_max=4, channel_max=1, group_max=4, realm_max=2)
    assert evaluate_budget(limits, usage, need_new=True, need_realm=False) == "channel"

    limits = BudgetLimits(global_max=4, channel_max=4, group_max=2, realm_max=2)
    assert evaluate_budget(limits, usage, need_new=True, need_realm=False) == "group"

    limits = BudgetLimits(global_max=4, channel_max=4, group_max=5, realm_max=1)
    assert evaluate_budget(limits, usage, need_new=True, need_realm=True) == "realm"


def test_snapshot_reports_planned_slots_and_limits() -> None:
    cfg = _features(
        slots=[
            {"account_id": "acc_a", "channel": "chat", "enabled": True},
            {"account_id": "acc_a", "channel": "build", "enabled": True},
            {"account_id": "acc_b", "channel": "chat", "enabled": False},
        ]
    )
    snapshot = manager(features=cfg).snapshot()
    assert snapshot["live_instances"] == 1
    assert snapshot["planned_instances"] == 2
    assert snapshot["limits"] == {
        "global_max": 2,
        "chat_max": 2,
        "build_max": 2,
        "max_realms": 1,
    }
    assert snapshot["slots"] == {"acc_a:chat": 0, "acc_a:build": 1}


def test_can_land_defers_at_channel_budget() -> None:
    cfg = _features(
        channels={
            "chat": {"enabled": True, "max_instances": 0},
            "build": {"enabled": True, "max_instances": 2},
            "official": {"enabled": False, "max_instances": 0},
        }
    )
    allowed, reason = manager(
        client=FakeClient(chat_alive=False), features=cfg
    ).can_land("chat", GROUP)
    assert allowed is False
    assert reason == "channel"


def test_can_land_uses_live_chat_page_snapshot() -> None:
    allowed, reason = manager().can_land("chat", GROUP)
    assert allowed is True
    assert reason is None


async def test_land_same_active_account_returns_lease(monkeypatch) -> None:
    class Store:
        def get_active_account(self):
            return type("A", (), {"id": "acc_a"})()

    instance = InstanceManager(
        cast("AIStudioClient", FakeClient()),
        features_provider=lambda: _features(),
        account_store=Store(),
    )
    lease = await instance.land(
        account_id="acc_a", channel="chat", surface="chat", model="m"
    )
    assert lease is not None
    assert lease.account_id == "acc_a"
    assert lease.realm_id == "default"
