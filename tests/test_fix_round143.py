"""Round-143 regression tests for the Cline OAuth admin write paths.

Six defects, all in the *admin* surface rather than in the sweeper or the
gateway 401 hook — the background worker already funnelled every rotation
through ``store_access_token``, but ``/admin/cline/oauth/*`` still ran its own
copy of the job:

1. 🔴 ``/admin/cline/oauth/refresh`` called ``cline_oauth.refresh_token``
   directly, bypassing the worker's lock and circuit. Cline's ``refreshToken``
   is single-use, so an operator clicking "Refresh" while the 60 s sweeper held
   the wire made both POST the same grant and the loser's ``invalid_grant``
   called ``mark_dead``. The admin path never touched the circuit at all, so its
   own ``invalid_grant`` did not even record the death.
2. 🟠 All three admin paths wrote ``acct.keys[0]`` only, so a 2-key pool kept a
   stale sibling — the round-robin cursor handed the next request to it, it
   401'd, and the on-demand hook burned a second rotation of the grant the
   operator had just consumed.
3. 🟠 Connect wrote ``refresh_token: None`` when
   ``cline_oauth.exchange_code`` returned None (a truncated Google-OAuth code),
   silently disarming auto-refresh on an already-connected account.
4. 🟠 Every write path has to pass the *pre-rotation* ``access_fingerprint`` so
   ``store_access_token`` overwrites exactly the keys the record owns — and
   connect has to stamp that fingerprint at all, or the next rotation would
   clobber raw API keys the operator added to the pool.
5. 🟠 ``ClineAutoRefresh.forget`` was never called, so ``CircuitBreaker._dead``
   survived disconnect / provider-delete / key-delete forever and a
   re-authenticated account stayed "blocked by the refresh circuit breaker".
   The module-global ``_workers`` cache was also keyed on ``id(state)`` with no
   eviction, so CPython's id recycling could hand a fresh ``AppState`` a worker
   bound to a dead one.
6. 🟠 A refresh response with no ``expiresAt`` used to leave the previous
   (already-stale) ``expires_at`` in place — the record stayed due forever and
   the single-use grant was re-presented on every tick until ``invalid_grant``.

The lock-race tests are driven with a signalling lock rather than sleeps: the
test observes that the second caller is *parked on the shared lock* before it
inspects the upstream POST count.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import json
import time
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

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
from wiwi.providers import cline_auto_refresh, cline_oauth
from wiwi.providers.cline_auto_refresh import ClineAutoRefresh, _worker_for
from wiwi.providers.cline_oauth import CLINE_API_BASE
from wiwi.router.router import Router

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
REFRESH_URL = f"{CLINE_API_BASE}/auth/refresh"

EXPIRY_FUTURE = "2099-01-01T00:00:00.000Z"
EXPIRY_SOON = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()

PROV = "cline-prov"


def _soon_iso() -> str:
    """An ISO timestamp 60 s out — i.e. already inside the 5-minute lead."""
    return (datetime.now(UTC) + timedelta(seconds=60)).isoformat()


def _config(keys: list[KeyDef] | None = None) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(
            name=PROV, provider="cline",
            base_url="https://api.cline.bot/api/v1",
            keys=keys or [KeyDef(label="default", key="workos:stale-token")],
        )],
        model_list=[ModelEntry(
            model_name="cline-model",
            wiwi_params=DeploymentParams(provider=PROV, model="z-ai/glm-5.2"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"),
    )


def _two_key_config() -> WiwiConfig:
    return _config([KeyDef(label="oauth-a", key="workos:stale-a"),
                    KeyDef(label="oauth-b", key="workos:stale-b")])


async def _app_and_client(cfg: WiwiConfig):
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            yield app, client


@pytest.fixture
async def app():
    """``(app, client)`` for a one-key Cline pool."""
    async for pair in _app_and_client(_config()):
        yield pair


@pytest.fixture
async def app2():
    """``(app, client)`` for a two-key Cline pool."""
    async for pair in _app_and_client(_two_key_config()):
        yield pair


def _code(access: str = "acc-new-1234567890",
          refresh: str | None = "ref-new",
          expires: str | None = EXPIRY_FUTURE,
          email: str | None = "u@x.io") -> str:
    """A complete (non-truncated) pasted auth code."""
    payload: dict[str, Any] = {"accessToken": access}
    if refresh is not None:
        payload["refreshToken"] = refresh
    if expires is not None:
        payload["expiresAt"] = expires
    if email is not None:
        payload["email"] = email
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _truncated_code(access: str = "acc-trunc-1234567890") -> str:
    """A Google-OAuth code whose signature ate every field after accessToken.

    ``exchange_code`` returns ``refresh_token``/``email``/``expires_at`` as None
    for this shape on purpose — see its docstring and ``tests/test_cline_oauth.py``.
    """
    return base64.b64encode(json.dumps({"accessToken": access}).encode()).decode()


async def _connect(client, **kwargs) -> httpx.Response:
    return await client.post("/admin/cline/oauth/connect", headers=AUTH,
                             json={"provider": PROV, "code": _code(**kwargs)})


async def _record(state) -> dict[str, Any]:
    return await state.config_store.get_setting(f"cline_oauth:{PROV}") or {}


async def _wait_for(event: asyncio.Event, timeout: float = 5.0) -> bool:
    try:
        await asyncio.wait_for(event.wait(), timeout)
        return True
    except TimeoutError:
        return False


async def _first_to_fire(*events: asyncio.Event, timeout: float = 5.0) -> int:
    waits = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED,
                                     timeout=timeout)
    finally:
        for w in waits:
            w.cancel()
    for i, w in enumerate(waits):
        if w in done:
            return i
    return -1


class _SignallingLock(asyncio.Lock):
    """An ``asyncio.Lock`` that fires ``arrived`` when a caller reaches it."""

    def __init__(self, arrived: asyncio.Event) -> None:
        super().__init__()
        self.arrived = arrived

    async def acquire(self) -> bool:
        self.arrived.set()
        return await super().acquire()


# -- #1: the admin refresh shares the worker's lock and circuit ---------------


@respx.mock
async def test_admin_refresh_and_sweeper_cannot_burn_the_same_grant(app):
    """A sweeper tick and an operator click must present the grant once.

    Pre-fix the handler called ``cline_oauth.refresh_token`` itself — no lock,
    no circuit — so it POSTed the same single-use ``refreshToken`` the sweeper
    had in flight.
    """
    gateway_app, client = app
    state = gateway_app.state.wiwi
    worker = state.cline_refresh
    assert isinstance(worker, ClineAutoRefresh), "lifespan must own the worker"

    arrived = asyncio.Event()
    worker._locks[PROV] = _SignallingLock(arrived)
    await state.config_store.set_setting(
        f"cline_oauth:{PROV}",
        {"refresh_token": "ref-1", "expires_at": EXPIRY_SOON, "email": "u@x.io"})

    upstream_started = asyncio.Event()
    second_post = asyncio.Event()
    release = asyncio.Event()
    presented: list[str] = []

    async def refresh(request: httpx.Request) -> httpx.Response:
        presented.append(json.loads(request.content)["refreshToken"])
        if len(presented) == 1:
            upstream_started.set()
        else:
            second_post.set()
        await release.wait()
        return httpx.Response(200, json={"data": {
            "accessToken": "acc-fresh", "refreshToken": "ref-2",
            "expiresAt": EXPIRY_FUTURE}})

    respx.post(REFRESH_URL).mock(side_effect=refresh)

    record = await _record(state)
    sweep = asyncio.create_task(worker._check_provider(PROV, record))
    assert await _wait_for(upstream_started), "sweeper never rotated"

    # The operator clicks "Refresh" while the sweeper holds the wire. Exactly
    # one of these may happen: it queues on the shared lock, or it POSTs the
    # grant the sweeper is already consuming.
    arrived.clear()
    click = asyncio.create_task(
        client.post("/admin/cline/oauth/refresh", headers=AUTH,
                    json={"provider": PROV}))
    assert await _first_to_fire(arrived, second_post) == 0, (
        "the admin refresh burned the sweeper's in-flight single-use grant "
        f"instead of sharing its lock (POSTs={presented})")
    assert presented == ["ref-1"]
    assert not click.done()

    release.set()
    r = await asyncio.wait_for(click, 5)
    await asyncio.wait_for(sweep, 5)
    assert r.status_code == 200
    assert presented == ["ref-1"], "the grant was presented twice"
    assert (await _record(state))["refresh_token"] == "ref-2"


@respx.mock
async def test_admin_refresh_marks_the_circuit_dead_on_invalid_grant(app):
    """The admin path records the death, so the sweeper stops burning grants.

    Pre-fix the handler never touched the circuit, so its ``invalid_grant``
    left the provider *not* dead and the sweeper kept presenting a consumed
    grant every 60 s forever.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, refresh="ref-dead")
    respx.post(REFRESH_URL).respond(status_code=400,
                                    json={"error": "invalid_grant"})

    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 401
    assert "invalid_grant" in r.json()["error"]["message"]
    assert state.cline_refresh._circuit.dead(PROV)
    assert state.cline_refresh.dead_code(PROV) == "invalid_grant"


