"""Redis-backed response cache (docs/CORE.md §6 ``CacheBackend`` seam).

Selected when ``general_settings.redis_url`` is set. Compared with
:class:`~wiwi.cache.response_cache.MemoryResponseCache` it trades ~0.3-1ms of
latency per lookup for three properties the memory backend cannot offer:

- entries survive a restart, so a deploy does not cold-start the cache
- every replica shares one cache instead of warming N independent copies
- capacity is bounded by Redis, not by ``cache_settings.max_entries``

For a single-process deployment the memory backend is strictly faster (a dict
lookup beats a network round-trip), so it remains the default.

The client is ``decode_responses=False``: :attr:`CacheEntry.payload` is
``bytes``, and decoding to ``str`` would force a lossy round-trip through a
text encoding for any non-UTF-8 body.

Every Redis call is wrapped: a cache must never fail a request, so an
unreachable Redis degrades to "always miss" rather than raising into
``run_chat_like``.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import orjson

from wiwi.cache.interface import CacheEntry

_DEFAULT_PREFIX = "wiwi:rsp"


class RedisResponseCache:
    """``CacheBackend`` over Redis ``GET``/``SETEX``/``DEL``.

    ``ttl_s`` is enforced by Redis itself (``SETEX``), so no local sweep is
    needed and stale entries cannot accumulate between reads.
    """

    def __init__(self, url: str, ttl_s: float = 3600.0,
                 prefix: str = _DEFAULT_PREFIX,
                 client: Any | None = None) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self.url = url
        self.ttl_s = ttl_s
        self.prefix = prefix
        # An injected client is closed on aclose() like any other: the cache
        # is the sole owner of whatever it is using, and a half-closed cache
        # would be a subtler bug than a closed caller-supplied client.
        self._client = client

    async def _redis(self) -> Any:
        """Lazily create the client so import never requires redis installed."""
        if self._client is None:
            import redis.asyncio as aioredis
            self._client = aioredis.from_url(self.url, decode_responses=False)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    # -- serialization ------------------------------------------------------
    # ``CacheEntry.payload`` is bytes and orjson refuses to serialize bytes
    # (TypeError: Type is not JSON serializable: bytes), so the payload is
    # base64-armored inside an otherwise-ordinary JSON document. base64 costs
    # ~33% size and is far cheaper than the upstream call being saved.
    @staticmethod
    def _encode(entry: CacheEntry) -> bytes:
        return orjson.dumps({
            "payload": base64.b64encode(entry.payload).decode("ascii"),
            "media_headers": entry.media_headers,
            "stored_at": entry.stored_at,
            "request_id": entry.request_id,
            "model": entry.model,
        })

    @staticmethod
    def _decode(raw: bytes) -> CacheEntry | None:
        try:
            doc = orjson.loads(raw)
            return CacheEntry(
                payload=base64.b64decode(doc["payload"]),
                media_headers=dict(doc.get("media_headers") or {}),
                stored_at=float(doc.get("stored_at") or 0.0),
                request_id=doc.get("request_id") or "",
                model=doc.get("model") or "",
            )
        except Exception:  # noqa: BLE001 — a corrupt entry is just a miss
            return None

    # -- CacheBackend -------------------------------------------------------
    async def get(self, key: str) -> CacheEntry | None:
        try:
            r = await self._redis()
            raw = await r.get(self._key(key))
        except Exception:  # noqa: BLE001 — cache is advisory, never fatal
            return None
        if raw is None:
            return None
        return self._decode(raw)

    async def set(self, key: str, entry: CacheEntry) -> None:
        # SETEX rejects a non-positive expiry, and int(0.9) == 0 would raise
        # mid-request for a sub-second TTL. Clamp instead.
        ttl = max(1, int(self.ttl_s))
        stored_at = entry.stored_at or time.time()
        payload = self._encode(
            entry if entry.stored_at else CacheEntry(
                payload=entry.payload, media_headers=entry.media_headers,
                stored_at=stored_at, request_id=entry.request_id,
                model=entry.model))
        try:
            r = await self._redis()
            await r.setex(self._key(key), ttl, payload)
        except Exception:  # noqa: BLE001, S110 — a failed write is just a miss
            pass

    async def delete(self, key: str) -> None:
        try:
            r = await self._redis()
            await r.delete(self._key(key))
        except Exception:  # noqa: BLE001, S110
            pass

    async def aclose(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001, S110
            pass
        finally:
            self._client = None
