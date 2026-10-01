"""Regression round 114 — stateful Responses (spec B).

The upstream adapter speaks OpenAI **chat** (the mock below answers in the chat
shape); wiwi re-encodes into the Responses dialect, and the chained second turn
must carry turn 1's assistant message in the upstream request.
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
    keys: [{label: default, key: sk-upstream}]
model_list:
  - model_name: gpt-4o
    wiwi_params: {provider: openai, model: gpt-4o}
"""

_UP = "https://api.openai.com/v1/chat/completions"
_AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}


def _chat(text: str) -> Response:
    return Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


async def _client(headers=None):
    app = create_app(load_config_from_string(_CFG))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    h = dict(_AUTH)
    h.update(headers or {})
    return app, mgr, AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                 headers=h)


@respx.mock
async def test_previous_response_id_chains_the_history_upstream():
    route = respx.post(_UP).mock(side_effect=[_chat("first"), _chat("second")])
    _, mgr, client = await _client()
    try:
        r1 = await client.post("/v1/responses", json={"model": "gpt-4o", "input": "one"})
        assert r1.status_code == 200, r1.text
        rid = r1.json()["id"]
        r2 = await client.post("/v1/responses",
                               json={"model": "gpt-4o", "input": "two",
                                     "previous_response_id": rid})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r2.status_code == 200, r2.text
    sent = route.calls[1].request.content.decode()
    # Turn 1's assistant text and turn 2's new input are both in the upstream body.
    assert "first" in sent and "two" in sent


@respx.mock
async def test_get_and_delete_round_trip():
    respx.post(_UP).mock(return_value=_chat("hi"))
    _, mgr, client = await _client()
    try:
        rid = (await client.post("/v1/responses",
                                 json={"model": "gpt-4o", "input": "x"})).json()["id"]
        got = await client.get(f"/v1/responses/{rid}")
        deleted = await client.delete(f"/v1/responses/{rid}")
        gone = await client.get(f"/v1/responses/{rid}")
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert got.status_code == 200 and got.json()["object"] == "response"
    assert got.json()["id"] == rid
    assert deleted.json()["deleted"] is True
    assert gone.status_code == 404


@respx.mock
async def test_unknown_previous_response_id_is_404():
    respx.post(_UP).mock(return_value=_chat("hi"))
    _, mgr, client = await _client()
    try:
        r = await client.post("/v1/responses",
                              json={"model": "gpt-4o", "input": "x",
                                    "previous_response_id": "resp_nope"})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 404


@respx.mock
async def test_store_false_does_not_persist():
    respx.post(_UP).mock(return_value=_chat("hi"))
    _, mgr, client = await _client()
    try:
        rid = (await client.post("/v1/responses",
                                 json={"model": "gpt-4o", "input": "x",
                                       "store": False})).json()["id"]
        got = await client.get(f"/v1/responses/{rid}")
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert got.status_code == 404


@respx.mock
async def test_another_key_cannot_read_a_stored_response():
    respx.post(_UP).mock(return_value=_chat("hi"))
    app, mgr, client = await _client()
    try:
        rid = (await client.post("/v1/responses",
                                 json={"model": "gpt-4o", "input": "x"})).json()["id"]
        # Mint a second virtual key directly through the auth service.
        state = app.state.wiwi
        await state.auth.create_key("other", custom_key="sk-other-key-123456")
        other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                            headers={"Authorization": "Bearer sk-other-key-123456"})
        try:
            got = await other.get(f"/v1/responses/{rid}")
        finally:
            await other.aclose()
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert got.status_code == 404


def _chat_stream(fragment: str) -> Response:
    body = (b'data: {"id":"c","object":"chat.completion.chunk","choices":'
            b'[{"index":0,"delta":{"role":"assistant","content":"' + fragment.encode() +
            b'"},"finish_reason":null}]}\n\n'
            b'data: {"id":"c","object":"chat.completion.chunk","choices":'
            b'[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b"data: [DONE]\n\n")
    return Response(200, headers={"content-type": "text/event-stream"}, content=body)


@respx.mock
async def test_streamed_response_can_be_continued_by_id():
    route = respx.post(_UP).mock(side_effect=[_chat_stream("streamed-first"),
                                              _chat("second")])
    _, mgr, client = await _client()
    try:
        # The Responses dialect always streams SSE; the id rides response.created.
        r1 = await client.post("/v1/responses",
                               json={"model": "gpt-4o", "input": "one", "stream": True})
        assert r1.status_code == 200, r1.text
        import re
        m = re.search(r'"id":\s*"(resp_[A-Za-z0-9]+)"', r1.text)
        assert m, r1.text[:400]
        rid = m.group(1)
        r2 = await client.post("/v1/responses",
                               json={"model": "gpt-4o", "input": "two",
                                     "previous_response_id": rid})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r2.status_code == 200, r2.text
    sent = route.calls[1].request.content.decode()
    assert "streamed-first" in sent and "two" in sent
