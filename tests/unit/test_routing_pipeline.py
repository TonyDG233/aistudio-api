"""D05-021/023/026/033/036/038: candidate pool, capacity and retry closure."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

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
    TierRequiredError,
    UsageLimitExceeded,
)
from aistudio_api.domain.instances import InstanceState
from aistudio_api.infrastructure.waa.signer_client import SignerTimeoutError
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
            make_instance("inst-c", account_id="acc-c", last_used=20.0, number=3),
        ],
        current_instance_id="inst-a",
    )
    context = await _pipeline(manager).begin(model_path="gemini-3.7-flash")
    assert manager.wake_calls == [(False, "chat")]
    # Sticky is now only a final tie-breaker: balanced last_used/LRU wins first.
    assert [item.instance_id for item in context.candidates] == [
        "inst-b",
        "inst-a",
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
    ready = make_instance(
        "inst-ready", state=InstanceState.ACTIVE, last_used=1.0, number=2
    )
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
async def test_two_idle_free_slots_distribute_sequential_requests(tmp_path) -> None:
    from tests.unit.test_instance_manager import (
        FakeCDP,
        _free_spec,
        _manager,
        _store_with_accounts,
    )

    store, _accounts = _store_with_accounts(
        tmp_path,
        ("free1", "free1@example.com", "Free"),
        ("free2", "free2@example.com", "Free"),
    )
    manager = _manager(
        store,
        FakeCDP(),
        specs=[_free_spec(count=2)],
        max_contexts=2,
    )
    await manager.startup()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    used: list[str] = []
    for _ in range(4):
        context = await pipeline.begin(model_path="gemini-3.7-flash")
        attempt = await pipeline.acquire(context)
        assert attempt is not None
        used.append(attempt.instance_id)
        await pipeline.release(attempt)

    assert used[0] != used[1]
    assert set(used) == {"inst-1", "inst-2"}
    assert used.count("inst-1") == 2
    assert used.count("inst-2") == 2


@pytest.mark.asyncio
async def test_paid_route_prefers_pro_slot_before_idle_ultra() -> None:
    manager = FakeManager(
        [
            make_instance(
                "inst-ultra",
                account_id="ultra",
                tier="Ultra",
                number=1,
                last_used=1.0,
            ),
            make_instance(
                "inst-pro",
                account_id="pro",
                tier="Pro",
                number=2,
                last_used=2.0,
            ),
        ]
    )
    context = await _pipeline(manager).begin(model_path="gemini-4-argon")
    assert [item.instance_id for item in context.candidates] == [
        "inst-pro",
        "inst-ultra",
    ]


def test_order_uses_candidate_account_tier_rank() -> None:
    pipeline = _pipeline(FakeManager([]))
    route = make_route("gemini-4-argon", paid=True, required_tiers=("Pro",))
    ultra = make_instance(
        "inst-ultra", account_id="ultra", tier="Ultra", number=1, last_used=0.0
    )
    pro = make_instance(
        "inst-pro", account_id="pro", tier="Pro", number=2, last_used=9.0
    )

    ordered = pipeline._order(route, [ultra, pro])

    assert [item.id for item in ordered] == ["inst-pro", "inst-ultra"]


@pytest.mark.asyncio
async def test_busy_pro_slot_falls_back_to_idle_ultra() -> None:
    manager = FakeManager(
        [
            make_instance(
                "inst-pro",
                account_id="pro",
                tier="Pro",
                number=1,
                last_used=1.0,
                in_flight=1,
            ),
            make_instance(
                "inst-ultra",
                account_id="ultra",
                tier="Ultra",
                number=2,
                last_used=2.0,
            ),
        ]
    )
    context = await _pipeline(manager).begin(model_path="gemini-4-argon")
    assert [item.instance_id for item in context.candidates] == [
        "inst-ultra",
        "inst-pro",
    ]


@pytest.mark.asyncio
async def test_429_dead_slot_is_rebound_while_request_continues_on_other_slot(
    monkeypatch,
) -> None:
    from aistudio_api.application import routing_pipeline as routing_pipeline_module
    from tests.unit.routing_fakes import RebindFakeManager

    class _Rotator:
        def get_account_stats(self, account_id: str):
            del account_id
            return SimpleNamespace(
                total_requests=0,
                model_requests_total=lambda _model: 0,
            )

        def is_account_available(
            self, account_id: str, *, model=None, surface=None
        ) -> bool:
            del account_id, model, surface
            return True

        def select_rebind_account(self, **kwargs):
            excluded = set(kwargs.get("exclude_account_ids") or ())
            if "free-c" in excluded:
                return None
            return SimpleNamespace(id="free-c", tier="Free")

        def record_rate_limited(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_auth_error(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_session_expired(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_success(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_error(self, *args, **kwargs) -> None:
            del args, kwargs

    class _Client:
        def __init__(self, session, *, account_id, tier, surface) -> None:
            del session
            self.account_id = account_id
            self.tier = tier
            self.surface = surface
            self.clear_calls = 0

        def clear_templates(self) -> bool:
            self.clear_calls += 1
            return True

    monkeypatch.setattr(routing_pipeline_module, "InstanceRequestClient", _Client)
    manager = RebindFakeManager(
        [
            make_instance(
                "inst-1", account_id="free-a", tier="Free", number=1, last_used=1.0
            ),
            make_instance(
                "inst-2", account_id="free-b", tier="Free", number=2, last_used=2.0
            ),
        ],
        current_instance_id="inst-1",
    )
    manager.account_tiers["free-c"] = "Free"
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", _Rotator()),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    first = await pipeline.acquire(context)
    assert first is not None
    assert first.instance_id == "inst-1"
    error = UsageLimitExceeded("429")

    assert (
        await pipeline.handle_error(context, first, error, has_yielded_data=False)
        == "retry_same"
    )
    assert (
        await pipeline.handle_error(context, first, error, has_yielded_data=False)
        == "switch"
    )
    # The failed slot was healed immediately, but this request continues on
    # inst-2 instead of re-taking the slot it just failed.
    assert manager.reassign_calls == [("inst-1", "free-c")]
    assert first.handle.released is True  # type: ignore[attr-defined]
    assert first.client.clear_calls >= 1  # type: ignore[attr-defined]
    await pipeline.release(first)

    second = await pipeline.acquire(context)
    assert second is not None
    assert second.instance_id == "inst-2"
    await pipeline.release(second)

    by_id = {item.id: item for item in manager.instances}
    assert by_id["inst-1"].account_id == "free-c"
    assert by_id["inst-2"].account_id == "free-b"
    assert {by_id["inst-1"].tier, by_id["inst-2"].tier} == {"Free"}
    assert all(item.account_id != "free-a" for item in manager.instances)
    assert manager.active_requests == 0


@pytest.mark.asyncio
async def test_single_slot_allows_successive_429_rebinds_within_budget(
    monkeypatch,
) -> None:
    from aistudio_api.application import routing_pipeline as routing_pipeline_module
    from tests.unit.routing_fakes import RebindFakeManager

    class _Rotator:
        def __init__(self) -> None:
            self.exclusions: list[set[str]] = []

        def get_account_stats(self, account_id: str):
            del account_id
            return SimpleNamespace(
                total_requests=0,
                model_requests_total=lambda _model: 0,
            )

        def is_account_available(
            self, account_id: str, *, model=None, surface=None
        ) -> bool:
            del account_id, model, surface
            return True

        def select_rebind_account(self, **kwargs):
            excluded: set[str] = set(kwargs.get("exclude_account_ids") or ())
            self.exclusions.append(excluded)
            for account_id in ("free-b", "free-c"):
                if account_id not in excluded:
                    return SimpleNamespace(id=account_id, tier="Free")
            return None

        def record_rate_limited(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_auth_error(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_session_expired(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_success(self, *args, **kwargs) -> None:
            del args, kwargs

        def record_error(self, *args, **kwargs) -> None:
            del args, kwargs

    class _Client:
        def __init__(self, session, *, account_id, tier, surface) -> None:
            del session
            self.account_id = account_id
            self.tier = tier
            self.surface = surface

        def clear_templates(self) -> bool:
            return False

    monkeypatch.setattr(routing_pipeline_module, "InstanceRequestClient", _Client)
    manager = RebindFakeManager(
        [
            make_instance(
                "inst-1", account_id="free-a", tier="Free", number=1, last_used=1.0
            )
        ],
        current_instance_id="inst-1",
    )
    manager.account_tiers.update({"free-b": "Free", "free-c": "Free"})
    rotator = _Rotator()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    error = UsageLimitExceeded("429")

    first = await pipeline.acquire(context)
    assert first is not None
    assert first.account_id == "free-a"
    assert (
        await pipeline.handle_error(context, first, error, has_yielded_data=False)
        == "retry_same"
    )
    assert (
        await pipeline.handle_error(context, first, error, has_yielded_data=False)
        == "switch"
    )
    await pipeline.release(first)

    second = await pipeline.acquire(context)
    assert second is not None
    assert second.account_id == "free-b"
    assert (
        await pipeline.handle_error(context, second, error, has_yielded_data=False)
        == "retry_same"
    )
    assert (
        await pipeline.handle_error(context, second, error, has_yielded_data=False)
        == "switch"
    )
    await pipeline.release(second)

    third = await pipeline.acquire(context)
    assert third is not None
    assert third.account_id == "free-c"
    pipeline.record_success(context, third)
    await pipeline.release(third)

    rebind_accounts = [
        account_id for _instance_id, account_id in manager.reassign_calls
    ]
    assert rebind_accounts == ["free-b", "free-c"]
    assert len(rebind_accounts) == len(set(rebind_accounts))
    assert context.budget.switches == 2
    assert context.budget.max_switches == 2
    assert context.attempted_accounts == {"free-a", "free-b", "free-c"}
    assert {item.account_id for item in manager.instances} == {"free-c"}
    assert rotator.exclusions[0] == {"free-a"}
    assert rotator.exclusions[1] == {"free-a", "free-b"}

    # The third 429 has no switch budget left and must not loop or re-bind.
    assert (
        await pipeline.handle_error(context, third, error, has_yielded_data=False)
        == "retry_same"
    )
    with pytest.raises(SurfaceSwitchExhaustedError):
        await pipeline.handle_error(context, third, error, has_yielded_data=False)
    assert manager.reassign_calls == [
        ("inst-1", "free-b"),
        ("inst-1", "free-c"),
    ]


@pytest.mark.asyncio
async def test_429_same_retry_sleeps_configured_delay(monkeypatch) -> None:
    """VPS #1: a 429 same-account retry backs off before hitting upstream again."""
    from aistudio_api.application import routing_pipeline as routing_pipeline_module

    sleep_mock = AsyncMock()
    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 2.0,
        },
    )
    context = await pipeline.begin(model_path="gemini-4-argon")
    assert context.retry_same_delay_sec == 2.0
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    outcome = await pipeline.handle_error(
        context,
        attempt,
        UsageLimitExceeded("HTTP 429: RPM burst"),
        has_yielded_data=False,
    )

    assert outcome == "retry_same"
    sleep_mock.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_routing_settings_failure_uses_default_retry_delay() -> None:
    """A broken settings provider must not silently disable the 429 backoff."""
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )

    def broken_provider() -> dict[str, object]:
        raise RuntimeError("config store unavailable")

    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=broken_provider,
    )
    context = await pipeline.begin(model_path="gemini-4-argon")

    assert context.retry_same_delay_sec == 2.0


