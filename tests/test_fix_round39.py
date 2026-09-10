"""Round-39 regression tests: journal replay integrity (#66, #67, #68).

Three live AUDIT findings in the durable stream-replay layer, each verified
against current source before this file was written:

1.  **#66 reconnect-to-empty-journal double-dispatches.** The replay gate in
    ``run_chat_like`` is ``if replay or is_complete(replay_id)`` — an
    *empty-but-active* journal (original stream still running in this
    process, no chunks journaled yet) yields ``replay == []`` and
    ``is_complete == False``, so a sub-second reconnect falls through to a
    FRESH upstream dispatch: the request is executed and billed twice while
    the original is still streaming. ``JournalStore.open()``'s eager
    file-touch exists precisely to prevent this, but the gate's shape defeats
    it (verified: `read_after -> []`, `is_complete -> False`, `_active` holds
    the id, gate `False`).
2.  **#67 journal replay is not scoped to the originating key.** The replay
    branch reads ``x-wiwi-stream-id`` and serves the journal's full chunk
    history to ANY authenticated caller holding the id. Nothing binds the
    journal to the virtual key that created it — the response-cache layer
    scopes its keys by ``key_id`` for exactly this reason.
3.  **#68 tape eviction desynchronizes resume.** ``StreamTape.replay``
    filters surviving entries by ``seq > last_seq``; when entries at or below
    ``last_seq`` were evicted, continuation building silently uses a partial
    tape (an evicted tool-call Open with surviving Args/Close yields no tool
    call — the model re-invokes a tool the client already saw).

Fixes under test:
- ``JournalStore.is_active()`` — same-process liveness, the missing half of
  the #66 gate; ``open()`` records the originating key id; ``owner_of()``
  reads it back (#67).
- The server's replay branch gates on ``replay or is_complete or is_active``
  (same process) and key-matches the caller, tailing instead of re-dispatch.
- ``StreamTape.head_evicted(last_seq)`` + resume fallback (#68).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app
from wiwi.streaming.tape_store import JournalStore

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config(tmp: Path) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=__import__("wiwi.config", fromlist=["RouterSettings"])
        .RouterSettings(stream_journal_dir=str(tmp / "journals")),
    )


@pytest.fixture
def store(tmp_path):
    return JournalStore(tmp_path / "js", ttl_s=600, max_bytes=1 << 20)


# ---------------------------------------------------------------------------
# 1. #66 — active-journal liveness (the missing gate half)
# ---------------------------------------------------------------------------

async def test_opened_journal_is_active(store):
    await store.open("req1")
    assert store.is_active("req1") is True
    store.release("req1")
    assert store.is_active("req1") is False


async def test_unknown_journal_is_not_active(store):
    assert store.is_active("never-opened") is False


async def test_active_journal_with_no_chunks_is_replay_gate_visible(store):
    """The exact #66 shape: open, no appends — the server must be able to
    distinguish this from 'no journal' (gate must be True)."""
    await store.open("req5")
    replay = store.read_after("req5", 0)
    complete = store.is_complete("req5")
    active = store.is_active("req5")
    # gate: replay or complete or ACTIVE (the new half)
    assert (replay or complete or active) is True


# ---------------------------------------------------------------------------
# 2. #67 — journal ownership scoping
# ---------------------------------------------------------------------------

async def test_open_records_originating_key(store):
    await store.open("req2", key_id="kid-A")
    assert store.owner_of("req2") == "kid-A"


async def test_open_without_key_records_none(store):
    await store.open("req3")
    assert store.owner_of("req3") is None


async def test_owner_of_unknown_journal_is_none(store):
    assert store.owner_of("nope") is None


async def test_owner_survives_chunks_and_finish(store):
    j = await store.open("req4", key_id="kid-B")
    await j.append(1, b"data: x\n\n")
    await j.finish(1)
    assert store.owner_of("req4") == "kid-B"
    assert store.is_complete("req4") is True
    store.release("req4")
    # ownership must survive release (it is read from the journal, not memory)
    assert store.owner_of("req4") == "kid-B"


# ---------------------------------------------------------------------------
# 3. #68 — evicted tape head detection
# ---------------------------------------------------------------------------

def test_head_evicted_detected_when_replay_starts_past_evicted_range():
    from wiwi.streaming import deltas as dl
    from wiwi.streaming.resume import StreamTape
    # Small tape: the two big text deltas evict everything appended before
    # them, and the first of the two evicts the second's own predecessor —
    # so by the end the tape head starts well past seq 2.
    tape = StreamTape(max_bytes=96)
    # StreamStart is control-only (seq 0, skipped by the tape), so the first
    # taped entry is the small text delta at seq 1.
    tape.append(dl.StreamStart(model="m"))
    seq1 = tape.append(dl.TextDelta("keep"))  # seq 1, evicted by the big ones
    tape.append(dl.TextDelta("y" * 4096))     # evicted
    tape.append(dl.TextDelta("z" * 4096))     # evicted
    tape.append(dl.ToolCallOpen(index=0, id="c0", name="f0"))
    tape.append(dl.ToolCallArgsDelta(index=0, args_fragment='{"a":1}'))
    tape.append(dl.ToolCallClose(index=0))
    tape.append(dl.Finish("tool_call"))
    tape.append(dl.StreamEnd())
    # Replaying from seq1: the first surviving entry is NOT seq1+1, so the
    # continuation's head (everything the client saw up to seq1) is gone.
    assert seq1 == 1
    entries = tape._entries
    assert entries, "test setup: tape must retain survivors"
    assert entries[0].seq > 2, "test setup: head must be evicted by the big deltas"
    assert tape.head_evicted(seq1) is True


def test_head_evicted_false_when_contiguous():
    from wiwi.streaming import deltas as dl
    from wiwi.streaming.resume import StreamTape
    tape = StreamTape(max_bytes=1 << 20)  # no eviction
    seq1 = tape.append(dl.StreamStart(model="m"))
    seq2 = tape.append(dl.TextDelta("hi"))
    tape.append(dl.StreamEnd())
    assert tape.head_evicted(seq1) is False
    assert tape.head_evicted(seq2) is False
    # replay from beyond the tape (nothing left) is not a gap — it's empty.
    assert tape.replay(seq2) == []


def test_head_evicted_false_when_replay_empty():
    """No survivors at all: resume is vacuously fine (fresh attempt is the
    same as continuation-from-nothing for text, but the helper must not
    claim a gap it cannot prove)."""
    from wiwi.streaming import deltas as dl
    from wiwi.streaming.resume import StreamTape
    tape = StreamTape(max_bytes=96)
    tape.append(dl.TextDelta("y" * 4096))
    last = tape.append(dl.StreamEnd())
    # everything evicted except the tail — replay from `last` is empty
    assert tape.replay(last) == []
    assert tape.head_evicted(last) is False


# ---------------------------------------------------------------------------
# End-to-end: reconnect to an ACTIVE journal must tail, not re-dispatch
# ---------------------------------------------------------------------------

STREAM_SSE = (
    b'data: {"choices":[{"delta":{"role":"assistant","content":"he"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    b'data: [DONE]\n\n'
)

_calls = [0]


async def _slow_sse():
    """TTFB long enough that the reconnect lands while the journal is
    open-but-empty — the exact #66 window."""
    _calls[0] += 1
    await asyncio.sleep(0.8)
    for chunk in STREAM_SSE.split(b"\n\n"):
        if chunk:
            yield chunk + b"\n\n"
            await asyncio.sleep(0.1)


