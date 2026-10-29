"""Session context descriptor and single-page lifecycle primitives."""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage


@dataclass
class SessionContext:
    """Encapsulates a fully-bootstrapped, verified AI Studio page context."""

    page: CDPPage
    snap_key: str
    auth_user: str
    auth_file: str | None
    base_template: dict[str, object]
    tier: str = "Free"
    context_id: int = field(default_factory=itertools.count(1).__next__)
    created_at: float = field(default_factory=time.time)
    in_flight: int = 0
    drain_event: asyncio.Event = field(default_factory=asyncio.Event)

    def incr_in_flight(self) -> None:
        """Increment in_flight count and clear the drain signal."""
        self.in_flight += 1
        self.drain_event.clear()

    def decr_in_flight(self) -> None:
        """Decrement in_flight count and signal drain_event when empty."""
        self.in_flight = max(0, self.in_flight - 1)
        if self.in_flight == 0:
            self.drain_event.set()

    def is_usable(self) -> bool:
        return not self.page.is_closed() and bool(self.snap_key)
