"""Structural validation for defaults/config.schema.json (no extra dependency)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "defaults" / "config.schema.json"
_DEFAULTS_PATH = _REPO_ROOT / "defaults" / "config.yaml"

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
}


def _is_json_type(value: object, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _JSON_TYPES.get(type_name)
    if expected is None:
        return True
    if type_name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected)


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    assert ref.startswith("#/"), ref
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    assert isinstance(node, dict)
    return cast("dict[str, Any]", node)


def _validate(
    value: object, schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[str]:
    if "$ref" in schema:
        return _validate(value, _resolve_ref(root, str(schema["$ref"])), root, path)

    errors: list[str] = []
    if "anyOf" in schema:
        branches = schema["anyOf"]
        assert isinstance(branches, list)
        branch_errors = [
            _validate(value, cast("dict[str, Any]", branch), root, path)
            for branch in branches
        ]
        if all(branch for branch in branch_errors):
            errors.append(f"{path}: no anyOf branch matched")

    if "enum" in schema:
        enum = schema["enum"]
        assert isinstance(enum, list)
        if not any(value == item and type(value) is type(item) for item in enum):
            errors.append(f"{path}: {value!r} not in enum {enum!r}")

    type_name = schema.get("type")
    if isinstance(type_name, str) and not _is_json_type(value, type_name):
        errors.append(f"{path}: expected {type_name}, got {type(value).__name__}")
        return errors

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: {value!r} does not match {pattern!r}")
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: shorter than minLength {min_length}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path}: {value} < minimum {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path}: {value} > maximum {maximum}")

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate(item, cast("dict[str, Any]", items), root, f"{path}[{index}]")
                )
        if schema.get("uniqueItems") is True:
            serialized = [
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value
            ]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: duplicate items")
        return errors

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}: missing required key {key!r}")
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, dict) else {}
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_path = f"{path}.{key}"
            child_schema = property_map.get(key)
            if isinstance(child_schema, dict):
                errors.extend(
                    _validate(item, cast("dict[str, Any]", child_schema), root, child_path)
                )
            elif additional is False:
                errors.append(f"{child_path}: unexpected key {key!r}")
            elif isinstance(additional, dict):
                errors.extend(
                    _validate(item, cast("dict[str, Any]", additional), root, child_path)
                )
    return errors


def _load_schema() -> dict[str, Any]:
    loaded = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _load_defaults() -> dict[str, Any]:
    loaded = yaml.safe_load(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _errors(instance: object) -> list[str]:
    return _validate(instance, _load_schema(), _load_schema(), "$")


def test_schema_location_is_defaults_only() -> None:
    assert _SCHEMA_PATH.is_file()
    assert not (_REPO_ROOT / "config").exists()


def test_defaults_config_matches_schema() -> None:
    assert _errors(_load_defaults()) == []


def test_legal_protocol_and_routing_example_matches_schema() -> None:
    example = _load_defaults()
    example["security"] = {"api_allow_anonymous": True}
    example["api_keys"] = [
        "sk-plain-example",
        {"name": "structured", "key": "sk-structured", "created_at": "2026-01-01"},
    ]
    example["max_contexts"] = 4
    example["instances"] = [
        {"count": 2, "surface": "build", "min_tier": "Pro", "max_tier": "Ultra"},
        {"count": 1, "surface": "chat", "only_tier": "Free"},
    ]
    features = cast("dict[str, Any]", example["features"])
    features["formats"] = {
        "openai": True,
        "gemini": True,
        "claude": True,
        "responses": True,
        "media": {"allow_private_urls": True},
    }
    atg = cast("dict[str, Any]", features["atg"])
    atg["mode"] = "prefer"
    atg["mode_by_format"] = {
        "gemini": "inherit",
        "openai": "required",
        "claude": "off",
        "responses": "prefer",
    }
    atg["empty_retries"] = 5
    atg["max_response_bytes"] = 104857600
    logging_block = cast("dict[str, Any]", example["logging"])
    logging_block["level"] = "DEBUG"
    rotator = cast("dict[str, Any]", example["rotator"])
    rotator["quota_scope"] = "account_surface"

    assert _errors(example) == []


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (
            lambda doc: cast("dict[str, Any]", doc["features"])["atg"].update(
                {"recovery": "auto"}
            ),
            "features.atg.recovery",
        ),
        (
            lambda doc: cast("dict[str, Any]", doc["features"])["atg"].update(
                {"empty_retries": 6}
            ),
            "features.atg.empty_retries",
        ),
        (
            lambda doc: cast("dict[str, Any]", doc["features"])["atg"].update(
                {"max_response_bytes": 100}
            ),
            "features.atg.max_response_bytes",
        ),
        (
            lambda doc: cast("dict[str, Any]", doc["features"])["atg"].update(
                {"control_position": "middle"}
            ),
            "features.atg.control_position",
        ),
        (
            lambda doc: cast("dict[str, Any]", doc["features"])["atg"].update(
                {
                    "mode_by_format": {
                        "gemini": "inherit",
                        "openai": "inherit",
                        "claude": "inherit",
                    }
                }
            ),
            "features.atg.mode_by_format",
        ),
        (lambda doc: doc.update({"limits": {"max_instances": 8}}), "limits"),
        (
            lambda doc: doc.update({"keepalive": {"enabled": True}}),
            "keepalive",
        ),
        (
            lambda doc: cast("list[dict[str, Any]]", doc["instances"])[0].update(
                {"account_id": "primary"}
            ),
            "instances[0].account_id",
        ),
        (
            lambda doc: cast("dict[str, Any]", doc["features"]).update(
                {"formats": ["openai", "gemini"]}
            ),
            "features.formats",
        ),
        (
            lambda doc: cast("dict[str, Any]", doc["features"]).update(
                {"build_surface": True}
            ),
            "features.build_surface",
        ),
    ],
)
def test_schema_rejects_invalid_or_removed_fields(mutate, path: str) -> None:
    example = _load_defaults()
    mutate(example)

    errors = _errors(example)

    assert errors, f"expected schema rejection for {path}"
    assert any(path in error for error in errors), errors


def test_schema_no_longer_declares_dead_gate_fields() -> None:
    schema = _load_schema()
    properties = cast("dict[str, Any]", schema["properties"])
    assert "limits" not in properties
    assert "keepalive" not in properties
    features = cast("dict[str, Any]", properties["features"])
    feature_properties = cast("dict[str, Any]", features["properties"])
    assert "build_surface" not in feature_properties
    instance_properties = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", properties["instances"])["items"],
    )["properties"]
    assert "account_id" not in cast("dict[str, Any]", instance_properties)
    logging_properties = cast(
        "dict[str, Any]", cast("dict[str, Any]", properties["logging"])["properties"]
    )
    assert "daily_dir" not in logging_properties
    assert "retention_days" not in logging_properties
