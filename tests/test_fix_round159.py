"""Regression round 159 — the ``workbuddy`` provider had no way to log in.

Every other OAuth provider in wiwi ships a login entry point: Cline has
``build_auth_url`` + ``POST /admin/cline/oauth/login-url``, Hermes has
``request_device_code`` + ``POST /admin/hermes/oauth/device-code``. WorkBuddy
had neither — its admin surface was import/export/refresh only, so the *only*
way to obtain a credential was to have already logged in somewhere else and
copy the resulting ``auths/`` JSON in by hand.

That matters because a WorkBuddy credential is not a static API key: it is an
OAuth pair whose refresh grant rotates on use, and a dead account can only be
revived by re-logging in. With no login flow, a revoke left the operator with
nothing to do inside wiwi.

The upstream flow (three legs, live-verified against both realms) is:

1. ``POST {base}/v2/plugin/auth/state?platform=CLI`` -> ``data.state`` +
   ``data.authUrl`` (the login URL, which the user opens in a browser);
2. ``GET {base}/v2/plugin/auth/token?state=<state>`` -> tokens once approved,
   HTTP 200 ``{"code": 11217, "msg": "11217:login ing..."}`` while pending;
3. ``GET {base}/v2/plugin/login/account?state=<state>`` with the access token
   as a bearer -> the account identity (uid / nickname / enterpriseId).

The login URL is *returned by the upstream*, never pinned: it lives on the API
host itself (``copilot.tencent.com`` for CN, ``www.workbuddy.ai`` for global)
and is host-allowlisted before it reaches the operator, so a compromised or
misrouted response cannot turn the admin UI into an open redirector.
"""

from __future__ import annotations

import time

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
from wiwi.providers import workbuddy_auth as wba
from wiwi.server.app import create_app

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

CN_STATE = "https://copilot.tencent.com/v2/plugin/auth/state"
GB_STATE = "https://www.workbuddy.ai/v2/plugin/auth/state"
CN_TOKEN = "https://copilot.tencent.com/v2/plugin/auth/token"
CN_ACCOUNT = "https://copilot.tencent.com/v2/plugin/login/account"
GB_TOKEN = "https://www.workbuddy.ai/v2/plugin/auth/token"
GB_ACCOUNT = "https://www.workbuddy.ai/v2/plugin/login/account"

CN_AUTH_URL = "https://copilot.tencent.com/login?platform=CLI&state=st-1"
GB_AUTH_URL = "https://www.workbuddy.ai/login?platform=CLI&state=st-9"


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


def _state_response(url: str, state: str) -> httpx.Response:
    return httpx.Response(200, json={
        "code": 0, "msg": "OK", "data": {"state": state, "authUrl": url}})


def _token_response(access: str, *, refresh: str = "RT-1",
                    with_expiry: bool = True, **extra) -> httpx.Response:
    data: dict[str, object] = {"accessToken": access, "refreshToken": refresh}
    if with_expiry:
        data["expiresIn"] = 3600
    data.update(extra)
    return httpx.Response(200, json={"code": 0, "msg": "OK", "data": data})


async def _login(client, provider: str, realm: str) -> dict:
    """Drive /start and return its body (the SPA's first poll call)."""
    r = await client.post("/admin/workbuddy/login/start", headers=AUTH,
                          json={"provider": provider, "realm": realm})
    assert r.status_code == 200, r.text
    return r.json()


# -- layer 1: the login-URL guard ---------------------------------------------


def test_accepted_auth_url_allows_the_upstream_login_hosts():
    assert wba.accepted_auth_url(CN_AUTH_URL) == CN_AUTH_URL
    assert wba.accepted_auth_url(GB_AUTH_URL) == GB_AUTH_URL
    # The CN web Origin is a plausible place for the same login page.
    assert wba.accepted_auth_url("https://www.codebuddy.cn/login?state=x")


def test_accepted_auth_url_rejects_anything_off_the_allowlist():
    for bad in ("https://evil.example.com/login",
                "https://copilot.tencent.com.evil.example/login",
                "https://copilot.tencent.com@evil.example/login"):
        with pytest.raises(wba.WorkBuddyAuthError):
            wba.accepted_auth_url(bad)


def test_accepted_auth_url_requires_https_and_a_host():
    for bad in ("http://copilot.tencent.com/login", "", "/login?state=x"):
        with pytest.raises(wba.WorkBuddyAuthError):
            wba.accepted_auth_url(bad)


# -- layer 2: start_login ------------------------------------------------------


