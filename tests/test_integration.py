"""End-to-end integration: HTTP requests through the app with mocked upstreams."""


import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


OPENAI_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2,
              "prompt_tokens_details": {"cached_tokens": 0},
              "completion_tokens_details": {"reasoning_tokens": 0}},
}


@respx.mock
async def test_chat_completion_happy_path(client):
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "hello"
    assert r.headers.get("x-wiwi-request-id")


@respx.mock
async def test_anthropic_surface_to_openai_backend(client):
    """Claude Code dialect in, OpenAI provider out — response back in Anthropic shape."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]},
        headers={"x-api-key": "sk-wiwi-master-test", "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "message"
    assert data["content"][0]["type"] == "text"
    assert data["content"][0]["text"] == "hello"
    assert data["stop_reason"] == "end_turn"


@respx.mock
async def test_auth_required(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


@respx.mock
async def test_unknown_model_404(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "nope", "messages": []},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 404


@respx.mock
async def test_streaming_chat(client):
    route = respx.post("https://api.openai.com/v1/chat/completions")
    route.respond(text=(
        'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"))
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200
    body = r.text
    assert "chat.completion.chunk" in body
    assert '"He"' in body and '"y"' in body
    assert "[DONE]" in body
    # final usage frame relayed from upstream usage
    assert "prompt_tokens" in body.split("[DONE]")[0][-800:]


@respx.mock
async def test_count_tokens(client):
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "abcd" * 10}]},
        headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200
    assert r.json()["input_tokens"] >= 10


async def test_models_list(client):
    r = await client.get("/v1/models",
                         headers={"Authorization": "Bearer sk-wiwi-master-test"})
    ids = [m["id"] for m in r.json()["data"]]
    assert "gpt-4o" in ids


async def test_models_list_requires_auth(client):
    r = await client.get("/v1/models")
    assert r.status_code == 401


@respx.mock
async def test_admin_key_lifecycle(client):
    h = {"Authorization": "Bearer sk-wiwi-master-test"}
    r = await client.post("/admin/keys/generate", json={"name": "team-a"},
                          headers=h)
    assert r.status_code == 200
    plaintext = r.json()["key"]
    assert plaintext.startswith("sk-wiwi-")
    # use it
    respx.post("https://api.openai.com/v1/chat/completions").respond(json=OPENAI_BODY)
    r2 = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {plaintext}"})
    assert r2.status_code == 200
    # list + delete
    lst = (await client.get("/admin/keys", headers=h)).json()
    kid = next(k["id"] for k in lst["keys"] if k["alias"] == "team-a")
    d = await client.delete(f"/admin/keys/{kid}", headers=h)
    assert d.json()["deleted"] is True


async def test_admin_requires_master(client):
    r = await client.get("/admin/keys",
                         headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
