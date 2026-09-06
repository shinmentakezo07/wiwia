"""Provider export/import backup: full account + keys + deployments round-trip."""

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
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="test-key")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def test_export_requires_admin(client):
    assert (await client.get("/admin/providers/export")).status_code == 401
    assert (await client.post("/admin/providers/import",
                              json={"providers": []})).status_code == 401


async def test_export_includes_secrets_settings_and_deployments(client):
    r = await client.get("/admin/providers/export", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert len(body["providers"]) == 1
    p = body["providers"][0]
    assert p["name"] == "p1"
    assert p["provider_type"] == "openai"
    assert "base_url" in p and "timeout_s" in p
    assert "round_robin" in p and "alias_id" in p
    assert "extra_headers" in p
    assert p["keys"] == [{"label": "a", "secret": "test-key",
                          "weight": 1, "enabled": True}]
    assert {"group_name": "gpt-4o", "model_id": "gpt-4o",
            "weight": 1} in p["deployments"]


async def test_import_creates_provider_with_keys_and_deployments(client):
    payload = {"providers": [{
        "name": "p2", "provider_type": "openai-compatible",
        "base_url": "https://up.example.com/v1",
        "timeout_s": 45.0, "extra_headers": {"X-Team": "t"},
        "round_robin": False, "alias_id": "backup",
        "keys": [{"label": "k1", "secret": "sk-import-1",
                  "weight": 2, "enabled": True}],
        "deployments": [{"group_name": "my-group",
                         "model_id": "my-model", "weight": 2}],
    }]}
    r = await client.post("/admin/providers/import", headers=H, json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["imported_providers"] == 1
    assert data["imported_keys"] == 1
    assert data["imported_deployments"] == 1

    exported = (await client.get("/admin/providers/export",
                                 headers=H)).json()["providers"]
    p2 = next(p for p in exported if p["name"] == "p2")
    assert p2["base_url"] == "https://up.example.com/v1"
    assert p2["timeout_s"] == 45.0
    assert p2["extra_headers"] == {"X-Team": "t"}
    assert p2["round_robin"] is False
    assert p2["alias_id"] == "backup"
    assert p2["keys"][0]["secret"] == "sk-import-1"
    assert p2["keys"][0]["weight"] == 2
    assert {"group_name": "my-group", "model_id": "my-model",
            "weight": 2} in p2["deployments"]


async def test_import_upserts_existing_provider(client):
    r = await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p1", "provider_type": "openai",
            "keys": [{"label": "a", "secret": "rotated-secret",
                      "weight": 3, "enabled": False}],
            "deployments": [{"group_name": "gpt-4o",
                             "model_id": "gpt-4o", "weight": 5}],
        }]})
    assert r.status_code == 200, r.text
    exported = (await client.get("/admin/providers/export",
                                 headers=H)).json()["providers"]
    p1 = next(p for p in exported if p["name"] == "p1")
    assert p1["keys"] == [{"label": "a", "secret": "rotated-secret",
                           "weight": 3, "enabled": False}]
    assert {"group_name": "gpt-4o", "model_id": "gpt-4o",
            "weight": 5} in p1["deployments"]


async def test_import_rejects_bad_type_atomically(client):
    before = (await client.get("/admin/providers/export",
                               headers=H)).json()["providers"]
    r = await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p-bad", "provider_type": "bogus",
            "keys": [{"label": "a", "secret": "sk-x"}],
        }]})
    assert r.status_code == 400
    after = (await client.get("/admin/providers/export",
                              headers=H)).json()["providers"]
    assert [p["name"] for p in after] == [p["name"] for p in before]


async def test_export_import_roundtrip(client):
    exported = (await client.get("/admin/providers/export",
                                 headers=H)).json()
    r = await client.post("/admin/providers/import", headers=H,
                          json={"providers": exported["providers"]})
    assert r.status_code == 200, r.text
    again = (await client.get("/admin/providers/export",
                              headers=H)).json()
    assert again["providers"] == exported["providers"]


async def test_export_scopes_by_provider(client):
    await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p2", "provider_type": "openai",
            "keys": [{"label": "a", "secret": "sk-x"}]}]})
    scoped = (await client.get("/admin/providers/export?provider=p2",
                               headers=H)).json()["providers"]
    assert [p["name"] for p in scoped] == ["p2"]
    r = await client.get("/admin/providers/export?provider=nope", headers=H)
    assert r.status_code == 404


async def test_import_alias_conflict_with_outsider_rejected(client):
    await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p-alias", "provider_type": "openai",
            "alias_id": "shared-alias",
            "keys": [{"label": "a", "secret": "sk-x"}]}]})
    r = await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p-other", "provider_type": "openai",
            "alias_id": "shared-alias",
            "keys": [{"label": "a", "secret": "sk-y"}]}]})
    assert r.status_code == 409
    listing = (await client.get("/admin/providers", headers=H)).json()
    assert all(p["name"] != "p-other" for p in listing["providers"])


async def test_import_alias_moves_between_imported_providers(client):
    for pname in ("pa", "pb"):
        await client.post("/admin/providers/import", headers=H, json={
            "providers": [{
                "name": pname, "provider_type": "openai",
                **({"alias_id": "moving"} if pname == "pa" else {}),
                "keys": [{"label": "a", "secret": "sk-x"}]}]})
    r = await client.post("/admin/providers/import", headers=H, json={
        "providers": [
            {"name": "pa", "provider_type": "openai", "alias_id": None,
             "keys": [{"label": "a", "secret": "sk-x"}]},
            {"name": "pb", "provider_type": "openai",
             "alias_id": "moving",
             "keys": [{"label": "a", "secret": "sk-x"}]},
        ]})
    assert r.status_code == 200, r.text
    exported = (await client.get("/admin/providers/export",
                                 headers=H)).json()["providers"]
    by_name = {p["name"]: p for p in exported}
    assert by_name["pa"]["alias_id"] is None
    assert by_name["pb"]["alias_id"] == "moving"


async def test_import_rejects_duplicate_deployments_and_bad_enabled(client):
    r = await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p-dup", "provider_type": "openai",
            "keys": [{"label": "a", "secret": "sk-x"}],
            "deployments": [
                {"group_name": "g", "model_id": "m"},
                {"group_name": "g", "model_id": "m"},
            ]}]})
    assert r.status_code == 400
    r = await client.post("/admin/providers/import", headers=H, json={
        "providers": [{
            "name": "p-dup", "provider_type": "openai",
            "keys": [{"label": "a", "secret": "sk-x",
                      "enabled": "false"}]}]})
    assert r.status_code == 400
    listing = (await client.get("/admin/providers", headers=H)).json()
    assert all(p["name"] != "p-dup" for p in listing["providers"])
