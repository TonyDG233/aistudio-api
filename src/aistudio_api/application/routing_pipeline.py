"""P2 request routing over the instance pool.

Flow: model eligibility (surface/lane/tier) -> wake eligible sleeping/unstarted
instances -> ordered candidates (sticky + least-used/LRU/FIFO, warm as a tie
break) -> ``active_requests`` handle.  There is no lease, reservation, defer,
pinned slot, circuit breaker or background rebalance.

Error policy:
* tier/403 admission rejects locally and never switches;
* 429 retries the same instance first, then switches inside the existing pool,
  and finally returns a 503 (never 429);
* 204 / transient RPC 404 retry the same instance without switching;
* once streaming body data was yielded, no switch happens.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, cast

from aistudio_api.application.account_orchestrator import record_rotator_event
from aistudio_api.application.model_access import (
    best_tier,
    tier_satisfies_required,
)
from aistudio_api.application.routing_eligibility import (
    ModelRoute,
    resolve_model_route,
    tier_allows,
)
from aistudio_api.application.routing_safety import RoutingBudget
from aistudio_api.domain.errors import (
    AistudioError,
    AuthError,
    BrowserTransportError,
    RequestError,
    RoutingError,
    SessionExpiredError,
    SurfaceCapacityError,
    SurfaceSwitchExhaustedError,
    TierRequiredError,
    UsageLimitExceeded,
    is_browser_session_hang_error,
    is_transient_rpc_error,
)
from aistudio_api.domain.instances import Instance, InstanceState
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.instance_client import InstanceRequestClient
from aistudio_api.infrastructure.gateway.session_protocol import RequestSession
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("routing")

if TYPE_CHECKING:
    from aistudio_api.application.account_rotator import AccountRotator


class RequestSlot(Protocol):
    @property
    def session(self) -> RequestSession | None: ...


class RequestHandle(Protocol):
    @property
    def slot(self) -> RequestSlot: ...

    def release(self) -> None: ...


class InstanceManagerProtocol(Protocol):
    @property
    def configured_surfaces(self) -> frozenset[str]: ...

    @property
    def current_instance_id(self) -> str | None: ...

    def eligible_instances(
        self, *, paid: bool, surface: str | None = None
    ) -> tuple[Instance, ...]: ...

    def lane_instances(self, *, paid: bool) -> tuple[Instance, ...]: ...

    async def wake_eligible(
        self, *, paid: bool, surface: str | None = None
    ) -> object: ...

    async def wake(self, instance_id: str) -> object: ...

    async def begin_request(self, instance_id: str) -> RequestHandle: ...



class GenerationClient(Protocol):
    """Structural client consumed by the Gemini application service."""

    account_id: str
    tier: str
    surface: str

    async def generate_content(
        self,
        *,
        model: str = ...,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh_capture: bool = False,
    ) -> ModelOutput: ...

    def stream_generate_content(
        self,
        *,
        model: str = ...,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh_capture: bool = False,
    ) -> AsyncGenerator[tuple[str, object | None], None]: ...

    async def count_tokens(self, *, packed_body: list[object]) -> int: ...

    def clear_templates(self) -> None: ...


@dataclass(frozen=True)
class RouteCandidate:
    instance_id: str
    account_id: str
    tier: str
    surface: str
    state: InstanceState
    in_flight: int
    last_used: float
    number: int

    @classmethod
    def from_instance(cls, instance: Instance) -> RouteCandidate:
        return cls(
            instance_id=instance.id,
            account_id=instance.account_id,
            tier=instance.tier,
            surface=instance.surface,
            state=instance.state,
            in_flight=instance.in_flight,
            last_used=instance.last_used,
            number=instance.number,
        )


@dataclass
class RouteContext:
    route: ModelRoute
    candidates: list[RouteCandidate]
    budget: RoutingBudget
    max_retries: int
    attempted_instances: set[str] = field(default_factory=set)
    failed_accounts: set[str] = field(default_factory=set)
    total_attempts: int = 0
    retry_same: bool = False
    last_reason: str = "initial"

    def next_candidate(self) -> RouteCandidate | None:
        for candidate in self.candidates:
            if candidate.instance_id in self.attempted_instances:
                continue
            if candidate.account_id in self.failed_accounts:
                continue
            return candidate
        return None

    def register_attempt(self) -> None:
        self.total_attempts += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "model": self.route.model,
            "surface": self.route.surface,
            "lane": self.route.lane,
            "candidates": [candidate.instance_id for candidate in self.candidates],
            "attempted": sorted(self.attempted_instances),
            "failed_accounts": sorted(self.failed_accounts),
            "budget": self.budget.snapshot(),
            "retry_same": self.retry_same,
            "last_reason": self.last_reason,
        }


@dataclass
class RouteAttempt:
    candidate: RouteCandidate
    handle: RequestHandle
    client: GenerationClient
    same_retries: int = 0

    @property
    def instance_id(self) -> str:
        return self.candidate.instance_id

    @property
    def account_id(self) -> str:
        return self.candidate.account_id

    @property
    def surface(self) -> str:
        return self.candidate.surface


RoutingSettingsProvider = Callable[[], dict[str, object]]


class RoutingPipelineProtocol(Protocol):
    async def begin(
        self, *, model_path: str, requested_surface: str | None = None
    ) -> RouteContext: ...

    async def acquire(self, context: RouteContext) -> RouteAttempt | None: ...

    async def release(self, attempt: RouteAttempt) -> None: ...

    async def handle_error(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        exc: Exception,
        *,
        has_yielded_data: bool = False,
    ) -> str: ...

    def record_success(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None: ...


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


class RoutingPipeline:
    """Plan and execute one request's route over existing instances."""

    def __init__(
        self,
        *,
        instance_manager: InstanceManagerProtocol,
        account_store: object | None = None,
        rotator: AccountRotator | None = None,
        routing_settings_provider: RoutingSettingsProvider | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._manager = instance_manager
        self._accounts = account_store
        self._rotator = rotator
        self._routing_settings_provider = routing_settings_provider
        self._clock = clock

    def _rotator_or_none(self) -> AccountRotator | None:
        return cast("AccountRotator | None", self._rotator)

    # --------------------------------------------------------------- planning

    async def begin(
        self, *, model_path: str, requested_surface: str | None = None
    ) -> RouteContext:
        manager = self._manager
        await self._ensure_started()
        surfaces = getattr(manager, "configured_surfaces", frozenset()) or frozenset(
            {"chat"}
        )
        route = resolve_model_route(
            model_path,
            requested_surface=requested_surface,
            enabled_surfaces=tuple(sorted(surfaces)),
        )
        if route.surface == "build":
            raise SurfaceCapacityError(
                surface="build",
                message="build surface 尚未实现（P4 接入前显式拒绝），不会静默换面。",
            )

        await self._wake(route)
        lane_candidates = list(
            manager.eligible_instances(paid=route.paid, surface=route.surface)
        )
        if not lane_candidates:
            if route.paid:
                lane_all = list(manager.lane_instances(paid=True))
                all_candidates = list(
                    manager.eligible_instances(paid=True)
                ) + list(manager.eligible_instances(paid=False))
                current = self._current_tier(lane_all + all_candidates)
                if not tier_satisfies_required(
                    current, route.required_tiers
                ):
                    raise self._tier_error(route, current)
            raise SurfaceCapacityError(surface=route.surface)

        eligible = [
            instance
            for instance in lane_candidates
            if tier_allows(instance.tier, route)
        ]
        if not eligible:
            current = self._current_tier(lane_candidates)
            if route.paid and not tier_satisfies_required(
                current, route.required_tiers
            ):
                raise self._tier_error(route, current)
            raise SurfaceCapacityError(surface=route.surface)

        available = [
            instance
            for instance in eligible
            if self._account_available(instance.account_id, route)
        ]
        if not available:
            raise SurfaceCapacityError(
                surface=route.surface,
                message=(
                    f"渠道 '{route.surface}' 的合格账号均处于 429/403 冷却或"
                    "登录态失效，请等待额度刷新或前往管理页检查。"
                ),
            )

        ordered = self._order(route, available)
        settings = self._routing_settings()
        max_switches = max(0, _as_int(settings.get("max_switches", 2), 2))
        context = RouteContext(
            route=route,
            candidates=[RouteCandidate.from_instance(item) for item in ordered],
            budget=RoutingBudget(max_switches=max_switches),
            max_retries=self._max_retries(),
        )
        logger.info(
            "[routing] model=%s surface=%s lane=%s candidates=%s max_switches=%d",
            route.model,
            route.surface,
            route.lane,
            ",".join(candidate.instance_id for candidate in context.candidates),
            max_switches,
        )
        return context

    def _order(
        self, route: ModelRoute, instances: Iterable[Instance]
    ) -> list[Instance]:
        sticky_id = getattr(self._manager, "current_instance_id", None)
        rotator = self._rotator
        rows: list[tuple[tuple[object, ...], Instance]] = []
        for instance in instances:
            stats = None
            if rotator is not None:
                getter = getattr(rotator, "get_account_stats", None)
                if callable(getter):
                    stats = getter(instance.account_id)
            total_requests = _as_int(getattr(stats, "total_requests", 0), 0)
            model_requests = 0
            if stats is not None:
                counter = getattr(stats, "model_requests_total", None)
                if callable(counter):
                    model_requests = _as_int(counter(route.model), 0)
            warm = 0 if instance.state is InstanceState.ACTIVE else 1
            key = (
                0 if instance.id == sticky_id else 1,
                total_requests,
                model_requests,
                instance.in_flight,
                instance.last_used,
                warm,
                instance.number,
            )
            rows.append((key, instance))
        rows.sort(key=lambda item: item[0])
        return [instance for _key, instance in rows]

    def _tier_error(self, route: ModelRoute, current_tier: str) -> TierRequiredError:
        required = route.required_tier or "Pro"
        return TierRequiredError(
            model=route.model,
            tier=route.tier or required,
            required_tier=required,
            current_tier=current_tier or "Free",
        )

    def _current_tier(self, instances: Iterable[Instance]) -> str:
        """Best usable tier across instance slots and configured accounts."""
        tiers = [instance.tier for instance in instances]
        store = self._accounts
        if store is not None:
            lister = getattr(store, "list_accounts", None)
            if callable(lister):
                try:
                    accounts = lister()
                    if not isinstance(accounts, (list, tuple)):
                        accounts = []
                    for account in accounts:
                        if getattr(account, "enabled", True) and not getattr(
                            account, "expired", False
                        ):
                            tiers.append(getattr(account, "tier", ""))
                except Exception as exc:
                    logger.debug("[routing] 读取账号层级失败: %s", exc)
        return best_tier(tiers)

    def _account_available(self, account_id: str, route: ModelRoute) -> bool:
        rotator = self._rotator_or_none()
        if rotator is None:
            return True
        checker = getattr(rotator, "is_account_available", None)
        if not callable(checker):
            return True
        try:
            return bool(
                checker(account_id, model=route.model, surface=route.surface)
            )
        except TypeError:
            return bool(checker(account_id, model=route.model))

    def _routing_settings(self) -> dict[str, object]:
        if self._routing_settings_provider is not None:
            try:
                return dict(self._routing_settings_provider())
            except Exception as exc:
                logger.warning("[routing] 路由配置读取失败，回退 max_switches=2: %s", exc)
                return {"max_switches": 2}
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_routing_settings,
        )

        return get_configured_routing_settings()

    def _max_retries(self) -> int:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_rotator_settings,
        )

        raw = get_configured_rotator_settings().get("max_retries", 5)
        return max(1, _as_int(raw, 5))

    async def _ensure_started(self) -> None:
        """Create/await instance records before the first request hits the pool."""
        if getattr(self._manager, "configured_surfaces", frozenset()):
            return
        startup = getattr(self._manager, "startup", None)
        if not callable(startup):
            return
        result = startup()
        if inspect.isawaitable(result):
            await result

    async def _wake(self, route: ModelRoute) -> None:
        wake = getattr(self._manager, "wake_eligible", None)
        if not callable(wake):
            return
        try:
            result = wake(paid=route.paid, surface=route.surface)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning("[routing] 唤醒休眠实例失败: %s", exc)

    # -------------------------------------------------------------- execution

    async def acquire(self, context: RouteContext) -> RouteAttempt | None:
        manager = self._manager
        last_error: Exception | None = None
        while True:
            candidate = context.next_candidate()
            if candidate is None:
                return None
            context.attempted_instances.add(candidate.instance_id)
            try:
                if candidate.state in (
                    InstanceState.UNSTARTED,
                    InstanceState.SLEEPING,
                ):
                    wake_one = getattr(manager, "wake", None)
                    if callable(wake_one):
                        wake_result = wake_one(candidate.instance_id)
                        if inspect.isawaitable(wake_result):
                            await wake_result
                handle: RequestHandle = await manager.begin_request(
                    candidate.instance_id
                )
                session = handle.slot.session
                if session is None:
                    handle.release()
                    raise SurfaceCapacityError(surface=context.route.surface)
                client = InstanceRequestClient(
                    session,
                    account_id=candidate.account_id,
                    tier=candidate.tier,
                    surface=candidate.surface,
                )
                context.register_attempt()
                logger.info(
                    "[routing] acquire instance=%s account=%s attempt=%d",
                    candidate.instance_id,
                    candidate.account_id,
                    context.total_attempts,
                )
                return RouteAttempt(
                    candidate=candidate, handle=handle, client=client
                )
            except (RoutingError, TierRequiredError):
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[routing] 实例 %s 不可用，尝试下一个候选: %s",
                    candidate.instance_id,
                    exc,
                )
                continue

        raise SurfaceCapacityError(
            surface=context.route.surface,
            message=f"没有可用实例: {last_error}" if last_error else None,
        )

    async def release(self, attempt: RouteAttempt) -> None:
        attempt.handle.release()

    def record_success(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        context.retry_same = False
        context.last_reason = "success"
        record_rotator_event(
            "success",
            account_id=attempt.account_id,
            model=context.route.model,
            surface=context.route.surface,
            usage=usage,
            rotator=self._rotator_or_none(),
        )
        try:
            from aistudio_api.api.state import runtime_state

            runtime_state.record(
                context.route.model,
                "success",
                usage=usage,
                finish_reason=finish_reason,
            )
        except Exception as exc:
            logger.debug("[routing] 统计记录失败: %s", exc)

    async def handle_error(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        exc: Exception,
        *,
        has_yielded_data: bool = False,
    ) -> Literal["retry_same", "switch", "raise"]:
        context.last_reason = type(exc).__name__
        if isinstance(exc, TierRequiredError):
            raise exc
        if isinstance(exc, ValueError):
            raise RequestError(400, str(exc)) from exc

        if isinstance(exc, SessionExpiredError):
            self._record_error("session_expired", context, attempt)
            attempt.client.clear_templates()
            if has_yielded_data:
                raise exc
            return await self._switch_or_raise(
                context, attempt, exc, reason="session_expired"
            )

        if isinstance(exc, AuthError):
            if not tier_allows(attempt.candidate.tier, context.route):
                raise self._tier_error(
                    context.route, attempt.candidate.tier
                ) from exc
            self._record_error("auth_error", context, attempt)
            attempt.client.clear_templates()
            if has_yielded_data:
                raise exc
            return await self._switch_or_raise(
                context, attempt, exc, reason="auth_error"
            )

        if isinstance(exc, UsageLimitExceeded):
            self._record_error("rate_limited", context, attempt)
            if has_yielded_data:
                raise exc
            if attempt.same_retries == 0 and context.total_attempts < context.max_retries:
                attempt.same_retries += 1
                context.retry_same = True
                context.budget.record_retry("429")
                logger.warning(
                    "[routing] 429 同账号重试 instance=%s account=%s",
                    attempt.instance_id,
                    attempt.account_id,
                )
                return "retry_same"
            return await self._switch_or_raise(
                context,
                attempt,
                exc,
                reason="429",
                quota_exhausted=True,
            )

        if isinstance(exc, RequestError) and exc.status in (204, 404):
            if (
                exc.status == 404
                and not is_transient_rpc_error(exc)
            ):
                self._record_error("error", context, attempt)
                raise exc
            if (
                not has_yielded_data
                and attempt.same_retries == 0
                and context.total_attempts < context.max_retries
            ):
                attempt.same_retries += 1
                context.retry_same = True
                context.budget.record_retry(f"retry_{exc.status}")
                attempt.client.clear_templates()
                logger.warning(
                    "[routing] %s 同账号重试 instance=%s account=%s",
                    exc.status,
                    attempt.instance_id,
                    attempt.account_id,
                )
                return "retry_same"
            self._record_error("error", context, attempt)
            if exc.status == 204:
                raise BrowserTransportError(
                    "上游返回 204 空响应，同账号重试后仍为空"
                ) from exc
            raise exc

        if is_browser_session_hang_error(exc):
            self._record_error("error", context, attempt)
            attempt.client.clear_templates()
            if has_yielded_data:
                raise exc
            return await self._switch_or_raise(
                context, attempt, exc, reason="session_hang"
            )

        if isinstance(exc, RequestError):
            self._record_error("error", context, attempt)
            raise exc

        if isinstance(exc, (AistudioError, BrowserTransportError)):
            self._record_error("error", context, attempt)
            raise exc

        self._record_error("error", context, attempt)
        raise exc

    async def _switch_or_raise(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        exc: Exception,
        *,
        reason: str,
        quota_exhausted: bool = False,
    ) -> Literal["switch"]:
        if not attempt.account_id:
            raise exc
        context.failed_accounts.add(attempt.account_id)
        exhausted = (
            context.total_attempts >= context.max_retries
            or not context.budget.can_switch()
            or context.next_candidate() is None
        )
        if exhausted:
            if quota_exhausted:
                raise SurfaceSwitchExhaustedError(
                    surface=context.route.surface
                ) from exc
            raise exc
        context.budget.record_switch(reason)
        context.retry_same = False
        await self._wake(context.route)
        logger.warning(
            "[routing] 切换实例 reason=%s from=%s budget=%s",
            reason,
            attempt.instance_id,
            context.budget.snapshot(),
        )
        return "switch"

    def _record_error(
        self, event: str, context: RouteContext, attempt: RouteAttempt
    ) -> None:
        record_rotator_event(
            event,
            account_id=attempt.account_id,
            model=context.route.model,
            surface=context.route.surface,
            rotator=self._rotator_or_none(),
        )
        try:
            from aistudio_api.api.state import runtime_state

            runtime_state.record(
                context.route.model,
                "rate_limited" if event == "rate_limited" else "errors",
            )
        except Exception as exc:
            logger.debug("[routing] 错误统计记录失败: %s", exc)


__all__ = [
    "GenerationClient",
    "InstanceManagerProtocol",
    "RequestHandle",
    "RequestSlot",
    "RouteAttempt",
    "RouteCandidate",
    "RouteContext",
    "RoutingPipeline",
    "RoutingPipelineProtocol",
]
