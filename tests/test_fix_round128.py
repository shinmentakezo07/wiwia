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
    """The regression: ``index: [0]`` raised ``TypeError: unhashable type``."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _start([0], {
        "type": "tool_use", "id": "toolu_1", "name": "f"}))
    assert len(out) == 1
    assert isinstance(out[0], dl.ToolCallOpen)
    # Coerced to a usable int rather than propagated.
    assert isinstance(out[0].index, int)
    assert not isinstance(out[0].index, bool)


def test_unhashable_index_does_not_crash_input_json_delta():
    """The ``input_json_delta`` arm reads ``index`` too (args must route right)."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_delta", _frame(
        type="content_block_delta", index=[1],
        delta={"type": "input_json_delta", "partial_json": '{"a":'}))
    assert len(out) == 1
    assert isinstance(out[0], dl.ToolCallArgsDelta)
    assert isinstance(out[0].index, int)


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


def test_bool_index_is_coerced_away():
    """``isinstance(True, int)`` is True, so a bool must be rejected explicitly."""
    a = fresh_adapter("anthropic")
    out = a.decode_stream_event("content_block_start", _tool_start(True))
    assert isinstance(out[0].index, int)
    assert not isinstance(out[0].index, bool)


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