@respx.mock
async def test_admin_refresh_reports_a_transient_failure_as_502(app):
    """A 5xx from Cline is transient — 502, and the provider is not killed."""
    _gateway_app, client = app
    await _connect(client, refresh="ref-ok")
    respx.post(REFRESH_URL).respond(status_code=502, text="bad gateway")

    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 502
    assert not _gateway_app.state.wiwi.cline_refresh._circuit.dead(PROV)


@respx.mock
async def test_dead_codes_is_initialized_so_a_rejection_does_not_raise(app):
    """``_do_refresh`` records the upstream code; the dict must exist.

    ``self._dead_codes[name] = ...`` on an uninitialized dict raised
    ``AttributeError`` out of the admin handler — a consumed grant and a 500.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, refresh="ref-dead")
    respx.post(REFRESH_URL).respond(status_code=400,
                                    json={"error": "invalid_grant"})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 401  # not a 500 from the AttributeError
    assert state.cline_refresh.dead_code(PROV) == "invalid_grant"


# -- #2: one click refreshes every key the record owns ------------------------


@respx.mock
async def test_admin_refresh_updates_every_owned_key_in_a_two_key_pool(app2):
    """``keys[0]``-only left a stale sibling that 401s and burns another grant.

    Pre-fix ``_update_provider_secret`` wrote ``acct.keys[0]``; the second key
    kept the old access token, the round-robin cursor handed the next request
    to it, and the gateway's 401 hook rotated the grant a *second* time.
    """
    _gateway_app, client = app2
    state = _gateway_app.state.wiwi
    await _connect(client, access="acc-one-1234567890", refresh="ref-1")

    respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-two-1234567890", "refreshToken": "ref-2",
        "expiresAt": EXPIRY_FUTURE}})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 200

    keys = state.router.providers[PROV].keys
    assert len(keys) == 2
    assert [k.secret for k in keys] == ["acc-two-1234567890"] * 2, (
        "a sibling key kept the stale token: "
        f"{[k.secret for k in keys]}")
    assert all(k.status == "active" for k in keys)


async def test_connect_updates_every_key_in_a_two_key_pool(app2):
    """The connect path must not be the lone ``keys[0]`` writer either."""
    _gateway_app, client = app2
    state = _gateway_app.state.wiwi
    await _connect(client, access="acc-one-1234567890")
    assert [k.secret for k in state.router.providers[PROV].keys] == \
        ["acc-one-1234567890"] * 2


# -- #3: a truncated code must not overwrite a working grant -------------------


async def test_truncated_code_connect_keeps_the_working_refresh_token(app):
    """Re-connecting with a truncated code must not disarm auto-refresh.

    ``exchange_code`` returns ``refresh_token=None`` for a truncated Google
    OAuth code *by design*. Writing that None over a live record made
    ``_sweep`` skip the provider forever
    (``if not record.get("refresh_token"): continue``), so a healthy,
    auto-refreshing account died at token expiry with no recovery.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, refresh="ref-good", email="u@x.io")

    r = await client.post(
        "/admin/cline/oauth/connect", headers=AUTH,
        json={"provider": PROV, "code": _truncated_code()})
    assert r.status_code == 200

    record = await _record(state)
    assert record["refresh_token"] == "ref-good", (
        "a truncated code overwrote the live refresh grant with null")
    assert record["email"] == "u@x.io"
    # And the sweeper must still consider the provider.
    assert record.get("expires_at")
    worker = state.cline_refresh
    assert worker._circuit.blocked(PROV) is False


