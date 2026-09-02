"""Round-20 regression tests: SSE parser flush + streaming hardens.

Regression targets (see the SSE/streaming audit and the round-20 fix plan):

- ``LineSSEParser`` buffered the final SSE frame when a stream ended without a
  trailing blank line (DeepSeek/B.A.I send ``"data: [DONE]\\n"`` with no final
  ``"\\n\\n"``). The frame was never emitted, so a ``[DONE]``-terminated stream
  looked like a mid-stream drop and wiwi emitted ``StreamError`` instead of a
  clean ``finish_reason``. Fix: ``flush()`` on the parser + wiring at the two
  ``gateway.py`` consumers.
"""

from __future__ import annotations

import httpx
import respx

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import LineSSEParser

# ---------------------------------------------------------------------------
# LineSSEParser.flush
# ---------------------------------------------------------------------------

def test_sse_parser_flush_emits_final_frame():
    """A stream ending with "data: [DONE]\\n" (no trailing blank line) must
    still yield the frame on flush() and clear its buffer."""
    p = LineSSEParser()
    assert p.feed_line("data: [DONE]") is None  # buffered, not emitted
    evt = p.flush()
    assert evt is not None
    assert evt.event == ""
    assert evt.data == "[DONE]"
    # buffer is drained; a second flush is a no-op.
    assert p.flush() is None


def test_sse_parser_flush_noop_after_blank_line():
    """A normal "data: x\\n\\n" stream already emitted on the blank line; the
    after-loop flush must not double-emit it."""
    p = LineSSEParser()
    evt = p.feed_line("data: x")
    assert evt is None
    evt2 = p.feed_line("")  # blank line -> emit
    assert evt2 is not None and evt2.data == "x"
    assert p.flush() is None  # nothing left to flush


def test_sse_parser_flush_joins_multiline_data():
    """Multi-line data (SSE \\n-joined) flushes as a single joined payload."""
    p = LineSSEParser()
    p.feed_line("data: a")
    p.feed_line("data: b")
    evt = p.flush()
    assert evt is not None and evt.data == "a\nb"


# ---------------------------------------------------------------------------
# Gateway streaming: [DONE]-terminated stream with NO trailing blank line
# ---------------------------------------------------------------------------

def _gw_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round20.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )


def _gw_req() -> ir.Request:
    return ir.Request(model="gpt-x",
                      messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
                      stream=True)


def _text_chunk(text: str) -> str:
    return ('{"id":"c1","object":"chat.completion.chunk","model":"gpt-x",'
            f'"choices":[{{"index":0,"delta":{{"content":"{text}"}},'
            '"finish_reason":null}]}')


@respx.mock
async def test_done_no_blank_line_is_clean_completion():
    """DeepSeek/B.A.I close the stream with "data: [DONE]\\n" and NO trailing
    blank line. This must be a clean completion (synthesized Finish('stop')) —
    the un-flushed parser previously turned it into a StreamError."""
    # Last frame is missing its newline: "data: [DONE]\n" (closed by EOF, no
    # fence blank-line after it). Simulate via httpx body ending with "\n".
    body = (
        f"data: {_text_chunk('hello')}\n\n".encode()
        + b"data: [DONE]\n"  # no trailing blank line
    )
    gw = Gateway(Router(_gw_config()), CostEngine())
    try:
        respx.post("https://round20.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body))
        ctx = RequestContext(surface="chat", ir_req=_gw_req(), group="gpt-x")
        out = []
        async for d in gw.stream(ctx):
            out.append(d)
        assert not any(isinstance(d, dl.StreamError) for d in out), \
            "a [DONE]-terminated stream without a trailing blank line must " \
            "not be treated as a mid-stream drop"
        assert any(isinstance(d, dl.Finish) and d.stop_reason == "stop"
                   for d in out)
        assert any(isinstance(d, dl.StreamEnd) for d in out)
        key = next(k for k in gw.router.providers["p1"].keys
                   if k.label == ctx.provider_key.label)
        assert key.status == "active"
    finally:
        await gw.aclose()
