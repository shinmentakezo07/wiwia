"""Provider adapter tests: OpenAI/Anthropic/Gemini encode + stream decode."""

import json

from wiwi.wire import openai_chat as oc
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter


def test_openai_encode_basic():
    req = oc.decode_request({"model": "x", "messages": [{"role": "user",
                                                         "content": "hi"}],
                             "max_tokens": 50})
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["model"] == "gpt-4o"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["max_tokens"] == 50


def test_openai_decode_response():
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "hello",
                                 "tool_calls": [{"id": "c1", "type": "function",
                                                 "function": {"name": "f",
                                                              "arguments": '{"a":1}'}}]},
                     "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                  "prompt_tokens_details": {"cached_tokens": 4},
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }
    turn = OpenAIAdapter().decode_response(200, json.dumps(payload).encode())
    assert turn.text == "hello"
    assert turn.tool_calls[0].args == {"a": 1}
    assert turn.stop_reason == "tool_call"
    assert turn.usage.cached_tokens == 4


def test_anthropic_stream_folding():
    ad = AnthropicAdapter()
    deltas = []
    events = [
        ("message_start", json.dumps({"type": "message_start", "message": {
            "model": "claude", "usage": {"input_tokens": 12}}}).replace(" ", "")),
        ("content_block_start", json.dumps({"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "text"}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta",
                                                      "text": "Hi"}})),
        ("content_block_start", json.dumps({"type": "content_block_start", "index": 1,
                                            "content_block": {"type": "tool_use",
                                                              "id": "tu1",
                                                              "name": "f"}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 1,
                                            "delta": {"type": "input_json_delta",
                                                      "partial_json": '{"x":'}})),
        ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 1,
                                            "delta": {"type": "input_json_delta",
                                                      "partial_json": '1}'}})),
        ("content_block_stop", json.dumps({"type": "content_block_stop", "index": 1})),
        ("message_delta", json.dumps({"type": "message_delta",
                                      "delta": {"stop_reason": "tool_use"},
                                      "usage": {"output_tokens": 7}})),
        ("message_stop", json.dumps({"type": "message_stop"})),
    ]
    for e, d in events:
        deltas.extend(ad.decode_stream_event(e, d))
    kinds = [type(x).__name__ for x in deltas]
    assert "StreamStart" in kinds
    assert "TextDelta" in kinds
    assert "ToolCallOpen" in kinds
    assert deltas[kinds.index("ToolCallOpen")].name == "f"
    args = "".join(x.args_fragment for x in deltas if type(x).__name__ == "ToolCallArgsDelta")
    assert args == '{"x":1}'
    assert "UsageFinal" in kinds
    assert "Finish" in kinds
    assert "StreamEnd" in kinds


def test_gemini_stream_decode():
    ad = GeminiAdapter()
    out = ad.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": [{"text": "he"}]}}]}))
    assert any(type(x).__name__ == "TextDelta" and x.text == "he" for x in out)
    out2 = ad.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": [{"text": "y"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}))
    kinds = [type(x).__name__ for x in out2]
    assert "UsageFinal" in kinds and "StreamEnd" in kinds


def test_anthropic_encode_request_defaults():
    from wiwi.wire import anthropic_messages as am
    req = am.decode_request({"model": "claude-sonnet-4-20250514",
                             "messages": [{"role": "user", "content": "hi"}]})
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["max_tokens"] == 4096  # mandatory default injection
    assert body["messages"][0]["content"][0]["text"] == "hi"
