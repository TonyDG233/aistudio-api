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

import asyncio
import inspect
import os
import random
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol, cast

from aistudio_api.application.account_orchestrator import record_rotator_event
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
    EngineSwitchingError,
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
from aistudio_api.domain.model_access import (
    best_tier,
    tier_rank,
    tier_satisfies_required,
)
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.instance_client import InstanceRequestClient
from aistudio_api.infrastructure.gateway.session_protocol import RequestSession
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.log_broadcast import sanitize_log_message
from aistudio_api.infrastructure.utils.logger import get_logger

DEFAULT_RETRY_SAME_DELAY_SEC = 2.0
DEFAULT_SWITCH_BACKOFF_RANGE = (3.0, 5.0)
_SWITCH_BACKOFF_ENV = "AISTUDIO_SWITCH_BACKOFF_SECONDS"

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

    def clear_templates(self) -> bool: ...

    async def refresh_auth_state(self) -> bool: ...


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
    min_tier: str = "Free"
    only_tier: str = ""
    max_tier: str = ""

    @property
    def account_tier_rank(self) -> int:
        """Rank of the account tier; lower is the smallest sufficient tier."""
        return tier_rank(self.tier)

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
            min_tier=instance.min_tier,
            only_tier=instance.only_tier,
            max_tier=instance.max_tier,
        )


