"""Durable stream journal: per-request JSONL of encoded SSE chunks.

Closes the restart-durability gap: StreamTape is in-process, so a wiwi kill
mid-stream left a reconnecting client with no memory of prior content. With
the journal, ``_stream_response`` appends every encoded SSE chunk (post
id-injection, base64) to ``<dir>/<request_id>.jsonl``; a reconnecting client
sends ``x-wiwi-stream-id: <request_id>`` + ``Last-Event-ID: <chunk seq>`` and
the same surface replays chunks > last_event_id from the journal, then tails
the file if the original request is still streaming. ``path_for`` owns that
id → file mapping and is injective: the id every client reads back in its
``x-wiwi-request-id`` header must never name another stream's journal, or the
per-key scoping below would be defeatable by a crafted id (AUDIT #191).

Line schema (one JSON object per line):
    {"seq": <int>, "ts": <unix seconds>, "data": "<base64 SSE chunk>",
     "done": <bool>}
``seq`` is the monotonic chunk counter shared with SSE id injection; the
``done`` line is written once with ``data: ""`` when the original stream
terminates. Journals expire after ``stream_journal_ttl_s`` and are swept at
startup and on a periodic background timer (see the lifespan sweep loop in
``server/app.py``); ``release()`` does not sweep.

Ownership: ``open()`` records the virtual key that originated the request in
the journal file itself (an ``owner`` line written before the first chunk
record), so replay can be scoped to that key across restarts — not just while
the journal is held in memory. ``owner_of()`` reads it back; ``is_active()``
reports same-process liveness (the half of the #66 replay gate that
``read_after`` + ``is_complete`` cannot see: an active-but-empty journal).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import re
import time
from pathlib import Path

import orjson
import structlog

# Request ids the journal directory may name directly. ``RequestContext``
# generates ``uuid4().hex[:16]``; a client-supplied ``x-wiwi-stream-id`` is
# adopted verbatim when a reconnect misses the replay gate (AUDIT #117), so
# this alphabet is the gate that keeps a crafted id out of another stream's
# file (AUDIT #191).
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class JournalTail:
    """Byte-offset cursor over one journal file, for reconnect tailing.

    ``JournalStore.read_after`` re-reads and re-parses the whole file on every
    call. The reconnect tail loop polls every 50 ms per client, so with a 1 MiB
    journal that is a sustained full re-parse of the file — megabytes per second
    of event-loop time, used only to discover that nothing new was appended.
    This cursor keeps the offset already consumed and reads strictly forward,
    so an idle poll costs one ``stat`` and one empty ``read``.

    Only appends are supported (the journal is append-only by construction). A
    partial trailing line left by a concurrent append is not consumed: the
    offset is advanced only past complete lines, so the next poll re-reads it
    whole. ``truncated()`` reports the one case that would silently corrupt the
    cursor's assumption — the file shrinking (a sweep unlinking it, or the
    path being reused) — so the caller can fall back to a full read.
    """

    __slots__ = ("_last_seq", "_offset", "_path")

    def __init__(self, path: Path, last_seq: int) -> None:
        self._path = path
        self._offset = 0
        self._last_seq = last_seq

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def truncated(self) -> bool:
        """True when the file is smaller than what has been consumed."""
        try:
            return self._path.stat().st_size < self._offset
        except OSError:
            return False

    def read_next(self) -> list[tuple[int, bytes, bool]]:
        """Consume complete lines appended since the last call.

        Returns ``(seq, chunk, done)`` triples in order. Ownership records
        (``seq == 0``, no payload) are skipped, matching ``read_after``.
        """
        try:
            with open(self._path, "rb") as fh:
                fh.seek(self._offset)
                raw = fh.read()
        except OSError:
            return []
        if not raw:
            return []
        # Only consume up to the last complete line; a torn tail is re-read.
        cut = raw.rfind(b"\n")
        if cut < 0:
            return []
        consumed = raw[: cut + 1]
        self._offset += len(consumed)
        out: list[tuple[int, bytes, bool]] = []
        for line in consumed.split(b"\n"):
            if not line:
                continue
            try:
                rec = orjson.loads(line)
            except ValueError:
                continue
            seq = rec.get("seq")
            if not isinstance(seq, int):
                continue
            done = bool(rec.get("done", False))
            if seq == 0 and not done:
                continue  # ownership record: no client-visible payload
            # A done record is NEVER filtered by the sequence cursor:
            # ``StreamJournal.finish(seq)`` writes it with the SAME seq as the
            # last data record, so by the time it is reached ``_last_seq``
            # already equals it and a plain ``seq <= _last_seq`` test dropped it
            # — leaving the tail loop running until the journal TTL expired
            # instead of terminating on the done record (AUDIT #282).
            # ``read_after`` never hit this because it filters against the
            # caller's fixed ``last_seq`` rather than an advancing cursor.
            if not done and seq <= self._last_seq:
                continue
            self._last_seq = max(self._last_seq, seq)
            out.append((seq, base64.b64decode(rec.get("data", "")), done))
        return out


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
        self._sweeper: asyncio.Task | None = None

    def path_for(self, request_id: str) -> Path:
        """Journal file for *request_id*.

        Injective (AUDIT #191). Stripping disallowed characters mapped
        ``'a/b'``, ``'a.b'``, ``'a b'`` and ``'a!b'`` all onto ``ab.jsonl``,
        so appending one stripped character to the request id every client
        sees in its ``x-wiwi-request-id`` header aliased the victim's exact
        journal — satisfying the #67 owner gate by varying only the stripped
        characters. A conforming id keeps the historical ``<id>.jsonl``
        name; anything else is hashed, so distinct ids never share a file
        and no crafted id can name a real journal. The ``h`` prefix pushes
        the hashed name (65 chars) outside the conforming alphabet, so the
        hashed and plain spaces cannot collide either.
        """
        if _ID_RE.fullmatch(request_id):
            return self.dir / f"{request_id}.jsonl"
        # ``surrogatepass``: the digest must stay injective over *every* str,
        # and the default handler folds distinct lone surrogates onto U+FFFD.
        digest = hashlib.sha256(
            request_id.encode("utf-8", "surrogatepass")).hexdigest()
        return self.dir / f"h{digest}.jsonl"

    def is_active(self, request_id: str) -> bool:
        """True when a journal for *request_id* is open in THIS process.

        The replay gate's missing half (AUDIT #66): an empty-but-active
        journal means the original stream is still running here, so a
        reconnect must tail it, never re-dispatch (double billing).
        """
        return request_id in self._active

    async def open(self, request_id: str,
                   key_id: str | None = None) -> StreamJournal:
        """Open (or return the existing) journal for *request_id*.

        *key_id* records the originating virtual key so replay can be scoped
        (AUDIT #67): a journal must only be readable by the key that created
        it. It is written as the FIRST record (an internal ownership line
        with ``owner`` set and no client-visible payload); ``read_after``
        ignores it like a done-marker. ``owner_of`` reads it back.
        """
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
                if key_id is not None:
                    # Ownership record: seq=0 keeps it out of every data
                    # replay (read_after filters seq > last_seq with
                    # last_seq >= 0, and data records start at seq 1).
                    rec = orjson.dumps({
                        "seq": 0, "ts": time.time(), "owner": key_id,
                    }) + b"\n"

                    def _write_owner(path=rec) -> None:
                        with open(j.path, "ab") as fh:
                            fh.write(path)

                    try:
                        await asyncio.to_thread(_write_owner)
                    except OSError:
                        pass  # best-effort: replay scoping degrades to none
                self._active[request_id] = j
            return j

    def owner_of(self, request_id: str) -> str | None:
        """The originating key id recorded at open(), or None (unknown or
        written by a pre-scoping version)."""
        for seq, _chunk, _done, owner in self._read_records(request_id, -1):
            if seq == 0 and owner is not None:
                return owner
        return None

    def release(self, request_id: str) -> None:
        """Drop *request_id* from the active set (the stream finished or the
        process-local journal holder is gone). The file stays on disk for
        reconnect replay until the TTL sweep removes it. Ownership survives —
        it is recorded in the file, not memory."""
        self._active.pop(request_id, None)

    def read_after(self, request_id: str, last_seq: int) -> list[tuple[int, bytes]]:
        """Read data records with seq > last_seq, in order.

        Done-marker and ownership records are internal (empty payload, not
        client-visible SSE) and are excluded — use :meth:`is_complete` for
        termination and :meth:`owner_of` for scoping. Tolerant of concurrent
        appends: partial trailing lines are ignored.
        """
        out: list[tuple[int, bytes]] = []
        for seq, chunk, done, _owner in self._read_records(request_id, last_seq):
            if not done:
                out.append((seq, chunk))
        return out

    def _read_records(self, request_id: str,
                      last_seq: int) -> list[tuple[int, bytes, bool, str | None]]:
        path = self.path_for(request_id)
        if not path.exists():
            return []
        out: list[tuple[int, bytes, bool, str | None]] = []
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
            owner = rec.get("owner")
            out.append((seq,
                        base64.b64decode(rec.get("data", "")),
                        bool(rec.get("done", False)),
                        owner if isinstance(owner, str) else None))
        return out


    def tail_reader(self, request_id: str, last_seq: int) -> JournalTail:
        """An incremental reader for the reconnect tail loop (AUDIT #282).

        ``read_after`` re-reads and re-parses the ENTIRE journal on every call,
        and the tail loop calls it every 50 ms per reconnecting client — with a
        1 MiB per-journal cap that is ~20 full megabyte-scale parses per second
        of event-loop time, growing with the journal. The tail cursor instead
        remembers the byte offset it has consumed, so each poll reads only the
        bytes appended since the previous one.

        A partial trailing line (a concurrent append caught mid-write) is left
        unconsumed and re-read next poll, which is the same tolerance
        ``_read_records`` documents.
        """
        return JournalTail(self.path_for(request_id), last_seq)

    def is_complete(self, request_id: str) -> bool:
        """True when the journal carries the original stream's done record."""
        return any(done for _, _, done, _owner
                   in self._read_records(request_id, 0))

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

    async def sweep_forever(self, interval_s: float = 60.0) -> None:
        """Background sweeper body: run :meth:`sweep` every *interval_s*.

        The startup sweep in ``server/app.py``'s lifespan only clears journals
        left by previous processes; a long-lived server would otherwise never
        expire its own journals until restart. Prefer :meth:`start` /
        :meth:`stop` for lifecycle management.
        """
        log = structlog.get_logger("wiwi.journals")
        while True:
            await asyncio.sleep(interval_s)
            try:
                removed = await asyncio.to_thread(self.sweep)
            except Exception:  # the sweeper must never die
                log.warning("journal_sweep_failed", exc_info=True)
                continue
            if removed:
                log.info("swept_stale_stream_journals", removed=removed)

    def start(self, interval_s: float = 60.0) -> None:
        """Start the background TTL sweeper task (idempotent).

        Same start/stop worker convention as ClineAutoRefresh / HealthHealer;
        driven by the lifespan in ``server/app.py``.
        """
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self.sweep_forever(interval_s))

    async def stop(self) -> None:
        """Cancel the background sweeper started by :meth:`start`."""
        task, self._sweeper = self._sweeper, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
