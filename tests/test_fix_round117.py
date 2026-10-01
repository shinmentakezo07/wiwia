"""Regression tests for bug-fix round 117 (2026-10-01).

TPS is output *generation* speed: completion tokens divided by the generation
phase only. Round 117 fixed four defects in how it was measured and reported:

1. ``build_log_event`` computed TPS two different ways — generation-phase for
   streaming requests, whole round-trip for non-streaming ones — and both fed
   the same quantile family, so a p50 mixed two incomparable measures.
2. Requests with no measurable generation phase were dropped from every
   aggregate by a bare ``tps > 0`` filter, with nothing reporting the
   denominator. TPS is now streaming-only by definition, and the share of
   requests that carry a TPS sample is published as ``tps_sample_ratio`` /
   ``wiwi_tps_sample_ratio`` so the gap is visible rather than silent.
3. ``wiwi_tps`` exported only p50/p95 while latency and TTFT also exported p99.
4. The nearest-rank formula was duplicated in ``wiwi.server.metrics`` and
   ``wiwi.server.stats``; ``stats.percentile_sorted`` is now the single owner.

Issue 4 of the original report — that ``wiwi_tps`` stays a scrape-time summary
over a 500-event ring, with no ``_bucket`` series — was knowingly declined and
is recorded in AUDIT.md rather than fixed here.
"""

import time

import pytest
import sqlalchemy.ext.asyncio as saa

from wiwi.core.context import RequestContext
from wiwi.core.gateway import build_log_event
from wiwi.ir import types as ir
from wiwi.logging_core.db_sink import DBSink
from wiwi.logging_core.events import LogEvent
from wiwi.server import metrics as metrics_mod
from wiwi.server import stats as stats_mod


def _event(ts: float, tps: float = 0.0, **kw) -> LogEvent:
    return LogEvent(stream="request", ts=ts, tps=tps, **kw)


# -- 1: one definition of TPS — generation phase only -----------------------------


def test_non_streaming_request_has_no_tps():
    """No generation phase to measure -> tps stays 0.0.

    This used to fall back to completion_tokens / total_latency, which folded
    queueing and prefill into "throughput" and reported a number that was not
    comparable with the streaming values in the same quantile family.
    """
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=100)
    ctx.started = ctx.started - 0.5  # 500ms round trip
    evt = build_log_event(ctx)
    assert evt.tok_out == 100
    assert evt.tps == 0.0


def test_streaming_tps_is_generation_phase():
    """Generation phase excludes TTFT: 50 tokens over 0.5s is 100 tps, not 83."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=50)
    ctx.first_token_at = ctx.started + 0.1   # 100ms TTFT
    ctx.last_token_at = ctx.started + 0.6    # 500ms generation
    evt = build_log_event(ctx)
    assert abs(evt.tps - 100.0) < 0.1


def test_stream_too_short_to_time_reports_no_tps():
    """Under the 0.05s threshold there is no usable generation window.

    Previously fell back to round-trip latency, reintroducing the second
    definition for exactly the requests least able to support a rate.
    """
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = ir.Usage(prompt_tokens=10, completion_tokens=30)
    ctx.first_token_at = ctx.started
    ctx.last_token_at = ctx.started + 0.01
    ctx.started = ctx.started - 0.3
    evt = build_log_event(ctx)
    assert evt.tps == 0.0


# -- 2: the TPS sample share is published, not silently dropped --------------------


def test_metrics_export_tps_sample_ratio():
    """2 of 4 window requests carry a TPS sample -> 0.5."""
    now = time.time()
    events = [_event(now, tps=40.0), _event(now, tps=60.0),
              _event(now), _event(now)]
    out = metrics_mod.render_metrics(events, totals=None)
    assert "wiwi_tps_sample_ratio 0.5" in out


def test_metrics_tps_sample_ratio_is_zero_without_samples():
    now = time.time()
    out = metrics_mod.render_metrics([_event(now), _event(now)], totals=None)
    assert "wiwi_tps_sample_ratio 0.0" in out


def test_overview_reports_tps_sample_ratio():
    now = time.time()
    events = [_event(now, tps=40.0), _event(now, tps=60.0), _event(now)]
    ov = stats_mod.overview(events, minutes=60, now=now)
    assert ov["tps_avg"] == 50.0
    assert round(ov["tps_sample_ratio"], 4) == 0.6667


def test_timeseries_reports_tps_sample_ratio_per_bucket():
    now = time.time()
    start = int(now // 60) * 60 - 60
    events = [_event(start + 10, tps=40.0), _event(start + 20, tps=60.0),
              _event(start + 30)]
    series = stats_mod.timeseries(events, bucket="minute", metric="tps",
                                  minutes=60, now=now)
    bucket = next(b for b in series["buckets"] if b["t"] == start)
    assert bucket["tps_avg"] == 50.0
    assert round(bucket["tps_sample_ratio"], 4) == 0.6667


def test_tps_sample_ratio_is_one_when_every_request_streams():
    now = time.time()
    ov = stats_mod.overview([_event(now, tps=30.0), _event(now, tps=70.0)],
                            minutes=60, now=now)
    assert ov["tps_sample_ratio"] == 1.0


# -- 3: p99 for TPS, matching latency and TTFT -------------------------------------


def test_metrics_export_tps_p99():
    now = time.time()
    events = [_event(now, tps=float(v)) for v in range(1, 101)]
    out = metrics_mod.render_metrics(events, totals=None)
    assert 'wiwi_tps{quantile="0.5"}' in out
    assert 'wiwi_tps{quantile="0.95"}' in out
    assert 'wiwi_tps{quantile="0.99"}' in out


def test_tps_quantiles_are_ordered():
    now = time.time()
    events = [_event(now, tps=float(v)) for v in range(1, 101)]
    out = metrics_mod.render_metrics(events, totals=None)

    def value(label: str) -> float:
        for line in out.splitlines():
            if line.startswith(label):
                return float(line.rsplit(" ", 1)[1])
        raise AssertionError(f"{label} missing from exposition")

    p50 = value('wiwi_tps{quantile="0.5"}')
    p95 = value('wiwi_tps{quantile="0.95"}')
    p99 = value('wiwi_tps{quantile="0.99"}')
    assert p50 <= p95 <= p99


# -- 4: one owner for the nearest-rank formula ------------------------------------


def test_percentile_sorted_matches_percentile():
    vals = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
    ordered = sorted(vals)
    for p in (0.5, 0.95, 0.99):
        assert stats_mod.percentile_sorted(ordered, p) == stats_mod.percentile(vals, p)


def test_percentile_sorted_of_empty_is_zero():
    assert stats_mod.percentile_sorted([], 0.95) == 0.0


def test_metrics_does_not_redefine_the_percentile_formula():
    """The exporter must consume stats' formula, not a private copy."""
    assert not hasattr(metrics_mod, "_percentile")


