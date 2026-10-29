"""Round-175 regression test: a broken Redis response cache must not be silent.

``RedisResponseCache`` swallows every Redis exception so the cache can never
fail a request (round 34's contract, still correct). But it swallowed them
*invisibly*: no log, no counter, no /health field, no metric. ``general_settings.
redis_url`` being set makes the operator believe a shared, restart-durable cache
is serving traffic; with Redis down every request is a silent miss, and the only
symptom was upstream traffic and spend at the uncached rate — indistinguishable
from a cache that is simply cold.

``build_response_cache`` already refuses to hide the *missing-package* variant
of exactly this trap. This round closes the runtime variant:

- ``errors`` counts every failed operation;
- one ``warning`` fires on the healthy→broken transition, not per request (a
  hot path must not flood);
- a success after the outage clears the latch so a later outage warns again;
- a corrupt entry (a producer bug, not an outage) stays an ordinary miss and
  must not be reported as a Redis failure.
"""

from __future__ import annotations

import time

import structlog

from wiwi.cache.interface import CacheEntry
from wiwi.cache.redis_cache import RedisResponseCache


class _FlakyRedis:
    """Minimal redis-py surface; ``down`` toggles an outage."""

    def __init__(self) -> None:
        self.down = False
        self.data: dict[str, bytes] = {}
        self.calls = 0

    def _check(self) -> None:
        self.calls += 1
        if self.down:
            raise ConnectionError("Error 111 connecting to 127.0.0.1:6379")

    async def get(self, k):
        self._check()
        return self.data.get(k)

    async def setex(self, k, ttl, v):
        self._check()
        self.data[k] = v

    async def delete(self, k):
        self._check()
        self.data.pop(k, None)
        return 1

    async def aclose(self):
        pass


def _entry(payload: bytes = b"{}") -> CacheEntry:
    return CacheEntry(payload=payload, stored_at=time.time(),
                      request_id="r", model="m")


def _cache(fake: _FlakyRedis) -> RedisResponseCache:
    return RedisResponseCache(url="redis://127.0.0.1:6379/0", ttl_s=60,
                              client=fake)


def _errors(logs: list[dict]) -> list[dict]:
    return [e for e in logs if e.get("event") == "redis_cache_error"]


async def test_outage_is_counted_and_warned_once():
    """A down Redis still degrades to a miss, but is now visible."""
    fake = _FlakyRedis()
    c = _cache(fake)
    fake.down = True

    with structlog.testing.capture_logs() as logs:
        assert await c.get("k1") is None
        await c.set("k1", _entry())
        await c.delete("k1")

    assert c.errors == 3, "every failed operation must be counted"
    assert len(_errors(logs)) == 1, (
        f"one line per outage, not one per request; got {logs}")
    assert _errors(logs)[0]["url"] == "redis://127.0.0.1:6379/0"
    assert _errors(logs)[0]["log_level"] == "warning"


async def test_warns_again_on_a_later_outage():
    """The latch must clear on success, or the second outage is silent."""
    fake = _FlakyRedis()
    c = _cache(fake)

    fake.down = True
    with structlog.testing.capture_logs() as first:
        await c.get("k")
    fake.down = False
    assert await c.get("k") is None  # healthy miss: this clears the latch
    fake.down = True
    with structlog.testing.capture_logs() as second:
        await c.get("k")

    assert len(_errors(first)) == 1 and len(_errors(second)) == 1, (
        f"the second outage was not reported: {second}")
    assert c.errors == 2, "a healthy miss is not a failure"


async def test_healthy_cache_never_warns():
    """Control: a working cache emits nothing and still round-trips."""
    fake = _FlakyRedis()
    c = _cache(fake)

    with structlog.testing.capture_logs() as logs:
        await c.set("k", _entry(b'{"a":1}'))
        got = await c.get("k")

    assert got is not None and got.payload == b'{"a":1}'
    assert c.errors == 0 and logs == []


async def test_corrupt_entry_is_a_miss_not_a_redis_failure():
    """A foreign/corrupt value is a producer bug, not an outage."""
    fake = _FlakyRedis()
    fake.data["wiwi:rsp:bad"] = b"not json"
    c = _cache(fake)

    with structlog.testing.capture_logs() as logs:
        assert await c.get("bad") is None

    assert c.errors == 0, "a decode failure must not be counted as an outage"
    assert _errors(logs) == []
