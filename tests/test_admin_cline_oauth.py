"""Cline OAuth admin endpoints: login URL, paste-code connect, token status,
and manual/proactive refresh — persisted via ConfigStore settings."""

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
from wiwi.providers.cline_oauth import CLINE_API_BASE

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="cline-prov", provider="cline",
                               base_url="https://api.cline.bot/api/v1",
                               keys=[KeyDef(label="default",
                                            key="workos:stale-token")])],
        model_list=[ModelEntry(
            model_name="cline-model",
            wiwi_params=DeploymentParams(provider="cline-prov",
                                         model="z-ai/glm-5.2"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


# -- auth guard ---------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/cline/oauth/status"),
    ("POST", "/admin/cline/oauth/login-url"),
    ("POST", "/admin/cline/oauth/connect"),
    ("POST", "/admin/cline/oauth/refresh"),
    ("DELETE", "/admin/cline/oauth/disconnect"),
])
async def test_cline_oauth_requires_admin(method, path):
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.request(method, path, json={"code": "x"})
            assert r.status_code == 401


# -- login url ----------------------------------------------------------------


async def test_login_url_returns_authorize_link(client):
    r = await client.post("/admin/cline/oauth/login-url", headers=AUTH,
                          json={"callback_url": "http://localhost:9000/cb"})
    assert r.status_code == 200
    body = r.json()
    assert body["auth_url"].startswith(f"{CLINE_API_BASE}/auth/authorize")
    assert "localhost" in body["auth_url"]


# -- connect: paste code -> provider key updated --------------------------------


def _code_payload(access="acc-new-1234567890", refresh="ref-new",
                  expires="2030-01-01T00:00:00.000Z") -> str:
    import base64
    import json
    payload = {"accessToken": access, "refreshToken": refresh,
               "email": "u@x.io", "expiresAt": expires}
    return base64.b64encode(json.dumps(payload).encode()).decode()


async def test_connect_updates_provider_key(client):
    code = _code_payload()
    r = await client.post("/admin/cline/oauth/connect", headers=AUTH,
                          json={"provider": "cline-prov", "code": code})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "u@x.io"
    assert body["access_token_masked"].startswith("acc-n")

    # The provider key secret must now be the raw new access token
    # (adapter re-applies the workos: prefix at request time).
    r2 = await client.get("/admin/providers", headers=AUTH)
    keys = {p["name"]: p for p in r2.json()["providers"]}["cline-prov"]["keys"]
    assert keys[0]["masked"] != ""
    assert keys[0]["label"] == "default"


async def test_connect_unknown_provider_404(client):
    code = _code_payload()
    r = await client.post("/admin/cline/oauth/connect", headers=AUTH,
                          json={"provider": "nope", "code": code})
    assert r.status_code == 404


async def test_connect_bad_code_400(client):
    r = await client.post("/admin/cline/oauth/connect", headers=AUTH,
                          json={"provider": "cline-prov", "code": "garbage"})
    assert r.status_code == 400


# -- status --------------------------------------------------------------------


async def test_status_before_connect_is_empty(client):
    r = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                         headers=AUTH)
    assert r.status_code == 200
    assert r.json()["connected"] is False


async def test_status_after_connect(client):
    await client.post("/admin/cline/oauth/connect", headers=AUTH,
                      json={"provider": "cline-prov",
                            "code": _code_payload(expires="2030-01-01T00:00:00Z")})
    r = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                         headers=AUTH)
    body = r.json()
    assert body["connected"] is True
    assert body["email"] == "u@x.io"
    assert body["needs_refresh"] is False  # 2030 expiry is far away


# -- refresh --------------------------------------------------------------------


