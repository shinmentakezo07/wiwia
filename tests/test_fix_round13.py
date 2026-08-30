"""Round-13 regression tests: B.AI + DeepSeek thinking-mode quirks.

DeepSeek's thinking-mode API (on by default for DeepSeek models hosted
behind B.AI) has two behaviors that break naive OpenAI-compatible clients —
both documented at api-docs.deepseek.com/guides/thinking_mode and widely
reported as HTTP 400s from routers that rebuild history:

1. When a request carries ``tools``, the ``reasoning_content`` of ALL
   previous assistant turns must be passed back (even turns without a tool
   call), or the API rejects with
   ``The "reasoning_content" in the thinking mode must be passed back to the API``.
2. ``reasoning_effort`` has no "none" value; disabling thinking requires
   ``{"thinking": {"type": "disabled"}}`` (Anthropic/Responses "none").

These tests pin the adapter's handling of both, plus the non-DeepSeek path
(history reasoning stripped, plain reasoning_effort forwarded).
"""

from __future__ import annotations

import json

from wiwi.ir import types as ir
from wiwi.providers.bai_adapter import BAIAdapter, _is_deepseek

# -- model-id detection ---------------------------------------------------------


def test_is_deepseek_matches_prefixes_and_vendors():
    assert _is_deepseek("deepseek-reasoner")
    assert _is_deepseek("deepseek-v4-pro")
    assert _is_deepseek("deepseek/deepseek-v4-flash")
    assert _is_deepseek("DeepSeek-R1")
    assert not _is_deepseek("gpt-4o")
    assert not _is_deepseek("claude-sonnet-5")


def _req(model: str = "deepseek-v4-pro", tools: bool = True, **gen) -> ir.Request:
    msgs = [
        ir.Message(role="user", parts=[ir.TextPart("weather?")]),
    ]
    if "history_reasoning" in gen:
        msgs.insert(1, ir.Message(role="assistant", parts=[
            ir.ThinkingPart(gen.pop("history_reasoning")),
            ir.TextPart("checking…"),
        ]))
        msgs.append(ir.Message(role="user", parts=[ir.TextPart("and now?")]))
    req = ir.Request(
        model=model,
        messages=msgs,
        gen_params=ir.GenParams(**gen),
    )
    if tools:
        req.tools = [ir.Tool(
            name="get_weather", description="weather",
            parameters_json_schema={"type": "object", "properties": {
                "city": {"type": "string"}}},
        )]
    return req


# -- deepseek + tools: reasoning_content round-trip (the 400 fix) ----------------


def test_deepseek_with_tools_replays_history_reasoning():
    """History assistant thinking must reach the wire as reasoning_content."""
    body = BAIAdapter().encode_request(
        _req(history_reasoning="I should call get_weather"),
        "deepseek-v4-pro", {"provider_type": "bai"})
    assistants = [m for m in body["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["reasoning_content"] == "I should call get_weather"
    # text content still present alongside
    assert assistants[0]["content"] == "checking…"


def test_deepseek_with_tools_pads_missing_history_reasoning():
    """The actual 400: client rebuilt history without reasoning_content.
    Every assistant message must carry the field (empty string when absent)."""
    req = ir.Request(
        model="deepseek-v4-pro",
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("weather?")]),
            # assistant turn with NO thinking part (client stripped it)
            ir.Message(role="assistant", parts=[ir.TextPart("calling tool")]),
            ir.Message(role="user", parts=[ir.TextPart("thanks")]),
        ],
        tools=[ir.Tool(name="t", description="d",
                       parameters_json_schema={"type": "object"})],
    )
    body = BAIAdapter().encode_request(req, "deepseek-v4-pro", {"provider_type": "bai"})
    for m in body["messages"]:
        if m["role"] == "assistant":
            assert "reasoning_content" in m  # presence is what DeepSeek validates


