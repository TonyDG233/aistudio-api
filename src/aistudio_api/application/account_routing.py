"""账号级路由覆盖：归一化与能力收窄。

账号级路由只能收窄组/渠道给出的能力集合，不能新增能力；缺省时跟随组。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

_ALLOWED_SURFACES = frozenset({"chat", "build"})
_DEFAULT_WEIGHT = 1.0
_MIN_WEIGHT = 0.1
_MAX_WEIGHT = 100.0
_MODEL_PREFIX = "models/"


def _normalize_surface(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    surface = value.strip().lower()
    return surface if surface in _ALLOWED_SURFACES else None


def _normalize_surfaces(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    seen: set[str] = set()
    surfaces: list[str] = []
    for item in raw:
        surface = _normalize_surface(item)
        if surface is None or surface in seen:
            continue
        seen.add(surface)
        surfaces.append(surface)
    return tuple(surfaces)


def _normalize_weight(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return _DEFAULT_WEIGHT
    value = float(raw)
    if math.isnan(value):
        return _DEFAULT_WEIGHT
    return min(max(value, _MIN_WEIGHT), _MAX_WEIGHT)


def _normalize_model_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if name.startswith(_MODEL_PREFIX):
        name = name[len(_MODEL_PREFIX) :].strip()
    return name


def _normalize_model_surfaces(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    surfaces: dict[str, str] = {}
    for key, value in raw.items():
        model = _normalize_model_name(key)
        surface = _normalize_surface(value)
        if not model or surface is None:
            continue
        surfaces[model] = surface
    return surfaces


@dataclass(frozen=True)
class AccountRouting:
    """账号级路由覆盖；空 ``surfaces`` 表示跟随组。"""

    surfaces: tuple[str, ...] = ()
    weight: float = 1.0
    model_surfaces: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "surfaces": list(self.surfaces),
            "weight": self.weight,
            "model_surfaces": dict(self.model_surfaces),
        }

    @classmethod
    def from_dict(cls, raw: object) -> AccountRouting:
        if isinstance(raw, cls):
            raw = raw.to_dict()
        if not isinstance(raw, Mapping):
            return cls()
        return cls(
            surfaces=_normalize_surfaces(raw.get("surfaces")),
            weight=_normalize_weight(raw.get("weight")),
            model_surfaces=_normalize_model_surfaces(raw.get("model_surfaces")),
        )


def normalize_account_routing(raw: object) -> AccountRouting:
    """把任意存档/请求输入归一化为合法的账号级路由覆盖。"""
    return AccountRouting.from_dict(raw)


def apply_account_routing(
    capable_surfaces: Iterable[str],
    routing: AccountRouting | None,
    model: str,
) -> tuple[str, ...]:
    """在能力集合上应用账号级覆盖，只收窄、不新增，并保持原顺序。"""
    capable = tuple(capable_surfaces)
    if routing is None:
        return capable

    normalized = AccountRouting.from_dict(routing)
    selected = capable
    if normalized.surfaces:
        allowed = set(normalized.surfaces)
        selected = tuple(surface for surface in capable if surface in allowed)

    pin = normalized.model_surfaces.get(_normalize_model_name(model))
    if pin is not None:
        selected = tuple(surface for surface in selected if surface == pin)
    return selected
