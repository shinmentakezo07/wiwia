"""Round 144: four WorkBuddy OAuth fixes.

1. A refresh response with no ``expiresIn`` **preserved** the already-stale
   expiry, so the token was due again on the very next sweep tick: one rotation
   plus one DB write per ``TICK_S`` until the single-use grant came back
   rejected and the key was marked dead. The shared
   ``wiwi.providers.token_expiry.compute_expires_at`` rule (AUDIT #425 for
   Hermes) now resolves the field, falling forward to ``2 * REFRESH_LEAD_S``.

2. ``POST /admin/workbuddy/import`` appended to the in-memory pool *before*
   ``config_store.add_key`` with no ``try`` at all: a failure halfway through a
   batch left every key live in memory, only some in the DB, an unhandled 500 and
   no audit row. It now persists first and mutates routing only after every
   write lands, rolling back the DB on failure.

3. The same handler snapshotted the taken-label set and never updated it inside
   the loop, so two entries sharing a ``nickname`` in ONE batch both got
   appended with identical labels; the DB (``INSERT OR REPLACE``) kept only the
   last, and ``router.get_key(label)`` could only ever resolve the first — the
   other account's token expired and 401'd forever.

4. ``mark_dead`` is permanent and keyed on ``(provider, label)``. A WorkBuddy
   label is derived from the account (``nickname or uid``), so re-importing a
   fresh auth for a deleted account reuses the label and *inherited the corpse*:
   ``blocked()`` short-circuits before any rotation, and only the admin
   force-refresh button could revive it. Delete/disconnect now purges the
   per-account lock, generation counter and circuit entry (via the existing
   ``CircuitBreaker.clear``), and the module-global ``_workers`` fallback cache
   — keyed on ``id(state)``, which CPython recycles — no longer pins a worker
   bound to a dead ``AppState``.
"""

from __future__ import annotations

import asyncio
import gc
import json
import time
import weakref
from dataclasses import dataclass
from typing import Any

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
from wiwi.providers import workbuddy_auth
from wiwi.providers import workbuddy_auto_refresh as wb_refresh
from wiwi.providers.workbuddy_auto_refresh import WorkBuddyAutoRefresh
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
# A global-domain account refreshes against CHAT_BASE_GLOBAL.
WB_REFRESH_GLOBAL = "https://www.workbuddy.ai/v2/plugin/auth/token/refresh"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"),
    )


def _auth_obj(uid: str, nickname: str, *, hours: float = 24.0) -> dict[str, Any]:
    """An auth JSON in the workbuddy2api auths/ file shape.

    ``hours < 0`` produces an already-expired ``expiresAt`` (the sweeper's cue).
    """
    return {
        "auth": {
            "accessToken": f"at-{uid}",
            "refreshToken": f"rt-{uid}",
            "expiresAt": int(time.time() + hours * 3600),
            "domain": "https://www.workbuddy.ai",
        },
        "account": {"uid": uid, "enterpriseId": "", "nickname": nickname},
    }


def _client(app, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app,
                                      raise_app_exceptions=raise_app_exceptions),
        base_url="http://test")


def _secret(auth: dict[str, Any]) -> str:
    """Serialize an auth object to the stored key-secret shape."""
    return json.dumps(auth)


# -- 1. a refresh response with no expiresIn must not spin the sweeper --------


@respx.mock
async def test_refresh_without_expires_in_does_not_spin_the_sweeper():
    """AUDIT-shaped storm: the preserved expiry is by construction already
    inside the 5-minute lead, so the next tick rotates again — forever, until
    the single-use grant is rejected."""
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            r = await c.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": "wb-storm",
                                   "accounts": [_auth_obj("u1", "alice",
                                                          hours=-1.0)]})
            assert r.status_code == 200, r.text
            key = state.router.providers["wb-storm"].get_key("alice")

        route = respx.post(WB_REFRESH_GLOBAL).mock(return_value=httpx.Response(
            200, json={"code": 0, "msg": "ok", "data": {
                "accessToken": "at-2", "refreshToken": "rt-2",
                # ``expiresIn`` deliberately ABSENT.
            }}))

        worker: WorkBuddyAutoRefresh = state.workbuddy_refresh
        await worker._check_key("wb-storm", "alice")
        assert route.call_count == 1

        # The rotated credential's expiry must be in the future — and strictly
        # beyond REFRESH_LEAD_S, so the next due-check reads False.
        rotated = workbuddy_auth.parse_auth(key.secret)
        assert rotated.access_token == "at-2"
        assert rotated.expires_at > time.time()
        assert not workbuddy_auth.expires_within_lead(
            rotated.expires_at, workbuddy_auth.REFRESH_LEAD_S)

        # The whole point: a second tick one "TICK_S" later must not rotate.
        await worker._check_key("wb-storm", "alice")
        assert route.call_count == 1, (
            "a second sweep tick rotated the single-use grant again: the "
            "missing expiresIn was preserved instead of falling forward")


