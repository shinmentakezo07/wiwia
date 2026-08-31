"""Tests for DB persistence of admin-added providers, keys, and deployments.

The core scenario: add a provider/key/deployment via the admin API, then
simulate a restart by creating a fresh app backed by the same DB, and
verify everything is still there.
"""

import httpx
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


def _config(db_url: str = "sqlite+aiosqlite:///:memory:") -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="test-key")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url=db_url),
    )


def _file_db_path(tmp_path) -> str:
    db = tmp_path / "test_persistence.db"
    return f"sqlite+aiosqlite:///{db}"


async def _create_client(config: WiwiConfig):
    app = create_app(config)
    lm = LifespanManager(app)
    await lm.__aenter__()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return app, lm, client


async def _shutdown(lm, client):
    await client.aclose()
    await lm.__aexit__(None, None, None)


# -- providers persist across restart --------------------------------------------

async def test_admin_added_provider_survives_restart(tmp_path):
    """Provider added via admin API persists to DB and reloads on restart."""
    db_url = _file_db_path(tmp_path)

    # session 1: add a provider
    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    r = await c.post("/admin/providers", json={
        "name": "p2", "provider_type": "anthropic",
        "label": "default", "key": "sk-ant-test"}, headers=H)
    assert r.status_code == 200, r.text
    await _shutdown(lm, c)

    # session 2: fresh app, same DB — provider must still be there
    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    listing = (await c2.get("/admin/providers", headers=H)).json()
    names = [p["name"] for p in listing["providers"]]
    assert "p2" in names, f"admin-added provider lost on restart: {names}"
    # the original YAML provider should also still be there
    assert "p1" in names
    # p2 should have its key
    p2 = next(p for p in listing["providers"] if p["name"] == "p2")
    assert len(p2["keys"]) == 1
    assert p2["keys"][0]["label"] == "default"
    assert p2["provider_type"] == "anthropic"
    await _shutdown(lm2, c2)


# -- provider keys persist across restart ----------------------------------------

async def test_admin_added_key_survives_restart(tmp_path):
    """Key added to a DB-sourced provider persists across restart."""
    db_url = _file_db_path(tmp_path)

    # session 1: add provider + extra key
    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    await c.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "k1", "key": "sk-1"}, headers=H)
    r = await c.post("/admin/providers/p2/keys", json={
        "label": "k2", "key": "sk-2", "weight": 3}, headers=H)
    assert r.status_code == 200, r.text
    await _shutdown(lm, c)

    # session 2: both keys should be present
    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    listing = (await c2.get("/admin/providers", headers=H)).json()
    p2 = next(p for p in listing["providers"] if p["name"] == "p2")
    labels = [k["label"] for k in p2["keys"]]
    assert "k1" in labels and "k2" in labels
    k2 = next(k for k in p2["keys"] if k["label"] == "k2")
    assert k2["weight"] == 3
    await _shutdown(lm2, c2)


# -- deleted keys stay deleted ---------------------------------------------------

async def test_deleted_key_stays_deleted(tmp_path):
    """Key removed via admin API does not reappear from DB on restart."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    await c.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "k1", "key": "sk-1"}, headers=H)
    await c.post("/admin/providers/p2/keys", json={
        "label": "k2", "key": "sk-2"}, headers=H)
    # delete k2
    r = await c.delete("/admin/providers/p2/keys/k2", headers=H)
    assert r.status_code == 200
    await _shutdown(lm, c)

    # restart: k2 should NOT come back
    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    listing = (await c2.get("/admin/providers", headers=H)).json()
    p2 = next(p for p in listing["providers"] if p["name"] == "p2")
    labels = [k["label"] for k in p2["keys"]]
    assert "k2" not in labels
    assert "k1" in labels
    await _shutdown(lm2, c2)


# -- deployments persist across restart ------------------------------------------

async def test_admin_added_deployment_survives_restart(tmp_path):
    """Deployment added via admin API persists to DB and reloads on restart."""
    db_url = _file_db_path(tmp_path)

    # session 1: add a provider + a deployment to a new model group
    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    await c.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "default", "key": "sk-test"}, headers=H)
    r = await c.post("/admin/model-groups/claude-3.5-sonnet/deployments", json={
        "provider": "p2", "model_id": "claude-3-5-sonnet-20241022",
        "weight": 2}, headers=H)
    assert r.status_code == 201, r.text
    await _shutdown(lm, c)

    # session 2: deployment should still be there
    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    models = (await c2.get("/admin/models", headers=H)).json()
    groups = {g["name"]: g for g in models["groups"]}
    assert "claude-3.5-sonnet" in groups
    deps = groups["claude-3.5-sonnet"]["deployments"]
    assert len(deps) == 1
    assert deps[0]["provider"] == "p2"
    assert deps[0]["model_id"] == "claude-3-5-sonnet-20241022"
    assert deps[0]["weight"] == 2
    await _shutdown(lm2, c2)


# -- alert rules persist ---------------------------------------------------------

async def test_alert_rules_survive_restart(tmp_path):
    """Alert rules set via admin API persist to DB and reload on restart."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    rules = [{"name": "high_error_rate", "threshold": 0.05}]
    r = await c.put("/admin/alert-rules", json={"rules": rules}, headers=H)
    assert r.status_code == 200
    await _shutdown(lm, c)

    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    r = (await c2.get("/admin/alert-rules", headers=H)).json()
    assert r["rules"] == rules
    await _shutdown(lm2, c2)


