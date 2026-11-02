"""Concurrent-request dedupe (singleflight) for non-streaming completions.

Two identical deterministic requests arriving together currently make two
upstream calls. The first caller becomes the *leader* and runs the work; the
others wait on its result and are served from it, exactly as if the response
cache had hit. The key is ``response_cache_key`` (which includes ``key_id``),
so requests from different virtual keys never share a result.

Streaming is deliberately excluded: fanning one upstream stream out to N
encoders with independent request ids and cancellation semantics is a much
larger change than this module is worth.

Cancellation: the flight future is shielded for waiters, so a waiter hanging
up does not cancel the leader. If the *leader* is cancelled, the future is
cancelled and a waiter retries as the new leader. That keeps the rare path
correct without detaching the upstream call from a request context that owns
billing and logging.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger("wiwi.cache")


@dataclass(frozen=True)
class FlightResult:
    """What a leader hands to its waiters.

    ``payload`` is the encoded response body, produced with the leader's
    request id; waiters rebase it onto their own id. ``turn`` is kept so the
    leader can continue through pricing/cache-store with the decoded result.
    """

    payload: dict[str, Any]
    usage: Any = None
    cost: float = 0.0
    turn: Any = None


class SingleFlight:
    def __init__(self, *, max_flights: int = 2048,
                 max_waiters: int = 64) -> None:
        self.max_flights = max(1, max_flights)
        self.max_waiters = max(0, max_waiters)
        self._flights: dict[str, asyncio.Future[FlightResult]] = {}
        self._waiters: dict[str, int] = {}
        self.leaders = 0
        self.joined = 0
        self.bypassed = 0

    async def run(
        self, key: str,
        factory: Callable[[], Awaitable[FlightResult]],
    ) -> tuple[FlightResult, bool]:
        """Return ``(result, joined)``; ``joined`` means another caller led."""
        for _ in range(2):
            fut = self._flights.get(key)
            if fut is not None:
                if self._waiters.get(key, 0) >= self.max_waiters:
                    # A hot key must not become a head-of-line queue: run the
                    # work independently instead of waiting behind it.
                    self.bypassed += 1
                    return await factory(), False
                self._waiters[key] = self._waiters.get(key, 0) + 1
                try:
                    return await asyncio.shield(fut), True
                except asyncio.CancelledError:
                    # Distinguish "the leader died" from "this waiter was
                    # cancelled". Only the former is retried.
                    if not fut.cancelled():
                        raise
                    continue
                finally:
                    n = self._waiters.get(key, 1) - 1
                    if n <= 0:
                        self._waiters.pop(key, None)
                    else:
                        self._waiters[key] = n

            if len(self._flights) >= self.max_flights:
                self.bypassed += 1
                return await factory(), False

            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._flights[key] = fut
            self._waiters[key] = 0
            self.leaders += 1
            try:
                result = await factory()
            except asyncio.CancelledError:
                # The leader's own request was cancelled. Cancel the shared
                # future so waiters can see ``fut.cancelled()`` and retry as
                # leaders instead of receiving a bogus CancelledError.
                if not fut.done():
                    fut.cancel()
                raise
            except BaseException as e:
                if not fut.done():
                    fut.set_exception(e)
                raise
            else:
                if not fut.done():
                    fut.set_result(result)
                return result, False
            finally:
                self._flights.pop(key, None)
                self._waiters.pop(key, None)

        # Both attempts lost their leader; run independently rather than loop
        # forever under churn.
        self.bypassed += 1
        return await factory(), False
