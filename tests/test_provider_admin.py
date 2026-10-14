"""Admin provider management: delete-key, patch-provider, delete-provider endpoints."""

import httpx
import pytest
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


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="test-key"),
                              KeyDef(label="b", key="test-key-2")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


H = {"Authorization": "Bearer sk-wiwi-master-test"}


async def test_delete_provider_key_happy(client):
    r = await client.delete("/admin/providers/p1/keys/a", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "label": "a"}
    # key gone from listing
    listing = (await client.get("/admin/providers", headers=H)).json()
    labels = [k["label"] for k in listing["providers"][0]["keys"]]
    assert "a" not in labels
    assert "b" in labels


async def test_delete_provider_key_unknown_provider(client):
    r = await client.delete("/admin/providers/nope/keys/a", headers=H)
    assert r.status_code == 404


async def test_delete_provider_key_unknown_label(client):
    r = await client.delete("/admin/providers/p1/keys/nope", headers=H)
    assert r.status_code == 404


async def test_delete_provider_key_requires_admin(client):
    r = await client.delete("/admin/providers/p1/keys/a")
    assert r.status_code == 401


async def test_delete_provider_cascades_group_deployments(client):
    """A provider feeding a model group is deleted, and the group loses it."""
    r = await client.delete("/admin/providers/p1", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["detached_deployments"] == [{"group": "gpt-4o", "model_id": "gpt-4o"}]
    # group dropped entirely (it held only that deployment), provider gone
    models = (await client.get("/admin/models", headers=H)).json()
    assert [g["name"] for g in models["groups"]] == []
    listing = (await client.get("/admin/providers", headers=H)).json()
    assert [p["name"] for p in listing["providers"]] == []

async def test_delete_provider_cascade_keeps_sibling_deployments(client):
    """Deleting one of two providers in a group keeps the other one routable."""
    await client.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "a", "key": "sk-x"}, headers=H)
    r = await client.post("/admin/model-groups/combo/deployments", json={
        "group": "combo", "provider": "p2", "model_id": "gpt-4o-mini"},
        headers=H)
    assert r.status_code == 201, r.text
    d = await client.delete("/admin/providers/p1", headers=H)
    assert d.status_code == 200, d.text
    assert d.json()["detached_deployments"] == [
        {"group": "gpt-4o", "model_id": "gpt-4o"}]
    models = (await client.get("/admin/models", headers=H)).json()
    by_name = {g["name"]: g["deployments"] for g in models["groups"]}
    assert "gpt-4o" not in by_name            # emptied group was dropped
    assert [x["provider"] for x in by_name["combo"]] == ["p2"]

async def test_delete_provider_cascade_partial_group(client):
    """A group holding two providers loses only the deleted one's entry."""
    await client.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "a", "key": "sk-x"}, headers=H)
    r = await client.post("/admin/model-groups/gpt-4o/deployments", json={
        "group": "gpt-4o", "provider": "p2", "model_id": "gpt-4o-mini"},
        headers=H)
    assert r.status_code == 201, r.text
    d = await client.delete("/admin/providers/p1", headers=H)
    assert d.status_code == 200, d.text
    models = (await client.get("/admin/models", headers=H)).json()
    by_name = {g["name"]: g for g in models["groups"]}
    assert [x["provider"] for x in by_name["gpt-4o"]["deployments"]] == ["p2"]


async def test_delete_provider_happy(client):
    """Delete a provider that no group references."""
    # build a fresh config with an unreferenced provider.
    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai", keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="orphan", provider="openai", keys=[KeyDef(label="a", key="k2")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete("/admin/providers/orphan", headers=H)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": True, "name": "orphan",
                                "detached_deployments": []}
            listing = (await c.get("/admin/providers", headers=H)).json()
            names = [p["name"] for p in listing["providers"]]
            assert "orphan" not in names
            assert "p1" in names


