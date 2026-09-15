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
  because the logging queue was full. The caller passes this in — a dropped
  event is absent from the ring buffer by definition, so it cannot be derived
  from *events*

The three quantile families are ``summary``, not ``histogram``: percentiles
are computed from the ring buffer at scrape time, so no ``_bucket``/``_sum``/
``_count`` series exist. Declaring them ``histogram`` made every sample parse
as an empty histogram (SampleCount=0) and broke ``histogram_quantile()``.

Metrics are computed from the in-memory ring buffer of LogEvents, so they
work without any external dependency.
"""

from __future__ import annotations

import math
import time
from collections import Counter

from wiwi.logging_core.events import LogEvent


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
                   dropped_request_logs: int = 0) -> str:
    """Render Prometheus-format metrics from a list of LogEvents.

    *dropped_request_logs* is the logging subsystem's queue-full drop counter.
    It cannot be derived from *events* — a dropped event is by definition
    absent from the ring buffer — so the caller passes it in. Without it a
    saturated log queue would silently under-report every counter below.
    """
    total = len(events)
    lines: list[str] = [
        _HEADER,
        f"wiwi_request_logs_dropped_total {int(dropped_request_logs)}",
    ]
    if total == 0:
        lines.append("# wiwi no requests in window")
        return "\n".join(lines) + "\n"

    durations = [e.latency_ms for e in events if e.latency_ms > 0]
    ttfts = [e.ttft_ms for e in events if e.ttft_ms > 0]
    tps_values = [e.tps for e in events if e.tps > 0]
    costs = [e.cost for e in events if e.cost > 0]

    tok_in = sum(e.tok_in for e in events)
    tok_out = sum(e.tok_out for e in events)
    tok_cached = sum(e.tok_cached for e in events)
    tok_cache_creation = sum(e.tok_cache_creation for e in events)
    tok_reasoning = sum(e.tok_reasoning for e in events)
    stream_errors = sum(1 for e in events if e.status >= 500 and e.was_stream)
    cache_hits = sum(1 for e in events if e.cache_hit or e.tok_cached > 0)
    response_cache_hits = sum(1 for e in events if e.response_cache_hit)
    usage_estimated = sum(1 for e in events if e.usage_estimated)

    # Count by status.
    status_counts = Counter(e.status for e in events)
    # Count by provider.
    provider_counts = Counter(e.provider for e in events if e.provider)

    # Counters.
    lines.append(f"wiwi_requests_total {total}")
    lines.append(f"wiwi_tokens_total{{kind=\"input\"}} {tok_in}")
    lines.append(f"wiwi_tokens_total{{kind=\"output\"}} {tok_out}")
    lines.append(f"wiwi_tokens_total{{kind=\"cached\"}} {tok_cached}")
    lines.append(f"wiwi_tokens_total{{kind=\"cache_creation\"}} {tok_cache_creation}")
    lines.append(f"wiwi_tokens_total{{kind=\"reasoning\"}} {tok_reasoning}")
    lines.append(f"wiwi_prompt_cache_hits_total {cache_hits}")
    lines.append(f"wiwi_prompt_cache_hit_rate {round(cache_hits / total, 4) if total else 0.0}")
    lines.append(f"wiwi_response_cache_hits_total {response_cache_hits}")
    lines.append(f"wiwi_usage_estimated_requests_total {usage_estimated}")
    lines.append(f"wiwi_cost_total {sum(costs):.6f}")
    lines.append(f"wiwi_stream_errors_total {stream_errors}")

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
# HELP wiwi_prompt_cache_hit_rate Fraction of requests with a prompt-cache hit.
# TYPE wiwi_prompt_cache_hit_rate gauge
# HELP wiwi_response_cache_hits_total Requests served from wiwi's exact-match response cache.
# TYPE wiwi_response_cache_hits_total counter
# HELP wiwi_usage_estimated_requests_total Requests whose token counts were estimated locally because upstream omitted usage.
# TYPE wiwi_usage_estimated_requests_total counter
# HELP wiwi_cost_total Total cost in USD.
# TYPE wiwi_cost_total counter
# HELP wiwi_stream_errors_total Mid-stream failures.
# TYPE wiwi_stream_errors_total counter
# HELP wiwi_requests_by_status Requests by HTTP status code.
# TYPE wiwi_requests_by_status gauge
# HELP wiwi_requests_by_provider Requests by serving provider account.
# TYPE wiwi_requests_by_provider gauge
# HELP wiwi_request_logs_dropped_total Request-log events dropped because the logging queue was full.
# TYPE wiwi_request_logs_dropped_total counter
# HELP wiwi_request_duration_ms Request latency in milliseconds (scrape-time quantiles).
# TYPE wiwi_request_duration_ms summary
# HELP wiwi_ttft_ms Time to first token in milliseconds (scrape-time quantiles).
# TYPE wiwi_ttft_ms summary
# HELP wiwi_tps Tokens per second (scrape-time quantiles).
# TYPE wiwi_tps summary
"""
