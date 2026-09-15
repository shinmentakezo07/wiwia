"""Admin stats rollup: computed on demand over the request-event ring buffer.

Pure functions over LogEvent lists so the math is unit-testable with
deterministic synthetic rings (no DB schema migration in v1). Events with
tps == 0 or ttft_ms == 0 (non-streaming / missing timing) are excluded from
those aggregates only.
"""

from __future__ import annotations

import math
import time

from wiwi.logging_core.events import LogEvent

BUCKET_SECONDS = {"minute": 60}

VALID_METRICS = ("tokens", "tps")


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


def percentile(values: list[float], p: float = 0.95) -> float:
    """Nearest-rank percentile of *values* (0.0 < p <= 1.0); 0.0 when empty.

    Single source of truth: the Prometheus exporter, the admin rollups and
    per-deployment latency all use this so their numbers cannot drift apart.
    """
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, min(len(s) - 1, math.ceil(len(s) * p) - 1))]


def _p95(values: list[float]) -> float:
    return percentile(values, 0.95)


def window_events(events: list[LogEvent], minutes: int,
                  now: float | None = None) -> list[LogEvent]:
    """Request-stream events newer than the window (oldest -> newest order kept).

    ``minutes == 0`` means all-time, exactly as in ``DBSink.read_overview`` and
    ``DBSink.read_timeseries``: the cutoff is skipped entirely. Computing it as
    ``now - 0`` instead selected only events stamped at precisely ``now``, so
    the ring backend answered an all-time request with an empty window while
    the DB backend answered it with every row.
    """
    now = time.time() if now is None else now
    cutoff = now - minutes * 60 if minutes > 0 else None
    return [e for e in events
            if e.stream == "request" and e.ts <= now
            and (cutoff is None or e.ts >= cutoff)]


def overview(events: list[LogEvent], minutes: int,
             now: float | None = None) -> dict:
    now = time.time() if now is None else now
    win = window_events(events, minutes, now)
    requests = len(win)
    errors = sum(1 for e in win if e.status >= 400 or e.error_code)
    cache_hits = sum(1 for e in win if e.cache_hit or e.tok_cached > 0)
    tps_values = [e.tps for e in win if e.tps > 0]
    ttft_values = [e.ttft_ms for e in win if e.ttft_ms > 0]
    return {
        "window_minutes": minutes,
        "generated_at": now,
        "requests": requests,
        "errors": errors,
        "error_rate": round(errors / requests, 4) if requests else 0.0,
        # All-time has no meaningful per-minute rate; the DB backend reports
        # 0.0 for it, and dividing by a near-zero normaliser here reported
        # billions of requests per minute for the same data.
        "requests_per_minute": round(requests / minutes, 2) if minutes > 0 else 0.0,
        "tok_in": sum(e.tok_in for e in win),
        "tok_cached": sum(e.tok_cached for e in win),
        "tok_cache_creation": sum(e.tok_cache_creation for e in win),
        "tok_reasoning": sum(e.tok_reasoning for e in win),
        "tok_out": sum(e.tok_out for e in win),
        # How many of the rows above carry *estimated* rather than
        # provider-reported token counts (AUDIT #131) — without it a spend
        # dashboard cannot tell measured traffic from guessed traffic.
        "estimated_requests": sum(1 for e in win if e.usage_estimated),
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / requests, 4) if requests else 0.0,
        "tps_avg": round(sum(tps_values) / len(tps_values), 2) if tps_values else 0.0,
        "tps_p95": round(_p95(tps_values), 2),
        "ttft_p95_ms": round(_p95(ttft_values), 1),
        "latency_p95_ms": round(_p95([e.latency_ms for e in win if e.latency_ms > 0]), 1),
        "cost": round(sum(e.cost for e in win), 6),
        "cache_savings": round(sum(e.cache_savings for e in win), 6),
    }


def timeseries(events: list[LogEvent], bucket: str, metric: str, minutes: int,
               now: float | None = None) -> dict:
    """Bucketed series aligned to the window end.

    ``bucket`` names the shape family the caller asked for (``"minute"`` is the
    only accepted value, matching ``BUCKET_SECONDS``); the concrete width comes
    from :func:`bucket_size_for`, so the ring backend returns the same buckets
    ``DBSink.read_timeseries`` returns for the same ``minutes`` — hourly for a
    7-day window, daily for a 30-day one. Tokens buckets carry the four
    token-type sums (for stacked areas); tps buckets carry avg + p95 across
    streaming requests in the bucket.

    ``minutes == 0`` is all-time: it spans the populated buckets rather than
    zero-filling a grid, mirroring the DB path.
    """
    if bucket not in BUCKET_SECONDS:
        raise ValueError(f"unsupported bucket {bucket!r}")
    if metric not in VALID_METRICS:
        raise ValueError(f"unsupported metric {metric!r}")
    now = time.time() if now is None else now
    size = bucket_size_for(minutes)
    win = window_events(events, minutes, now)
    if minutes > 0:
        # Bounded windows always zero-fill their fixed grid, even with no
        # events, so a client can index the last bucket unconditionally.
        n_buckets = max(1, minutes * 60 // size)
        first_t = int(now // size) * size - (n_buckets - 1) * size
    else:
        starts = [int(e.ts // size) * size for e in win]
        n_buckets = (max(starts) - min(starts)) // size + 1 if starts else 0
        first_t = min(starts) if starts else int(now // size) * size
    token_buckets = [[0, 0, 0, 0, 0] for _ in range(n_buckets)]
    tps_buckets: list[list[float]] = [[] for _ in range(n_buckets)]
    for e in win:
        idx = int((e.ts - first_t) // size)
        if not 0 <= idx < n_buckets:
            continue
        b = token_buckets[idx]
        b[0] += e.tok_in
        b[1] += e.tok_cached
        b[2] += e.tok_cache_creation
        b[3] += e.tok_reasoning
        b[4] += e.tok_out
        if e.tps > 0:
            tps_buckets[idx].append(e.tps)
    if metric == "tokens":
        buckets = [
            {"t": first_t + i * size, "tok_in": b[0], "tok_cached": b[1],
             "tok_cache_creation": b[2], "tok_reasoning": b[3], "tok_out": b[4]}
            for i, b in enumerate(token_buckets)
        ]
    else:
        buckets = [
            {"t": first_t + i * size, "tps_avg": round(sum(v) / len(v), 2) if v else 0.0,
             "tps_p95": round(_p95(v), 2)}
            for i, v in enumerate(tps_buckets)
        ]
    return {"bucket_seconds": size, "metric": metric, "buckets": buckets}
