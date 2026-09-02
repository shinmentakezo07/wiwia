"""Round-24 regression: streaming/state + decode-robustness bugs in the
OpenAI <-> Anthropic translation layer.

Regression targets (each test pins one verified bug from the 2026-09-02
audit of the translation layer):
  C1 streaming/state:
    - ChatStreamEncoder emitted a usage chunk even when the client did not
      send stream_options.include_usage (openai_chat.py final_frame).
    - ChatStreamEncoder put usage in a chunk WITH choices; OpenAI only ever
      sends usage in a final chunk with an EMPTY choices array.
    - AnthropicStreamEncoder dropped a pending thinking signature at stream
      end, or flushed it into the WRONG (later) thinking block.
    - ResponsesStreamEncoder's response.completed omitted the output array
      (Codex CLI requires it).
    - Responses truncation never surfaced: terminal event must be
      response.incomplete with status/incomplete_details.
    - Responses reasoning events used response.reasoning_text.* instead of
      response.reasoning_summary_text.*; message close missed
      output_text.done / content_part.done.
    - stop_sequence was hardcoded to None on both Anthropic encode paths and
      never read by the adapter.
    - Inbound Anthropic history with server_tool_use blocks vanished (the
      message decoded to zero parts and was dropped wholesale).
    - Empty text blocks were sent to Anthropic, which 400s on them.
  C2 decode robustness:
    - Non-dict content items crashed decode with TypeError (500) instead of
      being skipped.
    - thinking as a string crashed Anthropic decode with AttributeError.
    - max_tokens=0 fell through the falsy-or chain to max_completion_tokens.
    - The legacy "function_call" finish reason mapped to "stop" instead of
      "tool_call"; "developer" role never unified to "system"; refusals and
      compaction stop_reason were dropped.
"""

from __future__ import annotations

import json

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


def _frames(encoded: bytes) -> list[dict]:
    """Parse SSE bytes into (event, payload) dicts."""
    out = []
    for chunk in encoded.decode().split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        evt, data = None, None
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                evt = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if data:
            out.append({"event": evt, "data": json.loads(data)})
    return out


def _run_stream(encoder, deltas) -> bytes:
    out = b""
    for d in deltas:
        frame = encoder.feed(d)
        if frame:
            out += frame
    out += encoder.final_frame()
    return out


# ---------------------------------------------------------------------------
# C1.4: usage chunk gating + empty-choices shape
# ---------------------------------------------------------------------------

def test_chat_stream_omits_usage_without_include_usage():
    enc = oc.ChatStreamEncoder("gpt-4o", "r1")
    deltas = [dl.StreamStart(model="gpt-4o"), dl.TextDelta("hi"),
              dl.UsageFinal(prompt=5, output=2), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_stream(enc, deltas))
    assert not any("usage" in f["data"] for f in frames), \
        "usage must not be emitted when include_usage is false"


def test_chat_stream_usage_in_empty_choices_chunk_when_requested():
    enc = oc.ChatStreamEncoder("gpt-4o", "r1", include_usage=True)
    deltas = [dl.StreamStart(model="gpt-4o"), dl.TextDelta("hi"),
              dl.UsageFinal(prompt=5, output=2), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_stream(enc, deltas))
    usage_frames = [f for f in frames if "usage" in f["data"]]
    assert len(usage_frames) == 1
    assert usage_frames[0]["data"]["choices"] == [], \
        "OpenAI sends usage in a chunk with an EMPTY choices array"
    assert usage_frames[0]["data"]["usage"]["prompt_tokens"] == 5


def test_chat_stream_finish_chunk_has_no_usage_when_requested():
    """The finish_reason chunk itself must NOT carry usage when
    include_usage is on; usage rides only in the empty-choices chunk."""
    enc = oc.ChatStreamEncoder("gpt-4o", "r1", include_usage=True)
    deltas = [dl.StreamStart(model="gpt-4o"), dl.TextDelta("hi"),
              dl.UsageFinal(prompt=5, output=2), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_stream(enc, deltas))
    finish_frames = [f for f in frames
                     if f["data"].get("choices")
                     and f["data"]["choices"][0].get("finish_reason")]
    assert finish_frames, "a finish chunk must still be emitted"
    assert all("usage" not in f["data"] for f in finish_frames)


# ---------------------------------------------------------------------------
# C1.2: pending thinking signature at stream end / wrong block
# ---------------------------------------------------------------------------

