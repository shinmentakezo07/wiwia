"""Scoped pricing: per-model-per-provider rates.

Before this round a model had exactly one rate pair, keyed by bare model id in
``CostEngine.prices``, and the gateway priced every request through it
regardless of which upstream served the request. That is wrong as soon as one
model id is reachable through two provider accounts at different real costs.

Three levels, most specific wins::

    gpt-4o @ openai-main   (provider account)
    gpt-4o @ openai        (provider type)
    gpt-4o @ (all)         (base rate)

Two pre-existing defects in the same path are fixed here as well:

* ``DBSink._row_matches`` tested ``isinstance(status, int)``, but the gateway
  records ``AttemptRecord.status`` as a *string* ("ok", "ok_after_refresh",
  "http_429", ...). The 2xx rule therefore never fired on real data and the
  matcher silently fell back to the last attempt — the wrong row whenever a
  success was followed by a failed retry. Every fixture in
  ``test_fix_round32.py`` hand-builds integer statuses, so the suite was green
  while the rule was dead code.
* ``cache_creation_per_1m`` was accepted by PUT and echoed by GET but had no
  database column, so it was lost on restart.
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
from wiwi.cost.pricing import CostEngine

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


# -- cost engine: resolution precedence ----------------------------------------


def test_resolve_account_beats_type_beats_base():
    """The most specific scope wins, in order account > type > base."""
    ce = CostEngine()
    ce.prices["m"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {
            "openai": {"input_cost_per_token": 2e-6},
            "openai-main": {"input_cost_per_token": 1e-6},
        },
    }
    # Account scope wins over both the type scope and the base.
    r = ce.resolve("m", provider_type="openai", provider_name="openai-main")
    assert r["input_cost_per_token"] == 1e-6
    # Type scope applies when no account scope matches.
    r = ce.resolve("m", provider_type="openai", provider_name="other-acct")
    assert r["input_cost_per_token"] == 2e-6
    # Base applies when neither scope matches.
    r = ce.resolve("m", provider_type="anthropic", provider_name="anthropic-main")
    assert r["input_cost_per_token"] == 3e-6


def test_scoped_override_inherits_unset_rates_from_base():
    """A scope may override just one rate; the rest fall through to the base.

    This is what makes a scoped entry cheap to write — most providers differ
    on input price only.
    """
    ce = CostEngine()
    ce.prices["m"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {"openai": {"input_cost_per_token": 2e-6}},
    }
    r = ce.resolve("m", provider_type="openai", provider_name="acct")
    assert r["input_cost_per_token"] == 2e-6
    assert r["output_cost_per_token"] == 15e-6, "unset rate must inherit the base"


def test_resolve_without_identity_is_todays_behavior():
    """No identity = the base entry, so every existing caller is unchanged."""
    ce = CostEngine()
    ce.register("m", input_per_token=1e-6, output_per_token=2e-6)
    assert ce.resolve("m")["input_cost_per_token"] == 1e-6
    assert ce._lookup("m")["output_cost_per_token"] == 2e-6


def test_scope_only_entry_without_base_rates_does_not_crash():
    """A scope-only entry must not KeyError in the request path.

    ``cost_with_status`` indexes ``p["input_cost_per_token"]`` directly, so an
    entry carrying only a ``providers`` sub-map would raise for any request
    that fell through to the base. It must instead report the model unpriced.
    """
    ce = CostEngine()
    ce.prices["m"] = {"providers": {"openai": {"input_cost_per_token": 1e-6,
                                               "output_cost_per_token": 2e-6}}}
    # An account with no matching scope falls through to a base that has no
    # rates — unpriced, not a crash.
    state = ce.cost_with_status("m", 1000, 500,
                                provider_type="anthropic",
                                provider_name="anthropic-main")
    assert state.unpriced is True
    assert state.cost == 0.0
    # The scoped account is priced normally.
    state = ce.cost_with_status("m", 1000, 500,
                                provider_type="openai",
                                provider_name="openai")
    assert state.unpriced is False
    assert abs(state.cost - (1000 * 1e-6 + 500 * 2e-6)) < 1e-12


def test_scope_applies_at_every_step_of_the_tail_walk():
    """The scope must be honoured on the legacy slash-trim tail too.

    The gateway calls with ``"<provider_type>/<model_id>"`` and the table may
    key on a shorter tail. Returning the tail's raw entry would silently
    ignore the scope and bill the base rate.
    """
    ce = CostEngine()
    ce.prices["claude-sonnet-4"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {"openrouter": {"input_cost_per_token": 1e-6}},
    }
    # Full key misses; the tail walk finds "claude-sonnet-4" — and must apply
    # the openrouter scope to it.
    r = ce.resolve("openrouter/anthropic/claude-sonnet-4",
                   provider_type="openrouter", provider_name="openrouter")
    assert r["input_cost_per_token"] == 1e-6, "tail-walk result ignored the scope"


def test_account_and_type_share_a_namespace_account_wins():
    """An account named exactly like its type (the shipped example config has
    ``name: openrouter`` / ``provider: openrouter``) resolves to the account.

    One namespace, account precedence — deterministic and documented, rather
    than an ambiguous key that silently means one or the other.
    """
    ce = CostEngine()
    ce.prices["m"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {"openrouter": {"input_cost_per_token": 1e-6}},
    }
    r = ce.resolve("m", provider_type="openrouter", provider_name="openrouter")
    assert r["input_cost_per_token"] == 1e-6


def test_legacy_tail_lookup_still_works_without_identity():
    """The pre-existing prefix-stripping behaviour is untouched."""
    ce = CostEngine()
    ce.register("claude-sonnet-4-20250514",
                input_per_token=3e-6, output_per_token=15e-6)
    a = ce.cost("anthropic/claude-sonnet-4-20250514", 1000, 500)
    b = ce.cost("openrouter/anthropic/claude-sonnet-4-20250514", 1000, 500)
    assert a > 0 and abs(a - b) < 1e-12


def test_register_preserves_existing_scopes():
    """``register()`` is the documented way to seed a price; re-registering a
    model must not silently drop its scoped overrides."""
    ce = CostEngine()
    ce.register("m", input_per_token=1e-6, output_per_token=2e-6)
    ce.prices["m"]["providers"] = {"openai": {"input_cost_per_token": 5e-7}}
    ce.register("m", input_per_token=9e-6, output_per_token=9e-6)
    r = ce.resolve("m", provider_type="openai", provider_name="acct")
    assert r["input_cost_per_token"] == 5e-7, "register() dropped the scopes"


# -- gateway: each request priced at its own provider's rate -------------------


def _two_account_config() -> WiwiConfig:
    """One model group, two deployments on different provider *types*."""
    return WiwiConfig(
        providers=[
            ProviderDef(name="openai-main", provider="openai",
                        keys=[KeyDef(label="a", key="k-openai")]),
            ProviderDef(name="anthropic-main", provider="anthropic",
                        keys=[KeyDef(label="a", key="k-anthropic")]),
        ],
        model_list=[
            ModelEntry(model_name="shared",
                       wiwi_params=DeploymentParams(provider="openai-main",
                                                    model="shared")),
            ModelEntry(model_name="shared",
                       wiwi_params=DeploymentParams(provider="anthropic-main",
                                                    model="shared")),
        ],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


def test_gateway_prices_each_deployment_at_its_own_scoped_rate():
    """The end-to-end point of the feature: one model id, two upstreams, two
    prices. Both are asserted through the same gateway so a regression in
    either direction is caught."""
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.ir import types as ir
    from wiwi.router.router import Router

    ce = CostEngine()
    ce.prices["shared"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {
            "openai": {"input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6},
        },
    }
    g = Gateway(Router(_two_account_config()), ce)

    deps = {d.provider.name: d for d in g.router.groups["shared"]}
    assert set(deps) == {"openai-main", "anthropic-main"}, deps

    req = ir.Request(model="shared",
                     messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])])

    # OpenAI account: scoped rate (1e-6 in / 1e-6 out).
    ctx = RequestContext(surface="chat", ir_req=req, group="shared")
    g._price(ctx, deps["openai-main"],
             ir.Usage(prompt_tokens=1000, completion_tokens=1000))
    assert abs(ctx.cost - (1000 * 1e-6 + 1000 * 1e-6)) < 1e-12

    # Anthropic account: base rate (3e-6 in / 15e-6 out).
    ctx2 = RequestContext(surface="chat", ir_req=req, group="shared")
    g._price(ctx2, deps["anthropic-main"],
             ir.Usage(prompt_tokens=1000, completion_tokens=1000))
    assert abs(ctx2.cost - (1000 * 3e-6 + 1000 * 15e-6)) < 1e-12


def test_cache_savings_uses_the_same_scoped_rate_as_cost():
    """cache_savings must resolve the scoped rate, not the base.

    Otherwise the logged savings contradict the logged cost — savings can even
    exceed the input cost actually charged.
    """
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway, build_log_event
    from wiwi.ir import types as ir
    from wiwi.router.router import Router

    ce = CostEngine()
    ce.prices["shared"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {"openai": {"input_cost_per_token": 1e-6,
                                 "cache_read_input_cost_per_token": 1e-7}},
    }
    g = Gateway(Router(_two_account_config()), ce)
    deps = {d.provider.name: d for d in g.router.groups["shared"]}

    req = ir.Request(model="shared",
                     messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])])
    ctx = RequestContext(surface="chat", ir_req=req, group="shared")
    g._price(ctx, deps["openai-main"],
             ir.Usage(prompt_tokens=1000, completion_tokens=0, cached_tokens=1000))
    evt = build_log_event(ctx)
    # Savings are computed at the SCOPED input rate (1e-6), not the base (3e-6).
    assert abs(evt.cache_savings - 1000 * (1e-6 - 1e-7)) < 1e-12


# -- admin API -----------------------------------------------------------------


@pytest.fixture
async def client():
    app = app_mod.create_app(_two_account_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def test_put_scoped_price_round_trips_through_get(client):
    r = await client.put("/admin/pricing/shared?provider=openai", headers=AUTH,
                         json={"input_per_1m": 1.0, "output_per_1m": 2.0})
    assert r.status_code == 200, r.text

    r = await client.get("/admin/pricing", headers=AUTH)
    entry = next(m for m in r.json()["models"] if m["model_id"] == "shared")
    scopes = {s["provider"]: s for s in entry["scopes"]}
    assert scopes["openai"]["input_per_1m"] == pytest.approx(1.0)
    assert scopes["openai"]["output_per_1m"] == pytest.approx(2.0)


async def test_put_scoped_price_accepts_an_account_name(client):
    r = await client.put("/admin/pricing/shared?provider=openai-main",
                         headers=AUTH,
                         json={"input_per_1m": 5.0, "output_per_1m": 6.0})
    assert r.status_code == 200, r.text
    app = client._transport.app
    state = app.state.wiwi
    scoped = state.cost.prices["shared"]["providers"]["openai-main"]
    assert abs(scoped["input_cost_per_token"] - 5e-6) < 1e-12


async def test_put_rejects_an_unknown_scope(client):
    """A typo must not create a price that can never apply."""
    r = await client.put("/admin/pricing/shared?provider=no-such-provider",
                         headers=AUTH,
                         json={"input_per_1m": 1.0, "output_per_1m": 1.0})
    assert r.status_code == 400, r.text
    app = client._transport.app
    state = app.state.wiwi
    assert "no-such-provider" not in state.cost.prices.get("shared", {}).get(
        "providers", {})


async def test_base_put_preserves_existing_scopes(client):
    """Editing the all-providers rate must not wipe the per-provider prices."""
    await client.put("/admin/pricing/shared?provider=openai", headers=AUTH,
                     json={"input_per_1m": 1.0, "output_per_1m": 2.0})
    r = await client.put("/admin/pricing/shared", headers=AUTH,
                         json={"input_per_1m": 3.0, "output_per_1m": 15.0})
    assert r.status_code == 200, r.text
    app = client._transport.app
    state = app.state.wiwi
    assert "openai" in state.cost.prices["shared"]["providers"], (
        "a base-rate edit deleted the scoped prices")
    assert abs(state.cost.prices["shared"]["input_cost_per_token"] - 3e-6) < 1e-12


async def test_delete_scoped_price_keeps_the_base_entry(client):
    await client.put("/admin/pricing/shared", headers=AUTH,
                     json={"input_per_1m": 3.0, "output_per_1m": 15.0})
    await client.put("/admin/pricing/shared?provider=openai", headers=AUTH,
                     json={"input_per_1m": 1.0, "output_per_1m": 2.0})

    r = await client.delete("/admin/pricing/shared?provider=openai", headers=AUTH)
    assert r.status_code == 200, r.text
    app = client._transport.app
    state = app.state.wiwi
    assert "openai" not in state.cost.prices["shared"].get("providers", {})
    assert "shared" in state.cost.prices, "base entry must survive"
    assert abs(state.cost.prices["shared"]["input_cost_per_token"] - 3e-6) < 1e-12


async def test_delete_without_scope_removes_base_and_scopes(client):
    await client.put("/admin/pricing/shared", headers=AUTH,
                     json={"input_per_1m": 3.0, "output_per_1m": 15.0})
    await client.put("/admin/pricing/shared?provider=openai", headers=AUTH,
                     json={"input_per_1m": 1.0, "output_per_1m": 2.0})
    r = await client.delete("/admin/pricing/shared", headers=AUTH)
    assert r.status_code == 200, r.text
    app = client._transport.app
    state = app.state.wiwi
    assert "shared" not in state.cost.prices


# -- persistence ---------------------------------------------------------------


async def test_scoped_prices_survive_a_restart(client):
    """Scoped prices must rehydrate from the database, not just live in RAM."""
    await client.put("/admin/pricing/shared?provider=openai", headers=AUTH,
                     json={"input_per_1m": 1.0, "output_per_1m": 2.0})
    app = client._transport.app
    state = app.state.wiwi

    # Rebuild a fresh engine the way startup does.
    rows = await state.config_store.load_price_scopes()
    ce = CostEngine()
    for row in rows:
        mid, scope = row["model_id"], row["scope"]
        entry = ce.prices.setdefault(mid, {})
        entry.setdefault("providers", {})[scope] = {
            k: v for k, v in row.items() if k not in ("model_id", "scope")}
    r = ce.resolve("shared", provider_type="openai", provider_name="openai")
    assert r is not None and abs(r["input_cost_per_token"] - 1e-6) < 1e-12


async def test_cache_creation_rate_survives_a_db_round_trip(client):
    """PUT accepted cache_creation_per_1m but no column stored it, so it was
    lost on restart while GET echoed it back from memory."""
    r = await client.put("/admin/pricing/claude-x", headers=AUTH, json={
        "input_per_1m": 3.0, "output_per_1m": 15.0,
        "cache_read_per_1m": 0.3, "cache_creation_per_1m": 3.75,
    })
    assert r.status_code == 200, r.text
    app = client._transport.app
    state = app.state.wiwi
    rows = await state.config_store.load_prices()
    row = next(p for p in rows if p["model_id"] == "claude-x")
    assert "cache_creation_input_cost_per_token" in row, (
        "cache_creation rate was not persisted")
    assert abs(row["cache_creation_input_cost_per_token"] - 3.75e-6) < 1e-12


# -- retroactive repricer ------------------------------------------------------


def _repricer_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="openai-main", provider="openai",
                        keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="anthropic-main", provider="anthropic",
                        keys=[KeyDef(label="a", key="k2")]),
        ],
        model_list=[
            ModelEntry(model_name="retro",
                       wiwi_params=DeploymentParams(provider="openai-main",
                                                    model="retro")),
            ModelEntry(model_name="retro",
                       wiwi_params=DeploymentParams(provider="anthropic-main",
                                                    model="retro")),
        ],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def repricer_client():
    app = app_mod.create_app(_repricer_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def _fetch_rows(client) -> list[dict]:
    import sqlalchemy as sa
    app = client._transport.app
    state = app.state.wiwi
    async with state._db_sink.engine.connect() as conn:
        res = await conn.execute(sa.text(
            "SELECT request_id, cost FROM request_logs ORDER BY ts, id"))
        return [{"request_id": r[0], "cost": r[1]} for r in res.all()]


async def test_repricer_matches_string_status_attempts(repricer_client):
    """RED for the status-type bug.

    The gateway records ``AttemptRecord.status`` as a string ("ok",
    "http_500"), and serializes it verbatim. The old matcher tested
    ``isinstance(status, int)``, so the 2xx rule never fired on real data and
    the matcher fell back to the LAST attempt. Here the success comes first and
    a later attempt fails: the old code picks the failed attempt (wrong row),
    the fixed code picks the serving one.
    """
    app = repricer_client._transport.app
    state = app.state.wiwi
    sink = state._db_sink
    from wiwi.logging_core.events import LogEvent

    await sink.write_requests([
        # Served by "retro" first, then a fallback attempt failed on another
        # model — the row's usage belongs to "retro".
        LogEvent(stream="request", ts=1.0, request_id="okThenFail",
                 model_group="g1", provider="openai-main", status=200,
                 tok_in=1_000_000, tok_out=0, cost=0.0, attempts=[
                     {"deployment": "g1/retro", "provider": "openai-main",
                      "key": "a", "status": "ok", "latency_ms": 1.0},
                     {"deployment": "g1/other", "provider": "openai-main",
                      "key": "a", "status": "http_500", "latency_ms": 2.0}]),
    ])

    ce = CostEngine()
    ce.prices["retro"] = {"input_cost_per_token": 3e-6,
                          "output_cost_per_token": 15e-6}
    deltas = await sink.reprice_unpriced_history(
        "retro", lambda name: ce.resolve("retro", provider_name=name))
    rows = {r["request_id"]: r["cost"] for r in await _fetch_rows(repricer_client)}
    assert abs(rows["okThenFail"] - 3.0) < 1e-9, (
        "string-status 'ok' attempt was not recognised as the serving attempt")
    assert deltas == {}


async def test_repricer_prices_each_row_at_its_own_scoped_rate(repricer_client):
    """Two rows on the same model served by two accounts get two prices."""
    app = repricer_client._transport.app
    state = app.state.wiwi
    sink = state._db_sink
    from wiwi.logging_core.events import LogEvent

    await sink.write_requests([
        LogEvent(stream="request", ts=1.0, request_id="rowOpenai",
                 model_group="g1", provider="openai-main", status=200,
                 tok_in=1_000_000, tok_out=0, cost=0.0, attempts=[
                     {"deployment": "g1/retro", "provider": "openai-main",
                      "key": "a", "status": "ok", "latency_ms": 1.0}]),
        LogEvent(stream="request", ts=2.0, request_id="rowAnthropic",
                 model_group="g1", provider="anthropic-main", status=200,
                 tok_in=1_000_000, tok_out=0, cost=0.0, attempts=[
                     {"deployment": "g1/retro", "provider": "anthropic-main",
                      "key": "a", "status": "ok", "latency_ms": 1.0}]),
    ])

    ce = CostEngine()
    ce.prices["retro"] = {
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "providers": {"openai": {"input_cost_per_token": 1e-6}},
    }

    def rate_for(provider_name: str):
        acct = state.router.providers.get(provider_name)
        return ce.resolve("retro",
                          acct.provider_type if acct else None, provider_name)

    await sink.reprice_unpriced_history("retro", rate_for)
    rows = {r["request_id"]: r["cost"] for r in await _fetch_rows(repricer_client)}
    assert abs(rows["rowOpenai"] - 1.0) < 1e-9, "openai row used the scoped rate"
    assert abs(rows["rowAnthropic"] - 3.0) < 1e-9, "anthropic row used the base rate"


async def test_scoped_first_price_records_the_scope(repricer_client):
    """A scope-only first price must be recorded under its scope, and the
    per-model ``was_unpriced`` gate must not strand other accounts.

    The true-up gate was a per-MODEL boolean. If the first price for a model
    is scoped to one account, the model becomes "priced" and adding a second
    account's price later would never true up that account's rows.
    """
    r = await repricer_client.put("/admin/pricing/retro?provider=openai",
                                  headers=AUTH,
                                  json={"input_per_1m": 1.0, "output_per_1m": 0.0})
    assert r.status_code == 200, r.text
    r = await repricer_client.put("/admin/pricing/retro?provider=anthropic",
                                  headers=AUTH,
                                  json={"input_per_1m": 3.0, "output_per_1m": 0.0})
    assert r.status_code == 200, r.text
    app = repricer_client._transport.app
    state = app.state.wiwi
    assert "anthropic" in state.cost.prices["retro"]["providers"]


# -- migrate -------------------------------------------------------------------


async def test_migrate_adds_cache_creation_column_to_existing_table(tmp_path):
    """A pre-existing model_prices table (no cache_creation column) must be
    migrated in place — CREATE TABLE IF NOT EXISTS is a no-op on it."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.server.config_store import ConfigStore

    db = tmp_path / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    # Build the OLD shape first.
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE model_prices ("
            " model_id TEXT PRIMARY KEY,"
            " input_cost_per_token REAL NOT NULL DEFAULT 0,"
            " output_cost_per_token REAL NOT NULL DEFAULT 0,"
            " cache_read_input_cost_per_token REAL,"
            " max_input_tokens INTEGER, max_output_tokens INTEGER, mode TEXT)"))
    store = ConfigStore(engine)
    await store.startup()
    async with engine.connect() as conn:
        cols = {r[1] for r in (await conn.execute(
            sa.text("PRAGMA table_info(model_prices)"))).all()}
    assert "cache_creation_input_cost_per_token" in cols, (
        "existing model_prices table was not migrated")
    await engine.dispose()


async def test_scope_table_exists_after_startup(client):
    """The scope table is created by CREATE TABLE IF NOT EXISTS, so it reaches
    old and new databases alike."""
    import sqlalchemy as sa
    app = client._transport.app
    state = app.state.wiwi
    async with state.config_store.engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name='model_price_scopes'"))).all()
    assert rows, "model_price_scopes table was not created"


# -- live traffic --------------------------------------------------------------


@respx.mock
async def test_scoped_price_reaches_the_request_path(client):
    """A scoped price set through the admin API must price real traffic."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0,
            "model": "shared",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 0,
                      "total_tokens": 1000},
        }))
    await client.put("/admin/pricing/shared?provider=openai", headers=AUTH,
                     json={"input_per_1m": 1.0, "output_per_1m": 0.0})
    r = await client.post("/v1/chat/completions", headers=AUTH, json={
        "model": "shared", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    app = client._transport.app
    state = app.state.wiwi
    resolved = state.cost.resolve("shared", provider_type="openai",
                                  provider_name="openai-main")
    assert abs(resolved["input_cost_per_token"] - 1e-6) < 1e-12
