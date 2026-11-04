"""Round 193 — the Playground session cookie expired with no renewal path.

An active, logged-in Playground user hit a hard 401 after the 7-day session
cookie TTL: ``wiwi_session`` was signed once at login/signup
(``SESSION_TTL = 7 * 24 * 3600`` in ``wiwi/auth/users.py``) and no code path
ever re-signed it. ``verify_session`` rejects the expired cookie, so
``resolve_current_user`` returns ``None``, the Playground route answers
``401 authentication required``, and the SPA has neither a refresh endpoint
to call nor any grace in the UI — the only recovery was manual re-login.
The 24h playground *virtual key* was already self-healing (round 103: the
bearer cache re-validates via ``authenticate`` and re-mints); the *session*
was the token that never refreshed.

Fix (sliding renewal): the response to every request that authenticates via a
session cookie carries a freshly-signed ``wiwi_session`` cookie once the
cookie is at least half-way through its TTL (``SESSION_RENEW_AFTER`` =
half of ``SESSION_TTL``). The renewal is done centrally in
``RequestIdMiddleware`` — an ASGI-level ``http.response.start`` header
append — so *every* response surface (the Playground's streaming SSE, JSON
API responses, error responses alike) renews the session, only when the
identity actually came from a cookie, and never for synthetic-master (bearer)
or logged-out callers. Re-signing past the original expiry is impossible by
construction: the middleware re-signs only when ``verify_session`` succeeds,
which itself rejects expired cookies.

Two edges are pinned here:

* A logged-out / no-cookie / bearer-stream caller gets *no* ``Set-Cookie``
  (the renewal is stateless; there is no backing session row to resurrect).
* An expired cookie cannot be renewed into a live one — ``verify_session``
  returns ``None`` first, so the 401 path is unchanged.
"""

from __future__ import annotations

import time

import httpx
from asgi_lifespan import LifespanManager

from wiwi.auth.users import SESSION_TTL, sign_session, verify_session
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


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


async def _client():
    app = create_app(_config())
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t")
    _orig_close = client.aclose

    async def _close_then_lifespan():
        try:
            await _orig_close()
        finally:
            await lm.__aexit__(None, None, None)

    client.aclose = _close_then_lifespan  # type: ignore[method-assign]
    client._transport.app = app  # type: ignore[attr-defined]
    return client


async def _signup(client: httpx.AsyncClient) -> httpx.AsyncClient:
    r = await client.post("/auth/signup", json={
        "username": "pguser", "password": "password1"})
    assert r.status_code == 201, r.text
    return client


def _cookie_from(resp: httpx.Response) -> str:
    """The ``wiwi_session`` cookie value on a response, or ''."""
    raw = resp.headers.get("set-cookie", "")
    # httpx merges multiple Set-Cookie headers; find the session one.
    for part in raw.split(", "):
        if part.startswith("wiwi_session="):
            return part.split(";", 1)[0].split("=", 1)[1]
    return ""


def _set_cookie_header(resp: httpx.Response) -> str:
    return resp.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# 1. Sliding renewal: a near-expiry cookie is re-signed on every response
# ---------------------------------------------------------------------------


async def test_session_cookie_renews_when_halfway_through_ttl():
    """A cookie older than half its TTL is re-signed with a full TTL.

    This is the user-visible fix: an active Playground user sending on a
    4-day-old cookie keeps getting fresh cookies back, so the 7-day hard
    expiry never lands while they are active.
    """
    client = await _signup(await _client())
    app = client._transport.app  # type: ignore[attr-defined]
    secret = app.state.wiwi.users._secret
    r = await client.get("/auth/me")
    uid = r.json()["user"]["id"]

    # A cookie 5 days into its 7-day life (well past the half-way mark).
    old_exp = time.time() + SESSION_TTL - 5 * 24 * 3600
    old = sign_session(secret, uid, "user", expires=old_exp)
    resp = await client.get("/auth/me", headers={"Cookie": f"wiwi_session={old}"})
    assert resp.status_code == 200, resp.text
    renewed = _cookie_from(resp)
    assert renewed, (
        f"no wiwi_session Set-Cookie on a near-expiry response "
        f"({resp.headers.get('set-cookie')!r}) — the session cannot renew")
    parsed = verify_session(secret, renewed)
    assert parsed is not None, "renewed cookie does not verify"
    new_exp = parsed[2]
    # The renewal must carry a (near-)full TTL, not just re-emit the old one.
    assert new_exp > old_exp + 24 * 3600, (
        f"renewed expiry {new_exp} is not a full TTL past {old_exp}")
    assert new_exp >= time.time() + SESSION_TTL - 60
    assert renewed != old
    await client.aclose()


