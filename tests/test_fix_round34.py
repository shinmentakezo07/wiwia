"""Round-34 regression tests: response-cache safety (determinism guard) and
the Redis ``CacheBackend`` implementation.

Scope (all reproduced against source before fixing):

1. ``wiwi/cache/keygen.py`` hashes the full normalized IR but carries no
   notion of whether the request is *deterministic*. With
   ``cache_settings.enabled = true`` a caller sending ``temperature: 0.8``
   got the same completion back for the lifetime of the TTL, because the
   cache key does not vary with sampling randomness. Sampling parameters
   were an unconditional part of the key, so two requests that differed
   only in ``seed`` shared no entry, yet a single repeated creative prompt
   was frozen. Caching must be restricted to requests whose output is
   expected to be reproducible: ``temperature`` unset or ``0``.

2. ``wiwi/cache/interface.py`` declares ``CacheBackend`` for exactly this
   "plug in a Redis (or semantic) backend" purpose, but only the memory
   backend existed. ``GeneralSettings.redis_url`` was read from config and
   never used for caching. This round adds ``RedisResponseCache`` behind
   the existing protocol and wires ``redis_url`` to select it.

   Two contracts matter beyond a plain get/set:
   - ``payload`` is ``bytes``; ``orjson`` refuses to serialize ``bytes``
     (``TypeError: Type is not JSON serializable: bytes``), so the entry is
     stored as a base64-armored JSON blob rather than a naive dump.
   - Redis is advisory. A cache backend must NEVER be able to fail a
     request: every operation degrades to a miss when Redis is unreachable,
     mirroring how ``RedisRateLimiter`` falls back to memory.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.cache.interface import CacheEntry
from wiwi.cache.keygen import is_cacheable_request, response_cache_key
from wiwi.cache.redis_cache import RedisResponseCache
from wiwi.config import (
    CacheSettings,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.ir import types as ir


def _req(text: str = "hi", **gen) -> ir.Request:
    return ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart(text)])],
        gen_params=ir.GenParams(**gen),
    )


class _FakeRedis:
    """Minimal async stand-in for ``redis.asyncio.Redis``.

    Only the commands the cache actually issues are modelled: ``get``,
    ``setex``, ``delete``, ``ping``, and ``aclose``. Strings are stored as
    bytes to match ``decode_responses=False`` (which the cache uses so
    payload bytes round-trip untouched).
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, bytes] = {}
        self.expiries: dict[str, int] = {}
        self.deleted: list[str] = []
        self.closed = False
        self._fail = fail

    def _maybe_fail(self) -> None:
        if self._fail:
            raise ConnectionError("redis is down")

    async def ping(self) -> bool:
        self._maybe_fail()
        return True

    async def get(self, key: str) -> bytes | None:
        self._maybe_fail()
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self._maybe_fail()
        self.store[key] = value
        self.expiries[key] = ttl

    async def delete(self, key: str) -> int:
        self._maybe_fail()
        self.deleted.append(key)
        return 1 if self.store.pop(key, None) is not None else 0

    async def aclose(self) -> None:
        self.closed = True


def _entry(payload: bytes = b"{}", **kw) -> CacheEntry:
    return CacheEntry(payload=payload, stored_at=time.time(),
                      request_id="r", model="m", **kw)


# ---------------------------------------------------------------------------
# 1. Determinism guard
# ---------------------------------------------------------------------------

def test_cacheable_requires_deterministic_temperature():
    # No sampling params at all -> provider default temperature, which is
    # deterministic enough for exact-match reuse of a single response.
    assert is_cacheable_request(_req()) is True
    assert is_cacheable_request(_req(temperature=0)) is True
    assert is_cacheable_request(_req(temperature=0.0)) is True
    # Any real sampling temperature makes the output non-reproducible.
    assert is_cacheable_request(_req(temperature=0.8)) is False
    assert is_cacheable_request(_req(temperature=1)) is False
    assert is_cacheable_request(_req(temperature=0.2)) is False


def test_cacheable_ignores_unrelated_gen_params():
    # max_tokens / stop / top_p alone must not disqualify a request; the
    # guard is about sampling randomness, not about every generation knob.
    assert is_cacheable_request(_req(max_tokens=64, stop=["\n"])) is True
    assert is_cacheable_request(_req(temperature=0, seed=7)) is True


