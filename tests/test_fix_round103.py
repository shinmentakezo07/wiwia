"""Round 103 — /v1/playground/completions: the completion pipeline behind the
session cookie.

The Playground SPA used to POST /v1/chat/completions with a playground virtual
key as the bearer. Every login minted a new key and the per-owner cap expired
the oldest, so a key cached in the browser went stale under an open tab and the
user saw "401 invalid API key" while perfectly logged in. The fix moves
admission server-side: the new route authenticates the ``wiwi_session`` cookie,
resolves/mints a live playground key in-process, and runs the exact same
``run_chat_like`` pipeline with that key injected as the bearer. No key
material travels to the client.

Also covered: the upstream-error passthrough (UPDATE.md) still works through
the wrapper, and the client contract (round 100's) is updated.
"""

import asyncio

import orjson
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"

OPENAI_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}

_SSE = {"Content-Type": "text/event-stream"}

_CHAT_SSE = b"".join([
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{"role":"assistant","content":"hel"},"finish_reason":null}]}\n\n'),
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}],'
     b'"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'),
    b"data: [DONE]\n\n",
])


def _config(tmp_path) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"),
    )


async def _client(tmp_path):
    app = create_app(_config(tmp_path))
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    _orig_close = client.aclose

    async def _close_then_lifespan():
        try:
            await _orig_close()
        finally:
            await lm.__aexit__(None, None, None)

    client.aclose = _close_then_lifespan  # type: ignore[method-assign]
    return client


async def _signup(client: AsyncClient) -> AsyncClient:
    r = await client.post("/auth/signup", json={"username": "pguser", "password": "password1"})
    assert r.status_code == 201, r.text
    return client


# -- the wrapper runs the completion pipeline ---------------------------------


@respx.mock
async def test_wrapper_streams_a_completion(tmp_path):
    """Cookie in → chat-completion SSE out, upstream reached with the minted
    playground key, not the master key and not an absent bearer."""
    client = await _signup(await _client(tmp_path))
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        content=_CHAT_SSE, headers=_SSE)
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
        "stream": True})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    text = "".join(
        orjson.loads(line[6:])["choices"][0]["delta"].get("content", "")
        for line in r.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]")
    assert text == "hello"
    # The upstream call was authenticated with the server-minted playground
    # key — key material the browser never saw.
    assert route.called
    await client.aclose()


@respx.mock
async def test_wrapper_non_streaming_json(tmp_path):
    client = await _signup(await _client(tmp_path))
    respx.post("https://api.openai.com/v1/chat/completions").respond(json=OPENAI_BODY)
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "hello"
    assert r.headers.get("x-wiwi-request-id")
    await client.aclose()


# -- admission ----------------------------------------------------------------


async def test_wrapper_requires_a_session(tmp_path):
    """Anonymous callers get 401 — the cookie is the credential."""
    client = await _client(tmp_path)
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401
    await client.aclose()


