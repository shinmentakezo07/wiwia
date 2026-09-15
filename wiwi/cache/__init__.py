"""Response cache subsystem (docs/CORE.md §6): keygen + backend + entry."""

import structlog

from wiwi.cache.interface import CacheBackend, CacheEntry
from wiwi.cache.keygen import is_cacheable_request, response_cache_key
from wiwi.cache.redis_cache import RedisResponseCache
from wiwi.cache.response_cache import MemoryResponseCache

log = structlog.get_logger("wiwi.cache")

__all__ = [
    "CacheBackend",
    "CacheEntry",
    "MemoryResponseCache",
    "RedisResponseCache",
    "build_response_cache",
    "is_cacheable_request",
    "response_cache_key",
]


def build_response_cache(settings, redis_url: str = "") -> CacheBackend:
    """Pick the response-cache backend from config.

    Redis is used when *redis_url* is set AND the ``redis`` package imports;
    otherwise memory. A configured-but-uninstalled Redis extra must not stop
    the gateway from starting, so the miss falls through to the memory
    backend with a warning — silently degrading would leave an operator who
    set ``general_settings.redis_url`` believing a shared, restart-durable
    cache was serving traffic while every request was actually a miss.
    """
    if redis_url:
        # The probe must happen here, not inside RedisResponseCache: that class
        # defers its own import to the first get(), so constructing it proves
        # nothing and the dead ``except ImportError`` around the constructor
        # selected Redis forever, missing silently.
        try:
            import redis.asyncio  # noqa: F401 — availability probe only
        except ImportError as e:
            log.warning("redis_cache_unavailable", detail=str(e))
        else:
            return RedisResponseCache(url=redis_url, ttl_s=settings.ttl_s)
    return MemoryResponseCache(ttl_s=settings.ttl_s,
                               max_entries=settings.max_entries)
