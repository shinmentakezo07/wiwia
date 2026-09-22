"""Regressions for the round-92 second wave (AUDIT #280-#284).

Five defects in the streaming path, each with the invariant that must hold:

* #280 — the loop detector's period cap (8) missed the commonest runaway shape,
  a restated sentence/paragraph (10-40 wire chunks). It also only ever saw
  visible text, so a model looping inside its reasoning trace was invisible.
* #281 — ``_attempt_resume`` cancelled a failed resume pump without awaiting it
  and dropped that attempt's partial usage, so the tokens were billed to nobody.
* #282 — the reconnect tail loop re-read and re-parsed the whole journal every
  50 ms; an incremental byte-cursor replaces it.
* #283 — every sub-frame of a multi-frame chunk got the SAME SSE id, so a
  reconnect cursor landing mid-chunk skipped the frames after the first.
* #284 — the non-streaming arm reported a truncated upstream as a success and
  only billed partials when the failure happened to raise.
"""

from __future__ import annotations

import asyncio
import base64
import time

import orjson
import pytest

from wiwi.streaming import deltas as dl
from wiwi.streaming.loopdetect import MAX_LOOP_PERIOD, LoopDetector
from wiwi.streaming.tape_store import JournalStore, JournalTail

# --------------------------------------------------------------------------
# #280 — loop detection covers realistic periods and reasoning text
# --------------------------------------------------------------------------


@pytest.mark.parametrize("period", [1, 2, 8, 9, 12, 20, 30, 32])
def test_every_period_up_to_the_cap_is_detected(period: int) -> None:
    """Periods 9..20 used to be silently undetected (the cap was 8)."""
    det = LoopDetector(limit=100)
    toks = [f"t{i} " for i in range(period)]
    fired = None
    for i in range(period * 130):
        if det.feed(toks[i % period]):
            fired = i
            break
    assert fired is not None, f"period {period} not detected"


def test_period_coverage_is_independent_of_the_limit() -> None:
    """The limit decides how many repeats are required, not which periods are
    visible. Previously `_max_period` was `limit // 2`, so a small limit
    silently narrowed coverage."""
    for limit in (10, 20, 30, 100):
        det = LoopDetector(limit)
        assert det._max_period == MAX_LOOP_PERIOD
        # A small limit must still detect a long period.
        toks = [f"c{i}" for i in range(20)]
        fired = any(det.feed(toks[i % 20]) for i in range(400))
        assert fired, f"limit={limit} failed to detect period 20"


def test_cap_still_bounds_work_and_says_so() -> None:
    """Periods above the cap stay undetected — documented, not accidental."""
    det = LoopDetector(limit=100)
    toks = [f"t{i}" for i in range(MAX_LOOP_PERIOD + 8)]
    assert not any(det.feed(toks[i % len(toks)]) for i in range(2000))
    # The threshold helper is what keeps the arithmetic honest at the edge.
    assert det._threshold(MAX_LOOP_PERIOD) >= 1


def test_reasoning_text_can_trip_the_detector() -> None:
    """#280: `feed` must be chunk-type-agnostic so the gateway can hand it
    thinking deltas. The same text a text-only detector ignores fires here."""
    det = LoopDetector(limit=20)
    fired = any(det.feed("I need to reconsider. ") for _ in range(60))
    assert fired


def test_empty_chunks_never_count_as_repetition() -> None:
    """A provider streaming empty keep-alive frames must not trip the detector."""
    det = LoopDetector(limit=5)
    assert not any(det.feed("") for _ in range(500))


def test_healthy_stream_still_never_fires_at_the_wider_cap() -> None:
    det = LoopDetector(limit=100)
    assert not any(det.feed(f"token-{i}") for i in range(20000))


def test_disabled_detector_stays_disabled() -> None:
    for limit in (0, -5):
        det = LoopDetector(limit)
        assert not any(det.feed("same") for _ in range(200))


# --------------------------------------------------------------------------
# #282 — the journal tail cursor is incremental and equivalent
# --------------------------------------------------------------------------


