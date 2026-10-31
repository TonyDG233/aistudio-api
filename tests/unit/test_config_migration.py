"""Defaults single-source-of-truth and legacy runtime-config migration tests."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import cast

import pytest
import yaml

import aistudio_api.infrastructure.gateway.model_defaults as model_defaults
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    validate_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULTS_PATH = _REPO_ROOT / "defaults" / "config.yaml"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"

_LEGACY_CONFIG = """
groups:
  - name: production
    match_tier: [Pro]
channel_registry:
  chat:
    enabled: true
channels:
  chat:
    enabled: true
    max_instances: 2
api_keys:
  - key: sk-legacy-test
    name: legacy key
    created_at: "2026-01-01"
security:
  api_allow_anonymous: true
proxy:
  custom_endpoint: http://127.0.0.1:7890
instances:
  - count: 3
    surface: chat
    min_tier: Free
model_defaults:
  models:
    gemini-custom-model:
      generation_config_defaults:
        response_mime_type: application/json
logging:
  level: DEBUG
"""


def _read_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast("dict[str, object]", loaded)


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def test_default_config_matches_tracked_template_and_is_deep_copied():
    template = _read_yaml(_DEFAULTS_PATH)

    assert model_defaults._default_config() == template

    routed: dict[str, object] = model_defaults._default_config()
    assert routed["diagnostics"] == template["diagnostics"]
    features = _as_dict(routed["features"])
    routing = _as_dict(features["routing"])
    assert routing["retry_same_delay_sec"] == 2.0
    rotator = _as_dict(routed["rotator"])
    assert rotator["cooldowns"] == _as_dict(template["rotator"])["cooldowns"]
    assert routed["model_defaults"] == template["model_defaults"]
    assert features["atg"] == _as_dict(template["features"])["atg"]

    first = model_defaults._default_config()
    raw_keys = first["api_keys"]
    assert isinstance(raw_keys, list)
    raw_keys.append("mutated")
    logging_block = _as_dict(first["logging"])
    logging_block["level"] = "DEBUG"

    second = model_defaults._default_config()
    assert second == template
    assert second is not first


def test_dockerfile_copies_tracked_default_template_once():
    copy_lines = [
        line.strip()
        for line in _DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("COPY ") and "defaults/config.yaml" in line
    ]

    assert copy_lines == ["COPY defaults/config.yaml /app/defaults/config.yaml"]


def test_missing_template_raises_configuration_error(monkeypatch):
    def _missing_template() -> Path:
        raise FileNotFoundError("missing defaults/config.yaml")

    model_defaults.invalidate_config_cache()
    monkeypatch.setattr(
        model_defaults, "resolve_default_config_template", _missing_template
    )

    with pytest.raises(ConfigValidationError, match="配置校验失败"):
        model_defaults._default_config()

    model_defaults.invalidate_config_cache()


def test_legacy_config_is_migrated_and_atomically_written(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(_LEGACY_CONFIG, encoding="utf-8")

    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []

    def tracking_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(model_defaults.os, "replace", tracking_replace)

    validated = validate_config(path)

    assert len(replace_calls) == 1
    assert Path(replace_calls[0][0]).parent == tmp_path
    assert replace_calls[0][1] == str(path)
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []

    migrated = _read_yaml(path)
    assert "groups" not in migrated
    assert "channel_registry" not in migrated
    assert "channels" not in migrated
    assert migrated["instances"] == [
        {"count": 3, "surface": "chat", "only_tier": "Free"}
    ]
    assert migrated["api_keys"] == [
        {
            "key": "sk-legacy-test",
            "name": "legacy key",
            "created_at": "2026-01-01",
        }
    ]
    assert migrated["security"] == {"api_allow_anonymous": True}
    assert migrated["proxy"] == {"custom_endpoint": "http://127.0.0.1:7890"}
    logging_block = _as_dict(migrated["logging"])
    assert logging_block["level"] == "DEBUG"
    assert logging_block["dump_to_file"] is False
    model_defaults_block = _as_dict(migrated["model_defaults"])
    assert model_defaults_block["models"] == {
        "gemini-custom-model": {
            "generation_config_defaults": {"response_mime_type": "application/json"}
        }
    }
    assert migrated["diagnostics"] == {"hash_include_code": True}
    features = _as_dict(migrated["features"])
    assert _as_dict(features["routing"])["retry_same_delay_sec"] == 2.0
    assert _as_dict(features["atg"])["mode"] == "off"
    assert _as_dict(features["keepalive"])["auth_update"] is True
    assert (
        _as_dict(_as_dict(migrated["rotator"])["cooldowns"])["rate_limited_short"] == 60
    )
    assert "tier_quotas" in migrated
    assert "paid_model_rules" in migrated
    assert "account_presets" in migrated

    assert validated["instances"] == [
        {"count": 3, "surface": "chat", "only_tier": "Free"}
    ]


def test_instance_migration_handles_free_only_and_preserves_paid_interval(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
instances:
  - count: 1
    surface: chat
    min_tier: Free
    max_tier: Free
  - count: 2
    surface: build
    min_tier: Pro
    max_tier: Ultra
""",
        encoding="utf-8",
    )

    validate_config(path)

    assert _read_yaml(path)["instances"] == [
        {"count": 1, "surface": "chat", "only_tier": "Free"},
        {"count": 2, "surface": "build", "min_tier": "Pro", "max_tier": "Ultra"},
    ]


