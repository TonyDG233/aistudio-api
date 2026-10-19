"""Behavior contracts for WP5 phase-2 channel registry and selection."""

from __future__ import annotations

import pytest

from aistudio_api.application.channel_registry import (
    CHANNEL_ALIASES,
    KNOWN_CHANNELS,
    ChannelSelection,
    ChannelSpec,
    build_registry,
    enabled_channels,
    is_multi_mode,
    normalize_channel,
    parse_model_target,
    select_channel,
)
from aistudio_api.application.surface_orchestrator import SurfacePolicyError


def _expect_policy_error(
    code: str, *args: object, **kwargs: object
) -> SurfacePolicyError:
    with pytest.raises(SurfacePolicyError) as excinfo:
        select_channel(*args, **kwargs)  # type: ignore[arg-type]
    error = excinfo.value
    assert error.code == code
    assert error.http_status == 403
    assert error.status_code == 403
    assert error.message
    assert error.advice.strip()
    return error


def test_channel_constants_cover_aliases_and_canonical_keys() -> None:
    assert CHANNEL_ALIASES == {
        "chat": "chat",
        "pg": "chat",
        "playground": "chat",
        "build": "build",
        "official": "official",
        "gemini_app": "official",
    }
    assert KNOWN_CHANNELS == ("chat", "build", "official")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("chat", "chat"),
        (" PG ", "chat"),
        ("Playground", "chat"),
        ("build", "build"),
        ("official", "official"),
        ("gemini_app", "official"),
        ("", None),
        ("unknown", None),
        (None, None),
    ],
)
def test_normalize_channel(raw: object, expected: str | None) -> None:
    assert normalize_channel(raw) == expected


# ------------------------------------------------------------ parse_model_target


def test_parse_model_target_strips_models_prefix_without_treating_it_as_channel() -> (
    None
):
    assert parse_model_target("models/gemini-x") == (None, "gemini-x")
    assert parse_model_target("models/chat") == (None, "chat")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("pg/gemini-x", ("chat", "gemini-x")),
        ("chat/gemini-x", ("chat", "gemini-x")),
        ("playground/gemini-x", ("chat", "gemini-x")),
        ("build/gemini-x", ("build", "gemini-x")),
        ("official/gemini-x", ("official", "gemini-x")),
        ("gemini_app/gemini-x", ("official", "gemini-x")),
        ("models/pg/gemini-x", ("chat", "gemini-x")),
        ("models/build/gemini-x", ("build", "gemini-x")),
    ],
)
def test_parse_model_target_recognizes_channel_prefixes(
    target: str, expected: tuple[str | None, str]
) -> None:
    assert parse_model_target(target) == expected


@pytest.mark.parametrize("target", ["gemini-x", "nope/gemini-x", "pg/", ""])
def test_parse_model_target_leaves_non_channel_targets_bare(target: str) -> None:
    channel, clean_model = parse_model_target(target)
    assert channel is None
    assert clean_model == target


# ---------------------------------------------------------------- build_registry


def test_build_registry_defaults_match_single_account_behavior() -> None:
    registry = build_registry({})

    assert list(registry) == ["chat", "build", "official"]
    assert registry["chat"] == ChannelSpec(
        key="chat", label="pg", enabled=True, max_instances=2, implemented=True
    )
    assert registry["build"] == ChannelSpec(
        key="build", label="build", enabled=False, max_instances=2, implemented=True
    )
    assert registry["official"] == ChannelSpec(
        key="official",
        label="official",
        enabled=False,
        max_instances=0,
        implemented=False,
    )


@pytest.mark.parametrize("legacy", [True, False])
def test_build_surface_legacy_alias_enables_build(legacy: bool) -> None:
    registry = build_registry({"build_surface": legacy})
    assert registry["build"].enabled is legacy


def test_explicit_channels_build_overrides_build_surface_alias() -> None:
    registry = build_registry(
        {
            "build_surface": True,
            "channels": {"build": {"enabled": False, "max_instances": 3}},
        }
    )
    assert registry["build"].enabled is False
    assert registry["build"].max_instances == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("99", 16), (-3, 0), ("3", 3), ("not-a-number", 2), (None, 2)],
)
def test_max_instances_is_clamped_and_coerced(raw: object, expected: int) -> None:
    registry = build_registry({"channels": {"chat": {"max_instances": raw}}})
    assert registry["chat"].max_instances == expected


def test_official_is_never_enabled_or_implemented() -> None:
    registry = build_registry(
        {"channels": {"official": {"enabled": True, "max_instances": 9}}}
    )
    official = registry["official"]
    assert official.enabled is False
    assert official.implemented is False
    assert official.max_instances == 0


def test_malformed_channels_fall_back_to_defaults_and_legacy_alias() -> None:
    assert build_registry({"channels": ["chat"]}) == build_registry({})
    registry = build_registry(
        {
            "build_surface": True,
            "channels": {"chat": "enabled", "build": 1},
        }
    )
    assert registry["chat"].enabled is True
    assert registry["build"].enabled is True


def test_channel_aliases_are_accepted_as_channel_mapping_keys() -> None:
    registry = build_registry(
        {
            "channels": {
                "pg": {"enabled": False, "max_instances": 4},
                "gemini_app": {"enabled": True, "max_instances": 8},
            }
        }
    )
    assert registry["chat"].enabled is False
    assert registry["chat"].max_instances == 4
    assert registry["official"].enabled is False
    assert registry["official"].max_instances == 0