def test_anthropic_pending_sig_flushed_at_stream_end():
    """A signature arriving after the thinking block closed must not be
    dropped: it is emitted as a late signature_delta against the last
    thinking block before message_delta."""
    enc = am.AnthropicStreamEncoder("claude", "r1")
    deltas = [dl.StreamStart(model="claude"),
              dl.ThinkingDelta("thinking hard"),
              dl.TextDelta("answer"),  # closes the thinking block
              dl.ThinkingDelta("", signature="sig-abc"),  # late signature
              dl.UsageFinal(prompt=3, output=4), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_stream(enc, deltas))
    sigs = [f for f in frames
            if f["data"].get("type") == "content_block_delta"
            and f["data"]["delta"]["type"] == "signature_delta"]
    assert any(f["data"]["delta"]["signature"] == "sig-abc" for f in sigs), \
        "late signature must be flushed at final_frame, not dropped"


def test_anthropic_pending_sig_not_flushed_into_later_block():
    """A pending signature for block N must not be adopted by a LATER
    thinking block N+1: it must flush to the earlier block first."""
    enc = am.AnthropicStreamEncoder("claude", "r1")
    deltas = [dl.StreamStart(model="claude"),
              dl.ThinkingDelta("first"),
              dl.TextDelta("text"),  # closes block 0 (thinking)
              dl.ThinkingDelta("", signature="sig-old"),  # pending for blk 0
              dl.ThinkingDelta("second"),  # would open block 2 (thinking)
              dl.TextDelta("more"),
              dl.UsageFinal(prompt=1, output=1), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_stream(enc, deltas))
    sigs = [f["data"] for f in frames
            if f["data"].get("type") == "content_block_delta"
            and f["data"]["delta"]["type"] == "signature_delta"]
    # sig-old must attach to the FIRST thinking block (index 0), not the later one
    assert any(s["index"] == 0 and s["delta"]["signature"] == "sig-old"
               for s in sigs), sigs


# ---------------------------------------------------------------------------
# C1.3 + C1.5 + C1.6 + C2.14: Responses encoder shape
# ---------------------------------------------------------------------------

def _run_responses_stream(encoder, deltas) -> bytes:
    out = b""
    for d in deltas:
        frame = encoder.feed(d)
        if frame:
            out += frame
    out += encoder._completed()
    return out


def test_responses_completed_includes_output_array():
    enc = orp.ResponsesStreamEncoder("gpt-5", "r1")
    deltas = [dl.StreamStart(model="gpt-5"), dl.TextDelta("hello"),
              dl.ToolCallOpen(index=0, id="call_1", name="get_weather"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"city":"SF"}'),
              dl.ToolCallClose(index=0),
              dl.UsageFinal(prompt=2, output=3), dl.Finish("tool_call"),
              dl.StreamEnd()]
    frames = _frames(_run_responses_stream(enc, deltas))
    completed = [f for f in frames
                 if f["data"].get("type") == "response.completed"]
    assert completed, "response.completed must be emitted"
    output = completed[0]["data"]["response"].get("output")
    assert output, "response.completed must carry the output array"
    kinds = [o["type"] for o in output]
    assert "message" in kinds and "function_call" in kinds


def test_responses_incomplete_terminal_event_on_length():
    enc = orp.ResponsesStreamEncoder("gpt-5", "r1")
    deltas = [dl.StreamStart(model="gpt-5"), dl.TextDelta("partial"),
              dl.UsageFinal(prompt=2, output=99), dl.Finish("length"),
              dl.StreamEnd()]
    frames = _frames(_run_responses_stream(enc, deltas))
    incomplete = [f for f in frames
                  if f["data"].get("type") == "response.incomplete"]
    assert incomplete, "truncated stream must end with response.incomplete"
    resp = incomplete[0]["data"]["response"]
    assert resp["status"] == "incomplete"
    assert resp["incomplete_details"] == {"reason": "max_output_tokens"}
    assert not any(f["data"].get("type") == "response.completed" for f in frames)


def test_responses_encode_response_incomplete_status():
    turn = ir.AssistantTurn(text="partial", stop_reason="length")
    body = orp.encode_response(None, turn, "gpt-5", "r1")
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}


def test_responses_reasoning_event_names():
    enc = orp.ResponsesStreamEncoder("gpt-5", "r1")
    deltas = [dl.StreamStart(model="gpt-5"), dl.ThinkingDelta("pondering"),
              dl.TextDelta("answer"),
              dl.UsageFinal(prompt=1, output=1), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_responses_stream(enc, deltas))
    types = [f["data"]["type"] for f in frames]
    assert "response.reasoning_summary_text.delta" in types
    assert "response.reasoning_text.delta" not in types
    assert "response.reasoning_summary_text.done" in types