def test_cacheable_rejects_nondeterministic_n():
    # n > 1 asks for several sampled alternatives in one response; caching
    # would pin all of them to the first observed set.
    assert is_cacheable_request(_req(temperature=0, n=3)) is False


def test_cache_key_still_varies_by_temperature():
    # The guard is an admission rule, not a key change: distinct
    # (admissible) temperatures must still land in distinct entries.
    k0 = response_cache_key(_req(temperature=0), "g", "chat", "k")
    k_none = response_cache_key(_req(), "g", "chat", "k")
    assert k0 != k_none


# ---------------------------------------------------------------------------
# 2. RedisResponseCache
# ---------------------------------------------------------------------------

async def test_redis_cache_roundtrip_preserves_bytes():
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0, client=fake)
    assert await c.get("k") is None
    await c.set("k", _entry(b'{"a": 1}'))
    got = await c.get("k")
    assert got is not None
    assert got.payload == b'{"a": 1}'
    assert got.model == "m"
    assert got.request_id == "r"
    # TTL is honoured by Redis itself, not by us.
    assert fake.expiries["wiwi:rsp:k"] == 60


async def test_redis_cache_uses_ttl_and_namespaced_key():
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=42.0, client=fake,
                           prefix="wiwi:cache")
    await c.set("abc", _entry(b"{}"))
    assert list(fake.store) == ["wiwi:cache:abc"]
    assert fake.expiries["wiwi:cache:abc"] == 42


async def test_redis_cache_roundtrip_unicode_and_media_headers():
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0, client=fake)
    await c.set("k", _entry("héllo ✓".encode(),
                            media_headers={"content-type": "application/json"}))
    got = await c.get("k")
    assert got is not None
    assert got.payload == "héllo ✓".encode()
    assert got.media_headers == {"content-type": "application/json"}


async def test_redis_cache_rejects_ttl_under_one_second():
    # Redis SETEX rejects a non-positive expiry; sub-second TTLs must be
    # clamped to 1 rather than raising mid-request.
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=0.05, client=fake)
    await c.set("k", _entry(b"{}"))
    assert fake.expiries["wiwi:rsp:k"] == 1


async def test_redis_cache_never_raises_on_get_failure():
    # A cache backend must degrade to a miss, never fail the request.
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0,
                           client=_FakeRedis(fail=True))
    assert await c.get("k") is None


async def test_redis_cache_never_raises_on_set_failure():
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0,
                           client=_FakeRedis(fail=True))
    await c.set("k", _entry(b"{}"))  # must not raise


async def test_redis_cache_delete_swallows_errors():
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0,
                           client=_FakeRedis(fail=True))
    await c.delete("k")  # must not raise


async def test_redis_cache_delete_removes_entry():
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0, client=fake)
    await c.set("k", _entry(b"{}"))
    await c.delete("k")
    assert await c.get("k") is None


async def test_redis_cache_tolerates_corrupt_entry():
    # A foreign/garbled value under our key space is a miss, not a 500.
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0, client=fake)
    fake.store["wiwi:rsp:k"] = b"not-json-at-all"
    assert await c.get("k") is None


async def test_redis_cache_aclose_closes_client():
    fake = _FakeRedis()
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0, client=fake)
    await c.aclose()
    assert fake.closed is True


async def test_redis_cache_aclose_without_client_is_safe():
    c = RedisResponseCache(url="redis://unused", ttl_s=60.0, client=None)
    await c.aclose()  # must not raise


def test_redis_cache_rejects_nonpositive_ttl():
    with pytest.raises(ValueError):
        RedisResponseCache(url="redis://unused", ttl_s=0, client=None)


# ---------------------------------------------------------------------------
# 3. End-to-end: config selects the backend, guard applies on the wire
# ---------------------------------------------------------------------------

UPSTREAM_BODY = {
    "id": "chatcmpl-c", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


def _app_cfg(tmp_path, *, redis: bool = False) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                              keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake:6379/0" if redis else ""),
        router_settings=RouterSettings(
            stream_journal_enabled=False,
            stream_journal_dir=str(tmp_path / "journals")),
        cache_settings=CacheSettings(enabled=True, ttl_s=300.0, max_entries=16),
    )


