"""Delta coalescer: merge consecutive TextDeltas under backpressure.

When the client is slow to consume, the queue fills up and the pump blocks.
Instead of sending thousands of micro-frames, the coalescer merges
consecutive ``TextDelta``s into fewer, larger chunks — reducing SSE frame
overhead and client-side parse work.

It activates only when queue depth exceeds a threshold; for fast consumers,
the coalescer is a no-op (single delta passes through immediately).

Never coalesces across ``ToolCallOpen/Close``, ``ThinkingDelta``, ``UsageFinal``,
``Finish``, ``StreamEnd``, or ``StreamError`` — these are control deltas whose
ordering contract must be preserved.
"""

from __future__ import annotations

import time

from wiwi.streaming import deltas as dl

# Delta types that can be safely merged.
_MERGEABLE = dl.TextDelta


class DeltaCoalescer:
    """Merge consecutive TextDeltas when queue depth is high.

    Call :meth:`feed` for each delta. When the queue depth is below the
    threshold, deltas pass through immediately. When above, consecutive
    TextDeltas are buffered and flushed when:
    - a non-mergeable delta arrives,
    - the buffer exceeds max_bytes,
    - or max_ms has elapsed since the first buffered delta.
    """

    def __init__(self, max_bytes: int = 8192, max_ms: float = 50.0,
                 threshold: int = 100) -> None:
        self._max_bytes = max_bytes
        self._max_ms = max_ms
        self._threshold = threshold
        self._buf: list[str] = []
        self._buf_bytes = 0
        self._buf_start: float = 0.0

    def feed(self, delta: dl.IRStreamDelta, queue_depth: int = 0) -> list[dl.IRStreamDelta]:
        """Process *delta* and return the list of deltas to emit.

        *queue_depth* is the current depth of the output queue. When below
        *threshold*, all buffering is bypassed for zero-overhead passthrough.
        """
        if queue_depth < self._threshold:
            # Fast path: flush any pending buffer, then pass through.
            out = self._flush()
            out.append(delta)
            return out

        if isinstance(delta, _MERGEABLE):
            # Buffer the text for coalescing.
            if not self._buf:
                self._buf_start = time.monotonic()
            self._buf.append(delta.text)
            self._buf_bytes += len(delta.text)
            # Check flush conditions.
            if (self._buf_bytes >= self._max_bytes
                    or (time.monotonic() - self._buf_start) * 1000 >= self._max_ms):
                return self._flush()
            return []
        else:
            # Non-mergeable: flush buffer, then pass through.
            out = self._flush()
            out.append(delta)
            return out

    def _flush(self) -> list[dl.IRStreamDelta]:
        if not self._buf:
            return []
        merged = dl.TextDelta(text="".join(self._buf))
        self._buf.clear()
        self._buf_bytes = 0
        self._buf_start = 0.0
        return [merged]

    def drain(self) -> list[dl.IRStreamDelta]:
        """Flush any remaining buffered deltas."""
        return self._flush()
