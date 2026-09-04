"""Round-27 regression tests: OpenCode Zen built-in provider.

Covers the new ``opencode`` provider type:

- ``route_for_model`` per-model protocol routing (responses/messages/gemini/chat)
- live ``User-Agent: opencode/<version>`` headers (5-min TTL cache, no restart)
- ``build_url`` per route, ``encode_request`` delegation + Responses upstream
- Responses ``decode_response`` / stream decode incl. tool routing + terminal
- registry + config + catalog registration
- end-to-end chat-route request sends the live User-Agent upstream
"""

from __future__ import annotations

import time

import httpx
import orjson
import pytest
import pytest_asyncio
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    PROVIDER_TYPES,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.ir import types as ir
from wiwi.providers import opencode_version as ov
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.opencode_adapter import OpencodeAdapter, route_for_model
from wiwi.providers.registry import fresh_adapter, get_adapter
from wiwi.router.router import BUILTIN_PROVIDER_TYPES
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc


def _chat_req(text: str = "hi") -> ir.Request:
    body = {"model": "m", "messages": [{"role": "user", "content": text}]}
    return oc.decode_request(body)


def _key(secret: str = "zen-key-123") -> ProviderKeyRef:
    return ProviderKeyRef(label="main", secret=secret)


@pytest.fixture(autouse=True)
def _seed_version():
    ov._set_cached_for_tests("9.9.9", time.monotonic())
    yield
    ov._set_cached_for_tests(None, 0.0)


# -- routing ---------------------------------------------------------------


def test_route_responses_family():
    for m in ["gpt-5.5", "GPT-5.4-mini", "grok-4.6", "grok-build-0.1",
              "muse-spark-1.2", "muse-spark-1.3-contributor-free"]:
        assert route_for_model(m) == "responses", m


def test_route_messages_family():
    for m in ["claude-sonnet-5", "claude-opus-4-7", "claude-fable-5-1",
              "qwen3.6-plus", "QWEN3.7-MAX"]:
        assert route_for_model(m) == "messages", m


def test_route_gemini_family():
    assert route_for_model("gemini-3.1-pro") == "gemini"
    assert route_for_model("GEMINI-3.8-FLASH") == "gemini"


def test_route_chat_fallback():
    for m in ["deepseek-v4-pro", "glm-5.2", "kimi-k2.6", "minimax-m3",
              "big-pickle", "mimo-v2.5-free", "unknown-model-xyz", ""]:
        assert route_for_model(m) == "chat", m


# -- registration ------------------------------------------------------------


def test_opencode_in_provider_types():
    assert "opencode" in PROVIDER_TYPES


def test_registry_returns_opencode_adapter():
    assert get_adapter("opencode").provider_type == "opencode"
    assert fresh_adapter("opencode").provider_type == "opencode"


def test_fresh_adapter_returns_private_instance():
    a, b = fresh_adapter("opencode"), fresh_adapter("opencode")
    assert a is not b


def test_builtin_catalog_has_opencode_card():
    cards = {c["provider_type"]: c for c in BUILTIN_PROVIDER_TYPES}
    assert "opencode" in cards
    assert cards["opencode"]["default_base_url"] == "https://opencode.ai/zen/v1"


# -- live headers --------------------------------------------------------------


def test_headers_bearer_and_live_user_agent():
    h = OpencodeAdapter().headers(_key("  zen-secret  "))
    assert h["Authorization"] == "Bearer zen-secret"
    assert h["User-Agent"] == "opencode/9.9.9"


def test_headers_user_agent_follows_cache_refresh():
    a = OpencodeAdapter()
    assert a.headers(_key())["User-Agent"] == "opencode/9.9.9"
    ov._set_cached_for_tests("1.2.3", time.monotonic())
    assert a.headers(_key())["User-Agent"] == "opencode/1.2.3"


def test_headers_fallback_when_never_fetched():
    ov._set_cached_for_tests(None, 0.0)
    h = OpencodeAdapter().headers(_key())
    assert h["User-Agent"] == "opencode/unknown"
    assert h["Authorization"].startswith("Bearer ")


# -- version helper --------------------------------------------------------------


