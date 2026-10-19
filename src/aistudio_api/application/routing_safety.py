"""Request-scoped routing budgets and a process-global circuit breaker.

``RoutingBudget`` tracks how many account/instance switches and defers a single
request may consume. Same-account retries are counted for diagnostics but do not
consume either budget. Instances are not shared between requests.

``CircuitBreaker`` guards the account pool against a stream of switch failures.
It is process-global and thread-safe. Failures live in a rolling window; once
the threshold is reached the breaker opens for ``cooldown_seconds``. When the
cooldown expires the breaker becomes half-open (``is_open`` reports ``False``)
while recent failures are retained, so the next failed switch re-opens it
immediately. A successful switch clears both failures and open state.

These primitives deliberately do not log; the routing pipeline owns logging.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock


class RoutingBudget:
    """Per-request budget for switch and defer decisions."""

    def __init__(self, max_switches: int = 2, max_defers: int = 2) -> None:
        self._max_switches = max(0, max_switches)
        self._max_defers = max(0, max_defers)
        self._switches = 0
        self._defers = 0
        self._retries = 0
        self._last_defer_reason = ""

    def can_switch(self) -> bool:
        """Return whether another switch fits in the budget."""
        return self._switches < self._max_switches

    def can_defer(self) -> bool:
        """Return whether another defer fits in the budget."""
        return self._defers < self._max_defers

    def record_switch(self) -> bool:
        """Consume one switch. Return False when the budget is exhausted."""
        if not self.can_switch():
            return False
        self._switches += 1
        return True

    def record_defer(self, reason: str = "") -> bool:
        """Consume one defer. Return False when the budget is exhausted."""
        if not self.can_defer():
            return False
        self._defers += 1
        self._last_defer_reason = reason
        return True

    def record_retry(self) -> None:
        """Count a same-account retry without consuming switch/defer budget."""
        self._retries += 1

    def snapshot(self) -> dict[str, object]:
        """Return a diagnostic view of the current counters."""
        return {
            "max_switches": self._max_switches,
            "switches": self._switches,
            "max_defers": self._max_defers,
            "defers": self._defers,
            "retries": self._retries,
            "exhausted": (
                self._switches >= self._max_switches or self._defers >= self._max_defers
            ),
        }


class CircuitBreaker:
    """Process-global, thread-safe rolling-window circuit breaker."""

    def __init__(
        self,
        *,
        window_seconds: int = 600,
        failure_threshold: int = 5,
        cooldown_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = Lock()
        self._failures: list[float] = []
        self._open_until: float | None = None
        self._tripped = False

    def is_open(self, now: float | None = None) -> bool:
        """Return whether the breaker currently rejects switch attempts."""
        moment = self._resolve(now)
        with self._lock:
            return self._is_open_locked(moment)

    def record_switch_failure(self, now: float | None = None) -> bool:
        """Record a failed switch; True only when this call opens the breaker."""
        moment = self._resolve(now)
        with self._lock:
            self._failures.append(moment)
            self._purge_locked(moment)
            if self._open_until is not None:
                if moment < self._open_until:
                    return False
                self._open_until = None
            if len(self._failures) >= self._failure_threshold:
                self._open_until = moment + self._cooldown_seconds
                self._tripped = True
                return True
            return False

    def record_switch_success(self, now: float | None = None) -> None:
        """Clear failures and open state after a successful switch."""
        del now
        with self._lock:
            self._failures.clear()
            self._open_until = None
            self._tripped = False

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        """Return a diagnostic view, purging expired failures/open state."""
        moment = self._resolve(now)
        with self._lock:
            is_open = self._is_open_locked(moment)
            open_until = self._open_until
            retry_after = 0.0
            if is_open and open_until is not None:
                retry_after = round(max(0.0, open_until - moment), 1)
            return {
                "open": is_open,
                "failures": len(self._failures),
                "window_seconds": self._window_seconds,
                "failure_threshold": self._failure_threshold,
                "cooldown_seconds": self._cooldown_seconds,
                "open_until": open_until,
                "retry_after_seconds": retry_after,
            }

    def _resolve(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def _purge_locked(self, moment: float) -> None:
        cutoff = moment - self._window_seconds
        self._failures = [ts for ts in self._failures if ts >= cutoff]

    def _is_open_locked(self, moment: float) -> bool:
        self._purge_locked(moment)
        if self._open_until is not None:
            if moment < self._open_until:
                return True
            self._open_until = None
        if len(self._failures) < self._failure_threshold:
            self._tripped = False
        if not self._tripped and len(self._failures) >= self._failure_threshold:
            self._open_until = moment + self._cooldown_seconds
            self._tripped = True
            return True
        return False
