"""Round 177 — a cache hit must be served under the caller's own identity,
and a non-positive cache TTL must not be fatal on one backend only.

Two defects found auditing ``wiwi/cache/``:

1. ``RedisResponseCache.__init__`` raised ``ValueError`` for ``ttl_s <= 0``.
   That constructor runs inside ``create_app`` (via ``build_response_cache``),
   so ``cache_settings`` with a zero/negative TTL made the whole gateway refuse
   to boot — but only when ``general_settings.redis_url`` was set. The very same
   config booted fine on the memory backend, which simply expires everything
   immediately. Config validity depended on which backend happened to be
   selected. Both backends now mean the same thing by it: disabled, never serve,
   never store — announced once at startup instead of being invisible.

2. A hit replayed the stored bytes, envelope included, so the caller received
   the *producing* request's ``id`` (and a ``created``/``created_at`` frozen at
   the time of that request) while its own ``x-wiwi-request-id`` was different.
   On Chat/Messages that id is opaque, but on the Responses surface it is
   dereferenced by ``GET /v1/responses/{id}`` and ``previous_response_id``, so a
   hit handed the caller an id naming someone else's stored object. The hit is
   now rebased onto the serving request and persisted under that id, so the
   handle it returns resolves.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
import structlog
from asgi_lifespan import LifespanManager

from wiwi.cache import build_response_cache
from wiwi.cache.hit_rebase import rebase_cached_body
from wiwi.cache.interface import CacheEntry
from wiwi.cache.redis_cache import RedisResponseCache
from wiwi.config import (
    CacheSettings,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app

UPSTREAM_CHAT = {
    "id": "chatcmpl-upstream", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


def _cfg(ttl_s: float, redis_url: str = "") -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-master", database_url="sqlite+aiosqlite:///:memory:",
            redis_url=redis_url, store_responses=True),
        cache_settings=CacheSettings(enabled=True, ttl_s=ttl_s),
    )


class _FakeRedis:
    """Records writes so "never stores" is provable, not inferred."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.expiries: dict[str, int] = {}
        self.calls = 0

    async def get(self, key: str) -> bytes | None:
        self.calls += 1
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self.calls += 1
        self.store[key] = value
        self.expiries[key] = ttl

    async def delete(self, key: str) -> None:
        self.calls += 1
        self.store.pop(key, None)

    async def aclose(self) -> None:
        pass


def _entry(payload: bytes = b"{}") -> CacheEntry:
    return CacheEntry(payload=payload, stored_at=time.time())


# ---------------------------------------------------------------------------
# 1. A non-positive TTL must not be fatal, and must mean "disabled"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ttl_s", [0, -5])
def test_create_app_survives_nonpositive_ttl_with_redis(ttl_s):
    # The regression: this raised ValueError("ttl_s must be positive") before,
    # so a YAML value the schema accepts killed startup on the Redis path only.
    app = create_app(_cfg(ttl_s, redis_url="redis://127.0.0.1:6379/0"))
    backend = app.state.wiwi.response_cache
    assert isinstance(backend, RedisResponseCache)
    assert backend.disabled is True


def test_control_positive_ttl_still_selects_redis():
    app = create_app(_cfg(60, redis_url="redis://127.0.0.1:6379/0"))
    backend = app.state.wiwi.response_cache
    assert isinstance(backend, RedisResponseCache)
    assert backend.disabled is False


async def test_disabled_redis_never_serves_and_never_stores():
    fake = _FakeRedis()
    cache = RedisResponseCache(url="redis://unused", ttl_s=0, client=fake)

    await cache.set("k", _entry(b'{"id": "x"}'))
    assert fake.expiries == {}, "a disabled cache must not write"
    assert fake.calls == 0, "a disabled cache must not even contact the server"

    # Even a value another instance left under the same key must not be served:
    # that is what makes the memory and Redis meanings identical.
    fake.store["wiwi:rsp:k"] = b'{"payload": "e30=", "stored_at": 1.0}'
    assert await cache.get("k") is None
    assert fake.calls == 0


def test_disabled_by_ttl_is_announced_for_both_backends():
    # A cache the operator enabled but that can never hit is a silent cost
    # trap, the same class as the unreachable Redis fixed in round 175.
    for redis_url in ("", "redis://127.0.0.1:6379/0"):
        with structlog.testing.capture_logs() as logs:
            build_response_cache(CacheSettings(enabled=True, ttl_s=0),
                                 redis_url)
        events = [e for e in logs
                  if e["event"] == "response_cache_disabled_by_ttl"]
        assert len(events) == 1, f"redis_url={redis_url!r} logged {events}"
        assert events[0]["log_level"] == "warning"
        assert events[0]["ttl_s"] == 0


def test_positive_ttl_does_not_warn_disabled():
    with structlog.testing.capture_logs() as logs:
        build_response_cache(CacheSettings(enabled=True, ttl_s=60), "")
    assert not [e for e in logs
                if e["event"] == "response_cache_disabled_by_ttl"]


# ---------------------------------------------------------------------------
# 2. The hit is rebased onto the serving request's identity
# ---------------------------------------------------------------------------


def test_rebase_preserves_prefix_and_separator():
    assert rebase_cached_body({"id": "chatcmpl-old"}, "new")["id"] == "chatcmpl-new"
    assert rebase_cached_body({"id": "msg_old"}, "new")["id"] == "msg_new"
    assert rebase_cached_body({"id": "cmpl-old"}, "new")["id"] == "cmpl-new"
    assert rebase_cached_body({"id": "resp_old"}, "new")["id"] == "resp_new"


