"""Round 78: null-tolerant decoding in the provider adapters (AUDIT #160, #161,
#194, #195, #196, #197, #200, #201).

Every finding here is the same class of mistake in a different field: a
``.get(key, default)`` (or an unguarded sum) that defends against a *missing*
key but not against a JSON ``null`` — or, for #161/#196, a terminal/lifecycle
emission that was never made exactly-once. A ``null`` is not the empty shape
the default describes, so the poisoned value travelled into the IR and raised
somewhere far from its origin: mid-stream after the client already had a 200
(#160, #194, #195), on the *next turn* of the conversation (#197), or in the
wire encoder as an undispatchable ``tool_use`` block (#196).

Pinned here, per finding:

- **#160** — ``AnthropicAdapter``: ``partial_json: null`` decodes to ``""``, so
  the gateway's ``"".join()`` fold no longer raises ``TypeError``.
- **#161** — ``GeminiAdapter``: a parts-less usage-bearing intermediate frame
  followed by a finish frame yields exactly ONE ``UsageFinal``/``Finish``/
  ``StreamEnd``, and the surviving ``UsageFinal`` is the *finish* frame's
  counts, not the intermediate frame's.
- **#194** — ``openai``/``openrouter``/``nim``/``anthropic``: a null usage
  counter reads as ``0`` and the gateway's ``u.prompt + u.output`` /
  ``u.cached > 0`` arithmetic works.
- **#195** — ``GeminiAdapter``: a null ``candidatesTokenCount`` no longer
  raises ``TypeError`` out of either the sync or the stream decoder.
- **#196** — ``NimAdapter``: a second tool call reusing an index emits
  ``ToolCallOpen`` before its ``ToolCallClose``.
- **#197** — ``OpenRouterAdapter``: a non-dict streaming ``usage`` is ignored
  rather than raising ``AttributeError``; a ``reasoning_details`` entry with a
  null ``text``/``summary``/``data`` decodes to ``""`` so the next turn's
  OpenAI-wire replay (``reasoning += p.text``) does not raise.
- **#200** — ``OpenRouterAdapter``: a null mid-stream error message is coerced
  to a real string instead of reaching the client as ``"message": null``.
- **#201** — ``nim_tool_schema``: a tool declaring both ``type`` and
  ``_nim_arg_type`` parameters keeps BOTH, and the alias map still reverses
  every renamed key back to the name the tool declared.
"""

from __future__ import annotations

import json

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
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.nim_adapter import NimAdapter
from wiwi.providers.nim_tool_schema import (
    collect_nim_tool_aliases,
    sanitize_nim_tool_schemas,
    unalias_nim_tool_args,
)
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl


def _kinds(deltas: list[dl.IRStreamDelta]) -> list[str]:
    return [type(d).__name__ for d in deltas]


def _fold(deltas: list[dl.IRStreamDelta]) -> str:
    """The gateway's ``"".join(arg_bufs[index])`` fold over args fragments."""
    return "".join(d.args_fragment for d in deltas
                   if isinstance(d, dl.ToolCallArgsDelta))


# ---------------------------------------------------------------------------
# #160 — AnthropicAdapter: partial_json null
# ---------------------------------------------------------------------------


def test_anthropic_null_partial_json_folds_to_empty_string():
    """A null ``partial_json`` must decode to ``""``, not ``None``.

    ``args_fragment`` is contractually ``str`` and the gateway folds the
    fragments of a tool call with ``"".join()``, which raises ``TypeError`` on
    a ``None`` — mid-stream, after the client already had a 200.
    """
    a = AnthropicAdapter()
    out = a.decode_stream_event("", json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": None}}))
    assert out == [dl.ToolCallArgsDelta(index=0, args_fragment="")]
    assert _fold(out) == ""  # must not raise


def test_anthropic_typed_wrong_partial_json_folds_to_empty_string():
    """Any non-string ``partial_json`` reads as ``""`` (sibling-arm parity)."""
    a = AnthropicAdapter()
    for junk in (None, 5, True, {"a": 1}, ["x"]):
        out = a.decode_stream_event("", json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": junk}}))
        assert _fold(out) == ""


def test_anthropic_real_partial_json_still_forwarded():
    """Control: a genuine string fragment is untouched (still splits JSON)."""
    a = AnthropicAdapter()
    out = a.decode_stream_event("", json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"a":'}}))
    assert _fold(out) == '{"a":'


# ---------------------------------------------------------------------------
# #161 — GeminiAdapter: exactly one terminal tail
# ---------------------------------------------------------------------------


def test_gemini_parts_less_usage_then_finish_emits_one_tail():
    """A parts-less usage-bearing frame followed by a finish frame must yield
    exactly one ``UsageFinal``/``Finish``/``StreamEnd``.

    Gemini 2.5 / Vertex attach ``usageMetadata`` to every chunk, so the
    ``elif u and not parts:`` arm fires on an *intermediate* frame and the
    later finish frame fires the ``if finish:`` arm — two of each. Every
    consumer keeps the last value, so the stream was billed on the
    intermediate frame's counts.
    """
    a = GeminiAdapter()
    out: list[dl.IRStreamDelta] = []
    out += a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 7}}))
    out += a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 9}}))

    kinds = _kinds(out)
    assert kinds.count("UsageFinal") == 1, kinds
    assert kinds.count("Finish") == 1, kinds
    assert kinds.count("StreamEnd") == 1, kinds
    assert kinds.count("StreamStart") == 1, kinds
    # The surviving tail is the FIRST one (the intermediate frame terminated
    # the stream); the finish frame's counts must not be appended to it.
    usage = next(d for d in out if isinstance(d, dl.UsageFinal))
    assert usage.output == 7, usage