def test_clean_current_config_is_not_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(_DEFAULTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    before = path.stat()
    before_content = path.read_text(encoding="utf-8")

    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []

    def tracking_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(model_defaults.os, "replace", tracking_replace)

    validate_config(path)

    assert replace_calls == []
    assert path.read_text(encoding="utf-8") == before_content
    assert path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize(
    "content",
    [
        "rotator: [\n",
        "- not\n- a\n- mapping\n",
        "rotator:\n  max_retries: -1\n",
        "instances: {}\n",
        "instances:\n  - count: 1\n    surface: chat\n    min_tier: Free\n    max_tier: Pro\n",
    ],
)
def test_invalid_config_raises_without_overwrite(tmp_path, monkeypatch, content):
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")

    real_replace = os.replace
    replace_calls: list[tuple[str, str]] = []

    def tracking_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(model_defaults.os, "replace", tracking_replace)

    with pytest.raises(ConfigValidationError):
        validate_config(path)

    assert replace_calls == []
    assert path.read_text(encoding="utf-8") == content


def test_validate_config_never_rewrites_tracked_template(monkeypatch):
    template = _DEFAULTS_PATH.read_text(encoding="utf-8")

    divergent_defaults = model_defaults._default_config()
    divergent_defaults["migration_marker"] = True
    monkeypatch.setattr(model_defaults, "_default_config", lambda: divergent_defaults)

    def _fail_write(path: Path, content: str) -> None:
        raise AssertionError("tracked template must never be rewritten")

    monkeypatch.setattr(model_defaults, "_atomic_write_text", _fail_write)

    validate_config(_DEFAULTS_PATH)

    assert _DEFAULTS_PATH.read_text(encoding="utf-8") == template


def test_invalid_noncritical_logging_level_is_persisted_as_fallback(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("logging:\n  level: BOGUS\n", encoding="utf-8")

    validated = validate_config(path)

    assert _as_dict(validated["logging"])["level"] == "INFO"
    assert _as_dict(_read_yaml(path)["logging"])["level"] == "INFO"


def test_invalid_critical_config_still_raises_without_fallback_write(tmp_path):
    content = "logging:\n  level: BOGUS\nrotator:\n  max_retries: -1\n"
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigValidationError, match=r"rotator\.max_retries"):
        validate_config(path)

    assert path.read_text(encoding="utf-8") == content
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_migration_write_oserror_is_logged_and_keeps_validated_config(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "config.yaml"
    path.write_text(_LEGACY_CONFIG, encoding="utf-8")

    def _read_only_write(target: Path, content: str) -> None:
        raise PermissionError(13, "read-only file system", str(target))

    monkeypatch.setattr(model_defaults, "_atomic_write_text", _read_only_write)

    with caplog.at_level(logging.WARNING, logger="aistudio.config"):
        validated = validate_config(path)

    assert validated["instances"] == [
        {"count": 3, "surface": "chat", "only_tier": "Free"}
    ]
    warnings = [record.getMessage() for record in caplog.records]
    assert any(
        "无法写回已迁移的配置文件" in message and str(path) in message
        for message in warnings
    )
    assert path.read_text(encoding="utf-8") == _LEGACY_CONFIG
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_atomic_write_failure_removes_hidden_temp_file(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "config.yaml"
    path.write_text(_LEGACY_CONFIG, encoding="utf-8")

    def _failing_replace(src, dst):
        raise PermissionError(13, "denied", str(dst))

    monkeypatch.setattr(model_defaults.os, "replace", _failing_replace)

    with caplog.at_level(logging.WARNING, logger="aistudio.config"):
        validated = validate_config(path)

    assert validated["instances"] == [
        {"count": 3, "surface": "chat", "only_tier": "Free"}
    ]
    assert any(
        "无法写回已迁移的配置文件" in record.getMessage() for record in caplog.records
    )
    assert path.read_text(encoding="utf-8") == _LEGACY_CONFIG
    assert [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


@pytest.mark.parametrize(
    ("template_content", "message"),
    [
        ("rotator: [\n", "YAML"),
        ("- not\n- a\n- mapping\n", "顶层必须为字典"),
    ],
)
def test_invalid_default_template_raises(
    tmp_path, monkeypatch, template_content, message
):
    template_path = tmp_path / "defaults" / "config.yaml"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(template_content, encoding="utf-8")

    model_defaults.invalidate_config_cache()
    monkeypatch.setattr(
        model_defaults, "resolve_default_config_template", lambda: template_path
    )

    with pytest.raises(ConfigValidationError, match=message):
        model_defaults._default_config()

    model_defaults.invalidate_config_cache()


def test_migration_write_invalidates_stale_config_cache(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_LEGACY_CONFIG, encoding="utf-8")
    path_key = str(path.resolve())
    model_defaults._CONFIG_CACHE[path_key] = (
        path.stat().st_mtime,
        {"stale_marker": True},
    )

    try:
        validate_config(path)

        assert path_key not in model_defaults._CONFIG_CACHE

        reloaded = model_defaults._load_yaml_config(path)
        assert "stale_marker" not in reloaded
        assert "groups" not in reloaded
        assert "channels" not in reloaded
        assert reloaded["instances"] == [
            {"count": 3, "surface": "chat", "only_tier": "Free"}
        ]
        assert (
            _as_dict(_as_dict(reloaded["rotator"])["cooldowns"])["rate_limited_short"]
            == 60
        )
        cached = model_defaults._CONFIG_CACHE[path_key][1]
        assert cached["instances"] == reloaded["instances"]
    finally:
        model_defaults.invalidate_config_cache()


def test_docker_entrypoint_config_precedence_matches_config_module():
    script = (_REPO_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    block = script.split('export AISTUDIO_CONFIG_PATH="$CONFIG_FILE"', 1)[0]

    assert 'if [ -n "${AISTUDIO_CONFIG_PATH:-}" ]; then' in block
    assert 'elif [ -n "${AISTUDIO_CONFIG_FILE:-}" ]; then' in block
    assert block.index("AISTUDIO_CONFIG_PATH") < block.index(
        "AISTUDIO_CONFIG_FILE"
    ) < block.index('CONFIG_FILE="$DEFAULT_CONFIG_FILE"')
    assert 'CONFIG_FILE="$AISTUDIO_CONFIG_PATH"' in block
    assert 'CONFIG_FILE="$AISTUDIO_CONFIG_FILE"' in block
    assert "AISTUDIO_CONFIG_FILE !=" not in block
    assert 'export AISTUDIO_CONFIG_PATH="$CONFIG_FILE"' in script
