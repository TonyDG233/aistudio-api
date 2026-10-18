"""Unit tests for WP5 surface orchestration (groups / guards / quota / logging)."""

from __future__ import annotations

import logging

import pytest

from aistudio_api.application.surface_orchestrator import (
    DEFAULT_ALLOWED_BY_TIER,
    MAX_PAGE_INSTANCES_DEFAULT,
    SurfaceDecision,
    SurfacePolicyError,
    check_instance_quota,
    is_chat_only_model,
    match_group,
    resolve_surface,
)

GROUPS: list[dict] = [
    {
        "name": "vip",
        "match_emails_regex": [r"@vip\.corp$"],
        "allowed_surfaces": ["chat", "build"],
        "max_page_instances": 5,
    },
    {
        "name": "pro",
        "match_tier": ["pro", "ultra", "plus"],
        "allowed_surfaces": ["chat"],
        "max_page_instances": 2,
    },
    {
        "name": "free",
        "match_tier": ["free"],
        "allowed_surfaces": ["build"],
        "max_page_instances": 3,
    },
]


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AISTUDIO_SURFACE_OVERRIDE", raising=False)


# ---------------------------------------------------------------- constants


def test_default_allowed_by_tier_constant() -> None:
    assert DEFAULT_ALLOWED_BY_TIER == {
        "free": ["build"],
        "pro": ["chat"],
        "ultra": ["chat"],
        "plus": ["chat"],
    }
    assert MAX_PAGE_INSTANCES_DEFAULT == 2


# ---------------------------------------------------------------- match_group


def test_match_group_tier_hit() -> None:
    group = match_group("pro", None, GROUPS)
    assert group is not None
    assert group["name"] == "pro"


def test_match_group_email_regex_hit() -> None:
    group = match_group("free", "user@vip.corp", GROUPS)
    assert group is not None
    assert group["name"] == "vip"


def test_match_group_first_match_wins_in_list_order() -> None:
    groups = [
        {"name": "first", "match_tier": ["free"]},
        {"name": "second", "match_tier": ["free"]},
    ]
    group = match_group("free", None, groups)
    assert group is not None
    assert group["name"] == "first"


def test_match_group_fields_are_or_relationship() -> None:
    groups = [
        {"name": "mixed", "match_tier": ["ultra"], "match_emails_regex": [r"@x\.io$"]},
    ]
    # tier does NOT match but email does -> the group still matches.
    group = match_group("free", "a@x.io", groups)
    assert group is not None
    assert group["name"] == "mixed"


def test_match_group_no_match_returns_none() -> None:
    assert match_group("enterprise", "nobody@example.com", GROUPS) is None


def test_match_group_invalid_regex_is_ignored() -> None:
    groups = [{"name": "bad", "match_emails_regex": ["("]}]
    assert match_group("free", "user@vip.corp", groups) is None


def test_match_group_accepts_normalized_single_string_regex() -> None:
    # features.groups normalization emits a single string (or None), not a list.
    groups = [{"name": "corp", "match_emails_regex": r"@corp$", "match_tier": []}]
    group = match_group("free", "a@corp", groups)
    assert group is not None
    assert group["name"] == "corp"
    assert match_group("free", "a@other.io", groups) is None


# ------------------------------------------------------------ chat-only model


@pytest.mark.parametrize(
    "model",
    [
        "models/argon",
        "models/argon-2-preview",
        "argon",
        "models/gemini-2.5-pro",
        "models/gemini-3.1-pro",
        "models/gemini-3.1-pro-preview",
    ],
)
def test_is_chat_only_model_true_samples(model: str) -> None:
    assert is_chat_only_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "",
        "models/gemini-2.0-flash",
        "models/gemini-2.5-flash",
        "models/gemini-3.8-flash",
        "models/gemini-2.5-proxy",
        "models/gemini-3.1-professional",
    ],
)
def test_is_chat_only_model_false_samples(model: str) -> None:
    assert is_chat_only_model(model) is False


# --------------------------------------------------------- resolve_surface


@pytest.mark.parametrize(
    ("override", "expected"),
    [("build", "build"), ("CHAT", "chat")],
)
def test_resolve_override_beats_route_by_tier(
    monkeypatch: pytest.MonkeyPatch, override: str, expected: str
) -> None:
    monkeypatch.setenv("AISTUDIO_SURFACE_OVERRIDE", override)
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        "pro",
        "user@vip.corp",
        {"route_by_tier": True, "build_surface": False, "groups": GROUPS},
    )
    assert isinstance(decision, SurfaceDecision)
    assert decision.surface == expected
    assert decision.group_source == "override"
    assert decision.group_name is None
    assert decision.allowed == (expected,)


@pytest.mark.parametrize(
    ("tier", "build_surface", "expected"),
    [
        ("free", True, "build"),
        ("pro", True, "chat"),
        ("free", False, "chat"),
        (None, True, "chat"),
    ],
)
def test_resolve_route_by_tier_off_legacy_behavior(
    tier: str | None, build_surface: bool, expected: str
) -> None:
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        tier,
        None,
        {"route_by_tier": False, "build_surface": build_surface},
    )
    assert decision.surface == expected
    assert decision.group_source == "default-tier"
    assert decision.group_name is None


def test_resolve_default_tier_group_free_routes_build() -> None:
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        "free",
        None,
        {"route_by_tier": True, "build_surface": True, "groups": []},
    )
    assert decision.surface == "build"
    assert decision.group_source == "default-tier"
    assert decision.allowed == ("build",)


