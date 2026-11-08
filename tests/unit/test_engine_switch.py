"""Transactional engine hot-switch state machine tests (fully offline)."""

from __future__ import annotations

import asyncio
import gc
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.api.routes_system import get_system_config
from aistudio_api.application.engine_switch import (
    EngineSwitchConflictError,
    EngineSwitchDrainTimeoutError,
    EngineSwitcher,
    EngineSwitchError,
    EngineSwitchPreflightError,
)


def _settings(mode: str, *, drain: float = 0.0) -> dict[str, object]:
    return {
        "mode": mode,
        "switch": {
            "drain_timeout_s": drain,
            "startup_concurrency": 1,
            "switch_timeout_s": 5.0,
            "rollback_on_partial_failure": True,
        },
    }


class FakePool:
    def __init__(self, *, active: int = 0) -> None:
        self.engine_epoch = 1
        self.requests_open = True
        self.active = active
        self.phase = "idle"
        self.runtime: object | None = None
        self.paused = 0
        self.resumed = 0

    def total_active_requests(self) -> int:
        return self.active

    def pause_requests(self) -> None:
        self.requests_open = False
        self.paused += 1

    def resume_requests(self) -> None:
        self.requests_open = True
        self.resumed += 1

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


class FakeManager:
    def __init__(
        self,
        *,
        rebuild_outcomes: list[object] | None = None,
        fail_destroy: bool = False,
    ) -> None:
        self.destroyed = 0
        self.rebuilt = 0
        self.outcomes = list(rebuild_outcomes or [(1, 1)])
        self.fail_destroy = fail_destroy
        self.rebuild_gate: asyncio.Event | None = None

    async def destroy_runtimes(self) -> int:
        if self.fail_destroy:
            raise RuntimeError("destroy failed")
        self.destroyed += 1
        return self.destroyed

    async def rebuild_runtimes(
        self, *, concurrency: int = 2, timeout_s: float = 120.0
    ) -> tuple[int, int]:
        self.rebuilt += 1
        if self.rebuild_gate is not None and self.rebuilt == 1:
            await self.rebuild_gate.wait()
        outcome = self.outcomes[min(self.rebuilt - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


class ScopedManager(FakeManager):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lifecycle_lock = asyncio.Lock()
        self.scope_depth = 0
        self.max_scope_depth = 0
        self.rebinds = 0

    @asynccontextmanager
    async def lifecycle_scope(self):
        async with self.lifecycle_lock:
            self.scope_depth += 1
            self.max_scope_depth = max(self.max_scope_depth, self.scope_depth)
            try:
                yield
            finally:
                self.scope_depth -= 1

    async def rebind(self) -> None:
        async with self.lifecycle_lock:
            self.rebinds += 1


class FakeRuntime:
    def __init__(
        self, mode: str, *, fail_start: bool = False, close_delay: float = 0.0
    ) -> None:
        self.mode = mode
        self.fail_start = fail_start
        self.close_delay = close_delay
        self.started = 0
        self.closed = 0
        self.gate: asyncio.Event | None = None

    async def start(self) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_start:
            raise RuntimeError("preflight exploded")
        self.started += 1

    async def close(self) -> None:
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed += 1

    def status(self) -> dict[str, object]:
        return {"engine": self.mode, "transport_state": "fake", "worker_state": "ok"}


def _settings_mode(harness: Harness) -> object:
    block = harness.settings_holder["settings"]
    assert isinstance(block, dict)
    return block.get("mode")


class FakeKeepalive:
    def __init__(self, *, fail_pause: bool = False) -> None:
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.fail_pause = fail_pause

    async def pause(self) -> None:
        self.pause_calls += 1
        if self.fail_pause:
            raise RuntimeError("pause failed")
        self.paused = True

    def resume(self) -> None:
        self.resume_calls += 1
        self.paused = False


class Harness:
    def __init__(
        self,
        *,
        pool: FakePool | None = None,
        manager: FakeManager | None = None,
        fail_modes: set[str] | None = None,
        gates: dict[str, asyncio.Event] | None = None,
        writer_error: Exception | None = None,
        fail_pause: bool = False,
        close_delay: float = 0.0,
    ) -> None:
        self.close_delay = close_delay
        self.pool = pool or FakePool()
        self.manager = manager or FakeManager()
        self.fail_modes = fail_modes or set()
        self.gates = gates or {}
        self.writer_error = writer_error
        self.created: list[FakeRuntime] = []
        self.written: list[str] = []
        self.holder: dict[str, FakeRuntime] = {"runtime": FakeRuntime("browser")}
        self.keepalive = FakeKeepalive(fail_pause=fail_pause)
        self.settings_holder: dict[str, object] = {"settings": _settings("browser")}
        self.switcher = EngineSwitcher(
            pool=self.pool,
            manager=self.manager,
            runtime_factory=self._factory,
            runtime_getter=lambda: self.holder["runtime"],
            runtime_setter=lambda runtime: self.holder.__setitem__("runtime", runtime),
            current_settings=_settings("browser"),
            config_writer=self._writer,
            settings_setter=self._set_settings,
            keepalive_pause=self.keepalive.pause,
            keepalive_resume=self.keepalive.resume,
        )

    def _set_settings(self, settings_block: dict[str, object]) -> None:
        self.settings_holder["settings"] = dict(settings_block)

    def _factory(self, settings: dict[str, object]) -> FakeRuntime:
        mode = str(settings.get("mode") or "browser")
        runtime = FakeRuntime(
            mode,
            fail_start=mode in self.fail_modes,
            close_delay=self.close_delay,
        )
        runtime.gate = self.gates.get(mode)
        self.created.append(runtime)
        return runtime

    def _writer(self, text: str) -> None:
        if self.writer_error is not None:
            raise self.writer_error
        self.written.append(text)


@pytest.mark.asyncio
async def test_successful_switch_rebuilds_and_commits() -> None:
    harness = Harness()
    old = harness.holder["runtime"]
    result = await harness.switcher.switch(
        _settings("browserless"), target_config_text="engine: browserless\n"
    )
    assert result.switched is True
    assert result.epoch == 2
    assert result.rebuilt == 1 and result.total == 1
    assert harness.pool.runtime is harness.holder["runtime"]
    assert harness.holder["runtime"].mode == "browserless"
    assert harness.written == ["engine: browserless\n"]
    assert old.closed == 1
    assert harness.pool.requests_open is True
    assert harness.switcher.status()["engine_phase"] == "idle"
    assert harness.keepalive.pause_calls == 1
    assert harness.keepalive.paused is False
    assert harness.keepalive.resume_calls >= 1
    assert _settings_mode(harness) == "browserless"
    assert harness.switcher.status()["fingerprint_profile"] == "Firefox 152 (WAA)"


@pytest.mark.asyncio
async def test_preflight_failure_keeps_old_engine_running() -> None:
    harness = Harness(fail_modes={"browserless"})
    old = harness.holder["runtime"]
    with pytest.raises(EngineSwitchPreflightError):
        await harness.switcher.switch(_settings("browserless"), target_config_text="x")
    assert old.closed == 0
    assert harness.holder["runtime"] is old
    assert harness.pool.requests_open is True
    assert harness.written == []
    assert harness.manager.destroyed == 0
    assert harness.keepalive.pause_calls == 0
    assert _settings_mode(harness) == "browser"


@pytest.mark.asyncio
async def test_drain_timeout_aborts_without_destroying() -> None:
    harness = Harness(pool=FakePool(active=1))
    old = harness.holder["runtime"]
    with pytest.raises(EngineSwitchDrainTimeoutError):
        await harness.switcher.switch(
            _settings("browserless", drain=0.0), target_config_text="x"
        )
    assert old.closed == 0
    assert harness.manager.destroyed == 0
    assert harness.pool.requests_open is True
    assert harness.created[-1].closed == 1
    assert harness.keepalive.pause_calls == 1
    assert harness.keepalive.paused is False
    assert harness.keepalive.resume_calls >= 1


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_old_engine() -> None:
    harness = Harness(
        manager=FakeManager(rebuild_outcomes=[RuntimeError("rebuild failed"), (1, 1)])
    )
    old = harness.holder["runtime"]
    with pytest.raises(EngineSwitchError):
        await harness.switcher.switch(_settings("browserless"), target_config_text="x")
    assert old.closed == 1
    assert harness.holder["runtime"].mode == "browser"
    assert harness.pool.runtime is harness.holder["runtime"]
    assert harness.pool.requests_open is True
    assert harness.switcher.status()["engine_phase"] == "idle"
    assert harness.keepalive.paused is False
    assert harness.keepalive.resume_calls >= 1
    assert _settings_mode(harness) == "browser"
    assert harness.written == []


@pytest.mark.asyncio
async def test_config_write_failure_rolls_back() -> None:
    harness = Harness(writer_error=OSError("disk full"))
    old = harness.holder["runtime"]
    with pytest.raises(EngineSwitchError):
        await harness.switcher.switch(_settings("browserless"), target_config_text="x")
    assert old.closed == 1
    assert harness.holder["runtime"].mode == "browser"
    assert harness.pool.requests_open is True
    assert harness.keepalive.paused is False
    assert harness.keepalive.resume_calls >= 1
    assert _settings_mode(harness) == "browser"


@pytest.mark.asyncio
async def test_keepalive_pause_failure_keeps_old_engine() -> None:
    harness = Harness(fail_pause=True)
    old = harness.holder["runtime"]
    with pytest.raises(EngineSwitchError):
        await harness.switcher.switch(_settings("browserless"), target_config_text="x")
    assert old.closed == 0
    assert harness.holder["runtime"] is old
    assert harness.pool.requests_open is True
    assert harness.keepalive.paused is False
    assert harness.keepalive.resume_calls >= 1
    assert _settings_mode(harness) == "browser"


@pytest.mark.asyncio
async def test_concurrent_switch_returns_conflict() -> None:
    gate = asyncio.Event()
    harness = Harness(gates={"browserless": gate})
    first = asyncio.create_task(
        harness.switcher.switch(_settings("browserless"), target_config_text="x")
    )
    await asyncio.sleep(0)
    with pytest.raises(EngineSwitchConflictError):
        await harness.switcher.switch(_settings("browserless"), target_config_text="x")
    gate.set()
    result = await first
    assert result.switched is True


@pytest.mark.asyncio
async def test_identical_settings_are_a_noop() -> None:
    harness = Harness()
    result = await harness.switcher.switch(_settings("browser"), target_config_text="x")
    assert result.switched is False
    assert result.phase == "noop"
    assert harness.created == []
    assert harness.written == []


@pytest.mark.asyncio
async def test_get_config_reflects_switched_engine_settings() -> None:
    harness = Harness()
    await harness.switcher.switch(
        _settings("browserless"), target_config_text="engine: browserless\n"
    )
    state = SimpleNamespace(
        ui_debug=False,
        ui_sandbox=None,
        engine_settings=harness.settings_holder["settings"],
    )
    payload = await get_system_config(runtime_state=cast(Any, state))
    assert payload["engine"] == "browserless"
    engine_settings = payload["engine_settings"]
    assert isinstance(engine_settings, dict)
    assert engine_settings["mode"] == "browserless"


@pytest.mark.asyncio
async def test_cancelled_switch_reopens_gate_and_restores_target() -> None:
    gate = asyncio.Event()
    harness = Harness(gates={"browserless": gate})
    old = harness.holder["runtime"]
    task = asyncio.create_task(
        harness.switcher.switch(_settings("browserless"), target_config_text="x")
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert harness.pool.requests_open is True
    assert harness.keepalive.paused is False
    assert old.closed == 0
    assert harness.holder["runtime"] is old
    assert _settings_mode(harness) == "browser"
    assert harness.switcher.status()["engine_target"] == "browser"


@pytest.mark.asyncio
async def test_rollback_on_partial_failure_false_keeps_target() -> None:
    harness = Harness(
        manager=FakeManager(rebuild_outcomes=[RuntimeError("rebuild failed")])
    )
    target = _settings("browserless")
    target["switch"] = {
        **target["switch"],  # type: ignore[dict-item]
        "rollback_on_partial_failure": False,
    }
    with pytest.raises(EngineSwitchError):
        await harness.switcher.switch(target, target_config_text="x")
    assert harness.pool.requests_open is True
    assert harness.keepalive.paused is False
    assert harness.holder["runtime"].mode == "browserless"
    assert _settings_mode(harness) == "browserless"
    assert harness.manager.rebuilt == 1


@pytest.mark.asyncio
async def test_route_helper_maps_base_engine_switch_error_to_503() -> None:
    from fastapi import HTTPException

    from aistudio_api.api.routes_system import _run_engine_switch

    class _FailingSwitcher:
        def __init__(self) -> None:
            self.current_settings = {"mode": "browser"}

        async def switch(self, target, *, target_config_text: str):
            del target, target_config_text
            raise EngineSwitchError("boom")

    state = SimpleNamespace(
        engine_switcher=_FailingSwitcher(),
        engine_settings={"mode": "browser"},
    )
    with pytest.raises(HTTPException) as excinfo:
        await _run_engine_switch(
            cast(Any, state),
            target_engine=_settings("browserless"),
            target_config_text="x",
        )
    assert excinfo.value.status_code == 503
    assert excinfo.value.headers == {"Retry-After": "1"}


@pytest.mark.asyncio
async def test_cancel_during_drain_resumes_keepalive_and_gate() -> None:
    harness = Harness(pool=FakePool(active=1))
    old = harness.holder["runtime"]
    task = asyncio.create_task(
        harness.switcher.switch(
            _settings("browserless", drain=5.0), target_config_text="x"
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert harness.keepalive.pause_calls == 1
    assert harness.keepalive.paused is False
    assert harness.keepalive.resume_calls >= 1
    assert harness.pool.requests_open is True
    assert old.closed == 0
    assert _settings_mode(harness) == "browser"


@pytest.mark.asyncio
async def test_cancel_during_rebuild_rolls_back_to_old_engine() -> None:
    manager = FakeManager()
    manager.rebuild_gate = asyncio.Event()
    harness = Harness(manager=manager)
    old = harness.holder["runtime"]
    task = asyncio.create_task(
        harness.switcher.switch(_settings("browserless"), target_config_text="x")
    )
    for _ in range(20):
        await asyncio.sleep(0)
        if manager.rebuilt == 1:
            break
    assert manager.rebuilt == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert old.closed == 1
    assert manager.rebuilt >= 2  # target attempt + rollback rebuild
    assert harness.holder["runtime"].mode == "browser"
    assert harness.pool.requests_open is True
    assert harness.keepalive.paused is False
    assert _settings_mode(harness) == "browser"
    assert harness.switcher.status()["engine_target"] == "browser"


@pytest.mark.asyncio
async def test_switch_holds_lifecycle_scope_across_rebuild() -> None:
    manager = ScopedManager()
    manager.rebuild_gate = asyncio.Event()
    harness = Harness(manager=manager)
    switch = asyncio.create_task(
        harness.switcher.switch(_settings("browserless"), target_config_text="x")
    )
    for _ in range(30):
        await asyncio.sleep(0)
        if manager.rebuilt == 1:
            break
    assert manager.rebuilt == 1
    assert manager.scope_depth == 1

    manual = asyncio.create_task(manager.rebind())
    await asyncio.sleep(0)
    assert not manual.done()
    assert manager.rebinds == 0

    manager.rebuild_gate.set()
    result = await switch
    await manual
    assert result.switched is True
    assert manager.rebinds == 1
    assert manager.scope_depth == 0


@pytest.mark.asyncio
async def test_cancel_closes_new_runtime_before_reraising() -> None:
    gate = asyncio.Event()
    harness = Harness(gates={"browserless": gate}, close_delay=0.05)
    task = asyncio.create_task(
        harness.switcher.switch(_settings("browserless"), target_config_text="x")
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    new_runtime = harness.created[-1]
    assert new_runtime.closed == 1
    assert harness.pool.requests_open is True
    assert harness.keepalive.paused is False


@pytest.mark.asyncio
async def test_uninterruptible_wait_observes_rollback_exception_after_timeout() -> None:
    """Q-17: a timed-out rollback that raises must be observed, not leaked."""
    harness = Harness()
    loop = asyncio.get_running_loop()
    observed: list[dict[str, Any]] = []

    def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        observed.append(dict(context))

    async def rollback() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("rollback exploded after cancel") from None

    previous = loop.get_exception_handler()
    loop.set_exception_handler(handler)
    try:
        completed = await harness.switcher._await_uninterruptibly(rollback(), 0.05)
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        gc.collect()
    finally:
        loop.set_exception_handler(previous)

    assert completed is False
    assert harness.switcher.status()["orphaned_rollbacks"] == 0
    messages = " ".join(str(item.get("message", "")) for item in observed)
    assert "never retrieved" not in messages


@pytest.mark.asyncio
async def test_uninterruptible_wait_records_orphan_and_observes_late_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q-17: an uncancellable rollback is recorded as orphaned, not leaked."""
    import aistudio_api.application.engine_switch as engine_switch_module

    monkeypatch.setattr(engine_switch_module, "_ORPHAN_GRACE_S", 0.02)
    harness = Harness()
    loop = asyncio.get_running_loop()
    observed: list[dict[str, Any]] = []

    def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        observed.append(dict(context))

    release = asyncio.Event()
    finished = asyncio.Event()

    async def rollback() -> None:
        while not release.is_set():
            try:
                await asyncio.wait_for(release.wait(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                # Deliberately ignore cancellation; still running past grace.
                continue
        finished.set()
        raise RuntimeError("late rollback failure")

    previous = loop.get_exception_handler()
    loop.set_exception_handler(handler)
    try:
        completed = await harness.switcher._await_uninterruptibly(rollback(), 0.02)
        assert completed is False
        assert harness.switcher.status()["orphaned_rollbacks"] == 1
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2.0)
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        gc.collect()
    finally:
        loop.set_exception_handler(previous)

    assert harness.switcher.status()["orphaned_rollbacks"] == 1
    messages = " ".join(str(item.get("message", "")) for item in observed)
    assert "never retrieved" not in messages
