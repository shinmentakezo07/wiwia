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




# -- C3: Anthropic structured outputs GA (native output_config) ----------------

def test_anthropic_json_schema_uses_native_output_config():
    """json_schema response_format must ride as Anthropic's native
    output_config.format (2026 GA shape) — NOT as a prompt injection."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(response_format=ir.ResponseFormat(
            type="json_schema",
            json_schema={"type": "object", "properties": {"a": {"type": "string"}}},
            name="my_schema", strict=True)),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
            "name": "my_schema",
            "strict": True,
        }
    }
    # the schema instruction must NOT be injected into the system prompt
    sys_val = body.get("system")
    sys_text = sys_val if isinstance(sys_val, str) else (
        " ".join(b.get("text", "") for b in sys_val or [])
        if isinstance(sys_val, list) else "")
    assert "JSON Schema" not in sys_text and "json_schema" not in sys_text


def test_anthropic_json_object_still_prompt_injected():
    """json_object has no native Anthropic equivalent; instruction injection
    remains the fallback."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(response_format=ir.ResponseFormat(
            type="json_object")),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert "output_config" not in body
    sys_val = body.get("system")
    sys_text = sys_val if isinstance(sys_val, str) else (
        " ".join(b.get("text", "") for b in sys_val or [])
        if isinstance(sys_val, list) else "")
    assert "JSON object" in sys_text


def test_anthropic_decode_output_config_to_response_format():
    """Anthropic-inbound output_config must decode into IR response_format so
    it routes natively when headed to an OpenAI upstream."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"format": {
            "type": "json_schema",
            "schema": {"type": "object", "properties": {"a": {"type": "string"}}},
            "name": "my_schema",
            "strict": True}},
    })
    rf = req.gen_params.response_format
    assert rf is not None and rf.type == "json_schema"
    assert rf.json_schema == {"type": "object",
                              "properties": {"a": {"type": "string"}}}
    assert rf.name == "my_schema"
    assert rf.strict is True


def test_anthropic_output_config_roundtrip():
    """Anthropic dialect in -> IR -> Anthropic adapter out: output_config must
    survive a same-dialect round trip (passthrough fidelity)."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "output_config": {"format": {
            "type": "json_schema",
            "schema": {"type": "object"},
            "name": "s", "strict": True}},
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["output_config"]["format"]["name"] == "s"
    assert body["output_config"]["format"]["strict"] is True


def test_openai_json_schema_to_anthropic_native():
    """OpenAI dialect response_format.json_schema -> Anthropic upstream:
    becomes native output_config, not an injected instruction."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "out",
                                            "schema": {"type": "object"}}},
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"] == {"type": "object"}


# == C4: 2026 parameter surface ===============================================

def test_anthropic_extras_captured_on_decode():
    """Unmapped known-safe Anthropic top-level params must land in req.extras
    instead of being silently dropped."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "service_tier": "priority", "speed": "standard",
        "metadata": {"user_id": "u1"},
        "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
        "mcp_servers": [{"type": "url", "url": "https://mcp.example/sse"}],
        "container": {"type": "auto"},
        "fallbacks": [{"model": "claude-haiku-4-5", "max_tokens": 64}],
        "cache_control": {"type": "ephemeral"},
    })
    assert req.extras["service_tier"] == "priority"
    assert req.extras["speed"] == "standard"
    assert req.extras["metadata"] == {"user_id": "u1"}
    assert req.extras["context_management"]["edits"][0]["type"] == "clear_tool_uses_20250919"
    assert req.extras["mcp_servers"][0]["url"] == "https://mcp.example/sse"
    assert req.extras["container"] == {"type": "auto"}
    assert req.extras["fallbacks"][0]["model"] == "claude-haiku-4-5"
    assert req.extras["cache_control"] == {"type": "ephemeral"}