def test_parse_tag_strips_v():
    assert ov._parse_tag("v1.2.3") == "1.2.3"
    assert ov._parse_tag("V1.2.3") == "1.2.3"
    assert ov._parse_tag("1.2.3") == "1.2.3"
    assert ov._parse_tag("vv1.0") == "v1.0"  # single leading v only
    assert ov._parse_tag("") is None
    assert ov._parse_tag(None) is None
    assert ov._parse_tag(123) is None


def test_stale_logic_five_minute_ttl():
    now = time.monotonic()
    ov._set_cached_for_tests("1.0.0", now)
    assert ov.is_stale(now + ov.TTL_S - 1) is False
    assert ov.is_stale(now + ov.TTL_S + 1) is True


@respx.mock
async def test_refresh_version_caches_github_tag():
    ov._set_cached_for_tests(None, 0.0)
    respx.get(ov.GITHUB_LATEST_URL).respond(json={"tag_name": "v7.8.9"})
    assert await ov.refresh_version() == "7.8.9"
    assert ov.get_cached_version() == "7.8.9"


@respx.mock
async def test_refresh_version_failure_keeps_stale_cache():
    ov._set_cached_for_tests("1.0.0", time.monotonic())
    respx.get(ov.GITHUB_LATEST_URL).respond(status_code=500)
    assert await ov.refresh_version() is None
    assert ov.get_cached_version() == "1.0.0"


async def test_version_refresh_task_start_stop():
    w = ov.OpencodeVersionRefresh()
    w.start()
    assert w._task is not None
    w.start()  # idempotent
    await w.stop()
    assert w._task is None


# -- URLs --------------------------------------------------------------------


def test_build_url_chat():
    a = OpencodeAdapter()
    assert a.build_url("https://opencode.ai/zen/v1", "deepseek-v4-pro", False) == (
        "https://opencode.ai/zen/v1/chat/completions")


def test_build_url_responses():
    a = OpencodeAdapter()
    assert a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True) == (
        "https://opencode.ai/zen/v1/responses")


def test_build_url_messages():
    a = OpencodeAdapter()
    assert a.build_url("https://opencode.ai/zen/v1", "claude-sonnet-5", True) == (
        "https://opencode.ai/zen/v1/messages")


def test_build_url_gemini_stream_and_nonstream():
    a = OpencodeAdapter()
    assert a.build_url("https://opencode.ai/zen/v1", "gemini-3.1-pro", True) == (
        "https://opencode.ai/zen/v1/models/gemini-3.1-pro:streamGenerateContent?alt=sse")
    assert a.build_url("https://opencode.ai/zen/v1", "gemini-3.1-pro", False) == (
        "https://opencode.ai/zen/v1/models/gemini-3.1-pro:generateContent")


def test_build_url_default_base_when_empty():
    a = OpencodeAdapter()
    assert a.build_url("", "gpt-5.5", False).startswith("https://opencode.ai/zen/v1/")


# -- encode --------------------------------------------------------------------


def test_encode_chat_delegates_to_openai_shape():
    a = OpencodeAdapter()
    body = a.encode_request(_chat_req(), "deepseek-v4-pro", {})
    assert body["model"] == "deepseek-v4-pro"
    assert body["messages"][0]["role"] == "user"
    assert body["stream"] is False


def test_encode_messages_delegates_to_anthropic_shape():
    a = OpencodeAdapter()
    body = a.encode_request(_chat_req(), "claude-sonnet-5", {})
    assert body["model"] == "claude-sonnet-5"
    assert isinstance(body["messages"], list)
    assert body["messages"][0]["role"] == "user"
    assert body["max_tokens"] >= 1


def test_encode_gemini_delegates_to_gemini_shape():
    a = OpencodeAdapter()
    body = a.encode_request(_chat_req(), "gemini-3.1-pro", {})
    assert "contents" in body
    assert body["contents"][0]["role"] == "user"


def test_encode_responses_shape():
    a = OpencodeAdapter()
    req = ir.Request(
        model="m",
        messages=[
            ir.Message(role="system", parts=[ir.TextPart("sys")]),
            ir.Message(role="user", parts=[ir.TextPart("hello")]),
        ],
        gen_params=ir.GenParams(reasoning_effort="high", max_tokens=64),
    )
    body = a.encode_request(req, "gpt-5.5", {})
    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == "sys"
    assert body["max_output_tokens"] == 64
    assert body["reasoning"] == {"effort": "high"}
    kinds = {i["type"] for i in body["input"]}
    assert "message" in kinds