def test_gemini_finish_frame_after_terminal_tail_is_empty():
    """The frame after a completed tail contributes nothing further."""
    a = GeminiAdapter()
    a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 7}}))
    tail = a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 9}}))
    assert tail == [], tail


def test_gemini_finish_with_usage_after_content_emits_one_tail():
    """Control: the ordinary shape — content frames, then a finish frame with
    usage — still terminates exactly once."""
    a = GeminiAdapter()
    out: list[dl.IRStreamDelta] = []
    out += a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": [{"text": "hi"}]}}]}))
    out += a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}}))
    kinds = _kinds(out)
    assert kinds.count("UsageFinal") == 1, kinds
    assert kinds.count("StreamEnd") == 1, kinds
    assert kinds[-1] == "StreamEnd", kinds


def test_gemini_reset_clears_tail_flag():
    """``reset()`` must clear ``_saw_tail`` so a reused adapter serves the next
    stream's tail."""
    a = GeminiAdapter()
    a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 7}}))
    a.reset()
    tail = a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2}}))
    assert [d for d in tail if isinstance(d, dl.StreamEnd)], tail


# ---------------------------------------------------------------------------
# #194 — a JSON null usage counter must not reach UsageFinal
# ---------------------------------------------------------------------------


def _openai_wire_null_usage(adapter, frame: dict) -> dl.UsageFinal:
    out = adapter.decode_stream_event("", json.dumps(frame))
    return next(d for d in out if isinstance(d, dl.UsageFinal))


def test_openai_null_usage_counters_read_as_zero():
    u = _openai_wire_null_usage(OpenAIAdapter(), {
        "choices": [],
        "usage": {"prompt_tokens": None, "completion_tokens": None,
                  "prompt_tokens_details": {"cached_tokens": None},
                  "completion_tokens_details": {"reasoning_tokens": None}}})
    assert (u.prompt, u.output, u.cached, u.reasoning) == (0, 0, 0, 0)
    # The two gateway consumers that raised on the poisoned value.
    assert u.prompt + u.output == 0
    assert not u.cached > 0


def test_openrouter_null_usage_counters_read_as_zero():
    u = _openai_wire_null_usage(OpenRouterAdapter(), {
        "choices": [],
        "usage": {"prompt_tokens": None, "completion_tokens": None}})
    assert (u.prompt, u.output) == (0, 0)
    assert u.prompt + u.output == 0


def test_nim_null_usage_counters_read_as_zero():
    u = _openai_wire_null_usage(NimAdapter(), {
        "choices": [],
        "usage": {"prompt_tokens": None, "completion_tokens": None}})
    assert (u.prompt, u.output) == (0, 0)
    assert u.prompt + u.output == 0