# ------------------------------------------------------------- enabled_channels


def test_enabled_channels_filters_disabled_and_unimplemented() -> None:
    assert [spec.key for spec in enabled_channels(build_registry({}))] == ["chat"]

    dual = build_registry({"build_surface": True})
    assert [spec.key for spec in enabled_channels(dual)] == ["chat", "build"]

    build_only = build_registry(
        {
            "build_surface": True,
            "channels": {"chat": {"enabled": False}},
        }
    )
    assert [spec.key for spec in enabled_channels(build_only)] == ["build"]


# ----------------------------------------------------------------- is_multi_mode


def test_multi_mode_from_two_enabled_implemented_channels() -> None:
    assert is_multi_mode(build_registry({}), []) is False
    assert is_multi_mode(build_registry({"build_surface": True}), []) is True


def test_multi_mode_from_two_enabled_known_slots() -> None:
    registry = build_registry({})
    slots = [{"channel": "chat"}, {"channel": "pg"}]
    assert is_multi_mode(registry, slots) is True


def test_multi_mode_ignores_disabled_and_invalid_slots() -> None:
    registry = build_registry({})
    assert (
        is_multi_mode(
            registry,
            [
                {"channel": "chat"},
                {"channel": "build", "enabled": False},
                {"channel": "unknown"},
                {"enabled": True},
                {},
                None,
                "chat",
            ],
        )
        is False
    )


# --------------------------------------------------------------- select explicit


def test_select_explicit_channel_and_clean_model() -> None:
    selection = select_channel("pg/gemini-x", {})
    assert selection == ChannelSelection(
        channel="chat",
        surface="chat",
        clean_model="gemini-x",
        explicit=True,
        reason="explicit",
    )


def test_select_explicit_build_when_enabled() -> None:
    selection = select_channel("build/gemini-x", {"build_surface": True})
    assert selection.channel == "build"
    assert selection.surface == "build"
    assert selection.clean_model == "gemini-x"
    assert selection.explicit is True


def test_select_explicit_disabled_channel_is_denied() -> None:
    error = _expect_policy_error("surface_channel_disabled", "build/gemini-x", {})
    assert "build" in error.message
    assert "gemini-x" in error.message


def test_select_explicit_unknown_prefix_is_denied() -> None:
    error = _expect_policy_error("surface_channel_disabled", "mystery/gemini-x", {})
    assert "mystery" in error.message
    assert "gemini-x" in error.message


def test_select_explicit_official_is_denied() -> None:
    error = _expect_policy_error("surface_channel_disabled", "official/gemini-x", {})
    assert "official" in error.message


# ----------------------------------------------------------------- select bare


def test_select_bare_single_capable_channel() -> None:
    selection = select_channel("models/gemini-x", {})
    assert selection == ChannelSelection(
        channel="chat",
        surface="chat",
        clean_model="gemini-x",
        explicit=False,
        reason="capability-default",
    )

    build_only = select_channel(
        "gemini-x",
        {"build_surface": True, "channels": {"chat": {"enabled": False}}},
    )
    assert build_only.channel == "build"
    assert build_only.surface == "build"


def test_select_bare_without_capable_channel_is_denied() -> None:
    error = _expect_policy_error(
        "surface_no_capable_account",
        "gemini-x",
        {"channels": {"chat": {"enabled": False}}},
    )
    assert "gemini-x" in error.message


def test_select_multi_channel_prefers_the_mounted_channel() -> None:
    features = {"build_surface": True}

    chat = select_channel("gemini-x", features, mounted_channels={"chat"})
    assert chat.channel == "chat"
    assert chat.reason == "mounted"

    build = select_channel("gemini-x", features, mounted_channels={"build"})
    assert build.channel == "build"
    assert build.reason == "mounted"

    alias = select_channel("gemini-x", features, mounted_channels=("pg",))
    assert alias.channel == "chat"
    assert alias.reason == "mounted"


def test_select_multi_channel_mounted_ambiguity_uses_capability_default() -> None:
    features = {"build_surface": True}
    selection = select_channel("gemini-x", features, mounted_channels={"chat", "build"})
    assert selection.channel == "build"
    assert selection.reason == "capability-default"


def test_select_free_capable_dual_defaults_to_build() -> None:
    selection = select_channel("gemini-x", {"build_surface": True})
    assert selection.channel == "build"
    assert selection.surface == "build"
    assert selection.reason == "capability-default"


# --------------------------------------------------------------- chat-only guard


def test_chat_only_model_is_forced_to_chat_in_multi_mode() -> None:
    selection = select_channel(
        "gemini-2.5-pro",
        {"build_surface": True},
        model_is_chat_only=True,
    )
    assert selection.channel == "chat"
    assert selection.reason == "capability-default"


def test_chat_only_model_guard_on_explicit_build() -> None:
    error = _expect_policy_error(
        "surface_model_guard",
        "build/argon",
        {"build_surface": True},
        model_is_chat_only=True,
    )
    assert "argon" in error.message
    assert "build" in error.message


def test_chat_only_model_guard_when_only_build_is_capable() -> None:
    error = _expect_policy_error(
        "surface_model_guard",
        "argon",
        {"build_surface": True, "channels": {"chat": {"enabled": False}}},
        model_is_chat_only=True,
    )
    assert "argon" in error.message


def test_chat_only_model_can_use_explicit_chat() -> None:
    selection = select_channel("chat/argon", {}, model_is_chat_only=True)
    assert selection.channel == "chat"
    assert selection.clean_model == "argon"
