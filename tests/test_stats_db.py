"""Tests for DB-backed stats and bucket sizing helpers."""

from __future__ import annotations

import time

import httpx
import pytest
import sqlalchemy as sa
import sqlalchemy.ext.asyncio as saa
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
from wiwi.server.stats import bucket_size_for


@pytest.mark.parametrize("minutes,expected", [
    (0, 86400),       # all-time
    (15, 60),         # 15 min
    (60, 60),         # 1 hour
    (360, 60),        # 6 hours
    (1440, 60),       # 24 hours
    (1441, 3600),     # just over 24h -> 1 hour buckets
    (10080, 3600),    # 7 days
    (10081, 21600),   # just over 7d -> 6 hour buckets
    (43200, 21600),   # 30 days
    (43201, 86400),   # over 30d -> 1 day buckets
])
def test_bucket_size_for(minutes, expected):
    assert bucket_size_for(minutes) == expected


def _evt(ts: float, **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts}
    defaults.update(kw)
    return LogEvent(**defaults)


@pytest.fixture
async def db():
    """Temp in-memory SQLite DBSink with schema initialized."""
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    sink = DBSink(engine)
    await sink.startup()
    yield sink
    await engine.dispose()


async def _seed(sink: DBSink, events: list[LogEvent]) -> None:
    await sink.write_requests(events)


async def test_read_overview_all_time(db):
    now = time.time()
    events = [
        _evt(now - 30, status=200, tok_in=100, tok_cached=40, tok_reasoning=10,
             tok_out=50, tps=80.0, ttft_ms=250.0, latency_ms=1000.0, cost=0.01,
             cache_hit=True, cache_savings=0.002),
        _evt(now - 20, status=200, tok_in=200, tok_out=100, tps=40.0, ttft_ms=500.0,
             latency_ms=2000.0, cost=0.02),
        _evt(now - 10, status=500, error_code="api_error", tok_in=50, cost=0.0),
    ]
    await _seed(db, events)
    ov = await db.read_overview(0)  # 0 = all-time
    assert ov["requests"] == 3
    assert ov["errors"] == 1
    assert abs(ov["error_rate"] - round(1 / 3, 4)) < 1e-6
    assert ov["tok_in"] == 350
    assert ov["tok_cached"] == 40
    assert ov["tok_reasoning"] == 10
    assert ov["tok_out"] == 150
    assert ov["cache_hits"] == 1
    assert abs(ov["cache_hit_rate"] - round(1 / 3, 4)) < 1e-6
    assert abs(ov["cost"] - 0.03) < 1e-9
    assert abs(ov["cache_savings"] - 0.002) < 1e-9
    assert ov["tps_avg"] == 60.0  # (80 + 40) / 2
    assert ov["tps_p95"] == 80.0
    assert ov["ttft_p95_ms"] == 500.0
    assert ov["latency_p95_ms"] == 2000.0
    assert ov["window_minutes"] == 0


async def test_read_overview_filtered_by_time(db):
    now = time.time()
    events = [
        _evt(now - 30, tok_in=100, tok_out=50, tps=80.0, cost=0.01),
        _evt(now - 9999, tok_in=999, tok_out=999, cost=99.0),  # outside 60-min window
    ]
    await _seed(db, events)
    ov = await db.read_overview(60)  # last 60 minutes
    assert ov["requests"] == 1
    assert ov["tok_in"] == 100
    assert ov["tok_out"] == 50
    assert ov["cost"] == 0.01


async def test_read_overview_empty(db):
    ov = await db.read_overview(0)
    assert ov["requests"] == 0
    assert ov["errors"] == 0
    assert ov["tok_in"] == 0
    assert ov["tps_avg"] == 0.0
    assert ov["tps_p95"] == 0.0


async def test_read_timeseries_tokens(db):
    now = time.time()
    # Two events 1 hour apart, bucket_seconds=3600
    events = [
        _evt(now - 3000, tok_in=100, tok_cached=40, tok_reasoning=10, tok_out=50),
        _evt(now - 600, tok_in=200, tok_cached=8, tok_reasoning=2, tok_out=7),
    ]
    await _seed(db, events)
    ts = await db.read_timeseries(3600, "tokens", 0)  # all-time
    assert ts["bucket_seconds"] == 3600
    assert ts["metric"] == "tokens"
    assert len(ts["buckets"]) >= 1
    # Both events should fall in the same hourly bucket (within the same hour)
    total_in = sum(b["tok_in"] for b in ts["buckets"])
    total_out = sum(b["tok_out"] for b in ts["buckets"])
    assert total_in == 300
    assert total_out == 57