@dataclass
class RouteContext:
    route: ModelRoute
    candidates: list[RouteCandidate]
    budget: RoutingBudget
    max_retries: int
    attempted_instances: set[str] = field(default_factory=set)
    attempted_accounts: set[str] = field(default_factory=set)
    failed_accounts: set[str] = field(default_factory=set)
    total_attempts: int = 0
    retry_same: bool = False
    retry_same_delay_sec: float = 0.0
    last_reason: str = "initial"
    # countTokens reuses the route but must never touch the generation attempt
    # ledger or consume account generation quota.
    record_attempts: bool = True

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
            "attempted_accounts": sorted(self.attempted_accounts),
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

    def record_client(
        self,
        model: str,
        result: str,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
        rescued: bool = False,
    ) -> None: ...

    def record_empty_retry(
        self, context: RouteContext, attempt: RouteAttempt
    ) -> None: ...

    def record_count(
        self, model: str, result: str, *, input_tokens: int = 0
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


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _normalize_backoff_range(raw: object) -> tuple[float, float]:
    """Coerce one explicit backoff override into a non-negative interval."""
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        low = max(0.0, _as_float(raw[0], 0.0))
        high = max(low, _as_float(raw[1], low))
        return (low, high)
    value = max(0.0, _as_float(raw, 0.0))
    return (value, value)


def _default_switch_backoff_range() -> tuple[float, float]:
    """Resolve the 429/verification switch backoff without touching app wiring.

    Production defaults to a 3-5s random jitter so two slots never fire the
    same-IP account switch at exactly the same instant.  Tests disable it by
    setting ``AISTUDIO_SWITCH_BACKOFF_SECONDS`` through the shared autouse
    fixture instead of leaking pytest detection into production code; the env
    var accepts either a fixed value (``4``) or an explicit range (``3,5``).
    """
    raw = (os.getenv(_SWITCH_BACKOFF_ENV) or "").strip()
    if raw:
        if "," in raw:
            low_raw, _, high_raw = raw.partition(",")
            try:
                low = max(0.0, float(low_raw))
                high = max(low, float(high_raw))
                return (low, high)
            except ValueError:
                logger.warning(
                    "[routing] 忽略非法 %s=%r，回退默认切号退避",
                    _SWITCH_BACKOFF_ENV,
                    raw,
                )
        else:
            try:
                value = max(0.0, float(raw))
                return (value, value)
            except ValueError:
                logger.warning(
                    "[routing] 忽略非法 %s=%r，回退默认切号退避",
                    _SWITCH_BACKOFF_ENV,
                    raw,
                )
    return DEFAULT_SWITCH_BACKOFF_RANGE


def _summarize_error(exc: Exception | None, summary: str | None = None) -> str:
    """Compact, sanitized upstream reason used in routing/rotator warnings.

    Contains only the exception type, its ``status`` (when present) and the
    first 160 characters of the collapsed message; cookie/Bearer/API-key
    material is masked by :func:`sanitize_log_message` before truncation.
    """
    text = summary or ""
    if not text and exc is not None:
        header = type(exc).__name__
        status = getattr(exc, "status", None)
        if status is not None:
            header = f"{header} status={status}"
        message = " ".join(str(exc).split())
        text = f"{header}: {message}" if message else header
    collapsed = " ".join(str(text).split())
    return sanitize_log_message(collapsed)[:160]


class RoutingPipeline:
    """Plan and execute one request's route over existing instances."""

    def __init__(
        self,
        *,
        instance_manager: InstanceManagerProtocol,
        account_store: object | None = None,
        rotator: AccountRotator | None = None,
        routing_settings_provider: RoutingSettingsProvider | None = None,
        stats_recorder: Callable[..., None] | None = None,
        client_recorder: Callable[..., None] | None = None,
        count_token_recorder: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        switch_backoff_range: tuple[float, float] | None = None,
    ) -> None:
        self._manager = instance_manager
        self._accounts = account_store
        self._rotator = rotator
        self._routing_settings_provider = routing_settings_provider
        self._stats_recorder = stats_recorder
        self._client_recorder = client_recorder
        self._count_token_recorder = count_token_recorder
        self._clock = clock
        self._switch_backoff_range = (
            _normalize_backoff_range(switch_backoff_range)
            if switch_backoff_range is not None
            else _default_switch_backoff_range()
        )
        # Serializes dead-slot healing so two concurrent begin() calls cannot
        # rebind the same slot twice (or race the idle-check with reassign).
        self._heal_lock = asyncio.Lock()

    def _rotator_or_none(self) -> AccountRotator | None:
        return cast("AccountRotator | None", self._rotator)

    # --------------------------------------------------------------- planning

    async def begin(
        self, *, model_path: str, requested_surface: str | None = None
    ) -> RouteContext:
        manager = self._manager
        if not bool(getattr(manager, "requests_open", True)):
            raise EngineSwitchingError(
                "engine switch in progress; request rejected with 503"
            )
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
                all_candidates = list(manager.eligible_instances(paid=True)) + list(
                    manager.eligible_instances(paid=False)
                )
                current = self._current_tier(lane_all + all_candidates)
                if not tier_satisfies_required(current, route.required_tiers):
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
        # A previous request may have exhausted max_switches and left a
        # dead/429 account bound to an idle slot.  Heal those slots before
        # admitting the request: a permanently stuck slot must never turn the
        # whole pool into a 503 while dozens of healthy accounts wait.
        if len(available) < len(eligible) and await self._heal_unavailable_instances(
            route, eligible
        ):
            refreshed = {
                instance.id: instance
                for instance in manager.eligible_instances(
                    paid=route.paid, surface=route.surface
                )
            }
            eligible = [refreshed.get(instance.id, instance) for instance in eligible]
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
        retry_same_delay_sec = min(
            30.0,
            max(0.0, _as_float(settings.get("retry_same_delay_sec", 0.0), 0.0)),
        )
        context = RouteContext(
            route=route,
            candidates=[RouteCandidate.from_instance(item) for item in ordered],
            budget=RoutingBudget(max_switches=max_switches),
            max_retries=self._max_retries(),
            retry_same_delay_sec=retry_same_delay_sec,
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
        """Sort same-lane candidates for balanced dispatch.

        Ordering is load first (idle before busy), then the smallest sufficient
        account tier (Pro before Ultra when Pro satisfies the route), then
        least-used/model-used/LRU, and only then warm/sticky/number.  Sticky is
        therefore a final tie-breaker: it keeps a healthy slot logged in, it
        never gives one slot a monopoly on traffic.
        """
        sticky_id = getattr(self._manager, "current_instance_id", None)
        rotator = self._rotator
        rows: list[tuple[tuple[object, ...], Instance]] = []
        for instance in instances:
            candidate = RouteCandidate.from_instance(instance)
            if not tier_allows(candidate.tier, route):
                continue
            stats = None
            if rotator is not None:
                getter = getattr(rotator, "get_account_stats", None)
                if callable(getter):
                    stats = getter(candidate.account_id)
            total_requests = _as_int(getattr(stats, "total_requests", 0), 0)
            model_requests = 0
            if stats is not None:
                counter = getattr(stats, "model_requests_total", None)
                if callable(counter):
                    model_requests = _as_int(counter(route.model), 0)
            warm = 0 if instance.state is InstanceState.ACTIVE else 1
            sticky = 0 if instance.id == sticky_id else 1
            key = (
                candidate.in_flight,
                candidate.account_tier_rank,
                total_requests,
                model_requests,
                candidate.last_used,
                warm,
                sticky,
                candidate.number,
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
            return bool(checker(account_id, model=route.model, surface=route.surface))
        except TypeError:
            return bool(checker(account_id, model=route.model))

    def _routing_settings(self) -> dict[str, object]:
        if self._routing_settings_provider is not None:
            try:
                return dict(self._routing_settings_provider())
            except Exception as exc:
                logger.warning(
                    "[routing] 路由配置读取失败，回退 max_switches=2、"
                    "retry_same_delay_sec=%.1f: %s",
                    DEFAULT_RETRY_SAME_DELAY_SEC,
                    exc,
                )
                return {
                    "max_switches": 2,
                    "retry_same_delay_sec": DEFAULT_RETRY_SAME_DELAY_SEC,
                }
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
                context.attempted_accounts.add(candidate.account_id)
                logger.info(
                    "[routing] acquire instance=%s account=%s attempt=%d",
                    candidate.instance_id,
                    candidate.account_id,
                    context.total_attempts,
                )
                return RouteAttempt(candidate=candidate, handle=handle, client=client)
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
        if not context.record_attempts:
            return
        record_rotator_event(
            "success",
            account_id=attempt.account_id,
            model=context.route.model,
            surface=context.route.surface,
            usage=usage,
            rotator=self._rotator_or_none(),
        )
        if self._stats_recorder is not None:
            try:
                self._stats_recorder(
                    context.route.model,
                    "success",
                    usage=usage,
                    finish_reason=finish_reason,
                )
            except Exception as exc:
                logger.debug("[routing] 统计记录失败: %s", exc)

    def record_empty_retry(self, context: RouteContext, attempt: RouteAttempt) -> None:
        """Record one independent attempt for an empty-response retry."""
        if not context.record_attempts:
            return
        record_rotator_event(
            "error",
            account_id=attempt.account_id,
            model=context.route.model,
            surface=context.route.surface,
            rotator=self._rotator_or_none(),
        )
        if self._stats_recorder is not None:
            try:
                self._stats_recorder(context.route.model, "errors")
            except Exception as exc:
                logger.debug("[routing] 空响应重试统计记录失败: %s", exc)

    def record_client(
        self,
        model: str,
        result: str,
        *,
        usage: dict[str, object] | None = None,
        finish_reason: str | None = None,
        rescued: bool = False,
    ) -> None:
        """Record one client-request terminal outcome (never per attempt)."""
        if self._client_recorder is None:
            return
        try:
            self._client_recorder(
                model,
                result,
                usage=usage,
                finish_reason=finish_reason,
                rescued=rescued,
            )
        except Exception as exc:
            logger.debug("[routing] client 终态记录失败: %s", exc)

    def record_count(self, model: str, result: str, *, input_tokens: int = 0) -> None:
        """Record one isolated countTokens terminal (no attempt/quota)."""
        if self._count_token_recorder is None:
            return
        try:
            self._count_token_recorder(model, result, input_tokens=input_tokens)
        except Exception as exc:
            logger.debug("[routing] countTokens 统计记录失败: %s", exc)

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
        reason_summary = _summarize_error(exc)

        if isinstance(exc, SessionExpiredError):
            self._record_error("session_expired", context, attempt, exc=exc)
            attempt.client.clear_templates()
            if has_yielded_data:
                raise exc
            return await self._switch_or_raise(
                context, attempt, exc, reason="session_expired"
            )

        if isinstance(exc, AuthError):
            if getattr(exc, "verification_required", False):
                # Google 移动设备验证（verify-account）风控：账号已被上游锁死，
                # 任何同号重签都只会二次撞墙，必须立即标记失效并切号。
                # 该事件只隔离当前单账号，绝不连坐同源兄弟号。
                self._record_error("verification_required", context, attempt, exc=exc)
                logger.error(
                    "[routing] 账号触发 Google 移动设备验证风控 (verify-account)，"
                    "已自动标记失效隔离并切号 instance=%s account=%s model=%s "
                    "reason=%s",
                    attempt.instance_id,
                    attempt.candidate.account_id,
                    context.route.model,
                    exc,
                )
                if has_yielded_data:
                    raise exc
                return await self._switch_or_raise(
                    context, attempt, exc, reason="verification_required"
                )
            if not tier_allows(attempt.candidate.tier, context.route):
                # The upstream call really happened and returned 403 on a
                # tier that cannot satisfy the route: count it as an attempt
                # before surfacing the tier error.
                self._record_error("auth_error", context, attempt, exc=exc)
                raise self._tier_error(context.route, attempt.candidate.tier) from exc
            self._record_error("auth_error", context, attempt, exc=exc)
            if has_yielded_data:
                attempt.client.clear_templates()
                raise exc
            if (
                attempt.same_retries == 0
                and context.total_attempts < context.max_retries
            ):
                # 403 常见根因是页面缓存的 SAPISID/__Secure-1PSIDTS 已过期；
                # 先用仍然有效的浏览器会话重新取 cookie，再重新签名重试。
                if await self._refresh_auth_state(context, attempt, reason_summary):
                    attempt.same_retries += 1
                    context.retry_same = True
                    context.budget.record_retry("403")
                    attempt.client.clear_templates()
                    logger.warning(
                        "[routing] 403 同账号重新签名重试 model=%s surface=%s "
                        "instance=%s account=%s reason=%s",
                        context.route.model,
                        context.route.surface,
                        attempt.instance_id,
                        attempt.account_id,
                        reason_summary,
                    )
                    return "retry_same"
                logger.warning(
                    "[routing] 403 鉴权刷新失败，拒绝重发过期凭据改走切换/报错 "
                    "model=%s surface=%s instance=%s account=%s reason=%s",
                    context.route.model,
                    context.route.surface,
                    attempt.instance_id,
                    attempt.account_id,
                    reason_summary,
                )
            return await self._switch_or_raise(
                context, attempt, exc, reason="auth_error"
            )

        if isinstance(exc, UsageLimitExceeded):
            self._record_error("rate_limited", context, attempt, exc=exc)
            if has_yielded_data:
                raise exc
            if (
                attempt.same_retries == 0
                and context.total_attempts < context.max_retries
            ):
                attempt.same_retries += 1
                context.retry_same = True
                context.budget.record_retry("429")
                logger.warning(
                    "[routing] 429 同账号重试 model=%s surface=%s "
                    "instance=%s account=%s reason=%s",
                    context.route.model,
                    context.route.surface,
                    attempt.instance_id,
                    attempt.account_id,
                    reason_summary,
                )
                if context.retry_same_delay_sec > 0:
                    logger.info(
                        "[routing] 429 同账号退避 %.2fs 后重试 instance=%s",
                        context.retry_same_delay_sec,
                        attempt.instance_id,
                    )
                    await asyncio.sleep(context.retry_same_delay_sec)
                return "retry_same"
            return await self._switch_or_raise(
                context,
                attempt,
                exc,
                reason="429",
                quota_exhausted=True,
            )

        if isinstance(exc, RequestError):
            if exc.status == 404 and not is_transient_rpc_error(exc):
                # A real 404 (model/route not found) is deterministic: never
                # retry and never switch accounts.
                self._record_error("error", context, attempt, exc=exc)
                raise exc
            transient = exc.status in (204, 404, 408, 425) or exc.status >= 500
            if not transient:
                self._record_error("error", context, attempt, exc=exc)
                raise exc
            if (
                not has_yielded_data
                and attempt.same_retries == 0
                and context.total_attempts < context.max_retries
            ):
                # Upstream transient failures (output timeout, connection
                # reset, 5xx, ambiguous RPC 404) are NOT account-level
                # failures.  Retry the same account once and then surface the
                # error: never rebind a healthy Pro/Free account away because
                # the upstream hiccuped.
                self._record_error("error", context, attempt, exc=exc)
                attempt.same_retries += 1
                context.retry_same = True
                context.budget.record_retry(f"retry_{exc.status}")
                attempt.client.clear_templates()
                logger.warning(
                    "[routing] %s 同账号重试 model=%s instance=%s account=%s reason=%s",
                    exc.status,
                    context.route.model,
                    attempt.instance_id,
                    attempt.account_id,
                    reason_summary,
                )
                if context.retry_same_delay_sec > 0:
                    logger.info(
                        "[routing] %s 同账号退避 %.2fs 后重试 instance=%s",
                        exc.status,
                        context.retry_same_delay_sec,
                        attempt.instance_id,
                    )
                    await asyncio.sleep(context.retry_same_delay_sec)
                return "retry_same"
            self._record_error("error", context, attempt, exc=exc)
            if exc.status == 204:
                message = (
                    "上游返回 204 空响应，同账号重试后仍为空"
                    if attempt.same_retries > 0
                    else "上游返回 204 空响应"
                )
                raise BrowserTransportError(message) from exc
            raise exc

        if is_browser_session_hang_error(exc):
            # Output timeouts (``SignerTimeoutError`` wrapped as
            # ``BrowserTransportError``), network aborts and template-capture
            # timeouts are transport/hang failures, never quota (429) or
            # credential (401/403/verify) failures.  Switching accounts here
            # would evict a healthy Pro slot for no reason and cascade through
            # the whole pool on a single network blip, so only one same-account
            # retry is allowed and every later failure is surfaced as-is.
            self._record_error("error", context, attempt, exc=exc)
            attempt.client.clear_templates()
            if (
                not has_yielded_data
                and attempt.same_retries == 0
                and context.total_attempts < context.max_retries
            ):
                attempt.same_retries += 1
                context.retry_same = True
                context.budget.record_retry("session_hang")
                logger.warning(
                    "[routing] 会话挂起/超时同账号重试（绝不切号） model=%s "
                    "surface=%s instance=%s account=%s reason=%s",
                    context.route.model,
                    context.route.surface,
                    attempt.instance_id,
                    attempt.account_id,
                    reason_summary,
                )
                return "retry_same"
            logger.warning(
                "[routing] 会话挂起/超时不再换绑账号，直接报错 model=%s surface=%s "
                "instance=%s account=%s yielded=%s reason=%s",
                context.route.model,
                context.route.surface,
                attempt.instance_id,
                attempt.account_id,
                has_yielded_data,
                reason_summary,
            )
            raise exc

        if isinstance(exc, (AistudioError, BrowserTransportError)):
            self._record_error("error", context, attempt, exc=exc)
            raise exc

        self._record_error("error", context, attempt, exc=exc)
        raise exc

    async def _refresh_auth_state(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        reason_summary: str,
    ) -> bool:
        """Refresh the same instance's cookie/signature state before a 403 retry.

        Returns False when the client cannot refresh or the live cookie jar no
        longer carries SAPISID material; callers must then fall back to
        switching accounts/instances instead of resending stale credentials.
        """
        refresher = getattr(attempt.client, "refresh_auth_state", None)
        if not callable(refresher):
            logger.warning(
                "[routing] 客户端不支持 403 鉴权刷新 instance=%s account=%s reason=%s",
                attempt.instance_id,
                attempt.account_id,
                reason_summary,
            )
            return False
        try:
            result = refresher()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.warning(
                "[routing] 403 鉴权刷新异常 instance=%s account=%s error=%s",
                attempt.instance_id,
                attempt.account_id,
                _summarize_error(exc),
            )
            return False
        refreshed = bool(result)
        if refreshed:
            logger.info(
                "[routing] 403 鉴权态已刷新 instance=%s account=%s model=%s",
                attempt.instance_id,
                attempt.account_id,
                context.route.model,
            )
        return refreshed

    async def _try_rebind(
        self,
        context: RouteContext,
        attempt: RouteAttempt,
        reason: str,
        *,
        reuse: bool,
        summary: str = "",
        record_budget: bool = True,
    ) -> bool:
        """Request-triggered single-slot rebind.

        A lane may have only one configured slot while the account store holds
        several healthy accounts in that lane (e.g. ``1 Free + 1 Pro`` slots and
        Free accounts A/B).  A 429 on the sole Free slot marks A failed, but
        ``next_candidate()`` then has no second instance to switch to.  This
        path picks another eligible account in the same lane first, then
        releases the active handle, rebinds the *existing* slot in place (close
        Page/Context, activate the new account), clears the old template cache
        and lets the caller re-acquire the slot.  It is synchronous with the
        request and never spawns a background rebind.

        With multiple slots the same heal happens immediately after a dead slot
        error, but the current request keeps moving to another ready candidate
        (``reuse=False``) instead of re-taking the slot it just failed.

        There is no per-slot one-rebind cap: one slot may rebind A -> B -> C
        when each account 429s.  Loop and duplicate protection comes from
        ``attempted_accounts``/``failed_accounts`` plus the per-request
        ``max_switches`` budget.

        ``record_budget=False`` is used only by the budget-exhausted path to
        pre-heal the dead slot for *future* requests; it never keeps the
        current request alive.  429 / verification rebinds additionally wait a
        randomized same-IP backoff before the caller re-acquires.
        """
        manager = self._manager
        reassign = getattr(manager, "reassign", None)
        selector = getattr(self._rotator, "select_rebind_account", None)
        if not callable(reassign) or not callable(selector):
            return False

        exclusions = (
            set(context.failed_accounts)
            | set(context.attempted_accounts)
            | {attempt.account_id}
        )
        bound = self._bound_account_ids()
        bound.discard(attempt.account_id)
        exclusions |= bound

        # Pick the rebind target *before* dropping the handle: when no eligible
        # account exists the request keeps its handle instead of opening an
        # idle window it never uses.  ``select_rebind_account`` depends only on
        # lane/model/tier and the exclusion set, never on ``active_requests``.
        account = self._pick_rebind_account(selector, context, attempt, exclusions)
        if account is None:
            return False
        target_id = str(getattr(account, "id", "") or "")
        if not target_id:
            return False

        # The pool refuses to activate while the handle is held.  Release it
        # first, then read ``active_requests``: the ledger counts this request's
        # own handle, so only after release does ``> 0`` mean another concurrent
        # request is still in flight on the slot.
        await self.release(attempt)
        if self._instance_active_requests(attempt.instance_id) > 0:
            logger.info(
                "[routing] 跳过改绑：实例仍有在途请求 instance=%s",
                attempt.instance_id,
            )
            return False

        for _ in range(len(exclusions) + 1):
            if reason in ("429", "verification_required"):
                # Back off *before* the new account is activated, not after:
                # the goal is to cool the same-IP switch moment itself.  This
                # applies even on the budget-exhausted pre-heal path
                # (``record_budget=False``) so the fresh account never starts
                # cold right after an instant switch.
                await self._switch_backoff(
                    instance_id=attempt.instance_id,
                    reason=reason,
                )
            try:
                result = reassign(attempt.instance_id, target_id)
                instance = await result if inspect.isawaitable(result) else result
            except Exception as exc:
                logger.warning(
                    "[routing] 单槽请求期改绑失败 instance=%s target=%s: %s",
                    attempt.instance_id,
                    target_id,
                    exc,
                )
                context.failed_accounts.add(target_id)
                exclusions.add(target_id)
                account = self._pick_rebind_account(
                    selector, context, attempt, exclusions
                )
                if account is None:
                    return False
                target_id = str(getattr(account, "id", "") or "")
                if not target_id:
                    return False
                continue

            if isinstance(instance, Instance):
                updated = RouteCandidate.from_instance(instance)
            else:
                updated = replace(
                    attempt.candidate,
                    account_id=target_id,
                    tier=str(getattr(account, "tier", "") or attempt.candidate.tier),
                    state=InstanceState.ACTIVE,
                    in_flight=0,
                )
            context.candidates = [
                updated if candidate.instance_id == attempt.instance_id else candidate
                for candidate in context.candidates
            ]
            if reuse:
                context.attempted_instances.discard(attempt.instance_id)
            if record_budget:
                context.budget.record_switch(reason)
            context.retry_same = False
            context.last_reason = f"rebind:{reason}"
            attempt.client.clear_templates()
            logger.warning(
                "[routing] 请求期改绑 reason=%s model=%s surface=%s "
                "instance=%s %s -> %s reuse=%s budget_charged=%s detail=%s "
                "budget=%s",
                reason,
                context.route.model,
                context.route.surface,
                attempt.instance_id,
                attempt.account_id,
                target_id,
                reuse,
                record_budget,
                summary,
                context.budget.snapshot(),
            )
            return True
        return False

    def _pick_rebind_account(
        self,
        selector: Callable[..., object],
        context: RouteContext,
        attempt: RouteAttempt,
        exclusions: set[str],
    ) -> object | None:
        """Select one rebind target for this slot (never reads active_requests)."""
        try:
            return selector(
                lane=context.route.lane,
                model=context.route.model,
                surface=context.route.surface,
                exclude_account_ids=exclusions,
                only_tier=attempt.candidate.only_tier,
                min_tier=attempt.candidate.min_tier,
                max_tier=attempt.candidate.max_tier,
                required_tiers=context.route.required_tiers,
            )
        except Exception as exc:
            logger.warning("[routing] 选择改绑账号失败: %s", exc)
            return None

    async def _switch_backoff(self, *, instance_id: str = "", reason: str = "") -> None:
        """Sleep one randomized switch backoff interval (0 disables it)."""
        low, high = self._switch_backoff_range
        if high <= 0.0:
            return
        delay = random.uniform(low, high)
        logger.info(
            "[routing] 切号退避 %.2fs 后继续（防同 IP 瞬切连坐）instance=%s reason=%s",
            delay,
            instance_id or "-",
            reason or "-",
        )
        await asyncio.sleep(delay)

    def _instance_active_requests(self, instance_id: str) -> int:
        getter = getattr(self._manager, "instance_active_requests", None)
        if not callable(getter):
            return 0
        try:
            value = getter(instance_id)
            return int(value) if isinstance(value, (int, float, str)) else 0
        except Exception:
            return 0

    def _bound_account_ids(self) -> set[str]:
        bound_fn = getattr(self._manager, "instance_account_ids", None)
        if not callable(bound_fn):
            return set()
        try:
            raw_bound = bound_fn()
        except Exception as exc:
            logger.debug("[routing] 读取已绑定账号失败: %s", exc)
            return set()
        return (
            {str(item) for item in raw_bound if item}
            if isinstance(raw_bound, (list, tuple, set, frozenset))
            else set()
        )

    def _live_instance(self, instance_id: str, route: ModelRoute) -> Instance | None:
        """Re-read one slot's current view (bound account/state/in-flight).

        Prefers a manager ``get_instance`` accessor when available and falls
        back to the lane view, which also reflects accounts rebound by a
        concurrent request after the caller took its snapshot.
        """
        manager = self._manager
        getter = getattr(manager, "get_instance", None)
        if callable(getter):
            try:
                current = getter(instance_id)
            except Exception as exc:
                logger.debug("[routing] 读取实例 %s 最新状态失败: %s", instance_id, exc)
            else:
                if isinstance(current, Instance):
                    return current
        lister = getattr(manager, "lane_instances", None)
        if callable(lister):
            try:
                items = lister(paid=route.paid)
                if isinstance(items, Iterable):
                    for item in items:
                        if isinstance(item, Instance) and item.id == instance_id:
                            return item
            except Exception as exc:
                logger.debug("[routing] 枚举 %s 车道实例失败: %s", route.lane, exc)
        return None

    async def _heal_unavailable_instances(
        self, route: ModelRoute, eligible: Iterable[Instance]
    ) -> int:
        """Rebind idle slots whose bound account is currently unavailable.

        This is the request-admission counterpart of :meth:`_try_rebind`: a
        slot left bound to a 429/dead account by an exhausted request must be
        healed before the next request, otherwise ``begin()`` would reject a
        fully healthy pool with a permanent 503.

        Healing is serialized by :attr:`_heal_lock`; each slot is re-read
        inside the lock so a concurrent ``begin()``/``acquire()`` that already
        rebound it or started a request is observed.  Only idle slots are
        touched (``active_requests == 0``); a busy slot is skipped untouched so
        an in-flight request on another model is never drained.  The rebind
        does not consume any request switch/retry budget because no attempt has
        been made yet, and ``healed`` only counts real account changes.
        """
        manager = self._manager
        reassign = getattr(manager, "reassign", None)
        selector = getattr(self._rotator, "select_rebind_account", None)
        if not callable(reassign) or not callable(selector):
            return 0

        healed = 0
        async with self._heal_lock:
            bound = self._bound_account_ids()
            for stale in list(eligible):
                live = self._live_instance(stale.id, route)
                if live is None or not live.account_id:
                    continue
                if self._instance_active_requests(live.id) > 0:
                    logger.info(
                        "[routing] 跳过死槽自愈：实例仍有在途请求 instance=%s "
                        "account=%s",
                        live.id,
                        live.account_id,
                    )
                    continue
                if self._account_available(live.account_id, route):
                    continue
                exclusions = set(bound)
                exclusions.discard(live.account_id)
                try:
                    account = selector(
                        lane=route.lane,
                        model=route.model,
                        surface=route.surface,
                        exclude_account_ids=exclusions,
                        only_tier=live.only_tier,
                        min_tier=live.min_tier,
                        max_tier=live.max_tier,
                        required_tiers=route.required_tiers,
                    )
                except Exception as exc:
                    logger.warning(
                        "[routing] 死槽自愈选择账号失败 instance=%s: %s",
                        live.id,
                        exc,
                    )
                    continue
                if account is None:
                    continue
                target_id = str(getattr(account, "id", "") or "")
                if not target_id or target_id == live.account_id:
                    continue
                try:
                    result = reassign(live.id, target_id)
                    updated = await result if inspect.isawaitable(result) else result
                except Exception as exc:
                    logger.warning(
                        "[routing] 死槽自愈改绑失败 instance=%s target=%s: %s",
                        live.id,
                        target_id,
                        exc,
                    )
                    continue
                bound.add(target_id)
                healed += 1
                logger.warning(
                    "[routing] 死槽自愈完成 reason=begin model=%s surface=%s "
                    "instance=%s %s -> %s state=%s",
                    route.model,
                    route.surface,
                    live.id,
                    live.account_id,
                    target_id,
                    getattr(updated, "state", "-"),
                )
        return healed

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
        can_continue = (
            context.total_attempts < context.max_retries and context.budget.can_switch()
        )
        if can_continue:
            # Heal the failed slot immediately (429-dead slots must not stay in
            # the pool).  When another candidate exists the request continues on
            # it; the rebound slot is healthy for subsequent requests.
            reuse = context.next_candidate() is None
            if await self._try_rebind(
                context,
                attempt,
                reason,
                reuse=reuse,
                summary=_summarize_error(exc),
            ):
                return "switch"
        elif not self._account_available(attempt.account_id, context.route):
            # Budget/candidates are exhausted for THIS request, but the slot
            # must not stay bound to a dead/429 account: heal it in place for
            # subsequent requests without charging the exhausted budget.
            await self._try_rebind(
                context,
                attempt,
                reason,
                reuse=False,
                summary=_summarize_error(exc),
                record_budget=False,
            )
        if not can_continue or context.next_candidate() is None:
            if quota_exhausted:
                raise SurfaceSwitchExhaustedError(
                    surface=context.route.surface
                ) from exc
            raise exc
        context.budget.record_switch(reason)
        context.retry_same = False
        await self._wake(context.route)
        logger.warning(
            "[routing] 切换实例 reason=%s model=%s surface=%s from=%s "
            "detail=%s budget=%s",
            reason,
            context.route.model,
            context.route.surface,
            attempt.instance_id,
            _summarize_error(exc),
            context.budget.snapshot(),
        )
        return "switch"

    def _record_error(
        self,
        event: str,
        context: RouteContext,
        attempt: RouteAttempt,
        *,
        exc: Exception | None = None,
        summary: str | None = None,
    ) -> None:
        if not context.record_attempts:
            return
        record_rotator_event(
            event,
            account_id=attempt.account_id,
            model=context.route.model,
            surface=context.route.surface,
            reason=_summarize_error(exc, summary) or None,
            rotator=self._rotator_or_none(),
        )
        if self._stats_recorder is not None:
            try:
                self._stats_recorder(
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
