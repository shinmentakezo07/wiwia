"""Union Alpha uses Zen's Messages API behind the Chat Completions surface."""

from __future__ import annotations

import time

import httpx
import orjson
import pytest_asyncio
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.providers import opencode_version as ov
from wiwi.server.app import create_app


@pytest_asyncio.fixture
async def union_client(monkeypatch):
    monkeypatch.setattr(ov, "_cached_version", "9.9.9")
    monkeypatch.setattr(ov, "_fetched_at", time.monotonic())
    config = WiwiConfig(
        providers=[ProviderDef(
            name="zen", provider="opencode",
            base_url="https://opencode.ai/zen/v1",
            keys=[KeyDef(label="main", key="anonymous")],
        )],
        model_list=[ModelEntry(
            model_name="union", wiwi_params=DeploymentParams(
                provider="zen", model="union-alpha",
            ),
        )],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
        router_settings=RouterSettings(num_retries=0),
    )
    app = create_app(config)
    async with LifespanManager(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer sk-wiwi-master-test"},
    ) as client:
        yield client


def _request(*, stream=False):
    return {
        "model": "union",
        "messages": [
            {"role": "system", "content": "Use the weather tool."},
            {"role": "user", "content": "Weather in Paris?"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "weather", "description": "Look up weather",
            "parameters": {"type": "object", "properties": {
                "city": {"type": "string"}}, "required": ["city"]},
        }}],
        "max_tokens": 128,
        "stream": stream,
    }


def _assert_messages_request(request, *, stream):
    body = orjson.loads(request.content)
    assert body["model"] == "union-alpha"
    assert body["system"] == "Use the weather tool."
    assert [message["role"] for message in body["messages"]] == ["user"]
    assert body["tools"][0]["name"] == "weather"
    assert body["tools"][0]["input_schema"] == (
        _request()["tools"][0]["function"]["parameters"]
    )
    assert body["max_tokens"] == 128
    # Zen answers as an event stream, so the transport forces SSE on every
    # upstream request and the gateway reassembles for a non-streaming caller:
    # the client's flag no longer decides what goes upstream.
    assert body["stream"] is True
    headers = request.headers
    assert headers["x-opencode-session"].startswith("ses_")
    assert headers["x-opencode-request"].startswith("msg_")
    assert headers["x-opencode-client"] == "cli"
    assert headers["x-opencode-project"] == "global"
    assert headers["user-agent"] == "opencode/9.9.9"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in headers


#: The Messages stream Zen returns for the weather turn below. Every request
#: to this provider is answered as SSE, so both tests consume it — one through
#: the pump, one through the reassembly path a non-streaming client takes.
_MESSAGES_EVENTS = [
    {"type": "message_start", "message": {
        "id": "msg_union", "type": "message", "role": "assistant",
        "model": "union-alpha", "content": [], "stop_reason": None,
        "usage": {"input_tokens": 20, "output_tokens": 0},
    }},
    {"type": "content_block_start", "index": 0,
     "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "text_delta", "text": "Checking the weather."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "toolu_weather",
                       "name": "weather", "input": {}}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"city":'}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {
        "stop_reason": "tool_use", "stop_sequence": None,
    }, "usage": {"output_tokens": 12}},
    {"type": "message_stop"},
]


def _messages_sse() -> bytes:
    return b"".join(
        b"event: " + event["type"].encode() + b"\ndata: "
        + orjson.dumps(event) + b"\n\n" for event in _MESSAGES_EVENTS
    )


@respx.mock
async def test_union_alpha_chat_translates_messages_tool_use(union_client):
    # Non-streaming client, streaming-only upstream: the gateway pumps the SSE
    # and hands back one aggregated JSON completion.
    route = respx.post("https://opencode.ai/zen/v1/messages").respond(
        content=_messages_sse(),
        headers={"Content-Type": "text/event-stream"},
    )
    response = await union_client.post("/v1/chat/completions", json=_request())
    assert response.status_code == 200, response.text
    _assert_messages_request(route.calls[0].request, stream=False)
    choice = response.json()["choices"][0]
    assert choice["message"]["content"] == "Checking the weather."
    assert choice["finish_reason"] == "tool_calls"
    calls = choice["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["id"] == "toolu_weather"
    assert calls[0]["function"]["name"] == "weather"
    assert orjson.loads(calls[0]["function"]["arguments"]) == {"city": "Paris"}


@respx.mock
async def test_union_alpha_chat_stream_translates_messages_tool_arguments(union_client):
    route = respx.post("https://opencode.ai/zen/v1/messages").respond(
        content=_messages_sse(), headers={"Content-Type": "text/event-stream"},
    )
    response = await union_client.post(
        "/v1/chat/completions", json=_request(stream=True),
    )
    assert response.status_code == 200, response.text
    _assert_messages_request(route.calls[0].request, stream=True)
    chunks = [
        orjson.loads(line[6:]) for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    choices = [choice for chunk in chunks for choice in chunk.get("choices", [])]
    assert "".join(choice["delta"].get("content", "") or "" for choice in choices) == (
        "Checking the weather."
    )
    tool_deltas = [
        call for choice in choices for call in choice["delta"].get("tool_calls", [])
    ]
    assert {call["index"] for call in tool_deltas} == {tool_deltas[0]["index"]}
    assert [call["id"] for call in tool_deltas if call.get("id")] == ["toolu_weather"]
    assert [call["function"]["name"] for call in tool_deltas
            if call["function"].get("name")] == ["weather"]
    arguments = "".join(call["function"].get("arguments", "") for call in tool_deltas)
    assert orjson.loads(arguments) == {"city": "Paris"}
    assert [choice["finish_reason"] for choice in choices
            if choice.get("finish_reason")] == ["tool_calls"]