def test_anthropic_null_usage_counters_read_as_zero():
    """Both the ``message_start`` counters (carried on the final UsageFinal)
    and ``message_delta``'s ``output_tokens``."""
    a = AnthropicAdapter()
    start = a.decode_stream_event("", json.dumps({
        "type": "message_start",
        "message": {"model": "m", "usage": {
            "input_tokens": None, "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None}}}))
    assert start == [dl.StreamStart(model="m")]

    out = a.decode_stream_event("", json.dumps({
        "type": "message_delta", "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": None,
                  "output_tokens_details": {"thinking_tokens": None}}}))
    u = next(d for d in out if isinstance(d, dl.UsageFinal))
    assert (u.prompt, u.cached, u.cache_creation, u.reasoning, u.output) == \
        (0, 0, 0, 0, 0)
    assert u.prompt + u.output == 0


def test_openai_sync_null_usage_counters_read_as_zero():
    """The non-streaming decode feeds ``ir.Usage``, consumed by the same
    ``prompt_tokens + completion_tokens`` arithmetic."""
    body = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "x"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": None, "completion_tokens": 5}}).encode()
    turn = OpenAIAdapter().decode_response(200, body)
    assert turn.usage.prompt_tokens == 0
    assert turn.usage.prompt_tokens + turn.usage.completion_tokens == 5


def test_anthropic_sync_null_usage_counters_read_as_zero():
    body = json.dumps({
        "content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": None, "output_tokens": 2}}).encode()
    turn = AnthropicAdapter().decode_response(200, body)
    assert turn.usage.prompt_tokens == 0
    assert turn.usage.prompt_tokens + turn.usage.completion_tokens == 2


def test_null_usage_bool_is_not_a_token_count():
    """``isinstance(True, int)`` is True, but ``true`` is not a token count."""
    u = _openai_wire_null_usage(OpenAIAdapter(), {
        "choices": [], "usage": {"prompt_tokens": True, "completion_tokens": 4}})
    assert (u.prompt, u.output) == (0, 4)


def test_real_usage_counters_still_forwarded():
    """Control: genuine counts are untouched by the coercion."""
    u = _openai_wire_null_usage(OpenAIAdapter(), {
        "choices": [],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                  "prompt_tokens_details": {"cached_tokens": 3},
                  "completion_tokens_details": {"reasoning_tokens": 2}}})
    assert (u.prompt, u.output, u.cached, u.reasoning) == (11, 7, 3, 2)


def test_openai_wire_subclasses_inherit_the_null_usage_guard():
    """#194 names cline/workbuddy/bai as inheriting the OpenAI usage read;
    each must coerce through ``super()`` rather than re-read the raw dict."""
    from wiwi.providers.bai_adapter import BAIAdapter
    from wiwi.providers.cline_adapter import ClineAdapter
    from wiwi.providers.workbuddy_adapter import WorkBuddyAdapter

    frame = {"choices": [],
             "usage": {"prompt_tokens": None, "completion_tokens": None}}
    for cls in (ClineAdapter, WorkBuddyAdapter, BAIAdapter):
        u = _openai_wire_null_usage(cls(), frame)
        assert (u.prompt, u.output) == (0, 0), cls.__name__
        assert u.prompt + u.output == 0, cls.__name__


def _usage_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round78.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )


def _chunk(payload: dict) -> bytes:
    return b"data: " + orjson.dumps(payload) + b"\n\n"


@respx.mock
async def test_gateway_prices_a_null_usage_stream_without_failing_it():
    """End-to-end: the poisoned ``UsageFinal`` used to blow up inside
    ``_price_stream`` (``u.prompt + u.output``), which the pump's generic
    handler converted into a mid-stream ``StreamError`` — the client's answer
    was cut off and a healthy deployment was cooled, for a frame carrying a
    complete, valid answer."""
    body = (
        _chunk({"id": "c1", "choices": [{"index": 0, "delta": {"content": "hi"},
                                         "finish_reason": None}]})
        + _chunk({"id": "c1",
                  "choices": [{"index": 0, "delta": {},
                               "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": None, "completion_tokens": None}})
        + b"data: [DONE]\n\n"
    )
    respx.post("https://round78.example/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=body))
    gw = Gateway(Router(_usage_config()), CostEngine())
    try:
        ctx = RequestContext(
            surface="chat", group="gpt-x",
            ir_req=ir.Request(model="gpt-x", stream=True, messages=[
                ir.Message(role="user", parts=[ir.TextPart("hi")])]))
        out = []
        async for d in gw.stream(ctx):
            out.append(d)
        kinds = _kinds(out)
        assert not any(isinstance(d, dl.StreamError) for d in out), kinds
        assert "StreamEnd" in kinds, kinds
        assert [d.text for d in out if isinstance(d, dl.TextDelta)] == ["hi"]
    finally:
        await gw.aclose()


# ---------------------------------------------------------------------------
# #195 — Gemini: a null counter raised out of the decoder itself
# ---------------------------------------------------------------------------


def test_gemini_stream_null_candidates_count_does_not_raise():
    """``candidatesTokenCount + thoughtsTokenCount`` was unguarded, unlike its
    sibling reads, so a null raised ``TypeError`` out of the stream decoder."""
    a = GeminiAdapter()
    out = a.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": None,
                          "thoughtsTokenCount": 3}}))
    u = next(d for d in out if isinstance(d, dl.UsageFinal))
    assert (u.prompt, u.output, u.reasoning) == (1, 3, 3)


