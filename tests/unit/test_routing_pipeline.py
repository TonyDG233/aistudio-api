"""D05-021/023/026/033/036/038: candidate pool, capacity and retry closure."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.routing_eligibility import resolve_model_route
from aistudio_api.application.routing_pipeline import RoutingPipeline
from aistudio_api.domain.errors import (
    AuthError,
    BrowserTransportError,
    RequestError,
    SessionExpiredError,
    SurfaceCapacityError,
    SurfaceSwitchExhaustedError,
    UsageLimitExceeded,
)
from aistudio_api.domain.instances import InstanceState
from tests.unit.routing_fakes import (
    FakeHandle,
    FakeManager,
    make_context,
    make_instance,
    make_route,
)


def _pipeline(manager: FakeManager, *, max_switches: int = 2) -> RoutingPipeline:
    return RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": max_switches},
    )


class _CooldownRotator:
    def __init__(self, cooled: set[str]) -> None:
        self._cooled = cooled

    def is_account_available(
        self, account_id: str, *, model: str | None = None, surface: str | None = None
    ) -> bool:
        del model, surface
        return account_id not in self._cooled


@pytest.mark.asyncio
async def test_begin_wakes_sleeping_and_unstarted_candidates() -> None:
    manager = FakeManager(
        [
            make_instance(
                "inst-a",
                account_id="acc-a",
                state=InstanceState.SLEEPING,
                last_used=10.0,
                number=1,
            ),
            make_instance(
                "inst-b",
                account_id="acc-b",
                state=InstanceState.UNSTARTED,
                last_used=5.0,
                number=2,
            ),
            make_instance(
                "inst-c", account_id="acc-c", last_used=20.0, number=3
            ),
        ],
        current_instance_id="inst-a",
    )
    context = await _pipeline(manager).begin(model_path="gemini-3.7-flash")
    assert manager.wake_calls == [(False, "chat")]
    assert [item.instance_id for item in context.candidates] == [
        "inst-a",
        "inst-b",
        "inst-c",
    ]
    assert all(item.state is InstanceState.ACTIVE for item in context.candidates)


def test_warm_ready_is_only_a_tie_breaker() -> None:
    pipeline = _pipeline(FakeManager([]))
    route = make_route("gemini-3.7-flash")
    sleeping = make_instance(
        "inst-sleep",
        state=InstanceState.STARTING,
        last_used=1.0,
        number=1,
    )
    ready = make_instance("inst-ready", state=InstanceState.ACTIVE, last_used=1.0, number=2)
    ordered = pipeline._order(route, [sleeping, ready])
    assert [item.id for item in ordered] == ["inst-ready", "inst-sleep"]


@pytest.mark.asyncio
async def test_acquire_wakes_and_releases_active_request() -> None:
    instance = make_instance(
        "inst-sleep", account_id="acc", state=InstanceState.SLEEPING
    )
    manager = FakeManager([instance])
    pipeline = _pipeline(manager)
    context = make_context([instance])
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    assert manager.woken == ["inst-sleep"]
    assert manager.begin_calls == ["inst-sleep"]
    await pipeline.release(attempt)
    assert isinstance(attempt.handle, FakeHandle)
    assert attempt.handle.released is True


@pytest.mark.asyncio
async def test_chat_capacity_uses_quota_code_and_build_uses_busy_code() -> None:
    with pytest.raises(SurfaceCapacityError) as chat_info:
        await _pipeline(FakeManager([])).begin(model_path="gemini-3.7-flash")
    assert chat_info.value.status_code == 503
    assert chat_info.value.code == "surface_quota_exceeded"

    with pytest.raises(SurfaceCapacityError) as build_info:
        await _pipeline(FakeManager([])).begin(model_path="build/gemini-3.7-flash")
    assert build_info.value.status_code == 503
    assert build_info.value.code == "surface_instance_busy"


@pytest.mark.asyncio
async def test_pipeline_uses_real_instance_manager_active_requests(tmp_path) -> None:
    from tests.unit.test_instance_manager import (
        FakeCDP,
        _free_spec,
        _manager,
        _paid_spec,
        _store_with_accounts,
    )

    store, _accounts = _store_with_accounts(
        tmp_path,
        ("free", "free@example.com", "Free"),
        ("pro", "pro@example.com", "Pro"),
    )
    manager = _manager(
        store,
        FakeCDP(),
        specs=[_free_spec(), _paid_spec()],
        max_contexts=2,
    )
    await manager.startup()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    free_context = await pipeline.begin(model_path="gemini-3.7-flash")
    assert [item.tier for item in free_context.candidates] == ["Free"]
    attempt = await pipeline.acquire(free_context)
    assert attempt is not None
    assert manager.eligible_instances(paid=False)[0].in_flight == 1
    await pipeline.release(attempt)
    assert manager.eligible_instances(paid=False)[0].in_flight == 0

    paid_context = await pipeline.begin(model_path="gemini-4-argon")
    assert [item.tier for item in paid_context.candidates] == ["Pro"]


@pytest.mark.asyncio
async def test_cooled_accounts_are_not_candidates() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast(Any, _CooldownRotator({"acc-1"})),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    assert [item.instance_id for item in context.candidates] == ["inst-2"]

    pipeline_all_cooled = RoutingPipeline(
        instance_manager=manager,
        rotator=cast(Any, _CooldownRotator({"acc-1", "acc-2"})),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    with pytest.raises(SurfaceCapacityError) as info:
        await pipeline_all_cooled.begin(model_path="gemini-3.7-flash")
    assert info.value.code == "surface_quota_exceeded"


@pytest.mark.asyncio
async def test_invalid_paid_instance_is_capacity_not_tier_error() -> None:
    manager = FakeManager(
        [
            make_instance(
                "inst-bad",
                account_id="acc-1",
                tier="Pro",
                state=InstanceState.INVALID,
            )
        ]
    )
    with pytest.raises(SurfaceCapacityError) as info:
        await _pipeline(manager).begin(model_path="gemini-4-argon")
    assert info.value.code == "surface_quota_exceeded"


@pytest.mark.asyncio
async def test_pro_account_without_paid_instance_is_capacity() -> None:
    class _Accounts:
        def list_accounts(self):
            return [SimpleNamespace(tier="Pro", enabled=True, expired=False)]

    manager = FakeManager(
        [make_instance("inst-free", account_id="free", tier="Free")]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        account_store=_Accounts(),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    with pytest.raises(SurfaceCapacityError):
        await pipeline.begin(model_path="gemini-4-argon")


@pytest.mark.asyncio
async def test_429_retries_same_then_switches_then_503() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", tier="Pro", number=1),
            make_instance("inst-2", account_id="acc-2", tier="Pro", number=2),
        ]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-4-argon")

    first = await pipeline.acquire(context)
    assert first is not None
    error = UsageLimitExceeded("429")
    assert (
        await pipeline.handle_error(context, first, error, has_yielded_data=False)
        == "retry_same"
    )
    assert context.retry_same is True
    assert first.same_retries == 1
    assert context.budget.switches == 0

    assert (
        await pipeline.handle_error(context, first, error, has_yielded_data=False)
        == "switch"
    )
    assert context.budget.switches == 1
    assert manager.wake_calls[-1] == (True, "chat")
    await pipeline.release(first)

    second = await pipeline.acquire(context)
    assert second is not None
    assert second.instance_id == "inst-2"
    assert (
        await pipeline.handle_error(context, second, error, has_yielded_data=False)
        == "retry_same"
    )
    with pytest.raises(SurfaceSwitchExhaustedError) as info:
        await pipeline.handle_error(context, second, error, has_yielded_data=False)
    assert info.value.status_code == 503
    assert info.value.code == "surface_switch_exhausted"
    assert not isinstance(info.value, UsageLimitExceeded)


@pytest.mark.asyncio
async def test_no_switch_after_body_yielded() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", tier="Pro", number=1),
            make_instance("inst-2", account_id="acc-2", tier="Pro", number=2),
        ]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    with pytest.raises(UsageLimitExceeded):
        await pipeline.handle_error(
            context, attempt, UsageLimitExceeded("late 429"), has_yielded_data=True
        )
    assert context.budget.switches == 0
    assert context.failed_accounts == set()


@pytest.mark.asyncio
async def test_204_and_transient_404_retry_same_instance() -> None:
    manager = FakeManager([make_instance("inst-1", account_id="acc")])
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    assert (
        await pipeline.handle_error(
            context, attempt, RequestError(204, "no content"), has_yielded_data=False
        )
        == "retry_same"
    )
    with pytest.raises(BrowserTransportError):
        await pipeline.handle_error(
            context, attempt, RequestError(204, "no content"), has_yielded_data=False
        )

    transient = RequestError(
        404,
        "Ambiguous request for service '' and method "
        "'/GenerativeService.GenerateContent'.",
    )
    context2 = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt2 = await pipeline.acquire(context2)
    assert attempt2 is not None
    assert (
        await pipeline.handle_error(
            context2, attempt2, transient, has_yielded_data=False
        )
        == "retry_same"
    )
    with pytest.raises(RequestError):
        await pipeline.handle_error(
            context2,
            attempt2,
            RequestError(404, "Model not found"),
            has_yielded_data=False,
        )
    assert context2.budget.switches == 0


@pytest.mark.asyncio
async def test_switch_budget_zero_exhausts_immediately() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", tier="Pro", number=1),
            make_instance("inst-2", account_id="acc-2", tier="Pro", number=2),
        ]
    )
    pipeline = _pipeline(manager, max_switches=0)
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    assert (
        await pipeline.handle_error(
            context, attempt, UsageLimitExceeded("429"), has_yielded_data=False
        )
        == "retry_same"
    )
    with pytest.raises(SurfaceSwitchExhaustedError):
        await pipeline.handle_error(
            context, attempt, UsageLimitExceeded("429"), has_yielded_data=False
        )


@pytest.mark.asyncio
async def test_session_expired_switch_then_success() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    first = await pipeline.acquire(context)
    assert first is not None
    assert (
        await pipeline.handle_error(
            context,
            first,
            SessionExpiredError("expired"),
            has_yielded_data=False,
        )
        == "switch"
    )
    assert context.budget.switches == 1
    await pipeline.release(first)
    second = await pipeline.acquire(context)
    assert second is not None
    assert second.instance_id == "inst-2"
    await pipeline.release(second)


@pytest.mark.asyncio
async def test_session_expired_exhausted_raises_original() -> None:
    pipeline = _pipeline(
        FakeManager([make_instance("inst-1", account_id="acc-1")])
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    with pytest.raises(SessionExpiredError):
        await pipeline.handle_error(
            context,
            attempt,
            SessionExpiredError("expired"),
            has_yielded_data=False,
        )


@pytest.mark.asyncio
async def test_auth_error_switch_then_success() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    first = await pipeline.acquire(context)
    assert first is not None
    assert (
        await pipeline.handle_error(
            context, first, AuthError("auth failed"), has_yielded_data=False
        )
        == "switch"
    )
    await pipeline.release(first)
    second = await pipeline.acquire(context)
    assert second is not None
    assert second.instance_id == "inst-2"
    await pipeline.release(second)


@pytest.mark.asyncio
async def test_auth_error_exhausted_raises_original() -> None:
    pipeline = _pipeline(
        FakeManager([make_instance("inst-1", account_id="acc-1")])
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    with pytest.raises(AuthError):
        await pipeline.handle_error(
            context, attempt, AuthError("auth failed"), has_yielded_data=False
        )


@pytest.mark.asyncio
async def test_session_hang_switch_then_success() -> None:
    manager = FakeManager(
        [
            make_instance("inst-1", account_id="acc-1", number=1),
            make_instance("inst-2", account_id="acc-2", number=2),
        ]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    first = await pipeline.acquire(context)
    assert first is not None
    assert (
        await pipeline.handle_error(
            context,
            first,
            BrowserTransportError("cdp disconnected"),
            has_yielded_data=False,
        )
        == "switch"
    )
    await pipeline.release(first)
    second = await pipeline.acquire(context)
    assert second is not None
    assert second.instance_id == "inst-2"
    await pipeline.release(second)


@pytest.mark.asyncio
async def test_session_hang_exhausted_raises_original() -> None:
    pipeline = _pipeline(
        FakeManager([make_instance("inst-1", account_id="acc-1")])
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    with pytest.raises(BrowserTransportError):
        await pipeline.handle_error(
            context,
            attempt,
            BrowserTransportError("cdp disconnected"),
            has_yielded_data=False,
        )


def test_paid_model_build_is_rejected_by_paid_chat_only_rule() -> None:
    rules = {
        "argon": {
            "contains": ["argon"],
            "required_tiers": ["Pro", "Ultra", "Ultra_20X"],
        }
    }
    with pytest.raises(SurfaceCapacityError) as info:
        resolve_model_route(
            "gemini-4-argon",
            requested_surface="build",
            enabled_surfaces=("chat", "build"),
            rules=rules,
        )
    assert info.value.code == "surface_instance_busy"
    assert "[ROUTING-RULE: PAID_CHAT_ONLY]" in str(info.value)