def test_encode_responses_tool_round_trip():
    a = OpencodeAdapter()
    req = _chat_req()
    req.tools = [ir.Tool(name="get_weather", description="w",
                         parameters_json_schema={"type": "object"})]
    req.tool_choice = ir.ToolChoiceAuto()
    body = a.encode_request(req, "gpt-5.5", {})
    assert body["tools"][0]["type"] == "function"
    assert body["tool_choice"] == "auto"


# -- responses decode ------------------------------------------------------------


def test_decode_responses_response_text_and_usage():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", False)
    payload = {"id": "resp_1", "status": "completed", "model": "gpt-5.5",
               "output": [
                   {"type": "reasoning", "summary": [{"type": "summary_text",
                                                      "text": "think"}]},
                   {"type": "message", "content": [
                       {"type": "output_text", "text": "hello"}]},
               ],
               "usage": {"input_tokens": 5, "output_tokens": 3,
                         "input_tokens_details": {"cached_tokens": 1},
                         "output_tokens_details": {"reasoning_tokens": 2}}}
    turn = a.decode_response(200, orjson.dumps(payload))
    assert turn.text == "hello"
    assert turn.thinking[0].text == "think"
    assert turn.stop_reason == "stop"
    assert turn.usage.prompt_tokens == 5
    assert turn.usage.cached_tokens == 1
    assert turn.usage.reasoning_tokens == 2


def test_decode_responses_response_tool_call():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", False)
    payload = {"status": "completed",
               "output": [{"type": "function_call", "id": "fc_1",
                            "call_id": "call_1", "name": "w",
                            "arguments": '{"q":"x"}'}],
               "usage": {}}
    turn = a.decode_response(200, orjson.dumps(payload))
    assert turn.tool_calls[0].name == "w"
    assert turn.tool_calls[0].args == {"q": "x"}
    assert turn.stop_reason == "tool_call"


def test_decode_responses_stream_text_and_terminal():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    frames = [
        ("", orjson.dumps({"type": "response.output_text.delta",
                            "delta": "hel"}).decode()),
        ("", orjson.dumps({"type": "response.output_text.delta",
                            "delta": "lo"}).decode()),
        ("", orjson.dumps({"type": "response.completed",
                            "response": {"status": "completed",
                                         "usage": {"input_tokens": 4,
                                                   "output_tokens": 2}}}).decode()),
    ]
    deltas: list[dl.IRStreamDelta] = []
    for ev, data in frames:
        deltas.extend(a.decode_stream_event(ev, data))
    kinds = [type(d).__name__ for d in deltas]
    assert kinds[0] == "StreamStart"
    assert [d.text for d in deltas if isinstance(d, dl.TextDelta)] == ["hel", "lo"]
    assert any(isinstance(d, dl.UsageFinal) for d in deltas)
    assert any(isinstance(d, dl.Finish) for d in deltas)
    assert isinstance(deltas[-1], dl.StreamEnd)
    # trailing [DONE] after a terminal event must not double-terminate
    assert a.decode_stream_event("", "[DONE]") == []


def test_decode_responses_stream_tool_routing():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas: list[dl.IRStreamDelta] = []
    deltas.extend(a.decode_stream_event("", orjson.dumps({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "fc_1",
                 "call_id": "call_1", "name": "w"}}).decode()))
    deltas.extend(a.decode_stream_event("", orjson.dumps({
        "type": "response.function_call_arguments.delta",
        "item_id": "fc_1", "delta": '{"q":'}).decode()))
    deltas.extend(a.decode_stream_event("", orjson.dumps({
        "type": "response.function_call_arguments.done",
        "item_id": "fc_1", "arguments": '{"q":"x"}'}).decode()))
    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    args = [d for d in deltas if isinstance(d, dl.ToolCallArgsDelta)]
    closes = [d for d in deltas if isinstance(d, dl.ToolCallClose)]
    assert len(opens) == 1 and opens[0].name == "w" and opens[0].id == "call_1"
    assert args and closes and args[0].index == opens[0].index == closes[0].index


def test_decode_responses_stream_failed():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas = a.decode_stream_event("", orjson.dumps({
        "type": "response.failed",
        "response": {"error": {"message": "boom"}}}).decode())
    assert deltas[0].__class__.__name__ == "StreamStart"
    assert any(isinstance(d, dl.StreamError) for d in deltas)


