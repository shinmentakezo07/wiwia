"""Codec round-trip tests: decode -> encode for all three dialects."""

import json

from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


def test_openai_chat_basic_decode():
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        "temperature": 0.5,
        "max_tokens": 100,
    })
    assert req.messages[0].parts[0].text == "be brief"
    assert req.messages[1].parts[0].text == "hi"
    assert req.gen_params.temperature == 0.5
    assert req.gen_params.max_tokens == 100
    assert not req.stream


def test_openai_chat_tool_roundtrip():
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                                                  "function": {"name": "get_weather",
                                                               "arguments": '{"city":"SF"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny 20C"},
        ],
        "tools": [{"type": "function", "function": {"name": "get_weather",
                                                    "description": "w",
                                                    "parameters": {"type": "object"}}}],
    })
    assistant = req.messages[1]
    assert assistant.parts[0].name == "get_weather"
    assert assistant.parts[0].args == {"city": "SF"}
    tool_msg = req.messages[2]
    assert tool_msg.parts[0].tool_use_id == "call_1"
    assert req.tools[0].name == "get_weather"


def test_anthropic_decode_system_and_tools():
    req = am.decode_request({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": "be brief",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "weather?",
                                          "cache_control": {"type": "ephemeral"}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1",
                                               "name": "get_weather",
                                               "input": {"city": "SF"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1",
                                          "content": "sunny"}]},
        ],
        "tools": [{"name": "get_weather", "description": "w",
                   "input_schema": {"type": "object"}}],
    })
    assert req.messages[0].parts[0].text == "be brief"
    assert req.messages[1].parts[0].cache_control == {"type": "ephemeral"}
    assert req.messages[2].parts[0].name == "get_weather"
    assert req.messages[3].parts[0].content == "sunny"


def test_anthropic_stream_encoder_sequence():
    from wiwi.streaming import deltas as dl
    enc = am.AnthropicStreamEncoder("claude-x", "abc123")
    frames = []
    for d in [dl.StreamStart("claude-x"), dl.TextDelta("Hel"), dl.TextDelta("lo"),
              dl.ToolCallOpen(0, "tu_9", "f"), dl.ToolCallArgsDelta(0, '{"a":'),
              dl.ToolCallArgsDelta(0, '1}'), dl.ToolCallClose(0),
              dl.UsageFinal(prompt=10, output=5), dl.Finish("tool_call"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk.decode())
    frames.append(enc.final_frame().decode())  # message_delta w/ stop_reason + usage
    blob = "".join(frames)
    assert "message_start" in blob
    assert "text_delta" in blob and '"Hel"' in blob
    assert "tool_use" in blob and "tu_9" in blob
    assert "input_json_delta" in blob
    assert "message_delta" in blob and '"tool_use"' in blob  # stop_reason
    assert "message_stop" in blob
    # blocks: text(1) + tool_use(1); each start = one "event: content_block_start" line
    assert blob.count("event: content_block_start") == 2


def test_chat_stream_encoder_tool_args():
    from wiwi.streaming import deltas as dl
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    frames = []
    for d in [dl.StreamStart("gpt-4o"), dl.ToolCallOpen(0, "call_1", "f"),
              dl.ToolCallArgsDelta(0, '{"x":'), dl.ToolCallArgsDelta(0, "1}"),
              dl.ToolCallClose(0),
              dl.UsageFinal(prompt=8, output=3), dl.Finish("tool_call"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk)
    frames.append(enc.final_frame())  # usage+finish frame, then caller sends [DONE]
    frames.append(b"data: [DONE]\n\n")
    blob = b"".join(frames).decode()
    assert '"tool_calls"' in blob
    assert '"name":"f"' in blob.replace(" ", "") or '"name": "f"' in blob
    assert "[DONE]" in blob
    # final usage frame carries token counts
    assert "prompt_tokens" in blob.split("[DONE]")[0][-600:]

def test_responses_decode_stateless():
    req = orp.decode_request({
        "model": "claude-sonnet",
        "instructions": "be terse",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "hi"}]},
        ],
        "tools": [{"type": "function", "name": "f", "description": "",
                   "parameters": {"type": "object"}}],
    })
    assert req.messages[0].parts[0].text == "be terse"
    assert req.messages[1].parts[0].text == "hi"
    assert req.tools[0].name == "f"


def test_responses_rejects_previous_response_id():
    import pytest
    with pytest.raises(oc.DialectError):
        orp.decode_request({"model": "x", "previous_response_id": "resp_old",
                            "input": []})
