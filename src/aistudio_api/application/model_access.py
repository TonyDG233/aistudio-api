"""Process-local model access metadata (Paid/Pro-only markers)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from threading import RLock

_CHANNEL_PREFIXES = {"pg", "chat", "playground", "build", "official"}
# Known Paid models. Discovery may add more; these keep routing safe before the
# first ListModels request completes.
_DEFAULT_PAID_MODELS = {
    "argon",
    "argon-2-preview",
    "gemini-4-argon",
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "veo-3.1-generate-preview",
}
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


__all__ = [
    "clear_paid_models",
    "is_paid_model",
    "normalize_model_key",
    "set_paid_models",
]
