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
from typing import Any

from aistudio_api.domain.errors import EngineSwitchingError
from aistudio_api.domain.model_access import (
    TierSelector,
    canonical_tier,
    instance_lane_for_tier,
    resolve_tier_selector,
    tier_matches_selector,
)
from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.browser_engine_session import (
    BrowserEngineSession,
)
from aistudio_api.infrastructure.gateway.session_protocol import EngineSession
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
    """One normalized ``instances`` entry (count/surface/tier selector)."""

    count: int
    surface: str
    min_tier: str = ""
    only_tier: str = ""
    max_tier: str = ""

    @classmethod
    def from_mapping(cls, item: dict[str, object]) -> InstanceSpec:
        try:
            count = int(str(item.get("count", 1)))
        except (TypeError, ValueError):
            count = 1
        surface = str(item.get("surface") or "chat").lower()
        raw_max = item.get("max_tier")
        selector = resolve_tier_selector(
            only_tier=item.get("only_tier"),
            min_tier=item.get("min_tier"),
            max_tier=raw_max,
        )
        if selector.only_tier is not None:
            return cls(
                count=count,
                surface=surface,
                only_tier=selector.only_tier,
                min_tier=selector.min_tier,
                max_tier=selector.max_tier,
            )
        return cls(
            count=count,
            surface=surface,
            min_tier=selector.min_tier,
            max_tier=selector.max_tier,
        )

    @property
    def selector(self) -> TierSelector:
        if self.only_tier:
            return resolve_tier_selector(only_tier=self.only_tier)
        return resolve_tier_selector(
            min_tier=self.min_tier or None,
            max_tier=self.max_tier or None,
        )

    @property
    def lane(self) -> str:
        return self.selector.lane

    def accepts_tier(self, tier: object) -> bool:
        return tier_matches_selector(tier, self.selector)


@dataclass
class ActiveRequest:
    """One in-flight request handle; release is idempotent and must be in finally."""

    slot: InstanceSlot
    epoch: int = 0
    generation: int = 0
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
        if self.slot.generation != self.generation:
            log.warning(
                "[pool] stale request release ignored slot=%s gen=%d current=%d",
                self.slot_id,
                self.generation,
                self.slot.generation,
            )
            return
        if self.slot.engine_epoch != self.epoch:
            log.warning(
                "[pool] cross-epoch release slot=%s epoch=%d current=%d",
                self.slot_id,
                self.epoch,
                self.slot.engine_epoch,
            )
        self.slot.release_request()


