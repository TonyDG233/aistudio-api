"""Offline engine runtime/daemon lifecycle tests (P1-02, Q-07)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from aistudio_api.application.engine_switch import EngineSwitcher
from aistudio_api.infrastructure.waa import engine_runtime as engine_runtime_module
from aistudio_api.infrastructure.waa.engine_runtime import (
    BrowserEngineRuntime,
    BrowserlessEngineRuntime,
)
from aistudio_api.infrastructure.waa.signer_client import (
    SignerHealth,
    SignerUnavailableError,
)


class FakeSigner:
    def __init__(self, *, health_failures: int = 0, ready: bool = True) -> None:
        self.health_failures = health_failures
        self.ready = ready
        self.health_calls = 0
        self.released_all = 0
        self.closed = 0

    async def health(self) -> SignerHealth:
        self.health_calls += 1
        if self.health_calls <= self.health_failures:
            raise SignerUnavailableError("down")
        return SignerHealth(
            ok=True,
            ready=self.ready,
            workers=1,
            max_workers=8,
            version="1.0.0",
        )

    async def release_worker(self, *, release_all: bool = False) -> dict[str, object]:
        if release_all:
            self.released_all += 1
        return {"ok": True}

    async def close(self) -> None:
        self.closed += 1


class FakeDaemon:
    def __init__(self) -> None:
        self.starts = 0
        self.closes = 0

    async def start(self, *, health_client: object) -> None:
        del health_client
        self.starts += 1

    async def close(self) -> None:
        self.closes += 1


def _browserless_settings(
    *, mode: str = "local_subprocess", endpoint: str = "http://127.0.0.1:8081"
) -> dict[str, object]:
    return {
        "mode": "browserless",
        "signer": {
            "mode": mode,
            "endpoint": endpoint,
            "binary_path": "waa-signer/bin/waa-signer",
            "auto_start": True,
            "max_workers": 8,
            "request_timeout_s": 90,
        },
        "transport": {"mode": "signer_rpc", "read_timeout_s": 120},
    }


@pytest.mark.asyncio
async def test_browserless_runtime_starts_owned_daemon_then_closes() -> None:
    signer = FakeSigner(health_failures=1)
    daemon = FakeDaemon()
    runtime = BrowserlessEngineRuntime(
        _browserless_settings(),
        signer_client=cast(Any, signer),
        daemon=cast(Any, daemon),
    )

    await runtime.start()

    assert daemon.starts == 1
    assert signer.health_calls == 2

    await runtime.close()
    assert signer.released_all == 1
    assert daemon.closes == 1
    assert signer.closed == 0  # injected client is not owned/closed


@pytest.mark.asyncio
async def test_browserless_remote_mode_runs_without_local_daemon(caplog) -> None:
    signer = FakeSigner()
    daemon = FakeDaemon()
    runtime = BrowserlessEngineRuntime(
        _browserless_settings(mode="remote_http", endpoint="https://waa.example.com"),
        signer_client=cast(Any, signer),
        daemon=cast(Any, daemon),
    )
    with caplog.at_level("WARNING"):
        health = await runtime.preflight()
    assert health.ready is True
    assert daemon.starts == 0
    assert any("远程 waa-signer" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_browser_preflight_rejects_missing_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing-chrome"
    monkeypatch.setattr(
        engine_runtime_module, "find_chromium_executable", lambda: str(missing)
    )
    runtime = BrowserEngineRuntime(client=object())
    with pytest.raises(RuntimeError, match="不存在"):
        await runtime.preflight()


@pytest.mark.asyncio
async def test_browser_preflight_rejects_non_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "chrome"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        engine_runtime_module, "find_chromium_executable", lambda: str(executable)
    )
    monkeypatch.setattr(engine_runtime_module.os, "name", "posix")
    monkeypatch.setattr(engine_runtime_module.os, "access", lambda *a: False)
    runtime = BrowserEngineRuntime(client=object())
    with pytest.raises(RuntimeError, match="权限"):
        await runtime.preflight()


class _SharedDaemon:
    def __init__(self) -> None:
        self.running = True
        self.launches = 0
        self.closes = 0


class _SharedRuntime:
    mode = "browserless"

    def __init__(self, daemon: _SharedDaemon) -> None:
        self.daemon = daemon
        self.start_calls = 0
        self.closed = 0

    async def start(self) -> None:
        self.start_calls += 1
        if not self.daemon.running:
            self.daemon.running = True
            self.daemon.launches += 1

    async def close(self) -> None:
        self.closed += 1
        self.daemon.running = False

    def status(self) -> dict[str, object]:
        return {"engine": self.mode, "transport_state": "signer_rpc"}


class _Pool:
    def __init__(self) -> None:
        self.engine_epoch = 1
        self.requests_open = True
        self.phase = "idle"
        self.runtime: object | None = None

    def total_active_requests(self) -> int:
        return 0

    def pause_requests(self) -> None:
        self.requests_open = False

    def resume_requests(self) -> None:
        self.requests_open = True

    def set_engine_phase(self, phase: str) -> None:
        self.phase = phase

    def set_engine_runtime(self, runtime: object | None, *, engine: str) -> None:
        self.runtime = runtime

    def commit_epoch(self) -> int:
        self.engine_epoch += 1
        self.requests_open = True
        self.phase = "idle"
        return self.engine_epoch

    def slots(self) -> tuple[object, ...]:
        return ()


class _Manager:
    async def destroy_runtimes(self) -> int:
        return 1

    async def rebuild_runtimes(self, **kwargs: object) -> tuple[int, int]:
        del kwargs
        runtime = holder["runtime"]
        assert isinstance(runtime, _SharedRuntime)
        assert runtime.daemon.running is True
        return 1, 1


holder: dict[str, Any] = {}


@pytest.mark.asyncio
async def test_same_endpoint_browserless_switch_revives_shared_daemon() -> None:
    daemon = _SharedDaemon()
    old_runtime = _SharedRuntime(daemon)
    holder["runtime"] = old_runtime
    pool = _Pool()
    switcher = EngineSwitcher(
        pool=pool,
        manager=_Manager(),
        runtime_factory=lambda settings: _SharedRuntime(daemon),
        runtime_getter=lambda: holder["runtime"],
        runtime_setter=lambda runtime: holder.__setitem__("runtime", runtime),
        current_settings=_browserless_settings(),
        config_writer=lambda text: None,
    )

    result = await switcher.switch(
        {
            **_browserless_settings(),
            "signer": {**_browserless_settings()["signer"], "max_workers": 9},  # type: ignore[index]
        },
        target_config_text="engine:\n  mode: browserless\n",
    )

    new_runtime = holder["runtime"]
    assert isinstance(new_runtime, _SharedRuntime)
    assert result.switched is True
    assert old_runtime.closed == 1
    assert daemon.launches == 1
    assert daemon.running is True
    assert new_runtime.start_calls >= 2


@pytest.mark.asyncio
async def test_local_daemon_only_terminates_its_own_process() -> None:
    from aistudio_api.infrastructure.waa.local_daemon import LocalSignerDaemon

    class _Proc:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = 0

        def terminate(self) -> None:
            self.terminated += 1
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return int(self.returncode or 0)

    daemon = LocalSignerDaemon(
        endpoint="http://127.0.0.1:8081",
        binary_path="waa-signer/bin/waa-signer",
        state_dir="data/waa/workers",
    )
    external = _Proc()
    daemon._proc = cast(Any, external)
    daemon._started_by_us = False
    await daemon.close()
    assert external.terminated == 0

    owned = _Proc()
    daemon._proc = cast(Any, owned)
    daemon._started_by_us = True
    await daemon.close()
    assert owned.terminated == 1


@pytest.mark.asyncio
async def test_preflight_allows_saturated_signer_ready_false(caplog) -> None:
    signer = FakeSigner(ready=False)
    daemon = FakeDaemon()
    runtime = BrowserlessEngineRuntime(
        _browserless_settings(),
        signer_client=cast(Any, signer),
        daemon=cast(Any, daemon),
    )
    with caplog.at_level("WARNING"):
        health = await runtime.preflight()

    assert health.ok is True
    assert health.ready is False
    assert daemon.starts == 0
    assert any("ready=false" in record.message for record in caplog.records)
