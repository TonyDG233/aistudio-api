"""Tests for config deep merge, startup validation, instance normalization and lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import aistudio_api.api.app as app_module
import aistudio_api.infrastructure.gateway.model_defaults as model_defaults
from aistudio_api.application.account_rotator import AccountStats
from aistudio_api.config import resolve_hash_include_code
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    get_configured_instance_specs,
    get_configured_logging_settings,
    get_configured_rotator_settings,
    get_configured_routing_settings,
    get_configured_security_settings,
    invalidate_config_cache,
    validate_config,
    validate_config_mapping,
    write_config_text,
)


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_partial_rotator_block_deep_merges_with_defaults(tmp_path, monkeypatch):
    path = _write_config(tmp_path, "rotator:\n  max_retries: 2\n")

    rotator = get_configured_rotator_settings(path)

    assert rotator["max_retries"] == 2
    cooldowns = rotator["cooldowns"]
    assert isinstance(cooldowns, dict)
    assert set(cooldowns) >= {
        "rate_limited_short",
        "rate_limited_drip",
        "rate_limited_drip_extended",
        "auth_error",
    }

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(path))
    invalidate_config_cache()
    stats = AccountStats(account_id="acc_1")
    stats.record_rate_limited(model="gemini-2.5-pro")
    assert stats.get_cooldown_remaining("gemini-2.5-pro") > 0


def test_hash_include_code_defaults_true_and_env_overrides_yaml(tmp_path, monkeypatch):
    """D-09: diagnostic switch comes from config/env, defaults to true."""
    path = tmp_path / "config.yaml"
    path.write_text("diagnostics:\n  hash_include_code: false\n", encoding="utf-8")
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(path))
    monkeypatch.delenv("AISTUDIO_HASH_INCLUDE_CODE", raising=False)
    assert resolve_hash_include_code() is False

    monkeypatch.setenv("AISTUDIO_HASH_INCLUDE_CODE", "1")
    assert resolve_hash_include_code() is True


def test_routing_retry_same_delay_default_env_clamp_and_invalid(tmp_path, monkeypatch):
    """VPS #1: 429 same-account delay defaults to 2.0s and is env-overridable."""
    path = _write_config(
        tmp_path,
        "features:\n  routing:\n    max_switches: 1\n    retry_same_delay_sec: 1.5\n",
    )
    monkeypatch.delenv("AISTUDIO_RETRY_SAME_DELAY_SEC", raising=False)
    settings = get_configured_routing_settings(path)
    assert settings["max_switches"] == 1
    assert settings["retry_same_delay_sec"] == 1.5

    monkeypatch.setenv("AISTUDIO_RETRY_SAME_DELAY_SEC", "0")
    assert get_configured_routing_settings(path)["retry_same_delay_sec"] == 0.0

    monkeypatch.setenv("AISTUDIO_RETRY_SAME_DELAY_SEC", "99")
    assert get_configured_routing_settings(path)["retry_same_delay_sec"] == 30.0

    monkeypatch.setenv("AISTUDIO_RETRY_SAME_DELAY_SEC", "-3")
    assert get_configured_routing_settings(path)["retry_same_delay_sec"] == 0.0

    monkeypatch.setenv("AISTUDIO_RETRY_SAME_DELAY_SEC", "not-a-number")
    assert get_configured_routing_settings(path)["retry_same_delay_sec"] == 1.5


def test_routing_retry_same_delay_defaults_to_two_seconds(tmp_path, monkeypatch):
    path = _write_config(tmp_path, "features:\n  routing:\n    max_switches: 2\n")
    monkeypatch.delenv("AISTUDIO_RETRY_SAME_DELAY_SEC", raising=False)

    settings = get_configured_routing_settings(path)

    assert settings["retry_same_delay_sec"] == 2.0


