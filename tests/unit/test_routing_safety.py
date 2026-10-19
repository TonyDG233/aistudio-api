"""Behavior contracts for routing budgets and the circuit breaker."""

from __future__ import annotations

from aistudio_api.application.routing_safety import CircuitBreaker, RoutingBudget


class FakeClock:
    """Deterministic monotonic clock for circuit-breaker tests."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(
    clock: FakeClock,
    *,
    window_seconds: int = 600,
    failure_threshold: int = 5,
    cooldown_seconds: int = 300,
) -> CircuitBreaker:
    return CircuitBreaker(
        window_seconds=window_seconds,
        failure_threshold=failure_threshold,
        cooldown_seconds=cooldown_seconds,
        clock=clock,
    )


# --------------------------------------------------------------------------- #
# RoutingBudget
# --------------------------------------------------------------------------- #
def test_switch_budget_allows_two_then_exhausts() -> None:
    budget = RoutingBudget(max_switches=2)

    assert budget.can_switch() is True
    assert budget.record_switch() is True
    assert budget.record_switch() is True
    assert budget.can_switch() is False
    assert budget.record_switch() is False
    assert budget.snapshot()["switches"] == 2


def test_defer_budget_is_independent_from_switch_budget() -> None:
    budget = RoutingBudget(max_switches=2, max_defers=2)

    assert budget.record_defer("cooling") is True
    assert budget.record_defer("quota") is True
    assert budget.record_defer("again") is False
    assert budget.snapshot()["defers"] == 2

    assert budget.record_switch() is True
    assert budget.record_switch() is True
    assert budget.record_switch() is False
    assert budget.snapshot()["switches"] == 2


def test_retry_does_not_consume_switch_or_defer_budget() -> None:
    budget = RoutingBudget(max_switches=2, max_defers=2)

    for _ in range(5):
        budget.record_retry()

    snapshot = budget.snapshot()
    assert snapshot["retries"] == 5
    assert snapshot["switches"] == 0
    assert snapshot["defers"] == 0
    assert budget.can_switch() is True
    assert budget.can_defer() is True


def test_snapshot_reports_exhausted_when_a_budget_is_reached() -> None:
    budget = RoutingBudget(max_switches=2, max_defers=2)
    assert budget.snapshot()["exhausted"] is False

    budget.record_switch()
    budget.record_switch()
    assert budget.snapshot()["exhausted"] is True

    defers_only = RoutingBudget(max_switches=2, max_defers=1)
    defers_only.record_defer()
    assert defers_only.snapshot()["exhausted"] is True


def test_snapshot_returns_the_documented_keys() -> None:
    snapshot = RoutingBudget().snapshot()
    assert set(snapshot) == {
        "max_switches",
        "switches",
        "max_defers",
        "defers",
        "retries",
        "exhausted",
    }


def test_negative_caps_clamp_to_zero() -> None:
    budget = RoutingBudget(max_switches=-3, max_defers=-1)

    assert budget.can_switch() is False
    assert budget.can_defer() is False
    assert budget.record_switch() is False
    assert budget.record_defer("nope") is False
    assert budget.snapshot()["max_switches"] == 0
    assert budget.snapshot()["max_defers"] == 0
    assert budget.snapshot()["exhausted"] is True


def test_budgets_are_request_scoped_and_do_not_share_counters() -> None:
    first = RoutingBudget(max_switches=1)
    second = RoutingBudget(max_switches=1)

    assert first.record_switch() is True
    assert first.can_switch() is False
    assert second.can_switch() is True


# --------------------------------------------------------------------------- #
# CircuitBreaker
# --------------------------------------------------------------------------- #
def test_below_threshold_stays_closed() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(4):
        assert breaker.record_switch_failure() is False

    assert breaker.is_open() is False
    snapshot = breaker.snapshot()
    assert snapshot["open"] is False
    assert snapshot["failures"] == 4


def test_fifth_failure_opens_and_sixth_is_not_another_transition() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(4):
        assert breaker.record_switch_failure() is False
    assert breaker.record_switch_failure() is True

    assert breaker.is_open() is True

    clock.advance(10)
    assert breaker.record_switch_failure() is False
    assert breaker.is_open() is True

    snapshot = breaker.snapshot()
    assert snapshot["open"] is True
    assert snapshot["failures"] == 6
    assert snapshot["retry_after_seconds"] == 290.0


def test_cooldown_expiry_closes_the_breaker() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(5):
        breaker.record_switch_failure()
    assert breaker.is_open() is True

    clock.advance(300)
    assert breaker.is_open() is False
    assert breaker.snapshot()["open"] is False
    assert breaker.snapshot()["retry_after_seconds"] == 0.0

    clock.advance(1)
    assert breaker.is_open() is False


def test_new_failure_after_cooldown_reopens_immediately() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(5):
        breaker.record_switch_failure()
    clock.advance(300)
    assert breaker.is_open() is False

    clock.advance(1)
    assert breaker.record_switch_failure() is True
    assert breaker.is_open() is True


def test_success_resets_failures_and_open_state() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)

    for _ in range(5):
        breaker.record_switch_failure()
    assert breaker.is_open() is True

    breaker.record_switch_success()

    assert breaker.is_open() is False
    snapshot = breaker.snapshot()
    assert snapshot["failures"] == 0
    assert snapshot["open"] is False
    assert snapshot["open_until"] is None


def test_rolling_window_purges_old_failures() -> None:
    clock = FakeClock(1000.0)
    breaker = _breaker(clock, window_seconds=10, failure_threshold=5)

    breaker.record_switch_failure(now=1000.0)
    breaker.record_switch_failure(now=1001.0)
    breaker.record_switch_failure(now=1002.0)

    clock.now = 1013.0
    snapshot = breaker.snapshot()
    assert snapshot["failures"] == 0
    assert breaker.is_open() is False


def test_rolling_window_keeps_failures_inside_the_window() -> None:
    clock = FakeClock(1000.0)
    breaker = _breaker(clock, window_seconds=10, failure_threshold=5)

    breaker.record_switch_failure(now=1000.0)
    breaker.record_switch_failure(now=1010.0)

    clock.now = 1011.0
    assert breaker.snapshot()["failures"] == 1


def test_retry_after_is_rounded_to_one_decimal() -> None:
    clock = FakeClock(0.0)
    breaker = _breaker(
        clock, window_seconds=600, failure_threshold=1, cooldown_seconds=10
    )

    assert breaker.record_switch_failure(now=0.0) is True
    snapshot = breaker.snapshot(now=3.26)
    assert snapshot["retry_after_seconds"] == 6.7


def test_explicit_now_overrides_the_injected_clock() -> None:
    clock = FakeClock(50_000.0)
    breaker = _breaker(clock, failure_threshold=1, cooldown_seconds=10)

    assert breaker.record_switch_failure(now=100.0) is True
    assert breaker.is_open(now=105.0) is True
    assert breaker.is_open(now=110.0) is False


def test_snapshot_returns_the_documented_keys_and_not_open_retry() -> None:
    breaker = _breaker(FakeClock())

    snapshot = breaker.snapshot()
    assert set(snapshot) == {
        "open",
        "failures",
        "window_seconds",
        "failure_threshold",
        "cooldown_seconds",
        "open_until",
        "retry_after_seconds",
    }
    assert snapshot["open"] is False
    assert snapshot["retry_after_seconds"] == 0.0


def test_breaker_exposes_a_threading_lock() -> None:
    breaker = _breaker(FakeClock())

    assert breaker._lock.acquire(blocking=False) is True
    breaker._lock.release()
