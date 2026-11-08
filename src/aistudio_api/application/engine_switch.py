"""Transactional engine hot switch (Stage 2.1).

State machine::

    Validate(422) -> Preflight(503, old engine continues)
    -> Drain(bounded active_requests) -> Destroy(old slots / old engine)
    -> Rebuild(same slot topology + account bindings, new engine)
    -> Commit (config write + epoch) | Rollback

Hard rules: no blue-green, no concurrent old/new page, no unbounded wait,
``active_requests`` remains the only concurrency ledger.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("engine-switch")

# Bounded grace granted to a timed-out shielded rollback after cancellation.
# A rollback that still runs past this grace is recorded as an orphan.
_ORPHAN_GRACE_S = 1.0


class EngineSwitchError(RuntimeError):
    status_code = 503
    code = "engine_switch_failed"


class EngineSwitchConflictError(EngineSwitchError):
    status_code = 409
    code = "engine_switch_conflict"


class EngineSwitchPreflightError(EngineSwitchError):
    status_code = 503
    code = "engine_switch_preflight_failed"


class EngineSwitchDrainTimeoutError(EngineSwitchError):
    status_code = 503
    code = "engine_switch_drain_timeout"


class EngineSwitchRollbackError(EngineSwitchError):
    status_code = 503
    code = "engine_switch_rollback_failed"


@dataclass
class EngineStatus:
    engine: str = "browser"
    target: str = "browser"
    phase: str = "idle"
    epoch: int = 0
    rebuilt: int = 0
    total: int = 0
    transport_state: str = ""
    worker_state: str = ""
    proof_latency_ms: float | None = None
    fingerprint_profile: str = ""
    last_error: str | None = None
    orphaned_rollbacks: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "engine_phase": self.phase,
            "engine_target": self.target,
            "engine_epoch": self.epoch,
            "rebuilt": self.rebuilt,
            "total": self.total,
            "transport_state": self.transport_state,
            "worker_state": self.worker_state,
            "proof_latency_ms": self.proof_latency_ms,
            "fingerprint_profile": self.fingerprint_profile,
            "engine_error": self.last_error,
            "orphaned_rollbacks": self.orphaned_rollbacks,
        }


@dataclass
class EngineSwitchResult:
    switched: bool
    phase: str
    epoch: int
    rebuilt: int = 0
    total: int = 0
    error: str | None = None
    engine: str = "browser"
    extra: dict[str, object] = field(default_factory=dict)


class EngineSwitcher:
    """Owns the engine switch lock, phase, epoch and rollback bookkeeping."""

    def __init__(
        self,
        *,
        pool: Any,
        manager: Any,
        runtime_factory: Callable[[dict[str, object]], Any],
        runtime_getter: Callable[[], Any],
        runtime_setter: Callable[[Any], None],
        current_settings: dict[str, object],
        config_writer: Callable[[str], None] | None = None,
        settings_setter: Callable[[dict[str, object]], None] | None = None,
        keepalive_pause: Callable[[], Awaitable[object]] | None = None,
        keepalive_resume: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._manager = manager
        self._runtime_factory = runtime_factory
        self._runtime_getter = runtime_getter
        self._runtime_setter = runtime_setter
        self._current_settings = dict(current_settings)
        self._config_writer = config_writer
        self._settings_setter = settings_setter
        self._keepalive_pause = keepalive_pause
        self._keepalive_resume = keepalive_resume
        self._keepalive_paused = False
        self._clock = clock
        self._lock = asyncio.Lock()
        self._phase = "idle"
        self._target = str(current_settings.get("mode") or "browser")
        self._epoch = int(getattr(pool, "engine_epoch", 1) or 1)
        self._rebuilt = 0
        self._total = 0
        self._error: str | None = None
        self._proof_latency_ms: float | None = None
        self._orphaned_rollbacks = 0

    # -------------------------------------------------------------- inspection

    @property
    def current_settings(self) -> dict[str, object]:
        return dict(self._current_settings)

    @property
    def switching(self) -> bool:
        return self._lock.locked()

    def status(self) -> dict[str, object]:
        runtime = self._runtime_getter()
        runtime_status: dict[str, object] = {}
        getter: Any = getattr(runtime, "status", None)
        if callable(getter):
            try:
                raw_status = getter()
            except Exception:
                raw_status = {}
            if isinstance(raw_status, dict):
                runtime_status = {str(key): value for key, value in raw_status.items()}
        engine = str(
            runtime_status.get("engine")
            or self._current_settings.get("mode")
            or "browser"
        )
        if self._proof_latency_ms is None:
            self._record_proof_latency()
        return EngineStatus(
            engine=engine,
            target=self._target,
            phase=self._phase,
            epoch=self._epoch,
            rebuilt=self._rebuilt,
            total=self._total,
            transport_state=str(runtime_status.get("transport_state") or ""),
            worker_state=str(runtime_status.get("worker_state") or ""),
            proof_latency_ms=self._proof_latency_ms,
            fingerprint_profile=(
                "Firefox 152 (WAA)" if engine == "browserless" else ""
            ),
            last_error=self._error,
            orphaned_rollbacks=self._orphaned_rollbacks,
        ).as_dict()

    # ------------------------------------------------------------------ switch

    async def switch(
        self,
        target_settings: dict[str, object],
        *,
        target_config_text: str,
    ) -> EngineSwitchResult:
        """Run the full transaction. Caller validated the target mapping."""
        if self._lock.locked():
            raise EngineSwitchConflictError("已有引擎切换正在进行")
        async with self._lock:
            if target_settings == self._current_settings:
                self._phase = "idle"
                self._target = str(target_settings.get("mode") or "browser")
                return EngineSwitchResult(
                    switched=False,
                    phase="noop",
                    epoch=self._epoch,
                    engine=self._target,
                )
            try:
                async with self._lifecycle_scope():
                    return await self._run(target_settings, target_config_text)
            finally:
                # Any exit path (success, failure, cancellation, rollback
                # failure) must reopen the request gate and keepalive.
                if not self._pool.requests_open:
                    self._pool.resume_requests()
                self._pool.set_engine_phase("idle")
                self._resume_keepalive()
                runtime = self._runtime_getter()
                actual_mode = str(
                    getattr(runtime, "mode", "")
                    or self._current_settings.get("mode")
                    or "browser"
                )
                # Q-03: _target always reflects the engine actually running.
                self._target = actual_mode

    async def _run(
        self, target_settings: dict[str, object], target_config_text: str
    ) -> EngineSwitchResult:
        old_settings = dict(self._current_settings)
        old_runtime = self._runtime_getter()
        target_mode = str(target_settings.get("mode") or "browser")
        switch_cfg = target_settings.get("switch")
        switch_cfg = switch_cfg if isinstance(switch_cfg, dict) else {}
        drain_timeout_s = float(switch_cfg.get("drain_timeout_s", 12) or 12)
        concurrency = int(switch_cfg.get("startup_concurrency", 2) or 2)
        switch_timeout_s = float(switch_cfg.get("switch_timeout_s", 120) or 120)

        rollback_on_failure = bool(switch_cfg.get("rollback_on_partial_failure", True))
        new_runtime: Any = None
        old_closed = False
        self._target = target_mode
        self._error = None
        try:
            # 1. Preflight: validate the target engine without touching the
            #    running engine. Failure keeps the old engine fully alive.
            self._phase = "preflight"
            self._pool.set_engine_phase("preflight")
            new_runtime = self._runtime_factory(target_settings)
            try:
                await asyncio.wait_for(
                    new_runtime.start(), timeout=max(1.0, switch_timeout_s)
                )
            except Exception as exc:
                await self._safe_close(new_runtime)
                new_runtime = None
                raise EngineSwitchPreflightError(f"新引擎预检失败: {exc}") from exc

            # 2. Pause keepalive before touching slot sessions, then close the
            #    request gate and wait once, bounded.
            try:
                await self._pause_keepalive()
            except Exception as exc:
                await self._safe_close(new_runtime)
                new_runtime = None
                raise EngineSwitchError(f"keepalive 暂停失败: {exc}") from exc
            self._phase = "draining"
            self._pool.set_engine_phase("draining")
            self._pool.pause_requests()
            drained = await self._drain(drain_timeout_s)
            if not drained:
                self._pool.resume_requests()
                await self._safe_close(new_runtime)
                new_runtime = None
                raise EngineSwitchDrainTimeoutError(
                    f"在途请求排空超时 ({drain_timeout_s:.1f}s)"
                )

            # 3. Destroy old slot runtimes and the old engine itself.
            self._phase = "destroying"
            self._pool.set_engine_phase("destroying")
            await self._manager.destroy_runtimes()
            await self._safe_close(old_runtime)
            old_closed = True

            # 3b. P1-02: when old and new both use browserless on the same
            #     endpoint, closing the old runtime may have terminated the
            #     shared local daemon. Re-run the target preflight so the new
            #     runtime relaunches or safely re-attaches before rebuilding.
            self._phase = "preflight"
            self._pool.set_engine_phase("preflight")
            try:
                await asyncio.wait_for(
                    new_runtime.start(), timeout=max(1.0, switch_timeout_s)
                )
            except Exception as exc:
                raise EngineSwitchError(
                    f"旧引擎关闭后新引擎重新预检失败: {exc}"
                ) from exc

            # 4. Rebuild the same slot topology/bindings on the new engine.
            self._phase = "rebuilding"
            self._pool.set_engine_phase("rebuilding")
            self._pool.set_engine_runtime(new_runtime, engine=target_mode)
            self._rebuilt, self._total = await self._manager.rebuild_runtimes(
                concurrency=concurrency, timeout_s=switch_timeout_s
            )

            # 5. Commit runtime/epoch first; config write is the very last step
            #    (Q-04) so a rebuild failure can never persist target config.
            self._phase = "committing"
            self._pool.set_engine_phase("committing")
            self._current_settings = dict(target_settings)
            self._runtime_setter(new_runtime)
            self._set_settings(target_settings)
            self._epoch = int(self._pool.commit_epoch())
            self._phase = "idle"
            self._record_proof_latency()
            if self._config_writer is not None:
                self._config_writer(target_config_text)
            log.info(
                "[engine] switch complete engine=%s rebuilt=%d/%d epoch=%d",
                target_mode,
                self._rebuilt,
                self._total,
                self._epoch,
            )
            return EngineSwitchResult(
                switched=True,
                phase="idle",
                epoch=self._epoch,
                rebuilt=self._rebuilt,
                total=self._total,
                engine=target_mode,
            )
        except asyncio.CancelledError:
            self._error = "cancelled"
            if old_closed and rollback_on_failure:
                completed = await self._await_uninterruptibly(
                    self._rollback(
                        old_settings,
                        target_config_text,
                        new_runtime=new_runtime,
                        concurrency=concurrency,
                        timeout_s=switch_timeout_s,
                    ),
                    switch_timeout_s,
                )
                if not completed:
                    self._error = "cancelled; rollback timeout"
            elif not old_closed:
                self._phase = "idle"
                self._pool.set_engine_phase("idle")
                # Close must survive repeated cancellation; the original
                # CancelledError is re-raised after the bounded close.
                await self._close_bounded(new_runtime, switch_timeout_s)
            raise
        except EngineSwitchError as exc:
            self._error = str(exc)
            if old_closed:
                if rollback_on_failure:
                    await self._rollback(
                        old_settings,
                        target_config_text,
                        new_runtime=new_runtime,
                        concurrency=concurrency,
                        timeout_s=switch_timeout_s,
                    )
                else:
                    self._adopt_partial_target(target_settings, new_runtime)
            else:
                self._phase = "idle"
                self._pool.set_engine_phase("idle")
                await self._safe_close(new_runtime)
            raise
        except BaseException as exc:
            self._error = str(exc)
            if old_closed:
                if rollback_on_failure:
                    await self._rollback(
                        old_settings,
                        target_config_text,
                        new_runtime=new_runtime,
                        concurrency=concurrency,
                        timeout_s=switch_timeout_s,
                    )
                else:
                    self._adopt_partial_target(target_settings, new_runtime)
            else:
                self._pool.resume_requests()
                self._pool.set_engine_phase("idle")
                self._phase = "idle"
                await self._safe_close(new_runtime)
            raise EngineSwitchError(f"引擎切换失败: {exc}") from exc

    def _lifecycle_scope(self) -> Any:
        scope = getattr(self._manager, "lifecycle_scope", None)
        if callable(scope):
            return scope()

        @contextlib.asynccontextmanager
        async def _noop_scope():
            yield

        return _noop_scope()

    async def _drain(self, timeout_s: float) -> bool:
        deadline = self._clock() + max(0.0, timeout_s)
        while True:
            remaining = int(self._pool.total_active_requests())
            if remaining <= 0:
                return True
            if self._clock() >= deadline:
                log.warning(
                    "[engine] drain timeout active=%d after %.1fs",
                    remaining,
                    timeout_s,
                )
                return False
            await asyncio.sleep(0.05)

    async def _rollback(
        self,
        old_settings: dict[str, object],
        _target_config_text: str,
        *,
        new_runtime: Any,
        concurrency: int,
        timeout_s: float,
    ) -> None:
        """Restore the old engine after a post-destroy failure."""
        self._phase = "rolling_back"
        self._pool.set_engine_phase("rolling_back")
        log.warning(
            "[engine] switch failed after destroy; rolling back to %s",
            old_settings.get("mode"),
        )
        rollback_runtime: Any = None
        try:
            await self._close_bounded(new_runtime, timeout_s)
            self._pool.set_engine_runtime(
                None, engine=str(old_settings.get("mode") or "browser")
            )
            await self._manager.destroy_runtimes()
            rollback_runtime = self._runtime_factory(old_settings)
            await rollback_runtime.start()
            self._pool.set_engine_runtime(
                rollback_runtime, engine=str(old_settings.get("mode") or "browser")
            )
            self._rebuilt, self._total = await self._manager.rebuild_runtimes(
                concurrency=concurrency, timeout_s=timeout_s
            )
            self._runtime_setter(rollback_runtime)
            self._set_settings(old_settings)
            self._current_settings = dict(old_settings)
            self._target = str(old_settings.get("mode") or "browser")
            self._epoch = int(self._pool.commit_epoch())
            self._phase = "idle"
            self._error = None
            log.info(
                "[engine] rollback complete engine=%s rebuilt=%d/%d",
                old_settings.get("mode"),
                self._rebuilt,
                self._total,
            )
        except Exception as exc:
            self._error = f"rollback failed: {exc}"
            self._phase = "failed"
            self._pool.resume_requests()
            await self._safe_close(rollback_runtime)
            raise EngineSwitchRollbackError(self._error) from exc

    async def _pause_keepalive(self) -> None:
        if self._keepalive_pause is None:
            return
        # Mark paused before awaiting so a partially-failed pause callback is
        # still resumed by the outer finally.
        self._keepalive_paused = True
        result = await self._keepalive_pause()
        if result is False:
            raise RuntimeError("keepalive 未能在限期内暂停")

    def _resume_keepalive(self) -> None:
        if not self._keepalive_paused:
            return
        self._keepalive_paused = False
        if self._keepalive_resume is None:
            return
        with contextlib.suppress(Exception):
            self._keepalive_resume()

    def _adopt_partial_target(
        self, target_settings: dict[str, object], new_runtime: Any
    ) -> None:
        """rollback_on_partial_failure=false: keep the partially rebuilt target."""
        self._phase = "failed"
        self._current_settings = dict(target_settings)
        self._runtime_setter(new_runtime)
        self._set_settings(target_settings)
        log.error(
            "[engine] partial rebuild kept without rollback (rollback_on_partial_failure=false)"
        )

    def _set_settings(self, settings_block: dict[str, object]) -> None:
        if self._settings_setter is not None:
            self._settings_setter(dict(settings_block))

    def _record_proof_latency(self) -> None:
        best: float | None = None
        for slot in getattr(self._pool, "slots", lambda: ())():
            session = getattr(slot, "session", None)
            value = getattr(session, "last_proof_latency_ms", None)
            if (
                isinstance(value, (int, float))
                and value >= 0
                and (best is None or value < best)
            ):
                best = float(value)
        self._proof_latency_ms = best

    @staticmethod
    async def _safe_close(runtime: Any) -> None:
        if runtime is None:
            return
        with contextlib.suppress(Exception):
            await runtime.close()

    async def _await_uninterruptibly(self, coro: Any, timeout_s: float) -> bool:
        """Await ``coro`` in a child task, absorbing repeated cancellation.

        The child task inherits the current ContextVar transaction token, so a
        shielded rollback can re-enter ``InstanceManager.lifecycle_scope``.
        Repeated CancelledError delivered to the outer switch task is consumed
        in the wait loop; the caller only regains control after the child
        finishes or the bounded timeout expires.  Returns True on completion.

        Every exit path retrieves the child result (or installs a done callback
        that does) so a cancelled/failed rollback never surfaces later as an
        unobserved task exception.  When the child ignores cancellation past the
        bounded grace it is recorded as an orphaned rollback because it can
        outlive the outer lifecycle scope.
        """
        task: asyncio.Future[Any] = asyncio.ensure_future(coro)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout_s))
        orphan_registered = False
        try:
            while not task.done():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait({task}, timeout=remaining)
                except asyncio.CancelledError:
                    # Absorb repeated cancellation; keep waiting for rollback.
                    continue
            if task.done():
                self._observe_task(task)
                return True
            log.error(
                "[engine] uninterruptible wait timed out after %.1fs",
                timeout_s,
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task}, timeout=_ORPHAN_GRACE_S)
            if task.done():
                self._observe_task(task)
                return False
            # The shielded rollback ignored cancellation and is still running.
            # Record it explicitly and observe its eventual outcome.
            orphan_registered = True
            self._orphaned_rollbacks += 1
            task.add_done_callback(self._observe_task)
            log.error(
                "[engine] orphaned rollback still running after %.1fs timeout "
                "(orphaned=%d); outer lifecycle scope will release with the "
                "rollback task pending",
                timeout_s,
                self._orphaned_rollbacks,
            )
            return False
        finally:
            if not task.done() and not orphan_registered:
                task.cancel()
                task.add_done_callback(self._observe_task)

    @staticmethod
    def _observe_task(task: asyncio.Future[Any]) -> None:
        """Retrieve a child task outcome so it is never an unobserved error."""
        with contextlib.suppress(BaseException):
            task.result()

    async def _close_bounded(self, runtime: Any, timeout_s: float) -> None:
        """Close a runtime even if the caller is being cancelled/re-cancelled."""
        if runtime is None:
            return
        await self._await_uninterruptibly(self._safe_close(runtime), timeout_s)


__all__ = [
    "EngineStatus",
    "EngineSwitchConflictError",
    "EngineSwitchDrainTimeoutError",
    "EngineSwitchError",
    "EngineSwitchPreflightError",
    "EngineSwitchResult",
    "EngineSwitchRollbackError",
    "EngineSwitcher",
]
