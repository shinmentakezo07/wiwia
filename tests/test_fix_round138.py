"""Round 138: Hermes (Nous Portal) OAuth admin-flow correctness.

Seven defects in the multi-account Hermes connect surface, all reachable from a
single operator click sequence in the console:

1. **the credential blob was stored as the pool-key label** — ``/poll`` called
   ``_unique_hermes_key_label(state, provider, secret)`` with the whole
   ``hermes:{...}`` blob instead of the account label. Pool-key labels are NOT
   masked (unlike ``secret``), so the plaintext ``access_token`` and
   ``refresh_token`` were echoed by ``/poll``, ``/status``, ``/admin/providers``,
   every request log row and the auto-refresh log line — and permanently, because
   ``_update_secret`` only rewrites ``k.secret``.
2. **the ``hermes:pending`` bootstrap key was created and never removed** — it
   decodes to no credential, so ``credential_bearer`` fell through and sent
   ``Authorization: Bearer pending`` forever, in rotation, burning a guaranteed
   401 every cycle.
3. **``/refresh``'s ``except AttributeError`` fallback consumed the single-use
   grant and then 500'd** — it called ``state.hermes_refresh._update_secret`` on
   the very object it had just proven was ``None``, *after* Nous had rotated the
   refresh token. One admin click, one dead account.
4. **whole-pool disconnect was memory-only** — the ``provider_keys`` rows were
   left behind, so ``ConfigStore.load_all()`` brought every account back on the
   next restart.
5. **re-login appended a duplicate key** instead of rotating the account in
   place, leaving the old key holding a grant the new login already consumed.
6. **``request_device_code()`` blocked the event loop** for up to 30 s (AUDIT
   #426 threaded the other two call sites but missed this one, the *first* one).
7. **per-account refresh state was never pruned** — ``_locks`` /
   ``_generations`` / the circuit breaker grew without bound, so a forgotten
   account could never be forgotten again cleanly.
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time

import httpx
import orjson
import pytest
import respx
import sqlalchemy as sa
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


def _jwt(email: str) -> str:
    """A fake JWT whose payload carries *email* — ``_hermes_account_label`` reads it."""
    payload = base64.urlsafe_b64encode(orjson.dumps({"email": email})).decode().rstrip("=")
    return f"header.{payload}.signature"


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
async def ctx():
    """The httpx client plus the live AppState, for in-memory assertions."""
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app.state.wiwi


async def _db_secrets(state, provider: str) -> list[tuple[str, str]]:
    async with state.config_store.engine.begin() as conn:
        res = await conn.execute(
            sa.text("SELECT label, secret FROM provider_keys"
                    " WHERE provider_name = :p ORDER BY label"),
            {"p": provider})
        return [(r[0], r[1]) for r in res.fetchall()]


async def _connect(client, provider: str, email: str, *,
                   access: str | None = None, refresh: str = "ref-1") -> str:
    """Run one full device-code + poll cycle; return the resulting key label."""
    access = access or _jwt(email)
    with respx.mock:
        respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
            return_value=httpx.Response(200, json=DEVICE_CODE))
        respx.post(hermes_oauth.TOKEN_URL).mock(
            return_value=httpx.Response(200, json={
                "access_token": access, "refresh_token": refresh,
                "expires_in": 3600}))
        r = await client.post(f"/admin/hermes/oauth/device-code?provider={provider}",
                              headers=AUTH)
        assert r.status_code == 200, r.text
        r = await client.post("/admin/hermes/oauth/poll", headers=AUTH,
                              json={"state": r.json()["state"]})
        assert r.status_code == 200, r.text
        return str(r.json()["key_label"])


# -- #1: the pool-key label must never be the credential blob ------------------


@respx.mock
async def test_poll_label_is_the_account_label_not_the_credential_blob(ctx):
    """Bug #1: /poll used the encoded credential as the pool-key label.

    Labels are not masked, so this leaked both plaintext tokens into /poll,
    /status, the provider listing and every request log row — permanently, since
    a rotation only rewrites ``k.secret``.
    """
    client, state = ctx
    access = _jwt("alpha@example.com")

    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": access, "refresh_token": "ref-secret-grant",
            "expires_in": 3600}))

    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    assert r.status_code == 200
    r = await client.post("/admin/hermes/oauth/poll", headers=AUTH,
                          json={"state": r.json()["state"]})
    assert r.status_code == 200
    label = r.json()["key_label"]

    # The label is the account label the CLI would show...
    assert label == "alpha@example.com"
    # ...and neither plaintext token appears in ANY label, in memory...
    for k in state.router.providers["hermes-main"].keys:
        assert "ref-secret-grant" not in k.label
        assert access not in k.label
        assert not k.label.startswith("hermes:")
    # ...in the poll response, the status endpoint, or the provider listing.
    assert "ref-secret-grant" not in r.text and access not in r.text
    r = await client.get("/admin/hermes/oauth/status?provider=hermes-main",
                         headers=AUTH)
    assert r.status_code == 200
    assert "ref-secret-grant" not in r.text and access not in r.text
    r = await client.get("/admin/providers", headers=AUTH)
    assert "ref-secret-grant" not in r.text and access not in r.text
    # ...nor in the DB row's label column.
    rows = await _db_secrets(state, "hermes-main")
    assert any(lbl == "alpha@example.com" for lbl, _ in rows)
    for lbl, _ in rows:
        assert "ref-secret-grant" not in lbl and access not in lbl


# -- #2: the ``hermes:pending`` bootstrap key must not survive a connect ------


@respx.mock
async def test_poll_drops_the_bootstrap_placeholder_key(ctx):
    """Bug #2: /device-code seeds ``hermes:pending``; /poll only APPENDED.

    ``decode_credential("hermes:pending")`` is None, so the placeholder stayed
    enabled+active in smooth-WRR rotation forever and sent
    ``Authorization: Bearer pending`` — a guaranteed 401 per cycle.
    """
    client, state = ctx
    # A brand-new provider: /device-code must bootstrap it with the placeholder.
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-new",
                          headers=AUTH)
    assert r.status_code == 200
    bootstrapped = state.router.providers["hermes-new"]
    assert [k.secret for k in bootstrapped.keys] == ["hermes:pending"]

    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": _jwt("boot@example.com"),
            "refresh_token": "ref-boot", "expires_in": 3600}))
    r = await client.post("/admin/hermes/oauth/poll", headers=AUTH,
                          json={"state": r.json()["state"]})
    assert r.status_code == 200, r.text

    keys = state.router.providers["hermes-new"].keys
    assert [k.secret for k in keys if k.secret == "hermes:pending"] == []
    assert len(keys) == 1
    assert hermes_oauth.decode_credential(keys[0].secret)["access_token"]
    # ...and it is gone from the DB too, or it comes back on restart.
    assert all(secret != "hermes:pending"
               for _, secret in await _db_secrets(state, "hermes-new"))


@respx.mock
async def test_disconnect_can_clear_a_lone_placeholder(ctx):
    """Bug #2 (second half): /disconnect used to leave the placeholder behind,
    because removal required a decodable credential — an operator could never
    fully un-bootstrap the account."""
    client, state = ctx
    respx.post(hermes_oauth.DEVICE_CODE_URL).mock(
        return_value=httpx.Response(200, json=DEVICE_CODE))
    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-new",
                          headers=AUTH)
    assert state.router.providers["hermes-new"].keys[0].secret == "hermes:pending"

    r = await client.request("DELETE", "/admin/hermes/oauth/disconnect",
                             headers=AUTH, json={"provider": "hermes-new"})
    assert r.status_code == 200, r.text
    assert r.json()["disconnected"] == 1
    assert state.router.providers["hermes-new"].keys == []
    assert await _db_secrets(state, "hermes-new") == []


# -- #3: /refresh must not consume the grant when the worker is missing --------


@respx.mock
async def test_refresh_without_lifespan_worker_does_not_burn_the_grant(ctx):
    """Bug #3: the ``except AttributeError`` fallback ran the exchange FIRST and
    then called ``state.hermes_refresh._update_secret`` — on the None it had just
    proven — so the freshly rotated token was discarded with a 500 and the pool
    key kept the now-consumed single-use grant."""
    client, state = ctx
    label = await _connect(client, "hermes-main", "gamma@example.com",
                           refresh="ref-single-use")
    # Simulate the pre-lifespan / minimal-state condition the fallback targeted.
    state.hermes_refresh = None

    fresh_access = _jwt("gamma@example.com")
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": fresh_access, "refresh_token": "ref-rotated",
            "expires_in": 3600}))
    r = await client.post("/admin/hermes/oauth/refresh", headers=AUTH,
                          json={"provider": "hermes-main", "key_label": label})

    assert r.status_code == 200, r.text
    # The rotated credential landed on the pool key (and the DB), not nowhere.
    key = next(k for k in state.router.providers["hermes-main"].keys
               if k.label == label)
    cred = hermes_oauth.decode_credential(key.secret)
    assert cred["access_token"] == fresh_access
    assert cred["refresh_token"] == "ref-rotated"
    rows = dict(await _db_secrets(state, "hermes-main"))
    assert fresh_access in rows[label]


@respx.mock
async def test_refresh_surfaces_relogin_required_instead_of_crashing(ctx):
    """Bug #3 (second half): the old code also masked genuine AttributeErrors
    raised inside ``_refresh_locked`` after the grant was consumed."""
    client, state = ctx
    label = await _connect(client, "hermes-main", "delta@example.com",
                           refresh="ref-dead")
    state.hermes_refresh = None
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    r = await client.post("/admin/hermes/oauth/refresh", headers=AUTH,
                          json={"provider": "hermes-main", "key_label": label})
    assert r.status_code == 401, r.text
    # The dead grant was NOT silently replaced with a consumed-token attempt.
    key = next(k for k in state.router.providers["hermes-main"].keys
               if k.label == label)
    assert hermes_oauth.decode_credential(key.secret)["refresh_token"] == "ref-dead"


# -- #4: whole-pool disconnect must delete the DB rows too ---------------------


@respx.mock
async def test_whole_pool_disconnect_persists(ctx):
    """Bug #4: ``delete_key`` ran only ``if label:``, so a disconnect with no
    ``key_label`` (the documented "remove the whole pool" path) filtered the
    keys in memory and left every ``provider_keys`` row for the next restart."""
    client, state = ctx
    await _connect(client, "hermes-main", "one@example.com", refresh="ref-1")
    await _connect(client, "hermes-main", "two@example.com", refresh="ref-2")
    assert len(await _db_secrets(state, "hermes-main")) >= 2

    r = await client.request("DELETE", "/admin/hermes/oauth/disconnect",
                             headers=AUTH, json={"provider": "hermes-main"})
    assert r.status_code == 200, r.text
    assert r.json()["disconnected"] == 2
    assert [k for k in state.router.providers["hermes-main"].keys
            if hermes_oauth.decode_credential(k.secret)] == []
    assert [row for row in await _db_secrets(state, "hermes-main")
            if hermes_oauth.decode_credential(row[1])] == []


# -- #5: re-login rotates the account in place -------------------------------


@respx.mock
async def test_relogin_rotates_the_same_account_in_place(ctx):
    """Bug #5: ``_unique_hermes_key_label`` documented in-place rotation but
    /poll always appended (and the dedup base was the blob, so it could never
    match). The stale key kept a grant the new login had already consumed, so
    the sweeper later presented it -> invalid_grant -> that account's circuit
    dead forever."""
    client, state = ctx
    first = await _connect(client, "hermes-main", "same@example.com",
                           access=_jwt("same@example.com"), refresh="ref-old")
    second = await _connect(client, "hermes-main", "same@example.com",
                            access=_jwt("same@example.com"), refresh="ref-new")

    assert second == first, "re-login of one account must reuse its key label"
    managed = [k for k in state.router.providers["hermes-main"].keys
               if hermes_oauth.decode_credential(k.secret)]
    assert len(managed) == 1, "re-login must rotate, not duplicate"
    cred = hermes_oauth.decode_credential(managed[0].secret)
    assert cred["refresh_token"] == "ref-new"
    assert cred["label"] == "same@example.com"
    # And the DB has exactly one row for it, holding the rotated grant.
    rows = await _db_secrets(state, "hermes-main")
    assert [lbl for lbl, s in rows if hermes_oauth.decode_credential(s)] == [first]


# -- #6: the device-code request must not block the event loop ---------------


async def test_request_device_code_runs_off_the_event_loop(ctx, monkeypatch):
    """Bug #6: ``request_device_code()`` is a sync httpx POST with a 30 s
    timeout, called inline — it blocked every in-flight completion and stream
    for the portal round-trip (the AUDIT #426 shape)."""
    client, _ = ctx
    seen: dict[str, object] = {}

    def fake_request_device_code():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            seen["loop"] = False
        else:
            seen["loop"] = True
        seen["thread"] = threading.get_ident()
        return DEVICE_CODE

    monkeypatch.setattr(hermes_oauth, "request_device_code", fake_request_device_code)
    r = await client.post("/admin/hermes/oauth/device-code?provider=hermes-main",
                          headers=AUTH)
    assert r.status_code == 200, r.text
    assert seen.get("loop") is False, "device-code POST ran on the event loop"
    assert seen.get("thread") != threading.get_ident()


# -- #7: per-account refresh state is pruned on disconnect --------------------


@respx.mock
async def test_disconnect_reclaims_per_account_refresh_state(ctx):
    """Bug #7: ``_locks`` / ``_generations`` / the circuit entry only ever grew —
    nothing popped an account when /disconnect dropped its key."""
    client, state = ctx
    label = await _connect(client, "hermes-main", "leaky@example.com",
                           refresh="ref-x")
    worker = state.hermes_refresh
    assert worker is not None

    # A rotation populates all three per-account structures.
    respx.post(hermes_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": _jwt("leaky@example.com"),
            "refresh_token": "ref-y", "expires_in": 3600}))
    r = await client.post("/admin/hermes/oauth/refresh", headers=AUTH,
                          json={"provider": "hermes-main", "key_label": label})
    assert r.status_code == 200, r.text
    ident = ("hermes-main", label)
    assert ident in worker._locks and ident in worker._generations

    r = await client.request("DELETE", "/admin/hermes/oauth/disconnect",
                             headers=AUTH,
                             json={"provider": "hermes-main",
                                   "key_label": label})
    assert r.status_code == 200, r.text
    assert ident not in worker._locks
    assert ident not in worker._generations
    assert not worker._circuit.dead(ident)
    assert worker._circuit.streak(ident) == 0