def test_anthropic_extras_forwarded_to_anthropic_upstream():
    """Anthropic-inbound extras ride through to an Anthropic upstream."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "service_tier": "priority",
        "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["service_tier"] == "priority"
    assert body["context_management"] == {"edits": [{"type": "clear_tool_uses_20250919"}]}


def test_anthropic_extras_dropped_when_drop_params_true():
    """drop_params=True (default) on a non-Anthropic-shaped deployment must
    not blindly forward extras — but known-safe standard params pass only
    via the explicit allowlist. The anthropic adapter mirrors the OpenAI
    behaviour: unknown extras are dropped unless drop_params=False."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "service_tier": "priority",
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5",
                                             {"drop_params": True})
    # service_tier is in the Anthropic known-safe set; default drop_params
    # applies to *unknown* extras, not the recognized 2026 surface.
    assert body["service_tier"] == "priority"


def test_anthropic_extras_unknown_dropped_when_drop_params_true():
    """A param outside the known-safe set is dropped under default
    drop_params=True and forwarded when drop_params=False."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
    })
    req.extras["totally_unknown_param"] = {"weird": True}
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert "totally_unknown_param" not in body
    body2 = AnthropicAdapter().encode_request(
        req, "claude-sonnet-4-5", {"drop_params": False})
    assert body2["totally_unknown_param"] == {"weird": True}


def test_anthropic_top_k_roundtrip():
    """top_k decodes from the Anthropic dialect and re-encodes natively."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 40,
    })
    assert req.gen_params.top_k == 40
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["top_k"] == 40


def test_anthropic_top_k_ignored_by_openai_adapter():
    """top_k must NOT be sent to OpenAI-shaped upstreams (unknown param)."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "top_k": 40,
    })
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert "top_k" not in body


def test_openai_standard_params_forwarded_under_default_drop_params():
    """2026 OpenAI params land in extras (not in _KNOWN_KEYS) and must be
    forwarded to native OpenAI even with drop_params=True (the default)."""
    req = oc.decode_request({
        "model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}],
        "verbosity": "low",
        "web_search_options": {"search_context_size": "low"},
        "prediction": {"type": "content", "content": "four score"},
        "store": False,
        "metadata": {"session_id": "s1"},
        "prompt_cache_key": "cache-me",
        "safety_identifier": "user-123",
        "modalities": ["text"],
        "audio": {"voice": "alloy", "format": "wav"},
        "logit_bias": {"1": -100},
        "service_tier": "flex",
    })
    assert req.extras["verbosity"] == "low"
    body = OpenAIAdapter().encode_request(req, "gpt-5.2", {})
    assert body["verbosity"] == "low"
    assert body["web_search_options"] == {"search_context_size": "low"}
    assert body["prediction"]["content"] == "four score"
    assert body["store"] is False
    assert body["metadata"] == {"session_id": "s1"}
    assert body["prompt_cache_key"] == "cache-me"
    assert body["safety_identifier"] == "user-123"
    assert body["modalities"] == ["text"]
    assert body["audio"] == {"voice": "alloy", "format": "wav"}
    assert body["logit_bias"] == {"1": -100}
    assert body["service_tier"] == "flex"


def test_minimal_and_max_effort_levels():
    """2026 OpenAI reasoning_effort levels: minimal (shares the 1024 floor
    with low) and max (shares the 64000 cap with xhigh) extend the forward
    map; the inverse keeps its pre-existing boundaries for the collisions."""
    from wiwi.ir.types import effort_to_thinking_budget, thinking_budget_to_effort
    assert effort_to_thinking_budget("minimal") == 1024
    assert effort_to_thinking_budget("max") == 64000
    assert thinking_budget_to_effort(1024) == "low"
    assert thinking_budget_to_effort(64000) == "xhigh"


def test_minimal_effort_forwarded_to_native_openai():
    req = ir.Request(
        model="gpt-5.2",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="minimal"),
    )
    body = OpenAIAdapter().encode_request(req, "gpt-5.2", {})
    assert body["reasoning_effort"] == "minimal"


def test_anthropic_thinking_adaptive_decoded_and_encoded():
    """thinking.type=adaptive (2026) decodes into thinking_type and
    re-encodes natively as {"type": "adaptive"} with no budget."""
    req = am.decode_request({
        "model": "claude-opus-4-6", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "adaptive"},
    })
    assert req.gen_params.thinking_type == "adaptive"
    body = AnthropicAdapter().encode_request(req, "claude-opus-4-6", {})
    assert body["thinking"] == {"type": "adaptive"}


def test_anthropic_thinking_adaptive_no_budget_tokens():
    """Adaptive thinking must not carry budget_tokens (the API rejects the
    combination)."""
    req = am.decode_request({
        "model": "claude-opus-4-6", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "adaptive", "budget_tokens": 8000},
    })
    body = AnthropicAdapter().encode_request(req, "claude-opus-4-6", {})
    assert body["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in body["thinking"]


def test_anthropic_thinking_disabled_sets_effort_none():
    """thinking.type=disabled (2026) maps to reasoning_effort=none so an
    OpenAI upstream disables reasoning, and an Anthropic upstream simply
    omits the thinking key."""
    req = am.decode_request({
        "model": "claude-opus-4-6", "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "disabled"},
    })
    assert req.gen_params.thinking_type == "disabled"
    assert req.gen_params.reasoning_effort == "none"
    body = AnthropicAdapter().encode_request(req, "claude-opus-4-6", {})
    assert "thinking" not in body
    # crossing to OpenAI: effort none forwarded
    obody = OpenAIAdapter().encode_request(req, "gpt-5.2", {})
    assert obody["reasoning_effort"] == "none"


def test_anthropic_thinking_enabled_still_budgeted():
    """Classic enabled thinking keeps its budget path (regression guard)."""
    req = am.decode_request({
        "model": "claude-opus-4-6", "max_tokens": 8192,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 4000},
    })
    assert req.gen_params.thinking_type == "enabled"
    assert req.gen_params.thinking_budget == 4000
    body = AnthropicAdapter().encode_request(req, "claude-opus-4-6", {})
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4000}


# == C5: multimodal wiring ====================================================

def test_anthropic_document_base64_decoded_to_ir():
    """Anthropic document blocks (base64 PDF) decode into DocumentPart
    instead of being silently dropped."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": "JVBERi0xLjQ="}},
            {"type": "text", "text": "summarize this"},
        ]}],
    })
    parts = req.messages[0].parts
    doc = parts[0]
    assert isinstance(doc, ir.DocumentPart)
    assert doc.b64 == "JVBERi0xLjQ="
    assert doc.mime == "application/pdf"
    assert isinstance(parts[1], ir.TextPart)


