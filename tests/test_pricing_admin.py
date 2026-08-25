"""Admin pricing CRUD: add, edit, delete model pricing via /admin/pricing."""

from __future__ import annotations

import httpx
import pytest
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

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


# -- auth guards ---------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/pricing"),
    ("PUT", "/admin/pricing/some-model"),
    ("DELETE", "/admin/pricing/some-model"),
])
async def test_pricing_endpoints_require_master(client, method, path):
    r = await client.request(method, path, json={"input_per_1m": 1})
    assert r.status_code == 401
    r = await client.request(method, path, json={"input_per_1m": 1},
                             headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# -- GET -----------------------------------------------------------------------

async def test_pricing_list_starts_empty(client):
    r = await client.get("/admin/pricing", headers=AUTH)
    assert r.status_code == 200
    models = {m["model_id"]: m for m in r.json()["models"]}
    # No built-in prices ship — the table only lists user-added entries.
    assert "gpt-4o" not in models
    assert models == {}


# -- PUT (create + update) -----------------------------------------------------

async def test_pricing_put_creates_new_model(client):
    r = await client.put("/admin/pricing/my-custom-model", headers=AUTH, json={
        "input_per_1m": 2.5,
        "output_per_1m": 10.0,
        "cache_read_per_1m": 0.3,
        "max_input_tokens": 200000,
        "max_output_tokens": 8192,
        "mode": "chat",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == "my-custom-model"
    assert body["input_per_1m"] == 2.5
    assert body["output_per_1m"] == 10.0
    assert body["cache_read_per_1m"] == 0.3
    assert body["max_input_tokens"] == 200000
    assert body["max_output_tokens"] == 8192
    assert body["mode"] == "chat"

    # The new model now appears in the listing.
    models = {m["model_id"]: m for m in
              (await client.get("/admin/pricing", headers=AUTH)).json()["models"]}
    assert "my-custom-model" in models
    assert models["my-custom-model"]["input_per_1m"] == 2.5


async def test_pricing_put_slash_model_id(client):
    """Model ids that contain a slash (e.g. MiniMaxAI/MiniMax-M3) must be
    routable. The path converter must accept the slash, not 404."""
    r = await client.put("/admin/pricing/MiniMaxAI/MiniMax-M3", headers=AUTH, json={
        "input_per_1m": 0.23,
        "output_per_1m": 0.96,
        "mode": "chat",
    })
    assert r.status_code == 200, r.text
    assert r.json()["model_id"] == "MiniMaxAI/MiniMax-M3"
    # Confirm it lands in the listing under the exact slash-bearing id.
    models = {m["model_id"]: m for m in
              (await client.get("/admin/pricing", headers=AUTH)).json()["models"]}
    assert "MiniMaxAI/MiniMax-M3" in models
    assert models["MiniMaxAI/MiniMax-M3"]["input_per_1m"] == 0.23


async def test_pricing_put_updates_existing(client):
    # Create.
    await client.put("/admin/pricing/edit-me", headers=AUTH, json={
        "input_per_1m": 1.0, "output_per_1m": 2.0,
    })
    # Update with new values.
    r = await client.put("/admin/pricing/edit-me", headers=AUTH, json={
        "input_per_1m": 5.0,
        "output_per_1m": 20.0,
        "cache_read_per_1m": 0.5,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["input_per_1m"] == 5.0
    assert body["output_per_1m"] == 20.0
    assert body["cache_read_per_1m"] == 0.5


async def test_pricing_put_validates_required_fields(client):
    # Missing output_per_1m.
    r = await client.put("/admin/pricing/bad-model", headers=AUTH, json={
        "input_per_1m": 1.0,
    })
    assert r.status_code == 400
    # Non-numeric input.
    r = await client.put("/admin/pricing/bad-model", headers=AUTH, json={
        "input_per_1m": "free", "output_per_1m": 2.0,
    })
    assert r.status_code == 400


# -- DELETE --------------------------------------------------------------------

async def test_pricing_delete_removes_model(client):
    # Create then delete.
    await client.put("/admin/pricing/delete-me", headers=AUTH, json={
        "input_per_1m": 1.0, "output_per_1m": 2.0,
    })
    r = await client.delete("/admin/pricing/delete-me", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # Confirm it's gone from the listing.
    models = {m["model_id"]: m for m in
              (await client.get("/admin/pricing", headers=AUTH)).json()["models"]}
    assert "delete-me" not in models


async def test_pricing_delete_unknown_returns_deleted_false(client):
    r = await client.delete("/admin/pricing/never-existed", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["deleted"] is False


async def test_pricing_delete_slash_model_id(client):
    """DELETE must also accept a slash-bearing model id."""
    await client.put("/admin/pricing/zai/glm-5.2", headers=AUTH, json={
        "input_per_1m": 1.4, "output_per_1m": 4.4,
    })
    r = await client.delete("/admin/pricing/zai/glm-5.2", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    models = {m["model_id"]: m for m in
              (await client.get("/admin/pricing", headers=AUTH)).json()["models"]}
    assert "zai/glm-5.2" not in models


# -- cost engine integration ---------------------------------------------------

async def test_pricing_put_reflects_in_cost_engine(client):
    """A price set via the admin API must be used by the cost engine."""
    app = client._transport.app  # ASGITransport holds the app
    state = app.state.wiwi
    await client.put("/admin/pricing/cost-check", headers=AUTH, json={
        "input_per_1m": 3.0,
        "output_per_1m": 15.0,
    })
    # The cost engine stores per-token rates; 3.0/1M = 0.000003 per token.
    entry = state.cost.prices["cost-check"]
    assert abs(entry["input_cost_per_token"] - 0.000003) < 1e-12
    assert abs(entry["output_cost_per_token"] - 0.000015) < 1e-12
    # cost() should use the new entry.
    c = state.cost.cost("cost-check", prompt_tokens=1_000_000, completion_tokens=0)
    assert abs(c - 3.0) < 1e-6


async def test_pricing_delete_removes_from_cost_engine(client):
    app = client._transport.app
    state = app.state.wiwi
    await client.put("/admin/pricing/cost-del", headers=AUTH, json={
        "input_per_1m": 1.0, "output_per_1m": 1.0,
    })
    assert "cost-del" in state.cost.prices
    await client.delete("/admin/pricing/cost-del", headers=AUTH)
    assert "cost-del" not in state.cost.prices
    assert state.cost.cost("cost-del", 1000, 1000) == 0.0
