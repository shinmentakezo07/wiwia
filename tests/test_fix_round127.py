"""Round 127 — Anthropic inbound codec: block ordering and typed-wrong scalars.

Three defects in ``wiwi/wire/anthropic_messages.py``, all found by executing the
real encoder rather than reading it:

1. Parallel ``tool_use`` blocks emitted malformed block ordering — a text block
   opened, filled and closed *inside* a still-open sibling tool block.
2. ``top_k: true`` passed the ``isinstance(..., int)`` guard (``isinstance(True,
   int)`` is True) and reached the upstream as ``{"top_k": true}``.
3. A non-dict ``tool_use.input`` (``"raw string"``, ``[1, 2]``, ``5``) reached the
   upstream verbatim, where Anthropic requires an object.

Anthropic content blocks are strictly sequential, so defect 1 produces SSE the
API itself would never emit and a strict client cannot parse.
"""

from __future__ import annotations

import json
import re

import pytest

from wiwi.ir import types as ir
from wiwi.providers.registry import fresh_adapter
from wiwi.streaming import deltas as dl
from wiwi.wire.anthropic_messages import AnthropicStreamEncoder, decode_request

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _frames(deltas: list) -> list[tuple[str, dict]]:
    """Feed deltas through the encoder and return ``(event, data)`` pairs."""
    enc = AnthropicStreamEncoder("test-model", "req_1")
    raw = b""
    for d in deltas:
        out = enc.feed(d)
        if out:
            raw += out
    tail = enc.final_frame()
    if tail:
        raw += tail
    text = raw.decode()
    return [(m.group(1), json.loads(m.group(2)))
            for m in re.finditer(r"event: (\w+)\ndata: (\{.*?\})\n", text)]


def _block_events(frames) -> list[str]:
    """A compact ``KIND idx`` trace of block lifecycle + delta events."""
    out: list[str] = []
    for event, data in frames:
        if event == "content_block_start":
            kind = data["content_block"]["type"]
            out.append(f"OPEN {data['index']} {kind}")
        elif event == "content_block_delta":
            out.append(f"DELTA {data['index']} {data['delta']['type']}")
        elif event == "content_block_stop":
            out.append(f"CLOSE {data['index']}")
    return out


def _assert_well_formed(trace: list[str]) -> None:
    """Every opened block closes, and content never opens inside a tool block.

    Parallel ``tool_use`` siblings are genuinely open at the same time — the
    Anthropic upstream omits their ``content_block_stop`` and the encoder
    back-fills it (AUDIT #111) — so "one open at a time" is the wrong invariant
    here. The invariant that IS violated by the bug is narrower and is what the
    API cares about: a text/thinking block must never open while any tool block
    is still open, because blocks are strictly sequential and the tool block
    would otherwise wrap it.
    """
    open_idx: set[int] = set()
    for step in trace:
        parts = step.split()
        kind = parts[2] if len(parts) > 2 else ""
        if parts[0] == "OPEN":
            if kind in ("text", "thinking"):
                assert not open_idx, (
                    f"{kind} block {parts[1]} opened while tool block(s) "
                    f"{sorted(open_idx)} still open: {trace}"
                )
            open_idx.add(int(parts[1]))
        elif parts[0] == "DELTA":
            assert int(parts[1]) in open_idx, (
                f"delta for block {parts[1]} which is not open: {trace}"
            )
        elif parts[0] == "CLOSE":
            assert int(parts[1]) in open_idx, (
                f"block {parts[1]} closed but was never open: {trace}"
            )
            open_idx.discard(int(parts[1]))
    assert not open_idx, f"block(s) {sorted(open_idx)} never closed: {trace}"


def _anthropic_body(body: dict) -> dict:
    req = decode_request(body)
    return fresh_adapter("anthropic").encode_request(req, "mid", {})


# ---------------------------------------------------------------------------
# 1. Parallel tool calls: block ordering
# ---------------------------------------------------------------------------


