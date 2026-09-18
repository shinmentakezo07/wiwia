"""Round 68 — in-band upstream error frames and duplicate ``StreamStart``.

Two streaming defects found by a producer/consumer audit of the
``IRStreamDelta`` taxonomy:

1. ``OpenAIAdapter.decode_stream_event`` (and the NIM adapter's copy of it)
   built its delta list from ``usage`` + ``choices`` only, so an upstream
   frame shaped ``{"error": {...}}`` with no ``choices`` produced ``[]`` and
   was silently dropped. The stream then ended with no terminal delta, and the
   gateway's ``finish is None`` branch synthesized
   ``StreamError("upstream stream ended without completion")`` AND called
   ``_note_stream_failure`` — cooling a healthy deployment and feeding the
   key's retirement ladder for an error the upstream had reported cleanly.
   OpenRouter already mapped this shape (``openrouter_adapter.py:277-281``);
   OpenAI, NIM (its own copy of the loop) and B.A.I (subclass) did not.

2. Mid-stream resume re-emitted ``StreamStart``. ``started`` was set but never
   *checked* before the ``StreamStart`` arm, so a resume pump's opening delta
   was yielded verbatim on top of the original — a second ``message_start``
   mid-stream on the Anthropic surface (AUDIT #159).
"""

from __future__ import annotations

import asyncio

import httpx
import orjson
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
from wiwi.providers.nim_adapter import NimAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl


def _frame(payload: dict) -> str:
    return orjson.dumps(payload).decode()


# --------------------------------------------------------------------------
# 1. In-band error frames must become StreamError, not silence.
# --------------------------------------------------------------------------

def test_openai_adapter_maps_in_band_error_frame_to_stream_error():
    """An upstream ``{"error": ...}`` with no choices must not be dropped.

    Dropping it makes the gateway fabricate a generic "stream ended without
    completion" failure, which cools a healthy deployment and counts against
    the key — the exact misattribution this mapping prevents.
    """
    ad = OpenAIAdapter()
    out = ad.decode_stream_event(
        "", _frame({"error": {"message": "upstream exploded",
                              "type": "server_error"}}))

    errors = [d for d in out if isinstance(d, dl.StreamError)]
    assert errors, f"expected a StreamError, got {out!r}"
    assert "upstream exploded" in errors[0].message


def test_openai_adapter_in_band_error_preserves_provider_type():
    """The provider's own error type survives as ``etype``.

    A mid-stream frame carries no HTTP status, so ``kind``/``status`` alone
    cannot recover the classification a client retries on.
    """
    ad = OpenAIAdapter()
    out = ad.decode_stream_event(
        "", _frame({"error": {"message": "slow down",
                              "type": "rate_limit_error"}}))

    errors = [d for d in out if isinstance(d, dl.StreamError)]
    assert errors, f"expected a StreamError, got {out!r}"
    assert errors[0].etype == "rate_limit_error"


def test_nim_adapter_maps_in_band_error_frame_to_stream_error():
    """NIM carries its own copy of the decode loop, so it needs its own fix."""
    ad = NimAdapter()
    out = ad.decode_stream_event(
        "", _frame({"error": {"message": "nim upstream exploded",
                              "type": "server_error"}}))

    errors = [d for d in out if isinstance(d, dl.StreamError)]
    assert errors, f"expected a StreamError, got {out!r}"
    assert "nim upstream exploded" in errors[0].message


def test_error_frame_does_not_also_emit_a_usage_final():
    """An error frame carrying usage must not contribute a token count.

    The guard has to run *before* the usage parse (the ordering OpenRouter
    uses). Parsing usage first would emit a ``UsageFinal`` for a request that
    failed, feeding a partial count into cost accounting for tokens the
    upstream never actually billed.
    """
    ad = OpenAIAdapter()
    out = ad.decode_stream_event(
        "", _frame({"error": {"message": "boom", "type": "server_error"},
                    "usage": {"prompt_tokens": 999, "completion_tokens": 999}}))

    assert [d for d in out if isinstance(d, dl.StreamError)], out
    assert not [d for d in out if isinstance(d, dl.UsageFinal)], (
        f"a failed frame must not report usage, got {out!r}")


def test_openai_adapter_still_decodes_a_normal_chunk_after_the_error_guard():
    """Control: the error guard must not swallow ordinary content frames."""
    ad = OpenAIAdapter()
    out = ad.decode_stream_event(
        "", _frame({"choices": [{"delta": {"content": "hello"},
                                 "finish_reason": None}]}))

    assert [d.text for d in out if isinstance(d, dl.TextDelta)] == ["hello"]
    assert not [d for d in out if isinstance(d, dl.StreamError)]