@pytest.mark.asyncio
async def test_429_same_retry_skips_delay_when_zero(monkeypatch) -> None:
    from aistudio_api.application import routing_pipeline as routing_pipeline_module

    sleep_mock = AsyncMock()
    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 0.0,
        },
    )
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    outcome = await pipeline.handle_error(
        context, attempt, UsageLimitExceeded("429"), has_yielded_data=False
    )

    assert outcome == "retry_same"
    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_retry_same_never_sleeps(monkeypatch) -> None:
    """The 403 re-sign retry must not apply the 429 backoff."""
    from aistudio_api.application import routing_pipeline as routing_pipeline_module

    sleep_mock = AsyncMock()
    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 2.0,
        },
    )
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    assert (
        await pipeline.handle_error(
            context,
            attempt,
            AuthError("HTTP 403: WAA denied"),
            has_yielded_data=False,
        )
        == "retry_same"
    )
    sleep_mock.assert_not_awaited()


@pytest.mark.parametrize("status", [204, 503])
@pytest.mark.asyncio
async def test_transient_request_error_retry_same_sleeps_configured_delay(
    monkeypatch, status: int
) -> None:
    """Q-8: transient RequestError retries back off like the 429 branch."""
    from aistudio_api.application import routing_pipeline as routing_pipeline_module

    sleep_mock = AsyncMock()
    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "retry_same_delay_sec": 2.0,
        },
    )
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    outcome = await pipeline.handle_error(
        context,
        attempt,
        RequestError(status, "upstream hiccup"),
        has_yielded_data=False,
    )

    assert outcome == "retry_same"
    sleep_mock.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_429_warning_includes_model_and_upstream_reason(caplog) -> None:
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    with caplog.at_level(logging.WARNING):
        outcome = await pipeline.handle_error(
            context,
            attempt,
            UsageLimitExceeded("HTTP 429: RPM burst"),
            has_yielded_data=False,
        )

    assert outcome == "retry_same"
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "gemini-4-argon" in message and "HTTP 429: RPM burst" in message
        for message in messages
    )
    assert all("SAPISID" not in message for message in messages)


