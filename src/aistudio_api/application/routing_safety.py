"""Per-request routing budget (switch cap) with no dead circuit breaker.

D05-024/D05-035: only the consecutive-switch budget survives.  Same-account
retries are counted for diagnostics but never consume the switch budget.  There
is deliberately no defer budget, reservation, lease or circuit breaker here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoutingBudget:
    """Per-request switch budget."""

    max_switches: int = 2
    switches: int = 0
    retries: int = 0
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.max_switches = max(0, int(self.max_switches))

    def can_switch(self) -> bool:
        return self.switches < self.max_switches

    def record_switch(self, reason: str = "") -> bool:
        """Consume one switch; False means the budget is exhausted."""
        if not self.can_switch():
            return False
        self.switches += 1
        if reason:
            self.reasons.append(reason)
        return True

    def record_retry(self, reason: str = "") -> None:
        self.retries += 1
        if reason:
            self.reasons.append(reason)

    def snapshot(self) -> dict[str, object]:
        return {
            "max_switches": self.max_switches,
            "switches": self.switches,
            "retries": self.retries,
            "exhausted": self.switches >= self.max_switches,
            "reasons": list(self.reasons),
        }


__all__ = ["RoutingBudget"]