# --------------------------------------------------------------------------
# 2. A mid-stream resume must not emit a second StreamStart (AUDIT #159).
#
# The existing guard in tests/test_fix_round20.py cannot catch this: its
# harness uses OpenAI providers, whose adapter emits no StreamStart at all, so
# the gateway synthesizes the single one and the duplicate path is never
# reached. Only an Anthropic provider emits its own StreamStart (from
# ``message_start``), which is the unguarded path this test drives.
# --------------------------------------------------------------------------

def _anthropic_resume_config() -> WiwiConfig:
    """Primary + fallback Anthropic deployments, mid-stream resume enabled."""
    return WiwiConfig(
        providers=[
            ProviderDef(name="ant1", provider="anthropic",
                        base_url="https://r68-a.example/v1",
                        keys=[KeyDef(label="k1", key="sk-ant-1")]),
            ProviderDef(name="ant2", provider="anthropic",
                        base_url="https://r68-b.example/v1",
                        keys=[KeyDef(label="k2", key="sk-ant-2")]),
        ],
        model_list=[
            ModelEntry(model_name="claude-x",
                       wiwi_params=DeploymentParams(provider="ant1",
                                                    model="claude-x")),
            ModelEntry(model_name="claude-x-fb",
                       wiwi_params=DeploymentParams(provider="ant2",
                                                    model="claude-x")),
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            num_retries=0, allowed_fails=1, cooldown_time=60.0,
            stream_resume="enabled", stream_resume_max_retries=2,
            stream_idle_timeout_s=0.2,
            # The inbound surface is "messages", which enables the gateway's
            # ping keep-alive; its default 15 s interval would make this test
            # wait out a keep-alive cycle on every queue read. 0 disables the
            # ping (the pump does a plain queue.get()), keeping the test fast
            # without changing the resume path under test.
            stream_ping_interval_s=0.0,
            fallbacks={"claude-x": ["claude-x-fb", "claude-x"]}),
    )


def _anth_start(prompt: int) -> str:
    return ('event: message_start\ndata: {"type":"message_start","message":'
            f'{{"model":"claude-x","usage":{{"input_tokens":{prompt}}}}}}}\n\n')


_ANTH_BLOCK_START = (
    'event: content_block_start\ndata: {"type":"content_block_start",'
    '"index":0,"content_block":{"type":"text","text":""}}\n\n')


def _anth_text(text: str) -> str:
    return ('event: content_block_delta\ndata: {"type":"content_block_delta",'
            f'"index":0,"delta":{{"type":"text_delta","text":"{text}"}}}}\n\n')


_ANTH_STOP = (
    'event: message_delta\ndata: {"type":"message_delta",'
    '"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n')


@respx.mock
async def test_anthropic_resume_emits_exactly_one_stream_start():
    """A resume on a second Anthropic deployment must not re-open the message.

    Pre-fix the ``StreamStart`` arm yielded unconditionally: ``started`` was
    set but never checked, so the resume pump's own ``message_start`` was
    forwarded verbatim. A client reading a second ``message_start`` treats it
    as a NEW message — Claude Code re-initialises its context meter and
    accumulator and the turn splits across two message objects (AUDIT #159).
    """
    async def dying_body():
        yield _anth_start(11).encode()
        yield _ANTH_BLOCK_START.encode()
        yield _anth_text("partial ").encode()
        await asyncio.sleep(30)  # never finishes -> idle timeout -> StreamError

    async def good_body():
        yield _anth_start(99).encode()
        yield _ANTH_BLOCK_START.encode()
        yield _anth_text("resumed").encode()
        yield _ANTH_STOP.encode()

    respx.post("https://r68-a.example/v1/messages").mock(
        side_effect=lambda request: httpx.Response(
            200, content=dying_body(),
            headers={"content-type": "text/event-stream"}))
    respx.post("https://r68-b.example/v1/messages").mock(
        side_effect=lambda request: httpx.Response(
            200, content=good_body(),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_anthropic_resume_config()), CostEngine())
    ctx = RequestContext(
        surface="messages",
        ir_req=ir.Request(
            model="claude-x",
            messages=[ir.Message(role="user",
                                 parts=[ir.TextPart(text="hi")])]),
        group="claude-x")
    out = []
    try:
        async for d in gw.stream(ctx):
            out.append(d)
    finally:
        await gw.aclose()

    starts = [d for d in out if isinstance(d, dl.StreamStart)]
    assert len(starts) == 1, (
        "the resume attempt re-emitted StreamStart; an Anthropic client reads "
        f"a second message_start as a new message (got {len(starts)}): "
        f"{[type(d).__name__ for d in out]}")
    # The resumed attempt's text must still reach the client.
    text = "".join(d.text for d in out if isinstance(d, dl.TextDelta))
    assert "resumed" in text, text


