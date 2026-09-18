"""Round 82: counted log losses (#171, #173) and monotonic Prometheus counters (#180).

Three defects, one theme: an operator surface that reports "healthy" while
data is being lost, and a metric family that lies to PromQL.

#171 — ``_emit`` awaits ``write_requests(batch)``; a failure was logged and the
batch discarded, but ``dropped_request_logs`` was incremented ONLY on
``asyncio.QueueFull``. ``/health`` reported ``0`` and ``/metrics`` exported
``wiwi_request_logs_dropped_total 0`` while every request row was lost — and
``request_logs`` is the only durable copy.

#173 — ``log_proxy``'s ``except QueueFull: pass`` was silent, and a failed
``write_audit`` lost the audit row of a mutation that had already been applied
(the capped ring is the only remaining trace). Neither loss appeared on any
operator surface.

#180 — ``render_metrics`` summed over ``deque(maxlen=500)`` but declared
``wiwi_requests_total``, ``wiwi_cost_total``, ``wiwi_tokens_total``,
``wiwi_prompt_cache_hits_total``, ``wiwi_response_cache_hits_total`` and
``wiwi_usage_estimated_requests_total`` as ``counter``. PromQL reads each
eviction as a counter reset, so ``rate()``/``increase()`` returned negative or
wildly wrong values. Those series now come from
``LoggingSubsystem.totals`` (process-lifetime monotonic accumulators) under
unchanged metric NAMES; the honestly per-scrape values (cache hit rate,
status/provider breakdowns, quantile summaries) stay ring-derived and are
declared ``gauge``/``summary``.

The three quantile families are ``summary`` and must stay that way — declaring
them ``histogram`` made every sample parse as an empty histogram and broke
``histogram_quantile()``.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque

from wiwi.logging_core.events import LogEvent
from wiwi.logging_core.subsystem import (
    PROXY_QUEUE_SIZE,
    REQUEST_QUEUE_SIZE,
    LoggingSubsystem,
    RequestTotals,
)
from wiwi.server.metrics import render_metrics


class _FailingSink:
    """A DBSink whose every write fails, as during a DB outage or lock."""

    def __init__(self, error: str = "database is locked") -> None:
        self.error = error
        self.audit_attempts: list[LogEvent] = []

    async def write_requests(self, batch: list[LogEvent]) -> None:
        raise RuntimeError(self.error)

    async def write_audit(self, evt: LogEvent) -> None:
        self.audit_attempts.append(evt)
        raise RuntimeError(self.error)


def _req(**kw) -> LogEvent:
    return LogEvent(stream="request", ts=time.time(), status=200, **kw)


async def _drain_until(predicate, timeout: float = 5.0) -> bool:
    """Poll *predicate* until true; the log pump is asynchronous."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# -- #171: a failed request-log DB write is counted ---------------------------

async def test_failed_request_log_write_counts_every_lost_row():
    """A failing sink must increment the failure counter by the batch size.

    Pre-fix the exception was caught and the batch dropped with
    ``dropped_request_logs`` untouched, so a DB outage during a spend audit
    read as "everything is fine" while ``request_logs`` — the only durable
    copy — lost every row.
    """
    sub = LoggingSubsystem()
    sub.set_db_sink(_FailingSink())
    await sub.start()
    try:
        n = 7
        for _ in range(n):
            sub.log_request(_req())
        ok = await _drain_until(lambda: sub.failed_request_log_writes == n)
        assert ok, (f"expected {n} lost rows counted, saw "
                    f"{sub.failed_request_log_writes}")
    finally:
        await sub.stop()

    assert sub.failed_request_log_writes == n, (
        "the discarded batch must be counted by its size, not by batch count"
    )
    # The queue-full counter is a different loss mode and stays at 0: a DB
    # outage must not be reported as a saturated queue.
    assert sub.dropped_request_logs == 0
    assert sub.dropped_log_events == n


async def test_failed_write_losses_accumulate_across_batches():
    """Two failing batches sum; the counter is a lifetime total, not a flag."""
    sub = LoggingSubsystem()
    sub.set_db_sink(_FailingSink())
    await sub.start()
    try:
        # Batch 1: one event. The pump only runs at an await point, so a
        # single synchronous enqueue followed by a poll yields a batch of 1.
        sub.log_request(_req())
        assert await _drain_until(lambda: sub.failed_request_log_writes == 1)

        # Batch 2: three events enqueued back-to-back with no await between
        # them, so the pump cannot interleave and takes them as one batch.
        for _ in range(3):
            sub.log_request(_req())
        assert await _drain_until(lambda: sub.failed_request_log_writes == 4)
    finally:
        await sub.stop()

    assert sub.failed_request_log_writes == 4, (
        f"losses must accumulate across batches, saw "
        f"{sub.failed_request_log_writes}"
    )