@pytest.mark.asyncio
async def test_429_warning_redacts_cookie_and_bearer(caplog) -> None:
    """Reason summaries must not leak cookie or Bearer secrets to warnings."""
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    secret = "SENTINELSECRETCOOKIE123"

    with caplog.at_level(logging.WARNING):
        outcome = await pipeline.handle_error(
            context,
            attempt,
            UsageLimitExceeded(
                f"HTTP 429: Cookie: SAPISID={secret}; Authorization: Bearer {secret}"
            ),
            has_yielded_data=False,
        )

    assert outcome == "retry_same"
    messages = [record.getMessage() for record in caplog.records]
    assert any("gemini-4-argon" in message for message in messages)
    assert all(secret not in message for message in messages)


@pytest.mark.asyncio
async def test_403_warning_includes_model_and_upstream_reason(caplog) -> None:
    manager = FakeManager(
        [make_instance("inst-1", account_id="acc-1", tier="Pro", number=1)]
    )
    pipeline = _pipeline(manager)
    context = await pipeline.begin(model_path="gemini-4-argon")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    with caplog.at_level(logging.WARNING):
        outcome = await pipeline.handle_error(
            context,
            attempt,
            AuthError("HTTP 403: WAA denied"),
            has_yielded_data=False,
        )

    assert outcome == "retry_same"
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "gemini-4-argon" in message and "HTTP 403: WAA denied" in message
        for message in messages
    )
    assert all("SAPISID" not in message for message in messages)


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

    manager = FakeManager([make_instance("inst-free", account_id="free", tier="Free")])
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
    with pytest.raises(BrowserTransportError) as info:
        await pipeline.handle_error(
            context, attempt, RequestError(204, "no content"), has_yielded_data=False
        )
    assert "同账号重试后仍为空" in str(info.value)

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
    pipeline = _pipeline(FakeManager([make_instance("inst-1", account_id="acc-1")]))
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
    # 首次 403：同账号重新签名原位重试，不立即切号。
    assert (
        await pipeline.handle_error(
            context, first, AuthError("auth failed"), has_yielded_data=False
        )
        == "retry_same"
    )
    # 二次 403：才切换到健康账号。
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
    pipeline = _pipeline(FakeManager([make_instance("inst-1", account_id="acc-1")]))
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    # 首次 403 允许同账号重新签名重试一次。
    assert (
        await pipeline.handle_error(
            context, attempt, AuthError("auth failed"), has_yielded_data=False
        )
        == "retry_same"
    )
    # 二次 403 且无备用账号：抛出原始 AuthError。
    with pytest.raises(AuthError):
        await pipeline.handle_error(
            context, attempt, AuthError("auth failed"), has_yielded_data=False
        )


