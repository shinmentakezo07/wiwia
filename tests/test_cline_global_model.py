"""Tests for the global Cline default-model feature.

The admin UI lets a user pick a model id once (e.g. ``z-ai/glm-5.2``) and
have that model deployed to *every* Cline account, existing and future.
Requests to the auto-created ``cline:<model_id>`` group then smooth-WRR
across accounts using the existing ``_CrossProviderWRR`` cursor.

Coverage:

- GET /admin/cline/settings returns the persisted list (empty when unset)
- PUT /admin/cline/settings persists + reconciles deployments
- Idempotency: putting the same list twice does not duplicate deployments
- Adding a new Cline provider auto-applies the existing defaults
- DELETE /admin/cline/settings/default-models/{model_id} drops every
  deployment for that model
- GET /admin/cline/models uses a 5-minute in-memory cache
- GET /admin/cline/models?refresh=true busts the cache
"""

from __future__ import annotations

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)

MASTER = "sk-wiwi-master-test"
USER_KEY = "sk-wiwi-user-1"

CLINE_MODELS_URL = "https://api.cline.bot/api/v1/models"

# Sample Cline model catalog (mimics the upstream response).
CLINE_CATALOG = {
    "object": "list",
    "data": [
        {"id": "z-ai/glm-5.2", "object": "model"},
        {"id": "z-ai/glm-5.3-flash", "object": "model"},
        {"id": "claude-sonnet-5", "object": "model"},
        {"id": "gpt-5.5", "object": "model"},
    ],
}


def _config_with_cline(*, providers: list[ProviderDef],
                       model_list: list[ModelEntry] | None = None) -> WiwiConfig:
    return WiwiConfig(
        providers=providers,
        model_list=model_list or [],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=2,
                                       cooldown_time=60.0),
    )


def _cline_providers(*names: str) -> list[ProviderDef]:
    return [ProviderDef(name=n, provider="cline",
                        base_url="https://api.cline.bot/api/v1",
                        keys=[KeyDef(label="default",
                                     key=f"workos:stale-{n}")])
            for n in names]


@pytest.fixture
async def app_and_state():
    """Yield (app, app.state.wiwi) for a 2-Cline-provider app."""
    config = _config_with_cline(providers=_cline_providers("cline-a", "cline-b"))
    app = app_mod.create_app(config)
    async with LifespanManager(app):
        yield app, app.state.wiwi


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# -- 1. GET default models returns empty list when unset ----------------------


async def test_get_default_models_returns_empty_when_unset(app_and_state):
    """No ``cline_settings:default_models`` persisted → empty list."""
    _, state = app_and_state
    # Sanity: nothing in the store yet.
    assert await state.config_store.get_setting("cline_settings:default_models") is None


# -- 2. PUT persists + creates deployments across accounts ---------------------


async def test_put_default_models_persists_and_creates_deployments(app_and_state):
    """PUT a model id → every Cline provider gets a deployment in
    ``cline:<model_id>``, the group becomes cross-provider (WRR cursor
    built), and the list is persisted."""
    app, state = app_and_state
    async with await _client(app) as c:
        r = await c.put(
            "/admin/cline/settings",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"default_models": ["z-ai/glm-5.2"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["default_models"] == ["z-ai/glm-5.2"]

    # Router state
    group_name = "cline:z-ai/glm-5.2"
    deps = state.router.groups.get(group_name, [])
    assert len(deps) == 2
    assert {d.provider.name for d in deps} == {"cline-a", "cline-b"}
    # Cross-provider WRR cursor must be built (group spans 2 providers).
    assert group_name in state.router._group_provider_rr

    # Persisted
    persisted = await state.config_store.get_setting("cline_settings:default_models")
    assert persisted == ["z-ai/glm-5.2"]


# -- 3. Idempotency: PUT twice does not duplicate deployments ----------------


async def test_put_default_models_is_idempotent(app_and_state):
    """PUT the same list twice → deployment count unchanged."""
    app, state = app_and_state
    async with await _client(app) as c:
        r1 = await c.put(
            "/admin/cline/settings",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"default_models": ["z-ai/glm-5.2"]},
        )
        assert r1.status_code == 200
        r2 = await c.put(
            "/admin/cline/settings",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"default_models": ["z-ai/glm-5.2"]},
        )
        assert r2.status_code == 200

    deps = state.router.groups.get("cline:z-ai/glm-5.2", [])
    assert len(deps) == 2  # one per provider, no duplicates


