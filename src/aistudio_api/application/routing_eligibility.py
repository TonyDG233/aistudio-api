"""Model -> surface / Free-paid lane / tier admission.

P2 rules (06 / 08-09 D-04):

* only ``chat`` / ``build`` surfaces exist; no channel registry or aliases;
* bare model names default to ``chat`` and never silently switch surface;
* a model is paid when discovery marks it Paid *or* a tier rule requires a
  paid tier; paid models use the paid instance lane and require Pro+;
* ``paid_model_rules`` -> ``accessModes`` -> paid/capability.  An empty rule
  must never be treated as "Ultra only".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from aistudio_api.domain.errors import SurfaceCapacityError, SurfaceUnavailableError
from aistudio_api.domain.model_access import (
    best_tier,
    is_paid_model,
    minimum_required_tier,
    normalize_model_key,
    surface_capability,
    tier_rank,
    tier_satisfies_required,
)

VALID_SURFACES: tuple[str, ...] = ("chat", "build")
_DROPPED_SURFACE_PREFIXES = frozenset({"pg", "playground", "official"})
_PAID_MIN_TIERS: tuple[str, ...] = ("Pro", "Ultra", "Ultra_20X")


@dataclass(frozen=True)
class ModelRoute:
    """Eligibility facts frozen before candidate selection."""

    raw_model: str
    model: str
    surface: str
    paid: bool
    required_tiers: tuple[str, ...]
    required_tier: str | None
    tier: str

    @property
    def lane(self) -> str:
        return "paid" if self.paid else "free"


def _enabled(enabled_surfaces: Iterable[str]) -> tuple[str, ...]:
    surfaces = tuple(
        str(surface).strip().lower()
        for surface in enabled_surfaces
        if str(surface or "").strip().lower() in VALID_SURFACES
    )
    return surfaces or ("chat",)


def parse_model_surface(
    model_path: str,
    *,
    requested_surface: str | None = None,
    enabled_surfaces: Iterable[str] = VALID_SURFACES,
) -> tuple[str, str]:
    """Return ``(surface, clean_model)`` or raise an explicit routing error.

    Only ``chat/`` and ``build/`` are valid prefixes (conditional prefix).  Old
    ``pg``/``playground``/``official`` aliases are rejected rather than silently
    re-routed to another surface.
    """
    text = str(model_path or "").strip()
    if text.startswith("models/"):
        text = text[len("models/") :]
    head, separator, tail = text.partition("/")
    surface: str
    if separator and head in VALID_SURFACES:
        surface = head
        text = tail.removeprefix("models/")
    elif separator and head in _DROPPED_SURFACE_PREFIXES:
        raise SurfaceUnavailableError(
            surface=requested_surface or "chat",
            message=f"旧渠道前缀 '{head}/' 已移除，只支持 chat/build。",
        )
    else:
        surface = (requested_surface or "chat").strip().lower()

    if requested_surface is not None:
        wanted = requested_surface.strip().lower()
        if wanted not in VALID_SURFACES:
            raise ValueError(f"未知 surface: {requested_surface}")
        if surface != wanted:
            raise ValueError(f"模型前缀 surface={surface} 与请求 surface={wanted} 冲突")
        surface = wanted

    clean = text.strip()
    if not clean:
        raise ValueError("模型名称不能为空")

    surfaces = _enabled(enabled_surfaces)
    if surface not in surfaces:
        raise SurfaceCapacityError(
            surface=surface,
            message=f"渠道 '{surface}' 没有已配置的实例，拒绝静默换面。",
        )
    return surface, clean


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip() for item in value if isinstance(item, str) and item.strip()
    ]


def _rule_matches(rule: Mapping[str, object], clean_model: str) -> bool:
    contains = [item.lower() for item in _str_list(rule.get("contains"))]
    prefixes = [item.lower() for item in _str_list(rule.get("prefixes"))]
    exact = [
        item.removeprefix("models/").lower() for item in _str_list(rule.get("exact"))
    ]
    return (
        (bool(exact) and clean_model in exact)
        or any(clean_model.startswith(prefix) for prefix in prefixes)
        or any(token in clean_model for token in contains)
    )


def _live_paid_truth(clean_model: str) -> bool | None:
    """Live/snapshot Paid truth for a model, or ``None`` for fallback/unknown."""
    from aistudio_api.infrastructure.gateway.model_discovery import (
        discovery_entry_paid_truth,
        model_discovery,
    )

    return discovery_entry_paid_truth(
        model_discovery.get_cached_model_info(clean_model)
    )


def _access_modes(entry: Mapping[str, object] | None) -> set[int]:
    """Integer ``accessModes`` bits from a cached discovery entry.

    ``bool`` is a subclass of ``int`` in Python, but ``True``/``False`` are not
    access-mode bits; they must never be coerced into ``1``/``0``.
    """
    if entry is None:
        return set()
    raw_modes = entry.get("accessModes")
    if not isinstance(raw_modes, list):
        return set()
    return {
        int(mode)
        for mode in raw_modes
        if isinstance(mode, int) and not isinstance(mode, bool)
    }


def resolve_required_tiers(
    model_path: str,
    *,
    rules: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Resolve allowed tiers with live discovery truth ahead of static rules.

    Priority order:

    1. Live Free (``paid is False`` without Paid ``accessModes``) -> no
       restriction; a static ``image`` style rule must not override it.
    2. Live/cached Ultra-only (``accessModes`` has 4 without 3) -> Ultra only;
       the broad ``pro_required`` ``gemini-4`` substring rule must not downgrade
       an Ultra-exclusive model such as ``gemini-4-carbon``.
    3. ``paid_model_rules`` static substring/prefix/exact matches.
    4. Remaining cached ``accessModes`` / Paid marker / capability fallback.
    """
    clean = normalize_model_key(model_path)
    if not clean:
        return ()

    live_truth = _live_paid_truth(clean)
    if live_truth is False:
        return ()

    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    info = model_discovery.get_cached_model_info(clean)
    modes = _access_modes(info)
    if 4 in modes and 3 not in modes:
        return ("Ultra", "Ultra_20X")

    if rules is None:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_paid_model_rules,
        )

        rules = get_configured_paid_model_rules()

    for rule in rules.values():
        if not isinstance(rule, Mapping) or not _rule_matches(rule, clean):
            continue
        raw_tiers = rule.get("required_tiers")
        tiers = tuple(
            str(tier).strip()
            for tier in (raw_tiers if isinstance(raw_tiers, list) else [])
            if str(tier or "").strip()
        )
        if tiers:
            return tiers

    if info is not None:
        if 3 in modes:
            return _PAID_MIN_TIERS
        if bool(info.get("paid")):
            return _PAID_MIN_TIERS

    if is_paid_model(clean):
        return _PAID_MIN_TIERS
    return ()