def test_rebase_leaves_unrecognized_shapes_alone():
    # No separator: guessing a prefix would mangle a foreign id, which is worse
    # than the stale one.
    assert rebase_cached_body({"id": "opaque"}, "new")["id"] == "opaque"
    assert rebase_cached_body({}, "new") == {}
    assert rebase_cached_body({"id": None}, "new")["id"] is None


@pytest.mark.parametrize("stored", [123, ["a", "b"], None, "", "opaque"])
def test_rebase_never_mangles_a_non_identifier(stored):
    # A poisoned or foreign entry must degrade, never 500 the hit path.
    body = {"id": stored}
    rebase_cached_body(body, "rid")
    assert body["id"] == stored


def test_rebase_with_empty_request_id_keeps_the_stored_id():
    # Blanking the id would be strictly worse than a stale one.
    assert rebase_cached_body({"id": "chatcmpl-old"}, "")["id"] == "chatcmpl-old"


def test_rebase_refreshes_both_timestamp_fields():
    before = int(time.time()) - 10_000
    body = {"id": "chatcmpl-old", "created": before}
    rebase_cached_body(body, "new")
    assert body["created"] > before

    resp = {"id": "resp_old", "created_at": before}
    rebase_cached_body(resp, "new")
    assert resp["created_at"] > before


@pytest.mark.parametrize("path,body,headers,id_prefix", [
    ("/v1/chat/completions",
     {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
     {"Authorization": "Bearer sk-master"}, "chatcmpl-"),
    ("/v1/messages",
     {"model": "gpt-4o", "max_tokens": 10,
      "messages": [{"role": "user", "content": "hi"}]},
     {"Authorization": "Bearer sk-master",
      "anthropic-version": "2023-06-01"}, "msg_"),
    ("/v1/completions",
     {"model": "gpt-4o", "prompt": "hi", "max_tokens": 10},
     {"Authorization": "Bearer sk-master"}, "cmpl-"),
    ("/v1/responses",
     {"model": "gpt-4o", "input": "hi", "store": True},
     {"Authorization": "Bearer sk-master"}, "resp_"),
])
async def test_cache_hit_is_served_under_its_own_request_id(
        path, body, headers, id_prefix):
    app = create_app(_cfg(300))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            with respx.mock:
                route = respx.post(
                    "https://api.openai.com/v1/chat/completions")
                route.respond(json=UPSTREAM_CHAT)
                first = await c.post(path, json=body, headers=headers)
                hit = await c.post(path, json=body, headers=headers)
            assert first.status_code == 200 and hit.status_code == 200
            assert route.call_count == 1, "second call must be a cache hit"
            assert hit.headers.get("x-wiwi-cache") == "HIT"

            own_id = hit.headers["x-wiwi-request-id"]
            assert hit.json()["id"] == f"{id_prefix}{own_id}", (
                "the hit must not replay the producing request's id")
            assert first.json()["id"] != hit.json()["id"]


async def test_responses_hit_id_resolves_to_its_own_stored_object():
    # The reason the id matters: it is a *handle*, not decoration.
    app = create_app(_cfg(300))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            headers = {"Authorization": "Bearer sk-master"}
            body = {"model": "gpt-4o", "input": "hi", "store": True}
            with respx.mock:
                route = respx.post(
                    "https://api.openai.com/v1/chat/completions")
                route.respond(json=UPSTREAM_CHAT)
                first = await c.post("/v1/responses", json=body, headers=headers)
                hit = await c.post("/v1/responses", json=body, headers=headers)
                assert route.call_count == 1

                hit_id = hit.json()["id"]
                # The id must be the HIT's own, not the producing request's:
                # both resolve pre-fix, so resolution alone cannot tell the
                # difference between "my object" and "someone else's object".
                assert hit_id == f"resp_{hit.headers['x-wiwi-request-id']}"
                assert hit_id != first.json()["id"]
                got = await c.get(f"/v1/responses/{hit_id}", headers=headers)
                assert got.status_code == 200, (
                    "the id handed back by a cache hit must name a stored object")
                assert got.json()["id"] == hit_id

                # And it must be usable as the anchor for the next turn. Still
                # inside the mock: the continuation dispatches upstream.
                nxt = {**body, "input": "again", "previous_response_id": hit_id}
                cont = await c.post("/v1/responses", json=nxt, headers=headers)
                assert cont.status_code == 200
                assert route.call_count == 2


async def test_cache_hit_logs_its_own_request_id():
    # The hit is a real request for accounting purposes: it gets its own row
    # with zero tokens, never the producer's id.
    app = create_app(_cfg(300))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            headers = {"Authorization": "Bearer sk-master"}
            body = {"model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}]}
            with respx.mock:
                respx.post("https://api.openai.com/v1/chat/completions").respond(
                    json=UPSTREAM_CHAT)
                await c.post("/v1/chat/completions", json=body, headers=headers)
                hit = await c.post("/v1/chat/completions", json=body, headers=headers)

        # The logging pump is async: poll the ring until the row lands.
        hit_id = hit.headers["x-wiwi-request-id"]
        deadline = time.monotonic() + 5.0
        rows = []
        while not rows and time.monotonic() < deadline:
            events = [e for _, e in
                      await app.state.wiwi.logs.sse.replay("request", 0)]
            rows = [e for e in events if e.request_id == hit_id]
            if not rows:
                await asyncio.sleep(0.05)
        assert rows, "the hit must have its own log row"
        assert rows[0].response_cache_hit is True
        assert rows[0].cache_hit is False, "must not inflate prompt-cache metrics"
        assert rows[0].tok_in == 0 and rows[0].tok_out == 0
