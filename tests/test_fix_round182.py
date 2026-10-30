"""Round 182 — the second audit sweep over cache/server/streaming/wire.

Every test pins one defect from the audit pass over ``wiwi/cache``,
``wiwi/server``, ``wiwi/streaming`` and ``wiwi/wire``. The through-line is
again asymmetry: a guard that one exit path had and its sibling did not.

* **S1** a warning header longer than the cap was truncated with U+2026, and
  Starlette encodes headers as latin-1 — so a billed 200 became a 500 (after
  ``record_spend`` on the non-streaming path).
* **S2** deleting a YAML-defined provider wrote no tombstone, so a restart
  rebuilt the account, plaintext key included.
* **S3** a corrupt response-cache entry raised out of the hit path: a 500 with
  no request-log row and an un-refunded admission reservation.
* **S4** the Cline OAuth callback never enforced its pending-session TTL.
* **S5** a stream aborted before ``UsageFinal`` never refunded its RPM /
  estimated-TPM slot.
* **S6** ``/metrics`` was bearer-only while every other operator surface
  accepts a session cookie.
* **S7** the last-admin guard was check-then-act across three awaits.
* **S8** deleting one Cline default model popped the whole ``cline:<id>``
  group, including deployments of other provider types.
* **W1** ``context_window_exceeded`` encoded as ``finish_reason: "stop"``.
* **W2** the legacy ``functions``/``function_call`` protocol was dropped.
* **W3** ``/v1/completions`` rejected ``logprobs: false`` / ``echo: false``.
* **W4** the Anthropic encoder emitted content blocks after ``event: error``.
* **W5** a hosted ``tool_search`` item had one id in ``output_item.added`` and
  another in ``output_item.done``.
* **W6** Responses accumulated tool arguments with ``+=`` (quadratic).
* **W7/W8** a Chat ``file`` block with ``file_id`` decoded to an ImagePart, and
  ``input_audio`` had no type guard.
* **W9** ``with_history`` swallowed a malformed ``input`` before the decoder
  could reject it.
* **T1** ``JournalStore.is_complete`` read from cursor 0, so a zero-frame
  stream's done record was invisible.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import orjson
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.cache.interface import CacheEntry
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
from wiwi.ir import translation as tr
from wiwi.ir import types as ir
from wiwi.server.app import _warning_header_value, create_app
from wiwi.streaming import deltas as dl
from wiwi.streaming.tape_store import JournalStore
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as ocomp
from wiwi.wire import openai_responses as oresp

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_BODY = {
    "id": "chatcmpl-c", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}
STREAM_BODY = (
    b'data: {"choices":[{"delta":{"role":"assistant","content":"a"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'
)


def _config(*, router: RouterSettings | None = None,
            cache: bool = False, db_url: str = "sqlite+aiosqlite:///:memory:",
            providers: list[ProviderDef] | None = None) -> WiwiConfig:
    return WiwiConfig(
        providers=providers or [ProviderDef(name="p1", provider="openai",
                                            keys=[KeyDef(label="a", key="k1")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER, database_url=db_url),
        router_settings=router or RouterSettings(),
        cache_settings=CacheSettings(enabled=cache, ttl_s=300.0, max_entries=16),
    )


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# S1 — a warning header must be latin-1 on the wire
# ---------------------------------------------------------------------------

def test_warning_header_value_is_latin1_safe_and_ascii_truncated():
    long = ["tool 'cache_control' has no Chat Completions equivalent and was dropped"] * 13
    value = _warning_header_value(long)
    # The old marker was U+2026, which raised UnicodeEncodeError when Starlette
    # encoded the response headers — after the upstream had been billed.
    value.encode("latin-1")
    assert value.endswith("...")
    assert "…" not in value
    assert len(value) <= 900
    # A non-latin-1 character inside a warning (an operator-named model or a
    # caller-supplied tool name) is coerced, not raised.
    coerced = _warning_header_value(["model '\U0001f600' is unpriced"])
    coerced.encode("latin-1")


@respx.mock
async def test_many_dropped_tool_flags_still_answer_200_with_a_header():
    """The end-to-end shape: 13 warnings overflow the cap on a billed turn."""
    app = create_app(_config())
    async with LifespanManager(app), _client(app) as c:
        respx.post(OPENAI_URL).respond(json=OPENAI_BODY)
        tools = [{"name": f"t{i}", "input_schema": {"type": "object"},
                  "cache_control": {"type": "ephemeral"}} for i in range(13)]
        r = await c.post("/v1/messages", headers={**AUTH,
                                                  "anthropic-version": "2023-06-01"},
                         json={"model": "gpt-4o", "max_tokens": 64,
                               "messages": [{"role": "user", "content": "hi"}],
                               "tools": tools})
        assert r.status_code == 200, r.text
        header = r.headers["x-wiwi-translation-warnings"]
        header.encode("latin-1")
        assert header.endswith("...")


# ---------------------------------------------------------------------------
# S2 — deleting a YAML provider must leave a tombstone
# ---------------------------------------------------------------------------

async def test_deleting_a_yaml_provider_survives_restart(tmp_path):
    db = tmp_path / "delete.db"

    def _boot() -> tuple:
        cfg = _config(db_url=f"sqlite+aiosqlite:///{db}",
                      providers=[
                          ProviderDef(name="p1", provider="openai",
                                      keys=[KeyDef(label="a", key="k1")]),
                          ProviderDef(name="orphan", provider="openai",
                                      keys=[KeyDef(label="a", key="k2")]),
                      ])
        app = create_app(cfg)
        return app, LifespanManager(app)

    app1, lm1 = _boot()
    async with lm1, _client(app1) as c:
        r = await c.delete("/admin/providers/orphan", headers=AUTH)
        assert r.status_code == 200, r.text

    app2, lm2 = _boot()
    async with lm2, _client(app2) as c:
        listing = (await c.get("/admin/providers", headers=AUTH)).json()
        names = [p["name"] for p in listing["providers"]]
        assert "orphan" not in names, (
            "a deleted YAML account was rebuilt from the config on restart")
        assert "p1" in names


# ---------------------------------------------------------------------------
# S3 — a corrupt cache entry is a MISS, not a 500
# ---------------------------------------------------------------------------

class _CorruptCache:
    """Returns one undecodable entry for every key, and records deletes."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.sets = 0

    async def get(self, key: str) -> CacheEntry | None:
        return CacheEntry(payload=b'{"not":"what we stored"', stored_at=time.time())

    async def set(self, key: str, entry: CacheEntry) -> None:
        self.sets += 1

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

    async def aclose(self) -> None:
        return None


