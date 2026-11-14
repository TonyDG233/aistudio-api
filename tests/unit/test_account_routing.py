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


def _stats(rotator: AccountRotator, account_id: str):
    stats = rotator.get_account_stats(account_id)
    assert stats is not None
    return stats


def test_rebind_selection_cycles_through_all_five_accounts(tmp_path) -> None:
    """True LRU: five accounts rotate 1..5 -> 1, never ping-pong on two."""
    from tests.unit.test_instance_manager import _store_with_accounts

    store, accounts = _store_with_accounts(
        tmp_path,
        ("a1", "a1@example.com", "Pro"),
        ("a2", "a2@example.com", "Ultra"),
        ("a3", "a3@example.com", "Pro"),
        ("a4", "a4@example.com", "Ultra"),
        ("a5", "a5@example.com", "Pro"),
    )
    rotator = AccountRotator(store)
    order = ("a1", "a2", "a3", "a4", "a5")
    # Uneven historical totals must no longer dominate last_used.
    for index, name in enumerate(order, start=1):
        stats = _stats(rotator, accounts[name].id)
        stats.last_used = float(index)
        stats.total_requests = 100 - index * 10

    picked: list[str] = []
    for round_no in range(6):
        selected = rotator.select_rebind_account(lane="paid", required_tiers=("Pro",))
        assert selected is not None
        picked.append(selected.name)
        # Simulate one served call: that account moves to the LRU tail.
        _stats(rotator, selected.id).last_used = 100.0 + round_no

    assert picked == ["a1", "a2", "a3", "a4", "a5", "a1"]


def test_rebind_selection_keeps_legacy_order_when_last_used_is_zero(tmp_path) -> None:
    """All-zero LRU clocks fall back to tier rank, then total requests."""
    from tests.unit.test_instance_manager import _store_with_accounts

    store, accounts = _store_with_accounts(
        tmp_path,
        ("pro-busy", "busy@example.com", "Pro"),
        ("pro-idle", "idle@example.com", "Pro"),
        ("ultra", "ultra@example.com", "Ultra"),
    )
    rotator = AccountRotator(store)
    _stats(rotator, accounts["pro-busy"].id).total_requests = 9
    _stats(rotator, accounts["pro-idle"].id).total_requests = 1

    picked = rotator.select_rebind_account(lane="paid", required_tiers=("Pro",))

    assert picked is not None
    assert picked.name == "pro-idle"


def test_rebind_selection_restricts_to_allowed_groups(tmp_path) -> None:
    from aistudio_api.infrastructure.account.account_store import AccountStore

    store = AccountStore(accounts_dir=tmp_path / "accounts")
    alpha_new = store.save_account(
        name="alpha-new",
        email="alpha-new@example.com",
        storage_state={"cookies": []},
        tier="Free",
        group="alpha",
    )
    beta_old = store.save_account(
        name="beta-old",
        email="beta-old@example.com",
        storage_state={"cookies": []},
        tier="Free",
        group="beta",
    )
    alpha_old = store.save_account(
        name="alpha-old",
        email="alpha-old@example.com",
        storage_state={"cookies": []},
        tier="Free",
        group="alpha",
    )
    rotator = AccountRotator(store)
    _stats(rotator, alpha_new.id).last_used = 3.0
    _stats(rotator, beta_old.id).last_used = 0.0
    _stats(rotator, alpha_old.id).last_used = 1.0

    grouped = rotator.select_rebind_account(lane="free", allowed_groups=["alpha"])
    assert grouped is not None
    assert grouped.id == alpha_old.id
    assert grouped.id != beta_old.id

    unrestricted = rotator.select_rebind_account(lane="free")
    assert unrestricted is not None
    assert unrestricted.id == beta_old.id


def test_rebind_selection_respects_per_model_429_cooldown(tmp_path) -> None:
    from tests.unit.test_instance_manager import _store_with_accounts

    store, accounts = _store_with_accounts(
        tmp_path,
        ("free-a", "free-a@example.com", "Free"),
        ("free-b", "free-b@example.com", "Free"),
    )
    rotator = AccountRotator(store)
    a = accounts["free-a"]
    b = accounts["free-b"]
    _stats(rotator, a.id).last_used = 1.0
    _stats(rotator, b.id).last_used = 2.0

    rotator.record_rate_limited(a.id, "gemini-3.7-flash")

    assert rotator.is_account_available(a.id, model="gemini-3.7-flash") is False
    assert rotator.is_account_available(a.id, model="gemini-3.7-pro") is True
    # The just-429'd account is also moved to the LRU tail, never re-picked.
    assert _stats(rotator, a.id).last_used > 2.0

    cooled_model = rotator.select_rebind_account(lane="free", model="gemini-3.7-flash")
    assert cooled_model is not None
    assert cooled_model.id == b.id

    other_model = rotator.select_rebind_account(
        lane="free",
        model="gemini-3.7-pro",
        exclude_account_ids={b.id},
    )
    assert other_model is not None
    assert other_model.id == a.id


def test_pick_account_prefers_least_recently_used(monkeypatch, tmp_path) -> None:
    """InstanceManager startup/manual selection follows the rotator LRU clock."""
    from aistudio_api.application import instance_manager as instance_manager_module
    from tests.unit.test_instance_manager import (
        FakeCDP,
        _free_spec,
        _manager,
        _store_with_accounts,
    )

    store, accounts = _store_with_accounts(
        tmp_path,
        ("a1", "a1@example.com", "Free"),
        ("a2", "a2@example.com", "Free"),
        ("a3", "a3@example.com", "Free"),
    )
    rotator = AccountRotator(store)
    # a1 is the active/first account by order but a3 rested the longest.
    _stats(rotator, accounts["a1"].id).last_used = 2.0
    _stats(rotator, accounts["a2"].id).last_used = 3.0
    _stats(rotator, accounts["a3"].id).last_used = 1.0
    monkeypatch.setattr(instance_manager_module, "_account_rotator", lambda: rotator)

    manager = _manager(store, FakeCDP(), specs=[_free_spec(1)], max_contexts=1)
    active = store.get_account(accounts["a1"].id)
    assert active is not None
    ordered = manager._ordered_accounts(active)

    picked = manager._pick_account(_free_spec(1), ordered, set())

    assert picked is not None
    assert picked.id == accounts["a3"].id