async def test_truncated_code_callback_keeps_the_working_refresh_token(app):
    """The redirect callback is the second writer of the same record."""
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, refresh="ref-good")

    start = await client.post("/admin/cline/oauth/auto-connect", headers=AUTH,
                              json={"provider": PROV})
    code = _truncated_code()
    r = await client.get(f"/cline/oauth/callback?code={code}"
                         f"&state={start.json()['state']}",
                         follow_redirects=False)
    assert r.status_code == 302
    assert (await _record(state))["refresh_token"] == "ref-good"


# -- #4: the pre-rotation fingerprint on every write path ---------------------


async def test_connect_stamps_the_access_fingerprint_on_the_record(app):
    """Without a fingerprint the next rotation has no idea what it owns.

    ``_owned_keys`` treats "no fingerprint recorded" as "every key is mine",
    so an unstamped record made the following rotation overwrite raw API keys
    the operator had added to the pool.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, access="acc-one-1234567890")
    record = await _record(state)
    assert record.get("access_fingerprint") == \
        cline_oauth.access_fingerprint("acc-one-1234567890")


@respx.mock
async def test_admin_refresh_leaves_a_raw_api_key_sibling_untouched(app):
    """``owned`` must be the fingerprint the record issued *before* the swap.

    A rotation may only overwrite the keys holding the token this record last
    wrote; passing the post-rotation fingerprint (or nothing) would clobber a
    raw key the operator added — a permanent credential loss.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    acct = state.router.providers[PROV]
    acct.keys = []
    for label, secret in (("oauth", "acc-old-1234567890"),
                          ("raw", "sk-raw-not-ours")):
        from wiwi.router.router import ProviderKey
        acct.keys.append(ProviderKey(label=label, secret=secret,
                                     weight=1.0, status="active",
                                     enabled=True, cooldown_until=0.0))
    await state.config_store.set_setting(
        f"cline_oauth:{PROV}",
        {"refresh_token": "ref-1", "expires_at": EXPIRY_FUTURE,
         "email": "u@x.io",
         "access_fingerprint": cline_oauth.access_fingerprint(
             "acc-old-1234567890")})

    respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-new-1234567890", "refreshToken": "ref-2",
        "expiresAt": EXPIRY_FUTURE}})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 200

    secrets = {k.label: k.secret for k in acct.keys}
    assert secrets["oauth"] == "acc-new-1234567890"
    assert secrets["raw"] == "sk-raw-not-ours", (
        "the rotation clobbered a raw API key the record does not own")