@pytest.mark.asyncio
async def test_paid_tier_required_error_raises_without_switch() -> None:
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
    wake_calls_before = list(manager.wake_calls)
    error = TierRequiredError(
        model="gemini-4-argon",
        tier="Pro",
        required_tier="Ultra",
        current_tier="Pro",
    )

    with pytest.raises(TierRequiredError):
        await pipeline.handle_error(context, attempt, error, has_yielded_data=False)

    assert context.budget.switches == 0
    assert context.failed_accounts == set()
    assert manager.wake_calls == wake_calls_before


@pytest.mark.asyncio
async def test_auth_error_then_429_follows_same_instance_policy() -> None:
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

    # 首次 403：同账号重新签名原位重试，不切号。
    assert (
        await pipeline.handle_error(
            context, first, AuthError("auth failed"), has_yielded_data=False
        )
        == "retry_same"
    )
    assert first.same_retries == 1
    assert context.budget.switches == 0

    # 随后 429 复用同一重试预算：立即切号，而不是再次原位重试。
    assert (
        await pipeline.handle_error(
            context, first, UsageLimitExceeded("429"), has_yielded_data=False
        )
        == "switch"
    )
    assert context.budget.switches == 1
    await pipeline.release(first)

    second = await pipeline.acquire(context)
    assert second is not None
    assert second.instance_id == "inst-2"
    # 新实例上的 429 重新获得一次同账号重试机会。
    assert (
        await pipeline.handle_error(
            context, second, UsageLimitExceeded("429"), has_yielded_data=False
        )
        == "retry_same"
    )
    await pipeline.release(second)


