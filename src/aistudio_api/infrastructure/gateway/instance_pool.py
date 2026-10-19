"""Unified Context instance pool (WP5 v3).

All request instances live here. There is no privileged "default" instance and
no account-to-context hard binding: a slot is a BrowserContext that may be
reused while it is bound to the same account, or rebound to another eligible
account after its current pages are idle.

The pool owns *request* contexts only. ``BrowserSession`` remains the browser
process/control-plane object (login, cookie import, model discovery); it never
serves a generation request.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from aistudio_api.infrastructure.browser.cdp_client import CDPClient, CDPPage
from aistudio_api.infrastructure.gateway.realm_pool import RealmSession
from aistudio_api.infrastructure.utils.logger import get_logger

if TYPE_CHECKING:
    from aistudio_api.infrastructure.gateway.build_surface import BuildSurface

log = get_logger("instance-pool")


class SlotState(StrEnum):
    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    BUSY = "busy"
    COOLDOWN = "cooldown"
    RETIRING = "retiring"


class InstanceCapacityError(RuntimeError):
    """No request slot is available within the wait budget."""


@dataclass
class InstanceSlot:
    """One BrowserContext in the unified request pool."""

    slot_id: str
    context_id: str = ""
    state: SlotState = SlotState.EMPTY
    current_account_id: str = ""
    auth_file: str | None = None
    auth_user: str = "0"
    session: RealmSession | None = None
    generation: int = 0
    reservations: int = 0
    last_used: float = field(default_factory=time.monotonic)
    build_surface: Any = None
    assigned_slot_rows: tuple[dict[str, object], ...] = ()

    @property
    def realm_id(self) -> str:
        """Compatibility identity used by capture/stream service caches."""
        return f"{self.slot_id}-g{self.generation}"

    @property
    def in_flight(self) -> int:
        session_in_flight = 0
        if self.session is not None:
            with suppress(Exception):
                session_in_flight = int(self.session.in_flight_count)
        return max(self.reservations, session_in_flight)

    @property
    def chat_page(self) -> CDPPage | None:
        return self.session.page if self.session is not None else None

    @property
    def build_page(self) -> CDPPage | None:
        surface = self.build_surface
        state = getattr(surface, "state", None)
        return getattr(state, "page", None)

    def chat_ready(self) -> bool:
        page = self.chat_page
        try:
            return page is not None and not page.is_closed()
        except Exception:
            return False

    def build_ready(self) -> bool:
        page = self.build_page
        try:
            return page is not None and not page.is_closed()
        except Exception:
            return False

    async def ensure_build_surface(self, cdp: CDPClient) -> BuildSurface:
        from aistudio_api.infrastructure.gateway.build_surface import BuildSurface

        if self.build_surface is None:
            self.build_surface = BuildSurface(
                cdp,
                context_id=self.context_id,
                realm_id=self.realm_id,
            )
        surface = self.build_surface
        await surface.ensure_ready(
            self.auth_file, self.auth_user, account_id=self.current_account_id
        )
        return surface

    async def close_pages(self) -> None:
        surface = self.build_surface
        self.build_surface = None
        if surface is not None:
            page = getattr(getattr(surface, "state", None), "page", None)
            if page is not None:
                with suppress(Exception):
                    await page.close()
        if self.session is not None:
            with suppress(Exception):
                await self.session.close()
            self.session = None


@dataclass
class InstanceLease:
    """One reserved slot for a request attempt."""

    account_id: str
    channel: str
    surface: str
    slot_id: str
    realm_id: str
    context_id: str
    adapter: InstanceSlot
    is_borrow: bool = False
    slot_index: int | None = None
    released: bool = False


SessionFactory = Callable[..., Awaitable[RealmSession]]


class InstancePool:
    """Pool of equal BrowserContext slots with rebinding and idle retirement."""

    def __init__(
        self,
        cdp_provider: Callable[[], Awaitable[CDPClient]],
        *,
        features_provider: Callable[[], dict[str, object]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_factory: SessionFactory | None = None,
        on_slot_retired: Callable[[str], None] | None = None,
        wait_timeout_s: float = 5.0,
    ) -> None:
        self._cdp_provider = cdp_provider
        self._features_provider = features_provider
        self._clock = clock or time.monotonic
        self._session_factory = session_factory or RealmSession.bootstrap
        self._on_slot_retired = on_slot_retired
        self._wait_timeout_s = max(0.0, wait_timeout_s)
        self._slots: dict[str, InstanceSlot] = {}
        self._counter = itertools.count(1)
        self._condition = asyncio.Condition()

    # ------------------------------------------------------------------ config

    def _max_contexts(self) -> int:
        if self._features_provider is None:
            return 2
        try:
            features = self._features_provider()
            routing = features.get("routing")
            raw = routing if isinstance(routing, dict) else {}
            return max(1, int(raw.get("max_contexts", 2)))
        except (TypeError, ValueError, AttributeError):
            return 2

    # --------------------------------------------------------------- inspection

    @property
    def live_contexts(self) -> int:
        return sum(1 for slot in self._slots.values() if slot.context_id)

    @property
    def max_contexts(self) -> int:
        return self._max_contexts()

    def get(self, slot_id: str) -> InstanceSlot | None:
        return self._slots.get(slot_id)

    def slots(self) -> tuple[InstanceSlot, ...]:
        return tuple(self._slots.values())

    def page_counts(self) -> dict[str, int]:
        counts = {"chat": 0, "build": 0}
        for slot in self._slots.values():
            if slot.chat_ready():
                counts["chat"] += 1
            if slot.build_ready():
                counts["build"] += 1
        return counts

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "slot_id": slot.slot_id,
                "realm_id": slot.realm_id,
                "account_id": slot.current_account_id or None,
                "context_id": slot.context_id,
                "state": slot.state.value,
                "in_flight": slot.in_flight,
                "reservations": slot.reservations,
                "chat_ready": slot.chat_ready(),
                "build_ready": slot.build_ready(),
                "last_used": slot.last_used,
                "slot_rows": list(slot.assigned_slot_rows),
            }
            for slot in sorted(self._slots.values(), key=lambda item: item.slot_id)
        ]

    # ---------------------------------------------------------------- acquiring

    async def acquire(
        self,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str | None = None,
        channel: str = "chat",
        surface: str | None = None,
        timeout_s: float | None = None,
        borrow_accounts: set[str] | None = None,
    ) -> InstanceLease:
        """Reserve a ready slot for the account/channel.

        Reuses an existing same-account slot first. If the pool has spare
        Context capacity, creates a new slot. Otherwise rebinds the least
        recently used idle slot. When nothing is rebindable, waits up to
        ``timeout_s`` for a release and raises ``InstanceCapacityError``.
        """
        surface = surface or channel
        deadline = self._clock() + (
            self._wait_timeout_s if timeout_s is None else max(0.0, timeout_s)
        )
        needs_surface = False
        lease: InstanceLease | None = None
        slot: InstanceSlot | None = None
        async with self._condition:
            while True:
                lease = self._lease_existing_locked(
                    account_id, auth_file, auth_user or "0", channel, surface
                )
                if lease is not None:
                    if self._page_ready(lease.adapter, surface):
                        self._touch_locked(lease.adapter)
                        return lease
                    needs_surface = True
                    break

                if borrow_accounts:
                    lease = self._lease_borrow_locked(borrow_accounts, channel, surface)
                    if lease is not None:
                        self._touch_locked(lease.adapter)
                        return lease

                slot = self._reserve_slot_locked(account_id)
                if slot is not None:
                    break

                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise InstanceCapacityError(
                        "instance capacity exhausted: "
                        f"live={len(self._slots)} limit={self._max_contexts()}"
                    )
                try:
                    await asyncio.wait_for(self._condition.wait(), remaining)
                except TimeoutError as exc:
                    raise InstanceCapacityError(
                        "instance capacity exhausted: "
                        f"live={len(self._slots)} limit={self._max_contexts()}"
                    ) from exc

        # Bootstrap outside the condition lock; the slot stays reserved/loading.
        try:
            if needs_surface and lease is not None:
                await self._ensure_surface(lease.adapter, surface)
                return lease
            if slot is None:
                raise InstanceCapacityError("instance slot reservation lost")
            await self._load_slot(
                slot,
                account_id=account_id,
                auth_file=auth_file,
                auth_user=auth_user,
            )
            if not self._page_ready(slot, surface):
                await self._ensure_surface(slot, surface)
        except Exception:
            if lease is None:
                async with self._condition:
                    if slot is not None:
                        self._drop_slot_locked(slot)
                    self._condition.notify_all()
            else:
                await self.release(lease)
            raise

        async with self._condition:
            slot.reservations = max(1, slot.reservations)
            self._touch_locked(slot)
            return self._lease_for_slot(slot, channel, surface, is_borrow=False)

    async def release(self, lease: InstanceLease) -> None:
        if lease.released:
            return
        lease.released = True
        async with self._condition:
            slot = self._slots.get(lease.slot_id)
            if slot is not None:
                slot.reservations = max(0, slot.reservations - 1)
                slot.last_used = self._clock()
                if slot.reservations == 0 and slot.state is SlotState.BUSY:
                    slot.state = SlotState.READY
            self._condition.notify_all()

    # -------------------------------------------------------------- slot loading

    def _reserve_slot_locked(self, account_id: str) -> InstanceSlot | None:
        if len(self._slots) < self._max_contexts():
            slot = InstanceSlot(
                slot_id=f"slot-{next(self._counter)}",
                state=SlotState.LOADING,
                current_account_id=account_id,
                reservations=1,
                last_used=self._clock(),
            )
            self._slots[slot.slot_id] = slot
            return slot

        idle = [
            slot
            for slot in self._slots.values()
            if slot.in_flight == 0
            and slot.state in (SlotState.READY, SlotState.COOLDOWN, SlotState.EMPTY)
        ]
        if not idle:
            return None
        slot = min(idle, key=lambda item: item.last_used)
        slot.state = SlotState.LOADING
        slot.current_account_id = account_id
        slot.reservations = 1
        return slot

    async def _load_slot(
        self,
        slot: InstanceSlot,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str | None,
    ) -> None:
        cdp = await self._cdp_provider()
        old_context_id = slot.context_id
        old_realm_id = slot.realm_id
        if old_context_id:
            await slot.close_pages()
            with suppress(Exception):
                await cdp.dispose_browser_context(old_context_id)
            slot.context_id = ""
            slot.session = None
            if self._on_slot_retired is not None:
                with suppress(Exception):
                    self._on_slot_retired(old_realm_id)

        context_id = await cdp.create_browser_context()
        realm_id = f"{slot.slot_id}-g{slot.generation + 1}"
        try:
            session = await self._session_factory(
                cdp,
                context_id=context_id,
                auth_file=auth_file,
                auth_user=auth_user,
                realm_id=realm_id,
            )
        except Exception:
            with suppress(Exception):
                await cdp.dispose_browser_context(context_id)
            raise

        slot.context_id = context_id
        slot.session = session
        slot.auth_file = auth_file
        slot.auth_user = session.active_context.auth_user
        slot.generation += 1
        slot.current_account_id = account_id
        slot.state = SlotState.READY
        slot.last_used = self._clock()
        log.info(
            "[pool] slot loaded: slot=%s account=%s context=%s live=%d/%d",
            slot.slot_id,
            account_id,
            context_id,
            len(self._slots),
            self._max_contexts(),
        )

    # --------------------------------------------------------------- lease types

    def _lease_existing_locked(
        self,
        account_id: str,
        auth_file: str | None,
        auth_user: str,
        channel: str,
        surface: str,
    ) -> InstanceLease | None:
        for slot in self._slots.values():
            if slot.current_account_id != account_id:
                continue
            if slot.state not in (SlotState.READY, SlotState.BUSY):
                continue
            if slot.state is SlotState.READY:
                slot.state = SlotState.BUSY
            slot.auth_file = auth_file or slot.auth_file
            slot.auth_user = auth_user or slot.auth_user
            slot.reservations += 1
            return self._lease_for_slot(slot, channel, surface, is_borrow=False)
        return None

    def _lease_borrow_locked(
        self,
        borrow_accounts: set[str],
        channel: str,
        surface: str,
    ) -> InstanceLease | None:
        for slot in sorted(self._slots.values(), key=lambda item: item.last_used):
            if slot.current_account_id not in borrow_accounts:
                continue
            if slot.in_flight > 0 or slot.state not in (
                SlotState.READY,
                SlotState.BUSY,
            ):
                continue
            if not self._page_ready(slot, surface):
                continue
            slot.state = SlotState.BUSY
            slot.reservations += 1
            return self._lease_for_slot(slot, channel, surface, is_borrow=True)
        return None

    def _lease_for_slot(
        self,
        slot: InstanceSlot,
        channel: str,
        surface: str,
        *,
        is_borrow: bool,
    ) -> InstanceLease:
        return InstanceLease(
            account_id=slot.current_account_id,
            channel=channel,
            surface=surface,
            slot_id=slot.slot_id,
            realm_id=slot.realm_id,
            context_id=slot.context_id,
            adapter=slot,
            is_borrow=is_borrow,
            slot_index=int(slot.slot_id.rsplit("-", 1)[-1])
            if slot.slot_id.rsplit("-", 1)[-1].isdigit()
            else None,
        )

    @staticmethod
    def _page_ready(slot: InstanceSlot, surface: str) -> bool:
        if surface == "build":
            return slot.build_ready()
        return slot.chat_ready()

    async def _ensure_surface(self, slot: InstanceSlot, surface: str) -> None:
        if surface != "build":
            return
        cdp = await self._cdp_provider()
        await slot.ensure_build_surface(cdp)

    def _touch_locked(self, slot: InstanceSlot) -> None:
        slot.last_used = self._clock()
        if slot.state is SlotState.READY and slot.reservations > 0:
            slot.state = SlotState.BUSY

    # -------------------------------------------------------------- retirement

    async def retire_idle(self, *, max_idle_seconds: float | None = None) -> int:
        """Reserve and close idle slots, oldest first."""
        async with self._condition:
            now = self._clock()
            candidates = [
                slot
                for slot in self._slots.values()
                if slot.in_flight == 0
                and slot.state in (SlotState.READY, SlotState.COOLDOWN, SlotState.EMPTY)
                and (
                    max_idle_seconds is None or now - slot.last_used >= max_idle_seconds
                )
            ]
            for slot in candidates:
                slot.state = SlotState.RETIRING
        if not candidates:
            return 0
        await self._close_slots(candidates)
        async with self._condition:
            for slot in candidates:
                self._slots.pop(slot.slot_id, None)
            self._condition.notify_all()
        return len(candidates)

    async def close(self) -> None:
        async with self._condition:
            slots = list(self._slots.values())
            self._slots.clear()
            self._condition.notify_all()
        await self._close_slots(slots)

    async def _close_slots(self, slots: list[InstanceSlot]) -> None:
        cdp = None
        with suppress(Exception):
            cdp = await self._cdp_provider()
        for slot in slots:
            realm_id = slot.realm_id
            await slot.close_pages()
            if cdp is not None and slot.context_id:
                with suppress(Exception):
                    await cdp.dispose_browser_context(slot.context_id)
            if self._on_slot_retired is not None:
                with suppress(Exception):
                    self._on_slot_retired(realm_id)
            log.info(
                "[pool] slot retired: slot=%s account=%s",
                slot.slot_id,
                slot.current_account_id,
            )

    def _drop_slot_locked(self, slot: InstanceSlot) -> None:
        self._slots.pop(slot.slot_id, None)


__all__ = [
    "InstanceCapacityError",
    "InstanceLease",
    "InstancePool",
    "InstanceSlot",
    "SlotState",
]
