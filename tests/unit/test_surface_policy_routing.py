"""Routing tests for WP5: ``_resolve_surface`` adapter + streaming 4xx denials."""

from __future__ import annotations

import json
from typing import cast

import pytest

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.application import api_service_gemini as gemini_mod
from aistudio_api.application.api_service_gemini import (
    _build_gemini_streaming_response,
    _resolve_surface,
)
from aistudio_api.application.surface_orchestrator import SurfacePolicyError
from aistudio_api.infrastructure.account import account_store as account_store_mod
from aistudio_api.infrastructure.gateway import model_defaults as model_defaults_mod
from aistudio_api.infrastructure.gateway.client import AIStudioClient


class _Account:
    def __init__(self, tier: str | None, email: str | None = None) -> None:
        self.tier = tier
        self.email = email


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AISTUDIO_SURFACE_OVERRIDE", raising=False)


def _patch_features(monkeypatch: pytest.MonkeyPatch, features: dict) -> None:
    monkeypatch.setattr(
        model_defaults_mod,
        "get_features_config",
        lambda *args, **kwargs: features,
    )


def _patch_account(
    monkeypatch: pytest.MonkeyPatch, tier: str | None, email: str | None = None
) -> None:
    account = _Account(tier, email)
    monkeypatch.setattr(
        account_store_mod.AccountStore,
        "get_active_account",
        lambda self: account,
    )


def test_error_type_is_shared() -> None:
    assert gemini_mod.SurfacePolicyError is SurfacePolicyError


def test_service_override_wins_over_group_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AISTUDIO_SURFACE_OVERRIDE", "build")
    _patch_features(
        monkeypatch, {"route_by_tier": True, "build_surface": False, "groups": []}
    )
    _patch_account(monkeypatch, "pro")
    assert _resolve_surface("models/gemini-3.8-flash") == "build"


def test_service_legacy_routing_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # route_by_tier off -> old behavior, groups must be ignored.
    _patch_features(
        monkeypatch,
        {
            "build_surface": True,
            "route_by_tier": False,
            "groups": [
                {"name": "free", "match_tier": ["free"], "allowed_surfaces": ["chat"]}
            ],
        },
    )
    _patch_account(monkeypatch, "free")
    assert _resolve_surface("models/gemini-3.8-flash") == "build"

    _patch_account(monkeypatch, "pro")
    assert _resolve_surface("models/gemini-3.8-flash") == "chat"

    _patch_features(monkeypatch, {"build_surface": False, "route_by_tier": False})
    assert _resolve_surface("models/gemini-3.8-flash") == "chat"


def test_service_group_routing_routes_free_to_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_features(
        monkeypatch,
        {
            "route_by_tier": True,
            "build_surface": True,
            "groups": [
                {"name": "free", "match_tier": ["free"], "allowed_surfaces": ["build"]}
            ],
        },
    )
    _patch_account(monkeypatch, "free")
    assert _resolve_surface("models/gemini-3.8-flash") == "build"


def test_service_group_routing_by_normalized_email_regex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors the shape emitted by features.groups normalization: single-string
    # match_emails_regex, lowercase match_tier, filtered allowed_surfaces.
    _patch_features(
        monkeypatch,
        {
            "route_by_tier": True,
            "build_surface": True,
            "groups": [
                {
                    "name": "corp",
                    "match_tier": [],
                    "match_emails_regex": r"@corp$",
                    "allowed_surfaces": ["chat", "build"],
                    "max_page_instances": 2,
                }
            ],
        },
    )
    _patch_account(monkeypatch, "pro", "dev@corp")
    assert _resolve_surface("models/gemini-3.8-flash") == "chat"

    _patch_account(monkeypatch, "free", "dev@corp")
    assert _resolve_surface("models/gemini-3.8-flash") == "build"


def test_service_model_guard_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_features(
        monkeypatch,
        {
            "route_by_tier": True,
            "build_surface": True,
            "groups": [
                {"name": "free", "match_tier": ["free"], "allowed_surfaces": ["build"]}
            ],
        },
    )
    _patch_account(monkeypatch, "free")
    with pytest.raises(SurfacePolicyError) as err:
        _resolve_surface("models/argon")
    assert err.value.code == "surface_model_guard"
    assert err.value.http_status == 403


def test_service_group_disabled_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_features(
        monkeypatch,
        {
            "route_by_tier": True,
            "build_surface": True,
            "groups": [
                {"name": "banned", "match_tier": ["free"], "allowed_surfaces": []}
            ],
        },
    )
    _patch_account(monkeypatch, "free")
    with pytest.raises(SurfacePolicyError) as err:
        _resolve_surface("models/gemini-3.8-flash")
    assert err.value.code == "surface_group_disabled"
    assert err.value.http_status == 403


async def test_streaming_yields_4xx_policy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_features(
        monkeypatch,
        {
            "route_by_tier": True,
            "build_surface": True,
            "groups": [
                {"name": "free", "match_tier": ["free"], "allowed_surfaces": ["build"]}
            ],
        },
    )
    _patch_account(monkeypatch, "free")

    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hi")])]
    )
    response = _build_gemini_streaming_response(
        client=cast(AIStudioClient, object()), req=req, model_path="models/argon"
    )
    chunks = [cast(str, chunk) async for chunk in response.body_iterator]
    text = "".join(chunks)

    assert text.startswith("data: ")
    payload = json.loads(text[len("data: ") :].strip())
    assert payload["error"]["code"] == 403
    assert payload["error"]["status"] == "PERMISSION_DENIED"
    assert "argon" in payload["error"]["message"]
    assert payload["error"]["advice"]