def test_gemini_sync_null_candidates_count_does_not_raise():
    body = json.dumps({
        "candidates": [{"content": {"parts": [{"text": "hi"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": None,
                          "thoughtsTokenCount": 3}}).encode()
    turn = GeminiAdapter().decode_response(200, body)
    assert turn.usage.completion_tokens == 3
    assert turn.usage.prompt_tokens == 1


def test_gemini_null_thoughts_count_does_not_raise():
    body = json.dumps({
        "candidates": [{"content": {"parts": [{"text": "hi"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"candidatesTokenCount": 4,
                          "thoughtsTokenCount": None}}).encode()
    turn = GeminiAdapter().decode_response(200, body)
    assert turn.usage.completion_tokens == 4
    assert turn.usage.reasoning_tokens == 0


# ---------------------------------------------------------------------------
# #196 — NimAdapter: a reused index must open before it closes
# ---------------------------------------------------------------------------


def _tool_chunk(index: int, call_id: str, name: str,
                args: str | None = None) -> str:
    fn: dict[str, object] = {"name": name}
    if args is not None:
        fn["arguments"] = args
    return json.dumps({"choices": [{"delta": {"tool_calls": [
        {"index": index, "id": call_id, "function": fn}]}}]})


def test_nim_reused_index_opens_before_it_closes():
    """The superseded call's Open may still be deferred; closing without
    flushing it left the encoder an undispatchable ``tool_use`` block whose
    name is ``""``."""
    a = NimAdapter()
    out = a.decode_stream_event("", _tool_chunk(0, "call_1", "foo"))
    out += a.decode_stream_event("", _tool_chunk(0, "call_2", "bar",
                                                 '{"a":1}'))
    kinds = _kinds(out)
    assert kinds == ["ToolCallOpen", "ToolCallClose", "ToolCallOpen",
                     "ToolCallArgsDelta"], kinds
    assert kinds.index("ToolCallOpen") < kinds.index("ToolCallClose")
    opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
    assert [(d.id, d.name) for d in opens] == [("call_1", "foo"),
                                               ("call_2", "bar")]


def test_nim_reused_index_encodes_a_dispatchable_tool_use():
    """Rendered through the real Anthropic encoder, the superseded call must
    produce a named ``content_block_start``, not one with ``name: ""``."""
    from wiwi.wire.anthropic_messages import AnthropicStreamEncoder

    a = NimAdapter()
    enc = AnthropicStreamEncoder("m", "req")
    frames: list[bytes] = []
    for data in (_tool_chunk(0, "call_1", "foo"),
                 _tool_chunk(0, "call_2", "bar", '{"a":1}')):
        for d in a.decode_stream_event("", data):
            frame = enc.feed(d)
            if frame:
                frames.append(frame)
    body = b"".join(frames).decode()
    starts = [json.loads(line[len("data: "):])["content_block"]
              for line in body.splitlines()
              if line.startswith("data: ") and '"content_block_start"' in line]
    assert [b.get("name") for b in starts] == ["foo", "bar"], starts


def test_nim_reused_index_flushes_buffered_args_before_close():
    """Control: an *aliased* superseded call drains its buffered args before
    its Close, under the superseded call's own name."""
    a = NimAdapter()
    tools = [{"type": "function", "function": {
        "name": "create",
        "parameters": {"type": "object",
                       "properties": {"type": {"type": "string"}}}}}]
    a.set_tool_context({"tools": sanitize_nim_tool_schemas(tools)})
    out = a.decode_stream_event("", json.dumps({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_1",
         "function": {"name": "create", "arguments": '{"_nim_arg_type":"a"}'}}]}}]}))
    out += a.decode_stream_event("", _tool_chunk(0, "call_2", "other", "{}"))
    kinds = _kinds(out)
    # Open, then the un-aliased args of call_1, then its Close.
    assert kinds[:3] == ["ToolCallOpen", "ToolCallArgsDelta",
                         "ToolCallClose"], kinds
    assert json.loads(_fold(out[:3])) == {"type": "a"}


# ---------------------------------------------------------------------------
# #197 — OpenRouter: non-dict usage, and null reasoning text
# ---------------------------------------------------------------------------


def test_openrouter_non_dict_stream_usage_is_ignored():
    """``if u:`` let a truthy non-dict reach ``u.get`` and raise
    ``AttributeError`` out of the decoder; its siblings gate on ``dict``."""
    a = OpenRouterAdapter()
    for junk in ("bogus", 5, [1, 2], True):
        out = a.decode_stream_event("", json.dumps(
            {"choices": [], "usage": junk}))
        assert out == [], out


def test_openrouter_null_reasoning_text_does_not_poison_history():
    """A null ``text`` decoded to ``ThinkingPart(text=None)``, and the next
    turn's OpenAI-wire replay (``reasoning += p.text``) raised."""
    turn = OpenRouterAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {
            "role": "assistant", "content": "x",
            "reasoning_details": [
                {"type": "reasoning.text", "text": None},
                {"type": "reasoning.summary", "summary": None},
                {"type": "reasoning.encrypted", "data": None},
            ]}, "finish_reason": "stop"}],
        "usage": {}}).encode())
    assert [p.text for p in turn.thinking] == ["", "", ""]
    # The real consumer: encoding this history for the next turn must not raise.
    req = ir.Request(model="m", messages=[
        ir.Message(role="assistant",
                   parts=[ir.TextPart("x")] + list(turn.thinking))])
    body = OpenAIAdapter().encode_request(req, "m", {})
    assert body["messages"][0]["content"] == "x"