@respx.mock
async def test_corrupt_cache_entry_degrades_to_a_miss():
    app = create_app(_config(cache=True))
    async with LifespanManager(app), _client(app) as c:
        stub = _CorruptCache()
        app.state.wiwi.response_cache = stub
        route = respx.post(OPENAI_URL)
        route.respond(json=OPENAI_BODY)
        r = await c.post("/v1/chat/completions", headers=AUTH,
                         json={"model": "gpt-4o",
                               "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "hi"
        assert route.call_count == 1
        assert stub.deleted, "the undecodable entry was left in the cache"
        assert "x-wiwi-cache" not in r.headers
        # And the request was logged: the failure used to escape before the
        # log call, leaving no row at all.
        events = [e for _, e in await app.state.wiwi.logs.sse.replay("request", 0)]
        deadline = time.monotonic() + 5.0
        while not events and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
            events = [e for _, e in
                      await app.state.wiwi.logs.sse.replay("request", 0)]
        assert events, "no request-log row for the corrupt-cache miss"


# ---------------------------------------------------------------------------
# S4 — the Cline callback enforces its pending-session TTL
# ---------------------------------------------------------------------------

async def test_cline_callback_rejects_an_expired_state_token():
    app = create_app(_config())
    async with LifespanManager(app), _client(app) as c:
        app.state.wiwi.oauth_pending["stale-token"] = {
            "kind": "cline_redirect", "provider": "p1",
            "return_path": "/console/oauth",
            "created_at": time.time() - 3600.0, "ttl_s": 600.0,
        }
        r = await c.get("/cline/oauth/callback",
                        params={"code": "abc", "state": "stale-token"})
        assert r.status_code == 400, r.text
        assert "expired" in r.text
        # Single-use: the pop happened, so the token cannot be retried.
        assert "stale-token" not in app.state.wiwi.oauth_pending


# ---------------------------------------------------------------------------
# S5 — an aborted stream refunds its RPM/estimated-TPM slot
# ---------------------------------------------------------------------------

@respx.mock
async def test_a_stream_that_exits_without_usage_refunds_its_slot(monkeypatch):
    """A stream that exits with NO usage must refund its admission slot.

    Every streaming exit that can price its partial delivery sets
    ``ctx.usage``, which makes the admission reservation a real charge. The
    tail's other arm — no usage at all — used to release only the budget
    reservation, never the RPM slot or the estimated TPM, so the reservation
    throttled the key (and the global scope) for the rest of the window. The
    pricing fault below is how that arm is reached deterministically: its
    exception is suppressed by the pump, so the request still ends with an
    error frame and no usage.
    """
    from wiwi.core.gateway import Gateway

    def _unpriceable(self, *args, **kwargs):
        raise RuntimeError("pricing exploded")

    monkeypatch.setattr(Gateway, "_price_stream", _unpriceable)
    cfg = _config(router=RouterSettings(global_rpm=1))

    def _respond(request: httpx.Request) -> httpx.Response:
        body = orjson.loads(request.content)
        if body.get("stream"):
            return httpx.Response(200,
                                  headers={"content-type": "text/event-stream"},
                                  content=STREAM_BODY)
        return httpx.Response(200, json=OPENAI_BODY)

    app = create_app(cfg)
    async with LifespanManager(app), _client(app) as c:
        respx.post(OPENAI_URL).mock(side_effect=_respond)
        async with c.stream("POST", "/v1/chat/completions", headers=AUTH,
                            json={"model": "gpt-4o", "stream": True,
                                  "messages": [{"role": "user",
                                                "content": "hi"}]}) as r:
            assert r.status_code == 200
            async for _chunk in r.aiter_bytes():
                break  # hang up mid-stream, before UsageFinal

        # The teardown tail is shielded and may complete a tick later.
        deadline = time.monotonic() + 5.0
        r2 = None
        while True:
            r2 = await c.post("/v1/chat/completions", headers=AUTH,
                              json={"model": "gpt-4o",
                                    "messages": [{"role": "user",
                                                  "content": "hi"}]})
            if r2.status_code == 200 or time.monotonic() > deadline:
                break
            await asyncio.sleep(0.05)
        assert r2 is not None and r2.status_code == 200, (
            "the usage-less stream kept its rate-limit slot: "
            f"{None if r2 is None else r2.status_code} "
            f"{None if r2 is None else r2.text}")


# ---------------------------------------------------------------------------
# S6 — /metrics accepts an admin session cookie
# ---------------------------------------------------------------------------

async def test_metrics_accepts_an_admin_session_cookie():
    cfg = _config(router=RouterSettings(prometheus_enabled=True,
                                        prometheus_path="/metrics"))
    app = create_app(cfg)
    async with LifespanManager(app), _client(app) as c:
        anon = await c.get("/metrics")
        assert anon.status_code == 401
        login = await c.post("/auth/login", json={"master_key": MASTER})
        assert login.status_code == 200, login.text
        assert c.cookies.get("wiwi_session"), "no session cookie was issued"
        # Cookie only: no bearer header anywhere on this request.
        r = await c.get("/metrics")
        assert r.status_code == 200, r.text
        assert "text/html" not in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# S7 — the last-admin guard is one critical section
# ---------------------------------------------------------------------------

async def test_concurrent_demotions_cannot_leave_zero_admins():
    app = create_app(_config())
    async with LifespanManager(app), _client(app) as c:
        users = app.state.wiwi.users
        u1 = await users.create_user("adm-a", "password1")
        u2 = await users.create_user("adm-b", "password1")
        await users.patch(u1.id, role="admin")
        await users.patch(u2.id, role="admin")
        assert await users.count_admins() == 2

        r1, r2 = await asyncio.gather(
            c.patch(f"/admin/users/{u1.id}", json={"role": "user"}, headers=AUTH),
            c.patch(f"/admin/users/{u2.id}", json={"role": "user"}, headers=AUTH))
        assert sorted([r1.status_code, r2.status_code]) == [200, 400], (
            f"both demotions were admitted: {r1.status_code} {r2.status_code}")
        assert await users.count_admins() == 1


# ---------------------------------------------------------------------------
# S8 — deleting a Cline default model leaves other providers alone
# ---------------------------------------------------------------------------

async def test_deleting_a_cline_default_model_keeps_other_providers():
    app = create_app(_config())
    async with LifespanManager(app), _client(app) as c:
        r = await c.post("/admin/providers", headers=AUTH, json={
            "name": "c1", "provider_type": "cline",
            "base_url": "https://cline.example.com", "label": "a", "key": "k"})
        assert r.status_code == 200, r.text
        r = await c.post("/admin/providers", headers=AUTH, json={
            "name": "o1", "provider_type": "openai",
            "base_url": "https://api.openai.com/v1", "label": "a", "key": "k"})
        assert r.status_code == 200, r.text
        for prov in ("c1", "o1"):
            r = await c.post("/admin/model-groups/cline:foo/deployments",
                             headers=AUTH,
                             json={"group": "cline:foo", "provider": prov,
                                   "model_id": "foo"})
            assert r.status_code == 201, r.text

        r = await c.delete("/admin/cline/settings/default-models/foo", headers=AUTH)
        assert r.status_code == 200, r.text
        assert r.json()["removed_deployments"] == 1

        models = (await c.get("/admin/models", headers=AUTH)).json()
        by_name = {g["name"]: g["deployments"] for g in models["groups"]}
        assert [d["provider"] for d in by_name.get("cline:foo", [])] == ["o1"], (
            "the Cline delete removed a non-Cline deployment from the group")


# ---------------------------------------------------------------------------
# W1 — context_window_exceeded is OpenAI's "length"
# ---------------------------------------------------------------------------

def test_context_window_exceeded_maps_to_length():
    assert tr.ir_to_openai_finish("context_window_exceeded") == "length"
    # The reasons that genuinely have no OpenAI spelling still fall back.
    assert tr.ir_to_openai_finish("pause_turn") == "stop"
    assert tr.ir_to_openai_finish("compaction") == "stop"


# ---------------------------------------------------------------------------
# W2 — the legacy functions / function_call protocol
# ---------------------------------------------------------------------------

def test_legacy_functions_list_is_decoded_as_tools():
    req = oc.decode_request({
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
        "functions": [{"name": "f", "description": "d",
                       "parameters": {"type": "object"}}]})
    assert [t.name for t in req.tools] == ["f"]
    assert req.tools[0].description == "d"


def test_typeless_tools_entry_is_decoded_as_a_function():
    req = oc.decode_request({
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "g", "parameters": {"type": "object"}}]})
    assert [t.name for t in req.tools] == ["g"]


