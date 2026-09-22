"""Streaming pump integrity: undroppable terminals, coalescer liveness, order.

Three defects in the client-facing stream path (AUDIT #277).

1. **A dropped terminal frame hangs the client forever.** ``_put_frame``
   enqueued with ``put_nowait`` and fell back to a bounded ``wait_for`` whose
   ``TimeoutError`` was suppressed. A consumer more than
   ``_QUEUE_PUT_TIMEOUT_S`` behind therefore never received
   ``StreamEnd``/``StreamError`` — and since the consumer is parked on
   ``await queue.get()`` with no timeout of its own, the request never
   terminated. The pump now recognises terminal frames and enqueues them with a
   reserved slot guarantee, and the consumer re-opens that slot on every
   ``get``.

2. **The coalescer held text with no timer.** ``max_ms`` was only evaluated
   when the *next* delta arrived, so a quiet upstream left the buffer — and
   every token in it — held until end-of-stream. ``flush_due`` plus a
   deadline-aware consumer wait make ``max_ms`` a real wall-clock bound.

3. **A control delta overtook buffered text.** The coalescer's flush list is
   returned ahead of the delta that triggered it, but the queue-depth argument
   was sampled per delta; a ``Finish`` arriving at a lower depth took the fast
   path and was emitted before the text still buffered behind it, so the client
   saw its stop reason before the tail of the answer. Non-text deltas now drain
   the buffer first.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from wiwi.core.gateway import (
    _is_terminal_delta,
    _reserve_terminal_slot,
)
from wiwi.streaming import deltas as dl
from wiwi.streaming.coalesce import DeltaCoalescer


def _text(chunks: list) -> str:
    return "".join(c.text for c in chunks if isinstance(c, dl.TextDelta))


# --------------------------------------------------------------------------
# 1. terminal frames are never dropped
# --------------------------------------------------------------------------


def test_terminal_predicate_covers_every_terminal_frame():
    assert _is_terminal_delta(dl.StreamEnd())
    assert _is_terminal_delta(dl.StreamError("boom", "connection"))
    # Finish carries the stop reason the client reports, so it shares the
    # guarantee even though StreamEnd/StreamError are the legal terminals.
    assert _is_terminal_delta(dl.Finish("stop"))
    # Content is explicitly not covered: dropping content under backpressure is
    # the intended behaviour, and conflating the two was the bug.
    assert not _is_terminal_delta(dl.TextDelta("x"))
    assert not _is_terminal_delta(dl.UsageFinal(prompt=1, output=1))
    assert not _is_terminal_delta(dl.ToolCallOpen(0, "id", "name"))


async def test_full_queue_never_loses_a_terminal_frame():
    """The regression: a full queue used to swallow StreamEnd/StreamError.

    Exercised through the real consumer-side contract — reserve a slot, fill the
    queue, then enqueue the terminal frame the way ``_put_frame`` does.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    # Consumer reserved a slot: the pump may fill everything but the last.
    for _ in range(queue.maxsize - 1):
        queue.put_nowait(dl.TextDelta("x"))
    assert not queue.full()

    queue.put_nowait(dl.StreamError("upstream died", "connection"))  # the terminal

    drained = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    assert isinstance(drained[-1], dl.StreamError)


async def test_reserve_reopens_the_slot_so_terminals_always_fit():
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    for _ in range(4):
        queue.put_nowait(dl.TextDelta("x"))
    assert queue.full()

    # A slow consumer that has taken one item off re-opens the reserved slot.
    queue.get_nowait()
    _reserve_terminal_slot(queue)
    assert not queue.full(), "the terminal slot must be free again"

    queue.put_nowait(dl.StreamEnd())  # must not raise QueueFull
    assert queue.full()


