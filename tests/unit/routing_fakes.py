"""Small offline fakes for routing/API tests (no real browser or network)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from aistudio_api.application.routing_eligibility import ModelRoute
from aistudio_api.application.routing_pipeline import (
    RouteAttempt,
    RouteCandidate,
    RouteContext,
)
from aistudio_api.application.routing_safety import RoutingBudget
from aistudio_api.domain.instances import Instance, InstanceState
from aistudio_api.domain.model_access import (
    instance_lane_for_tier,
    normalize_model_key,
)


@dataclass
class FakeSlot:
    session: Any


@dataclass
class FakeHandle:
    slot: FakeSlot
    released: bool = False

    def release(self) -> None:
        self.released = True


@dataclass
class FakeSession:
    """Session payload consumed by the scripted ``InstanceRequestClient``."""

    account_id: str
    generate: Callable[..., Awaitable[Any]] | None = None
    stream: Callable[..., AsyncIterator[tuple[str, object | None]]] | None = None
    refresh_ok: bool = True
    refresh_calls: int = 0

    async def refresh_auth_state(self) -> bool:
        self.refresh_calls += 1
        return self.refresh_ok


@dataclass
class TrackingHandle(FakeHandle):
    manager: RebindFakeManager | None = None
    instance_id: str = ""

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        if self.manager is not None:
            self.manager.active_requests = max(0, self.manager.active_requests - 1)
            if self.instance_id:
                self.manager.active_by_instance[self.instance_id] = max(
                    0, self.manager.active_by_instance.get(self.instance_id, 0) - 1
                )


def make_instance(
    instance_id: str,
    *,
    account_id: str = "acc-1",
    tier: str = "Free",
    state: InstanceState = InstanceState.ACTIVE,
    surface: str = "chat",
    number: int = 1,
    last_used: float = 0.0,
    in_flight: int = 0,
) -> Instance:
    return Instance(
        id=instance_id,
        number=number,
        account_id=account_id,
        state=state,
        surface=surface,
        min_tier="Free",
        in_flight=in_flight,
        latest=False,
        tier=tier,
        last_used=last_used,
    )


def make_route(
    model: str = "gemini-3.7-flash",
    *,
    surface: str = "chat",
    paid: bool = False,
    required_tiers: tuple[str, ...] = (),
    required_tier: str | None = None,
) -> ModelRoute:
    return ModelRoute(
        raw_model=model,
        model=model,
        surface=surface,
        paid=paid,
        required_tiers=required_tiers,
        required_tier=required_tier,
        tier=required_tier or ("Pro" if paid else "Free"),
    )


def make_context(
    instances: list[Instance] | None = None,
    *,
    model: str = "gemini-3.7-flash",
    surface: str = "chat",
    paid: bool = False,
    required_tiers: tuple[str, ...] = (),
    max_switches: int = 2,
    max_retries: int = 5,
) -> RouteContext:
    return RouteContext(
        route=make_route(
            model,
            surface=surface,
            paid=paid,
            required_tiers=required_tiers,
            required_tier=(required_tiers[0] if required_tiers else None),
        ),
        candidates=[RouteCandidate.from_instance(item) for item in instances or []],
        budget=RoutingBudget(max_switches=max_switches),
        max_retries=max_retries,
    )


class FakeManager:
    """In-memory stand-in for InstanceManager request-path methods."""

    def __init__(
        self,
        instances: list[Instance] | None = None,
        *,
        sessions: dict[str, Any] | None = None,
        current_instance_id: str | None = None,
    ) -> None:
        self.instances = list(instances or [])
        self.sessions = sessions or {}
        self._current = current_instance_id
        self.wake_calls: list[tuple[bool, str | None]] = []
        self.woken: list[str] = []
        self.begin_calls: list[str] = []

    @property
    def configured_surfaces(self) -> frozenset[str]:
        return frozenset(item.surface for item in self.instances) or frozenset({"chat"})

    @property
    def current_instance_id(self) -> str | None:
        return self._current

    def eligible_instances(
        self, *, paid: bool, surface: str | None = None
    ) -> tuple[Instance, ...]:
        lane = "paid" if paid else "free"
        return tuple(
            item
            for item in self.instances
            if instance_lane_for_tier(item.tier) == lane
            and (surface is None or item.surface == surface)
            and item.state
            in (
                InstanceState.UNSTARTED,
                InstanceState.SLEEPING,
                InstanceState.STARTING,
                InstanceState.ACTIVE,
            )
        )

    def lane_instances(self, *, paid: bool) -> tuple[Instance, ...]:
        lane = "paid" if paid else "free"
        return tuple(
            item
            for item in self.instances
            if instance_lane_for_tier(item.tier) == lane
        )

    async def wake_eligible(
        self, *, paid: bool | None = None, surface: str | None = None
    ) -> list[Instance]:
        self.wake_calls.append((bool(paid), surface))
        for index, item in enumerate(self.instances):
            if paid is not None and (instance_lane_for_tier(item.tier) == "paid") != paid:
                continue
            if surface is not None and item.surface != surface:
                continue
            if item.state in (InstanceState.UNSTARTED, InstanceState.SLEEPING):
                self.instances[index] = _with_state(item, InstanceState.ACTIVE)
        return list(self.instances)

    async def wake(self, instance_id: str) -> Instance:
        self.woken.append(instance_id)
        for index, item in enumerate(self.instances):
            if item.id == instance_id:
                self.instances[index] = _with_state(item, InstanceState.ACTIVE)
                return self.instances[index]
        raise RuntimeError(f"unknown instance {instance_id}")

    async def begin_request(self, instance_id: str) -> FakeHandle:
        self.begin_calls.append(instance_id)
        for item in self.instances:
            if item.id == instance_id:
                if item.state is not InstanceState.ACTIVE:
                    raise RuntimeError("not active")
                session = self.sessions.get(instance_id)
                if session is None:
                    session = FakeSession(account_id=item.account_id)
                    self.sessions[instance_id] = session
                return FakeHandle(slot=FakeSlot(session=session))
        raise RuntimeError(f"unknown instance {instance_id}")


class RebindFakeManager(FakeManager):
    """Fake manager that supports request-triggered in-place account rebinds."""

    def __init__(
        self,
        instances: list[Instance] | None = None,
        *,
        sessions: dict[str, Any] | None = None,
        account_sessions: dict[str, Any] | None = None,
        current_instance_id: str | None = None,
    ) -> None:
        super().__init__(
            instances, sessions=sessions, current_instance_id=current_instance_id
        )
        self.account_sessions = dict(account_sessions or {})
        self.account_tiers = {item.account_id: item.tier for item in self.instances}
        self.reassign_calls: list[tuple[str, str]] = []
        self.active_requests = 0
        self.active_by_instance: dict[str, int] = {}
        self.last_handles: dict[str, TrackingHandle] = {}

    async def begin_request(self, instance_id: str) -> TrackingHandle:
        self.begin_calls.append(instance_id)
        item = next(
            (candidate for candidate in self.instances if candidate.id == instance_id),
            None,
        )
        if item is None:
            raise RuntimeError(f"unknown instance {instance_id}")
        if item.state is not InstanceState.ACTIVE:
            raise RuntimeError("not active")
        session = self.sessions.get(instance_id)
        if session is None:
            session = self.account_sessions.get(item.account_id) or FakeSession(
                account_id=item.account_id
            )
            self.sessions[instance_id] = session
        self.active_requests += 1
        self.active_by_instance[instance_id] = (
            self.active_by_instance.get(instance_id, 0) + 1
        )
        handle = TrackingHandle(
            slot=FakeSlot(session=session),
            manager=self,
            instance_id=instance_id,
        )
        self.last_handles[instance_id] = handle
        return handle

    async def reassign(self, instance_id: str, account_id: str) -> Instance:
        if self.active_by_instance.get(instance_id, 0) > 0:
            raise RuntimeError("instance busy: release active request first")
        self.reassign_calls.append((instance_id, account_id))
        for index, item in enumerate(self.instances):
            if item.id != instance_id:
                continue
            updated = replace(
                item,
                account_id=account_id,
                tier=self.account_tiers.get(account_id, item.tier),
            )
            self.instances[index] = updated
            self.sessions[instance_id] = self.account_sessions.get(
                account_id
            ) or FakeSession(account_id=account_id)
            return updated
        raise RuntimeError(f"unknown instance {instance_id}")

    def instance_account_ids(self) -> set[str]:
        return {item.account_id for item in self.instances if item.account_id}

    def instance_active_requests(self, instance_id: str) -> int:
        return self.active_by_instance.get(instance_id, 0)


def _with_state(instance: Instance, state: InstanceState) -> Instance:
    return Instance(
        id=instance.id,
        number=instance.number,
        account_id=instance.account_id,
        state=state,
        surface=instance.surface,
        min_tier=instance.min_tier,
        only_tier=instance.only_tier,
        max_tier=instance.max_tier,
        in_flight=instance.in_flight,
        latest=instance.latest,
        tier=instance.tier,
        last_used=instance.last_used,
    )


@dataclass
class ScriptedPipeline:
    """API-service pipeline fake with scripted error actions."""

    attempts: list[RouteAttempt]
    actions: list[str] = field(default_factory=list)
    context: RouteContext = field(
        default_factory=lambda: make_context(
            [make_instance("inst-1", account_id="acc-1", tier="Pro")],
            model="gemini-3.7-flash",
            paid=True,
            required_tiers=("Pro",),
        )
    )
    successes: list[tuple[str, dict[str, object] | None]] = field(default_factory=list)
    client_terminals: list[tuple[str, str, bool]] = field(default_factory=list)
    count_terminals: list[tuple[str, str, int]] = field(default_factory=list)
    empty_retries: int = 0

    async def begin(
        self, *, model_path: str, requested_surface: str | None = None
    ) -> RouteContext:
        del requested_surface
        clean = normalize_model_key(model_path) or model_path
        self.context.route = replace(
            self.context.route, raw_model=model_path, model=clean
        )
        return self.context

    async def acquire(self, context: RouteContext) -> RouteAttempt | None:
        del context
        if self.attempts:
            return self.attempts.pop(0)
        return None

    async def release(self, attempt: RouteAttempt) -> None:
        handle = attempt.handle
        if isinstance(handle, FakeHandle):
            handle.release()

    async def handle_error(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        exc: Exception,
        *,
        has_yielded_data: bool = False,
    ) -> str:
        del context, attempt, has_yielded_data
        if self.actions:
            return self.actions.pop(0)
        raise exc

    def record_success(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        del context, finish_reason
        self.successes.append((attempt.instance_id, usage))

    def record_client(
        self,
        model: str,
        result: str,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
        rescued: bool = False,
    ) -> None:
        del usage, finish_reason
        self.client_terminals.append((model, result, rescued))

    def record_count(
        self, model: str, result: str, *, input_tokens: int = 0
    ) -> None:
        self.count_terminals.append((model, result, input_tokens))

    def record_empty_retry(self, context: RouteContext, attempt: RouteAttempt) -> None:
        del context, attempt
        self.empty_retries += 1


def make_attempt(
    client: Any,
    *,
    instance_id: str = "inst-1",
    account_id: str = "acc-1",
    tier: str = "Pro",
    surface: str = "chat",
    same_retries: int = 0,
) -> RouteAttempt:
    candidate = RouteCandidate(
        instance_id=instance_id,
        account_id=account_id,
        tier=tier,
        surface=surface,
        state=InstanceState.ACTIVE,
        in_flight=0,
        last_used=0.0,
        number=1,
    )
    return RouteAttempt(
        candidate=candidate,
        handle=FakeHandle(slot=FakeSlot(session=object())),
        client=client,
        same_retries=same_retries,
    )


__all__ = [
    "FakeHandle",
    "FakeManager",
    "FakeSession",
    "FakeSlot",
    "RebindFakeManager",
    "ScriptedPipeline",
    "TrackingHandle",
    "make_attempt",
    "make_context",
    "make_instance",
    "make_route",
]
