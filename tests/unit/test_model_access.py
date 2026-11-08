"""Tests for Paid model metadata and eligibility helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aistudio_api.domain.model_access import (
    TierSelectorError,
    account_is_paid,
    clear_paid_models,
    instance_lane_for_tier,
    is_chat_only_model,
    is_paid_model,
    model_account_requirement,
    model_requires_paid_account,
    resolve_tier_selector,
    set_paid_models,
    surface_capability,
    tier_matches_spec,
)
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService

EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "dev"
    / "evidence"
    / "B6"
    / "model_discovery_fix.json"
)


@pytest.fixture(autouse=True)
def _restore_paid_cache():
    clear_paid_models()
    yield
    clear_paid_models()


def test_paid_models_are_chat_only_and_require_paid_account() -> None:
    clear_paid_models()
    set_paid_models(
        [
            {"id": "models/gemini-3-flash-preview", "paid": True},
            {"id": "models/gemini-3.8-flash", "paid": False},
        ]
    )

    assert is_paid_model("gemini-3-flash-preview") is True
    assert is_paid_model("chat/gemini-3-flash-preview") is True
    assert is_paid_model("pg/gemini-3-flash-preview") is False
    assert is_paid_model("models/gemini-3.8-flash") is False

    assert is_chat_only_model("gemini-3-flash-preview") is True
    assert is_chat_only_model("build/gemini-3-flash-preview") is True
    assert is_chat_only_model("gemini-3.8-flash") is False
    assert model_requires_paid_account("gemini-3-flash-preview") is True
    assert model_account_requirement("gemini-3-flash-preview") == "paid"
    assert model_requires_paid_account("gemini-3.1-pro-preview") is False
    assert model_account_requirement("gemini-3.1-pro-preview") == "any"
    assert surface_capability("gemini-3-flash-preview", ("chat", "build")) == ("chat",)


def test_tier_selectors_enforce_exact_and_interval_semantics() -> None:
    # only_tier Pro is exactly Pro; Ultra needs its own selector.
    assert tier_matches_spec("Pro", only_tier="Pro") is True
    assert tier_matches_spec("Ultra", only_tier="Pro") is False
    assert tier_matches_spec("Ultra_20X", only_tier="Pro") is False
    # only_tier Ultra matches the normalized Ultra family.
    assert tier_matches_spec("Ultra", only_tier="Ultra") is True
    assert tier_matches_spec("Ultra_20X", only_tier="Ultra") is True
    assert tier_matches_spec("Pro", only_tier="Ultra") is False
    # min=max=Pro is the same closed interval and excludes Ultra.
    assert tier_matches_spec("Pro", min_tier="Pro", max_tier="Pro") is True
    assert tier_matches_spec("Ultra", min_tier="Pro", max_tier="Pro") is False
    # Legacy min_tier Free is exactly Free and never a paid tier.
    assert tier_matches_spec("Free", min_tier="Free") is True
    assert tier_matches_spec("Plus", min_tier="Free") is False
    assert tier_matches_spec("Pro", min_tier="Free") is False
    # A Free spec never binds Pro/Ultra even if max is left open.
    assert tier_matches_spec("Ultra", only_tier="Free") is False
    assert tier_matches_spec("Pro", min_tier="Free", max_tier="Free") is False


def test_max_tier_only_defaults_paid_lower_bound_to_pro() -> None:
    selector = resolve_tier_selector(max_tier="Ultra")
    assert selector.min_tier == "Pro"
    assert selector.max_tier == "Ultra"
    assert selector.lane == "paid"

    free_selector = resolve_tier_selector(max_tier="Free")
    assert free_selector.lane == "free"
    assert (free_selector.min_tier, free_selector.max_tier) == ("Free", "Free")

    with pytest.raises(TierSelectorError, match="Pro"):
        resolve_tier_selector(max_tier="Plus")
    with pytest.raises(TierSelectorError, match="Pro"):
        resolve_tier_selector(min_tier="Plus", max_tier="Plus")


def test_normalized_free_spec_is_consistent_with_only_tier() -> None:
    selector = resolve_tier_selector(
        only_tier="Free", min_tier="Free", max_tier="Free"
    )
    assert selector.lane == "free"
    assert selector.only_tier == "Free"
    assert (selector.min_tier, selector.max_tier) == ("Free", "Free")

    with pytest.raises(TierSelectorError, match="only_tier"):
        resolve_tier_selector(only_tier="Pro", min_tier="Ultra")


def test_invalid_tier_selector_values_are_hard_errors() -> None:
    for kwargs in (
        {"only_tier": 1},
        {"min_tier": [1]},
        {"max_tier": True},
        {"only_tier": "Diamond"},
    ):
        with pytest.raises(TierSelectorError):
            resolve_tier_selector(**kwargs)

    # A valid sibling must not mask an invalid selector in the same item.
    with pytest.raises(TierSelectorError, match="min_tier"):
        resolve_tier_selector(only_tier="Pro", min_tier=1)


def test_account_tier_paid_eligibility() -> None:
    assert account_is_paid("Pro") is True
    assert account_is_paid("Ultra") is True
    assert account_is_paid("Plus") is True
    assert account_is_paid("Ultra_20X") is True
    assert instance_lane_for_tier("Ultra_20X") == "paid"
    assert account_is_paid("Free") is False
    assert account_is_paid(None) is False


def test_discovery_updates_downgrade_upgrade_and_preserves_absent_models() -> None:
    clear_paid_models()
    assert is_paid_model("gemini-4-argon") is True

    set_paid_models(
        [
            {"id": "models/gemini-4-argon", "paid": False},
            {"id": "models/new-paid-model", "paid": True},
        ]
    )
    assert is_paid_model("gemini-4-argon") is False
    assert is_paid_model("new-paid-model") is True
    # Absent from a partial snapshot: keep conservative last-known status.
    assert is_paid_model("gemini-3-flash-preview") is True


def test_snapshot_restores_status_and_applies_downgrade(tmp_path, monkeypatch) -> None:
    cache_file = tmp_path / "models_cache.json"
    monkeypatch.setattr(
        "aistudio_api.config.settings.model_cache_file", str(cache_file)
    )
    clear_paid_models()
    service = ModelDiscoveryService()
    service._save_snapshot(
        [
            {"id": "models/gemini-4-argon", "paid": False},
            {"id": "models/new-paid-model", "paid": True},
        ]
    )

    clear_paid_models()
    restored = ModelDiscoveryService()
    assert restored._cached_models
    assert is_paid_model("gemini-4-argon") is False
    assert is_paid_model("new-paid-model") is True


def test_fallback_paid_models_allow_catalog_projection_without_discovery() -> None:
    assert is_chat_only_model("gemini-4-argon") is True
    assert is_chat_only_model("gemini-3-flash-preview") is True
    assert is_chat_only_model("gemini-3.1-pro-preview") is False
    assert is_chat_only_model("gemini-3.8-flash") is False


def test_default_paid_seeds_cover_every_live_paid_model() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    live_paid = {
        str(item["id"]) for item in payload["models"] if item.get("paid") is True
    }
    assert live_paid

    clear_paid_models()
    for model_id in sorted(live_paid):
        assert is_paid_model(model_id) is True, model_id
    # Q2: "pro" in the name is not a Paid signal.
    assert is_paid_model("gemini-3.1-pro-preview") is False


def test_live_evidence_entries_all_have_explicit_paid_flags() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    models = payload["models"]
    assert len(models) == 31
    for item in models:
        assert isinstance(item.get("paid"), bool), item.get("id")