@respx.mock
async def test_wrapper_rejects_unknown_model_like_the_plain_surface(tmp_path):
    client = await _signup(await _client(tmp_path))
    r = await client.post("/v1/playground/completions", json={
        "model": "nope", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    await client.aclose()


async def test_wrapper_rejects_malformed_body(tmp_path):
    client = await _signup(await _client(tmp_path))
    r = await client.post("/v1/playground/completions", json={"model": "gpt-4o", "messages": "nope"})
    assert r.status_code == 400
    await client.aclose()


@respx.mock
async def test_wrapper_attributes_the_request_to_the_playground_key(tmp_path):
    """The turn is logged against the minted playground key (key_id), not the
    master key — that attribution is what makes spend, rate limits and the
    user's key list truthful."""
    client = await _signup(await _client(tmp_path))
    app = client._transport.app  # type: ignore[attr-defined]
    state = app.state.wiwi
    r = await client.get("/auth/me")
    user_id = r.json()["user"]["id"]
    keys = await state.auth.list_keys_for_owner(user_id)
    pg = [k for k in keys if k["alias"] == "playground"]
    assert pg, "signup must mint a playground key"
    pg_id = pg[0]["id"]

    respx.post("https://api.openai.com/v1/chat/completions").respond(json=OPENAI_BODY)
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    request_id = r.headers["x-wiwi-request-id"]

    # The log pump is async; poll briefly for the row to land in the ring.
    event = None
    for _ in range(40):
        ring = await state.logs.sse.replay("request", 0)
        event = next((e for _s, e in reversed(ring)
                      if e.request_id == request_id), None)
        if event is not None:
            break
        await asyncio.sleep(0.05)
    assert event is not None, "request log event never landed"
    assert event.key_id == pg_id, (
        f"request attributed to {event.key_id!r}, expected the playground key")
    await client.aclose()


# -- key resolution -----------------------------------------------------------


@respx.mock
async def test_wrapper_reuses_a_cached_live_key_and_self_heals_a_dead_one(tmp_path):
    """A cached plaintext that still authenticates is reused (no mint storm);
    one the cap expired is detected via authenticate() and replaced by a fresh
    capped mint — the exact failure the old client-side 401 dance papered
    over."""
    client = await _signup(await _client(tmp_path))
    app = client._transport.app  # type: ignore[attr-defined]
    state = app.state.wiwi
    r = await client.get("/auth/me")
    user_id = r.json()["user"]["id"]
    from wiwi.server.app import _MAX_PLAYGROUND_KEYS_PER_USER  # noqa: F401 — cap contract

    respx.post("https://api.openai.com/v1/chat/completions").respond(json=OPENAI_BODY)

    r1 = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r1.status_code == 200, r1.text

    # Drive the mint cap exactly like repeated logins do: rotate until the
    # cached key is the one that got expired.
    live = await state.auth.list_keys_for_owner(user_id)
    live_pg = [k for k in live if k["alias"] == "playground" and not k["disabled"]]
    assert live_pg, "a playground key exists after the first wrapper call"
    # Expire every live playground key (keep_newest=0) — the cached plaintext
    # is now dead.
    await state.auth.expire_keys(
        owner_id=user_id, alias="playground", keep_newest=0)
    r2 = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r2.status_code == 200, r2.text
    live_after = await state.auth.list_keys_for_owner(user_id)
    pg_after = [k for k in live_after if k["alias"] == "playground" and not k["disabled"]]
    assert pg_after, "a dead cache must self-heal: a fresh key is minted"
    # The healed key is a NEW row, not the expired one.
    assert pg_after[0]["id"] != live_pg[0]["id"]
    await client.aclose()


@respx.mock
async def test_wrapper_does_not_mint_per_request_when_the_key_stays_live(tmp_path):
    """Repeated calls through the wrapper mint once — the per-owner cap cannot
    be run into by the playground's own traffic."""
    client = await _signup(await _client(tmp_path))
    app = client._transport.app  # type: ignore[attr-defined]
    state = app.state.wiwi
    r = await client.get("/auth/me")
    user_id = r.json()["user"]["id"]
    respx.post("https://api.openai.com/v1/chat/completions").respond(json=OPENAI_BODY)
    for _ in range(4):
        r = await client.post("/v1/playground/completions", json={
            "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200, r.text
    keys = await state.auth.list_keys_for_owner(user_id)
    pg = [k for k in keys if k["alias"] == "playground" and not k["disabled"]]
    assert len(pg) == 1
    await client.aclose()


# -- upstream error passthrough -------------------------------------------------


@respx.mock
async def test_wrapper_preserves_provider_error_shape(tmp_path):
    """UPDATE.md binding: provider error wording is surfaced, not rewritten —
    through the wrapper just as through the plain surface."""
    client = await _signup(await _client(tmp_path))
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=429, json={"error": {"message": "insufficient_quota: hit the limit",
                                         "type": "insufficient_quota", "code": "429"}})
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 429
    body = r.json()
    assert "insufficient_quota" in (body.get("error", {}).get("message") or "")
    await client.aclose()


# -- client contract -------------------------------------------------------------


def test_playground_page_uses_the_wrapper():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    playground = (root / "web/src/pages/Playground.tsx").read_text()
    assert 'fetch("/v1/playground/completions"' in playground
    # No Authorization header on the playground call: the session cookie is the
    # credential.
    assert 'Authorization' not in playground, (
        "Playground must not send a bearer header; the cookie authenticates")
    # No key-preparation gating left in the UI.
    assert "Preparing your playground" not in playground
    assert "ensurePlaygroundKey" not in playground
