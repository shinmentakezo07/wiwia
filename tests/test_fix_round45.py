"""Round-45 regression tests — AUDIT #115-#118.

Four defects found by a fresh audit pass; each is a failure the existing
suite cannot see because the shipped fix's regression test exercised the
code differently than production does.

- #115 an expired bounded retirement never revives on the live request path:
  ``available`` excluded ``invalid`` even after the cooldown window elapsed,
  so ``pick_deployment`` filtered the deployment out and ``pick_key`` /
  ``recover()`` was unreachable for exactly the single-key provider the #69
  fix was filed against. The round-41 test passed because it called
  ``key.recover()`` manually — no production path does.
- #116 a non-streaming response was cached BEFORE the virtual-key budget
  check, so a 402'd completion was served as free 200 cache hits (zero
  spend recorded) for the whole TTL.
- #117 a reconnect whose replay gate misses journals under its own NEW
  request id, so later reconnects (still carrying the original stream id)
  can never see the re-dispatched attempt as active and dispatch again
  while it is still running — double upstream call, double billing. The
  fix adopts the client's stream id and opens the journal before dispatch.
- #118 the mid-stream resume continuation flattened thinking deltas to
  unsigned bare text, dropping signatures and ``redacted_thinking`` blobs;
  Anthropic validates signatures on the final assistant turn, so resume
  could never succeed for extended-thinking streams.

Each test fails against the pre-fix source.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import orjson
import respx
from asgi_lifespan import LifespanManager

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
from wiwi.providers.registry import fresh_adapter
from wiwi.router.router import ProviderAccount, ProviderKey, Router
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.streaming.resume import StreamTape, build_continuation_messages

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

OPENAI_BODY = {
    "id": "chatcmpl-c", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant",
                                         "content": "hello"},
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


# ---------------------------------------------------------------------------
# #115 — expired bounded retirement must revive on the live path
# ---------------------------------------------------------------------------

def _storm(key: ProviderKey) -> None:
    """Drive a key to bounded retirement the way ``on_result`` does."""
    for _ in range(5):
        key.err_count += 1
        key.mark_invalid(60.0)


def test_expired_bounded_retirement_is_available_again():
    """The bounded window of a 5xx-storm retirement must lapse back into
    availability. Pre-fix: ``available`` excluded 'invalid' unconditionally,
    so the key was gone forever even after the window elapsed."""
    key = ProviderKey(label="only", secret="s")
    _storm(key)
    assert not key.available, "sanity: retired within its window"

    key.cooldown_until = time.monotonic() - 0.001  # window elapsed
    assert key.available, (
        "a bounded retirement (mark_invalid with a window) must become "
        "available again once the window elapses; otherwise the revival "
        "path pick_key -> recover() is unreachable and a single-key "
        "provider 503s forever"
    )


async def test_expired_retired_key_revives_through_pick_key():
    """The live path: healthy -> pick_key. pick_key is the only production
    caller of recover(); it must be reachable once the window elapses."""
    key = ProviderKey(label="only", secret="s")
    acct = ProviderAccount(name="p1", provider_type="openai",
                           base_url="http://x", keys=[key])
    _storm(key)
    key.cooldown_until = time.monotonic() - 0.001

    assert acct.healthy, (
        "deployment selection gates on ProviderAccount.healthy; an expired "
        "bounded retirement must not make a provider permanently unhealthy"
    )
    picked, _ = await acct.pick_key()
    assert picked is key
    assert key.status == "active", "pick_key must run recover() on the expired key"


async def test_terminal_invalid_stays_out_of_rotation():
    """``mark_invalid(None)`` (no window) is deliberately terminal: cooldown
    stays 0.0 and the key must NOT become available merely because no
    cooldown is pending."""
    key = ProviderKey(label="dead", secret="s")
    key.mark_invalid(None)
    assert key.cooldown_until == 0.0
    assert not key.available
    acct = ProviderAccount(name="p1", provider_type="openai",
                           base_url="http://x", keys=[key])
    assert not acct.healthy
    picked, wait = await acct.pick_key()
    assert picked is None
    assert wait > 0  # a finite retry hint, not a crash


async def test_pick_key_soonest_covers_invalid_windows():
    """When every key is unavailable, the returned retry hint must consider
    an invalid key's pending window, not only cooling keys."""
    k_invalid = ProviderKey(label="inv", secret="s")
    k_invalid.mark_invalid(5.0)          # window ends sooner
    k_invalid.cooldown_until = time.monotonic() + 5.0
    k_cooling = ProviderKey(label="cool", secret="s")
    k_cooling.mark_cooling(30.0)         # cooling window ends later
    acct = ProviderAccount(name="p1", provider_type="openai",
                           base_url="http://x",
                           keys=[k_invalid, k_cooling])
    picked, wait = await acct.pick_key()
    assert picked is None
    assert 0 < wait <= 5.5, f"soonest window is the invalid key's 5s, got {wait}"


