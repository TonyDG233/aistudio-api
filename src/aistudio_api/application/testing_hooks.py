"""Deterministic test-only fault injection.

Production behavior is unchanged unless ``AISTUDIO_TEST_HOOKS=1``. The hook
raises the same ``UsageLimitExceeded`` as an upstream 429, so the normal
rate-limit switch path runs without consuming real quota.
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()
_forced_429_count = 0


def reset_test_hooks() -> None:
    global _forced_429_count
    with _lock:
        _forced_429_count = 0


def _enabled() -> bool:
    return os.environ.get("AISTUDIO_TEST_HOOKS", "").strip() == "1"


def _matches(target: str, actual: str) -> bool:
    if not target:
        return True
    wanted = target.removeprefix("models/")
    value = actual.removeprefix("models/")
    return wanted == value or wanted in value


def forced_429(
    *,
    account_id: str | None,
    model: str,
    surface: str,
) -> bool:
    """Return True once per configured count for the matching account/model."""
    global _forced_429_count
    if not _enabled():
        return False
    target_account = os.environ.get("AISTUDIO_TEST_FORCE_429_ACCOUNT", "").strip()
    target_model = os.environ.get("AISTUDIO_TEST_FORCE_429_MODEL", "").strip()
    target_surface = os.environ.get("AISTUDIO_TEST_FORCE_429_SURFACE", "").strip()
    if target_account and target_account != (account_id or ""):
        return False
    if not _matches(target_model, model):
        return False
    if target_surface and target_surface != surface:
        return False
    try:
        limit = max(0, int(os.environ.get("AISTUDIO_TEST_FORCE_429_COUNT", "1")))
    except ValueError:
        limit = 1
    with _lock:
        if _forced_429_count >= limit:
            return False
        _forced_429_count += 1
        return True


__all__ = ["forced_429", "reset_test_hooks"]