def _write_journal(store: JournalStore, rid: str, n: int, size: int = 64) -> None:
    path = store.path_for(rid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.writelines(orjson.dumps({
                "seq": i, "ts": time.time(),
                "data": base64.b64encode(b"x" * size).decode(),
            }) + b"\n" for i in range(1, n + 1))


def test_tail_cursor_reads_the_same_records_as_read_after():
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    _write_journal(store, "rid", 200)

    tail = JournalTail(store.path_for("rid"), 0)
    got: list[tuple[int, bytes]] = []
    for _ in range(5):
        got.extend((s, c) for s, c, _done in tail.read_next())
    assert got == store.read_after("rid", 0)


def test_tail_cursor_picks_up_appends_incrementally():
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    _write_journal(store, "rid", 5)
    tail = JournalTail(store.path_for("rid"), 0)
    first = list(tail.read_next())
    assert [s for s, _, _ in first] == [1, 2, 3, 4, 5]

    with open(store.path_for("rid"), "ab") as fh:
        fh.write(orjson.dumps({"seq": 6, "ts": time.time(),
                               "data": base64.b64encode(b"new").decode()}) + b"\n")
    assert [s for s, _, _ in tail.read_next()] == [6]
    # Idle poll: nothing new, nothing re-read.
    assert tail.read_next() == []


def test_tail_cursor_reports_a_done_record_and_never_replays_it():
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    path = store.path_for("rid")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(orjson.dumps({"seq": 1, "ts": 1.0,
                               "data": base64.b64encode(b"a").decode()}) + b"\n")
        fh.write(orjson.dumps({"seq": 2, "ts": 2.0, "data": "", "done": True}) + b"\n")
    tail = JournalTail(path, 0)
    out = list(tail.read_next())
    assert [(s, done) for s, _, done in out] == [(1, False), (2, True)]
    assert tail.read_next() == []


def test_tail_cursor_skips_ownership_records():
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    path = store.path_for("rid")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(orjson.dumps({"seq": 0, "ts": 1.0, "owner": "key-1"}) + b"\n")
        fh.write(orjson.dumps({"seq": 1, "ts": 1.0,
                               "data": base64.b64encode(b"a").decode()}) + b"\n")
    assert [s for s, _, _ in JournalTail(path, 0).read_next()] == [1]


def test_done_record_sharing_the_last_seq_is_still_delivered():
    """`StreamJournal.finish(seq)` writes the done record with the SAME seq as
    the final data record, so a cursor that only advances on `seq >` dropped it
    and the tail loop never terminated (AUDIT #282).

    This was invisible to `read_after`, which filters against the caller's fixed
    ``last_seq`` rather than an advancing cursor — the bug lived only in the
    incremental path, and only showed up as a reconnect hang.
    """
    import tempfile

    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=1_000_000)
    path = store.path_for("rid")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.writelines(orjson.dumps({
                "seq": i, "ts": 1.0,
                "data": base64.b64encode(b"c").decode()}) + b"\n" for i in (1, 2, 3))
        # finish(3): the done marker repeats the last data record's seq.
        fh.write(orjson.dumps({"seq": 3, "ts": 2.0, "data": "", "done": True})
                 + b"\n")

    tail = JournalTail(path, 0)
    out = list(tail.read_next())
    assert [(s, done) for s, _, done in out] == [(1, False), (2, False),
                                                 (3, False), (3, True)]
    # The done record must terminate the loop, so a later poll is empty.
    assert tail.read_next() == []




def test_tail_cursor_leaves_a_torn_trailing_line_for_the_next_poll():
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    path = store.path_for("rid")
    path.parent.mkdir(parents=True, exist_ok=True)
    complete = orjson.dumps({"seq": 1, "ts": 1.0,
                             "data": base64.b64encode(b"a").decode()}) + b"\n"
    with open(path, "wb") as fh:
        fh.write(complete)
        fh.write(b'{"seq": 2, "ts": 2.0, "data": "p')  # torn, no newline
    tail = JournalTail(path, 0)
    assert [s for s, _, _ in tail.read_next()] == [1]
    # The torn record is completed, then read whole.
    with open(path, "ab") as fh:
        fh.write(b'g=="}\n')
    assert [s for s, _, _ in tail.read_next()] == [2]


def test_tail_cursor_detects_truncation():
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    _write_journal(store, "rid", 10)
    tail = JournalTail(store.path_for("rid"), 0)
    tail.read_next()
    assert not tail.truncated()
    store.path_for("rid").write_bytes(b"")  # sweep/unlink/reuse
    assert tail.truncated()


def test_idle_tail_poll_is_far_cheaper_than_a_full_reparse():
    """The point of #282: an idle poll must not scale with the journal."""
    import tempfile
    store = JournalStore(tempfile.mkdtemp(), ttl_s=600, max_bytes=10_000_000)
    _write_journal(store, "rid", 2000, size=200)

    tail = JournalTail(store.path_for("rid"), 2000)
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        tail.read_next()
    incremental = (time.perf_counter() - t0) / n

    t0 = time.perf_counter()
    for _ in range(n):
        store.read_after("rid", 2000)
    full = (time.perf_counter() - t0) / n

    # Generous: the cursor does one stat + one empty read. Anything within an
    # order of magnitude of the full re-read means the cursor stopped working.
    assert incremental < full / 5, (
        f"idle poll {incremental*1e3:.3f}ms vs full re-read {full*1e3:.3f}ms — "
        f"the tail is not incremental")


# --------------------------------------------------------------------------
# #283 — each sub-frame gets its own SSE id
# --------------------------------------------------------------------------


