"""D-04/D-06: tier admission rejects locally and never preempts at request time."""

from __future__ import annotations

import pytest

from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import AuthError, TierRequiredError
from aistudio_api.domain.instances import InstanceState
from tests.unit.routing_fakes import (
    FakeManager,
    make_context,
    make_instance,
)


def _pipeline(manager: FakeManager) -> RoutingPipeline:
    return RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )


@pytest.mark.asyncio
async def test_free_only_pool_rejects_paid_model_without_switching() -> None:
    manager = FakeManager(
        [make_instance("inst-free", account_id="free", tier="Free")]
    )
    with pytest.raises(TierRequiredError) as info:
        await _pipeline(manager).begin(model_path="gemini-4-argon")

    error = info.value
    assert error.model == "gemini-4-argon"
    assert error.required_tier == "Pro"
    assert error.current_tier == "Free"
    assert error.tier == "Pro"
    assert error.as_fields() == {
        "tier": "Pro",
        "model": "gemini-4-argon",
        "required_tier": "Pro",
        "current_tier": "Free",
    }
    assert manager.begin_calls == []


@pytest.mark.asyncio
async def test_plus_pool_is_paid_lane_but_below_pro_requirement() -> None:
    manager = FakeManager(
        [
            make_instance(
                "inst-plus",
                account_id="plus",
                tier="Plus",
                state=InstanceState.ACTIVE,
            )
        ]
    )
    with pytest.raises(TierRequiredError) as info:
        await _pipeline(manager).begin(model_path="gemini-4-argon")

    assert info.value.current_tier == "Plus"
    assert info.value.required_tier == "Pro"
    assert manager.begin_calls == []


@pytest.mark.asyncio
async def test_ultra_only_model_rejects_pro_instance() -> None:
    manager = FakeManager(
        [make_instance("inst-pro", account_id="pro", tier="Pro")]
    )
    with pytest.raises(TierRequiredError) as info:
        await _pipeline(manager).begin(model_path="veo-2.0-generate-001")

    assert info.value.required_tier == "Ultra"
    assert info.value.current_tier == "Pro"
    assert manager.begin_calls == []


@pytest.mark.asyncio
async def test_paid_model_without_rule_requires_pro_not_ultra() -> None:
    """An empty rule must not be interpreted as Ultra-only (C06/E-07)."""
    manager = FakeManager(
        [make_instance("inst-pro", account_id="pro", tier="Pro")]
    )
    context = await _pipeline(manager).begin(model_path="gemini-3-flash-preview")
    assert context.route.paid is True
    assert context.route.required_tier == "Pro"
    assert context.candidates[0].instance_id == "inst-pro"


@pytest.mark.asyncio
async def test_upstream_auth_403_on_wrong_tier_never_switches() -> None:
    manager = FakeManager(
        [
            make_instance("plus-inst", account_id="plus", tier="Plus", number=1),
            make_instance("pro-inst", account_id="pro", tier="Pro", number=2),
        ]
    )
    pipeline = _pipeline(manager)
    context = make_context(
        [make_instance("plus-inst", account_id="plus", tier="Plus")],
        paid=True,
        required_tiers=("Pro",),
    )
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    with pytest.raises(TierRequiredError) as info:
        await pipeline.handle_error(
            context, attempt, AuthError("upstream tier 403")
        )
    assert info.value.required_tier == "Pro"
    assert info.value.current_tier == "Plus"
    assert context.budget.switches == 0
    assert manager.begin_calls == ["plus-inst"]