@pytest.mark.asyncio
async def test_session_hang_retries_same_account_without_switch() -> None:
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
    hang = BrowserTransportError("cdp disconnected")

    # 首次传输中断：同账号重试 1 次，绝不切号/换绑。
    assert (
        await pipeline.handle_error(context, first, hang, has_yielded_data=False)
        == "retry_same"
    )
    assert first.same_retries == 1
    assert context.budget.switches == 0
    assert context.failed_accounts == set()

    # 同账号重试后仍然失败：直接抛出原异常，绝不触发 _switch_or_raise。
    with pytest.raises(BrowserTransportError):
        await pipeline.handle_error(context, first, hang, has_yielded_data=False)
    assert context.budget.switches == 0
    assert context.failed_accounts == set()
    await pipeline.release(first)


@pytest.mark.asyncio
async def test_session_hang_after_body_yielded_raises_without_retry() -> None:
    pipeline = _pipeline(FakeManager([make_instance("inst-1", account_id="acc-1")]))
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    with pytest.raises(BrowserTransportError):
        await pipeline.handle_error(
            context,
            attempt,
            BrowserTransportError("cdp disconnected"),
            has_yielded_data=True,
        )
    assert attempt.same_retries == 0
    assert context.budget.switches == 0
    assert context.failed_accounts == set()


@pytest.mark.asyncio
async def test_signer_timeout_stream_error_never_switches() -> None:
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
    timeout = BrowserTransportError(
        "browserless rpc stream failed: waa-signer 请求超时"
    )

    assert (
        await pipeline.handle_error(context, first, timeout, has_yielded_data=False)
        == "retry_same"
    )
    assert first.same_retries == 1
    assert context.budget.switches == 0
    assert context.failed_accounts == set()

    with pytest.raises(BrowserTransportError):
        await pipeline.handle_error(context, first, timeout, has_yielded_data=False)
    assert context.budget.switches == 0
    assert context.failed_accounts == set()
    await pipeline.release(first)


@pytest.mark.asyncio
async def test_raw_signer_timeout_error_never_switches() -> None:
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
    timeout = SignerTimeoutError("waa-signer 请求超时")

    # 裸 SignerTimeoutError 也享受一次同账号重试，绝不切号。
    assert (
        await pipeline.handle_error(context, attempt, timeout, has_yielded_data=False)
        == "retry_same"
    )
    assert attempt.same_retries == 1
    assert context.budget.switches == 0
    assert context.failed_accounts == set()

    with pytest.raises(SignerTimeoutError):
        await pipeline.handle_error(
            context,
            attempt,
            timeout,
            has_yielded_data=False,
        )
    assert context.budget.switches == 0
    assert context.failed_accounts == set()
    await pipeline.release(attempt)


class _HealingRotator:
    """Rotator fake exposing availability + rebind selection for heal tests."""

    def __init__(
        self, unavailable: set[str] | None = None, target: str | None = "healthy"
    ) -> None:
        self.unavailable = set(unavailable or ())
        self.target = target
        self.select_exclusions: list[set[str]] = []

    def get_account_stats(self, account_id: str):
        del account_id
        return SimpleNamespace(
            total_requests=0,
            model_requests_total=lambda _model: 0,
        )

    def is_account_available(
        self, account_id: str, *, model=None, surface=None
    ) -> bool:
        del model, surface
        return account_id not in self.unavailable

    def select_rebind_account(self, **kwargs):
        excluded: set[str] = set(kwargs.get("exclude_account_ids") or ())
        self.select_exclusions.append(excluded)
        if self.target is None or self.target in excluded:
            return None
        return SimpleNamespace(id=self.target, tier="Free")

    def record_rate_limited(self, *args, **kwargs) -> None:
        del args, kwargs

    def record_auth_error(self, *args, **kwargs) -> None:
        del args, kwargs

    def record_session_expired(self, *args, **kwargs) -> None:
        del args, kwargs

    def record_success(self, *args, **kwargs) -> None:
        del args, kwargs

    def record_error(self, *args, **kwargs) -> None:
        del args, kwargs