async def test_delete_provider_cascade_survives_restart(tmp_path):
    """A cascaded YAML-sourced deployment must not come back on restart.

    The deployment comes from the YAML ``model_list``, so it has no DB row:
    without the ``detached=1`` tombstone the cascade writes, the next startup
    rebuilds it from YAML and it fails routing against a provider that no
    longer exists (AUDIT #297, same class as a manual deployment detach).
    """
    import httpx
    from asgi_lifespan import LifespanManager

    db = tmp_path / "cascade.db"

    def _boot() -> tuple:
        cfg = WiwiConfig(
            providers=[ProviderDef(name="p1", provider="openai",
                                   keys=[KeyDef(label="a", key="k1")])],
            model_list=[ModelEntry(model_name="gpt-4o",
                                   wiwi_params=DeploymentParams(
                                       provider="p1", model="gpt-4o"))],
            general_settings=GeneralSettings(
                master_key="sk-wiwi-master-test",
                database_url=f"sqlite+aiosqlite:///{db}"),
        )
        app = create_app(cfg)
        lm = LifespanManager(app)
        return app, lm

    app1, lm1 = _boot()
    async with lm1:
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete("/admin/providers/p1", headers=H)
            assert r.status_code == 200, r.text

    # Simulate a restart: brand-new app over the same DB.
    app2, lm2 = _boot()
    async with lm2:
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            models = (await c.get("/admin/models", headers=H)).json()
            assert [g["name"] for g in models["groups"]] == [], (
                "cascaded YAML deployment returned after restart: "
                f"{models['groups']}")

async def test_delete_provider_unknown(client):
    r = await client.delete("/admin/providers/nope", headers=H)
    assert r.status_code == 404


async def test_delete_provider_requires_admin(client):
    r = await client.delete("/admin/providers/p1")
    assert r.status_code == 401


async def test_patch_provider_rename(client):
    r = await client.patch("/admin/providers/p1", json={"name": "p1-renamed"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "p1-renamed"
    listing = (await client.get("/admin/providers", headers=H)).json()
    names = [p["name"] for p in listing["providers"]]
    assert "p1-renamed" in names
    assert "p1" not in names


async def test_patch_provider_rename_collision(client):
    # add a second provider first
    await client.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "a", "key": "sk-x"}, headers=H)
    r = await client.patch("/admin/providers/p1", json={"name": "p2"}, headers=H)
    assert r.status_code == 409


async def test_patch_provider_change_base_url(client):
    r = await client.patch("/admin/providers/p1",
                           json={"base_url": "https://custom.example.com/v1"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["base_url"] == "https://custom.example.com/v1"


async def test_patch_provider_change_type(client):
    r = await client.patch("/admin/providers/p1", json={"provider_type": "anthropic"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["provider_type"] == "anthropic"


async def test_patch_provider_bad_type(client):
    r = await client.patch("/admin/providers/p1", json={"provider_type": "bogus"}, headers=H)
    assert r.status_code == 400


async def test_patch_provider_unknown(client):
    r = await client.patch("/admin/providers/nope", json={"name": "x"}, headers=H)
    assert r.status_code == 404


async def test_reveal_provider_key_happy(client):
    """GET the plaintext secret for a provider key (admin-only reveal)."""
    r = await client.get("/admin/providers/p1/keys/a/secret", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"label": "a", "secret": "test-key"}


async def test_reveal_provider_key_unknown_provider(client):
    r = await client.get("/admin/providers/nope/keys/a/secret", headers=H)
    assert r.status_code == 404


async def test_reveal_provider_key_unknown_label(client):
    r = await client.get("/admin/providers/p1/keys/nope/secret", headers=H)
    assert r.status_code == 404


async def test_reveal_provider_key_requires_admin(client):
    r = await client.get("/admin/providers/p1/keys/a/secret")
    assert r.status_code == 401


async def test_listing_still_masks_long_secret(client):
    """The pool listing must keep masking even with a reveal endpoint present."""
    r = await client.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "long", "key": "sk-abcdefghijklmnop-1234"}, headers=H)
    assert r.status_code == 200, r.text
    listing = (await client.get("/admin/providers", headers=H)).json()
    p2 = next(p for p in listing["providers"] if p["name"] == "p2")
    k = next(k for k in p2["keys"] if k["label"] == "long")
    assert k["masked"] != "sk-abcdefghijklmnop-1234"
    assert "secret" not in k


async def test_patch_provider_requires_admin(client):
    r = await client.patch("/admin/providers/p1", json={"name": "x"})
    assert r.status_code == 401
