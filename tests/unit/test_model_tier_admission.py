"""D05-046/D05-047: rules -> accessModes -> paid/capability admission."""

from __future__ import annotations

import pytest

from aistudio_api.application.model_access import clear_paid_models, set_paid_models
from aistudio_api.application.routing_eligibility import (
    resolve_model_route,
    resolve_required_tiers,
    tier_allows,
)
from aistudio_api.infrastructure.gateway.model_discovery import model_discovery


@pytest.fixture(autouse=True)
def _clean_paid_state():
    clear_paid_models()
    saved = model_discovery._cached_models
    model_discovery._cached_models = []
    try:
        yield
    finally:
        model_discovery._cached_models = saved
        clear_paid_models()


def test_config_rule_wins_for_image_models() -> None:
    route = resolve_model_route("gemini-3.1-flash-image")
    assert route.paid is True
    assert route.required_tier == "Pro"
    assert route.required_tiers == ("Pro", "Ultra", "Ultra_20X")


def test_ultra_rule_is_explicit() -> None:
    tiers = resolve_required_tiers(
        "veo-3.1-generate-preview",
        rules={
            "ultra_required": {
                "contains": ["veo"],
                "required_tiers": ["Ultra", "Ultra_20X"],
            }
        },
    )
    assert tiers == ("Ultra", "Ultra_20X")


def test_access_modes_ultra_bit() -> None:
    model_discovery._cached_models = [
        {"id": "ultra-only-model", "accessModes": [4], "paid": True}
    ]
    assert resolve_required_tiers("ultra-only-model", rules={}) == (
        "Ultra",
        "Ultra_20X",
    )


def test_access_modes_pro_bit() -> None:
    model_discovery._cached_models = [
        {"id": "pro-model", "accessModes": [1, 3], "paid": True}
    ]
    assert resolve_required_tiers("pro-model", rules={}) == (
        "Pro",
        "Ultra",
        "Ultra_20X",
    )


def test_empty_rules_and_non_paid_model_has_no_tier_requirement() -> None:
    model_discovery._cached_models = [{"id": "mystery-model", "accessModes": [1]}]
    assert resolve_required_tiers("mystery-model", rules={}) == ()


def test_paid_marker_without_rules_defaults_to_pro_not_ultra() -> None:
    set_paid_models([{"id": "paid-but-rules-empty", "paid": True}])
    assert resolve_required_tiers("paid-but-rules-empty", rules={}) == (
        "Pro",
        "Ultra",
        "Ultra_20X",
    )


def test_plus_does_not_satisfy_pro_requirement() -> None:
    route = resolve_model_route("gemini-4-argon")
    assert tier_allows("Pro", route) is True
    assert tier_allows("Ultra_20X", route) is True
    assert tier_allows("Plus", route) is False
    assert tier_allows("Free", route) is False
