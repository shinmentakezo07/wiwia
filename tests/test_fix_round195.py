"""Round 195 — streamed Responses turns stored a rebuilt turn, not the
streamed one.

Two fidelity defects in the streamed→stored path, both of the #480 shape:

1. **Interleaved items collapsed.** ``_streamed_response_payload`` rebuilds the
   stored object from three flat accumulators (all thinking, one text blob, all
   tools), so text emitted *before and after* a tool call became a single
   message item and the stored ``output`` array differed from the streamed
   terminal object the client consumed. A client that keeps the streamed
   object and a client that later ``GET``s the stored copy held two different
   turns for the same response id.

2. **The encrypted reasoning blob was dropped.** ``_capture_delta`` appended
   only ``d.text`` to ``thinking_buf``; the blob on ``ThinkingDelta`` never
   reached the accumulator, so streamed reasoning items were stored without
   ``encrypted_content``. This is AUDIT #355's encoder fix landing in a sibling
   copy that was never patched (round 162 fixed the encoder; the stored rebuild
   here silently re-broke it for streamed turns).

The fix captures the IR deltas themselves (the only ordering-faithful
accumulator) and rebuilds the stored object from them, with the blob intact.
"""

from __future__ import annotations

import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_responses as orp

_AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}
_ANTH_HEADERS = {"anthropic-version": "2023-06-01"}

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
                            base_url="http://test", headers=dict(_AUTH))


def _stream_upstream(*events: str):
    return Response(200, headers={"content-type": "text/event-stream"},
                    content="\n\n".join(events) + "\n\n")


def test_stored_streamed_payload_matches_live_terminal_object():
    """The stored `output` array is the terminal object the client saw.

    Interleaved text-before/text-after a tool call must stay two message items
    in the stored copy, and the stored ids must be the ids the stream minted
    (``msg_<req>_1``, not ``msg_<req>``).
    """
    deltas = [
        dl.StreamStart(model="m", group="g"),
        dl.TextDelta("before"),
        dl.ToolCallOpen(index=0, name="f", id="c1"),
        dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
        dl.ToolCallClose(index=0),
        dl.TextDelta("after"),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ]
    enc = orp.ResponsesStreamEncoder(model="m", req_id="r195")
    for d in deltas:
        enc.feed(d)
    blob = enc._completed().decode()
    live = next(
        __import__("orjson").loads(line[6:])
        for line in blob.splitlines() if line.startswith("data: ")
        and '"response.completed"' in line
    )["response"]

    stored = orp.ResponsesStreamEncoder.rebuild_stored_payload(
        "m", "r195", deltas)
    assert [i["id"] for i in stored["output"]] == [
        i["id"] for i in live["output"]], stored["output"]
    assert [(i["type"], i.get("content", [{}])[0].get("text") if i["type"] == "message" else None)
            for i in stored["output"]] == [
        (i["type"], i.get("content", [{}])[0].get("text") if i["type"] == "message" else None)
        for i in live["output"]]
    assert [i["type"] for i in stored["output"]] == ["message", "function_call", "message"]


def test_rebuilt_stored_payload_keeps_encrypted_blob():
    """AUDIT #355's blob survives the streamed→stored round trip."""
    deltas = [
        dl.StreamStart(model="m", group="g"),
        dl.ThinkingDelta(text="hmm", encrypted_content="blob1"),
        dl.TextDelta("answer"),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish("stop"),
        dl.StreamEnd(),
    ]
    stored = orp.ResponsesStreamEncoder.rebuild_stored_payload(
        "m", "r195b", deltas)
    reasoning = [i for i in stored["output"] if i["type"] == "reasoning"]
    assert reasoning and reasoning[0]["encrypted_content"] == "blob1"


def test_rebuilt_stored_payload_handles_tool_only_turn():
    """A tool-only turn gets no synthetic empty message item."""
    deltas = [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, name="f", id="c1"),
        dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
        dl.ToolCallClose(index=0),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish("tool_calls"),
        dl.StreamEnd(),
    ]
    stored = orp.ResponsesStreamEncoder.rebuild_stored_payload(
        "m", "r195c", deltas)
    assert [i["type"] for i in stored["output"]] == ["function_call"]


