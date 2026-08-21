"""Incremental SSE parsing (upstream) and frame writing helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class SSEEvent:
    event: str  # event name ("" when absent, e.g. OpenAI data-only frames)
    data: str   # joined data payload


class LineSSEParser:
    """Feed lines from `aiter_lines()`; yields SSEEvent at blank-line boundaries."""

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def feed_line(self, line: str) -> SSEEvent | None:
        line = line.removesuffix("\r")
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
            self._event = line[6:].strip()
        elif line.startswith("data:"):
            self._data.append(line[5:].strip())
        return None


def sse_frame(event: str, payload: str | bytes) -> bytes:
    name = f"event: {event}\n" if event else ""
    data = payload.encode() if isinstance(payload, str) else payload
    return name.encode() + b"data: " + data + b"\n\n"


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Convenience: wrap aiter_lines() into (event, data) tuples. Yields ("done","[DONE]") sentinels too."""
    parser = LineSSEParser()
    async for line in lines:
        evt = parser.feed_line(line)
        if evt is not None:
            yield evt.event, evt.data
