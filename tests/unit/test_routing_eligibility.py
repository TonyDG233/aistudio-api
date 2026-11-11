"""D05-006/009/014: surface, lane and tier eligibility without groups/tags."""

from __future__ import annotations

import pytest

from aistudio_api.application.routing_eligibility import (
    parse_model_surface,
    resolve_model_route,
    resolve_required_tiers,
    tier_allows,
)
from aistudio_api.domain.errors import SurfaceCapacityError, SurfaceUnavailableError
from aistudio_api.domain.model_access import clear_paid_models
from aistudio_api.infrastructure.gateway import model_defaults
from aistudio_api.infrastructure.gateway.model_discovery import (
    ModelDiscoveryService,
    model_discovery,
)


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


def test_bare_model_defaults_to_chat() -> None:
    assert parse_model_surface("gemini-3.7-flash") == ("chat", "gemini-3.7-flash")


@pytest.mark.parametrize(
    ("model_path", "expected"),
    [
        ("gemini-3.7-flash", ("chat", "gemini-3.7-flash")),
        ("models/gemini-3.7-flash", ("chat", "gemini-3.7-flash")),
        ("chat/gemini-3.7-flash", ("chat", "gemini-3.7-flash")),
        ("build/gemini-3.7-flash", ("build", "gemini-3.7-flash")),
    ],
)
def test_surface_prefix_matrix(model_path: str, expected: tuple[str, str]) -> None:
    assert parse_model_surface(model_path) == expected


@pytest.mark.parametrize(
    "model_path",
    [
        "pg/gemini-3.7-flash",
        "playground/gemini-3.7-flash",
        "official/gemini-3.7-flash",
    ],
)
def test_legacy_channel_alias_is_rejected(model_path: str) -> None:
    with pytest.raises(SurfaceUnavailableError):
        parse_model_surface(model_path)


def test_unconfigured_surface_is_rejected_with_capacity_code() -> None:
    with pytest.raises(SurfaceCapacityError) as info:
        parse_model_surface("build/gemini-3.7-flash", enabled_surfaces=("chat",))
    assert info.value.code == "surface_instance_busy"
    assert info.value.status_code == 503


def test_surface_conflict_is_explicit() -> None:
    with pytest.raises(ValueError):
        parse_model_surface(
            "build/gemini-3.7-flash",
            requested_surface="chat",
            enabled_surfaces=("chat", "build"),
        )


def test_rule_order_first_match_wins() -> None:
    rules = {
        "first": {"contains": ["special"], "required_tiers": ["Ultra"]},
        "second": {"contains": ["special"], "required_tiers": ["Pro"]},
    }
    assert resolve_required_tiers("special-model", rules=rules) == ("Ultra",)


def test_free_model_uses_free_lane() -> None:
    route = resolve_model_route("gemini-3.7-flash")
    assert route.paid is False
    assert route.lane == "free"
    assert route.required_tiers == ()


def test_paid_rule_moves_model_to_paid_lane() -> None:
    route = resolve_model_route(
        "custom-image-model",
        rules={"image": {"contains": ["image"], "required_tiers": ["Pro"]}},
    )
    assert route.paid is True
    assert route.lane == "paid"
    assert route.required_tier == "Pro"


def test_empty_rules_do_not_become_ultra_only(monkeypatch) -> None:
    monkeypatch.setattr(
        model_defaults, "get_configured_paid_model_rules", lambda *a, **k: {}
    )
    assert resolve_required_tiers("gemini-3.7-flash", rules={}) == ()
    route = resolve_model_route(
        "gemini-3.7-flash", rules={}, enabled_surfaces=("chat",)
    )
    assert route.paid is False


def test_discovery_rules_are_resolved_before_non_ultra_access_modes(
    monkeypatch,
) -> None:
    service = ModelDiscoveryService()
    service._cached_models = [
        {"id": "configured-model", "accessModes": [3], "paid": True}
    ]
    monkeypatch.setattr(
        model_defaults,
        "get_configured_paid_model_rules",
        lambda *a, **k: {
            "configured": {
                "contains": ["configured-model"],
                "required_tiers": ["Pro", "Ultra"],
            }
        },
    )
    assert service.get_required_tiers("configured-model") == ["Pro", "Ultra"]


def test_discovery_access_modes_without_rules() -> None:
    service = ModelDiscoveryService()
    service._cached_models = [
        {"id": "ultra-only", "accessModes": [4], "paid": True},
        {"id": "open-model", "accessModes": [1], "paid": False},
    ]
    assert service.get_required_tiers("ultra-only") == ["Ultra", "Ultra_20X"]
    assert service.get_required_tiers("open-model") == []


def test_tier_rank_satisfies_requirement() -> None:
    route = resolve_model_route(
        "tier-model",
        rules={"tier": {"exact": ["tier-model"], "required_tiers": ["Pro"]}},
    )
    assert tier_allows("Pro", route)
    assert tier_allows("Ultra", route)
    assert tier_allows("Ultra_20X", route)
    assert not tier_allows("Plus", route)
    assert not tier_allows("Free", route)


@pytest.mark.asyncio
async def test_carbon_requires_ultra_and_never_uses_pro_slot() -> None:
    """``gemini-4-carbon`` is Ultra-only, so a Pro-only pool rejects it (403)."""
    from aistudio_api.application.routing_pipeline import RoutingPipeline
    from aistudio_api.domain.errors import TierRequiredError
    from tests.unit.routing_fakes import FakeManager, make_instance

    model_discovery._cached_models = [
        {"id": "gemini-4-carbon", "accessModes": [4], "paid": True}
    ]
    manager = FakeManager(
        [make_instance("inst-pro", account_id="pro", tier="Pro")],
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    with pytest.raises(TierRequiredError) as info:
        await pipeline.begin(model_path="gemini-4-carbon")

    assert info.value.status_code == 403
    assert info.value.required_tier == "Ultra"
    assert info.value.current_tier == "Pro"
    assert manager.begin_calls == []