def test_routing_switch_backoff_default_clamp_and_invalid(tmp_path):
    """Switch backoff defaults to 4.0s and is clamped into 0..30."""
    configured = _write_config(
        tmp_path,
        "features:\n  routing:\n    max_switches: 2\n    switch_backoff_sec: 6\n",
    )
    assert get_configured_routing_settings(configured)["switch_backoff_sec"] == 6.0

    missing = tmp_path / "missing.yaml"
    missing.write_text("features:\n  routing:\n    max_switches: 2\n", encoding="utf-8")
    assert get_configured_routing_settings(missing)["switch_backoff_sec"] == 4.0

    too_high = tmp_path / "high.yaml"
    too_high.write_text(
        "features:\n  routing:\n    switch_backoff_sec: 99\n", encoding="utf-8"
    )
    assert get_configured_routing_settings(too_high)["switch_backoff_sec"] == 30.0

    negative = tmp_path / "negative.yaml"
    negative.write_text(
        "features:\n  routing:\n    switch_backoff_sec: -5\n", encoding="utf-8"
    )
    assert get_configured_routing_settings(negative)["switch_backoff_sec"] == 0.0

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "features:\n  routing:\n    switch_backoff_sec: not-a-number\n",
        encoding="utf-8",
    )
    assert get_configured_routing_settings(invalid)["switch_backoff_sec"] == 4.0

    normalized = validate_config_mapping({"features": {"routing": {}}})
    features_block = normalized["features"]
    assert isinstance(features_block, dict)
    routing_block = features_block.get("routing")
    assert isinstance(routing_block, dict)
    assert routing_block["switch_backoff_sec"] == 4.0


def test_invalid_rotator_type_fails_startup_validation(tmp_path):
    path = _write_config(tmp_path, "rotator:\n  - not-a-mapping\n")

    with pytest.raises(ConfigValidationError, match="rotator 必须为字典"):
        validate_config(path)


def test_invalid_rotator_cooldown_value_fails_startup_validation(tmp_path):
    path = _write_config(
        tmp_path,
        "rotator:\n  cooldowns:\n    rate_limited_short: not-a-number\n",
    )

    with pytest.raises(ConfigValidationError, match=r"rotator\.cooldowns"):
        validate_config(path)


def test_security_anonymous_flag_defaults_false_and_accepts_true(tmp_path):
    default_path = _write_config(tmp_path, "logging:\n  level: INFO\n")
    assert get_configured_security_settings(default_path) is False

    enabled_path = tmp_path / "enabled.yaml"
    enabled_path.write_text(
        "security:\n  api_allow_anonymous: true\n", encoding="utf-8"
    )
    assert get_configured_security_settings(enabled_path) is True


def test_security_invalid_boolean_falls_back_to_false(tmp_path):
    path = _write_config(tmp_path, "security:\n  api_allow_anonymous: maybe\n")

    assert get_configured_security_settings(path) is False


def test_non_dict_logging_and_security_blocks_fallback_with_warning(tmp_path, caplog):
    path = _write_config(tmp_path, "logging: []\nsecurity: null\n")

    with caplog.at_level(logging.WARNING):
        normalized = validate_config_mapping({"logging": [], "security": None})
        logging_settings = get_configured_logging_settings(path)
        allow_anonymous = get_configured_security_settings(path)

    assert isinstance(normalized["logging"], dict)
    assert isinstance(normalized["security"], dict)
    assert logging_settings["level"] == "INFO"
    assert allow_anonymous is False
    messages = [record.message for record in caplog.records]
    assert any("logging 必须为字典" in message for message in messages)
    assert any("security 必须为字典" in message for message in messages)


@pytest.mark.parametrize("content", ["logging: []\n", "security: null\n"])
def test_write_config_text_rejects_non_dict_blocks(tmp_path, content):
    path = tmp_path / "config.yaml"

    with pytest.raises(ConfigValidationError, match="必须为字典"):
        write_config_text(content, path)

    assert not path.exists()


def test_instances_are_normalized_deduplicated_and_bad_items_dropped(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 2
    surface: chat
    min_tier: Free
  - count: 1
    surface: CHAT
    min_tier: free
  - count: 1
    surface: build
    min_tier: ultra_20x
  - count: 0
    surface: chat
    min_tier: Pro
  - count: 1
    surface: video
    min_tier: Pro
  - just-a-string
""",
    )

    assert get_configured_instance_specs(path) == [
        {
            "count": 3,
            "surface": "chat",
            "only_tier": "Free",
            "min_tier": "Free",
            "max_tier": "Free",
        },
        {"count": 1, "surface": "build", "min_tier": "Ultra_20X"},
    ]


def test_instances_only_tier_is_valid_and_normalized(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    only_tier: Free
  - count: 1
    surface: chat
    only_tier: Pro
""",
    )

    assert get_configured_instance_specs(path) == [
        {
            "count": 1,
            "surface": "chat",
            "only_tier": "Free",
            "min_tier": "Free",
            "max_tier": "Free",
        },
        {"count": 1, "surface": "chat", "only_tier": "Pro"},
    ]


