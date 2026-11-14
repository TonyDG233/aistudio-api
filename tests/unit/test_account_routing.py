"""D05-006/009/014: Free/Pro lane isolation and instance-level tier gate."""

from __future__ import annotations

import inspect

import pytest

from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.application.routing_eligibility import resolve_model_route
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import TierRequiredError
from aistudio_api.domain.instances import InstanceState
from aistudio_api.domain.model_access import (
    account_is_paid,
    instance_lane_for_tier,
    tier_satisfies_min,
)
from tests.unit.routing_fakes import FakeManager, make_instance


def test_free_and_paid_lanes_are_derived_from_tier() -> None:
    assert instance_lane_for_tier("Free") == "free"
    assert instance_lane_for_tier("Plus") == "paid"
    assert instance_lane_for_tier("Pro") == "paid"
    assert instance_lane_for_tier("Ultra_20X") == "paid"
    assert account_is_paid("Plus") is True


def test_instance_min_tier_cannot_mix_lanes() -> None:
    assert tier_satisfies_min("Pro", "Pro") is True
    assert tier_satisfies_min("Ultra", "Pro") is True
    assert tier_satisfies_min("Free", "Pro") is False
    assert tier_satisfies_min("Plus", "Pro") is False
    assert tier_satisfies_min("Free", "Free") is True


def test_free_model_route_never_targets_paid_lane() -> None:
    route = resolve_model_route("gemini-3.7-flash")
    assert route.lane == "free"


def test_paid_model_route_never_targets_free_lane() -> None:
    route = resolve_model_route("gemini-4-argon")
    assert route.lane == "paid"
    assert route.required_tier == "Pro"


@pytest.mark.asyncio
async def test_free_instance_is_not_used_for_paid_model() -> None:
    manager = FakeManager(
        [
            make_instance("free-inst", account_id="free", tier="Free"),
            make_instance("pro-inst", account_id="pro", tier="Pro"),
        ]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    context = await pipeline.begin(model_path="gemini-4-argon")
    assert [item.instance_id for item in context.candidates] == ["pro-inst"]


@pytest.mark.asyncio
async def test_paid_lane_plus_instance_fails_pro_admission() -> None:
    manager = FakeManager(
        [
            make_instance(
                "plus-inst",
                account_id="plus",
                tier="Plus",
                state=InstanceState.ACTIVE,
            )
        ]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    with pytest.raises(TierRequiredError):
        await pipeline.begin(model_path="gemini-4-argon")


def test_rebind_selection_is_rank_based_and_prefers_minimal_paid_tier(
    tmp_path,
) -> None:
    from tests.unit.test_instance_manager import _store_with_accounts

    store, accounts = _store_with_accounts(
        tmp_path,
        ("pro", "pro@example.com", "Pro"),
        ("ultra", "ultra@example.com", "Ultra"),
    )
    rotator = AccountRotator(store)

    picked = rotator.select_rebind_account(lane="paid", required_tiers=("Pro",))
    assert picked is not None
    assert picked.id == accounts["pro"].id

    # Rank-based requirement: Ultra still satisfies a Pro requirement.
    only_ultra = rotator.select_rebind_account(
        lane="paid",
        required_tiers=("Pro",),
        exclude_account_ids={accounts["pro"].id},
    )
    assert only_ultra is not None
    assert only_ultra.id == accounts["ultra"].id


def test_rebind_selection_obeys_only_tier_interval(tmp_path) -> None:
    from tests.unit.test_instance_manager import _store_with_accounts

    store, accounts = _store_with_accounts(
        tmp_path,
        ("pro", "pro@example.com", "Pro"),
        ("ultra", "ultra@example.com", "Ultra"),
    )
    rotator = AccountRotator(store)

    picked = rotator.select_rebind_account(lane="paid", only_tier="Pro")
    assert picked is not None
    assert picked.id == accounts["pro"].id
    assert (
        rotator.select_rebind_account(
            lane="paid",
            only_tier="Pro",
            exclude_account_ids={accounts["pro"].id},
        )
        is None
    )


def test_rotator_is_auth_locked_tracks_session_expiry(tmp_path) -> None:
    from tests.unit.test_instance_manager import _store_with_accounts

    store, accounts = _store_with_accounts(
        tmp_path,
        ("pro", "pro@example.com", "Pro"),
    )
    rotator = AccountRotator(store)
    locked_id = accounts["pro"].id

    assert rotator.is_auth_locked(locked_id) is False
    rotator.record_session_expired(locked_id)
    assert rotator.is_auth_locked(locked_id) is True


def test_rotator_has_no_group_or_affinity_parameters() -> None:
    signature = inspect.signature(AccountRotator.get_next_account)
    assert "active_groups" not in signature.parameters
    assert "required_tiers" in signature.parameters
    candidates_sig = inspect.signature(AccountRotator._get_eligible_candidates)
    assert "active_groups" not in candidates_sig.parameters
    assert "ignore_auth_cooldown" not in candidates_sig.parameters
