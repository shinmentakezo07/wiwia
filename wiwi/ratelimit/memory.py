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
    events: deque[float] = field(default_factory=deque)


class RateLimiter:
    def __init__(self, global_rpm: int | None = None, global_tpm: int | None = None):
        self.global_rpm = global_rpm
        self.global_tpm = global_tpm
        self._windows: dict[str, _Window] = {}
        self._inflight = 0

    def _window(self, scope: str) -> _Window:
        w = self._windows.get(scope)
        if w is None:
            w = self._windows[scope] = _Window()
        return w

    @staticmethod
    def _prune(w: _Window, now: float) -> None:
        cutoff = now - 60.0
        while w.events and w.events[0] < cutoff:
            w.events.popleft()

    def check(self, key_id: str, key_rpm: int | None = None,
              key_tpm: int | None = None, est_tokens: int = 0) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        checks: list[tuple[_Window, int | None]] = []
        if self.global_rpm:
            checks.append((self._window("global:rpm"), self.global_rpm))
        if self.global_tpm:
            checks.append((self._window("global:tpm"), self.global_tpm))
        if key_rpm:
            checks.append((self._window(f"{key_id}:rpm"), key_rpm))
        if key_tpm:
            checks.append((self._window(f"{key_id}:tpm"), key_tpm))

        for w, limit in checks:
            self._prune(w, now)
            if limit and len(w.events) >= limit:
                retry_after = int(max(1.0, 60.0 - (now - w.events[0]))) + 1
                return False, min(retry_after, 60)
        for w, _ in checks:
            w.events.append(now)
        return True, 0

    def record_tokens(self, key_id: str, tokens: int) -> None:
        now = time.monotonic()
        for scope in ("global:tpm", f"{key_id}:tpm"):
            if scope in self._windows:
                self._windows[scope].events.append(now)
