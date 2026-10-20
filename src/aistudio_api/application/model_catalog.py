"""Model-list projection for WP5 v3.

Single-channel deployments keep returning bare model IDs. Multi-channel
deployments return one canonical prefix per real channel:

- ``pg/`` for the chat channel (``chat``/``playground`` are input aliases only)
- ``build/`` for the build channel

``official/`` is never listed before WP9 is implemented. A model appears at
most once per real channel; aliases never duplicate the whole list.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from aistudio_api.application.channel_registry import (
    ChannelSpec,
    build_registry,
    enabled_channels,
    normalize_channel,
)
from aistudio_api.application.surface_orchestrator import is_chat_only_model

_PREFIX_TO_CHANNEL = {
    "pg": "chat",
    "chat": "chat",
    "playground": "chat",
    "build": "build",
    "official": "official",
}
_CHANNEL_TO_PREFIX = {"chat": "pg", "build": "build", "official": "official"}


def clean_model_id(raw: object) -> str:
    text = str(raw or "").strip()
    return text.removeprefix("models/")


def split_catalog_prefix(model_id: str) -> tuple[str, str | None]:
    """Return ``(clean_model, canonical_channel)`` for a requested model ID."""
    raw = model_id.removeprefix("models/").strip()
    head, separator, tail = raw.partition("/")
    channel = _PREFIX_TO_CHANNEL.get(head.lower())
    if separator and tail and channel is not None:
        return tail, channel
    return raw, None


def catalog_prefix(channel: str) -> str:
    canonical = normalize_channel(channel) or channel
    return _CHANNEL_TO_PREFIX.get(canonical, canonical)


def _catalog_channels(registry: Mapping[str, ChannelSpec]) -> list[ChannelSpec]:
    # enabled_channels already requires enabled+implemented; official is not
    # implemented before WP9, so it cannot leak into this list.
    return [
        spec for spec in enabled_channels(registry) if spec.key in ("chat", "build")
    ]


def project_model_ids(
    models: Iterable[Mapping[str, object]],
    features: Mapping[str, object],
) -> list[str]:
    """Project discovered models into the public catalog IDs."""
    registry = build_registry(features)
    channels = _catalog_channels(registry)
    seen_models: list[str] = []
    paid_models: set[str] = set()
    for item in models:
        clean = clean_model_id(item.get("id") or item.get("name"))
        if clean and clean not in seen_models:
            seen_models.append(clean)
        if clean and item.get("paid") is True:
            paid_models.add(clean)

    if len(channels) <= 1:
        return seen_models

    projected: list[str] = []
    for clean in seen_models:
        for spec in channels:
            if spec.key == "build" and (
                clean in paid_models or is_chat_only_model(clean)
            ):
                continue
            candidate = f"{spec.prefix}/{clean}"
            if candidate not in projected:
                projected.append(candidate)
    return projected


def resolve_model_request(model_id: str) -> tuple[str, str | None]:
    """Strip a catalog prefix for discovery while preserving the channel hint."""
    clean, channel = split_catalog_prefix(model_id)
    return clean, channel


__all__ = [
    "catalog_prefix",
    "clean_model_id",
    "project_model_ids",
    "resolve_model_request",
    "split_catalog_prefix",
]
