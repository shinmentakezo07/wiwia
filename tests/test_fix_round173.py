"""Round 173 — two open register items closed out.

**AUDIT #237 (read half).** The journal's read paths did blocking FS work on
the event loop: ``owner_of``/``read_after``/``is_complete`` each ran
``Path.read_bytes()`` synchronously, and the reconnect gate called all three
back-to-back — three whole-file reads per reconnect, on the loop, on a feature
that is on by default. The write paths had been routed through
``asyncio.to_thread`` since #105; the reads never were, and #282's incremental
tail cursor fixed only the poll loop, not the gate.

This round makes the read side async: ``replay_state`` reads the journal ONCE
off-loop and answers owner/frames/complete/active in one struct, and the
fallback reads gain ``*_async`` twins. The sync spellings stay for the offline
tools that own no loop (sweeper internals, tests).

**AUDIT #328 (regression gap).** The Gemini mid-conversation-system fix is in
the code and correct, but nothing pinned it — round 64's test covers the
Anthropic adapter only. A regression that exists in no test file is one refactor
away from silently regressing, and the register entry still said ``open``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.ir import types as ir
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.server.app import create_app
from wiwi.streaming.tape_store import JournalStore

MASTER = "sk-wiwi-master-test"

CHUNKS = b"".join([
    (
        b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
        b'[{"index":0,"delta":{"role":"assistant","content":"hel"},'
        b'"finish_reason":null}]}\n\n'
    ),
    (
        b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
        b'[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    ),
    b"data: [DONE]\n\n",
])

SSE_HEADERS = {"Content-Type": "text/event-stream"}


def _config(tmp_path: Path, journal_dir: str) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        router_settings=RouterSettings(stream_journal_dir=journal_dir),
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"),
    )

def _sbody() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


# -- 1. the store seam: one off-loop read answers the whole gate ----------


async def test_replay_state_answers_the_whole_gate_from_one_read(tmp_path):
    """``replay_state`` returns owner, frames, complete and active together.

    Pre-fix there was no such method: the gate read the whole file three times
    through three sync methods. The seam existing and being correct is what the
    server-side tests below drive; here the store contract itself is pinned on
    a finished journal, an unfinished one, and an unknown id.
    """
    store = JournalStore(str(tmp_path / "j"), ttl_s=600, max_bytes=1_048_576)
    j = await store.open("s1", key_id="kid-A")
    await j.append(1, b"data: a\n\n", first_seq=1)
    await j.finish(1)
    store.release("s1")

    st = await store.replay_state("s1", 0)
    assert st.owner == "kid-A"
    assert [seq for seq, _ in st.frames] == [1]
    assert st.complete is True
    assert st.active is False

    j2 = await store.open("s2", key_id="kid-B")
    await j2.append(1, b"data: b\n\n", first_seq=1)
    st2 = await store.replay_state("s2", 0)
    assert st2.owner == "kid-B"
    assert st2.complete is False
    assert st2.active is True

    st3 = await store.replay_state("nope", 0)
    assert st3.owner is None
    assert st3.frames == []
    assert st3.complete is False


async def test_replay_state_slices_frames_at_the_cursor(tmp_path):
    """The frame-granularity cursor (#493) survives the consolidated read."""
    store = JournalStore(str(tmp_path / "j"), ttl_s=600, max_bytes=1_048_576)
    j = await store.open("s1", key_id="kid-A")
    # One chunk carrying two frames, numbered 1..2 (first_seq=1).
    await j.append(2, b"data: a\n\ndata: b\n\n", first_seq=1)
    await j.finish(2)
    store.release("s1")

    assert [seq for seq, _ in (await store.replay_state("s1", 0)).frames] == [1, 2]
    assert [seq for seq, _ in (await store.replay_state("s1", 1)).frames] == [2]
    assert (await store.replay_state("s1", 2)).frames == []


async def test_async_read_twins_read_the_same_records_as_the_sync_ones(tmp_path):
    """``owner_of_async``/``read_after_async``/``is_complete_async`` keep the
    sync spellings' contract; the sweeper and offline tools still own them."""
    store = JournalStore(str(tmp_path / "j"), ttl_s=600, max_bytes=1_048_576)
    j = await store.open("s1", key_id="kid-A")
    await j.append(1, b"data: a\n\n", first_seq=1)
    await j.finish(1)
    store.release("s1")

    assert await store.read_after_async("s1", 0) == store.read_after("s1", 0)
    assert (await store.is_complete_async("s1")) is True


# -- 2. the reads run OFF the event loop -----------------------------------


async def test_replay_gate_reads_do_not_block_the_event_loop(tmp_path, monkeypatch):
    """#237's own measurement shape: while a replay-state read is in flight,
    loop callbacks must keep running. Pre-fix, ``read_bytes`` of a ~1 MiB
    journal stalled the loop for the whole read (measured 8.2 ms gap).

    Instrumented by counting loop ticks driven by a peer task while the read
    executes, against a padded journal large enough that a synchronous read
    starves the ticker.
    """
    import time

    store = JournalStore(str(tmp_path / "j"), ttl_s=600,
                         max_bytes=4 * 1024 * 1024)
    j = await store.open("s1", key_id="kid-A")
    pad = b"data: " + b"x" * 2048 + b"\n\n"
    for i in range(1, 400):
        await j.append(i, pad, first_seq=i)
    await j.finish(399)
    store.release("s1")

    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)

    t = asyncio.create_task(ticker())
    t0 = time.perf_counter()
    st = await store.replay_state("s1", 0)
    elapsed = time.perf_counter() - t0
    stop.set()
    await t

    assert len(st.frames) == 399
    # A blocking read of ~1 MiB starves ``sleep(0)`` completely (pre-fix
    # measurement: zero ticks during the read). Off-loop, the ticker runs.
    assert ticks > 0, (
        f"event loop starved for the whole read ({elapsed * 1000:.1f} ms)")


