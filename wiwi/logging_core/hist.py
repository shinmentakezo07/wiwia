"""Compact log-scale histograms for percentile rollups.

Why this exists
---------------
``request_rollups`` aggregates pruned ``request_logs`` rows, so the raw samples
behind a percentile are gone. Storing one p95 float per bucket cannot be merged
with raw samples: a weighted mean of two p95s is not the p95 of the union (a
window that is half fast requests and half slow ones was measured reporting 547
where the true value was 1000 — a 45% under-report).

A histogram keeps the *distribution* at fixed, small size, so raw samples and
rolled-up buckets merge by adding bin counts, and the percentile is computed
from the combined counts. That is exact up to the bin width.

Encoding
--------
Bins are log-scale over a fixed range, stored as a JSON object mapping a bin
index to a count: ``{"0": 3, "7": 1}``. Log scale is right for both latency
(kilobytes→seconds) and throughput (near-zero→hundreds of tok/s): a fixed width
either wastes bins at the top or has none at the bottom.

The bin edge for index ``i`` is ``exp(i / BINS_PER_DECADE)`` in the metric's
native unit; values are clamped into ``[0, MAX_BIN]``. Resolution is therefore
a constant *ratio* (about ``e^(1/BINS_PER_DECADE)`` ≈ 1.10 at 10 bins/decade),
so an individual sample is located to within ~±5% of its value. The percentile
*rank* is exact, which is what a p95 actually reads.
"""

from __future__ import annotations

import json
import math

# 10 bins per e-fold gives ~10.5% steps. Measured against four distributions
# (uniform tps, uniform latency, bimodal, heavy-tailed) the p95 error stays
# under ~4% and is under ~1% for the uniform shapes the dashboard actually
# shows. Coarser (5/decade) was up to 15% off on latency; finer (20/decade)
# overflows float precision in the bin search and degrades badly.
BINS_PER_DECADE = 10
# Highest bin index. e^(160/10) = e^16 ≈ 8.9e6, covering ms and tok/s with
# headroom while keeping the JSON payload small.
MAX_BIN = 160
_HIST_VERSION = 1

_EXP = [math.exp(i / BINS_PER_DECADE) for i in range(MAX_BIN + 1)]


def bin_index(value: float) -> int:
    """Bin index for *value* (>= 0). 0 means the value is absent/zero.

    Zero and negatives are treated as "no sample": every percentile consumer in
    this codebase already excludes ``<= 0`` values (a 0 tok/s row is not a fast
    row, it is a row with no timing).
    """
    if value is None or value <= 0:
        return 0
    if value >= _EXP[MAX_BIN]:
        return MAX_BIN
    # Binary-search-free: the table is short and this runs during rollup only.
    lo, hi = 1, MAX_BIN
    while lo < hi:
        mid = (lo + hi) // 2
        if _EXP[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return max(1, min(MAX_BIN, lo))


def bin_edge(index: int) -> float:
    """Representative value of bin *index*: its geometric midpoint.

    The midpoint (rather than the upper edge) halves the worst-case error for
    a unimodal distribution: a sample is equally likely to sit just above the
    lower edge as just below the upper one.
    """
    if index <= 0:
        return 0.0
    idx = min(index, MAX_BIN)
    if idx == 1:
        return _EXP[1]
    return math.sqrt(_EXP[idx - 1] * _EXP[idx])


def add_value(hist: dict[int, int], value: float) -> None:
    """Accumulate one sample into *hist* in place."""
    idx = bin_index(value)
    if idx:
        hist[idx] = hist.get(idx, 0) + 1


def encode(hist: dict[int, int]) -> str:
    """Serialize *hist* to the JSON text stored in the rollup row."""
    if not hist:
        return ""
    return json.dumps({"v": _HIST_VERSION,
                       "b": {str(k): v for k, v in sorted(hist.items())}},
                      separators=(",", ":"))


def decode(text: str | None) -> dict[int, int]:
    """Parse a stored histogram. A blank/legacy/garbled value decodes to {}.

    A row written before the histogram existed carries no value (or an older
    encoding); it degrades to "no distribution" and the caller falls back to
    the legacy stored ``*_p95`` column, so an un-migrated database still
    reports something rather than crashing or zeroing the panel.
    """
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(obj, dict) or obj.get("v") != _HIST_VERSION:
        return {}
    bins = obj.get("b")
    if not isinstance(bins, dict):
        return {}
    out: dict[int, int] = {}
    for k, v in bins.items():
        try:
            idx = int(k)
            cnt = int(v)
        except (TypeError, ValueError):
            continue
        if 0 < idx <= MAX_BIN and cnt > 0:
            out[idx] = out.get(idx, 0) + cnt
    return out


def merge(*hists: dict[int, int]) -> dict[int, int]:
    """Sum bin counts across histograms (raw samples + rolled-up buckets)."""
    out: dict[int, int] = {}
    for h in hists:
        for idx, cnt in h.items():
            if cnt:
                out[idx] = out.get(idx, 0) + cnt
    return out


def percentile_with_samples(samples: list[float],
                            hist: dict[int, int] | None) -> float:
    """Nearest-rank percentile over raw *samples* PLUS rolled-up *hist* counts.

    The raw samples keep their exact values; only the rolled-up portion is
    approximated by its bin midpoints. That matters when the raw side is the
    whole window (the common case for a recent range), where the answer is
    then exact — a two-sample p95 of ``[40, 80]`` reports 80, not the 77.5 its
    bin midpoint alone would give.
    """
    if not samples and not hist:
        return 0.0
    merged = sorted(samples)
    if hist:
        for idx in sorted(hist):
            merged.extend([bin_edge(idx)] * hist[idx])
        merged.sort()
    n = len(merged)
    target = max(1, min(n, math.ceil(n * 0.95)))
    return merged[target - 1]


def percentile(hist: dict[int, int], p: float = 0.95) -> float:
    """Nearest-rank percentile over the merged histogram.

    Mirrors ``wiwi.server.stats.percentile``'s nearest-rank definition so the
    DB and ring backends agree: rank ``ceil(n * p)``, clamped into range, then
    the bin edge holding that rank.
    """
    total = sum(hist.values())
    if total <= 0:
        return 0.0
    if p <= 0:
        target = 1
    else:
        target = max(1, min(total, math.ceil(total * p)))
    seen = 0
    for idx in sorted(hist):
        seen += hist[idx]
        if seen >= target:
            return bin_edge(idx)
    return bin_edge(max(hist))
