"""Round 196 — hosted-call replay decodes, not drops; streamed store failures
are loud.

Three defects on the ``previous_response_id`` continuation path:

1. **#575 — a provider-executed call was unreplayable.**
   Stored Responses history carries ``web_search_call`` / ``tool_search_call``
   items (round 195 stores the streamed turn verbatim). The request decoder
   skipped both with a bare ``responses_unsupported_input_item`` warning, so
   the next turn's upstream saw the follow-up and the assistant's text but no
   record that a search ran. Decoding the item alone is not enough, though:
   the Anthropic adapter's request path re-emitted a hosted ``ToolUsePart``
   verbatim, with no pairing check, which would have put an unpaired
   ``server_tool_use`` in front of a provider that rejects it outright (a
   400). It now applies the pairing gate the wire encoders already used:
   paired history replays verbatim, an unpaired call is dropped with a
   caller-visible warning, and an unpaired result's payload rides as text.
   The Responses dialect carries no result item at all, so a Responses
   continuation replayed to Anthropic ships **no hosted block**.

2. **#574 — a streamed store-persistence failure was swallowed.**
   ``_teardown_tail`` wrapped ``response_store.put`` in
   ``contextlib.suppress(Exception)``; a DB outage silently lost every
   streamed continuation id with no signal in the proxy log. It now logs
   ``response_store_put_on_stream_failed`` plus a ``log_proxy("error", …)``
   entry, matching the cache-hit and singleflight twins.

3. **#573 — a ``tool_call`` stop with no tool item.** Recorded as **declined,
   not fixed** (AUDIT #272/#358). No wire value was invented and no test
   pins a behaviour that was deliberately left alone; this file only covers
   the two fixes above.
"""

from __future__ import annotations

import orjson
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.server.app import create_app
from wiwi.wire import openai_responses as orp

_AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

_ANTHROPIC_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: anthropic
    provider: anthropic
    base_url: https://api.anthropic.com/v1
    keys: [{label: default, key: sk-upstream}]
model_list:
  - model_name: claude-x
    wiwi_params: {provider: anthropic, model: claude-x}
