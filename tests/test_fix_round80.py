"""Round-80 regression tests — the auth/rate-limit findings of the round-75 sweep.

Four defects, all in the shared admin/key boundary:

* **#162** — revoking a credential was undone by an in-flight
  ``authenticate()``. Revocation here works by *evicting* the cache entry,
  which is a no-op while the entry is absent — exactly the window between the
  DB read and the cache store. The late store then republished the live
  ``AuthInfo`` it had read before the revocation, so the credential kept
  authenticating for the rest of the 60 s TTL. For a key with no ``max_budget``
  and no expiry, deletion is its *only* revocation path.
* **#163** — ``rpm: 0`` / ``tpm: 0`` on a virtual key meant *unlimited*, not
  *blocked*: the limiter guarded each scope with ``if key_rpm:``, so an
  operator parking a key by zeroing it silently granted it unlimited
  throughput. ``DeploymentParams`` rejects ``rpm <= 0`` for exactly this
  semantic, so the two boundaries disagreed.
* **#198** — ``create_key``'s per-owner key cap was a check-then-act race:
  ``count_keys`` awaits, so concurrent creates all read the same pre-insert
  count and every one of them passed. The cap is what stops a user rotating
  around per-key budgets and rate limits.
* **#202** — ``ttl_seconds: 0`` meant opposite things on create (*no expiry*,
  the falsy guard) and on update (*expire immediately*, the ``is not None``
  branch). ``None`` is the documented "clear" value, so ``0`` reaching the
  immediate-expiry branch was not an encoding anyone intended.

Assertions are behavioural: they hold for any correct implementation, so a
later refactor cannot silently re-open these.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.service import AuthService
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.ratelimit.memory import RateLimiter
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _memory_engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def _svc(max_keys_per_user: int = 50) -> tuple[object, AuthService]:
    engine = _memory_engine()
    svc = AuthService(engine, MASTER, max_keys_per_user)
    await svc.startup()
    return engine, svc


def _usable(info) -> bool:
    """The request path's own liveness test (``app.py``'s ``authenticate``).

    A credential is only accepted when it resolved to *something*, that
    something is not disabled, and it is not past its expiry. The in-flight
    race is about whether the caller ends up holding a credential that passes
    this, so the tests below assert on the predicate rather than on ``is None``
    — ``set_disabled`` legitimately returns the row with ``disabled=True``.
    """
    if info is None or info.disabled:
        return False
    return not (info.expires_at is not None and time.time() > info.expires_at)


# ============================================================================
# #162 — a revocation must not be undone by an in-flight authenticate()
# ============================================================================


class _GatedConn:
    """Connection proxy that pauses the first statement after it executes."""

    def __init__(self, conn, owner: _GatedEngine):
        self._conn = conn
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def execute(self, *args, **kwargs):
        result = await self._conn.execute(*args, **kwargs)
        owner = self._owner
        if owner.armed:
            # The row has been read; the caller has not stored it yet. This is
            # exactly the window AUDIT #162 is about, and it is reached without
            # patching any production method.
            owner.armed = False
            owner.read_done.set()
            await owner.release.wait()
        return result


class _GatedConnect:
    def __init__(self, cm, owner: _GatedEngine):
        self._cm = cm
        self._owner = owner

    async def __aenter__(self):
        return _GatedConn(await self._cm.__aenter__(), self._owner)

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


class _GatedEngine:
    """An ``AsyncEngine`` whose first ``connect().execute()`` blocks.

    ``connect`` is wrapped so a test can hold ``authenticate()`` inside its DB
    read; ``begin`` and everything else delegate straight through, so the
    revocation paths the test then drives are the production ones.
    """

    def __init__(self, engine):
        self._engine = engine
        self.armed = False
        self.read_done = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def connect(self):
        return _GatedConnect(self._engine.connect(), self)

    def begin(self):
        return self._engine.begin()


REVOCATIONS = {
    "delete_key": lambda svc, kid: svc.delete_key(kid),
    "set_disabled": lambda svc, kid: svc.set_disabled(kid, True),
    "expire_keys": lambda svc, kid: svc.expire_keys(None, keep_newest=0),
}


@pytest.mark.parametrize("revoke", list(REVOCATIONS))
async def test_revocation_during_an_inflight_authenticate_takes_effect(revoke):
    """A revocation landing between the DB read and the cache store must win.

    The interleaving is driven deterministically at the DB read (the only
    await point in the lookup→store pair), not with sleeps: the statement
    returns the still-live row, the test then completes the revocation, and
    only then does ``authenticate`` resume.

    RED: pre-fix the resumed call stored the ``AuthInfo`` it had read while the
    row was live and returned it, so both this call *and* every later cached
    call accepted the revoked credential for the rest of the 60 s TTL.
    """
    engine, svc = await _svc()
    plaintext, kid = await svc.create_key("victim")
    assert _usable(await svc.authenticate(plaintext)), "control: warm-up must pass"
    # Force the next authenticate() to be a miss, so it actually reaches the DB
    # read this test gates on. (Evicting is the same operation every revocation
    # path performs; the assertion above has already proved the key was live.)
    svc.evict(plaintext)

    gate = _GatedEngine(engine)
    svc.engine = gate
    gate.armed = True
    inflight = asyncio.create_task(svc.authenticate(plaintext))
    await gate.read_done.wait()

    # The credential is revoked while the lookup is still in flight.
    await REVOCATIONS[revoke](svc, kid)

    gate.release.set()
    in_flight_info = await inflight

    assert not _usable(in_flight_info), (
        f"the in-flight authenticate() returned a usable credential after "
        f"{revoke} revoked it — the late cache store undid the revocation")
    assert not _usable(await svc.authenticate(plaintext)), (
        f"the revoked credential was re-cached by the in-flight lookup and "
        f"still authenticates after {revoke}")
    await engine.dispose()


async def test_revocation_after_the_store_still_takes_effect():
    """Control: the ordinary ordering (store, then revoke) must keep working.

    The fix must not be "the cache is never written"; a revocation that lands
    *after* a completed lookup has to evict what was stored, which is what the
    generation check must not break.
    """
    engine, svc = await _svc()
    plaintext, kid = await svc.create_key("victim")
    assert _usable(await svc.authenticate(plaintext))

    await svc.delete_key(kid)

    assert not _usable(await svc.authenticate(plaintext))
    await engine.dispose()


# ============================================================================
# #163 — rpm/tpm of 0 is a *blocked* key, not an unlimited one
# ============================================================================


@pytest.mark.parametrize("field", ["rpm", "tpm"])
async def test_zero_key_limit_is_rejected_at_creation(field):
    """``rpm: 0`` must not be storable: every consumer reads it as "no cap".

    RED: pre-fix it was accepted and stored as 0, and the limiter's falsy
    scope guard then skipped the cap entirely, so an operator parking a key by
    zeroing it got an unlimited one. ``DeploymentParams`` already rejects
    ``<= 0`` for this exact semantic.
    """
    engine, svc = await _svc()
    with pytest.raises(ValueError, match="must be > 0"):
        await svc.create_key("parked", **{field: 0})
    await engine.dispose()


@pytest.mark.parametrize("field", ["rpm", "tpm"])
async def test_zero_key_limit_is_rejected_on_update(field):
    """The same boundary on the update path, which shares ``_coerce_limit``."""
    engine, svc = await _svc()
    _, kid = await svc.create_key("k")
    with pytest.raises(ValueError, match="must be > 0"):
        await svc.update_key(kid, {field: 0})
    assert (await svc.get_key(kid))[field] is None, "the rejected patch was stored"
    await engine.dispose()


async def test_positive_key_limit_still_enforces():
    """Control: a real cap must survive the fix.

    Drives the same pair the request path uses — ``authenticate`` to obtain the
    stored limits, then the limiter — so a key minted with ``rpm=2`` serves two
    requests per window and refuses the third.
    """
    engine, svc = await _svc()
    plaintext, _kid = await svc.create_key("capped", rpm=2, tpm=1000)
    info = await svc.authenticate(plaintext)
    assert (info.rpm, info.tpm) == (2, 1000)

    limiter = RateLimiter()
    allowed = [await limiter.check(info.key_id, info.rpm, info.tpm, est_tokens=10)
               for _ in range(3)]
    assert [ok for ok, _ in allowed] == [True, True, False]
    assert allowed[2][1] > 0, "a refusal must carry a retry horizon"
    await engine.dispose()


@pytest.mark.parametrize("field", ["key_rpm", "key_tpm"])
async def test_limiter_fails_closed_on_a_stored_zero(field):
    """A 0 already in the DB (written before the boundary was tightened) must
    refuse rather than skip the scope.

    RED: pre-fix ``if key_rpm:`` skipped the check, so a stored 0 admitted
    every request and created no window at all.
    """
    limiter = RateLimiter()
    allowed, retry_after = await limiter.check("k1", **{field: 0})
    assert allowed is False
    assert retry_after > 0


async def test_global_zero_limit_still_means_no_cap():
    """Control: the *config-level* global knobs keep their truthy test.

    ``global_rpm``/``global_tpm`` are deployment settings where 0 has always
    meant "cap off", and they are not reachable through the key API. The fix
    narrows the key-scoped scopes only, so a gateway configured with 0 must
    still admit freely rather than refuse everything.
    """
    limiter = RateLimiter(global_rpm=0, global_tpm=0)
    allowed = [await limiter.check("k1", est_tokens=10_000) for _ in range(5)]
    assert all(ok for ok, _ in allowed), (
        "a disabled global cap must not start refusing requests")


async def test_max_budget_of_zero_is_still_a_meaningful_cap():
    """Control: ``max_budget: 0`` must NOT be swept up by the new rule.

    It is a real "spend nothing" cap (the budget check reads it with
    ``is not None``), unlike the rate limits it shares ``_coerce_limit`` with.
    """
    engine, svc = await _svc()
    plaintext, kid = await svc.create_key("broke", max_budget=0)
    assert (await svc.get_key(kid))["max_budget"] == 0.0
    info = await svc.authenticate(plaintext)
    assert info.over_budget is True
    await engine.dispose()


async def test_admin_api_rejects_zero_limits_with_400():
    """The operator-facing surface: parking a key by zeroing it is a 400.

    RED: pre-fix the key was minted and silently served unlimited traffic.
    """
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.post("/admin/keys/generate",
                             json={"name": "parked", "rpm": 0}, headers=AUTH)
            assert r.status_code == 400, f"got {r.status_code}: {r.text[:200]}"
            assert "must be > 0" in r.json()["error"]["message"]

            _, kid = await app.state.wiwi.auth.create_key("k", rpm=5)
            r2 = await c.patch(f"/admin/keys/{kid}", json={"tpm": 0}, headers=AUTH)
            assert r2.status_code == 400, f"got {r2.status_code}: {r2.text[:200]}"
            assert (await app.state.wiwi.auth.get_key(kid))["rpm"] == 5


# ============================================================================
# #198 — the per-owner key cap must hold under concurrent creates
# ============================================================================


async def test_concurrent_creates_respect_the_per_owner_cap():
    """Five simultaneous creates against a cap of one must mint exactly one.

    RED: ``count_keys`` awaits, so every create read the same pre-insert count
    and passed — five concurrent creates against ``max_keys_per_user=1`` minted
    all five (3, in the audit's run; the count varied with scheduling).
    """
    engine, svc = await _svc(max_keys_per_user=1)
    results = await asyncio.gather(
        *(svc.create_key(f"k{i}", owner_id="u1") for i in range(5)),
        return_exceptions=True)

    minted = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, ValueError)]
    assert len(minted) == 1, f"the cap admitted {len(minted)} concurrent creates"
    assert len(refused) == 4
    assert all("key limit reached" in str(e) for e in refused)
    assert await svc.count_keys("u1") == 1, "more rows exist than the cap allows"
    await engine.dispose()


async def test_concurrent_creates_for_different_owners_all_succeed():
    """Control: the cap is per owner, so one owner's cap must not block another.

    Guards against "fixed" by a single service-wide lock that would serialize
    every user's key creation (and, if the cap check were also hoisted out of
    the per-owner scope, reject unrelated owners outright).
    """
    engine, svc = await _svc(max_keys_per_user=1)
    results = await asyncio.gather(
        *(svc.create_key(f"k{i}", owner_id=f"u{i}") for i in range(5)),
        return_exceptions=True)
    assert not [r for r in results if isinstance(r, Exception)]
    for i in range(5):
        assert await svc.count_keys(f"u{i}") == 1
    await engine.dispose()


async def test_cap_still_refuses_a_sequential_create_and_exempts_admins():
    """Control: the ordinary cap check and the admin exemption are unchanged."""
    engine, svc = await _svc(max_keys_per_user=1)
    await svc.create_key("first", owner_id="u1")
    with pytest.raises(ValueError, match="key limit reached"):
        await svc.create_key("second", owner_id="u1")
    # Unowned keys (admin sessions) are exempt from the cap.
    await svc.create_key("admin-1")
    await svc.create_key("admin-2")
    assert await svc.count_keys(None) == 2
    await engine.dispose()


# ============================================================================
# #202 — ttl_seconds: 0 must mean one thing, on both paths
# ============================================================================


async def test_zero_ttl_behaves_identically_on_create_and_update():
    """``ttl_seconds: 0`` is rejected by both paths, with the same message.

    RED: pre-fix create mapped 0 to *no expiry* (the falsy guard) while update
    mapped it to *expire immediately* (the ``is not None`` branch) — the same
    value meaning opposite things depending on which endpoint saw it. ``None``
    is the documented "clear" encoding, so 0 is simply invalid.
    """
    engine, svc = await _svc()
    _, kid = await svc.create_key("k")

    with pytest.raises(ValueError) as create_err:
        await svc.create_key("zero", ttl_seconds=0)
    with pytest.raises(ValueError) as update_err:
        await svc.update_key(kid, {"ttl_seconds": 0})

    assert type(create_err.value) is type(update_err.value)
    assert str(create_err.value) == str(update_err.value), (
        "the same value must be rejected for the same stated reason on both paths")
    assert (await svc.get_key(kid))["expires_at"] is None, (
        "the rejected patch set an expiry anyway")
    await engine.dispose()


async def test_none_still_clears_and_omitted_still_means_never():
    """Control: the documented ``None`` encodings keep working on both paths.

    On create, ``None`` (and an omitted argument) is "never expires"; on
    update, ``None`` clears an existing expiry. Neither may be broken by
    tightening 0.
    """
    engine, svc = await _svc()
    _, omitted = await svc.create_key("omitted")
    _, explicit_none = await svc.create_key("explicit", ttl_seconds=None)
    assert (await svc.get_key(omitted))["expires_at"] is None
    assert (await svc.get_key(explicit_none))["expires_at"] is None

    _, timed = await svc.create_key("timed", ttl_seconds=3600)
    assert (await svc.get_key(timed))["expires_at"] > time.time()
    assert (await svc.update_key(timed, {"ttl_seconds": None}))["expires_at"] is None
    await engine.dispose()


async def test_positive_ttl_is_relative_on_both_paths():
    """Control: a positive duration still lands in the future, create or update."""
    engine, svc = await _svc()
    _, kid = await svc.create_key("k")
    before = time.time()
    row = await svc.update_key(kid, {"ttl_seconds": 60})
    assert row["expires_at"] > before + 55
    await engine.dispose()
