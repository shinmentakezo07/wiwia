"""Round 64 — Anthropic /v1/messages fidelity for Claude Code (AUDIT #156).

Each test defends one observable contract that was broken. They are written
against what a Claude Code client (or the Anthropic SDK) actually sees, not
against internal wiring.
"""

from __future__ import annotations

import base64
import json

import orjson
import pytest

from wiwi.core.context import RequestContext
from wiwi.core.gateway import media_tokens
from wiwi.cost.pricing import estimate_media_tokens, estimate_tokens
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc


def _frames(blob: bytes) -> list[tuple[str, dict]]:
    out = []
    for raw in blob.split(b"\n\n"):
        if not raw.strip():
            continue
        ev = data = None
        for line in raw.split(b"\n"):
            if line.startswith(b"event: "):
                ev = line[7:].decode()
            elif line.startswith(b"data: "):
                data = line[6:].decode()
        if data is not None:
            out.append((ev, json.loads(data)))
    return out


def _run_stream(encoder, deltas) -> bytes:
    out = b""
    for d in deltas:
        chunk = encoder.feed(d)
        if chunk:
            out += chunk
    return out + encoder.final_frame()


# -- 1. message_start carries real usage -------------------------------------


def test_message_start_carries_upstream_usage():
    """Claude Code reads prompt/cache usage from message_start to size its
    context window and decide when to auto-compact. Emitting zeros left its
    running total at 0% for the entire session."""
    a = AnthropicAdapter()
    deltas = a.decode_stream_event("message_start", json.dumps({
        "type": "message_start",
        "message": {"id": "m", "model": "claude",
                    "usage": {"input_tokens": 15000,
                              "cache_read_input_tokens": 12000,
                              "cache_creation_input_tokens": 300,
                              "output_tokens": 1}}}))
    assert deltas[0] == dl.StreamStart(model="claude", prompt=15000,
                                       cached=12000, cache_creation=300)

    enc = am.AnthropicStreamEncoder("claude", "rid")
    blob = enc.feed(deltas[0])
    _ev, data = _frames(blob)[0]
    usage = data["message"]["usage"]
    assert usage["input_tokens"] == 15000
    assert usage["cache_read_input_tokens"] == 12000
    assert usage["cache_creation_input_tokens"] == 300


def test_message_start_usage_defaults_to_zero_without_upstream_report():
    """Providers that report usage only at the end must not crash the encoder
    and still produce a well-formed frame."""
    enc = am.AnthropicStreamEncoder("gpt-x", "rid")
    _ev, data = _frames(enc.feed(dl.StreamStart(model="gpt-x")))[0]
    assert data["message"]["usage"]["input_tokens"] == 0


# -- 2. builtin suppression keys on the flag, not the name --------------------


def test_function_tool_named_web_search_survives_anthropic_encode():
    """A caller-defined function tool that happens to be named ``web_search``
    is a real call; matching on the name deleted it from the response."""
    turn = ir.AssistantTurn(
        text="Looking.",
        tool_calls=[ir.ToolUsePart(id="t1", name="web_search", args={"q": "x"})],
        stop_reason="tool_call")
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r")
    assert [b["type"] for b in body["content"]] == ["text", "tool_use"]
    assert body["stop_reason"] == "tool_use"


def test_function_tool_named_web_search_survives_chat_encode():
    turn = ir.AssistantTurn(
        text="Looking.",
        tool_calls=[ir.ToolUsePart(id="c1", name="web_search", args={"q": "x"})],
        stop_reason="tool_call")
    body = oc.encode_response(ctx=None, turn=turn, model="gpt-x", req_id="r")
    msg = body["choices"][0]["message"]
    assert msg["tool_calls"][0]["function"]["name"] == "web_search"
    assert body["choices"][0]["finish_reason"] == "tool_calls"


def test_provider_hosted_call_is_suppressed_on_anthropic_surface():
    turn = ir.AssistantTurn(
        text="Found.",
        tool_calls=[ir.ToolUsePart(id="srvtoolu_1", name="web_search",
                                   args={"q": "x"}, builtin="web_search")],
        stop_reason="tool_call")
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r")
    assert [b["type"] for b in body["content"]] == ["text"]
    assert body["stop_reason"] == "end_turn"


# -- 3. sync server_tool_use is tagged, matching the stream path --------------