def test_instances_min_max_interval_is_valid(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    min_tier: Pro
    max_tier: Ultra
""",
    )

    assert get_configured_instance_specs(path) == [
        {"count": 1, "surface": "chat", "min_tier": "Pro", "max_tier": "Ultra"},
    ]


def test_instances_max_tier_alone_is_valid(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    max_tier: Free
""",
    )

    assert get_configured_instance_specs(path) == [
        {
            "count": 1,
            "surface": "chat",
            "only_tier": "Free",
            "min_tier": "Free",
            "max_tier": "Free",
        },
    ]


def test_instances_max_tier_above_free_defaults_lower_bound_to_pro(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    max_tier: Ultra
""",
    )

    assert get_configured_instance_specs(path) == [
        {"count": 1, "surface": "chat", "min_tier": "Pro", "max_tier": "Ultra"},
    ]


def test_instances_max_tier_plus_alone_is_rejected(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    max_tier: Plus
""",
    )

    with pytest.raises(ConfigValidationError, match="Pro"):
        validate_config(path)


def test_instances_paid_interval_upper_below_pro_is_rejected(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    min_tier: Plus
    max_tier: Plus
""",
    )

    with pytest.raises(ConfigValidationError, match="Pro"):
        validate_config(path)


@pytest.mark.parametrize(
    ("selector_yaml", "field"),
    [
        ("only_tier: 1", "only_tier"),
        ("only_tier: [Free]", "only_tier"),
        ("only_tier: true", "only_tier"),
        ("only_tier: Diamond", "only_tier"),
        ("min_tier: 1", "min_tier"),
        ("min_tier: [Free]", "min_tier"),
        ("min_tier: false", "min_tier"),
        ("min_tier: Diamond", "min_tier"),
        ("max_tier: 1", "max_tier"),
        ("max_tier: [Free]", "max_tier"),
        ("max_tier: true", "max_tier"),
        ("max_tier: Diamond", "max_tier"),
    ],
)
def test_instances_invalid_tier_selectors_are_hard_errors(
    tmp_path, selector_yaml, field
):
    path = _write_config(
        tmp_path,
        f"""
instances:
  - count: 1
    surface: chat
    {selector_yaml}
""",
    )

    with pytest.raises(ConfigValidationError, match=field):
        validate_config(path)


def test_invalid_tier_selector_is_rejected_even_with_valid_sibling(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    only_tier: Pro
    min_tier: 1
""",
    )

    with pytest.raises(ConfigValidationError, match="min_tier"):
        validate_config(path)


def test_only_free_and_legacy_min_free_share_one_merge_key(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    only_tier: Free
  - count: 2
    surface: chat
    min_tier: Free
  - count: 1
    surface: chat
    max_tier: Free
""",
    )

    assert get_configured_instance_specs(path) == [
        {
            "count": 4,
            "surface": "chat",
            "only_tier": "Free",
            "min_tier": "Free",
            "max_tier": "Free",
        },
    ]


def test_normalized_instance_specs_are_consumable_by_the_pool(tmp_path):
    from aistudio_api.infrastructure.gateway.instance_pool import InstanceSpec

    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    only_tier: Free
  - count: 1
    surface: chat
    min_tier: Pro
""",
    )

    specs = get_configured_instance_specs(path)
    parsed = [InstanceSpec.from_mapping(spec) for spec in specs]

    assert [item.lane for item in parsed] == ["free", "paid"]
    assert parsed[0].accepts_tier("Free") is True
    assert parsed[0].accepts_tier("Pro") is False
    assert parsed[1].accepts_tier("Pro") is True
    assert parsed[1].accepts_tier("Ultra") is True


def test_instances_missing_all_tier_selectors_is_invalid(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
""",
    )

    with pytest.raises(ConfigValidationError, match="tier 选择器"):
        validate_config(path)


def test_instances_only_tier_conflict_is_invalid(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    only_tier: Pro
    min_tier: Ultra
""",
    )

    with pytest.raises(ConfigValidationError, match="only_tier"):
        validate_config(path)


