"""Behavior contracts for WP5 v3 model-list projection."""

from __future__ import annotations

from aistudio_api.application.model_catalog import (
    catalog_prefix,
    project_model_ids,
    resolve_model_request,
    split_catalog_prefix,
)


def _features(*, chat: bool = True, build: bool = False) -> dict[str, object]:
    return {
        "channels": {
            "chat": {"enabled": chat, "max_pages": 1},
            "build": {"enabled": build, "max_pages": 1},
            "official": {"enabled": False, "max_pages": 0},
        }
    }


def _models(*ids: str) -> list[dict[str, object]]:
    return [{"id": model_id} for model_id in ids]


def test_single_channel_returns_bare_ids() -> None:
    assert project_model_ids(
        _models("models/gemini-3.8-flash", "models/gemini-2.5-pro"),
        _features(),
    ) == ["gemini-3.8-flash", "gemini-2.5-pro"]


def test_multi_channel_emits_one_prefix_per_real_channel() -> None:
    projected = project_model_ids(
        _models("models/gemini-3.8-flash"),
        _features(build=True),
    )
    assert projected == ["pg/gemini-3.8-flash", "build/gemini-3.8-flash"]


def test_chat_only_model_is_not_duplicated_into_build() -> None:
    projected = project_model_ids(
        [
            {"id": "models/gemini-4-argon", "paid": True},
            {"id": "models/gemini-2.5-pro", "paid": True},
        ],
        _features(build=True),
    )
    assert projected == ["pg/gemini-4-argon", "pg/gemini-2.5-pro"]


def test_duplicate_discovery_entries_are_deduped() -> None:
    projected = project_model_ids(
        _models("models/flash", "flash", "models/flash"),
        _features(build=True),
    )
    assert projected == ["pg/flash", "build/flash"]


def test_official_is_not_listed_before_wp9() -> None:
    features = _features(build=True)
    features["channels"]["official"]["enabled"] = True  # type: ignore[index]
    assert all(
        not item.startswith("official/")
        for item in project_model_ids(_models("flash"), features)
    )


def test_input_prefixes_are_parsed_not_listed() -> None:
    assert split_catalog_prefix("pg/flash") == ("flash", "chat")
    assert split_catalog_prefix("chat/flash") == ("flash", "chat")
    assert split_catalog_prefix("playground/flash") == ("flash", "chat")
    assert split_catalog_prefix("build/flash") == ("flash", "build")
    assert split_catalog_prefix("models/flash") == ("flash", None)
    assert catalog_prefix("chat") == "pg"
    assert resolve_model_request("pg/flash") == ("flash", "chat")
