"""Regression round 113 — the legacy /v1/completions surface (spec A).

The inbound dialect is Completions, but the upstream adapter always speaks
OpenAI **chat** (`wiwi/providers/openai_adapter.py`): the mocks below answer in
the chat shape and wiwi re-encodes for the caller — which is the whole point of
the hub-and-spoke design.
"""

import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: openai
    provider: openai
    keys:
      - label: default
        key: sk-upstream
model_list:
  - model_name: gpt-3.5-turbo-instruct
    wiwi_params:
      provider: openai
      model: gpt-3.5-turbo-instruct
"""

_UPSTREAM = "https://api.openai.com/v1/chat/completions"


async def _client():
    app = create_app(load_config_from_string(_CFG))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app),
                         base_url="http://test",
                         headers={"Authorization": "Bearer sk-wiwi-master-test"})
    return mgr, client


@respx.mock
async def test_completions_non_streaming_end_to_end():
    respx.post(_UPSTREAM).mock(
        return_value=Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 1,
            "model": "gpt-3.5-turbo-instruct",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi there"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3,
                      "total_tokens": 5}}))
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions",
                              json={"model": "gpt-3.5-turbo-instruct", "prompt": "hi"})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "text_completion"
    assert body["choices"][0]["text"] == "hi there"
    assert body["usage"]["total_tokens"] == 5


async def test_completions_rejects_multi_prompt_with_openai_error_envelope():
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions",
                              json={"model": "gpt-3.5-turbo-instruct",
                                    "prompt": ["a", "b"]})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert r.json()["error"]["code"] == "invalid_request_error"


@respx.mock
async def test_completions_tool_stop_is_reported_as_stop():
    respx.post(_UPSTREAM).mock(
        return_value=Response(200, json={
            "id": "chatcmpl-2", "object": "chat.completion", "created": 1,
            "model": "gpt-3.5-turbo-instruct",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": ""}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}))
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions",
                              json={"model": "gpt-3.5-turbo-instruct", "prompt": "hi"})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    # The tool protocol does not exist here, so the client must not be told to
    # run a tool it was never given.
    assert r.json()["choices"][0]["finish_reason"] == "stop"


@respx.mock
async def test_completions_streaming_frames_and_done():
    respx.post(_UPSTREAM).mock(
        return_value=Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"id":"c","object":"chat.completion.chunk","choices":'
                    b'[{"index":0,"delta":{"role":"assistant","content":"he"},'
                    b'"finish_reason":null}]}\n\n'
                    b'data: {"id":"c","object":"chat.completion.chunk","choices":'
                    b'[{"index":0,"delta":{"content":"llo"},"finish_reason":"stop"}]}\n\n'
                    b"data: [DONE]\n\n"))
    mgr, client = await _client()
    try:
        r = await client.post("/v1/completions", json={
            "model": "gpt-3.5-turbo-instruct", "prompt": "hi",
            "stream": True, "stream_options": {"include_usage": True}})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 200
    text = r.text
    assert '"object":"text_completion"' in text
    assert '"text":"he"' in text and '"text":"llo"' in text
    assert text.rstrip().endswith("data: [DONE]")