def test_instances_inverted_min_max_is_invalid(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    min_tier: Ultra
    max_tier: Pro
""",
    )

    with pytest.raises(ConfigValidationError, match="min_tier"):
        validate_config(path)


def test_instances_free_and_paid_interval_cannot_mix(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 1
    surface: chat
    min_tier: Free
    max_tier: Pro
""",
    )

    with pytest.raises(ConfigValidationError, match="Free"):
        validate_config(path)


def test_instances_all_invalid_raises_config_validation_error(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 0
    surface: chat
    min_tier: Pro
  - count: 1
    surface: video
    min_tier: Pro
""",
    )

    with pytest.raises(ConfigValidationError, match="未包含任何有效实例"):
        get_configured_instance_specs(path)


def test_instances_must_be_list(tmp_path):
    path = _write_config(tmp_path, "instances: {}\n")

    with pytest.raises(ConfigValidationError, match="instances 必须为列表"):
        validate_config(path)


def test_validate_config_rejects_all_invalid_instances(tmp_path):
    path = _write_config(
        tmp_path,
        """
instances:
  - count: 0
    surface: chat
    min_tier: Pro
  - count: 1
    surface: video
    min_tier: Pro
""",
    )

    with pytest.raises(ConfigValidationError, match="未包含任何有效实例"):
        validate_config(path)


@pytest.mark.parametrize(
    ("preset_yaml", "missing_field"),
    [
        ("- action: enable_all", "id"),
        ("- id: preset_missing_action", "action"),
    ],
)
def test_validate_config_requires_preset_id_and_action(
    tmp_path, preset_yaml, missing_field
):
    path = _write_config(tmp_path, f"account_presets:\n  {preset_yaml}\n")

    with pytest.raises(
        ConfigValidationError,
        match=f"第 1 个 preset 缺少非空字符串字段 {missing_field}",
    ):
        validate_config(path)


def test_write_config_text_uses_atomic_replace_and_writes_content(
    tmp_path, monkeypatch
):
    path = tmp_path / "config.yaml"
    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []

    def tracking_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(model_defaults.os, "replace", tracking_replace)

    content = "logging:\n  level: WARNING\n"
    written_path = write_config_text(content, path)

    assert written_path == path
    assert path.read_text(encoding="utf-8") == content
    assert len(replace_calls) == 1
    assert Path(replace_calls[0][0]).parent == tmp_path
    assert replace_calls[0][1] == str(path)
    assert not list(tmp_path.glob("*.tmp"))


def test_write_config_text_rejects_invalid_config_without_writing(tmp_path):
    path = tmp_path / "config.yaml"

    with pytest.raises(ConfigValidationError):
        write_config_text("rotator: []\n", path)

    assert not path.exists()


class _RecordingComponent:
    def __init__(self, name: str, method: str, events: list[str]) -> None:
        self._name = name
        self._method = method
        self._events = events

    def _record(self) -> None:
        self._events.append(f"{self._name}.{self._method}")


class _SyncComponent(_RecordingComponent):
    def __getattr__(self, item: str):
        if item == self._method:
            return self._record
        raise AttributeError(item)


class _AsyncComponent(_RecordingComponent):
    def __getattr__(self, item: str):
        if item != self._method:
            raise AttributeError(item)

        async def record() -> None:
            self._record()

        return record


def test_shutdown_runtime_order_and_state_cleanup():
    events: list[str] = []
    keepalive = _AsyncComponent("keepalive", "stop", events)
    instance_manager = _SyncComponent("instance_manager", "close", events)
    client = _AsyncComponent("client", "close", events)
    state = SimpleNamespace(
        keepalive=keepalive,
        instance_manager=instance_manager,
        client=client,
        account_service=object(),
        rotator=object(),
    )

    asyncio.run(app_module._shutdown_runtime(state, ()))  # type: ignore[arg-type]

    assert events == ["keepalive.stop", "instance_manager.close", "client.close"]
    assert state.keepalive is None
    assert state.instance_manager is None
    assert state.client is None
    assert state.account_service is None
    assert state.rotator is None


def test_shutdown_runtime_cancels_background_tasks_first():
    events: list[str] = []
    keepalive = _SyncComponent("keepalive", "stop", events)

    async def scenario() -> None:
        task = asyncio.create_task(asyncio.sleep(30))
        state = SimpleNamespace(
            keepalive=keepalive,
            instance_manager=None,
            client=None,
            account_service=None,
            rotator=None,
        )
        await app_module._shutdown_runtime(state, (task,))  # type: ignore[arg-type]
        await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(scenario())
    assert events == ["keepalive.stop"]


def test_auth_error_cooldown_inherits_tracked_template_default():
    normalized = validate_config_mapping(
        {
            "rotator": {
                "cooldowns": {
                    "rate_limited_short": 60,
                    "rate_limited_drip": 300,
                    "rate_limited_drip_extended": 600,
                }
            }
        }
    )
    rotator = normalized["rotator"]
    assert isinstance(rotator, dict)
    cooldowns = rotator["cooldowns"]
    assert isinstance(cooldowns, dict)
    template_rotator = model_defaults._default_config()["rotator"]
    assert isinstance(template_rotator, dict)
    template_cooldowns = template_rotator["cooldowns"]
    assert isinstance(template_cooldowns, dict)
    assert template_cooldowns["auth_error"] == 0
    assert cooldowns["auth_error"] == 0
    assert cooldowns["rate_limited_short"] == 60


def test_auth_error_cooldown_still_rejects_non_numeric_when_present():
    with pytest.raises(ConfigValidationError, match=r"rotator\.cooldowns\.auth_error"):
        validate_config_mapping(
            {"rotator": {"cooldowns": {"auth_error": "not-a-number"}}}
        )


def test_default_tier_quotas_free_flash_limit_and_hydrogen_bucket():
    from aistudio_api.application.routing_eligibility import resolve_required_tiers
    from aistudio_api.config import resolve_default_config_template
    from aistudio_api.infrastructure.gateway.model_defaults import resolve_tier_quota

    default_path = resolve_default_config_template()
    invalidate_config_cache()
    defaults = validate_config_mapping({})
    tier_quotas = defaults["tier_quotas"]
    assert isinstance(tier_quotas, dict)

    assert tier_quotas["Free"]["flash_models"]["daily_limit"] == 50
    assert tier_quotas["Free"]["pro_models"]["daily_limit"] == 0
    assert tier_quotas["Free"]["default_limit"] == 50
    assert "hydrogen" in tier_quotas["Free"]["flash_models"]["match"]["contains"]
    assert (
        resolve_tier_quota("Free", "gemini-4-hydrogen", config_path=default_path) == 50
    )
    assert (
        resolve_tier_quota("Free", "gemini-3.7-flash", config_path=default_path) == 50
    )
    assert (
        resolve_tier_quota("Free", "gemini-3.1-pro-preview", config_path=default_path)
        == 0
    )

    for (
        tier_name,
        expected_hydrogen,
        expected_flash,
        expected_pro,
        expected_default,
    ) in (
        ("Plus", 100, 150, 50, 50),
        ("Pro", 100, 150, 50, 50),
        ("Ultra", 200, 200, 100, 200),
        ("Ultra_20X", 200, 200, 100, 200),
    ):
        bucket_keys = list(tier_quotas[tier_name].keys())
        assert bucket_keys.index("hydrogen_models") < bucket_keys.index("flash_models")
        assert tier_quotas[tier_name]["hydrogen_models"]["match"]["contains"] == [
            "hydrogen"
        ]
        assert tier_quotas[tier_name]["flash_models"]["match"]["contains"] == ["flash"]
        assert tier_quotas[tier_name]["default_limit"] == expected_default
        assert (
            resolve_tier_quota(tier_name, "gemini-4-hydrogen", config_path=default_path)
            == expected_hydrogen
        )
        assert (
            resolve_tier_quota(tier_name, "gemini-3.8-flash", config_path=default_path)
            == expected_flash
        )
        assert (
            resolve_tier_quota(
                tier_name, "gemini-3.1-pro-preview", config_path=default_path
            )
            == expected_pro
        )

    assert tier_quotas["Pro"]["argon_models"]["match"]["contains"] == ["argon"]
    assert resolve_tier_quota("Pro", "gemini-4-argon", config_path=default_path) == 50
    assert (
        resolve_tier_quota("Pro", "gemini-3-pro-image", config_path=default_path) == 50
    )
    assert (
        resolve_tier_quota("Pro", "gemini-3.1-flash-image", config_path=default_path)
        == 150
    )

    for ultra_tier in ("Ultra", "Ultra_20X"):
        assert tier_quotas[ultra_tier]["argon_models"]["match"]["contains"] == [
            "argon",
            "carbon",
        ]
        assert (
            resolve_tier_quota(ultra_tier, "gemini-4-argon", config_path=default_path)
            == 200
        )
        assert (
            resolve_tier_quota(ultra_tier, "gemini-4-carbon", config_path=default_path)
            == 200
        )
        assert (
            resolve_tier_quota(
                ultra_tier, "veo-3.1-generate-preview", config_path=default_path
            )
            == 50
        )

    paid_rules = defaults["paid_model_rules"]
    assert isinstance(paid_rules, dict)
    assert "gemini-4" not in paid_rules["pro_required"]["contains"]
    assert "3.1-pro" in paid_rules["pro_required"]["contains"]
    assert resolve_required_tiers("gemini-4-hydrogen", rules=paid_rules) == ()
    assert resolve_required_tiers("gemini-4-argon", rules=paid_rules) == (
        "Pro",
        "Ultra",
        "Ultra_20X",
    )
    assert resolve_required_tiers("gemini-3.1-pro-preview", rules=paid_rules) == (
        "Pro",
        "Ultra",
        "Ultra_20X",
    )
    assert resolve_required_tiers("gemini-4-carbon", rules=paid_rules) == (
        "Ultra",
        "Ultra_20X",
    )


def test_proactive_switch_defaults_off_and_threshold_fifteen(tmp_path):
    path = _write_config(tmp_path, "features:\n  routing: {}\n")

    settings = get_configured_routing_settings(path)

    assert settings["proactive_switch_mode"] == "off"
    assert settings["proactive_switch_threshold"] == 15
    assert settings["proactive_switch_groups"] == []


def test_proactive_switch_normalizes_mode_threshold_and_groups(tmp_path):
    path = _write_config(
        tmp_path,
        "features:\n  routing:\n"
        "    proactive_switch_mode: Groups\n"
        "    proactive_switch_threshold: 42\n"
        "    proactive_switch_groups: [alpha, ' beta ', alpha, '']\n",
    )

    settings = get_configured_routing_settings(path)

    assert settings["proactive_switch_mode"] == "groups"
    assert settings["proactive_switch_threshold"] == 42
    assert settings["proactive_switch_groups"] == ["alpha", "beta"]


def test_proactive_switch_accepts_integral_float_threshold(tmp_path):
    path = _write_config(
        tmp_path,
        "features:\n  routing:\n    proactive_switch_threshold: 20.0\n",
    )

    settings = get_configured_routing_settings(path)

    assert settings["proactive_switch_threshold"] == 20
    assert isinstance(settings["proactive_switch_threshold"], int)


def test_proactive_switch_invalid_values_fall_back_to_defaults(tmp_path):
    path = _write_config(
        tmp_path,
        "features:\n  routing:\n"
        "    proactive_switch_mode: nope\n"
        "    proactive_switch_threshold: 0\n"
        "    proactive_switch_groups: not-a-list\n",
    )

    settings = get_configured_routing_settings(path)

    assert settings["proactive_switch_mode"] == "off"
    assert settings["proactive_switch_threshold"] == 15
    assert settings["proactive_switch_groups"] == []


def test_validate_config_mapping_normalizes_proactive_switch_block():
    normalized = validate_config_mapping(
        {
            "features": {
                "routing": {
                    "proactive_switch_mode": "ALL",
                    "proactive_switch_threshold": 1001,
                    "proactive_switch_groups": ("x", "x", " y ", 3),
                }
            }
        }
    )

    features = normalized.get("features")
    assert isinstance(features, dict)
    routing = features.get("routing")
    assert isinstance(routing, dict)
    assert routing["proactive_switch_mode"] == "all"
    assert routing["proactive_switch_threshold"] == 15
    assert routing["proactive_switch_groups"] == ["x", "y"]
