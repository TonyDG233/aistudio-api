"""Surface orchestration (WP5): account groups, surface policy and page quota.

This module is the single source of truth for "which surface (``chat`` or
``build``) a request runs on". It is intentionally side-effect free (no browser
or client calls) so both the request path and the management panel can share it.

Resolution priority (see :func:`resolve_surface`):

1. ``AISTUDIO_SURFACE_OVERRIDE`` env var (``chat``/``build``) - explicit
   testing/debug knob, always wins.
2. ``features.route_by_tier`` off - legacy behavior: ``build`` when
   ``features.build_surface`` is enabled and the account tier is ``free``,
   otherwise ``chat``.
3. ``features.route_by_tier`` on - group policy (``features.groups`` with
   ``match_tier`` / ``match_emails_regex``, ``allowed_surfaces``,
   ``max_page_instances``); accounts that match no explicit group fall back to
   their tier default group (see :data:`DEFAULT_ALLOWED_BY_TIER`).

Policy denials raise :class:`SurfacePolicyError` (never a silent surface or
account switch); every decision is logged with a grep-able ``[surface]`` line.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("application.surface")

# 未显式分组的账号按 tier 落入的自动默认组策略（PLAN WP5）。
DEFAULT_ALLOWED_BY_TIER: dict[str, list[str]] = {
    "free": ["build"],
    "pro": ["chat"],
    "ultra": ["chat"],
    "plus": ["chat"],
}
# 未知/缺省 tier 的自动默认组策略。
UNKNOWN_TIER_ALLOWED: list[str] = ["chat"]
# 页面实例配额缺省值（组未显式给出 max_page_instances 时）。
MAX_PAGE_INSTANCES_DEFAULT = 2

SURFACE_OVERRIDE_ENV = "AISTUDIO_SURFACE_OVERRIDE"
_VALID_SURFACES = ("chat", "build")

# 面策略错误 code -> HTTP 状态；未列出者统一 403（策略拒绝语义）。
_HTTP_STATUS_BY_CODE = {
    "surface_quota_exceeded": 429,
    "surface_switch_exhausted": 503,
    "surface_instance_not_ready": 503,
    "surface_no_capable_account": 403,
    "surface_channel_disabled": 403,
}
_HTTP_STATUS_DEFAULT = 403
_STATUS_STRING_BY_HTTP = {
    400: "INVALID_ARGUMENT",
    403: "PERMISSION_DENIED",
    429: "RESOURCE_EXHAUSTED",
    503: "UNAVAILABLE",
}

_PRO_MODEL_RE = re.compile(r"-pro($|-)")
_EMAIL_RE_CACHE: dict[str, re.Pattern[str] | None] = {}


class SurfacePolicyError(Exception):
    """Explicit surface-policy denial (group disabled / model guard / quota).

    Carries the machine code (``surface_group_disabled`` /
    ``surface_model_guard`` / ``surface_quota_exceeded``), a human message that
    states the group policy, and an ``advice`` remediation hint. Never carries a
    silent fallback: callers must surface this as an error.
    """

    def __init__(
        self,
        code: str,
        *,
        message: str,
        advice: str,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.advice = advice
        self.http_status = (
            int(http_status)
            if http_status is not None
            else _HTTP_STATUS_BY_CODE.get(code, _HTTP_STATUS_DEFAULT)
        )

    @property
    def status(self) -> str:
        """Gemini-style status string for the HTTP status."""
        return _STATUS_STRING_BY_HTTP.get(self.http_status, "FAILED_PRECONDITION")

    @property
    def status_code(self) -> int:
        """Integer HTTP status alias consumed by the gateway client's error mapping."""
        return self.http_status

    def to_error_body(self) -> dict[str, object]:
        """Build a Gemini-shaped error body (``error.code/message/status/advice``)."""
        return {
            "error": {
                "code": self.http_status,
                "message": self.message,
                "status": self.status,
                "advice": self.advice,
            }
        }


@dataclass(frozen=True)
class SurfaceDecision:
    """Immutable outcome of one surface resolution."""

    surface: str
    group_name: str | None
    group_source: str  # "explicit" | "default-tier" | "override"
    allowed: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class GroupPolicy:
    """Constraint layer for one request/account (WP5 phase 2).

    A group only narrows the candidate pool: account set (tier/email match),
    allowed surfaces, page-instance budget and sort weight. It never widens the
    system-derived model/channel capability.
    """

    name: str | None
    source: str  # "explicit" | "default-tier" | "implicit-global"
    allowed_surfaces: tuple[str, ...]
    max_page_instances: int
    weight: float
    enabled: bool
    tier_key: str | None = None


def _normalize_tier(tier: object) -> str | None:
    if tier is None:
        return None
    text = str(tier).strip().lower()
    return text or None


