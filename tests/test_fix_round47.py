"""Round-47 regressions: streaming delta-contract defects.

Each test below fails against the pre-fix source and pins the observable
contract, not the implementation.

  * #111 — encoders close only ONE open tool block on the terminal frame, so
    every parallel tool call but the last is never finished for the client.
  * #129 — the OpenAI adapter's synthesized-open index is not cleared at
    ``finish_reason``, so a tool call that reuses that index after the
    finish frame emits ``ToolCallArgsDelta`` with no preceding
    ``ToolCallOpen``.
"""

from __future__ import annotations

import json

import orjson

from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire.anthropic_messages import AnthropicStreamEncoder
from wiwi.wire.openai_responses import ResponsesStreamEncoder


def _chunk(**delta) -> str:
    return orjson.dumps({"choices": [{"index": 0, "delta": delta}]}).decode()


# ---------------------------------------------------------------------------
# #111 — terminal frame must close EVERY open tool block, not just one.
# ---------------------------------------------------------------------------


def _parallel_tool_seq() -> list[dl.IRStreamDelta]:
    """Two tool calls open at once, neither closed by the adapter (the
    Anthropic upstream ends the message with both still open)."""
    return [
        dl.StreamStart(model="claude"),
        dl.ToolCallOpen(index=0, id="t0", name="get_weather"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"city":"SF"}'),
        dl.ToolCallOpen(index=1, id="t1", name="get_time"),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"tz":"PT"}'),
        dl.UsageFinal(prompt=5, output=7),
        dl.Finish(stop_reason="tool_call"),
        dl.StreamEnd(),
    ]


def _drive(encoder, seq):
    body = b""
    for d in seq:
        c = encoder.feed(d)
        if c:
            body += c
    return body


def test_anthropic_encoder_closes_every_open_tool_block():
    """Anthropic content blocks are strictly nested: every
    ``content_block_start`` must be matched by a ``content_block_stop``, and
    a ``tool_use`` block that never stops is a malformed stream that Claude
    Code rejects."""
    enc = AnthropicStreamEncoder("claude", "req1")
    body = _drive(enc, _parallel_tool_seq())
    full = (body + enc.final_frame()).decode()

    starts = full.count('"type":"content_block_start"')
    stops = full.count('"type":"content_block_stop"')
    assert starts == 2, f"expected 2 tool blocks opened, got {starts}"
    assert stops == starts, (
        f"only {stops} content_block_stop for {starts} content_block_start: "
        "the first parallel tool call is never finished for the client"
    )
    # Both tool blocks must be individually closed by index.
    assert '"content_block_stop","index":0' in full.replace(" ", "")
    assert '"content_block_stop","index":1' in full.replace(" ", "")


def test_responses_encoder_closes_every_open_tool_item():
    """Codex CLI counts tool calls from the terminal response payload, so an
    unclosed parallel call is both a missing ``output_item.done`` and a
    missing entry in ``response.completed``'s output array."""
    enc = ResponsesStreamEncoder("gpt", "req1")
    body = _drive(enc, _parallel_tool_seq())
    full = (body + enc._completed()).decode()

    added = full.count('"response.output_item.added"')
    done = full.count('"response.output_item.done"')
    assert added == 2, f"expected 2 items opened, got {added}"
    assert done == added, (
        f"only {done} output_item.done for {added} output_item.added: "
        "the first parallel tool call is never finished for the client"
    )

    completed = [ln for ln in full.split("\n") if '"response.completed"' in ln]
    assert completed, "no terminal response.completed event"
    resp = json.loads(completed[0][len("data: "):])
    fc = [i for i in resp["response"]["output"] if i.get("type") == "function_call"]
    assert len(fc) == 2, (
        f"terminal payload carries {len(fc)} of 2 function_call items: "
        "the client loses a parallel tool call entirely"
    )


# ---------------------------------------------------------------------------
# #129 — synthesized-open index must not survive the finish frame.
# ---------------------------------------------------------------------------


def test_openai_adapter_reopen_after_finish_emits_open_before_args():
    """A provider that omits ``id`` on the first tool chunk makes the adapter
    synthesize a ``ToolCallOpen``. That index is cleared from
    ``_open_tool_indices`` at ``finish_reason`` but was left in
    ``_synthesized_opens``, so a tool call reusing the index after the finish
    frame took the "adopt the real id" branch and emitted
    ``ToolCallArgsDelta`` with no preceding ``ToolCallOpen`` — args routed to
    a tool call the encoder never opened, and the args silently dropped."""
    ad = OpenAIAdapter()
    ad.reset()
    # 1. args with no id -> synthesized Open + args
    ad.decode_stream_event("", _chunk(
        tool_calls=[{"index": 0, "function": {"arguments": '{"a":1}'}}]))
    # 2. finish_reason closes all open calls
    ad.decode_stream_event("", orjson.dumps(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}).decode())
    # 3. a NEW call reusing index 0, now carrying a real id
    deltas = ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "call_real",
         "function": {"name": "g", "arguments": '{"b":2}'}}]))

    kinds = [type(d).__name__ for d in deltas]
    assert "ToolCallArgsDelta" in kinds, f"args should still be forwarded: {kinds}"
    assert "ToolCallOpen" in kinds, (
        f"ToolCallArgsDelta emitted with no preceding ToolCallOpen "
        f"(contract violation): {kinds}"
    )
    assert kinds.index("ToolCallOpen") < kinds.index("ToolCallArgsDelta"), (
        f"ToolCallOpen must precede ToolCallArgsDelta, got {kinds}"
    )
    assert next(d for d in deltas if isinstance(d, dl.ToolCallOpen)).index == 0
