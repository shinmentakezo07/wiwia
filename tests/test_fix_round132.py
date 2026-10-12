"""Hermes (Nous Portal) OAuth admin endpoints: device-code start, poll,
status, refresh, disconnect — tokens persisted via ConfigStore and landed on
the provider's pool key prefixed ``hermes:``.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.providers import hermes_oauth

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}

DEVICE_CODE = {
    "device_code": "dev-xyz",
    "user_code": "WXYZ-1234",
    "verification_uri": f"{hermes_oauth.PORTAL_BASE}/device",
    "verification_uri_complete": f"{hermes_oauth.PORTAL_BASE}/device?user_code=WXYZ-1234",
    "expires_in": 600,
    "interval": 5,
}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="hermes-main", provider="hermes",
                               keys=[KeyDef(label="default", key="stale-key")])],
        model_list=[ModelEntry(
            model_name="hermes-model",
            wiwi_params=DeploymentParams(provider="hermes-main",
                                         model="Hermes-4-405B"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# -- auth guard ---------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("POST", "/admin/hermes/oauth/device-code?provider=hermes-main"),
    ("POST", "/admin/hermes/oauth/poll"),
    ("GET", "/admin/hermes/oauth/status?provider=hermes-main"),
    ("POST", "/admin/hermes/oauth/refresh"),
    ("DELETE", "/admin/hermes/oauth/disconnect"),
])
async def test_hermes_oauth_requires_admin(method, path):
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.request(method, path, json={"state": "s"})
            assert r.status_code == 401


# -- device-code + poll: full connect flow ------------------------------------

@respx.mock
async def test_device_code_bootstraps_account_without_prior_key(client):
    """OAuth must not require an API key first — the account is created with a
    placeholder pool key, and /poll replaces it with the real token."""
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-tok", "refresh_token": "ref-tok", "expires_in": 3600}))

    # A brand-new provider name that does not exist yet.
    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-new",
                          headers=AUTH)
    assert r.status_code == 200
    state = r.json()["state"]
    assert r.json()["verification_uri_complete"].startswith(hermes_oauth.PORTAL_BASE)

    r = await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})
    assert r.status_code == 200
    assert r.json()["connected"] is True

    # The bootstrapped account now carries the token on its pool key.
    r = await client.get("/admin/providers/hermes-new/keys/default/secret", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["secret"] == "hermes:acc-tok"


@respx.mock
async def test_device_code_then_poll_connects(client):
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-tok",
            "refresh_token": "ref-tok",
            "token_type": "Bearer",
            "expires_in": 3600,
        }))

    # 1. Start the device-code session.
    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    state = body["state"]
    assert body["user_code"] == "WXYZ-1234"
    assert body["verification_uri_complete"].startswith(hermes_oauth.PORTAL_BASE)

    # 2. Poll once — approved, token lands on the pool key prefixed hermes:.
    r = await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})
    assert r.status_code == 200
    poll = r.json()
    assert poll["connected"] is True
    assert poll["access_token_masked"] != "acc-tok"  # masked, never raw

    # The pool key now carries the token; reveal to confirm the hermes: prefix.
    r = await client.get("/admin/providers/hermes-main/keys/default/secret", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["secret"] == "hermes:acc-tok"


@respx.mock
async def test_poll_pending_returns_pending(client):
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "authorization_pending"}))

    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    state = r.json()["state"]
    r = await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})
    assert r.status_code == 200
    assert r.json()["pending"] is True


@respx.mock
async def test_poll_unknown_state_404s(client):
    r = await client.post("/admin/hermes/oauth/poll", headers=AUTH,
                          json={"state": "nope"})
    assert r.status_code == 404


@respx.mock
async def test_device_code_rejects_non_hermes_provider(client):
    # Add an OpenAI account, then try to start a Hermes device flow on it.
    r = await client.post("/admin/providers", headers=AUTH, json={
        "name": "oa", "provider_type": "openai", "label": "k", "key": "sk-x",
    })
    assert r.status_code == 200
    r = await client.post("/admin/hermes/oauth/device-code?provider=oa", headers=AUTH)
    assert r.status_code == 400


# -- status -------------------------------------------------------------------

async def test_status_not_connected_before_connect(client):
    r = await client.get("/admin/hermes/oauth/status?provider=hermes-main", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["connected"] is False


@respx.mock
async def test_status_connected_after_poll(client):
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-tok", "refresh_token": "ref-tok", "expires_in": 3600}))

    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    state = r.json()["state"]
    await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})

    r = await client.get("/admin/hermes/oauth/status?provider=hermes-main", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["has_refresh_token"] is True
    assert body["needs_refresh"] is False  # 1h TTL, well outside the lead window


# -- refresh ------------------------------------------------------------------

@respx.mock
async def test_refresh_requires_prior_connect(client):
    r = await client.post("/admin/hermes/oauth/refresh", headers=AUTH,
                          json={"provider": "hermes-main"})
    assert r.status_code == 400


@respx.mock
async def test_refresh_rotates_token_on_pool_key(client):
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    # First the connect poll, then the refresh — both hit the token URL.
    respx.post(hermes_oauth.TOKEN_URL).mock(side_effect=[
        httpx.Response(200, json={
            "access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600}),
        httpx.Response(200, json={
            "access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}),
    ])

    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    state = r.json()["state"]
    await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})

    r = await client.post("/admin/hermes/oauth/refresh", headers=AUTH,
                          json={"provider": "hermes-main"})
    assert r.status_code == 200

    r = await client.get("/admin/providers/hermes-main/keys/default/secret", headers=AUTH)
    assert r.json()["secret"] == "hermes:acc-2"


@respx.mock
async def test_refresh_terminal_grant_forces_relogin(client):
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(side_effect=[
        httpx.Response(200, json={
            "access_token": "acc-1", "refresh_token": "ref-1", "expires_in": 3600}),
        httpx.Response(400, json={"error": "invalid_grant"}),
    ])

    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    state = r.json()["state"]
    await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})

    r = await client.post("/admin/hermes/oauth/refresh", headers=AUTH,
                          json={"provider": "hermes-main"})
    assert r.status_code == 401


# -- disconnect ---------------------------------------------------------------

@respx.mock
async def test_disconnect_clears_status(client):
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-tok", "refresh_token": "ref-tok", "expires_in": 3600}))

    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    state = r.json()["state"]
    await client.post("/admin/hermes/oauth/poll", headers=AUTH, json={"state": state})

    r = await client.request("DELETE", "/admin/hermes/oauth/disconnect", headers=AUTH,
                             json={"provider": "hermes-main"})
    assert r.status_code == 200
    assert r.json()["disconnected"] is True

    r = await client.get("/admin/hermes/oauth/status?provider=hermes-main", headers=AUTH)
    assert r.json()["connected"] is False
