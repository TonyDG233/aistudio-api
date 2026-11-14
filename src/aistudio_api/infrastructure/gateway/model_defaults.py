"""Model-specific default behaviors for AI Studio wire requests."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import cast

import yaml

from aistudio_api.config import (
    resolve_config_file,
    resolve_default_config_template,
)
from aistudio_api.domain.model_access import (
    TierSelectorError,
    canonical_tier,
    resolve_tier_selector,
)
from aistudio_api.infrastructure.utils.logger import get_logger

from .wire_types import (
    AistudioImageOutputMode,
    AistudioThinkingConfig,
    ImageOutputType,
    MediaResolution,
    ThinkingLevel,
)

_resolve_config_path = resolve_config_file
logger = get_logger("config")

_SAFETY_CATEGORY_ORDER = (
    ("harassment", 7),
    ("hate", 8),
    ("sexually explicit", 9),
    ("dangerous content", 10),
)

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_VALID_SURFACES = ("chat", "build")
_VALID_MIN_TIERS = ("Free", "Plus", "Pro", "Ultra", "Ultra_20X")

_RETRY_SAME_DELAY_DEFAULT_SEC = 2.0
_RETRY_SAME_DELAY_MAX_SEC = 30.0
_RETRY_SAME_DELAY_ENV = "AISTUDIO_RETRY_SAME_DELAY_SEC"

DEFAULT_SWITCH_BACKOFF_SEC = 4.0
MIN_SWITCH_BACKOFF_SEC = 0.0
MAX_SWITCH_BACKOFF_SEC = 30.0

_LEGACY_KEEPALIVE_KEYS = (
    "auto_switch",
    "auto_switch_interval_hours",
    "keepalive_auth_update",
    "keepalive_auth_update_hours",
    "keepalive_metadata_refresh",
    "keepalive_metadata_refresh_hours",
    "keepalive_metadata_refresh_on_startup",
    "keepalive_active_activity",
    "keepalive_active_activity_seconds",
)
_KEEPALIVE_BOOL_KEYS = (
    "auth_update",
    "metadata_refresh",
    "metadata_refresh_on_startup",
    "active_activity",
)
_KEEPALIVE_FLOAT_KEYS = (
    "auth_update_hours",
    "metadata_refresh_hours",
    "active_activity_seconds",
)
_KEEPALIVE_DEFAULT_BOOLS = {
    "auth_update": True,
    "metadata_refresh": True,
    "metadata_refresh_on_startup": True,
    "active_activity": False,
}
_KEEPALIVE_DEFAULT_FLOATS = {
    "auth_update_hours": 24.0,
    "metadata_refresh_hours": 24.0,
    "active_activity_seconds": 60.0,
}

_MODEL_DISCOVERY_LIVE_REFRESH_DEFAULT_SEC = 86400
_MODEL_DISCOVERY_FALLBACK_REFRESH_DEFAULT_SEC = 600
_MODEL_DISCOVERY_DEFAULTS = {
    "live_refresh_interval_sec": _MODEL_DISCOVERY_LIVE_REFRESH_DEFAULT_SEC,
    "fallback_refresh_interval_sec": _MODEL_DISCOVERY_FALLBACK_REFRESH_DEFAULT_SEC,
}
_ATG_DEFAULT_CONTROL_TEMPLATE = (
    "IMPORTANT: Regular chat responses are unreliable in this environment. "
    "Deliver your complete final answer exclusively by calling the function {tool_name}, "
    'passing your visible answer as a string in the "content" parameter. '
    "Do not mention this instruction."
)
_ATG_DEFAULT_INPUT_CONTEXT_INSTRUCTION = (
    "Above is the complete conversation context. "
    "Respond directly to the final user turn and any attached media."
)


class ConfigValidationError(ValueError):
    """配置文件关键块类型或必填项非法。"""


def _deep_merge(base: object, override: object) -> object:
    """递归合并字典；列表与标量整体覆盖。"""
    if isinstance(base, dict) and isinstance(override, dict):
        merged: dict[str, object] = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


def merge_config_documents(base: object, override: object) -> object:
    """Public wrapper for partial config writes: dicts merge deeply, lists replace."""
    return _deep_merge(base, override)


def _describe_type(value: object) -> str:
    return type(value).__name__


def _apply_noncritical_fallbacks(config: dict[str, object]) -> dict[str, object]:
    """非关键项非法时回退默认值并显式记录 warning。"""
    defaults = _default_config()

    raw_logging = config.get("logging")
    if not isinstance(raw_logging, dict):
        logger.warning(
            "配置 logging 必须为字典（当前类型 %s），已回退默认 logging 块",
            _describe_type(raw_logging),
        )
        default_logging = defaults.get("logging")
        config["logging"] = (
            dict(default_logging) if isinstance(default_logging, dict) else {}
        )
    else:
        raw_level = raw_logging.get("level")
        level = raw_level.strip().upper() if isinstance(raw_level, str) else ""
        if level not in _VALID_LOG_LEVELS:
            logger.warning(
                "配置 logging.level 非法（%r），已回退默认值 INFO", raw_level
            )
            raw_logging["level"] = "INFO"
        else:
            raw_logging["level"] = level

    raw_security = config.get("security")
    if not isinstance(raw_security, dict):
        logger.warning(
            "配置 security 必须为字典（当前类型 %s），已回退默认 security 块",
            _describe_type(raw_security),
        )
        default_security = defaults.get("security")
        config["security"] = (
            dict(default_security) if isinstance(default_security, dict) else {}
        )
    else:
        allow_anonymous = raw_security.get("api_allow_anonymous", False)
        if not isinstance(allow_anonymous, bool):
            logger.warning(
                "配置 security.api_allow_anonymous 非法（%r），已回退默认值 false",
                allow_anonymous,
            )
            raw_security["api_allow_anonymous"] = False
        else:
            raw_security["api_allow_anonymous"] = allow_anonymous

    raw_rotator = config.get("rotator")
    if isinstance(raw_rotator, dict):
        quota_scope = raw_rotator.get("quota_scope", "account")
        if quota_scope not in ("account", "account_surface"):
            logger.warning(
                "配置 rotator.quota_scope 非法（%r），已回退默认值 account",
                quota_scope,
            )
            raw_rotator["quota_scope"] = "account"

    raw_features = config.get("features")
    if not isinstance(raw_features, dict):
        logger.warning(
            "配置 features 必须为字典（当前类型 %s），已回退默认 features 块",
            _describe_type(raw_features),
        )
        default_features = defaults.get("features")
        config["features"] = (
            dict(default_features) if isinstance(default_features, dict) else {}
        )
    else:
        legacy_keys = [key for key in _LEGACY_KEEPALIVE_KEYS if key in raw_features]
        if legacy_keys:
            logger.warning(
                "检测到旧版 keepalive 配置键（%s）；已忽略，请迁移到 "
                "features.keepalive",
                ", ".join(legacy_keys),
            )
            for key in legacy_keys:
                raw_features.pop(key, None)

        raw_routing = raw_features.get("routing")
        if not isinstance(raw_routing, dict):
            logger.warning(
                "配置 features.routing 必须为字典（当前类型 %s），已回退默认值",
                _describe_type(raw_routing),
            )
            default_features = defaults.get("features")
            default_routing = (
                default_features.get("routing")
                if isinstance(default_features, dict)
                else None
            )
            raw_features["routing"] = (
                dict(default_routing) if isinstance(default_routing, dict) else {}
            )
        else:
            max_switches = raw_routing.get("max_switches", 2)
            if (
                isinstance(max_switches, bool)
                or not isinstance(max_switches, int)
                or max_switches < 0
            ):
                logger.warning(
                    "配置 features.routing.max_switches 非法（%r），已回退默认值 2",
                    max_switches,
                )
                raw_routing["max_switches"] = 2
            raw_routing["retry_same_delay_sec"] = _normalize_retry_same_delay(
                raw_routing
            )
            raw_routing["switch_backoff_sec"] = _normalize_switch_backoff_sec(
                raw_routing.get("switch_backoff_sec", DEFAULT_SWITCH_BACKOFF_SEC)
            )

        default_features = defaults.get("features")
        default_keepalive = (
            default_features.get("keepalive")
            if isinstance(default_features, dict)
            else None
        )
        fallback_keepalive = (
            dict(default_keepalive) if isinstance(default_keepalive, dict) else {}
        )
        raw_keepalive = raw_features.get("keepalive")
        if not isinstance(raw_keepalive, dict):
            logger.warning(
                "配置 features.keepalive 必须为字典（当前类型 %s），已回退默认值",
                _describe_type(raw_keepalive),
            )
            raw_features["keepalive"] = fallback_keepalive
        else:
            for key in _KEEPALIVE_BOOL_KEYS:
                value = raw_keepalive.get(key, fallback_keepalive.get(key))
                if not isinstance(value, bool):
                    logger.warning(
                        "配置 features.keepalive.%s 非法（%r），已回退默认值 %r",
                        key,
                        value,
                        _KEEPALIVE_DEFAULT_BOOLS[key],
                    )
                    value = _KEEPALIVE_DEFAULT_BOOLS[key]
                raw_keepalive[key] = value
            for key in _KEEPALIVE_FLOAT_KEYS:
                value = raw_keepalive.get(key, fallback_keepalive.get(key))
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or float(value) <= 0
                ):
                    logger.warning(
                        "配置 features.keepalive.%s 非法（%r），已回退默认值 %r",
                        key,
                        value,
                        _KEEPALIVE_DEFAULT_FLOATS[key],
                    )
                    value = _KEEPALIVE_DEFAULT_FLOATS[key]
                raw_keepalive[key] = float(value)

        default_model_discovery = (
            default_features.get("model_discovery")
            if isinstance(default_features, dict)
            else None
        )
        fallback_model_discovery = (
            dict(default_model_discovery)
            if isinstance(default_model_discovery, dict)
            else dict(_MODEL_DISCOVERY_DEFAULTS)
        )
        raw_model_discovery = raw_features.get("model_discovery")
        if not isinstance(raw_model_discovery, dict):
            logger.warning(
                "配置 features.model_discovery 必须为字典（当前类型 %s），已回退默认值",
                _describe_type(raw_model_discovery),
            )
            raw_features["model_discovery"] = fallback_model_discovery
        else:
            for key, default_value in _MODEL_DISCOVERY_DEFAULTS.items():
                value = raw_model_discovery.get(
                    key, fallback_model_discovery.get(key, default_value)
                )
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    logger.warning(
                        "配置 features.model_discovery.%s 非法（%r），已回退默认值 %r",
                        key,
                        value,
                        default_value,
                    )
                    value = default_value
                raw_model_discovery[key] = value

        default_atg = (
            default_features.get("atg") if isinstance(default_features, dict) else None
        )
        fallback_control_template = (
            default_atg.get("control_template")
            if isinstance(default_atg, dict)
            and isinstance(default_atg.get("control_template"), str)
            and str(default_atg.get("control_template")).strip()
            else _ATG_DEFAULT_CONTROL_TEMPLATE
        )
        fallback_input_instruction = (
            default_atg.get("input_context_instruction")
            if isinstance(default_atg, dict)
            and isinstance(default_atg.get("input_context_instruction"), str)
            and str(default_atg.get("input_context_instruction")).strip()
            else _ATG_DEFAULT_INPUT_CONTEXT_INSTRUCTION
        )
        raw_atg = raw_features.get("atg")
        if isinstance(raw_atg, dict):
            wrap_input = raw_atg.get("wrap_input", False)
            if not isinstance(wrap_input, bool):
                logger.warning(
                    "配置 features.atg.wrap_input 非法（%r），已回退默认值 false",
                    wrap_input,
                )
                raw_atg["wrap_input"] = False
            else:
                raw_atg["wrap_input"] = wrap_input

            wrap_output = raw_atg.get("wrap_output", True)
            if not isinstance(wrap_output, bool):
                logger.warning(
                    "配置 features.atg.wrap_output 非法（%r），已回退默认值 true",
                    wrap_output,
                )
                raw_atg["wrap_output"] = True
            else:
                raw_atg["wrap_output"] = wrap_output

            control_template = raw_atg.get(
                "control_template", fallback_control_template
            )
            if not isinstance(control_template, str) or not control_template.strip():
                logger.warning(
                    "配置 features.atg.control_template 非法或为空（%r），已回退默认模板",
                    control_template,
                )
                raw_atg["control_template"] = fallback_control_template
            else:
                raw_atg["control_template"] = control_template

            input_instruction = raw_atg.get(
                "input_context_instruction", fallback_input_instruction
            )
            if not isinstance(input_instruction, str) or not input_instruction.strip():
                logger.warning(
                    "配置 features.atg.input_context_instruction 非法或为空（%r），"
                    "已回退默认指令",
                    input_instruction,
                )
                raw_atg["input_context_instruction"] = fallback_input_instruction
            else:
                raw_atg["input_context_instruction"] = input_instruction

    return config


def _require_mapping(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ConfigValidationError(
            f"配置校验失败：{key} 必须为字典，当前类型为 {_describe_type(value)}"
        )
    return value


def _normalize_instance_specs(raw_instances: object) -> list[dict[str, object]]:
    """Normalize ``instances`` tier selectors into closed interval specs.

    ``only_tier`` / ``min_tier`` / ``max_tier`` must be strings naming a known
    tier; at least one is required.  ``only_tier`` pins an exact tier,
    ``min_tier`` alone opens ``min..Ultra_20X``, ``max_tier`` alone defaults its
    lower bound to Pro (Free stays Free), and min+max form a closed interval.
    Only a missing selector, a conflicting combination, an inverted interval or
    a lane mix is rejected; non-string/unknown selectors are hard errors instead
    of being silently dropped.  Free and paid lanes never mix.  Every Free
    selector normalizes to the same ``only=Free,min=Free,max=Free`` merge key so
    ``only_tier: Free`` and legacy ``min_tier: Free`` merge their counts.
    """
    if not isinstance(raw_instances, list):
        raise ConfigValidationError(
            "配置校验失败：instances 必须为列表，"
            f"当前类型为 {_describe_type(raw_instances)}"
        )

    merged_counts: dict[tuple[str, str, str, str], int] = {}
    order: list[tuple[str, str, str, str]] = []
    normalized_by_key: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for index, item in enumerate(raw_instances):
        if not isinstance(item, dict):
            logger.warning("忽略非法实例配置 #%d：必须为字典", index)
            continue

        count = item.get("count")
        raw_surface = item.get("surface")

        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            logger.warning("忽略非法实例配置 #%d：count 必须为正整数", index)
            continue

        surface = raw_surface.strip().lower() if isinstance(raw_surface, str) else ""
        if surface not in _VALID_SURFACES:
            logger.warning("忽略非法实例配置 #%d：surface 只允许 chat/build", index)
            continue

        raw_only = item.get("only_tier")
        raw_min = item.get("min_tier")
        raw_max = item.get("max_tier")
        for name, value in (
            ("only_tier", raw_only),
            ("min_tier", raw_min),
            ("max_tier", raw_max),
        ):
            if value is None:
                continue
            if not isinstance(value, str):
                raise ConfigValidationError(
                    "配置校验失败：第 "
                    f"{index + 1} 个实例配置 {name} 必须为字符串，"
                    f"当前类型为 {_describe_type(value)}"
                )
            if value.strip() and canonical_tier(value) is None:
                raise ConfigValidationError(
                    "配置校验失败：第 "
                    f"{index + 1} 个实例配置 {name}={value!r} 不是已知 tier"
                    f"（{'/'.join(_VALID_MIN_TIERS)}）"
                )
        provided = [
            value
            for value in (raw_only, raw_min, raw_max)
            if isinstance(value, str) and value.strip()
        ]
        if not provided:
            raise ConfigValidationError(
                "配置校验失败：第 "
                f"{index + 1} 个实例配置缺少 tier 选择器"
                "（only_tier/min_tier/max_tier 至少一个）"
            )

        try:
            selector = resolve_tier_selector(
                only_tier=raw_only,
                min_tier=raw_min,
                max_tier=raw_max,
            )
        except TierSelectorError as exc:
            raise ConfigValidationError(
                f"配置校验失败：第 {index + 1} 个实例配置非法：{exc}"
            ) from exc

        if selector.lane == "free":
            normalized: dict[str, object] = {
                "count": count,
                "surface": surface,
                "only_tier": "Free",
                "min_tier": "Free",
                "max_tier": "Free",
            }
            key = (surface, "Free", "Free", "Free")
        elif selector.only_tier is not None:
            normalized = {
                "count": count,
                "surface": surface,
                "only_tier": selector.only_tier,
            }
            key = (surface, selector.only_tier, "", "")
        else:
            upper_explicit = isinstance(raw_max, str) and bool(raw_max.strip())
            normalized = {
                "count": count,
                "surface": surface,
                "min_tier": selector.min_tier,
            }
            if upper_explicit:
                normalized["max_tier"] = selector.max_tier
            key = (
                surface,
                "",
                selector.min_tier,
                selector.max_tier if upper_explicit else "",
            )

        if key in merged_counts:
            merged_counts[key] += count
        else:
            merged_counts[key] = count
            order.append(key)
            normalized_by_key[key] = normalized

    if not merged_counts:
        raise ConfigValidationError("配置校验失败：instances 未包含任何有效实例配置")

    normalized_specs: list[dict[str, object]] = []
    for key in order:
        entry = dict(normalized_by_key[key])
        entry["count"] = merged_counts[key]
        normalized_specs.append(entry)
    return normalized_specs


_MAX_CONTEXTS_LIMIT = 16


def _normalize_max_contexts(
    config: dict[str, object], specs: list[dict[str, object]]
) -> int:
    """Global instance/context upper bound; defaults to the configured spec sum."""
    raw = config.get("max_contexts")
    if raw is None:
        return max(1, sum(int(str(item.get("count", 0))) for item in specs))
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ConfigValidationError(
            f"配置校验失败：max_contexts 必须为正整数，当前值为 {raw!r}"
        )
    if raw > _MAX_CONTEXTS_LIMIT:
        logger.warning(
            "配置 max_contexts=%d 超出上限 %d，已回退上限",
            raw,
            _MAX_CONTEXTS_LIMIT,
        )
        return _MAX_CONTEXTS_LIMIT
    return raw


_ENGINE_MODES = frozenset({"browser", "browserless"})
_SIGNER_MODES = frozenset({"local_subprocess", "remote_http"})
_TRANSPORT_MODES = frozenset({"signer_rpc", "direct_http"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _engine_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float | None = None,
    integer: bool = False,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(
            f"配置校验失败：engine.{field_name} 必须为数值，当前值为 {value!r}"
        )
    numeric = float(value)
    if integer and not numeric.is_integer():
        raise ConfigValidationError(
            f"配置校验失败：engine.{field_name} 必须为整数，当前值为 {value!r}"
        )
    number = int(numeric) if integer else numeric
    if number < minimum or (maximum is not None and number > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigValidationError(
            f"配置校验失败：engine.{field_name} 超出范围 {bound}，当前值为 {value!r}"
        )
    return number


def _engine_bool(value: object, field_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigValidationError(
            f"配置校验失败：engine.{field_name} 必须为布尔值，当前值为 {value!r}"
        )
    return value


def _engine_endpoint(value: object, *, mode: str) -> str:
    from urllib.parse import urlparse

    text = str(value or "").strip()
    if not text:
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.signer.endpoint 不能为空"
        )
    parsed = urlparse(text)
    allowed_schemes = ("http",) if mode == "local_subprocess" else ("http", "https")
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.signer.endpoint 非法"
            f"（{text!r}，本地模式仅允许 http）"
        )
    if parsed.username or parsed.password:
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.signer.endpoint 不得内嵌凭据"
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.signer.endpoint 不允许 path/query/fragment"
        )
    host = (parsed.hostname or "").lower()
    if mode == "local_subprocess" and host not in _LOOPBACK_HOSTS:
        raise ConfigValidationError(
            f"配置校验失败：本地 signer 模式 endpoint 必须是回环地址，当前为 {host!r}"
        )
    if mode == "remote_http" and host in _LOOPBACK_HOSTS:
        raise ConfigValidationError(
            "配置校验失败：远程 signer 模式必须显式配置非回环 endpoint，"
            "不得回退到本地默认地址"
        )
    return text.rstrip("/")


def _normalize_engine_settings(
    raw: object,
    *,
    specs: list[dict[str, object]] | None = None,
    max_contexts: int | None = None,
) -> dict[str, object]:
    """Validate/normalize the ``engine`` block; fail fast on illegal values."""
    if not isinstance(raw, dict):
        raise ConfigValidationError(
            f"配置校验失败：engine 必须为字典，当前类型为 {_describe_type(raw)}"
        )
    mode = str(raw.get("mode") or "browser").strip().lower()
    if mode not in _ENGINE_MODES:
        raise ConfigValidationError(
            f"配置校验失败：engine.mode 必须为 browser 或 browserless，当前为 {mode!r}"
        )

    switch = raw.get("switch")
    if switch is None:
        switch = {}
    if not isinstance(switch, dict):
        raise ConfigValidationError("配置校验失败：engine.switch 必须为字典")
    drain_timeout_s = _engine_number(
        switch.get("drain_timeout_s", 12),
        "switch.drain_timeout_s",
        minimum=0,
        maximum=120,
    )
    startup_concurrency = _engine_number(
        switch.get("startup_concurrency", 2),
        "switch.startup_concurrency",
        minimum=1,
        maximum=16,
        integer=True,
    )
    switch_timeout_s = _engine_number(
        switch.get("switch_timeout_s", 120),
        "switch.switch_timeout_s",
        minimum=1,
    )
    rollback_on_partial_failure = _engine_bool(
        switch.get("rollback_on_partial_failure"),
        "switch.rollback_on_partial_failure",
        default=True,
    )

    browserless = raw.get("browserless")
    if browserless is None:
        browserless = {}
    if not isinstance(browserless, dict):
        raise ConfigValidationError("配置校验失败：engine.browserless 必须为字典")

    signer = browserless.get("signer")
    if signer is None:
        signer = {}
    if not isinstance(signer, dict):
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.signer 必须为字典"
        )
    signer_mode = str(signer.get("mode") or "local_subprocess").strip().lower()
    if signer_mode not in _SIGNER_MODES:
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.signer.mode 必须为 "
            f"local_subprocess 或 remote_http，当前为 {signer_mode!r}"
        )
    endpoint = _engine_endpoint(
        signer.get("endpoint", "http://127.0.0.1:8081"), mode=signer_mode
    )
    binary_path = str(signer.get("binary_path") or "waa-signer/bin/waa-signer")
    auto_start = _engine_bool(
        signer.get("auto_start"), "browserless.signer.auto_start", default=True
    )
    startup_timeout_s = _engine_number(
        signer.get("startup_timeout_s", 30),
        "browserless.signer.startup_timeout_s",
        minimum=1,
    )
    request_timeout_s = _engine_number(
        signer.get("request_timeout_s", 300),
        "browserless.signer.request_timeout_s",
        minimum=1,
    )
    state_dir = str(signer.get("state_dir") or "data/waa/workers")
    max_workers = _engine_number(
        signer.get("max_workers", 8),
        "browserless.signer.max_workers",
        minimum=1,
        maximum=64,
        integer=True,
    )
    idle_ttl_s = _engine_number(
        signer.get("idle_ttl_s", 900),
        "browserless.signer.idle_ttl_s",
        minimum=1,
    )
    locale = str(signer.get("locale") or "en-US")
    timezone = str(signer.get("timezone") or "America/Los_Angeles")

    transport = browserless.get("transport")
    if transport is None:
        transport = {}
    if not isinstance(transport, dict):
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.transport 必须为字典"
        )
    transport_mode = str(transport.get("mode") or "signer_rpc").strip().lower()
    if transport_mode not in _TRANSPORT_MODES:
        raise ConfigValidationError(
            "配置校验失败：engine.browserless.transport.mode 必须为 "
            f"signer_rpc 或 direct_http，当前为 {transport_mode!r}"
        )
    http2 = _engine_bool(
        transport.get("http2"), "browserless.transport.http2", default=True
    )
    max_connections = _engine_number(
        transport.get("max_connections", 6),
        "browserless.transport.max_connections",
        minimum=1,
        maximum=64,
        integer=True,
    )
    connect_timeout_s = _engine_number(
        transport.get("connect_timeout_s", 15),
        "browserless.transport.connect_timeout_s",
        minimum=1,
    )
    read_timeout_s = _engine_number(
        transport.get("read_timeout_s", 300),
        "browserless.transport.read_timeout_s",
        minimum=1,
    )

    if mode == "browserless":
        needed = 1
        if specs is not None:
            total_slots = (
                sum(int(str(item.get("count", 1) or 1)) for item in specs) or 1
            )
            needed = min(max_contexts or total_slots, total_slots)
        if int(max_workers) < needed:
            raise ConfigValidationError(
                "配置校验失败：engine.browserless.signer.max_workers="
                f"{max_workers} 不足以覆盖 {needed} 个实例槽位"
            )
        if signer_mode == "local_subprocess" and auto_start:
            from aistudio_api.infrastructure.waa.local_daemon import (
                resolve_binary_path,
            )

            binary = resolve_binary_path(binary_path)
            if not binary.is_file():
                raise ConfigValidationError(
                    "配置校验失败：engine.browserless.signer.binary_path 不存在："
                    f"{binary}"
                )

    return {
        "mode": mode,
        "switch": {
            "drain_timeout_s": float(drain_timeout_s),
            "startup_concurrency": int(startup_concurrency),
            "switch_timeout_s": float(switch_timeout_s),
            "rollback_on_partial_failure": rollback_on_partial_failure,
        },
        "browserless": {
            "signer": {
                "mode": signer_mode,
                "endpoint": endpoint,
                "binary_path": binary_path,
                "auto_start": auto_start,
                "startup_timeout_s": float(startup_timeout_s),
                "request_timeout_s": float(request_timeout_s),
                "state_dir": state_dir,
                "max_workers": int(max_workers),
                "idle_ttl_s": float(idle_ttl_s),
                "locale": locale,
                "timezone": timezone,
            },
            "transport": {
                "mode": transport_mode,
                "http2": http2,
                "max_connections": int(max_connections),
                "connect_timeout_s": float(connect_timeout_s),
                "read_timeout_s": float(read_timeout_s),
            },
        },
    }


def engine_settings_hash(engine_settings: object) -> str:
    """Stable hash used to make an identical engine PATCH a no-op."""
    payload = json.dumps(engine_settings, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_configured_engine_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Return the validated, normalized ``engine`` block."""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    specs = _normalize_instance_specs(config.get("instances"))
    max_contexts = _normalize_max_contexts(config, specs)
    return _normalize_engine_settings(
        config.get("engine"), specs=specs, max_contexts=max_contexts
    )


