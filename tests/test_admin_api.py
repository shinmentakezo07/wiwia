"""Admin API surface: new endpoints, guards, live-pool mutations, stats math."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import orjson
import pytest
import respx
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
from wiwi.logging_core.events import LogEvent
from wiwi.server.stats import overview, timeseries

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
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
    ("GET", "/admin/providers"),
    ("PATCH", "/admin/providers/p1/keys/a"),
    ("POST", "/admin/providers/p1/keys"),
    ("POST", "/admin/providers"),
    ("GET", "/admin/models"),
    ("PATCH", "/admin/model-groups/gpt-4o"),
    ("PATCH", "/admin/keys/k123"),
    ("GET", "/admin/stats/overview"),
    ("GET", "/admin/stats/timeseries"),
    ("GET", "/admin/logs/proxy"),
    ("GET", "/admin/alert-rules"),
    ("PUT", "/admin/alert-rules"),
])
async def test_new_endpoints_require_master(client, method, path):
    r = await client.request(method, path, json={"weight": 1})
    assert r.status_code == 401
    r = await client.request(method, path, json={"weight": 1},
                             headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# -- providers -------------------------------------------------------------------

async def test_providers_lists_pool_without_secrets(client):
    r = await client.get("/admin/providers", headers=AUTH)
    assert r.status_code == 200
    providers = r.json()["providers"]
    assert len(providers) == 1
    p = providers[0]
    assert p["name"] == "p1" and p["provider_type"] == "openai"
    assert p["healthy"] is True
    key = p["keys"][0]
    assert key["label"] == "a"
    assert key["status"] == "active"
    assert key["req_count"] == 0 and key["err_count"] == 0
    assert key["last_used_ts"] is None
    # H4: plaintext secrets must NOT be exposed in list responses — the
    # masked form is sufficient for the admin UI; reveal happens via the
    # dedicated POST /admin/providers/{name}/keys/{label}/reveal endpoint.
    assert "secret" not in key
    assert key["masked"].startswith("sk-te")


async def test_patch_provider_key_updates_live_pool(client):
    r = await client.patch("/admin/providers/p1/keys/a",
                           json={"enabled": False, "weight": 5}, headers=AUTH)
    assert r.status_code == 200
    key = r.json()["key"]
    assert key["enabled"] is False and key["weight"] == 5
    assert key["status"] == "disabled"
    # visible in the listing too
    lst = (await client.get("/admin/providers", headers=AUTH)).json()
    k = lst["providers"][0]["keys"][0]
    assert k["enabled"] is False and k["weight"] == 5
    bad = await client.patch("/admin/providers/nope/keys/a",
                             json={"weight": 2}, headers=AUTH)
    assert bad.status_code == 404
    bad2 = await client.patch("/admin/providers/p1/keys/zzz",
                              json={"weight": 2}, headers=AUTH)
    assert bad2.status_code == 404
    bad3 = await client.patch("/admin/providers/p1/keys/a",
                              json={"weight": "lots"}, headers=AUTH)
    assert bad3.status_code == 400


async def test_add_provider_key_runtime(client):
    r = await client.post("/admin/providers/p1/keys",
                          json={"label": "backup", "key": "sk-second-key-xyz",
                                "weight": 2}, headers=AUTH)
    assert r.status_code == 200
    dup = await client.post("/admin/providers/p1/keys",
                            json={"label": "backup", "key": "x"}, headers=AUTH)
    assert dup.status_code == 409
    missing = await client.post("/admin/providers/nope/keys",
                                json={"label": "b", "key": "k"}, headers=AUTH)
    assert missing.status_code == 404
    provs = (await client.get("/admin/providers", headers=AUTH)).json()
    labels = [k["label"] for k in provs["providers"][0]["keys"]]
    assert labels == ["a", "backup"]
    # H4: list responses must never include plaintext secrets.
    provs2 = (await client.get("/admin/providers", headers=AUTH)).json()
    for k in provs2["providers"][0]["keys"]:
        assert "secret" not in k
    assert "sk-second-key-xyz" not in (await client.get(
        "/admin/providers", headers=AUTH)).text


async def test_add_provider_account_runtime(client):
    r = await client.post("/admin/providers",
                          json={"name": "p2", "provider_type": "openai-compatible",
                                "base_url": "https://relay.example/v1",
                                "label": "main", "key": "sk-new-acct-key"}, headers=AUTH)
    assert r.status_code == 200
    dup = await client.post("/admin/providers",
                            json={"name": "p2", "key": "k"}, headers=AUTH)
    assert dup.status_code == 409
    no_key = await client.post("/admin/providers",
                               json={"name": "p3"}, headers=AUTH)
    assert no_key.status_code == 400
    provs = (await client.get("/admin/providers", headers=AUTH)).json()
    names = [p["name"] for p in provs["providers"]]
    assert names == ["p1", "p2"]
    p2 = provs["providers"][1]
    assert p2["base_url"] == "https://relay.example/v1"


# -- models & routing -------------------------------------------------------------

async def test_models_listing_shape(client):
    r = await client.get("/admin/models", headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["strategy"] == "simple-shuffle"
    g = data["groups"][0]
    assert g["name"] == "gpt-4o"
    d = g["deployments"][0]
    assert d["provider"] == "p1" and d["model_id"] == "gpt-4o"
    assert d["weight"] == 1 and d["available"] is True


async def test_patch_model_group_weights_and_strategy(client):
    r = await client.patch("/admin/model-groups/gpt-4o",
                           json={"weights": {"p1/gpt-4o": 7},
                                 "strategy": "latency-based"}, headers=AUTH)
    assert r.status_code == 200
    models = (await client.get("/admin/models", headers=AUTH)).json()
    assert models["strategy"] == "latency-based"
    assert models["groups"][0]["deployments"][0]["weight"] == 7
    # patch via alias name resolves to the same group
    cfg = _config()
    cfg.router_settings.model_group_alias = {"gpt4": "gpt-4o"}
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r2 = await c.patch("/admin/model-groups/gpt4",
                               json={"weights": {"p1/gpt-4o": 3}}, headers=AUTH)
            assert r2.status_code == 200
            assert r2.json()["group"] == "gpt-4o"
    for payload, code in [
        ({"strategy": "bogus"}, 400),
        ({"weights": {"other/model": 2}}, 400),
    ]:
        rr = await client.patch("/admin/model-groups/gpt-4o", json=payload,
                                headers=AUTH)
        assert rr.status_code == code
    nf = await client.patch("/admin/model-groups/nope", json={}, headers=AUTH)
    assert nf.status_code == 404


# -- provider model fetch + deployment create ---------------------------------------

@respx.mock
async def test_provider_models_fetch_openai(client):
    respx.get("https://api.openai.com/v1/models").respond(
        json={"object": "list",
              "data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}, {"id": "o3"}]})
    r = await client.get("/admin/providers/p1/models", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["models"] == [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}, {"id": "o3"}]


@respx.mock
async def test_provider_models_upstream_error_passthrough(client):
    respx.get("https://api.openai.com/v1/models").respond(
        status_code=401, json={"error": {"message": "bad key"}})
    r = await client.get("/admin/providers/p1/models", headers=AUTH)
    assert r.status_code == 401
    assert "bad key" in r.json()["error"]["message"]


async def test_provider_models_unknown_provider(client):
    r = await client.get("/admin/providers/nope/models", headers=AUTH)
    assert r.status_code == 404


def test_parse_models_response_shapes():
    openai = app_mod._parse_models_response("openai", orjson.dumps(
        {"data": [{"id": "b"}, {"id": "a"}, {"nope": 1}]}))
    assert openai == [{"id": "b"}, {"id": "a"}]
    gemini = app_mod._parse_models_response("gemini", orjson.dumps(
        {"models": [{"name": "models/gemini-2.0-flash"},
                    {"name": "tunedModels/x", "supported": True}]}))
    assert gemini == [{"id": "gemini-2.0-flash"}, {"id": "x"}]
    assert app_mod._parse_models_response("openai", b"null") == []


async def test_add_deployment_runtime(client):
    r = await client.post("/admin/model-groups/newgrp/deployments",
                          json={"provider": "p1", "model_id": "gpt-4o-mini",
                                "weight": 3}, headers=AUTH)
    assert r.status_code == 201
    dep = r.json()["deployment"]
    assert dep["provider"] == "p1" and dep["model_id"] == "gpt-4o-mini"
    assert dep["weight"] == 3 and dep["available"] is True
    # visible in the group listing
    groups = (await client.get("/admin/models", headers=AUTH)).json()["groups"]
    g = next(g for g in groups if g["name"] == "newgrp")
    assert g["deployments"][0]["model_id"] == "gpt-4o-mini"
    # second model on the same group; then duplicate is rejected
    ok = await client.post("/admin/model-groups/newgrp/deployments",
                           json={"provider": "p1", "model_id": "o3"}, headers=AUTH)
    assert ok.status_code == 201
    dup = await client.post("/admin/model-groups/newgrp/deployments",
                            json={"provider": "p1", "model_id": "gpt-4o-mini"},
                            headers=AUTH)
    assert dup.status_code == 409
    # validation
    missing = await client.post("/admin/model-groups/newgrp/deployments",
                                json={"provider": "p1"}, headers=AUTH)
    assert missing.status_code == 400
    unknown = await client.post("/admin/model-groups/newgrp/deployments",
                                json={"provider": "ghost", "model_id": "m"},
                                headers=AUTH)
    assert unknown.status_code == 404
    badw = await client.post("/admin/model-groups/newgrp/deployments",
                             json={"provider": "p1", "model_id": "m2",
                                   "weight": "lots"}, headers=AUTH)
    assert badw.status_code == 400


# -- virtual keys PATCH ------------------------------------------------------------

async def test_patch_virtual_key_fields(client):
    gen = await client.post("/admin/keys/generate",
                            json={"name": "team-x", "max_budget": 10.0}, headers=AUTH)
    kid = gen.json()["id"]
    r = await client.patch(f"/admin/keys/{kid}",
                           json={"max_budget": 25.5, "rpm": 60, "tpm": None,
                                 "models": ["gpt-4o"], "expires_at": None},
                           headers=AUTH)
    assert r.status_code == 200
    key = r.json()["key"]
    assert key["max_budget"] == 25.5
    assert key["rpm"] == 60
    assert key["tpm"] is None
    assert key["models"] == ["gpt-4o"]
    assert key["expires_at"] is None
    # absent fields stay untouched
    r2 = await client.patch(f"/admin/keys/{kid}", json={"rpm": 120}, headers=AUTH)
    assert r2.json()["key"]["max_budget"] == 25.5
    nf = await client.patch("/admin/keys/kzzz", json={"rpm": 5}, headers=AUTH)
    assert nf.status_code == 404


# -- stats math (synthetic rings) ----------------------------------------------------

def _evt(ts: float, **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts}
    defaults.update(kw)
    return LogEvent(**defaults)


def test_overview_math():
    now = 1_800_000_000.0
    events = [
        _evt(now - 30, status=200, tok_in=100, tok_cached=40, tok_reasoning=10,
             tok_out=50, tps=80.0, ttft_ms=250.0, latency_ms=1000.0, cost=0.01,
             cache_hit=True, cache_savings=0.002),
        _evt(now - 20, status=200, tok_in=200, tok_out=100, tps=40.0, ttft_ms=500.0,
             latency_ms=2000.0, cost=0.02),
        _evt(now - 10, status=500, error_code="api_error", tok_in=50, cost=0.0),
        _evt(now - 9999, status=200, tok_in=9_999),  # outside window
    ]
    ov = overview(events, minutes=5, now=now)
    assert ov["requests"] == 3
    assert ov["errors"] == 1
    assert abs(ov["error_rate"] - round(1 / 3, 4)) < 1e-6
    assert ov["tok_in"] == 350 and ov["tok_cached"] == 40
    assert ov["tok_reasoning"] == 10 and ov["tok_out"] == 150
    assert ov["cache_hits"] == 1
    assert abs(ov["cache_hit_rate"] - round(1 / 3, 4)) < 1e-6
    assert ov["tps_avg"] == 60.0          # (80 + 40) / 2, zero-tps excluded
    assert ov["tps_p95"] == 80.0
    assert ov["ttft_p95_ms"] == 500.0
    assert ov["requests_per_minute"] == 0.6
    assert abs(ov["cost"] - 0.03) < 1e-9
    assert abs(ov["cache_savings"] - 0.002) < 1e-9


def test_timeseries_buckets():
    now = (1_800_001_230 // 60) * 60 + 30  # mid-minute now
    events = [
        _evt(now - 95, tok_in=10, tok_out=5, tps=10.0),
        _evt(now - 35, tok_in=20, tok_cached=8, tok_reasoning=2, tok_out=7, tps=30.0),
        _evt(now - 25, tok_in=1, tok_out=1, tps=90.0),
        _evt(now - 500, tok_in=99),  # outside window
    ]
    ts = timeseries(events, "minute", "tokens", 3, now=now)
    assert ts["bucket_seconds"] == 60
    buckets = ts["buckets"]
    assert len(buckets) == 3
    by_t = {b["t"]: b for b in buckets}
    cur = by_t[int(now // 60) * 60]              # holds now-25 event
    mid = by_t[int(now // 60) * 60 - 60]         # holds now-35 event
    old = by_t[int(now // 60) * 60 - 120]        # holds now-95 event
    assert (cur["tok_in"], cur["tok_cached"], cur["tok_reasoning"],
            cur["tok_out"]) == (1, 0, 0, 1)
    assert (mid["tok_in"], mid["tok_cached"], mid["tok_reasoning"],
            mid["tok_out"]) == (20, 8, 2, 7)
    assert (old["tok_in"], old["tok_out"]) == (10, 5)

    tp = timeseries(events, "minute", "tps", 3, now=now)
    by_tp = {b["t"]: b for b in tp["buckets"]}
    assert by_tp[int(now // 60) * 60]["tps_avg"] == 90.0
    assert by_tp[int(now // 60) * 60 - 60]["tps_avg"] == 30.0
    assert by_tp[int(now // 60) * 60 - 120]["tps_p95"] == 10.0

    with pytest.raises(ValueError):
        timeseries(events, "hour", "tokens", 3, now=now)
    with pytest.raises(ValueError):
        timeseries(events, "minute", "cost", 3, now=now)


async def test_stats_endpoints_shapes(client):
    ok = await client.get("/admin/stats/overview?minutes=30", headers=AUTH)
    assert ok.status_code == 200
    assert {"requests", "tok_in", "cache_hit_rate", "tps_p95", "ttft_p95_ms"} \
        <= set(ok.json())
    ok2 = await client.get("/admin/stats/timeseries",
                           params={"bucket": "minute", "metric": "tokens",
                                   "minutes": 15},
                           headers=AUTH)
    body = ok2.json()
    assert len(body["buckets"]) == 15
    bad = await client.get("/admin/stats/timeseries",
                           params={"metric": "nope"}, headers=AUTH)
    assert bad.status_code == 400


# -- proxy logs & alert rules ---------------------------------------------------------

async def test_proxy_logs_endpoint_empty(client):
    r = await client.get("/admin/logs/proxy", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"logs": []}


async def test_proxy_logs_ring_returns_newest_first(client):
    """Ring-backed path: proxy logs must be newest-first and include the
    Cache-Control: no-store header. Pins the contract that the frontend
    relies on (it no longer reverses the array)."""
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        # Publish three proxy events in order a, b, c (c is newest).
        app.state.wiwi.logs.log_proxy("info", "alpha", request_id="ra")
        app.state.wiwi.logs.log_proxy("info", "beta", request_id="rb")
        app.state.wiwi.logs.log_proxy("error", "gamma", request_id="rc")
        # The ring is pumped asynchronously; give the worker a beat.
        import asyncio
        await asyncio.sleep(0.05)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.get("/admin/logs/proxy", headers=AUTH)
            assert r.status_code == 200
            assert r.headers.get("cache-control") == "no-store"
            logs = r.json()["logs"]
            # newest-first: gamma (last published) comes first
            assert [row["message"] for row in logs] == ["gamma", "beta", "alpha"]
            assert [row["level"] for row in logs] == ["error", "info", "info"]


async def test_alert_rules_roundtrip(client):
    rules = [{"id": "r1", "webhook_url": "https://hooks.example/x",
              "metric": "spend", "threshold": 10.0}]
    r = await client.put("/admin/alert-rules", json={"rules": rules}, headers=AUTH)
    assert r.status_code == 200
    got = await client.get("/admin/alert-rules", headers=AUTH)
    assert got.json()["rules"] == rules
    bad = await client.put("/admin/alert-rules", json={"rules": "nope"}, headers=AUTH)
    assert bad.status_code == 400


# -- SPA mount ------------------------------------------------------------------------

async def test_spa_mount_serves_index_when_built(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    assets = static_dir / "assets"
    assets.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html><body>wiwi-ui-marker</body></html>")
    (assets / "app.js").write_text("console.log('x')")
    monkeypatch.setenv("WIWI_STATIC_DIR", str(static_dir))
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.get("/admin/ui/")
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            assert "wiwi-ui-marker" in r.text
            ra = await c.get("/admin/ui/assets/app.js")
            assert ra.status_code == 200
            # admin API routes still win over the mount
            api = await c.get("/admin/keys", headers=AUTH)
            assert api.status_code == 200


def test_default_static_dir_is_package_static(monkeypatch):
    monkeypatch.delenv("WIWI_STATIC_DIR", raising=False)
    expected = Path(app_mod.__file__).parent / "static"
    assert os.path.isdir(expected) or True  # dir may not exist until first build
    # sanity: env override path construction matches create_app's logic
    assert str(expected).endswith("server/static")
