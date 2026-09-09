"""Response cache subsystem (docs/CORE.md §6): keygen + backend + entry."""

from wiwi.cache.interface import CacheBackend, CacheEntry
from wiwi.cache.keygen import is_cacheable_request, response_cache_key
from wiwi.cache.redis_cache import RedisResponseCache
from wiwi.cache.response_cache import MemoryResponseCache

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
    the gateway from starting, so the ImportError falls through like any
    other missing optional dependency.
    """
    if redis_url:
        try:
            return RedisResponseCache(url=redis_url, ttl_s=settings.ttl_s)
        except ImportError:  # pragma: no cover - redis extra not installed
            pass
    return MemoryResponseCache(ttl_s=settings.ttl_s,
                               max_entries=settings.max_entries)