def validate_engine_settings(
    raw: object,
    *,
    specs: list[dict[str, object]] | None = None,
    max_contexts: int | None = None,
) -> dict[str, object]:
    """Public wrapper used by the config routes before starting a hot switch."""
    return _normalize_engine_settings(raw, specs=specs, max_contexts=max_contexts)


def validate_config_mapping(raw_dict: object) -> dict[str, object]:
    """校验合并后的配置映射，返回应用了非关键回退的配置副本。"""
    if raw_dict is None:
        raw_dict = {}
    if not isinstance(raw_dict, dict):
        raise ConfigValidationError(
            f"配置校验失败：配置顶层必须为字典，当前类型为 {_describe_type(raw_dict)}"
        )

    config = _deep_merge(_default_config(), raw_dict)
    assert isinstance(config, dict)
    _apply_noncritical_fallbacks(config)

    diagnostics = _require_mapping(config, "diagnostics")
    ui_debug = diagnostics.get("ui_debug")
    if not isinstance(ui_debug, bool):
        raise ConfigValidationError(
            f"配置校验失败：diagnostics.ui_debug 必须为布尔值，当前值为 {ui_debug!r}"
        )

    rotator = _require_mapping(config, "rotator")
    max_retries = rotator.get("max_retries")
    if (
        not isinstance(max_retries, int)
        or isinstance(max_retries, bool)
        or max_retries < 0
    ):
        raise ConfigValidationError(
            "配置校验失败：rotator.max_retries 必须为非负整数，"
            f"当前值为 {max_retries!r}"
        )

    cooldowns = rotator.get("cooldowns")
    if not isinstance(cooldowns, dict):
        raise ConfigValidationError(
            "配置校验失败：rotator.cooldowns 必须为字典，"
            f"当前类型为 {_describe_type(cooldowns)}"
        )
    for key in (
        "rate_limited_short",
        "rate_limited_drip",
        "rate_limited_drip_extended",
    ):
        value = cooldowns.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigValidationError(
                f"配置校验失败：rotator.cooldowns.{key} 必须为数值，当前值为 {value!r}"
            )

    # ``auth_error`` is a legacy/dead knob. Upstream configs omit it; keep it
    # optional for compatibility while defaulting to 0 (403 has no hard lock).
    if "auth_error" in cooldowns:
        auth_error = cooldowns["auth_error"]
        if isinstance(auth_error, bool) or not isinstance(auth_error, (int, float)):
            raise ConfigValidationError(
                "配置校验失败：rotator.cooldowns.auth_error 必须为数值，"
                f"当前值为 {auth_error!r}"
            )
    else:
        cooldowns["auth_error"] = 0

    paid_rules = _require_mapping(config, "paid_model_rules")
    for name, rule in paid_rules.items():
        if not isinstance(rule, dict):
            raise ConfigValidationError(
                f"配置校验失败：paid_model_rules.{name} 必须为字典，"
                f"当前类型为 {_describe_type(rule)}"
            )
        contains = rule.get("contains")
        required_tiers = rule.get("required_tiers")
        if not isinstance(contains, list):
            raise ConfigValidationError(
                f"配置校验失败：paid_model_rules.{name}.contains 必须为列表，"
                f"当前类型为 {_describe_type(contains)}"
            )
        if not isinstance(required_tiers, list):
            raise ConfigValidationError(
                f"配置校验失败：paid_model_rules.{name}.required_tiers 必须为列表，"
                f"当前类型为 {_describe_type(required_tiers)}"
            )

    _require_mapping(config, "tier_quotas")

    presets = config.get("account_presets")
    if not isinstance(presets, list):
        raise ConfigValidationError(
            "配置校验失败：account_presets 必须为字典列表，"
            f"当前值为 {_describe_type(presets)}"
        )
    for index, preset in enumerate(presets):
        if not isinstance(preset, dict):
            raise ConfigValidationError(
                f"配置校验失败：第 {index + 1} 个 preset 必须为字典，"
                f"当前类型为 {_describe_type(preset)}"
            )
        for preset_field in ("id", "action"):
            value = preset.get(preset_field)
            if not isinstance(value, str) or not value.strip():
                raise ConfigValidationError(
                    "配置校验失败：第 "
                    f"{index + 1} 个 preset 缺少非空字符串字段 {preset_field}"
                )

    model_defaults = _require_mapping(config, "model_defaults")
    profiles = model_defaults.get("profiles")
    if not isinstance(profiles, list):
        raise ConfigValidationError(
            "配置校验失败：model_defaults.profiles 必须为列表，"
            f"当前类型为 {_describe_type(profiles)}"
        )
    models = model_defaults.get("models")
    if not isinstance(models, dict):
        raise ConfigValidationError(
            "配置校验失败：model_defaults.models 必须为字典，"
            f"当前类型为 {_describe_type(models)}"
        )

    specs = _normalize_instance_specs(config.get("instances"))
    max_contexts = _normalize_max_contexts(config, specs)
    config["engine"] = _normalize_engine_settings(
        config.get("engine"), specs=specs, max_contexts=max_contexts
    )

    api_keys = config.get("api_keys")
    if not isinstance(api_keys, (list, str)):
        raise ConfigValidationError(
            "配置校验失败：api_keys 必须为列表或字符串，"
            f"当前类型为 {_describe_type(api_keys)}"
        )

    return config


