"""Runtime config path resolution via AISTUDIO_CONFIG_PATH."""

from __future__ import annotations

from pathlib import Path

from aistudio_api.config import resolve_config_file
from aistudio_api.infrastructure.gateway.model_defaults import (
    invalidate_config_cache,
    write_config_text,
)

_DEFAULTS = Path(__file__).resolve().parents[2] / "defaults" / "config.yaml"


def test_config_path_env_missing_file_is_seeded_from_defaults(
    monkeypatch, tmp_path
):
    target = tmp_path / "nested" / "deeper" / "config.yaml"
    monkeypatch.setenv("AISTUDIO_CONFIG_PATH", str(target))
    monkeypatch.delenv("AISTUDIO_CONFIG_FILE", raising=False)

    resolved = resolve_config_file()

    assert resolved == target
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == _DEFAULTS.read_text(encoding="utf-8")


def test_config_path_env_is_used_for_atomic_writes_and_wins_over_file_env(
    monkeypatch, tmp_path
):
    preferred = tmp_path / "path" / "config.yaml"
    legacy = tmp_path / "file" / "config.yaml"
    monkeypatch.setenv("AISTUDIO_CONFIG_PATH", str(preferred))
    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(legacy))

    resolved = resolve_config_file()
    assert resolved == preferred
    assert not legacy.exists()

    content = _DEFAULTS.read_text(encoding="utf-8") + "\n# env-path-marker\n"
    written = write_config_text(content)
    invalidate_config_cache()

    assert written == preferred
    assert "# env-path-marker" in preferred.read_text(encoding="utf-8")
    assert not legacy.exists()