def test_openrouter_real_reasoning_text_still_decoded():
    """Control: genuine reasoning text round-trips unchanged."""
    turn = OpenRouterAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {
            "role": "assistant", "content": "x",
            "reasoning_details": [
                {"type": "reasoning.text", "text": "why",
                 "signature": "sig"},
                {"type": "reasoning.summary", "summary": "sum"},
                {"type": "reasoning.encrypted", "data": "blob", "id": "rs_1"},
            ]}, "finish_reason": "stop"}],
        "usage": {}}).encode())
    assert [(p.text, p.signature) for p in turn.thinking] == [
        ("why", "sig"), ("sum", None), ("blob", "rs_1")]


def test_openrouter_stream_null_reasoning_text_is_dropped():
    """The streaming arm gates on truthiness, so a null text yields no delta
    rather than an empty ThinkingDelta."""
    out = OpenRouterAdapter().decode_stream_event("", json.dumps({
        "choices": [{"delta": {"reasoning_details": [
            {"type": "reasoning.text", "text": None},
            {"type": "reasoning.summary", "summary": None},
            {"type": "reasoning.encrypted", "data": None}]}}]}))
    assert out == [], out


# ---------------------------------------------------------------------------
# #200 — OpenRouter: a null mid-stream error message
# ---------------------------------------------------------------------------


def test_openrouter_null_stream_error_message_is_coerced():
    out = OpenRouterAdapter().decode_stream_event("", json.dumps(
        {"error": {"message": None}, "choices": []}))
    err = next(d for d in out if isinstance(d, dl.StreamError))
    assert err.message == "OpenRouter stream error"


def test_openrouter_real_stream_error_message_is_kept():
    out = OpenRouterAdapter().decode_stream_event("", json.dumps(
        {"error": {"message": "rate limited"}, "choices": []}))
    err = next(d for d in out if isinstance(d, dl.StreamError))
    assert err.message == "rate limited"


# ---------------------------------------------------------------------------
# #201 — nim_tool_schema: an alias collision destroyed a real property
# ---------------------------------------------------------------------------


def test_nim_alias_collision_keeps_both_properties():
    """A tool declaring both a ``type`` parameter and a literal
    ``_nim_arg_type`` parameter must be told to the model as TWO parameters,
    not one."""
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {
            "type": "object",
            "properties": {"type": {"type": "string"},
                           "_nim_arg_type": {"type": "integer"}},
            "required": ["type", "_nim_arg_type"],
        }}}]
    params = sanitize_nim_tool_schemas(tools)[0]["function"]["parameters"]
    assert len(params["properties"]) == 2, params["properties"]
    assert params["required"] == list(params["properties"]), params["required"]
    assert len(set(params["required"])) == 2, "required named a parameter twice"


