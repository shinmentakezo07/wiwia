"""Round 200 — preserve event order and tool-group boundaries on SSE streams."""

import json

from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import LineSSEParser
from wiwi.wire.anthropic_messages import AnthropicStreamEncoder
from wiwi.wire.openai_responses import ResponsesStreamEncoder


def _events(raw: bytes) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ")]


def test_unframed_event_does_not_overtake_pending_framed_event():
    parser = LineSSEParser(allow_unframed=True)
    assert parser.feed_line('data: {"seq":1}') is None
    assert parser.feed_line('{"seq":2}').data == '{"seq":1}'
    assert parser.feed_line('data: {"seq":3}').data == '{"seq":2}'
    assert parser.feed_line("").data == '{"seq":3}'
    assert parser.flush() is None


def test_unframed_event_at_eof_is_not_lost_behind_pending_frame():
    parser = LineSSEParser(allow_unframed=True)
    assert parser.feed_line('data: {"seq":1}') is None
    assert parser.feed_line('{"seq":2}').data == '{"seq":1}'
    assert parser.flush().data == '{"seq":2}'
    assert parser.flush() is None


def test_anthropic_text_waits_for_sibling_after_current_tool_closes():
    enc = AnthropicStreamEncoder("m", "r200")
    raw = b"".join(enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "a", "alpha"),
        dl.ToolCallOpen(1, "b", "beta"),
        dl.ToolCallClose(1),
        dl.TextDelta("after current close"),
    ])
    events = _events(raw)
    assert [e for e in events if e["type"] == "content_block_delta"
            and e["delta"]["type"] == "text_delta"] == []
    assert enc._open_block == "tool"
    assert enc._open_tool == 0


def test_responses_text_waits_after_current_sibling_closes():
    enc = ResponsesStreamEncoder("m", "r200")
    raw = b"".join(enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "a", "alpha"),
        dl.ToolCallOpen(1, "b", "beta"),
        dl.ToolCallClose(1),
        dl.TextDelta("after-current-close"),
    ])
    assert b"after-current-close" not in raw
    assert enc._item_open == "tool"


def test_responses_stream_end_closes_last_tool_before_message_done():
    enc = ResponsesStreamEncoder("m", "r200")
    raw = b"".join(enc.feed(d) or b"" for d in [
        dl.StreamStart("m"),
        dl.ToolCallOpen(0, "a", "alpha"),
        dl.ToolCallOpen(1, "b", "beta"),
        dl.ToolCallClose(1),
        dl.TextDelta("after-current-close"),
        dl.ToolCallArgsDelta(0, '{"a":1}'),
        dl.StreamEnd(),
    ])
    events = _events(raw)
    done = [(e["output_index"], e["item"]["type"])
            for e in events if e["type"] == "response.output_item.done"]
    assert [e[0] for e in done] == [1, 0, 2]
    assert done[-1][1] == "message"
    assert enc._item_open is None
