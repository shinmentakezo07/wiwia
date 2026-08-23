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