async def test_failed_write_and_queue_full_are_separate_counters():
    """Both loss modes are visible and neither masks the other."""
    sub = LoggingSubsystem()
    sub.set_db_sink(_FailingSink())
    await sub.start()
    sub.log_request(_req())
    assert await _drain_until(lambda: sub.failed_request_log_writes == 1)
    # stop() terminates the pump, so the queue is no longer drained and can be
    # overfilled synchronously — no private attribute access required.
    await sub.stop()

    overflow = 3
    for _ in range(REQUEST_QUEUE_SIZE + overflow):
        sub.log_request(_req())

    assert sub.dropped_request_logs == overflow, (
        f"expected exactly {overflow} queue-full drops, saw "
        f"{sub.dropped_request_logs}"
    )
    assert sub.failed_request_log_writes == 1
    assert sub.dropped_log_events == 1 + overflow


# -- #173: proxy and audit losses are counted ---------------------------------

def test_full_proxy_queue_counts_drops():
    """A saturated proxy queue must count every dropped event.

    ``log_proxy``'s ``except QueueFull: pass`` made proxy-log loss invisible:
    the operator saw the stdout line but nothing on /health or /metrics.
    """
    sub = LoggingSubsystem()
    assert sub.dropped_proxy_logs == 0
    for _ in range(PROXY_QUEUE_SIZE):
        sub.log_proxy("info", "fits")
    assert sub.dropped_proxy_logs == 0, "a non-full queue must not count drops"

    overflow = 5
    for _ in range(overflow):
        sub.log_proxy("info", "dropped")
    assert sub.dropped_proxy_logs == overflow
    assert sub.dropped_log_events == overflow
    # The other streams are untouched by a proxy-stream loss.
    assert sub.dropped_request_logs == 0
    assert sub.failed_request_log_writes == 0
    assert sub.failed_audit_log_writes == 0


async def test_failed_audit_write_is_counted_and_ring_still_holds_the_row():
    """A failed ``write_audit`` is counted, and the ring remains the fallback.

    The mutation the row describes has already been applied, so the durable
    row is unrecoverable; the capped ring copy is the only trace and must both
    survive and be reported as a loss.
    """
    sub = LoggingSubsystem()
    sink = _FailingSink()
    sub.set_db_sink(sink)
    await sub.log_audit("admin", "delete_provider", "ghost", {"name": "ghost"})

    assert sub.failed_audit_log_writes == 1
    assert sub.dropped_log_events == 1
    assert len(sink.audit_attempts) == 1, "the write must actually be attempted"

    rows = await sub.read_audit()
    assert [r["target"] for r in rows] == ["ghost"], (
        "the ring copy must survive a failed durable write"
    )


async def test_audit_success_does_not_count_a_loss():
    """A healthy sink must not report phantom audit losses."""

    class _OkSink:
        def __init__(self) -> None:
            self.written: list[LogEvent] = []

        async def write_audit(self, evt: LogEvent) -> None:
            self.written.append(evt)

    sub = LoggingSubsystem()
    sink = _OkSink()
    sub.set_db_sink(sink)
    await sub.log_audit("admin", "create_key", "k1")

    assert len(sink.written) == 1
    assert sub.failed_audit_log_writes == 0
    assert sub.dropped_log_events == 0


async def test_audit_without_db_sink_is_not_a_loss():
    """No sink configured is a degraded mode, not a loss: nothing was written
    and nothing was discarded, so counting it would make the loss counter
    meaningless on every sink-less deployment."""
    sub = LoggingSubsystem()
    await sub.log_audit("admin", "create_key", "k1")

    assert sub.failed_audit_log_writes == 0
    assert sub.dropped_log_events == 0


# -- #180: declared counters never decrease across scrapes --------------------

_COUNTER_TYPES = re.compile(r"^# TYPE (\S+) counter$", re.MULTILINE)
_GAUGE_TYPES = re.compile(r"^# TYPE (\S+) gauge$", re.MULTILINE)


