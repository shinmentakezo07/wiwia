"""Incremental repetition detector for streaming text.

The gateway aborts a stream when the model starts emitting the same content
over and over — a degenerate loop that would otherwise run until the token
limit or the client's patience runs out.

The naive check ("is the whole window periodic?") is O(n^2) in the window
size and was previously run on **every token** once the window filled, which
cost hundreds of microseconds per token at the shipped ``stream_loop_limit``
of 100.  This module keeps the same detection semantics for short periods —
where real degenerate loops live — while doing O(1) work per token.

Detection model: for each candidate period ``p`` (1..8) track the length of
the run of consecutive tokens that each equal the token ``p`` positions back.
When a run reaches ``limit - p``, the window is periodic with period ``p``,
which is exactly the condition the old full-window scan raised on.

The period cap is deliberate.  Periods above :data:`MAX_LOOP_PERIOD` are not
detected: a genuine period-40 loop does not fit the "same chunk repeatedly"
failure mode, and covering it would require the quadratic scan back.  Periods
1..8 are what runaway decoding actually produces.
"""

from __future__ import annotations

from collections import deque

#: Longest repetition period detected.  Bounds per-token work to 8 comparisons.
MAX_LOOP_PERIOD = 8


class LoopDetector:
    """Detect ``limit`` consecutive repetitions of a short-period pattern.

    Feed each text fragment with :meth:`feed`; it returns ``True`` once the
    stream is repeating and should be aborted.  Safe to construct with
    ``limit <= 0``, which disables detection (every feed returns ``False``).
    """

    __slots__ = ("_limit", "_max_period", "_recent", "_runs")

    def __init__(self, limit: int) -> None:
        self._limit = max(0, limit)
        if self._limit <= 0:
            self._max_period = 0
            self._recent: deque[str] = deque(maxlen=1)
            self._runs: list[int] = []
            return
        # Period p needs at least p prior tokens plus a run of limit - p, so
        # periods above limit // 2 can never satisfy the threshold.  Matching
        # the old scan's n // 2 bound keeps behaviour identical.
        self._max_period = min(MAX_LOOP_PERIOD, max(1, self._limit // 2))
        self._recent = deque(maxlen=MAX_LOOP_PERIOD)
        self._runs = [0] * (MAX_LOOP_PERIOD + 1)

    def feed(self, text: str) -> bool:
        """Record one text fragment; return True when a loop is detected."""
        if self._max_period == 0:
            return False
        recent = self._recent
        runs = self._runs
        limit = self._limit
        n = len(recent)
        top = min(n, self._max_period)
        looping = False
        for p in range(1, top + 1):
            if text == recent[n - p]:
                runs[p] += 1
            else:
                runs[p] = 0
            if runs[p] >= limit - p:
                looping = True
        recent.append(text)
        return looping