def _parallel_tool_deltas() -> list:
    """Two parallel tool_use blocks, then text that arrives while both are open.

    The Anthropic upstream omits ``content_block_stop`` for parallel siblings,
    so both stay registered in ``_tool_blocks`` — this is the live shape, the
    same one AUDIT #111 was filed against.
    """
    return [
        dl.StreamStart(model="test-model"),
        dl.ToolCallOpen(index=0, id="toolu_a", name="Grep"),
        dl.ToolCallOpen(index=1, id="toolu_b", name="Read"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"pattern":'),
        dl.TextDelta(text="MID"),
        dl.UsageFinal(output=9),
        dl.Finish(stop_reason="tool_call"),
        dl.StreamEnd(),
    ]


def test_parallel_tool_calls_close_every_block_before_deferred_text_opens():
    """The regression: text must not open inside a still-open tool block.

    Before the fix ``final_frame`` flushed the deferred buffer BEFORE sweeping
    the open tool blocks, so the trace was::

        OPEN 0 tool_use / OPEN 1 tool_use / CLOSE 1
        OPEN 2 text / DELTA 2 text_delta / CLOSE 2 / CLOSE 0

    Block 0's stop arrived after a whole text block had opened and closed
    inside it.
    """
    frames = _frames(_parallel_tool_deltas())
    trace = _block_events(frames)
    _assert_well_formed(trace)

    # Every block that opened must also close.
    opened = {int(s.split()[1]) for s in trace if s.startswith("OPEN")}
    closed = {int(s.split()[1]) for s in trace if s.startswith("CLOSE")}
    assert opened == closed, f"unclosed blocks: {trace}"


def test_deferred_text_block_opens_after_every_tool_block_closes():
    """The text block's index must exceed every tool block's — not interleave."""
    trace = _block_events(_frames(_parallel_tool_deltas()))
    tool_indices = [int(s.split()[1]) for s in trace if s.startswith("OPEN") and s.endswith("tool_use")]
    text_indices = [int(s.split()[1]) for s in trace if s.startswith("OPEN") and s.endswith("text")]
    assert tool_indices == [0, 1], trace
    assert text_indices, f"deferred text was dropped: {trace}"
    assert min(text_indices) > max(tool_indices), (
        f"text block opened before the tool group closed: {trace}"
    )


def test_deferred_text_content_survives_the_reordering():
    """Reordering must not drop the buffered text it was protecting."""
    frames = _frames(_parallel_tool_deltas())
    payload = json.dumps(frames)
    assert "MID" in payload, f"buffered text lost: {frames}"


def test_toolcallclose_does_not_flush_while_a_sibling_tool_block_is_open():
    """Closing ONE parallel tool must not drain the buffer.

    ``_open_block`` tracks only the most recently opened block, so the old
    ``self._open_block != "tool"`` guard passed as soon as the first sibling
    closed. The guard must consult ``_tool_blocks`` — the open *set*.
    """
    enc = AnthropicStreamEncoder("test-model", "req_1")
    raw = b""
    for d in [
        dl.StreamStart(model="test-model"),
        dl.ToolCallOpen(index=0, id="toolu_a", name="Grep"),
        dl.ToolCallOpen(index=1, id="toolu_b", name="Read"),
        dl.TextDelta(text="MID"),
        dl.ToolCallClose(index=1),
    ]:
        out = enc.feed(d)
        if out:
            raw += out
    body = raw.decode()
    # The sibling at index 0 is still open, so no text block may have opened yet.
    assert '"type":"text"' not in body.replace(" ", ""), (
        "deferred text flushed while a parallel sibling tool block was still open"
    )


# --- controls: the already-correct cases must not change ---------------------


def test_control_single_tool_still_flushes_deferred_text_after_the_tool_closes():
    """Single tool: today's (correct) output must be preserved byte-for-byte."""
    trace = _block_events(_frames([
        dl.StreamStart(model="test-model"),
        dl.ToolCallOpen(index=0, id="toolu_a", name="Grep"),
        dl.TextDelta(text="MID"),
        dl.UsageFinal(output=9),
        dl.Finish(stop_reason="tool_call"),
        dl.StreamEnd(),
    ]))
    assert trace == [
        "OPEN 0 tool_use", "CLOSE 0", "OPEN 1 text", "DELTA 1 text_delta", "CLOSE 1",
    ], trace
    _assert_well_formed(trace)


def test_control_no_tools_emits_a_single_text_block():
    trace = _block_events(_frames([
        dl.StreamStart(model="test-model"),
        dl.TextDelta(text="hi"),
        dl.UsageFinal(output=2),
        dl.Finish(stop_reason="stop"),
        dl.StreamEnd(),
    ]))
    assert trace == ["OPEN 0 text", "DELTA 0 text_delta", "CLOSE 0"], trace
    _assert_well_formed(trace)


def test_control_parallel_tools_without_deferred_text_still_close_all():
    """No buffered content -> the flush is a no-op and ordering is unchanged."""
    trace = _block_events(_frames([
        dl.StreamStart(model="test-model"),
        dl.ToolCallOpen(index=0, id="toolu_a", name="Grep"),
        dl.ToolCallOpen(index=1, id="toolu_b", name="Read"),
        dl.UsageFinal(output=9),
        dl.Finish(stop_reason="tool_call"),
        dl.StreamEnd(),
    ]))
    assert trace == ["OPEN 0 tool_use", "OPEN 1 tool_use", "CLOSE 1", "CLOSE 0"], trace
    assert len([s for s in trace if s.startswith("OPEN")]) == 2
    assert len([s for s in trace if s.startswith("CLOSE")]) == 2


# ---------------------------------------------------------------------------
# 2. top_k: a bool is not an int
# ---------------------------------------------------------------------------


def test_top_k_bool_is_dropped_rather_than_forwarded():
    """``isinstance(True, int)`` is True, so the old guard let it through.

    ``ir.coerce_int`` — the helper the sibling decoders use — rejects bools
    explicitly, and AUDIT #187's own fix sketch named it.
    """
    req = decode_request({
        "model": "m", "max_tokens": 16, "top_k": True,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.gen_params.top_k is None, req.gen_params.top_k

    body = _anthropic_body({
        "model": "m", "max_tokens": 16, "top_k": True,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert "top_k" not in body, body


def test_top_k_false_is_dropped_too():
    req = decode_request({
        "model": "m", "max_tokens": 16, "top_k": False,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.gen_params.top_k is None


def test_top_k_genuine_int_is_preserved():
    """Control: the guard must not over-correct and drop real values."""
    req = decode_request({
        "model": "m", "max_tokens": 16, "top_k": 40,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.gen_params.top_k == 40
    body = _anthropic_body({
        "model": "m", "max_tokens": 16, "top_k": 40,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert body.get("top_k") == 40, body


# ---------------------------------------------------------------------------
# 3. tool_use.input must be an object
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["raw string", [1, 2], 5, 0.5, True])
def test_non_dict_tool_use_input_is_coerced_to_an_empty_object(bad):
    """Anthropic's ``tool_use.input`` is an object; anything else 400s upstream.

    ``b.get("input") or {}`` only caught falsy values, so every TRUTHY non-dict
    was forwarded verbatim.
    """
    req = decode_request({
        "model": "m", "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "f", "input": bad},
        ]}],
    })
    part = req.messages[0].parts[0]
    assert isinstance(part, ir.ToolUsePart)
    assert part.args == {}, f"non-dict input survived: {part.args!r}"


def test_tool_use_input_upstream_body_is_always_an_object():
    body = _anthropic_body({
        "model": "m", "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "f", "input": "raw string"},
        ]}],
    })
    block = body["messages"][0]["content"][0]
    assert block["type"] == "tool_use"
    assert isinstance(block["input"], dict), block


@pytest.mark.parametrize("block_type", ["server_tool_use", "mcp_tool_use"])
def test_non_dict_server_tool_input_is_coerced_too(block_type):
    """The server-tool arm carries the identical ``or {}`` read."""
    req = decode_request({
        "model": "m", "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": block_type, "id": "srv_1", "name": "web_search", "input": [1, 2]},
        ]}],
    })
    part = req.messages[0].parts[0]
    assert isinstance(part, ir.ToolUsePart)
    assert part.args == {}, f"non-dict input survived: {part.args!r}"


def test_genuine_dict_tool_use_input_is_preserved():
    """Control: a real object must pass through untouched."""
    payload = {"pattern": "foo", "limit": 3}
    req = decode_request({
        "model": "m", "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "f", "input": payload},
        ]}],
    })
    assert req.messages[0].parts[0].args == payload


def test_absent_input_still_defaults_to_an_empty_object():
    req = decode_request({
        "model": "m", "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "f"},
        ]}],
    })
    assert req.messages[0].parts[0].args == {}
