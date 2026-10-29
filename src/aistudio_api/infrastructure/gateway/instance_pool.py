"""Request instance pool.

D-05/D-10/D-11: one instance is exactly one account + one BrowserContext + one
Page + one surface. The pool owns request runtimes only; the control session is
not an instance and never occupies pool capacity.

There is no lease/reservation/borrow/defer/pinned/rebalance machinery here.
``active_requests`` is the only concurrency primitive. Closing or retiring a
slot rejects new requests, waits once (bounded) for active requests to drain,
and always closes the Page before disposing the BrowserContext.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.utils.logger import get_logger

log = get_logger("instance-pool")

_VALID_SURFACES = ("chat", "build")


class SlotState(StrEnum):
    """Runtime lifecycle of one configured instance slot."""

    UNSTARTED = "unstarted"
    STARTING = "starting"
    READY = "ready"
    SLEEPING = "sleeping"
    RETIRING = "retiring"
    INVALID = "invalid"


class InstanceCapacityError(RuntimeError):
    """No instance/Context capacity is available."""


class InstanceTierError(RuntimeError):
    """An account tier does not belong to the instance's Free/paid lane."""


class InstanceBusyError(RuntimeError):
    """A slot still has in-flight requests and would be overwritten."""


@dataclass(frozen=True)
class InstanceSpec:
    """One normalized ``instances`` entry (count/surface/min_tier)."""

    count: int
    surface: str
    min_tier: str

    @classmethod
    def from_mapping(cls, item: dict[str, object]) -> InstanceSpec:
        try:
            count = int(str(item.get("count", 1)))
        except (TypeError, ValueError):
            count = 1
        return cls(
            count=count,
            surface=str(item.get("surface") or "chat").lower(),
            min_tier=str(item.get("min_tier") or "Free"),
        )


@dataclass
class ActiveRequest:
    """One in-flight request handle; release is idempotent and must be in finally."""

    slot: InstanceSlot
    _released: bool = False

    @property
    def slot_id(self) -> str:
        return self.slot.slot_id

    @property
    def realm_id(self) -> str:
        return self.slot.realm_id

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.slot.release_request()