@respx.mock
async def test_reconnect_to_active_journal_tails_not_redispatches(tmp_path):
    _calls[0] = 0
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=lambda request: httpx.Response(
            200, content=_slow_sse(),
            headers={"content-type": "text/event-stream"}))
    app = create_app(_config(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            # 1. start a streaming request in the background
            task = asyncio.create_task(c.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers=H))
            # 2. wait until the upstream was called once AND the journal
            #    file exists but is still EMPTY (the #66 window).
            jdir = tmp_path / "journals"
            rid = None
            for _ in range(100):
                files = sorted(jdir.glob("*.jsonl"),
                               key=lambda p: p.stat().st_mtime)
                if files and _calls[0] >= 1:
                    rid = files[-1].stem
                    if files[-1].stat().st_size == 0:
                        break
                await asyncio.sleep(0.02)
            assert rid, "no journal appeared — journaling misconfigured?"
            # 3. reconnect with the original request id while it is ACTIVE
            r2 = await c.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={**H, "x-wiwi-stream-id": rid, "Last-Event-ID": "0"})
            assert r2.status_code == 200
            assert r2.headers.get("x-wiwi-stream-replay") == rid
            # 4. the reconnect itself must NOT have triggered a new upstream
            #    dispatch — still exactly one call from step 1.
            assert _calls[0] == 1
            await task
            # 5. after the original finished, still one dispatch total.
            assert _calls[0] == 1


@respx.mock
async def test_cross_key_replay_blocked_same_key_allowed(tmp_path):
    """#67 end-to-end: a journal created by key A must not replay for key B
    (cross-tenant content disclosure), while key A replaying its own stream
    keeps working."""
    SSE = (b'data: {"choices":[{"delta":{"role":"assistant","content":"hi"}}]}\n\n'
           b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
           b'data: [DONE]\n\n')
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=SSE, headers={"content-type": "text/event-stream"})
    app = create_app(_config(tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            mh = {"Authorization": "Bearer sk-wiwi-master-test"}
            k1 = (await c.post("/admin/keys/generate", headers=mh,
                               json={"name": "one"})).json()["key"]
            k2 = (await c.post("/admin/keys/generate", headers=mh,
                               json={"name": "two"})).json()["key"]
            # key-1 streams; capture its request id
            r1 = await c.post("/v1/chat/completions",
                              json={"model": "gpt-4o", "stream": True,
                                    "messages": [{"role": "user", "content": "hi"}]},
                              headers={"Authorization": f"Bearer {k1}"})
            rid = r1.headers["x-wiwi-request-id"]
            # key-2 tries to replay key-1's stream: must NOT get a replay
            r2 = await c.post("/v1/chat/completions",
                              json={"model": "gpt-4o", "stream": True,
                                    "messages": [{"role": "user", "content": "hi"}]},
                              headers={"Authorization": f"Bearer {k2}",
                                       "x-wiwi-stream-id": rid,
                                       "Last-Event-ID": "0"})
            assert r2.headers.get("x-wiwi-stream-replay") is None, (
                "cross-key replay leaked another key's stream")
            # key-1 replays its own stream: allowed
            r3 = await c.post("/v1/chat/completions",
                              json={"model": "gpt-4o", "stream": True,
                                    "messages": [{"role": "user", "content": "hi"}]},
                              headers={"Authorization": f"Bearer {k1}",
                                       "x-wiwi-stream-id": rid,
                                       "Last-Event-ID": "0"})
            assert r3.headers.get("x-wiwi-stream-replay") == rid