@respx.mock
async def test_sweeper_passes_the_pre_rotation_fingerprint_too(app):
    """The worker path (sweeper / 401 hook) keeps the same ownership rule."""
    _gateway_app, _client = app
    state = _gateway_app.state.wiwi
    acct = state.router.providers[PROV]
    from wiwi.router.router import ProviderKey
    acct.keys = [
        ProviderKey(label="oauth", secret="acc-old-1234567890", weight=1.0,
                    status="active", enabled=True, cooldown_until=0.0),
        ProviderKey(label="raw", secret="sk-raw-not-ours", weight=1.0,
                    status="active", enabled=True, cooldown_until=0.0),
    ]
    await state.config_store.set_setting(
        f"cline_oauth:{PROV}",
        {"refresh_token": "ref-1", "expires_at": _soon_iso(),
         "email": "u@x.io",
         "access_fingerprint": cline_oauth.access_fingerprint(
             "acc-old-1234567890")})
    respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-new-1234567890", "refreshToken": "ref-2",
        "expiresAt": EXPIRY_FUTURE}})

    assert await state.cline_refresh._sweep() == 1
    secrets = {k.label: k.secret for k in acct.keys}
    assert secrets["oauth"] == "acc-new-1234567890"
    assert secrets["raw"] == "sk-raw-not-ours"


