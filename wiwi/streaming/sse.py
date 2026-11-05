"""Incremental SSE parsing (upstream) and frame writing helpers."""

from __future__ import annotations

import codecs
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class SSEEvent:
    event: str  # event name ("" when absent, e.g. OpenAI data-only frames)
    data: str   # joined data payload


# The SSE wire format defines exactly three line terminators (WHATWG HTML §9.2.6):
# CRLF, a lone CR, and LF. Nothing else ends a line.
_LINE_END = re.compile(r"\r\n|\r|\n")


class SSELineSplitter:
    """Split a decoded SSE body into lines on the wire's own terminators.

    **Not** ``str.splitlines``. That splits on the whole Unicode newline family
    — ``\\x0b \\x0c \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029`` as well as CR/LF —
    and three of those (``\\x85`` NEL, ``\\u2028`` LINE SEPARATOR, ``\\u2029``
    PARAGRAPH SEPARATOR) are legal *unescaped* inside a JSON string, which is
    what ``orjson`` emits. An upstream reply containing U+2028 therefore used to
    arrive here as two half-lines: the parser saw a truncated ``data:`` payload,
    every adapter's ``decode_stream_event`` answered ``[]`` for it (a bad JSON
    frame is dropped, never raised), and the client silently lost that delta
    with a clean terminal frame after it — a corrupted turn that nothing
    downstream can detect (AUDIT #566).

    ``httpx.Response.aiter_lines`` has exactly this defect, so the streaming
    paths must not use it; feed :func:`iter_sse_lines` the raw body chunks
    instead.

    ``feed``/``flush`` mirror :class:`LineSSEParser`'s shape: ``feed`` returns
    every line completed by the text so far, holding back a trailing CR that
    may be the first half of a CRLF whose LF is still in flight; ``flush``
    returns the final unterminated line.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        start = 0
        while True:
            m = _LINE_END.search(self._buf, start)
            if m is None:
                break
            # A CR at the very end may be half of a CRLF split across chunks;
            # wait for the next feed rather than inventing a line boundary.
            if m.group() == "\r" and m.end() == len(self._buf):
                break
            out.append(self._buf[start:m.start()])
            start = m.end()
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        """Emit the final line, terminated or not (a body may end on its last
        ``data:`` line — DeepSeek/B.A.I close with ``"data: [DONE]\\n"``)."""
        buf, self._buf = self._buf, ""
        if not buf:
            return []
        # A trailing CR is a terminator once the body is over, not half of a
        # CRLF that will never arrive.
        buf = buf.removesuffix("\r")
        return _LINE_END.split(buf)


def split_sse_lines(text: str) -> list[str]:
    """Split an already-decoded SSE body into lines (see :class:`SSELineSplitter`).

    The whole-body counterpart of :func:`iter_sse_lines`, for callers that hold
    the complete body rather than a chunk stream — the healer's probe
    classifier is the one in-tree user.
    """
    splitter = SSELineSplitter()
    return splitter.feed(text) + splitter.flush()


async def iter_sse_lines(chunks: AsyncIterator[bytes],
                         encoding: str = "utf-8") -> AsyncIterator[str]:
    """Turn raw upstream body chunks into SSE lines.

    Use this instead of ``httpx.Response.aiter_lines()``, which splits on the
    Unicode newline family rather than the SSE wire's terminators and so tears
    any frame whose JSON payload carries U+2028/U+2029/U+0085 (AUDIT #566).

    Decoding is incremental, so a multi-byte character split across two chunks
    is reassembled rather than mangled; ``errors="replace"`` matches what httpx
    itself does. Pass ``resp.encoding`` so a non-UTF-8 body is decoded the way
    the response declared.
    """
    decoder = codecs.getincrementaldecoder(encoding or "utf-8")(errors="replace")
    splitter = SSELineSplitter()
    async for chunk in chunks:
        for line in splitter.feed(decoder.decode(chunk)):
            yield line
    for line in splitter.feed(decoder.decode(b"", True)):
        yield line
    for line in splitter.flush():
        yield line


def _looks_like_payload(line: str) -> bool:
    """True when a line is a self-contained payload rather than an SSE field.

    The unframed mode exists for the envelope providers (Cline, WorkBuddy),
    which answer with one whole JSON object per line. Recognising the shape
    is what keeps that mode from also claiming the SSE *field* lines
    (``id:``, ``retry:``) — a line that starts with a letter is a field, and
    a field is not a payload (AUDIT #502).
    """
    stripped = line.lstrip()
    return stripped.startswith(("{", "["))


class LineSSEParser:
    """Feed lines from :func:`iter_sse_lines`; yields SSEEvent at blank-line boundaries.

    ``allow_unframed`` handles providers that answer a "streaming" request with
    **one whole JSON object per line** instead of framed SSE — Cline and
    WorkBuddy do this, and they are streaming-only, so the shape reaches every
    streaming client. Such a line has no ``data:``/``event:``/``:`` prefix, so
    the strict parser silently discards it; with ``allow_unframed`` it is
    emitted as an immediate data event instead. Off by default, because a strict
    caller (one that would rather drop a malformed line than misread it) must
    keep the old behaviour. If a bare line arrives while a framed event is
    buffered, the older frame is returned first and the bare event is queued
    for the next call (or ``flush``); that call still consumes its input line.
    """

    def __init__(self, allow_unframed: bool = False) -> None:
        self._event = ""
        self._data: list[str] = []
        self._allow_unframed = allow_unframed
        # A bare JSON line terminates itself, but a pending framed event must
        # still be delivered first. Hold that later event until the next call
        # so feed_line keeps its one-event-per-call API.
        self._pending: SSEEvent | None = None

    def feed_line(self, line: str) -> SSEEvent | None:
        if self._pending is not None:
            pending, self._pending = self._pending, None
            # The queued event is the result of THIS call, but the line that
            # triggered it still needs parsing into the next frame's buffer.
            self.feed_line(line)
            return pending
        line = line.removeprefix("\ufeff")
        if line == "":
            if self._data:
                evt = SSEEvent(self._event, "\n".join(self._data))
                self._event, self._data = "", []
                return evt
            self._event = ""
            return None
        if line.startswith(":"):
            return None  # comment/heartbeat
        if line.startswith("event:"):
            self._event = line[6:].removeprefix(" ")
        elif line.startswith("data:"):
            self._data.append(line[5:].removeprefix(" "))
        elif line.startswith(("id:", "retry:")):
            # A FIELD of the frame, not a payload (AUDIT #502). The unframed
            # catch-all below used to claim these lines, so the parser
            # emitted an EXTRA event per field line whose payload was the
            # literal text "id: 42" — a payload no adapter can parse, so it
            # was decoded and discarded. The frame's own payload was never
            # affected.
            #
            # The id is the UPSTREAM's own frame cursor, not wiwi's: wiwi
            # re-numbers every frame it emits (see ``_inject_id``), so the
            # upstream's id is not the client's ``Last-Event-ID`` and is
            # dropped. ``retry:`` is a reconnection hint the gateway does not
            # act on.
            return None
        elif self._allow_unframed and _looks_like_payload(line):
            # One self-contained JSON object on its own line; it terminates
            # itself, so emit immediately rather than buffering for a blank line
            # that an envelope body never sends.
            #
            # The shape test is what keeps this from swallowing an unknown
            # SSE FIELD line: only a line that actually looks like a payload
            # qualifies (AUDIT #502).
            if self._data:
                self._pending = SSEEvent("", line)
                return self.flush()
            return SSEEvent("", line)
        return None


    def flush(self) -> SSEEvent | None:
        """Emit a pending frame at end of stream.

        ``feed_line`` only emits at a blank-line boundary, so a stream that ends
        on the last ``data:`` line with no trailing blank line — DeepSeek/B.A.I
        close with ``"data: [DONE]\\n"`` — would drop the final frame. Call once
        after the line feed loop completes. Idempotent: after emitting, the
        buffer is drained and a second flush returns None.
        """
        if self._data:
            evt = SSEEvent(self._event, "\n".join(self._data))
            self._event, self._data = "", []
            return evt
        self._event = ""
        pending, self._pending = self._pending, None
        return pending


def split_frames(chunk: bytes) -> list[bytes]:
    """Split one chunk of SSE bytes into its individual frames.

    A chunk may carry several frames joined by blank-line boundaries — the
    encoders emit them that way (chat's ``final_frame()`` is the finish chunk
    plus the usage chunk, Anthropic's is ``content_block_stop`` plus
    ``message_delta``). The frames are returned in order WITHOUT the blank-line
    terminator; empty segments (a leading or trailing separator) are dropped.

    The blank-line boundary is the only delimiter the SSE wire format has, and
    wiwi writes single-line ``data:`` payloads (JSON has no raw newlines), so
    a split on it is exact for the frames this codebase produces — the same
    assumption ``_inject_id`` already makes.
    """
    return [f for f in chunk.split(b"\n\n") if f]


def _sse_sanitize(value: str) -> str:
    """Strip CR/LF so a caller-controlled value cannot inject extra SSE lines
    (or a full forged frame) into the output stream."""
    return value.replace("\r", "").replace("\n", "")


def sse_frame(event: str, payload: str | bytes,
              event_id: int | str | None = None) -> bytes:
    """Build a single SSE frame.

    When *event_id* is provided, an ``id:`` line is included so clients can
    reconnect with ``Last-Event-ID`` to resume from where they left off.
    """
    parts: list[bytes] = []
    if event_id is not None:
        parts.append(f"id: {_sse_sanitize(str(event_id))}\n".encode())
    if event:
        parts.append(f"event: {_sse_sanitize(event)}\n".encode())
    data = payload.encode() if isinstance(payload, str) else payload
    parts.append(b"data: " + data + b"\n\n")
    return b"".join(parts)


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Convenience: wrap :func:`iter_sse_lines` into (event, data) tuples.

    Yields the ("done", "[DONE]") sentinels too.

    The parser is flushed once the line feed ends. ``feed_line`` only emits at a
    blank-line boundary, so a body that closes on its last ``data:`` line with
    no trailing blank line — DeepSeek/B.A.I end with ``"data: [DONE]\\n"`` —
    would lose that final frame, and with it the terminal sentinel every
    decoder needs to finish a turn cleanly (AUDIT #565). ``flush`` is
    idempotent, so a body that *does* end on a blank line yields nothing extra.
    """
    parser = LineSSEParser()
    async for line in lines:
        evt = parser.feed_line(line)
        if evt is not None:
            yield evt.event, evt.data
    evt = parser.flush()
    if evt is not None:
        yield evt.event, evt.data
