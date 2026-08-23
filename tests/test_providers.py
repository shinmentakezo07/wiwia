"""Provider adapter tests: OpenAI/Anthropic/Gemini encode + stream decode."""

import json

from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.wire import openai_chat as oc


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


def _vision_req(parts):
    from wiwi.ir import types as ir
    return ir.Request(model="x", messages=[ir.Message(role="user", parts=parts)])


def test_openai_encode_text_then_image():
    from wiwi.ir import types as ir
    body = OpenAIAdapter().encode_request(_vision_req([
        ir.TextPart("look"), ir.ImagePart(url="https://x/i.png")]), "gpt-4o", {})
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1] == {"type": "image_url", "image_url": {"url": "https://x/i.png"}}


def test_openai_encode_image_then_text():
    """Regression: TextPart after an ImagePart must not raise TypeError (list+str)."""
    from wiwi.ir import types as ir
    body = OpenAIAdapter().encode_request(_vision_req([
        ir.ImagePart(url="https://x/i.png"), ir.TextPart("what is this?")]),
        "gpt-4o", {})
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "image_url", "image_url": {"url": "https://x/i.png"}}
    assert content[1] == {"type": "text", "text": "what is this?"}


def test_gemini_function_response_uses_tool_name():
    """Regression: functionResponse.name must be the function name, resolved from
    the matching ToolUsePart in history — not the raw tool_use_id."""
    from wiwi.ir import types as ir
    req = ir.Request(model="gemini", messages=[
        ir.Message(role="assistant", parts=[
            ir.ToolUsePart(id="call_abc123", name="get_weather", args={"city": "SF"})]),
        ir.Message(role="tool", parts=[
            ir.ToolResultPart(tool_use_id="call_abc123", content='{"temp": 20}')]),
    ])
    body = GeminiAdapter().encode_request(req, "gemini-2.0-flash", {})
    fr = body["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["name"] == "get_weather"


def test_gemini_function_response_falls_back_to_call_prefix_strip():
    """No ToolUsePart in history (pruned context): strip the synthetic call_ prefix."""
    from wiwi.ir import types as ir
    req = ir.Request(model="gemini", messages=[
        ir.Message(role="tool", parts=[
            ir.ToolResultPart(tool_use_id="call_getwx", content="ok")]),
    ])
    body = GeminiAdapter().encode_request(req, "gemini-2.0-flash", {})
    fr = body["contents"][-1]["parts"][0]["functionResponse"]
    assert fr["name"] == "getwx"


# -- reasoning effort cross-provider mapping -----------------------------------

def test_effort_to_thinking_budget_mapping():
    from wiwi.ir.types import effort_to_thinking_budget, thinking_budget_to_effort
    assert effort_to_thinking_budget("low") == 1024
    assert effort_to_thinking_budget("medium") == 8000
    assert effort_to_thinking_budget("high") == 32000
    assert thinking_budget_to_effort(1024) == "low"
    assert thinking_budget_to_effort(5000) == "medium"
    assert thinking_budget_to_effort(32000) == "high"


def test_gen_params_effective_effort():
    from wiwi.ir.types import GenParams
    # reasoning_effort set directly
    g = GenParams(reasoning_effort="high")
    assert g.effective_reasoning_effort() == "high"
    assert g.effective_thinking_budget() == 32000
    # thinking_budget set directly
    g = GenParams(thinking_budget=1024)
    assert g.effective_thinking_budget() == 1024
    assert g.effective_reasoning_effort() == "low"
    # neither set
    g = GenParams()
    assert g.effective_reasoning_effort() is None
    assert g.effective_thinking_budget() is None
    # both set: direct value wins
    g = GenParams(reasoning_effort="low", thinking_budget=32000)
    assert g.effective_reasoning_effort() == "low"
    assert g.effective_thinking_budget() == 32000


def test_openai_adapter_maps_thinking_budget_to_effort():
    """Client sent thinking_budget (Anthropic dialect) routed to OpenAI provider."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="gpt-4o", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams(thinking_budget=32000))
    body = OpenAIAdapter().encode_request(req, "o3", {})
    assert body["reasoning_effort"] == "high"


def test_openai_adapter_passes_effort_directly():
    """Client sent reasoning_effort (OpenAI dialect) routed to OpenAI provider."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="o3", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams(reasoning_effort="low"))
    body = OpenAIAdapter().encode_request(req, "o3", {})
    assert body["reasoning_effort"] == "low"


def test_anthropic_adapter_maps_effort_to_thinking_budget():
    """Client sent reasoning_effort (OpenAI dialect) routed to Anthropic provider."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="claude", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams(reasoning_effort="medium"))
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["thinking"]["type"] == "enabled"
    assert body["thinking"]["budget_tokens"] == 8000


def test_anthropic_adapter_passes_thinking_budget_directly():
    """Client sent thinking_budget (Anthropic dialect) routed to Anthropic provider."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="claude", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams(thinking_budget=5000))
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["thinking"]["budget_tokens"] == 5000


def test_gemini_adapter_maps_effort_to_thinking_budget():
    """Client sent reasoning_effort (OpenAI dialect) routed to Gemini provider."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="gemini", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams(reasoning_effort="high"))
    body = GeminiAdapter().encode_request(req, "gemini-2.0-flash", {})
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 32000


def test_gemini_adapter_maps_thinking_budget_directly():
    """Client sent thinking_budget (Anthropic dialect) routed to Gemini provider."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="gemini", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams(thinking_budget=1024))
    body = GeminiAdapter().encode_request(req, "gemini-2.0-flash", {})
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 1024


def test_no_reasoning_config_means_no_thinking_key():
    """No reasoning_effort or thinking_budget → no thinking/reasoning keys in body."""
    from wiwi.ir.types import GenParams, Message, Request, TextPart
    req = Request(model="gpt-4o", messages=[Message(role="user", parts=[TextPart("hi")])],
                  gen_params=GenParams())
    oai_body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert "reasoning_effort" not in oai_body
    ant_body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert "thinking" not in ant_body
    gem_body = GeminiAdapter().encode_request(req, "gemini-2.0-flash", {})
    assert "thinkingConfig" not in gem_body.get("generationConfig", {})


def test_openai_chat_decode_captures_reasoning_effort():
    """Wire codec captures reasoning_effort from client request into IR."""
    req = oc.decode_request({"model": "o3", "messages": [{"role": "user",
                                                          "content": "think"}],
                             "reasoning_effort": "high"})
    assert req.gen_params.reasoning_effort == "high"


def test_anthropic_decode_captures_thinking_budget():
    """Wire codec captures thinking.budget_tokens from client request into IR."""
    from wiwi.wire import anthropic_messages as am
    req = am.decode_request({"model": "claude-sonnet-4-20250514",
                             "messages": [{"role": "user", "content": "hi"}],
                             "thinking": {"type": "enabled", "budget_tokens": 8000}})
    assert req.gen_params.thinking_budget == 8000