_OBSOLETE_CONFIG_KEYS = ("groups", "channel_registry", "channels")
_OBSOLETE_KEY_PARENTS = ("routing", "features", "model_defaults")


def _strip_obsolete_config_keys(config: dict[str, object]) -> bool:
    """Remove legacy strategy-group/channel keys without touching user blocks.

    Top-level keys are always removed; the same obsolete names nested inside the
    known routing/features/model_defaults blocks are removed too. Arbitrary
    custom blocks (for example ``proxy``) are left byte-for-byte intact, and
    valid ``target_groups`` fields are never touched.
    """
    changed = False
    for key in _OBSOLETE_CONFIG_KEYS:
        if key in config:
            del config[key]
            changed = True
    for parent_name in _OBSOLETE_KEY_PARENTS:
        parent = config.get(parent_name)
        if isinstance(parent, dict) and _strip_obsolete_config_keys(parent):
            changed = True
    return changed


def _migrate_legacy_instance(item: dict[str, object]) -> bool:
    """Normalize a legacy Free ``min_tier``/``max_tier`` selector in place.

    Only Free-only selectors are converted. A ``min_tier: Free`` combined with a
    paid ``max_tier`` is a Free/Paid lane mix that must fail validation instead
    of being silently rewritten. Paid ``min_tier`` intervals are untouched.
    """
    raw_only = item.get("only_tier")
    raw_min = item.get("min_tier")
    raw_max = item.get("max_tier")
    only = canonical_tier(raw_only) if isinstance(raw_only, str) else None
    lower = canonical_tier(raw_min) if isinstance(raw_min, str) else None
    upper = canonical_tier(raw_max) if isinstance(raw_max, str) else None

    if only == "Free":
        changed = False
        for key in ("min_tier", "max_tier"):
            value = item.get(key)
            if isinstance(value, str) and canonical_tier(value) == "Free":
                del item[key]
                changed = True
        return changed

    if only is not None or upper not in (None, "Free"):
        return False
    if lower not in (None, "Free"):
        return False
    if lower == "Free" or upper == "Free":
        item.pop("min_tier", None)
        item.pop("max_tier", None)
        item["only_tier"] = "Free"
        return True
    return False