def test_rebuilt_stored_payload_handles_builtin_search_query():
    """Builtin search items keep their extracted query (round 158 / #480)."""
    stored = orp.ResponsesStreamEncoder.rebuild_stored_payload(
        "m", "r195d",
        [dl.StreamStart(model="m", group="g"),
         dl.ToolCallOpen(index=0, name="web_search", id="srv1", builtin="web_search"),
         dl.ToolCallArgsDelta(index=0, args_fragment='{"query":"wiwi proxy"}'),
         dl.ToolCallClose(index=0),
         dl.UsageFinal(prompt=1, output=1), dl.Finish("stop"), dl.StreamEnd()],
    )
    search = [i for i in stored["output"] if i["type"] == "web_search_call"]
    assert search and search[0]["action"]["query"] == "wiwi proxy"


@respx.mock
async def test_streamed_turn_stored_equals_streamed_object_end_to_end():
    """Round 195 end-to-end: stream a turn, then GET it — same items."""
    # Text before AND after a tool call, so the stranded interleave is visible.
    respx.post(_ANTH_UP).mock(return_value=_stream_upstream(
        'event: message_start\ndata: {"type":"message_start","message":'
        '{"id":"m1","model":"claude-x","usage":{"input_tokens":5,"output_tokens":0}}}\n\n',
        'event: content_block_start\ndata: {"type":"content_block_start",'
        '"index":0,"content_block":{"type":"text","text":""}}\n\n',
        'event: content_block_delta\ndata: {"type":"content_block_delta",'
        '"index":0,"delta":{"type":"text_delta","text":"before"}}\n\n',
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
        'event: content_block_start\ndata: {"type":"content_block_start",'
        '"index":1,"content_block":{"type":"tool_use","id":"toolu_1",'
        '"name":"get_weather","input":{}}}\n\n',
        'event: content_block_delta\ndata: {"type":"content_block_delta",'
        '"index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"city\\": \\"SF\\"}"}}\n\n',
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
        'event: content_block_start\ndata: {"type":"content_block_start",'
        '"index":2,"content_block":{"type":"text","text":""}}\n\n',
        'event: content_block_delta\ndata: {"type":"content_block_delta",'
        '"index":2,"delta":{"type":"text_delta","text":"after"}}\n\n',
        'event: content_block_stop\ndata: {"type":"content_block_stop","index":2}\n\n',
        'event: message_delta\ndata: {"type":"message_delta",'
        '"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n\n',
        'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ))
    mgr, client = await _client(_ANTHROPIC_CFG)
    try:
        async with client.stream("POST", "/v1/responses", json={
                "model": "claude-x", "input": "hi", "stream": True,
                "tools": [{"type": "function", "name": "get_weather",
                           "parameters": {"type": "object", "properties": {}}}]}) as r:
            raw = "".join([c async for c in r.aiter_text()])
        assert r.status_code == 200, raw
        import orjson
        resp_id = ""
        live_output = None
        for line in raw.split("\n"):
            if line.startswith("data: ") and '"response.completed"' in line:
                obj = orjson.loads(line[6:])
                resp_id = obj["response"]["id"]
                live_output = obj["response"]["output"]
                break
        assert resp_id, f"no terminal frame: {raw[:400]}"
        assert live_output, "no output items in live stream"
        stored = await client.get(f"/v1/responses/{resp_id}")
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert stored.status_code == 200, stored.text
    stored_output = stored.json()["output"]
    # The whole point of round 195: stored copy == streamed object.
    assert [i["id"] for i in stored_output] == [i["id"] for i in live_output], (
        f"\nstreamed: {[i['id'] for i in live_output]}\n"
        f"stored:   {[i['id'] for i in stored_output]}")
    assert [i["type"] for i in stored_output] == [
        i["type"] for i in live_output]
    assert stored_output == live_output, (
        f"\nstored: {stored_output}\nlive:   {live_output}")
