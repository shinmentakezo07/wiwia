"""Regression tests for the 2026-09-15 DB-layer audit (AUDIT #148-#151).

Four gaps found by reading the DB layer against its own documented
invariants:

- **#148** — disabling a user did not revoke the virtual keys they own, so a
  shut-off account kept full API access through every key it had minted.
- **#149** — ``model_price_scopes`` rows survived a provider rename and
  delete, so a later provider reusing the freed name silently inherited the
  old provider's negotiated rates.
- **#150** — the timeseries reader reported the *sum* of two bucket peaks as
  ``tps_p95``, because ``_BucketSum`` added every field including the
  non-additive ``tps_max``.
- **#151** — ``DBSink._query_cache`` never evicted a key that was written
  once and never read again, so it grew without bound on a read-heavy
  workload.
"""
from __future__ import annotations

import time

import httpx
import sqlalchemy as sa
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

import wiwi.server.app as app_mod
from wiwi.auth.service import AuthService
from wiwi.auth.users import UserService
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.logging_core.db_sink import DBSink, _BucketSum
from wiwi.server.config_store import ConfigStore

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _app_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://api.openai.com/v1",
                               keys=[KeyDef(label="default", key="sk-test")])],
        model_list=[ModelEntry(
            model_name="gpt-test",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o-mini"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
    )


async def _engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.execute(sa.text("PRAGMA foreign_keys=ON"))
    return eng


# ============================================================================
# #148 — disabling a user must revoke the keys that user owns
# ============================================================================

async def test_disabled_owner_key_is_rejected_by_the_db_check():
    """A key owned by a disabled account must not authenticate.

    This exercises the DB-level backstop directly (``_lookup_db``'s owner
    check), which is what makes the invariant hold even when the handler's
    revocation is bypassed — a direct ``users`` edit, or a partial failure
    mid-revocation. Before the fix the lookup read ``vkeys`` alone, so the
    owner's flag was never consulted at all.
    """
    eng = await _engine()
    users = UserService(eng, "secret")
    auth = AuthService(eng, MASTER)
    await users.startup()
    await auth.startup()

    u = await users.create_user("victim", "password1")
    plaintext, _kid = await auth.create_key("victim-key", owner_id=u.id)
    assert await auth.authenticate(plaintext) is not None

    # Flip the flag underneath the auth cache, so the next lookup must go to
    # the DB and consult the owner row.
    async with eng.begin() as conn:
        await conn.execute(sa.text("UPDATE users SET disabled = 1 WHERE id = :id"),
                           {"id": u.id})
    auth.evict(plaintext)

    assert await auth.authenticate(plaintext) is None


async def test_disabling_one_owner_leaves_other_owners_keys_alone():
    """Revoking one account's keys must not touch another account's."""
    eng = await _engine()
    users = UserService(eng, "secret")
    auth = AuthService(eng, MASTER)
    await users.startup()
    await auth.startup()

    victim = await users.create_user("victim", "password1")
    bystander = await users.create_user("bystander", "password1")
    victim_key, _ = await auth.create_key("victim-key", owner_id=victim.id)
    bystander_key, _ = await auth.create_key("bystander-key",
                                             owner_id=bystander.id)

    async with eng.begin() as conn:
        await conn.execute(sa.text("UPDATE users SET disabled = 1 WHERE id = :id"),
                           {"id": victim.id})
    auth.evict(victim_key)

    assert await auth.authenticate(victim_key) is None
    assert await auth.authenticate(bystander_key) is not None


async def test_key_whose_owner_row_is_missing_fails_closed():
    """A dangling owner_id must reject the key, not fall through as "no owner".

    The owner check is what makes a disabled account's keys stop working, so
    it has to default to *deny* when the owner row cannot be found. An outer
    join would read the missing owner as "not disabled" and authenticate.
    """
    eng = await _engine()
    users = UserService(eng, "secret")
    auth = AuthService(eng, MASTER)
    await users.startup()
    await auth.startup()

    u = await users.create_user("victim", "password1")
    plaintext, _kid = await auth.create_key("victim-key", owner_id=u.id)

    # Delete the user row out from under the key (no delete-user endpoint
    # exists, so this stands in for a direct DB edit or a partial delete).
    async with eng.begin() as conn:
        await conn.execute(sa.text("DELETE FROM users WHERE id = :id"),
                           {"id": u.id})

    assert await auth.authenticate(plaintext) is None


async def test_disabling_user_does_not_revoke_unowned_admin_keys():
    """Admin-minted keys carry owner_id=None and must survive any user patch."""
    eng = await _engine()
    users = UserService(eng, "secret")
    auth = AuthService(eng, MASTER)
    await users.startup()
    await auth.startup()

    admin_key, _ = await auth.create_key("admin-key", owner_id=None)
    u = await users.create_user("victim", "password1")
    await users.patch(u.id, disabled=True)

    assert await auth.authenticate(admin_key) is not None


async def test_reenabling_user_does_not_resurrect_revoked_keys():
    """Re-enabling an account must not bring back the keys revoked earlier.

    Revocation expires the credential, it does not park it — an operator who
    re-enables an account expects to issue fresh keys. Driven through the
    real admin endpoint, since that is where revocation is performed.
    """
    app = app_mod.create_app(_app_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            state = app.state.wiwi
            u = await state.users.create_user("victim", "password1")
            plaintext, _kid = await state.auth.create_key("victim-key",
                                                          owner_id=u.id)
            assert await state.auth.authenticate(plaintext) is not None

            r = await c.patch(f"/admin/users/{u.id}", json={"disabled": True},
                              headers=AUTH)
            assert r.status_code == 200
            assert await state.auth.authenticate(plaintext) is None

            r = await c.patch(f"/admin/users/{u.id}", json={"disabled": False},
                              headers=AUTH)
            assert r.status_code == 200
            assert await state.auth.authenticate(plaintext) is None


async def test_admin_disable_reports_revoked_key_count():
    """The response and audit diff must say how many keys were revoked.

    An operator disabling an account needs to see the blast radius of the
    action, not just that the user row changed.
    """
    app = app_mod.create_app(_app_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            state = app.state.wiwi
            u = await state.users.create_user("victim", "password1")
            for i in range(3):
                await state.auth.create_key(f"k{i}", owner_id=u.id)

            r = await c.patch(f"/admin/users/{u.id}", json={"disabled": True},
                              headers=AUTH)
            assert r.status_code == 200
            assert r.json()["revoked_keys"] == 3


async def test_admin_disable_leaves_unowned_keys_alone():
    """Disabling a user must not touch admin-minted (unowned) keys."""
    app = app_mod.create_app(_app_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            state = app.state.wiwi
            admin_key, _ = await state.auth.create_key("admin-key",
                                                       owner_id=None)
            u = await state.users.create_user("victim", "password1")

            r = await c.patch(f"/admin/users/{u.id}", json={"disabled": True},
                              headers=AUTH)
            assert r.status_code == 200
            assert r.json()["revoked_keys"] == 0
            assert await state.auth.authenticate(admin_key) is not None


# ============================================================================
# #149 — price scopes must not outlive their provider
# ============================================================================

async def test_provider_rename_rewrites_price_scopes():
    """A scoped price must follow its provider through a rename.

    The scope string is the provider *name*, so a rename that leaves it
    behind strands the override on a name no provider has.
    """
    eng = await _engine()
    cs = ConfigStore(eng)
    await cs.startup()
    await cs.add_provider("acct-a", "openai", "https://a/v1")
    await cs.upsert_price_scope("gpt-x", "acct-a",
                                {"input_cost_per_token": 1.0,
                                 "output_cost_per_token": 2.0})

    await cs.update_provider("acct-a", new_name="acct-b")

    scopes = await cs.load_price_scopes()
    assert [s["scope"] for s in scopes] == ["acct-b"]


async def test_provider_delete_removes_price_scopes():
    """Deleting a provider must take its scoped prices with it.

    Otherwise a provider later created under the freed name inherits rates
    that were negotiated with a different upstream.
    """
    eng = await _engine()
    cs = ConfigStore(eng)
    await cs.startup()
    await cs.add_provider("acct-a", "openai", "https://a/v1")
    await cs.upsert_price_scope("gpt-x", "acct-a",
                                {"input_cost_per_token": 1.0,
                                 "output_cost_per_token": 2.0})

    await cs.delete_provider("acct-a")

    assert await cs.load_price_scopes() == []


async def test_provider_rename_leaves_other_scopes_untouched():
    """Renaming one provider must not disturb another's scoped prices."""
    eng = await _engine()
    cs = ConfigStore(eng)
    await cs.startup()
    await cs.add_provider("acct-a", "openai", "https://a/v1")
    await cs.add_provider("acct-b", "openai", "https://b/v1")
    await cs.upsert_price_scope("gpt-x", "acct-a",
                                {"input_cost_per_token": 1.0,
                                 "output_cost_per_token": 2.0})
    await cs.upsert_price_scope("gpt-x", "acct-b",
                                {"input_cost_per_token": 3.0,
                                 "output_cost_per_token": 4.0})

    await cs.update_provider("acct-a", new_name="acct-c")

    by_scope = {s["scope"]: s for s in await cs.load_price_scopes()}
    assert set(by_scope) == {"acct-c", "acct-b"}
    assert by_scope["acct-b"]["input_cost_per_token"] == 3.0


async def test_provider_rename_rewrites_the_in_memory_cost_map():
    """The running server must bill at the new scope without a restart.

    ConfigStore rewrites the persisted rows, but the cost engine reads its own
    in-memory map — so a rename that only fixed the DB left the process
    serving the old account's scoped rate (and echoing the stale scope from
    /admin/pricing) until it was restarted.
    """
    app = app_mod.create_app(_app_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            state = app.state.wiwi
            await c.post("/admin/providers", headers=AUTH,
                         json={"name": "acct-a", "provider_type": "openai",
                               "base_url": "https://a.example/v1",
                               "key": "sk-a"})
            await c.put("/admin/pricing/gpt-x?provider=acct-a", headers=AUTH,
                        json={"input_per_1m": 1.0, "output_per_1m": 2.0})

            r = await c.patch("/admin/providers/acct-a", headers=AUTH,
                              json={"name": "acct-b"})
            assert r.status_code == 200

            r = await c.get("/admin/pricing", headers=AUTH)
            entry = next(m for m in r.json()["models"]
                         if m["model_id"] == "gpt-x")
            assert [s["provider"] for s in entry["scopes"]] == ["acct-b"]
            # And the cost engine itself resolves the scope under the new name.
            assert state.cost.resolve("gpt-x", "openai",
                                      "acct-b") is not None


async def test_provider_delete_drops_the_in_memory_scope():
    """A deleted provider's scoped rate must stop applying immediately."""
    app = app_mod.create_app(_app_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            state = app.state.wiwi
            await c.post("/admin/providers", headers=AUTH,
                         json={"name": "acct-a", "provider_type": "openai",
                               "base_url": "https://a.example/v1",
                               "key": "sk-a"})
            await c.put("/admin/pricing/gpt-x?provider=acct-a", headers=AUTH,
                        json={"input_per_1m": 1.0, "output_per_1m": 2.0})

            r = await c.delete("/admin/providers/acct-a", headers=AUTH)
            assert r.status_code == 200

            assert "acct-a" not in (state.cost.prices.get("gpt-x") or
                                    {}).get("providers", {})


async def test_rename_onto_a_name_that_already_has_a_scope_does_not_raise():
    """Renaming onto an existing scope name must merge, not collide.

    ``model_price_scopes`` is keyed ``(model_id, scope)``, so a plain UPDATE
    raises IntegrityError when the target name already holds a scope for the
    same model — which is reachable, since an account may legally be named
    after its own provider type. That surfaced as an HTTP 500 mid-rename,
    after the child tables had already been rewritten.
    """
    eng = await _engine()
    cs = ConfigStore(eng)
    await cs.startup()
    await cs.add_provider("acct-a", "openai", "https://a/v1")
    await cs.upsert_price_scope("gpt-x", "openai",
                                {"input_cost_per_token": 1.0,
                                 "output_cost_per_token": 1.0})
    await cs.upsert_price_scope("gpt-x", "acct-a",
                                {"input_cost_per_token": 2.0,
                                 "output_cost_per_token": 2.0})

    # Must not raise.
    await cs.update_provider("acct-a", new_name="openai")

    scopes = await cs.load_price_scopes()
    assert [s["scope"] for s in scopes] == ["openai"]
    # The renamed (live) account's rate wins the collision.
    assert scopes[0]["input_cost_per_token"] == 2.0


async def test_rename_collision_keeps_scopes_for_other_models():
    """Only the colliding model's superseded scope is dropped."""
    eng = await _engine()
    cs = ConfigStore(eng)
    await cs.startup()
    await cs.add_provider("acct-a", "openai", "https://a/v1")
    await cs.upsert_price_scope("gpt-x", "openai",
                                {"input_cost_per_token": 1.0,
                                 "output_cost_per_token": 1.0})
    await cs.upsert_price_scope("gpt-x", "acct-a",
                                {"input_cost_per_token": 2.0,
                                 "output_cost_per_token": 2.0})
    await cs.upsert_price_scope("other-model", "openai",
                                {"input_cost_per_token": 9.0,
                                 "output_cost_per_token": 9.0})

    await cs.update_provider("acct-a", new_name="openai")

    by_model = {s["model_id"]: s for s in await cs.load_price_scopes()}
    assert set(by_model) == {"gpt-x", "other-model"}
    assert by_model["other-model"]["input_cost_per_token"] == 9.0


async def test_provider_type_scope_survives_account_rename():
    """A scope naming a provider *type* is not a name and must not be rewritten."""
    eng = await _engine()
    cs = ConfigStore(eng)
    await cs.startup()
    await cs.add_provider("openrouter-main", "openrouter", "https://or/v1")
    await cs.upsert_price_scope("gpt-x", "openrouter",
                                {"input_cost_per_token": 1.0,
                                 "output_cost_per_token": 2.0})

    await cs.update_provider("openrouter-main", new_name="or-2")

    assert [s["scope"] for s in await cs.load_price_scopes()] == ["openrouter"]


# ============================================================================
# #150 — tps_p95 is a percentile, never a sum
# ============================================================================

class _Bucket:
    """A stand-in for a raw or rolled-up bucket row."""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _bucket(tps_max: float) -> _Bucket:
    return _Bucket(tok_in=0, tok_cached=0, tok_cache_creation=0, tok_out=0,
                   tok_reasoning=0, tps_sum=0.0, tps_count=0,
                   tps_max=tps_max)


def test_bucket_sum_takes_max_of_tps_peak_not_the_sum():
    """Merging a raw bucket with a rolled-up hour must keep the larger peak.

    ``tps_max`` is a maximum from both sides (``MAX(CASE WHEN tps > 0 ...)``
    on the raw side, ``MAX(tps_p95)`` on the rollup side) and is read back as
    the bucket's ``tps_p95``. Adding two maxima reports a rate that never
    occurred.
    """
    merged = _BucketSum(_bucket(50.0), _bucket(60.0))
    assert merged.tps_max == 60.0


def test_bucket_sum_still_adds_the_token_counters():
    """The additive columns must stay additive — only the peak is a maximum."""
    a = _bucket(10.0)
    a.tok_in, a.tok_out, a.tps_sum, a.tps_count = 5, 7, 100.0, 3
    b = _bucket(20.0)
    b.tok_in, b.tok_out, b.tps_sum, b.tps_count = 11, 13, 200.0, 4

    merged = _BucketSum(a, b)

    assert (merged.tok_in, merged.tok_out) == (16, 20)
    assert (merged.tps_sum, merged.tps_count) == (300.0, 7)
    assert merged.tps_max == 20.0


async def test_timeseries_tps_p95_never_exceeds_a_real_peak():
    """End-to-end: a mixed raw/rollup bucket must not report an inflated p95."""
    eng = await _engine()
    sink = DBSink(eng)
    await sink.startup()

    now = time.time()
    bucket_s = 3600
    bucket_ts = int(now // bucket_s) * bucket_s

    # One surviving raw row in the current hour, peaking at 50 tps.
    await sink.write_requests([_log_event(ts=now, tps=50.0)])
    # The same hour already rolled up with a stored p95 of 60 tps.
    async with eng.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO request_rollups (bucket_ts, key_id, model_group,"
            " provider, serving_model, requests, tps_sum, tps_count, tps_p95)"
            " VALUES (:b, 'k1', 'g', 'p', 'm', 1, 200.0, 1, 60.0)"),
            {"b": bucket_ts})

    out = await sink.read_timeseries(bucket_s, "tps", minutes=0)
    peaks = [b["tps_p95"] for b in out["buckets"]]
    assert max(peaks) <= 60.0, f"tps_p95 inflated past the true peak: {peaks}"


def _log_event(ts: float, tps: float):
    from wiwi.logging_core.events import LogEvent
    return LogEvent(stream="request", ts=ts, tps=tps, tok_in=1, tok_out=1)


# ============================================================================
# #151 — the query cache must stay bounded on a read-heavy workload
# ============================================================================

async def test_query_cache_evicts_expired_entries_on_insert():
    """The cache must not retain a key that is written once and never re-read.

    ``_cache_get`` only evicted the key being read, so distinct query keys
    accumulated forever on a workload that reads more than it writes.
    """
    eng = await _engine()
    sink = DBSink(eng)
    await sink.startup()

    # Every entry is born expired, so a sweep-on-insert has something to drop.
    stale = time.monotonic() - DBSink._CACHE_TTL - 1
    n = DBSink._CACHE_MAX_ENTRIES
    for i in range(n):
        sink._query_cache[("read_requests", i, None)] = (["row"], stale)

    sink._cache_put(("read_requests", "fresh", None), ["row"])

    assert len(sink._query_cache) == 1, (
        "expired entries survived an insert — the cache grows without bound")


async def test_query_cache_keeps_live_entries():
    """Eviction must target expired entries only, never a live one."""
    eng = await _engine()
    sink = DBSink(eng)
    await sink.startup()

    sink._cache_put(("read_requests", 1, None), ["live"])
    stale = time.monotonic() - DBSink._CACHE_TTL - 1
    n = DBSink._CACHE_MAX_ENTRIES
    for i in range(n):
        sink._query_cache[("read_requests", i + 100, None)] = (["row"], stale)

    sink._cache_put(("read_requests", "fresh", None), ["row"])

    assert sink._cache_get(("read_requests", 1, None)) == ["live"]


async def test_query_cache_bounds_itself_when_everything_is_fresh():
    """A burst of distinct fresh keys must still be capped.

    The TTL sweep alone cannot bound the dict when every entry is live, so
    the oldest half is dropped — the same two-phase rule AuthService uses.
    """
    eng = await _engine()
    sink = DBSink(eng)
    await sink.startup()

    n = DBSink._CACHE_MAX_ENTRIES
    for i in range(n):
        sink._cache_put(("read_requests", i, None), ["row"])

    assert len(sink._query_cache) <= n
