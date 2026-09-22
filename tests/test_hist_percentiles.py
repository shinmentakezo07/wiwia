"""Histogram percentile rollup: merging raw + rolled-up without the old error.

The bug these pin: ``request_rollups`` stored one p95 float per bucket, and the
reader combined it with raw samples by a sample-count-weighted mean. A mean of
quantiles is not the quantile of the union — measured, a window that was half
fast and half slow reported 547 where the true p95 was 1000 (a 45%
under-report).

The fix stores a compact log-scale histogram per bucket, so merging is "add
bin counts then take the percentile". These tests assert the properties that
matter to a consumer:

1. a mixed window is accurate to within a few percent (not 45);
2. an all-raw window is EXACT (no binning error at all);
3. an all-rolled-up window is within the bin resolution;
4. repeated sweeps into one bucket MERGE rather than overwrite;
5. a row written before the column existed still reports a value (fallback);
6. garbage in ``p95_hist`` degrades to the fallback, never crashes.
"""
from __future__ import annotations

import json
import math
import random
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.logging_core import hist
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent


def _p95_true(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, min(len(s) - 1, math.ceil(len(s) * 0.95) - 1))]


async def _sink(tmp_path, name: str) -> tuple[DBSink, object]:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    sink = DBSink(eng)
    await sink.startup()
    return sink, eng


def _evt(ts, request_id, latency_ms=None, tps=None, tok_in=10) -> LogEvent:
    return LogEvent(stream="request", ts=ts, request_id=request_id,
                    surface="chat", key_id="k", model_group="m", provider="p",
                    status=200, cost=0.0, tok_in=tok_in,
                    latency_ms=latency_ms or 0.0, tps=tps or 0.0)


# -- the histogram primitive --------------------------------------------------

def test_histogram_merge_is_exact_and_roundtrips():
    """Adding bin counts == binning the union; encode/decode is lossless."""
    random.seed(0)
    pop = [random.uniform(1, 1000) for _ in range(1000)]
    all_at_once: dict[int, int] = {}
    for v in pop:
        hist.add_value(all_at_once, v)
    left: dict[int, int] = {}
    right: dict[int, int] = {}
    for v in pop[:400]:
        hist.add_value(left, v)
    for v in pop[400:]:
        hist.add_value(right, v)
    assert hist.merge(left, right) == all_at_once
    assert hist.decode(hist.encode(all_at_once)) == all_at_once


def test_histogram_percentile_is_accurate_on_realistic_shapes():
    """The p95 error stays small across the distributions a gateway sees."""
    random.seed(1)
    cases = {
        "uniform tps": [random.uniform(10, 100) for _ in range(500)],
        "uniform latency": [random.uniform(5, 5000) for _ in range(500)],
        "bimodal": ([random.uniform(1, 10) for _ in range(400)]
                    + [random.uniform(900, 1100) for _ in range(400)]),
        "heavy tail": [random.paretovariate(1.5) * 10 for _ in range(500)],
    }
    for label, pop in cases.items():
        h: dict[int, int] = {}
        for v in pop:
            hist.add_value(h, v)
        got, true = hist.percentile(h), _p95_true(pop)
        err = abs(got - true) / true
        assert err < 0.08, f"{label}: p95 off by {err:.1%} ({got} vs {true})"


def test_histogram_ignores_zero_and_negative_samples():
    """A 0-relevance row is 'no sample', matching every other consumer."""
    h: dict[int, int] = {}
    for v in (0.0, -1.0, None):
        hist.add_value(h, v)
    assert h == {}
    assert hist.percentile(h) == 0.0


def test_histogram_decode_tolerates_junk():
    """A blank/legacy/garbled value decodes to {}, never raises."""
    assert hist.decode(None) == {}
    assert hist.decode("") == {}
    assert hist.decode("not json") == {}
    assert hist.decode('{"v": 99, "b": {"1": 1}}') == {}   # wrong version
    assert hist.decode('{"v": 1, "b": "nope"}') == {}
    assert hist.decode('{"v": 1, "b": {"x": 1}}') == {}     # bad bin key


# -- the overview surface -----------------------------------------------------