@pytest.mark.asyncio
async def test_begin_heals_idle_slot_left_on_unavailable_account() -> None:
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    rotator = _HealingRotator({"dead"})
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    context = await pipeline.begin(model_path="gemini-3.7-flash")

    assert [candidate.account_id for candidate in context.candidates] == ["healthy"]
    assert manager.reassign_calls == [("inst-1", "healthy")]


@pytest.mark.asyncio
async def test_begin_does_not_heal_slot_with_active_requests() -> None:
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    manager.active_by_instance["inst-1"] = 1
    rotator = _HealingRotator({"dead"})
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    with pytest.raises(SurfaceCapacityError):
        await pipeline.begin(model_path="gemini-3.7-flash")

    assert manager.reassign_calls == []


@pytest.mark.asyncio
async def test_heal_rereads_live_account_and_skips_rebound_slot() -> None:
    from dataclasses import replace

    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    rotator = _HealingRotator({"dead"})
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    stale = manager.instances[0]
    # A concurrent request already rebound the slot to a healthy account.
    manager.instances[0] = replace(manager.instances[0], account_id="healthy")

    healed = await pipeline._heal_unavailable_instances(  # type: ignore[attr-defined]
        make_route("gemini-3.7-flash"), [stale]
    )

    assert healed == 0
    assert manager.reassign_calls == []


@pytest.mark.asyncio
async def test_heal_skips_slot_that_became_busy_after_snapshot() -> None:
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    rotator = _HealingRotator({"dead"})
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    stale = manager.instances[0]
    # Another model on the same slot acquired an in-flight request after the
    # candidate snapshot was taken.  Healing must never drain it.
    manager.active_by_instance["inst-1"] = 1

    healed = await pipeline._heal_unavailable_instances(  # type: ignore[attr-defined]
        make_route("gemini-3.7-flash"), [stale]
    )

    assert healed == 0
    assert manager.reassign_calls == []


@pytest.mark.asyncio
async def test_exhausted_budget_heals_dead_slot_without_charging_budget() -> None:
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    rotator = _HealingRotator()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 0},
        switch_backoff_range=(0.0, 0.0),
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    rotator.unavailable.add("dead")

    assert (
        await pipeline.handle_error(
            context,
            attempt,
            UsageLimitExceeded("429"),
            has_yielded_data=False,
        )
        == "retry_same"
    )
    with pytest.raises(SurfaceSwitchExhaustedError):
        await pipeline.handle_error(
            context,
            attempt,
            UsageLimitExceeded("429"),
            has_yielded_data=False,
        )

    assert manager.reassign_calls == [("inst-1", "healthy")]
    assert context.budget.switches == 0
    assert attempt.handle.released is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_204_without_retry_reports_plain_empty_response() -> None:
    pipeline = _pipeline(FakeManager([make_instance("inst-1", account_id="acc")]))
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    with pytest.raises(BrowserTransportError) as info:
        await pipeline.handle_error(
            context,
            attempt,
            RequestError(204, "no content"),
            has_yielded_data=True,
        )

    assert str(info.value) == "上游返回 204 空响应"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.asyncio
async def test_transient_request_error_never_switches_or_rebinds(status: int) -> None:
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [
            make_instance("inst-1", account_id="free-a", tier="Free", number=1),
            make_instance("inst-2", account_id="free-b", tier="Free", number=2),
        ]
    )
    manager.account_tiers["healthy"] = "Free"
    rotator = _HealingRotator()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    error = RequestError(status, "upstream hiccup")

    assert (
        await pipeline.handle_error(context, attempt, error, has_yielded_data=False)
        == "retry_same"
    )
    assert context.budget.switches == 0

    with pytest.raises(RequestError):
        await pipeline.handle_error(context, attempt, error, has_yielded_data=False)

    assert context.budget.switches == 0
    assert context.failed_accounts == set()
    assert manager.reassign_calls == []