@respx.mock
@pytest.mark.parametrize("payload", [0, True, "7200", None, -5, [], {}])
async def test_unusable_expires_in_falls_forward_not_past(payload):
    """``0`` / ``true`` / a string are not usable lifetimes either.

    Each starts from an ALREADY-EXPIRED stored credential, so "preserving the
    old expiry" and "landing in the past" are the same failure.
    """
    respx.post(WB_REFRESH_GLOBAL).mock(return_value=httpx.Response(
        200, json={"code": 0, "msg": "ok", "data": {
            "accessToken": "at-2", "refreshToken": "rt-2",
            "expiresIn": payload}}))
    auth = workbuddy_auth.parse_auth(_auth_obj("u2", "bob", hours=-1.0)
                                     and _secret(_auth_obj("u2", "bob",
                                                           hours=-1.0)))
    assert auth.expires_at < time.time()
    outcome = await workbuddy_auth.refresh_token(auth)
    assert outcome.ok and outcome.auth is not None, payload
    assert outcome.auth.expires_at > time.time(), payload
    assert not workbuddy_auth.expires_within_lead(
        outcome.auth.expires_at, workbuddy_auth.REFRESH_LEAD_S), payload


# -- 2. import must persist before it mutates routing -------------------------


async def test_import_rolls_back_when_a_key_write_fails():
    """A mid-batch DB failure used to leave every key live in memory, some in
    the DB, and no audit row — on restart the account silently reverts."""
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        # raise_app_exceptions=False so the pre-fix bare 500 is observable as
        # a response instead of propagating into the test.
        async with _client(app, raise_app_exceptions=False) as c:
            calls: list[str] = []
            real = state.config_store.add_key

            async def boom(provider_name: str, label: str, secret: str,
                           *a: Any, **kw: Any) -> None:
                calls.append(label)
                if len(calls) == 3:
                    raise RuntimeError("database is locked")
                return await real(provider_name, label, secret, *a, **kw)

            state.config_store.add_key = boom  # type: ignore[method-assign]
            accounts = [_auth_obj(f"u{i}", f"acct{i}") for i in range(5)]
            r = await c.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": "wb-fail", "accounts": accounts})

            assert r.status_code == 500, r.text
            assert len(calls) == 3, calls
            # Nothing routable, in memory or on disk.
            assert "wb-fail" not in state.router.providers
            providers = (await c.get("/admin/providers",
                                     headers=AUTH)).json()["providers"]
            assert all(p["name"] != "wb-fail" for p in providers)


async def test_import_append_rolls_back_and_leaves_the_pool_untouched():
    """The append-to-an-existing-provider branch had the same hole: three
    appends, a failure on the second, and the pool grew by three."""
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app, raise_app_exceptions=False) as c:
            assert (await c.post("/admin/workbuddy/import", headers=AUTH,
                                 json={"provider": "wb-pool",
                                       "accounts": [_auth_obj("u0", "orig")]}
                                 )).status_code == 200
            before = {k.label for k in state.router.providers["wb-pool"].keys}

            calls: list[str] = []
            real = state.config_store.add_key

            async def boom(provider_name: str, label: str, secret: str,
                           *a: Any, **kw: Any) -> None:
                calls.append(label)
                if len(calls) == 2:
                    raise RuntimeError("database is locked")
                return await real(provider_name, label, secret, *a, **kw)

            state.config_store.add_key = boom  # type: ignore[method-assign]
            r = await c.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": "wb-pool",
                                   "accounts": [_auth_obj(f"v{i}", f"add{i}")
                                                for i in range(3)]})
            assert r.status_code == 500, r.text
            after = {k.label for k in state.router.providers["wb-pool"].keys}
            assert after == before, (
                f"a failed import still mutated the live pool: {after}")


# -- 3. duplicate labels within ONE batch must be rejected -------------------


async def test_import_rejects_duplicate_label_within_one_batch():
    """``taken`` was snapshotted before the loop, so both duplicates slipped
    through; the DB kept one, ``get_key(label)`` resolved the other forever."""
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            r = await c.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": "wb-dup", "accounts": [
                                 _auth_obj("d1", "alice"),
                                 _auth_obj("d2", "bob"),
                                 _auth_obj("d3", "alice"),
                             ]})
            assert r.status_code == 409, r.text
            assert "wb-dup" not in state.router.providers
            providers = (await c.get("/admin/providers",
                                     headers=AUTH)).json()["providers"]
            assert all(p["name"] != "wb-dup" for p in providers)


async def test_import_still_accepts_distinct_labels_in_one_batch():
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            r = await c.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": "wb-ok", "accounts": [
                                 _auth_obj("e1", "alice"),
                                 _auth_obj("e2", "bob"),
                             ]})
            assert r.status_code == 200, r.text
            assert {k.label for k in state.router.providers["wb-ok"].keys} \
                == {"alice", "bob"}


# -- 4. a re-created account must not inherit a dead circuit ------------------