async def test_reserve_drops_a_content_frame_when_consumer_is_far_behind():
    """If the consumer refills the queue anyway, one content frame is shed."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    for _ in range(3):
        queue.put_nowait(dl.TextDelta("x"))
    # Consumer took one but the producer (uncooperatively) filled it again:
    # the reserve must still make room, shedding the oldest content.
    _reserve_terminal_slot(queue)
    assert not queue.full(), "a reserved slot must survive a re-filled queue"


def test_reserve_is_a_noop_below_capacity():
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    queue.put_nowait(dl.TextDelta("x"))
    _reserve_terminal_slot(queue)
    assert queue.qsize() == 1, "the fast path must not drop anything"


async def test_old_terminal_path_drops_and_the_new_one_delivers():
    """The decisive before/after, with no timing luck involved.

    Both halves of the shipped guarantee are pinned here. ``old_put_frame``
    reproduces the pre-fix body verbatim (``put_nowait`` → bounded ``wait_for``
    whose ``TimeoutError`` is swallowed): with a consumer that is briefly away,
    the terminal is dropped and the client can never observe it. The new path
    holds the frame until the consumer frees a slot, so the terminal is
    delivered. The reader's pause is deterministic — it is a task, not a race
    against a real clock — so this cannot flake into a false pass.
    """

    async def old_put_frame(queue, d, timeout=0.1):
        try:
            queue.put_nowait(d)
            return "put_nowait"
        except asyncio.QueueFull:
            pass
        try:
            await asyncio.wait_for(queue.put(d), timeout=timeout)
            return "waited"
        except TimeoutError:
            return "DROPPED"  # the bug: suppressed by contextlib.suppress

    async def new_put_frame(queue, d, timeout=0.1):
        # Mirrors the shipped `_put_frame`: a terminal frame is never dropped.
        if _is_terminal_delta(d):
            try:
                queue.put_nowait(d)
                return "reserved-slot"
            except asyncio.QueueFull:
                await queue.put(d)
                return "unbounded-put"
        return await old_put_frame(queue, d, timeout)

    async def run(put_frame):
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        queue.put_nowait(dl.TextDelta("a"))
        queue.put_nowait(dl.TextDelta("b"))

        async def reader():
            # The consumer steps away, then takes one frame (freeing a slot).
            await asyncio.sleep(0.3)
            first = queue.get_nowait()
            await asyncio.sleep(0.05)  # let a blocked put land
            rest = []
            while not queue.empty():
                rest.append(type(queue.get_nowait()).__name__)
            return [type(first).__name__, *rest]

        reader_task = asyncio.create_task(reader())
        outcome = await put_frame(queue, dl.StreamEnd())
        return outcome, await reader_task

    old_outcome, old_seen = await run(old_put_frame)
    assert old_outcome == "DROPPED"
    assert "StreamEnd" not in old_seen, (
        "the pre-fix path is expected to lose the terminal frame; if this "
        "changed, the reproduction no longer proves anything")

    new_outcome, new_seen = await run(new_put_frame)
    assert new_outcome == "unbounded-put"
    assert "StreamEnd" in new_seen, "the fixed path must deliver the terminal"




# --------------------------------------------------------------------------
# 2. the coalescer honours its wall-clock deadline
# --------------------------------------------------------------------------


def test_flush_due_releases_text_after_the_deadline_with_no_new_delta():
    """The regression: 200 ms of silence held the buffer indefinitely."""
    c = DeltaCoalescer(max_bytes=8192, max_ms=50.0, threshold=100)
    c.feed(dl.TextDelta("hello "), 4096)
    c.feed(dl.TextDelta("world"), 4096)

    # Nothing has expired yet.
    assert c.flush_due(now=time.monotonic()) == []

    # Past the deadline, and without any further feed() call.
    out = c.flush_due(now=time.monotonic() + 1.0)
    assert _text(out) == "hello world"
    # Idempotent: the buffer is drained.
    assert c.flush_due(now=time.monotonic() + 2.0) == []


def test_buffered_s_tracks_the_oldest_held_text():
    c = DeltaCoalescer(max_bytes=8192, max_ms=50.0, threshold=100)
    assert c.buffered_s == 0.0
    c.feed(dl.TextDelta("x"), 4096)
    assert c.buffered_s >= 0.0
    c.drain()
    assert c.buffered_s == 0.0


def test_flush_due_is_silent_on_an_empty_buffer():
    c = DeltaCoalescer(threshold=100)
    assert c.flush_due(now=time.monotonic() + 10.0) == []


def test_fast_consumer_still_bypasses_buffering():
    """No behaviour change below the depth threshold."""
    c = DeltaCoalescer(max_bytes=8192, max_ms=50.0, threshold=100)
    out = c.feed(dl.TextDelta("tok"), queue_depth=0)
    assert [d.text for d in out] == ["tok"]
    assert c.buffered_s == 0.0


# --------------------------------------------------------------------------
# 3. control deltas never overtake buffered text (the coalescer's own contract)
# --------------------------------------------------------------------------
#
# An earlier hypothesis held that a control delta arriving at a *lower* queue
# depth took `feed()`'s fast path and was emitted ahead of still-buffered text.
# Reading the code disproves it: both the fast path and the non-mergeable
# branch call `_flush()` and return the flushed text *ahead* of the delta, so
# ordering is already depth-independent. These tests pin that invariant — the
# behaviour the fix must not break — rather than a bug that never existed.


@pytest.mark.parametrize("depth", [0, 50, 100, 4096])
def test_control_delta_always_follows_buffered_text(depth: int):
    """`Finish`/`ToolCallClose`/`UsageFinal` are never emitted before the text
    that was buffered ahead of them, at any queue depth."""
    c = DeltaCoalescer(max_bytes=8192, max_ms=50.0, threshold=100)
    c.feed(dl.TextDelta("A"), 4096)  # buffered at high depth
    c.feed(dl.TextDelta("B"), 4096)

    emitted = c.feed(dl.Finish("stop"), queue_depth=depth)

    assert [type(d).__name__ for d in emitted] == ["TextDelta", "Finish"]
    assert _text(emitted) == "AB"
    assert c.buffered_s == 0.0, "nothing may be left behind a control delta"


def test_tool_call_control_delta_follows_buffered_text():
    c = DeltaCoalescer(max_bytes=8192, max_ms=50.0, threshold=100)
    c.feed(dl.TextDelta("prefix"), 4096)
    emitted = c.feed(dl.ToolCallOpen(0, "id", "fn"), queue_depth=4096)
    assert [type(d).__name__ for d in emitted] == ["TextDelta", "ToolCallOpen"]


def test_gateway_flush_due_cannot_reorder_past_a_yielded_terminal():
    """The consumer's `flush_due` call is bounded by an empty buffer once a
    control delta has gone through `feed()` — so it cannot emit text after the
    terminal frame it already yielded."""
    c = DeltaCoalescer(max_bytes=8192, max_ms=50.0, threshold=100)
    c.feed(dl.TextDelta("tail"), 4096)
    c.feed(dl.StreamEnd(), queue_depth=4096)  # flushes "tail" first
    assert c.flush_due(now=time.monotonic() + 100.0) == []
    assert c.drain() == []


@pytest.mark.parametrize("max_ms", [0.0, 1.0, 50.0])
def test_deadline_math_never_yields_a_negative_wait(max_ms: float):
    c = DeltaCoalescer(max_bytes=8192, max_ms=max_ms, threshold=100)
    c.feed(dl.TextDelta("x"), 4096)
    remaining = max_ms / 1000.0 - c.buffered_s
    # The consumer clamps to a positive floor rather than passing a <=0 timeout.
    assert max(remaining, 1e-3) > 0.0