@dataclass
class InstanceSlot:
    """One configured request instance."""

    slot_id: str
    surface: str
    min_tier: str
    lane: str
    context_id: str = ""
    account_id: str = ""
    auth_file: str | None = None
    auth_user: str = "0"
    tier: str = "Free"
    session: PooledSession | None = None
    cdp: CDPClient | None = field(default=None, repr=False, compare=False)
    state: SlotState = SlotState.UNSTARTED
    generation: int = 0
    active_requests: int = 0
    last_error: str | None = None
    last_used: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)
    lifecycle_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, repr=False, compare=False
    )
    active_zero: asyncio.Event = field(
        default_factory=asyncio.Event, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.active_zero.set()

    @property
    def realm_id(self) -> str:
        """Realm identity consumed by capture/stream caches."""
        return f"{self.slot_id}-g{self.generation}"

    @property
    def in_flight(self) -> int:
        return self.active_requests

    @property
    def page(self) -> CDPPage | None:
        return self.session.page if self.session is not None else None

    def page_closed(self) -> bool:
        page = self.page
        if page is None:
            return True
        try:
            return page.is_closed()
        except Exception:
            return True

    def acquire_request(self) -> None:
        self.active_requests += 1
        self.active_zero.clear()
        self.last_used = time.monotonic()

    def release_request(self) -> None:
        self.active_requests = max(0, self.active_requests - 1)
        self.last_used = time.monotonic()
        if self.active_requests == 0:
            self.active_zero.set()


SessionFactory = Callable[..., Awaitable[PooledSession]]


class InstancePool:
    """Pool of isolated, single-page request instances."""

    def __init__(
        self,
        cdp_provider: Callable[[], Awaitable[CDPClient]],
        *,
        specs: Sequence[InstanceSpec | dict[str, object]] | None = None,
        max_contexts: int = 1,
        specs_provider: Callable[[], Sequence[InstanceSpec | dict[str, object]]]
        | None = None,
        max_contexts_provider: Callable[[], int] | None = None,
        session_factory: SessionFactory | None = None,
        on_slot_retired: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        load_attempts: int = 3,
        retry_delay_s: float = 0.0,
    ) -> None:
        self._cdp_provider = cdp_provider
        self._static_specs = list(specs or [])
        self._specs_provider = specs_provider
        self._max_contexts = max(1, int(max_contexts))
        self._max_contexts_provider = max_contexts_provider
        self._session_factory = session_factory or PooledSession.bootstrap
        self._on_slot_retired = on_slot_retired
        self._clock = clock or time.monotonic
        self._load_attempts = max(1, int(load_attempts))
        self._retry_delay_s = max(0.0, retry_delay_s)
        self._slots: dict[str, InstanceSlot] = {}
        self._counter = itertools.count(1)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ config

    @staticmethod
    def _coerce_spec(item: InstanceSpec | dict[str, object]) -> InstanceSpec:
        if isinstance(item, InstanceSpec):
            return item
        return InstanceSpec.from_mapping(dict(item))

    @property
    def specs(self) -> tuple[InstanceSpec, ...]:
        raw: Sequence[InstanceSpec | dict[str, object]]
        if self._specs_provider is not None:
            raw = self._specs_provider()
        else:
            raw = self._static_specs
        return tuple(self._coerce_spec(item) for item in raw)

    @property
    def max_contexts(self) -> int:
        if self._max_contexts_provider is not None:
            return max(1, int(self._max_contexts_provider()))
        return self._max_contexts

    # --------------------------------------------------------------- inspection

    @property
    def live_contexts(self) -> int:
        return sum(1 for slot in self._slots.values() if slot.context_id)

    def get(self, slot_id: str) -> InstanceSlot | None:
        return self._slots.get(slot_id)

    def slots(self) -> tuple[InstanceSlot, ...]:
        return tuple(self._slots.values())

    def eligible_slots(
        self, *, paid: bool, surface: str | None = None
    ) -> tuple[InstanceSlot, ...]:
        """Configured candidates in one lane (includes sleeping/unstarted)."""
        lane = "paid" if paid else "free"
        wanted = (
            SlotState.UNSTARTED,
            SlotState.STARTING,
            SlotState.READY,
            SlotState.SLEEPING,
        )
        return tuple(
            slot
            for slot in self._slots.values()
            if slot.lane == lane
            and slot.state in wanted
            and (surface is None or slot.surface == surface)
        )

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "slot_id": slot.slot_id,
                "realm_id": slot.realm_id,
                "account_id": slot.account_id or None,
                "surface": slot.surface,
                "min_tier": slot.min_tier,
                "lane": slot.lane,
                "state": slot.state.value,
                "generation": slot.generation,
                "context_id": slot.context_id,
                "pages": 1 if slot.session is not None else 0,
                "active_requests": slot.active_requests,
                "last_error": slot.last_error,
            }
            for slot in sorted(self._slots.values(), key=lambda item: item.slot_id)
        ]

    # ------------------------------------------------------------------ slots

    async def add_slot(
        self,
        spec: InstanceSpec | dict[str, object],
        *,
        lane: str,
        slot_id: str | None = None,
    ) -> InstanceSlot:
        """Allocate one configured instance record (no Context yet)."""
        normalized = self._coerce_spec(spec)
        if normalized.surface not in _VALID_SURFACES:
            raise ValueError(f"unknown surface: {normalized.surface}")
        if lane not in ("free", "paid"):
            raise ValueError(f"unknown instance lane: {lane}")
        async with self._lock:
            if len(self._slots) >= self.max_contexts:
                raise InstanceCapacityError(
                    f"instance capacity exhausted: live={len(self._slots)} "
                    f"limit={self.max_contexts}"
                )
            resolved_id = slot_id or f"slot-{next(self._counter)}"
            if resolved_id in self._slots:
                raise ValueError(f"duplicate slot id: {resolved_id}")
            slot = InstanceSlot(
                slot_id=resolved_id,
                surface=normalized.surface,
                min_tier=normalized.min_tier,
                lane=lane,
                last_used=self._clock(),
                created_at=self._clock(),
            )
            self._slots[resolved_id] = slot
            return slot

    async def activate(
        self,
        slot_id: str,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str = "0",
        tier: str = "Free",
        account_lane: str | None = None,
        surface: str | None = None,
        timeout_s: float = 90.0,
    ) -> InstanceSlot:
        """Start (or restart) exactly one Context and one Page for a slot.

        Any previous runtime is closed first: Page close happens before
        ``dispose_browser_context``. A new generation/realm_id is assigned and
        the retire callback is fired for the old realm.
        """
        slot = self._slots.get(slot_id)
        if slot is None:
            raise InstanceCapacityError(f"unknown instance: {slot_id}")
        target_surface = (surface or slot.surface).lower()
        if target_surface not in _VALID_SURFACES:
            raise ValueError(f"unknown surface: {target_surface}")
        if account_lane is not None and account_lane != slot.lane:
            raise InstanceTierError(
                f"account lane {account_lane} cannot use {slot.lane} instance"
            )

        async with slot.lifecycle_lock:
            if slot.state is SlotState.RETIRING:
                raise InstanceCapacityError(f"instance retiring: {slot_id}")
            if (
                slot.state is SlotState.READY
                and slot.account_id == account_id
                and slot.surface == target_surface
                and slot.auth_file == auth_file
                and not slot.page_closed()
            ):
                slot.last_used = self._clock()
                return slot

            if slot.active_requests > 0:
                raise InstanceBusyError(
                    f"instance busy: {slot_id} ({slot.active_requests} in-flight)"
                )

            await self._close_runtime(slot)
            slot.state = SlotState.STARTING
            slot.account_id = account_id
            slot.auth_file = auth_file
            slot.auth_user = auth_user or "0"
            slot.tier = tier or "Free"
            slot.surface = target_surface
            slot.last_error = None
            try:
                await asyncio.wait_for(
                    self._bootstrap_runtime(slot, timeout_s=timeout_s),
                    timeout=timeout_s,
                )
            except asyncio.CancelledError:
                await self._close_runtime(slot)
                slot.state = SlotState.UNSTARTED
                raise
            except BaseException as exc:
                await self._close_runtime(slot)
                slot.state = SlotState.INVALID
                slot.last_error = str(exc)
                log.warning(
                    "[pool] activate failed slot=%s account=%s error=%s",
                    slot.slot_id,
                    account_id,
                    exc,
                )
                raise
            slot.state = SlotState.READY
            slot.last_used = self._clock()
            log.info(
                "[pool] instance ready: slot=%s account=%s surface=%s context=%s "
                "realm=%s live=%d/%d",
                slot.slot_id,
                account_id,
                slot.surface,
                slot.context_id,
                slot.realm_id,
                self.live_contexts,
                self.max_contexts,
            )
            return slot

    async def _bootstrap_runtime(self, slot: InstanceSlot, *, timeout_s: float) -> None:
        del timeout_s
        cdp = await self._cdp_provider()
        slot.cdp = cdp
        next_generation = slot.generation + 1
        realm_id = f"{slot.slot_id}-g{next_generation}"
        last_error: BaseException | None = None
        for attempt in range(self._load_attempts):
            context_id = await cdp.create_browser_context()
            try:
                session = await self._session_factory(
                    cdp,
                    context_id=context_id,
                    auth_file=slot.auth_file,
                    auth_user=slot.auth_user,
                    realm_id=realm_id,
                )
            except asyncio.CancelledError:
                with suppress(Exception):
                    await cdp.dispose_browser_context(context_id)
                raise
            except BaseException as exc:
                last_error = exc
                with suppress(Exception):
                    await cdp.dispose_browser_context(context_id)
                if attempt + 1 < self._load_attempts and self._retry_delay_s:
                    await asyncio.sleep(self._retry_delay_s)
                continue

            slot.context_id = context_id
            slot.session = session
            slot.generation = next_generation
            return
        raise last_error or RuntimeError("session bootstrap failed")

    async def begin_request(self, slot_id: str) -> ActiveRequest:
        """Acquire the single active-request handle for a ready instance."""
        slot = self._slots.get(slot_id)
        if slot is None:
            raise InstanceCapacityError(f"unknown instance: {slot_id}")
        if slot.state is not SlotState.READY:
            raise InstanceCapacityError(
                f"instance not ready: {slot_id} state={slot.state.value}"
            )
        if slot.page_closed():
            slot.state = SlotState.INVALID
            slot.last_error = "instance page closed"
            raise InstanceCapacityError(f"instance page closed: {slot_id}")
        slot.acquire_request()
        return ActiveRequest(slot=slot)

    async def sleep(self, slot_id: str) -> InstanceSlot:
        """Release the runtime but keep the configured SLEEPING instance."""
        slot = self._slots.get(slot_id)
        if slot is None:
            raise InstanceCapacityError(f"unknown instance: {slot_id}")
        async with slot.lifecycle_lock:
            if slot.active_requests > 0:
                raise InstanceCapacityError(f"instance busy: {slot_id}")
            await self._close_runtime(slot)
            slot.state = SlotState.SLEEPING
            slot.last_error = None
            return slot

    async def retire_runtime(self, slot_id: str, *, timeout_s: float) -> bool:
        """D-05 bounded retire: stop new work, drain once, then force-close.

        Returns True when the bounded wait timed out and the old Page/Context had
        to be force-closed. The slot is kept (UNSTARTED) so a rebind can reuse it
        only after the old Page/Context is gone. The caller must not create a new
        page until this coroutine returns.
        """
        slot = self._slots.get(slot_id)
        if slot is None:
            raise InstanceCapacityError(f"unknown instance: {slot_id}")
        timed_out = False
        async with slot.lifecycle_lock:
            if slot.context_id == "" and slot.session is None:
                slot.state = SlotState.UNSTARTED
                return False
            slot.state = SlotState.RETIRING
            if slot.active_requests > 0:
                try:
                    await asyncio.wait_for(
                        slot.active_zero.wait(), timeout=max(0.0, timeout_s)
                    )
                except TimeoutError:
                    timed_out = True
                    log.warning(
                        "[pool] retire drain timeout %.2fs slot=%s active=%d; "
                        "force-closing old Page/Context",
                        timeout_s,
                        slot.slot_id,
                        slot.active_requests,
                    )
            await self._close_runtime(slot)
            slot.state = SlotState.UNSTARTED
            slot.last_error = None
            return timed_out

    async def close_slot(self, slot_id: str) -> None:
        """Manually close one instance; refuse while requests are active."""
        slot = self._slots.get(slot_id)
        if slot is None:
            return
        async with slot.lifecycle_lock:
            if slot.active_requests > 0:
                raise InstanceCapacityError(f"instance busy: {slot_id}")
            slot.state = SlotState.RETIRING
            await self._close_runtime(slot)
        async with self._lock:
            self._slots.pop(slot_id, None)
        log.info("[pool] instance closed: slot=%s", slot_id)

    async def close(self) -> None:
        async with self._lock:
            slots = list(self._slots.values())
            self._slots.clear()
        for slot in slots:
            slot.state = SlotState.RETIRING
            with suppress(Exception):
                await slot.lifecycle_lock.acquire()
                try:
                    await self._close_runtime(slot)
                finally:
                    slot.lifecycle_lock.release()

    # ---------------------------------------------------------------- runtime

    async def _close_runtime(self, slot: InstanceSlot) -> None:
        """Close Page first, then dispose BrowserContext, then retire caches."""
        had_runtime = bool(slot.context_id or slot.session)
        old_realm = slot.realm_id
        session = slot.session
        context_id = slot.context_id
        cdp = slot.cdp
        slot.session = None
        slot.context_id = ""
        if session is not None:
            try:
                await session.close()
            except Exception as exc:
                log.warning(
                    "[pool] close session failed slot=%s: %s", slot.slot_id, exc
                )
        if context_id and cdp is not None:
            try:
                await cdp.dispose_browser_context(context_id)
            except Exception as exc:
                log.warning(
                    "[pool] dispose context failed slot=%s context=%s: %s",
                    slot.slot_id,
                    context_id,
                    exc,
                )
        if had_runtime and self._on_slot_retired is not None:
            try:
                self._on_slot_retired(old_realm)
            except Exception as exc:
                log.warning(
                    "[pool] retire callback failed slot=%s realm=%s: %s",
                    slot.slot_id,
                    old_realm,
                    exc,
                )


__all__ = [
    "ActiveRequest",
    "InstanceBusyError",
    "InstanceCapacityError",
    "InstancePool",
    "InstanceSlot",
    "InstanceSpec",
    "InstanceTierError",
    "SessionFactory",
    "SlotState",
]