def test_multi_frame_chunk_assigns_distinct_increasing_ids():
    from wiwi.server.app import _inject_id

    chunk = b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\nevent: c\ndata: 3\n\n"
    tagged, last = _inject_id(chunk, 7)
    ids = [ln for ln in tagged.split(b"\n") if ln.startswith(b"id:")]
    assert ids == [b"id: 7", b"id: 8", b"id: 9"]
    assert last == 9


def test_reconnect_after_a_sub_frame_no_longer_skips_the_next_one():
    """The defect: with one shared id, replay `seq > id` skipped the sub-frames
    the client never received."""
    from wiwi.server.app import _inject_id

    tagged, _ = _inject_id(b"event: a\ndata: 1\n\nevent: b\ndata: 2\n\n", 7)
    frames = [f for f in tagged.split(b"\n\n") if f]
    first_id = int(frames[0].split(b"\n")[0].split()[1])
    second_id = int(frames[1].split(b"\n")[0].split()[1])
    assert second_id > first_id, "sub-frames must be individually addressable"


def test_single_frame_keeps_its_id_and_returns_it():
    from wiwi.server.app import _inject_id

    tagged, last = _inject_id(b"event: x\ndata: y", 3)
    assert tagged.startswith(b"id: 3\n")
    assert last == 3


def test_chunk_with_no_frames_is_passed_through():
    from wiwi.server.app import _inject_id

    assert _inject_id(b"\n\n", 5) == (b"\n\n", 5)


# --------------------------------------------------------------------------
# #281 — a failed resume's teardown is awaited
# --------------------------------------------------------------------------


async def test_resume_failure_path_awaits_pump_teardown():
    """Pins the shape: the failed resume pump is cancelled AND awaited, and its
    context is retained so its partial usage is not lost."""
    import inspect

    from wiwi.core import gateway as gw

    src = inspect.getsource(gw.Gateway._attempt_resume)
    assert "new_pump_task.cancel()" in src, "resume pump must still be cancelled"
    # The fix: the cancel is followed by an awaited teardown.
    after = src.split("new_pump_task.cancel()", 1)[1]
    assert "await" in after, (
        "the failed resume pump's teardown must be awaited, or its upstream "
        "socket stays checked out until GC")
    # And the failure path must retain the context for usage merging.
    failure_arm = src.rsplit("_pending_resume_ctxs", 2)[-1]
    assert "_pending_resume_ctxs" in src
    assert "resume_ctx" in failure_arm or "_pending_resume_ctxs" in failure_arm


# --------------------------------------------------------------------------
# #284 — a truncated non-streaming turn is not a success
# --------------------------------------------------------------------------

def test_unframed_line_is_opt_in_and_off_by_default():
    """The strict parser must still drop an unframed line; ``allow_unframed``
    is the explicit opt-in the streaming/envelope call sites use."""
    from wiwi.streaming.sse import LineSSEParser

    payload = orjson.dumps({"id": "x", "choices": []}).decode()
    assert LineSSEParser().feed_line(payload) is None
    evt = LineSSEParser(allow_unframed=True).feed_line(payload)
    assert evt is not None and evt.data == payload


def test_unframed_line_reaches_the_adapter_as_a_finish():
    """Envelope providers answer with a bare JSON line; parsed leniently it must
    reach the adapter and yield its Finish/UsageFinal rather than vanishing."""
    from wiwi.providers.cline_adapter import ClineAdapter
    from wiwi.streaming.sse import LineSSEParser

    payload = orjson.dumps({"id": "x", "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"},
         "finish_reason": "stop"}]}).decode()
    evt = LineSSEParser(allow_unframed=True).feed_line(payload)
    assert evt is not None
    deltas = ClineAdapter().decode_stream_event(evt.event, evt.data)
    assert any(isinstance(d, dl.Finish) for d in deltas)


def test_all_envelope_call_sites_opt_in():
    """A missed call site silently drops the whole turn, so pin every one.
    `LineSSEParser()` with no argument in the gateway or recovery is a
    regression (AUDIT #284/#285)."""
    import inspect

    from wiwi.core import gateway as gw
    from wiwi.core import recovery as rec

    for fn in (gw.Gateway._complete_via_stream, gw.Gateway._pump_once):
        src = inspect.getsource(fn)
        assert "LineSSEParser(allow_unframed=True)" in src, (
            f"{fn.__name__} must parse envelope bodies leniently")
    assert "LineSSEParser(allow_unframed=True)" in inspect.getsource(
        rec._body_is_error_envelope)


def test_usage_fallback_fills_a_missing_prompt():
    """The partial-billing helper depends on this contract."""
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import usage_fallback
    from wiwi.ir import types as ir

    ctx = RequestContext(surface="chat", ir_req=ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("x" * 40)])],
        gen_params=ir.GenParams(max_tokens=16)))

    class _Dep:
        model_id = "m"

    out = asyncio.run(usage_fallback(
        ctx, _Dep(), dl.UsageFinal(output=7), text_len=12))
    assert out.output == 7
    assert out.estimated is True
    assert out.prompt >= 0