# -- 3. the server gate uses the async seam (end-to-end reconnect) --------


@respx.mock
async def test_reconnect_replay_still_serves_from_disk(tmp_path):
    """End-to-end control: after the gate moves to the async seam, a reconnect
    replays the finished journal with no second upstream call."""
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    journal_dir = str(tmp_path / "journals")
    app = create_app(_config(tmp_path, journal_dir))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
        vk = {"Authorization": f"Bearer {plain}"}
        r = await c.post("/v1/chat/completions", json=_sbody(), headers=vk)
        assert r.status_code == 200, r.text
        rid = r.headers["x-wiwi-request-id"]
        assert app.state.wiwi.journals.is_complete(rid) is True

        r2 = await c.post("/v1/chat/completions", json=_sbody(), headers={
            **vk, "x-wiwi-stream-id": rid, "last-event-id": "0"})
        assert r2.status_code == 200, r2.text
        assert b'"content":"hel"' in r2.content, r2.text
        assert route.call_count == 1, "replay must not call upstream"


@respx.mock
async def test_replay_state_gate_enforces_owner_scoping(tmp_path):
    """The consolidated read must not lose the #67/#320 owner gate: a second
    key reconnecting under the first key's stream id is refused a replay and
    billed as a fresh request instead."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    journal_dir = str(tmp_path / "journals")
    app = create_app(_config(tmp_path, journal_dir))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain_a, _kid_a = await app.state.wiwi.auth.create_key(alias="a")
        plain_b, _kid_b = await app.state.wiwi.auth.create_key(alias="b")
        r = await c.post("/v1/chat/completions", json=_sbody(),
                         headers={"Authorization": f"Bearer {plain_a}"})
        assert r.status_code == 200, r.text
        rid = r.headers["x-wiwi-request-id"]

        r2 = await c.post("/v1/chat/completions", json=_sbody(), headers={
            "Authorization": f"Bearer {plain_b}",
            "x-wiwi-stream-id": rid, "last-event-id": "0"})
        assert r2.status_code == 200, r2.text
        # B paid for its own fresh answer: two upstream calls total.
        assert respx.post(
            "https://api.openai.com/v1/chat/completions").call_count == 2


# -- 4. AUDIT #328: pin the Gemini mid-conversation system behaviour ------


def _gem(msgs: list[tuple[str, str]]) -> ir.Request:
    return ir.Request(
        model="gem", gen_params=ir.GenParams(max_tokens=8),
        messages=[ir.Message(role=r, parts=[ir.TextPart(text=t)])
                  for r, t in msgs])


def test_gemini_leading_system_becomes_system_instruction():
    req = _gem([("system", "s1"), ("user", "u1")])
    body = GeminiAdapter().encode_request(req, "gem-2", {})
    assert body["systemInstruction"] == {"parts": [{"text": "s1"}]}
    assert [c["role"] for c in body["contents"]] == ["user"]


def test_gemini_mid_conversation_system_keeps_its_position():
    """The #328 defect: every system message was hoisted into
    ``systemInstruction``, destroying its position relative to the turns
    around it. The fix folds a later system message into the adjacent user
    content as explicitly-marked text — never to the front."""
    req = _gem([("system", "s1"), ("user", "u1"),
                ("system", "s2"), ("user", "u2")])
    body = GeminiAdapter().encode_request(req, "gem-2", {})
    # Only the LEADING system block is the instruction.
    assert body["systemInstruction"] == {"parts": [{"text": "s1"}]}
    # s2 stays between u1 and u2 — folded into the adjacent user content as
    # explicitly-marked text, so its position relative to both turns survives
    # and no ``role: system`` entry reaches ``contents``.
    assert [c["role"] for c in body["contents"]] == ["user"]
    texts = [p["text"] for c in body["contents"] for p in c["parts"]]
    assert texts == ["u1", "[system]\ns2", "u2"]


def test_gemini_system_after_model_turn_opens_its_own_user_entry():
    """A system message that follows a model turn cannot merge backwards; it
    opens its own user entry so the ordering relative to the model turn is
    preserved."""
    req = _gem([("user", "u1"), ("assistant", "a1"), ("system", "s2")])
    body = GeminiAdapter().encode_request(req, "gem-2", {})
    assert "systemInstruction" not in body
    roles = [c["role"] for c in body["contents"]]
    texts = [p["text"] for c in body["contents"] for p in c["parts"]]
    assert roles == ["user", "model", "user"]
    assert texts == ["u1", "a1", "[system]\ns2"]


def test_gemini_empty_mid_conversation_system_is_dropped():
    """A system part with no text contributes nothing — no phantom empty
    user entry and no empty instruction part."""
    req = _gem([("user", "u1"), ("system", "")])
    body = GeminiAdapter().encode_request(req, "gem-2", {})
    assert "systemInstruction" not in body
    assert [p["text"] for c in body["contents"] for p in c["parts"]] == ["u1"]