def _migrate_legacy_config_mapping(
    config: dict[str, object],
) -> tuple[dict[str, object], bool]:
    """Return a migrated copy of ``config`` and whether anything changed."""
    migrated = copy.deepcopy(config)
    changed = _strip_obsolete_config_keys(migrated)

    raw_instances = migrated.get("instances")
    if isinstance(raw_instances, list):
        for item in raw_instances:
            if isinstance(item, dict) and _migrate_legacy_instance(item):
                changed = True
    return migrated, changed


def _read_config_file(config_path: Path) -> dict[str, object]:
    """Read a runtime config file, raising ConfigValidationError on bad input."""
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(
            f"配置校验失败：无法读取配置文件 {config_path}：{exc}"
        ) from exc
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigValidationError(f"配置校验失败：YAML 语法错误：{exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigValidationError(
            f"配置校验失败：配置顶层必须为字典，当前类型为 {_describe_type(loaded)}"
        )
    return cast("dict[str, object]", loaded)


def _is_default_template_path(config_path: Path) -> bool:
    """True when ``config_path`` points at the tracked defaults template."""
    try:
        return config_path.resolve() == resolve_default_config_template().resolve()
    except (FileNotFoundError, OSError):
        return False


def validate_config(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """启动时校验合并后的配置；关键块非法会立即抛出 ConfigValidationError。

    校验通过后会迁移旧版运行配置（移除废弃策略组/渠道键、把 legacy
    ``min_tier: Free`` 归一为 ``only_tier: Free``），并以默认模板深合并补齐所有
    当前配置块。只有内容确实发生变化时才原子写回，且绝不改写受版本控制的
    ``defaults/config.yaml`` 模板。
    """
    resolved_path = _resolve_config_path(config_path)
    exists = resolved_path.is_file()
    raw = _read_config_file(resolved_path) if exists else {}
    migrated, migration_changed = _migrate_legacy_config_mapping(raw)
    validated = validate_config_mapping(migrated)

    if (
        exists
        and not _is_default_template_path(resolved_path)
        and (migration_changed or validated != raw)
    ):
        content = yaml.dump(validated, allow_unicode=True, sort_keys=False)
        try:
            _atomic_write_text(resolved_path, content)
        except OSError as exc:
            logger.warning(
                "无法写回已迁移的配置文件 %s（%s）；继续使用内存中已校验的配置",
                resolved_path,
                exc,
            )
        else:
            invalidate_config_cache()
            logger.info("已迁移并写回旧版配置文件：%s", resolved_path)
    return validated


@dataclass(frozen=True)
class ModelDefaults:
    is_image_model: bool = False
    default_tools: tuple[str, ...] = ()
    generation_config_defaults: dict[str, object] = field(default_factory=dict)
    clear_generation_config_indexes: tuple[int, ...] = ()
    safety_settings: tuple[tuple[object, ...], ...] | None = None
    disable_safety_settings: bool = False
    drop_unsupported_params: bool = False
    specified_fields: frozenset[str] = frozenset()

    def generation_config_overrides(self) -> dict[str, object]:
        return dict(self.generation_config_defaults)

    def safety_settings_overrides(self) -> list[list[object]] | None:
        if self.safety_settings is None:
            return None
        return [list(item) for item in self.safety_settings]

    def merged(self, other: ModelDefaults) -> ModelDefaults:
        merged_generation = dict(self.generation_config_defaults)
        merged_generation.update(other.generation_config_defaults)
        merged_tools = self.default_tools + tuple(
            tool for tool in other.default_tools if tool not in self.default_tools
        )
        merged_indexes = self.clear_generation_config_indexes + tuple(
            idx
            for idx in other.clear_generation_config_indexes
            if idx not in self.clear_generation_config_indexes
        )
        return ModelDefaults(
            is_image_model=self.is_image_model or other.is_image_model,
            default_tools=merged_tools,
            generation_config_defaults=merged_generation,
            clear_generation_config_indexes=merged_indexes,
            safety_settings=other.safety_settings
            if other.safety_settings is not None
            else self.safety_settings,
            disable_safety_settings=self.disable_safety_settings
            or other.disable_safety_settings,
            drop_unsupported_params=(
                other.drop_unsupported_params
                if "drop_unsupported_params" in other.specified_fields
                else self.drop_unsupported_params
            ),
            specified_fields=self.specified_fields | other.specified_fields,
        )

    def overridden(self, other: ModelDefaults) -> ModelDefaults:
        merged_generation = dict(self.generation_config_defaults)
        if "generation_config_defaults" in other.specified_fields:
            merged_generation.update(other.generation_config_defaults)
        return ModelDefaults(
            is_image_model=other.is_image_model
            if "is_image_model" in other.specified_fields
            else self.is_image_model,
            default_tools=other.default_tools
            if "default_tools" in other.specified_fields
            else self.default_tools,
            generation_config_defaults=merged_generation,
            clear_generation_config_indexes=(
                other.clear_generation_config_indexes
                if "clear_generation_config_indexes" in other.specified_fields
                else self.clear_generation_config_indexes
            ),
            safety_settings=other.safety_settings
            if "safety_settings" in other.specified_fields
            else self.safety_settings,
            disable_safety_settings=(
                other.disable_safety_settings
                if "disable_safety_settings" in other.specified_fields
                else self.disable_safety_settings
            ),
            drop_unsupported_params=(
                other.drop_unsupported_params
                if "drop_unsupported_params" in other.specified_fields
                else self.drop_unsupported_params
            ),
            specified_fields=self.specified_fields | other.specified_fields,
        )


@dataclass(frozen=True)
class ModelProfile:
    name: str
    exact: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    defaults: ModelDefaults = field(default_factory=ModelDefaults)

    def matches(self, model: str) -> bool:
        return bool(
            (self.exact and model in self.exact)
            or any(model.startswith(prefix) for prefix in self.prefixes)
            or any(token in model for token in self.contains)
        )


def normalize_model_name(model: str) -> str:
    """Normalize model string (strip, lowercase, remove 'models/' prefix)."""
    return model.strip().removeprefix("models/").lower()


_normalize_model_name = normalize_model_name


def _coerce_thinking_level(value: object) -> ThinkingLevel:
    if isinstance(value, ThinkingLevel):
        return value
    if isinstance(value, int):
        return ThinkingLevel(value)
    return ThinkingLevel[str(value).strip().upper()]


def _coerce_media_resolution(value: object) -> int:
    if isinstance(value, MediaResolution):
        return int(value)
    if isinstance(value, int):
        return value
    return int(MediaResolution[str(value).strip().upper()])


def _coerce_image_output_mode(value: object) -> AistudioImageOutputMode | list[int]:
    if isinstance(value, AistudioImageOutputMode):
        return value
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        output_type = ImageOutputType(
            int(value.get("output_type", ImageOutputType.IMAGE))
        )
        include_text = bool(value.get("include_text"))
        return AistudioImageOutputMode(
            output_type=output_type, include_text=include_text
        )
    label = str(value).strip().lower()
    if label == "text_and_image":
        return AistudioImageOutputMode.text_and_image()
    if label == "image_only":
        return AistudioImageOutputMode.image_only()
    raise ValueError(f"Unsupported image_output_mode: {value!r}")


def _coerce_generation_defaults(raw: dict[str, object] | None) -> dict[str, object]:
    raw = raw or {}
    coerced: dict[str, object] = {}
    for key, value in raw.items():
        if key == "thinking_config" and isinstance(value, dict):
            raw_level = value.get(
                "level", value.get("thinkingLevel", value.get("thinking_level"))
            )
            raw_budget = value.get(
                "budget", value.get("thinkingBudget", value.get("thinking_budget"))
            )
            raw_mode = value.get("mode")
            level = _coerce_thinking_level(raw_level) if raw_level is not None else None
            budget = (
                int(str(raw_budget))
                if raw_budget is not None and not isinstance(raw_budget, bool)
                else None
            )
            if level is not None or budget is not None or raw_mode is not None:
                mode = (
                    int(str(raw_mode))
                    if raw_mode is not None and not isinstance(raw_mode, bool)
                    else 1
                )
                coerced[key] = AistudioThinkingConfig(
                    level=level, mode=mode, budget=budget
                ).to_wire()
            else:
                coerced[key] = None
        elif key == "image_output_mode":
            coerced[key] = (
                _coerce_image_output_mode(value) if value is not None else None
            )
        elif key == "media_resolution":
            coerced[key] = (
                _coerce_media_resolution(value) if value is not None else None
            )
        else:
            coerced[key] = value
    return coerced


def _normalize_safety_category(name: object) -> str:
    return " ".join(
        str(name).strip().lower().replace("_", " ").replace("-", " ").split()
    )


def _coerce_safety_threshold(value: object) -> int:
    threshold = int(str(value))
    if threshold < 1 or threshold > 5:
        raise ValueError(f"Unsupported safety threshold: {value!r}")
    return threshold


def _coerce_safety_settings(raw: object) -> tuple[tuple[object, ...], ...] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        normalized = {
            _normalize_safety_category(name): _coerce_safety_threshold(value)
            for name, value in raw.items()
        }
        supported = {name for name, _ in _SAFETY_CATEGORY_ORDER}
        unknown = [name for name in normalized if name not in supported]
        if unknown:
            raise ValueError(f"Unsupported safety categories: {', '.join(unknown)}")
        return tuple(
            (None, None, category, normalized[name])
            for name, category in _SAFETY_CATEGORY_ORDER
            if name in normalized
        )
    if isinstance(raw, (list, tuple)):
        settings = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) < 4:
                raise ValueError(f"Unsupported safety setting entry: {item!r}")
            settings.append(
                (None, None, int(item[2]), _coerce_safety_threshold(item[3]))
            )
        return tuple(settings)
    raise ValueError(f"Unsupported safety_settings: {raw!r}")


