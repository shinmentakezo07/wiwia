"""Pipeline audit — truncation signalling order (2026-09-21 sweep).

The pump used to emit the terminal ``UsageFinal`` and *then* decide the stream
was truncated, so the client received a usage frame (its "N tokens delivered"
signal, read as the tail of a completed turn) immediately followed by a
``StreamError``. Two contradictory terminals in a row. The truncation decision
now runs first, so a truncated stream carries exactly one terminal.

These tests drive the real pump through ``Gateway.stream`` with a mocked
upstream, the way the round-20 streaming tests do.
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

_URL = "https://audit-pipeline.example/v1/chat/completions"


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://audit-pipeline.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )


def _req() -> ir.Request:
    return ir.Request(model="gpt-x",
                      messages=[ir.Message(role="user",
                                           parts=[ir.TextPart("hi")])],
                      stream=True)


def _chunk(text: str | None = None, usage: dict | None = None) -> bytes:
    delta = {"content": text} if text is not None else {}
    obj: dict = {"id": "c1", "object": "chat.completion.chunk",
                 "model": "gpt-x",
                 "choices": [{"index": 0, "delta": delta,
                              "finish_reason": None}]}
    if usage is not None:
        obj["choices"] = []
        obj["usage"] = usage
    return f"data: {obj}\n\n".encode()


# --------------------------------------------------------------------------
# truncation: usage must not precede the error
# --------------------------------------------------------------------------


@respx.mock
async def test_truncated_stream_emits_no_usage_frame_before_the_error():
    """Content then a usage chunk, with NO finish_reason and NO [DONE] — the
    body just stopped. The client must see one terminal, not usage+error."""
    body = _chunk("hello") + _chunk(usage={"prompt_tokens": 5,
                                           "completion_tokens": 2})
    respx.post(_URL).mock(return_value=httpx.Response(200, content=body))
    gw = Gateway(Router(_config()), CostEngine())
    try:
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
        out = [d async for d in gw.stream(ctx)]
    finally:
        await gw.aclose()

    kinds = [type(d).__name__ for d in out]
    assert "StreamError" in kinds
    assert "UsageFinal" not in kinds, (
        "a truncated stream must not hand the client a usage frame and then "
        f"an error: {kinds}")
    # And the error is the LAST thing the consumer sees.
    assert kinds[-1] == "StreamError"


@respx.mock
async def test_truncated_stream_still_reports_partial_usage_to_the_log():
    """Billing is unaffected by the ordering fix: the tokens delivered before
    the truncation are still priced onto the context."""
    body = _chunk("hello") + _chunk(usage={"prompt_tokens": 9,
                                           "completion_tokens": 4})
    respx.post(_URL).mock(return_value=httpx.Response(200, content=body))
    gw = Gateway(Router(_config()), CostEngine())
    try:
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
        _ = [d async for d in gw.stream(ctx)]
        # The pump priced the partial delivery onto the context.
        assert ctx.usage is not None
    finally:
        await gw.aclose()


@respx.mock
async def test_done_terminated_stream_is_a_clean_stop_with_usage():
    """The healthy mirror: a [DONE]-terminated stream with a usage chunk keeps
    its UsageFinal, its synthesized Finish and its StreamEnd."""
    body = (_chunk("hello")
            + _chunk(usage={"prompt_tokens": 5, "completion_tokens": 2})
            + b"data: [DONE]\n\n")
    respx.post(_URL).mock(return_value=httpx.Response(200, content=body))
    gw = Gateway(Router(_config()), CostEngine())
    try:
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
        out = [d async for d in gw.stream(ctx)]
    finally:
        await gw.aclose()

    assert any(isinstance(d, dl.UsageFinal) for d in out)
    assert any(isinstance(d, dl.StreamEnd) for d in out)
    assert not any(isinstance(d, dl.StreamError) for d in out)
    # Usage precedes the terminal pair, as the contract requires.
    kinds = [type(d).__name__ for d in out]
    assert kinds.index("UsageFinal") < kinds.index("StreamEnd")


@respx.mock
async def test_finish_terminated_stream_keeps_usage_then_finish():
    body = (_chunk("hello")
            + _chunk(usage={"prompt_tokens": 5, "completion_tokens": 2})
            + b'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-x",'
              b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
    respx.post(_URL).mock(return_value=httpx.Response(200, content=body))
    gw = Gateway(Router(_config()), CostEngine())
    try:
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
        out = [d async for d in gw.stream(ctx)]
    finally:
        await gw.aclose()

    kinds = [type(d).__name__ for d in out]
    assert "StreamError" not in kinds
    assert kinds.index("UsageFinal") < kinds.index("Finish") < kinds.index("StreamEnd")


# --------------------------------------------------------------------------
# provider-hosted tool pairing: a result must never ship unpaired
# --------------------------------------------------------------------------


def _server_turn(result_id: str | None, *, extra_call: bool = False) -> tuple[bytes, dict]:
    """Feed a hosted call (optionally two) then one result block."""
    from wiwi.wire import anthropic_messages as am

    enc = am.AnthropicStreamEncoder("m", "r")
    enc.feed(dl.StreamStart(model="m", group=""))
    enc.feed(dl.ToolCallOpen(index=0, id="srv-1", name="web_search",
                             builtin="web_search",
                             block_type="server_tool_use"))
    enc.feed(dl.ToolCallClose(index=0))
    if extra_call:
        enc.feed(dl.ToolCallOpen(index=1, id="srv-2", name="web_search",
                                 builtin="web_search",
                                 block_type="server_tool_use"))
        enc.feed(dl.ToolCallClose(index=1))
    out = enc.feed(dl.ServerToolResultDelta(index=0, block={
        "type": "web_search_tool_result", "tool_use_id": result_id,
        "content": [{"x": 1}]}))
    return out or b"", dict(enc._server_calls)


def test_hosted_result_with_matching_id_pairs_the_call():
    blob, pending = _server_turn("srv-1")
    assert b"server_tool_use" in blob
    assert b"web_search_tool_result" in blob
    assert pending == {}


def test_hosted_result_with_mismatched_id_still_pairs_the_single_call():
    """An upstream that rewrites or drops the call id must not leave the
    result unpaired — the shape Anthropic rejects on replay, and the exact
    case the buffering exists to prevent. With ONE call buffered the pairing
    is unambiguous, so it is made."""
    blob, pending = _server_turn("srv-OTHER")
    assert b"server_tool_use" in blob, (
        "the result shipped without its call: " + repr(blob[:200]))
    assert b"web_search_tool_result" in blob
    assert pending == {}


def test_hosted_result_with_absent_id_pairs_the_single_call():
    blob, pending = _server_turn(None)
    assert b"server_tool_use" in blob
    assert pending == {}


def test_hosted_result_with_ambiguous_id_does_not_guess():
    """Two calls buffered and a result naming neither: guessing would pair the
    result with the WRONG call. Nothing is emitted; the calls stay buffered
    (and are dropped at final_frame, so no unpaired block ships)."""
    blob, pending = _server_turn("zzz", extra_call=True)
    assert b"server_tool_use" not in blob
    assert b"web_search_tool_result" not in blob
    assert sorted(pending) == [0, 1]
