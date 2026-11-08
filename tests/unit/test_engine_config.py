"""Stage 2.1 engine configuration contract (offline, no daemon/browser)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from aistudio_api.config import resolve_default_config_template
from aistudio_api.infrastructure.gateway.model_defaults import (
    ConfigValidationError,
    engine_settings_hash,
    validate_engine_settings,
)

_DEFAULTS = Path(__file__).resolve().parents[2] / "defaults" / "config.yaml"


def _mapping(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _default_engine() -> dict[str, Any]:
    loaded = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return copy.deepcopy(_mapping(loaded["engine"]))


def _browserless(engine: dict[str, Any]) -> dict[str, Any]:
    engine = copy.deepcopy(engine)
    engine["mode"] = "browserless"
    # Most config tests must not depend on a built signer binary.
    _mapping(_mapping(engine["browserless"])["signer"])["auto_start"] = False
    return engine


def test_defaults_ship_browser_mode_and_full_block() -> None:
    engine = _default_engine()
    assert engine["mode"] == "browser"
    normalized = validate_engine_settings(engine)
    assert normalized["mode"] == "browser"
    assert _mapping(normalized["switch"])["drain_timeout_s"] == 12.0
    assert "browser" not in normalized
    signer = _mapping(_mapping(normalized["browserless"])["signer"])
    assert signer["endpoint"] == "http://127.0.0.1:8081"


def test_invalid_mode_is_rejected() -> None:
    engine = _default_engine()
    engine["mode"] = "sidecar"
    with pytest.raises(ConfigValidationError):
        validate_engine_settings(engine)


def test_local_signer_endpoint_must_be_loopback() -> None:
    engine = _browserless(_default_engine())
    _mapping(_mapping(engine["browserless"])["signer"])["endpoint"] = (
        "http://signer.example.com"
    )
    with pytest.raises(ConfigValidationError, match="回环"):
        validate_engine_settings(engine)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:8081",
        "http://127.0.0.1:8081/waa",
        "http://127.0.0.1:8081?token=1",
        "http://127.0.0.1:8081#frag",
        "http://user:pass@127.0.0.1:8081",
    ],
)
def test_local_signer_endpoint_rejects_non_base_http(endpoint: str) -> None:
    engine = _browserless(_default_engine())
    _mapping(_mapping(engine["browserless"])["signer"])["endpoint"] = endpoint
    with pytest.raises(ConfigValidationError):
        validate_engine_settings(engine)


def test_local_signer_endpoint_accepts_trailing_root() -> None:
    engine = _browserless(_default_engine())
    _mapping(_mapping(engine["browserless"])["signer"])["endpoint"] = (
        "http://127.0.0.1:8081/"
    )
    normalized = validate_engine_settings(engine)
    signer = _mapping(_mapping(normalized["browserless"])["signer"])
    assert signer["endpoint"] == "http://127.0.0.1:8081"


def test_remote_signer_requires_explicit_non_loopback_endpoint() -> None:
    engine = _browserless(_default_engine())
    signer = _mapping(_mapping(engine["browserless"])["signer"])
    signer["mode"] = "remote_http"
    with pytest.raises(ConfigValidationError, match="非回环"):
        validate_engine_settings(engine)
    signer["endpoint"] = "https://waa.example.com:8443"
    normalized = validate_engine_settings(engine)
    normalized_signer = _mapping(_mapping(normalized["browserless"])["signer"])
    assert normalized_signer["endpoint"] == "https://waa.example.com:8443"


def test_invalid_transport_mode_is_rejected() -> None:
    engine = _browserless(_default_engine())
    _mapping(_mapping(engine["browserless"])["transport"])["mode"] = "carrier-pigeon"
    with pytest.raises(ConfigValidationError, match="signer_rpc"):
        validate_engine_settings(engine)


def test_max_workers_must_cover_configured_slots() -> None:
    engine = _browserless(_default_engine())
    _mapping(_mapping(engine["browserless"])["signer"])["max_workers"] = 1
    with pytest.raises(ConfigValidationError, match="max_workers"):
        validate_engine_settings(engine, specs=[{"count": 2}], max_contexts=2)


def test_missing_local_binary_fails_fast(tmp_path: Path) -> None:
    engine = _browserless(_default_engine())
    signer = _mapping(_mapping(engine["browserless"])["signer"])
    signer["binary_path"] = str(tmp_path / "missing-signer")
    signer["auto_start"] = True
    with pytest.raises(ConfigValidationError, match="binary_path"):
        validate_engine_settings(engine)
    # auto_start=false keeps prebuilt deployments valid without a binary.
    signer["auto_start"] = False
    validate_engine_settings(engine)


def test_engine_hash_is_stable_and_changes_with_mode() -> None:
    engine = _default_engine()
    first = validate_engine_settings(engine)
    second = validate_engine_settings(_default_engine())
    assert engine_settings_hash(first) == engine_settings_hash(second)
    changed = _browserless(engine)
    assert engine_settings_hash(first) != engine_settings_hash(changed)


def test_default_config_path_exists() -> None:
    assert resolve_default_config_template().is_file()


def test_integer_fields_reject_fractional_values() -> None:
    engine = _default_engine()
    _mapping(engine["switch"])["startup_concurrency"] = 2.9
    with pytest.raises(ConfigValidationError, match="整数"):
        validate_engine_settings(engine)
    _mapping(engine["switch"])["startup_concurrency"] = 2.0
    normalized = validate_engine_settings(engine)
    assert _mapping(normalized["switch"])["startup_concurrency"] == 2


def test_windows_binary_exe_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.waa import local_daemon

    monkeypatch.setattr(local_daemon.sys, "platform", "win32")
    binary = tmp_path / "waa-signer"
    binary.with_name("waa-signer.exe").write_text("x", encoding="utf-8")
    assert local_daemon.resolve_binary_path(str(binary)) == binary.with_name(
        "waa-signer.exe"
    )


def test_windows_exe_fallback_satisfies_config_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aistudio_api.infrastructure.waa import local_daemon

    monkeypatch.setattr(local_daemon.sys, "platform", "win32")
    binary = tmp_path / "waa-signer"
    binary.with_name("waa-signer.exe").write_text("x", encoding="utf-8")
    engine = _browserless(_default_engine())
    signer = _mapping(_mapping(engine["browserless"])["signer"])
    signer["binary_path"] = str(binary)
    signer["auto_start"] = True
    normalized = validate_engine_settings(engine)
    normalized_signer = _mapping(_mapping(normalized["browserless"])["signer"])
    assert normalized_signer["binary_path"] == str(binary)


def test_removed_unimplemented_engine_keys_are_absent() -> None:
    loaded = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))
    engine = _mapping(loaded["engine"])
    assert "browser" not in engine
    browserless = _mapping(engine["browserless"])
    assert "fingerprint" not in browserless
    transport = _mapping(browserless["transport"])
    assert "http3" not in transport
    signer = _mapping(browserless["signer"])
    assert "health_interval_s" not in signer


def test_unused_engine_resolvers_are_removed() -> None:
    import aistudio_api.config as config

    assert not hasattr(config, "resolve_engine_settings")
    assert not hasattr(config, "resolve_engine_mode")
