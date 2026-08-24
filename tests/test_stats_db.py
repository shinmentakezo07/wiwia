"""Tests for DB-backed stats and bucket sizing helpers."""

from __future__ import annotations

import time

import pytest
import sqlalchemy.ext.asyncio as saa

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