def test_legacy_function_call_replay_pairs_the_result():
    req = oc.decode_request({
        "model": "m",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None,
             "function_call": {"name": "f", "arguments": '{"a": 1}'}},
            {"role": "function", "name": "f", "content": "42"},
        ]})
    call = req.messages[1]
    assert isinstance(call.parts[0], ir.ToolUsePart)
    assert call.parts[0].name == "f" and call.parts[0].args == {"a": 1}
    result = req.messages[2]
    assert result.role == "tool"
    assert isinstance(result.parts[0], ir.ToolResultPart)
    assert result.parts[0].tool_use_id == call.parts[0].id
    assert result.parts[0].content == "42"


def test_legacy_function_call_sets_the_tool_choice():
    req = oc.decode_request({
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
        "functions": [{"name": "f"}],
        "function_call": {"name": "f"}})
    assert isinstance(req.tool_choice, ir.ToolChoiceNamed)
    assert req.tool_choice.name == "f"
    auto = oc.decode_request({
        "model": "m", "messages": [{"role": "user", "content": "hi"}],
        "function_call": "auto"})
    assert isinstance(auto.tool_choice, ir.ToolChoiceAuto)


# ---------------------------------------------------------------------------
# W3 — /v1/completions accepts the explicit defaults
# ---------------------------------------------------------------------------