@dataclass
class InstanceSlot:
    """One configured request instance."""

    slot_id: str
    surface: str
    min_tier: str
    lane: str
    only_tier: str = ""
    max_tier: str = ""
    context_id: str = ""
    account_id: str = ""
    auth_file: str | None = None
    auth_user: str = "0"
    tier: str = "Free"
    session: EngineSession | None = None
    cdp: CDPClient | None = field(default=None, repr=False, compare=False)
    state: SlotState = SlotState.UNSTARTED
    generation: int = 0
    engine_epoch: int = 0
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
    def selector(self) -> TierSelector:
        if self.only_tier:
            return resolve_tier_selector(only_tier=self.only_tier)
        return resolve_tier_selector(
            min_tier=self.min_tier or None,
            max_tier=self.max_tier or None,
        )

    def accepts_tier(self, tier: object) -> bool:
        return tier_matches_selector(tier, self.selector)

    @property
    def engine(self) -> str:
        return str(getattr(self.session, "engine", "browser") or "browser")

    @property
    def page(self) -> CDPPage | None:
        if self.session is None:
            return None
        return getattr(self.session, "page", None)

    def session_alive(self) -> bool:
        """True while a request runtime exists (Page optional for browserless)."""
        if self.session is None:
            return False
        checker = getattr(self.session, "is_alive", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        page = self.page
        if page is None:
            return True
        try:
            return not page.is_closed()
        except Exception:
            return False

    def page_closed(self) -> bool:
        """Compatibility predicate: no live runtime counts as closed."""
        return not self.session_alive()

    def acquire_request(self) -> None:
        self.active_requests += 1
        self.active_zero.clear()
        self.last_used = time.monotonic()

    def release_request(self) -> None:
        self.active_requests = max(0, self.active_requests - 1)
        self.last_used = time.monotonic()
        if self.active_requests == 0:
            self.active_zero.set()


SessionFactory = Callable[..., Awaitable[EngineSession]]


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
        engine_runtime: Any | None = None,
        engine: str = "browser",
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
        self._session_factory = session_factory or BrowserEngineSession.bootstrap
        self._engine_runtime = engine_runtime
        self._engine = str(engine or "browser")
        self._on_slot_retired = on_slot_retired
        self._clock = clock or time.monotonic
        self._load_attempts = max(1, int(load_attempts))
        self._retry_delay_s = max(0.0, retry_delay_s)
        self._slots: dict[str, InstanceSlot] = {}
        self._counter = itertools.count(1)
        self._lock = asyncio.Lock()
        # Engine epoch/generation and the request gate used by hot switching.
        self._epoch = 1
        self._requests_open = True
        self._engine_phase = "idle"
        self._gate_lock = asyncio.Lock()

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
        """Live BrowserContext count (unchanged for the traditional engine)."""
        return sum(1 for slot in self._slots.values() if slot.context_id)

    @property
    def live_runtimes(self) -> int:
        """Live request runtimes across either engine (Page optional)."""
        return sum(1 for slot in self._slots.values() if slot.session is not None)

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def engine_phase(self) -> str:
        return self._engine_phase

    @property
    def engine_epoch(self) -> int:
        return self._epoch

    @property
    def requests_open(self) -> bool:
        return self._requests_open

    def set_engine_phase(self, phase: str) -> None:
        self._engine_phase = str(phase)

    def set_engine_runtime(self, runtime: Any | None, *, engine: str) -> None:
        self._engine_runtime = runtime
        self._engine = str(engine or "browser")

    def pause_requests(self) -> None:
        self._requests_open = False

    def resume_requests(self) -> None:
        self._requests_open = True

    def commit_epoch(self) -> int:
        self._epoch += 1
        self._requests_open = True
        self._engine_phase = "idle"
        return self._epoch

    def total_active_requests(self) -> int:
        return sum(int(slot.active_requests) for slot in self._slots.values())

    def get(self, slot_id: str) -> InstanceSlot | None:
        return self._slots.get(slot_id)

    def slots(self) -> tuple[InstanceSlot, ...]:
        return tuple(self._slots.values())

    async def sync_account_tier(
        self,
        account_id: str,
        tier: str,
        *,
        drain_timeout_s: float = 12.0,
    ) -> int:
        """Synchronize a manually detected tier into every bound slot.

        Same-lane changes update ``slot.tier`` and ``session.active_context.tier``
        in place. A cross-lane change (e.g. Free -> Pro) must never leave the old
        account serving from a slot whose configured lane no longer accepts it:
        the slot is retired, unbound and left UNSTARTED so the manager can refill
        it with an account that still belongs to that lane.

        Slot state/binding mutations happen under the pool lock; runtime close
        happens under the slot lifecycle lock with the caller's bounded drain
        timeout. Returns the number of affected slots.
        """
        if not account_id:
            return 0
        canonical = canonical_tier(tier) or str(tier or "")
        target_lane = instance_lane_for_tier(canonical)
        evict: list[InstanceSlot] = []
        affected = 0
        async with self._lock:
            for slot in self._slots.values():
                if slot.account_id != account_id:
                    continue
                affected += 1
                if slot.lane == target_lane:
                    slot.tier = canonical
                    session = slot.session
                    context = (
                        getattr(session, "active_context", None)
                        if session is not None
                        else None
                    )
                    if context is not None:
                        context.tier = canonical
                    continue
                slot.state = SlotState.RETIRING
                evict.append(slot)

        for slot in evict:
            try:
                await self.retire_runtime(
                    slot.slot_id, timeout_s=max(0.0, float(drain_timeout_s))
                )
            except Exception as exc:
                log.warning(
                    "[pool] cross-lane retire failed slot=%s account=%s: %s",
                    slot.slot_id,
                    account_id,
                    exc,
                )
            finally:
                async with self._lock:
                    if slot.account_id == account_id:
                        slot.account_id = ""
                        slot.auth_file = None
                        slot.auth_user = "0"
                        slot.tier = "Free"
                        slot.last_error = None
                        if slot.context_id or slot.session is not None:
                            slot.state = SlotState.INVALID
                        else:
                            slot.state = SlotState.UNSTARTED
            slot.last_used = self._clock()
            log.info(
                "[pool] cross-lane tier change unbound slot=%s account=%s "
                "from_lane=%s to_lane=%s tier=%s state=%s",
                slot.slot_id,
                account_id,
                "paid" if slot.lane == "paid" else "free",
                target_lane,
                canonical,
                slot.state.value,
            )
        return affected

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
        if normalized.lane != lane:
            raise ValueError(
                f"instance tier selector lane {normalized.lane} cannot use {lane} slot"
            )
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
                only_tier=normalized.only_tier,
                max_tier=normalized.max_tier,
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
        force: bool = False,
    ) -> InstanceSlot:
        """Start (or restart) exactly one Context and one Page for a slot.

        A READY slot already bound to the same account/surface/auth file is
        normally reused without touching its runtime. ``force=True`` bypasses
        that no-op so a manual restart always closes the old Page/Context and
        bootstraps a new generation/realm. Any previous runtime is closed first:
        Page close happens before ``dispose_browser_context``. The retire
        callback is fired for the old realm.
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
        if not slot.accepts_tier(tier):
            raise InstanceTierError(
                f"account tier {tier} outside instance tier spec "
                f"only={slot.only_tier or '-'} min={slot.min_tier or '-'} "
                f"max={slot.max_tier or '-'}"
            )

        async with slot.lifecycle_lock:
            if slot.state is SlotState.RETIRING:
                raise InstanceCapacityError(f"instance retiring: {slot_id}")
            if (
                not force
                and slot.state is SlotState.READY
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
        next_generation = slot.generation + 1
        realm_id = f"{slot.slot_id}-g{next_generation}"
        last_error: BaseException | None = None

        if self._engine_runtime is not None:
            # Engine-neutral path: the runtime owns CDP/Context creation for the
            # browser engine and cookie/proof/transport plumbing for browserless.
            for attempt in range(self._load_attempts):
                try:
                    build = await self._engine_runtime.create_session(
                        slot_id=slot.slot_id,
                        auth_file=slot.auth_file,
                        auth_user=slot.auth_user,
                        tier=slot.tier,
                        account_id=slot.account_id,
                        realm_id=realm_id,
                    )
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    last_error = exc
                    if attempt + 1 < self._load_attempts and self._retry_delay_s:
                        await asyncio.sleep(self._retry_delay_s)
                    continue
                slot.cdp = getattr(build, "cdp", None)
                slot.context_id = str(getattr(build, "context_id", "") or "")
                slot.session = build.session
                slot.generation = next_generation
                context = build.session.active_context
                if context is not None:
                    context.tier = slot.tier
                return
            raise last_error or RuntimeError("session bootstrap failed")

        cdp = await self._cdp_provider()
        slot.cdp = cdp
        for attempt in range(self._load_attempts):
            context_id = await cdp.create_browser_context()
            try:
                session = await self._session_factory(
                    cdp,
                    context_id=context_id,
                    auth_file=slot.auth_file,
                    auth_user=slot.auth_user,
                    realm_id=realm_id,
                    account_id=slot.account_id,
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
            context = session.active_context
            if context is not None:
                context.tier = slot.tier
            return
        raise last_error or RuntimeError("session bootstrap failed")

    async def begin_request(self, slot_id: str) -> ActiveRequest:
        """Acquire the single active-request handle for a ready instance.

        The gate lock makes the pause/acquire barrier explicit: once
        ``pause_requests`` flips the gate, no new request can pass this check.
        """
        async with self._gate_lock:
            if not self._requests_open:
                raise EngineSwitchingError(
                    "engine switch in progress; request rejected before routing"
                )
            slot = self._slots.get(slot_id)
            if slot is None:
                raise InstanceCapacityError(f"unknown instance: {slot_id}")
            if slot.state is not SlotState.READY:
                raise InstanceCapacityError(
                    f"instance not ready: {slot_id} state={slot.state.value}"
                )
            if not slot.session_alive():
                slot.state = SlotState.INVALID
                slot.last_error = "instance runtime closed"
                raise InstanceCapacityError(f"instance runtime closed: {slot_id}")
            slot.engine_epoch = self._epoch
            slot.acquire_request()
            return ActiveRequest(
                slot=slot, epoch=self._epoch, generation=slot.generation
            )

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
                if slot.active_requests > 0:
                    slot.active_requests = 0
                    slot.active_zero.set()
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
            if timed_out:
                # The old Page/Context is gone and the remaining handles are
                # aborted; the slot must be reusable for the new binding.
                slot.active_requests = 0
                slot.active_zero.set()
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