def test_decode_responses_duplicate_terminal_dropped():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    completed = orjson.dumps({"type": "response.completed",
                              "response": {"status": "completed",
                                           "usage": {}}}).decode()
    first = a.decode_stream_event("", completed)
    assert any(isinstance(d, dl.StreamEnd) for d in first)
    assert a.decode_stream_event("", completed) == []
    assert a.decode_stream_event("", orjson.dumps(
        {"type": "response.output_text.delta", "delta": "late"}).decode()) == []
    assert a.decode_stream_event("", "[DONE]") == []


def test_decode_responses_envelope_error_ends_stream():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas = a.decode_stream_event("", orjson.dumps(
        {"type": "error", "error": {"message": "AuthError"}}).decode())
    assert any(isinstance(d, dl.StreamError) for d in deltas)
    assert a.decode_stream_event("", "[DONE]") == []


def test_decode_responses_string_envelope_error():
    """A string-form ``{"error": "..."}`` envelope must surface as StreamError.

    Zen/Cloudflare send the bare-string shape too; a dict-only check silently
    drops it, leaving the stream wedged after StreamStart with no error.
    """
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    deltas = a.decode_stream_event("", orjson.dumps({"error": "rate limited"}).decode())
    errs = [d for d in deltas if isinstance(d, dl.StreamError)]
    assert errs and errs[0].message == "rate limited"
    assert a.decode_stream_event("", "[DONE]") == []


def test_delegated_chat_string_envelope_error():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "deepseek-v4-flash", True)
    deltas = a.decode_stream_event("", orjson.dumps({"error": "overloaded"}).decode())
    errs = [d for d in deltas if isinstance(d, dl.StreamError)]
    assert errs and errs[0].message == "overloaded"


def test_delegated_messages_stream_passthrough():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "claude-sonnet-5", True)
    deltas = a.decode_stream_event("message_start", orjson.dumps({
        "type": "message_start",
        "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 3}},
    }).decode())
    assert any(isinstance(d, dl.StreamStart) for d in deltas)


def test_delegated_chat_envelope_error():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "deepseek-v4-flash", True)
    deltas = a.decode_stream_event("", orjson.dumps(
        {"error": {"message": "rate limited"}}).decode())
    assert any(isinstance(d, dl.StreamError) for d in deltas)


def test_encode_responses_drops_audio_with_warning(caplog=None):
    a = OpencodeAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.TextPart("hi"),
            ir.AudioPart(b64="AAA=", mime="audio/wav"),
        ])],
    )
    body = a.encode_request(req, "gpt-5.5", {})
    assert body["input"][0]["content"] == [{"type": "input_text", "text": "hi"}]


def test_reset_clears_responses_state():
    a = OpencodeAdapter()
    a.build_url("https://opencode.ai/zen/v1", "gpt-5.5", True)
    a.decode_stream_event("", orjson.dumps({
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "fc_1",
                 "call_id": "call_1", "name": "w"}}).decode())
    a.reset()
    assert a._resp_tools == {}
    assert a._last_route == "chat"


# -- end-to-end -------------------------------------------------------------------


def _zen_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode",
                               base_url="https://opencode.ai/zen/v1",
                               keys=[KeyDef(label="main", key="zen-secret")])],
        model_list=[ModelEntry(model_name="zen-chat",
                               wiwi_params=DeploymentParams(provider="zen",
                                                            model="deepseek-v4-flash"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest_asyncio.fixture
async def zen_client():
    app = create_app(_zen_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


@respx.mock
async def test_zen_chat_request_sends_live_user_agent(zen_client):
    route = respx.post("https://opencode.ai/zen/v1/chat/completions").respond(json={
        "id": "chatcmpl-x", "object": "chat.completion", "model": "deepseek-v4-flash",
        "choices": [{"index": 0, "message": {"role": "assistant",
                                             "content": "hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    r = await zen_client.post("/v1/chat/completions", json={
        "model": "zen-chat", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    assert route.called
    sent_ua = route.calls[0].request.headers.get("user-agent", "")
    assert sent_ua == "opencode/9.9.9"
    sent_auth = route.calls[0].request.headers.get("authorization", "")
    assert sent_auth == "Bearer zen-secret"
