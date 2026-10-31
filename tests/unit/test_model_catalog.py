"""Minimal ModelCatalog projection contracts (P1, no channel registry)."""

from __future__ import annotations

import pytest

from aistudio_api.application.model_catalog import (
    catalog_prefix,
    clean_model_id,
    project_model_ids,
    resolve_model_request,
    split_catalog_prefix,
)
from aistudio_api.domain.model_access import clear_paid_models, set_paid_models


def _models(*ids: str) -> list[dict[str, object]]:
    return [{"id": model_id} for model_id in ids]


def test_single_surface_returns_bare_ids() -> None:
    assert project_model_ids(
        _models("models/gemini-3.8-flash", "models/gemini-2.5-pro")
    ) == ["gemini-3.8-flash", "gemini-2.5-pro"]


def test_build_surface_lists_bare_and_build_prefixed_ids() -> None:
    projected = project_model_ids(
        _models("models/gemini-3.8-flash"),
        enabled_surfaces=("chat", "build"),
    )
    assert projected == [
        "gemini-3.8-flash",
        "build/gemini-3.8-flash",
    ]
    assert not any(model.startswith(("pg/", "chat/")) for model in projected)


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
        "build/gemini-2.5-pro",
    ]


def test_duplicate_discovery_entries_are_deduped() -> None:
    projected = project_model_ids(
        _models("models/flash", "flash", "models/flash"),
        enabled_surfaces=("chat", "build"),
    )
    assert projected == ["flash", "build/flash"]


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("gemini-x", ("gemini-x", None)),
        ("models/gemini-x", ("gemini-x", None)),
        ("chat/gemini-x", ("chat/gemini-x", None)),
        ("build/gemini-x", ("gemini-x", "build")),
        ("pg/gemini-x", ("pg/gemini-x", None)),
        ("playground/gemini-x", ("playground/gemini-x", None)),
        ("official/gemini-x", ("official/gemini-x", None)),
    ],
)
def test_catalog_prefix_matrix(
    model_id: str, expected: tuple[str, str | None]
) -> None:
    assert split_catalog_prefix(model_id) == expected
    assert resolve_model_request(model_id) == expected


def test_catalog_prefix_helpers() -> None:
    assert catalog_prefix("build") == "build"
    assert clean_model_id("models/flash") == "flash"
    assert clean_model_id("build/flash") == "build/flash"
