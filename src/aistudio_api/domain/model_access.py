"""Process-local model access metadata (Paid markers and eligibility helpers).

This module is the single source of truth for "is this model Paid/chat-only".
Discovery and snapshots update it; routing/catalog code only consumes it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import RLock

_CHANNEL_PREFIXES = {"chat", "build"}

# Known Paid models. Discovery/snapshots may override per model; these keep
# routing safe before the first ListModels request completes.  Every live
# Paid model from docs/dev/evidence/B6/model_discovery_fix.json is seeded here.
_DEFAULT_PAID_MODELS = {
    "antigravity-preview-09-2026",
    "deep-research-preview-04-2026",
    "deep-research-max-preview-04-2026",
    "gemini-4-argon",
    "gemini-4-carbon",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
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

_TIER_CANONICAL = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "ultra": "Ultra",
    "ultra_20x": "Ultra_20X",
    "ultra20x": "Ultra_20X",
}

# Authoritative observed ``GetAiStudioBenefitTier`` integer code -> canonical
# tier label map.  Both the cookie parser and the account refresher consume
# this single table; do not re-declare local copies.
BENEFIT_TIER_BY_CODE: dict[int, str] = {
    0: "Free",
    1: "Pro",
    2: "Ultra",
    3: "Plus",
}

# Canonical tier -> ``X-AIStudio-G1-Tier`` request header value. Free has no
# entry on purpose: upstream expects the header to be absent for Free accounts.
BENEFIT_TIER_TO_HEADER: dict[str, str] = {
    "Pro": "TIER1",
    "Ultra": "TIER2",
    "Ultra_20X": "TIER2",
    "Plus": "TIER0",
}

_PAID_MODELS: set[str] = set(_DEFAULT_PAID_MODELS)
_LOCK = RLock()


class TierSelectorError(ValueError):
    """An ``instances`` tier selector is missing, mixed or contradictory."""


@dataclass(frozen=True)
class TierSelector:
    """Normalized closed tier interval for one instance slot.

    ``only_tier``/``min_tier``/``max_tier`` are canonical labels from the input;
    the effective interval is always ``min_tier..max_tier`` (inclusive) inside
    one lane.  ``only_tier: Ultra`` expands to ``Ultra..Ultra_20X`` so the
    normalized Ultra family is accepted while ``only_tier: Pro`` stays exactly
    Pro and never reaches Ultra.
    """

    lane: str
    only_tier: str | None
    min_tier: str
    max_tier: str


def canonical_tier(value: object) -> str | None:
    """Return the canonical tier label, or ``None`` for an unknown/empty value."""
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return _TIER_CANONICAL.get(text)


def normalize_tier(value: object) -> str:
    """Return the canonical tier label, treating unknown/empty values as Free."""
    return canonical_tier(value) or "Free"


def benefit_tier_from_code(code: int, *, unknown: str = "Free") -> str:
    """Map an observed benefit-tier integer code to a canonical tier label.

    ``unknown`` keeps the two call-site policies intact: the RPC parser treats
    unrecognised codes as Free, while the refresher surfaces ``unknown-<code>``
    for non-zero codes it has never seen.
    """
    return BENEFIT_TIER_BY_CODE.get(code, unknown)


def tier_header_value(value: object) -> str | None:
    """Return the ``X-AIStudio-G1-Tier`` value for a tier; Free/unknown -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return BENEFIT_TIER_TO_HEADER.get(normalize_tier(text))