# --------------------------------------------------------------------------
# 3. A hosted tool-search call must not be labelled a web search (B4).
#
# ``_builtin_call_item`` hardcoded ``web_search_call`` for every hosted
# builtin, so an Anthropic ``tool_search_tool_bm25``/``_regex`` step (which
# maps onto the Responses surface's generic ``tool_search``) was rendered to a
# Responses client as a WEB SEARCH. The OpenAI SDK models these as distinct
# output items — ``ResponseToolSearchCall`` has ``type: "tool_search_call"``
# with ``arguments``/``execution``, versus ``ResponseFunctionWebSearch``'s
# ``type: "web_search_call"`` with ``action.query`` — so a client rendering the
# trace showed a search that never happened and lost the real search step.
# --------------------------------------------------------------------------

def test_responses_sync_labels_tool_search_as_tool_search_call():
    """A tool_search builtin renders as ``tool_search_call``, not a web search."""
    from wiwi.wire import openai_responses as oresp

    turn = ir.AssistantTurn(
        text="",
        tool_calls=[ir.ToolUsePart(
            id="st_1", name="tool_search_tool_bm25_20251119",
            args={"query": "crm tools"}, builtin="tool_search_tool_bm25_20251119")],
        thinking=[], usage=ir.Usage())
    ctx = RequestContext(surface="responses",
                         ir_req=ir.Request(model="gpt-5", messages=[]),
                         group="gpt-5")
    body = oresp.encode_response(ctx, turn, "gpt-5", "req1")

    items = [i for i in body["output"] if i.get("type", "").endswith("_call")]
    assert items, body["output"]
    assert items[0]["type"] == "tool_search_call", (
        f"a tool_search step must not be labelled a web search: {items[0]!r}")


def test_responses_web_search_still_renders_as_web_search_call():
    """Control: a real web search keeps its own item type and shape."""
    from wiwi.wire import openai_responses as oresp

    turn = ir.AssistantTurn(
        text="",
        tool_calls=[ir.ToolUsePart(
            id="ws_1", name="web_search_20250305",
            args={"query": "weather"}, builtin="web_search_20250305")],
        thinking=[], usage=ir.Usage())
    ctx = RequestContext(surface="responses",
                         ir_req=ir.Request(model="gpt-5", messages=[]),
                         group="gpt-5")
    body = oresp.encode_response(ctx, turn, "gpt-5", "req1")

    items = [i for i in body["output"] if i.get("type", "").endswith("_call")]
    assert items[0]["type"] == "web_search_call", items[0]
    assert items[0]["action"]["query"] == "weather", items[0]


def test_responses_tool_search_item_matches_the_sdk_shape():
    """The item must carry the fields ``ResponseToolSearchCall`` declares.

    The SDK models it as ``{type, id, arguments, call_id, execution, status}``
    — ``arguments`` (not ``action``) and an ``execution`` of "server" for a
    provider-hosted search. Emitting web_search_call's shape here would leave
    the client unable to read the item at all.
    """
    from wiwi.wire import openai_responses as oresp

    turn = ir.AssistantTurn(
        text="",
        tool_calls=[ir.ToolUsePart(
            id="st_2", name="tool_search_tool_regex_20251119",
            args={"query": "orders"}, builtin="tool_search_tool_regex_20251119")],
        thinking=[], usage=ir.Usage())
    ctx = RequestContext(surface="responses",
                         ir_req=ir.Request(model="gpt-5", messages=[]),
                         group="gpt-5")
    body = oresp.encode_response(ctx, turn, "gpt-5", "req1")

    item = next(i for i in body["output"]
                if i.get("type") == "tool_search_call")
    assert item["execution"] == "server", item
    assert item["status"] == "completed", item
    assert "arguments" in item, item


# --------------------------------------------------------------------------
# 4. A mid-stream failure must close the items it leaves open (B5).
#
# ``_completed()`` sweeps every open output item before its terminal event
# (AUDIT #111), but the ``StreamError`` arm emitted ``response.failed``
# immediately. A client that had already received ``response.output_item.added``
# for a message/tool therefore never saw the matching ``output_item.done``, and
# the item stayed in_progress forever. The Anthropic encoder closes its blocks
# on the error path; this surface did not.
# --------------------------------------------------------------------------

def _responses_frames(enc, deltas) -> str:
    out = b""
    for d in deltas:
        f = enc.feed(d)
        if f:
            out += f
    return out.decode()


