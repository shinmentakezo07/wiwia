"""Round-15 regression tests: tool/assistant id coercion + interleaved
text/thinking-during-tool legality + [DONE]-terminated stream synthesis.

Regression targets (see AUDIT / review of the round-14..15 fixes):

- ``AnthropicStreamEncoder`` emitted an ``input_json_delta`` at a block opened
  as ``text``/``thinking`` when a text/thinking delta interrupted an open tool
  call. Anthropic content blocks are strictly sequential, so the interleaved
  delta must be suppressed and tool-args deltas must keep routing to the
  ``tool_use`` block. Fix: ``TextDelta``/``ThinkingDelta`` while
  ``_open_block == "tool"`` are dropped; ``ToolCallArgsDelta`` looks up the
  block index from ``_tool_blocks`` and never falls back to ``_block_idx - 1``.
- Non-string tool/assistant ids (int from a provider) were emitted verbatim into
  Anthropic ``content_block_start`` (``"id":12345``) and OpenAI-style
  ``tool_calls[].id``, which Claude Code rejects ("string id" error). Fix:
  coerce to ``str`` at the IR boundary (``ToolUsePart``/``ToolResultPart``
  ``__post_init__``) AND at the SSE emit point (``tool_id``).
- An OpenAI-compatible upstream that closes a stream purely with ``[DONE]``
  (DeepSeek/B.A.I) and omits the trailing ``finish_reason``/usage chunk left
  ``finish is None and usage_final is None`` in the gateway pump, so wiwi
  emitted ``StreamError("upstream stream ended without completion")`` and the
  client's OpenAI SDK saw the chat stream close without a ``finish_reason``.
  Fix: track ``saw_terminal``; a ``[DONE]``-terminated stream synthesizes
  ``Finish("stop")``.
"""

from __future__ import annotations

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
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# ---------------------------------------------------------------------------
# IR boundary id coercion
# ---------------------------------------------------------------------------

def test_ir_tool_use_part_coerces_non_string_id():
    """ToolUsePart.id must be a str even when a provider hands back an int."""
    p = ir.ToolUsePart(id=12345, name="f")
    assert p.id == "12345"
    assert isinstance(p.id, str)


def test_ir_tool_result_part_coerces_non_string_id():
    """ToolResultPart.tool_use_id must be a str even for an int input."""
    p = ir.ToolResultPart(tool_use_id=100, content="ok")
    assert p.tool_use_id == "100"
    assert isinstance(p.tool_use_id, str)


