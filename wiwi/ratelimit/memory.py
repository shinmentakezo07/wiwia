"""Rate limiter: sliding-window rpm/tpm counters, global + per-key scopes.

Memory backend is exact for a single instance. Redis parity interface exists
(redis_url config) but memory is the MVP backend.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Event:
    ts: float
    tokens: int          # 1 for rpm events
    estimated: bool = False  # True until record_tokens() confirms actual usage


@dataclass
class _Window:
    # rpm windows store request events; tpm windows store token events.
    events: deque = field(default_factory=deque)
    is_token: bool = False

    def count(self) -> int:
        return sum(e.tokens for e in self.events)


class RateLimiter:
    def __init__(self, global_rpm: int | None = None, global_tpm: int | None = None):
        self.global_rpm = global_rpm
        self.global_tpm = global_tpm
        self._windows: dict[str, _Window] = {}
        self._inflight = 0
        # Serializes check() and record_tokens() so concurrent callers cannot
        # both pass the limit at the same instant, and so a reservation can
        # be replaced (not appended alongside) when the actual usage arrives.
        self._lock = asyncio.Lock()

    def _window(self, scope: str, is_token: bool = False) -> _Window:
        w = self._windows.get(scope)
        if w is None:
            w = self._windows[scope] = _Window(is_token=is_token)
        return w

    @staticmethod
    def _prune(w: _Window, now: float) -> None:
        cutoff = now - 60.0
        while w.events and w.events[0].ts < cutoff:
            w.events.popleft()

    async def check(self, key_id: str, key_rpm: int | None = None,
                    key_tpm: int | None = None, est_tokens: int = 0) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds).

        Atomic against itself and against :meth:`record_tokens` so two
        concurrent callers cannot both pass when only one slot is free.
        """
        async with self._lock:
            now = time.monotonic()
            checks: list[tuple[_Window, int]] = []
            if self.global_rpm:
                checks.append((self._window("global:rpm"), self.global_rpm))
            if self.global_tpm:
                checks.append((self._window("global:tpm", is_token=True), self.global_tpm))
            if key_rpm:
                checks.append((self._window(f"{key_id}:rpm"), key_rpm))
            if key_tpm:
                checks.append((self._window(f"{key_id}:tpm", is_token=True), key_tpm))

            for w, limit in checks:
                self._prune(w, now)
                # prospective admission: the incoming request's cost must fit
                cost = est_tokens if w.is_token else 1
                if w.count() + cost > limit:
                    retry_after = int(max(1.0, 60.0 - (now - w.events[0].ts))) + 1
                    return False, min(retry_after, 60)
            # reserve: one event per rpm scope; an estimated-cost event per tpm scope
            for w, limit in checks:
                if w.is_token:
                    w.events.append(_Event(ts=now, tokens=max(0, est_tokens),
                                           estimated=True))
                else:
                    w.events.append(_Event(ts=now, tokens=1))
            return True, 0

    async def record_tokens(self, key_id: str, tokens: int) -> None:
        """Post-request confirmation: replace the newest estimated reservation
        with the actual usage (prevents double-counting estimate + actual).

        Atomic against :meth:`check` so the replacement is exclusive with
        the next admission decision.
        """
        async with self._lock:
            now = time.monotonic()
            for scope in ("global:tpm", f"{key_id}:tpm"):
                w = self._windows.get(scope)
                if w is None or not w.is_token:
                    continue
                self._prune(w, now)
                for e in reversed(w.events):
                    if e.estimated:
                        e.tokens = max(0, tokens)
                        e.estimated = False
                        break
                else:
                    w.events.append(_Event(ts=now, tokens=max(0, tokens)))
