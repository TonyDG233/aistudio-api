"""ATG configuration loader.

Ported from Ark ``src/atg/config.js``. Precedence: explicit ``overrides`` dict >
``config.yaml features.atg`` (which itself layers YAML > ``AISTUDIO_*`` env
baseline > built-in defaults via :func:`get_features_config`) > built-in
defaults. Mode ``off`` disables the module entirely (zero pipeline change).

The features section is re-read on every construction so a hot-reloaded
``config.yaml`` (PUT /config/yaml) is reflected without a restart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from aistudio_api.infrastructure.gateway.model_defaults import get_features_config

from .model_filter import ModelFilter, create_model_filter

DEFAULT_MODE = "off"
DEFAULT_TOOL_PREFIX = "atg_emit_"
DEFAULT_CONTROL_POSITION = "tail"
DEFAULT_RECOVERY = "repair"
DEFAULT_EMPTY_RETRIES = 2
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_SIDE_BUFFER_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES_CEILING = 100 * 1024 * 1024

_VALID_MODES = ("off", "prefer", "required")

_LEADING_INT_RE = re.compile(r"^[+-]?\d+")


def _parse_mode(value: object) -> str:
    """Align with Ark ``parseMode``: valid modes pass, everything else is off."""
    text = str(value or "").strip().lower()
    if text in ("prefer", "required"):
        return text
    return "off"


def resolve_atg_mode(
    cfg_or_format: str | AtgConfig | Mapping[str, object],
    api_format: str | None = None,
) -> str:
    """Resolve the effective ATG mode for a protocol face.

    Supports both ``resolve_atg_mode(api_format)`` (reading ``features.atg``)
    and ``resolve_atg_mode(cfg, api_format)`` (reading a loaded
    :class:`AtgConfig` or raw mapping). ``mode_by_format.<format>`` inherits
    ``mode`` unless it explicitly names ``off`` / ``prefer`` / ``required``.
    """
    if isinstance(cfg_or_format, str):
        target_format = cfg_or_format
        features = get_features_config()
        raw_atg = features.get("atg")
        atg: Mapping[str, object] = raw_atg if isinstance(raw_atg, Mapping) else {}
    else:
        if api_format is None:
            raise TypeError(
                "resolve_atg_mode(cfg, api_format) requires api_format when cfg is provided"
            )
        target_format = api_format
        if isinstance(cfg_or_format, AtgConfig):
            by_format = cfg_or_format.mode_by_format
            if isinstance(by_format, Mapping):
                value = str(by_format.get(target_format, "inherit")).strip().lower()
                if value in _VALID_MODES:
                    return value
            return _parse_mode(cfg_or_format.mode)
        atg = cfg_or_format

    by_format = atg.get("mode_by_format")
    if isinstance(by_format, Mapping):
        value = str(by_format.get(target_format, "inherit")).strip().lower()
        if value in _VALID_MODES:
            return value
    return _parse_mode(atg.get("mode", DEFAULT_MODE))


def _parse_bounded_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    """Align with Ark ``parseBoundedInt`` (parseInt base 10 then clamp)."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    else:
        match = _LEADING_INT_RE.match(str(value).strip())
        if match is None:
            return fallback
        parsed = int(match.group(0))
    return min(maximum, max(minimum, parsed))


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


