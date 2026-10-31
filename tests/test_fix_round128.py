"""Round 128 — Anthropic streaming decoder: unhashable index / tool_use_id.

Two unguarded reads in ``decode_stream_event`` where ``.get(k, default)`` was
mistaken for a type guard — it only defaults a MISSING key, so an upstream
frame (or a re-serializing proxy) carrying a list/dict reached a ``set.add`` or
a ``dict.get`` and raised ``TypeError: unhashable type``.

That escape is not cosmetic. It leaves ``decode_stream_event`` mid-stream, and
the pump's mid-stream handler converts it to a ``StreamError``: the partial is
billed, ``record_fail`` fires, and a HEALTHY key is moved to ``cooling`` — a
frame carrying no semantics penalizes a healthy credential.

Fixes lift the guards the OpenAI/NIM/OpenRouter decoders already carry, using
this file's existing ``as_str`` helper rather than a new coercion idiom.
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


def _stop(index) -> str:
    return _frame(type="content_block_stop", index=index)


def _tool_start(index) -> str:
    return _start(index, {"type": "tool_use", "id": "toolu_1", "name": "f"})


# ---------------------------------------------------------------------------
# AUDIT #384 — content_block_*.index must be guarded
# ---------------------------------------------------------------------------


def test_unhashable_index_does_not_crash_content_block_start():
    """The regression: ``index: [0]`` raised ``TypeError: unhashable type``.

    The frame is dropped rather than opened on a guessed index. ``0`` is a
    real block, so opening a call there would supersede whichever call is live
    on it and route this call's arguments onto that one — the corruption
    AUDIT #419 records (round 186 closed the second half).
    """
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start([0], {
        "type": "tool_use", "id": "toolu_1", "name": "f"}))
    assert out == [], out


def test_unhashable_index_does_not_crash_input_json_delta():
    """The ``input_json_delta`` arm reads ``index`` too (args must route right).

    No call is open for an unreadable index, so there is nothing the fragment
    can belong to: it is dropped rather than guessed onto block 0 (AUDIT #419).
    """
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_delta", _frame(
        type="content_block_delta", index=[1],
        delta={"type": "input_json_delta", "partial_json": '{"a":'}))
    assert out == [], out


def test_unhashable_index_does_not_crash_content_block_stop():
    """The stop arm's ``idx in self._tool_indices`` test must not raise either.

    Both frames coerce to index 0, so the call closes rather than being
    orphaned — the good outcome: an unclosed tool_use block is 400-bait on the
    client's next replay.
    """
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start(0))
    out = a.decode_stream_event("content_block_stop", _stop({"i": 0}))
    assert len(out) == 1
    assert isinstance(out[0], dl.ToolCallClose)
    assert out[0].index == 0


def test_bool_index_is_rejected_not_coerced_to_block_one():
    """``isinstance(True, int)`` is True, so a bool must be rejected explicitly."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _tool_start(True))
    assert out == [], out


def test_genuine_int_index_is_preserved():
    """Control: real indices must round-trip untouched."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _tool_start(7))
    assert out[0].index == 7


def test_normal_tool_lifecycle_is_unaffected():
    """Control: open -> args -> close still emits the full contract."""
    a = fresh_adapter("anthropic")
    opened = a.decode_stream_event("content_block_start", _tool_start(0))
    args = a.decode_stream_event("content_block_delta", _frame(
        type="content_block_delta", index=0,
        delta={"type": "input_json_delta", "partial_json": '{"x":1}'}))
    closed = a.decode_stream_event("content_block_stop", _stop(0))
    assert isinstance(opened[0], dl.ToolCallOpen)
    assert isinstance(args[0], dl.ToolCallArgsDelta)
    assert args[0].index == 0
    assert isinstance(closed[0], dl.ToolCallClose)
    assert closed[0].index == 0


# ---------------------------------------------------------------------------
# NEW — unhashable tool_use_id in a *_tool_result block
# ---------------------------------------------------------------------------


def test_unhashable_tool_use_id_does_not_crash_a_server_tool_result():
    """The regression: ``tool_use_id: ["x"]`` raised ``TypeError: unhashable type``.

    Every sibling arm in this branch already coerces (``as_str`` on the id at
    the ``tool_use`` arm, ``isinstance(..., str)`` on the block type); this one
    read ``cb.get("tool_use_id", "")`` straight into a ``dict.get``.
    """
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start(2, {
        "type": "web_search_tool_result",
        "tool_use_id": ["x"],
        "content": [],
    }))
    assert len(out) == 1
    assert isinstance(out[0], dl.ServerToolResultDelta)
    # Unknown pairing -> no builtin tag, rather than a crash.
    assert out[0].builtin is None


def test_dict_tool_use_id_also_does_not_crash():
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start(2, {
        "type": "web_search_tool_result",
        "tool_use_id": {"a": 1},
        "content": [],
    }))
    assert isinstance(out[0], dl.ServerToolResultDelta)


def test_server_tool_result_still_pairs_with_its_call():
    """Control: the pairing the lookup exists for must keep working.

    A ``server_tool_use`` at id X, then its result naming X, must come back
    tagged with the call's name — that is what lets the encoder suppress or
    re-render the provider-hosted pair.
    """
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _start(0, {
        "type": "server_tool_use", "id": "srv_1", "name": "web_search"}))
    out = a.decode_stream_event("content_block_start", _start(1, {
        "type": "web_search_tool_result",
        "tool_use_id": "srv_1",
        "content": [],
    }))
    assert isinstance(out[0], dl.ServerToolResultDelta)
    assert out[0].builtin == "web_search"


def test_missing_tool_use_id_does_not_crash():
    """A missing key defaulted to ``""`` before; keep that behaviour."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start(2, {
        "type": "web_search_tool_result", "content": []}))
    assert isinstance(out[0], dl.ServerToolResultDelta)
    assert out[0].builtin is None