@respx.mock
async def test_refresh_roundtrip_persists_rotated_token(client):
    await client.post("/admin/cline/oauth/connect", headers=AUTH,
                      json={"provider": "cline-prov",
                            "code": _code_payload(access="acc-old-1234567890",
                                                  refresh="ref-old")})
    route = respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        json={"data": {"accessToken": "acc-new-1234567890", "refreshToken": "ref-new",
                       "expiresAt": "2030-06-01T00:00:00.000Z"}})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": "cline-prov"})
    assert r.status_code == 200
    assert route.called
    body = r.json()
    assert body["access_token_masked"].startswith("acc-n")

    # status should reflect the new expiry
    r2 = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                          headers=AUTH)
    assert r2.json()["connected"] is True


@respx.mock
async def test_refresh_unrecoverable_reports_relogin(client):
    await client.post("/admin/cline/oauth/connect", headers=AUTH,
                      json={"provider": "cline-prov",
                            "code": _code_payload(refresh="ref-dead")})
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        status_code=400, json={"error": "invalid_grant"})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": "cline-prov"})
    assert r.status_code == 401  # re-login required
    assert "invalid_grant" in r.json()["error"]["message"]
    assert r.json()["error"]["type"] == "authentication_error"


@respx.mock
async def test_refresh_transient_error_returns_502(client):
    await client.post("/admin/cline/oauth/connect", headers=AUTH,
                      json={"provider": "cline-prov",
                            "code": _code_payload(refresh="ref-ok")})
    respx.post(f"{CLINE_API_BASE}/auth/refresh").respond(
        status_code=502, text="bad gateway")
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": "cline-prov"})
    assert r.status_code == 502


@respx.mock
async def test_refresh_when_never_connected_returns_400(client):
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": "cline-prov"})
    assert r.status_code == 400


# -- disconnect -----------------------------------------------------------------


async def test_disconnect_clears_oauth_state(client):
    # connect first
    await client.post("/admin/cline/oauth/connect", headers=AUTH,
                      json={"provider": "cline-prov", "code": _code_payload()})
    # verify connected
    r = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                         headers=AUTH)
    assert r.json()["connected"] is True
    # disconnect
    r = await client.request("DELETE", "/admin/cline/oauth/disconnect",
                             headers=AUTH, json={"provider": "cline-prov"})
    assert r.status_code == 200
    assert r.json()["disconnected"] is True
    # status should now show not connected
    r = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                         headers=AUTH)
    assert r.json()["connected"] is False


async def test_disconnect_unknown_provider_404(client):
    r = await client.request("DELETE", "/admin/cline/oauth/disconnect",
                             headers=AUTH, json={"provider": "nope"})
    assert r.status_code == 404


async def test_disconnect_when_not_connected_is_idempotent(client):
    r = await client.request("DELETE", "/admin/cline/oauth/disconnect",
                             headers=AUTH, json={"provider": "cline-prov"})
    assert r.status_code == 200
    assert r.json()["disconnected"] is True


# -- empty key pool guard -------------------------------------------------------


async def test_connect_rejected_when_no_keys(client):
    # Delete the only key to simulate an empty pool.
    await client.delete(
        "/admin/providers/cline-prov/keys/default", headers=AUTH)
    r = await client.post("/admin/cline/oauth/connect", headers=AUTH,
                          json={"provider": "cline-prov",
                                "code": _code_payload()})
    assert r.status_code == 400
    assert "no keys" in r.json()["error"]["message"]
    # the OAuth state must NOT be persisted (no misleading "connected")
    r2 = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                          headers=AUTH)
    assert r2.json()["connected"] is False


# -- provider deletion cleans up OAuth state ------------------------------------


def _config_no_models() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="cline-prov", provider="cline",
                               base_url="https://api.cline.bot/api/v1",
                               keys=[KeyDef(label="default",
                                            key="workos:stale-token")])],
        model_list=[],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client_no_models():
    app = app_mod.create_app(_config_no_models())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def test_provider_delete_cleans_oauth_settings(client_no_models):
    await client_no_models.post("/admin/cline/oauth/connect", headers=AUTH,
                                json={"provider": "cline-prov",
                                      "code": _code_payload()})
    r = await client_no_models.delete("/admin/providers/cline-prov",
                                       headers=AUTH)
    assert r.status_code == 200


# -- auto-connect (redirect flow) ---------------------------------------------