def test_sync_decode_tags_server_tool_use_as_builtin():
    """Before this, an identical upstream response produced a clean stream but
    a phantom client tool_use in non-streaming mode."""
    turn = AnthropicAdapter().decode_response(200, orjson.dumps({
        "id": "m", "type": "message", "role": "assistant", "model": "claude",
        "content": [{"type": "server_tool_use", "id": "srvtoolu_9",
                     "name": "code_execution", "input": {"code": "1+1"}}],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 2}}))
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].builtin == "code_execution"
    # And the client therefore sees no phantom call.
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r")
    assert [b["type"] for b in body["content"]] == ["text"]


# -- 4. stop_reason vocabulary survives the round trip -----------------------


@pytest.mark.parametrize("anthropic_reason,ir_reason", [
    ("pause_turn", "pause_turn"),
    ("stop_sequence", "stop_sequence"),
    ("model_context_window_exceeded", "context_window_exceeded"),
    ("compaction", "compaction"),
    ("end_turn", "stop"),
    ("max_tokens", "length"),
    ("refusal", "content_filter"),
])
def test_stop_reason_round_trips_through_the_ir(anthropic_reason, ir_reason):
    turn = AnthropicAdapter().decode_response(200, orjson.dumps({
        "id": "m", "type": "message", "role": "assistant", "model": "claude",
        "content": [{"type": "text", "text": "x"}],
        "stop_reason": anthropic_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}))
    assert turn.stop_reason == ir_reason
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r")
    assert body["stop_reason"] == anthropic_reason


def test_tool_use_stop_reason_round_trips_with_a_tool_block():
    """tool_use is excluded from the matrix above: the A1 guard downgrades it
    to end_turn when the content carries no tool_use block, which is correct.
    With a real block it must survive."""
    turn = AnthropicAdapter().decode_response(200, orjson.dumps({
        "id": "m", "type": "message", "role": "assistant", "model": "claude",
        "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                     "input": {"file_path": "a"}}],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}))
    assert turn.stop_reason == "tool_call"
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r")
    assert body["stop_reason"] == "tool_use"


def test_streaming_stop_reason_preserves_pause_turn():
    a = AnthropicAdapter()
    deltas = a.decode_stream_event("message_delta", json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "pause_turn", "stop_sequence": None},
        "usage": {"output_tokens": 7}}))
    finish = next(d for d in deltas if isinstance(d, dl.Finish))
    assert finish.stop_reason == "pause_turn"

    enc = am.AnthropicStreamEncoder("claude", "r")
    blob = _run_stream(enc, [dl.StreamStart(model="claude"), dl.TextDelta("x"),
                             *deltas, dl.StreamEnd()])
    md = next(d for _e, d in _frames(blob) if d.get("type") == "message_delta")
    assert md["delta"]["stop_reason"] == "pause_turn"


def test_downgraded_tool_use_clears_stop_sequence():
    """A matched stop sequence is only meaningful with stop_reason
    'stop_sequence'; leaving it set on a downgraded turn is a spurious match."""
    enc = am.AnthropicStreamEncoder("claude", "r")
    blob = _run_stream(enc, [
        dl.StreamStart(model="claude"),
        dl.ToolCallOpen(index=0, id="t1", name="web_search", builtin="web_search"),
        dl.ToolCallClose(index=0),
        dl.Finish("tool_call", stop_sequence="STOP"),
        dl.StreamEnd()])
    md = next(d for _e, d in _frames(blob) if d.get("type") == "message_delta")
    assert md["delta"]["stop_reason"] == "end_turn"
    assert md["delta"]["stop_sequence"] is None


# -- 5. errored streams stay well-formed -------------------------------------


