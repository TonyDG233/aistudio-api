"""Channel registry (WP5 phase 2): aliases, prefix parsing and selection.

Channels are fixed per request (see BOARD ``docs/dev/evidence/wp5b-20261019``):
``chat`` (aliases ``pg``/``playground``), ``build`` and ``official``. The
registry is side-effect free so the request path and the management panel share
the same view of which channels are enabled and implemented.

Selection never silently switches channel: deny paths raise
:class:`~aistudio_api.application.surface_orchestrator.SurfacePolicyError`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn

from aistudio_api.application.surface_orchestrator import SurfacePolicyError

CHANNEL_ALIASES: dict[str, str] = {
    "chat": "chat",
    "pg": "chat",
    "playground": "chat",
    "build": "build",
    "official": "official",
    "gemini_app": "official",
}

KNOWN_CHANNELS: tuple[str, ...] = ("chat", "build", "official")

_CHANNEL_LABELS: dict[str, str] = {
    "chat": "pg",
    "build": "build",
    "official": "official",
}
_IMPLEMENTED_CHANNELS = frozenset({"chat", "build"})
_SURFACE_BY_CHANNEL: dict[str, str | None] = {
    "chat": "chat",
    "build": "build",
    "official": None,
}
_DEFAULT_MAX_INSTANCES = 2
_MAX_INSTANCES_LIMIT = 16
_DEFAULT_MAX_PAGES = {"chat": 2, "build": 2, "official": 0}
_QUOTA_POOL_CHOICES = frozenset({"free", "pro", "official"})
_DISPLAY_PREFIX = {"chat": "pg", "build": "build", "official": "official"}


@dataclass(frozen=True)
class ChannelSpec:
    """Effective configuration for one channel.

    ``max_instances`` is the v2 compatibility alias; v3 budgets use
    ``max_pages``. ``quota_pool`` is ``None`` when the channel accepts any
    account pool (zero-config compatibility).
    """

    key: str
    label: str
    enabled: bool
    max_instances: int
    implemented: bool
    max_pages: int = 2
    allowed_tags: tuple[str, ...] = ()
    display_prefix: str = ""

    @property
    def prefix(self) -> str:
        """Canonical model-list/UI prefix (``pg`` for the chat channel)."""
        return self.display_prefix or _DISPLAY_PREFIX.get(self.key, self.key)


@dataclass(frozen=True)
class ChannelSelection:
    """Outcome of resolving a model target to a channel."""

    channel: str
    surface: str | None
    clean_model: str
    explicit: bool
    reason: str


def normalize_channel(value: object) -> str | None:
    """Map a channel name or alias to its canonical key (``None`` if unknown)."""
    if value is None:
        return None
    return CHANNEL_ALIASES.get(str(value).strip().lower())


def parse_model_target(model_path: str) -> tuple[str | None, str]:
    """Split an optional channel prefix from a model name.

    ``models/`` is stripped first. A first ``/`` segment that is a known
    channel/alias plus a non-empty remainder is an explicit channel prefix;
    everything else is a bare model name (so ``models/gemini-x`` stays bare).
    """
    stripped = str(model_path or "").removeprefix("models/")
    head, separator, rest = stripped.partition("/")
    channel = normalize_channel(head)
    if separator and channel is not None and rest:
        return channel, rest
    return None, stripped


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_max_instances(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return min(_MAX_INSTANCES_LIMIT, max(0, parsed))


def _coerce_quota_pool(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text == "any":
        return None
    return text if text in _QUOTA_POOL_CHOICES else None


def _coerce_allowed_tags(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw: list[object] = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = list(value)
    else:
        return ()
    tags: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text.casefold() not in {tag.casefold() for tag in tags}:
            tags.append(text)
    return tuple(tags)


def _channel_specs(
    raw_channels: Mapping[object, object] | None,
) -> dict[str, Mapping[str, object]]:
    specs: dict[str, Mapping[str, object]] = {}
    if not isinstance(raw_channels, Mapping):
        return specs
    for raw_key, raw_value in raw_channels.items():
        key = normalize_channel(raw_key)
        if key is not None and isinstance(raw_value, Mapping):
            specs[key] = raw_value
    return specs


def build_registry(features: Mapping[str, object]) -> dict[str, ChannelSpec]:
    """Build the effective channel registry from ``features``.

    Explicit ``features.channels`` entries win. Missing entries fall back to the
    v1 defaults; ``features.build_surface`` is a legacy alias for
    ``channels.build.enabled`` used only when ``channels.build`` is absent.
    """
    raw_channels = features.get("channels")
    configured = _channel_specs(
        raw_channels if isinstance(raw_channels, Mapping) else None
    )
    build_legacy_enabled = bool(features.get("build_surface", False))

    registry: dict[str, ChannelSpec] = {}
    for key in KNOWN_CHANNELS:
        raw = configured.get(key)
        if key == "official":
            registry[key] = ChannelSpec(
                key=key,
                label=_CHANNEL_LABELS[key],
                enabled=False,
                max_instances=0,
                implemented=False,
                max_pages=0,
                display_prefix=_DISPLAY_PREFIX[key],
            )
            continue
        if raw is None:
            enabled = True if key == "chat" else build_legacy_enabled
            max_instances = _DEFAULT_MAX_INSTANCES
            max_pages = max_instances if key in ("chat", "build") else 0
            allowed_tags: tuple[str, ...] = ()
        else:
            enabled = _coerce_bool(raw.get("enabled"), True)
            max_instances = _coerce_max_instances(
                raw.get("max_instances"), _DEFAULT_MAX_INSTANCES
            )
            max_pages = max_instances if key in ("chat", "build") else 0
            legacy_pool = _coerce_quota_pool(raw.get("quota_pool"))
            allowed_tags = _coerce_allowed_tags(raw.get("allowed_tags"))
            if not allowed_tags and legacy_pool:
                allowed_tags = (legacy_pool,)
        registry[key] = ChannelSpec(
            key=key,
            label=_CHANNEL_LABELS[key],
            enabled=enabled,
            max_instances=max_instances,
            implemented=key in _IMPLEMENTED_CHANNELS,
            max_pages=max_pages,
            allowed_tags=allowed_tags,
            display_prefix=_DISPLAY_PREFIX[key],
        )
    return registry


def enabled_channels(
    registry: Mapping[str, ChannelSpec],
) -> tuple[ChannelSpec, ...]:
    """Enabled, implemented channels in canonical order."""
    return tuple(
        registry[key]
        for key in KNOWN_CHANNELS
        if key in registry and registry[key].enabled and registry[key].implemented
    )


def channel_prefix(channel: str) -> str:
    """Return the external model-list prefix for a canonical channel."""
    return _DISPLAY_PREFIX.get(normalize_channel(channel) or "", "")


def model_list_prefixes(
    registry: Mapping[str, ChannelSpec],
) -> tuple[tuple[str, str], ...]:
    """(canonical channel, external prefix) for enabled+implemented channels."""
    return tuple((spec.key, spec.prefix) for spec in enabled_channels(registry))


def is_multi_mode(
    registry: Mapping[str, ChannelSpec],
    slots: Sequence[object],
) -> bool:
    """True when two channels can run concurrently or two slots ask for it."""
    if len(enabled_channels(registry)) >= 2:
        return True
    active_slots = 0
    for slot in slots:
        if not isinstance(slot, Mapping):
            continue
        if normalize_channel(slot.get("channel")) is None:
            continue
        if not _coerce_bool(slot.get("enabled"), True):
            continue
        active_slots += 1
        if active_slots >= 2:
            return True
    return False


def _channel_error(code: str, *, message: str, advice: str) -> SurfacePolicyError:
    return SurfacePolicyError(code, message=message, advice=advice)


def _raise_channel_disabled(channel: str, model_path: str) -> NoReturn:
    raise _channel_error(
        "surface_channel_disabled",
        message=(
            f"渠道 '{channel}' 不可用（model={model_path}）：该渠道未启用或尚未实现。"
        ),
        advice=(
            "请在 features.channels 中启用该渠道并确认已实现，"
            "或改用已启用的渠道前缀（chat/pg/playground/build）或裸模型名；"
            "本次请求不静默换渠道。"
        ),
    )


def _raise_unknown_channel(prefix: str, model_path: str) -> NoReturn:
    raise _channel_error(
        "surface_channel_disabled",
        message=(
            f"未知渠道前缀 '{prefix}'（model={model_path}）："
            "可用渠道为 chat(pg/playground)、build、official。"
        ),
        advice=(
            "请使用 chat/、pg/、playground/、build/ 或 official/ 前缀，"
            "或直接使用裸模型名；本次请求不静默换渠道。"
        ),
    )


def _raise_no_capable_channel(model_path: str) -> NoReturn:
    raise _channel_error(
        "surface_no_capable_account",
        message=(
            f"模型 '{model_path}' 没有可用的已实现渠道：chat/build 均被禁用或未实现。"
        ),
        advice=(
            "请在 features.channels 中至少启用 chat 或 build 渠道，"
            "并确认配置结构为 channel->mapping；本次请求不静默换渠道。"
        ),
    )


def _raise_model_guard(model_path: str, channel: str) -> NoReturn:
    raise _channel_error(
        "surface_model_guard",
        message=(
            f"模型 '{model_path}' 属 argon/Pro 系，仅可在 chat 渠道使用，"
            f"当前渠道 '{channel}' 不允许。"
        ),
        advice=(
            "请改用 chat/pg/playground 前缀，或改用支持 build 的非 chat-only 模型；"
            "本次请求不静默换渠道。"
        ),
    )


def _unknown_prefix(model_path: str) -> str | None:
    stripped = str(model_path or "").removeprefix("models/")
    head, separator, _rest = stripped.partition("/")
    if separator and head.strip() and normalize_channel(head) is None:
        return head.strip()
    return None


def _selection(
    channel: str, clean_model: str, *, explicit: bool, reason: str
) -> ChannelSelection:
    return ChannelSelection(
        channel=channel,
        surface=_SURFACE_BY_CHANNEL[channel],
        clean_model=clean_model,
        explicit=explicit,
        reason=reason,
    )


def select_channel(
    model_path: str,
    features: Mapping[str, object],
    *,
    mounted_channels: Collection[str] = (),
    model_is_chat_only: bool = False,
) -> ChannelSelection:
    """Resolve a model target to one channel without ever switching silently."""
    registry = build_registry(features)
    explicit_channel, clean_model = parse_model_target(model_path)

    if explicit_channel is None:
        unknown_prefix = _unknown_prefix(model_path)
        if unknown_prefix is not None:
            _raise_unknown_channel(unknown_prefix, model_path)

    if explicit_channel is not None:
        spec = registry[explicit_channel]
        if not spec.enabled or not spec.implemented:
            _raise_channel_disabled(explicit_channel, model_path)
        if model_is_chat_only and explicit_channel != "chat":
            _raise_model_guard(model_path, explicit_channel)
        return _selection(
            explicit_channel, clean_model, explicit=True, reason="explicit"
        )

    candidates = enabled_channels(registry)
    if not candidates:
        _raise_no_capable_channel(model_path)

    if len(candidates) == 1:
        chosen = candidates[0]
        if model_is_chat_only and chosen.key != "chat":
            _raise_model_guard(model_path, chosen.key)
        return _selection(
            chosen.key, clean_model, explicit=False, reason="capability-default"
        )

    if model_is_chat_only:
        chat = next((spec for spec in candidates if spec.key == "chat"), None)
        if chat is None:
            _raise_model_guard(model_path, "build")
        return _selection(
            chat.key, clean_model, explicit=False, reason="capability-default"
        )

    mounted = {normalize_channel(item) for item in mounted_channels}
    mounted.discard(None)
    mounted_candidates = [spec for spec in candidates if spec.key in mounted]
    if len(mounted_candidates) == 1:
        return _selection(
            mounted_candidates[0].key,
            clean_model,
            explicit=False,
            reason="mounted",
        )

    build = next((spec for spec in candidates if spec.key == "build"), None)
    chosen = build if build is not None else candidates[0]
    return _selection(
        chosen.key, clean_model, explicit=False, reason="capability-default"
    )


__all__ = [
    "CHANNEL_ALIASES",
    "KNOWN_CHANNELS",
    "ChannelSelection",
    "ChannelSpec",
    "build_registry",
    "channel_prefix",
    "enabled_channels",
    "is_multi_mode",
    "model_list_prefixes",
    "normalize_channel",
    "parse_model_target",
    "select_channel",
]
