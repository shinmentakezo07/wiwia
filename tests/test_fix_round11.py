"""Round-11 regression tests: usage stats must survive a restart.

Bug: the admin stats endpoints consulted the DB only for all-time (>1440
minute) windows and served 1h/6h/24h windows from the in-memory request ring.
The ring starts empty on every process start, so on Railway/Neon deploys
(where the DB persists but the process restarts) the 1h/6h/24h usage cards
silently reset to zero even though the request_logs rows were still there.

Fix: when a DB sink exists, every window reads from the DB; the ring remains
only as the no-DB fallback. These tests seed the DB directly and never touch
the ring, reproducing the post-restart state: any non-zero answer must come
from the DB.
"""

from __future__ import annotations

import asyncio
import time

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
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent

MASTER = "sk-wiwi-master-round11"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="***************")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


def _evt(ts: float, key_id: str = "", **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts, "key_id": key_id,
                "status": 200, "tok_in": 100, "tok_out": 50,
                "latency_ms": 200.0, "cost": 0.01}
    defaults.update(kw)
    return LogEvent(**defaults)


@pytest.fixture
async def restarted_app_client():
    """App whose DB holds recent traffic but whose in-memory ring is empty —
    the exact state right after a Railway restart with Neon."""
    cfg = _config()
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        sink: DBSink = app.state.wiwi.logs.db_sink
        now = time.time()
        # 30 events over the last ~10 minutes: all inside the 1h window.
        events = [
            _evt(now - i * 20, request_id=f"r-{i}")
            for i in range(30)
        ]
        await sink.write_requests(events)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c, sink


async def test_overview_1h_window_comes_from_db_after_restart(restarted_app_client):
    """/admin/stats/overview?minutes=60 must reflect DB rows, not the empty ring."""
    c, _ = restarted_app_client
    r = await c.get("/admin/stats/overview?minutes=60", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["requests"] == 30, (
        "1h window must be served from the DB (ring is empty after restart)")
    assert body["tok_in"] == 30 * 100
    assert body["tok_out"] == 30 * 50


async def test_overview_24h_window_comes_from_db_after_restart(restarted_app_client):
    c, _ = restarted_app_client
    r = await c.get("/admin/stats/overview?minutes=1440", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["requests"] == 30


async def test_overview_6h_window_comes_from_db_after_restart(restarted_app_client):
    c, _ = restarted_app_client
    r = await c.get("/admin/stats/overview?minutes=360", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["requests"] == 30


async def test_timeseries_1h_window_comes_from_db_after_restart(restarted_app_client):
    """/admin/stats/timeseries?minutes=60 must bucket the DB rows, not the ring."""
    c, _ = restarted_app_client
    r = await c.get("/admin/stats/timeseries?minutes=60&metric=tokens",
                    headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["bucket_seconds"] == 60
    tok = sum(b["tok_in"] for b in body["buckets"])
    assert tok == 30 * 100, (
        "1h timeseries must aggregate DB rows (ring is empty after restart)")


async def test_small_window_db_path_keeps_user_scoping(tmp_path):
    """The DB path for small windows must still scope by the caller's keys:
    a non-admin user sees only rows whose key_id belongs to them."""
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        sink: DBSink = app.state.wiwi.logs.db_sink
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            # User signs up and mints a key; master mints a second key.
            r = await c.post("/auth/signup", json={"username": "user1",
                                                   "password": "password1"})
            assert r.status_code == 201
            ka = (await c.post("/admin/keys/generate",
                               json={"name": "ka"})).json()
            kb = (await c.post("/admin/keys/generate", json={"name": "kb"},
                               headers=AUTH)).json()
            now = time.time()
            await sink.write_requests([
                _evt(now, key_id=ka["id"], request_id="own-1"),
                _evt(now, key_id=kb["id"], request_id="other-1"),
            ])
            # Poll until the pump flush lands (async DB write path).
            for _ in range(50):
                ov = await c.get("/admin/stats/overview?minutes=60")
                if ov.json().get("requests", 0) >= 1:
                    break
                await asyncio.sleep(0.05)
            body = ov.json()
            assert body["requests"] == 1, (
                "small-window DB path must scope stats to the caller's keys")
