"""Regression tests for OpenAI <-> Anthropic cross-provider translation enhancements.

Covers:
- Anthropic max_tokens > budget_tokens constraint
- reasoning_effort "none" disables thinking (no thinking key emitted)
- reasoning_effort "xhigh" maps to a large thinking budget
- thinking_tokens captured from Anthropic usage (stream + non-stream)
- reasoning_content included in OpenAI non-streaming encode_response
- reasoning_content captured from OpenAI-compatible provider responses
- tool_choice: none passed through to Anthropic (was mapped to "auto")
- server_tool_use content blocks handled by Anthropic adapter
- pause_turn stop reason mapped
- output_tokens_details in Anthropic encode_response
- ChatStreamEncoder _usage/_stop properly initialized (no AttributeError)
- Cross-provider round-trip: OpenAI dialect -> Anthropic provider -> OpenAI response
- Cross-provider round-trip: Anthropic dialect -> OpenAI provider -> Anthropic response
"""

import json

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# -- Anthropic adapter: max_tokens > budget_tokens constraint ------------------

def test_anthropic_max_tokens_raised_above_budget():
    """When thinking budget >= max_tokens, adapter must raise max_tokens."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=8000, max_tokens=1000),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["thinking"]["budget_tokens"] == 8000
    assert body["max_tokens"] > 8000


def test_anthropic_budget_clamped_to_minimum():
    """budget_tokens below 1024 must be clamped to the API minimum."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=500, max_tokens=4096),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["thinking"]["budget_tokens"] >= 1024


def test_anthropic_max_tokens_unchanged_when_already_above_budget():
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=1024, max_tokens=8192),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["max_tokens"] == 8192


# -- reasoning_effort "none" disables thinking --------------------------------

def test_anthropic_effort_none_disables_thinking():
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="none"),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert "thinking" not in body


def test_openai_effort_none_forwarded():
    """OpenAI adapter should forward reasoning_effort='none' as-is."""
    req = ir.Request(
        model="o3",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="none"),
    )
    body = OpenAIAdapter().encode_request(req, "o3", {})
    assert body["reasoning_effort"] == "none"


# -- reasoning_effort "xhigh" maps to large budget ----------------------------

def test_xhigh_effort_maps_to_large_budget():
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="xhigh", max_tokens=100000),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["thinking"]["budget_tokens"] == 64000


def test_xhigh_budget_to_effort():
    from wiwi.ir.types import thinking_budget_to_effort
    assert thinking_budget_to_effort(64000) == "xhigh"
    assert thinking_budget_to_effort(50000) == "xhigh"


# -- thinking_tokens captured from Anthropic usage -----------------------------

def test_anthropic_decode_response_captures_thinking_tokens():
    payload = {
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude", "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "answer"}],
        "usage": {
            "input_tokens": 10, "output_tokens": 20,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 0,
            "output_tokens_details": {"thinking_tokens": 15},
        },
    }
    turn = AnthropicAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.usage.reasoning_tokens == 15
    assert turn.usage.cached_tokens == 3
    assert turn.thinking[0].text == "hmm"
    assert turn.text == "answer"


def test_anthropic_stream_captures_thinking_tokens():
    ad = AnthropicAdapter()
    deltas = []
    events = [
        ("message_start", json.dumps({"type": "message_start", "message": {
            "model": "claude", "usage": {"input_tokens": 12}}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta",
                                                      "text": "Hi"}})),
        ("message_delta", json.dumps({"type": "message_delta",
                                      "delta": {"stop_reason": "end_turn"},
                                      "usage": {"output_tokens": 7,
                                                "output_tokens_details": {
                                                    "thinking_tokens": 42}}})),
        ("message_stop", json.dumps({"type": "message_stop"})),
    ]
    for e, d in events:
        deltas.extend(ad.decode_stream_event(e, d))
    usage = [d for d in deltas if isinstance(d, dl.UsageFinal)]
    assert len(usage) == 1
    assert usage[0].reasoning == 42
    assert usage[0].output == 7