def test_resolve_default_tier_group_unknown_tier_routes_chat() -> None:
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        "enterprise",
        None,
        {"route_by_tier": True, "build_surface": True, "groups": []},
    )
    assert decision.surface == "chat"
    assert decision.allowed == ("chat",)


def test_resolve_explicit_group_by_email_regex() -> None:
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        "free",
        "user@vip.corp",
        {"route_by_tier": True, "build_surface": True, "groups": GROUPS},
    )
    assert decision.surface == "build"  # free tier prefers build, both allowed
    assert decision.group_name == "vip"
    assert decision.group_source == "explicit"
    assert set(decision.allowed) == {"chat", "build"}


def test_resolve_multi_surface_prefers_tier_default() -> None:
    groups = [
        {"name": "both", "match_tier": ["pro"], "allowed_surfaces": ["chat", "build"]}
    ]
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        "pro",
        None,
        {"route_by_tier": True, "build_surface": True, "groups": groups},
    )
    assert decision.surface == "chat"
    assert decision.allowed == ("chat", "build")


def test_resolve_multi_surface_unknown_tier_prefers_chat() -> None:
    # unknown tier -> preferred "chat" is present -> chat (not allowed[0])
    groups = [
        {"name": "both", "match_tier": ["weird"], "allowed_surfaces": ["build", "chat"]}
    ]
    decision = resolve_surface(
        "models/gemini-3.8-flash",
        "weird",
        None,
        {"route_by_tier": True, "build_surface": True, "groups": groups},
    )
    assert decision.surface == "chat"
    assert decision.allowed == ("build", "chat")


def test_resolve_filters_build_when_feature_disabled() -> None:
    # free default group wants [build] but build_surface is off -> nothing usable.
    with pytest.raises(SurfacePolicyError) as err:
        resolve_surface(
            "models/gemini-3.8-flash",
            "free",
            None,
            {"route_by_tier": True, "build_surface": False, "groups": []},
        )
    assert err.value.code == "surface_group_disabled"
    assert err.value.http_status == 403
    assert err.value.advice


def test_resolve_group_disabled_raises() -> None:
    groups = [{"name": "banned", "match_tier": ["free"], "allowed_surfaces": []}]
    with pytest.raises(SurfacePolicyError) as err:
        resolve_surface(
            "models/gemini-3.8-flash",
            "free",
            None,
            {"route_by_tier": True, "build_surface": True, "groups": groups},
        )
    assert err.value.code == "surface_group_disabled"
    body = err.value.to_error_body()
    error_body = body["error"]
    assert isinstance(error_body, dict)
    assert error_body["code"] == 403
    assert error_body["advice"] == err.value.advice


def test_resolve_model_guard_raises_for_argon_on_build_only_group() -> None:
    groups = [{"name": "free", "match_tier": ["free"], "allowed_surfaces": ["build"]}]
    with pytest.raises(SurfacePolicyError) as err:
        resolve_surface(
            "models/argon",
            "free",
            None,
            {"route_by_tier": True, "build_surface": True, "groups": groups},
        )
    assert err.value.code == "surface_model_guard"
    assert err.value.http_status == 403
    assert "argon" in err.value.message
    assert err.value.advice


def test_resolve_model_guard_passes_on_chat_group() -> None:
    decision = resolve_surface(
        "models/argon",
        "pro",
        None,
        {"route_by_tier": True, "build_surface": True, "groups": []},
    )
    assert decision.surface == "chat"


# ------------------------------------------------------- check_instance_quota


def test_check_instance_quota_allows_under_limit() -> None:
    check_instance_quota("free", 2, 1, "build", need_new=True)


def test_check_instance_quota_skips_when_no_new_instance() -> None:
    check_instance_quota("free", 2, 5, "build", need_new=False)


def test_check_instance_quota_raises_when_full() -> None:
    with pytest.raises(SurfacePolicyError) as err:
        check_instance_quota("free", 2, 2, "build", need_new=True)
    assert err.value.code == "surface_quota_exceeded"
    assert err.value.http_status == 429
    assert "free" in err.value.message
    assert "2" in err.value.message


def test_surface_policy_error_status_code_alias() -> None:
    err = SurfacePolicyError("surface_quota_exceeded", message="m", advice="a")
    assert err.status_code == 429
    assert err.http_status == 429
    other = SurfacePolicyError("surface_model_guard", message="m", advice="a")
    assert other.status_code == 403


def test_check_instance_quota_uses_default_when_none() -> None:
    check_instance_quota(
        "free", None, MAX_PAGE_INSTANCES_DEFAULT - 1, "build", need_new=True
    )
    with pytest.raises(SurfacePolicyError):
        check_instance_quota(
            "free", None, MAX_PAGE_INSTANCES_DEFAULT, "build", need_new=True
        )


# --------------------------------------------------------------- logging


def test_decision_log_line_is_grepable(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="aistudio.application.surface")
    resolve_surface(
        "models/gemini-3.8-flash",
        "free",
        "user@vip.corp",
        {"route_by_tier": True, "build_surface": True, "groups": GROUPS},
    )
    text = caplog.text
    assert "[surface]" in text
    assert "model=models/gemini-3.8-flash" in text
    assert "source=explicit" in text
    assert "-> build" in text
