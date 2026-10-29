"""Round 176 — stream-contract defects across decoders, encoders and seams.

Each case is a legal upstream shape whose decode produced a delta sequence no
wire encoder can render honestly (AUDIT #242, #243, #244), an encoder that
orphaned a block when an index was reused (#523), or a seam that broke its own
"never raise" promise (#522).
"""

from __future__ import annotations

import json

import pytest

from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire.anthropic_messages import AnthropicStreamEncoder
from wiwi.wire.openai_responses import ResponsesStreamEncoder


def _ev(adapter, event: str, payload: dict):
    return adapter.decode_stream_event(event, json.dumps(payload))


def _kinds(deltas) -> list[str]:
    return [type(d).__name__ for d in deltas]


# -- #242: OpenRouter's error arm must close open tool calls ----------------


def test_openrouter_error_frame_without_choices_closes_open_tool_calls():
    """An error frame with no ``choices`` is the common OpenRouter shape.

    The close loop was gated on ``choices[0].finish_reason == "error"``, so
    this frame emitted a bare ``StreamError`` with the tool call still open.
    """
    a = OpenRouterAdapter()
    a.reset()
    _ev(a, "", {"choices": [{
        "index": 0,
        "delta": {"tool_calls": [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":'}}]},
        "finish_reason": None}]})
    out = _ev(a, "", {"error": {"message": "upstream died"}})

    kinds = _kinds(out)
    assert "ToolCallClose" in kinds, kinds
    # The error stays the terminal frame: nothing may follow it.
    assert kinds[-1] == "StreamError", kinds
    assert not a._open_tool_indices, "open tool state survived the error"


def test_openrouter_error_frame_still_terminal_with_no_open_tools():
    """A plain error on a text-only stream is unchanged: exactly one frame."""
    a = OpenRouterAdapter()
    a.reset()
    out = _ev(a, "", {"error": {"message": "boom"}})
    assert _kinds(out) == ["StreamError"]


# -- #243: Anthropic must not re-open an index that is already open --------


def test_anthropic_repeated_content_block_start_does_not_orphan_the_first():
    """Two ``content_block_start``s on one index used to emit two Opens.

    The wire encoder then remaps the second block to a fresh index, leaving the
    client with a ``tool_use`` block that is never stopped.
    """
    a = AnthropicAdapter()
    a.reset()
    first = _ev(a, "content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "t1", "name": "get_weather"}})
    second = _ev(a, "content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "t2", "name": "get_time"}})

    deltas = list(first) + list(second)
    kinds = _kinds(deltas)
    # One Open per call, and the superseded call is closed first.
    assert kinds.count("ToolCallOpen") == 2, kinds
    assert "ToolCallClose" in kinds, kinds
    assert kinds.index("ToolCallClose") < len(kinds) - 1, kinds


def test_anthropic_repeated_start_renders_one_closed_block_per_call():
    """End to end: every ``content_block_start`` the client sees is stopped."""
    a = AnthropicAdapter()
    a.reset()
    enc = AnthropicStreamEncoder(model="m", req_id="r")
    deltas = []
    deltas += _ev(a, "content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "t1", "name": "get_weather"}})
    deltas += _ev(a, "content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "t2", "name": "get_time"}})
    deltas += _ev(a, "content_block_delta", {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"x":1}'}})
    deltas += _ev(a, "content_block_stop", {
        "type": "content_block_stop", "index": 0})

    raw = b""
    for d in deltas:
        frame = enc.feed(d)
        if frame:
            raw += frame
    text = raw.decode()
    assert text.count("event: content_block_start") == 2, text
    assert text.count("event: content_block_stop") == 2, text


# -- #244: Gemini must not discard parts riding a blocked frame ------------


def test_gemini_blocked_frame_keeps_the_parts_it_carries():
    """``blockReason`` arrives on a frame that can also carry content.

    The stream decoder returned at ``blockReason`` before decoding
    ``candidates[0].content.parts``, so text delivered in that very frame was
    dropped from a response the client had already been paid for.
    """
    g = GeminiAdapter()
    g.reset()
    out = _ev(g, "", {
        "promptFeedback": {"blockReason": "SAFETY"},
        "candidates": [{"content": {"parts": [{"text": "visible before the block"}]}}],
    })
    texts = [d.text for d in out if isinstance(d, dl.TextDelta)]
    assert "".join(texts) == "visible before the block", _kinds(out)
    # The block still terminates the turn, and nothing follows the terminal.
    kinds = _kinds(out)
    assert "Finish" in kinds and kinds[-1] == "StreamEnd", kinds
    finishes = [d for d in out if isinstance(d, dl.Finish)]
    assert finishes[0].stop_reason == "content_filter", finishes


def test_gemini_blocked_frame_with_usage_still_reports_it():
    g = GeminiAdapter()
    g.reset()
    out = _ev(g, "", {
        "promptFeedback": {"blockReason": "SAFETY"},
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 0},
    })
    usage = [d for d in out if isinstance(d, dl.UsageFinal)]
    assert usage and usage[0].prompt == 11, _kinds(out)


# -- tape accounting: logprobs deltas must count against the bound ---------


def test_tape_charges_logprobs_deltas_against_its_budget():
    """``LogprobsDelta`` is recorded but used to account as 0 bytes.

    A 20k-token response with ``logprobs`` therefore retained ~5 MB of entries
    while ``StreamTape.bytes`` stayed at 0, so the 256 KiB bound never evicted
    anything. The deltas never enter the continuation, but they are stored, so
    they must be charged.
    """
    from wiwi.streaming.resume import StreamTape

    frame = {"content": [{
        "token": "hello", "logprob": -0.12,
        "top_logprobs": [{"token": f"tok{i}", "logprob": -1.0} for i in range(5)],
    }]}
    assert dl.LogprobsDelta(logprobs=frame)

    tape = StreamTape(max_bytes=4096)
    for _ in range(2000):
        tape.append(dl.LogprobsDelta(logprobs=frame))

    assert tape.bytes > 0, "logprobs accounted as zero bytes"
    assert tape.bytes <= 4096, tape.bytes
    assert len(tape._entries) < 2000, "the budget never evicted anything"


# -- validate_tool_args: the documented "never raise" contract --------------


@pytest.mark.parametrize("raw", [None, 5, 1.5, [1], {"a": 1}, True, b"x"])
def test_validate_tool_args_never_raises_on_a_non_str_payload(raw):
    """Its docstring promises it never raises; ``raw_args.encode`` did.

    ``raw_args`` is typed ``str``, but the oversize check and both log paths
    read it through ``encode``/``_fingerprint``. A dict from an adapter that
    skipped ``coerce_args_fragment``, or any other non-string, raised
    ``AttributeError`` straight out of the seam.
    """
    from wiwi.streaming.validation import validate_tool_args

    valid, msg = validate_tool_args(
        "t", raw, {"type": "object", "properties": {"a": {"type": "number"}}})
    assert isinstance(valid, bool)
    assert isinstance(msg, str)




# -- #523: an encoder must close a block an index-reusing Open supersedes ---


def _reopened_stream():
    """A legal delta prefix whose index 0 is opened twice (reuse, no Close)."""
    return [
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="a", name="f1"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"x":1}'),
        dl.ToolCallOpen(index=0, id="b", name="f2"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"y":2}'),
        dl.ToolCallClose(index=0),
        dl.Finish(),
    ]


def test_anthropic_encoder_closes_a_block_superseded_by_index_reuse():
    """``_tool_blocks[index]`` was overwritten, orphaning the first block.

    Every Anthropic content block a client is told to open must be closed. The
    old block's ``content_block_stop`` could never be emitted because the
    mapping that identifies it was gone, so the message ended with an open
    ``tool_use`` block and the client saw a truncated call.
    """
    enc = AnthropicStreamEncoder(model="m", req_id="r")
    raw = b"".join(
        (enc.feed(d) or b"") for d in _reopened_stream()).decode()

    starts = [l for l in raw.splitlines() if '"content_block_start"' in l]
    stops = [l for l in raw.splitlines() if '"content_block_stop"' in l]
    assert len(starts) == 2, raw
    assert len(stops) == 2, f"{len(starts)} blocks opened, {len(stops)} closed"

    opened = {json.loads(l[6:])["index"] for l in starts}
    closed = {json.loads(l[6:])["index"] for l in stops}
    assert opened == closed, f"unclosed block(s): {opened - closed}"


def test_responses_encoder_completes_an_item_superseded_by_index_reuse():
    """``self._tools[index]`` was overwritten, orphaning the first item.

    The Responses protocol pairs ``output_item.added`` with
    ``output_item.done``; an item that is added and never done is rendered as
    truncated even though the turn completed.
    """
    enc = ResponsesStreamEncoder(model="m", req_id="r")
    raw = b"".join(
        (enc.feed(d) or b"") for d in _reopened_stream()
        + [dl.StreamEnd()]).decode()

    def _frames(kind: str) -> list[dict]:
        out = []
        for line in raw.splitlines():
            if not line.startswith("data: "):
                continue
            body = json.loads(line[6:])
            if body.get("type") == f"response.{kind}":
                out.append(body)
        return out

    added, done = _frames("output_item.added"), _frames("output_item.done")
    assert len(added) == 2, raw
    assert len(done) == 2, f"{len(added)} items added, {len(done)} done"
    assert ({a["output_index"] for a in added}
            == {d["output_index"] for d in done})
