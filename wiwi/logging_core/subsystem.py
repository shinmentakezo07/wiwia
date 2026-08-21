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
from dataclasses import asdict

import orjson
import structlog

from wiwi.logging_core.events import LogEvent

log = structlog.get_logger(__name__)

REQUEST_QUEUE_SIZE = 50_000
PROXY_QUEUE_SIZE = 10_000


class SSEBroadcastSink:
    """Fan-out to admin SSE clients. Ring buffer per stream for Last-Event-ID replay."""

    def __init__(self, ring_size: int = 500):
        self._subs: dict[str, list[asyncio.Queue]] = {"request": [], "proxy": []}
        self._rings: dict[str, deque] = {
            "request": deque(maxlen=ring_size),
            "proxy": deque(maxlen=ring_size),
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

    def replay(self, stream: str, last_event_id: int) -> list[tuple[int, LogEvent]]:
        return [(i, e) for i, e in self._rings[stream] if i > last_event_id]

    async def publish(self, stream: str, event: LogEvent) -> None:
        async with self._lock:
            self._seq += 1
            seq = self._seq
            self._rings[stream].append((seq, event))
            subs = list(self._subs[stream])
        for q in subs:
            try:
                q.put_nowait((seq, event))
            except asyncio.QueueFull:
                pass  # slow admin client: drop rather than backpressure the gateway


class LoggingSubsystem:
    def __init__(self) -> None:
        self.sse = SSEBroadcastSink()
        self._request_q: asyncio.Queue[LogEvent | None] = asyncio.Queue(maxsize=REQUEST_QUEUE_SIZE)
        self._proxy_q: asyncio.Queue[LogEvent | None] = asyncio.Queue(maxsize=PROXY_QUEUE_SIZE)
        self.dropped_request_logs = 0
        self._tasks: list[asyncio.Task] = []
        self._db_sink = None  # set by server when DB is available

    def set_db_sink(self, sink) -> None:
        self._db_sink = sink

    # -- producers (called from request path; never block) --------------------
    def log_request(self, event: LogEvent) -> None:
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
            pass
        getattr(log, level if level != "warn" else "warning")(message, request_id=request_id, **kw)

    async def log_audit(self, actor: str, action: str, target: str,
                        diff: dict | None = None) -> None:
        evt = LogEvent(stream="audit", ts=time.time(), actor=actor,
                       action=action, target=target, diff=diff or {})
        if self._db_sink is not None:
            await self._db_sink.write_audit(evt)

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._pump(self._request_q, "request")),
            asyncio.create_task(self._pump(self._proxy_q, "proxy")),
        ]

    async def stop(self) -> None:
        await self._request_q.put(None)
        await self._proxy_q.put(None)
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
                log.error("request_log_db_write_failed", error=str(e), count=len(batch))


def public_dict(evt: LogEvent) -> dict:
    d = asdict(evt)
    d.pop("diff", None)
    return d


def encode_sse(seq: int, evt: LogEvent) -> bytes:
    name = "log.created" if evt.stream == "request" else "proxy.log"
    payload = orjson.dumps(public_dict(evt))
    return b"id: " + str(seq).encode() + b"\nevent: " + name.encode() + b"\ndata: " + payload + b"\n\n"
