"""Round 190 — ``iter_sse_events`` must flush the parser at end of stream.

``LineSSEParser.feed_line`` emits only at a blank-line boundary, and
``flush`` exists for the body that closes on its last ``data:`` line with no
trailing blank line (DeepSeek/B.A.I send ``"data: [DONE]\\n"``). The wrapper
never called it, so the final frame — including the ``[DONE]`` sentinel a
decoder needs to finish a turn cleanly — was dropped (AUDIT #565).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from wiwi.streaming.sse import iter_sse_events


async def _collect(lines: list[str]) -> list[tuple[str, str]]:
    async def source() -> AsyncIterator[str]:
        for line in lines:
            yield line

    return [evt async for evt in iter_sse_events(source())]


async def test_final_frame_without_a_trailing_blank_line_is_emitted():
    # The blank line closes frame 1; frame 2 ends at EOF with no terminator.
    assert await _collect(['data: {"a": 1}', "", "data: [DONE]"]) == [
        ("", '{"a": 1}'),
        ("", "[DONE]"),
    ]


async def test_final_event_name_survives_the_flush():
    assert await _collect(
        ['data: {"a": 1}', "", "event: message_stop", 'data: {"type": "message_stop"}']
    ) == [
        ("", '{"a": 1}'),
        ("message_stop", '{"type": "message_stop"}'),
    ]


async def test_trailing_blank_line_does_not_duplicate_the_frame():
    # The control: a body that already ended on a blank line yields each frame
    # exactly once (flush is idempotent).
    assert await _collect(['data: {"a": 1}', "", "data: [DONE]", ""]) == [
        ("", '{"a": 1}'),
        ("", "[DONE]"),
    ]


async def test_multi_line_data_is_joined_and_flushed():
    assert await _collect(["data: a", "data: b"]) == [("", "a\nb")]


async def test_empty_stream_yields_nothing():
    assert await _collect([]) == []


async def test_comment_only_tail_is_not_emitted():
    assert await _collect(['data: {"a": 1}', "", ": heartbeat"]) == [("", '{"a": 1}')]


async def test_last_frame_is_not_lost_after_an_earlier_blank_boundary():
    assert await _collect(["data: one", "", "data: two"]) == [("", "one"), ("", "two")]
