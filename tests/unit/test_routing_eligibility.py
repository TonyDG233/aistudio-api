"""Behavior contracts for the WP5 v3 pure eligibility graph."""

from __future__ import annotations

import pytest

from aistudio_api.application.account_routing import normalize_account_routing
from aistudio_api.application.routing_eligibility import (
    AccountView,
    choose_channel,
    effective_account_pools,
    eligible_accounts,
    resolve_account_group,
)
from aistudio_api.application.surface_orchestrator import SurfacePolicyError


def _features(
    *,
    build: bool = True,
    chat_pool: str | None = None,
    build_pool: str | None = None,
    groups: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "route_by_tier": False,
        "build_surface": build,
        "channels": {
            "chat": {"enabled": True, "max_pages": 1, "quota_pool": chat_pool},
            "build": {"enabled": build, "max_pages": 1, "quota_pool": build_pool},
            "official": {"enabled": False, "max_pages": 0},
        },
        "groups": groups or [],
    }


def _account(
    account_id: str,
    *,
    tier: str | None,
    email: str | None = None,
    expired: bool = False,
    routing: dict[str, object] | None = None,
) -> AccountView:
    return AccountView(
        account_id=account_id,
        tier=tier,
        email=email,
        expired=expired,
        routing=normalize_account_routing(routing),
    )


def _groups() -> list[dict[str, object]]:
    return [
        {
            "name": "pro",
            "match_tier": ["pro", "ultra", "plus"],
            "allowed_surfaces": ["chat"],
            "max_pages": 2,
        },
        {
            "name": "free",
            "match_tier": ["free"],
            "allowed_surfaces": ["build"],
            "max_pages": 2,
        },
    ]


def test_tier_default_quota_pools_are_narrowable() -> None:
    assert effective_account_pools("free", None) == ("free",)
    assert effective_account_pools("pro", None) == ("pro", "free")
    assert effective_account_pools("mystery", None) == ("free", "pro")

    free_narrowed = _account("free-1", tier="free", routing={"quota_pools": ["pro"]})
    pro_narrowed = _account("pro-1", tier="pro", routing={"quota_pools": ["free"]})
    assert effective_account_pools("free", free_narrowed.routing) == ()
    assert effective_account_pools("pro", pro_narrowed.routing) == ("free",)


def test_pro_only_model_requires_paid_account() -> None:
    features = _features()
    accounts = [_account("free-1", tier="free"), _account("pro-1", tier="pro")]

    chat_candidates = eligible_accounts(
        accounts, "chat", "models/gemini-4-argon", features=features
    )
    assert [item.account_id for item in chat_candidates] == ["pro-1"]

    build_candidates = eligible_accounts(
        accounts, "build", "models/gemini-4-argon", features=features
    )
    assert build_candidates == []


def test_groups_are_resolved_per_account_not_per_request() -> None:
    features = _features(groups=_groups())
    accounts = [
        _account("free-1", tier="free"),
        _account("pro-1", tier="pro"),
    ]

    assert (
        resolve_account_group("free", None, features, features["groups"]).name == "free"
    )
    assert (
        resolve_account_group("pro", None, features, features["groups"]).name == "pro"
    )

    # A flash request can use the free account's native build group.
    build = eligible_accounts(
        accounts, "build", "models/gemini-3.8-flash", features=features
    )
    assert [item.account_id for item in build] == ["free-1"]

    # The same flash request can use the pro account's native chat group.
    chat = eligible_accounts(
        accounts, "chat", "models/gemini-3.8-flash", features=features
    )
    assert [item.account_id for item in chat] == ["pro-1"]


def test_channel_quota_pool_rejects_wrong_tier() -> None:
    features = _features(chat_pool="pro", build_pool="free", groups=_groups())
    accounts = [_account("free-1", tier="free"), _account("pro-1", tier="pro")]

    chat = eligible_accounts(
        accounts, "chat", "models/gemini-3.8-flash", features=features
    )
    assert [item.account_id for item in chat] == ["pro-1"]

    build = eligible_accounts(
        accounts, "build", "models/gemini-3.8-flash", features=features
    )
    assert [item.account_id for item in build] == ["free-1"]


def test_group_allowed_quota_pools_only_narrows() -> None:
    groups = [
        {
            "name": "pro-only",
            "match_tier": ["pro"],
            "allowed_surfaces": ["chat"],
            "allowed_quota_pools": ["pro"],
        }
    ]
    features = _features(chat_pool="free", groups=groups)
    pro = _account("pro-1", tier="pro")
    assert eligible_accounts([pro], "chat", "models/flash", features=features) == []


def test_expired_and_surface_pinned_accounts_are_ineligible() -> None:
    features = _features(groups=_groups())
    expired = _account("expired", tier="free", expired=True)
    pinned = _account("pinned", tier="pro", routing={"surfaces": ["build"]})

    assert (
        eligible_accounts([expired], "build", "models/flash", features=features) == []
    )
    assert eligible_accounts([pinned], "chat", "models/flash", features=features) == []


def test_bare_model_prefers_build_when_build_has_candidates() -> None:
    features = _features(groups=_groups())
    accounts = [_account("free-1", tier="free"), _account("pro-1", tier="pro")]

    selection = choose_channel("models/flash", accounts, features)
    assert selection.channel == "build"
    assert selection.explicit is False


def test_bare_model_falls_back_to_chat_when_build_has_no_candidates() -> None:
    groups = [
        {
            "name": "pro",
            "match_tier": ["pro"],
            "allowed_surfaces": ["chat"],
        }
    ]
    features = _features(groups=groups)
    accounts = [_account("pro-1", tier="pro")]

    selection = choose_channel("models/flash", accounts, features)
    assert selection.channel == "chat"
    assert selection.explicit is False


def test_explicit_prefix_is_pinned_and_never_silently_switches() -> None:
    features = _features(groups=_groups())
    accounts = [_account("free-1", tier="free"), _account("pro-1", tier="pro")]

    selection = choose_channel("build/flash", accounts, features)
    assert selection.channel == "build"
    assert selection.explicit is True

    with pytest.raises(SurfacePolicyError) as exc:
        choose_channel("build/argon", accounts, features)
    assert exc.value.code == "surface_model_guard"


def test_no_eligible_account_raises_explicit_error() -> None:
    features = _features(groups=_groups())
    accounts = [_account("free-1", tier="free")]

    with pytest.raises(SurfacePolicyError) as exc:
        choose_channel("pg/argon", accounts, features)
    assert exc.value.code == "surface_no_capable_account"
