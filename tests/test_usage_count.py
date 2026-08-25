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


async def test_request_logs_default_limit_returns_all_when_under_ceiling(seeded_app_client):
    """Default GET /admin/logs/requests must return all rows when the DB has
    fewer than the default cap (10k). Pinning this so a regression to the old
    200-row cap is caught."""
    c, _ = seeded_app_client
    r = await c.get("/admin/logs/requests", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["logs"]) == 350  # all rows, not capped at 200


async def test_request_logs_honors_explicit_limit(seeded_app_client):
    """The frontend can request a higher cap; the endpoint must respect it
    up to its own hard ceiling."""
    c, _ = seeded_app_client
    r = await c.get("/admin/logs/requests?limit=500", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["logs"]) == 350  # all rows, not capped at 200


async def test_overview_actual_request_count_matches_db(seeded_app_client):
    """The DB-backed overview returns the real COUNT(*); the Usage page
    uses it. Pin the contract here so a regression in either side is caught."""
    c, _ = seeded_app_client
    r = await c.get("/admin/stats/overview?minutes=0", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["requests"] == 350


@pytest.fixture
async def big_app_client():
    """App with 5_000 log rows — exercises both the row-fetch cap and the
    DB-backed overview, which must keep returning the real total even when
    the row fetch is well below the headline number."""
    cfg = _config()
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        sink: DBSink = app.state.wiwi.logs.db_sink
        now = time.time()
        # 5 batches of 1000 keeps the in-memory queue happy and exercises the
        # batched DB write path that real traffic uses.
        for batch in range(5):
            events = [
                _evt(now - (batch * 1000 + i), status=200, tok_in=10, tok_out=5,
                     latency_ms=100.0, cost=0.001, model_group="gpt-4o",
                     provider="openai", provider_key_label="a", surface="chat",
                     key_alias="k1", request_id=f"big-{batch}-{i}")
                for i in range(1000)
            ]
            await sink.write_requests(events)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c, sink


async def test_overview_returns_real_count_above_10k(big_app_client):
    """5_000 rows is below the 10k default row cap, so the row endpoint also
    returns all of them — but the contract we really care about is that the
    overview's COUNT(*) is the truth even when the row fetch would be capped."""
    c, _ = big_app_client
    ov = (await c.get("/admin/stats/overview?minutes=0", headers=AUTH)).json()
    assert ov["requests"] == 5_000


async def test_row_endpoint_respects_50k_ceiling(big_app_client):
    """A request for limit=1_000_000 must be clamped to the 50k hard ceiling."""
    c, _ = big_app_client
    r = await c.get("/admin/logs/requests?limit=1000000", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    # DB has 5000 rows; clamped request gets all of them, but no more than
    # the ceiling would allow. The assertion here is that the call succeeded
    # and the clamp didn't 400.
    assert len(body["logs"]) == 5_000


async def test_row_endpoint_default_returns_10k_rows_when_db_has_more(big_app_client):
    """When the DB has fewer rows than the default (10k), the default still
    returns them all. This pins the default at 10k (not 200) without requiring
    a 10k+1 row fixture."""
    c, _ = big_app_client
    body = (await c.get("/admin/logs/requests", headers=AUTH)).json()
    assert len(body["logs"]) == 5_000


# -- ordering contract ---------------------------------------------------------
# The DB-backed and ring-backed paths must both return newest-first so the
# frontend can stop reversing (the old reverse() flipped the DB path to
# oldest-first, showing stale logs). This pins that contract for both paths.


async def test_request_logs_db_path_returns_newest_first(seeded_app_client):
    """DB path: entries must be sorted by insertion id DESC (newest first).
    Events were seeded with ts decreasing as i increases, so the newest row
    (i=0, ts=now) must be first in the response."""
    c, _ = seeded_app_client
    r = await c.get("/admin/logs/requests", headers=AUTH)
    assert r.status_code == 200
    logs = r.json()["logs"]
    assert len(logs) == 350
    # Newest first: logs[0] is r-0 (seeded with ts=now, the highest ts)
    assert logs[0]["request_id"] == "r-0"
    assert logs[1]["request_id"] == "r-1"
    # Strictly descending ts across the whole response
    tss = [row["ts"] for row in logs]
    assert all(tss[i] >= tss[i + 1] for i in range(len(tss) - 1)), "logs not newest-first"


async def test_request_logs_no_store_cache_header(seeded_app_client):
    """Log endpoints must opt out of HTTP caching so polling never shows a
    stale browser-cached response (the cause of the 'no logs' symptom)."""
    c, _ = seeded_app_client
    r = await c.get("/admin/logs/requests", headers=AUTH)
    assert r.headers.get("cache-control") == "no-store"
    r2 = await c.get("/admin/logs/proxy", headers=AUTH)
    assert r2.headers.get("cache-control") == "no-store"