def test_completions_accepts_false_logprobs_and_echo():
    req = ocomp.decode_request({"model": "m", "prompt": "x",
                                "logprobs": False, "echo": False})
    assert req.messages[0].parts[0].text == "x"


def test_completions_still_rejects_a_real_logprobs_request():
    for key in ("logprobs", "echo"):
        with pytest.raises(ocomp.DialectError):
            ocomp.decode_request({"model": "m", "prompt": "x", key: True})


def test_completions_rejects_a_non_integer_best_of():
    # ``"2"`` used to slip past ``coerce_int`` and land in ``extras``, where it
    # was forwarded upstream as a real best_of request.
    with pytest.raises(ocomp.DialectError):
        ocomp.decode_request({"model": "m", "prompt": "x", "best_of": "2"})
    with pytest.raises(ocomp.DialectError):
        ocomp.decode_request({"model": "m", "prompt": "x", "best_of": 2})
    assert ocomp.decode_request({"model": "m", "prompt": "x",
                                 "best_of": 1}) is not None


# ---------------------------------------------------------------------------
# W4 — nothing content-bearing after the terminal error frame
# ---------------------------------------------------------------------------

def test_anthropic_stream_error_drops_buffered_content():
    enc = am.AnthropicStreamEncoder("claude-x", "req1")
    out = b""
    out += enc.feed(dl.StreamStart(model="claude-x")) or b""
    out += enc.feed(dl.ToolCallOpen(index=0, id="t1", name="tool")) or b""
    # Text written around a tool call is buffered and normally flushed later.
    out += enc.feed(dl.TextDelta("late text")) or b""
    out += enc.feed(dl.StreamError(message="upstream died",
                                   kind="connection")) or b""
    tail = out + enc.final_frame()

    text = tail.decode()
    error_at = text.index("event: error")
    after = text[error_at:]
    assert "content_block_start" not in after, (
        f"a content block was emitted after the terminal error:\n{after}")
    assert "content_block_delta" not in after
    assert "late text" not in after
    # The stream is still terminated well-formed.
    assert "message_delta" in after