@pytest.mark.asyncio
async def test_429_rebind_waits_randomized_switch_backoff(monkeypatch) -> None:
    from aistudio_api.application import routing_pipeline as routing_pipeline_module
    from tests.unit.routing_fakes import RebindFakeManager

    order: list[str] = []

    async def _fake_sleep(delay: float) -> None:
        assert delay == 2.0
        order.append("sleep")

    sleep_mock = AsyncMock(side_effect=_fake_sleep)
    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="free-a", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    original_reassign = manager.reassign

    async def _recording_reassign(instance_id: str, account_id: str):
        order.append("reassign")
        return await original_reassign(instance_id, account_id)

    monkeypatch.setattr(manager, "reassign", _recording_reassign)
    rotator = _HealingRotator()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 2},
        switch_backoff_range=(2.0, 2.0),
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    rotator.unavailable.add("free-a")
    error = UsageLimitExceeded("429")

    assert (
        await pipeline.handle_error(context, attempt, error, has_yielded_data=False)
        == "retry_same"
    )
    sleep_mock.assert_not_awaited()
    assert (
        await pipeline.handle_error(context, attempt, error, has_yielded_data=False)
        == "switch"
    )
    sleep_mock.assert_awaited_once_with(2.0)
    assert manager.reassign_calls == [("inst-1", "healthy")]
    # The cool-down must happen before the new account is activated.
    assert order == ["sleep", "reassign"]


@pytest.mark.asyncio
async def test_exhausted_budget_rebind_still_waits_switch_backoff(monkeypatch) -> None:
    from aistudio_api.application import routing_pipeline as routing_pipeline_module
    from tests.unit.routing_fakes import RebindFakeManager

    sleep_mock = AsyncMock()
    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    rotator = _HealingRotator()
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", rotator),
        routing_settings_provider=lambda: {"max_switches": 0},
        switch_backoff_range=(2.0, 2.0),
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None
    rotator.unavailable.add("dead")

    assert (
        await pipeline.handle_error(
            context,
            attempt,
            UsageLimitExceeded("429"),
            has_yielded_data=False,
        )
        == "retry_same"
    )
    with pytest.raises(SurfaceSwitchExhaustedError):
        await pipeline.handle_error(
            context,
            attempt,
            UsageLimitExceeded("429"),
            has_yielded_data=False,
        )

    # record_budget=False must still cool down before the rebind.
    sleep_mock.assert_awaited_once_with(2.0)
    assert manager.reassign_calls == [("inst-1", "healthy")]
    assert context.budget.switches == 0


@pytest.mark.asyncio
async def test_try_rebind_selects_target_before_releasing_handle() -> None:
    """Q-10: pick the target first, then release the handle for the rebind."""
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    manager.account_tiers["healthy"] = "Free"
    released_at_selection: list[bool] = []

    class _RecordingRotator(_HealingRotator):
        def select_rebind_account(self, **kwargs):
            handle = manager.last_handles.get("inst-1")
            released_at_selection.append(bool(handle and handle.released))
            return super().select_rebind_account(**kwargs)

    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", _RecordingRotator()),
        routing_settings_provider=lambda: {"max_switches": 2},
        switch_backoff_range=(0.0, 0.0),
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    rebound = await pipeline._try_rebind(  # type: ignore[attr-defined]
        context, attempt, "429", reuse=False
    )

    assert rebound is True
    assert released_at_selection == [False]
    assert attempt.handle.released is True  # type: ignore[attr-defined]
    assert manager.reassign_calls == [("inst-1", "healthy")]


@pytest.mark.asyncio
async def test_try_rebind_keeps_handle_when_no_target_account() -> None:
    """Q-10: no eligible target means no handle release / idle window."""
    from tests.unit.routing_fakes import RebindFakeManager

    manager = RebindFakeManager(
        [make_instance("inst-1", account_id="dead", tier="Free", number=1)]
    )
    pipeline = RoutingPipeline(
        instance_manager=manager,
        rotator=cast("Any", _HealingRotator(target=None)),
        routing_settings_provider=lambda: {"max_switches": 2},
    )
    context = await pipeline.begin(model_path="gemini-3.7-flash")
    attempt = await pipeline.acquire(context)
    assert attempt is not None

    rebound = await pipeline._try_rebind(  # type: ignore[attr-defined]
        context, attempt, "429", reuse=False
    )

    assert rebound is False
    assert attempt.handle.released is False  # type: ignore[attr-defined]
    assert manager.reassign_calls == []