def test_anthropic_document_url_decoded_to_ir():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "document", "title": "Q3 report",
             "source": {"type": "url", "url": "https://example.com/r.pdf"}},
        ]}],
    })
    doc = req.messages[0].parts[0]
    assert isinstance(doc, ir.DocumentPart)
    assert doc.url == "https://example.com/r.pdf"
    assert doc.name == "Q3 report"


def test_anthropic_document_roundtrip_through_adapter():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": "JVBERi0xLjQ="}},
        ]}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    blocks = body["messages"][0]["content"]
    assert blocks[0] == {"type": "document",
                         "source": {"type": "base64",
                                    "media_type": "application/pdf",
                                    "data": "JVBERi0xLjQ="}}


def test_anthropic_document_url_roundtrip():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "document", "title": "spec",
             "source": {"type": "url", "url": "https://example.com/s.pdf"}},
        ]}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    block = body["messages"][0]["content"][0]
    assert block["type"] == "document"
    assert block["source"]["type"] == "url"
    assert block["source"]["url"] == "https://example.com/s.pdf"
    assert block["title"] == "spec"


def test_document_part_dropped_safely_by_openai_adapter():
    """OpenAI has no document-input equivalent; DocumentPart must not crash
    the adapter (silently dropped today is acceptable)."""
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[
            ir.DocumentPart(b64="JVBERi0xLjQ="),
            ir.TextPart("summarize"),
        ])],
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["messages"][0]["content"] == "summarize"