# -- reasoning_content in OpenAI encode_response ------------------------------

def test_openai_encode_response_includes_reasoning_content():
    turn = ir.AssistantTurn(
        text="answer",
        thinking=[ir.ThinkingPart("let me think...")],
        usage=ir.Usage(prompt_tokens=5, completion_tokens=10),
    )
    resp = oc.encode_response(ctx=None, turn=turn, model="o3", req_id="abc")
    msg = resp["choices"][0]["message"]
    assert msg["content"] == "answer"
    assert msg["reasoning_content"] == "let me think..."


def test_openai_encode_response_no_reasoning_content_when_empty():
    turn = ir.AssistantTurn(text="hello", usage=ir.Usage(prompt_tokens=5,
                                                         completion_tokens=10))
    resp = oc.encode_response(ctx=None, turn=turn, model="gpt-4o", req_id="abc")
    msg = resp["choices"][0]["message"]
    assert "reasoning_content" not in msg


# -- reasoning_content captured from OpenAI-compatible provider ---------------

def test_openai_decode_response_captures_reasoning_content():
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "answer",
                                 "reasoning_content": "step by step"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    turn = OpenAIAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.text == "answer"
    assert turn.thinking[0].text == "step by step"


def test_openai_decode_response_captures_reasoning_key():
    """Some providers use 'reasoning' instead of 'reasoning_content'."""
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "answer",
                                 "reasoning": "thought process"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    turn = OpenAIAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.thinking[0].text == "thought process"


# -- tool_choice: none passed through to Anthropic ----------------------------

