"""Local ``waa-signer`` daemon: external exit mode must stay fail-closed."""

from __future__ import annotations

from typing import Any

import pytest

from aistudio_api.infrastructure.waa.local_daemon import LocalSignerDaemon

_PROXY_VARS = ("AISTUDIO_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def _daemon(**overrides: Any) -> LocalSignerDaemon:
    kwargs: dict[str, Any] = {
        "endpoint": "http://127.0.0.1:8081",
        "binary_path": "waa-signer/bin/waa-signer",
        "state_dir": "data/waa/workers",
        "proxy": "socks5://127.0.0.1:1080",
    }
    kwargs.update(overrides)
    return LocalSignerDaemon(**kwargs)


def test_external_mode_command_disables_proxy_discovery() -> None:
    command = _daemon(external_exits=True)._command()

    assert "-no-proxy-discovery" in command
    assert "-proxy" not in command


def test_external_mode_child_env_strips_proxy_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _PROXY_VARS:
        monkeypatch.setenv(name, "http://proxy.invalid:8080")
        monkeypatch.setenv(name.lower(), "http://proxy.invalid:8080")
    monkeypatch.setenv("AISTUDIO_KEEP_ME", "1")

    env = _daemon(external_exits=True)._child_env()

    upper_keys = {key.upper() for key in env}
    assert not upper_keys.intersection(_PROXY_VARS)
    assert env["AISTUDIO_KEEP_ME"] == "1"


def test_off_mode_command_and_env_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")

    daemon = _daemon()
    command = daemon._command()

    assert "-no-proxy-discovery" not in command
    assert command[command.index("-proxy") + 1] == "socks5://127.0.0.1:1080"
    assert daemon._child_env()["HTTPS_PROXY"] == "http://proxy.invalid:8080"
