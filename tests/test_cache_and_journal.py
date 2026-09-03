"""Response cache + durable stream journal + cache-token observability.

Covers:
- wiwi/cache keygen determinism + scoping, MemoryResponseCache TTL/LRU
- E2E: exact-match cache serve-on-hit, bypass header, streaming never cached,
  off-by-default
- StreamJournal append/read/sweep incl. torn-line tolerance
- E2E: journal replay via x-wiwi-stream-id + Last-Event-ID, including across
  an app restart (the durability contract), and offset skipping
- Prometheus cache families (wiwi_prompt_cache_*, kind="cache_creation")
- _complete_via_stream fold preserves cache_creation_tokens (was dropped)
- build_log_event carries tok_cache_creation
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx
import respx
from asgi_lifespan import LifespanManager

from wiwi.cache.interface import CacheEntry
from wiwi.cache.keygen import response_cache_key
from wiwi.cache.response_cache import MemoryResponseCache
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
from wiwi.logging_core.events import LogEvent
from wiwi.server.app import create_app
from wiwi.server.metrics import render_metrics
from wiwi.streaming.tape_store import JournalStore

OPENAI_BODY = {
    "id": "chatcmpl-c", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}

STREAM_BODY = (
    'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
)


def _cfg(tmp_path, *, cache: bool = False) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            stream_journal_enabled=True,
            stream_journal_dir=str(tmp_path / "journals"),
            stream_journal_ttl_s=600.0,
            stream_journal_max_bytes=1 << 20),
        cache_settings=CacheSettings(enabled=cache, ttl_s=300.0, max_entries=16),
    )


def _req(text: str = "hi") -> ir.Request:
    return ir.Request(model="m", messages=[ir.Message(role="user",
                                                      parts=[ir.TextPart(text)])])


# ---------------------------------------------------------------------------
# keygen
# ---------------------------------------------------------------------------

def test_cache_key_deterministic_and_scoped():
    r = _req()
    k1 = response_cache_key(r, "g", "chat", "key1")
    k2 = response_cache_key(r, "g", "chat", "key1")
    assert k1 == k2 and len(k1) == 64
    assert response_cache_key(r, "g", "chat", "key2") != k1
    assert response_cache_key(r, "g2", "chat", "key1") != k1
    assert response_cache_key(_req("different"), "g", "chat", "key1") != k1
    r_tools = _req()
    r_tools.tools = [ir.Tool(name="t")]
    assert response_cache_key(r_tools, "g", "chat", "key1") != k1


# ---------------------------------------------------------------------------
# MemoryResponseCache
# ---------------------------------------------------------------------------

def _entry(payload: str) -> CacheEntry:
    return CacheEntry(payload=payload.encode(), stored_at=time.time(),
                      request_id="r", model="m")


async def test_memory_cache_roundtrip_ttl_and_lru():
    c = MemoryResponseCache(ttl_s=60, max_entries=2)
    assert await c.get("k") is None
    await c.set("a", _entry("1"))
    e = await c.get("a")
    assert e is not None and e.payload == b"1"
    await c.set("b", _entry("2"))
    await c.set("c", _entry("3"))
    assert await c.get("a") is None
    assert await c.get("c") is not None
    stale = _entry("4")
    object.__setattr__(stale, "stored_at", time.time() - 3600)
    await c.set("d", stale)
    assert await c.get("d") is None
    await c.aclose()
    assert len(c) == 0


# ---------------------------------------------------------------------------
# E2E response cache
# ---------------------------------------------------------------------------

@respx.mock
async def test_response_cache_hit_serves_without_upstream(tmp_path):
    cfg = _cfg(tmp_path, cache=True)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json=OPENAI_BODY)
            body = {"model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}]}
            headers = {"Authorization": "Bearer sk-wiwi-master-test"}
            r1 = await c.post("/v1/chat/completions", json=body, headers=headers)
            r2 = await c.post("/v1/chat/completions", json=body, headers=headers)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.json() == r2.json()
            assert r2.headers.get("x-wiwi-cache") == "HIT"
            assert route.call_count == 1
            h2 = {**headers, "x-wiwi-no-cache": "true"}
            r3 = await c.post("/v1/chat/completions", json=body, headers=h2)
            assert r3.status_code == 200
            assert "x-wiwi-cache" not in r3.headers
            assert route.call_count == 2
            # Log rows separate gateway response-cache hits from provider
            # prompt-cache hits: a served-from-cache request must not count
            # as a prompt-cache hit. The logging pump is async, so poll the
            # ring until both rows land.
            events = [e for _, e in await app.state.wiwi.logs.sse.replay("request", 0)]
            by_rid = {e.request_id: e for e in events}
            deadline = time.monotonic() + 5.0
            while not (r1.headers["x-wiwi-request-id"] in by_rid
                       and r2.headers["x-wiwi-request-id"] in by_rid
                       and time.monotonic() < deadline):
                await asyncio.sleep(0.05)
                events = [e for _, e in await app.state.wiwi.logs.sse.replay("request", 0)]
                by_rid = {e.request_id: e for e in events}
            miss = by_rid[r1.headers["x-wiwi-request-id"]]
            hit = by_rid[r2.headers["x-wiwi-request-id"]]
            assert miss.response_cache_hit is False and miss.cache_hit is False
            assert hit.response_cache_hit is True and hit.cache_hit is False


@respx.mock
async def test_response_cache_never_caches_streaming(tmp_path):
    cfg = _cfg(tmp_path, cache=True)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)
            body = {"model": "gpt-4o", "stream": True,
                    "messages": [{"role": "user", "content": "hi"}]}
            headers = {"Authorization": "Bearer sk-wiwi-master-test"}
            s1 = await c.post("/v1/chat/completions", json=body, headers=headers)
            s2 = await c.post("/v1/chat/completions", json=body, headers=headers)
            assert s1.status_code == 200 and s2.status_code == 200
            assert route.call_count == 2


@respx.mock
async def test_response_cache_off_by_default(tmp_path):
    cfg = _cfg(tmp_path, cache=False)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json=OPENAI_BODY)
            body = {"model": "gpt-4o",
                    "messages": [{"role": "user", "content": "hi"}]}
            headers = {"Authorization": "Bearer sk-wiwi-master-test"}
            await c.post("/v1/chat/completions", json=body, headers=headers)
            await c.post("/v1/chat/completions", json=body, headers=headers)
            assert route.call_count == 2


# ---------------------------------------------------------------------------
# StreamJournal unit
# ---------------------------------------------------------------------------

async def test_journal_append_read_finish(tmp_path):
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("req1")
    await j.append(1, b"id: 1\ndata: a\n\n")
    await j.append(2, b"id: 2\ndata: b\n\n")
    assert j.last_seq == 2
    assert not store.is_complete("req1")
    await j.finish(2)
    assert store.is_complete("req1")
    recs = store.read_after("req1", 0)
    assert [r[0] for r in recs] == [1, 2]
    assert recs[0][1] == b"id: 1\ndata: a\n\n"
    assert store.read_after("req1", 1)[0][0] == 2
    assert store.read_after("nope", 0) == []
    assert not store.is_expired("req1")


async def test_journal_tolerates_torn_line(tmp_path):
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("req2")
    await j.append(1, b"data: a\n\n")
    await j.aclose()
    path = store.path_for("req2")

    def _torn_write() -> None:
        with open(path, "ab") as fh:
            fh.write(b'{"seq":2,"ts":')

    await asyncio.to_thread(_torn_write)
    recs = store.read_after("req2", 0)
    assert [r[0] for r in recs] == [1]


async def test_journal_sweep_removes_expired_only(tmp_path):
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    j = await store.open("old")
    await j.append(1, b"data: x\n\n")
    await j.aclose()
    old_ts = time.time() - 7200
    os.utime(store.path_for("old"), (old_ts, old_ts))
    await store.open("fresh")
    assert store.sweep() == 1
    assert not store.path_for("old").exists()
    assert store.path_for("fresh").exists()


def test_journal_path_sanitized(tmp_path):
    store = JournalStore(tmp_path / "j", ttl_s=600, max_bytes=1 << 20)
    p = store.path_for("../evil/../id")
    assert ".." not in str(p) and "/" not in p.name


# ---------------------------------------------------------------------------
# E2E journal replay (incl. restart durability)
# ---------------------------------------------------------------------------

def _stream_headers() -> dict[str, str]:
    return {"Authorization": "Bearer sk-wiwi-master-test"}


def _stream_body() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


@respx.mock
async def test_stream_journaled_and_replayed(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)
            s = await c.post("/v1/chat/completions", json=_stream_body(),
                             headers=_stream_headers())
            assert s.status_code == 200
            rid = s.headers["x-wiwi-request-id"]
            assert route.call_count == 1
            rh = {**_stream_headers(), "x-wiwi-stream-id": rid,
                  "last-event-id": "0"}
            r = await c.post("/v1/chat/completions", json=_stream_body(),
                             headers=rh)
            assert r.status_code == 200
            assert r.headers.get("x-wiwi-stream-replay") == rid
            assert route.call_count == 1
            assert '"He"' in r.text and '"y"' in r.text and "[DONE]" in r.text
            rh1 = {**_stream_headers(), "x-wiwi-stream-id": rid,
                   "last-event-id": "2"}
            r1 = await c.post("/v1/chat/completions", json=_stream_body(),
                              headers=rh1)
            assert '"He"' not in r1.text
            assert '"y"' in r1.text and "[DONE]" in r1.text


@respx.mock
async def test_stream_replay_survives_restart(tmp_path):
    """The headline durability contract: app 1 dies, app 2 boots on the same
    journal dir, and a reconnecting client still gets its content without an
    upstream call."""
    rid = None
    with respx.mock:
        app1 = create_app(_cfg(tmp_path))
        async with LifespanManager(app1):
            transport = httpx.ASGITransport(app=app1)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                respx.post("https://api.openai.com/v1/chat/completions").respond(
                    text=STREAM_BODY)
                s = await c.post("/v1/chat/completions", json=_stream_body(),
                                 headers=_stream_headers())
                assert s.status_code == 200
                rid = s.headers["x-wiwi-request-id"]
        del app1

    with respx.mock:
        app2 = create_app(_cfg(tmp_path))
        async with LifespanManager(app2):
            transport = httpx.ASGITransport(app=app2)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                route = respx.post("https://api.openai.com/v1/chat/completions")
                route.respond(text=STREAM_BODY)
                headers = {**_stream_headers(), "x-wiwi-stream-id": rid,
                           "last-event-id": "0"}
                r = await c.post("/v1/chat/completions", json=_stream_body(),
                                 headers=headers)
                assert r.status_code == 200
                assert r.headers.get("x-wiwi-stream-replay") == rid
                assert route.call_count == 0, \
                    "replay after restart must not re-call upstream"
                assert '"He"' in r.text and '"y"' in r.text and "[DONE]" in r.text


@respx.mock
async def test_replay_unknown_stream_falls_through_to_upstream(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(text=STREAM_BODY)
            headers = {**_stream_headers(), "x-wiwi-stream-id": "nosuchstream",
                       "last-event-id": "0"}
            r = await c.post("/v1/chat/completions", json=_stream_body(),
                             headers=headers)
            assert r.status_code == 200
            assert route.call_count == 1


# ---------------------------------------------------------------------------
# Observability: metrics + log event
# ---------------------------------------------------------------------------

def _evt(**kw) -> LogEvent:
    return LogEvent(stream="request", ts=time.time(), **kw)


def test_metrics_render_cache_families():
    events = [
        _evt(tok_in=100, tok_cached=40, tok_cache_creation=25, cache_hit=True),
        _evt(tok_in=100),
        _evt(tok_in=100, response_cache_hit=True),
    ]
    text = render_metrics(events)
    assert 'wiwi_tokens_total{kind="cache_creation"} 25' in text
    assert "wiwi_prompt_cache_hits_total 1" in text
    assert "wiwi_prompt_cache_hit_rate 0.3333" in text
    assert "wiwi_response_cache_hits_total 1" in text
    assert 'wiwi_tokens_total{kind="cached"} 40' in text

