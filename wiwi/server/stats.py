"""Admin stats rollup: computed on demand over the request-event ring buffer.

Pure functions over LogEvent lists so the math is unit-testable with
deterministic synthetic rings (no DB schema migration in v1). Events with
tps == 0 or ttft_ms == 0 (non-streaming / missing timing) are excluded from
those aggregates only.
"""

from __future__ import annotations

import time

from wiwi.logging_core.events import LogEvent

BUCKET_SECONDS = {"minute": 60}

VALID_METRICS = ("tokens", "tps")


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def window_events(events: list[LogEvent], minutes: int,
                  now: float | None = None) -> list[LogEvent]:
    """Request-stream events newer than the window (oldest -> newest order kept)."""
    now = time.time() if now is None else now
    cutoff = now - minutes * 60
    return [e for e in events if e.stream == "request" and cutoff <= e.ts <= now]


def overview(events: list[LogEvent], minutes: int,
             now: float | None = None) -> dict:
    now = time.time() if now is None else now
    win = window_events(events, minutes, now)
    requests = len(win)
    errors = sum(1 for e in win if e.status >= 400 or e.error_code)
    cache_hits = sum(1 for e in win if e.cache_hit or e.tok_cached > 0)
    tps_values = [e.tps for e in win if e.tps > 0]
    ttft_values = [e.ttft_ms for e in win if e.ttft_ms > 0]
    minutes_norm = max(minutes, 1e-9)
    return {
        "window_minutes": minutes,
        "generated_at": now,
        "requests": requests,
        "errors": errors,
        "error_rate": round(errors / requests, 4) if requests else 0.0,
        "requests_per_minute": round(requests / minutes_norm, 2),
        "tok_in": sum(e.tok_in for e in win),
        "tok_cached": sum(e.tok_cached for e in win),
        "tok_reasoning": sum(e.tok_reasoning for e in win),
        "tok_out": sum(e.tok_out for e in win),
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / requests, 4) if requests else 0.0,
        "tps_avg": round(sum(tps_values) / len(tps_values), 2) if tps_values else 0.0,
        "tps_p95": round(_p95(tps_values), 2),
        "ttft_p95_ms": round(_p95(ttft_values), 1),
        "latency_p95_ms": round(_p95([e.latency_ms for e in win]), 1),
        "cost": round(sum(e.cost for e in win), 6),
        "cache_savings": round(sum(e.cache_savings for e in win), 6),
    }


def timeseries(events: list[LogEvent], bucket: str, metric: str, minutes: int,
               now: float | None = None) -> dict:
    """Bucketed series aligned to the window end.

    Returns exactly `minutes` buckets for bucket="minute"; older buckets may be
    empty. Tokens buckets carry the four token-type sums (for stacked areas);
    tps buckets carry avg + p95 across streaming requests in the bucket.
    """
    if bucket not in BUCKET_SECONDS:
        raise ValueError(f"unsupported bucket {bucket!r}")
    if metric not in VALID_METRICS:
        raise ValueError(f"unsupported metric {metric!r}")
    now = time.time() if now is None else now
    size = BUCKET_SECONDS[bucket]
    n_buckets = max(1, minutes)
    window_start = int(now // size) * size - (n_buckets - 1) * size
    win = window_events(events, minutes + 1, now)  # +1 covers partial first bucket
    token_buckets = [[0, 0, 0, 0] for _ in range(n_buckets)]
    tps_buckets: list[list[float]] = [[] for _ in range(n_buckets)]
    for e in win:
        idx = int((e.ts - window_start) // size)
        if not 0 <= idx < n_buckets:
            continue
        b = token_buckets[idx]
        b[0] += e.tok_in
        b[1] += e.tok_cached
        b[2] += e.tok_reasoning
        b[3] += e.tok_out
        if e.tps > 0:
            tps_buckets[idx].append(e.tps)
    if metric == "tokens":
        buckets = [
            {"t": window_start + i * size, "tok_in": b[0], "tok_cached": b[1],
             "tok_reasoning": b[2], "tok_out": b[3]}
            for i, b in enumerate(token_buckets)
        ]
    else:
        buckets = [
            {"t": window_start + i * size, "tps_avg": round(sum(v) / len(v), 2) if v else 0.0,
             "tps_p95": round(_p95(v), 2)}
            for i, v in enumerate(tps_buckets)
        ]
    return {"bucket_seconds": size, "metric": metric, "buckets": buckets}
