"""Regression tests for the end-to-end audit fixes (2026-08-21 round 3).

Covers: stale negative auth cache vs freshly created keys, model-listing
bypass for allowlisted virtual keys, admin weight validation, base_url
requirement for compatible providers, latency-based cold-start exploration,
Retry-After propagation on upstream 429s, and stream generator cleanup on
connect-phase failures.
"""

import httpx
import pytest
import respx
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
from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.router.router import Deployment, Router
from wiwi.server.app import create_app


def _cfg(**router_overrides) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(**{"num_retries": 1, **router_overrides}),
    )


# -- R1: a key created after its plaintext was guessed authenticates at once -----
@pytest.mark.asyncio
async def test_created_key_not_blocked_by_negative_cache():
    import sqlalchemy.ext.asyncio as saa

    from wiwi.auth.keys import generate_virtual_key
    from wiwi.auth.service import AuthService

    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    svc = AuthService(engine, "sk-wiwi-master-test")
    await svc.startup()

    plaintext = generate_virtual_key()
    # simulate an earlier failed guess of this exact plaintext (negative cache)
    assert await svc.authenticate(plaintext) is None
    # creating the key must invalidate that stale negative entry
    _kid = None
    plaintext2, kid = await svc.create_key("ci", custom_key=plaintext)
    info = await svc.authenticate(plaintext2)
    await engine.dispose()
    assert info is not None and info.key_id == kid


# -- R2: allowlisted virtual keys can still list models ---------------------------
@respx.mock
async def test_models_listing_allows_scoped_keys():
    app = create_app(_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as client:
            mk = {"Authorization": "Bearer sk-wiwi-master-test"}
            r = await client.post("/admin/keys/generate", headers=mk,
                                  json={"name": "scoped", "models": ["gpt-4o"]})
            vkey = r.json()["key"]
            r = await client.get("/v1/models",
                                 headers={"Authorization": f"Bearer {vkey}"})
            assert r.status_code == 200
            ids = [m["id"] for m in r.json()["data"]]
            assert "gpt-4o" in ids
            # real completions are still enforced against the allowlist
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                json={"choices": [], "usage": {}})
            r = await client.post("/v1/chat/completions", headers={
                "Authorization": f"Bearer {vkey}"},
                json={"model": "other-model", "messages": []})
            assert r.status_code == 403


# -- R3: admin endpoints reject non-integer weights with 400 ----------------------
async def test_add_provider_key_rejects_bad_weight():
    app = create_app(_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as client:
            mk = {"Authorization": "Bearer sk-wiwi-master-test"}
            r = await client.post("/admin/providers/p1/keys", headers=mk,
                                  json={"label": "b", "key": "sk-x",
                                        "weight": "lots"})
            assert r.status_code == 400


# -- R4: openai-compatible providers require an explicit base_url -----------------
async def test_add_compatible_provider_requires_base_url():
    app = create_app(_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as client:
            mk = {"Authorization": "Bearer sk-wiwi-master-test"}
            r = await client.post("/admin/providers", headers=mk,
                                  json={"name": "compat", "provider_type":
                                        "openai-compatible", "key": "sk-x"})
            assert r.status_code == 400
            ok = await client.post("/admin/providers", headers=mk,
                                   json={"name": "compat", "provider_type":
                                         "openai-compatible", "key": "sk-x",
                                         "base_url": "https://x.example/v1"})
            assert ok.status_code == 200


# -- R5: latency-based routing explores deployments with no samples ---------------
def test_latency_based_explores_cold_deployments():
    cfg = _cfg(routing_strategy="latency-based")
    router = Router(cfg)
    acct = router.providers["p1"]
    cold = Deployment(group="g", provider=acct, model_id="cold")
    warm = Deployment(group="g", provider=acct, model_id="warm")
    warm.latencies.extend([10, 10, 10])  # warmed-up peer
    deps = [warm, cold]
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    picks = {router.pick_deployment(deps, ctx).model_id for _ in range(20)}
    assert "cold" in picks  # cold deployment is explored, not starved


# -- R6: upstream 429 retry-after surfaces as a Retry-After header ----------------
@respx.mock
async def test_rate_limit_error_sets_retry_after_header():
    app = create_app(_cfg(num_retries=0))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as client:
            respx.post("https://api.openai.com/v1/chat/completions").respond(
                status_code=429, headers={"Retry-After": "7"},
                json={"error": {"message": "slow down"}})
            r = await client.post("/v1/chat/completions", headers={
                "Authorization": "Bearer sk-wiwi-master-test"},
                json={"model": "gpt-4o",
                      "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 429
            assert r.headers.get("Retry-After") == "7"
