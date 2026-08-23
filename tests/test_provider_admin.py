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


async def test_delete_provider_referenced_by_group(client):
    """Provider still used by a model group → 409 with group names in the message."""
    r = await client.delete("/admin/providers/p1", headers=H)
    assert r.status_code == 409
    assert "gpt-4o" in r.json()["error"]["message"]


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
            assert r.json() == {"deleted": True, "name": "orphan"}
            listing = (await c.get("/admin/providers", headers=H)).json()
            names = [p["name"] for p in listing["providers"]]
            assert "orphan" not in names
            assert "p1" in names


async def test_delete_provider_unknown(client):
    r = await client.delete("/admin/providers/nope", headers=H)
    assert r.status_code == 404


async def test_delete_provider_requires_admin(client):
    r = await client.delete("/admin/providers/p1")
    assert r.status_code == 401
