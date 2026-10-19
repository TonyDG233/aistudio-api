"""Behavior contracts for WP5 phase-2 group/capability policy primitives."""

from __future__ import annotations

import pytest

from aistudio_api.application.surface_orchestrator import (
    GroupPolicy,
    SurfacePolicyError,
    account_matches_group,
    group_identity,
    intersect_surfaces,
    select_group,
    surface_capability,
)

GROUPS = [
    {
        "name": "vip",
        "enabled": True,
        "weight": 3.0,
        "match_emails_regex": "^vip@",
        "allowed_surfaces": ["chat", "build"],
        "max_page_instances": 5,
    },
    {
        "name": "pro",
        "enabled": True,
        "weight": 1.0,
        "match_tier": ["pro", "ultra", "plus"],
        "allowed_surfaces": ["chat"],
        "max_page_instances": 2,
    },
    {
        "name": "disabled",
        "enabled": False,
        "match_tier": ["free"],
        "allowed_surfaces": ["build"],
    },
    {
        "name": "empty",
        "enabled": True,
        "match_tier": ["free"],
        "allowed_surfaces": [],
    },
    {
        "name": "free",
        "enabled": True,
        "weight": 2.0,
        "match_tier": ["free"],
        "allowed_surfaces": ["build"],
        "max_page_instances": 3,
    },
]


def test_implicit_global_group_when_route_by_tier_off() -> None:
    policy = select_group("pro", None, GROUPS, route_by_tier=False, build_enabled=True)
    assert policy.source == "implicit-global"
    assert policy.name is None
    assert policy.allowed_surfaces == ("chat", "build")
    assert policy.max_page_instances == 2
    assert policy.enabled is True


def test_explicit_group_first_match_with_weight_and_quota() -> None:
    policy = select_group(
        "free", "vip@example.com", GROUPS, route_by_tier=True, build_enabled=True
    )
    assert policy.name == "vip"
    assert policy.source == "explicit"
    assert policy.weight == 3.0
    assert policy.max_page_instances == 5
    assert policy.allowed_surfaces == ("chat", "build")


def test_disabled_and_empty_groups_are_skipped() -> None:
    # disabled group is ignored, empty group is ignored, next matching group wins.
    policy = select_group("free", None, GROUPS, route_by_tier=True, build_enabled=True)
    assert policy.name == "free"
    assert policy.allowed_surfaces == ("build",)


def test_unmatched_account_falls_back_to_tier_default() -> None:
    free = select_group("free", None, [], route_by_tier=True, build_enabled=True)
    assert free.source == "default-tier"
    assert free.tier_key == "free"
    assert free.allowed_surfaces == ("build",)
    assert free.enabled is True

    pro = select_group("pro", None, [], route_by_tier=True, build_enabled=True)
    assert pro.allowed_surfaces == ("chat",)


def test_default_tier_free_disabled_when_build_channel_off() -> None:
    policy = select_group("free", None, [], route_by_tier=True, build_enabled=False)
    assert policy.allowed_surfaces == ()
    assert policy.enabled is False


def test_surface_capability_chat_only_and_free_capable() -> None:
    assert surface_capability("models/gemini-4-argon", ("chat", "build")) == ("chat",)
    assert surface_capability("models/gemini-3.8-flash", ("chat", "build")) == (
        "chat",
        "build",
    )
    assert surface_capability("models/gemini-4-argon", ("build",)) == ()


def test_intersect_surfaces_never_widens() -> None:
    policy = GroupPolicy(
        name="pro",
        source="explicit",
        allowed_surfaces=("chat",),
        max_page_instances=2,
        weight=1.0,
        enabled=True,
    )
    assert intersect_surfaces(("chat", "build"), policy) == ("chat",)
    assert intersect_surfaces(("build",), policy) == ()
    assert intersect_surfaces(("chat", "build"), policy, ("build",)) == ()


def test_account_membership_for_each_group_source() -> None:
    explicit = select_group("pro", None, GROUPS, route_by_tier=True, build_enabled=True)
    assert account_matches_group(explicit, "ultra", None, GROUPS) is True
    assert account_matches_group(explicit, "free", None, GROUPS) is False

    default = select_group("free", None, [], route_by_tier=True, build_enabled=True)
    assert account_matches_group(default, "free", None, GROUPS) is True
    assert account_matches_group(default, "pro", None, GROUPS) is False

    implicit = select_group(
        "pro", None, GROUPS, route_by_tier=False, build_enabled=True
    )
    assert account_matches_group(implicit, "free", None, GROUPS) is True


def test_group_identity_is_stable() -> None:
    first = select_group("pro", None, GROUPS, route_by_tier=True, build_enabled=True)
    second = select_group("pro", None, GROUPS, route_by_tier=True, build_enabled=True)
    assert group_identity(first) == group_identity(second) == ("pro", "explicit", None)


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("surface_no_capable_account", 403),
        ("surface_channel_disabled", 403),
        ("surface_model_guard", 403),
        ("surface_quota_exceeded", 429),
        ("surface_switch_exhausted", 503),
        ("surface_instance_not_ready", 503),
    ],
)
def test_new_policy_error_status_mapping(code: str, status: int) -> None:
    err = SurfacePolicyError(code, message="m", advice="a")
    assert err.http_status == status
    assert err.status_code == status
    assert err.to_error_body()["error"]["advice"] == "a"  # type: ignore[index]
