"""Pure WP5 v3 eligibility graph.

This module answers one question without touching the browser or the request
pipeline: given an account, a channel and a model, may this account serve the
request, and under which group/tag constraints?

Design rules (see ``docs/dev/WP5-V3-DESIGN-20261019.md``):

- Groups are *per-account* constraint layers, never a request-level singleton.
- A channel's ``allowed_tags`` is an admission gate, not a model list.
- Account tags and group surfaces only narrow the tier-derived capability.
- Pro-only models require a paid account independently of tags.
- Candidate ordering lives in ``routing_selection``; switch-specific planning
  lives in ``instance_switch``; capacity lives in the instance pool/manager.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from aistudio_api.application.account_routing import (
    AccountRouting,
    apply_account_routing,
    normalize_account_routing,
)
from aistudio_api.application.channel_registry import (
    ChannelSelection,
    ChannelSpec,
    build_registry,
    enabled_channels,
    normalize_channel,
    select_channel,
)
from aistudio_api.application.routing_selection import rank_eligible
from aistudio_api.application.surface_orchestrator import (
    GroupPolicy,
    SurfacePolicyError,
    account_is_paid,
    account_tier_quota_pools,
    is_chat_only_model,
    model_requires_paid_account,
    select_group,
)
from aistudio_api.domain.account_tags import account_tags, tag_matches

_QUOTA_POOLS = frozenset({"free", "pro", "official"})


@dataclass(frozen=True)
class AccountView:
    """Browser-independent snapshot of one account used by routing."""

    account_id: str
    tier: str | None
    email: str | None
    expired: bool
    routing: AccountRouting
    name: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", tuple(account_tags(self.tags, self.tier)))

    @classmethod
    def from_account(cls, account: object) -> AccountView:
        raw_routing = getattr(account, "routing", None)
        tier = getattr(account, "tier", None)
        return cls(
            account_id=str(getattr(account, "id", "") or ""),
            tier=tier,
            email=getattr(account, "email", None),
            expired=bool(getattr(account, "expired", False)),
            routing=normalize_account_routing(raw_routing),
            name=str(getattr(account, "name", "") or ""),
            tags=tuple(account_tags(getattr(account, "tags", []), tier)),
        )


@dataclass(frozen=True)
class EligibleAccount:
    """One account that may serve a specific channel/model request."""

    account: AccountView
    group: GroupPolicy
    quota_pools: tuple[str, ...]
    affinity: int
    channel: str

    @property
    def account_id(self) -> str:
        return self.account.account_id


def normalize_quota_pools(value: object) -> tuple[str, ...]:
    """Normalize a quota-pool list, preserving order and dropping unknown values."""
    if isinstance(value, str):
        raw: Iterable[object] = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = value
    else:
        return ()
    pools: list[str] = []
    for item in raw:
        text = str(item).strip().lower()
        if text in _QUOTA_POOLS and text not in pools:
            pools.append(text)
    return tuple(pools)


def effective_account_pools(
    tier: object,
    routing: AccountRouting | None,
) -> tuple[str, ...]:
    """Tier-derived pools narrowed by account routing (never widened)."""
    base = account_tier_quota_pools(tier)
    normalized = normalize_account_routing(routing)
    if not normalized.quota_pools:
        return base
    requested = set(normalized.quota_pools)
    return tuple(pool for pool in base if pool in requested)


def channel_affinity(tier: object, channel: str) -> int:
    """Lower is better: native channel before cross-surface fallback."""
    canonical = normalize_channel(channel) or channel
    paid = account_is_paid(tier)
    if canonical == "chat":
        return 0 if paid else 1
    if canonical == "build":
        return 1 if paid else 0
    return 2


def resolve_account_group(
    tier: object,
    email: object,
    features: Mapping[str, object],
    groups: object,
) -> GroupPolicy:
    """Resolve the first matching group for one account.

    Explicit groups always apply when present; ``route_by_tier`` only decides
    the fallback for accounts that match no explicit group.
    """
    registry = build_registry(features)
    build_enabled = registry["build"].enabled
    route_by_tier = bool(features.get("route_by_tier", False))
    if not bool(features.get("groups_enabled", True)):
        groups = []
    return select_group(
        tier,
        email,
        groups,
        route_by_tier=route_by_tier,
        build_enabled=build_enabled,
    )


def _group_surface_allows(group: GroupPolicy, channel: str) -> bool:
    return channel in group.allowed_surfaces


def eligible_account(
    account: AccountView,
    channel: str,
    model: str,
    *,
    features: Mapping[str, object],
    registry: Mapping[str, ChannelSpec] | None = None,
    groups: object = None,
) -> EligibleAccount | None:
    """Return the account's eligibility for one fixed channel/model."""
    canonical = normalize_channel(channel)
    if canonical is None:
        return None
    reg = registry if registry is not None else build_registry(features)
    spec = reg.get(canonical)
    if spec is None or not spec.enabled or not spec.implemented:
        return None
    if account.expired:
        return None
    if model_requires_paid_account(model) and (
        canonical != "chat" or not account_is_paid(account.tier)
    ):
        return None

    group_config = features.get("groups") if groups is None else groups
    group = resolve_account_group(account.tier, account.email, features, group_config)
    if not group.enabled or not _group_surface_allows(group, canonical):
        return None

    narrowed = apply_account_routing(group.allowed_surfaces, account.routing, model)
    if canonical not in narrowed:
        return None

    if not tag_matches(spec.allowed_tags, account.tags):
        return None

    pools = effective_account_pools(account.tier, account.routing)
    return EligibleAccount(
        account=account,
        group=group,
        quota_pools=pools,
        affinity=channel_affinity(account.tier, canonical),
        channel=canonical,
    )


