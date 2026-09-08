"""Shared recovery primitives: backoff, circuit breakers.

Used by the router retry loop, the Cline/WorkBuddy auto-refresh services, and
the HealthHealer background service. Contracts only — no dialect or provider
branching (invariant: those live in wiwi/wire/ and wiwi/providers/). This
module must never import wiwi.router or wiwi.core.gateway: they import from
here, so the direction recovery -> router would create a cycle.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from enum import Enum

import structlog

log = structlog.get_logger("wiwi.recovery")


@dataclass(frozen=True)
class Backoff:
    """Exponential delay with jitter; honors upstream ``retry_after``.

    ``delay`` reproduces the router's historical inline math exactly:
    ``min(cap_s, max(retry_after or 0, base_s * 2**attempt)) + uniform(0, jitter_s)``
    — retry_after competes with the exponential floor and is capped.
    """

    base_s: float
    cap_s: float
    jitter_s: float = 0.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        exp = self.base_s * (2 ** max(0, attempt))
        ra = retry_after if (retry_after is not None and retry_after > 0) else 0.0
        d = min(self.cap_s, max(ra, exp))
        if self.jitter_s > 0:
            d += random.uniform(0.0, self.jitter_s)
        return d


class CircuitBreaker:
    """Per-target failure streak with exponential backoff and a permanent-dead state.

    Replaces the ``{"streak": n, "until": t}`` dict idiom previously duplicated
    in cline_auto_refresh and workbuddy_auto_refresh (streak >= 99 / until=inf
    meant permanently dead; that is ``mark_dead`` here). After a backoff window
    elapses the target is unblocked but its streak persists, so the next trip
    waits longer. Targets must be hashable.
    """

    def __init__(self, base_s: float, cap_s: float,
                 clock: Callable[[], float] = time.time) -> None:
        self._base = base_s
        self._cap = cap_s
        self._clock = clock
        self._streaks: dict[Hashable, int] = {}
        self._until: dict[Hashable, float] = {}
        self._dead: set[Hashable] = set()

    def trip(self, target: Hashable) -> None:
        streak = self._streaks.get(target, 0) + 1
        self._streaks[target] = streak
        window = min(self._cap, self._base * 2 ** (streak - 1))
        self._until[target] = self._clock() + window

    def clear(self, target: Hashable) -> None:
        self._streaks.pop(target, None)
        self._until.pop(target, None)
        self._dead.discard(target)

    def blocked(self, target: Hashable) -> bool:
        if target in self._dead:
            return True
        return self._clock() < self._until.get(target, 0.0)

    def dead(self, target: Hashable) -> bool:
        return target in self._dead

    def mark_dead(self, target: Hashable) -> None:
        self._dead.add(target)

    def streak(self, target: Hashable) -> int:
        return self._streaks.get(target, 0)


class ProbeVerdict(Enum):
    """Classification of a HealthHealer probe outcome (see specs/
    2026-09-08-recovery-healer-design.md, Part B verdict table)."""

    HEALTHY = "healthy"
    ALIVE_THROTTLED = "alive_throttled"
    CREDS_VALID_MODEL_BAD = "creds_valid_model_bad"
    CREDS_REJECTED = "creds_rejected"
    UNREACHABLE = "unreachable"


def probe_verdict(status: int | None) -> ProbeVerdict:
    """Classify a probe HTTP outcome; ``status=None`` means transport failure."""
    if status == 200:
        return ProbeVerdict.HEALTHY
    if status == 429:
        return ProbeVerdict.ALIVE_THROTTLED
    if status in (401, 403):
        return ProbeVerdict.CREDS_REJECTED
    if status in (400, 404):
        return ProbeVerdict.CREDS_VALID_MODEL_BAD
    return ProbeVerdict.UNREACHABLE


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP ``Retry-After`` header: delta-seconds or an HTTP-date
    (RFC 7231). Returns seconds from now (>= 0), or None when absent/garbage."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    # RFC 7231 also allows an HTTP-date (e.g. "Wed, 21 Oct 2026 07:28:00 GMT").
    # Parse it and compute seconds from now; clamp to >= 0.
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        pass
    return None