def _defaults_from_mapping(raw: dict[str, object] | None) -> ModelDefaults:
    raw_dict = raw or {}
    raw_default_tools = raw_dict.get("default_tools")
    default_tools_list = (
        raw_default_tools if isinstance(raw_default_tools, (list, tuple)) else ()
    )
    raw_gen_defaults = raw_dict.get("generation_config_defaults")
    gen_defaults = raw_gen_defaults if isinstance(raw_gen_defaults, dict) else None
    raw_clear_indexes = raw_dict.get("clear_generation_config_indexes")
    clear_indexes_list = (
        raw_clear_indexes if isinstance(raw_clear_indexes, (list, tuple)) else ()
    )
    return ModelDefaults(
        is_image_model=bool(raw_dict.get("is_image_model", False)),
        default_tools=tuple(str(x) for x in default_tools_list),
        generation_config_defaults=_coerce_generation_defaults(gen_defaults),
        clear_generation_config_indexes=tuple(int(str(v)) for v in clear_indexes_list),
        safety_settings=_coerce_safety_settings(raw_dict.get("safety_settings")),
        disable_safety_settings=bool(raw_dict.get("disable_safety_settings", False)),
        drop_unsupported_params=bool(raw_dict.get("drop_unsupported_params", False)),
        specified_fields=frozenset(raw_dict.keys()),
    )


