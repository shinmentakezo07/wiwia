"""Redis-backed rate limiter for multi-worker deployments.

Implements the same ``check`` / ``record_tokens`` interface as the in-memory
``RateLimiter``, using sorted sets in Redis for sliding-window counting.

Usage:
    limiter = RedisRateLimiter(redis_url="redis://localhost:6379/0", ...)
    allowed, retry_after = limiter.check(key_id, rpm, tpm, est_tokens)
    limiter.record_tokens(key_id, actual_tokens)

When Redis is unavailable, falls back to the in-memory implementation.

Token accounting: each sorted-set member is ``"{ts}:{cost}:{uid}"`` — the
score carries the timestamp for window pruning, and the member carries the
token cost so TPM windows enforce a token *sum* (not a request count) plus a
unique uid so concurrent same-second, same-cost reservations cannot collide
(zadd overwrites same-member entries, silently dropping a reservation). The
uid is the request id when one is given, which is what lets
:meth:`record_tokens` reconcile the estimate down to actual usage.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from wiwi.ratelimit.memory import RateLimiter as MemoryRateLimiter

_WINDOW_S = 60.0


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


def _member(ts: float, cost: int, uid: str) -> str:
    return f"{ts}:{cost}:{uid}"


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
                    key_tpm: int | None = None, est_tokens: int = 0,
                    request_id: str = "") -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        if self._redis is None:
            return await self._memory.check(key_id, key_rpm, key_tpm,
                                            est_tokens, request_id)

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
            members: list[str] = []
            for _scope, _limit, is_token in scopes:
                cost = est_tokens if is_token else 1
                uid = request_id or uuid4().hex
                members.append(_member(now, cost, uid))

            pipe = self._redis.pipeline()
            for scope, _limit, _is_token in scopes:
                pipe.zremrangebyscore(scope, 0, now - _WINDOW_S)
            for (scope, _limit, _is_token), member in zip(scopes, members):
                pipe.zadd(scope, {member: now})
            # Tokens-per-minute is a token budget, not a request budget: sum
            # the per-member token costs (zrange withscores would only give
            # timestamps; the cost lives in the member string). One fetch
            # serves both the sum and the oldest-timestamp read.
            for scope, _limit, _is_token in scopes:
                pipe.zrange(scope, 0, -1)
            results = await pipe.execute()

            # results: [pruned x N, added x N, members x N]
            n = len(scopes)
            raw_members = results[2 * n:]
            for i, (scope, limit, is_token) in enumerate(scopes):
                live = raw_members[i]
                total = sum(int(m.split(":")[1]) for m in live)
                if total > limit:
                    # Undo our reservation and report when the window frees up.
                    await self._undo_reservations(scopes, members)
                    retry_after = await self._compute_retry_after(scope, now)
                    return False, min(retry_after, int(_WINDOW_S))
            return True, 0
        except Exception:  # noqa: BLE001
            # Redis error: fall back to memory
            return await self._memory.check(key_id, key_rpm, key_tpm,
                                            est_tokens, request_id)

    async def _compute_retry_after(self, scope: str, now: float) -> int:
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

    async def _undo_reservations(self, scopes: list, members: list[str]) -> None:
        if self._redis is None:
            return
        try:
            pipe = self._redis.pipeline()
            for (_scope, _limit, _is_token), member in zip(scopes, members):
                pipe.zrem(_scope, member)
            await pipe.execute()
        except Exception:  # noqa: BLE001, S110
            pass

    async def record_tokens(self, key_id: str, tokens: int,
                            request_id: str = "") -> None:
        """Post-request confirmation of actual token usage.

        Reconciles the Redis reservation tagged with *request_id* down to the
        actual usage (delta via zadd on the same member) so the next admission
        check sums real consumption, not just estimates. Falls back to memory
        when the request is unknown there (e.g. it was already pruned) or when
        no request id was supplied.
        """
        if self._redis is None:
            await self._memory.record_tokens(key_id, tokens, request_id)
            return

        actual = max(0, tokens)
        try:
            for scope in (f"{key_id}:tpm", "global:tpm"):
                members = await self._redis.zrange(scope, 0, -1)
                target = None
                if request_id:
                    for m in members:
                        # member format: "{ts}:{cost}:{uid}"
                        if m.split(":")[2] == request_id:
                            target = m
                            break
                if target is None:
                    continue
                ts, _est, uid = target.split(":", 2)
                # The replacement member differs from the original whenever
                # est != actual, so a plain zadd would leave the estimate in
                # place alongside the reconciled member (double-counting).
                # Remove the old one first; keeping the uid makes a second
                # record_tokens call for the same request idempotent.
                pipe = self._redis.pipeline()
                pipe.zrem(scope, target)
                pipe.zadd(scope, {_member(float(ts), actual, uid): float(ts)})
                await pipe.execute()
        except Exception:  # noqa: BLE001, S110
            pass
        await self._memory.record_tokens(key_id, tokens, request_id)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
