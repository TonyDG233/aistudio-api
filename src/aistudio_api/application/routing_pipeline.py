"""Single routing pipeline for WP5 phase 2.

Resolution order: capability/channel -> candidate pool -> group constraints ->
sort -> instance landing -> failure chain. The channel is fixed in step 1 and
never changes inside a request; every denial is explicit (no silent fallback).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NoReturn

from aistudio_api.application.account_routing import (
    apply_account_routing,
    normalize_account_routing,
)
from aistudio_api.application.channel_registry import (
    build_registry,
    enabled_channels,
    is_multi_mode,
    parse_model_target,
    select_channel,
)
from aistudio_api.application.routing_safety import CircuitBreaker, RoutingBudget
from aistudio_api.application.surface_orchestrator import (
    GroupPolicy,
    SurfacePolicyError,
    account_matches_group,
    intersect_surfaces,
    is_chat_only_model,
    resolve_surface,
    select_group,
    surface_capability,
)
from aistudio_api.infrastructure.gateway.instance_manager import (
    InstanceLease,
    InstanceManager,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("application.routing")

_CHANNEL_SURFACES = {"chat": "chat", "build": "build"}


@dataclass
class Candidate:
    """One eligible account/channel pair, before instance landing."""

    account_id: str
    name: str
    tier: str | None
    email: str | None
    channel: str
    surface: str
    weight: float
    available: bool
    slot_index: int | None = None
    reason: str = ""


@dataclass
class RouteContext:
    """Request-scoped routing state (candidates frozen at step 4)."""

    request_id: str
    model_path: str
    clean_model: str
    api_format: str
    channel: str
    surface: str
    group: GroupPolicy
    candidates: list[Candidate]
    budget: RoutingBudget
    breaker: CircuitBreaker
    multi_mode: bool
    started_at: float
    attempted: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    retry_same: bool = False
    last_reason: str = "initial"

    def next_candidate(self, failed_account_id: str | None = None) -> Candidate | None:
        if failed_account_id:
            self.failed.add(failed_account_id)
        for candidate in self.candidates:
            if candidate.account_id in self.failed:
                continue
            if candidate.account_id in self.attempted:
                continue
            return candidate
        return None

    def snapshot(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "model": self.clean_model,
            "api_format": self.api_format,
            "channel": self.channel,
            "surface": self.surface,
            "group": self.group.name or self.group.source,
            "candidates": [candidate.account_id for candidate in self.candidates],
            "attempted": sorted(self.attempted),
            "failed": sorted(self.failed),
            "budget": self.budget.snapshot(),
            "circuit": self.breaker.snapshot(),
        }


def _raise(
    code: str,
    *,
    message: str,
    advice: str,
) -> NoReturn:
    logger.warning("[surface] 策略拒绝 code=%s: %s", code, message)
    raise SurfacePolicyError(code, message=message, advice=advice)


class RoutingPipeline:
    """Plan and execute one request's route."""

    def __init__(
        self,
        client,
        *,
        account_store=None,
        features_provider: Callable[[], dict[str, object]] | None = None,
        breaker: CircuitBreaker | None = None,
        rotator=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._store = account_store
        self._features_provider = features_provider
        self._breaker = breaker or CircuitBreaker(clock=clock)
        self._rotator_override = rotator
        self._clock = clock
        self.instances = InstanceManager(
            client,
            features_provider=features_provider,
            account_store=account_store,
        )

    def _store_or_default(self):
        if self._store is not None:
            return self._store
        from aistudio_api.infrastructure.account.account_store import AccountStore

        return AccountStore()

    def _features(self) -> dict[str, object]:
        if self._features_provider is not None:
            try:
                return dict(self._features_provider())
            except Exception:
                return {}
        try:
            from aistudio_api.infrastructure.gateway.model_defaults import (
                get_features_config,
            )

            return dict(get_features_config())
        except Exception:
            return {}

    def _rotator(self):
        if self._rotator_override is not None:
            return self._rotator_override
        try:
            from aistudio_api.api.state import runtime_state

            return runtime_state.rotator
        except Exception:
            return None

    def _select(
        self,
        model_path: str,
        features: dict[str, object],
        active,
    ) -> tuple[str, str, str]:
        """Return ``(channel, surface, clean_model)`` for the request."""
        explicit_channel, clean_model = parse_model_target(model_path)
        registry = build_registry(features)
        channels = enabled_channels(registry)
        route_by_tier = bool(features.get("route_by_tier"))
        if (
            explicit_channel is None
            and not route_by_tier
            and len(channels) > 1
            and active is not None
        ):
            surface = resolve_surface(
                model_path, active.tier, active.email, features
            ).surface
            return surface, surface, clean_model
        selection = select_channel(
            model_path,
            features,
            mounted_channels=self.instances.mounted_channels(),
            model_is_chat_only=is_chat_only_model(clean_model),
        )
        if selection.surface is None:
            _raise(
                "surface_channel_disabled",
                message=f"渠道 '{selection.channel}' 尚未实现，无法处理模型 '{model_path}'。",
                advice="WP9 前官网渠道不可用；请改用 chat/build 渠道或裸模型名。",
            )
        return selection.channel, selection.surface, selection.clean_model

    def begin(self, *, model_path: str, api_format: str) -> RouteContext:
        features = self._features()
        store = self._store_or_default()
        active = store.get_active_account()
        channel, surface, clean_model = self._select(model_path, features, active)

        groups = features.get("groups") or []
        raw_channels = features.get("channels")
        channels_cfg = raw_channels if isinstance(raw_channels, dict) else {}
        raw_build = channels_cfg.get("build")
        build_cfg = raw_build if isinstance(raw_build, dict) else {}
        build_enabled = bool(
            build_cfg.get("enabled", features.get("build_surface", False))
        )
        group = select_group(
            getattr(active, "tier", None),
            getattr(active, "email", None),
            groups,
            route_by_tier=bool(features.get("route_by_tier")),
            build_enabled=build_enabled,
        )
        if surface not in group.allowed_surfaces:
            _raise(
                "surface_group_disabled",
                message=(
                    f"分组 '{group.name or group.source}' 不允许 {surface} 面"
                    f"（allowed={list(group.allowed_surfaces)}）。"
                ),
                advice="请调整该组 allowed_surfaces 或账号分组；本次请求不换面、不换渠道。",
            )

        registry = build_registry(features)
        enabled_surfaces = tuple(
            spec.key
            for spec in enabled_channels(registry)
            if spec.key in _CHANNEL_SURFACES
        )
        capability = surface_capability(clean_model, enabled_surfaces)
        if surface not in capability:
            _raise(
                "surface_model_guard",
                message=(
                    f"模型 '{clean_model}' 的能力集为 {list(capability)}，"
                    f"不包含已解析的 {surface} 面。"
                ),
                advice="请改用 chat 面或支持该面的模型；本次请求不静默换面。",
            )

        slots = self.instances.slot_indexes()
        rotator = self._rotator()
        candidates: list[Candidate] = []
        for account in store.list_accounts():
            if getattr(account, "expired", False):
                continue
            if group.source != "implicit-global" and not account_matches_group(
                group, account.tier, account.email, groups
            ):
                continue
            routing = normalize_account_routing(store.get_account_routing(account.id))
            narrowed = intersect_surfaces(
                capability,
                group,
                apply_account_routing(enabled_surfaces, routing, clean_model),
            )
            if surface not in narrowed:
                continue
            available = True
            if rotator is not None:
                try:
                    available = bool(
                        rotator.is_account_available(
                            account.id, model=clean_model, surface=surface
                        )
                    )
                except Exception:
                    available = True
            candidates.append(
                Candidate(
                    account_id=account.id,
                    name=account.name,
                    tier=account.tier,
                    email=account.email,
                    channel=channel,
                    surface=surface,
                    weight=max(0.1, group.weight * routing.weight),
                    available=available,
                    slot_index=slots.get((account.id, channel)),
                )
            )

        if not candidates:
            _raise(
                "surface_no_capable_account",
                message=(
                    f"模型 '{clean_model}' 在组 '{group.name or group.source}' / "
                    f"渠道 '{channel}' 下没有可用账号。"
                ),
                advice=(
                    "请检查账号 tier/邮箱分组、账号 routing 覆盖、凭据是否过期，"
                    "或调整该组 allowed_surfaces；本次请求不静默换组。"
                ),
            )

        active_id = getattr(active, "id", None)
        candidates.sort(
            key=lambda candidate: (
                0 if candidate.available else 1,
                0 if candidate.account_id == active_id else 1,
                0 if candidate.slot_index is not None else 1,
                self._score(candidate, rotator),
                candidate.account_id,
            )
        )
        raw_routing = features.get("routing")
        routing_cfg = raw_routing if isinstance(raw_routing, dict) else {}
        raw_slots = features.get("slots")
        slots_list = raw_slots if isinstance(raw_slots, list) else []
        try:
            max_switches = max(0, int(routing_cfg.get("max_switches", 2)))
        except (TypeError, ValueError):
            max_switches = 2
        budget = RoutingBudget(max_switches=max_switches)
        ctx = RouteContext(
            request_id=f"route-{int(self._clock() * 1000)}",
            model_path=model_path,
            clean_model=clean_model,
            api_format=api_format,
            channel=channel,
            surface=surface,
            group=group,
            candidates=candidates,
            budget=budget,
            breaker=self._breaker,
            multi_mode=is_multi_mode(registry, slots_list),
            started_at=self._clock(),
        )
        logger.info(
            "[surface] request=%s model=%s format=%s channel=%s surface=%s "
            "group=%s source=%s candidates=%s multi_mode=%s step=①->④",
            ctx.request_id,
            clean_model,
            api_format,
            channel,
            surface,
            group.name or "-",
            group.source,
            ",".join(candidate.account_id for candidate in candidates),
            ctx.multi_mode,
        )
        return ctx

    @staticmethod
    def _score(candidate: Candidate, rotator) -> tuple:
        stats = None
        if rotator is not None:
            try:
                stats = rotator.get_account_stats(candidate.account_id)
            except Exception:
                stats = None
        requests = float(getattr(stats, "requests", 0) or 0)
        last_used = float(getattr(stats, "last_used", 0) or 0)
        return (requests / candidate.weight, last_used, candidate.account_id)

    def next_candidate(
        self, ctx: RouteContext, failed_account_id: str | None = None
    ) -> Candidate | None:
        """Pick the next candidate that can land within budget."""
        candidate = ctx.next_candidate(failed_account_id)
        while candidate is not None:
            allowed, reason = self.instances.can_land(ctx.channel, ctx.group)
            if allowed:
                return candidate
            if not ctx.budget.can_defer():
                _raise(
                    "surface_quota_exceeded",
                    message=(
                        f"渠道 '{ctx.channel}' 实例预算已满（{reason}），"
                        "且顺延预算已耗尽。"
                    ),
                    advice="请提高 global/channel/group 预算、关闭闲置实例，或稍后重试。",
                )
            ctx.budget.record_defer(reason or "budget")
            logger.info(
                "[surface:budget] request=%s account=%s defer=%s step=⑤",
                ctx.request_id,
                candidate.account_id,
                reason,
            )
            ctx.attempted.add(candidate.account_id)
            candidate = ctx.next_candidate(None)
        return None

    async def land(
        self,
        ctx: RouteContext,
        candidate: Candidate,
        *,
        require_idle: bool = True,
    ) -> InstanceLease | None:
        ctx.attempted.add(candidate.account_id)
        lease = await self.instances.land(
            account_id=candidate.account_id,
            channel=ctx.channel,
            surface=ctx.surface,
            model=ctx.clean_model,
            require_idle=require_idle,
        )
        if lease is None:
            ctx.failed.add(candidate.account_id)
            return None
        logger.info(
            "[surface] request=%s account=%s channel=%s realm=%s slot=%s step=⑤",
            ctx.request_id,
            lease.account_id,
            lease.channel,
            lease.realm_id,
            lease.slot_index,
        )
        return lease

    def record_retry(self, ctx: RouteContext, reason: str) -> None:
        ctx.budget.record_retry()
        ctx.last_reason = reason
        logger.info(
            "[surface] request=%s retry_same reason=%s budget=%s",
            ctx.request_id,
            reason,
            ctx.budget.snapshot(),
        )

    def record_switch(self, ctx: RouteContext, reason: str) -> None:
        if self._breaker.is_open():
            _raise(
                "surface_switch_exhausted",
                message="路由熔断已打开，拒绝继续切换账号/实例。",
                advice="请稍后重试并检查上游账号健康状态；本次请求不继续切换。",
            )
        if not ctx.budget.record_switch():
            _raise(
                "surface_switch_exhausted",
                message=(
                    "单请求切换预算已耗尽"
                    f"（max_switches={ctx.budget.snapshot()['max_switches']}）。"
                ),
                advice="请调高 features.routing.max_switches 或修复上游账号；本次请求不继续切换。",
            )
        ctx.last_reason = reason
        logger.warning(
            "[surface:switch] request=%s reason=%s budget=%s",
            ctx.request_id,
            reason,
            ctx.budget.snapshot(),
        )

    def record_switch_failure(self, ctx: RouteContext) -> None:
        opened = self._breaker.record_switch_failure()
        if opened:
            logger.error(
                "[surface:circuit] opened request=%s snapshot=%s",
                ctx.request_id,
                self._breaker.snapshot(),
            )

    def record_success(self, ctx: RouteContext) -> None:
        self._breaker.record_switch_success()
        ctx.retry_same = False
        logger.info(
            "[surface] request=%s success account_budget=%s circuit=%s",
            ctx.request_id,
            ctx.budget.snapshot(),
            self._breaker.snapshot(),
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "circuit": self._breaker.snapshot(),
            "instances": self.instances.snapshot(),
        }


_pipeline: RoutingPipeline | None = None
_pipeline_client = None


def get_routing_pipeline() -> RoutingPipeline | None:
    """Lazy process-wide pipeline bound to the current runtime client."""
    global _pipeline, _pipeline_client
    try:
        from aistudio_api.api.state import runtime_state

        client = runtime_state.client
    except Exception:
        client = None
    if client is None:
        return None
    if _pipeline is None or _pipeline_client is not client:
        _pipeline = RoutingPipeline(client)
        _pipeline_client = client
    return _pipeline


__all__ = [
    "Candidate",
    "RouteContext",
    "RoutingPipeline",
    "get_routing_pipeline",
]