def test_default_switch_backoff_reads_zero_env(monkeypatch) -> None:
    """Q-7: pytest disables the backoff through the env var, not PYTEST detection."""
    monkeypatch.setenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", "0")
    pipeline = _pipeline(FakeManager([]))
    assert pipeline._switch_backoff_range == (0.0, 0.0)


def test_default_switch_backoff_uses_production_range_without_env(monkeypatch) -> None:
    monkeypatch.delenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", raising=False)
    pipeline = _pipeline(FakeManager([]))
    assert pipeline._switch_backoff_range == (3.0, 5.0)


def test_backoff_range_from_seconds_applies_quarter_jitter() -> None:
    from aistudio_api.application.routing_pipeline import _backoff_range_from_seconds

    assert _backoff_range_from_seconds(0.0) == (0.0, 0.0)
    assert _backoff_range_from_seconds(-2.0) == (0.0, 0.0)
    assert _backoff_range_from_seconds(4.0) == (3.0, 5.0)
    assert _backoff_range_from_seconds(2.0) == (1.5, 2.5)


def test_explicit_routing_settings_switch_backoff_beats_env(monkeypatch) -> None:
    """An injected provider key must win over the shared pytest env default."""
    monkeypatch.setenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", "0")
    pipeline = RoutingPipeline(
        instance_manager=FakeManager([]),
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "switch_backoff_sec": 4.0,
        },
    )
    assert pipeline._resolve_switch_backoff_range() == (3.0, 5.0)


def test_switch_backoff_reads_live_config_when_env_missing(monkeypatch) -> None:
    from aistudio_api.infrastructure.gateway import model_defaults

    monkeypatch.delenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", raising=False)
    monkeypatch.setattr(
        model_defaults,
        "get_configured_routing_settings",
        lambda *args, **kwargs: {"max_switches": 2, "switch_backoff_sec": 2.0},
    )
    pipeline = RoutingPipeline(instance_manager=FakeManager([]))

    assert pipeline._resolve_switch_backoff_range() == (1.5, 2.5)


def test_switch_backoff_provider_without_key_reads_live_config(monkeypatch) -> None:
    """P2-2: a provider that omits ``switch_backoff_sec`` must not skip live config."""
    from aistudio_api.infrastructure.gateway import model_defaults

    monkeypatch.delenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", raising=False)
    monkeypatch.setattr(
        model_defaults,
        "get_configured_routing_settings",
        lambda *args, **kwargs: {"max_switches": 2, "switch_backoff_sec": 2.0},
    )
    pipeline = RoutingPipeline(
        instance_manager=FakeManager([]),
        routing_settings_provider=lambda: {"max_switches": 2},
    )

    assert pipeline._resolve_switch_backoff_range() == (1.5, 2.5)


def test_switch_backoff_range_falls_back_to_default(monkeypatch) -> None:
    from aistudio_api.infrastructure.gateway import model_defaults

    monkeypatch.delenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", raising=False)
    monkeypatch.setattr(
        model_defaults,
        "get_configured_routing_settings",
        lambda *args, **kwargs: {"max_switches": 2},
    )
    pipeline = RoutingPipeline(instance_manager=FakeManager([]))
    assert pipeline._resolve_switch_backoff_range() == (3.0, 5.0)


@pytest.mark.asyncio
async def test_switch_backoff_sleep_uses_resolved_range(monkeypatch) -> None:
    from aistudio_api.application import routing_pipeline as routing_pipeline_module

    sleep_mock = AsyncMock()
    captured: list[tuple[float, float]] = []

    def _fake_uniform(low: float, high: float) -> float:
        captured.append((low, high))
        return 4.0

    monkeypatch.setattr(routing_pipeline_module.asyncio, "sleep", sleep_mock)
    monkeypatch.setattr(routing_pipeline_module.random, "uniform", _fake_uniform)
    pipeline = RoutingPipeline(
        instance_manager=FakeManager([]),
        routing_settings_provider=lambda: {
            "max_switches": 2,
            "switch_backoff_sec": 4.0,
        },
    )

    await pipeline._switch_backoff(instance_id="inst-1", reason="429")

    assert captured == [(3.0, 5.0)]
    sleep_mock.assert_awaited_once_with(4.0)


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
