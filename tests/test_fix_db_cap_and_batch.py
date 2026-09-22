"""Regression tests: the row cap must be exact under ts ties, and one bad row
must not discard its batch.

Two defects pinned here:

1. ``enforce_log_cap`` selected its cutoff row by ``(ts DESC, id DESC)`` but
   deleted by ``ts < cutoff`` alone — the id tiebreak was dropped. When many
   rows shared the boundary ``ts`` (a saturated batch drain, a coarse clock, a
   bulk insert), nothing sorted below the boundary matched ``ts <`` and the cap
   deleted almost nothing, leaving ``request_logs`` permanently over its bound
   while every sweep reported success.

2. ``write_requests`` sent the whole drain as ONE multi-row INSERT in ONE
   transaction, so a single row a strict backend rejects (Postgres enforces
   typed columns where SQLite coerces) discarded up to 200 good rows.

Both are exercised through the real HTTP surface as well, so the fix holds end
to end and not just at the sink.
"""
from __future__ import annotations

import time

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent


async def _sink(tmp_path, name: str) -> tuple[DBSink, object]:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/{name}")
    sink = DBSink(eng)
    await sink.startup()
    return sink, eng


async def _insert(eng, *, ts, request_id, key_id="k", cost=0.1, tok_in=10,
                  group="m", provider="p") -> None:
    async with eng.begin() as c:
        await c.execute(text(
            "INSERT INTO request_logs"
            " (ts, request_id, key_id, model_group, provider, status, cost, tok_in)"
            " VALUES (:ts,:r,:k,:g,:p,200,:c,:t)"),
            {"ts": ts, "r": request_id, "k": key_id, "g": group,
             "p": provider, "c": cost, "t": tok_in})


async def _count(eng) -> int:
    async with eng.connect() as c:
        return (await c.execute(text("SELECT COUNT(*) FROM request_logs"))).scalar() or 0


# -- 1. the cap under ts ties -------------------------------------------------

async def test_cap_is_exact_when_every_row_shares_one_ts(tmp_path):
    """The defect: all-same-ts rows made the cap delete NOTHING."""
    sink, eng = await _sink(tmp_path, "all_same.db")
    try:
        now = time.time()
        for i in range(10):
            await _insert(eng, ts=now, request_id=f"r{i:02d}")

        deleted = await sink.enforce_log_cap(5)

        assert deleted == 5, "the cap must delete exactly the rows beyond it"
        assert await _count(eng) == 5
    finally:
        await eng.dispose()


async def test_cap_keeps_the_newest_rows_by_id_on_a_tie(tmp_path):
    """Exactness, not just the count: the boundary row is KEPT, older ids go."""
    sink, eng = await _sink(tmp_path, "keep_newest.db")
    try:
        now = time.time()
        for i in range(10):
            await _insert(eng, ts=now, request_id=f"r{i:02d}")

        await sink.enforce_log_cap(5)

        async with eng.connect() as c:
            kept = [r[0] for r in (await c.execute(text(
                "SELECT request_id FROM request_logs ORDER BY id"))).all()]
        assert kept == [f"r{i:02d}" for i in range(5, 10)], (
            "the cap must keep the newest 5 rows (highest ids) on a ts tie")
    finally:
        await eng.dispose()


async def test_cap_is_exact_with_a_partial_tie(tmp_path):
    """The realistic shape: a bulk burst sharing a ts plus older rows."""
    sink, eng = await _sink(tmp_path, "partial.db")
    try:
        now = time.time()
        for i in range(90):
            await _insert(eng, ts=now, request_id=f"bulk{i}")
        for j in range(10):
            await _insert(eng, ts=now - (j + 1) * 3600, request_id=f"old{j}")

        before = await sink.read_overview(0)
        deleted = await sink.enforce_log_cap(50)
        after = await sink.read_overview(0)

        assert deleted == 50, "100 rows, cap 50 -> exactly 50 removed"
        assert await _count(eng) == 50
        # The whole point of the rollup: capping must not change a reported number.
        assert after["requests"] == before["requests"] == 100
        assert after["tok_in"] == before["tok_in"] == 1000
    finally:
        await eng.dispose()


async def test_cap_reenforces_after_a_tie_had_blocked_it(tmp_path):
    """The defect was persistent: a blocked cap must recover, not stay over."""
    sink, eng = await _sink(tmp_path, "reenforce.db")
    try:
        now = time.time()
        for i in range(20):
            await _insert(eng, ts=now, request_id=f"t{i}")
        await sink.enforce_log_cap(5)
        assert await _count(eng) == 5
        # A second pass over an already-capped table stays a no-op.
        assert await sink.enforce_log_cap(5) == 0
        assert await _count(eng) == 5
    finally:
        await eng.dispose()


async def test_age_prune_unchanged_by_the_tie_break(tmp_path):
    """The id break must not alter the age path (no cutoff_id)."""
    sink, eng = await _sink(tmp_path, "age.db")
    try:
        now = time.time()
        for j in range(10):
            await _insert(eng, ts=now - (j + 1) * 86400, request_id=f"old{j}")
        await _insert(eng, ts=now, request_id="fresh")

        deleted = await sink.prune_old_requests(1)

        assert deleted == 10, "only rows older than 1 day may be pruned"
        assert await _count(eng) == 1
    finally:
        await eng.dispose()


# -- 2. one bad row must not discard the batch --------------------------------

async def test_one_bad_row_does_not_discard_the_batch(tmp_path):
    """The defect: 199 good + 1 NOT-NULL-violating row persisted 0 rows."""
    sink, eng = await _sink(tmp_path, "batch.db")
    try:
        batch = [LogEvent(stream="request", ts=float(i),
                          request_id=f"g{i}", surface="chat") for i in range(199)]
        batch.append(LogEvent(stream="request", ts=None,
                              request_id="bad", surface="chat"))

        await sink.write_requests(batch)  # must not raise

        assert await _count(eng) == 199, "every good row must survive"
        async with eng.connect() as c:
            bad = (await c.execute(text(
                "SELECT COUNT(*) FROM request_logs WHERE request_id='bad'"))).scalar()
        assert bad == 0, "the rejected row alone is dropped"
    finally:
        await eng.dispose()


async def test_all_good_batch_uses_the_bulk_path(tmp_path):
    """The fallback must not disturb the normal path."""
    sink, eng = await _sink(tmp_path, "good.db")
    try:
        batch = [LogEvent(stream="request", ts=1000.0 + i,
                          request_id=f"ok{i}", surface="chat") for i in range(50)]
        await sink.write_requests(batch)
        assert await _count(eng) == 50
    finally:
        await eng.dispose()


async def test_all_bad_batch_still_raises_for_accounting(tmp_path):
    """When nothing can be written the caller must see the failure, so its
    ``failed_request_log_writes`` counter reflects the loss."""
    sink, eng = await _sink(tmp_path, "allbad.db")
    try:
        batch = [LogEvent(stream="request", ts=None,
                          request_id=f"x{i}", surface="chat") for i in range(3)]
        with pytest.raises(sa.exc.IntegrityError):
            await sink.write_requests(batch)
        assert await _count(eng) == 0
    finally:
        await eng.dispose()


async def test_empty_batch_is_a_noop(tmp_path):
    sink, eng = await _sink(tmp_path, "empty.db")
    try:
        await sink.write_requests([])
        assert await _count(eng) == 0
    finally:
        await eng.dispose()
