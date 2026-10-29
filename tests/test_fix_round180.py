"""Round-180 regressions — the 2026-10-29 backend sweep.

Eight fixes, each with the defect it closes:

1. ``stats.timeseries`` (all-time) mapped an event to the bucket after the last
   equal bucket *start*, so a multi-day chart attributed day-1 rows to day 2
   (the ``starts`` list carried one entry per event, not one per bucket). The
   index is arithmetic again, so an empty middle bucket stays empty.
2. ``HealerSettings`` validated only ``probation_weight``; ``tick_s: 0`` turned
   the paid probe loop into a busy-spin (AUDIT #383).
3. Concurrent ``POST /admin/providers`` for one name both succeeded, leaving the
   DB holding one key and the router another (AUDIT #373).
4. A 429 with no ``Retry-After`` cooled the *key*, and the OpenCode free tier
   shares one ``anonymous`` sentinel, so one model's blip was a whole-tier
   outage (AUDIT #469).
5. Hermes device-code sessions piggybacked on the Cline pending map and its
   Cline-specific TTL (AUDIT #424).
6. Per-deployment limits had no column, so they never round-tripped through the
   DB or a backup (AUDIT #461).
7. A failed Cline OAuth *record* write dropped the replacement single-use grant,
   so the next tick presented a consumed token and retired the provider
   (AUDIT #462).
8. Admin edits to a YAML-defined provider applied in memory only and reverted
   on restart; a rename also resurrected the old YAML account (AUDIT #319).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    HealerSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.logging_core.events import LogEvent
from wiwi.providers import cline_oauth
from wiwi.providers.cline_auto_refresh import ClineAutoRefresh
from wiwi.providers.cline_oauth import CLINE_API_BASE
from wiwi.router.router import ProviderAccount, ProviderKey, Router, deployment_scoped_failure
from wiwi.server import stats
from wiwi.server.config_store import ConfigStore

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}
REFRESH_URL = f"{CLINE_API_BASE}/auth/refresh"


def _config(provider: str = "openai", key: str = "sk-live") -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(
            name="p1", provider=provider, base_url="https://p1.example",
            keys=[KeyDef(label="default", key=key)],
        )],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"),
    )


async def _boot(cfg: WiwiConfig):
    from wiwi.server.app import create_app

    app = create_app(cfg)
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://t", headers=AUTH)
    return app, lm, client


# -- 1. all-time timeseries --------------------------------------------------


def _event(ts: float, tok_out: int) -> LogEvent:
    return LogEvent(stream="request", ts=ts, tok_in=1, tok_out=tok_out)


def test_all_time_timeseries_keeps_each_day_in_its_own_bucket():
    """All-time maps a row to its own bucket, including the first one.

    The bucket index is arithmetic over a contiguous grid. A search over a
    per-event ``starts`` list put a row in the first bucket on the last day of
    the chart, because ``bisect_right`` returns the position after the last
    equal element.
    """
    now = 1_800_000_000.0
    day = 86_400
    d1 = int((now - 2 * day) // day) * day
    d2 = d1 + day
    events = [_event(d1 + 10, 1), _event(d1 + 20, 2), _event(d2 + 40, 100)]

    series = stats.timeseries(events, "minute", "tokens", 0, now=now)

    assert series["bucket_seconds"] == day
    assert [b["t"] for b in series["buckets"]] == [d1, d2]
    assert [b["tok_out"] for b in series["buckets"]] == [3, 100]


def test_all_time_timeseries_keeps_an_empty_middle_bucket_empty():
    """A gap must stay a gap: the index is arithmetic, not a start lookup.

    All-time zero-fills the span between its first and last populated bucket, so
    a row after a silent day belongs two buckets on. Searching the *populated*
    starts instead would slide it into the empty bucket.
    """
    now = 1_800_000_000.0
    day = 86_400
    d1 = int((now - 3 * day) // day) * day
    d3 = d1 + 2 * day
    events = [_event(d1 + 10, 1), _event(d1 + 20, 2), _event(d3 + 40, 100)]

    series = stats.timeseries(events, "minute", "tokens", 0, now=now)

    assert [b["t"] for b in series["buckets"]] == [d1, d1 + day, d3]
    assert [b["tok_out"] for b in series["buckets"]] == [3, 0, 100]


def test_all_time_timeseries_single_bucket_still_reports_the_total():
    """Control: the one-bucket shape must not regress to a dropped row."""
    now = 1_800_000_000.0
    series = stats.timeseries([_event(now - 30, 5)], "minute", "tokens", 0,
                              now=now)
    assert len(series["buckets"]) == 1
    assert series["buckets"][0]["tok_out"] == 5


# -- 2. healer spend knobs ---------------------------------------------------


@pytest.mark.parametrize("kwargs", [
    {"tick_s": 0},
    {"tick_s": -1.0},
    {"probe_timeout_s": 0},
    {"max_probes_per_sweep": 0},
    {"probes_to_restore": 0},
    {"min_probe_interval_s": -1.0},
    {"probe_backoff_base_s": -5.0},
])
def test_healer_rejects_degenerate_spend_knobs(kwargs):
    """A typo here spends real money or disables a guard (AUDIT #383)."""
    with pytest.raises(ValidationError):
        HealerSettings(**kwargs)


def test_healer_keeps_small_positive_and_zero_interval_values():
    """Fast cadences and ``0`` intervals stay legal — tests use them."""
    s = HealerSettings(enabled=True, tick_s=0.05, min_probe_interval_s=0.0)
    assert s.tick_s == 0.05
    assert s.min_probe_interval_s == 0.0


# -- 3. concurrent provider creation ----------------------------------------


async def test_concurrent_provider_creates_leave_one_consistent_account():
    """The guard and the insert are one critical section (AUDIT #373)."""
    app, lm, client = await _boot(_config())
    try:
        async with client:
            body = {"name": "dup", "provider_type": "openai",
                    "base_url": "https://dup.example",
                    "key": "sk-dup", "label": "default"}
            first, second = await asyncio.gather(
                client.post("/admin/providers", json=body),
                client.post("/admin/providers", json={**body, "key": "sk-dup-2"}),
            )
            assert sorted([first.status_code, second.status_code]) == [200, 409]

            winner = next(r for r in (first, second) if r.status_code == 200)
            assert winner.json()["name"] == "dup"
            live = app.state.wiwi.router.providers["dup"].keys[0].secret
            stored = await app.state.wiwi.config_store.load_all()
            db_key = next(k for k in stored["keys"] if k["label"] == "default")
            assert db_key["secret"] == live, (
                "the DB and the router disagree about the created account")
    finally:
        await client.aclose()
        await lm.__aexit__(None, None, None)


# -- 4. a bare 429 is a deployment failure ----------------------------------


def test_bare_429_is_deployment_scoped():
    assert deployment_scoped_failure(429, None) is True
    assert deployment_scoped_failure(429, 0.0) is True
    assert deployment_scoped_failure(429, 12.0) is False
    assert deployment_scoped_failure(503, None) is True
    assert deployment_scoped_failure(401, None) is False


@pytest.mark.parametrize("failover_mode", ["any_error", "standard"])
def test_bare_429_leaves_the_shared_sentinel_key_in_rotation(failover_mode):
    """One model's 429 must not retire the sentinel every free model shares."""
    acct = ProviderAccount(
        name="zen", provider_type="opencode", base_url="https://zen.example",
        keys=[ProviderKey(label="anonymous", secret="anonymous")])
    key = acct.keys[0]

    acct.on_result(key, 429, None, failover_mode=failover_mode)

    assert key.status == "active"
    assert key.available
    assert key.err_count == 0


def test_429_with_retry_after_still_cools_the_key():
    """A stated window IS a quota statement about the credential."""
    acct = ProviderAccount(
        name="p", provider_type="openai", base_url="https://p.example",
        keys=[ProviderKey(label="k", secret="sk")])
    key = acct.keys[0]

    acct.on_result(key, 429, 7.0, failover_mode="standard")

    assert key.status == "cooling"
    assert not key.available


# -- 5. one pending-session store, per-session TTL ---------------------------


async def test_pending_oauth_sessions_are_one_store_with_per_flow_ttl():
    app, lm, _client = await _boot(_config())
    try:
        state = app.state.wiwi
        state.oauth_pending["hermes"] = {
            "kind": "hermes_device_code", "created_at": 0.0, "ttl_s": 600.0}
        state.oauth_pending["workbuddy"] = {
            "kind": "workbuddy_login", "created_at": 0.0, "ttl_s": 5000.0}

        state.evict_expired_pending(now=1000.0)

        assert "hermes" not in state.oauth_pending
        assert "workbuddy" in state.oauth_pending, (
            "one flow's TTL shortened another flow's window")
    finally:
        await _client.aclose()
        await lm.__aexit__(None, None, None)


# -- 6. per-deployment limits round-trip ------------------------------------


async def test_deployment_limits_round_trip_and_survive_a_weight_edit():
    cs = ConfigStore(create_async_engine("sqlite+aiosqlite:///:memory:"))
    await cs.startup()
    try:
        await cs.add_deployment("g", "p", "m", 1, rpm=11, tpm=2200,
                                timeout=9.5, max_tokens=1234,
                                prompt_cache=True)
        stored = await cs.load_all()
        row = next(d for d in stored["deployments"] if d["model_id"] == "m")
        assert (row["rpm"], row["tpm"], row["timeout"], row["max_tokens"],
                row["prompt_cache"]) == (11, 2200, 9.5, 1234, True)

        # A weight edit must not wipe the limits it never mentioned.
        await cs.update_deployment_weight("g", "p", "m", 4)
        row = next(d for d in (await cs.load_all())["deployments"]
                   if d["model_id"] == "m")
        assert row["weight"] == 4
        assert (row["rpm"], row["tpm"], row["max_tokens"],
                row["prompt_cache"]) == (11, 2200, 1234, True)

        # An explicit None clears a stored limit.
        await cs.add_deployment("g", "p", "m", 4, rpm=None)
        row = next(d for d in (await cs.load_all())["deployments"]
                   if d["model_id"] == "m")
        assert row["rpm"] is None
        assert row["tpm"] == 2200
    finally:
        await cs.engine.dispose()


async def test_detach_keeps_the_stored_limits():
    """A tombstone must not erase the row's limits for a later re-attach."""
    cs = ConfigStore(create_async_engine("sqlite+aiosqlite:///:memory:"))
    await cs.startup()
    try:
        await cs.add_deployment("g", "p", "m", 3, rpm=5, max_tokens=99)
        await cs.delete_deployment("g", "p", "m")
        row = next(d for d in (await cs.load_all())["deployments"]
                   if d["model_id"] == "m")
        assert row["detached"] is True
        assert (row["weight"], row["rpm"], row["max_tokens"]) == (3, 5, 99)
    finally:
        await cs.engine.dispose()


# -- 7. a failed record write keeps the rotated grant -----------------------


class _Mini:
    def __init__(self, router: Router, config_store: ConfigStore) -> None:
        self.router = router
        self.config_store = config_store


@respx.mock
async def test_failed_record_write_is_retried_without_re_presenting_the_grant(
        monkeypatch):
    """The consumed grant must never be presented again (AUDIT #462)."""
    router = Router(_config(provider="cline", key="workos:stale"))
    cs = ConfigStore(create_async_engine("sqlite+aiosqlite:///:memory:"))
    await cs.startup()
    state = _Mini(router=router, config_store=cs)
    worker = ClineAutoRefresh(state)  # type: ignore[arg-type]
    record = {"refresh_token": "ref-1",
              "expires_at": cline_oauth.resolve_expires_at(3600)}
    await cs.set_setting("cline_oauth:p1", record)

    failures = {"n": 1}
    real_set = cs.set_setting

    async def flaky_set(key, value):
        if failures["n"]:
            failures["n"] -= 1
            raise RuntimeError("settings table is locked")
        return await real_set(key, value)

    monkeypatch.setattr(cs, "set_setting", flaky_set)

    refresh = respx.post(REFRESH_URL).respond(json={"data": {
        "accessToken": "acc-new-1234567890", "refreshToken": "ref-2",
        "expiresAt": cline_oauth.resolve_expires_at(3600)}})

    # First attempt: the rotation succeeds upstream, the record write fails.
    assert await worker.refresh_now("p1") is False
    assert refresh.call_count == 1

    # Second attempt: the pending grant is persisted WITHOUT a new rotation.
    assert await worker.flush_pending("p1") is True
    assert refresh.call_count == 1, (
        "the worker re-presented the consumed refresh token")

    stored = await cs.get_setting("cline_oauth:p1")
    assert stored["refresh_token"] == "ref-2"
    assert worker._pending.get("p1") is None
    assert router.providers["p1"].keys[0].secret == "acc-new-1234567890"
    await cs.engine.dispose()


# -- 8. DB rows override YAML ----------------------------------------------


async def test_renaming_a_yaml_provider_does_not_resurrect_the_old_name(
        tmp_path, monkeypatch):
    """The rename writes an override row plus a tombstone (AUDIT #319)."""
    monkeypatch.setenv("P1KEY", "sk-real")
    cfg_path = tmp_path / "wiwi.yaml"
    cfg_path.write_text(f"""
providers:
  - name: p1
    provider: openai
    base_url: https://api.openai.com/v1
    keys:
      - {{label: default, key: os.environ/P1KEY}}
model_list:
  - model_name: gpt-4o
    litellm_params: {{model: gpt-4o}}
    wiwi_params: {{provider: p1, model: gpt-4o}}
general_settings:
  master_key: {MASTER}
  database_url: "sqlite+aiosqlite:///{tmp_path}/app.db"
""")

    from wiwi.config import load_config
    from wiwi.server.app import create_app

    def boot():
        app = create_app(load_config(str(cfg_path)))
        return app, LifespanManager(app)

    app, lm = boot()
    await lm.__aenter__()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t", headers=AUTH) as client:
            r = await client.patch("/admin/providers/p1", json={"name": "p1x"})
            assert r.status_code == 200, r.text
    finally:
        await lm.__aexit__(None, None, None)

    app2, lm2 = boot()
    await lm2.__aenter__()
    try:
        names = set(app2.state.wiwi.router.providers)
        assert "p1x" in names, "the renamed account did not survive the restart"
        assert "p1" not in names, "the YAML copy of the old name came back"
    finally:
        await lm2.__aexit__(None, None, None)


async def test_rotated_key_overrides_the_yaml_credential_at_startup(tmp_path):
    """A persisted rotation wins over the YAML secret on the next boot."""
    db = f"sqlite+aiosqlite:///{tmp_path}/app.db"
    cfg = _config(key="sk-yaml-original")
    cfg.general_settings = GeneralSettings(master_key=MASTER, database_url=db)

    app, lm, _client = await _boot(cfg)
    try:
        await app.state.wiwi.config_store.update_key_secret(
            "p1", "default", "sk-rotated")
    finally:
        await _client.aclose()
        await lm.__aexit__(None, None, None)

    cfg2 = _config(key="sk-yaml-original")
    cfg2.general_settings = GeneralSettings(master_key=MASTER, database_url=db)
    app2, lm2, client2 = await _boot(cfg2)
    try:
        assert app2.state.wiwi.router.providers["p1"].keys[0].secret == \
            "sk-rotated"
    finally:
        await client2.aclose()
        await lm2.__aexit__(None, None, None)
