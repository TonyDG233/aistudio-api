"""P1-03: shielded rollback must re-enter the real manager lifecycle scope."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aistudio_api.application.engine_switch import EngineSwitcher
from aistudio_api.application.instance_manager import InstanceManager
from aistudio_api.infrastructure.gateway.instance_pool import InstancePool
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport
from tests.unit.test_instance_manager import (
    FakeCDP,
    FakePage,
    FakeSession,
    _factory,
    _free_spec,
    _ready,
    _store_with_accounts,
)


class GatedInstanceManager(InstanceManager):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rebuilt = 0
        self.rebuild_gate: asyncio.Event | None = None
        self.post_rebuild_gate: asyncio.Event | None = None
        self.rollback_gate: asyncio.Event | None = None

    async def rebuild_runtimes(
        self, *, concurrency: int = 2, timeout_s: float = 120.0
    ) -> tuple[int, int]:
        self.rebuilt += 1
        if self.rebuilt == 1 and self.rebuild_gate is not None:
            await self.rebuild_gate.wait()
        if self.rebuilt == 2 and self.rollback_gate is not None:
            await self.rollback_gate.wait()
        result = await super().rebuild_runtimes(
            concurrency=concurrency, timeout_s=timeout_s
        )
        if self.rebuilt == 1 and self.post_rebuild_gate is not None:
            await self.post_rebuild_gate.wait()
        return result


class _Runtime:
    def __init__(
        self,
        mode: str,
        *,
        cdp: FakeCDP | None = None,
        close_delay: float = 0.0,
        session_factory: Any = None,
    ) -> None:
        self.mode = mode
        self.cdp = cdp
        self.close_delay = close_delay
        self.session_factory = session_factory
        self.closed = 0
        self.started = 0

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed += 1

    async def create_session(self, **kwargs: Any) -> Any:
        assert self.cdp is not None
        context_id = await self.cdp.create_browser_context()
        if self.session_factory is not None:
            session = await self.session_factory(
                self.cdp,
                context_id=context_id,
                auth_file=kwargs.get("auth_file"),
                auth_user=kwargs.get("auth_user"),
                realm_id=str(kwargs.get("realm_id") or ""),
                account_id=kwargs.get("account_id"),
            )
        else:
            session = FakeSession(self.cdp.events, str(kwargs.get("auth_user") or "0"))
        return SimpleNamespace(session=session, context_id=context_id, cdp=self.cdp)

    def status(self) -> dict[str, object]:
        return {"engine": self.mode}


class _Keepalive:
    def __init__(self) -> None:
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0

    async def pause(self) -> None:
        self.pause_calls += 1
        self.paused = True

    def resume(self) -> None:
        self.resume_calls += 1
        self.paused = False


def _settings(mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "switch": {
            "drain_timeout_s": 2.0,
            "startup_concurrency": 1,
            "switch_timeout_s": 5.0,
            "rollback_on_partial_failure": True,
        },
    }


class RealPooledSessionFactory:
    """Build a real ``PooledSession`` over FakeCDP/FakePage (no network)."""

    def __init__(self) -> None:
        self.sessions: list[PooledSession] = []

    async def __call__(self, cdp: FakeCDP, **kwargs: Any) -> PooledSession:
        realm_id = str(kwargs["realm_id"])
        session = PooledSession(
            context=SessionContext(
                page=cast(Any, FakePage(cdp.events)),
                snap_key=f"snap:{realm_id}",
                auth_user=str(kwargs.get("auth_user") or "0"),
                auth_file=kwargs.get("auth_file"),
                base_template={"url": "https://example.invalid", "body": "[]"},
                tier="Free",
            ),
            transport=XHRStreamTransport(),
            realm_id=realm_id,
        )
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_cancel_after_destroy_rolls_back_with_real_lifecycle_scope(
    tmp_path,
) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    pool = InstancePool(
        cast(Any, lambda: _ready(cdp)),
        specs=[_free_spec()],
        max_contexts=1,
        session_factory=_factory(cdp),
        load_attempts=1,
    )
    manager = GatedInstanceManager(pool, store)
    await manager.startup()
    manager.rebuild_gate = asyncio.Event()

    old_runtime = _Runtime("browser", cdp=cdp)
    holder: dict[str, _Runtime] = {"runtime": old_runtime}
    created: list[_Runtime] = []

    def factory(settings_block: dict[str, object]) -> _Runtime:
        runtime = _Runtime(
            str(settings_block.get("mode")),
            cdp=cdp,
            close_delay=0.01 if not created else 0.0,
        )
        created.append(runtime)
        return runtime

    keepalive = _Keepalive()
    switcher = EngineSwitcher(
        pool=pool,
        manager=manager,
        runtime_factory=factory,
        runtime_getter=lambda: holder["runtime"],
        runtime_setter=lambda runtime: holder.__setitem__("runtime", runtime),
        current_settings=_settings("browser"),
        config_writer=lambda text: None,
        keepalive_pause=keepalive.pause,
        keepalive_resume=keepalive.resume,
    )

    task = asyncio.create_task(
        switcher.switch(_settings("browserless"), target_config_text="x")
    )
    for _ in range(50):
        await asyncio.sleep(0)
        if manager.rebuilt == 1:
            break
    assert manager.rebuilt == 1
    assert manager._lifecycle_depth == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)

    # Shielded rollback ran under the inherited lifecycle transaction token.
    assert manager.rebuilt >= 2
    assert holder["runtime"].mode == "browser"
    assert created[0].closed == 1
    assert manager._lifecycle_depth == 0
    assert manager._lifecycle_lock.locked() is False
    assert manager._lifecycle_txn.get() is None
    assert pool.requests_open is True
    assert keepalive.paused is False
    assert keepalive.resume_calls >= 1
    assert pool.slots()[0].session is not None


@pytest.mark.asyncio
async def test_repeated_cancel_waits_for_real_rollback_before_releasing_scope(
    tmp_path,
) -> None:
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    pool = InstancePool(
        cast(Any, lambda: _ready(cdp)),
        specs=[_free_spec()],
        max_contexts=1,
        session_factory=_factory(cdp),
        load_attempts=1,
    )
    manager = GatedInstanceManager(pool, store)
    await manager.startup()
    manager.rebuild_gate = asyncio.Event()
    manager.rollback_gate = asyncio.Event()

    holder: dict[str, _Runtime] = {"runtime": _Runtime("browser", cdp=cdp)}
    created: list[_Runtime] = []

    def factory(settings_block: dict[str, object]) -> _Runtime:
        runtime = _Runtime(
            str(settings_block.get("mode")),
            cdp=cdp,
            close_delay=0.01 if not created else 0.0,
        )
        created.append(runtime)
        return runtime

    keepalive = _Keepalive()
    switcher = EngineSwitcher(
        pool=pool,
        manager=manager,
        runtime_factory=factory,
        runtime_getter=lambda: holder["runtime"],
        runtime_setter=lambda runtime: holder.__setitem__("runtime", runtime),
        current_settings=_settings("browser"),
        config_writer=lambda text: None,
        keepalive_pause=keepalive.pause,
        keepalive_resume=keepalive.resume,
    )

    task = asyncio.create_task(
        switcher.switch(_settings("browserless"), target_config_text="x")
    )
    for _ in range(50):
        await asyncio.sleep(0)
        if manager.rebuilt == 1:
            break
    assert manager.rebuilt == 1

    # First cancellation enters rollback.
    task.cancel()
    for _ in range(100):
        await asyncio.sleep(0.001)
        if manager.rebuilt == 2:
            break
    assert manager.rebuilt == 2

    # Repeated cancellation while the shielded rollback is still running.
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)

    manual_entered = asyncio.Event()

    async def manual_scope() -> None:
        async with manager.lifecycle_scope():
            manual_entered.set()

    manual = asyncio.create_task(manual_scope())
    await asyncio.sleep(0.02)
    assert not manual_entered.is_set()

    manager.rollback_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)
    await asyncio.wait_for(manual_entered.wait(), timeout=2.0)
    await manual

    assert manager.rebuilt >= 2
    assert holder["runtime"].mode == "browser"
    assert created[0].closed == 1
    assert manager._lifecycle_depth == 0
    assert manager._lifecycle_lock.locked() is False
    assert manager._lifecycle_txn.get() is None
    assert pool.requests_open is True
    assert keepalive.paused is False
    assert keepalive.resume_calls >= 1
    assert pool.slots()[0].session is not None


@pytest.mark.asyncio
async def test_cancelled_rollback_restores_real_pooled_session(tmp_path) -> None:
    """Q-18: real PooledSession survives cancel+rollback with lock/state clean."""
    store, _accounts = _store_with_accounts(
        tmp_path, ("free", "free@example.com", "Free")
    )
    cdp = FakeCDP()
    sessions = RealPooledSessionFactory()
    pool = InstancePool(
        cast(Any, lambda: _ready(cdp)),
        specs=[_free_spec()],
        max_contexts=1,
        session_factory=sessions,
        load_attempts=1,
    )
    manager = GatedInstanceManager(pool, store)
    await manager.startup()
    started_session = pool.slots()[0].session
    assert isinstance(started_session, PooledSession)
    manager.post_rebuild_gate = asyncio.Event()
    manager.rollback_gate = asyncio.Event()

    holder: dict[str, _Runtime] = {"runtime": _Runtime("browser", cdp=cdp)}
    created: list[_Runtime] = []

    def factory(settings_block: dict[str, object]) -> _Runtime:
        runtime = _Runtime(
            str(settings_block.get("mode")),
            cdp=cdp,
            close_delay=0.01 if not created else 0.0,
            session_factory=sessions,
        )
        created.append(runtime)
        return runtime

    keepalive = _Keepalive()
    switcher = EngineSwitcher(
        pool=pool,
        manager=manager,
        runtime_factory=factory,
        runtime_getter=lambda: holder["runtime"],
        runtime_setter=lambda runtime: holder.__setitem__("runtime", runtime),
        current_settings=_settings("browser"),
        config_writer=lambda text: None,
        keepalive_pause=keepalive.pause,
        keepalive_resume=keepalive.resume,
    )

    task = asyncio.create_task(
        switcher.switch(_settings("browserless"), target_config_text="x")
    )
    for _ in range(200):
        await asyncio.sleep(0.001)
        if manager.rebuilt == 1 and isinstance(pool.slots()[0].session, PooledSession):
            break
    assert manager.rebuilt == 1
    assert manager._lifecycle_depth == 1
    # A real PooledSession was built for the target rebuild before cancelling.
    target_session = pool.slots()[0].session
    assert isinstance(target_session, PooledSession)

    # First cancellation enters the shielded rollback, which rebuilds again.
    task.cancel()
    for _ in range(200):
        await asyncio.sleep(0.001)
        if manager.rebuilt == 2:
            break
    assert manager.rebuilt == 2

    # Repeated cancellation must not release the real lifecycle scope early.
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)

    manual_entered = asyncio.Event()

    async def manual_scope() -> None:
        async with manager.lifecycle_scope():
            manual_entered.set()

    manual = asyncio.create_task(manual_scope())
    await asyncio.sleep(0.02)
    assert not manual_entered.is_set()

    manager.rollback_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)
    await asyncio.wait_for(manual_entered.wait(), timeout=2.0)
    await manual

    # New (target) runtime and its real session were closed by rollback.
    assert created[0].closed == 1
    assert len(sessions.sessions) >= 3
    assert target_session is sessions.sessions[1]
    assert target_session.page is not None
    assert target_session.page.is_closed() is True

    # The restored slot holds a real PooledSession, not a fake.
    final_session = pool.slots()[0].session
    assert isinstance(final_session, PooledSession)
    assert final_session is sessions.sessions[-1]
    assert final_session.engine == "browser"
    assert final_session.is_alive() is True
    assert all(isinstance(item, PooledSession) for item in sessions.sessions)

    # Rollback completed and every shared lifecycle/concurrency marker is reset.
    assert manager.rebuilt >= 2
    assert holder["runtime"].mode == "browser"
    assert manager._lifecycle_depth == 0
    assert manager._lifecycle_lock.locked() is False
    assert manager._lifecycle_txn.get() is None
    assert pool.requests_open is True
    assert pool.total_active_requests() == 0
    assert keepalive.paused is False
    assert keepalive.resume_calls >= 1