# -- review findings: the DB backend's ratio and its zero-row shapes ---------------


@pytest.fixture
async def db():
    """Temp in-memory SQLite DBSink, matching tests/test_stats_db.py's fixture."""
    engine = saa.create_async_engine("sqlite+aiosqlite:///:memory:")
    sink = DBSink(engine)
    await sink.startup()
    yield sink
    await engine.dispose()


async def test_db_overview_ratio_is_not_capped_by_the_5000_row_sample(db):
    """The ratio's numerator must be an exact count, not the capped sample.

    `tps_avg`/`tps_p95` may be approximated from a LIMIT 5000 sample, but a
    coverage disclosure built from that same sample saturates at 5000/requests
    and under-reports exactly on the high-traffic gateways it describes. 6000
    streaming rows against a 5000-row sample reported 0.8333 instead of 1.0.
    """
    now = time.time()
    await db.write_requests([_event(now, tps=50.0) for _ in range(6000)])
    ov = await db.read_overview(0)
    assert ov["requests"] == 6000
    assert ov["tps_sample_ratio"] == 1.0


async def test_db_overview_ratio_counts_only_streaming_rows(db):
    """Half the window streams -> 0.5, against the exact denominator."""
    now = time.time()
    await db.write_requests(
        [_event(now, tps=50.0) for _ in range(6000)] + [_event(now) for _ in range(6000)])
    ov = await db.read_overview(0)
    assert ov["requests"] == 12000
    assert ov["tps_sample_ratio"] == 0.5


async def test_db_overview_ratio_never_exceeds_one(db):
    now = time.time()
    await db.write_requests([_event(now, tps=50.0) for _ in range(20)])
    ov = await db.read_overview(0)
    assert 0.0 <= ov["tps_sample_ratio"] <= 1.0


async def test_db_overview_zero_row_shape_includes_tps_sample_ratio(db):
    """The no-rows early return must carry the same keys as a populated read."""
    ov = await db.read_overview(0, key_ids=[])
    assert "tps_sample_ratio" in ov
    assert ov["tps_sample_ratio"] == 0.0


async def test_db_timeseries_zero_row_shape_includes_tps_sample_ratio(db):
    """`_read_timeseries_empty` is the only path a keyless user ever hits."""
    series = await db.read_timeseries(60, "tps", 60, key_ids=[])
    assert series["buckets"]
    for bucket in series["buckets"]:
        assert bucket["tps_sample_ratio"] == 0.0


async def test_db_timeseries_bucket_ratio_matches_populated_read(db):
    now = time.time()
    await db.write_requests(
        [_event(now, tps=40.0), _event(now, tps=60.0), _event(now)])
    series = await db.read_timeseries(60, "tps", 60)
    populated = [b for b in series["buckets"] if b["tps_avg"] > 0]
    assert populated
    # 3 requests in the bucket, 2 carrying a TPS sample.
    assert populated[0]["tps_sample_ratio"] == 0.6667
    # Zero-filled buckets have no denominator and must not divide.
    for bucket in series["buckets"]:
        assert bucket["tps_sample_ratio"] <= 1.0