def test_int_tool_use_id_does_not_crash():
    """Hashable but wrong-typed: must not become a set/dict member either."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start(2, {
        "type": "web_search_tool_result", "tool_use_id": 5, "content": []}))
    assert isinstance(out[0], dl.ServerToolResultDelta)
    assert out[0].builtin is None


# ---------------------------------------------------------------------------
# The consequence: a healthy key must not be cooled
# ---------------------------------------------------------------------------


def test_hostile_frames_do_not_cool_a_healthy_key():
    """End-to-end consequence of the escape (the reason these matter).

    A ``TypeError`` out of ``decode_stream_event`` reaches the pump's mid-stream
    handler, which bills the partial and records a fail against the deployment —
    so a frame with no semantics moves a healthy key to ``cooling``. Here we
    drive every hostile frame through a real adapter and assert none of them
    raises; nothing downstream can cool a key for a frame that decoded fine.
    """
    a = fresh_adapter("anthropic")
    hostile = [
        _start([0], {"type": "tool_use", "id": "t", "name": "f"}),
        _start({"i": 0}, {"type": "tool_use", "id": "t", "name": "f"}),
        _stop([0]),
        _frame(type="content_block_delta", index=[0],
               delta={"type": "input_json_delta", "partial_json": "{"}),
        _start(1, {"type": "web_search_tool_result",
                   "tool_use_id": ["x"], "content": []}),
        _start(2, {"type": "web_search_tool_result",
                   "tool_use_id": {"a": 1}, "content": []}),
        _start(3, {"type": "web_search_tool_result", "content": []}),
    ]
    for raw in hostile:
        etype = orjson.loads(raw).get("type")
        out = a.decode_stream_event(etype, raw)
        assert isinstance(out, list), raw


# ---------------------------------------------------------------------------
# A wrong-but-numeric index must not be silently rerouted to block 0
# ---------------------------------------------------------------------------
#
# `_block_index` normalizes a numerically whole float (``1.0`` -> ``1``) and
# returns ``None`` for a value that names no block at all (round 186). Neither
# answer may be ``0`` for an unusable value: ``0`` is a real block, and sending
# another call's args there corrupts a live tool call rather than failing one.
#
# The distinction matters because Anthropic's ``index`` is the provider's own
# content-block id, NOT an array position — the openai/nim decoders can fall
# back to the loop's array index ``i`` because their index is positional, but an
# Anthropic block id has no such fallback.
#
# Note ``hash(1.0) == hash(1)``, so a membership test against the open set
# would have silently passed; the guard has to test the TYPE.


def _tool_start_at(index, block_id: str, name: str) -> str:
    return _start(index, {"type": "tool_use", "id": block_id, "name": name})


def _args_delta(index, fragment: str) -> str:
    return _frame(type="content_block_delta", index=index,
                  delta={"type": "input_json_delta", "partial_json": fragment})


def _args_by_block(a, frames) -> dict[int, str]:
    """Reassemble per-block tool args the way the gateway does."""
    bufs: dict[int, list[str]] = {}
    for etype, data in frames:
        for d in a.decode_stream_event(etype, data):
            if isinstance(d, dl.ToolCallOpen):
                bufs[d.index] = []
            elif isinstance(d, dl.ToolCallArgsDelta):
                bufs.setdefault(d.index, []).append(d.args_fragment)
    return {i: "".join(v) for i, v in bufs.items()}


def test_float_index_keeps_its_own_tool_call_args():
    """``index: 1.0`` names block 1 and must not land on block 0."""
    a = fresh_adapter("anthropic")
    decoded = []
    for raw in [
        _frame(type="message_start", message={"model": "m"}),
        _tool_start_at(0, "A", "Grep"),
        _tool_start_at(1, "B", "Read"),
        _args_delta(1.0, '{"p":"a"}'),
    ]:
        decoded.append((orjson.loads(raw).get("type"), raw))
    args = _args_by_block(a, decoded)
    assert args.get(0, "") == "", f"block 0 stole block 1's args: {args}"
    assert args.get(1) == '{"p":"a"}', f"block 1 lost its args: {args}"


def test_float_index_does_not_concatenate_into_a_sibling_tool():
    """Two tools, float index on the second: the observed corruption.

    Before the fix block 0's args became ``'{"p":"a"}{"q":"b"}'`` — invalid
    JSON, handed to a client that would parse and fail on it.
    """
    a = fresh_adapter("anthropic")
    decoded = []
    for raw in [
        _frame(type="message_start", message={"model": "m"}),
        _tool_start_at(0, "A", "Grep"),
        _tool_start_at(1, "B", "Read"),
        _args_delta(1.0, '{"p":"a"}'),
        _args_delta(0, '{"q":"b"}'),
    ]:
        decoded.append((orjson.loads(raw).get("type"), raw))
    args = _args_by_block(a, decoded)
    assert args.get(0) == '{"q":"b"}', f"block 0 corrupted: {args}"
    assert args.get(1) == '{"p":"a"}', f"block 1 lost its args: {args}"
    for blob in args.values():
        # must be parseable, or the client cannot run the tool
        json.loads(blob)


def test_float_index_still_emits_deltas_for_a_valid_block():
    """The delta itself must survive — not be dropped along with the bad routing."""
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start_at(1, "B", "Read"))
    out = a.decode_stream_event(
        "content_block_delta",
        _args_delta(1.0, '{"city":"Paris"}'))
    assert len(out) == 1, out
    assert isinstance(out[0], dl.ToolCallArgsDelta)
    assert out[0].index == 1, out[0]
    assert out[0].args_fragment == '{"city":"Paris"}'


def test_genuine_int_index_is_untouched():
    """Control: the common path must be unaffected."""
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start_at(1, "B", "Read"))
    out = a.decode_stream_event("content_block_delta", _args_delta(1, '{"a":1}'))
    assert out[0].index == 1


def test_unusable_index_is_dropped_not_guessed():
    """An unaddressable index names no block, so the fragment is dropped.

    Round 128 kept the frame and routed it to block 0 "so the client is not
    charged for a lost fragment". That guess is the corruption AUDIT #419
    records: when a call is live on block 0, the fragment is appended to a
    DIFFERENT call's arguments and the stream still terminates cleanly, so
    nothing downstream can detect it. A fragment with no block is dropped.
    """
    a = fresh_adapter("anthropic")
    for bad in ([0], {"i": 0}, "1", None, True, 1.5):
        out = a.decode_stream_event("content_block_delta", _args_delta(bad, "{"))
        assert out == [], bad


def test_numeric_string_index_is_not_treated_as_numeric():
    """``"1"`` is a coercion hazard, not a number: it must not become block 1.

    Accepting it would be worse than dropping the frame — ``"1"`` and ``1``
    are distinct dict keys, so it would split one tool call across two blocks.
    """
    a = fresh_adapter("anthropic")
    a.decode_stream_event("content_block_start", _tool_start_at(1, "B", "Read"))
    out = a.decode_stream_event("content_block_delta", _args_delta("1", "{"))
    assert out == [], out


def test_float_start_and_stop_stay_paired():
    """A float index on both ends must still open and close the SAME block."""
    a = fresh_adapter("anthropic")
    opened = a.decode_stream_event("content_block_start", _tool_start_at(2.0, "A", "f"))
    closed = a.decode_stream_event("content_block_stop", _stop(2.0))
    assert isinstance(opened[0], dl.ToolCallOpen)
    assert opened[0].index == 2
    assert len(closed) == 1, closed
    assert isinstance(closed[0], dl.ToolCallClose)
    assert closed[0].index == 2, "open and close disagreed — orphaned tool block"
