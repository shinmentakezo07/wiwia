"""Round 212 — the Playground reuses one hidden key instead of minting per login.

``/auth/login`` and ``/auth/signup`` used to mint a fresh "playground" virtual
key on every call and return its plaintext as ``playground_key``. Repeated
logins therefore accumulated live credentials in the owner's key list (five at
a time, rotated on each login) and leaked the plaintext in the response body,
while the SPA never read the value — it posts to /v1/playground/completions
and the server injects the key itself.

Now login/signup start a session and nothing else. The wrapper mints one key
on first use, caches it, and reuses it for every later request, including
after another login. The per-owner cap is one, so a re-mint (TTL expiry,
revoke, process restart) retires the previous live key rather than adding a
rival to the list.
"""

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
from wiwi.server.services.playground import (
    MAX_PLAYGROUND_KEYS_PER_USER as _MAX_PLAYGROUND_KEYS_PER_USER,
)

MASTER = "sk-wiwi-master-test"

_SSE = {"Content-Type": "text/event-stream"}
_CHAT_SSE = b"".join([
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{"role":"assistant","content":"hi"},'
     b'"finish_reason":null}]}\n\n'),
    (b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
     b'[{"index":0,"delta":{},"finish_reason":"stop"}],'
     b'"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'),
    b"data: [DONE]\n\n",
])

_BODY = {"model": "gpt-4o", "stream": True,
         "messages": [{"role": "user", "content": "hi"}]}


def _config(tmp_path) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
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
    return app, client


async def _playground_keys(client: AsyncClient) -> list[dict]:
    r = await client.get("/admin/keys")
    assert r.status_code == 200, r.text
    return [k for k in r.json()["keys"] if k["alias"] == "playground"]


async def test_login_and_signup_mint_no_playground_key(tmp_path):
    _app, client = await _client(tmp_path)
    try:
        r = await client.post("/auth/signup",
                              json={"username": "pg1", "password": "password1"})
        assert r.status_code == 201
        assert "playground_key" not in r.json(), "signup must not expose a key"
        assert await _playground_keys(client) == [], "signup must not mint a key"

        for _ in range(3):
            r = await client.post("/auth/login",
                                  json={"username": "pg1",
                                        "password": "password1"})
            assert r.status_code == 200
            assert "playground_key" not in r.json(), "login must not expose a key"

        assert await _playground_keys(client) == [], "login must not mint a key"
    finally:
        await client.aclose()


@respx.mock
async def test_playground_requests_share_one_key_across_logins(tmp_path):
    _app, client = await _client(tmp_path)
    try:
        await client.post("/auth/signup",
                          json={"username": "pg2", "password": "password1"})
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            content=_CHAT_SSE, headers=_SSE)

        for _ in range(3):
            r = await client.post("/v1/playground/completions", json=_BODY)
            assert r.status_code == 200, r.text[:400]

        keys = await _playground_keys(client)
        assert len(keys) == 1, keys

        # A second login must not rotate or duplicate the credential.
        await client.post("/auth/login",
                          json={"username": "pg2", "password": "password1"})
        r = await client.post("/v1/playground/completions", json=_BODY)
        assert r.status_code == 200, r.text[:400]

        after = await _playground_keys(client)
        assert [k["id"] for k in after] == [keys[0]["id"]], (
            "the playground key must be reused after a login, not re-minted")
    finally:
        await client.aclose()


async def test_fallback_endpoint_returns_the_same_live_key(tmp_path):
    _app, client = await _client(tmp_path)
    try:
        await client.post("/auth/signup",
                          json={"username": "pg3", "password": "password1"})
        k1 = (await client.post("/auth/playground-key")).json()["key"]
        await client.post("/auth/login",
                          json={"username": "pg3", "password": "password1"})
        k2 = (await client.post("/auth/playground-key")).json()["key"]
        assert k1 == k2
        assert len(await _playground_keys(client)) == 1
    finally:
        await client.aclose()


def test_playground_key_cap_is_one():
    assert _MAX_PLAYGROUND_KEYS_PER_USER == 1
