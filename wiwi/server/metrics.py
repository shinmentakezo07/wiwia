"""Prometheus-compatible /metrics endpoint.

Exposes gateway metrics in the Prometheus text exposition format:
- ``wiwi_requests_total``: counter of all requests
- ``wiwi_request_duration_ms``: histogram of request latency
- ``wiwi_tokens_total``: counter of tokens (input/output/cached/reasoning)
- ``wiwi_cost_total``: counter of total cost in USD
- ``wiwi_ttft_ms``: histogram of time-to-first-token
- ``wiwi_tps``: histogram of tokens per second
- ``wiwi_stream_errors_total``: counter of mid-stream failures
- ``wiwi_provider_cooldowns``: gauge of providers in cooldown

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
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, math.ceil(len(sorted_vals) * p / 100) - 1))
    return sorted_vals[idx]


def render_metrics(events: list[LogEvent]) -> str:
    """Render Prometheus-format metrics from a list of LogEvents."""
    total = len(events)
    if total == 0:
        return _HEADER + "# wiwi no requests in window\n"

    durations = [e.latency_ms for e in events if e.latency_ms > 0]
    ttfts = [e.ttft_ms for e in events if e.ttft_ms > 0]
    tps_values = [e.tps for e in events if e.tps > 0]
    costs = [e.cost for e in events if e.cost > 0]

    tok_in = sum(e.tok_in for e in events)
    tok_out = sum(e.tok_out for e in events)
    tok_cached = sum(e.tok_cached for e in events)
    tok_reasoning = sum(e.tok_reasoning for e in events)
    stream_errors = sum(1 for e in events if e.status >= 500 and e.was_stream)

    # Count by status.
    status_counts = Counter(e.status for e in events)
    # Count by provider.
    provider_counts = Counter(e.provider for e in events if e.provider)

    lines: list[str] = [_HEADER]

    # Counters.
    lines.append(f"wiwi_requests_total {total}")
    lines.append(f"wiwi_tokens_total{{kind=\"input\"}} {tok_in}")
    lines.append(f"wiwi_tokens_total{{kind=\"output\"}} {tok_out}")
    lines.append(f"wiwi_tokens_total{{kind=\"cached\"}} {tok_cached}")
    lines.append(f"wiwi_tokens_total{{kind=\"reasoning\"}} {tok_reasoning}")
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

    # Histograms (simplified: just p50, p95, p99).
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
# HELP wiwi_tokens_total Token usage by kind (input/output/cached/reasoning).
# TYPE wiwi_tokens_total counter
# HELP wiwi_cost_total Total cost in USD.
# TYPE wiwi_cost_total counter
# HELP wiwi_stream_errors_total Mid-stream failures.
# TYPE wiwi_stream_errors_total counter
# HELP wiwi_request_duration_ms Request latency in milliseconds.
# TYPE wiwi_request_duration_ms histogram
# HELP wiwi_ttft_ms Time to first token in milliseconds.
# TYPE wiwi_ttft_ms histogram
# HELP wiwi_tps Tokens per second.
# TYPE wiwi_tps histogram
"""
