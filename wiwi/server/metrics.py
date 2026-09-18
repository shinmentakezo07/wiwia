"""Prometheus-compatible /metrics endpoint.

Exposes gateway metrics in the Prometheus text exposition format:
- ``wiwi_requests_total``: counter of all requests
- ``wiwi_request_duration_ms``: quantile summary of request latency
- ``wiwi_tokens_total``: counter of tokens (input/output/cached/reasoning)
- ``wiwi_cost_total``: counter of total cost in USD
- ``wiwi_ttft_ms``: quantile summary of time-to-first-token
- ``wiwi_tps``: quantile summary of tokens per second
- ``wiwi_stream_errors_total``: counter of mid-stream failures
- ``wiwi_request_logs_dropped_total``: counter of request-log events dropped
  because the logging queue was full
- ``wiwi_request_logs_failed_total``: counter of request-log rows lost to a
  failed DB write
- ``wiwi_proxy_logs_dropped_total``: counter of proxy-log events dropped
  because the logging queue was full
- ``wiwi_audit_logs_failed_total``: counter of audit rows lost to a failed DB
  write
- ``wiwi_log_events_dropped_total``: sum of the four loss counters above
- ``wiwi_spend_charge_failures_total``: counter of spend charges that failed to
  persist, i.e. requests whose budget cap was not enforced (AUDIT #179)

Counters versus window values
-----------------------------
The gateway computes this exposition from the in-memory LogEvent ring buffer,
which is a ``deque(maxlen=500)``. Anything derived from the ring *decreases*
whenever an event is evicted, so it cannot be declared ``counter``: PromQL
reads every eviction as a counter reset, and ``rate()``/``increase()`` then
return negative or wildly wrong values (AUDIT #180). Two classes therefore
exist here:

- Series declared ``counter`` (``wiwi_requests_total``, ``wiwi_tokens_total``,
  ``wiwi_prompt_cache_hits_total``, ``wiwi_response_cache_hits_total``,
  ``wiwi_usage_estimated_requests_total``, ``wiwi_cost_total``,
  ``wiwi_stream_errors_total`` and the four loss counters) are rendered from
  ``LoggingSubsystem.totals`` — process-lifetime monotonic accumulators — and
  from the subsystem's lifetime loss counters. They never decrease while the
  process lives. The metric NAMES are unchanged, so existing dashboards keep
  working; only the data source moved off the ring.
  ``wiwi_spend_charge_failures_total`` is the same kind of value, passed in
  from ``AppState`` (it counts a non-log event, so the logging subsystem does
  not own it).
- Series that are honestly per-scrape window values stay ring-derived and are
  declared ``gauge`` (``wiwi_prompt_cache_hit_rate``,
  ``wiwi_requests_by_status``, ``wiwi_requests_by_provider``) or ``summary``
  (the three quantile families).

The three quantile families are ``summary``, not ``histogram``: percentiles
are computed from the ring buffer at scrape time, so no ``_bucket``/``_sum``/
``_count`` series exist. Declaring them ``histogram`` made every sample parse
as an empty histogram (SampleCount=0) and broke ``histogram_quantile()``.

The loss counters are passed in for the same reason: a lost event is absent
from the ring by definition, so it cannot be derived from *events*.
"""

from __future__ import annotations

import math
import time
from collections import Counter

from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import RequestTotals


def _escape_label(value: str) -> str:
    """Escape a label value for Prometheus text exposition format."""
    return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")

