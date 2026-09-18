"""Logging subsystem: in-memory queues + background workers + sinks.

Three streams never mix (docs/CORE.md §4):
  request -> DBSink(batched) + SSE broadcast
  proxy   -> stdout JSON + SSE broadcast
  audit   -> synchronous DB write
Nothing here ever blocks a response; the DB sink degrades to drop+count when slow.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, replace

import orjson
import structlog

from wiwi.logging_core.events import LogEvent

log = structlog.get_logger(__name__)

REQUEST_QUEUE_SIZE = 50_000
PROXY_QUEUE_SIZE = 10_000

# Fields that hold full prompt/response text when store_prompts_in_spend_logs
# is enabled. They are stripped from the in-memory ring buffer + SSE broadcast
# copy to prevent RAM bloat (up to 500 full LLM responses held in the ring),
# while still being persisted to the DB by the DBSink which receives the
# original unstripped event from the batch.
_HEAVY_FIELDS: tuple[str, ...] = ("request_body", "response_body")


def _lightweight_copy(evt: LogEvent) -> LogEvent:
    """Return a shallow copy of *evt* with heavy prompt/response bodies set to None.

    The DB write path (DBSink.write_requests) receives the *original* event from
    the batch, so prompt logging still works when enabled. This copy is only used
    for the in-memory ring buffer and live SSE fan-out, where holding hundreds of
    full LLM responses would balloon memory.
    """
    if all(getattr(evt, f) is None for f in _HEAVY_FIELDS):
        return evt  # already lightweight; avoid an unnecessary copy
    return replace(evt, **{f: None for f in _HEAVY_FIELDS})


class SSEBroadcastSink:
    """Fan-out to admin SSE clients. Ring buffer per stream for Last-Event-ID replay."""

    def __init__(self, ring_size: int = 500):
        self._subs: dict[str, list[asyncio.Queue]] = {"request": [], "proxy": [],
                                                      "audit": []}
        self._rings: dict[str, deque] = {
            "request": deque(maxlen=ring_size),
            "proxy": deque(maxlen=ring_size),
            # Audit events had no ring, so they were dropped outright whenever
            # the DB sink was unavailable (they bypass the queue path and are
            # awaited directly). A ring keeps them replayable.
            "audit": deque(maxlen=ring_size),
        }
        self._seq = 0
        self._lock = asyncio.Lock()

    async def subscribe(self, stream: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subs[stream].append(q)
        return q

    async def unsubscribe(self, stream: str, q: asyncio.Queue) -> None:
        async with self._lock:
            if q in self._subs[stream]:
                self._subs[stream].remove(q)

    async def replay(self, stream: str, last_event_id: int) -> list[tuple[int, LogEvent]]:
        # Snapshot the ring under the lock so a concurrent publish() cannot
        # mutate the deque while we iterate it.
        async with self._lock:
            return [(i, e) for i, e in self._rings[stream] if i > last_event_id]

    async def publish(self, stream: str, event: LogEvent) -> None:
        # Store a lightweight copy (prompt/response bodies stripped) in the
        # ring buffer and broadcast it to SSE subscribers. The original event
        # remains in the batch list for DBSink.write_requests to persist the
        # full bodies when store_prompts_in_spend_logs is enabled.
        ring_evt = _lightweight_copy(event) if stream == "request" else event
        async with self._lock:
            self._seq += 1
            seq = self._seq
            self._rings[stream].append((seq, ring_evt))
            subs = list(self._subs[stream])
        for q in subs:
            try:
                q.put_nowait((seq, ring_evt))
            except asyncio.QueueFull:
                pass  # slow admin client: drop rather than backpressure the gateway


@dataclass
class RequestTotals:
    """Process-lifetime monotonic totals over the request stream.

    Prometheus counters must not be derived from the 500-event ring: every
    eviction would read as a counter reset, so ``rate()``/``increase()``
    returned negative or wildly wrong values (AUDIT #180). These totals are
    accumulated as each request event is accepted, and ``render_metrics``
    renders them verbatim for the series declared ``counter``. The
    per-scrape-only families (quantile summaries, status/provider
    breakdowns, the cache hit rate) stay window-derived and are declared
    ``gauge``/``summary``, which is what the ring can honestly serve.

    The predicates below must stay identical to the ring-side ones in
    ``render_metrics``: the two agree exactly until the first eviction.
    """

    requests: int = 0
    tok_in: int = 0
    tok_out: int = 0
    tok_cached: int = 0
    tok_cache_creation: int = 0
    tok_reasoning: int = 0
    cost: float = 0.0
    cache_hits: int = 0
    response_cache_hits: int = 0
    usage_estimated: int = 0
    stream_errors: int = 0

    def add(self, evt: LogEvent) -> None:
        """Fold one served request event into the lifetime totals."""
        self.requests += 1
        self.tok_in += evt.tok_in
        self.tok_out += evt.tok_out
        self.tok_cached += evt.tok_cached
        self.tok_cache_creation += evt.tok_cache_creation
        self.tok_reasoning += evt.tok_reasoning
        self.cost += evt.cost
        if evt.cache_hit or evt.tok_cached > 0:
            self.cache_hits += 1
        if evt.response_cache_hit:
            self.response_cache_hits += 1
        if evt.usage_estimated:
            self.usage_estimated += 1
        if evt.status >= 500 and evt.was_stream:
            self.stream_errors += 1


class LoggingSubsystem:
    def __init__(self) -> None:
        self.sse = SSEBroadcastSink()
        self._request_q: asyncio.Queue[LogEvent | None] = asyncio.Queue(maxsize=REQUEST_QUEUE_SIZE)
        self._proxy_q: asyncio.Queue[LogEvent | None] = asyncio.Queue(maxsize=PROXY_QUEUE_SIZE)
        # Process-lifetime counters of lost log events, one per stream and
        # loss mode. They are kept apart rather than merged into a single
        # counter because the causes (and therefore the operator's remedy)
        # differ: a queue-full drop means the log pipeline is saturated,
        # while a failed write means the DB is unavailable while the
        # requests themselves succeeded. ``dropped_log_events`` sums them
        # for a single "is anything being lost?" alert.
        self.dropped_request_logs = 0
        self.failed_request_log_writes = 0
        self.dropped_proxy_logs = 0
        self.failed_audit_log_writes = 0
        self.totals = RequestTotals()
        self._tasks: list[asyncio.Task] = []
        self._db_sink = None  # set by server when DB is available

    @property
    def dropped_log_events(self) -> int:
        """Every log event lost on any stream, by any loss mode."""
        return (self.dropped_request_logs + self.failed_request_log_writes
                + self.dropped_proxy_logs + self.failed_audit_log_writes)

    def set_db_sink(self, sink) -> None:
        self._db_sink = sink

    @property
    def db_sink(self):
        return self._db_sink

    # -- producers (called from request path; never block) --------------------
    def log_request(self, event: LogEvent) -> None:
        # Accumulate the process-lifetime totals at accept time, before the
        # queue can drop the event: a Prometheus counter must count what the
        # gateway served, not what survived the ring buffer (AUDIT #180).
        self.totals.add(event)
        try:
            self._request_q.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_request_logs += 1

    def log_proxy(self, level: str, message: str, request_id: str = "", **kw: object) -> None:
        evt = LogEvent(stream="proxy", ts=time.time(), level=level,  # type: ignore[arg-type]
                       message=message, request_id=request_id)
        try:
            self._proxy_q.put_nowait(evt)
        except asyncio.QueueFull:
            # A saturated proxy queue silently lost the event; count it so
            # /health and /metrics can report the loss (AUDIT #173).
            self.dropped_proxy_logs += 1
        getattr(log, level if level != "warn" else "warning")(message, request_id=request_id, **kw)

    async def log_audit(self, actor: str, action: str, target: str,
                        diff: dict | None = None) -> None:
        evt = LogEvent(stream="audit", ts=time.time(), actor=actor,
                       action=action, target=target, diff=diff or {})
        # Always record to the audit ring first, so the event survives even
        # when there is no DB sink (or the write fails): admin audit trails
        # must not vanish silently on a database outage.
        await self.sse.publish("audit", evt)
        if self._db_sink is not None:
            try:
                await self._db_sink.write_audit(evt)
                return
            except Exception:
                # The mutation this audit row describes has already been
                # applied, so losing the durable row is unrecoverable: the
                # capped ring copy is the only remaining trace. Count it
                # (AUDIT #173).
                self.failed_audit_log_writes += 1
                log.warning("audit_write_failed", actor=actor, action=action,
                            target=target, exc_info=True)
                return
        log.warning("audit_no_db_sink", actor=actor, action=action, target=target)

    async def read_audit(self, limit: int = 200) -> list[dict]:
        """Newest-first audit rows from the in-memory ring.

        The fallback half of the audit trail: ``DBSink.read_audit`` serves the
        durable copy, and this serves the ring when no DB sink is configured
        (or the write failed). Same row shape and ordering contract as the DB
        accessor so the caller renders one format either way — including
        ``diff``, which ``public_dict`` strips (it is noise on the request and
        proxy streams, but it is the entire payload of an audit row).
        """
        ring = list(await self.sse.replay("audit", 0))
        # The ring is oldest→newest, so slice the newest N then reverse to
        # match read_requests' newest-first contract.
        out: list[dict] = []
        for _, e in reversed(ring[-limit:]):
            d = public_dict(e)
            d["diff"] = e.diff
            out.append(d)
        return out

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._pump(self._request_q, "request")),
            asyncio.create_task(self._pump(self._proxy_q, "proxy")),
        ]

    async def stop(self) -> None:
        for q in (self._request_q, self._proxy_q):
            try:
                q.put_nowait(None)  # never block shutdown on a full log queue
            except asyncio.QueueFull:
                pass
        for t in self._tasks:
            try:
                await asyncio.wait_for(t, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                t.cancel()

    async def _pump(self, q: asyncio.Queue, stream: str) -> None:
        batch: list[LogEvent] = []
        while True:
            item = await q.get()
            if item is None:
                break
            batch.append(item)
            while len(batch) < 200:
                try:
                    nxt = q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    q.put_nowait(None)  # re-queue terminator for next drain
                    break
                batch.append(nxt)
            await self._emit(stream, batch)
            batch = []

    async def _emit(self, stream: str, batch: list[LogEvent]) -> None:
        for evt in batch:
            await self.sse.publish(stream, evt)
        if stream == "request" and self._db_sink is not None:
            try:
                await self._db_sink.write_requests(batch)
            except Exception as e:  # noqa: BLE001 — logging must never crash the gateway
                # The batch is discarded and request_logs is the only durable
                # copy, so count every lost row: without this the drop counter
                # stayed at 0 and a DB outage reported itself as healthy
                # (AUDIT #171).
                self.failed_request_log_writes += len(batch)
                log.error("request_log_db_write_failed", error=str(e), count=len(batch))


def public_dict(evt: LogEvent) -> dict:
    d = asdict(evt)
    d.pop("diff", None)
    return d


def encode_sse(seq: int, evt: LogEvent) -> bytes:
    name = "log.created" if evt.stream == "request" else "proxy.log"
    payload = orjson.dumps(public_dict(evt))
    return b"id: " + str(seq).encode() + b"\nevent: " + name.encode() + b"\ndata: " + payload + b"\n\n"