@respx.mock
async def test_forget_does_not_corrupt_an_in_flight_refresh(ctx):
    """Bug #7 (safety): forgetting an account whose refresh is mid-flight must
    not hand the next caller a fresh lock + zeroed generation — that is exactly
    how a rotating single-use grant gets presented twice (AUDIT #169)."""
    client, state = ctx
    label = await _connect(client, "hermes-main", "busy@example.com",
                           refresh="ref-busy")
    worker = state.hermes_refresh
    assert worker is not None
    ident = ("hermes-main", label)
    lock = worker._locks.setdefault(ident, asyncio.Lock())
    worker._generations[ident] = 7

    async with lock:  # a refresh holds this account's lock
        worker.forget("hermes-main", label)
        assert ident in worker._locks, "lock popped while held"
        assert worker._generations.get(ident) == 7, \
            "generation reset while a refresh was in flight"

    # Once idle, the account is reclaimable.
    worker.forget("hermes-main", label)
    assert ident not in worker._locks
    assert ident not in worker._generations


def test_placeholder_helper_does_not_scatter_the_literal():
    """The literal lives in one place; ``/poll`` and ``/disconnect`` both go
    through it."""
    assert hermes_oauth.is_placeholder(hermes_oauth.HERMES_PLACEHOLDER_SECRET)
    assert not hermes_oauth.is_placeholder("hermes:not-json")
    blob = hermes_oauth.encode_credential("a", "b", time.time() + 60, "x@y.z")
    assert hermes_oauth.decode_credential(blob) is not None
    assert not hermes_oauth.is_placeholder(blob)