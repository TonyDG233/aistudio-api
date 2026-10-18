"""Unit tests for surface routing (AISTUDIO_SURFACE_OVERRIDE / tier-based)."""

from __future__ import annotations

import pytest

from aistudio_api.application.api_service_gemini import _resolve_surface
from aistudio_api.infrastructure.account import account_store as account_store_mod
from aistudio_api.infrastructure.gateway import model_defaults as model_defaults_mod


@pytest.fixture
def no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AISTUDIO_SURFACE_OVERRIDE", raising=False)


def test_override_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISTUDIO_SURFACE_OVERRIDE", "build")
    assert _resolve_surface("models/gemini-3.8-flash") == "build"


def test_override_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AISTUDIO_SURFACE_OVERRIDE", "CHAT")
    assert _resolve_surface("models/gemini-3.8-flash") == "chat"


def test_flag_off_defaults_chat(
    monkeypatch: pytest.MonkeyPatch, no_override: None
) -> None:
    monkeypatch.setattr(
        model_defaults_mod,
        "get_features_config",
        lambda *args, **kwargs: {"build_surface": False},
    )
    assert _resolve_surface("models/gemini-3.8-flash") == "chat"


def test_flag_on_tier_free_routes_build(
    monkeypatch: pytest.MonkeyPatch, no_override: None
) -> None:
    monkeypatch.setattr(
        model_defaults_mod,
        "get_features_config",
        lambda *args, **kwargs: {"build_surface": True},
    )

    class _Acc:
        tier = "free"

    monkeypatch.setattr(
        account_store_mod.AccountStore, "get_active_account", lambda self: _Acc()
    )
    assert _resolve_surface("models/gemini-3.8-flash") == "build"


def test_flag_on_tier_pro_routes_chat(
    monkeypatch: pytest.MonkeyPatch, no_override: None
) -> None:
    monkeypatch.setattr(
        model_defaults_mod,
        "get_features_config",
        lambda *args, **kwargs: {"build_surface": True},
    )

    class _Acc:
        tier = "pro"

    monkeypatch.setattr(
        account_store_mod.AccountStore, "get_active_account", lambda self: _Acc()
    )
    assert _resolve_surface("models/gemini-3.8-flash") == "chat"