def test_responses_message_close_emits_text_done_and_part_done():
    enc = orp.ResponsesStreamEncoder("gpt-5", "r1")
    deltas = [dl.StreamStart(model="gpt-5"), dl.TextDelta("hi there"),
              dl.UsageFinal(prompt=1, output=1), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_responses_stream(enc, deltas))
    types = [f["data"]["type"] for f in frames]
    assert "response.output_text.done" in types
    assert "response.content_part.done" in types


def test_responses_args_without_open_dropped():
    """ArgsDelta with no preceding Open violates the IR contract; the
    encoder must drop the fragment (defensive, like the Anthropic encoder),
    not synthesize a colliding output_index."""
    enc = orp.ResponsesStreamEncoder("gpt-5", "r1")
    deltas = [dl.StreamStart(model="gpt-5"), dl.TextDelta("hi"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"x":1}'),
              dl.UsageFinal(prompt=1, output=1), dl.Finish("stop"),
              dl.StreamEnd()]
    frames = _frames(_run_responses_stream(enc, deltas))
    arg_deltas = [f for f in frames if f["data"].get("type")
                  == "response.function_call_arguments.delta"]
    assert not arg_deltas, "args without an Open must be dropped"
    # and the completed event must not contain a phantom function_call
    completed = next(f for f in frames
                     if f["data"].get("type") == "response.completed")
    kinds = [o["type"] for o in completed["data"]["response"]["output"]]
    assert "function_call" not in kinds


# ---------------------------------------------------------------------------
# C1.1: anthropic adapter reset() clears pending usage state
# ---------------------------------------------------------------------------

def test_anthropic_adapter_reset_clears_pending_usage():
    a = AnthropicAdapter()
    a.decode_stream_event("message_start", json.dumps({
        "type": "message_start",
        "message": {"model": "claude", "usage": {"input_tokens": 42,
                                                 "cache_read_input_tokens": 7,
                                                 "cache_creation_input_tokens": 3}}}))
    assert a._pending_prompt == 42
    a.reset()
    assert a._pending_prompt == 0
    assert a._pending_cached == 0
    assert a._pending_cache_creation == 0


# ---------------------------------------------------------------------------
# C1.7: stop_sequence capture + emission
# ---------------------------------------------------------------------------

def test_anthropic_adapter_reads_stop_sequence():
    a = AnthropicAdapter()
    deltas = a.decode_stream_event("message_delta", json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "stop_sequence", "stop_sequence": "\n\n"},
        "usage": {"output_tokens": 5}}))
    finish = next(d for d in deltas if isinstance(d, dl.Finish))
    assert finish.stop_reason == "stop"
    assert finish.stop_sequence == "\n\n"


def test_anthropic_encoder_emits_stop_sequence():
    enc = am.AnthropicStreamEncoder("claude", "r1")
    deltas = [dl.StreamStart(model="claude"), dl.TextDelta("hi"),
              dl.UsageFinal(prompt=1, output=1),
              dl.Finish("stop", stop_sequence="\n\n"), dl.StreamEnd()]
    frames = _frames(_run_stream(enc, deltas))
    md = next(f for f in frames if f["data"].get("type") == "message_delta")
    assert md["data"]["delta"]["stop_sequence"] == "\n\n"


def test_anthropic_encode_response_stop_sequence():
    turn = ir.AssistantTurn(text="hi", stop_reason="stop",
                            stop_sequence="\n\n")
    body = am.encode_response(None, turn, "claude", "r1")
    assert body["stop_sequence"] == "\n\n"


# ---------------------------------------------------------------------------
# C1.8: server_tool_use history decode
# ---------------------------------------------------------------------------

def test_anthropic_decode_server_tool_use_history():
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "search the web"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "plan", "signature": "sig1"},
                {"type": "server_tool_use", "id": "srvtoolu_01",
                 "name": "web_search", "input": {"query": "wiwi gateway"}},
                {"type": "text", "text": "found it"},
            ]},
            {"role": "user", "content": [
                {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_01",
                 "content": [{"type": "web_search_result",
                              "url": "https://example.com", "title": "ex"}]},
            ]},
        ],
    }
    req = am.decode_request(body)
    roles = [m.role for m in req.messages]
    assert roles == ["user", "assistant", "user"], roles
    assistant_parts = req.messages[1].parts
    assert any(isinstance(p, ir.ToolUsePart) and p.id == "srvtoolu_01"
               for p in assistant_parts), assistant_parts
    # thinking block survives with signature
    think = [p for p in assistant_parts if isinstance(p, ir.ThinkingPart)]
    assert think and think[0].signature == "sig1"
    # server-tool result decodes to a ToolResultPart bound to the same id
    result_parts = [p for p in req.messages[2].parts
                    if isinstance(p, ir.ToolResultPart)]
    assert result_parts and result_parts[0].tool_use_id == "srvtoolu_01"


