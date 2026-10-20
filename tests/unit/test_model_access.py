"""Tests for Paid model routing metadata."""

from __future__ import annotations

import pytest

from aistudio_api.application.model_access import (
    clear_paid_models,
    is_paid_model,
    set_paid_models,
)
from aistudio_api.application.surface_orchestrator import (
    is_chat_only_model,
    model_account_requirement,
    model_requires_paid_account,
    surface_capability,
)
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService


def _restore_fallback_paid() -> None:
    from aistudio_api.infrastructure.gateway.model_discovery import _FALLBACK_MODELS

    set_paid_models(_FALLBACK_MODELS)


@pytest.fixture(autouse=True)
def _restore_paid_cache():
    _restore_fallback_paid()
    yield
    _restore_fallback_paid()


def test_paid_models_are_chat_only_and_require_paid_account() -> None:
    clear_paid_models()
    set_paid_models(
        [
            {"id": "models/gemini-3-flash-preview", "paid": True},
            {"id": "models/gemini-3.8-flash", "paid": False},
        ]
    )

    assert is_paid_model("gemini-3-flash-preview") is True
    assert is_paid_model("pg/gemini-3-flash-preview") is True
    assert is_paid_model("models/gemini-3.8-flash") is False

    assert is_chat_only_model("gemini-3-flash-preview") is True
    assert is_chat_only_model("build/gemini-3-flash-preview") is True
    assert is_chat_only_model("gemini-3.8-flash") is False
    assert model_requires_paid_account("gemini-3-flash-preview") is True
    assert model_account_requirement("gemini-3-flash-preview") == "paid"
    assert model_requires_paid_account("gemini-3.1-pro-preview") is False
    assert model_account_requirement("gemini-3.1-pro-preview") == "any"
    assert surface_capability("gemini-3-flash-preview", ("chat", "build")) == ("chat",)


def test_discovery_updates_downgrade_upgrade_and_preserves_absent_models() -> None:
    clear_paid_models()
    # gimini-4-argon is in the cold-start defaults.
    assert is_paid_model("gemini-4-argon") is True

    set_paid_models(
        [
            {"id": "models/gemini-4-argon", "paid": False},  # downgraded
            {"id": "models/new-paid-model", "paid": True},  # upgraded
        ]
    )
    assert is_paid_model("gemini-4-argon") is False
    assert is_paid_model("new-paid-model") is True
    # Absent from a partial snapshot: keep conservative last-known status.
    assert is_paid_model("gemini-3-flash-preview") is True


def test_snapshot_restores_status_and_applies_downgrade(tmp_path, monkeypatch) -> None:
    cache_file = tmp_path / "models_cache.json"
    monkeypatch.setattr(
        "aistudio_api.config.settings.model_cache_file", str(cache_file)
    )
    clear_paid_models()
    service = ModelDiscoveryService()
    service._save_snapshot(
        [
            {"id": "models/gemini-4-argon", "paid": False},
            {"id": "models/new-paid-model", "paid": True},
        ]
    )

    clear_paid_models()
    restored = ModelDiscoveryService()
    assert restored._cached_models
    assert is_paid_model("gemini-4-argon") is False
    assert is_paid_model("new-paid-model") is True


def test_fallback_paid_models_allow_catalog_projection_without_discovery() -> None:
    assert is_chat_only_model("gemini-4-argon") is True
    assert is_chat_only_model("gemini-3-flash-preview") is True
    assert is_chat_only_model("gemini-3.1-pro-preview") is False
    assert is_chat_only_model("gemini-3.8-flash") is False