def eligible_accounts(
    accounts: Iterable[AccountView],
    channel: str,
    model: str,
    *,
    features: Mapping[str, object],
    registry: Mapping[str, ChannelSpec] | None = None,
    groups: object = None,
) -> list[EligibleAccount]:
    """Return eligible accounts sorted by native affinity, group/account weight."""
    result: list[EligibleAccount] = []
    for account in accounts:
        candidate = eligible_account(
            account,
            channel,
            model,
            features=features,
            registry=registry,
            groups=groups,
        )
        if candidate is not None:
            result.append(candidate)
    return rank_eligible(result)


def _no_capable_account(
    model_path: str,
    channel: str,
    reason: str,
) -> SurfacePolicyError:
    return SurfacePolicyError(
        "surface_no_capable_account",
        message=(
            f"模型 '{model_path}' 在渠道 '{channel}' 没有合格账号：{reason}。"
            "本次请求不静默换渠道或降级。"
        ),
        advice=(
            "请检查账号 tier/tag、组 allowed_surfaces、"
            "渠道的 enabled/allowed_tags，或为请求显式指定可用渠道前缀。"
        ),
    )


def choose_channel(
    model_path: str,
    accounts: Sequence[AccountView],
    features: Mapping[str, object],
    *,
    groups: object = None,
    mounted_channels: Collection[str] = (),
) -> ChannelSelection:
    """Resolve a model target to a channel using eligible-account previews.

    Explicit prefixes are pinned. For bare multi-channel requests, build is
    preferred when it has eligible accounts (native free pool), otherwise chat.
    The chosen channel is final for the request; no silent per-attempt switch.
    """
    registry = build_registry(features)
    selection = select_channel(
        model_path,
        features,
        mounted_channels=mounted_channels,
        model_is_chat_only=is_chat_only_model(model_path),
    )
    preview = eligible_accounts(
        accounts,
        selection.channel,
        selection.clean_model,
        features=features,
        registry=registry,
        groups=groups,
    )
    if preview:
        return selection

    enabled = enabled_channels(registry)
    if selection.explicit or len(enabled) <= 1 or is_chat_only_model(model_path):
        raise _no_capable_account(model_path, selection.channel, "资格图为空")

    for preferred in ("build", "chat"):
        if preferred == selection.channel:
            continue
        candidate = next((spec for spec in enabled if spec.key == preferred), None)
        if candidate is None:
            continue
        alternate = eligible_accounts(
            accounts,
            preferred,
            selection.clean_model,
            features=features,
            registry=registry,
            groups=groups,
        )
        if alternate:
            pinned = select_channel(
                f"{candidate.prefix}/{selection.clean_model}",
                features,
                mounted_channels=mounted_channels,
                model_is_chat_only=False,
            )
            return replace(pinned, explicit=False, reason="candidate-preview")

    raise _no_capable_account(model_path, selection.channel, "chat/build 资格图均为空")


__all__ = [
    "AccountView",
    "EligibleAccount",
    "channel_affinity",
    "choose_channel",
    "effective_account_pools",
    "eligible_account",
    "eligible_accounts",
    "normalize_quota_pools",
    "resolve_account_group",
]