async def test_auto_connect_returns_callback_url(client):
    """auto-connect returns an auth_url whose callback_url points at wiwi's
    own /cline/oauth/callback (not the SPA), and a state token is stored."""
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "cline-prov"})
    assert r.status_code == 200
    body = r.json()
    assert "auth_url" in body
    assert "state" in body
    assert body["provider"] == "cline-prov"
    # The callback URL embedded in the Cline auth URL must be our endpoint.
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(body["auth_url"]).query)
    callback = qs.get("callback_url", [""])[0]
    assert "/cline/oauth/callback" in callback
    assert f"state={body['state']}" in callback


async def test_auto_connect_unknown_provider_404(client):
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "nope"})
    assert r.status_code == 404


async def test_auto_connect_requires_admin(client):
    r = await client.post("/admin/cline/oauth/auto-connect",
                          json={"provider": "cline-prov"})
    assert r.status_code == 401


async def test_callback_persists_tokens_and_redirects(client):
    """The redirect callback decodes the code, stores tokens, and 302s to
    the SPA with ?cline_connected=1."""
    # Step 1: initiate auto-connect to get a state token.
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "cline-prov"})
    assert r.status_code == 200
    state = r.json()["state"]
    # Step 2: simulate Cline redirecting back with a valid code.
    code = _code_payload()
    r2 = await client.get(
        f"/cline/oauth/callback?code={code}&state={state}",
        follow_redirects=False)
    assert r2.status_code == 302
    loc = r2.headers["location"]
    assert "cline_connected=1" in loc
    assert "cline_email=u%40x.io" in loc or "cline_email=u@x.io" in loc
    # Step 3: status should now show connected.
    r3 = await client.get("/admin/cline/oauth/status?provider=cline-prov",
                          headers=AUTH)
    assert r3.json()["connected"] is True
    assert r3.json()["email"] == "u@x.io"


async def test_callback_invalid_state_returns_error(client):
    """An unknown/expired state token redirects with an error flag."""
    code = _code_payload()
    r = await client.get(
        f"/cline/oauth/callback?code={code}&state=bogus-state",
        follow_redirects=False)
    # Missing/invalid state → 400 JSON (not a redirect).
    assert r.status_code == 400


async def test_callback_bad_code_redirects_with_error(client):
    """A valid state but undecodable code redirects to SPA with error."""
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "cline-prov"})
    state = r.json()["state"]
    r2 = await client.get(
        f"/cline/oauth/callback?code=garbage&state={state}",
        follow_redirects=False)
    assert r2.status_code == 302
    assert "cline_error=" in r2.headers["location"]


async def test_callback_state_is_single_use(client):
    """The state token is consumed on first use; a second callback with the
    same state fails."""
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "cline-prov"})
    state = r.json()["state"]
    code = _code_payload()
    r1 = await client.get(
        f"/cline/oauth/callback?code={code}&state={state}",
        follow_redirects=False)
    assert r1.status_code == 302
    # Second use of the same state must fail.
    r2 = await client.get(
        f"/cline/oauth/callback?code={code}&state={state}",
        follow_redirects=False)
    assert r2.status_code == 400


async def test_auto_connect_custom_return_path(client):
    """return_path is honored when it's a /console path."""
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "cline-prov",
                                "return_path": "/console/providers/cline-prov"})
    assert r.status_code == 200
    state = r.json()["state"]
    code = _code_payload()
    r2 = await client.get(
        f"/cline/oauth/callback?code={code}&state={state}",
        follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"].startswith("/console/providers/cline-prov")


async def test_auto_connect_open_redirect_guard(client):
    """A non-/console return_path is coerced back to /console/oauth."""
    r = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                          json={"provider": "cline-prov",
                                "return_path": "https://evil.example/"})
    assert r.status_code == 200
    state = r.json()["state"]
    code = _code_payload()
    r2 = await client.get(
        f"/cline/oauth/callback?code={code}&state={state}",
        follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"].startswith("/console/oauth")
