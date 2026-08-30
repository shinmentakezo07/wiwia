"""Round-12 regression tests: B.AI built-in provider adapter.

B.AI (https://docs.b.ai/llmservice) exposes a unified LLM API at
https://api.b.ai/v1 that is OpenAI Chat Completions compatible, so the
adapter delegates encoding to the OpenAI adapter. What must hold:

- `bai` is a first-class provider type across every surface (config,
  registry, catalog, default base URL).
- History assistant messages must NOT carry `reasoning_content` (B.AI is
  an OpenAI-compatible gateway, not native OpenAI) while reasoning_effort
  IS forwarded (B.AI hosts reasoning models that report reasoning tokens).
- Response/stream decoding (text, reasoning_content, tool calls, usage)
  rides on the OpenAI adapter and must work for B.AI-shaped payloads.
"""

from __future__ import annotations

import json

from wiwi.config import PROVIDER_TYPES
from wiwi.ir import types as ir
from wiwi.providers.bai_adapter import BAIAdapter
from wiwi.providers.registry import get_adapter
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc

# -- registration across surfaces ---------------------------------------------


def test_bai_in_provider_types():
    assert "bai" in PROVIDER_TYPES


def test_registry_returns_bai_adapter():
    adapter = get_adapter("bai")
    assert isinstance(adapter, BAIAdapter)
    assert adapter.provider_type == "bai"


def test_default_base_url_is_bai_docs_url():
    from wiwi.router.router import _default_base_url
    assert _default_base_url("bai") == "https://api.b.ai/v1"


def test_bai_catalog_card():
    from wiwi.router.router import BUILTIN_PROVIDER_TYPES
    card = next(p for p in BUILTIN_PROVIDER_TYPES
                if p["provider_type"] == "bai")
    assert card["label"] == "B.AI"
    assert card["default_base_url"] == "https://api.b.ai/v1"
    assert "docs.b.ai" in card["docs_url"]
    # Default model ids surfaced for one-click deploy in the admin UI.
    assert "deepseek-v4-flash" in card["latest_models"]
    assert "deepseek-v4-flash-vision-exp" in card["latest_models"]


# -- request encoding ----------------------------------------------------------


def _bai_request(**gen) -> ir.Request:
    return ir.Request(
        model="bai-model",
        messages=[
            ir.Message(role="system", parts=[ir.TextPart("be brief")]),
            ir.Message(role="user", parts=[ir.TextPart("hi")]),
        ],
        gen_params=ir.GenParams(**gen),
    )


def test_encode_request_basic_shape():
    body = BAIAdapter().encode_request(
        _bai_request(), "my-model", {"provider_type": "bai"})
    assert body["model"] == "my-model"
    assert body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "hi"


def test_encode_request_omits_history_reasoning_content():
    """B.AI is an OpenAI-compatible gateway: history assistant messages must
    not carry reasoning_content (native OpenAI accepts it, compatible
    gateways reject the unrecognized field)."""
    req = ir.Request(
        model="bai-model",
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")]),
            ir.Message(role="assistant", parts=[
                ir.ThinkingPart("hmm"),
                ir.TextPart("hello"),
            ]),
            ir.Message(role="user", parts=[ir.TextPart("more")]),
        ],
    )
    body = BAIAdapter().encode_request(req, "my-model", {"provider_type": "bai"})
    for m in body["messages"]:
        assert "reasoning_content" not in m
    # text content survived
    assert body["messages"][1]["content"] == "hello"


def test_encode_request_forwards_reasoning_effort():
    """Unlike strict compatible gateways, B.AI accepts reasoning_effort."""
    body = BAIAdapter().encode_request(
        _bai_request(reasoning_effort="high"), "my-model", {"provider_type": "bai"})
    assert body["reasoning_effort"] == "high"


def test_encode_request_maps_thinking_budget_to_effort():
    """Anthropic-dialect thinking_budget is normalized by the IR and
    forwarded as reasoning_effort."""
    body = BAIAdapter().encode_request(
        _bai_request(thinking_budget=32000), "my-model", {"provider_type": "bai"})
    assert body["reasoning_effort"] == "high"


def test_encode_request_no_reasoning_by_default():
    body = BAIAdapter().encode_request(_bai_request(), "my-model", {"provider_type": "bai"})
    assert "reasoning_effort" not in body
    assert "reasoning" not in body


def test_encode_request_tools_and_tool_choice():
    req = ir.Request(
        model="bai-model",
        messages=[ir.Message(role="user", parts=[ir.TextPart("weather?")])],
        tools=[ir.Tool(
            name="get_weather", description="Get weather",
            parameters_json_schema={"type": "object", "properties": {
                "city": {"type": "string"}}},
        )],
        tool_choice=ir.ToolChoiceAuto(),
    )
    body = BAIAdapter().encode_request(req, "my-model", {"provider_type": "bai"})
    assert body["tools"][0]["function"]["name"] == "get_weather"
    assert body["tool_choice"] == "auto"


def test_encode_request_stream_options_only_when_client_asked():
    body = BAIAdapter().encode_request(
        _bai_request(), "my-model", {"provider_type": "bai"})
    assert "stream_options" not in body


