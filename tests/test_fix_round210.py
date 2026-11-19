"""Round 210 — provider-level enable/disable.

An operator must be able to take a whole provider account out of rotation
without deleting it or its keys. The switch is an account-level gate:

1. ``ProviderAccount.healthy`` is false while disabled and ``pick_key``
   refuses it, so every deployment backed by the account reports unavailable.
2. ``PATCH /admin/providers/<name> {"enabled": false}`` persists the flag; a
   restart over the same database keeps it disabled.
3. Provider export/import round-trips the flag.
"""

from __future__ import annotations

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
from wiwi.router.router import Router
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config(database_url: str = "sqlite+aiosqlite:///:memory:") -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        enabled=False,
                        keys=[KeyDef(label="a", key="test-key")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url=database_url),
    )


def test_disabled_account_is_unavailable_and_never_picks():
    router = Router(_config())

    async def run():
        acct = router.providers["p1"]
        assert acct.enabled is False
        assert acct.healthy is False
        assert await acct.pick_key() == (None, 0.0)
        assert router.groups["gpt-4o"][0].available is False

    import asyncio
    asyncio.run(run())


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_provider_list_exposes_enabled(client):
    listing = (await client.get("/admin/providers", headers=H)).json()
    assert listing["providers"][0]["enabled"] is False


async def test_patch_provider_enabled_toggles_and_validates(client):
    r = await client.patch("/admin/providers/p1", json={"enabled": True}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    listing = (await client.get("/admin/providers", headers=H)).json()
    assert listing["providers"][0]["enabled"] is True

    r = await client.patch("/admin/providers/p1", json={"enabled": False}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert r.json()["healthy"] is False

    bad = await client.patch("/admin/providers/p1",
                             json={"enabled": "false"}, headers=H)
    assert bad.status_code == 400


async def test_disable_survives_restart(tmp_path):
    db = tmp_path / "enabled.db"

    def boot() -> tuple:
        app = create_app(_config(f"sqlite+aiosqlite:///{db}"))
        return app, LifespanManager(app)

    app1, lm1 = boot()
    async with lm1:
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # Config starts disabled; enable it, then disable it again through
            # the API so the persisted row is what the restart must honour.
            r = await c.patch("/admin/providers/p1", json={"enabled": True}, headers=H)
            assert r.status_code == 200, r.text
            r = await c.patch("/admin/providers/p1", json={"enabled": False}, headers=H)
            assert r.status_code == 200, r.text

    app2, lm2 = boot()
    async with lm2:
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            listing = (await c.get("/admin/providers", headers=H)).json()
            assert listing["providers"][0]["enabled"] is False
            models = (await c.get("/admin/models", headers=H)).json()
            deployments = models["groups"][0]["deployments"]
            assert deployments[0]["available"] is False


async def test_provider_export_import_round_trips_enabled(client):
    doc = (await client.get("/admin/providers/export", headers=H)).json()
    assert doc["providers"][0]["enabled"] is False

    r = await client.patch("/admin/providers/p1", json={"enabled": True}, headers=H)
    assert r.status_code == 200, r.text

    r = await client.post("/admin/providers/import", json=doc, headers=H)
    assert r.status_code == 200, r.text
    listing = (await client.get("/admin/providers", headers=H)).json()
    assert listing["providers"][0]["enabled"] is False
