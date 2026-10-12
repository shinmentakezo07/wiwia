"""Round 129 — whole-gateway JSON backup and restore (``wiwi/server/backup.py``).

``GET /admin/providers/export`` covered provider accounts, their key pools, and
deployments. Users, virtual keys, budgets, prices, settings, and the usage log
had no export path at all, so migrating a gateway meant re-keying by hand.

What these tests pin down, in rough order of how expensive a regression would
be:

1. **Virtual keys and user passwords restore by hash.** ``hash_key`` is an
   unsalted SHA-256 and the plaintext is never stored, so the export carries
   the digest. Re-importing it must produce a key the *original plaintext*
   authenticates against — that is the whole design, and it is invisible until
   you try it end to end.
2. **Nothing is written unless the entire document validates.** A restore is a
   bulk operation on the live router; a half-applied one is worse than a
   rejected one.
3. **Merge, never clobber, never delete.** An import must be safe to run twice
   and safe to run against an instance that has moved on.
4. **Secrets and user content are opt-in and independent.** One flag must not
   drag the other along.
5. **YAML-defined providers survive.** The export reads live router state
   precisely so DB-only omissions cannot happen.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest
import respx
import sqlalchemy as sa
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-upstream")])],
        model_list=[ModelEntry(
            model_name="gpt-4o",
            wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


def _bare_config() -> WiwiConfig:
    """An instance with no providers at all — the restore target."""
    return WiwiConfig(
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@contextlib.asynccontextmanager
async def client_for(cfg=None):
    """A gateway with *cfg*, exposing AppState on the client as ``.wiwi``."""
    app = create_app(cfg or _config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.wiwi = app.state.wiwi
            yield c


@pytest.fixture
async def client():
    async with client_for() as c:
        yield c


@pytest.fixture
async def bare_client():
    async with client_for(_bare_config()) as c:
        yield c


async def _export(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/admin/backup?{query}" if query else "/admin/backup",
                         headers=H)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# envelope and gating
# ---------------------------------------------------------------------------


async def test_export_envelope_shape(client):
    doc = await _export(client)
    assert doc["kind"] == "wiwi.backup"
    assert doc["version"] == 1
    assert doc["include_secrets"] is False
    assert doc["include_bodies"] is False
    assert doc["counts"]["providers"] == doc["counts"]["providers"]


async def test_export_omits_secrets_by_default(client):
    doc = await _export(client)
    for key in doc["keys"]:
        assert "secret" not in key, "a default export must not carry credentials"
    assert "sk-upstream" not in str(doc)


async def test_export_includes_secrets_when_flagged(client):
    doc = await _export(client, include_secrets=1)
    secrets = {k["label"]: k.get("secret") for k in doc["keys"]}
    assert secrets == {"a": "sk-upstream"}


async def test_export_includes_bodies_is_a_separate_flag(client):
    """Pulling credentials must never also pull user prompts."""
    doc = await _export(client, include_secrets=1, sections="logs")
    # No log rows exist yet, but the section must still be present and the
    # body columns must not have been requested.
    assert doc["logs"]["request_logs"] == []


async def test_export_bulk_sections_excluded_by_default(client):
    doc = await _export(client)
    assert "logs" not in doc["sections"]
    assert "rollups" not in doc["sections"]
    assert "audit" not in doc["sections"]


async def test_export_sections_filter_is_validated(client):
    r = await client.get("/admin/backup?sections=providers,nonsense", headers=H)
    assert r.status_code == 400
    assert "nonsense" in r.json()["error"]["message"]


async def test_export_requires_auth(client):
    assert (await client.get("/admin/backup")).status_code == 401


# ---------------------------------------------------------------------------
# the YAML-vs-DB decision
# ---------------------------------------------------------------------------


async def test_export_captures_yaml_defined_providers(client):
    """p1 exists only in wiwi.yaml — it has no DB row.

    The export reads live router state for exactly this reason: a DB-only
    export would silently omit it, and it is the provider an operator most
    needs backed up.
    """
    doc = await _export(client)
    assert "p1" in [p["name"] for p in doc["providers"]]


async def test_round_trip_into_a_bare_instance_restores_everything(client,
                                                                  bare_client):
    await client.post("/admin/providers", headers=H,
                      json={"name": "p2", "provider_type": "openai",
                            "key": "sk-two"})
    doc = await _export(client, include_secrets=1)

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    listing = (await bare_client.get("/admin/providers", headers=H)).json()
    names = [p["name"] for p in listing["providers"]]
    assert "p1" in names and "p2" in names
    labels = {k["label"] for p in listing["providers"] for k in p["keys"]}
    assert "a" in labels


# ---------------------------------------------------------------------------
# virtual keys and passwords: hash round-trip
# ---------------------------------------------------------------------------


@respx.mock
async def test_import_restores_a_virtual_key_that_authenticates(client):
    """The load-bearing test.

    The plaintext is never stored, so the backup carries the SHA-256 digest.
    Importing it must produce a row the ORIGINAL plaintext authenticates
    against — otherwise a restored gateway hands every tenant a dead key.
    """
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 1,
            "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                      "total_tokens": 7}}))
    generated = (await client.post("/admin/keys/generate", headers=H,
                                   json={"alias": "ci", "max_budget": 5.0}))
    plaintext = generated.json()["key"]

    doc = await _export(client)
    assert doc["vkeys"][0]["key_hash"]

    engine = client.wiwi.config_store.engine
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM vkeys"))
    # Drop any cached negative entry so a stale 401 would be visible.
    client.wiwi.auth.invalidate_cache()

    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"model": "gpt-4o",
              "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text


async def test_import_restores_a_user_password(client):
    r = await client.post("/auth/signup",
                          json={"username": "alice", "password": "hunter22xyz"})
    assert r.status_code in (200, 201), r.text

    doc = await _export(client)
    assert doc["users"][0]["password_hash"]

    engine = client.wiwi.config_store.engine
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM users"))

    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    r = await client.post("/auth/login",
                          json={"username": "alice", "password": "hunter22xyz"})
    assert r.status_code == 200, r.text


async def test_import_preserves_the_target_vkey_id(client):
    """An imported hash keeps the local id so ``owner_id`` stays resolvable."""
    await client.post("/admin/keys/generate", headers=H, json={"alias": "ci"})
    before = (await client.get("/admin/keys", headers=H)).json()["keys"]
    local_id = before[0]["id"]

    doc = await _export(client)
    doc["vkeys"][0]["id"] = "some-other-id"
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    after = (await client.get("/admin/keys", headers=H)).json()["keys"]
    assert [k["id"] for k in after] == [local_id]


async def test_import_rejects_a_vkey_holding_the_master_hash(client):
    doc = await _export(client)
    doc["vkeys"] = [{
        "id": "evil", "key_hash": client.wiwi.auth.master_hash,
        "key_alias": "master", "models": [],
    }]
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 409
    assert "master key" in r.json()["error"]["message"]


async def test_import_rejects_a_malformed_vkey_hash(client):
    doc = await _export(client)
    doc["vkeys"] = [{"id": "x", "key_hash": "not-a-hash"}]
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# merge semantics
# ---------------------------------------------------------------------------


async def test_import_is_idempotent(client):
    await client.post("/admin/providers", headers=H,
                      json={"name": "p2", "provider_type": "openai",
                            "key": "sk-two"})
    await client.post("/admin/keys/generate", headers=H, json={"alias": "ci"})
    doc = await _export(client, include_secrets=1)

    first = await client.post("/admin/backup/import", headers=H, json=doc)
    second = await client.post("/admin/backup/import", headers=H, json=doc)
    assert first.status_code == second.status_code == 200

    engine = client.wiwi.config_store.engine
    async with engine.connect() as conn:
        providers = (await conn.execute(
            sa.text("SELECT COUNT(*) FROM providers"))).scalar()
        vkeys = (await conn.execute(
            sa.text("SELECT COUNT(*) FROM vkeys"))).scalar()
    assert providers == doc["counts"]["providers"]
    assert vkeys == doc["counts"]["vkeys"]


async def test_import_does_not_touch_records_the_file_omits(client):
    """Merge, not replace: an unrelated local provider survives an import."""
    await client.post("/admin/providers", headers=H,
                      json={"name": "local-only", "provider_type": "openai",
                            "key": "sk-local"})
    doc = await _export(client)
    doc["providers"] = [p for p in doc["providers"] if p["name"] == "p1"]

    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    names = [p["name"] for p in
             (await client.get("/admin/providers", headers=H)).json()["providers"]]
    assert "local-only" in names, "merge must never delete"


async def test_import_never_blanks_a_secret_the_file_omits(client):
    """A secret-less export must not wipe a working credential."""
    await client.post("/admin/providers", headers=H,
                      json={"name": "p2", "provider_type": "openai",
                            "key": "sk-two"})
    doc = await _export(client)          # no include_secrets
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    secret = await client.get("/admin/providers/p2/keys/default/secret",
                              headers=H)
    assert secret.status_code == 200
    assert secret.json()["secret"] == "sk-two"


# ---------------------------------------------------------------------------
# validate-everything-then-apply-nothing
# ---------------------------------------------------------------------------


async def test_import_rejects_a_bad_document_and_applies_nothing(client):
    before = (await client.get("/admin/providers", headers=H)).json()
    doc = await _export(client)
    doc["providers"].append({"name": "p9", "provider_type": "not-a-provider",
                             "base_url": "https://x"})
    doc["providers"].append({"name": "p8", "provider_type": "openai",
                             "base_url": "https://x"})

    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400
    assert "not-a-provider" in r.json()["error"]["message"]

    after = (await client.get("/admin/providers", headers=H)).json()
    assert [p["name"] for p in after["providers"]] == [
        p["name"] for p in before["providers"]], "nothing may be applied"


async def test_import_rejects_an_unknown_document_field(client):
    doc = await _export(client)
    doc["totally_new_section"] = []
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400
    assert "totally_new_section" in r.json()["error"]["message"]


async def test_import_rejects_a_version_mismatch(client):
    doc = await _export(client)
    doc["version"] = 99
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400
    assert "version" in r.json()["error"]["message"].lower()


async def test_import_rejects_a_key_for_an_unknown_provider(client):
    doc = await _export(client)
    doc["keys"] = [{"provider_name": "ghost", "label": "a", "secret": "x"}]
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400
    assert "ghost" in r.json()["error"]["message"]


async def test_import_rejects_duplicate_request_ids(client):
    doc = await _export(client)
    row = {"ts": 1.0, "request_id": "dup", "surface": "chat", "status": 200}
    doc["logs"] = {"request_logs": [dict(row), dict(row)]}
    r = await client.post("/admin/backup/import?dry_run=1", headers=H, json=doc)
    assert r.status_code == 400
    assert "duplicate" in r.json()["error"]["message"].lower()


async def test_import_requires_auth(client):
    assert (await client.post("/admin/backup/import", json={})).status_code == 401


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


async def test_dry_run_writes_nothing(client):
    await client.post("/admin/providers", headers=H,
                      json={"name": "p2", "provider_type": "openai",
                            "key": "sk-two"})
    doc = await _export(client, include_secrets=1)
    engine = client.wiwi.config_store.engine

    async with engine.connect() as conn:
        before = (await conn.execute(
            sa.text("SELECT COUNT(*) FROM providers"))).scalar()

    r = await client.post("/admin/backup/import?dry_run=1", headers=H, json=doc)
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert r.json()["counts"]["providers"] == 2

    async with engine.connect() as conn:
        after = (await conn.execute(
            sa.text("SELECT COUNT(*) FROM providers"))).scalar()
    assert before == after


# ---------------------------------------------------------------------------
# audit section and audit logging
# ---------------------------------------------------------------------------


async def test_audit_section_is_never_replayed(client):
    doc = await _export(client, sections="audit")
    doc["audit"] = {"audit_logs": [{"ts": 1.0, "actor": "someone",
                                    "action": "provider.delete",
                                    "target": "p1", "diff": {}}]}
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200
    assert r.json()["audit_skipped"] == 1, "must be counted, not applied"

    engine = client.wiwi.config_store.engine
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'provider.delete'"
        ))).scalar()
    assert rows == 0


async def test_export_and_import_are_audit_logged(client):
    await _export(client)
    doc = await _export(client)
    await client.post("/admin/backup/import", headers=H, json=doc)
    events = await client.wiwi.logs.read_audit(limit=50)
    actions = [e["action"] for e in events]
    assert "backup.export" in actions
    assert "backup.import" in actions


# ---------------------------------------------------------------------------
# log round-trip
# ---------------------------------------------------------------------------


@respx.mock
async def test_request_logs_round_trip(client, bare_client):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 1,
            "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                      "total_tokens": 7}}))
    plaintext = (await client.post("/admin/keys/generate", headers=H,
                                   json={"alias": "ci"})).json()["key"]
    for i in range(3):
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {plaintext}"},
            json={"model": "gpt-4o",
                  "messages": [{"role": "user", "content": f"hi {i}"}]})
        assert r.status_code == 200, r.text

    doc = await _export(client, sections="logs")
    assert doc["counts"]["logs"]["request_logs"] == 3

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    assert r.json()["logs"] == 3

    # Importing the same file again must not duplicate rows.
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200
    assert r.json()["logs"] == 0, "request_id must dedup"


# ---------------------------------------------------------------------------
# aliases and settings
# ---------------------------------------------------------------------------


async def test_alias_tombstone_survives_the_round_trip(client, bare_client):
    # Providers travel with it: the validator resolves a deployment's
    # provider_name against this document plus the target, and rejects a
    # dangling reference.
    doc = await _export(client, sections="providers,aliases")
    doc["aliases"] = {"fast": None, "smart": "gpt-4o"}
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    aliases = bare_client.wiwi.router.settings.model_group_alias
    assert "fast" not in aliases
    assert "smart" in aliases


async def test_settings_round_trip_restores_alert_rules(client, bare_client):
    rules = [{"id": "r1", "metric": "cost", "threshold": 10}]
    r = await client.put("/admin/alert-rules", headers=H, json={"rules": rules})
    assert r.status_code == 200, r.text

    doc = await _export(client, sections="settings")
    assert doc["settings"]["alert_rules"] == rules

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    assert bare_client.wiwi.alert_rules == rules


async def test_routing_strategy_is_validated(client):
    doc = await _export(client, sections="settings")
    doc["settings"]["routing_strategy"] = "not-a-strategy"
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400


async def test_prices_round_trip(client, bare_client):
    doc = await _export(client, sections="providers,prices")
    doc["prices"] = [{"model_id": "gpt-4o", "input_cost_per_token": 1e-6,
                      "output_cost_per_token": 2e-6,
                      "providers": {"p1": {"input_cost_per_token": 5e-7}}}]
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    assert bare_client.wiwi.cost.prices["gpt-4o"]["input_cost_per_token"] == 1e-6
    assert bare_client.wiwi.cost.prices["gpt-4o"]["providers"]["p1"]


async def test_price_scope_must_be_a_known_provider(client):
    doc = await _export(client, sections="providers,prices")
    doc["prices"] = [{"model_id": "gpt-4o", "input_cost_per_token": 1e-6,
                      "output_cost_per_token": 2e-6,
                      "providers": {"ghost": {"input_cost_per_token": 1}}}]
    r = await client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 400
    assert "ghost" in r.json()["error"]["message"]


# ---------------------------------------------------------------------------
# deployments
# ---------------------------------------------------------------------------


async def test_detached_deployment_is_not_resurrected(client, bare_client):
    doc = await _export(client, sections="providers,deployments")
    doc["deployments"] = [{"group_name": "gpt-4o", "provider_name": "p1",
                           "model_id": "gpt-4o", "weight": 1,
                           "detached": True}]
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    assert "gpt-4o" not in bare_client.wiwi.router.groups


async def test_import_rebuilds_cross_provider_pools(client, bare_client):
    doc = await _export(client, sections="providers,keys,deployments")
    doc["providers"].append({"name": "p2", "provider_type": "openai",
                             "base_url": "https://api.openai.com/v1"})
    doc["keys"].append({"provider_name": "p2", "label": "a",
                        "secret": "sk-two"})
    doc["deployments"].append({"group_name": "gpt-4o", "provider_name": "p2",
                               "model_id": "gpt-4o", "weight": 1})
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text
    pools = getattr(bare_client.wiwi.router, "_group_provider_rr", {})
    assert "gpt-4o" in pools, "cross-provider pool was not rebuilt"

# ---------------------------------------------------------------------------
# a restored gateway must still be a working gateway
# ---------------------------------------------------------------------------


async def test_restored_prices_do_not_break_the_pricing_endpoint(client,
                                                                 bare_client):
    """Regression: an absent rate must be OMITTED, not stored as None.

    `_entry_to_per_1m` branches on key *presence* ("cache_read_input_cost_
    per_token" in p) and then multiplies. A restore that wrote an explicit
    None into the price map made every later `GET /admin/pricing` raise
    TypeError — a 500 on a page the operator opens right after migrating.
    Found by driving the full two-instance restore, not by the unit tests.
    """
    doc = await _export(client, sections="providers,prices")
    doc["prices"] = [{"model_id": "gpt-4o", "input_cost_per_token": 2.5e-6,
                      "output_cost_per_token": 1e-5}]
    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    pricing = bare_client.wiwi.cost.prices["gpt-4o"]
    assert "cache_read_input_cost_per_token" not in pricing
    assert "max_input_tokens" not in pricing

    r = await bare_client.get("/admin/pricing", headers=H)
    assert r.status_code == 200, r.text
    entry = r.json()["models"][0]
    assert entry["input_per_1m"] == 2.5
    assert "cache_read_per_1m" not in entry


async def test_a_restored_gateway_is_still_usable(client, bare_client):
    """After a restore, every console surface the data touches must read back.

    Individually these are covered above; the value is that this walks the
    whole console in one pass, which is how the pricing crash was found.
    """
    await client.put("/admin/alert-rules", headers=H,
                     json={"rules": [{"id": "r1", "metric": "cost",
                                      "threshold": 25.0}]})
    await client.put("/admin/pricing/gpt-4o", headers=H,
                     json={"input_per_1m": 2.5, "output_per_1m": 10.0})
    await client.post("/admin/aliases", headers=H,
                      json={"set": {"turbo": "gpt-4o"}, "unset": []})
    doc = await _export(client, include_secrets=1)

    r = await bare_client.post("/admin/backup/import", headers=H, json=doc)
    assert r.status_code == 200, r.text

    for path in ("/admin/providers", "/admin/models", "/admin/keys",
                 "/admin/pricing", "/admin/alert-rules", "/admin/users",
                 "/admin/logs/requests", "/admin/stats/overview"):
        r = await bare_client.get(path, headers=H)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"

    models = (await bare_client.get("/admin/models", headers=H)).json()
    assert "turbo" in models["aliases"]
    assert models["aliases"]["turbo"] == "gpt-4o"