def _percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted list; *p* is 0-100.

    Same nearest-rank formula as :func:`wiwi.server.stats.percentile`
    (which owns it for the admin rollups); this variant takes a pre-sorted
    list because several quantiles are rendered from one sample.
    """
    if not sorted_vals:
        return 0.0
    # Pre-sorted input, so index directly; stats.percentile owns the formula.
    idx = max(0, min(len(sorted_vals) - 1,
                     math.ceil(len(sorted_vals) * p / 100) - 1))
    return sorted_vals[idx]


def render_metrics(events: list[LogEvent],
                   dropped_request_logs: int = 0,
                   totals: RequestTotals | None = None,
                   failed_request_log_writes: int = 0,
                   dropped_proxy_logs: int = 0,
                   failed_audit_log_writes: int = 0,
                   spend_charge_failures: int = 0) -> str:
    """Render Prometheus-format metrics from a list of LogEvents.

    *events* is the request-stream ring window: it is the only source for the
    quantile summaries, the status/provider breakdowns and the cache hit rate,
    all of which are honestly per-scrape values.

    The four loss counters, *totals* and *spend_charge_failures* cannot be
    derived from *events* — a lost event is absent from the ring by
    definition, and a counter that is re-derived from a 500-event ring
    decreases on every eviction — so the caller passes them in from the
    subsystem that owns them for the process lifetime
    (:class:`wiwi.logging_core.subsystem.LoggingSubsystem` for the log
    counters, ``AppState`` for the spend failures). Without them a saturated
    log queue, an unavailable DB or a silently failing spend charge would
    under-report on every operator surface.

    ``totals=None`` falls back to deriving the ``counter`` series from the
    ring window, which is correct only for a synthetic ring that is never
    evicted (tests, one-off renders). Production callers must pass the
    subsystem's ``totals``; the fallback exists so a caller that predates the
    lifetime counters still renders a sane, if non-monotonic, exposition.
    """
    total = len(events)
    lost = (int(dropped_request_logs) + int(failed_request_log_writes)
            + int(dropped_proxy_logs) + int(failed_audit_log_writes))
    lines: list[str] = [
        _HEADER,
        f"wiwi_request_logs_dropped_total {int(dropped_request_logs)}",
        f"wiwi_request_logs_failed_total {int(failed_request_log_writes)}",
        f"wiwi_proxy_logs_dropped_total {int(dropped_proxy_logs)}",
        f"wiwi_audit_logs_failed_total {int(failed_audit_log_writes)}",
        f"wiwi_log_events_dropped_total {lost}",
        f"wiwi_spend_charge_failures_total {int(spend_charge_failures)}",
    ]
    if total == 0 and totals is None:
        lines.append("# wiwi no requests in window")
        return "\n".join(lines) + "\n"

    durations = [e.latency_ms for e in events if e.latency_ms > 0]
    ttfts = [e.ttft_ms for e in events if e.ttft_ms > 0]
    tps_values = [e.tps for e in events if e.tps > 0]

    # Count by status.
    status_counts = Counter(e.status for e in events)
    # Count by provider.
    provider_counts = Counter(e.provider for e in events if e.provider)

    if totals is None:
        # Synthetic-ring fallback: same predicates as RequestTotals.add, so
        # the two sources agree exactly until the first eviction.
        totals = RequestTotals(
            requests=total,
            tok_in=sum(e.tok_in for e in events),
            tok_out=sum(e.tok_out for e in events),
            tok_cached=sum(e.tok_cached for e in events),
            tok_cache_creation=sum(e.tok_cache_creation for e in events),
            tok_reasoning=sum(e.tok_reasoning for e in events),
            cost=sum(e.cost for e in events),
            cache_hits=sum(1 for e in events if e.cache_hit or e.tok_cached > 0),
            response_cache_hits=sum(1 for e in events if e.response_cache_hit),
            usage_estimated=sum(1 for e in events if e.usage_estimated),
            stream_errors=sum(1 for e in events
                              if e.status >= 500 and e.was_stream),
        )

    # Counters: process-lifetime monotonic values from the logging subsystem.
    lines.append(f"wiwi_requests_total {totals.requests}")
    lines.append(f"wiwi_tokens_total{{kind=\"input\"}} {totals.tok_in}")
    lines.append(f"wiwi_tokens_total{{kind=\"output\"}} {totals.tok_out}")
    lines.append(f"wiwi_tokens_total{{kind=\"cached\"}} {totals.tok_cached}")
    lines.append(f"wiwi_tokens_total{{kind=\"cache_creation\"}} {totals.tok_cache_creation}")
    lines.append(f"wiwi_tokens_total{{kind=\"reasoning\"}} {totals.tok_reasoning}")
    lines.append(f"wiwi_prompt_cache_hits_total {totals.cache_hits}")
    lines.append(f"wiwi_response_cache_hits_total {totals.response_cache_hits}")
    lines.append(f"wiwi_usage_estimated_requests_total {totals.usage_estimated}")
    lines.append(f"wiwi_cost_total {totals.cost:.6f}")
    lines.append(f"wiwi_stream_errors_total {totals.stream_errors}")

    # Window values: a rate over the current ring only, so a gauge. The
    # numerator must come from the same window as the denominator — pairing a
    # lifetime hit count with a window request count would drift towards 0 as
    # the process ages.
    window_cache_hits = sum(1 for e in events if e.cache_hit or e.tok_cached > 0)
    rate = round(window_cache_hits / total, 4) if total else 0.0
    lines.append(f"wiwi_prompt_cache_hit_rate {rate}")

    # Status breakdown.
    for status, count in sorted(status_counts.items()):
        lines.append(
            f"wiwi_requests_by_status{{status=\"{_escape_label(str(status))}\"}} {count}")

    # Provider breakdown.
    for provider, count in sorted(provider_counts.items()):
        lines.append(
            f"wiwi_requests_by_provider{{provider=\"{_escape_label(provider)}\"}} {count}")

    # Summaries: quantiles computed over the ring buffer at scrape time
    # (p50, p95, p99 for latency/ttft; p50, p95 for tps).
    if durations:
        sd = sorted(durations)
        lines.append(f"wiwi_request_duration_ms{{quantile=\"0.5\"}} {_percentile(sd, 50):.1f}")
        lines.append(f"wiwi_request_duration_ms{{quantile=\"0.95\"}} {_percentile(sd, 95):.1f}")
        lines.append(f"wiwi_request_duration_ms{{quantile=\"0.99\"}} {_percentile(sd, 99):.1f}")
    if ttfts:
        st = sorted(ttfts)
        lines.append(f"wiwi_ttft_ms{{quantile=\"0.5\"}} {_percentile(st, 50):.1f}")
        lines.append(f"wiwi_ttft_ms{{quantile=\"0.95\"}} {_percentile(st, 95):.1f}")
        lines.append(f"wiwi_ttft_ms{{quantile=\"0.99\"}} {_percentile(st, 99):.1f}")
    if tps_values:
        sv = sorted(tps_values)
        lines.append(f"wiwi_tps{{quantile=\"0.5\"}} {_percentile(sv, 50):.2f}")
        lines.append(f"wiwi_tps{{quantile=\"0.95\"}} {_percentile(sv, 95):.2f}")

    lines.append(f"# ts {time.time()}")
    return "\n".join(lines) + "\n"


_HEADER = """# HELP wiwi_requests_total Total number of requests.
# TYPE wiwi_requests_total counter
# HELP wiwi_tokens_total Token usage by kind (input/output/cached/cache_creation/reasoning).
# TYPE wiwi_tokens_total counter
# HELP wiwi_prompt_cache_hits_total Requests served with a provider prompt-cache hit.
# TYPE wiwi_prompt_cache_hits_total counter
# HELP wiwi_prompt_cache_hit_rate Fraction of requests in the current window with a prompt-cache hit.
# TYPE wiwi_prompt_cache_hit_rate gauge
# HELP wiwi_response_cache_hits_total Requests served from wiwi's exact-match response cache.
# TYPE wiwi_response_cache_hits_total counter
# HELP wiwi_usage_estimated_requests_total Requests whose token counts were estimated locally because upstream omitted usage.
# TYPE wiwi_usage_estimated_requests_total counter
# HELP wiwi_cost_total Total cost in USD.
# TYPE wiwi_cost_total counter
# HELP wiwi_stream_errors_total Mid-stream failures.
# TYPE wiwi_stream_errors_total counter
# HELP wiwi_requests_by_status Requests by HTTP status code in the current window.
# TYPE wiwi_requests_by_status gauge
# HELP wiwi_requests_by_provider Requests by serving provider account in the current window.
# TYPE wiwi_requests_by_provider gauge
# HELP wiwi_request_logs_dropped_total Request-log events dropped because the logging queue was full.
# TYPE wiwi_request_logs_dropped_total counter
# HELP wiwi_request_logs_failed_total Request-log rows lost to a failed database write.
# TYPE wiwi_request_logs_failed_total counter
# HELP wiwi_proxy_logs_dropped_total Proxy-log events dropped because the logging queue was full.
# TYPE wiwi_proxy_logs_dropped_total counter
# HELP wiwi_audit_logs_failed_total Audit rows lost to a failed database write.
# TYPE wiwi_audit_logs_failed_total counter
# HELP wiwi_log_events_dropped_total Log events lost on any stream, by any loss mode.
# TYPE wiwi_log_events_dropped_total counter
# HELP wiwi_spend_charge_failures_total Spend charges that failed to persist (the budget cap was not enforced).
# TYPE wiwi_spend_charge_failures_total counter
# HELP wiwi_request_duration_ms Request latency in milliseconds (scrape-time quantiles).
# TYPE wiwi_request_duration_ms summary
# HELP wiwi_ttft_ms Time to first token in milliseconds (scrape-time quantiles).
# TYPE wiwi_ttft_ms summary
# HELP wiwi_tps Tokens per second (scrape-time quantiles).
# TYPE wiwi_tps summary
"""
