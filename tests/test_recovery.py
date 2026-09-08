"""Recovery primitives + HealthHealer: backoff, circuits, verdicts, probes, probation."""

import time

from wiwi.core.recovery import Backoff, CircuitBreaker


class TestBackoff:
    def test_monotone_in_attempt(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        delays = [b.delay(i) for i in range(6)]
        assert delays == sorted(delays)
        assert delays[0] == 0.5
        assert delays[3] == 4.0

    def test_cap_clamps(self):
        b = Backoff(base_s=0.5, cap_s=2.0)
        assert b.delay(10) == 2.0

    def test_jitter_bounds(self):
        b = Backoff(base_s=0.5, cap_s=30.0, jitter_s=0.25)
        for _ in range(20):
            assert 0.5 <= b.delay(0) <= 0.75

    def test_retry_after_floored_by_exponential_and_capped(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        assert b.delay(0, retry_after=10.0) == 10.0
        assert b.delay(0, retry_after=0.001) == 0.5
        assert b.delay(6, retry_after=99.0) == 30.0

    def test_negative_attempt_treated_as_zero(self):
        b = Backoff(base_s=0.5, cap_s=30.0)
        assert b.delay(-3) == 0.5

    def test_matches_router_inline_math(self):
        """Pins the exact expression this primitive replaces at the router's
        retry sleep: min(5.0, max(ra, 0.5 * 2**attempt)) + uniform(0, 0.25)."""
        b = Backoff(base_s=0.5, cap_s=5.0, jitter_s=0.25)
        expected = min(5.0, max(2.0, 0.5 * (2 ** 3)))  # 4.0
        d = b.delay(3, retry_after=2.0)
        assert expected <= d <= expected + 0.25


class TestCircuitBreaker:
    def test_trip_blocks_then_expires(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        assert cb.blocked("t")
        now[0] += 61.0
        assert not cb.blocked("t")

    def test_streak_doubles_window(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        now[0] += 61.0
        assert not cb.blocked("t")
        cb.trip("t")
        now[0] += 61.0   # second window is 120s: still blocked
        assert cb.blocked("t")
        now[0] += 61.0   # 122s past second trip: open again
        assert not cb.blocked("t")

    def test_cap(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=90.0, clock=lambda: now[0])
        cb.trip("t")            # 60s window
        now[0] += 61.0          # 1061
        cb.trip("t")            # 120 -> capped at 90 => until 1151
        now[0] += 89.0          # 1150: still blocked
        assert cb.blocked("t")
        now[0] += 2.0           # 1152: open
        assert not cb.blocked("t")

    def test_clear_resets(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.trip("t")
        cb.clear("t")
        assert not cb.blocked("t")
        assert cb.streak("t") == 0

    def test_mark_dead_is_permanent(self):
        now = [1000.0]
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0, clock=lambda: now[0])
        cb.mark_dead("t")
        now[0] += 10 ** 9
        assert cb.blocked("t")
        assert cb.dead("t")
        cb.clear("t")
        assert not cb.dead("t")

    def test_targets_are_independent(self):
        cb = CircuitBreaker(base_s=60.0, cap_s=3600.0)
        cb.mark_dead("a")
        assert cb.blocked("a")
        assert not cb.blocked("b")
