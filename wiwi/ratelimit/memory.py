"""Rate limiter: sliding-window rpm/tpm counters, global + per-key scopes.

Memory backend is exact for a single instance. Redis parity interface exists
(redis_url config) but memory is the MVP backend.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Window:
    # rpm windows store timestamps; tpm windows store (timestamp, tokens) pairs.
    events: deque = field(default_factory=deque)
    is_token: bool = False

    def count(self) -> int:
        if self.is_token:
            return sum(n for _, n in self.events)
        return len(self.events)


class RateLimiter:
    def __init__(self, global_rpm: int | None = None, global_tpm: int | None = None):
        self.global_rpm = global_rpm
        self.global_tpm = global_tpm
        self._windows: dict[str, _Window] = {}
        self._inflight = 0

    def _window(self, scope: str, is_token: bool = False) -> _Window:
        w = self._windows.get(scope)
        if w is None:
            w = self._windows[scope] = _Window(is_token=is_token)
        return w

    @staticmethod
    def _prune(w: _Window, now: float) -> None:
        cutoff = now - 60.0
        while w.events and w.events[0][0] < cutoff:
            w.events.popleft()

    def check(self, key_id: str, key_rpm: int | None = None,
              key_tpm: int | None = None, est_tokens: int = 0) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
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
            # (1 event for rpm scopes, est_tokens for tpm scopes)
            cost = max(1, est_tokens) if w.is_token else 1
            if w.count() + cost > limit:
                retry_after = int(max(1.0, 60.0 - (now - w.events[0][0]))) + 1
                return False, min(retry_after, 60)
        # reserve: one timestamp event for rpm scopes; est_tokens for tpm scopes
        for w, limit in checks:
            if w.is_token:
                w.events.append((now, max(0, est_tokens)))
            else:
                w.events.append((now, 1))
        return True, 0

    def record_tokens(self, key_id: str, tokens: int) -> None:
        """Post-request adjustment: add actual token usage to the tpm windows."""
        now = time.monotonic()
        for scope in ("global:tpm", f"{key_id}:tpm"):
            w = self._windows.get(scope)
            if w is not None and w.is_token:
                self._prune(w, now)
                w.events.append((now, max(0, tokens)))
