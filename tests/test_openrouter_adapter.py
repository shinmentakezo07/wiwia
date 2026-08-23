"""Tests for the OpenRouter adapter: parameter translation, reasoning_details,
mid-stream errors, and cross-dialect routing."""

import json

from wiwi.ir import types as ir
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# -- encode: reasoning parameter translation -----------------------------------

def test_reasoning_effort_maps_to_reasoning_effort():
    """OpenAI reasoning_effort -> OpenRouter reasoning.effort."""
    req = oc.decode_request({
        "model": "openai/o3-mini",
        "messages": [{"role": "user", "content": "think"}],
        "reasoning_effort": "high",
    })
    body = OpenRouterAdapter().encode_request(req, "openai/o3-mini", {})
    assert body["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in body


def test_reasoning_effort_none_disables_reasoning():
    req = oc.decode_request({
        "model": "stealth/ox-alpha",
        "messages": [{"role": "user", "content": "quick"}],
        "reasoning_effort": "none",
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    assert body["reasoning"] == {"enabled": False}


def test_thinking_budget_maps_to_reasoning_max_tokens():
    """Anthropic thinking_budget -> OpenRouter reasoning.max_tokens."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 20000,
        "messages": [{"role": "user", "content": "think"}],
        "thinking": {"type": "enabled", "budget_tokens": 10000},
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    assert body["reasoning"] == {"max_tokens": 10000}


def test_thinking_budget_clamped_to_1024():
    """OpenRouter enforces min 1024 for reasoning.max_tokens (Anthropic models)."""
    req = ir.Request(
        model="~anthropic/claude-sonnet-latest",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=500),
    )
    body = OpenRouterAdapter().encode_request(req, "~anthropic/claude-sonnet-latest", {})
    assert body["reasoning"]["max_tokens"] >= 1024


def test_no_reasoning_config_omits_reasoning_key():
    req = ir.Request(
        model="openai/gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(),
    )
    body = OpenRouterAdapter().encode_request(req, "openai/gpt-4o", {})
    assert "reasoning" not in body
    assert "reasoning_effort" not in body


# -- encode: max_completion_tokens -------------------------------------------

def test_max_tokens_renamed_to_max_completion_tokens():
    """OpenRouter deprecates max_tokens in favor of max_completion_tokens."""
    req = ir.Request(
        model="stealth/ox-alpha",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(max_tokens=16384),
    )
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    assert "max_completion_tokens" in body
    assert body["max_completion_tokens"] == 16384
    assert "max_tokens" not in body


# -- encode: stream_options ---------------------------------------------------

def test_stream_options_not_sent_by_default():
    req = ir.Request(
        model="x", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        stream=True,
    )
    body = OpenRouterAdapter().encode_request(req, "x", {})
    assert "stream_options" not in body


def test_stream_options_sent_when_explicitly_requested():
    req = ir.Request(
        model="x", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        stream=True, stream_options_include_usage=True,
    )
    body = OpenRouterAdapter().encode_request(req, "x", {})
    assert body["stream_options"] == {"include_usage": True}


# -- decode: non-streaming response with reasoning_details -------------------

def test_decode_response_reasoning_string():
    """OpenRouter returns reasoning as a string field."""
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "answer",
                                 "reasoning": "step by step"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    turn = OpenRouterAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.text == "answer"
    assert turn.thinking[0].text == "step by step"


def test_decode_response_reasoning_details():
    """OpenRouter returns structured reasoning_details array."""
    payload = {
        "choices": [{"message": {
            "role": "assistant", "content": "answer",
            "reasoning_details": [
                {"type": "reasoning.text", "text": "thinking here",
                 "signature": "sig_abc"},
                {"type": "reasoning.summary", "summary": "Analyzed the problem"},
                {"type": "reasoning.encrypted", "data": "eyJlbmNy...",
                 "id": "rs_1"},
            ],
        }, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                  "completion_tokens_details": {"reasoning_tokens": 8},
                  "prompt_tokens_details": {"cached_tokens": 3}},
    }
    turn = OpenRouterAdapter().decode_response(200, json.dumps(payload).encode())
    assert len(turn.thinking) == 3
    assert turn.thinking[0].text == "thinking here"
    assert turn.thinking[0].signature == "sig_abc"
    assert turn.thinking[1].text == "Analyzed the problem"
    assert turn.thinking[2].text == "eyJlbmNy..."
    assert turn.usage.reasoning_tokens == 8
    assert turn.usage.cached_tokens == 3


def test_decode_response_finish_reason_error():
    """finish_reason='error' maps to 'stop' (error already surfaced)."""
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "partial"},
                     "finish_reason": "error"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    turn = OpenRouterAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.stop_reason == "stop"


# -- decode: streaming with reasoning ----------------------------------------

def test_stream_reasoning_string():
    """Streaming reasoning via delta.reasoning field."""
    ad = OpenRouterAdapter()
    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"reasoning": "thinking..."}}]}),
        json.dumps({"choices": [{"delta": {"content": "answer"}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        "[DONE]",
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    kinds = [type(d).__name__ for d in deltas_out]
    assert "ThinkingDelta" in kinds
    assert "TextDelta" in kinds
    assert "UsageFinal" in kinds
    assert "Finish" in kinds
    assert "StreamEnd" in kinds


def test_stream_reasoning_details():
    """Streaming reasoning via delta.reasoning_details array."""
    ad = OpenRouterAdapter()
    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"reasoning_details": [
            {"type": "reasoning.text", "text": "thinking", "signature": "sig1"}]}}]}),
        json.dumps({"choices": [{"delta": {"reasoning_details": [
            {"type": "reasoning.summary", "summary": "summary text"}]}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        "[DONE]",
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    thinking = [d for d in deltas_out if isinstance(d, dl.ThinkingDelta)]
    assert len(thinking) == 2
    assert thinking[0].text == "thinking"
    assert thinking[0].signature == "sig1"
    assert thinking[1].text == "summary text"


# -- decode: mid-stream error ------------------------------------------------

def test_stream_mid_stream_error():
    """OpenRouter mid-stream error: top-level error + finish_reason='error'."""
    ad = OpenRouterAdapter()
    deltas_out = []
    events = [
        json.dumps({"choices": [{"delta": {"content": "partial"}}]}),
        json.dumps({"error": {"code": "server_error",
                              "message": "Provider disconnected"},
                    "choices": [{"delta": {"content": ""},
                                 "finish_reason": "error"}]}),
    ]
    for ev in events:
        deltas_out.extend(ad.decode_stream_event("", ev))
    kinds = [type(d).__name__ for d in deltas_out]
    assert "TextDelta" in kinds
    assert "StreamError" in kinds
    err = [d for d in deltas_out if isinstance(d, dl.StreamError)]
    assert err[0].message == "Provider disconnected"


# -- cross-dialect: Anthropic client -> OpenRouter provider ------------------

def test_cross_dialect_anthropic_to_openrouter():
    """Claude Code sends thinking budget; OpenRouter receives reasoning.max_tokens."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 16384,
        "messages": [{"role": "user", "content": "think hard"}],
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "stream": True,
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    assert body["reasoning"] == {"max_tokens": 10000}
    assert body["max_completion_tokens"] == 16384
    assert "max_tokens" not in body
    assert "reasoning_effort" not in body
    assert "stream_options" not in body


def test_cross_dialect_openai_to_openrouter():
    """OpenAI client sends reasoning_effort; OpenRouter receives reasoning.effort."""
    req = oc.decode_request({
        "model": "stealth/ox-alpha",
        "messages": [{"role": "user", "content": "think"}],
        "reasoning_effort": "high",
        "stream": True,
        "stream_options": {"include_usage": True},
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    assert body["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in body
    assert body["stream_options"] == {"include_usage": True}


# -- registry -----------------------------------------------------------------

def test_registry_returns_openrouter_adapter():
    from wiwi.providers.registry import get_adapter
    adapter = get_adapter("openrouter")
    assert isinstance(adapter, OpenRouterAdapter)


def test_registry_rejects_unknown_type():
    """Unknown provider types must raise, not silently fall back to OpenAI."""
    import pytest

    from wiwi.providers.registry import get_adapter
    with pytest.raises(ValueError, match="unsupported provider type"):
        get_adapter("unknown-type")


# -- multi-turn conversation handling (the 400 bug) -------------------------

def test_multi_turn_thinking_preserved():
    """Assistant thinking blocks from prior turns must be preserved as
    ``reasoning`` on the message, not silently dropped."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 16384,
        "messages": [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Let me calculate...",
                 "signature": "sig_abc"},
                {"type": "text", "text": "The answer is 4."},
            ]},
            {"role": "user", "content": "What about 3+3?"},
        ],
        "stream": True,
        "thinking": {"type": "enabled", "budget_tokens": 10000},
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    msgs = body["messages"]
    # The assistant message should have reasoning field
    assistant_msg = next(m for m in msgs if m["role"] == "assistant")
    assert assistant_msg["reasoning"] == "Let me calculate..."
    assert assistant_msg["content"] == "The answer is 4."


def test_multi_turn_tool_result_no_empty_user_message():
    """Tool results from Anthropic-style user messages must be converted to
    role=tool messages without leaving an empty trailing user message."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 16384,
        "messages": [
            {"role": "user", "content": "Read the file"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_01", "name": "Read",
                 "input": {"path": "/tmp/test"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01",
                 "content": "file contents"},
            ]},
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "Thanks!"},
        ],
        "stream": True,
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    msgs = body["messages"]
    # Should have: system(skip, no system here), user, assistant, tool, assistant, user
    roles = [m["role"] for m in msgs]
    assert "tool" in roles
    assert roles.count("tool") == 1
    # No empty user messages (content: null or "" without tool_calls)
    for m in msgs:
        if m["role"] == "user":
            assert m["content"] not in (None, ""), f"Empty user message: {m}"
    # The tool result should be a proper tool message
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "toolu_01"
    assert tool_msg["content"] == "file contents"


def test_multi_turn_no_null_content_without_tool_calls():
    """No message should have content: null unless it has tool_calls."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 16384,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm"},
            ]},
            {"role": "user", "content": "ok"},
        ],
        "stream": True,
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    for m in body["messages"]:
        if m.get("content") is None and "tool_calls" not in m:
            assert False, f"Message has null content without tool_calls: {m}"


def test_multi_turn_tool_result_with_text():
    """When a user message has both text and tool_result, both should be
    emitted correctly."""
    req = am.decode_request({
        "model": "stealth/ox-alpha",
        "max_tokens": 16384,
        "messages": [
            {"role": "user", "content": "Read the file"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_01", "name": "Read",
                 "input": {"path": "/tmp/test"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01",
                 "content": "file contents"},
                {"type": "text", "text": "and then tell me the size"},
            ]},
        ],
        "stream": True,
    })
    body = OpenRouterAdapter().encode_request(req, "stealth/ox-alpha", {})
    msgs = body["messages"]
    # Should have tool message + user message with the text
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    user_msgs = [m for m in msgs if m["role"] == "user"]
    # First user = "Read the file", second user = "and then tell me the size"
    assert user_msgs[-1]["content"] == "and then tell me the size"