def _as_str_list(value: object) -> list[str]:
    """Coerce a YAML field into a list of non-empty strings."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _compiled_email_re(pattern: str) -> re.Pattern[str] | None:
    if pattern not in _EMAIL_RE_CACHE:
        try:
            _EMAIL_RE_CACHE[pattern] = re.compile(pattern)
        except re.error:
            _EMAIL_RE_CACHE[pattern] = None
    return _EMAIL_RE_CACHE[pattern]


def match_group(tier: object, email: object, groups: object) -> dict | None:
    """Return the first group whose ``match_emails_regex`` OR ``match_tier`` hits.

    Within a single group the two match fields are an **OR** relationship; across
    the list, the first matching group wins (list order is authoritative).
    """
    tier_norm = _normalize_tier(tier)
    email_norm = str(email or "").strip()
    if not isinstance(groups, (list, tuple)):
        return None
    for group in groups:
        if not isinstance(group, dict):
            continue
        patterns = _as_str_list(group.get("match_emails_regex"))
        if patterns and email_norm:
            for pattern in patterns:
                compiled = _compiled_email_re(pattern)
                if compiled is not None and compiled.search(email_norm):
                    return group
        if tier_norm is not None:
            tiers = {t.lower() for t in _as_str_list(group.get("match_tier"))}
            if tier_norm in tiers:
                return group
    return None


def is_chat_only_model(model_path: str) -> bool:
    """True for argon / Pro-family models that may only run on the ``chat`` surface.

    Covers ``models/argon`` (and any name containing ``argon``) plus Pro-name
    models matched by ``-pro($|-)`` (e.g. ``gemini-2.5-pro``,
    ``gemini-3.1-pro-preview``). Flash/other names return False.
    """
    name = str(model_path or "").removeprefix("models/").lower()
    if not name:
        return False
    if "argon" in name:
        return True
    return bool(_PRO_MODEL_RE.search(name))


def _surface_available(surface: str, build_enabled: bool) -> bool:
    if surface == "chat":
        return True
    if surface == "build":
        return build_enabled
    return False


def _group_max_instances(group: dict) -> int:
    try:
        value = int(group.get("max_page_instances", MAX_PAGE_INSTANCES_DEFAULT))
    except (TypeError, ValueError):
        value = MAX_PAGE_INSTANCES_DEFAULT
    return min(16, max(0, value))


def _group_weight(group: dict) -> float:
    try:
        value = float(group.get("weight", 1.0))
    except (TypeError, ValueError):
        value = 1.0
    return min(100.0, max(0.1, value))


def select_group(
    tier: object,
    email: object,
    groups: object,
    *,
    route_by_tier: bool,
    build_enabled: bool,
) -> GroupPolicy:
    """Resolve the constraint group for one account.

    - ``route_by_tier`` off -> implicit global group (all accounts; enabled
      surfaces only). This reproduces the zero-config candidate pool.
    - ``route_by_tier`` on -> first enabled, non-empty explicit group whose
      tier/email matches; otherwise the tier default group.
    - Disabled or empty groups are treated as non-existent and skipped.
    """
    tier_norm = _normalize_tier(tier)
    enabled_surfaces = tuple(
        surface
        for surface in _VALID_SURFACES
        if _surface_available(surface, build_enabled)
    )
    if not route_by_tier:
        return GroupPolicy(
            name=None,
            source="implicit-global",
            allowed_surfaces=enabled_surfaces,
            max_page_instances=MAX_PAGE_INSTANCES_DEFAULT,
            weight=1.0,
            enabled=True,
            tier_key=None,
        )

    if isinstance(groups, (list, tuple)):
        for group in groups:
            if not isinstance(group, dict):
                continue
            if not bool(group.get("enabled", True)):
                continue
            if match_group(tier_norm, email, [group]) is None:
                continue
            allowed = tuple(
                surface
                for surface in _as_str_list(group.get("allowed_surfaces"))
                if surface in _VALID_SURFACES
                and _surface_available(surface, build_enabled)
            )
            if not allowed:
                # 空组/停用组视同不存在：继续尝试后续分组或 tier 自动组。
                continue
            return GroupPolicy(
                name=str(group.get("name") or "unnamed"),
                source="explicit",
                allowed_surfaces=allowed,
                max_page_instances=_group_max_instances(group),
                weight=_group_weight(group),
                enabled=True,
                tier_key=None,
            )

    allowed_default = tuple(
        surface
        for surface in DEFAULT_ALLOWED_BY_TIER.get(
            tier_norm or "", UNKNOWN_TIER_ALLOWED
        )
        if _surface_available(surface, build_enabled)
    )
    return GroupPolicy(
        name=None,
        source="default-tier",
        allowed_surfaces=allowed_default,
        max_page_instances=MAX_PAGE_INSTANCES_DEFAULT,
        weight=1.0,
        enabled=bool(allowed_default),
        tier_key=tier_norm,
    )


def group_identity(policy: GroupPolicy) -> tuple[str | None, str, str | None]:
    """Stable identity used to compare per-account group membership."""
    return (policy.name, policy.source, policy.tier_key)


def account_matches_group(
    policy: GroupPolicy,
    tier: object,
    email: object,
    groups: object,
) -> bool:
    """Whether an account belongs to the request's effective group."""
    if policy.source == "implicit-global":
        return True
    if policy.source == "default-tier":
        return _normalize_tier(tier) == policy.tier_key
    if policy.source == "explicit":
        if not isinstance(groups, (list, tuple)):
            return False
        for group in groups:
            if not isinstance(group, dict):
                continue
            if not bool(group.get("enabled", True)):
                continue
            name = str(group.get("name") or "unnamed")
            if name != policy.name:
                continue
            return match_group(_normalize_tier(tier), email, [group]) is not None
        return False
    return False


