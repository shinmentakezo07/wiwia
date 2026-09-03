"""Exact-match response cache: memory LRU + TTL backend (docs/CORE.md §6).

Non-streaming requests only. The app registers one shared instance; the
request path calls ``get`` before dispatching upstream and ``set`` after the
encoded response exists. Eviction: lazy TTL check on read, LRU cap on write.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from wiwi.cache.interface import CacheBackend, CacheEntry


class MemoryResponseCache(CacheBackend):
    def __init__(self, ttl_s: float = 3600.0, max_entries: int = 256) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()

    async def get(self, key: str) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.time() - entry.stored_at > self.ttl_s:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry

    async def set(self, key: str, entry: CacheEntry) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    async def aclose(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
