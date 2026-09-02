"""Round-25 regression: parallel tool-call encoder integrity on the Responses
and Anthropic surfaces, plus pins for the stream-pump encode guard and the
streaming Retry-After path.

Regression targets:

- ``ResponsesStreamEncoder._close_item()`` emitted ``output_item.done`` for the
  currently-open tool but left the entry in ``self._tools``, so a later
  ``ToolCallClose`` for that index re-emitted a SECOND ``output_item.done`` at
  the same ``output_index``. Reachable whenever a ``TextDelta``/``ThinkingDelta``
  arrives while two tool calls are open (one tool is closed by the interleave,
  then closed again by its own Close). Duplicate ``output_item.done`` makes
  Codex CLI count a phantom tool call. Fix: ``_close_item`` delegates to
  ``_close_tool``, which pops the entry.
- AUDIT #2's coverage gap: the existing parallel test
  (``test_responses_encoder_parallel_tool_calls_preserved``) only checked
  ``call_id``/``name`` containment and never asserted ``output_index``, so the
  index-corruption class of bug could pass. These tests assert per-index
  routing of args deltas and close events on both surfaces.
- AUDIT #1 (stream pump deadlock when ``encode_request`` throws before
  ``ready.set()``) and AUDIT #4 (streaming path never parsed ``Retry-After``)
  were fixed in ``84b084a`` with no regression test; pinned here so they stay
  fixed.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
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
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire.openai_responses import ResponsesStreamEncoder

# -- helpers -------------------------------------------------------------------

def _events(blob) -> list[dict]:
    """Parse an SSE blob into the JSON payload of each frame."""
    if isinstance(blob, (bytes, bytearray)):
        blob = blob.decode()
    out = []
    for frame in blob.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: "):]))
    return out


def _feed(enc, seq) -> bytes:
    out = b""
    for d in seq:
        chunk = enc.feed(d)
        if chunk:
            out += chunk if isinstance(chunk, (bytes, bytearray)) else b"".join(chunk)
    return out


def _done_events(blob) -> list[dict]:
    return [e for e in _events(blob) if e["type"] == "response.output_item.done"]


# -- the bug: duplicate output_item.done after an interleave --------------------

def test_responses_no_duplicate_output_item_done_after_text_interleave():
    """A TextDelta while two tools are open closes the open tool; the later
    ToolCallClose for that index must NOT emit output_item.done again."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":'),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":'),
        dl.TextDelta(text="interjected"),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
        dl.StreamEnd(),
    ])
    idxs = [e["output_index"] for e in _done_events(blob)]
    assert len(idxs) == len(set(idxs)), f"duplicate output_item.done: {idxs}"


def test_responses_no_duplicate_output_item_done_after_thinking_interleave():
    """Same invariant for an interrupting thinking delta."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":'),
        dl.ThinkingDelta(text="hmm"),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
        dl.StreamEnd(),
    ])
    idxs = [e["output_index"] for e in _done_events(blob)]
    assert len(idxs) == len(set(idxs)), f"duplicate output_item.done: {idxs}"


def test_responses_stream_end_does_not_reclose_a_closed_tool():
    """StreamEnd flushes the open item; a tool already closed by its own
    ToolCallClose must not be flushed a second time."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":'),
        dl.ToolCallClose(index=0),
        dl.StreamEnd(),
    ])
    idxs = [e["output_index"] for e in _done_events(blob)]
    assert idxs == [0], f"expected exactly one done for index 0, got {idxs}"


# -- AUDIT #2 coverage gap: output_index routing under interleave ---------------

def test_responses_parallel_args_route_to_own_output_index():
    """Interleaved args deltas must each carry the output_index assigned at
    that tool's Open — not the most recently opened one."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":'),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":'),
        dl.ToolCallArgsDelta(index=0, args_fragment='1}'),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
    ])
    deltas = [e for e in _events(blob)
              if e["type"] == "response.function_call_arguments.delta"]
    routed = [(e["output_index"], e["delta"]) for e in deltas]
    assert routed == [(0, '{"a":'), (1, '{"b":'), (0, "1}")], routed


def test_responses_parallel_close_uses_own_output_index():
    """Each ToolCallClose closes at the output_index its Open reserved."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":1}'),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":2}'),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
    ])
    dones = _done_events(blob)
    assert [(e["output_index"], e["item"]["call_id"],
             e["item"]["arguments"]) for e in dones] == [
        (0, "c0", '{"a":1}'), (1, "c1", '{"b":2}')]


