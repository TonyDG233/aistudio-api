"""Process-local model access metadata (Paid markers and eligibility helpers).

This module is the single source of truth for "is this model Paid/chat-only".
Discovery and snapshots update it; routing/catalog code only consumes it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from threading import RLock

_CHANNEL_PREFIXES = {"pg", "chat", "playground", "build", "official"}

# Known Paid models. Discovery/snapshots may override per model; these keep
# routing safe before the first ListModels request completes.  Every live
# Paid model from docs/dev/evidence/B6/model_discovery_fix.json is seeded here.
_DEFAULT_PAID_MODELS = {
    "antigravity-preview-09-2026",
    "deep-research-preview-04-2026",
    "deep-research-max-preview-04-2026",
    "gemini-4-argon",
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-nano-banana-2.1",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
    "lyria-3.5",
    "lyria-3-pro-preview",
    "lyria-3-clip-preview",
    "gemini-omni-1.1-flash",
    "argon",
    "argon-2-preview",
    "veo-2.0-generate-001",
    "veo-3.1-generate-preview",
}

PAID_TIERS = frozenset({"pro", "ultra", "plus", "paid", "ultra_20x"})

# Instance lanes are derived from account tier, never from the model name.
_TIER_RANK = {
    "free": 0,
    "plus": 1,
    "pro": 2,
    "ultra": 3,
    "ultra_20x": 4,
    "paid": 2,
}
_INSTANCE_LANE_FREE = "free"
_INSTANCE_LANE_PAID = "paid"

_PAID_MODELS: set[str] = set(_DEFAULT_PAID_MODELS)
_LOCK = RLock()


def normalize_model_key(value: object) -> str:
    text = str(value or "").strip().removeprefix("models/").lower()
    head, separator, tail = text.partition("/")
    if separator and tail and head in _CHANNEL_PREFIXES:
        return tail
    return text


def set_paid_models(models: Iterable[Mapping[str, object]]) -> None:
    """Update Paid status for every model present in a discovery snapshot.

    The built-in defaults are only a cold-start seed. Live discovery is
    authoritative for models it contains: a model marked free is removed from
    the Paid set, and a newly Paid model is added. Models missing from a
    partial snapshot keep their previous status (conservative: a known Paid
    model never silently becomes free because it was absent).
    """
    for item in models:
        key = normalize_model_key(item.get("id") or item.get("name"))
        if not key:
            continue
        with _LOCK:
            if item.get("paid") is True:
                _PAID_MODELS.add(key)
            else:
                _PAID_MODELS.discard(key)


def clear_paid_models() -> None:
    with _LOCK:
        _PAID_MODELS.clear()
        _PAID_MODELS.update(_DEFAULT_PAID_MODELS)


def is_paid_model(model_path: object) -> bool:
    key = normalize_model_key(model_path)
    if not key:
        return False
    with _LOCK:
        return key in _PAID_MODELS


def is_chat_only_model(model_path: object) -> bool:
    """Paid models may only run on the ``chat`` surface."""
    # [ROUTING-RULE: PAID_CHAT_ONLY]
    # 因当前 Build 渠道暂无法正常使用 Paid 模型（非正价 Pro/Ultra 不可用
    # 且正价额度极少），此处硬编码限制 Paid 模型仅允许走 chat 渠道。
    # 若未来上游放开或路由策略调整，在此处修改解绑。
    return is_paid_model(model_path)


def model_requires_paid_account(model_path: object) -> bool:
    """True only when the model is marked Paid by discovery."""
    return is_paid_model(model_path)


def model_account_requirement(model_path: object) -> str:
    return "paid" if model_requires_paid_account(model_path) else "any"


def account_is_paid(tier: object) -> bool:
    return str(tier or "").strip().lower() in PAID_TIERS


def tier_rank(tier: object) -> int:
    """Numeric rank for tier comparisons (unknown tiers behave like Free)."""
    key = str(tier or "").strip().lower().replace(" ", "_").replace("-", "_")
    return _TIER_RANK.get(key, 0)


def instance_lane_for_tier(tier: object) -> str:
    """Free accounts use the Free lane; paid accounts use the paid lane."""
    return _INSTANCE_LANE_PAID if account_is_paid(tier) else _INSTANCE_LANE_FREE


def tier_satisfies_min(account_tier: object, min_tier: object) -> bool:
    """True when an account may occupy an instance whose spec requires min_tier."""
    if instance_lane_for_tier(account_tier) != instance_lane_for_tier(min_tier):
        return False
    return tier_rank(account_tier) >= tier_rank(min_tier)


def minimum_required_tier(required_tiers: Iterable[object]) -> str | None:
    """Return the canonical label of the lowest tier allowed by a requirement."""
    labels = [
        str(tier).strip()
        for tier in required_tiers
        if str(tier or "").strip()
    ]
    if not labels:
        return None
    return min(labels, key=tier_rank)


def tier_satisfies_required(account_tier: object, required_tiers: Iterable[object]) -> bool:
    """True when the account tier reaches at least one allowed requirement tier.

    ``paid_model_rules`` / ``accessModes`` list the allowed tiers; the lowest
    entry is the minimum capability (e.g. Pro allows Pro/Ultra/Ultra_20X but not
    Plus). An empty requirement means no tier restriction.
    """
    minimum = minimum_required_tier(required_tiers)
    if minimum is None:
        return True
    return tier_rank(account_tier) >= tier_rank(minimum)


def best_tier(tiers: Iterable[object]) -> str:
    """Return the highest-ranked tier label, defaulting to Free."""
    labels = [str(tier).strip() for tier in tiers if str(tier or "").strip()]
    if not labels:
        return "Free"
    return max(labels, key=tier_rank)


def surface_capability(
    model_path: object,
    enabled_surfaces: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """System-derived capability: chat-only models are restricted to chat."""
    enabled = tuple(
        surface for surface in enabled_surfaces if surface in ("chat", "build")
    )
    if is_chat_only_model(model_path):
        return ("chat",) if "chat" in enabled else ()
    return enabled


__all__ = [
    "PAID_TIERS",
    "account_is_paid",
    "best_tier",
    "clear_paid_models",
    "instance_lane_for_tier",
    "is_chat_only_model",
    "is_paid_model",
    "minimum_required_tier",
    "model_account_requirement",
    "model_requires_paid_account",
    "normalize_model_key",
    "set_paid_models",
    "surface_capability",
    "tier_rank",
    "tier_satisfies_min",
    "tier_satisfies_required",
]