def test_anthropic_tool_choice_none_passed_through():
    """Previously mapped to 'auto' (workaround for pre-4.x APIs); now passed."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d", parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceNone(),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"] == {"type": "none"}


def test_anthropic_tool_choice_auto_passed_through():
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d", parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceAuto(),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"] == {"type": "auto"}


# -- server_tool_use content blocks -------------------------------------------

def test_anthropic_decode_server_tool_use():
    """server_tool_use blocks (web_search etc.) must be decoded as tool calls."""
    payload = {
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude", "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "Let me search."},
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
             "input": {"query": "weather"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    turn = AnthropicAdapter().decode_response(200, json.dumps(payload).encode())
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].id == "srvtoolu_1"
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].args == {"query": "weather"}


def test_anthropic_stream_server_tool_use():
    """server_tool_use in streaming should emit ToolCallOpen + close."""
    ad = AnthropicAdapter()
    deltas = []
    events = [
        ("message_start", json.dumps({"type": "message_start", "message": {
            "model": "claude", "usage": {"input_tokens": 10}}})),
        ("content_block_start", json.dumps({"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "server_tool_use",
                                                              "id": "srv_1",
                                                              "name": "web_search"}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "input_json_delta",
                                                      "partial_json": '{"query":"x"}'}})),
        ("content_block_stop", json.dumps({"type": "content_block_stop", "index": 0})),
        ("message_delta", json.dumps({"type": "message_delta",
                                      "delta": {"stop_reason": "tool_use"},
                                      "usage": {"output_tokens": 5}})),
        ("message_stop", json.dumps({"type": "message_stop"})),
    ]
    for e, d in events:
        deltas.extend(ad.decode_stream_event(e, d))
    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1
    assert opens[0].id == "srv_1"
    assert opens[0].name == "web_search"
    closes = [d for d in deltas if isinstance(d, dl.ToolCallClose)]
    assert len(closes) == 1


# -- pause_turn stop reason ---------------------------------------------------

def test_anthropic_pause_turn_mapped_to_stop():
    """pause_turn is a valid Anthropic stop_reason; map to IR 'stop'."""
    payload = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude",
        "stop_reason": "pause_turn",
        "content": [{"type": "text", "text": "partial"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    turn = AnthropicAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.stop_reason == "stop"


# -- output_tokens_details in Anthropic encode_response ----------------------

def test_anthropic_encode_response_includes_thinking_tokens():
    turn = ir.AssistantTurn(
        text="answer",
        thinking=[ir.ThinkingPart("hmm")],
        usage=ir.Usage(prompt_tokens=10, completion_tokens=20, reasoning_tokens=15),
    )
    resp = am.encode_response(ctx=None, turn=turn, model="claude", req_id="abc")
    assert resp["usage"]["output_tokens_details"]["thinking_tokens"] == 15


def test_anthropic_encode_response_no_thinking_tokens_details_when_zero():
    turn = ir.AssistantTurn(
        text="answer",
        usage=ir.Usage(prompt_tokens=10, completion_tokens=20),
    )
    resp = am.encode_response(ctx=None, turn=turn, model="claude", req_id="abc")
    details = resp["usage"].get("output_tokens_details", {})
    assert not details or details.get("thinking_tokens", 0) == 0


# -- ChatStreamEncoder _usage/_stop initialization ---------------------------

def test_chat_stream_encoder_no_attribute_error_on_final_frame():
    """If the stream ends without UsageFinal/Finish, final_frame must not crash."""
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    for d in [dl.StreamStart("gpt-4o"), dl.TextDelta("hi"), dl.StreamEnd()]:
        enc.feed(d)
    frame = enc.final_frame()
    assert b"finish_reason" in frame


# -- Cross-provider round-trip: OpenAI dialect -> Anthropic provider -----------

def test_cross_provider_openai_to_anthropic_thinking():
    """OpenAI client sends reasoning_effort=high, routed to Anthropic provider.
    The Anthropic request must have thinking enabled with correct budget,
    and max_tokens must be > budget_tokens."""
    req = oc.decode_request({
        "model": "claude-via-openai",
        "messages": [{"role": "user", "content": "think hard"}],
        "reasoning_effort": "high",
        "max_tokens": 1000,
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["thinking"]["type"] == "enabled"
    assert body["thinking"]["budget_tokens"] == 32000
    assert body["max_tokens"] > 32000


def test_cross_provider_openai_to_anthropic_none():
    """OpenAI client sends reasoning_effort=none, routed to Anthropic provider.
    Thinking must NOT be enabled on the Anthropic side."""
    req = oc.decode_request({
        "model": "claude-via-openai",
        "messages": [{"role": "user", "content": "quick answer"}],
        "reasoning_effort": "none",
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert "thinking" not in body


def test_cross_provider_anthropic_to_openai_thinking():
    """Anthropic client sends thinking budget, routed to OpenAI provider.
    The OpenAI request must have reasoning_effort set."""
    req = am.decode_request({
        "model": "o3-via-anthropic",
        "messages": [{"role": "user", "content": "think"}],
        "thinking": {"type": "enabled", "budget_tokens": 32000},
        "max_tokens": 100000,
    })
    body = OpenAIAdapter().encode_request(req, "o3", {})
    assert body["reasoning_effort"] == "high"


# -- Full cross-provider round-trip: response encoding ------------------------

def test_cross_provider_anthropic_to_openai_response():
    """Anthropic provider response decoded, then re-encoded in OpenAI dialect."""
    anthropic_resp = {
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-sonnet-4", "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "reasoning here"},
            {"type": "text", "text": "final answer"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20,
                  "output_tokens_details": {"thinking_tokens": 12}},
    }
    turn = AnthropicAdapter().decode_response(200, json.dumps(anthropic_resp).encode())
    oai_resp = oc.encode_response(ctx=None, turn=turn, model="claude-sonnet-4",
                                  req_id="abc")
    msg = oai_resp["choices"][0]["message"]
    assert msg["content"] == "final answer"
    assert msg["reasoning_content"] == "reasoning here"
    assert oai_resp["usage"]["completion_tokens_details"]["reasoning_tokens"] == 12


def test_cross_provider_openai_to_anthropic_response():
    """OpenAI provider response decoded, then re-encoded in Anthropic dialect."""
    openai_resp = {
        "choices": [{"message": {"role": "assistant", "content": "answer",
                                 "reasoning_content": "thinking process"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                  "completion_tokens_details": {"reasoning_tokens": 8}},
    }
    turn = OpenAIAdapter().decode_response(200, json.dumps(openai_resp).encode())
    ant_resp = am.encode_response(ctx=None, turn=turn, model="gpt-4o", req_id="abc")
    assert ant_resp["content"][0]["type"] == "thinking"
    assert ant_resp["content"][0]["thinking"] == "thinking process"
    assert ant_resp["content"][1]["type"] == "text"
    assert ant_resp["content"][1]["text"] == "answer"
    assert ant_resp["usage"]["output_tokens_details"]["thinking_tokens"] == 8


# -- Streaming cross-provider: OpenAI provider -> Anthropic client ------------

def test_cross_provider_stream_openai_to_anthropic():
    """OpenAI stream events decoded, then encoded in Anthropic SSE format."""
    ad = OpenAIAdapter()
    enc = am.AnthropicStreamEncoder("gpt-4o", "abc")
    frames = []

    events = [
        json.dumps({"choices": [{"delta": {"role": "assistant", "content": ""}}]}),
        json.dumps({"choices": [{"delta": {"content": "Hel"}}]}),
        json.dumps({"choices": [{"delta": {"content": "lo"}}]}),
        json.dumps({"choices": [{"delta": {"reasoning_content": "thinking"}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
    ]
    # The gateway emits StreamStart separately (not from the adapter), so
    # inject it before processing to simulate the real flow.
    start_chunk = enc.feed(dl.StreamStart(model="gpt-4o"))
    if start_chunk:
        frames.append(start_chunk.decode())
    for ev in events:
        for d in ad.decode_stream_event("", ev):
            chunk = enc.feed(d)
            if chunk:
                frames.append(chunk.decode())
    frames.append(enc.final_frame().decode())
    blob = "".join(frames)
    assert "message_start" in blob
    assert "text_delta" in blob and "Hel" in blob
    assert "thinking_delta" in blob and "thinking" in blob
    assert "message_delta" in blob


# -- Streaming cross-provider: Anthropic provider -> OpenAI client ------------

def test_cross_provider_stream_anthropic_to_openai():
    """Anthropic stream events decoded, then encoded in OpenAI chat.completion.chunk."""
    ad = AnthropicAdapter()
    enc = oc.ChatStreamEncoder("claude", "abc")
    frames = []

    events = [
        ("message_start", json.dumps({"type": "message_start", "message": {
            "model": "claude", "usage": {"input_tokens": 10}}})),
        ("content_block_start", json.dumps({"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "thinking"}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "thinking_delta",
                                                      "thinking": "reasoning"}})),
        ("content_block_stop", json.dumps({"type": "content_block_stop", "index": 0})),
        ("content_block_start", json.dumps({"type": "content_block_start", "index": 1,
                                            "content_block": {"type": "text"}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 1,
                                            "delta": {"type": "text_delta",
                                                      "text": "answer"}})),
        ("content_block_stop", json.dumps({"type": "content_block_stop", "index": 1})),
        ("message_delta", json.dumps({"type": "message_delta",
                                      "delta": {"stop_reason": "end_turn"},
                                      "usage": {"output_tokens": 5}})),
        ("message_stop", json.dumps({"type": "message_stop"})),
    ]
    for e, d in events:
        for delta in ad.decode_stream_event(e, d):
            chunk = enc.feed(delta)
            if chunk:
                frames.append(chunk)
    frames.append(enc.final_frame())
    blob = b"".join(frames).decode()
    assert "reasoning_content" in blob
    assert "answer" in blob
    assert "finish_reason" in blob


# -- Error message extraction from OpenAI-compatible providers ----------------

def test_error_extraction_openrouter_nested():
    """OpenRouter wraps the real error in metadata.raw; extract it."""
    from wiwi.providers.base import error_from_provider_status
    body = json.dumps({
        "error": {
            "message": "Provider returned error",
            "code": 400,
            "metadata": {"raw": "context length exceeded", "provider_name": "Stealth"},
        },
    })
    err = error_from_provider_status(400, body, "openrouter")
    assert "context length exceeded" in err.message
    assert "Stealth" in err.message


def test_error_extraction_openai_shape():
    """Standard OpenAI error: {"error": {"message": "..."}}."""
    from wiwi.providers.base import error_from_provider_status
    body = json.dumps({"error": {"message": "invalid model"}})
    err = error_from_provider_status(400, body, "openai")
    assert err.message == "openai: invalid model"


def test_error_extraction_anthropic_shape():
    """Anthropic error: {"type": "error", "error": {"message": "..."}}."""
    from wiwi.providers.base import error_from_provider_status
    body = json.dumps({"type": "error", "error": {"message": "overloaded"}})
    err = error_from_provider_status(529, body, "anthropic")
    assert "overloaded" in err.message


def test_error_extraction_plain_text():
    """Non-JSON body falls back to the raw text."""
    from wiwi.providers.base import error_from_provider_status
    err = error_from_provider_status(500, "Internal Server Error", "custom")
    assert "Internal Server Error" in err.message


def test_error_context_window_detection_with_extracted_msg():
    """The context-window heuristic should run against the extracted message."""
    from wiwi.providers.base import error_from_provider_status
    body = json.dumps({
        "error": {
            "message": "Provider returned error",
            "metadata": {"raw": "maximum context length is 8192 tokens"},
        },
    })
    err = error_from_provider_status(400, body, "openrouter")
    assert err.etype == "context_window_exceeded"


# -- reasoning_effort guard for openai-compatible -----------------------------

def test_reasoning_effort_not_forwarded_to_openai_compatible():
    """openai-compatible backends may reject reasoning_effort; must not forward."""
    req = ir.Request(
        model="some-model",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="high"),
    )
    body = OpenAIAdapter().encode_request(
        req, "some-model", {"provider_type": "openai-compatible"})
    assert "reasoning_effort" not in body


def test_reasoning_effort_forwarded_to_native_openai():
    """Native OpenAI endpoint should receive reasoning_effort."""
    req = ir.Request(
        model="o3",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="high"),
    )
    body = OpenAIAdapter().encode_request(
        req, "o3", {"provider_type": "openai"})
    assert body["reasoning_effort"] == "high"


def test_thinking_budget_not_mapped_for_openai_compatible():
    """thinking_budget from Anthropic dialect should not be forwarded as
    reasoning_effort to openai-compatible backends."""
    req = ir.Request(
        model="some-model",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=32000),
    )
    body = OpenAIAdapter().encode_request(
        req, "some-model", {"provider_type": "openai-compatible"})
    assert "reasoning_effort" not in body


# -- stream_options only sent when client explicitly asks ---------------------

def test_stream_options_not_sent_by_default():
    """stream_options must not be sent unless the client explicitly requested
    include_usage. Some OpenRouter providers reject the field with a 400."""
    req = ir.Request(
        model="x", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        stream=True, stream_options_include_usage=False,
    )
    body = OpenAIAdapter().encode_request(req, "x", {"provider_type": "openai-compatible"})
    assert "stream_options" not in body


def test_stream_options_sent_when_explicitly_requested():
    req = ir.Request(
        model="x", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        stream=True, stream_options_include_usage=True,
    )
    body = OpenAIAdapter().encode_request(req, "x", {"provider_type": "openai"})
    assert body["stream_options"] == {"include_usage": True}


def test_anthropic_dialect_stream_no_stream_options():
    """Anthropic dialect streaming request must not produce stream_options
    when routed to an OpenAI-compatible backend."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 16384,
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,
    })
    body = OpenAIAdapter().encode_request(req, "stealth/ox-alpha", {
        "provider_type": "openai-compatible", "drop_params": True})
    assert "stream_options" not in body


