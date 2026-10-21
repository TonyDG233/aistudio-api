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
from aistudio_api.infrastructure.gateway.pooled_session import PooledSession
from aistudio_api.infrastructure.utils.logger import get_logger

BUILD_CHANNEL_READY_TIMEOUT_S = 60.0

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
    session: PooledSession | None = None
    generation: int = 0
    reservations: int = 0
    pending_pages: int = 0
    pending_channel: str = ""
    chat_counted: bool = False
    build_counted: bool = False
    build_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, repr=False, compare=False
    )
    chat_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, repr=False, compare=False
    )
    group_name: str = ""
    channel_role: str = ""
    last_used: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)
    pinned: bool = False
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

        async with self.build_lock:
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
        self.chat_counted = False
        self.build_counted = False
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


SessionFactory = Callable[..., Awaitable[PooledSession]]


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
        self._session_factory = session_factory or PooledSession.bootstrap
        self._on_slot_retired = on_slot_retired
        self._wait_timeout_s = max(0.0, wait_timeout_s)
        self._slots: dict[str, InstanceSlot] = {}
        self._counter = itertools.count(1)
        self._condition = asyncio.Condition()

    # ------------------------------------------------------------------ config

    def _max_contexts(self) -> int:
        if self._features_provider is None:
            return 1
        try:
            features = self._features_provider()
            routing = features.get("routing")
            raw = routing if isinstance(routing, dict) else {}
            return max(1, int(raw.get("max_contexts", 1)))
        except (TypeError, ValueError, AttributeError):
            return 1

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
        return self._page_counts_locked()

    def _slot_page_counts(self, slot: InstanceSlot) -> dict[str, int]:
        # Count acquired surface pages, not the bootstrap chat page that a
        # build-only context creates for BotGuard. ``pending_pages`` is the
        # reservation for a surface currently being created; it must not be
        # added to an already-counted page (that would double-count a build
        # page during ensure_build_surface).
        if (
            slot.state in (SlotState.READY, SlotState.COOLDOWN, SlotState.EMPTY)
            and slot.in_flight == 0
            and slot.pending_pages == 0
        ):
            if slot.chat_counted and not slot.chat_ready():
                slot.chat_counted = False
            if slot.build_counted and not slot.build_ready():
                slot.build_counted = False
        chat = 1 if slot.chat_counted else 0
        build = 1 if slot.build_counted else 0
        if slot.pending_pages:
            pending_channel = slot.pending_channel or slot.channel_role
            if pending_channel == "chat":
                chat = max(chat, 1)
            elif pending_channel == "build":
                build = max(build, 1)
        return {"chat": chat, "build": build}

    def _page_counts_locked(
        self, *, exclude_slot_id: str | None = None
    ) -> dict[str, int]:
        counts = {"chat": 0, "build": 0}
        for slot in self._slots.values():
            if exclude_slot_id and slot.slot_id == exclude_slot_id:
                continue
            slot_counts = self._slot_page_counts(slot)
            counts["chat"] += slot_counts["chat"]
            counts["build"] += slot_counts["build"]
        return counts

    def _group_page_count_locked(
        self, group_name: str, *, exclude_slot_id: str | None = None
    ) -> int:
        total = 0
        for slot in self._slots.values():
            if exclude_slot_id and slot.slot_id == exclude_slot_id:
                continue
            if slot.group_name == group_name:
                total += sum(self._slot_page_counts(slot).values())
        return total

    def _features(self) -> dict[str, object]:
        if self._features_provider is None:
            return {}
        try:
            return dict(self._features_provider())
        except Exception:
            return {}

    def channel_max_instances(self, channel: str) -> int | None:
        """该渠道允许并存的实例数；未配置返回 None（只受全局实例上限约束）。"""
        features = self._features()
        raw_channels = features.get("channels")
        channels = raw_channels if isinstance(raw_channels, dict) else {}
        key = "chat" if channel in ("chat", "pg") else channel
        raw = channels.get(key)
        spec = raw if isinstance(raw, dict) else {}
        if "max_instances" not in spec:
            return None
        try:
            return max(0, int(spec.get("max_instances", 0)))
        except (TypeError, ValueError):
            return None

    def channel_enabled(self, channel: str) -> bool:
        features = self._features()
        raw_channels = features.get("channels")
        channels = raw_channels if isinstance(raw_channels, dict) else {}
        key = "chat" if channel in ("chat", "pg") else channel
        raw = channels.get(key)
        spec = raw if isinstance(raw, dict) else {}
        if key == "build":
            return bool(spec.get("enabled", features.get("build_surface", False)))
        return bool(spec.get("enabled", True))

    def _channel_max_pages(self, channel: str) -> int:
        features = self._features()
        raw_channels = features.get("channels")
        channels = raw_channels if isinstance(raw_channels, dict) else {}
        raw = channels.get(channel)
        spec = raw if isinstance(raw, dict) else {}
        try:
            configured = max(0, int(spec.get("max_pages", 2)))
        except (TypeError, ValueError):
            configured = 2
        if channel in ("chat", "build"):
            # Page count is not an independent user budget: one channel page is
            # one instance. Deriving it from max_instances keeps every path
            # (startup, manual create, switch_to, 429 request acquire) bounded
            # by the same channel cap.
            instance_cap = self.channel_max_instances(channel)
            return configured if instance_cap is None else max(0, instance_cap)
        return configured

    def _global_max_pages(self) -> int:
        # Total page budget equals the user-visible instance limit.
        return max(1, self._max_contexts())

    def _check_page_budget_locked(
        self,
        *,
        channel: str,
        group_name: str | None,
        group_max_pages: int | None,
        adding: int = 1,
        exclude_slot_id: str | None = None,
    ) -> None:
        counts = self._page_counts_locked(exclude_slot_id=exclude_slot_id)
        if sum(counts.values()) + adding > self._global_max_pages():
            raise InstanceCapacityError(
                f"page budget exhausted: global>{self._global_max_pages()}"
            )
        if counts.get(channel, 0) + adding > self._channel_max_pages(channel):
            raise InstanceCapacityError(
                f"page budget exhausted: {channel}>{self._channel_max_pages(channel)}"
            )
        if group_name and group_max_pages is not None:
            group_count = self._group_page_count_locked(
                group_name, exclude_slot_id=exclude_slot_id
            )
            if group_count + adding > group_max_pages:
                raise InstanceCapacityError(
                    f"page budget exhausted: group {group_name}>{group_max_pages}"
                )

    def has_active_work(self) -> bool:
        """True while a slot is loading, reserved, or serving a request."""
        return any(
            slot.state is SlotState.LOADING
            or slot.in_flight > 0
            or slot.pending_pages > 0
            for slot in self._slots.values()
        )

    def budget_snapshot(self) -> dict[str, object]:
        channel_limits: dict[str, int] = {}
        for channel in ("chat", "build", "official"):
            channel_limits[channel] = self._channel_max_pages(channel)
        return {
            "global_max_pages": self._global_max_pages(),
            "channel_max_pages": channel_limits,
            "max_contexts": self.max_contexts,
            "pages": self.page_counts(),
            "contexts": self.live_contexts,
        }

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
                "chat_counted": slot.chat_counted,
                "build_counted": slot.build_counted,
                "pending_pages": slot.pending_pages,
                "pages": self._slot_page_counts(slot),
                "last_used": slot.last_used,
                "created_at": slot.created_at,
                "pinned": slot.pinned,
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
        group_name: str | None = None,
        group_max_pages: int | None = None,
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
                    account_id,
                    auth_file,
                    auth_user or "0",
                    channel,
                    surface,
                    group_name,
                    group_max_pages,
                )
                if lease is not None:
                    if self._page_ready(lease.adapter, surface):
                        self._touch_locked(lease.adapter)
                        return lease
                    needs_surface = True
                    break

                # A browser can be recycled while its slot record remains. Revive
                # that same-account slot instead of treating it as capacity.
                slot = self._reserve_stale_slot_locked(
                    account_id,
                    auth_file=auth_file,
                    auth_user=auth_user or "0",
                    channel=channel,
                    group_name=group_name,
                    group_max_pages=group_max_pages,
                )
                if slot is not None:
                    break

                if borrow_accounts:
                    lease = self._lease_borrow_locked(
                        borrow_accounts,
                        channel,
                        surface,
                        group_name,
                        group_max_pages,
                    )
                    if lease is not None:
                        self._touch_locked(lease.adapter)
                        return lease

                slot = self._reserve_slot_locked(
                    account_id,
                    channel=channel,
                    group_name=group_name,
                    group_max_pages=group_max_pages,
                )
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
        except BaseException:
            if lease is None:
                if slot is not None:
                    await self._discard_slot(slot)
            else:
                await self.release(lease)
            raise

        async with self._condition:
            slot.reservations = max(1, slot.reservations)
            self._touch_locked(slot)
            self._condition.notify_all()
            return self._lease_for_slot(slot, channel, surface, is_borrow=False)

    async def create_instance(
        self,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str = "0",
        channel: str = "chat",
    ) -> InstanceSlot:
        """Activate one explicit instance (account + channel) and pin it.

        A second channel for the same account reuses that account's context and
        only adds its channel page; it never creates a second context.
        """
        async with self._condition:
            existing = next(
                (
                    slot
                    for slot in self._slots.values()
                    if slot.current_account_id == account_id
                    and slot.state is not SlotState.RETIRING
                ),
                None,
            )
            if existing is not None:
                existing.pinned = True
                slot = existing
                created = False
            else:
                if len(self._slots) >= self._max_contexts():
                    raise InstanceCapacityError(
                        f"instance capacity exhausted: live={len(self._slots)} "
                        f"limit={self._max_contexts()}"
                    )
                self._check_page_budget_locked(
                    channel=channel,
                    group_name=None,
                    group_max_pages=None,
                )
                slot = InstanceSlot(
                    slot_id=f"slot-{next(self._counter)}",
                    state=SlotState.LOADING,
                    current_account_id=account_id,
                    auth_file=auth_file,
                    auth_user=auth_user or "0",
                    channel_role=channel,
                    pending_pages=1,
                    pending_channel=channel,
                    pinned=True,
                    last_used=self._clock(),
                )
                self._slots[slot.slot_id] = slot
                created = True
                self._condition.notify_all()
        if not created:
            await self.ensure_channel(slot.slot_id, channel)
            return slot
        try:
            await self._load_slot(
                slot,
                account_id=account_id,
                auth_file=auth_file,
                auth_user=auth_user,
            )
            if channel == "build":
                await self._ensure_surface(slot, "build")
        except BaseException:
            await self._discard_slot(slot)
            raise
        async with self._condition:
            slot.state = SlotState.READY
            slot.reservations = 0
            slot.pending_pages = 0
            self._condition.notify_all()
        return slot

    async def check_create_budget(
        self,
        channel: str,
        *,
        account_id: str | None = None,
        group_name: str | None = None,
        group_max_pages: int | None = None,
    ) -> None:
        """Preflight an explicit instance creation without allocating anything."""
        async with self._condition:
            existing = (
                next(
                    (
                        slot
                        for slot in self._slots.values()
                        if slot.current_account_id == account_id
                        and slot.state is not SlotState.RETIRING
                    ),
                    None,
                )
                if account_id
                else None
            )
            if existing is None and len(self._slots) >= self._max_contexts():
                raise InstanceCapacityError(
                    f"instance capacity exhausted: live={len(self._slots)} "
                    f"limit={self._max_contexts()}"
                )
            self._check_page_budget_locked(
                channel=channel,
                group_name=group_name,
                group_max_pages=group_max_pages,
            )

    async def ensure_channel(self, slot_id: str, channel: str) -> None:
        """Activate one channel page on an existing explicit instance."""
        surface = "build" if channel == "build" else "chat"
        async with self._condition:
            slot = self._slots.get(slot_id)
            if slot is None:
                raise InstanceCapacityError(f"slot missing: {slot_id}")
            if slot.state is SlotState.RETIRING:
                raise InstanceCapacityError(f"slot retiring: {slot_id}")
            if self._page_ready(slot, surface):
                return
            self._check_page_budget_locked(
                channel=surface,
                group_name=slot.group_name or None,
                group_max_pages=None,
            )
            slot.pending_pages = max(1, slot.pending_pages + 1)
            slot.pending_channel = surface
        try:
            if surface == "build":
                await asyncio.wait_for(
                    self._ensure_surface(slot, "build"),
                    timeout=BUILD_CHANNEL_READY_TIMEOUT_S,
                )
            else:
                await asyncio.wait_for(
                    self._ensure_chat_surface(slot),
                    timeout=BUILD_CHANNEL_READY_TIMEOUT_S,
                )
        except TimeoutError as exc:
            async with self._condition:
                slot.pending_pages = max(0, slot.pending_pages - 1)
                slot.pending_channel = ""
            raise InstanceCapacityError(
                f"渠道启动超时（{BUILD_CHANNEL_READY_TIMEOUT_S:.0f}s）"
            ) from exc
        except BaseException:
            async with self._condition:
                slot.pending_pages = max(0, slot.pending_pages - 1)
                slot.pending_channel = ""
            raise
        async with self._condition:
            slot.pending_pages = max(0, slot.pending_pages - 1)
            slot.pending_channel = ""
            slot.channel_role = surface
            self._condition.notify_all()

    async def close_channel(self, slot_id: str, channel: str) -> None:
        """Close one channel page while keeping the account context alive."""
        surface = "build" if channel == "build" else "chat"
        session = None
        build_surface = None
        async with self._condition:
            slot = self._slots.get(slot_id)
            if slot is None:
                return
            if slot.in_flight > 0:
                raise InstanceCapacityError(f"slot busy: {slot_id}")
            if surface == "chat":
                session = slot.session
                slot.session = None
                slot.chat_counted = False
            else:
                build_surface = slot.build_surface
                slot.build_surface = None
                slot.build_counted = False
            slot.channel_role = (
                "build" if slot.build_counted else ("chat" if slot.chat_counted else "")
            )
            self._condition.notify_all()
        if session is not None:
            with suppress(Exception):
                await session.close()
        if build_surface is not None:
            page = getattr(getattr(build_surface, "state", None), "page", None)
            if page is not None:
                with suppress(Exception):
                    await page.close()

    async def _ensure_chat_surface(self, slot: InstanceSlot) -> None:
        async with slot.chat_lock:
            if slot.chat_ready():
                slot.chat_counted = True
                return
            if not slot.context_id:
                raise InstanceCapacityError(f"slot has no context: {slot.slot_id}")
            cdp = await self._cdp_provider()
            session = await self._session_factory(
                cdp,
                context_id=slot.context_id,
                auth_file=slot.auth_file,
                auth_user=slot.auth_user,
                realm_id=slot.realm_id,
            )
            slot.session = session
            slot.chat_counted = True

    async def close_instance(self, slot_id: str) -> None:
        """Close one explicit instance; refuse while it still has requests."""
        async with self._condition:
            slot = self._slots.get(slot_id)
            if slot is None:
                return
            if slot.in_flight > 0:
                raise InstanceCapacityError(f"slot busy: {slot_id}")
            slot.state = SlotState.RETIRING
            self._slots.pop(slot_id, None)
            self._condition.notify_all()
        await self._close_slots([slot])

    async def reassign_instance(
        self,
        slot_id: str,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str | None,
        channel: str = "chat",
    ) -> None:
        """Rebind an explicit instance to another account, keeping its identity."""
        await self.rebind(
            slot_id,
            account_id=account_id,
            auth_file=auth_file,
            auth_user=auth_user,
            channel=channel,
        )
        slot = self._slots.get(slot_id)
        if slot is not None:
            slot.pinned = True

    async def rebind(
        self,
        slot_id: str,
        *,
        account_id: str,
        auth_file: str | None,
        auth_user: str | None,
        channel: str,
        group_name: str = "",
        group_max_pages: int | None = None,
    ) -> None:
        """Rebind an idle slot to another account (background switch)."""
        async with self._condition:
            slot = self._slots.get(slot_id)
            if slot is None:
                raise InstanceCapacityError(f"unknown slot: {slot_id}")
            if slot.in_flight > 0:
                raise InstanceCapacityError(f"slot busy: {slot_id}")
            if slot.state not in (
                SlotState.READY,
                SlotState.COOLDOWN,
                SlotState.EMPTY,
            ):
                raise InstanceCapacityError(
                    f"slot not idle: {slot_id} state={slot.state.value}"
                )
            self._check_page_budget_locked(
                channel=channel,
                group_name=group_name or None,
                group_max_pages=group_max_pages,
                exclude_slot_id=slot.slot_id,
            )
            slot.state = SlotState.LOADING
            slot.current_account_id = account_id
            slot.group_name = group_name
            slot.channel_role = channel
            slot.chat_counted = False
            slot.build_counted = False
            slot.pending_pages = 1
            slot.reservations = 0
        try:
            await self._load_slot(
                slot,
                account_id=account_id,
                auth_file=auth_file,
                auth_user=auth_user,
            )
            if channel == "build":
                async with self._condition:
                    slot.state = SlotState.LOADING
                await self._ensure_surface(slot, "build")
        except BaseException:
            await self._discard_slot(slot)
            raise
        async with self._condition:
            slot.reservations = 0
            slot.state = SlotState.READY
            self._condition.notify_all()

    async def release(self, lease: InstanceLease) -> None:
        if lease.released:
            return
        async with self._condition:
            slot = self._slots.get(lease.slot_id)
            if slot is not None:
                slot.reservations = max(0, slot.reservations - 1)
                slot.last_used = self._clock()
                if slot.reservations == 0:
                    slot.pending_pages = 0
                    slot.pending_channel = ""
                    if not slot.chat_ready():
                        slot.chat_counted = False
                    if not slot.build_ready():
                        slot.build_counted = False
                    if slot.state is SlotState.BUSY:
                        slot.state = SlotState.READY
            lease.released = True
            self._condition.notify_all()

    # -------------------------------------------------------------- slot loading

    def _reserve_slot_locked(
        self,
        account_id: str,
        *,
        channel: str,
        group_name: str | None,
        group_max_pages: int | None,
    ) -> InstanceSlot | None:
        idle = [
            slot
            for slot in self._slots.values()
            if slot.in_flight == 0
            and not slot.pinned
            and slot.state in (SlotState.READY, SlotState.COOLDOWN, SlotState.EMPTY)
        ]

        if len(self._slots) < self._max_contexts():
            try:
                self._check_page_budget_locked(
                    channel=channel,
                    group_name=group_name,
                    group_max_pages=group_max_pages,
                )
            except InstanceCapacityError:
                # Page budget is full. Wait for an existing slot to become idle or
                # ready; an in-flight same-page request may satisfy this one.
                if not idle:
                    return None
            else:
                slot = InstanceSlot(
                    slot_id=f"slot-{next(self._counter)}",
                    state=SlotState.LOADING,
                    current_account_id=account_id,
                    reservations=1,
                    pending_pages=1,
                    group_name=group_name or "",
                    channel_role=channel,
                    last_used=self._clock(),
                )
                self._slots[slot.slot_id] = slot
                return slot

        if not idle:
            return None
        for slot in sorted(idle, key=lambda item: item.last_used):
            try:
                self._check_page_budget_locked(
                    channel=channel,
                    group_name=group_name,
                    group_max_pages=group_max_pages,
                    exclude_slot_id=slot.slot_id,
                )
            except InstanceCapacityError:
                continue
            slot.state = SlotState.LOADING
            slot.current_account_id = account_id
            slot.reservations = 1
            slot.chat_counted = False
            slot.build_counted = False
            slot.pending_pages = 1
            slot.group_name = group_name or ""
            slot.channel_role = channel
            return slot
        return None

    def _reserve_stale_slot_locked(
        self,
        account_id: str,
        *,
        auth_file: str | None,
        auth_user: str,
        channel: str,
        group_name: str | None,
        group_max_pages: int | None,
    ) -> InstanceSlot | None:
        """Reuse a same-account slot whose pages/context died with the browser."""
        for slot in self._slots.values():
            if slot.current_account_id != account_id:
                continue
            if slot.in_flight > 0 or slot.state in (
                SlotState.LOADING,
                SlotState.RETIRING,
            ):
                continue
            if slot.chat_ready() or slot.build_ready():
                continue
            try:
                self._check_page_budget_locked(
                    channel=channel,
                    group_name=group_name,
                    group_max_pages=group_max_pages,
                    exclude_slot_id=slot.slot_id,
                )
            except InstanceCapacityError:
                continue
            slot.state = SlotState.LOADING
            slot.current_account_id = account_id
            slot.auth_file = auth_file or slot.auth_file
            slot.auth_user = auth_user or slot.auth_user
            slot.group_name = group_name or ""
            slot.channel_role = channel
            slot.pending_pages = 1
            slot.pending_channel = channel
            slot.chat_counted = False
            slot.build_counted = False
            slot.reservations = 0
            return slot
        return None

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
        slot.chat_counted = False
        slot.build_counted = False
        if old_context_id:
            await slot.close_pages()
            with suppress(Exception):
                await cdp.dispose_browser_context(old_context_id)
            slot.context_id = ""
            slot.session = None
            if self._on_slot_retired is not None:
                with suppress(Exception):
                    self._on_slot_retired(old_realm_id)

        realm_id = f"{slot.slot_id}-g{slot.generation + 1}"
        context_id = ""
        session = None
        last_error: Exception | None = None
        # Cold-start BotGuard capture occasionally misses the wire template on a
        # fresh context. Recreate the context once before failing the request.
        for attempt in range(3):
            context_id = await cdp.create_browser_context()
            slot.context_id = context_id
            try:
                session = await self._session_factory(
                    cdp,
                    context_id=context_id,
                    auth_file=auth_file,
                    auth_user=auth_user,
                    realm_id=realm_id,
                )
                break
            except Exception as exc:
                last_error = exc
                with suppress(Exception):
                    await cdp.dispose_browser_context(context_id)
                slot.context_id = ""
                if attempt < 2:
                    await asyncio.sleep(2.0)
            except BaseException:
                with suppress(Exception):
                    await cdp.dispose_browser_context(context_id)
                slot.context_id = ""
                raise
        if session is None:
            raise last_error or RuntimeError("pooled session bootstrap failed")

        slot.context_id = context_id
        slot.session = session
        slot.auth_file = auth_file
        slot.auth_user = session.active_context.auth_user
        slot.generation += 1
        slot.current_account_id = account_id
        slot.state = SlotState.READY
        slot.pending_pages = 0
        slot.pending_channel = ""
        if slot.channel_role == "build":
            # The chat page is only a BotGuard bootstrap page for a build-only
            # context. Keep the context/cookies, but close the hidden page so it
            # neither counts as an instance page nor costs a renderer.
            with suppress(Exception):
                await session.close()
            slot.session = None
            slot.chat_counted = False
            slot.build_counted = True
        elif slot.channel_role == "chat":
            slot.chat_counted = True
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
        group_name: str | None = None,
        group_max_pages: int | None = None,
    ) -> InstanceLease | None:
        for slot in self._slots.values():
            if slot.current_account_id != account_id:
                continue
            if slot.state not in (SlotState.READY, SlotState.BUSY):
                continue
            if surface == "chat" and not slot.chat_ready():
                continue
            counted = slot.chat_counted if surface == "chat" else slot.build_counted
            pending = slot.pending_pages > 0 and slot.channel_role == surface
            needs_accounting = not counted and not pending
            if needs_accounting:
                try:
                    self._check_page_budget_locked(
                        channel=surface,
                        group_name=group_name,
                        group_max_pages=group_max_pages,
                    )
                except InstanceCapacityError:
                    continue
            if slot.state is SlotState.READY:
                slot.state = SlotState.BUSY
            slot.auth_file = auth_file or slot.auth_file
            slot.auth_user = auth_user or slot.auth_user
            slot.group_name = group_name or slot.group_name
            slot.channel_role = surface
            if surface == "chat":
                slot.chat_counted = True
            else:
                slot.build_counted = True
                if needs_accounting and not slot.build_ready():
                    slot.pending_pages += 1
            slot.reservations += 1
            return self._lease_for_slot(slot, channel, surface, is_borrow=False)
        return None

    def _lease_borrow_locked(
        self,
        borrow_accounts: set[str],
        channel: str,
        surface: str,
        group_name: str | None = None,
        group_max_pages: int | None = None,
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
            counted = slot.chat_counted if surface == "chat" else slot.build_counted
            if not counted:
                try:
                    self._check_page_budget_locked(
                        channel=surface,
                        group_name=group_name,
                        group_max_pages=group_max_pages,
                    )
                except InstanceCapacityError:
                    continue
                if surface == "chat":
                    slot.chat_counted = True
                else:
                    slot.build_counted = True
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
        if surface == "chat":
            await self._ensure_chat_surface(slot)
            return
        if surface != "build":
            return
        cdp = await self._cdp_provider()
        await slot.ensure_build_surface(cdp)
        slot.pending_pages = 0
        slot.pending_channel = ""
        slot.build_counted = True

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
                and not slot.pinned
                and slot.state in (SlotState.READY, SlotState.COOLDOWN, SlotState.EMPTY)
                and (
                    max_idle_seconds is None or now - slot.last_used >= max_idle_seconds
                )
            ]
            for slot in candidates:
                slot.state = SlotState.RETIRING
        if not candidates:
            return 0
        try:
            await self._close_slots(candidates)
        finally:
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

    async def _discard_slot(self, slot: InstanceSlot) -> None:
        if slot.context_id:
            await self._close_slots([slot])
        else:
            await slot.close_pages()
        async with self._condition:
            self._slots.pop(slot.slot_id, None)
            self._condition.notify_all()

    def _drop_slot_locked(self, slot: InstanceSlot) -> None:
        self._slots.pop(slot.slot_id, None)


__all__ = [
    "InstanceCapacityError",
    "InstanceLease",
    "InstancePool",
    "InstanceSlot",
    "SlotState",
]