async def test_fresh_session_cookie_is_not_re_set():
    """A cookie still in the first half of its life is left alone.

    Re-signing every response would be a cookie storm (and churn the cookie
    jar mid-stream); the threshold keeps renewal — and therefore the fix —
    invisible for a fresh session.
    """
    client = await _signup(await _client())
    app = client._transport.app  # type: ignore[attr-defined]
    secret = app.state.wiwi.users._secret
    r = await client.get("/auth/me")
    uid = r.json()["user"]["id"]

    fresh = sign_session(secret, uid, "user", expires=time.time() + SESSION_TTL)
    resp = await client.get("/auth/me", headers={"Cookie": f"wiwi_session={fresh}"})
    assert resp.status_code == 200, resp.text
    assert _cookie_from(resp) == "", (
        f"fresh cookie was re-set ({resp.headers.get('set-cookie')!r})")
    await client.aclose()


async def test_expired_session_cookie_cannot_renew_into_a_live_one():
    """An expired cookie stays a 401 — renewal never resurrects it.

    The middleware re-signs only when ``verify_session`` succeeds, and that
    function rejects expired cookies, so the hard-expiry path is unchanged:
    this pins that the fix does not turn the TTL into a decorative number.
    """
    client = await _signup(await _client())
    app = client._transport.app  # type: ignore[attr-defined]
    secret = app.state.wiwi.users._secret
    r = await client.get("/auth/me")
    uid = r.json()["user"]["id"]

    dead = sign_session(secret, uid, "user", expires=time.time() - 60)
    resp = await client.get("/auth/me", headers={"Cookie": f"wiwi_session={dead}"})
    assert resp.status_code == 200, resp.text  # /auth/me is anonymous-tolerant
    assert resp.json()["user"] is None
    assert _cookie_from(resp) == "", (
        f"expired cookie was renewed into {resp.headers.get('set-cookie')!r}")
    await client.aclose()


# ---------------------------------------------------------------------------
# 2. Renewal only for cookie sessions — never for bearer / logged-out callers
# ---------------------------------------------------------------------------


async def test_no_session_cookie_means_no_renewal():
    """Anonymous callers get no Set-Cookie.

    There is no backing session row to resurrect; the renewal is a stateless
    re-sign of a verified cookie, so a request without one must observe
    nothing.
    """
    client = await _client()
    resp = await client.get("/auth/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"] is None
    assert _cookie_from(resp) == "", (
        f"anonymous caller received a session cookie: "
        f"{resp.headers.get('set-cookie')!r}")
    await client.aclose()


async def test_bearer_caller_gets_no_session_cookie():
    """A master-bearer admin is not a cookie session: no cookie is minted.

    ``resolve_current_user`` returns the synthetic master actor for a bearer;
    the renewal must not turn bearer auth into a standing session cookie
    (that would outlive the bearer's rotation story and show up as an
    unexpected credential in the response).
    """
    client = await _client()
    resp = await client.get("/auth/me", headers={
        "Authorization": f"Bearer {MASTER}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["id"] == "master"
    assert _cookie_from(resp) == "", (
        f"bearer caller received a session cookie: "
        f"{resp.headers.get('set-cookie')!r}")
    await client.aclose()


async def test_renewal_constants():
    """Pin the tuning contract: SESSION_TTL is 7 days, renewal at half."""
    from wiwi.auth.users import SESSION_RENEW_AFTER_S

    assert SESSION_TTL == 7 * 24 * 3600
    assert SESSION_RENEW_AFTER_S == SESSION_TTL // 2