# ---------------------------------------------------------------------------
# W5/W6 — one hosted id per item; args accumulate linearly
# ---------------------------------------------------------------------------

def _responses_frames(raw: bytes) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ")]


def test_hosted_tool_search_uses_one_id_in_added_and_done():
    enc = oresp.ResponsesStreamEncoder("gpt-5", "req1")
    out = b""
    out += enc.feed(dl.StreamStart(model="gpt-5")) or b""
    out += enc.feed(dl.ToolCallOpen(index=0, id="", name="tool_search",
                                   builtin="tool_search_bm25")) or b""
    out += enc.feed(dl.ToolCallArgsDelta(index=0,
                                        args_fragment='{"query":"rust"}')) or b""
    out += enc.feed(dl.ToolCallClose(index=0)) or b""
    frames = _responses_frames(out)
    added = next(f for f in frames if f["type"] == "response.output_item.added")
    done = next(f for f in frames if f["type"] == "response.output_item.done")
    assert added["item"]["id"] == done["item"]["id"] == "ts_req1_0"
    assert added["item"]["type"] == done["item"]["type"] == "tool_search_call"


def test_responses_tool_args_accumulate_in_order():
    enc = oresp.ResponsesStreamEncoder("gpt-5", "req1")
    fragments = ['{"a"', ': 1, "b"', ': [1, 2, 3]}']
    out = b""
    out += enc.feed(dl.StreamStart(model="gpt-5")) or b""
    out += enc.feed(dl.ToolCallOpen(index=0, id="call_1", name="f")) or b""
    for frag in fragments:
        out += enc.feed(dl.ToolCallArgsDelta(index=0,
                                            args_fragment=frag)) or b""
    # The fragments are buffered as a LIST and joined once at close. String
    # ``+=`` per fragment made a large call quadratic on the event loop (256 KB
    # of args in 8-byte fragments is gigabytes of memcpy); the gateway pump and
    # the Anthropic encoder already accumulate this way.
    assert enc._tools[0]["args"] == fragments
    out += enc.feed(dl.ToolCallClose(index=0)) or b""
    frames = _responses_frames(out)
    done = next(f for f in frames if f["type"] == "response.output_item.done")
    assert json.loads(done["item"]["arguments"]) == {"a": 1, "b": [1, 2, 3]}
    argdone = next(f for f in frames
                   if f["type"] == "response.function_call_arguments.done")
    assert argdone["arguments"] == "".join(fragments)


# ---------------------------------------------------------------------------
# W7/W8 — Chat content-block decode asymmetries
# ---------------------------------------------------------------------------

def test_chat_file_id_decodes_to_a_document():
    req = oc.decode_request({
        "model": "m",
        "messages": [{"role": "user", "content": [
            {"type": "file", "file": {"file_id": "file-abc"}}]}]})
    part = req.messages[0].parts[0]
    assert isinstance(part, ir.DocumentPart)
    assert part.file_id == "file-abc"


def test_chat_input_audio_requires_a_string_payload():
    req = oc.decode_request({
        "model": "m",
        "messages": [{"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"data": 5, "format": {"a": 1}}},
            {"type": "text", "text": "after"}]}]})
    kinds = [type(p).__name__ for p in req.messages[0].parts]
    assert "AudioPart" not in kinds
    assert kinds == ["TextPart"]


# ---------------------------------------------------------------------------
# W9 — a malformed input reaches the decoder's own rejection
# ---------------------------------------------------------------------------

def test_with_history_leaves_a_malformed_input_to_the_decoder():
    merged = oresp.with_history(
        {"model": "m", "previous_response_id": "resp_1", "input": {"q": 1}},
        previous=[{"type": "message", "role": "assistant",
                   "content": "earlier"}])
    assert merged["input"] == {"q": 1}
    with pytest.raises(oresp.DialectError):
        oresp.decode_request(merged)


# ---------------------------------------------------------------------------
# T1 — a zero-frame journal is complete
# ---------------------------------------------------------------------------

async def test_is_complete_sees_a_zero_frame_done_record(tmp_path):
    store = JournalStore(tmp_path, 600.0, 1 << 20)
    assert store.is_complete("req-zero") is False
    journal = await store.open("req-zero")
    await journal.finish(0)  # zero frames emitted: the done record is seq 0
    assert store.is_complete("req-zero") is True
    assert await store.is_complete_async("req-zero") is True