def test_anthropic_encoder_coerces_non_string_tool_id():
    """SSE content_block_start must carry a string tool_use id."""
    enc = am.AnthropicStreamEncoder("m", "r1")
    out = b""
    for d in [dl.StreamStart("m"), dl.ToolCallOpen(0, 12345, "f"),
              dl.ToolCallArgsDelta(0, '{"a":'), dl.ToolCallClose(0),
              dl.UsageFinal(prompt=1, output=1), dl.Finish("tool_call"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            out += chunk if isinstance(chunk, (bytes, bytearray)) else b"".join(chunk)
    out += enc.final_frame()
    blob = out.decode()
    # The tool_use content_block_start must emit "id":"12345" (a string).
    assert '"id":"12345"' in blob
    assert '"id":12345' not in blob


def test_openai_chat_encoder_coerces_non_string_tool_id():
    """OpenAI-dialect tool_calls[].id must be a string."""
    enc = oc.ChatStreamEncoder("m", "r1")
    frames = []
    for d in [dl.StreamStart("m"), dl.ToolCallOpen(0, 99, "f")]:
        chunk = enc.feed(d)
        if chunk:
            frame = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else b"".join(chunk).decode()
            frames.append(frame)
    # The tool_calls[].id lives in the ToolCallOpen feed frame.
    open_frame = next(f for f in frames if '"tool_calls"' in f)
    assert '"id":"99"' in open_frame
    assert '"id":99' not in open_frame


# ---------------------------------------------------------------------------
# Interleaved text/thinking-during-tool legality
# ---------------------------------------------------------------------------

def _render_anthropic(seq, enc=None):
    enc = enc or am.AnthropicStreamEncoder("m", "r1")
    out = b""
    for d in seq:
        chunk = enc.feed(d)
        if chunk:
            out += chunk if isinstance(chunk, (bytes, bytearray)) else b"".join(chunk)
    out += enc.final_frame()
    return out.decode()


def _input_json_fragments(blob):
    """Return the partial_json fragments from every input_json_delta."""
    frags = []
    for block in blob.split("\n\n"):
        if "content_block_delta" not in block or "input_json_delta" not in block:
            continue
        payload = orjson.loads(block.split("data: ", 1)[1])
        frags.append(payload["delta"]["partial_json"])
    return frags


def _block_types(blob):
    start = {}
    for block in blob.split("\n\n"):
        if "content_block_start" not in block:
            continue
        payload = orjson.loads(block.split("data: ", 1)[1])
        start[payload["index"]] = payload["content_block"]["type"]
    return start


def _assert_deltas_legal(blob):
    """Every content_block_delta must target the index of a matching block type."""
    types = _block_types(blob)
    for block in blob.split("\n\n"):
        if "content_block_delta" not in block:
            continue
        payload = orjson.loads(block.split("data: ", 1)[1])
        idx = payload["index"]
        dtype = payload["delta"]["type"]
        if dtype == "input_json_delta":
            assert types.get(idx) == "tool_use", \
                f"input_json_delta on non-tool block {idx} ({types.get(idx)})"
        elif dtype == "text_delta":
            assert types.get(idx) == "text", \
                f"text_delta on non-text block {idx} ({types.get(idx)})"
        elif dtype == "thinking_delta":
            assert types.get(idx) == "thinking", \
                f"thinking_delta on non-thinking block {idx} ({types.get(idx)})"


def test_anthropic_interleaved_text_tool_keeps_input_json_on_tool():
    blob = _render_anthropic([
        dl.StreamStart("m"), dl.ToolCallOpen(0, "c", "get_weather"),
        dl.ToolCallArgsDelta(0, '{"city":"'), dl.TextDelta("wait"),
        dl.ToolCallArgsDelta(0, 'SZ"}'), dl.ToolCallClose(0),
        dl.Finish("tool_call"), dl.StreamEnd()])
    _assert_deltas_legal(blob)
    # The tool's args fragments must be fully present on the tool_use block
    # (interleaved text did not clobber the fragment stream).
    assert "".join(_input_json_fragments(blob)) == '{"city":"SZ"}'


def test_anthropic_interleaved_thinking_tool_keeps_input_json_on_tool():
    blob = _render_anthropic([
        dl.StreamStart("m"), dl.ToolCallOpen(0, "c", "get_weather"),
        dl.ToolCallArgsDelta(0, '{"city":"'), dl.ThinkingDelta("hmm"),
        dl.ToolCallArgsDelta(0, 'SZ"}'), dl.ToolCallClose(0),
        dl.Finish("tool_call"), dl.StreamEnd()])
    _assert_deltas_legal(blob)
    assert "".join(_input_json_fragments(blob)) == '{"city":"SZ"}'


def test_anthropic_parallel_toolargs_legal():
    """Parallel tool calls (Open(0), Open(1), then per-index args) stay legal."""
    blob = _render_anthropic([
        dl.StreamStart("m"), dl.ToolCallOpen(0, "c0", "f0"),
        dl.ToolCallOpen(1, "c1", "f1"), dl.ToolCallArgsDelta(0, '{"a":'),
        dl.TextDelta("x"), dl.ToolCallArgsDelta(1, '{"b":'),
        dl.ToolCallClose(0), dl.ToolCallClose(1),
        dl.Finish("tool_call"), dl.StreamEnd()])
    _assert_deltas_legal(blob)


# ---------------------------------------------------------------------------
# [DONE]-terminated stream with no finish/usage (DeepSeek/B.A.I)
# ---------------------------------------------------------------------------

def test_openai_decode_done_no_finish_terminates():
    """A [DONE] chunk yields StreamEnd; the encoder + final_frame must emit a
    finish_reason even when upstream never sent one (DeepSeek quirk)."""
    ad = OpenAIAdapter()
    ad.reset()
    enc = oc.ChatStreamEncoder("m", "r1")
    deltas = ad.decode_stream_event("", "[DONE]")
    assert any(isinstance(d, dl.StreamEnd) for d in deltas)
    blob = b""
    for d in deltas:
        chunk = enc.feed(d)
        if chunk:
            blob += chunk if isinstance(chunk, (bytes, bytearray)) else b"".join(chunk)
    # final_frame() (called by the gateway after StreamEnd) always emits a
    # finish_reason. This is the client-facing contract that was broken.
    final = enc.final_frame(stop="stop").decode()
    assert '"finish_reason":"stop"' in final


def test_openai_decode_stream_event_produces_finish_when_provider_sends_it():
    """A normal upstream finish_reason still maps to a Finish delta."""
    ad = OpenAIAdapter()
    ad.reset()
    deltas = ad.decode_stream_event("", orjson.dumps({
        "choices": [{"delta": {"content": "hi"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                  "prompt_tokens_details": {"cached_tokens": 0},
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }).decode())
    assert any(isinstance(d, dl.Finish) for d in deltas)
    assert any(isinstance(d, dl.TextDelta) for d in deltas)
    assert any(isinstance(d, dl.UsageFinal) for d in deltas)


def test_openai_decode_stream_event_ignores_empty_finish_reason():
    """Providing finish_reason: null (common on DeepSeek) must not emit Finish,
    but also must not crash — a later [DONE] handles termination."""
    ad = OpenAIAdapter()
    ad.reset()
    deltas = ad.decode_stream_event("", orjson.dumps({
        "choices": [{"delta": {"content": "hi"}, "finish_reason": None}],
    }).decode())
    assert not any(isinstance(d, dl.Finish) for d in deltas)
    assert any(isinstance(d, dl.TextDelta) for d in deltas)


# ---------------------------------------------------------------------------
# Gateway-level: [DONE]-terminated stream is a CLEAN completion (DeepSeek)
# ---------------------------------------------------------------------------

def _gw_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round15.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1"),
                                     KeyDef(label="k2", key="sk-2")])],
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


def _sse(*data: str) -> bytes:
    return b"".join(f"data: {d}\n\n".encode() for d in data)


def _text_chunk(text: str) -> str:
    return ('{"id":"c1","object":"chat.completion.chunk","model":"gpt-x",'
            f'"choices":[{{"index":0,"delta":{{"content":"{text}"}},'
            '"finish_reason":null}]}')


@respx.mock
async def test_done_terminated_no_finish_is_clean_completion():
    """DeepSeek/B.A.I close the stream with [DONE] and no finish_reason/usage.
    This is a clean completion — wiwi must synthesize Finish('stop') and keep the
    key active (NOT cooling), so the client's OpenAI SDK sees a finish_reason."""
    gw = Gateway(Router(_gw_config()), CostEngine())
    try:
        respx.post("https://round15.example/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, content=_sse(_text_chunk("hello"), "[DONE]")))
        ctx = RequestContext(surface="chat", ir_req=_gw_req(), group="gpt-x")
        out = []
        async for d in gw.stream(ctx):
            out.append(d)
        # No StreamError: the stream completed cleanly.
        assert not any(isinstance(d, dl.StreamError) for d in out)
        # A synthesized Finish("stop") must be present, then a StreamEnd.
        assert any(isinstance(d, dl.Finish) and d.stop_reason == "stop"
                   for d in out)
        assert any(isinstance(d, dl.StreamEnd) for d in out)
        # Key stays healthy — this was NOT a mid-stream death.
        acct = gw.router.providers["p1"]
        key = next(k for k in acct.keys if k.label == ctx.provider_key.label)
        assert key.status == "active"
    finally:
        await gw.aclose()