def test_nim_alias_collision_map_is_invertible():
    """Every renamed key must reverse to the name the tool declared — the
    adapter un-aliases the model's arguments with this map."""
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {
            "type": "object",
            "properties": {"type": {"type": "string"},
                           "_nim_arg_type": {"type": "integer"}},
            "required": ["type", "_nim_arg_type"],
        }}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    aliases = collect_nim_tool_aliases(sanitized)["t"]
    props = sanitized[0]["function"]["parameters"]["properties"]
    assert set(aliases) == set(props), (aliases, props)
    # The model answers with the sanitized names; the un-alias pass must
    # restore exactly the two names the tool declared.
    args = {"_nim_arg_type": "s", "_nim_arg__nim_arg_type": 5}
    assert unalias_nim_tool_args(args, aliases) == {"type": "s",
                                                    "_nim_arg_type": 5}


def test_nim_alias_collision_resolves_to_a_fixed_point():
    """Displacing a literal alias one prefix further can collide again
    (``type`` + ``_nim_arg_type`` + ``_nim_arg__nim_arg_type``); the
    resolution must propagate until no property is shadowed, and every alias
    must stay ``_nim_arg_`` + its original so the reverse map stays exact."""
    declared = {"type": {"type": "string"},
                "_nim_arg_type": {"type": "integer"},
                "_nim_arg__nim_arg_type": {"type": "boolean"}}
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {"type": "object", "properties": declared,
                       "required": list(declared)}}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert len(params["properties"]) == len(declared), params["properties"]
    assert len(set(params["required"])) == len(declared), params["required"]
    aliases = collect_nim_tool_aliases(sanitized)["t"]
    assert all(k == f"_nim_arg_{v}" for k, v in aliases.items()), aliases
    args = {k: i for i, k in enumerate(params["properties"])}
    assert set(unalias_nim_tool_args(args, aliases)) == set(declared)


def test_nim_alias_collision_resolves_in_nested_schemas():
    """The same collision inside a nested object's ``properties`` must not
    shadow a nested property (the rename map is per ``properties`` node)."""
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string"},
            "inner": {"type": "object",
                      "properties": {"type": {"type": "integer"},
                                     "_nim_arg_type": {"type": "boolean"}},
                      "required": ["type", "_nim_arg_type"]}}}}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    inner = sanitized[0]["function"]["parameters"]["properties"]["inner"]
    assert len(inner["properties"]) == 2, inner["properties"]
    assert len(set(inner["required"])) == 2, inner["required"]
    aliases = collect_nim_tool_aliases(sanitized)["t"]
    assert all(k == f"_nim_arg_{v}" for k, v in aliases.items()), aliases


def test_nim_plain_unsafe_param_alias_unchanged():
    """Control: the ordinary single-``type`` case keeps its historical alias."""
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {"type": "object",
                       "properties": {"type": {"type": "string"},
                                      "name": {"type": "string"}},
                       "required": ["type"]}}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert set(params["properties"]) == {"_nim_arg_type", "name"}
    assert params["required"] == ["_nim_arg_type"]
    assert collect_nim_tool_aliases(sanitized)["t"] == {"_nim_arg_type": "type"}


def test_nim_no_unsafe_param_is_untouched():
    """Control: a schema with no unsafe name is returned unchanged."""
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {"type": "object",
                       "properties": {"a": {"type": "string"},
                                      "b": {"type": "integer"}},
                       "required": ["a"]}}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert set(params["properties"]) == {"a", "b"}
    assert params["required"] == ["a"]
    assert "t" not in collect_nim_tool_aliases(sanitized)
    """Control: the ordinary single-``type`` case keeps its historical alias."""
    tools = [{"type": "function", "function": {
        "name": "t",
        "parameters": {"type": "object",
                       "properties": {"type": {"type": "string"},
                                      "name": {"type": "string"}},
                       "required": ["type"]}}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert set(params["properties"]) == {"_nim_arg_type", "name"}
    assert params["required"] == ["_nim_arg_type"]
    assert collect_nim_tool_aliases(sanitized)["t"] == {"_nim_arg_type": "type"}


def test_nim_literal_alias_named_param_unchanged():
    """Control: a tool that only *declares* ``_nim_arg_type`` (no ``type``
    param) keeps its name — it is not displaced."""
    tools = [{"type": "function", "function": {
        "name": "create",
        "parameters": {"type": "object",
                       "properties": {"_nim_arg_type": {"type": "string"},
                                      "name": {"type": "string"}}}}}]
    sanitized = sanitize_nim_tool_schemas(tools)
    params = sanitized[0]["function"]["parameters"]
    assert set(params["properties"]) == {"_nim_arg_type", "name"}
    assert collect_nim_tool_aliases(sanitized)["create"] == {
        "_nim_arg_type": "type"}