async def _two_calls(app, body):
    """Fire the same request twice; return both responses + upstream count."""
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            with respx.mock:
                route = respx.post("https://api.openai.com/v1/chat/completions")
                route.respond(json=UPSTREAM_BODY)
                headers = {"Authorization": "Bearer sk-wiwi-master-test"}
                r1 = await c.post("/v1/chat/completions", json=body, headers=headers)
                r2 = await c.post("/v1/chat/completions", json=body, headers=headers)
            return r1, r2, route.call_count


@pytest.mark.parametrize("temperature,expect_cached", [
    (None, True), (0, True), (0.0, True), (0.8, False), (1.0, False),
])
async def test_e2e_sampling_temperature_not_cached(tmp_path, temperature,
                                                   expect_cached):
    from wiwi.server.app import create_app

    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    if temperature is not None:
        body["temperature"] = temperature
    _r1, r2, calls = await _two_calls(create_app(_app_cfg(tmp_path)), body)
    assert r2.status_code == 200
    if expect_cached:
        assert r2.headers.get("x-wiwi-cache") == "HIT"
        assert calls == 1
    else:
        assert "x-wiwi-cache" not in r2.headers
        assert calls == 2, "non-deterministic request must reach upstream twice"


async def test_e2e_redis_url_selects_redis_backend(tmp_path, monkeypatch):
    """``general_settings.redis_url`` must actually pick the Redis backend."""
    from wiwi.cache import redis_cache as rc_mod
    from wiwi.server.app import create_app

    shared: dict[str, bytes] = {}

    class _Shared:  # stands in for a Redis server process
        async def get(self, k): return shared.get(k)

        async def setex(self, k, ttl, v): shared[k] = v

        async def delete(self, k): shared.pop(k, None); return 1

        async def aclose(self): pass

    def _init(self, url, ttl_s=3600.0, prefix="wiwi:rsp", client=None):
        self.url = url
        self.ttl_s = ttl_s
        self.prefix = prefix
        self._client = client or _Shared()

    monkeypatch.setattr(rc_mod.RedisResponseCache, "__init__", _init)

    app = create_app(_app_cfg(tmp_path, redis=True))
    from wiwi.cache import RedisResponseCache as _R
    assert isinstance(app.state.wiwi.response_cache, _R)

    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    r1, r2, calls = await _two_calls(app, body)
    assert r2.headers.get("x-wiwi-cache") == "HIT"
    assert calls == 1
    assert r1.json() == r2.json()
    # Payload really landed in the (fake) Redis, namespaced.
    assert [k for k in shared if k.startswith("wiwi:rsp:")]


# ---------------------------------------------------------------------------
# 4. REDIS_URL env override (container/Railway ergonomics)
# ---------------------------------------------------------------------------

async def test_redis_url_env_overrides_config(tmp_path, monkeypatch):
    """REDIS_URL must work without mounting a custom wiwi.yaml.

    The shipped container boots on wiwi.yaml.example, whose redis_url is
    commented out, so an env-only deployment would otherwise be unable to
    select the Redis backend.
    """
    from wiwi.cache import RedisResponseCache as _R
    from wiwi.server.app import create_app

    monkeypatch.setenv("REDIS_URL", "redis://from-env:6379/0")
    app = create_app(_app_cfg(tmp_path, redis=False))
    assert isinstance(app.state.wiwi.response_cache, _R)
    assert app.state.wiwi.response_cache.url == "redis://from-env:6379/0"


async def test_redis_url_env_takes_precedence_over_yaml(tmp_path, monkeypatch):
    from wiwi.server.app import create_app

    monkeypatch.setenv("REDIS_URL", "redis://env-wins:6379/0")
    app = create_app(_app_cfg(tmp_path, redis=True))
    assert app.state.wiwi.response_cache.url == "redis://env-wins:6379/0"


async def test_empty_redis_url_env_falls_back_to_config(tmp_path, monkeypatch):
    """An empty env var must not shadow a configured URL (Railway/Render
    sometimes inject empty values)."""
    from wiwi.server.app import create_app

    monkeypatch.setenv("REDIS_URL", "")
    app = create_app(_app_cfg(tmp_path, redis=True))
    assert app.state.wiwi.response_cache.url == "redis://fake:6379/0"