def test_error_frame_closes_open_blocks():
    """A stream dying mid-tool-args must not leave a content_block_start with
    no matching stop, or the client renders a tool call with empty arguments."""
    enc = am.AnthropicStreamEncoder("claude", "r")
    out = b""
    for d in [dl.StreamStart(model="claude"),
              dl.ToolCallOpen(index=0, id="t1", name="Read"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"file_path":'),
              dl.StreamError("upstream idle", "timeout")]:
        chunk = enc.feed(d)
        if chunk:
            out += chunk
    frames = _frames(out)
    types = [d.get("type") for _e, d in frames]
    assert "error" in types
    starts = [d["index"] for _e, d in frames
              if d.get("type") == "content_block_start"]
    stops = [d["index"] for _e, d in frames
             if d.get("type") == "content_block_stop"]
    assert sorted(starts) == sorted(stops)


def test_stream_error_type_reflects_kind():
    """Claude Code's retry/backoff keys on the error type; every failure was
    reported as an opaque api_error."""
    def err_type(d):
        enc = am.AnthropicStreamEncoder("claude", "r")
        blob = enc.feed(d)
        return _frames(blob)[0][1]["error"]["type"]

    assert err_type(dl.StreamError("idle", "timeout")) == "timeout_error"
    assert err_type(dl.StreamError("gone", "connection")) == "api_error"
    assert err_type(dl.StreamError("overloaded", "status", 529)) == "overloaded_error"
    assert err_type(dl.StreamError("slow down", "status", 429)) == "rate_limit_error"
    # An upstream error frame names its own type and carries no HTTP status;
    # that name is the most precise signal and must pass through.
    assert err_type(dl.StreamError("busy", "status",
                                   etype="overloaded_error")) == "overloaded_error"


def test_interleaved_text_is_preserved_not_dropped():
    """Text arriving while a tool block is open cannot be emitted inline
    (Anthropic blocks are sequential) but must not be discarded — it is part of
    the answer and the client replays it on the next turn."""
    enc = am.AnthropicStreamEncoder("claude", "r")
    out = b""
    for d in [dl.StreamStart(model="claude"),
              dl.ToolCallOpen(index=0, id="t1", name="Bash"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"cmd":'),
              dl.TextDelta("Let me explain."),
              dl.ToolCallArgsDelta(index=0, args_fragment='"ls"}'),
              dl.ToolCallClose(index=0),
              dl.UsageFinal(prompt=1, output=1),
              dl.Finish("tool_call"), dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            out += chunk
    out += enc.final_frame()
    frames = _frames(out)
    kinds = [d["content_block"]["type"] for _e, d in frames
             if d.get("type") == "content_block_start"]
    assert kinds == ["tool_use", "text"]
    texts = [d["delta"]["text"] for _e, d in frames
             if d.get("type") == "content_block_delta"
             and d["delta"].get("type") == "text_delta"]
    assert texts == ["Let me explain."]


def test_interleaved_thinking_is_preserved_not_dropped():
    enc = am.AnthropicStreamEncoder("claude", "r")
    out = b""
    for d in [dl.StreamStart(model="claude"),
              dl.ToolCallOpen(index=0, id="t1", name="Bash"),
              dl.ThinkingDelta("considering the options"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"cmd":"ls"}'),
              dl.ToolCallClose(index=0),
              dl.UsageFinal(prompt=1, output=1),
              dl.Finish("tool_call"), dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            out += chunk
    out += enc.final_frame()
    frames = _frames(out)
    kinds = [d["content_block"]["type"] for _e, d in frames
             if d.get("type") == "content_block_start"]
    assert kinds == ["tool_use", "thinking"]
    thinks = [d["delta"]["thinking"] for _e, d in frames
              if d.get("type") == "content_block_delta"
              and d["delta"].get("type") == "thinking_delta"]
    assert thinks == ["considering the options"]


def test_deferred_content_emitted_at_final_frame_if_tool_never_closes():
    """An upstream may end a message with the tool block still open; the
    buffered content must still reach the client."""
    enc = am.AnthropicStreamEncoder("claude", "r")
    out = b""
    for d in [dl.StreamStart(model="claude"),
              dl.ToolCallOpen(index=0, id="t1", name="Bash"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"cmd":"ls"}'),
              dl.TextDelta("trailing note"),
              dl.UsageFinal(prompt=1, output=1),
              dl.Finish("tool_call"), dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            out += chunk
    out += enc.final_frame()
    texts = [d["delta"]["text"] for _e, d in _frames(out)
             if d.get("type") == "content_block_delta"
             and d["delta"].get("type") == "text_delta"]
    assert texts == ["trailing note"]
    starts = [d["index"] for _e, d in _frames(out)
              if d.get("type") == "content_block_start"]
    stops = [d["index"] for _e, d in _frames(out)
             if d.get("type") == "content_block_stop"]
    assert sorted(starts) == sorted(stops)


# -- 6. effort reaches every backend ----------------------------------------


def test_output_config_effort_reaches_anthropic_upstream():
    req = am.decode_request({
        "model": "claude", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"effort": "xhigh"}})
    assert req.gen_params.effort == "xhigh"
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["output_config"]["effort"] == "xhigh"


def test_output_config_effort_becomes_reasoning_effort_on_openai():
    from wiwi.providers.openai_adapter import OpenAIAdapter

    req = am.decode_request({
        "model": "gpt-x", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"effort": "high"}})
    body = OpenAIAdapter().encode_request(req, "gpt-x",
                                          {"provider_type": "openai"})
    assert body["reasoning_effort"] == "high"


def test_adaptive_thinking_yields_effort_for_budget_based_provider():
    """Claude Code 2.1.x sends thinking {"type":"adaptive"}; only the Anthropic
    adapter acted on it, so every other backend got no reasoning control."""
    req = am.decode_request({
        "model": "m", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "adaptive"}})
    assert req.gen_params.effective_reasoning_effort() is None
    # A budget-based provider still resolves a default budget rather than
    # silently dropping the request for thinking.
    req2 = am.decode_request({
        "model": "m", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"}})
    assert req2.gen_params.effective_thinking_budget() == 1024


# -- 7. max_tokens: 0 is a cache pre-warm, not a request for 4096 ------------


def test_zero_max_tokens_is_preserved():
    req = am.decode_request({"model": "claude", "max_tokens": 0,
                             "messages": [{"role": "user", "content": "hi"}]})
    assert req.gen_params.max_tokens == 0
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["max_tokens"] == 0


def test_absent_max_tokens_still_defaults():
    req = am.decode_request({"model": "claude",
                             "messages": [{"role": "user", "content": "hi"}]})
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["max_tokens"] == 4096


# -- 8. system prompt handling ----------------------------------------------


def test_empty_system_block_is_not_forwarded():
    """Anthropic rejects empty text blocks; the message path filtered them but
    the system array did not."""
    req = ir.Request(model="m", messages=[
        ir.Message(role="system", parts=[
            ir.TextPart(""),
            ir.TextPart("You are Claude Code.",
                        cache_control={"type": "ephemeral"})]),
        ir.Message(role="user", parts=[ir.TextPart("hi")]),
    ])
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert all(b.get("text") for b in body["system"])


def test_mid_conversation_system_role_is_preserved():
    """A system entry appended mid-conversation is a real API role; rewriting
    it to user weakened the instruction and moved its cache position."""
    req = am.decode_request({
        "model": "m", "max_tokens": 10,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "system", "content": [{"type": "text", "text": "Be terse."}]},
        ]})
    assert [m.role for m in req.messages] == ["user", "system"]
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert [m["role"] for m in body["messages"]] == ["user", "system"]
    assert "system" not in body  # not hoisted to the top-level field


# -- 9. cache_control survives on media ------------------------------------


def test_image_and_document_cache_control_round_trip():
    req = am.decode_request({
        "model": "m", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": "AA"},
             "cache_control": {"type": "ephemeral"}},
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": "JVBERi0="},
             "cache_control": {"type": "ephemeral"}},
        ]}]})
    blocks = req.messages[0].parts
    assert blocks[0].cache_control == {"type": "ephemeral"}
    assert blocks[1].cache_control == {"type": "ephemeral"}

    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    content = body["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1]["cache_control"] == {"type": "ephemeral"}


# -- 10. MCP / unknown server-tool blocks ----------------------------------


def test_mcp_tool_use_is_paired_with_its_result_on_replay():
    """Rewriting the call to a plain tool_use left the mcp_tool_result
    unpaired, which Anthropic rejects."""
    req = am.decode_request({
        "model": "m", "max_tokens": 10,
        "messages": [
            {"role": "assistant", "content": [
                {"type": "mcp_tool_use", "id": "mcptu_1",
                 "name": "mcp__fs__read", "input": {"p": "/a"}}]},
            {"role": "user", "content": [
                {"type": "mcp_tool_result", "tool_use_id": "mcptu_1",
                 "content": [{"type": "text", "text": "data"}]}]},
        ]})
    call = next(p for m in req.messages for p in m.parts
                if isinstance(p, ir.ToolUsePart))
    assert call.block_type == "mcp_tool_use"
    assert call.builtin is not None  # provider-executed: the client cannot run it

    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    flat = [b for m in body["messages"] for b in m["content"]]
    assert [b["type"] for b in flat] == ["mcp_tool_use", "mcp_tool_result"]


# -- 11. count_tokens counts media -----------------------------------------


def test_count_tokens_counts_base64_images():
    """A 300 KB screenshot used to report 7 tokens instead of ~1500, so
    auto-compact never fired."""
    b64 = base64.b64encode(b"\x89PNG" + b"a" * 300_000).decode()
    req = ir.Request(model="claude-sonnet-4-5", messages=[
        ir.Message(role="user", parts=[
            ir.TextPart("what is this?"),
            ir.ImagePart(b64=b64, mime="image/png")])])
    ctx = RequestContext(surface="messages", ir_req=req)
    assert media_tokens(ctx) > 500


def test_media_estimator_scales_with_payload():
    assert estimate_media_tokens(0) == 0
    small = estimate_media_tokens(10_000, "image/jpeg")
    large = estimate_media_tokens(400_000, "image/jpeg")
    assert small < large
    # PNG is lossless, so the same byte count buys fewer tokens than JPEG.
    assert estimate_media_tokens(400_000, "image/png") > \
        estimate_media_tokens(400_000, "image/jpeg")


def test_claude_models_use_a_real_tokenizer():
    """chars/4 undercounted every Claude model; cl100k is a far closer proxy."""
    text = "def f():\n    return {'a': 1, 'b': [2, 3]}\n" * 200
    assert estimate_tokens(text, "claude-sonnet-4-5") > len(text) // 4


# -- 12. error body carries the request id ---------------------------------


def test_anthropic_error_body_includes_request_id():
    body = am.error_body(400, "invalid_request_error", "bad", request_id="abc123")
    assert body["request_id"] == "abc123"
    assert body["error"]["type"] == "invalid_request_error"


def test_anthropic_error_body_omits_request_id_when_unknown():
    assert "request_id" not in am.error_body(400, "invalid_request_error", "bad")


# -- 13. OpenRouter synthesized-open leak ----------------------------------


def test_openrouter_finish_sweep_clears_synthesized_opens():
    """A stale index made a later call on the same index emit ArgsDelta with no
    Open, which every encoder drops."""
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    ad = OpenRouterAdapter()
    ad.reset()
    first = ad.decode_stream_event("", orjson.dumps({"choices": [{"delta": {
        "tool_calls": [{"index": 0,
                        "function": {"name": "Read",
                                     "arguments": '{"a":1}'}}]}}]}).decode())
    assert any(isinstance(d, dl.ToolCallOpen) for d in first)
    ad.decode_stream_event("", orjson.dumps(
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}).decode())
    assert ad._synthesized_opens == set()