def _profile_from_mapping(raw: dict[str, object]) -> ModelProfile:
    raw_match = raw.get("match")
    match: dict[str, object] = raw_match if isinstance(raw_match, dict) else {}
    raw_exact = match.get("exact")
    exact_list = raw_exact if isinstance(raw_exact, (list, tuple)) else ()
    raw_prefixes = match.get("prefixes")
    prefixes_list = raw_prefixes if isinstance(raw_prefixes, (list, tuple)) else ()
    raw_contains = match.get("contains")
    contains_list = raw_contains if isinstance(raw_contains, (list, tuple)) else ()
    return ModelProfile(
        name=str(raw.get("name", "unnamed")),
        exact=tuple(_normalize_model_name(str(v)) for v in exact_list),
        prefixes=tuple(_normalize_model_name(str(v)) for v in prefixes_list),
        contains=tuple(str(v).strip().lower() for v in contains_list),
        defaults=_defaults_from_mapping(raw),
    )


def _coerce_retry_same_delay(value: object) -> float | None:
    """Parse a 429 same-account retry delay and clamp it to 0..30 seconds."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return min(_RETRY_SAME_DELAY_MAX_SEC, max(0.0, parsed))


def _normalize_switch_backoff_sec(raw: object) -> float:
    """Coerce ``features.routing.switch_backoff_sec`` into 0..30 seconds.

    ``int``/``float``/numeric ``str`` are accepted; booleans, non-numeric
    values and NaN fall back to :data:`DEFAULT_SWITCH_BACKOFF_SEC`, while
    negative/oversized numbers are clamped into
    :data:`MIN_SWITCH_BACKOFF_SEC`..:data:`MAX_SWITCH_BACKOFF_SEC`.
    """
    if raw is None or isinstance(raw, bool):
        logger.warning(
            "配置 features.routing.switch_backoff_sec 非法（%r），回退默认 %s",
            raw,
            DEFAULT_SWITCH_BACKOFF_SEC,
        )
        return DEFAULT_SWITCH_BACKOFF_SEC
    try:
        parsed = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "配置 features.routing.switch_backoff_sec 非法（%r），回退默认 %s",
            raw,
            DEFAULT_SWITCH_BACKOFF_SEC,
        )
        return DEFAULT_SWITCH_BACKOFF_SEC
    if parsed != parsed:  # NaN
        logger.warning(
            "配置 features.routing.switch_backoff_sec 非法（%r），回退默认 %s",
            raw,
            DEFAULT_SWITCH_BACKOFF_SEC,
        )
        return DEFAULT_SWITCH_BACKOFF_SEC
    return min(MAX_SWITCH_BACKOFF_SEC, max(MIN_SWITCH_BACKOFF_SEC, parsed))


def _routing_defaults() -> dict[str, object]:
    return {
        "max_switches": 2,
        "retry_same_delay_sec": _RETRY_SAME_DELAY_DEFAULT_SEC,
        "switch_backoff_sec": DEFAULT_SWITCH_BACKOFF_SEC,
    }


def _normalize_retry_same_delay(raw_routing: dict[str, object]) -> float:
    """Normalize the persisted config value without applying env overrides.

    Runtime-only overrides must never dirty the config file during startup
    migration; :func:`_resolve_retry_same_delay` layers the env value on top
    when the routing settings are actually consumed.
    """
    raw_delay = raw_routing.get("retry_same_delay_sec", _RETRY_SAME_DELAY_DEFAULT_SEC)
    delay = _coerce_retry_same_delay(raw_delay)
    if delay is None:
        logger.warning(
            "配置 features.routing.retry_same_delay_sec 非法（%r），回退默认 %s",
            raw_delay,
            _RETRY_SAME_DELAY_DEFAULT_SEC,
        )
        delay = _RETRY_SAME_DELAY_DEFAULT_SEC
    return delay


def _resolve_retry_same_delay(raw_routing: dict[str, object]) -> float:
    """Resolve ``features.routing.retry_same_delay_sec`` with env override.

    Invalid YAML/env values never raise: they fall back to the production
    default so a typo cannot crash startup or silently disable the delay.
    """
    delay = _normalize_retry_same_delay(raw_routing)
    env_value = os.getenv(_RETRY_SAME_DELAY_ENV)
    if env_value:
        env_delay = _coerce_retry_same_delay(env_value)
        if env_delay is None:
            logger.warning(
                "环境变量 %s 非法（%r），忽略并使用配置值 %s",
                _RETRY_SAME_DELAY_ENV,
                env_value,
                delay,
            )
        else:
            delay = env_delay
    return delay


@lru_cache(maxsize=1)
def _default_config_template() -> dict[str, object]:
    """Load the tracked ``defaults/config.yaml`` template (single source of truth)."""
    try:
        template_path = resolve_default_config_template()
    except FileNotFoundError as exc:
        raise ConfigValidationError(f"配置校验失败：{exc}") from exc
    try:
        loaded = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigValidationError(
            f"配置校验失败：无法读取默认配置模板 {template_path}：{exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigValidationError(
            f"配置校验失败：默认配置模板 {template_path} YAML 语法错误：{exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ConfigValidationError(
            "配置校验失败：默认配置模板 "
            f"{template_path} 顶层必须为字典，当前类型为 {_describe_type(loaded)}"
        )
    return cast("dict[str, object]", loaded)


def _default_config() -> dict[str, object]:
    """Return an independent deep copy of the tracked default configuration.

    Every caller receives its own copy so mutation cannot leak into the cached
    template or into another caller.
    """
    return copy.deepcopy(_default_config_template())


_CONFIG_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_API_KEYS_CACHE: dict[str, tuple[float, frozenset[str], list[dict[str, str]]]] = {}


def invalidate_config_cache() -> None:
    """Clear in-memory configuration, template and API keys cache."""
    _CONFIG_CACHE.clear()
    _API_KEYS_CACHE.clear()
    _default_config_template.cache_clear()
    _compiled_profiles.cache_clear()
    _compiled_model_overrides.cache_clear()


def _atomic_write_text(path: Path, content: str) -> None:
    """同目录临时文件 + fsync + os.replace 原子写回。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)  # noqa: PTH105 - 原子写回要求使用 os.replace
    except BaseException:
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink(missing_ok=True)
        raise


