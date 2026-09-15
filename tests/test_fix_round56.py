"""Regression tests for the request-log rollup + cap (log_max_rows).

The property under test: bounding raw ``request_logs`` rows must not change
any number the dashboard reports. Aggregates move into ``request_rollups``
before the raw rows are deleted, so totals, token counts, cost and cache
statistics survive — only per-request detail is dropped.
"""
from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.logging_core.db_sink import DBSink


def _row(i: int, now: float, groups: int = 3) -> dict:
    """One request_logs row; group index varies so the rollup has dimensions."""
    g = i % groups
    return {
        "ts": now - (i * 3600),  # one per hour going back
        "request_id": f"r{i}", "key_id": f"k{g}", "key_alias": f"a{g}",
        "model_group": f"m{g}", "provider": f"p{g}", "provider_key_label": "L",
        "surface": "chat", "status": 200 if i % 7 else 500, "error_code": "",
        "tok_in": 100 + g, "tok_out": 50 + g, "tok_cached": 10,
        "tok_reasoning": 5, "tok_cache_creation": 2, "cost": 0.001,
        "cache_hit": 1 if i % 4 == 0 else 0, "cache_savings": 0.0001,
        "tps": 40.0 + g, "ttft_ms": 20.0 + g, "latency_ms": 100.0 + g,
        "was_stream": 1, "response_cache_hit": 0, "usage_estimated": 0,
    }


async def _seed(sink: DBSink, engine, n: int, now: float) -> None:
    rows = [_row(i, now) for i in range(n)]
    cols = ", ".join(rows[0].keys())
    ph = ", ".join(f":{k}" for k in rows[0])
    async with engine.begin() as conn:
        for i in range(0, len(rows), 1000):
            await conn.execute(
                text(f"INSERT INTO request_logs ({cols}) VALUES ({ph})"),
                rows[i:i + 1000])


async def _count(engine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar() or 0


async def test_cap_rolls_up_before_deleting_and_preserves_every_aggregate(tmp_path):
    """The whole point: cap the raw table without changing a reported number."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cap.db")
    sink = DBSink(eng)
    await sink.startup()
    try:
        now = time.time()
        await _seed(sink, eng, 600, now)

        before = await sink.read_overview(0)
        before_ts = await sink.read_timeseries(86400, "tokens", 0)
        assert before["requests"] == 600

        deleted = await sink.enforce_log_cap(100)
        assert deleted == 500, "the cap must delete exactly the rows beyond it"
        assert await _count(eng, "request_logs") == 100
        assert await _count(eng, "request_rollups") > 0, (
            "the deleted rows must have been rolled up, not dropped")

        after = await sink.read_overview(0)
        after_ts = await sink.read_timeseries(86400, "tokens", 0)

        # Every field the dashboard reads must be identical.
        for field in ("requests", "errors", "tok_in", "tok_cached", "tok_out",
                      "tok_reasoning", "tok_cache_creation", "cost",
                      "cache_savings", "cache_hits", "estimated_requests"):
            assert after[field] == before[field], (
                f"{field} changed across the rollup: "
                f"{before[field]} -> {after[field]}")
        assert (sum(b["tok_in"] for b in after_ts["buckets"])
                == sum(b["tok_in"] for b in before_ts["buckets"]))
    finally:
        await eng.dispose()


async def test_cap_is_idempotent(tmp_path):
    """Re-running the sweep must not double-count rolled-up rows."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/idem.db")
    sink = DBSink(eng)
    await sink.startup()
    try:
        now = time.time()
        await _seed(sink, eng, 300, now)
        await sink.enforce_log_cap(100)
        first = await sink.read_overview(0)
        rollups_after_first = await _count(eng, "request_rollups")

        for _ in range(3):
            assert await sink.enforce_log_cap(100) == 0, (
                "a table already under the cap must be a no-op")
        again = await sink.read_overview(0)

        assert again["requests"] == first["requests"]
        assert again["tok_in"] == first["tok_in"]
        assert await _count(eng, "request_rollups") == rollups_after_first
    finally:
        await eng.dispose()


async def test_age_prune_preserves_aggregates(tmp_path):
    """Retention by age must roll up too, not just the row cap."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/age.db")
    sink = DBSink(eng)
    await sink.startup()
    try:
        now = time.time()
        await _seed(sink, eng, 400, now)  # 400 hours ≈ 16.6 days
        before = await sink.read_overview(0)

        deleted = await sink.prune_old_requests(7)
        assert deleted > 0, "rows older than the retention window must be pruned"
        after = await sink.read_overview(0)

        assert after["requests"] == before["requests"]
        assert after["tok_in"] == before["tok_in"]
        assert abs(after["cost"] - before["cost"]) < 1e-9
    finally:
        await eng.dispose()


async def test_zero_disables_the_cap_and_retention(tmp_path):
    """0 means 'unlimited' for the cap and 'keep forever' for retention."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/off.db")
    sink = DBSink(eng)
    await sink.startup()
    try:
        await _seed(sink, eng, 50, time.time())
        assert await sink.enforce_log_cap(0) == 0
        assert await sink.prune_old_requests(0) == 0
        assert await _count(eng, "request_logs") == 50
        assert await _count(eng, "request_rollups") == 0
    finally:
        await eng.dispose()


async def test_rollup_overview_respects_the_time_window(tmp_path):
    """A window narrower than the rolled-up span must not pull in old buckets."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/win.db")
    sink = DBSink(eng)
    await sink.startup()
    try:
        now = time.time()
        await _seed(sink, eng, 300, now)  # hourly for 300h
        await sink.enforce_log_cap(50)

        recent = await sink.read_overview(60)      # last hour
        everything = await sink.read_overview(0)   # all time
        assert everything["requests"] == 300
        # The newest 50 rows are the most recent 50 hours, so a 1-hour window
        # sees a small subset — never the whole rolled-up history.
        assert recent["requests"] < everything["requests"]
    finally:
        await eng.dispose()


async def test_key_scoped_reads_include_rolled_up_rows(tmp_path):
    """A per-user read must still see their rolled-up traffic."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/scope.db")
    sink = DBSink(eng)
    await sink.startup()
    try:
        now = time.time()
        await _seed(sink, eng, 300, now)  # 3 key groups, 100 rows each
        before = await sink.read_overview(0, key_ids=["k1"])
        await sink.enforce_log_cap(30)
        after = await sink.read_overview(0, key_ids=["k1"])
        assert after["requests"] == before["requests"] == 100
        assert after["tok_in"] == before["tok_in"]
    finally:
        await eng.dispose()