async def test_read_timeseries_tps(db):
    # Use explicit timestamps within a single hour bucket so the assertion is
    # deterministic regardless of the wall-clock position within the hour.
    base = 3_600_000  # divisible by 3600 -> hour-aligned bucket boundary
    events = [
        _evt(base + 100, tps=80.0, tok_out=50),
        _evt(base + 200, tps=40.0, tok_out=7),
    ]
    await _seed(db, events)
    ts = await db.read_timeseries(3600, "tps", 0)
    assert ts["bucket_seconds"] == 3600
    assert ts["metric"] == "tps"
    # Both in same bucket: avg = (80+40)/2 = 60, p95 (max approximation) = 80
    non_empty = [b for b in ts["buckets"] if b["tps_avg"] > 0]
    assert len(non_empty) == 1
    assert non_empty[0]["tps_avg"] == 60.0
    assert non_empty[0]["tps_p95"] == 80.0  # approximated as max(tps) in bucket


async def test_read_timeseries_unsupported_metric_raises(db):
    now = time.time()
    await _seed(db, [_evt(now - 30, tok_in=10, tok_out=5)])
    with pytest.raises(ValueError):
        await db.read_timeseries(60, "cost", 0)


async def test_read_timeseries_filtered_by_time(db):
    now = time.time()
    events = [
        _evt(now - 600, tok_in=100, tok_out=50),   # within 60 min
        _evt(now - 9999, tok_in=999, tok_out=999),  # outside 60 min
    ]
    await _seed(db, events)
    ts = await db.read_timeseries(60, "tokens", 60)  # 1-min buckets, last 60 min
    total_in = sum(b["tok_in"] for b in ts["buckets"])
    assert total_in == 100
    assert sum(b["tok_out"] for b in ts["buckets"]) == 50


async def test_read_timeseries_zero_fills_sparse_buckets(db):
    """Buckets with no traffic should be zero-filled, not skipped (dense array)."""
    # Use hour-aligned timestamps so bucket boundaries are deterministic:
    # hours 0, 1, and 3 have events; hour 2 is deliberately empty.
    base = 3_600_000  # divisible by 3600
    events = [
        _evt(base + 100, tok_in=100, tok_out=10),         # hour 0 bucket
        _evt(base + 3600 + 100, tok_in=200, tok_out=20),   # hour 1 bucket
        # hour 2 bucket deliberately empty
        _evt(base + 10800 + 100, tok_in=300, tok_out=30),  # hour 3 bucket
    ]
    await _seed(db, events)
    ts = await db.read_timeseries(3600, "tokens", 0)  # all-time, 1h buckets
    buckets = ts["buckets"]
    # The returned series must be dense: no gaps between min and max bucket_t.
    ts_list = [b["t"] for b in buckets]
    assert ts_list == sorted(ts_list)
    assert ts_list == list(range(ts_list[0], ts_list[-1] + 1, 3600))
    # The empty middle bucket must be present with zero values.
    empty_bucket = base + 7200  # hour 2 boundary
    eb = next(b for b in buckets if b["t"] == empty_bucket)
    assert eb["tok_in"] == 0
    assert eb["tok_out"] == 0
    # Non-empty buckets retain their sums.
    assert sum(b["tok_in"] for b in buckets) == 600
    assert sum(b["tok_out"] for b in buckets) == 60


async def test_read_timeseries_empty(db):
    ts = await db.read_timeseries(3600, "tokens", 0)
    assert ts["bucket_seconds"] == 3600
    assert ts["buckets"] == []


async def test_ts_index_created(db):
    """The ts index should exist after startup."""
    async with db.engine.connect() as conn:
        indexes = (await conn.execute(sa.text(
            "PRAGMA index_list('request_logs')"))).all()
    index_names = {r[1] for r in indexes}
    assert "idx_request_logs_ts" in index_names


async def test_ts_index_idempotent(db):
    """Calling startup again should not fail (index already exists)."""
    await db.startup()  # second call
    async with db.engine.connect() as conn:
        indexes = (await conn.execute(sa.text(
            "PRAGMA index_list('request_logs')"))).all()
    index_names = {r[1] for r in indexes}
    assert "idx_request_logs_ts" in index_names


# -- endpoint-level tests (Task 5: app.py routing) -----------------------------

MASTER = "sk-wiwi-master-test"
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


@pytest.fixture
async def app_client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def test_overview_endpoint_all_time(app_client):
    """minutes=0 should return all-time overview via DB."""
    r = await app_client.get("/admin/stats/overview?minutes=0", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["requests"] == 0
    assert body["window_minutes"] == 0


async def test_overview_endpoint_7d(app_client):
    """minutes=10080 should work (7 days, via DB)."""
    r = await app_client.get("/admin/stats/overview?minutes=10080", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["window_minutes"] == 10080


async def test_timeseries_endpoint_7d(app_client):
    """minutes=10080 should return hourly buckets via DB."""
    r = await app_client.get(
        "/admin/stats/timeseries",
        params={"bucket": "minute", "metric": "tps", "minutes": 10080},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bucket_seconds"] == 3600  # 1-hour buckets for 7d
    assert body["metric"] == "tps"


async def test_timeseries_endpoint_all_time(app_client):
    """minutes=0 should return daily buckets via DB."""
    r = await app_client.get(
        "/admin/stats/timeseries",
        params={"bucket": "minute", "metric": "tokens", "minutes": 0},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bucket_seconds"] == 86400  # 1-day buckets for all-time
    assert body["metric"] == "tokens"