def surface_capability(
    model_path: str,
    enabled_surfaces: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """System-derived capability: chat-only models are restricted to chat."""
    enabled = tuple(
        surface for surface in enabled_surfaces if surface in _VALID_SURFACES
    )
    if is_chat_only_model(model_path):
        return ("chat",) if "chat" in enabled else ()
    return enabled


def intersect_surfaces(
    capability: tuple[str, ...],
    policy: GroupPolicy,
    account_surfaces: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Apply group and account-level narrowing; never widens capability."""
    allowed = set(capability) & set(policy.allowed_surfaces)
    if account_surfaces is not None:
        allowed &= set(account_surfaces)
    return tuple(surface for surface in capability if surface in allowed)


def _log_decision(
    model_path: str,
    tier_norm: str | None,
    email: object,
    decision: SurfaceDecision,
) -> None:
    logger.info(
        "[surface] model=%s tier=%s email=%s group=%s source=%s allowed=%s -> %s (%s)",
        model_path,
        tier_norm or "unknown",
        email or "-",
        decision.group_name or "-",
        decision.group_source,
        ",".join(decision.allowed) or "-",
        decision.surface,
        decision.reason,
    )


def _deny(code: str, *, message: str, advice: str) -> SurfacePolicyError:
    logger.warning(
        "[surface] 策略拒绝 code=%s: %s | 处置建议: %s", code, message, advice
    )
    return SurfacePolicyError(code, message=message, advice=advice)


def resolve_surface(
    model_path: str,
    tier: object,
    email: object,
    features: dict[str, object] | None,
) -> SurfaceDecision:
    """Resolve the surface for one request per the WP5 policy.

    Raises :class:`SurfacePolicyError` when the resolved group has no usable
    surface or when a chat-only model is routed to a group without ``chat``.
    """
    features = features or {}
    tier_norm = _normalize_tier(tier)

    override = (os.getenv(SURFACE_OVERRIDE_ENV) or "").strip().lower()
    if override in _VALID_SURFACES:
        decision = SurfaceDecision(
            surface=override,
            group_name=None,
            group_source="override",
            allowed=(override,),
            reason=f"环境变量 {SURFACE_OVERRIDE_ENV}={override} 显式覆盖",
        )
        _log_decision(model_path, tier_norm, email, decision)
        return decision

    build_enabled = bool(features.get("build_surface"))
    route_by_tier = bool(features.get("route_by_tier"))
    groups = features.get("groups") or []

    if not route_by_tier:
        surface = "build" if (build_enabled and tier_norm == "free") else "chat"
        default_allowed = ("build",) if build_enabled else ("chat",)
        if is_chat_only_model(model_path):
            # chat-only 模型（argon/Pro 系）在旧路由下也强制 chat，不派发到 build。
            surface = "chat"
            default_allowed = ("chat",)
        decision = SurfaceDecision(
            surface=surface,
            group_name=None,
            group_source="default-tier",
            allowed=default_allowed,
            reason=(
                "route_by_tier=false 沿用旧行为: "
                f"build_surface={build_enabled}, tier={tier_norm or 'unknown'} "
                f"-> {surface}"
            ),
        )
        _log_decision(model_path, tier_norm, email, decision)
        return decision

    group = match_group(tier_norm, email, groups)
    if group is not None:
        group_name: str | None = str(group.get("name") or "unnamed")
        group_source = "explicit"
        raw_allowed = _as_str_list(group.get("allowed_surfaces"))
    else:
        group_name = None
        group_source = "default-tier"
        raw_allowed = list(
            DEFAULT_ALLOWED_BY_TIER.get(tier_norm or "", UNKNOWN_TIER_ALLOWED)
        )

    allowed: list[str] = []
    for raw_surface in raw_allowed:
        surface_norm = raw_surface.strip().lower()
        if surface_norm not in _VALID_SURFACES:
            continue
        if not _surface_available(surface_norm, build_enabled):
            continue
        if surface_norm not in allowed:
            allowed.append(surface_norm)

    if not allowed:
        raise _deny(
            "surface_group_disabled",
            message=(
                f"分组 '{group_name or tier_norm or 'unknown'}' 无可用面 "
                f"(allowed_surfaces={raw_allowed or []}, build_surface={build_enabled})。"
            ),
            advice=(
                "请在该分组 allowed_surfaces 中至少保留 chat，或开启 "
                "features.build_surface 以启用 build 面；本次请求不换面、不换账号。"
            ),
        )

    if is_chat_only_model(model_path) and "chat" not in allowed:
        raise _deny(
            "surface_model_guard",
            message=(
                f"模型 '{model_path}' 属 argon/Pro 系，仅可在 chat 面使用，"
                f"但分组 '{group_name or tier_norm or 'unknown'}' "
                f"allowed={allowed} 不含 chat。"
            ),
            advice=(
                "请在分组 allowed_surfaces 中加入 chat，或改用该分组支持的"
                "非 chat-only 模型；本次请求不静默换面、不换账号。"
            ),
        )

    if is_chat_only_model(model_path):
        # chat-only 模型强制 chat（上方守卫已保证 chat ∈ allowed，否则已拒绝）。
        surface = "chat"
    elif len(allowed) == 1:
        surface = allowed[0]
    elif "build" in allowed:
        # 同凭证多面许可：非 chat-only 模型优先 build（把 chat 配额留给 Pro/argon 系）。
        surface = "build"
    else:
        surface = "chat"

    decision = SurfaceDecision(
        surface=surface,
        group_name=group_name,
        group_source=group_source,
        allowed=tuple(allowed),
        reason=(
            f"route_by_tier=true, group_source={group_source}, "
            f"allowed={allowed}, tier={tier_norm or 'unknown'} -> {surface}"
        ),
    )
    _log_decision(model_path, tier_norm, email, decision)
    return decision


def check_instance_quota(
    group_name: str,
    max_instances: int | None,
    live_instances: int,
    surface: str,
    need_new: bool,
) -> None:
    """Raise :class:`SurfacePolicyError` when a new page instance would exceed quota.

    语义（按账号校验）：仅当本次请求需要新建页面实例（``need_new=True``）时校验；
    口径 = 该账号当前存活页面实例数（chat 页 + build 页各计 1）≥ 配额即显式拒绝。
    配额 0 = 禁止新建（如禁止为该账号创建 build 页）。不排队、不静默换面。
    """
    if not need_new:
        return
    quota = MAX_PAGE_INSTANCES_DEFAULT if max_instances is None else int(max_instances)
    if live_instances >= quota:
        raise _deny(
            "surface_quota_exceeded",
            message=(
                f"页面实例配额已满（按账号校验）: 组='{group_name}', "
                f"配额={quota}, 当前存活实例={live_instances}"
                f"（chat/build 页各计 1）；需要新建 {surface} 页被拒。"
            ),
            advice=(
                "请调高该组 max_page_instances、关闭闲置实例，"
                "或将账号移入其它分组；本次请求不排队、不静默换面。"
            ),
        )


def get_configured_groups(
    config_path: str | os.PathLike[str] | None = None,
) -> list[dict]:
    """Read ``features.groups`` from config.yaml (hot-reload friendly).

    Read fresh on each call so a PUT /config/yaml takes effect immediately.
    Malformed or missing config degrades to an empty list (no groups).
    """
    try:
        import yaml

        from aistudio_api.config import resolve_config_file

        path = resolve_config_file(config_path)
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return []
        features = data.get("features")
        if not isinstance(features, dict):
            return []
        raw_groups = features.get("groups")
        if not isinstance(raw_groups, list):
            return []
        return [g for g in raw_groups if isinstance(g, dict)]
    except Exception:
        return []


__all__ = [
    "DEFAULT_ALLOWED_BY_TIER",
    "MAX_PAGE_INSTANCES_DEFAULT",
    "SURFACE_OVERRIDE_ENV",
    "GroupPolicy",
    "SurfaceDecision",
    "SurfacePolicyError",
    "account_matches_group",
    "check_instance_quota",
    "get_configured_groups",
    "group_identity",
    "intersect_surfaces",
    "is_chat_only_model",
    "match_group",
    "resolve_surface",
    "select_group",
    "surface_capability",
]