@respx.mock
async def test_start_login_cn_posts_to_the_cn_host_with_the_cn_origin():
    route = respx.post(CN_STATE).mock(
        return_value=_state_response(CN_AUTH_URL, "st-1"))
    started = await wba.start_login("cn")
    assert started.state == "st-1"
    assert started.auth_url == CN_AUTH_URL
    assert started.realm == "cn"
    req = route.calls.last.request
    assert "platform=CLI" in str(req.url)
    assert req.headers["origin"] == wba.ORIGIN_CN
    assert req.headers["user-agent"].startswith("CLI/")


@respx.mock
async def test_start_login_global_posts_to_the_global_host():
    route = respx.post(GB_STATE).mock(
        return_value=_state_response(GB_AUTH_URL, "st-9"))
    started = await wba.start_login("global")
    assert started.realm == "global"
    assert started.auth_url == GB_AUTH_URL
    assert route.calls.last.request.headers["origin"] == wba.ORIGIN_GLOBAL


@respx.mock
async def test_start_login_raises_on_a_business_error_envelope():
    respx.post(CN_STATE).mock(return_value=httpx.Response(
        200, json={"code": 11201, "msg": "11201:bad platform"}))
    with pytest.raises(wba.WorkBuddyAuthError):
        await wba.start_login("cn")


@respx.mock
async def test_start_login_refuses_a_foreign_login_url():
    respx.post(CN_STATE).mock(return_value=_state_response(
        "https://evil.example.com/login?state=st-1", "st-1"))
    with pytest.raises(wba.WorkBuddyAuthError):
        await wba.start_login("cn")


# -- layer 3: poll_login -------------------------------------------------------


@respx.mock
async def test_poll_login_returns_none_while_the_user_has_not_approved():
    respx.get(CN_TOKEN).mock(return_value=httpx.Response(
        200, json={"code": 11217, "msg": "11217:login ing..."}))
    assert await wba.poll_login("st-1", "cn") is None


@respx.mock
async def test_poll_login_builds_a_usable_credential_on_approval():
    respx.get(GB_TOKEN).mock(return_value=_token_response(
        "AT-1", domain="workbuddy.ai"))
    respx.get(GB_ACCOUNT).mock(return_value=httpx.Response(200, json={
        "code": 0, "msg": "OK",
        "data": {"uid": "u9", "nickname": "neo", "enterpriseId": "e1"}}))
    auth = await wba.poll_login("st-9", "global")
    assert auth is not None
    assert auth.access_token == "AT-1"
    assert auth.refresh_token == "RT-1"
    assert (auth.uid, auth.nickname, auth.enterprise_id) == ("u9", "neo", "e1")
    assert auth.region() == "global"
    assert auth.expires_at > time.time() + 3000
    # Round-trips through the same parser the provider-key path uses.
    assert wba.parse_auth(auth.to_secret()).access_token == "AT-1"


@respx.mock
async def test_poll_login_keeps_a_domainless_global_token_on_the_global_host():
    """An empty domain reads as CN, so a global token would 401 on the CN host.

    The upstream may omit ``domain`` entirely; the realm the operator selected
    is then the only thing that knows which host minted the token.
    """
    respx.get(GB_TOKEN).mock(return_value=_token_response("AT-2"))
    respx.get(GB_ACCOUNT).mock(return_value=httpx.Response(
        401, text="<html>401 Authorization Required</html>"))
    auth = await wba.poll_login("st-9", "global")
    assert auth is not None
    assert auth.domain
    assert auth.region() == "global"
    assert auth.chat_base() == wba.CHAT_BASE_GLOBAL


@respx.mock
async def test_poll_login_degrades_when_the_account_leg_fails():
    """A token is usable without uid/nickname — identity is cosmetic."""
    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-3"))
    respx.get(CN_ACCOUNT).mock(return_value=httpx.Response(500, text="boom"))
    auth = await wba.poll_login("st-1", "cn")
    assert auth is not None
    assert auth.access_token == "AT-3"
    assert auth.uid == ""
    assert auth.region() == "cn"


@respx.mock
async def test_poll_login_falls_forward_when_expires_in_is_missing():
    """A missing ``expiresIn`` must not record the token as already due."""
    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-4", with_expiry=False))
    respx.get(CN_ACCOUNT).mock(return_value=httpx.Response(500, text="boom"))
    auth = await wba.poll_login("st-1", "cn")
    assert auth is not None
    assert auth.expires_at > time.time() + wba.REFRESH_LEAD_S


# -- layer 4: the admin surface ------------------------------------------------


@respx.mock
async def test_admin_login_start_returns_the_upstream_url_and_a_session(client):
    respx.post(CN_STATE).mock(return_value=_state_response(CN_AUTH_URL, "st-1"))
    body = await _login(client, "workbuddy-main", "cn")
    assert body["auth_url"] == CN_AUTH_URL
    assert body["realm"] == "cn"
    assert body["provider"] == "workbuddy-main"
    assert body["state"]


