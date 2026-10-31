"""Round 186 — an unreadable Anthropic block index must not be guessed onto block 0.

Round 128 normalized a numerically whole float index (``1.0`` -> ``1``) but
kept routing every *unreadable* index (a list, a dict, a numeric string, a
bool, a non-integral float) to block ``0`` "so the frame is not dropped". Block
0 is a real block: when a call is open there, the fragment is appended to that
call's JSON, the stream still terminates cleanly, and the client executes the
tool with arguments it never received. Nothing downstream can detect that — the
gateway folds fragments by index and every wire encoder trusts the decoder's
Open -> ArgsDelta* -> Close contract.

AUDIT #419's own fix sketch called for the remaining half: an unreadable index
names no block, so ``_block_index`` returns ``None`` and every arm drops the
frame instead of guessing. The float normalization from round 128 is preserved
(it names the *same* block), and a stop with an unreadable index still closes
the one call it can only be about, so the stream stays well-formed.

#420 (the ``[DONE]`` terminator on an anthropic-typed upstream) is re-pinned as
a control: it was already fixed in round 162 and this round must not disturb it.
"""

from __future__ import annotations

import json

import orjson

from wiwi.providers.registry import fresh_adapter
from wiwi.streaming import deltas as dl


def _frame(**payload) -> str:
    return orjson.dumps(payload).decode()


def _start(index, block: dict) -> str:
    return _frame(type="content_block_start", index=index, content_block=block)


def _tool_start(index, call_id: str = "t", name: str = "f") -> str:
    return _start(index, {"type": "tool_use", "id": call_id, "name": name})


def _args(index, fragment: str) -> str:
    return _frame(type="content_block_delta", index=index,
                  delta={"type": "input_json_delta", "partial_json": fragment})


def _stop(index) -> str:
    return _frame(type="content_block_stop", index=index)


def _feed(adapter, frames) -> list:
    out: list = []
    for raw in frames:
        out.extend(adapter.decode_stream_event(orjson.loads(raw).get("type"), raw))
    return out


def _args_by_block(adapter, frames) -> dict[int, str]:
    """Reassemble per-block tool args the way the gateway does."""
    bufs: dict[int, list[str]] = {}
    for d in _feed(adapter, frames):
        if isinstance(d, dl.ToolCallOpen):
            bufs[d.index] = []
        elif isinstance(d, dl.ToolCallArgsDelta):
            bufs.setdefault(d.index, []).append(d.args_fragment)
    return {i: "".join(v) for i, v in bufs.items()}


# -- the corruption #419 recorded -------------------------------------------


def test_an_unreadable_index_does_not_corrupt_a_live_sibling():
    """Two calls open, a list index on the second's fragment.

    Before this round the fragment landed on block 0 — the *other* call —
    concatenating two calls' arguments into JSON neither client can parse.
    """
    a = fresh_adapter("anthropic")
    args = _args_by_block(a, [
        _tool_start(0, "A", "Grep"),
        _tool_start(1, "B", "Read"),
        _args([1], '{"p":"a"}'),
    ])
    assert args[0] == "", f"block 0 stole block 1's args: {args}"
    assert args.get(1, "") == "", f"a fragment appeared from nowhere: {args}"


def test_a_non_integral_float_index_does_not_land_on_block_zero():
    a = fresh_adapter("anthropic")
    args = _args_by_block(a, [_tool_start(0, "A", "Grep"), _args(1.5, '{"p":"a"}')])
    assert args[0] == "", args


def test_a_numeric_string_index_is_dropped_not_coerced():
    a = fresh_adapter("anthropic")
    args = _args_by_block(a, [
        _tool_start(0, "A", "Grep"),
        _tool_start(1, "B", "Read"),
        _args("1", '{"p":"a"}'),
    ])
    assert args[0] == "" and args.get(1, "") == "", args


def test_args_for_an_unopened_index_are_dropped():
    """Contract: ArgsDelta lives strictly inside Open -> Args* -> Close."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_delta", _args(7, "ORPHAN"))
    assert out == [], out


# -- start / stop must not guess either --------------------------------------


def test_an_unreadable_start_does_not_supersede_a_live_call():
    """A second start on a guessed index 0 would close and reopen the live call."""
    a = fresh_adapter("anthropic")
    opened = a.decode_stream_event("content_block_start", _tool_start(0, "A", "Grep"))
    dropped = a.decode_stream_event("content_block_start", _start([0], {
        "type": "tool_use", "id": "B", "name": "Read"}))
    closed = a.decode_stream_event("content_block_stop", _stop(0))
    assert isinstance(opened[0], dl.ToolCallOpen)
    assert dropped == [], dropped
    assert isinstance(closed[0], dl.ToolCallClose)
    assert closed[0].index == 0, "the live call was lost with the malformed frame"


def test_an_unreadable_stop_closes_the_only_open_call():
    """One open call: the stop can only refer to it, so close it, not orphan it."""
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start(3, "A", "Grep"))
    out = a.decode_stream_event("content_block_stop", _stop([3]))
    assert len(out) == 1, out
    assert isinstance(out[0], dl.ToolCallClose)
    assert out[0].index == 3


def test_an_unreadable_stop_with_several_open_calls_closes_none():
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start(0, "A", "Grep"))
    a.decode_stream_event("content_block_start", _tool_start(1, "B", "Read"))
    out = a.decode_stream_event("content_block_stop", _stop([0]))
    assert out == [], out


def test_an_unreadable_result_block_does_not_guess_its_number():
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start([2], {
        "type": "web_search_tool_result", "tool_use_id": "srv_1", "content": []}))
    assert out == [], out


# -- controls ----------------------------------------------------------------


def test_the_whole_float_control_still_routes_to_its_own_block():
    """Round 128's fix is preserved: 1.0 names block 1, not block 0."""
    a = fresh_adapter("anthropic")
    args = _args_by_block(a, [_tool_start(1, "B", "Read"), _args(1.0, '{"p":"a"}')])
    assert args[1] == '{"p":"a"}', args
    json.loads(args[1])


def test_done_terminator_still_flushes_open_tools():
    """#420 control: the sentinel closes what is open and ends the stream."""
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start(0, "A", "Grep"))
    out = a.decode_stream_event("", "[DONE]")
    assert [type(d).__name__ for d in out] == [
        "ToolCallClose", "Finish", "StreamEnd"]