# -- #5: a dead Cline account must be reclaimable -----------------------------


@respx.mock
async def test_disconnect_then_reconnect_makes_the_account_refreshable_again(app):
    """``mark_dead`` is permanent unless something pops the circuit.

    ``forget`` was never called, so disconnecting a dead account and pasting a
    fresh code left the sweeper reporting "blocked by the refresh circuit
    breaker" forever.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, refresh="ref-dead")
    respx.post(REFRESH_URL).respond(status_code=400,
                                    json={"error": "invalid_grant"})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 401
    assert state.cline_refresh._circuit.dead(PROV)

    d = await client.request("DELETE", "/admin/cline/oauth/disconnect",
                             headers=AUTH, json={"provider": PROV})
    assert d.status_code == 200
    assert not state.cline_refresh._circuit.dead(PROV)
    assert state.cline_refresh._circuit.blocked(PROV) is False

    await _connect(client, refresh="ref-fresh")
    respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-again-1234567890", "refreshToken": "ref-next",
        "expiresAt": EXPIRY_FUTURE}})
    # Put the token back inside the lead window so the sweeper is due.
    record = await _record(state)
    record["expires_at"] = _soon_iso()
    await state.config_store.set_setting(f"cline_oauth:{PROV}", record)

    assert await state.cline_refresh._sweep() == 1
    assert state.router.providers[PROV].keys[0].secret == "acc-again-1234567890"


async def test_delete_provider_clears_the_dead_cline_circuit(app):
    """Deleting the account deletes the credential — and the circuit with it."""
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    await _connect(client, refresh="ref-dead")
    state.cline_refresh._circuit.mark_dead(PROV)

    # ``_config`` wires a model group to the provider; drop it so the delete
    # guard (which is about group references, not auth state) passes.
    state.router.groups.clear()
    r = await client.delete(f"/admin/providers/{PROV}", headers=AUTH)
    assert r.status_code == 200
    assert not state.cline_refresh._circuit.dead(PROV)


async def test_delete_provider_key_clears_the_dead_cline_circuit(app2):
    """Same reclaim on the key-delete path, for the same reason."""
    _gateway_app, client = app2
    state = _gateway_app.state.wiwi
    state.cline_refresh._circuit.mark_dead(PROV)
    r = await client.delete(f"/admin/providers/{PROV}/keys/oauth-b", headers=AUTH)
    assert r.status_code == 200
    assert not state.cline_refresh._circuit.dead(PROV)


@dataclass
class _State:
    """The subset of AppState the Cline worker touches."""

    router: Router
    config_store: Any = None
    cline_refresh: Any = None


def test_worker_cache_rebuilds_when_cpython_recycles_an_id():
    """``id(state)`` is reused after a collection — a stale worker must not win.

    The fallback cache was a bare ``_workers.setdefault(id(state), ...)``: CPython
    recycles the address of a collected object, so a freshly created AppState
    could be handed the worker of the *dead* one — dead circuit and stale
    generation counters included.
    """
    state = _State(router=Router(_config()))
    assert state.cline_refresh is None  # exercise the fallback cache
    dead = _State(router=Router(_config()))
    stale = object()
    cline_auto_refresh._workers[id(state)] = (weakref.ref(dead), stale)
    try:
        worker = _worker_for(state)
        assert worker is not stale  # the planted, dead-state worker
        assert isinstance(worker, ClineAutoRefresh)
        # Same object on the second hand-out, though.
        assert _worker_for(state) is worker
    finally:
        cline_auto_refresh._workers.pop(id(state), None)


def test_worker_cache_prunes_collected_states():
    """Entries whose state was collected must not be retained forever."""
    state = _State(router=Router(_config()))
    _worker_for(state)
    assert id(state) in cline_auto_refresh._workers
    del state
    gc.collect()
    _worker_for(_State(router=Router(_config())))
    assert all(ref() is not None
               for ref, _w in cline_auto_refresh._workers.values()), (
        "the worker cache retained a worker bound to a collected state")


# -- #6: a response with no expiresAt must not leave the record permanently due


@respx.mock
async def test_admin_refresh_without_expires_at_moves_the_expiry_forward(app):
    """A missing ``expiresAt`` must not preserve the already-stale value.

    The token was only ever refreshed because it was inside the 5-minute lead,
    so preserving that expiry made the record due again on the very next 60 s
    tick — one rotation + one DB write per tick until ``invalid_grant`` killed
    the provider.
    """
    _gateway_app, client = app
    state = _gateway_app.state.wiwi
    # Connect with an expiry already inside the lead window.
    await _connect(client, refresh="ref-1", expires=_soon_iso())

    respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-new-1234567890", "refreshToken": "ref-2"}})
    r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                          json={"provider": PROV})
    assert r.status_code == 200

    record = await _record(state)
    epoch = cline_oauth.parse_expires_at(record.get("expires_at"))
    assert epoch is not None
    assert epoch > time.time() + cline_oauth.REFRESH_LEAD_S, (
        "the record is still inside the lead window — a refresh storm")
    # And the sweeper is therefore not due, so the single-use grant is not
    # re-presented on the next tick.
    assert await state.cline_refresh._sweep() == 1
    refreshed_calls = [c for c in respx.calls
                       if c.request.url == httpx.URL(REFRESH_URL)]
    assert len(refreshed_calls) == 1, (
        f"the sweeper re-presented the single-use grant ({len(refreshed_calls)}"
        " upstream calls)")


@respx.mock
async def test_worker_refresh_without_expires_at_moves_the_expiry_forward(app):
    """The sweeper path resolves the same field through the shared helper."""
    _gateway_app, _client = app
    state = _gateway_app.state.wiwi
    await state.config_store.set_setting(
        f"cline_oauth:{PROV}",
        {"refresh_token": "ref-1", "expires_at": _soon_iso(),
         "email": "u@x.io"})
    respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-new-1234567890", "refreshToken": "ref-2"}})
    assert await state.cline_refresh._sweep() == 1
    epoch = cline_oauth.parse_expires_at((await _record(state))["expires_at"])
    assert epoch is not None and epoch > time.time() + cline_oauth.REFRESH_LEAD_S
    # A second sweep finds nothing due — the storm is over.
    assert await state.cline_refresh._sweep() == 1
    assert respx.calls.call_count == 1


# -- #7 (AUDIT #446): a mid-rotation DB failure must not cost the grant ---------
#
# ``_do_refresh`` persists the rotation in two writes: the OAuth record (which
# holds the only copy of the *replacement* single-use grant) and the pool key.
# Pre-fix the pool key was written FIRST and neither write was guarded, so:
#
# * a failing record write left a torn state — the pool key already holding the
#   new access token while the record still held the *consumed* grant and its
#   stale ``expires_at``, which the next tick re-presented into
#   ``unrecoverable_refresh_error`` → ``mark_dead``; and
# * the escaping exception ran out through ``_refresh_locked`` into
#   ``core/gateway.py``'s 401-hook call sites, neither of which wraps
#   ``await refresh_hook(...)`` — so an upstream 401 surfaced as a gateway 500.
#
# Post-fix the record goes first and each write is guarded.


def _patched(cs, name: str, impl):
    """Shadow ``cs.<name>`` with *impl*, returning the restore callable."""
    real = getattr(cs, name)
    setattr(cs, name, impl)
    return lambda: setattr(cs, name, real)


async def _rotation(*payloads: dict[str, str]):
    """Register the refresh route, recording the grant presented each time."""
    presented: list[str] = []

    async def refresh(request: httpx.Request) -> httpx.Response:
        presented.append(json.loads(request.content)["refreshToken"])
        data = payloads[min(len(presented) - 1, len(payloads) - 1)]
        return httpx.Response(200, json={"data": data})

    respx.post(REFRESH_URL).mock(side_effect=refresh)
    return presented


def _fresh(access: str, refresh: str) -> dict[str, str]:
    return {"accessToken": access, "refreshToken": refresh,
            "expiresAt": EXPIRY_FUTURE}


@respx.mock
async def test_record_write_failure_is_reported_not_raised(app):
    """``set_setting`` blowing up must not escape ``_refresh_locked``.

    ``core/gateway.py`` calls ``await refresh_hook(...)`` unguarded, so an
    escaping write turned an upstream 401 into a gateway 500. The rotation must
    report ``False`` instead, leave the provider alive (not dead, not tripped,
    no ``dead_code``) so an operator can retry, and — because the record write
    comes first — perform no partial rotation: neither the pool key nor the
    stored record may advance past a rotation that was not persisted.
    """
    gateway_app, client = app
    state = gateway_app.state.wiwi
    worker = state.cline_refresh
    await _connect(client, access="acc-one-1234567890", refresh="ref-1")

    cs = state.config_store

    async def failing_set(key, value):
        if key == f"cline_oauth:{PROV}":
            raise RuntimeError("settings table is locked")
        raise AssertionError(f"unexpected set_setting({key!r})")

    restore = _patched(cs, "set_setting", failing_set)
    presented = await _rotation(_fresh("acc-two-1234567890", "ref-2"))
    try:
        rotated = await worker.refresh_now(PROV)
    finally:
        restore()

    assert rotated is False, "a failed record write was reported as a rotation"
    assert presented == ["ref-1"]
    assert not worker._circuit.dead(PROV)
    assert worker._circuit.blocked(PROV) is False
    assert worker.dead_code(PROV) is None
    # No torn write: pre-fix the pool key was advanced to a token whose grant
    # the record no longer tracked.
    assert state.router.providers[PROV].keys[0].secret == "acc-one-1234567890"
    assert (await _record(state))["refresh_token"] == "ref-1"

    # And the operator is not locked out: the same provider still rotates.
    assert await worker.refresh_now(PROV) is True
    assert (await _record(state))["refresh_token"] == "ref-2"
    assert state.router.providers[PROV].keys[0].secret == "acc-two-1234567890"


@respx.mock
async def test_admin_refresh_survives_a_record_write_failure_as_502(app):
    """The admin surface reports the failure — 502, never a 500.

    Pre-fix the escaping ``set_setting`` propagated out of the handler (which
    wraps no part of the worker call) and the request died as an unhandled
    exception. Post-fix ``refresh_key_now`` sees ``rotated=False`` with a
    healthy circuit, which maps to the transient-failure branch: 502 with
    ``relogin_required`` False — the grant was not *rejected*, only unwritable.
    """
    gateway_app, client = app
    state = gateway_app.state.wiwi
    await _connect(client, access="acc-one-1234567890", refresh="ref-1")

    async def failing_set(key, value):
        if key == f"cline_oauth:{PROV}":
            raise RuntimeError("settings table is locked")
        raise AssertionError(f"unexpected set_setting({key!r})")

    restore = _patched(state.config_store, "set_setting", failing_set)
    await _rotation(_fresh("acc-two-1234567890", "ref-2"))
    try:
        r = await client.post("/admin/cline/oauth/refresh", headers=AUTH,
                              json={"provider": PROV})
    finally:
        restore()

    assert r.status_code == 502, (
        f"a DB failure surfaced as {r.status_code}, not a 502: {r.text}")
    assert not state.cline_refresh._circuit.dead(PROV)
    assert state.cline_refresh.dead_code(PROV) is None


@respx.mock
async def test_oauth_record_is_persisted_before_the_pool_key(app):
    """Ordering itself: the record write must precede the key write.

    This is the actual fix rather than one of its side effects. The record is
    the only copy of the replacement single-use grant, so writing the pool key
    first means a crash between the two writes loses the grant with nothing
    durable left to re-present.
    """
    gateway_app, client = app
    state = gateway_app.state.wiwi
    await _connect(client, access="acc-one-1234567890", refresh="ref-1")

    cs = state.config_store
    order: list[str] = []
    real_set, real_key = cs.set_setting, cs.update_key_secret

    async def spy_set(key, value):
        order.append(f"set_setting:{key}")
        return await real_set(key, value)

    async def spy_key(provider, label, secret, **kwargs):
        order.append(f"update_key_secret:{label}")
        return await real_key(provider, label, secret, **kwargs)

    restore_set = _patched(cs, "set_setting", spy_set)
    restore_key = _patched(cs, "update_key_secret", spy_key)
    await _rotation(_fresh("acc-two-1234567890", "ref-2"))
    try:
        assert await state.cline_refresh.refresh_now(PROV) is True
    finally:
        restore_set()
        restore_key()

    record_i = next((i for i, c in enumerate(order)
                     if c == f"set_setting:cline_oauth:{PROV}"), None)
    key_i = next((i for i, c in enumerate(order)
                  if c.startswith("update_key_secret:")), None)
    assert record_i is not None, f"the OAuth record was never written ({order})"
    assert key_i is not None, f"the pool key was never written ({order})"
    assert record_i < key_i, (
        "the pool key was written before the OAuth record, so a failure "
        f"between the two loses the refresh grant ({order})")


@respx.mock
async def test_key_write_failure_keeps_the_provider_alive_for_the_next_run(app):
    """A failing key write must not kill the account — the grant already moved.

    The record is written first, so a pool-key failure leaves the *new* grant
    durably stored: the circuit stays healthy and the next run presents a grant
    upstream still honours. Pre-fix the key write came first and its exception
    escaped, so the record was never updated and there was nothing left to
    present at all.
    """
    gateway_app, client = app
    state = gateway_app.state.wiwi
    worker = state.cline_refresh
    await _connect(client, access="acc-one-1234567890", refresh="ref-1")

    real_store = worker.store_access_token
    attempts = {"n": 0}

    async def failing_store(provider, secret, *, owned=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("keys table is locked")
        return await real_store(provider, secret, owned=owned)

    worker.store_access_token = failing_store
    presented = await _rotation(
        _fresh("acc-two-1234567890", "ref-2"),
        _fresh("acc-three-1234567890", "ref-3"))
    try:
        rotated = await worker.refresh_now(PROV)
    finally:
        worker.store_access_token = real_store

    assert rotated is False, "a failed key write was reported as a rotation"
    assert presented == ["ref-1"]
    assert not worker._circuit.dead(PROV)
    assert worker._circuit.blocked(PROV) is False
    assert worker.dead_code(PROV) is None
    # The record already holds the replacement grant — that is the whole point
    # of writing it first.
    assert (await _record(state))["refresh_token"] == "ref-2"
    # The pool key still holds the pre-rotation token, which is why the next
    # attempt has to succeed to recover.
    assert state.router.providers[PROV].keys[0].secret == "acc-one-1234567890"

    assert await worker.refresh_now(PROV) is True
    assert presented == ["ref-1", "ref-2"], (
        f"the next run did not present the stored grant ({presented})")
    assert (await _record(state))["refresh_token"] == "ref-3"
    assert state.router.providers[PROV].keys[0].secret == "acc-three-1234567890"