def test_openai_input_audio_decoded_to_ir():
    """OpenAI input_audio content parts decode into AudioPart."""
    req = oc.decode_request({
        "model": "gpt-4o-audio-preview",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "transcribe this"},
            {"type": "input_audio",
             "input_audio": {"data": "AQIDBA==", "format": "wav"}},
        ]}],
    })
    parts = req.messages[0].parts
    assert isinstance(parts[0], ir.TextPart)
    audio = parts[1]
    assert isinstance(audio, ir.AudioPart)
    assert audio.b64 == "AQIDBA=="
    assert audio.mime == "audio/wav"


def test_openai_input_audio_no_empty_text_fallback():
    """An audio-only message must not fall into the empty-TextPart fallback
    (previously an all-audio message became TextPart(""))."""
    req = oc.decode_request({
        "model": "gpt-4o-audio-preview",
        "messages": [{"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"data": "AQIDBA==", "format": "mp3"}},
        ]}],
    })
    assert len(req.messages[0].parts) == 1
    assert isinstance(req.messages[0].parts[0], ir.AudioPart)


def test_anthropic_image_file_source_decoded():
    """Anthropic image source.type=file carries a file_id so
    Anthropic->Anthropic passthrough works."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "file", "file_id": "file-abc123"}},
        ]}],
    })
    img = req.messages[0].parts[0]
    assert isinstance(img, ir.ImagePart)
    assert img.file_id == "file-abc123"


def test_anthropic_image_file_source_roundtrip():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "file", "file_id": "file-abc123"}},
        ]}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    block = body["messages"][0]["content"][0]
    assert block["source"] == {"type": "file", "file_id": "file-abc123"}


def test_anthropic_tool_result_images_collected():
    """tool_result content lists with image blocks: text joined into content,
    images collected into ToolResultPart.images."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": [
                 {"type": "text", "text": "screenshot attached"},
                 {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png",
                                              "data": "aGk="}},
             ]},
        ]}],
    })
    tr = req.messages[0].parts[0]
    assert isinstance(tr, ir.ToolResultPart)
    assert tr.content == "screenshot attached"
    assert len(tr.images) == 1
    assert tr.images[0].b64 == "aGk="
    assert tr.images[0].mime == "image/png"


def test_anthropic_tool_result_images_roundtrip():
    """ToolResultPart.images re-encode as block-form tool_result content
    (text + image blocks) on the Anthropic upstream."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": [
                 {"type": "text", "text": "screenshot attached"},
                 {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png",
                                              "data": "aGk="}},
             ]},
        ]}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    block = body["messages"][0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_1"
    assert isinstance(block["content"], list)
    kinds = [c["type"] for c in block["content"]]
    assert kinds == ["text", "image"]
    assert block["content"][0]["text"] == "screenshot attached"
    assert block["content"][1]["source"]["data"] == "aGk="


def test_openai_tool_message_image_content():
    """OpenAI tool messages with image content parts: text concatenated into
    ToolResultPart.content, images collected into .images."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "look"},
            {"role": "tool", "tool_call_id": "call_1",
             "content": [
                 {"type": "text", "text": "found a button"},
                 {"type": "image_url",
                  "image_url": {"url": "data:image/png;base64,aGk="}},
             ]},
        ],
    })
    tr = req.messages[1].parts[0]
    assert isinstance(tr, ir.ToolResultPart)
    assert tr.tool_use_id == "call_1"
    assert tr.content == "found a button"
    assert len(tr.images) == 1
    assert tr.images[0].b64 == "aGk="
    assert tr.images[0].mime == "image/png"


def test_openai_adapter_emits_multimodal_tool_result():
    """ToolResultPart with images crosses to OpenAI as content-parts form."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "look"},
            {"role": "tool", "tool_call_id": "call_1",
             "content": [
                 {"type": "text", "text": "found a button"},
                 {"type": "image_url",
                  "image_url": {"url": "data:image/png;base64,aGk="}},
             ]},
        ],
    })
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    tool_msg = next(m for m in body["messages"] if m.get("role") == "tool")
    assert isinstance(tool_msg["content"], list)
    kinds = [c["type"] for c in tool_msg["content"]]
    assert kinds == ["text", "image_url"]
    assert tool_msg["content"][0]["text"] == "found a button"
    assert tool_msg["content"][1]["image_url"]["url"] == "data:image/png;base64,aGk="