async def test_all_raw_window_is_exact(tmp_path):
    """No bins involved: raw samples alone must give the true p95."""
    sink, eng = await _sink(tmp_path, "raw.db")
    try:
        now = time.time()
        # 20 samples; the true p95 by nearest rank is the 19th.
        vals = [float(i) for i in range(1, 21)]
        await sink.write_requests(
            [_evt(now - i, f"r{i}", latency_ms=v) for i, v in enumerate(vals)])
        ov = await sink.read_overview(0)
        assert ov["latency_p95_ms"] == _p95_true(vals)
        assert ov["requests"] == 20
    finally:
        await eng.dispose()


async def test_mixed_window_beats_the_old_weighted_mean(tmp_path):
    """Half fast (raw) + half slow (rolled up) must land near the true p95.

    The old weighted mean reported 547 where the truth was 1000. The
    histogram must be much closer.
    """
    sink, eng = await _sink(tmp_path, "mixed.db")
    try:
        now = time.time()
        random.seed(3)
        slow = [random.uniform(900, 1100) for _ in range(100)]
        fast = [random.uniform(1, 10) for _ in range(100)]
        # Slow rows are old (but kept raw via a far cutoff), fast are recent.
        events = ([_evt(now - 100000 - i, f"s{i}", latency_ms=v)
                   for i, v in enumerate(slow)]
                  + [_evt(now - i, f"f{i}", latency_ms=v)
                     for i, v in enumerate(fast)])
        await sink.write_requests(events)

        # Roll up only the slow half (they are >1000s old).
        await sink.rollup_and_prune(cutoff_ts=now - 50000)

        ov = await sink.read_overview(0)
        true = _p95_true(slow + fast)
        got = ov["latency_p95_ms"]
        assert abs(got - true) / true < 0.08, (
            f"mixed p95 off by {abs(got-true)/true:.1%}: {got} vs {true}")
        assert ov["requests"] == 200, "the rollup must preserve the totals"
    finally:
        await eng.dispose()


async def test_repeated_sweeps_merge_instead_of_overwrite(tmp_path):
    """Two sweeps into the same hour must accumulate, not replace."""
    sink, eng = await _sink(tmp_path, "merge.db")
    try:
        now = time.time()
        first = [_evt(now - i, f"a{i}", latency_ms=10.0) for i in range(50)]
        second = [_evt(now - i, f"b{i}", latency_ms=5000.0) for i in range(50)]
        await sink.write_requests(first)
        await sink.rollup_and_prune(cutoff_ts=now + 1)
        await sink.write_requests(second)
        await sink.rollup_and_prune(cutoff_ts=now + 1)

        async with eng.connect() as c:
            rows = (await c.execute(text(
                "SELECT requests FROM request_rollups"))).all()
        assert len(rows) == 1, "both sweeps must land in one bucket"
        assert rows[0][0] == 100, f"histogram overwrote instead of merging: {rows}"

        ov = await sink.read_overview(0)
        assert ov["latency_p95_ms"] > 4000, (
            "half the merged samples are slow, so the p95 must be too")
    finally:
        await eng.dispose()


async def test_legacy_row_without_histogram_still_reports(tmp_path):
    """A pre-existing database keeps answering from the scalar columns."""
    sink, eng = await _sink(tmp_path, "legacy.db")
    try:
        now = time.time()
        async with eng.begin() as c:
            await c.execute(text("""
                INSERT INTO request_rollups
                  (bucket_ts,key_id,model_group,provider,serving_model,requests,
                   tps_sum,tps_count,tps_p95,ttft_p95_ms,latency_p95_ms,p95_hist)
                VALUES (:b,'k','m','p','mid',10, 0,0, 0,0,1234.0, '')"""),
                {"b": now - 3600})
        ov = await sink.read_overview(0)
        assert ov["requests"] == 10
        assert ov["latency_p95_ms"] == 1234.0, "must fall back to the scalar"
    finally:
        await eng.dispose()


async def test_garbled_histogram_falls_back_without_crashing(tmp_path):
    sink, eng = await _sink(tmp_path, "garbled.db")
    try:
        now = time.time()
        async with eng.begin() as c:
            await c.execute(text("""
                INSERT INTO request_rollups
                  (bucket_ts,key_id,model_group,provider,serving_model,requests,
                   tps_sum,tps_count,tps_p95,ttft_p95_ms,latency_p95_ms,p95_hist)
                VALUES (:b,'k','m','p','mid',5, 0,0, 0,0,777.0, 'not json')"""),
                {"b": now - 3600})
        ov = await sink.read_overview(0)
        assert ov["latency_p95_ms"] == 777.0
    finally:
        await eng.dispose()