def test_responses_stream_error_closes_open_message_item():
    """A failure after a message opened must emit its output_item.done."""
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("gpt-5", "req1")
    body = _responses_frames(enc, [
        dl.StreamStart(model="gpt-5"),
        dl.TextDelta("partial answer"),
        dl.StreamError(message="upstream died", kind="connection"),
    ])

    assert "response.output_item.added" in body, body
    assert "response.output_item.done" in body, (
        "the open message item was never closed on the error path:\n" + body)
    # The failure is still reported.
    assert "response.failed" in body, body


def test_responses_stream_error_closes_open_tool_item():
    """A failure with a tool call open must close that item too."""
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("gpt-5", "req1")
    body = _responses_frames(enc, [
        dl.StreamStart(model="gpt-5"),
        dl.ToolCallOpen(index=0, id="call_1", name="get_weather"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"city":'),
        dl.StreamError(message="upstream died", kind="connection"),
    ])

    assert "response.output_item.done" in body, (
        "the open tool item was never closed on the error path:\n" + body)
    assert "response.failed" in body, body


# --------------------------------------------------------------------------
# 5. The Anthropic encoder's deferred buffer must be bounded (B6).
#
# Text/thinking arriving behind an open tool_use block is buffered in
# ``_deferred`` and flushed when the tool closes (AUDIT #156). Nothing capped
# that list, so a stream that interleaves unboundedly behind one long-open
# tool block grows the buffer without limit — the same unbounded-growth class
# that ``MAX_TOOL_ARGS_BYTES`` (streaming/validation.py) and the coalescer's
# ``max_bytes`` already guard. A cap that drops the OLDEST entries keeps the
# most recent content (what the model is actually saying now) and bounds memory.
# --------------------------------------------------------------------------

def test_anthropic_deferred_buffer_is_bounded():
    """Deferred text must not grow without limit behind an open tool block."""
    from wiwi.wire.anthropic_messages import (
        MAX_DEFERRED_CHARS,
        AnthropicStreamEncoder,
    )

    enc = AnthropicStreamEncoder("claude-x", "req1")
    enc.feed(dl.StreamStart(model="claude-x"))
    enc.feed(dl.ToolCallOpen(index=0, id="t1", name="tool"))

    # Far more deferred text than the cap allows, all while the tool is open.
    chunk = "x" * 4096
    for _ in range(200):
        enc.feed(dl.TextDelta(chunk))

    total = sum(len(t) for _, t, _ in enc._deferred)
    assert total <= MAX_DEFERRED_CHARS, (
        f"deferred buffer grew to {total} chars, cap is {MAX_DEFERRED_CHARS}")
    assert enc._deferred, "the buffer must still hold the most recent content"


def test_anthropic_deferred_keeps_the_newest_content_when_capped():
    """On overflow the most recent text survives, not the oldest."""
    from wiwi.wire.anthropic_messages import (
        MAX_DEFERRED_CHARS,
        AnthropicStreamEncoder,
    )

    enc = AnthropicStreamEncoder("claude-x", "req1")
    enc.feed(dl.StreamStart(model="claude-x"))
    enc.feed(dl.ToolCallOpen(index=0, id="t1", name="tool"))

    enc.feed(dl.TextDelta("OLDEST"))
    for _ in range(200):
        enc.feed(dl.TextDelta("y" * 4096))
    enc.feed(dl.TextDelta("NEWEST"))

    joined = "".join(t for _, t, _ in enc._deferred)
    assert len(joined) <= MAX_DEFERRED_CHARS
    assert "NEWEST" in joined, "the newest content was dropped instead of the oldest"
    assert "OLDEST" not in joined, "the oldest content should be evicted first"


def test_anthropic_deferred_caps_a_single_oversized_delta():
    """One delta larger than the cap must still be trimmed to the cap.

    The eviction loop has to handle the case where a single entry alone
    exceeds ``MAX_DEFERRED_CHARS`` — otherwise a model that emits one huge
    block behind an open tool slips past the cap entirely.
    """
    from wiwi.wire.anthropic_messages import (
        MAX_DEFERRED_CHARS,
        AnthropicStreamEncoder,
    )

    enc = AnthropicStreamEncoder("claude-x", "req1")
    enc.feed(dl.StreamStart(model="claude-x"))
    enc.feed(dl.ToolCallOpen(index=0, id="t1", name="tool"))
    enc.feed(dl.TextDelta("z" * (MAX_DEFERRED_CHARS * 3)))

    total = sum(len(t) for _, t, _ in enc._deferred)
    assert total <= MAX_DEFERRED_CHARS, (
        f"a single oversized delta escaped the cap: {total} > {MAX_DEFERRED_CHARS}")
