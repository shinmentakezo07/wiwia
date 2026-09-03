"""Durable stream journal: per-request JSONL of encoded SSE chunks.

Closes the restart-durability gap: StreamTape is in-process, so a wiwi kill
mid-stream left a reconnecting client with no memory of prior content. With
the journal, ``_stream_response`` appends every encoded SSE chunk (post
id-injection, base64) to ``<dir>/<request_id>.jsonl``; a reconnecting client
sends ``x-wiwi-stream-id: <request_id>`` + ``Last-Event-ID: <chunk seq>`` and
the same surface replays chunks > last_event_id from the journal, then tails
the file if the original request is still streaming.

Line schema (one JSON object per line):
    {"seq": <int>, "ts": <unix seconds>, "data": "<base64 SSE chunk>",
     "done": <bool>}
``seq`` is the monotonic chunk counter shared with SSE id injection; the
``done`` line is written once with ``data: ""`` when the original stream
terminates. Journals expire after ``stream_journal_ttl_s`` and are swept at
startup plus opportunistically at write-finish.
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path

import orjson


class StreamJournal:
    """Append-only journal for one request's encoded SSE chunks."""

    def __init__(self, path: Path, max_bytes: int = 1_048_576) -> None:
        self.path = path
        self._max_bytes = max(1024, max_bytes)
        self._overflow = False
        self._fh = None
        self._lock = asyncio.Lock()
        self._last_seq = 0

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @staticmethod
    def _encode_record(seq: int, chunk: bytes, done: bool) -> bytes:
        return orjson.dumps({
            "seq": seq,
            "ts": time.time(),
            "data": base64.b64encode(chunk).decode("ascii"),
            "done": done,
        }) + b"\n"

    def _open_sync(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return open(self.path, "ab")

    async def append(self, seq: int, chunk: bytes, done: bool = False) -> None:
        record = self._encode_record(seq, chunk, done)

        def _write(fh, path) -> None:
            # Per-journal byte cap: once exceeded, stop appending for the rest
            # of the stream. Replay degrades to the first max_bytes of chunks
            # (the done record never lands, so tail-followers expire by TTL).
            if not self._overflow:
                try:
                    if path.stat().st_size + len(record) > self._max_bytes:
                        self._overflow = True
                except OSError:
                    pass
            if self._overflow:
                return
            fh.write(record)
            fh.flush()

        async with self._lock:
            if self._fh is None:
                self._fh = await asyncio.to_thread(self._open_sync)
            await asyncio.to_thread(_write, self._fh, self.path)
            if not done:
                self._last_seq = seq

    async def finish(self, seq: int) -> None:
        await self.append(seq, b"", done=True)
        await self.aclose()

    async def aclose(self) -> None:
        async with self._lock:
            if self._fh is not None:
                fh, self._fh = self._fh, None
                await asyncio.to_thread(fh.close)


class JournalStore:
    """Registry of active journals + sweep/replay operations on the journal dir."""

    def __init__(self, directory: str | Path, ttl_s: float,
                 max_bytes: int) -> None:
        self.dir = Path(directory)
        self.ttl_s = max(1.0, ttl_s)
        self.max_bytes = max_bytes
        self._active: dict[str, StreamJournal] = {}
        self._lock = asyncio.Lock()

    def path_for(self, request_id: str) -> Path:
        safe = "".join(c for c in request_id if c.isalnum() or c in "-_")
        return self.dir / f"{safe}.jsonl"

    async def open(self, request_id: str) -> StreamJournal:
        async with self._lock:
            j = self._active.get(request_id)
            if j is None:
                j = StreamJournal(self.path_for(request_id),
                                  max_bytes=self.max_bytes)
                # Touch eagerly: a reconnect arriving before the first chunk
                # must find the file rather than fall through to a duplicate
                # upstream dispatch.
                try:
                    j.path.parent.mkdir(parents=True, exist_ok=True)
                    j.path.touch(exist_ok=True)
                except OSError:
                    pass
                self._active[request_id] = j
            return j

    def release(self, request_id: str) -> None:
        self._active.pop(request_id, None)

    def read_after(self, request_id: str, last_seq: int) -> list[tuple[int, bytes]]:
        """Read data records with seq > last_seq, in order.

        Done-marker records are internal (empty payload, not client-visible
        SSE) and are excluded — use :meth:`is_complete` for termination.
        Tolerant of concurrent appends: partial trailing lines are ignored.
        """
        out: list[tuple[int, bytes]] = []
        for seq, chunk, done in self._read_records(request_id, last_seq):
            if not done:
                out.append((seq, chunk))
        return out

    def _read_records(self, request_id: str, last_seq: int) -> list[tuple[int, bytes, bool]]:
        path = self.path_for(request_id)
        if not path.exists():
            return []
        out: list[tuple[int, bytes, bool]] = []
        try:
            raw = path.read_bytes()
        except OSError:
            return []
        for line in raw.split(b"\n"):
            if not line:
                continue
            try:
                rec = orjson.loads(line)
            except ValueError:
                continue  # torn tail from a concurrent append
            seq = rec.get("seq")
            if not isinstance(seq, int) or seq <= last_seq:
                continue
            out.append((seq,
                        base64.b64decode(rec.get("data", "")),
                        bool(rec.get("done", False))))
        return out

    def is_complete(self, request_id: str) -> bool:
        """True when the journal carries the original stream's done record."""
        return any(done for _, _, done in self._read_records(request_id, 0))

    def is_expired(self, request_id: str, now: float | None = None) -> bool:
        path = self.path_for(request_id)
        if not path.exists():
            return True
        now = time.time() if now is None else now
        try:
            return now - path.stat().st_mtime > self.ttl_s
        except OSError:
            return True

    def sweep(self, now: float | None = None) -> int:
        """Delete expired journals. Returns count removed."""
        now = time.time() if now is None else now
        removed = 0
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            entries = list(self.dir.iterdir())
        except OSError:
            return 0
        for p in entries:
            if not p.name.endswith(".jsonl"):
                continue
            try:
                if now - p.stat().st_mtime > self.ttl_s:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed
