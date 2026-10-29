"""D05-024/D05-035/D05-041: switch budget only; no dead breaker/background reorder."""

from __future__ import annotations

from aistudio_api.application import routing_safety
from aistudio_api.application.routing_safety import RoutingBudget


def test_default_switch_budget_is_two() -> None:
    budget = RoutingBudget()
    assert budget.max_switches == 2
    assert budget.can_switch()
    assert budget.record_switch("a")
    assert budget.record_switch("b")
    assert not budget.can_switch()
    assert not budget.record_switch("c")
    assert budget.snapshot()["switches"] == 2
    assert budget.snapshot()["exhausted"] is True


def test_retry_does_not_consume_switch_budget() -> None:
    budget = RoutingBudget(max_switches=1)
    budget.record_retry("204")
    budget.record_retry("404")
    assert budget.retries == 2
    assert budget.switches == 0
    assert budget.can_switch()


def test_zero_budget_never_switches() -> None:
    budget = RoutingBudget(max_switches=0)
    assert not budget.can_switch()
    assert not budget.record_switch("429")


def test_no_lease_defer_circuit_breaker_api() -> None:
    for legacy in ("CircuitBreaker", "record_defer", "can_defer"):
        assert not hasattr(routing_safety, legacy)
    budget = RoutingBudget()
    for legacy in ("record_defer", "can_defer", "lease", "reservation"):
        assert not hasattr(budget, legacy)