def resolve_model_route(
    model_path: str,
    *,
    requested_surface: str | None = None,
    enabled_surfaces: Iterable[str] = VALID_SURFACES,
    rules: Mapping[str, object] | None = None,
) -> ModelRoute:
    """Freeze surface, lane and tier requirement for one request."""
    surfaces = tuple(enabled_surfaces)
    surface, clean = parse_model_surface(
        model_path,
        requested_surface=requested_surface,
        enabled_surfaces=surfaces,
    )
    # [ROUTING-RULE: PAID_CHAT_ONLY]
    # 因当前 Build 渠道暂无法正常使用 Paid 模型（非正价 Pro/Ultra 不可用
    # 且正价额度极少），此处硬编码限制 Paid 模型仅允许走 chat 渠道。
    # 若未来上游放开或路由策略调整，在此处修改解绑。
    capabilities = surface_capability(clean, surfaces)
    if surface not in capabilities:
        raise SurfaceCapacityError(
            surface=surface,
            message=(
                "[ROUTING-RULE: PAID_CHAT_ONLY] Paid 模型当前仅允许 chat 渠道；"
                "Build 渠道暂不可用，若上游放开请在此处解绑。"
            ),
        )
    required = resolve_required_tiers(clean, rules=rules)
    live_truth = _live_paid_truth(clean)
    if live_truth is False:
        # Live discovery is authoritative: never route a real Free model into
        # the paid lane because of a stale seed/static rule.
        paid = False
        required = ()
    elif live_truth is True:
        paid = True
        if not required:
            required = _PAID_MIN_TIERS
    else:
        paid = is_paid_model(clean) or any(tier_rank(tier) > 0 for tier in required)
        if paid and not required:
            required = _PAID_MIN_TIERS
    minimum = minimum_required_tier(required)
    return ModelRoute(
        raw_model=str(model_path),
        model=clean,
        surface=surface,
        paid=paid,
        required_tiers=required,
        required_tier=minimum,
        tier=minimum or "Free",
    )


def tier_allows(account_tier: object, route: ModelRoute) -> bool:
    return tier_satisfies_required(account_tier, route.required_tiers)


def best_current_tier(tiers: Iterable[object]) -> str:
    return best_tier(tiers)


__all__ = [
    "VALID_SURFACES",
    "ModelRoute",
    "best_current_tier",
    "parse_model_surface",
    "resolve_model_route",
    "resolve_required_tiers",
    "tier_allows",
]