# -- routing strategy persists ---------------------------------------------------

async def test_routing_strategy_survives_restart(tmp_path):
    """Routing strategy changed via admin API persists to DB and reloads."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    r = await c.patch("/admin/model-groups/gpt-4o", json={"strategy": "latency-based"},
                      headers=H)
    assert r.status_code == 200, r.text
    await _shutdown(lm, c)

    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    models = (await c2.get("/admin/models", headers=H)).json()
    assert models["strategy"] == "latency-based"
    await _shutdown(lm2, c2)


# -- YAML entries are not duplicated on restart ----------------------------------

async def test_yaml_provider_not_duplicated(tmp_path):
    """YAML-sourced provider should not be duplicated in DB after restart."""
    db_url = _file_db_path(tmp_path)

    # session 1: just start the app (no admin additions)
    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    listing = (await c.get("/admin/providers", headers=H)).json()
    assert len(listing["providers"]) == 1  # only p1 from YAML
    await _shutdown(lm, c)

    # session 2: restart — p1 should still be exactly one entry
    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    listing2 = (await c2.get("/admin/providers", headers=H)).json()
    assert len(listing2["providers"]) == 1
    assert listing2["providers"][0]["name"] == "p1"
    await _shutdown(lm2, c2)


# -- provider rename persists ----------------------------------------------------

async def test_provider_rename_survives_restart(tmp_path):
    """Provider renamed via admin API keeps the new name after restart."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    # add a provider, then rename it
    await c.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "default", "key": "sk-test"}, headers=H)
    r = await c.patch("/admin/providers/p2", json={"name": "p2-renamed"}, headers=H)
    assert r.status_code == 200
    await _shutdown(lm, c)

    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    listing = (await c2.get("/admin/providers", headers=H)).json()
    names = [p["name"] for p in listing["providers"]]
    assert "p2-renamed" in names
    assert "p2" not in names
    await _shutdown(lm2, c2)


# -- detached deployments stay detached -----------------------------------------

async def test_deleted_deployment_stays_deleted(tmp_path):
    """Detach must persist: a removed deployment must not reappear after restart."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    for mid in ("a", "b"):
        r = await c.post("/admin/model-groups/grp/deployments",
                         json={"provider": "p1", "model_id": mid}, headers=H)
        assert r.status_code == 201, r.text
    r = await c.delete("/admin/model-groups/grp/deployments"
                       "?provider=p1&model_id=a", headers=H)
    assert r.status_code == 200, r.text
    await _shutdown(lm, c)

    # session 2: only "b" should come back
    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    groups = (await c2.get("/admin/models", headers=H)).json()["groups"]
    grp = next((g for g in groups if g["name"] == "grp"), None)
    assert grp is not None, f"group lost after restart: {[g['name'] for g in groups]}"
    assert [d["model_id"] for d in grp["deployments"]] == ["b"]
    await _shutdown(lm2, c2)


async def test_deleted_deployment_emptied_group_stays_gone(tmp_path):
    """Detaching the only deployment of a group keeps the group away on restart."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    r = await c.post("/admin/model-groups/grp/deployments",
                     json={"provider": "p1", "model_id": "solo"}, headers=H)
    assert r.status_code == 201, r.text
    r = await c.delete("/admin/model-groups/grp/deployments"
                       "?provider=p1&model_id=solo", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["group_emptied"] is True
    await _shutdown(lm, c)

    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    groups = (await c2.get("/admin/models", headers=H)).json()["groups"]
    assert "grp" not in [g["name"] for g in groups]
    await _shutdown(lm2, c2)


# -- key weight update persists --------------------------------------------------

async def test_key_weight_update_survives_restart(tmp_path):
    """Key weight changed via admin API persists across restart."""
    db_url = _file_db_path(tmp_path)

    cfg = _config(db_url)
    _, lm, c = await _create_client(cfg)
    await c.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "k1", "key": "sk-1"}, headers=H)
    await c.post("/admin/providers/p2/keys", json={
        "label": "k2", "key": "sk-2", "weight": 1}, headers=H)
    r = await c.patch("/admin/providers/p2/keys/k2", json={"weight": 5}, headers=H)
    assert r.status_code == 200
    await _shutdown(lm, c)

    cfg2 = _config(db_url)
    _, lm2, c2 = await _create_client(cfg2)
    listing = (await c2.get("/admin/providers", headers=H)).json()
    p2 = next(p for p in listing["providers"] if p["name"] == "p2")
    k2 = next(k for k in p2["keys"] if k["label"] == "k2")
    assert k2["weight"] == 5
    await _shutdown(lm2, c2)
