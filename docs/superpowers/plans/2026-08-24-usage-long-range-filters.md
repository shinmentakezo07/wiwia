# Usage 7d/30d/All-Time Range Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7-day, 30-day, and all-time range options to the Usage page by wiring the stats endpoints to the SQLite DB for long ranges while keeping the in-memory ring buffer for short ranges.

**Architecture:** The stats endpoints (`/admin/stats/overview`, `/admin/stats/timeseries`) currently read from a 500-event in-memory ring buffer and clamp `minutes` to 1440. We add DB-backed aggregate methods to `DBSink`, a bucket-sizing helper to `stats.py`, and routing logic in `app.py` that sends ranges > 1440 minutes (or 0 = all-time) to the DB. The frontend gets three new dropdown options and adapts axis formatting for multi-day ranges.

**Tech Stack:** Python 3.11, SQLAlchemy async + aiosqlite, pytest + pytest-asyncio, React 19 + TypeScript + Vite + Tailwind 4, recharts.

## Global Constraints

- Ruff only: `line-length = 100`, target `py311`. Pydantic v2 for config; plain dataclasses for IR/streaming hot paths.
- Async throughout (`httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths). Never `print` from library code — use `structlog`.
- Tests: pytest + pytest-asyncio with `asyncio_mode = "auto"` — bare `async def test_…`, no `@pytest.mark.asyncio`.
- Commit style: imperative present tense, capitalized, no prefix tags (e.g. `Add DB-backed stats for long ranges`).
- Never add dialect- or provider-specific branches in `core/`, `router/`, or `auth/`.
- Never commit `wiwi.yaml` or `wiwi.db`.
- Run `.venv/bin/python -m pytest tests/ -q` and `.venv/bin/ruff check wiwi/ tests/` before committing.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `wiwi/server/stats.py` | Add `bucket_size_for(minutes)` helper. Existing in-memory `overview()`/`timeseries()` unchanged. |
| `wiwi/logging_core/db_sink.py` | Add `read_overview(minutes)` and `read_timeseries(bucket_seconds, metric, minutes)` methods + `idx_request_logs_ts` index in startup. |
| `wiwi/server/app.py` | Raise `minutes` cap to 43200, handle `minutes=0` (all-time), route long ranges to DB sink. |
| `web/src/pages/Usage.tsx` | Add 7d/30d/all dropdown options, handle "all" range state, multi-day axis formatting. |
| `web/src/api/client.ts` | Accept `number` for minutes (0 = all-time); no type change needed since it's already `number`. |
| `tests/test_stats_db.py` | Table-driven DB-backed stats tests. |

---

### Task 1: Add `bucket_size_for()` helper to `stats.py`

**Files:**
- Modify: `wiwi/server/stats.py` (after the `VALID_METRICS` constant, around line 12)
- Test: `tests/test_stats_db.py` (create new file)

**Interfaces:**
- Produces: `bucket_size_for(minutes: int) -> int` — returns bucket size in seconds. `minutes == 0` means all-time (returns 86400). Otherwise: ≤1440 → 60, ≤10080 → 3600, ≤43200 → 21600, >43200 → 86400.

- [ ] **Step 1: Write the failing test**

Create `tests/test_stats_db.py`:

```python
"""Tests for DB-backed stats and bucket sizing helpers."""

from __future__ import annotations

import pytest

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'bucket_size_for'`

- [ ] **Step 3: Write minimal implementation**

Add to `wiwi/server/stats.py`, after the `VALID_METRICS` line (line 12):

```python
def bucket_size_for(minutes: int) -> int:
    """Return bucket size in seconds appropriate for the time range.

    minutes == 0 means all-time (uses 1-day buckets).
    """
    if minutes == 0:
        return 86400
    if minutes <= 1440:
        return 60
    if minutes <= 10080:
        return 3600
    if minutes <= 43200:
        return 21600
    return 86400
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v`
Expected: PASS (11 parametrized cases)

- [ ] **Step 5: Run ruff**

Run: `.venv/bin/ruff check wiwi/server/stats.py tests/test_stats_db.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add wiwi/server/stats.py tests/test_stats_db.py
git commit -m "Add bucket_size_for helper for long-range stats"
```

---

### Task 2: Add `read_overview()` to `DBSink`

**Files:**
- Modify: `wiwi/logging_core/db_sink.py` (add method to `DBSink` class, after `read_requests` around line 130)
- Test: `tests/test_stats_db.py` (extend)

**Interfaces:**
- Consumes: `DBSink.engine` (SQLAlchemy async engine), `stats._p95()` from `wiwi.server.stats`
- Produces: `async def read_overview(self, minutes: int) -> dict` — returns the same dict shape as `stats.overview()`. `minutes == 0` means all-time (no ts cutoff).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stats_db.py`:

```python
import time

import sqlalchemy as sa

from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent


def _evt(ts: float, **kw) -> LogEvent:
    defaults = {"stream": "request", "ts": ts}
    defaults.update(kw)
    return LogEvent(**defaults)


@pytest.fixture
async def db():
    """Temp in-memory SQLite DBSink with schema initialized."""
    engine = sa.create_async_engine("sqlite+aiosqlite:///:memory:")
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k read_overview`
Expected: FAIL with `AttributeError: 'DBSink' object has no attribute 'read_overview'`

- [ ] **Step 3: Write minimal implementation**

Add to `wiwi/logging_core/db_sink.py`, at the top add imports:

```python
import time

from wiwi.server.stats import _p95
```

Add method to `DBSink` class, after `read_requests` (after line 130, at the end of the class):

```python
    async def read_overview(self, minutes: int) -> dict:
        """DB-backed overview with the same dict shape as stats.overview().

        minutes == 0 means all-time (no ts cutoff).
        """
        now = time.time()
        cutoff = now - minutes * 60 if minutes > 0 else 0.0
        where_clause = "WHERE ts >= :cutoff" if minutes > 0 else ""
        params: dict = {}
        if minutes > 0:
            params["cutoff"] = cutoff

        async with self.engine.connect() as conn:
            row = (await conn.execute(sa.text(f"""
                SELECT COUNT(*) AS requests,
                       SUM(CASE WHEN status >= 400 OR error_code != '' THEN 1 ELSE 0 END) AS errors,
                       COALESCE(SUM(tok_in), 0) AS tok_in,
                       COALESCE(SUM(tok_cached), 0) AS tok_cached,
                       COALESCE(SUM(tok_reasoning), 0) AS tok_reasoning,
                       COALESCE(SUM(tok_out), 0) AS tok_out,
                       SUM(CASE WHEN cache_hit = 1 OR tok_cached > 0 THEN 1 ELSE 0 END) AS cache_hits,
                       COALESCE(SUM(cost), 0) AS cost,
                       COALESCE(SUM(cache_savings), 0) AS cache_savings
                FROM request_logs
                {where_clause}
            """), params)).one()

            requests = row.requests or 0
            errors = row.errors or 0
            cache_hits = row.cache_hits or 0

            # p95 from bounded sample (max 5000 rows) to avoid full-table scan
            sample_query = "SELECT tps FROM request_logs"
            sample_where = "WHERE tps > 0"
            if minutes > 0:
                sample_where += " AND ts >= :cutoff"
            sample_query = f"{sample_query} {sample_where} ORDER BY id DESC LIMIT 5000"
            tps_rows = (await conn.execute(sa.text(sample_query), params)).all()
            tps_values = [r[0] for r in tps_rows]

            ttft_query = "SELECT ttft_ms FROM request_logs"
            ttft_where = "WHERE ttft_ms > 0"
            if minutes > 0:
                ttft_where += " AND ts >= :cutoff"
            ttft_query = f"{ttft_query} {ttft_where} ORDER BY id DESC LIMIT 5000"
            ttft_rows = (await conn.execute(sa.text(ttft_query), params)).all()
            ttft_values = [r[0] for r in ttft_rows]

            lat_query = "SELECT latency_ms FROM request_logs"
            lat_where = "WHERE latency_ms > 0"
            if minutes > 0:
                lat_where += " AND ts >= :cutoff"
            lat_query = f"{lat_query} {lat_where} ORDER BY id DESC LIMIT 5000"
            lat_rows = (await conn.execute(sa.text(lat_query), params)).all()
            lat_values = [r[0] for r in lat_rows]

        minutes_norm = max(minutes, 1e-9)
        return {
            "window_minutes": minutes,
            "generated_at": now,
            "requests": requests,
            "errors": errors,
            "error_rate": round(errors / requests, 4) if requests else 0.0,
            "requests_per_minute": round(requests / minutes_norm, 2) if minutes > 0 else 0.0,
            "tok_in": row.tok_in or 0,
            "tok_cached": row.tok_cached or 0,
            "tok_reasoning": row.tok_reasoning or 0,
            "tok_out": row.tok_out or 0,
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / requests, 4) if requests else 0.0,
            "tps_avg": round(sum(tps_values) / len(tps_values), 2) if tps_values else 0.0,
            "tps_p95": round(_p95(tps_values), 2),
            "ttft_p95_ms": round(_p95(ttft_values), 1),
            "latency_p95_ms": round(_p95(lat_values), 1),
            "cost": round(row.cost or 0, 6),
            "cache_savings": round(row.cache_savings or 0, 6),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k read_overview`
Expected: PASS (4 tests)

- [ ] **Step 5: Run ruff**

Run: `.venv/bin/ruff check wiwi/logging_core/db_sink.py tests/test_stats_db.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add wiwi/logging_core/db_sink.py tests/test_stats_db.py
git commit -m "Add DB-backed read_overview for long-range stats"
```

---

### Task 3: Add `read_timeseries()` to `DBSink`

**Files:**
- Modify: `wiwi/logging_core/db_sink.py` (add method after `read_overview`)
- Test: `tests/test_stats_db.py` (extend)

**Interfaces:**
- Consumes: `DBSink.engine`, `stats._p95()` from `wiwi.server.stats`
- Produces: `async def read_timeseries(self, bucket_seconds: int, metric: str, minutes: int) -> dict` — returns `{"bucket_seconds": int, "metric": str, "buckets": list[dict]}`. Same shape as `stats.timeseries()`. `minutes == 0` means all-time.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stats_db.py`:

```python
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
    now = time.time()
    events = [
        _evt(now - 3000, tps=80.0, tok_out=50),
        _evt(now - 600, tps=40.0, tok_out=7),
    ]
    await _seed(db, events)
    ts = await db.read_timeseries(3600, "tps", 0)
    assert ts["bucket_seconds"] == 3600
    assert ts["metric"] == "tps"
    # Both in same bucket: avg = (80+40)/2 = 60, p95 (max approximation) = 80
    non_empty = [b for b in ts["buckets"] if b["tps_avg"] > 0]
    assert len(non_empty) == 1
    assert non_empty[0]["tps_avg"] == 60.0


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


async def test_read_timeseries_empty(db):
    ts = await db.read_timeseries(3600, "tokens", 0)
    assert ts["bucket_seconds"] == 3600
    assert ts["buckets"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k read_timeseries`
Expected: FAIL with `AttributeError: 'DBSink' object has no attribute 'read_timeseries'`

- [ ] **Step 3: Write minimal implementation**

Add method to `DBSink` class in `wiwi/logging_core/db_sink.py`, after `read_overview`:

```python
    async def read_timeseries(self, bucket_seconds: int, metric: str,
                              minutes: int) -> dict:
        """DB-backed timeseries with the same dict shape as stats.timeseries().

        minutes == 0 means all-time (no ts cutoff).
        For metric="tps", tps_p95 is approximated as max(tps) in the bucket.
        """
        if metric not in ("tokens", "tps"):
            raise ValueError(f"unsupported metric {metric!r}")

        now = time.time()
        cutoff = now - minutes * 60 if minutes > 0 else 0.0
        params: dict = {}
        where_ts = ""
        if minutes > 0:
            params["cutoff"] = cutoff
            where_ts = "WHERE ts >= :cutoff"

        # Align bucket start to the bucket grid
        bucket_start = int(now // bucket_seconds) * bucket_seconds
        if minutes > 0:
            n_buckets = max(1, minutes * 60 // bucket_seconds)
            bucket_start = bucket_start - (n_buckets - 1) * bucket_seconds

        async with self.engine.connect() as conn:
            rows = (await conn.execute(sa.text(f"""
                SELECT :bucket_start + (CAST((ts - :bucket_start) / :bs AS INTEGER)) * :bs AS bucket_t,
                       SUM(tok_in) AS tok_in,
                       SUM(tok_cached) AS tok_cached,
                       SUM(tok_reasoning) AS tok_reasoning,
                       SUM(tok_out) AS tok_out,
                       SUM(CASE WHEN tps > 0 THEN tps ELSE 0 END) AS tps_sum,
                       COUNT(CASE WHEN tps > 0 THEN 1 END) AS tps_count,
                       MAX(CASE WHEN tps > 0 THEN tps ELSE 0 END) AS tps_max
                FROM request_logs
                {where_ts}
                GROUP BY bucket_t
                ORDER BY bucket_t
            """), {**params, "bucket_start": bucket_start, "bs": bucket_seconds})).all()

        if metric == "tokens":
            buckets = [
                {"t": r.bucket_t, "tok_in": r.tok_in or 0, "tok_cached": r.tok_cached or 0,
                 "tok_reasoning": r.tok_reasoning or 0, "tok_out": r.tok_out or 0}
                for r in rows
            ]
        else:
            buckets = [
                {"t": r.bucket_t,
                 "tps_avg": round(r.tps_sum / r.tps_count, 2) if r.tps_count else 0.0,
                 "tps_p95": round(r.tps_max or 0.0, 2)}
                for r in rows
            ]
        return {"bucket_seconds": bucket_seconds, "metric": metric, "buckets": buckets}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k read_timeseries`
Expected: PASS (4 tests)

- [ ] **Step 5: Run ruff**

Run: `.venv/bin/ruff check wiwi/logging_core/db_sink.py tests/test_stats_db.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add wiwi/logging_core/db_sink.py tests/test_stats_db.py
git commit -m "Add DB-backed read_timeseries for long-range stats"
```

---

### Task 4: Add `ts` index to `DBSink.startup()`

**Files:**
- Modify: `wiwi/logging_core/db_sink.py` (in `_migrate` method, around line 65)
- Test: `tests/test_stats_db.py` (extend)

**Interfaces:**
- No new interface; modifies existing `startup()` to ensure `idx_request_logs_ts` exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stats_db.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k ts_index`
Expected: FAIL — `idx_request_logs_ts` not found in index_names

- [ ] **Step 3: Write minimal implementation**

In `wiwi/logging_core/db_sink.py`, in the `_migrate` method, add the index creation after the column-add loop (before the method ends):

```python
            # Index for fast time-range queries on long-range stats
            await conn.execute(sa.text(
                "CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(ts)"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k ts_index`
Expected: PASS (2 tests)

- [ ] **Step 5: Run ruff**

Run: `.venv/bin/ruff check wiwi/logging_core/db_sink.py tests/test_stats_db.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add wiwi/logging_core/db_sink.py tests/test_stats_db.py
git commit -m "Add ts index to request_logs for long-range stats queries"
```

---

### Task 5: Wire DB routing in `app.py` stats endpoints

**Files:**
- Modify: `wiwi/server/app.py` (the `admin_stats_overview` and `admin_stats_timeseries` endpoint functions)
- Test: `tests/test_stats_db.py` (extend with endpoint-level tests)

**Interfaces:**
- Consumes: `DBSink.read_overview(minutes)`, `DBSink.read_timeseries(bucket_seconds, metric, minutes)`, `stats.bucket_size_for(minutes)`
- Produces: Updated `/admin/stats/overview` and `/admin/stats/timeseries` endpoints that accept `minutes=0` (all-time) and `minutes` up to 43200, routing to DB when `minutes > 1440` or `minutes == 0`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stats_db.py`:

```python
import httpx
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
                               keys=[KeyDef(label="a", key="sk-test-key-123")])],
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k "endpoint"`
Expected: FAIL — `minutes=0` is clamped to 1 by `max(1, min(minutes, 1440))`, and `minutes=10080` is clamped to 1440. `bucket_seconds` will be 60, not 3600/86400.

- [ ] **Step 3: Write minimal implementation**

In `wiwi/server/app.py`, add import at the top of the file (near the other `from wiwi.server.stats` import, which already exists as `stats_mod`):

The file already has `from wiwi.server import stats as stats_mod` (or similar). Verify the import and add `bucket_size_for` access via `stats_mod.bucket_size_for`.

Replace the `admin_stats_overview` function:

```python
    @app.get("/admin/stats/overview")
    async def admin_stats_overview(request: Request, minutes: int = 60):
        resp = _require_admin(request)
        if resp:
            return resp
        minutes = max(0, min(minutes, 43200))
        sink = state.logs.db_sink
        if sink is not None and (minutes == 0 or minutes > 1440):
            return ORJSONResponse(await sink.read_overview(minutes))
        minutes_ring = minutes if minutes > 0 else 1440
        return ORJSONResponse(stats_mod.overview(_request_events(), minutes_ring))
```

Replace the `admin_stats_timeseries` function:

```python
    @app.get("/admin/stats/timeseries")
    async def admin_stats_timeseries(request: Request, bucket: str = "minute",
                                     metric: str = "tokens", minutes: int = 60):
        resp = _require_admin(request)
        if resp:
            return resp
        try:
            minutes = max(0, min(minutes, 43200))
            sink = state.logs.db_sink
            if sink is not None and (minutes == 0 or minutes > 1440):
                bs = stats_mod.bucket_size_for(minutes)
                return ORJSONResponse(
                    await sink.read_timeseries(bs, metric, minutes))
            minutes_ring = minutes if minutes > 0 else 1440
            return ORJSONResponse(
                stats_mod.timeseries(_request_events(), bucket, metric, minutes_ring))
        except ValueError as e:
            return _err(400, "invalid_request_error", str(e), request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stats_db.py -v -k "endpoint"`
Expected: PASS (4 tests)

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All tests pass (existing tests unaffected — they use `minutes <= 1440` which still hits the in-memory path)

- [ ] **Step 6: Run ruff**

Run: `.venv/bin/ruff check wiwi/ tests/`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add wiwi/server/app.py tests/test_stats_db.py
git commit -m "Route long-range stats to DB, support all-time queries"
```

---

### Task 6: Add 7d/30d/all options to Usage page dropdown

**Files:**
- Modify: `web/src/pages/Usage.tsx` (lines 59-63 `RANGE_OPTIONS`, line 237 `useState(60)`, lines 260-263 `allLogs` cutoff, line 580 axis formatter)

**Interfaces:**
- No new interfaces; modifies existing `range` state, `RANGE_OPTIONS`, cutoff logic, and chart axis formatter.

- [ ] **Step 1: Update `RANGE_OPTIONS`**

In `web/src/pages/Usage.tsx`, replace the `RANGE_OPTIONS` constant (lines 59-63):

```typescript
const RANGE_OPTIONS = [
  { value: "15", label: "Last 15 min" },
  { value: "60", label: "Last hour" },
  { value: "360", label: "Last 6 hours" },
  { value: "1440", label: "Last 24 hours" },
  { value: "10080", label: "Last 7 days" },
  { value: "43200", label: "Last 30 days" },
  { value: "0", label: "All time" },
];
```

Note: `"0"` is the value for all-time. The backend treats `minutes=0` as all-time.

- [ ] **Step 2: Update range state type**

Change the `range` state (line 237) — it's already `number`, and `0` is a valid number for all-time. No type change needed. The default stays `60` (last hour).

- [ ] **Step 3: Update `allLogs` cutoff logic**

Replace the `allLogs` useMemo (lines 260-263):

```typescript
  const allLogs = useMemo(() => {
    const all = logsQuery.data?.logs ?? [];
    if (range === 0) return all;  // all-time: no cutoff
    const cutoff = Math.floor(Date.now() / 1000) - range * 60;
    return all.filter((l) => l.ts >= cutoff);
  }, [logsQuery.data, range]);
```

- [ ] **Step 4: Update the TPS chart X-axis formatter**

Replace the `tickFormatter` on line 580:

```typescript
                  tickFormatter={(t: number) =>
                    range === 0 || range > 1440 ? fmtDateTime(t) : fmtTime(t)
                  }
```

This uses `fmtDateTime` (already imported) for multi-day ranges to show date + time, and `fmtTime` (time only) for short ranges.

- [ ] **Step 5: Build the frontend**

Run: `cd web && bun run build`
Expected: Build succeeds with no TypeScript errors, output written to `wiwi/server/static/`

- [ ] **Step 6: Verify in the running panel**

Start the gateway with `wiwi --config wiwi.yaml`, open the admin UI, navigate to Usage, and verify:
- The dropdown now shows 7 options (15 min through All time)
- Selecting "Last 7 days" updates the stat cards and charts
- The TPS chart X-axis shows date + time labels for multi-day ranges
- Selecting "All time" works without errors

- [ ] **Step 7: Commit**

```bash
git add web/src/pages/Usage.tsx wiwi/server/static/
git commit -m "Add 7d/30d/all-time range options to Usage page"
```

---

## Self-Review

**1. Spec coverage:**
- DB-backed `read_overview()` → Task 2 ✓
- DB-backed `read_timeseries()` → Task 3 ✓
- `bucket_size_for()` helper → Task 1 ✓
- `idx_request_logs_ts` index → Task 4 ✓
- Raise cap to 43200 + route long ranges to DB + handle all-time → Task 5 ✓
- `RANGE_OPTIONS` with 7d/30d/all → Task 6 ✓
- Axis label formatting for multi-day → Task 6 ✓
- `api/client.ts` change → Not needed; `minutes` is already `number`, and `0` is a valid int the backend accepts. The existing `getOverview(minutes: number)` and `getTimeseries(metric, minutes: number)` work as-is. ✓
- Tests for all backend pieces → Tasks 1-5 ✓

**2. Placeholder scan:** No TBD/TODO/vague steps. All code blocks contain full implementations.

**3. Type consistency:** `read_overview(minutes: int) -> dict`, `read_timeseries(bucket_seconds: int, metric: str, minutes: int) -> dict`, `bucket_size_for(minutes: int) -> int` — signatures match across all tasks and the spec. Frontend uses `range === 0` for all-time, backend uses `minutes == 0` for all-time — consistent. ✓
