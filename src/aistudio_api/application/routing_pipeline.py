"""Single routing pipeline for WP5 v3.

Resolution order: channel/capability -> per-account eligibility -> candidate
sort -> pre-dispatch slot acquire -> failure chain. The channel is fixed in step
1 and never changes inside a request; every denial is explicit.

Groups are per-account constraints, not a request-level singleton. The active
account is only a weak preference; it never decides the group or the channel.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NoReturn

from aistudio_api.application.channel_registry import (
    build_registry,
    is_multi_mode,
    normalize_channel,
)
from aistudio_api.application.routing_eligibility import (
    AccountView,
    choose_channel,
    eligible_accounts,
)
from aistudio_api.application.routing_safety import CircuitBreaker, RoutingBudget
from aistudio_api.application.surface_orchestrator import (
    GroupPolicy,
    SurfacePolicyError,
)
from aistudio_api.infrastructure.gateway.instance_pool import (
    InstanceCapacityError,
    InstanceLease,
    InstancePool,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("application.routing")


@dataclass
class Candidate:
    """One eligible account/channel pair, before instance acquisition."""

    account_id: str
    name: str
    tier: str | None
    email: str | None
    channel: str
    surface: str
    group: GroupPolicy
    quota_pools: tuple[str, ...]
    affinity: int
    weight: float
    available: bool
    slot_index: int | None = None
    reason: str = ""


@dataclass
class RouteContext:
    """Request-scoped routing state; candidates are frozen at begin()."""

    request_id: str
    model_path: str
    clean_model: str
    api_format: str
    channel: str
    surface: str
    candidates: list[Candidate]
    budget: RoutingBudget
    breaker: CircuitBreaker
    multi_mode: bool
    started_at: float
    borrow_enabled: bool = False
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
            "groups": sorted(
                {
                    candidate.group.name or candidate.group.source
                    for candidate in self.candidates
                }
            ),
            "candidates": [
                {
                    "account_id": candidate.account_id,
                    "group": candidate.group.name or candidate.group.source,
                    "quota_pools": list(candidate.quota_pools),
                    "affinity": candidate.affinity,
                }
                for candidate in self.candidates
            ],
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
        pool = getattr(client, "instance_pool", None)
        if pool is None:
            pool = InstancePool(
                client._ensure_browser_cdp_client,
                features_provider=self._features,
            )
        self.pool: InstancePool = pool

    def _instance_manager(self):
        try:
            from aistudio_api.api.state import runtime_state

            return runtime_state.instance_manager
        except Exception:
            return None

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

    def _auth_for(self, account_id: str) -> tuple[str | None, str | None]:
        try:
            from aistudio_api.api.state import runtime_state

            service = runtime_state.account_service
            if service is not None:
                path = service.get_account_auth_path(account_id)
                account = service.get_account(account_id)
                return (
                    str(path) if path is not None else None,
                    getattr(account, "auth_user", None),
                )
        except Exception:
            pass
        return None, None

    def _mounted_channels(self) -> tuple[str, ...]:
        counts = self.pool.page_counts()
        mounted = tuple(
            channel for channel in ("chat", "build") if counts.get(channel, 0) > 0
        )
        if mounted:
            return mounted
        # Control-plane fallback is only a display hint; it never authorizes a
        # request instance.
        return ()

    def _slot_indexes(self, features: dict[str, object]) -> dict[tuple[str, str], int]:
        raw_slots = features.get("slots")
        slots: list[object] = raw_slots if isinstance(raw_slots, list) else []
        indexes: dict[tuple[str, str], int] = {}
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict) or not bool(slot.get("enabled", True)):
                continue
            account_id = str(slot.get("account_id") or "").strip()
            channel = normalize_channel(slot.get("channel"))
            if not account_id or channel is None:
                continue
            indexes[(account_id, channel)] = index
        return indexes

    def begin(self, *, model_path: str, api_format: str) -> RouteContext:
        features = self._features()
        store = self._store_or_default()
        accounts = list(store.list_accounts())
        raw_groups = features.get("groups")
        groups: list[object] = raw_groups if isinstance(raw_groups, list) else []
        registry = build_registry(features)
        raw_slots = features.get("slots")
        slots: list[object] = raw_slots if isinstance(raw_slots, list) else []
        views = [AccountView.from_account(account) for account in accounts]
        manager = self._instance_manager()
        if manager is not None:
            instance_accounts = manager.instance_account_ids()
            if instance_accounts:
                # Keep explicit instances authoritative, but do not drop accounts
                # just because their instance is temporarily invalid/asleep.
                views = [
                    view for view in views if view.account_id in instance_accounts
                ]
        selection = choose_channel(
            model_path,
            views,
            features,
            groups=groups,
            mounted_channels=self._mounted_channels(),
        )
        channel = selection.channel
        surface = selection.surface
        clean_model = selection.clean_model
        if surface is None:
            _raise(
                "surface_channel_disabled",
                message=f"渠道 '{channel}' 尚未实现，无法处理模型 '{model_path}'。",
                advice="WP9 前官网渠道不可用；请改用 chat/build 渠道或裸模型名。",
            )

        eligible = eligible_accounts(
            views,
            channel,
            clean_model,
            features=features,
            registry=registry,
            groups=groups,
        )
        if not eligible:
            group_names = [
                str(group.get("name") or f"group-{index}")
                for index, group in enumerate(groups)
                if isinstance(group, dict)
            ]
            _raise(
                "surface_no_capable_account",
                message=(
                    f"模型 '{clean_model}' / 渠道 '{channel}' 没有合格账号。"
                    f"已配置组={group_names or ['implicit-global']}。"
                ),
                advice=(
                    "请检查账号 tier/额度池、组 allowed_surfaces/"
                    "allowed_quota_pools、渠道 quota_pool，或为请求显式指定可用渠道前缀。"
                ),
            )

        slot_indexes = self._slot_indexes(features)
        rotator = self._rotator()
        ready_accounts = {
            slot.current_account_id
            for slot in self.pool.slots()
            if slot.current_account_id
            and slot.in_flight == 0
            and (slot.chat_ready() or slot.build_ready())
        }
        candidates: list[Candidate] = []
        for view in eligible:
            account = view.account
            available = True
            if rotator is not None:
                try:
                    available = bool(
                        rotator.is_account_available(
                            account.account_id, model=clean_model, surface=surface
                        )
                    )
                except Exception:
                    available = True
            candidates.append(
                Candidate(
                    account_id=account.account_id,
                    name=account.name,
                    tier=account.tier,
                    email=account.email,
                    channel=channel,
                    surface=surface,
                    group=view.group,
                    quota_pools=view.quota_pools,
                    affinity=view.affinity,
                    weight=max(0.1, view.group.weight * account.routing.weight),
                    available=available,
                    slot_index=slot_indexes.get((account.account_id, channel)),
                    reason="eligible",
                )
            )

        candidates.sort(
            key=lambda candidate: (
                0 if candidate.available else 1,
                candidate.affinity,
                -candidate.weight,
                self._score(candidate, rotator),
                # Slot/ready reuse is a tie-breaker, not a starvation switch:
                # an already-warm account must not pin all future requests.
                0 if candidate.slot_index is not None else 1,
                0 if candidate.account_id in ready_accounts else 1,
                candidate.account_id,
            )
        )

        raw_routing = features.get("routing")
        routing_cfg = raw_routing if isinstance(raw_routing, dict) else {}
        try:
            max_switches = max(0, int(routing_cfg.get("max_switches", 2)))
        except (TypeError, ValueError):
            max_switches = 2
        ctx = RouteContext(
            request_id=f"route-{int(self._clock() * 1000)}",
            model_path=model_path,
            clean_model=clean_model,
            api_format=api_format,
            channel=channel,
            surface=surface,
            candidates=candidates,
            budget=RoutingBudget(max_switches=max_switches),
            breaker=self._breaker,
            multi_mode=is_multi_mode(registry, slots),
            started_at=self._clock(),
            borrow_enabled=bool(routing_cfg.get("borrow", False)),
        )
        logger.info(
            "[surface] request=%s model=%s format=%s channel=%s surface=%s "
            "groups=%s candidates=%s multi_mode=%s step=①->④",
            ctx.request_id,
            clean_model,
            api_format,
            channel,
            surface,
            ",".join(
                sorted(
                    {
                        candidate.group.name or candidate.group.source
                        for candidate in candidates
                    }
                )
            ),
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
        # Last-used is the primary fairness signal. Lifetime request totals must
        # not starve an older account behind newer accounts forever. Keep a
        # candidate-identity tie-break in the outer sort so a warm slot can
        # break an exact fairness tie.
        return (last_used, requests / candidate.weight)

    def next_candidate(
        self, ctx: RouteContext, failed_account_id: str | None = None
    ) -> Candidate | None:
        return ctx.next_candidate(failed_account_id)

    async def acquire(
        self,
        ctx: RouteContext,
        candidate: Candidate,
        *,
        require_idle: bool = True,
    ) -> InstanceLease:
        """Acquire a ready slot, switching/loading an eligible account first."""
        del require_idle
        ctx.attempted.add(candidate.account_id)
        auth_file, auth_user = self._auth_for(candidate.account_id)
        first_attempt = not ctx.failed and not ctx.attempted - {candidate.account_id}
        timeout_s = 30.0 if first_attempt else 15.0
        try:
            lease = await self.pool.acquire(
                account_id=candidate.account_id,
                auth_file=auth_file,
                auth_user=auth_user,
                channel=ctx.channel,
                surface=ctx.surface,
                timeout_s=timeout_s,
                group_name=candidate.group.name or candidate.group.source,
                group_max_pages=candidate.group.max_pages,
            )
        except InstanceCapacityError as exc:
            capacity_code = (
                "surface_instance_busy"
                if ctx.channel == "build"
                else "surface_quota_exceeded"
            )
            if not ctx.borrow_enabled:
                _raise(
                    capacity_code,
                    message=(
                        f"渠道 '{ctx.channel}' 实例预算已满，无法为账号 "
                        f"'{candidate.account_id}' 装载/切换实例。"
                    ),
                    advice="等待当前实例释放；build 不跨账号抢占单实例名额。",
                )
            borrow_ids = {
                item.account_id
                for item in ctx.candidates
                if item.account_id not in ctx.failed
                and item.account_id not in ctx.attempted
            }
            if not borrow_ids:
                _raise(
                    capacity_code,
                    message=f"渠道 '{ctx.channel}' 无空闲可借实例：{exc}",
                    advice="等待当前实例释放；build 不跨账号抢占单实例名额。",
                )
            try:
                lease = await self.pool.acquire(
                    account_id=candidate.account_id,
                    auth_file=auth_file,
                    auth_user=auth_user,
                    channel=ctx.channel,
                    surface=ctx.surface,
                    timeout_s=0.0,
                    borrow_accounts=borrow_ids,
                    group_name=candidate.group.name or candidate.group.source,
                    group_max_pages=candidate.group.max_pages,
                )
            except InstanceCapacityError as borrow_exc:
                _raise(
                    capacity_code,
                    message=(f"渠道 '{ctx.channel}' 无可用/可借实例：{borrow_exc}"),
                    advice="等待当前实例释放；build 不跨账号抢占单实例名额。",
                )
        logger.info(
            "[surface] request=%s account=%s channel=%s realm=%s slot=%s borrow=%s step=⑤",
            ctx.request_id,
            lease.account_id,
            lease.channel,
            lease.realm_id,
            lease.slot_index,
            lease.is_borrow,
        )
        return lease

    async def land(
        self,
        ctx: RouteContext,
        candidate: Candidate,
        *,
        require_idle: bool = True,
    ) -> InstanceLease:
        """Compatibility alias for v3 acquire."""
        return await self.acquire(ctx, candidate, require_idle=require_idle)

    async def release(self, ctx: RouteContext, lease: InstanceLease | None) -> None:
        del ctx
        if lease is not None:
            await self.pool.release(lease)

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
        features = self._features()
        raw_slots = features.get("slots")
        slots: list[object] = raw_slots if isinstance(raw_slots, list) else []
        planned = sum(
            1
            for slot in slots
            if isinstance(slot, dict) and bool(slot.get("enabled", True))
        )
        return {
            "circuit": self._breaker.snapshot(),
            "instances": {
                "contexts": self.pool.snapshot(),
                "max_contexts": self.pool.max_contexts,
                "pages": self.pool.page_counts(),
                "budgets": self.pool.budget_snapshot(),
                "planned_instances": planned,
            },
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