@respx.mock
async def test_reimport_after_key_delete_is_refreshable_again():
    """WorkBuddy labels come from the account (``nickname or uid``), so
    delete + re-import reuses the label — and, pre-fix, the ``mark_dead``."""
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            assert (await c.post("/admin/workbuddy/import", headers=AUTH,
                                 json={"provider": "wb-dead",
                                       "accounts": [_auth_obj("z1", "alice",
                                                              hours=-1.0)]}
                                 )).status_code == 200

        worker: WorkBuddyAutoRefresh = state.workbuddy_refresh
        respx.post(WB_REFRESH_GLOBAL).mock(return_value=httpx.Response(
            401, json={"code": 12153, "msg": "Offline user session not found"}))
        assert await worker.refresh_now("wb-dead", "alice",
                                        require_due=True) is False
        assert worker._circuit.dead(("wb-dead", "alice"))
        # A second attempt is refused by the permanent dead circuit.
        assert await worker.refresh_now("wb-dead", "alice",
                                        require_due=False) is False

        async with _client(app) as c:
            d = await c.delete("/admin/providers/wb-dead/keys/alice",
                               headers=AUTH)
            assert d.status_code == 200, d.text
        assert ("wb-dead", "alice") not in worker._locks
        assert ("wb-dead", "alice") not in worker._generations
        assert not worker._circuit.dead(("wb-dead", "alice"))

        # Re-import the same account under the same derived label.
        async with _client(app) as c:
            r = await c.post("/admin/workbuddy/import", headers=AUTH,
                             json={"provider": "wb-dead",
                                   "accounts": [_auth_obj("z2", "alice",
                                                          hours=-1.0)]})
            assert r.status_code == 200, r.text

        respx.post(WB_REFRESH_GLOBAL).mock(return_value=httpx.Response(
            200, json={"code": 0, "msg": "ok", "data": {
                "accessToken": "at-fresh", "refreshToken": "rt-fresh",
                "expiresIn": 7200}}))
        assert await worker.refresh_now("wb-dead", "alice",
                                        require_due=True) is True, (
            "a re-imported account stayed blocked by the dead circuit of the "
            "account it replaced")


@respx.mock
async def test_delete_provider_purges_every_workbuddy_account_circuit():
    app = create_app(_config())
    async with LifespanManager(app):
        state = app.state.wiwi
        async with _client(app) as c:
            assert (await c.post("/admin/workbuddy/import", headers=AUTH,
                                 json={"provider": "wb-all", "accounts": [
                                     _auth_obj("y1", "alice", hours=-1.0),
                                     _auth_obj("y2", "bob", hours=-1.0)]}
                                 )).status_code == 200

        worker: WorkBuddyAutoRefresh = state.workbuddy_refresh
        worker._circuit.mark_dead(("wb-all", "alice"))
        worker._circuit.mark_dead(("wb-all", "bob"))
        worker._locks[("wb-all", "alice")] = asyncio.Lock()
        worker._generations[("wb-all", "bob")] = 3

        async with _client(app) as c:
            d = await c.delete("/admin/providers/wb-all", headers=AUTH)
            assert d.status_code == 200, d.text

        for label in ("alice", "bob"):
            assert not worker._circuit.dead(("wb-all", label))
            assert ("wb-all", label) not in worker._locks
            assert ("wb-all", label) not in worker._generations


def test_fallback_worker_cache_does_not_pin_a_dead_state():
    """``_workers`` was a module-global ``dict[int, Worker]`` keyed on
    ``id(state)``: entries were never evicted, and CPython recycles ``id()``,
    so a re-created ``AppState`` could be handed a worker bound to a dead one
    and inherit its locks and dead circuits."""
    @dataclass
    class _Mini:
        router: Any = None

    state = _Mini()
    worker = wb_refresh._worker_for(state)
    assert worker._state is state
    ref = weakref.ref(worker)
    del worker, state
    gc.collect()
    assert ref() is None, (
        "the fallback worker outlived its state — a module-global id()-keyed "
        "cache can hand a new AppState a worker bound to a dead one")


async def test_forget_keeps_state_of_an_in_flight_rotation():
    """A refresh that is mid-flight must keep its lock and generation counter:
    the holder captured the generation before entering and re-checks it under
    the lock, so zeroing it would make it re-present a consumed grant."""
    # ``forget`` touches only the per-account maps, so a state-less worker is
    # the supported way to exercise it (``_Mini`` above for the id()-cache test).
    worker = WorkBuddyAutoRefresh(state=None)
    ident = ("wb", "in-flight")
    lock = asyncio.Lock()
    await lock.acquire()
    worker._locks[ident] = lock
    worker._generations[ident] = 4
    worker._circuit.mark_dead(ident)

    worker.forget("wb", "in-flight")
    assert ident in worker._locks
    assert worker._generations[ident] == 4
    assert worker._circuit.dead(ident)

    lock.release()
    worker.forget("wb", "in-flight")
    assert ident not in worker._locks
    assert ident not in worker._generations
    assert not worker._circuit.dead(ident)


@pytest.mark.parametrize("seconds", [0, True, "x", None, -5, 2.5])
def test_compute_expires_at_contract_for_workbuddy_lead(seconds):
    from wiwi.providers.token_expiry import compute_expires_at

    now = 1_000_000.0
    got = compute_expires_at(seconds, now,
                             lead_s=workbuddy_auth.REFRESH_LEAD_S)
    assert got > now + workbuddy_auth.REFRESH_LEAD_S