async def test_expired_retired_key_reaches_pick_deployment():
    """End-to-end through the Router: after the storm's window elapses,
    deployment selection must yield the deployment again (pre-fix it
    returned None and execute_with_retries answered 503 forever)."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p",
                                                            model="m"))],
        router_settings=RouterSettings(num_retries=0),
    )
    router = Router(cfg)
    key = router.providers["p"].keys[0]
    _storm(key)
    key.cooldown_until = time.monotonic() - 0.001

    _group, deps = router.resolve_group("m")
    assert deps, "model group must still resolve"
    dep = router.pick_deployment(deps, None)
    assert dep is not None, (
        "an expired bounded retirement must not filter the only deployment "
        "out of selection — that is the 503-forever shape of AUDIT #115"
    )
    picked, _ = await dep.provider.pick_key()
    assert picked is not None and picked.status == "active"


# ---------------------------------------------------------------------------
# #116 — a 402'd response must never enter the response cache
# ---------------------------------------------------------------------------

def _chat_body() -> dict:
    return {"model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}]}


@respx.mock
async def test_cache_never_serves_over_budget_payload(tmp_path):
    """First request breaches the key's budget -> 402. Pre-fix the payload
    was cached BEFORE that decision, so the identical retry got a 200 with
    the full completion and zero spend recorded, for the whole TTL."""
    cfg = _cfg(tmp_path, cache=True)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            # Make gpt-4o priced so the request carries a real cost.
            pr = await c.put(
                "/admin/pricing/gpt-4o",
                json={"input_per_1m": 1.0, "output_per_1m": 1.0},
                headers=AUTH)
            assert pr.status_code == 200, pr.text

            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="capped", max_budget=1e-9)
            vk = {"Authorization": f"Bearer {plain}"}

            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json=OPENAI_BODY)

            r1 = await c.post("/v1/chat/completions", json=_chat_body(),
                              headers=vk)
            assert r1.status_code == 402, (
                f"sancy: a max_budget=0 key must be refused, got {r1.status_code}"
            )
            # The identical retry must hit the budget wall again — never a
            # free cached 200.
            r2 = await c.post("/v1/chat/completions", json=_chat_body(),
                              headers=vk)
            assert r2.status_code == 402, (
                "a response whose spend was refused with 402 must not be "
                "cached and replayed as a free 200 for the TTL"
            )
            assert r2.json()["error"]["type"] == "budget_exceeded"
            assert route.call_count == 2


@respx.mock
async def test_cache_still_serves_funded_keys(tmp_path):
    """Control for the reorder: a key WITH budget still gets 200 + HIT +
    exactly one upstream call."""
    cfg = _cfg(tmp_path, cache=True)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            await c.put("/admin/pricing/gpt-4o",
                        json={"input_per_1m": 1.0, "output_per_1m": 1.0},
                        headers=AUTH)
            plain, _kid = await app.state.wiwi.auth.create_key(
                alias="funded", max_budget=10.0)
            vk = {"Authorization": f"Bearer {plain}"}
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json=OPENAI_BODY)

            r1 = await c.post("/v1/chat/completions", json=_chat_body(),
                              headers=vk)
            assert r1.status_code == 200
            r2 = await c.post("/v1/chat/completions", json=_chat_body(),
                              headers=vk)
            assert r2.status_code == 200
            assert r2.headers.get("x-wiwi-cache") == "HIT"
            assert route.call_count == 1


# ---------------------------------------------------------------------------
# #117 — a re-dispatched reconnect must be visible to later reconnects
# ---------------------------------------------------------------------------

def _stream_body_and_headers() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


@respx.mock
async def test_reconnect_during_redispatch_ttft_tails_not_double_dispatch(tmp_path):
    """Client reconnects with stream id 'R' (its earlier attempt died
    without a done record); the gate misses, so this attempt dispatches
    upstream. While it waits for its first token, the client reconnects
    AGAIN with the same id — that second reconnect must tail the journal,
    never dispatch a second upstream call. Pre-fix the re-dispatch journaled
    under its own NEW request id, so the gate could not see it and both
    calls billed."""
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            attempts = 0
            ttft = asyncio.Event()

            async def slow_upstream(request):
                nonlocal attempts
                attempts += 1
                await asyncio.wait_for(ttft.wait(), timeout=15.0)
                return httpx.Response(200, text=STREAM_BODY)

            respx.post("https://api.openai.com/v1/chat/completions").mock(
                side_effect=slow_upstream)

            rid = "target-stream"
            rh = {**AUTH, "x-wiwi-stream-id": rid, "last-event-id": "0"}

            # Reconnect #1: gate misses (no journal for 'target-stream') ->
            # dispatch. With the fix, it journals under 'target-stream'
            # BEFORE the upstream call is even made.
            t1 = asyncio.create_task(c.post("/v1/chat/completions",
                                            json=_stream_body_and_headers(),
                                            headers=rh))
            for _ in range(500):
                if attempts:
                    break
                await asyncio.sleep(0.01)
            assert attempts == 1
            # The adopted journal must already be active pre-dispatch.
            assert app.state.wiwi.journals.is_active(rid)

            # Reconnect #2, while attempt #1 is still in TTFT.
            t2 = asyncio.create_task(c.post("/v1/chat/completions",
                                            json=_stream_body_and_headers(),
                                            headers=rh))
            await asyncio.sleep(0.3)
            assert attempts == 1, (
                "a reconnect during a re-dispatch's time-to-first-token must "
                "tail the journal, not dispatch a duplicate upstream call "
                "(double upstream call, double billing)"
            )

            ttft.set()
            r1 = await asyncio.wait_for(t1, timeout=15.0)
            r2 = await asyncio.wait_for(t2, timeout=15.0)
            assert r1.status_code == 200 and r2.status_code == 200
            assert r2.headers.get("x-wiwi-stream-replay") == rid
            # The tail picked up the content the original attempt produced.
            assert "He" in r2.text and "[DONE]" in r2.text
            assert attempts == 1


@respx.mock
async def test_pre_dispatch_failure_releases_adopted_journal(tmp_path):
    """If the re-dispatched reconnect fails before its first chunk, the
    adopted journal must not linger in the active set (nothing may tail a
    stream the client never received content for)."""
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                status_code=500)
            rid = "doomed-stream"
            rh = {**AUTH, "x-wiwi-stream-id": rid, "last-event-id": "0"}
            r = await c.post("/v1/chat/completions",
                             json=_stream_body_and_headers(), headers=rh)
            assert r.status_code == 502
            assert not app.state.wiwi.journals.is_active(rid), (
                "a failed-before-first-chunk attempt must release its journal"
            )


# ---------------------------------------------------------------------------
# #118 — resume continuation preserves thinking fidelity
# ---------------------------------------------------------------------------

def test_continuation_keeps_signature_and_redacted_blocks():
    """Taped thinking must come back as structured ThinkingParts: the
    signature travels with its block, redacted blobs stay their own block
    type. Pre-fix: one unsigned bare-text part, redacted content gone."""
    tape = StreamTape()
    tape.append(dl.ThinkingDelta(text="alpha "))
    tape.append(dl.ThinkingDelta(text="beta", signature="sig-1"))
    tape.append(dl.TextDelta(text="answer"))
    tape.append(dl.ThinkingDelta(text="", block_type="redacted_thinking",
                                 data="BLOB123"))
    msgs = build_continuation_messages(tape, [])
    assistant = next(m for m in msgs if m.role == "assistant")
    thinking = [p for p in assistant.parts if isinstance(p, ir.ThinkingPart)]
    assert len(thinking) == 2
    assert thinking[0].text == "alpha beta"
    assert thinking[0].signature == "sig-1", (
        "the continuation's thinking block must carry the taped signature; "
        "Anthropic rejects an unsigned thinking block in the final assistant "
        "turn, so resume could never succeed for extended-thinking streams"
    )
    redacted = [p for p in thinking if p.block_type == "redacted_thinking"]
    assert redacted and redacted[0].data == "BLOB123", (
        "redacted_thinking blobs must survive the continuation; they are "
        "mandatory before tool use on some turns"
    )


def test_continuation_thinking_encodes_to_valid_anthropic_blocks():
    """The full resume contract: what the continuation builder emits must
    encode into Anthropic wire blocks Anthropic accepts."""
    tape = StreamTape()
    tape.append(dl.ThinkingDelta(text="alpha beta", signature="sig-1"))
    tape.append(dl.ThinkingDelta(text="", block_type="redacted_thinking",
                                 data="BLOB123"))
    msgs = build_continuation_messages(tape, [])
    adapter = fresh_adapter("anthropic")
    out = adapter.encode_request(
        ir.Request(model="claude-x", messages=msgs), "claude-x", {})
    assistant = next(m for m in out["messages"] if m["role"] == "assistant")
    content = assistant["content"]
    assert {"type": "thinking", "thinking": "alpha beta",
            "signature": "sig-1"} in content
    assert {"type": "redacted_thinking", "data": "BLOB123"} in content


def test_continuation_splits_thinking_runs_on_interleaved_text():
    """Text between thinking deltas starts a NEW block: signatures must not
    be glued onto text they never signed."""
    tape = StreamTape()
    tape.append(dl.ThinkingDelta(text="first", signature="sig-a"))
    tape.append(dl.TextDelta(text="visible"))
    tape.append(dl.ThinkingDelta(text="second", signature="sig-b"))
    msgs = build_continuation_messages(tape, [])
    assistant = next(m for m in msgs if m.role == "assistant")
    thinking = [p for p in assistant.parts if isinstance(p, ir.ThinkingPart)]
    assert [(p.text, p.signature) for p in thinking] == [
        ("first", "sig-a"), ("second", "sig-b")]


def test_replay_thinking_still_returns_joined_text():
    """The legacy helper stays working for its existing callers/tests."""
    tape = StreamTape()
    tape.append(dl.ThinkingDelta(text="thin"))
    tape.append(dl.ThinkingDelta(text="king", signature="s"))
    assert tape.replay_thinking() == "thinking"


def test_tool_args_join_survives_many_fragments():
    """#104 residual: argument buffers join once per close, not per
    fragment (O(n^2) string concatenation on long argument streams)."""
    full = orjson.dumps({"k": "v", "pad": "x" * 40}).decode()
    tape = StreamTape()
    tape.append(dl.ToolCallOpen(index=0, id="c1", name="f"))
    # Fragment the argument JSON the way a stream does: pieces of one doc.
    step = 7
    for i in range(0, len(full), step):
        tape.append(dl.ToolCallArgsDelta(index=0, args_fragment=full[i:i + step]))
    tape.append(dl.ToolCallClose(index=0))
    calls = tape.replay_tool_calls()
    assert len(calls) == 1
    assert calls[0].args == orjson.loads(full)