async def test_migration_widens_an_existing_rollup_table(tmp_path):
    """A table created before ``p95_hist`` gains the column in place."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mig.db")
    try:
        # Simulate the old schema: same table, without p95_hist.
        async with eng.begin() as c:
            await c.execute(text("""
                CREATE TABLE request_rollups (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  bucket_ts REAL NOT NULL, key_id TEXT DEFAULT '',
                  model_group TEXT DEFAULT '', provider TEXT DEFAULT '',
                  serving_model TEXT DEFAULT '', requests INTEGER DEFAULT 0,
                  tps_sum REAL DEFAULT 0, tps_count INTEGER DEFAULT 0,
                  tps_p95 REAL DEFAULT 0, ttft_p95_ms REAL DEFAULT 0,
                  latency_p95_ms REAL DEFAULT 0
                )"""))
        sink = DBSink(eng)
        await sink.startup()   # must not raise
        async with eng.connect() as c:
            cols = {r[1] for r in (await c.execute(
                text("PRAGMA table_info(request_rollups)"))).all()}
        assert "p95_hist" in cols
    finally:
        await eng.dispose()


async def test_histogram_column_survives_the_upsert(tmp_path):
    """The written row actually carries a decodable histogram."""
    sink, eng = await _sink(tmp_path, "stored.db")
    try:
        now = time.time()
        await sink.write_requests(
            [_evt(now - i, f"r{i}", latency_ms=float(i + 1)) for i in range(30)])
        await sink.rollup_and_prune(cutoff_ts=now + 1)
        async with eng.connect() as c:
            stored = (await c.execute(text(
                "SELECT p95_hist FROM request_rollups"))).scalar()
        assert stored, "the rollup must persist a histogram"
        decoded = json.loads(stored)
        assert "latency_ms" in decoded
        assert hist.decode(decoded["latency_ms"]), "and it must decode"
    finally:
        await eng.dispose()


# -- keeping the metric lists in sync -----------------------------------------
#
# Adding a percentile metric means touching several places (the series tuple,
# the legacy column tuple, both DDLs, the migration list). These tests fail
# loudly if one is missed, which is the failure mode the old
# "Not done: a new metric needs a column here too" note described.

def test_rollup_metric_lists_stay_in_sync():
    """The series list, the legacy column list and the written columns agree."""
    from wiwi.logging_core import db_sink as d
    assert len(d._HIST_SERIES) == len(d._LEGACY_P95_COLS), (
        "each histogram series needs exactly one legacy scalar column")
    for col in d._LEGACY_P95_COLS:
        assert col in d._ROLLUP_COLS, f"{col} missing from the written columns"
    assert "p95_hist" in d._ROLLUP_COLS
    # Every series must be a real column on request_logs, or the writer reads
    # a nonexistent attribute.
    for series in d._HIST_SERIES:
        assert series in d._COLS, f"{series} is not a request_logs column"


def test_both_ddls_and_migration_carry_every_rollup_column():
    """SQLite/Postgres DDL and the migration agree with ``_ROLLUP_COLS``.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op on an existing table, so a new
    column must appear in the DDL (for fresh databases) AND in
    ``_ROLLUP_MIGRATE_COLUMNS`` (for existing ones). Miss either and one class
    of install silently lacks the column.

    Only columns added *after* the original schema need a migration entry —
    the baseline columns are already present in every deployed database.
    """
    from wiwi.logging_core import db_sink as d
    migrated = {name for name, _ in d._ROLLUP_MIGRATE_COLUMNS}
    baseline = (set(d._ROLLUP_KEY_COLS) | set(d._ROLLUP_ADDITIVE)
                | set(d._LEGACY_P95_COLS))
    for col in d._ROLLUP_COLS:
        assert col in d._ROLLUP_DDL_SQLITE, f"{col} absent from the SQLite DDL"
        assert col in d._ROLLUP_DDL_PG, f"{col} absent from the Postgres DDL"
        if col not in baseline:
            assert col in migrated, (
                f"{col} was added after the first release but is not in "
                "_ROLLUP_MIGRATE_COLUMNS, so an existing database would never "
                "gain it")