@dataclass(frozen=True)
class AtgConfig:
    mode: str = DEFAULT_MODE
    mode_by_format: dict[str, str] = field(default_factory=dict)
    tool_prefix: str = DEFAULT_TOOL_PREFIX
    control_position: str = DEFAULT_CONTROL_POSITION
    strict: bool = False
    wrap_input: bool = False
    empty_retries: int = DEFAULT_EMPTY_RETRIES
    recovery: str = DEFAULT_RECOVERY
    side_buffer_bytes: int = 0
    client_synthetic_guard: bool = True
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    text_model_pattern: str = ""
    non_text_model_pattern: str = ""
    model_filter: ModelFilter = field(default_factory=create_model_filter)

    @property
    def enabled(self) -> bool:
        """Derived flag: ATG is active whenever the mode is not ``off``."""
        return self.mode != "off"

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "mode_by_format": dict(self.mode_by_format),
            "tool_prefix": self.tool_prefix,
            "control_position": self.control_position,
            "strict": self.strict,
            "wrap_input": self.wrap_input,
            "empty_retries": self.empty_retries,
            "recovery": self.recovery,
            "side_buffer_bytes": self.side_buffer_bytes,
            "client_synthetic_guard": self.client_synthetic_guard,
            "max_response_bytes": self.max_response_bytes,
            "text_model_pattern": self.text_model_pattern,
            "non_text_model_pattern": self.non_text_model_pattern,
        }


def load_atg_config(overrides: dict[str, object] | None = None) -> AtgConfig:
    """Load the effective ATG config from features + overrides + defaults."""
    overrides = overrides or {}
    features = get_features_config()
    raw_atg = features.get("atg")
    raw: dict[str, object] = raw_atg if isinstance(raw_atg, dict) else {}

    def pick(key: str, default: object) -> object:
        if key in overrides:
            return overrides[key]
        return raw.get(key, default)

    mode = _parse_mode(pick("mode", DEFAULT_MODE))
    raw_by_format = pick("mode_by_format", {})
    mode_by_format: dict[str, str] = {}
    if isinstance(raw_by_format, Mapping):
        for fmt, val in raw_by_format.items():
            if not isinstance(fmt, str):
                continue
            parsed_val = str(val or "inherit").strip().lower()
            mode_by_format[fmt] = (
                parsed_val if parsed_val in _VALID_MODES else "inherit"
            )
    tool_prefix = str(pick("tool_prefix", DEFAULT_TOOL_PREFIX) or "").strip()
    if not tool_prefix:
        tool_prefix = DEFAULT_TOOL_PREFIX
    control_position = (
        "head"
        if str(pick("control_position", DEFAULT_CONTROL_POSITION)) == "head"
        else "tail"
    )
    recovery = "fail" if str(pick("recovery", DEFAULT_RECOVERY)) == "fail" else "repair"
    text_model_pattern = str(pick("text_model_pattern", "") or "")
    non_text_model_pattern = str(pick("non_text_model_pattern", "") or "")

    model_filter_override = overrides.get("model_filter")
    model_filter = (
        model_filter_override
        if isinstance(model_filter_override, ModelFilter)
        else create_model_filter(text_model_pattern, non_text_model_pattern)
    )

    return AtgConfig(
        mode=mode,
        mode_by_format=mode_by_format,
        tool_prefix=tool_prefix,
        control_position=control_position,
        strict=_coerce_bool(pick("strict", False), False),
        wrap_input=_coerce_bool(pick("wrap_input", False), False),
        empty_retries=_parse_bounded_int(
            pick("empty_retries", DEFAULT_EMPTY_RETRIES),
            DEFAULT_EMPTY_RETRIES,
            0,
            5,
        ),
        recovery=recovery,
        side_buffer_bytes=_parse_bounded_int(
            pick("side_buffer_bytes", 0), 0, 0, MAX_SIDE_BUFFER_BYTES
        ),
        client_synthetic_guard=_coerce_bool(pick("client_synthetic_guard", True), True),
        max_response_bytes=_parse_bounded_int(
            pick("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES),
            DEFAULT_MAX_RESPONSE_BYTES,
            1024,
            MAX_RESPONSE_BYTES_CEILING,
        ),
        text_model_pattern=text_model_pattern,
        non_text_model_pattern=non_text_model_pattern,
        model_filter=model_filter,
    )


__all__ = [
    "DEFAULT_MODE",
    "DEFAULT_TOOL_PREFIX",
    "AtgConfig",
    "load_atg_config",
    "resolve_atg_mode",
]
