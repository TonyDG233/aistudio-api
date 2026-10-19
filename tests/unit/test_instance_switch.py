"""Behavior contracts for WP5 v3 idle-slot rebalancing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.account_routing import normalize_account_routing
from aistudio_api.application.instance_switch import (
    InstanceSwitcher,
    SlotView,
    plan_rebalance,
)
from aistudio_api.application.routing_eligibility import AccountView


def _account(account_id: str, tier: str) -> AccountView:
    return AccountView(
        account_id=account_id,
        tier=tier,
        email=None,
        expired=False,
        routing=normalize_account_routing(None),
    )


def _features() -> dict[str, object]:
    return {
        "route_by_tier": False,
        "channels": {
            "chat": {"enabled": True, "max_pages": 1},
            "build": {"enabled": True, "max_pages": 1},
        },
        "groups": [
            {
                "name": "pro",
                "match_tier": ["pro"],
                "allowed_surfaces": ["chat"],
            },
            {
                "name": "free",
                "match_tier": ["free"],
                "allowed_surfaces": ["build"],
            },
        ],
    }


def test_rebalance_skips_in_flight_and_keeps_eligible_current_account() -> None:
    slots = [
        SlotView("slot-1", "chrome", "pro", "pro-1", 0),
        SlotView("slot-2", "chat", "pro", "free-1", 0),
        SlotView("slot-3", "chat", "pro", "free-2", 1),
    ]
    # slot-1's channel is intentionally invalid; it must be ignored.
    moves = plan_rebalance(
        slots,
        [_account("pro-1", "pro"), _account("free-1", "free")],
        _features(),
    )
    assert [(move.slot_id, move.to_account_id) for move in moves] == [
        ("slot-2", "pro-1")
    ]


def test_most_constrained_slot_gets_scarce_account_first() -> None:
    slots = [
        SlotView("slot-chat", "chat", "pro", "", 0),
        SlotView("slot-build", "build", "free", "", 0),
    ]
    moves = plan_rebalance(
        slots,
        [_account("free-1", "free"), _account("pro-1", "pro")],
        _features(),
    )
    assert {move.slot_id: move.to_account_id for move in moves} == {
        "slot-chat": "pro-1",
        "slot-build": "free-1",
    }


@pytest.mark.asyncio
async def test_switcher_applies_only_planned_idle_moves() -> None:
    class FakePool:
        def __init__(self) -> None:
            self.rebound: list[tuple[str, str]] = []

        def slots(self):
            return [
                SimpleNamespace(
                    slot_id="slot-1",
                    channel_role="chat",
                    group_name="pro",
                    current_account_id="free-1",
                    in_flight=0,
                    chat_ready=lambda: True,
                    build_ready=lambda: False,
                ),
                SimpleNamespace(
                    slot_id="slot-2",
                    channel_role="chat",
                    group_name="pro",
                    current_account_id="pro-1",
                    in_flight=1,
                    chat_ready=lambda: True,
                    build_ready=lambda: False,
                ),
            ]

        async def rebind(self, slot_id, **kwargs) -> None:
            self.rebound.append((slot_id, kwargs["account_id"]))

    class FakeStore:
        def list_accounts(self):
            return [
                SimpleNamespace(
                    id="free-1",
                    name="",
                    tier="free",
                    email=None,
                    expired=False,
                    routing={},
                ),
                SimpleNamespace(
                    id="pro-1",
                    name="",
                    tier="pro",
                    email=None,
                    expired=False,
                    routing={},
                ),
            ]

    class FakeAccountService:
        def get_account_auth_path(self, account_id):
            return f"{account_id}.json"

        def get_account(self, account_id):
            return SimpleNamespace(id=account_id, auth_user="0")

    pool = FakePool()
    switcher = InstanceSwitcher(
        cast("Any", pool),
        FakeStore(),
        FakeAccountService(),
        features_provider=_features,
    )
    result = await switcher.rebalance()
    assert result["moves"] == [
        {"slot_id": "slot-1", "from": "free-1", "to": "pro-1", "channel": "chat"}
    ]
    assert pool.rebound == [("slot-1", "pro-1")]