def write_config_text(
    content: str,
    config_path: str | os.PathLike[str] | None = None,
) -> Path:
    """校验合并配置后原子写回配置文件，并刷新缓存。"""
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ConfigValidationError(f"配置校验失败：YAML 语法错误：{exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigValidationError("配置校验失败：YAML 顶层必须为字典结构")
    for block_name in ("logging", "security"):
        if block_name in parsed and not isinstance(parsed[block_name], dict):
            raise ConfigValidationError(
                f"配置校验失败：{block_name} 必须为字典，"
                f"当前类型为 {_describe_type(parsed[block_name])}"
            )
    validate_config_mapping(parsed)
    resolved_path = _resolve_config_path(config_path)
    _atomic_write_text(resolved_path, content)
    invalidate_config_cache()
    return resolved_path


def _load_yaml_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return _default_config()

    path_key = str(config_path.resolve())
    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    cached = _CONFIG_CACHE.get(path_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    else:
        loaded, _ = _migrate_legacy_config_mapping(loaded)
    merged = _deep_merge(_default_config(), loaded)
    assert isinstance(merged, dict)
    merged = _apply_noncritical_fallbacks(merged)
    _CONFIG_CACHE[path_key] = (mtime, merged)
    return merged


@lru_cache(maxsize=8)
def _compiled_profiles(config_path: str) -> tuple[ModelProfile, ...]:
    config = _load_yaml_config(Path(config_path))
    raw_md = config.get("model_defaults")
    model_defaults = raw_md if isinstance(raw_md, dict) else {}
    raw_profiles = model_defaults.get("profiles")
    profiles_list = raw_profiles if isinstance(raw_profiles, list) else []
    return tuple(
        _profile_from_mapping(item) for item in profiles_list if isinstance(item, dict)
    )


@lru_cache(maxsize=8)
def _compiled_model_overrides(config_path: str) -> dict[str, ModelDefaults]:
    config = _load_yaml_config(Path(config_path))
    raw_md = config.get("model_defaults")
    model_defaults = raw_md if isinstance(raw_md, dict) else {}
    raw_models = model_defaults.get("models")
    models_dict = raw_models if isinstance(raw_models, dict) else {}
    overrides: dict[str, ModelDefaults] = {}
    for model_name, raw_defaults in models_dict.items():
        overrides[_normalize_model_name(str(model_name))] = _defaults_from_mapping(
            raw_defaults if isinstance(raw_defaults, dict) else {}
        )
    return overrides


def resolve_model_defaults(
    model: str, *, config_path: str | os.PathLike[str] | None = None
) -> ModelDefaults:
    normalized = _normalize_model_name(model)
    resolved_path = str(_resolve_config_path(config_path))
    config = _load_yaml_config(Path(resolved_path))
    raw_md = config.get("model_defaults")
    global_drop = False
    if isinstance(raw_md, dict) and "drop_unsupported_params" in raw_md:
        global_drop = bool(raw_md["drop_unsupported_params"])
    elif "drop_unsupported_params" in config:
        global_drop = bool(config["drop_unsupported_params"])

    profiles = _compiled_profiles(resolved_path)
    resolved = ModelDefaults(drop_unsupported_params=global_drop)
    for profile in profiles:
        if profile.matches(normalized):
            resolved = resolved.merged(profile.defaults)
    override = _compiled_model_overrides(resolved_path).get(normalized)
    if override is not None:
        resolved = resolved.overridden(override)

    if resolved.is_image_model:
        allowed = {"google_search", "image_search", "google_search_and_image_search"}
        filtered = tuple(t for t in resolved.default_tools if t in allowed)
        if filtered != resolved.default_tools:
            from dataclasses import replace

            resolved = replace(resolved, default_tools=filtered)

    return resolved


def get_configured_api_key_items(
    config_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, str]]:
    """获取 config.yaml 中配置的结构化 API Key 列表（含备注名与创建时间）。"""
    resolved_path = _resolve_config_path(config_path)
    path_key = str(resolved_path.resolve())
    try:
        mtime = resolved_path.stat().st_mtime if resolved_path.exists() else 0.0
    except OSError:
        mtime = 0.0

    cached = _API_KEYS_CACHE.get(path_key)
    if cached is not None and cached[0] == mtime:
        return list(cached[2])

    config = _load_yaml_config(resolved_path)
    raw_keys = config.get("api_keys")
    items: list[dict[str, str]] = []
    if isinstance(raw_keys, list):
        for k in raw_keys:
            if isinstance(k, dict):
                key_val = str(k.get("key") or "").strip()
                name_val = str(k.get("name") or "API Key").strip()
                created_val = str(k.get("created_at") or "").strip()
                if key_val:
                    items.append(
                        {"key": key_val, "name": name_val, "created_at": created_val}
                    )
            elif isinstance(k, str) and k.strip():
                items.append({"key": k.strip(), "name": "API Key", "created_at": ""})
    elif isinstance(raw_keys, str):
        for line in raw_keys.splitlines():
            for part in line.split(","):
                s = part.strip()
                if s:
                    items.append({"key": s, "name": "API Key", "created_at": ""})

    keys_set = frozenset(item["key"] for item in items if item.get("key"))
    _API_KEYS_CACHE[path_key] = (mtime, keys_set, items)
    return items


def get_configured_api_keys(
    config_path: str | os.PathLike[str] | None = None,
) -> frozenset[str]:
    """获取 config.yaml 中配置的所有有效 API Key 集合。"""
    resolved_path = _resolve_config_path(config_path)
    path_key = str(resolved_path.resolve())
    try:
        mtime = resolved_path.stat().st_mtime if resolved_path.exists() else 0.0
    except OSError:
        mtime = 0.0

    cached = _API_KEYS_CACHE.get(path_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    items = get_configured_api_key_items(config_path)
    return frozenset(item["key"] for item in items if item.get("key"))


def get_configured_logging_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """获取 config.yaml 中配置的 logging 设置。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_logging = config.get("logging")
    if isinstance(raw_logging, dict):
        return {
            "level": str(raw_logging.get("level") or "INFO").strip().upper(),
            "dump_requests": bool(raw_logging.get("dump_requests", False)),
            "dump_to_file": bool(raw_logging.get("dump_to_file", False)),
            "dump_dir": str(raw_logging.get("dump_dir") or "").strip(),
        }
    return {
        "level": "INFO",
        "dump_requests": False,
        "dump_to_file": False,
        "dump_dir": "",
    }


def update_configured_logging_settings(
    level: str | None = None,
    dump_requests: bool | None = None,
    dump_to_file: bool | None = None,
    dump_dir: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """更新 config.yaml 中的 logging 设置并刷新缓存。"""
    resolved_path = _resolve_config_path(config_path)
    content = (
        resolved_path.read_text(encoding="utf-8") if resolved_path.exists() else ""
    )
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        parsed = {}

    raw_logging = parsed.get("logging")
    logging_dict = dict(raw_logging) if isinstance(raw_logging, dict) else {}
    if level is not None:
        logging_dict["level"] = level.strip().upper()
    if dump_requests is not None:
        logging_dict["dump_requests"] = dump_requests
    if dump_to_file is not None:
        logging_dict["dump_to_file"] = dump_to_file
    if dump_dir is not None:
        logging_dict["dump_dir"] = dump_dir

    parsed["logging"] = logging_dict
    write_config_text(
        yaml.dump(parsed, allow_unicode=True, sort_keys=False),
        config_path=config_path,
    )
    return get_configured_logging_settings(config_path)


# ─── 订阅层级每日软额度 (Tier Quotas) 与预设方案 (Presets) ───
def get_configured_tier_quotas(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """获取 config.yaml 中配置的订阅层级软额度规则，若缺失则安全回退至系统默认配置。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_quotas = config.get("tier_quotas")
    if isinstance(raw_quotas, dict):
        return raw_quotas
    def_quotas = _default_config().get("tier_quotas")
    if isinstance(def_quotas, dict):
        return def_quotas
    return {}


def resolve_tier_quota(
    tier: str | None,
    model: str | None,
    config_path: str | os.PathLike[str] | None = None,
) -> int:
    """计算指定账号层级在目标模型上的每日软额度限制（仅用于轮询打分权重，超额绝不禁用账号）。"""
    normalized_model = _normalize_model_name(model) if model else ""
    quotas = get_configured_tier_quotas(config_path)

    # 1. 精确模型覆盖 overrides (优先级最高)
    raw_overrides = quotas.get("overrides")
    if isinstance(raw_overrides, dict):
        for m_key, val in raw_overrides.items():
            if _normalize_model_name(str(m_key)) == normalized_model:
                try:
                    return int(str(val))
                except (ValueError, TypeError):
                    pass

    # 2. 匹配对应层级规则
    clean_tier = (tier or "Free").strip()
    if clean_tier not in quotas:
        matched_key = None
        for k in quotas:
            if k.lower() == clean_tier.lower():
                matched_key = k
                break
        if matched_key:
            clean_tier = matched_key
        elif "Free" in quotas:
            clean_tier = "Free"
        else:
            clean_tier = next(iter(quotas), "Free")

    tier_rules = quotas.get(clean_tier)
    if not isinstance(tier_rules, dict):
        return 50

    default_limit = int(str(tier_rules.get("default_limit", 50)))
    if not normalized_model:
        return default_limit

    for rule_key, rule_data in tier_rules.items():
        if rule_key == "default_limit" or not isinstance(rule_data, dict):
            continue
        raw_match = rule_data.get("match")
        if isinstance(raw_match, dict):
            contains = [
                str(c).lower()
                for c in (raw_match.get("contains") or [])
                if isinstance(c, str)
            ]
            prefixes = [
                str(p).lower()
                for p in (raw_match.get("prefixes") or [])
                if isinstance(p, str)
            ]
            exact = [
                _normalize_model_name(str(e))
                for e in (raw_match.get("exact") or [])
                if isinstance(e, str)
            ]
            if (
                (exact and normalized_model in exact)
                or any(normalized_model.startswith(p) for p in prefixes)
                or any(c in normalized_model for c in contains)
            ):
                return int(str(rule_data.get("daily_limit", default_limit)))

    return default_limit


def get_configured_presets(
    config_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, object]]:
    """获取 config.yaml 中配置的预设方案列表。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_presets = config.get("account_presets")
    if isinstance(raw_presets, list):
        return [p for p in raw_presets if isinstance(p, dict)]
    def_presets = _default_config().get("account_presets")
    if isinstance(def_presets, list):
        return [p for p in def_presets if isinstance(p, dict)]
    return []


def get_configured_rotator_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """获取 config.yaml 中配置的 rotator 重试与冷却时间，若缺失安全回退至系统默认配置。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_rotator = config.get("rotator")
    if isinstance(raw_rotator, dict):
        return raw_rotator
    def_rotator = _default_config().get("rotator")
    if isinstance(def_rotator, dict):
        return def_rotator
    return {}


def get_configured_paid_model_rules(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """获取 config.yaml 中配置的 paid_model_rules 付费模型判定与准入规则，若缺失安全回退至系统默认配置。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_rules = config.get("paid_model_rules")
    if isinstance(raw_rules, dict):
        return raw_rules
    def_rules = _default_config().get("paid_model_rules")
    if isinstance(def_rules, dict):
        return def_rules
    return {}


def get_features_config(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Return the merged ``features`` block (hot-reloadable via mtime cache)."""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_features = config.get("features")
    if isinstance(raw_features, dict):
        return raw_features
    defaults = _default_config().get("features")
    return dict(defaults) if isinstance(defaults, dict) else {}


def get_configured_formats(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    formats = get_features_config(config_path).get("formats")
    return dict(formats) if isinstance(formats, dict) else {}


def get_configured_routing_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """返回 features.routing 配置；缺失/非法时回退 max_switches=2。

    ``retry_same_delay_sec`` 默认 2.0 秒，限制在 0..30；环境变量
    ``AISTUDIO_RETRY_SAME_DELAY_SEC`` 可覆盖（非法值忽略，不抛异常）。
    ``switch_backoff_sec`` 默认 4.0 秒，同样限制在 0..30。
    """
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_features = config.get("features")
    raw_routing = (
        raw_features.get("routing") if isinstance(raw_features, dict) else None
    )
    if not isinstance(raw_routing, dict):
        logger.warning(
            "配置 features.routing 非法（%r），回退默认 max_switches=2",
            _describe_type(raw_routing),
        )
        return _routing_defaults()
    raw_switches = raw_routing.get("max_switches", 2)
    if (
        isinstance(raw_switches, bool)
        or not isinstance(raw_switches, int)
        or raw_switches < 0
    ):
        logger.warning(
            "配置 features.routing.max_switches 非法（%r），回退默认 2",
            raw_switches,
        )
        raw_switches = 2
    return {
        "max_switches": raw_switches,
        "retry_same_delay_sec": _resolve_retry_same_delay(raw_routing),
        "switch_backoff_sec": _normalize_switch_backoff_sec(
            raw_routing.get("switch_backoff_sec", DEFAULT_SWITCH_BACKOFF_SEC)
        ),
    }


def get_configured_keepalive_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """返回 features.keepalive 配置；缺失/非法时回退 24h auth/metadata 默认值。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_features = config.get("features")
    raw_keepalive = (
        raw_features.get("keepalive") if isinstance(raw_features, dict) else None
    )
    if not isinstance(raw_keepalive, dict):
        logger.warning(
            "配置 features.keepalive 非法（%r），回退默认值",
            _describe_type(raw_keepalive),
        )
        return {
            **_KEEPALIVE_DEFAULT_BOOLS,
            **_KEEPALIVE_DEFAULT_FLOATS,
        }
    return dict(raw_keepalive)


def get_configured_model_discovery_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, int]:
    """返回 features.model_discovery 刷新节奏；非法值回退 24h/10min 默认。

    ``live_refresh_interval_sec`` 只用于 ``last_source == "live_page"`` 的成功
    刷新；其他来源（fallback/snapshot/live_http）走较短的
    ``fallback_refresh_interval_sec`` 重试。
    """
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_features = config.get("features")
    raw_discovery = (
        raw_features.get("model_discovery") if isinstance(raw_features, dict) else None
    )
    settings: dict[str, int] = {}
    if not isinstance(raw_discovery, dict):
        logger.warning(
            "配置 features.model_discovery 非法（%r），回退默认刷新节奏",
            _describe_type(raw_discovery),
        )
        raw_discovery = {}
    for key, default_value in _MODEL_DISCOVERY_DEFAULTS.items():
        value = raw_discovery.get(key, default_value)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            logger.warning(
                "配置 features.model_discovery.%s 非法（%r），回退默认值 %r",
                key,
                value,
                default_value,
            )
            value = default_value
        settings[key] = value
    return settings


def resolve_quota_scope(
    config_path: str | os.PathLike[str] | None = None,
) -> str:
    """D-01: ``account``（默认）或 ``account_surface``；非法值回退 account。"""
    rotator = get_configured_rotator_settings(config_path)
    raw_scope = rotator.get("quota_scope", "account")
    if raw_scope in ("account", "account_surface"):
        return str(raw_scope)
    logger.warning("配置 rotator.quota_scope 非法（%r），回退默认 account", raw_scope)
    return "account"


def get_configured_security_settings(
    config_path: str | os.PathLike[str] | None = None,
) -> bool:
    """获取 security.api_allow_anonymous；缺失或非法时返回 False。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    raw_security = config.get("security")
    if not isinstance(raw_security, dict):
        logger.warning(
            "配置 security 非法（%r），API 免密开关回退 false",
            _describe_type(raw_security),
        )
        return False
    allow_anonymous = raw_security.get("api_allow_anonymous", False)
    if isinstance(allow_anonymous, bool):
        return allow_anonymous
    logger.warning(
        "配置 security.api_allow_anonymous 非法（%r），回退 false", allow_anonymous
    )
    return False


def get_configured_instance_specs(
    config_path: str | os.PathLike[str] | None = None,
) -> list[dict[str, object]]:
    """Normalize ``instances`` into merged closed tier-interval specs.

    ``only_tier`` pins one exact tier; ``min_tier`` alone opens a paid
    ``min..Ultra_20X`` interval; ``max_tier`` alone defaults the paid lower
    bound to Pro (Free max stays Free); min+max form a closed interval.  Free
    selectors always normalize to ``only=Free,min=Free,max=Free`` so duplicate
    specs merge counts, and unknown/non-string selectors fail loudly.
    """
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    return _normalize_instance_specs(config.get("instances"))


def get_configured_max_contexts(
    config_path: str | os.PathLike[str] | None = None,
) -> int:
    """全局实例/Context 上限；缺省为 instances count 之和，上限 16。"""
    resolved_path = _resolve_config_path(config_path)
    config = _load_yaml_config(resolved_path)
    specs = _normalize_instance_specs(config.get("instances"))
    return _normalize_max_contexts(config, specs)
