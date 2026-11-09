"""Round 203 — the Chat stream encoder must not emit text mid-tool-call (AUDIT #594).

The Anthropic upstream can interleave prose with a *still-open* parallel tool
call: index 0 opens, index 1 opens and closes, then the model writes text under
index 0 before index 0's `content_block_stop`. The Anthropic decoder forwards
that as `ToolCallOpen(0)`, `ToolCallOpen(1)`, `ToolCallClose(1)`,
`TextDelta`, `ToolCallArgsDelta(0)`, `ToolCallClose(0)`.

`ChatStreamEncoder` had no tool-open tracking at all, so the text went out as a
`content` chunk between two `tool_calls` fragments. Both sibling encoders defer
it (`ResponsesStreamEncoder` since AUDIT #367, `AnthropicStreamEncoder` since
#156); the Chat surface was the one that leaked.
"""

import json

from wiwi.streaming import deltas as dl
from wiwi.wire.openai_chat import ChatStreamEncoder


def _chunks(raw: bytes) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ") and line[6:].strip() != "[DONE]"]


def _text_out(raw: bytes) -> str:
    return "".join(c["choices"][0]["delta"].get("content") or ""
                   for c in _chunks(raw))


def _tool_frames(raw: bytes) -> list[dict]:
    return [tc for c in _chunks(raw)
            for tc in c["choices"][0]["delta"].get("tool_calls") or []]


# The exact delta sequence a real Anthropic SSE stream produces, verified by
# driving AnthropicAdapter.decode_stream_event and feeding the result here.
_INTERLEAVED = [
    dl.StreamStart("m"),
    dl.ToolCallOpen(0, "t0", "alpha"),
    dl.ToolCallOpen(1, "t1", "beta"),
    dl.ToolCallArgsDelta(1, '{"b":1}'),
    dl.ToolCallClose(1),
    dl.ToolCallArgsDelta(0, '{"a":'),
    dl.TextDelta("Let me also check X."),
    dl.ToolCallArgsDelta(0, "1}"),
    dl.ToolCallClose(0),
]


def test_text_waits_for_the_last_open_sibling():
    """The defect: text escaped while tool 0 was still open."""
    enc = ChatStreamEncoder("m", "r203")
    frames = [enc.feed(d) for d in _INTERLEAVED]
    before_last_close = b"".join(f for f in frames[:8] if f)
    assert _text_out(before_last_close) == ""


def test_text_is_delivered_after_the_last_sibling_closes():
    """Deferred, not dropped — the prose is part of the answer (#156/#367)."""
    enc = ChatStreamEncoder("m", "r203")
    raw = b"".join(enc.feed(d) or b"" for d in _INTERLEAVED)
    assert _text_out(raw) == "Let me also check X."


def test_deferred_text_does_not_introduce_a_new_tool_call():
    """Still exactly two calls, one Open each — the deferral adds no phantom."""
    enc = ChatStreamEncoder("m", "r203")
    raw = b"".join(enc.feed(d) or b"" for d in _INTERLEAVED)
    opens = [tc for tc in _tool_frames(raw) if "id" in tc]
    assert [tc["index"] for tc in opens] == [0, 1]
    assert {tc["function"]["name"] for tc in opens} == {"alpha", "beta"}


def test_args_after_deferred_text_still_reach_their_own_index():
    """The text must not close a tool: tool 0's later args still route to 0."""
    enc = ChatStreamEncoder("m", "r203")
    raw = b"".join(enc.feed(d) or b"" for d in _INTERLEAVED)
    args: dict[int, str] = {}
    for tc in _tool_frames(raw):
        frag = (tc.get("function") or {}).get("arguments")
        if frag:
            args[tc["index"]] = args.get(tc["index"], "") + frag
    assert args == {0: '{"a":1}', 1: '{"b":1}'}


def test_sequential_text_with_no_open_tool_is_unchanged():
    """A plain text stream must still be emitted inline, chunk for chunk."""
    enc = ChatStreamEncoder("m", "r203")
    raw = b"".join(enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.TextDelta("Hello "),
        dl.TextDelta("world"),
    ])
    assert [c["choices"][0]["delta"].get("content") for c in _chunks(raw)] == [
        "", "Hello ", "world"]


def test_text_after_a_tool_fully_closes_is_inline_again():
    """Once no tool is open, deferral must not keep buffering."""
    enc = ChatStreamEncoder("m", "r203")
    frames = [enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "t0", "alpha"),
        dl.ToolCallClose(0),
        dl.TextDelta("done"),
    ]]
    assert b"done" in frames[-1]


def test_thinking_waits_for_the_last_open_sibling_too():
    """reasoning_content is prose as well; the same rule applies."""
    enc = ChatStreamEncoder("m", "r203")
    frames = [enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "t0", "alpha"),
        dl.ToolCallOpen(1, "t1", "beta"),
        dl.ToolCallClose(1),
        dl.ThinkingDelta("hmm"),
        dl.ToolCallClose(0),
    ]]
    before = b"".join(f for f in frames[:5] if f)
    assert b"hmm" not in before
    assert any((c["choices"][0]["delta"].get("reasoning_content") or "") == "hmm"
               for c in _chunks(b"".join(f for f in frames if f)))


def test_builtin_tool_does_not_hold_text_hostage():
    """A suppressed hosted call never opens a client-visible call, so it must
    not defer prose behind a Close the Anthropic surface may never send."""
    enc = ChatStreamEncoder("m", "r203")
    frames = [enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "s0", "web_search", builtin="web_search"),
        dl.TextDelta("searching"),
    ]]
    assert b"searching" in frames[-1]

def test_prose_buffered_at_stream_end_is_drained_by_final_frame():
    """The turn can end with a call still open and no Close ever arriving.

    `final_frame` is the guaranteed drain point (app.py always calls it), so
    buffered prose must land before the finish chunk rather than vanish.
    """
    enc = ChatStreamEncoder("m", "r203")
    raw = b"".join(enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "t0", "alpha"),
        dl.TextDelta("orphan prose"),
    ])
    assert b"orphan prose" not in raw
    raw += enc.final_frame()
    assert b"orphan prose" in raw
    # ...and it precedes the finish chunk that closes the turn.
    assert raw.index(b"orphan prose") < raw.index(b'"finish_reason":"stop"')


def test_final_frame_with_empty_buffer_is_unchanged():
    """No tool call, no deferral: the finish frame is exactly what it was."""
    enc = ChatStreamEncoder("m", "r203")
    for d in [dl.StreamStart("m"), dl.TextDelta("hi")]:
        enc.feed(d)
    assert enc.final_frame().startswith(b"data: ")

def test_stop_sequence_branch_does_not_drop_buffered_prose():
    """The stop_sequence rebuild must append to the drained buffer, not replace it."""
    enc = ChatStreamEncoder("m", "r203")
    for d in [dl.StreamStart("m"), dl.ToolCallOpen(0, "t0", "alpha"),
              dl.TextDelta("kept prose"), dl.Finish("stop", stop_sequence="END")]:
        enc.feed(d)
    raw = enc.final_frame()
    assert b"kept prose" in raw
    assert b"stop_sequence" in raw
