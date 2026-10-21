"""Incremental SSE parsing (upstream) and frame writing helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class SSEEvent:
    event: str  # event name ("" when absent, e.g. OpenAI data-only frames)
    data: str   # joined data payload


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
    """Feed lines from `aiter_lines()`; yields SSEEvent at blank-line boundaries.

    ``allow_unframed`` handles providers that answer a "streaming" request with
    **one whole JSON object per line** instead of framed SSE — Cline and
    WorkBuddy do this, and they are streaming-only, so the shape reaches every
    streaming client. Such a line has no ``data:``/``event:``/``:`` prefix, so
    the strict parser silently discards it; with ``allow_unframed`` it is
    emitted as an immediate data event instead. Off by default, because a strict
    caller (one that would rather drop a malformed line than misread it) must
    keep the old behaviour.
    """

    def __init__(self, allow_unframed: bool = False) -> None:
        self._event = ""
        self._data: list[str] = []
        self._allow_unframed = allow_unframed

    def feed_line(self, line: str) -> SSEEvent | None:
        line = line.removeprefix("\ufeff").removesuffix("\r")
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
        return None


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
    """Convenience: wrap aiter_lines() into (event, data) tuples. Yields ("done","[DONE]") sentinels too."""
    parser = LineSSEParser()
    async for line in lines:
        evt = parser.feed_line(line)
        if evt is not None:
            yield evt.event, evt.data