async def test_admin_login_endpoints_require_the_master_key(client):
    r = await client.post("/admin/workbuddy/login/start", json={"realm": "cn"})
    assert r.status_code == 401
    r = await client.post("/admin/workbuddy/login/poll", json={"state": "x"})
    assert r.status_code == 401


@respx.mock
async def test_admin_login_start_rejects_an_unknown_realm(client):
    r = await client.post("/admin/workbuddy/login/start", headers=AUTH,
                          json={"provider": "wb-x", "realm": "moon"})
    assert r.status_code == 400


async def test_admin_login_poll_unknown_state_is_404(client):
    r = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                          json={"state": "not-a-session"})
    assert r.status_code == 404


@respx.mock
async def test_admin_login_poll_reports_pending_without_consuming_the_session(client):
    respx.post(CN_STATE).mock(return_value=_state_response(CN_AUTH_URL, "st-1"))
    started = await _login(client, "workbuddy-main", "cn")
    respx.get(CN_TOKEN).mock(return_value=httpx.Response(
        200, json={"code": 11217, "msg": "11217:login ing..."}))
    r = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                          json={"state": started["state"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"pending": True}
    # The session survived the pending poll, so the SPA can poll again.
    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-7"))
    respx.get(CN_ACCOUNT).mock(return_value=httpx.Response(200, json={
        "code": 0, "data": {"uid": "u7", "nickname": "trinity"}}))
    r2 = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                           json={"state": started["state"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["connected"] is True
    # Consumed: a further poll is a 404, not a replay of the same login.
    r3 = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                           json={"state": started["state"]})
    assert r3.status_code == 404


@respx.mock
async def test_admin_login_poll_creates_the_provider_and_stores_the_credential(client):
    respx.post(CN_STATE).mock(return_value=_state_response(CN_AUTH_URL, "st-1"))
    started = await _login(client, "workbuddy-login", "cn")
    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-LOGIN"))
    respx.get(CN_ACCOUNT).mock(return_value=httpx.Response(200, json={
        "code": 0, "data": {"uid": "u-l", "nickname": "login-acct"}}))
    r = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                          json={"state": started["state"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["provider"] == "workbuddy-login"
    assert body["key_label"] == "login-acct"

    listing = (await client.get("/admin/workbuddy/accounts", headers=AUTH)).json()
    acct = next(a for a in listing["accounts"] if a["provider"] == "workbuddy-login")
    assert acct["valid_auth"] is True
    assert acct["uid"] == "u-l"
    assert acct["nickname"] == "login-acct"
    assert acct["has_refresh_token"] is True


@respx.mock
async def test_admin_login_poll_refuses_a_provider_of_another_type(client):
    respx.post(CN_STATE).mock(return_value=_state_response(CN_AUTH_URL, "st-1"))
    started = await _login(client, "p1", "cn")  # p1 is an openai provider
    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-X"))
    respx.get(CN_ACCOUNT).mock(return_value=httpx.Response(200, json={
        "code": 0, "data": {"uid": "u-x", "nickname": "whatever"}}))
    r = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                          json={"state": started["state"]})
    assert r.status_code == 409


@respx.mock
async def test_admin_login_poll_relogin_rotates_the_same_key_in_place(client):
    """A second login for the same uid must not append a duplicate pool key."""
    respx.post(CN_STATE).mock(return_value=_state_response(CN_AUTH_URL, "st-1"))
    respx.get(CN_ACCOUNT).mock(return_value=httpx.Response(200, json={
        "code": 0, "data": {"uid": "u-r", "nickname": "rot"}}))

    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-A"))
    first = await _login(client, "wb-rot", "cn")
    r1 = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                           json={"state": first["state"]})
    assert r1.status_code == 200, r1.text

    respx.get(CN_TOKEN).mock(return_value=_token_response("AT-B"))
    second = await _login(client, "wb-rot", "cn")
    r2 = await client.post("/admin/workbuddy/login/poll", headers=AUTH,
                           json={"state": second["state"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["key_label"] == r1.json()["key_label"]

    listing = (await client.get("/admin/workbuddy/accounts", headers=AUTH)).json()
    rows = [a for a in listing["accounts"] if a["provider"] == "wb-rot"]
    assert len(rows) == 1, f"re-login duplicated the key: {rows}"
    # Export emits the raw access token (``accessToken``), not the stored
    # auth-JSON blob — so this compares the token directly. Reaching this line
    # at all also proves the rotated secret still parses: export skips a key
    # whose secret is not valid auth JSON, which would leave the list empty.
    exported = (await client.get("/admin/workbuddy/export", headers=AUTH,
                                 params={"provider": "wb-rot"})).json()
    assert [a["auth"]["accessToken"] for a in exported["accounts"]] == ["AT-B"]