def test_encode_request_rejects_extra_body_kept():
    """deployment extra_body passes through (setdefault semantics)."""
    params = {"provider_type": "bai", "extra_body": {"user": "u-42"}}
    body = BAIAdapter().encode_request(_bai_request(), "my-model", params)
    assert body["user"] == "u-42"


def test_headers_use_bearer_auth():
    from wiwi.providers.base import ProviderKeyRef
    h = BAIAdapter().headers(ProviderKeyRef(label="main", secret="sk-bai-test"))
    assert h["Authorization"] == "Bearer sk-bai-test"


def test_build_url_appends_chat_completions():
    a = BAIAdapter()
    assert a.build_url("https://api.b.ai/v1", "m", False) == "https://api.b.ai/v1/chat/completions"
    assert a.build_url("https://api.b.ai/v1/", "m", True) == "https://api.b.ai/v1/chat/completions"


# -- response decoding ----------------------------------------------------------


def test_decode_response_text_and_usage():
    body = {
        "id": "chatcmpl-x", "object": "chat.completion",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "Hello!"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }
    turn = BAIAdapter().decode_response(200, json.dumps(body).encode())
    assert turn.text == "Hello!"
    assert turn.stop_reason == "stop"
    assert turn.usage.prompt_tokens == 12
    assert turn.usage.completion_tokens == 8
    assert turn.tool_calls == []


def test_decode_response_reasoning_and_tool_calls():
    body = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning_content": "thinking about it",
                "tool_calls": [{
                    "id": "call_01", "type": "function",
                    "function": {"name": "get_weather",
                                 "arguments": '{"city":"Shenzhen"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                  "completion_tokens_details": {"reasoning_tokens": 3}},
    }
    turn = BAIAdapter().decode_response(200, json.dumps(body).encode())
    assert turn.thinking[0].text == "thinking about it"
    assert turn.stop_reason == "tool_call"
    assert turn.tool_calls[0].name == "get_weather"
    assert turn.tool_calls[0].args == {"city": "Shenzhen"}
    assert turn.usage.reasoning_tokens == 3


# -- streaming decoding -----------------------------------------------------------


def _fresh() -> BAIAdapter:
    return BAIAdapter()


def test_stream_text_and_reasoning_deltas():
    a = _fresh()
    out = a.decode_stream_event("", json.dumps({
        "choices": [{"delta": {"content": "Hi", "reasoning_content": "hmm"}}],
    }))
    assert dl.TextDelta(text="Hi") in out
    assert dl.ThinkingDelta(text="hmm") in out


def test_stream_tool_call_open_args_close():
    a = _fresh()
    out = []
    out += a.decode_stream_event("", json.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "f", "arguments": ""}}]}}],
    }))
    out += a.decode_stream_event("", json.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"x":'}}]}}],
    }))
    out += a.decode_stream_event("", json.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "1}"}}]}}],
    }))
    out += a.decode_stream_event("", json.dumps({
        "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
    }))
    kinds = [type(d) for d in out]
    assert kinds.count(dl.ToolCallOpen) == 1
    assert kinds.count(dl.ToolCallArgsDelta) == 2
    assert kinds.count(dl.ToolCallClose) == 1
    open_delta = next(d for d in out if isinstance(d, dl.ToolCallOpen))
    assert open_delta.id == "call_1" and open_delta.name == "f"
    # args fragments concatenate to the full arguments
    args = "".join(d.args_fragment for d in out if isinstance(d, dl.ToolCallArgsDelta))
    assert json.loads(args) == {"x": 1}
    assert out[-1] == dl.Finish("tool_call")


def test_stream_usage_final_and_done():
    a = _fresh()
    out = a.decode_stream_event("", json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3,
                  "prompt_tokens_details": {"cached_tokens": 2}},
    }))
    assert out == [dl.UsageFinal(prompt=7, cached=2, reasoning=0, output=3)]
    assert a.decode_stream_event("", "[DONE]") == [dl.StreamEnd()]


def test_stream_full_pass_roundtrip_via_wire():
    """A complete B.AI SSE chunk sequence decodes into a legal IR delta
    sequence that the inbound wire encoders can render."""
    a = _fresh()
    deltas: list[dl.IRStreamDelta] = [dl.StreamStart(model="bai-model")]
    for data in [
        json.dumps({"choices": [{"delta": {"reasoning_content": "hmm"}}]}),
        json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        json.dumps({"choices": [], "usage": {"prompt_tokens": 4,
                                             "completion_tokens": 2}}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "[DONE]",
    ]:
        deltas += a.decode_stream_event("", data)
    # legality: exactly one UsageFinal, after last content delta, then Finish,
    # then StreamEnd (the streaming contract).
    kinds = [type(d) for d in deltas]
    assert kinds.count(dl.UsageFinal) == 1
    assert kinds.index(dl.UsageFinal) > max(
        i for i, d in enumerate(deltas) if isinstance(d, dl.TextDelta))
    assert kinds[-2:] == [dl.Finish, type(deltas[-1])]
    assert deltas[-1] == dl.StreamEnd()
    # And the OpenAI chat wire encoder can render the stream for a chat client.
    enc = oc.ChatStreamEncoder(model="bai-model", req_id="test")
    frames = [f for f in (enc.feed(d) for d in deltas) if f]
    assert frames, "encoder must produce chat.completion.chunk frames"
    assert all(b"data: " in f for f in frames)
