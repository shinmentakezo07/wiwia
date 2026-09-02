"""Regression tests for admin-manageable model_group_alias (round 18).

Covers the new POST /admin/aliases endpoint, its persistence in the settings
table as a per-key overlay with tombstones, validation of chains (cycle,
depth, shadow, self-loop, provider-alias collision), and auth.
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.server.app import AppState, create_app


def _config(*, yaml_aliases: dict[str, str] | None = None,
            provider_aliases: dict[str, str] | None = None) -> WiwiConfig:
    providers = [
        ProviderDef(name="p1", provider="openai",
                    keys=[KeyDef(label="a", key="k1")]),
    ]
    if provider_aliases:
        for pname, aid in provider_aliases.items():
            providers.append(ProviderDef(name=pname, provider="openai",
                                          keys=[KeyDef(label="a", key="k1")],
                                          alias_id=aid))
    return WiwiConfig(
        providers=providers,
        model_list=[
            ModelEntry(model_name="group-a",
                       wiwi_params=DeploymentParams(provider="p1", model="m-a")),
            ModelEntry(model_name="group-b",
                       wiwi_params=DeploymentParams(provider="p1", model="m-b")),
            ModelEntry(model_name="group-c",
                       wiwi_params=DeploymentParams(provider="p1", model="m-c")),
            ModelEntry(model_name="group-d",
                       wiwi_params=DeploymentParams(provider="p1", model="m-d")),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(model_group_alias=yaml_aliases or {}),
    )


@pytest_asyncio.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


H = {"Authorization": "Bearer sk-wiwi-master-test"}


async def test_add_alias_happy(client):
    """Adding an alias takes effect live and shows up in /admin/models."""
    c, _app = client
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"fast": "group-a"}, "unset": []})
    assert r.status_code == 200, r.text
    assert r.json()["aliases"] == {"fast": "group-a"}
    listing = (await c.get("/admin/models", headers=H)).json()
    assert listing["aliases"] == {"fast": "group-a"}


async def test_set_overrides_yaml_per_key(client):
    """DB overrides only the named key; other YAML aliases stay visible."""
    c, _app = client
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"x": "group-b"}, "unset": []})
    assert r.status_code == 200, r.text
    listing = (await c.get("/admin/models", headers=H)).json()
    assert listing["aliases"] == {"x": "group-b"}


async def test_persistence_across_restart(tmp_path):
    """Setting an alias and restarting against the same DB keeps it; YAML-only
    aliases still load."""
    db = tmp_path / "wiwi.db"
    # first lifecycle: set an alias
    cfg1 = _config(yaml_aliases={"yaml-only": "group-a"})
    cfg1.general_settings.database_url = f"sqlite+aiosqlite:///{db}"
    app1 = create_app(cfg1)
    async with LifespanManager(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/admin/aliases", headers=H,
                             json={"set": {"runtime": "group-b"}, "unset": []})
            assert r.status_code == 200, r.text
    # second lifecycle: same DB path, fresh AppState
    cfg2 = _config(yaml_aliases={"yaml-only": "group-a"})
    cfg2.general_settings.database_url = f"sqlite+aiosqlite:///{db}"
    app2 = create_app(cfg2)
    async with LifespanManager(app2):
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            listing = (await c.get("/admin/models", headers=H)).json()
            assert listing["aliases"] == {"yaml-only": "group-a", "runtime": "group-b"}


async def test_unset_yaml_alias_tombstone(tmp_path):
    """Unsetting a YAML-defined alias removes it live AND after restart."""
    db = tmp_path / "wiwi.db"
    cfg1 = _config(yaml_aliases={"old": "group-a"})
    cfg1.general_settings.database_url = f"sqlite+aiosqlite:///{db}"
    app1 = create_app(cfg1)
    async with LifespanManager(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/admin/aliases", headers=H,
                             json={"set": {}, "unset": ["old"]})
            assert r.status_code == 200, r.text
            assert r.json()["aliases"] == {}
    cfg2 = _config(yaml_aliases={"old": "group-a"})
    cfg2.general_settings.database_url = f"sqlite+aiosqlite:///{db}"
    app2 = create_app(cfg2)
    async with LifespanManager(app2):
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            listing = (await c.get("/admin/models", headers=H)).json()
            assert "old" not in listing["aliases"]


async def test_cycle_rejected_and_no_partial_apply(client):
    """A cyclic batch is rejected atomically — the live map is unchanged."""
    c, _app = client
    # Pre-set a good alias so we can verify atomicity.
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"good": "group-a"}, "unset": []})
    assert r.status_code == 200, r.text
    # Now submit a batch where one entry creates a cycle; live map must NOT change.
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"a2": "b2", "b2": "a2"}, "unset": []})
    assert r.status_code == 400
    listing = (await c.get("/admin/models", headers=H)).json()
    assert listing["aliases"] == {"good": "group-a"}
    assert "a2" not in listing["aliases"]
    assert "b2" not in listing["aliases"]


async def test_cycle_via_existing_chain_rejected():
    """Set q -> p when YAML already has p -> q forms a cycle."""
    cfg = _config(yaml_aliases={"p": "q"})
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/admin/aliases", headers=H,
                             json={"set": {"q": "p"}, "unset": []})
            assert r.status_code == 400
            assert "cycle" in r.json()["error"]["message"].lower()


async def test_shadow_group_rejected(client):
    """Alias key equal to a literal group name shadows it; rejected."""
    c, _app = client
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"group-a": "group-b"}, "unset": []})
    assert r.status_code == 400
    assert "shadow" in r.json()["error"]["message"].lower()


async def test_provider_alias_id_collision_rejected():
    """Alias key colliding with a provider alias_id is rejected (dead alias)."""
    cfg = _config(provider_aliases={"p2": "shared"})
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/admin/aliases", headers=H,
                             json={"set": {"shared": "group-a"}, "unset": []})
            assert r.status_code == 400
            assert "provider" in r.json()["error"]["message"].lower()


async def test_self_loop_rejected(client):
    c, _app = client
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"loop": "loop"}, "unset": []})
    assert r.status_code == 400
    assert "self" in r.json()["error"]["message"].lower()


async def test_chain_depth_bound_8_allowed_9_rejected(client):
    c, _app = client
    # 8 hops: h1 -> h2 -> ... -> h8 -> group-a. All within the runtime bound.
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "h1": "h2", "h2": "h3", "h3": "h4", "h4": "h5",
        "h5": "h6", "h6": "h7", "h7": "h8", "h8": "group-a",
    }, "unset": []})
    assert r.status_code == 200, r.text
    # 9 hops: extend with k9 -> group-b forming a 9-edge chain.
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "k1": "k2", "k2": "k3", "k3": "k4", "k4": "k5",
        "k5": "k6", "k6": "k7", "k7": "k8", "k8": "k9",
        "k9": "group-b",
    }, "unset": []})
    assert r.status_code == 400
    assert "hops" in r.json()["error"]["message"].lower()


async def test_auth_required(client):
    c, _app = client
    r = await c.post("/admin/aliases",
                     json={"set": {"x": "group-a"}, "unset": []})
    assert r.status_code == 401


async def test_audit_event_recorded(client):
    """Successful alias update emits an 'aliases.update' audit event."""
    c, app = client
    r = await c.post("/admin/aliases", headers=H,
                     json={"set": {"audited": "group-c"}, "unset": []})
    assert r.status_code == 200, r.text
    state: AppState = app.state.wiwi
    audit_events = [e for _seq, e in state.logs.sse._rings.get("audit", [])]
    found = [e for e in audit_events
             if e.action == "aliases.update" and e.target == "model_group_alias"]
    assert found, f"no aliases.update audit event found in {audit_events!r}"
    assert found[-1].diff == {"set": {"audited": "group-c"}, "unset": []}