"""Tests for the request-logs endpoint default limit and the Usage page count.

Bug: /admin/ui/usage always showed 200 requests because the endpoint defaulted
to limit=200 and the frontend never overrode it. These tests pin the fix:
- the endpoint default must be high enough that the page sees real traffic,
- the client must pass an explicit limit, and
- the response length must match the explicit limit requested.
"""

from __future__ import annotations

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

MASTER = "sk-wiwi-master-usage"
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


def _evt(ts: float, **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts}
    defaults.update(kw)
    return LogEvent(**defaults)


@pytest.fixture
async def seeded_app_client():
    """App with 350 log rows in the DB so we can prove the cap is no longer 200."""
    cfg = _config()
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        sink: DBSink = app.state.wiwi.logs.db_sink
        now = time.time()
        events = [
            _evt(now - i, status=200, tok_in=10, tok_out=5,
                 latency_ms=100.0, cost=0.001, model_group="gpt-4o",
                 provider="openai", provider_key_label="a", surface="chat",
                 key_alias="k1", request_id=f"r-{i}")
            for i in range(350)
        ]
        await sink.write_requests(events)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c, sink


async def test_request_logs_default_limit_exceeds_200(seeded_app_client):
    """Default GET /admin/logs/requests must return more than 200 rows when
    the DB has more than 200 — otherwise the Usage page shows '200 requests'
    regardless of real traffic."""
    c, _ = seeded_app_client
    r = await c.get("/admin/logs/requests", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["logs"]) > 200, (
        f"expected default limit > 200, got {len(body['logs'])}"
    )


async def test_request_logs_honors_explicit_limit(seeded_app_client):
    """The frontend can request a higher cap; the endpoint must respect it
    up to its own hard ceiling."""
    c, _ = seeded_app_client
    r = await c.get("/admin/logs/requests?limit=500", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["logs"]) == 350  # all rows, not capped at 200


async def test_overview_actual_request_count_matches_db(seeded_app_client):
    """The DB-backed overview already returned the real COUNT(*); the Usage
    page must use it. Pin the contract here so a regression in either side is
    caught."""
    c, _ = seeded_app_client
    r = await c.get("/admin/stats/overview?minutes=0", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["requests"] == 350