# ---------------------------------------------------------------------------
# C1.9: empty text blocks never sent to Anthropic
# ---------------------------------------------------------------------------

def test_anthropic_adapter_filters_empty_text_parts():
    a = AnthropicAdapter()
    req = ir.Request(
        model="claude", messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")]),
            ir.Message(role="assistant", parts=[
                ir.TextPart(""),  # OpenAI content:"" echo
                ir.ToolUsePart(id="t1", name="f", args={}),
            ]),
            ir.Message(role="user", parts=[ir.ToolResultPart(tool_use_id="t1",
                                                             content="ok")]),
        ])
    body = a.encode_request(req, "claude-x", {})
    for m in body["messages"]:
        for b in m["content"]:
            if b["type"] == "text":
                assert b["text"], "empty text block must not reach Anthropic"


# ---------------------------------------------------------------------------
# C2: decode robustness
# ---------------------------------------------------------------------------

def test_openai_chat_non_dict_content_item_skipped():
    body = {"model": "gpt-4o", "messages": [
        {"role": "user", "content": ["plain string", {"type": "text",
                                                      "text": "real"}]},
    ]}
    req = oc.decode_request(body)
    texts = [p.text for p in req.messages[0].parts
             if isinstance(p, ir.TextPart)]
    assert texts == ["real"]


def test_anthropic_non_dict_content_block_skipped():
    body = {"model": "claude", "max_tokens": 10, "messages": [
        {"role": "user", "content": ["plain string", {"type": "text",
                                                      "text": "real"}]},
    ]}
    req = am.decode_request(body)
    texts = [p.text for p in req.messages[0].parts
             if isinstance(p, ir.TextPart)]
    assert texts == ["real"]


def test_anthropic_thinking_string_does_not_crash():
    body = {"model": "claude", "max_tokens": 10,
            "thinking": "enabled",  # malformed: must be a dict
            "messages": [{"role": "user", "content": "hi"}]}
    req = am.decode_request(body)
    assert req.gen_params.thinking_budget is None


def test_openai_chat_max_tokens_zero_respected():
    body = {"model": "gpt-4o", "max_tokens": 0,
            "messages": [{"role": "user", "content": "hi"}]}
    req = oc.decode_request(body)
    assert req.gen_params.max_tokens == 0


def test_openai_legacy_function_call_finish_reason():
    a = OpenAIAdapter()
    turn = a.decode_response(200, json.dumps({
        "choices": [{"message": {"role": "assistant", "content": None,
                                 "function_call": {"name": "f",
                                                   "arguments": "{}"}},
                     "finish_reason": "function_call"}],
    }).encode())
    assert turn.stop_reason == "tool_call"
    # streaming map too
    a2 = OpenAIAdapter()
    deltas = a2.decode_stream_event("message", json.dumps({
        "choices": [{"delta": {}, "finish_reason": "function_call"}]}))
    finish = next(d for d in deltas if isinstance(d, dl.Finish))
    assert finish.stop_reason == "tool_call"


def test_openai_chat_developer_role_maps_to_system():
    body = {"model": "gpt-4o", "messages": [
        {"role": "developer", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]}
    req = oc.decode_request(body)
    assert req.messages[0].role == "system"


# ---------------------------------------------------------------------------
# C6 preview (same decode maps): refusal + compaction
# ---------------------------------------------------------------------------

def test_openai_adapter_capture_refusal():
    a = OpenAIAdapter()
    turn = a.decode_response(200, json.dumps({
        "choices": [{"message": {"role": "assistant", "content": None,
                                 "refusal": "I cannot help with that."},
                     "finish_reason": "content_filter"}],
    }).encode())
    assert turn.text == "I cannot help with that."


def test_anthropic_compaction_stop_reason():
    a = AnthropicAdapter()
    turn = a.decode_response(200, json.dumps({
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "compaction",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }).encode())
    assert turn.stop_reason == "stop"
    a2 = AnthropicAdapter()
    deltas = a2.decode_stream_event("message_delta", json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "compaction"},
        "usage": {"output_tokens": 1}}))
    finish = next(d for d in deltas if isinstance(d, dl.Finish))
    assert finish.stop_reason == "stop"
