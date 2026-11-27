"""Round 216 — back-to-back unframed lines behind a framed event are not dropped."""

from wiwi.streaming.sse import LineSSEParser


def test_consecutive_unframed_lines_after_framed_event_all_survive():
    parser = LineSSEParser(allow_unframed=True)
    assert parser.feed_line('data: {"a":1}') is None
    assert parser.feed_line('{"b":2}').data == '{"a":1}'
    assert parser.feed_line('{"c":3}').data == '{"b":2}'
    assert parser.feed_line('{"d":4}').data == '{"c":3}'
    evt = parser.flush()
    assert evt is not None and evt.data == '{"d":4}'
    assert parser.flush() is None


def test_unframed_run_then_framed_event_keeps_order():
    parser = LineSSEParser(allow_unframed=True)
    out = []
    for line in ['data: {"s":1}', '{"s":2}', '{"s":3}', 'data: {"s":4}', ""]:
        evt = parser.feed_line(line)
        if evt is not None:
            out.append(evt.data)
    evt = parser.flush()
    if evt is not None:
        out.append(evt.data)
    assert out == ['{"s":1}', '{"s":2}', '{"s":3}', '{"s":4}']
