"""Redis-backed rate limiter for multi-worker deployments.

Implements the same ``check`` / ``record_tokens`` interface as the in-memory
``RateLimiter``, using sorted sets in Redis for sliding-window counting.

Usage:
    limiter = RedisRateLimiter(redis_url="redis://localhost:6379/0", ...)
    allowed, retry_after = limiter.check(key_id, rpm, tpm, est_tokens)
    limiter.record_tokens(key_id, actual_tokens)

When Redis is unavailable, falls back to the in-memory implementation.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from wiwi.ratelimit.memory import RateLimiter as MemoryRateLimiter


@dataclass
class _Event:
    ts: float
    tokens: int
    estimated: bool = False


@dataclass
class _Window:
    events: deque = field(default_factory=deque)
    is_token: bool = False

    def count(self) -> int:
        return sum(e.tokens for e in self.events)


def compute_retry_after_seconds(
    oldest_event_ts: float | None, now: float, window_s: float = 60.0
) -> int:
    """Compute ``Retry-After`` (seconds) for a sliding-window rate limit.

    ``oldest_event_ts`` is the timestamp of the oldest live event in the
    60-second window — the moment it will age out is when the caller can
    retry. If unknown (e.g. the read failed), return the full window length
    so the client backs off safely.

    Always returns at least 1, and is floored to the window length so the
    caller never gets a tighter suggestion than the window itself.

    This is extracted as a pure function so the arithmetic is testable
    without a live Redis. The previous version of the Redis backend
    hard-coded ``60.0 - (now - (now - 60.0))`` which is a no-op and made
    every Redis 429 claim a 1-second retry interval.
    """
    if oldest_event_ts is None:
        return int(window_s)
    seconds_until_free = window_s - (now - oldest_event_ts)
    # +1 so a freshly-added member never reads as "already free" (sub-second
    # race); clamp into [1, window_s] so the value is always a sensible
    # whole-second Retry-After.
    return max(1, min(int(window_s), int(seconds_until_free) + 1))


class RedisRateLimiter:
    """Redis-backed rate limiter using sorted sets for sliding windows.

    Falls back to in-memory when Redis is not available or when the Redis
    connection fails, so the gateway always works in dev.
    """

    def __init__(self, redis_url: str, global_rpm: int | None = None,
                 global_tpm: int | None = None):
        self._memory = MemoryRateLimiter(global_rpm=global_rpm, global_tpm=global_tpm)
        self._redis: Any = None
        self._redis_url = redis_url
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
        except ImportError:
            self._redis = None

    async def check(self, key_id: str, key_rpm: int | None = None,
                    key_tpm: int | None = None, est_tokens: int = 0) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        if self._redis is None:
            return self._memory.check(key_id, key_rpm, key_tpm, est_tokens)

        now = time.time()

        scopes: list[tuple[str, int, bool]] = []
        if self._memory.global_rpm:
            scopes.append(("global:rpm", self._memory.global_rpm, False))
        if self._memory.global_tpm:
            scopes.append(("global:tpm", self._memory.global_tpm, True))
        if key_rpm:
            scopes.append((f"{key_id}:rpm", key_rpm, False))
        if key_tpm:
            scopes.append((f"{key_id}:tpm", key_tpm, True))

        try:
            pipe = self._redis.pipeline()
            for scope, _limit, _is_token in scopes:
                pipe.zremrangebyscore(scope, 0, now - 60.0)
            for scope, _limit, is_token in scopes:
                score = now
                value = f"{now}:{est_tokens if is_token else 1}"
                pipe.zadd(scope, {value: score})
            for scope, _limit, _is_token in scopes:
                pipe.zcard(scope)
            results = await pipe.execute()

            # results: [pruned x N, added x N, counts x N]
            n = len(scopes)
            counts = results[2 * n:]
            for i, (scope, limit, _is_token) in enumerate(scopes):
                count = int(counts[i])
                # The score includes our just-added member; count is total
                # including our reservation. Check if over limit.
                if count > 0 and count > limit:
                    # Undo our reservation
                    await self._undo_reservations(scopes, now, est_tokens)
                    # Compute retry_after from the oldest live member's score
                    # (the next moment it'll age out of the 60s window).
                    # Falls back to a safe 60s if the read fails.
                    retry_after = await self._compute_retry_after(
                        scope, now, est_tokens
                    )
                    return False, min(retry_after, 60)
            return True, 0
        except Exception:  # noqa: BLE001
            # Redis error: fall back to memory
            return self._memory.check(key_id, key_rpm, key_tpm, est_tokens)

    async def _compute_retry_after(self, scope: str, now: float,
                                   est_tokens: int) -> int:
        """Return Retry-After seconds based on the oldest live member in *scope*.

        The arithmetic itself lives in :func:`compute_retry_after_seconds`;
        this wrapper just looks up the oldest score from Redis. If the read
        fails for any reason we conservatively return the full window length
        (60s) so the client backs off safely.
        """
        if self._redis is None:
            return compute_retry_after_seconds(None, now)
        try:
            oldest = await self._redis.zrange(scope, 0, 0, withscores=True)
        except Exception:  # noqa: BLE001
            return compute_retry_after_seconds(None, now)
        if not oldest:
            return compute_retry_after_seconds(None, now)
        # `zrange ... withscores=True` returns [(member, score), ...] with
        # score as float (epoch seconds, since we stored `now` as the score).
        oldest_ts = float(oldest[0][1])
        return compute_retry_after_seconds(oldest_ts, now)

    async def _undo_reservations(self, scopes: list, now: float, est_tokens: int) -> None:
        if self._redis is None:
            return
        try:
            pipe = self._redis.pipeline()
            for scope, _limit, is_token in scopes:
                value = f"{now}:{est_tokens if is_token else 1}"
                pipe.zrem(scope, value)
            await pipe.execute()
        except Exception:  # noqa: BLE001, S110
            pass

    async def record_tokens(self, key_id: str, tokens: int) -> None:
        """Post-request confirmation of actual token usage."""
        if self._redis is None:
            self._memory.record_tokens(key_id, tokens)
            return
        # In Redis mode, we rely on the check-time reservation being accurate
        # enough. True reconciliation would require finding and replacing the
        # estimated member. For simplicity, we update the memory fallback too
        # so cost accounting stays consistent.
        self._memory.record_tokens(key_id, tokens)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
