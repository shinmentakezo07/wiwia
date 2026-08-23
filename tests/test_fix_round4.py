"""Regression tests for bug-fix round 4 (2026-08-23).

Covers: build_log_event crash when ctx.usage is None (client disconnects
mid-stream before the pump sets usage).
"""

from wiwi.core.context import RequestContext
from wiwi.core.gateway import build_log_event
from wiwi.ir import types as ir


# -- R1: build_log_event must not crash when usage is None -------------------------
# Happens when a client disconnects mid-stream: the pump is cancelled before
# _price_stream sets ctx.usage, but the finally block still calls build_log_event.
def test_build_log_event_with_none_usage():
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    # simulate a stream that produced tokens then got cancelled before pricing
    ctx.first_token_at = ctx.started
    ctx.last_token_at = ctx.started + 1.0  # stream_secs > 0.05 triggers tps calc
    ctx.usage = None  # pump never set it
    evt = build_log_event(ctx)
    assert evt.tps == 0.0
    assert evt.tok_in == 0
    assert evt.tok_out == 0


def test_build_log_event_with_none_usage_no_tokens():
    """No first_token_at (stream cancelled before any data flowed)."""
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="g", messages=[]))
    ctx.usage = None
    evt = build_log_event(ctx)
    assert evt.tps == 0.0
    assert evt.tok_out == 0
