"""Minimal ModelCatalog projection contracts (P1, no channel registry)."""

from __future__ import annotations

from aistudio_api.application.model_access import clear_paid_models, set_paid_models
from aistudio_api.application.model_catalog import (
    catalog_prefix,
    clean_model_id,
    project_model_ids,
    resolve_model_request,
    split_catalog_prefix,
)


def _models(*ids: str) -> list[dict[str, object]]:
    return [{"id": model_id} for model_id in ids]


def test_single_surface_returns_bare_ids() -> None:
    assert project_model_ids(
        _models("models/gemini-3.8-flash", "models/gemini-2.5-pro")
    ) == ["gemini-3.8-flash", "gemini-2.5-pro"]


def test_build_surface_lists_bare_and_prefixed_ids() -> None:
    projected = project_model_ids(
        _models("models/gemini-3.8-flash"),
        enabled_surfaces=("chat", "build"),
    )
    assert projected == [
        "gemini-3.8-flash",
        "pg/gemini-3.8-flash",
        "build/gemini-3.8-flash",
    ]


def test_chat_only_model_is_not_projected_into_build() -> None:
    clear_paid_models()
    set_paid_models(
        [
            {"id": "gemini-4-argon", "paid": True},
            {"id": "gemini-2.5-pro", "paid": False},
        ]
    )
    projected = project_model_ids(
        [
            {"id": "models/gemini-4-argon", "paid": True},
            {"id": "models/gemini-2.5-pro", "paid": False},
        ],
        enabled_surfaces=("chat", "build"),
    )
    assert projected == [
        "gemini-4-argon",
        "gemini-2.5-pro",
        "pg/gemini-4-argon",
        "pg/gemini-2.5-pro",
        "build/gemini-2.5-pro",
    ]


def test_duplicate_discovery_entries_are_deduped() -> None:
    projected = project_model_ids(
        _models("models/flash", "flash", "models/flash"),
        enabled_surfaces=("chat", "build"),
    )
    assert projected == ["flash", "pg/flash", "build/flash"]


def test_input_prefixes_are_parsed_not_listed() -> None:
    assert split_catalog_prefix("pg/flash") == ("flash", "chat")
    assert split_catalog_prefix("chat/flash") == ("flash", "chat")
    assert split_catalog_prefix("playground/flash") == ("flash", "chat")
    assert split_catalog_prefix("build/flash") == ("flash", "build")
    assert split_catalog_prefix("models/flash") == ("flash", None)
    assert catalog_prefix("chat") == "pg"
    assert resolve_model_request("pg/flash") == ("flash", "chat")
    assert clean_model_id("models/flash") == "flash"