def test_deepseek_with_tool_calls_history_keeps_reasoning():
    """The canonical failing shape: assistant message WITH tool_calls whose
    reasoning was stripped. Tool results also unaffected."""
    req = ir.Request(
        model="deepseek-reasoner",
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("weather?")]),
            ir.Message(role="assistant", parts=[
                ir.ToolUsePart(id="call_1", name="get_weather", args={"city": "Sz"}),
            ]),
            ir.Message(role="tool", parts=[
                ir.ToolResultPart(tool_use_id="call_1", content="Sunny 28C"),
            ]),
            ir.Message(role="user", parts=[ir.TextPart("now dress advice?")]),
        ],
        tools=[ir.Tool(name="get_weather", description="w",
                       parameters_json_schema={"type": "object"})],
    )
    body = BAIAdapter().encode_request(req, "deepseek-reasoner", {"provider_type": "bai"})
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == ""
    assert assistant["tool_calls"][0]["function"]["name"] == "get_weather"
    assert body["messages"][2] == {"role": "tool", "tool_call_id": "call_1",
                                   "content": "Sunny 28C"}


def test_deepseek_without_tools_strips_history_reasoning():
    """No tools -> API ignores history reasoning (and older R1 400'd on it);
    strict compatible path applies."""
    body = BAIAdapter().encode_request(
        _req(tools=False, history_reasoning="secret CoT"),
        "deepseek-v4-pro", {"provider_type": "bai"})
    for m in body["messages"]:
        assert "reasoning_content" not in m


def test_deepseek_with_tools_but_thinking_disabled_strips_reasoning():
    """thinking explicitly disabled (effort none): no round-trip constraint,
    history reasoning goes out clean."""
    req = ir.Request(
        model="deepseek-v4-pro",
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")]),
            ir.Message(role="assistant", parts=[
                ir.ThinkingPart("old CoT"), ir.TextPart("hello")]),
            ir.Message(role="user", parts=[ir.TextPart("more")]),
        ],
        tools=[ir.Tool(name="t", description="d",
                       parameters_json_schema={"type": "object"})],
        gen_params=ir.GenParams(reasoning_effort="none"),
    )
    body = BAIAdapter().encode_request(req, "deepseek-v4-pro", {"provider_type": "bai"})
    for m in body["messages"]:
        assert "reasoning_content" not in m
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


# -- deepseek reasoning controls --------------------------------------------------


def test_deepseek_effort_forwarded():
    body = BAIAdapter().encode_request(
        _req(reasoning_effort="max"), "deepseek-v4-pro", {"provider_type": "bai"})
    assert body["reasoning_effort"] == "max"


def test_deepseek_thinking_budget_maps_to_effort():
    body = BAIAdapter().encode_request(
        _req(thinking_budget=32000), "deepseek-v4-pro", {"provider_type": "bai"})
    assert body["reasoning_effort"] == "high"


def test_deepseek_disable_uses_thinking_toggle():
    body = BAIAdapter().encode_request(
        _req(reasoning_effort="none"), "deepseek-v4-pro", {"provider_type": "bai"})
    assert body["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in body


def test_deepseek_no_effort_no_thinking_key():
    body = BAIAdapter().encode_request(_req(), "deepseek-v4-pro", {"provider_type": "bai"})
    assert "reasoning_effort" not in body
    assert "thinking" not in body


# -- non-deepseek B.AI path unchanged ----------------------------------------------


def test_plain_bai_model_strips_history_reasoning():
    body = BAIAdapter().encode_request(
        _req(model="gpt-5.5", history_reasoning="x"),
        "gpt-5.5", {"provider_type": "bai"})
    for m in body["messages"]:
        assert "reasoning_content" not in m


def test_plain_bai_model_forwards_effort():
    body = BAIAdapter().encode_request(
        _req(model="gpt-5.5", reasoning_effort="high"),
        "gpt-5.5", {"provider_type": "bai"})
    assert body["reasoning_effort"] == "high"
    assert "thinking" not in body


# -- decode side: reasoning still captured for the client --------------------------


def test_decode_response_captures_reasoning_content():
    body = {
        "choices": [{"message": {"role": "assistant", "content": "Answer",
                                 "reasoning_content": "step step"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                  "completion_tokens_details": {"reasoning_tokens": 7}},
    }
    turn = BAIAdapter().decode_response(200, json.dumps(body).encode())
    assert turn.thinking[0].text == "step step"
    assert turn.usage.reasoning_tokens == 7