def _declared(kind: re.Pattern[str], text: str) -> set[str]:
    return set(kind.findall(text))


def _samples(text: str) -> dict[str, float]:
    """Map every sample line (name+labels) to its value."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, value = line.rpartition(" ")
        try:
            out[key] = float(value)
        except ValueError:
            continue
    return out


def _evt(cost: float, tok_in: int, tok_out: int, *, cache_hit: bool = False,
         response_cache_hit: bool = False, usage_estimated: bool = False,
         status: int = 200, was_stream: bool = False) -> LogEvent:
    return LogEvent(stream="request", ts=time.time(), status=status,
                    tok_in=tok_in, tok_out=tok_out, cost=cost,
                    cache_hit=cache_hit, response_cache_hit=response_cache_hit,
                    usage_estimated=usage_estimated, was_stream=was_stream,
                    latency_ms=12.0, ttft_ms=3.0, tps=20.0)


def test_declared_counters_never_decrease_across_scrapes():
    """The #180 regression: rotating the ring must not lower a counter.

    Pre-fix every counter was re-derived from ``deque(maxlen=500)``, so
    ``wiwi_cost_total`` went 0.5 -> 500 -> 0.5 as expensive traffic rotated
    out, and ``wiwi_requests_total`` was pinned at the ring size forever.
    """
    sub = LoggingSubsystem()
    ring: deque[LogEvent] = deque(maxlen=500)
    scrapes: list[tuple[set[str], dict[str, float]]] = []

    workload = [
        [_evt(0.001, 10, 5) for _ in range(500)],                      # cheap
        [_evt(1.0, 1000, 500, cache_hit=True) for _ in range(500)],    # expensive
        [_evt(0.001, 10, 5) for _ in range(500)],                      # cheap again
    ]
    for phase in workload:
        for e in phase:
            sub.log_request(e)
            ring.append(e)
        text = render_metrics(list(ring), sub.dropped_request_logs,
                              sub.totals, sub.failed_request_log_writes,
                              sub.dropped_proxy_logs, sub.failed_audit_log_writes)
        scrapes.append((_declared(_COUNTER_TYPES, text), _samples(text)))

    counter_names = scrapes[0][0]
    assert "wiwi_requests_total" in counter_names
    assert "wiwi_cost_total" in counter_names
    assert "wiwi_tokens_total" in counter_names
    assert "wiwi_prompt_cache_hits_total" in counter_names
    assert "wiwi_response_cache_hits_total" in counter_names
    assert "wiwi_usage_estimated_requests_total" in counter_names

    # Every series the exposition itself declares ``counter`` must be
    # non-decreasing across scrapes. ``wiwi_cost_total`` is unlabelled and the
    # others are per-kind, so match by name prefix.
    def _counters(sample_map: dict[str, float]) -> dict[str, float]:
        return {k: v for k, v in sample_map.items()
                if k.split("{")[0] in counter_names}

    checked = 0
    for i in range(1, len(scrapes)):
        prev, cur = _counters(scrapes[i - 1][1]), _counters(scrapes[i][1])
        for series, value in cur.items():
            assert series in prev, f"{series} vanished between scrapes"
            assert value >= prev[series], (
                f"{series} decreased across scrapes: {prev[series]} -> {value}; "
                "PromQL reads that as a counter reset"
            )
            checked += 1
    assert checked >= 7, (
        f"only {checked} counter series were asserted; the check is vacuous"
    )

    # The pre-fix shape must be impossible: cost was re-derived from the ring,
    # so it fell back to 0.5 once the expensive events rotated out.
    final = scrapes[-1][1]
    assert final["wiwi_cost_total"] > 500.0, (
        "wiwi_cost_total must accumulate the expensive phase, not follow the "
        "ring back down"
    )
    assert (final['wiwi_tokens_total{kind="input"}']
            > scrapes[0][1]['wiwi_tokens_total{kind="input"}'])
    assert final["wiwi_requests_total"] == 1500, (
        "wiwi_requests_total must count every request served, not the ring size"
    )


def test_counter_series_track_lifetime_not_the_window():
    """The declared counters exceed the window once the ring evicts.

    This is the observable difference between a ring-derived value and a
    process-lifetime one: with more than 500 requests served, the counter
    must exceed the ring size.
    """
    sub = LoggingSubsystem()
    ring: deque[LogEvent] = deque(maxlen=500)
    for _ in range(700):
        e = _evt(0.01, 7, 3)
        sub.log_request(e)
        ring.append(e)

    text = render_metrics(list(ring), totals=sub.totals)
    samples = _samples(text)
    assert samples["wiwi_requests_total"] == 700, (
        "wiwi_requests_total must count every request served, not the 500 "
        "the ring still holds"
    )
    assert len(ring) == 500
    # A gauge that is honestly window-scoped still reports the window.
    assert 'wiwi_requests_by_status{status="200"} 500' in text


def test_window_gauges_are_not_monotonic():
    """Gauge series stay window-derived and are NOT asserted monotonic.

    The counter fix must not leak into the genuinely per-scrape families: a
    gauge may fall, and here it must — the cache hit rate drops as cache-hit
    traffic leaves the window, which is exactly why it is a gauge.
    """
    sub = LoggingSubsystem()
    ring: deque[LogEvent] = deque(maxlen=500)
    rates: list[float] = []

    for phase in ([_evt(0.0, 1, 1, cache_hit=True) for _ in range(500)],
                  [_evt(0.0, 1, 1) for _ in range(500)]):
        for e in phase:
            sub.log_request(e)
            ring.append(e)
        text = render_metrics(list(ring), totals=sub.totals)
        assert "wiwi_prompt_cache_hit_rate" in _declared(_GAUGE_TYPES, text), (
            "the hit rate is a per-scrape value and must be declared gauge"
        )
        rates.append(_samples(text)["wiwi_prompt_cache_hit_rate"])

    assert rates == [1.0, 0.0], f"expected the window rate to fall, saw {rates}"
    assert rates[1] < rates[0], (
        "a gauge is allowed to decrease; only declared counters must not"
    )


def test_quantile_families_stay_summary():
    """The three quantile families are ``summary``, never ``histogram``.

    Declaring them ``histogram`` made every sample parse as an empty histogram
    (SampleCount=0) and broke ``histogram_quantile()``. This test exists so the
    #180 counter fix cannot regress that declaration.
    """
    text = render_metrics([_evt(0.0, 1, 1)])
    for family in ("wiwi_request_duration_ms", "wiwi_ttft_ms", "wiwi_tps"):
        assert f"# TYPE {family} summary" in text, f"{family} must be summary"
        assert f"# TYPE {family} histogram" not in text
    assert "wiwi_request_duration_ms{" in text
    assert "wiwi_ttft_ms{" in text
    assert "wiwi_tps{" in text
    # No histogram-derived series may appear.
    for suffix in ("_bucket", "_sum", "_count"):
        assert f"wiwi_request_duration_ms{suffix}" not in text


def test_loss_counters_are_exported_and_summed():
    """All four loss counters and their sum reach the exposition (#171/#173)."""
    text = render_metrics(
        [], dropped_request_logs=2, totals=RequestTotals(),
        failed_request_log_writes=3, dropped_proxy_logs=5,
        failed_audit_log_writes=7,
    )
    samples = _samples(text)
    assert samples["wiwi_request_logs_dropped_total"] == 2
    assert samples["wiwi_request_logs_failed_total"] == 3
    assert samples["wiwi_proxy_logs_dropped_total"] == 5
    assert samples["wiwi_audit_logs_failed_total"] == 7
    assert samples["wiwi_log_events_dropped_total"] == 17
    for name in ("wiwi_request_logs_failed_total", "wiwi_proxy_logs_dropped_total",
                 "wiwi_audit_logs_failed_total", "wiwi_log_events_dropped_total"):
        assert f"# TYPE {name} counter" in text


def test_spend_charge_failures_are_exported():
    """A failed spend charge reaches the exposition as its own counter (#179).

    The budget cap silently stops being enforced when ``update_spend`` raises,
    so the count must be scrapable, not just present in /health. It is not a
    log-stream loss, so it is passed in separately from the log counters.
    """
    text = render_metrics([], totals=RequestTotals(), spend_charge_failures=4)
    assert _samples(text)["wiwi_spend_charge_failures_total"] == 4
    assert "# TYPE wiwi_spend_charge_failures_total counter" in text
    # Defaults to 0 and is not folded into the log-loss sum.
    default = render_metrics([])
    assert _samples(default)["wiwi_spend_charge_failures_total"] == 0
    assert _samples(default)["wiwi_log_events_dropped_total"] == 0