def test_responses_parallel_close_reversed_order():
    """Closing order may differ from opening order; indices must still be the
    ones assigned at Open time."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallClose(index=1),
        dl.ToolCallClose(index=0),
    ])
    dones = _done_events(blob)
    assert [(e["output_index"], e["item"]["call_id"]) for e in dones] == [
        (1, "c1"), (0, "c0")]


def test_responses_sibling_open_does_not_close_first_tool():
    """A second ToolCallOpen must not emit output_item.done for the first tool
    (parallel calls are siblings, not sequential)."""
    enc = ResponsesStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
    ])
    assert _done_events(blob) == [], "sibling Open prematurely closed a tool"


# -- AUDIT #3 coverage gap: Anthropic per-tool block routing --------------------

def test_anthropic_parallel_args_route_to_own_block_index():
    """On the Anthropic surface each tool's args land on the content block
    created by its own content_block_start."""
    enc = am.AnthropicStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":'),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":'),
        dl.ToolCallArgsDelta(index=0, args_fragment='1}'),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish(stop_reason="tool_call"),
        dl.StreamEnd(),
    ])
    starts = {}
    deltas = []
    for e in _events(blob):
        if e["type"] == "content_block_start" and e["content_block"]["type"] == "tool_use":
            starts[e["content_block"]["id"]] = e["index"]
        elif (e["type"] == "content_block_delta"
              and e["delta"]["type"] == "input_json_delta"):
            deltas.append((e["index"], e["delta"]["partial_json"]))
    assert starts == {"c0": 0, "c1": 1}, starts
    assert deltas == [(0, '{"a":'), (1, '{"b":'), (0, "1}")], deltas


def test_anthropic_args_delta_with_no_open_tool_is_dropped_not_crashed():
    """An ArgsDelta before any Open must be dropped (never stamped onto a
    text/thinking block) instead of raising IndexError."""
    enc = am.AnthropicStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.TextDelta(text="hello"),
        dl.ToolCallArgsDelta(index=3, args_fragment='{"oops":'),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish(stop_reason="stop"),
        dl.StreamEnd(),
    ])
    leaked = [e for e in _events(blob)
              if e["type"] == "content_block_delta"
              and e["delta"]["type"] == "input_json_delta"]
    assert leaked == [], f"orphan args leaked into the stream: {leaked}"


def test_anthropic_parallel_blocks_close_exactly_once():
    """With two tools open and an interrupting TextDelta, each content block
    gets exactly one content_block_stop."""
    enc = am.AnthropicStreamEncoder("m", "r1")
    blob = _feed(enc, [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":'),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":'),
        dl.TextDelta(text="interjected"),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish(stop_reason="tool_call"),
        dl.StreamEnd(),
    ])
    stops = [e["index"] for e in _events(blob) if e["type"] == "content_block_stop"]
    assert sorted(stops) == [0, 1], f"blocks not closed exactly once: {stops}"


# -- AUDIT #1 pin: encode failure must not deadlock the stream pump -------------

@pytest.mark.asyncio
async def test_stream_pump_survives_encode_failure():
    """If encode_request raises, _pump_once must record the error and set
    ready — otherwise the caller blocks forever on ready.wait()."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0),
    )
    router = Router(cfg)
    gw = Gateway(router, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=ir.Request(model="gpt-4o", messages=[]))
    dep = router.groups["gpt-4o"][0]
    key = dep.provider.get_key("a")

    queue: asyncio.Queue = asyncio.Queue()
    ready = asyncio.Event()
    err_box: list = [None]

    with patch.object(OpenAIAdapter, "encode_request",
                      side_effect=RuntimeError("unsupported tool schema")):
        task = asyncio.create_task(gw._pump(dep, key, ctx, queue, ready, err_box))
        # Without the guard this hangs forever and wait_for raises TimeoutError.
        await asyncio.wait_for(ready.wait(), timeout=2.0)
        await task

    assert err_box[0] is not None, "encode failure never surfaced an error"
    assert err_box[0].status == 400
    assert "failed to encode request" in err_box[0].message


# -- AUDIT #4 pin: streaming path parses Retry-After ----------------------------

@pytest.mark.asyncio
@respx.mock
async def test_stream_error_path_parses_retry_after():
    """A 429 on a streaming request must carry retry_after from the upstream
    Retry-After header (the non-streaming path already does)."""
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0),
    )
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=429, headers={"Retry-After": "12"},
        json={"error": {"message": "slow down"}})

    router = Router(cfg)
    gw = Gateway(router, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=ir.Request(model="gpt-4o", messages=[]))
    dep = router.groups["gpt-4o"][0]
    key = dep.provider.get_key("a")

    queue: asyncio.Queue = asyncio.Queue()
    ready = asyncio.Event()
    err_box: list = [None]
    task = asyncio.create_task(gw._pump(dep, key, ctx, queue, ready, err_box))
    await asyncio.wait_for(ready.wait(), timeout=5.0)
    await task

    assert err_box[0] is not None, "no error recorded for upstream 429"
    assert err_box[0].retry_after == 12, (
        f"streaming path dropped Retry-After: {err_box[0].retry_after}")