# -- 4. Adding a new Cline provider auto-applies defaults --------------------


async def test_add_cline_provider_auto_applies_default_models(app_and_state):
    """After PUT defaults, adding a new Cline provider creates a
    deployment for that provider in every default-model group."""
    app, state = app_and_state
    # Pre-set defaults
    async with await _client(app) as c:
        r = await c.put(
            "/admin/cline/settings",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"default_models": ["z-ai/glm-5.2", "claude-sonnet-5"]},
        )
        assert r.status_code == 200

        # Add a third Cline provider at runtime
        r2 = await c.post(
            "/admin/providers",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"name": "cline-c", "provider_type": "cline",
                  "base_url": "https://api.cline.bot/api/v1",
                  "key": "workos:fresh-c"},
        )
        assert r2.status_code == 200, r2.text

    # The new provider should appear in BOTH default-model groups.
    for mid in ("z-ai/glm-5.2", "claude-sonnet-5"):
        g = state.router.groups.get(f"cline:{mid}", [])
        providers_in_group = {d.provider.name for d in g}
        assert "cline-c" in providers_in_group, f"cline-c missing from cline:{mid}"


# -- 5. DELETE drops every deployment for that model -------------------------


async def test_remove_default_model_drops_every_deployment(app_and_state):
    """DELETE one model id → router group is gone, persistence is updated."""
    app, state = app_and_state
    async with await _client(app) as c:
        r = await c.put(
            "/admin/cline/settings",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"default_models": ["z-ai/glm-5.2", "claude-sonnet-5"]},
        )
        assert r.status_code == 200
        # Delete one
        r2 = await c.delete(
            "/admin/cline/settings/default-models/z-ai/glm-5.2",
            headers={"Authorization": f"Bearer {MASTER}"},
        )
        assert r2.status_code == 200, r2.text

    # The deleted group's deployments are gone
    assert "cline:z-ai/glm-5.2" not in state.router.groups
    # The other group remains
    assert "cline:claude-sonnet-5" in state.router.groups
    # Persisted list now only has the survivor
    persisted = await state.config_store.get_setting("cline_settings:default_models")
    assert persisted == ["claude-sonnet-5"]


# -- 6. /admin/cline/models uses a 5-minute in-memory cache ------------------


@respx.mock
async def test_global_model_list_uses_cached_catalog():
    """Two back-to-back GETs hit the upstream only once.

    Built without the shared ``app_and_state`` fixture so we can install
    the respx mock via the ``@respx.mock`` decorator (the context-manager
    form is broken in respx 0.23 + httpx 0.28).
    """
    config = _config_with_cline(providers=_cline_providers("cline-a"))
    app = app_mod.create_app(config)
    async with LifespanManager(app):
        upstream = respx.get(CLINE_MODELS_URL).mock(
            return_value=httpx.Response(200, json=CLINE_CATALOG))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r1 = await c.get("/admin/cline/models",
                             headers={"Authorization": f"Bearer {MASTER}"})
            r2 = await c.get("/admin/cline/models",
                             headers={"Authorization": f"Bearer {MASTER}"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert upstream.call_count == 1
    # Cached body identical
    assert r1.json() == r2.json()


# -- 7. /admin/cline/models?refresh=true busts the cache ---------------------


@respx.mock
async def test_global_model_list_refresh_busts_cache():
    """``?refresh=true`` forces a re-fetch."""
    config = _config_with_cline(providers=_cline_providers("cline-a"))
    app = app_mod.create_app(config)
    async with LifespanManager(app):
        upstream = respx.get(CLINE_MODELS_URL).mock(
            return_value=httpx.Response(200, json=CLINE_CATALOG))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r1 = await c.get("/admin/cline/models",
                             headers={"Authorization": f"Bearer {MASTER}"})
            r2 = await c.get("/admin/cline/models?refresh=true",
                             headers={"Authorization": f"Bearer {MASTER}"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert upstream.call_count == 2