def _selector_label(name: str, value: object) -> str | None:
    """Validate one raw selector value and return its canonical label.

    A non-string or unknown tier name is a hard configuration error: silently
    treating a typo (or a number/list) as "not provided" previously let a slot
    drift to an unintended lane.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TierSelectorError(
            f"{name} 必须为字符串，当前类型为 {type(value).__name__}"
        )
    if not value.strip():
        return None
    label = canonical_tier(value)
    if label is None:
        raise TierSelectorError(
            f"{name}={value!r} 不是已知 tier（Free/Plus/Pro/Ultra/Ultra_20X）"
        )
    return label


def resolve_tier_selector(
    *,
    only_tier: object = None,
    min_tier: object = None,
    max_tier: object = None,
) -> TierSelector:
    """Normalize one ``instances`` tier selector into a closed lane interval.

    Raises :class:`TierSelectorError` when no selector is provided, a selector is
    not a string or names an unknown tier, ``only_tier`` is combined with
    min/max, the interval is inverted, or Free/paid lanes would be mixed.
    """
    only = _selector_label("only_tier", only_tier)
    lower = _selector_label("min_tier", min_tier)
    upper = _selector_label("max_tier", max_tier)

    if only is None and lower is None and upper is None:
        raise TierSelectorError(
            "instances 配置项至少需要 only_tier/min_tier/max_tier 之一"
        )

    if only is not None:
        if only == "Free":
            implicit_lower, implicit_upper = "Free", "Free"
        elif only == "Ultra":
            implicit_lower, implicit_upper = "Ultra", "Ultra_20X"
        else:
            implicit_lower = implicit_upper = only
        # The normalized form may spell out only+min+max for the same interval
        # (e.g. Free -> Free..Free).  Any other min/max alongside only_tier is a
        # contradictory selector and must fail loudly.
        if (lower is not None and lower != implicit_lower) or (
            upper is not None and upper != implicit_upper
        ):
            raise TierSelectorError("only_tier 不能与 min_tier/max_tier 同时配置")
        lane = "free" if only == "Free" else "paid"
        return TierSelector(lane, only, implicit_lower, implicit_upper)

    if lower is None and upper is not None:
        if upper == "Free":
            return TierSelector("free", None, "Free", "Free")
        if tier_rank(upper) < tier_rank("Pro"):
            raise TierSelectorError(
                "max_tier 单独配置时不能低于 Pro（Plus 上限无法满足付费准入），"
                "如确需 Plus 槽位请显式使用 only_tier: Plus"
            )
        return TierSelector("paid", None, "Pro", upper)

    if lower is not None and upper is None:
        if lower == "Free":
            return TierSelector("free", None, "Free", "Free")
        return TierSelector("paid", None, lower, "Ultra_20X")

    assert lower is not None and upper is not None
    if lower == "Free" or upper == "Free":
        if lower == "Free" and upper == "Free":
            return TierSelector("free", None, "Free", "Free")
        raise TierSelectorError("Free/Paid 实例池不能混合，Free 实例只能是 Free")
    if tier_rank(lower) > tier_rank(upper):
        raise TierSelectorError(f"min_tier={lower} 高于 max_tier={upper}，闭区间非法")
    if tier_rank(lower) < tier_rank("Plus"):
        raise TierSelectorError("Paid 实例的 min_tier 不能低于 Plus")
    if tier_rank(upper) < tier_rank("Pro"):
        raise TierSelectorError(
            "Paid 实例的 tier 区间上界低于 Pro，无法满足付费准入；"
            "如确需 Plus 槽位请显式使用 only_tier: Plus"
        )
    return TierSelector("paid", None, lower, upper)


def tier_matches_selector(account_tier: object, selector: TierSelector) -> bool:
    """True when an account tier fits a normalized instance interval/lane."""
    if instance_lane_for_tier(account_tier) != selector.lane:
        return False
    rank = tier_rank(account_tier)
    return tier_rank(selector.min_tier) <= rank <= tier_rank(selector.max_tier)


def tier_matches_spec(
    account_tier: object,
    *,
    only_tier: object = None,
    min_tier: object = None,
    max_tier: object = None,
) -> bool:
    """Validate and match one raw ``instances`` selector against an account tier."""
    selector = resolve_tier_selector(
        only_tier=only_tier, min_tier=min_tier, max_tier=max_tier
    )
    return tier_matches_selector(account_tier, selector)


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
    labels = [str(tier).strip() for tier in required_tiers if str(tier or "").strip()]
    if not labels:
        return None
    return min(labels, key=tier_rank)


def tier_satisfies_required(
    account_tier: object, required_tiers: Iterable[object]
) -> bool:
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
    "BENEFIT_TIER_BY_CODE",
    "BENEFIT_TIER_TO_HEADER",
    "PAID_TIERS",
    "TierSelector",
    "TierSelectorError",
    "account_is_paid",
    "benefit_tier_from_code",
    "best_tier",
    "canonical_tier",
    "clear_paid_models",
    "instance_lane_for_tier",
    "is_chat_only_model",
    "is_paid_model",
    "minimum_required_tier",
    "model_account_requirement",
    "model_requires_paid_account",
    "normalize_model_key",
    "normalize_tier",
    "resolve_tier_selector",
    "set_paid_models",
    "surface_capability",
    "tier_header_value",
    "tier_matches_selector",
    "tier_matches_spec",
    "tier_rank",
    "tier_satisfies_min",
    "tier_satisfies_required",
]