# -- 14. Gemini tool_choice ------------------------------------------------


def test_gemini_encodes_tool_choice():
    from wiwi.providers.gemini_adapter import GeminiAdapter

    req = ir.Request(
        model="gemini", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="Read", description="r",
                       parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceRequired())
    body = GeminiAdapter().encode_request(req, "gemini-2.0", {})
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"


def test_gemini_encodes_named_tool_choice():
    from wiwi.providers.gemini_adapter import GeminiAdapter

    req = ir.Request(
        model="gemini", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="Read", description="r",
                       parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceNamed("Read"))
    body = GeminiAdapter().encode_request(req, "gemini-2.0", {})
    cfg = body["toolConfig"]["functionCallingConfig"]
    assert cfg["mode"] == "ANY"
    assert cfg["allowedFunctionNames"] == ["Read"]


def test_gemini_encodes_documents():
    from wiwi.providers.gemini_adapter import GeminiAdapter

    req = ir.Request(model="gemini", messages=[
        ir.Message(role="user", parts=[
            ir.TextPart("summarize"),
            ir.DocumentPart(b64="JVBERi0=", mime="application/pdf")])])
    body = GeminiAdapter().encode_request(req, "gemini-2.0", {})
    parts = body["contents"][0]["parts"]
    assert any("inline_data" in p for p in parts)


# -- 15. opencode responses route honours disable_parallel_tool_use --------


def test_opencode_responses_route_honours_disable_parallel():
    from wiwi.providers.opencode_adapter import _encode_responses_request

    req = ir.Request(
        model="zen", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(disable_parallel_tool_use=True))
    body = _encode_responses_request(req, "zen", {})
    assert body["parallel_tool_calls"] is False
