"""Minimal model-list projection for the new single-instance architecture.

Bare model IDs stay the default API entry point. When the build surface is
enabled, canonical ``build/`` aliases are projected for models that can run
there. Paid/chat-only models are never projected into ``build``. Legacy
``pg``/``playground``/``chat`` catalog aliases are not emitted or parsed. No
channel registry, strategy groups, or leases are involved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from aistudio_api.domain.model_access import is_chat_only_model

_PREFIX_TO_SURFACE = {"build": "build"}
_SURFACE_TO_PREFIX = {"build": "build"}


def clean_model_id(raw: object) -> str:
    text = str(raw or "").strip()
    return text.removeprefix("models/")


def split_catalog_prefix(model_id: str) -> tuple[str, str | None]:
    """Return ``(clean_model, surface)`` for a requested model ID."""
    raw = model_id.removeprefix("models/").strip()
    head, separator, tail = raw.partition("/")
    surface = _PREFIX_TO_SURFACE.get(head.lower())
    if separator and tail and surface is not None:
        return tail, surface
    return raw, None


def catalog_prefix(surface: str) -> str:
    return _SURFACE_TO_PREFIX.get(surface, surface)


def project_model_ids(
    models: Iterable[Mapping[str, object]],
    *,
    enabled_surfaces: Iterable[str] = ("chat",),
) -> list[str]:
    """Project discovered models into public catalog IDs."""
    surfaces = [surface for surface in enabled_surfaces if surface in ("chat", "build")]
    seen: list[str] = []
    for item in models:
        clean = clean_model_id(item.get("id") or item.get("name"))
        if clean and clean not in seen:
            seen.append(clean)

    if "build" not in surfaces:
        return seen

    projected: list[str] = list(seen)
    for clean in seen:
        if is_chat_only_model(clean):
            continue
        candidate = f"{catalog_prefix('build')}/{clean}"
        if candidate not in projected:
            projected.append(candidate)
    return projected


def resolve_model_request(model_id: str) -> tuple[str, str | None]:
    """Strip a catalog prefix for discovery while preserving the surface hint."""
    return split_catalog_prefix(model_id)


__all__ = [
    "catalog_prefix",
    "clean_model_id",
    "project_model_ids",
    "resolve_model_request",
    "split_catalog_prefix",
]
