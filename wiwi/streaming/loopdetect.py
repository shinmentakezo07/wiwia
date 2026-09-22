"""Incremental repetition detector for streaming text.

The gateway aborts a stream when the model starts emitting the same content
over and over — a degenerate loop that would otherwise run until the token
limit or the client's patience runs out.

The naive check ("is the whole window periodic?") is O(n^2) in the window
size and was previously run on **every chunk** once the window filled, which
cost hundreds of microseconds per chunk at the shipped ``stream_loop_limit``
of 100.  Its replacement keeps those semantics for short periods — where real
degenerate loops live — while doing O(1) work per chunk.

Detection model: for each candidate period ``p`` (1..:data:`MAX_LOOP_PERIOD`)
track the length of the run of consecutive chunks that each equal the chunk ``p``
positions back.  When a run reaches ``limit - p``, the window is periodic with
period ``p``, which is exactly the condition the old full-window scan raised on.

The period cap is deliberate: covering arbitrary periods would require the
quadratic scan back.  It is set to :data:`MAX_LOOP_PERIOD` (32), which covers
the two shapes runaway decoding actually produces — a single chunk repeated, and
a sentence/paragraph restated (10-40 wire chunks).  The previous cap of 8 missed
the second entirely (AUDIT #280).

Callers must feed **all** generated text, not just visible text: a model can
run away inside its reasoning trace, and a detector fed only ``TextDelta`` sees
nothing (AUDIT #280).  ``feed`` is chunk-type-agnostic, so the gateway hands it
thinking deltas too.
"""

from __future__ import annotations

from collections import deque

#: Longest repetition period detected.  Bounds per-token work to this many
#: comparisons.  The old cap of 8 covered only "the same token/word over and
#: over" and missed the commonest runaway shape — the model restating the *same
#: sentence or paragraph*, which is 10-40 chunks on the wire.  Periods 9..20
#: were silently undetected, so a model stuck repeating a one-line apology ran
#: to the token limit while the detector reported nothing (AUDIT #280).  32
#: covers that whole class at a cost of 32 integer compares per chunk, which is
#: still far below the per-chunk JSON/SSE work it guards.
MAX_LOOP_PERIOD = 32


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
        # Coverage is a property of the detector, not of the configured limit:
        # any period up to MAX_LOOP_PERIOD must be detectable, and the limit
        # only decides how many repeats are required. The old code capped the
        # period at ``limit // 2`` (and at the constant, then 8), so a small
        # ``stream_loop_limit`` silently *narrowed period coverage* — limit=20
        # could only ever see periods 1..8 — while a larger one widened it.
        # That is backwards from the knob's documented meaning (AUDIT #280).
        self._max_period = MAX_LOOP_PERIOD
        # The window must hold every lag we read: ``recent[n - p]`` with
        # p == _max_period must still be a live element, not one evicted by the
        # append below. Exactly _max_period is therefore both necessary and
        # sufficient.
        self._recent = deque(maxlen=MAX_LOOP_PERIOD)
        self._runs = [0] * (MAX_LOOP_PERIOD + 1)

    def _threshold(self, period: int) -> int:
        """Repeats required at *period* to declare a loop.

        ``limit - period`` is the repeat count that makes a window of ``limit``
        chunks exactly periodic — the condition the original full-window scan
        raised on. For a period at or above the limit that arithmetic goes to
        zero or negative, which would fire on a single repeat; floor it at 1 so
        such a period still requires at least one confirming repetition.
        """
        return max(1, self._limit - period)

    def feed(self, text: str) -> bool:
        """Record one generated chunk; return True when a loop is detected.

        Chunk-type-agnostic by design: the gateway feeds both visible text and
        reasoning text, because a model can run away inside its thinking trace
        where a text-only detector sees nothing (AUDIT #280). An empty chunk is
        ignored — it carries no evidence either way, and counting it as a
        repetition would let a provider's empty keep-alive frames trip the
        detector.
        """
        if self._max_period == 0 or not text:
            return False
        recent = self._recent
        runs = self._runs
        n = len(recent)
        top = min(n, self._max_period)
        looping = False
        for p in range(1, top + 1):
            if text == recent[n - p]:
                runs[p] += 1
            else:
                runs[p] = 0
            if runs[p] >= self._threshold(p):
                looping = True
        recent.append(text)
        return looping
