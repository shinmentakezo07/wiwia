"""Round 121 — /v1/playground/completions: a disabled key must heal, not wedge.

The Playground wrapper (round 103) resolves a playground virtual key per actor
and caches its plaintext on ``AppState.pg_bearers``. The stated contract is that
the plaintext is re-validated on every resolution "so a key the per-owner cap
expired, a TTL expiry, or a revoke is detected on the next request and healed by
a fresh mint".

``AuthService.authenticate`` heals two of those three. It refuses an expired
credential on both the cached and the miss path (``_expired``), so a TTL expiry
and a per-owner-cap expiry both return ``None`` and the wrapper re-mints.

It does NOT refuse a *disabled* one. ``_lookup_db`` selects ``v.disabled`` into
``AuthInfo`` but does not filter on it — deliberately, because every caller
checks ``info.disabled`` itself. So ``authenticate`` returns a live-looking
``AuthInfo`` for a revoked key.

``_playground_bearer`` only checked ``is not None``, so it treated that
disabled key as healthy, handed the plaintext back, and never re-minted. The
downstream admission check in ``run_chat_like`` does test ``info.disabled``, so
every subsequent Playground request answered 401 "key disabled or expired" — and
because the wrapper's own revalidation kept passing, no request could ever
heal it. The user was permanently locked out of the Playground while logged in,
which is precisely the failure class this wrapper was built to eliminate.

Any owner may disable their own key (``POST /admin/keys/{id}/disable`` allows
the owner when they are not an admin), so this is reachable from the UI without
an operator ever touching the account.
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

MASTER = "sk-wiwi-master-test"

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
    return client


async def _signup(client: AsyncClient) -> tuple[AsyncClient, str]:
    r = await client.post("/auth/signup",
                          json={"username": "pguser", "password": "password1"})
    assert r.status_code == 201, r.text
    return client, r.json()["playground_key"]


async def _disable_own_key(client: AsyncClient, key_id: str) -> None:
    r = await client.post(f"/admin/keys/{key_id}/disable", json={"disabled": True})
    assert r.status_code == 200, r.text


@respx.mock
async def test_disabled_playground_key_heals_on_the_next_request(tmp_path):
    """A key the owner disables must be replaced by a fresh mint, not served.

    Sequence: signup (a playground key is minted and cached) → the owner
    disables that key → a Playground request must still succeed. Before the
    fix it answered 401 forever, because the wrapper's revalidation accepted
    the disabled key and so never re-minted.
    """
    client, pg_key = await _signup(await _client(tmp_path))
    assert pg_key, "signup must mint a playground key"

    # The owner disables their own playground key.
    listing = await client.get("/admin/keys")
    assert listing.status_code == 200, listing.text
    rows = [k for k in listing.json()["keys"] if k["alias"] == "playground"]
    assert len(rows) == 1, rows
    await _disable_own_key(client, rows[0]["id"])

    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        content=_CHAT_SSE, headers=_SSE)
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })

    assert r.status_code == 200, (
        f"a disabled playground key must be re-minted, not served; "
        f"got {r.status_code}: {r.text[:400]}")
    assert route.called, "the request must reach upstream"
    await client.aclose()


@respx.mock
async def test_disabled_playground_key_is_replaced_not_reused(tmp_path):
    """The healing must be a real re-mint, not the disabled key slipping
    through a loosened check.

    Asserts the recovered path mints a *different* credential and that the
    revoked one stays revoked — so this cannot be satisfied by simply ignoring
    ``info.disabled`` at admission.
    """
    client, _pg_key = await _signup(await _client(tmp_path))
    listing = await client.get("/admin/keys")
    rows = [k for k in listing.json()["keys"] if k["alias"] == "playground"]
    old_id = rows[0]["id"]
    await _disable_own_key(client, old_id)

    respx.post("https://api.openai.com/v1/chat/completions").respond(
        content=_CHAT_SSE, headers=_SSE)
    r = await client.post("/v1/playground/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })
    assert r.status_code == 200, r.text[:400]

    after = await client.get("/admin/keys")
    rows_after = [k for k in after.json()["keys"] if k["alias"] == "playground"]
    assert len(rows_after) == 2, rows_after
    still_disabled = [k for k in rows_after if k["id"] == old_id]
    assert still_disabled and still_disabled[0]["disabled"] is True, (
        "the revoked key must stay revoked; the fix re-mints, it does not "
        "silently re-enable")
    fresh = [k for k in rows_after if k["id"] != old_id]
    assert fresh and not fresh[0]["disabled"], fresh
    await client.aclose()


@respx.mock
async def test_reenabled_playground_key_is_not_churned(tmp_path):
    """The healthy path must be untouched: a live, enabled key is reused, not
    re-minted on every request.

    Guards against over-correcting into "mint a rival on every call", which
    would burn the per-owner cap and create a fresh credential per request.
    """
    client, _pg_key = await _signup(await _client(tmp_path))
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        content=_CHAT_SSE, headers=_SSE)
    body = {"model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True}
    for _ in range(3):
        r = await client.post("/v1/playground/completions", json=body)
        assert r.status_code == 200, r.text[:400]

    listing = await client.get("/admin/keys")
    rows = [k for k in listing.json()["keys"] if k["alias"] == "playground"]
    assert len(rows) == 1, (
        f"an enabled key must be reused across requests; got {len(rows)} "
        f"playground keys — the wrapper is minting on every call")
    await client.aclose()