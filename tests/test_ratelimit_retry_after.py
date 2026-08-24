"""Tests for the Redis rate limiter's Retry-After calculation.

The previous implementation of ``RedisRateLimiter.check`` computed
``retry_after`` as ``60.0 - (now - (now - 60.0))`` — a no-op that
simplified to a constant, so every Redis-backed 429 told the client to
retry in 1 second regardless of actual window state. The arithmetic was
extracted into :func:`compute_retry_after_seconds` so it is testable
without a live Redis connection.
"""

from __future__ import annotations

import time

from wiwi.ratelimit.redis import compute_retry_after_seconds


class TestComputeRetryAfterSeconds:
    """Pure-function tests for the retry_after arithmetic.

    The values are: 1 (minimum), some positive integer, 60 (max). The
    helper must never return 0 (clients would hammer), and must never
    return more than the window length (clients would wait pointlessly).
    """

    def test_unknown_oldest_returns_full_window(self):
        # If we couldn't read the oldest member (Redis down, empty scope),
        # fall back to the full window so the client backs off safely.
        assert compute_retry_after_seconds(None, now=100.0) == 60

    def test_brand_new_event_returns_near_full_window(self):
        # oldest_ts == now → almost the full window until it ages out.
        # 60 - 0 + 1 = 61, then min(60, 61) clamps to 60.
        now = 1000.0
        out = compute_retry_after_seconds(oldest_event_ts=now, now=now)
        assert out == 60
        assert out >= 1
        assert out <= 60

    def test_just_added_event_clamps_to_window(self):
        # A member added 0.1s ago should report "retry in ~60s" — we
        # don't promise sub-second precision.
        now = 1000.0
        out = compute_retry_after_seconds(oldest_event_ts=now - 0.1, now=now)
        assert 1 <= out <= 60

    def test_half_window_remaining(self):
        # Oldest event is 30s old → 30s left in the window.
        now = 1000.0
        out = compute_retry_after_seconds(oldest_event_ts=now - 30.0, now=now)
        # 60 - 30 + 1 = 31
        assert out == 31

    def test_almost_expired(self):
        # Oldest event is 59.5s old → < 1s left, but the helper still
        # floors to 1s so the client doesn't hammer.
        now = 1000.0
        out = compute_retry_after_seconds(oldest_event_ts=now - 59.5, now=now)
        # 60 - 59.5 + 1 = 1.5 → int = 1 → max(1, ...) = 1
        assert out == 1

    def test_fully_expired_old_event(self):
        # Edge case: oldest event is *older* than 60s (shouldn't happen
        # because Redis prunes it, but be safe). The helper clamps to 1.
        now = 1000.0
        out = compute_retry_after_seconds(oldest_event_ts=now - 120.0, now=now)
        assert out == 1

    def test_return_value_is_always_int_in_range(self):
        """Property-ish: for any plausible (oldest, now) the output is an
        int in [1, 60]. Catches future regressions of the arithmetic."""
        now = time.time()
        for delta in (-10, -1, 0, 1, 10, 30, 59, 59.9, 60, 70):
            out = compute_retry_after_seconds(
                oldest_event_ts=now + delta, now=now
            )
            assert isinstance(out, int)
            assert 1 <= out <= 60

    def test_previous_bug_is_now_fixed(self):
        """The original bug: every Redis 429 returned retry_after = 1
        regardless of any actual window state. With the fix the value
        varies with ``oldest_event_ts``."""
        now = 1000.0
        # Oldest event 30s ago → should NOT be 1.
        assert compute_retry_after_seconds(now - 30.0, now) != 1
        # Oldest event 5s ago → should be in the 50s range.
        val = compute_retry_after_seconds(now - 5.0, now)
        assert val > 30