"""

_ANTH_UP = "https://api.anthropic.com/v1/messages"


async def _client(cfg: str):
    app = create_app(load_config_from_string(cfg))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    return mgr, AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test",
                            headers=dict(_AUTH))


def _anth_search_turn(query: str, *, result: bool = True,
                      srv_id: str = "srvtoolu_1") -> dict:
    content: list[dict] = [
        {"type": "text", "text": "Let me search."},
        {"type": "server_tool_use", "id": srv_id,
         "name": "web_search", "input": {"query": query}},
    ]
    if result:
        content.append({
            "type": "web_search_tool_result", "tool_use_id": srv_id,
            "content": [{"type": "web_search_result",
                         "url": "https://e.com", "title": "E"}]})
    return {"id": "msg_x", "type": "message", "role": "assistant",
            "model": "claude-x", "content": content,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5}}


# ---------------------------------------------------------------------------
# #575 — decode arm
# ---------------------------------------------------------------------------

def test_web_search_call_decodes_as_hosted_call():
    req = orp.decode_request({
        "model": "gpt-x",
        "input": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed",
             "action": {"type": "search", "query": "wiwi proxy"}},
        ]})
    parts = [p for m in req.messages for p in m.parts]
    hosted = [p for p in parts if isinstance(p, ir.ToolUsePart)]
    assert [p.id for p in hosted] == ["ws_1"]
    assert hosted[0].builtin == "web_search"
    assert hosted[0].block_type == "server_tool_use"
    assert hosted[0].args == {"query": "wiwi proxy"}


def test_tool_search_call_decodes_as_bm25_owner():
    req = orp.decode_request({
        "model": "gpt-x",
        "input": [
            {"type": "tool_search_call", "id": "ts_1", "call_id": "call_1",
             "status": "completed", "execution": "server",
             "arguments": {"query": "weather api"}},
        ]})
    hosted = [p for m in req.messages for p in m.parts
              if isinstance(p, ir.ToolUsePart)]
    assert [p.id for p in hosted] == ["call_1"], (
        "call_id is preferred over the item id for pairing")
    assert hosted[0].builtin == "tool_search_bm25"


def test_id_less_hosted_call_is_dropped_with_warning(capsys):
    req = orp.decode_request({
        "model": "gpt-x",
        "input": [
            {"type": "web_search_call", "status": "completed",
             "action": {"type": "search", "query": "wiwi proxy"}},
        ]})
    assert not [p for m in req.messages for p in m.parts
                if isinstance(p, ir.ToolUsePart)]
    assert "responses_unsupported_input_item" in capsys.readouterr().out


def test_hosted_call_round_trips_to_web_search_call_on_responses_encode():
    # The Responses encoder re-emits the hosted item from an AssistantTurn;
    # build one the way the gateway does from ToolCallOpen.
    from wiwi.streaming import deltas as dl
    deltas = [dl.StreamStart(model="gpt-x", group="g"),
              dl.ToolCallOpen(index=0, id="ws_1", name="web_search",
                              builtin="web_search",
                              block_type="server_tool_use"),
              dl.ToolCallArgsDelta(index=0,
                                   args_fragment='{"query": "wiwi proxy"}'),
              dl.ToolCallClose(index=0),
              dl.UsageFinal(prompt=1, output=1),
              dl.Finish("tool_calls"),
              dl.StreamEnd()]
    enc = orp.ResponsesStreamEncoder("gpt-x", "r196")
    blob = b"".join(
        frame for frame in (enc.feed(d) for d in deltas) if frame).decode()
    blob += enc._completed().decode()
    assert "web_search_call" in blob
    assert "wiwi proxy" in blob


def test_hosted_tool_search_round_trips_as_tool_search_call():
    from wiwi.streaming import deltas as dl
    deltas = [dl.StreamStart(model="gpt-x", group="g"),
              dl.ToolCallOpen(index=0, id="ts_1", name="tool_search_bm25",
                              builtin="tool_search_bm25",
                              block_type="server_tool_use"),
              dl.ToolCallArgsDelta(index=0,
                                   args_fragment='{"query": "weather"}'),
              dl.ToolCallClose(index=0),
              dl.UsageFinal(prompt=1, output=1),
              dl.Finish("tool_calls"),
              dl.StreamEnd()]
    enc = orp.ResponsesStreamEncoder("gpt-x", "r196t")
    blob = b"".join(
        frame for frame in (enc.feed(d) for d in deltas) if frame).decode()
    blob += enc._completed().decode()
    assert "tool_search_call" in blob


# ---------------------------------------------------------------------------
# #575 — Anthropic request-side pairing gate
# ---------------------------------------------------------------------------

def _messages_req(messages: list[ir.Message]) -> ir.Request:
    return ir.Request(model="claude-x", messages=messages, tools=[])


def _hosted_assistant(query: str = "wiwi proxy", *, call_id: str = "srvtoolu_1",
                      with_result: bool = True) -> ir.Message:
    parts: list[ir.Part] = [
        ir.TextPart("Let me search."),
        ir.ToolUsePart(id=call_id, name="web_search",
                       args={"query": query},
                       builtin="web_search",
                       block_type="server_tool_use"),
    ]
    if with_result:
        parts.append(ir.ToolResultPart(
            tool_use_id=call_id,
            content='[{"type":"web_search_result"}]',
            block_type="web_search_tool_result",
            extra_blocks=[{"type": "web_search_result",
                           "url": "https://e.com", "title": "E"}]))
    return ir.Message(role="assistant", parts=parts)


def test_anthropic_paired_hosted_call_replays_verbatim():
    body = AnthropicAdapter().encode_request(
        _messages_req([
            ir.Message(role="user", parts=[ir.TextPart("search this")]),
            _hosted_assistant(),
            ir.Message(role="user", parts=[ir.TextPart("summarize")]),
        ]), "claude-x", {})
    content = body["messages"][1]["content"]
    types = [b["type"] for b in content]
    assert types == ["text", "server_tool_use", "web_search_tool_result"]
    assert content[1]["id"] == "srvtoolu_1"
    assert content[2]["tool_use_id"] == "srvtoolu_1"


def test_anthropic_unpaired_hosted_call_is_dropped():
    adapter = AnthropicAdapter()
    body = adapter.encode_request(
        _messages_req([
            ir.Message(role="user", parts=[ir.TextPart("search this")]),
            _hosted_assistant(with_result=False),
            ir.Message(role="user", parts=[ir.TextPart("follow up")]),
        ]), "claude-x", {})
    content = body["messages"][1]["content"]
    types = [b["type"] for b in content]
    assert "server_tool_use" not in types, types
    assert types == ["text"], types
    assert any("unpaired provider-hosted call" in w
               for w in adapter.translation_warnings)


def test_anthropic_unpaired_hosted_result_folds_to_text():
    adapter = AnthropicAdapter()
    # Decoded Responses history: a hosted call with NO paired result, plus a
    # result whose call never made it into this history. Neither may reach
    # Anthropic as a block.
    body = adapter.encode_request(
        _messages_req([
            ir.Message(role="user", parts=[ir.TextPart("search this")]),
            ir.Message(role="assistant", parts=[
                ir.TextPart("Searching."),
                ir.ToolUsePart(id="ws_1", name="web_search",
                               args={"query": "wiwi proxy"},
                               builtin="web_search",
                               block_type="server_tool_use")]),
            ir.Message(role="tool", parts=[
                ir.ToolResultPart(tool_use_id="ws_1",
                                  content="search hits here",
                                  block_type="web_search_tool_result")]),
            ir.Message(role="user", parts=[ir.TextPart("follow up")]),
        ]), "claude-x", {})
    flat = orjson.dumps(body).decode()
    assert "server_tool_use" not in flat
    assert "web_search_tool_result" not in flat
    assert "ws_1" not in flat, (
        "the hosted call id must not survive into the replayed history")
    assert "search hits here" in flat, (
        "the result payload must ride as text, not vanish")
    assert any("unpaired provider-hosted tool result" in w
               for w in adapter.translation_warnings)


def test_anthropic_plain_tool_pair_is_unaffected():
    body = AnthropicAdapter().encode_request(
        _messages_req([
            ir.Message(role="user", parts=[ir.TextPart("run it")]),
            ir.Message(role="assistant", parts=[
                ir.ToolUsePart(id="toolu_9", name="f", args={"x": 1})]),
            ir.Message(role="user", parts=[
                ir.ToolResultPart(tool_use_id="toolu_9", content="42")]),
        ]), "claude-x", {})
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"


# ---------------------------------------------------------------------------
# Stored-continuation round trip (decode → persist → replay)
# ---------------------------------------------------------------------------

@respx.mock
async def test_stored_hosted_search_replays_without_unpaired_hosted_block():
    """A stored Responses turn replays into the next turn; it must not put an
    unpaired server_tool_use in front of Anthropic.

    The Anthropic upstream that answered turn 1 returns a paired search trace,
    but the Responses surface has no item for the hosted *result* — the stored
    turn carries the call only. On continuation the history therefore holds an
    unpaired call; replayed to Anthropic it is dropped with a caller-visible
    warning instead of being sent (a 400).
    """
    respx.post(_ANTH_UP).mock(return_value=Response(200, content=orjson.dumps(
        _anth_search_turn("wiwi proxy"))))
    mgr, client = await _client(_ANTHROPIC_CFG)
    try:
        r1 = await client.post("/v1/responses", json={
            "model": "claude-x", "input": "search the web",
            "store": True,
            "tools": [{"type": "web_search"}]})
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        stored_id = body1["id"]
        assert any(i.get("type") == "web_search_call"
                   for i in body1["output"]), body1["output"]

        respx.post(_ANTH_UP).mock(return_value=Response(200, content=orjson.dumps(
            {"id": "msg_y", "type": "message", "role": "assistant",
             "model": "claude-x",
             "content": [{"type": "text", "text": "Summary."}],
             "stop_reason": "end_turn",
             "usage": {"input_tokens": 20, "output_tokens": 3}})))
        r2 = await client.post("/v1/responses", json={
            "model": "claude-x", "previous_response_id": stored_id,
            "input": "summarize what you found"})
        assert r2.status_code == 200, r2.text
        sent = orjson.loads(
            respx.calls[-1].request.content) if hasattr(respx, "calls") else None
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert sent is not None
    # The upstream request that carried the continuation: the stored turn
    # holds an unpaired hosted call (Responses has no result item), so the
    # replay carries no hosted block at all — never a lone server_tool_use.
    assistants = [m for m in sent["messages"] if m["role"] == "assistant"]
    assert assistants, sent
    flat_blocks = [b for m in assistants for b in m.get("content", [])
                   if isinstance(b, dict)]
    types = [b["type"] for b in flat_blocks]
    assert "server_tool_use" not in types, types
    assert "web_search_tool_result" not in types, types
    assert any(b.get("type") == "text" for b in flat_blocks), types

# ---------------------------------------------------------------------------
# #574 — streamed store-persistence failure is loud
# ---------------------------------------------------------------------------

@respx.mock
async def test_streamed_store_failure_is_logged_not_swallowed(monkeypatch,
                                                                capsys):
    """A DB failure during the streamed store write must surface in the proxy
    log ring and as a warning, and the stream must still complete."""
    from wiwi.server import app as server_app

    class _BrokenStore:
        async def startup(self): ...
        def start(self): ...
        async def stop(self): ...
        async def put(self, *a, **kw):
            raise OSError("disk full")
    monkeypatch.setattr(server_app, "ResponseStore", lambda *a, **kw: _BrokenStore())

    events = [
        ('event: message_start\ndata: {"type":"message_start","message":'
         '{"id":"m1","model":"claude-x","usage":'
         '{"input_tokens":5,"output_tokens":0}}}\n\n'),
        ('event: content_block_start\ndata: {"type":"content_block_start",'
         '"index":0,"content_block":{"type":"text","text":""}}\n\n'),
        ('event: content_block_delta\ndata: {"type":"content_block_delta",'
         '"index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'),
        ('event: content_block_stop\ndata: {"type":"content_block_stop",'
         '"index":0}\n\n'),
        ('event: message_delta\ndata: {"type":"message_delta",'
         '"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'),
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]
    respx.post(_ANTH_UP).mock(return_value=Response(
        200, headers={"content-type": "text/event-stream"},
        content="".join(events)))
    mgr, client = await _client(_ANTHROPIC_CFG)
    try:
        async with client.stream("POST", "/v1/responses", json={
                "model": "claude-x", "input": "hi",
                "stream": True,
                "store": True}) as r:
            raw = "".join([c async for c in r.aiter_text()])
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 200, raw
    assert "hi" in raw
    joined = capsys.readouterr().out
    assert "response_store_put_on_stream_failed" in joined, joined
    assert "disk full" in joined, joined
