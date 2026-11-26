"""Round 213 — ten wire/IR defects found by auditing ``wiwi/ir/`` and ``wiwi/wire/``.

Every test reproduces one defect through the real codec or encoder and pins
the corrected behaviour:

* ``document`` blocks referencing a Files-API id were dropped on the Messages
  surface (the image arm decoded them, the document arm did not).
* Two consecutive signed thinking blocks fused into one, the first block's
  signature overwritten by the second's — in the stream encoder, the resume
  tape and the force-stream fold.
* A ``redacted_thinking`` delta closed an open ``tool_use`` block, so the
  call's later argument fragments were dropped.
* Streamed logprobs on the Responses surface kept only the last token's frame.
* A reasoning-only sync turn on the Responses surface carried an empty
  message item the stream path never emits.
* ``stream`` / ``stream_options.include_usage`` read the string ``"false"`` as
  true on the Chat and Completions surfaces.
* The legacy ``functions``/``function_call`` keys stayed in ``extras`` after
  being decoded, so they were forwarded upstream beside the modern shape.
* A Chat-inbound ``refusal`` decoded to nothing.
* ``Tool.strict`` accepted any truthy value and re-emitted it verbatim.
* An unknown usage convention (``cached_in_prompt=None``) was re-based as if
  the prompt excluded the cached count.
* ``thinking_budget=0`` reached ``effective_thinking_budget`` as 8000.
"""

import json

import orjson

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.streaming import deltas as dl
from wiwi.streaming.resume import StreamTape
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as ocmpl
from wiwi.wire import openai_responses as orp

# -- helpers -----------------------------------------------------------------

_BASE = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def _anthropic_events(deltas):
    """Feed IR deltas through the Anthropic stream encoder, final frame included."""
    enc = am.AnthropicStreamEncoder("m", "r")
    out = []
    for d in deltas:
        out += _events(enc.feed(d))
    out += _events(enc.final_frame())
    return out


def _events(raw):
    if not raw:
        return []
    out = []
    for chunk in raw.split(b"\n\n"):
        if chunk.startswith(b"event: "):
            _event, _, data = chunk.partition(b"\n")
            out.append(orjson.loads(data[6:].decode()))
    return out


def _blocks(events):
    """{block index: [kind, text, signature]} rebuilt from the client's view."""
    blocks = {}
    for f in events:
        idx = f.get("index")
        if f.get("type") == "content_block_start":
            cb = f["content_block"]
            blocks[idx] = [cb["type"], cb.get("text") or cb.get("thinking")
                           or cb.get("data") or "", None]
        elif f.get("type") == "content_block_delta":
            d = f["delta"]
            if d["type"] in ("thinking_delta", "text_delta", "input_json_delta"):
                blocks[idx][1] += (d.get("thinking") or d.get("text")
                                   or d.get("partial_json") or "")
            elif d["type"] == "signature_delta":
                assert blocks[idx][2] is None, (
                    f"two signatures on block {idx}: {blocks[idx][2]!r} then "
                    f"{d['signature']!r}")
                blocks[idx][2] = d["signature"]
    return blocks


def _chat_chunks(deltas, **kw):
    enc = oc.ChatStreamEncoder("m", "r", **kw)
    chunks = []
    for d in deltas:
        chunks += _data_frames(enc.feed(d))
    chunks += _data_frames(enc.final_frame())
    return chunks


def _data_frames(raw):
    if not raw:
        return []
    return [orjson.loads(chunk[6:].decode())
            for chunk in raw.split(b"\n\n") if chunk.startswith(b"data: ")]


def _responses_events(deltas):
    enc = orp.ResponsesStreamEncoder("m", "r")
    out = []
    for d in deltas:
        out += _data_frames(enc.feed(d))
    out += _data_frames(enc._completed())
    return out


# -- 1. a Files-API document reference survives decode -----------------------


def test_anthropic_document_file_id_decodes_to_a_document_part():
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "summarize"},
            {"type": "document",
             "source": {"type": "file", "file_id": "file-abc"},
             "title": "spec.pdf", "cache_control": {"type": "ephemeral"}}]}]})
    parts = req.messages[0].parts
    assert [type(p).__name__ for p in parts] == ["TextPart", "DocumentPart"]
    doc = parts[1]
    assert (doc.file_id, doc.name, doc.context) == ("file-abc", "spec.pdf", None)
    assert doc.cache_control == {"type": "ephemeral"}


def test_anthropic_document_file_id_reaches_the_upstream_body():
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "file", "file_id": "file-abc"},
             "title": "spec.pdf"}]}]})
    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    assert body["messages"][0]["content"] == [
        {"type": "document", "source": {"type": "file", "file_id": "file-abc"},
         "title": "spec.pdf"}]


def test_anthropic_document_file_source_without_an_id_is_dropped():
    """A file source naming no id is not a document, same rule as the image arm."""
    for source in ({"type": "file"}, {"type": "file", "file_id": None},
                   {"type": "file", "file_id": ""}, {"type": "file", "file_id": 7}):
        req = am.decode_request({
            "model": "claude-x", "max_tokens": 10,
            "messages": [{"role": "user", "content": [
                {"type": "document", "source": source},
                {"type": "text", "text": "describe"}]}]})
        assert [p.text for p in req.messages[0].parts] == ["describe"], source


# -- 2. consecutive signed thinking blocks stay separate ----------------------


_SIGNED_PAIR = [
    dl.StreamStart(model="m"),
    dl.ThinkingDelta("th"), dl.ThinkingDelta("", signature="SIG1"),
    dl.ThinkingDelta("th2"), dl.ThinkingDelta("", signature="SIG2"),
    dl.TextDelta("answer"),
    dl.UsageFinal(prompt=1, output=2), dl.Finish("stop"), dl.StreamEnd(),
]


def test_two_signed_thinking_blocks_stay_two_blocks():
    blocks = _blocks(_anthropic_events(_SIGNED_PAIR))
    assert blocks == {0: ["thinking", "th", "SIG1"],
                      1: ["thinking", "th2", "SIG2"],
                      2: ["text", "answer", None]}


def test_a_signature_ends_a_thinking_run():
    """Thinking text after a signed block opens a NEW block; text that merely
    continues a block (no signature seen yet) still coalesces."""
    events = _anthropic_events([
        dl.StreamStart(model="m"),
        dl.ThinkingDelta("al"), dl.ThinkingDelta("pha"), dl.ThinkingDelta("", signature="S"),
        dl.ThinkingDelta("beta"),
        dl.Finish("stop"), dl.StreamEnd()])
    assert {i: b[:3] for i, b in _blocks(events).items()} == {
        0: ["thinking", "alpha", "S"], 1: ["thinking", "beta", None]}


def test_consecutive_signed_thinking_blocks_from_a_real_anthropic_stream():
    """Same shape through the real adapter: two upstream blocks, two output
    blocks, each keeping its own signature."""
    a = AnthropicAdapter()

    def feed(event, payload):
        return a.decode_stream_event(event, json.dumps({"type": event, **payload}))

    deltas = feed("message_start", {"message": {"model": "claude", "content": [],
                                                "usage": {"input_tokens": 3, "output_tokens": 1}}})
    deltas += feed("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": ""}})
    deltas += feed("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "th"}})
    deltas += feed("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "SIG1"}})
    deltas += feed("content_block_stop", {"index": 0})
    deltas += feed("content_block_start", {"index": 1, "content_block": {"type": "thinking", "thinking": ""}})
    deltas += feed("content_block_delta", {"index": 1, "delta": {"type": "thinking_delta", "thinking": "th2"}})
    deltas += feed("content_block_delta", {"index": 1, "delta": {"type": "signature_delta", "signature": "SIG2"}})
    deltas += feed("content_block_stop", {"index": 1})
    assert [(type(d).__name__, d.text, d.signature) for d in deltas[1:]] == [
        ("ThinkingDelta", "th", None), ("ThinkingDelta", "", "SIG1"),
        ("ThinkingDelta", "th2", None), ("ThinkingDelta", "", "SIG2")]
    blocks = _blocks(_anthropic_events(deltas))
    assert blocks == {0: ["thinking", "th", "SIG1"], 1: ["thinking", "th2", "SIG2"]}


def test_deferred_then_live_thinking_keep_their_own_signatures():
    """Thinking buffered behind a tool call, then a second signed block once the
    tool has closed: the live block must not adopt the buffered signature."""
    events = _anthropic_events([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(0, "a", "f"),
        dl.ThinkingDelta("th"), dl.ThinkingDelta("", signature="SIG1"),
        dl.ToolCallClose(0),
        dl.ThinkingDelta("th2"), dl.ThinkingDelta("", signature="SIG2"),
        dl.Finish("stop"), dl.StreamEnd()])
    kinds = {i: b[:3] for i, b in _blocks(events).items()}
    assert kinds[0][:2] == ["tool_use", ""]
    assert (kinds[1][1], kinds[1][2]) == ("th", "SIG1"), kinds
    assert (kinds[2][1], kinds[2][2]) == ("th2", "SIG2"), kinds


def test_tape_folds_a_thinking_run_at_each_signature():
    """The resume tape used to split a run only on a non-thinking delta, so two
    signed blocks became one part carrying the second signature."""
    tape = StreamTape()
    for d in _SIGNED_PAIR[1:6]:
        tape.append(d)
    assert [(p.text, p.signature) for p in tape.replay_thinking_parts()] == [
        ("th", "SIG1"), ("th2", "SIG2")]


def test_tape_still_splits_a_run_on_interleaved_text():
    tape = StreamTape()
    for d in (dl.ThinkingDelta("first", signature="sig-a"), dl.TextDelta("visible"),
              dl.ThinkingDelta("second", signature="sig-b")):
        tape.append(d)
    assert [(p.text, p.signature) for p in tape.replay_thinking_parts()] == [
        ("first", "sig-a"), ("second", "sig-b")]


def test_tape_treats_a_signature_bearing_delta_as_a_run_end():
    """Text and signature on ONE delta (OpenRouter's shape): the delta closes its
    own run, so following thinking text is a new part."""
    tape = StreamTape()
    tape.append(dl.ThinkingDelta("one", signature="s1"))
    tape.append(dl.ThinkingDelta("two", signature="s2"))
    assert [(p.text, p.signature) for p in tape.replay_thinking_parts()] == [
        ("one", "s1"), ("two", "s2")]


# -- 4. a redacted block must not close an open tool block -------------------


def test_redacted_thinking_does_not_close_an_open_tool_block():
    """The tool's later argument fragments must still land on the tool block."""
    events = _anthropic_events([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(0, "toolu_1", "get_weather"),
        dl.ToolCallArgsDelta(0, '{"a"'),
        dl.ThinkingDelta("", block_type="redacted_thinking", data="BLOB"),
        dl.ToolCallArgsDelta(0, ':1}'),
        dl.ToolCallClose(0),
        dl.UsageFinal(prompt=1, output=2), dl.Finish("tool_call"), dl.StreamEnd()])
    blocks = _blocks(events)
    assert blocks[0] == ["tool_use", '{"a":1}', None], blocks
    assert blocks[1] == ["redacted_thinking", "BLOB", None], blocks
    # Every content_block_start has a matching stop, in order.
    opened = [f["index"] for f in events if f.get("type") == "content_block_start"]
    stopped = [f["index"] for f in events if f.get("type") == "content_block_stop"]
    assert opened == stopped, (opened, stopped)
    assert events[-1]["delta"]["stop_reason"] == "tool_use"


def test_redacted_thinking_after_the_tool_group_closes_still_emits():
    """Redacted thinking arriving once no tool is open is emitted in place, as
    before."""
    events = _anthropic_events([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(0, "t", "f"), dl.ToolCallClose(0),
        dl.ThinkingDelta("", block_type="redacted_thinking", data="BLOB"),
        dl.Finish("stop"), dl.StreamEnd()])
    kinds = {i: b[:2] for i, b in _blocks(events).items()}
    assert kinds == {0: ["tool_use", ""], 1: ["redacted_thinking", "BLOB"]}


def test_redacted_thinking_inside_the_tool_group_precedes_the_deferred_text():
    """A redacted block deferred behind the tool group keeps its arrival order
    relative to deferred prose."""
    events = _anthropic_events([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(0, "t", "f"),
        dl.ThinkingDelta("", block_type="redacted_thinking", data="BLOB"),
        dl.TextDelta("prose"),
        dl.ToolCallClose(0),
        dl.Finish("stop"), dl.StreamEnd()])
    kinds = [(i, b[0], b[1]) for i, b in _blocks(events).items()]
    assert kinds == [(0, "tool_use", ""), (1, "redacted_thinking", "BLOB"),
                     (2, "text", "prose")], kinds


def test_redacted_thinking_is_dropped_after_a_stream_error():
    """Nothing content-bearing may follow the error frame, a deferred redacted
    block included."""
    enc = am.AnthropicStreamEncoder("m", "r")
    raw = enc.feed(dl.StreamStart(model="m"))
    raw += enc.feed(dl.ToolCallOpen(0, "t", "f"))
    raw += (enc.feed(dl.ThinkingDelta("", block_type="redacted_thinking",
                                      data="BLOB")) or b"")
    raw += enc.feed(dl.StreamError("boom", kind="connection"))
    raw += enc.final_frame()
    events = _events(raw)
    error_at = next(i for i, f in enumerate(events) if f["type"] == "error")
    assert not [f for f in events[error_at + 1:]
                if f["type"] == "content_block_start"], events


# -- force-stream fold keeps thinking blocks separate ------------------------


class _ScriptedThinkingAdapter:
    """A ``force_stream`` adapter whose stream is a fixed list of IR deltas.

    ``force_stream = True`` is what routes a non-streaming caller through
    ``_complete_via_stream``, the arm whose fold is under test."""

    force_stream = True

    def __init__(self, deltas: list[dl.IRStreamDelta]) -> None:
        self._deltas = deltas

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        return base_url.rstrip("/") + "/chat/completions"

    def encode_request(self, ir_req, model_id, params):
        return {}

    def headers(self, key) -> dict[str, str]:
        return {}

    def decode_stream_event(self, event: str, data: str):
        return list(self._deltas) if data == "GO" else []


def _force_dep():
    from wiwi.router.router import Deployment, ProviderAccount

    return Deployment(group="m", provider=ProviderAccount(
        name="a", provider_type="openai", base_url="https://a.example/v1"),
        model_id="m")


async def _fold(deltas: list[dl.IRStreamDelta]):
    """Run ``deltas`` through the real force-stream arm and return the turn."""
    import httpx
    import respx

    from wiwi.config import GeneralSettings, WiwiConfig
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.providers.base import ProviderKeyRef
    from wiwi.router.router import Router

    config = WiwiConfig(general_settings=GeneralSettings(master_key="sk-x"))
    gw = Gateway(Router(config), CostEngine())
    ctx = RequestContext(
        surface="chat",
        ir_req=ir.Request(model="m", messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")])]),
        group="m")

    async def _body():
        yield b"data: GO\n\n"

    try:
        with respx.mock:
            respx.post("https://a.example/v1/chat/completions").mock(
                side_effect=lambda r: httpx.Response(
                    200, content=_body(),
                    headers={"content-type": "text/event-stream"}))
            return await gw._complete_via_stream(
                _force_dep(), ProviderKeyRef(label="ka", secret="sk-a"),
                ctx, _ScriptedThinkingAdapter(deltas))
    finally:
        await gw.aclose()


async def test_force_stream_fold_keeps_thinking_blocks_separate():
    """A non-streaming caller served by a streaming-only upstream received every
    thinking block flattened into one unsigned part."""
    turn = await _fold([
        dl.StreamStart(model="m"),
        dl.ThinkingDelta("th"), dl.ThinkingDelta("", signature="SIG1"),
        dl.ThinkingDelta("th2"), dl.ThinkingDelta("", signature="SIG2"),
        dl.TextDelta("answer"),
        dl.UsageFinal(prompt=3, output=4),
        dl.Finish("stop"), dl.StreamEnd()])
    assert [(p.text, p.signature) for p in turn.thinking] == [
        ("th", "SIG1"), ("th2", "SIG2")]
    assert turn.text == "answer"
    assert (turn.usage.prompt_tokens, turn.usage.completion_tokens) == (3, 4)
    assert turn.stop_reason == "stop"


async def test_force_stream_fold_tool_call_args_and_stop_reason():
    """Control for the same fold: the non-thinking carriers are unchanged."""
    turn = await _fold([
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(0, "call_1", "get_weather", block_type="tool_use"),
        dl.ToolCallArgsDelta(0, '{"city":'), dl.ToolCallArgsDelta(0, '"Paris"}'),
        dl.ToolCallClose(0),
        dl.UsageFinal(prompt=3, output=4),
        dl.Finish("tool_call"), dl.StreamEnd()])
    assert [(c.id, c.name, c.args) for c in turn.tool_calls] == [
        ("call_1", "get_weather", {"city": "Paris"})]
    assert turn.stop_reason == "tool_call"


async def test_force_stream_fold_carries_a_redacted_block():
    turn = await _fold([
        dl.StreamStart(model="m"),
        dl.ThinkingDelta("", block_type="redacted_thinking", data="BLOB"),
        dl.ThinkingDelta("th", signature="SIG"),
        dl.UsageFinal(prompt=1, output=1),
        dl.Finish("stop"), dl.StreamEnd()])
    assert [(p.block_type, p.text, p.signature, p.data) for p in turn.thinking] == [
        ("redacted_thinking", "", None, "BLOB"), ("thinking", "th", "SIG", None)]


# -- 5. streamed logprobs accumulate -----------------------------------------


def test_responses_stream_merges_logprobs_across_frames():
    events = _responses_events([
        dl.StreamStart(model="m"),
        dl.LogprobsDelta({"content": [{"token": "hello", "logprob": -0.1}]}),
        dl.LogprobsDelta({"content": [{"token": " world", "logprob": -0.2}]}),
        dl.Finish("stop"), dl.StreamEnd()])
    completed = events[-1]["response"]
    assert completed["logprobs"] == {"content": [
        {"token": "hello", "logprob": -0.1}, {"token": " world", "logprob": -0.2}]}


def test_responses_stream_without_logprobs_grows_no_field():
    events = _responses_events([
        dl.StreamStart(model="m"), dl.TextDelta("x"),
        dl.Finish("stop"), dl.StreamEnd()])
    assert "logprobs" not in events[-1]["response"]


def test_responses_stored_payload_carries_the_merged_logprobs():
    stored = orp.ResponsesStreamEncoder.rebuild_stored_payload("m", "r", [
        dl.StreamStart(model="m"),
        dl.LogprobsDelta({"content": [{"token": "a"}]}),
        dl.LogprobsDelta({"content": [{"token": "b"}]}),
        dl.Finish("stop"), dl.StreamEnd()])
    assert [t["token"] for t in stored["logprobs"]["content"]] == ["a", "b"]


# -- 6. a reasoning-only sync turn carries no empty message ------------------


def test_responses_sync_reasoning_only_turn_emits_no_empty_message():
    resp = orp.encode_response(
        None, ir.AssistantTurn(text="", thinking=[ir.ThinkingPart("plan")]), "m", "r")
    assert [o["type"] for o in resp["output"]] == ["reasoning"]


def test_responses_sync_reasoning_only_turn_matches_the_stream():
    """Sync and stream must agree on the output shape for the same turn."""
    streamed = _responses_events([
        dl.StreamStart(model="m"), dl.ThinkingDelta("plan"),
        dl.Finish("stop"), dl.StreamEnd()])[-1]["response"]["output"]
    sync = orp.encode_response(
        None, ir.AssistantTurn(text="", thinking=[ir.ThinkingPart("plan")]), "m", "r")
    assert [o["type"] for o in sync["output"]] == [o["type"] for o in streamed]


def test_responses_sync_reasoning_turn_with_text_keeps_the_message():
    resp = orp.encode_response(
        None, ir.AssistantTurn(text="answer", thinking=[ir.ThinkingPart("plan")]),
        "m", "r")
    assert [o["type"] for o in resp["output"]] == ["reasoning", "message"]


def test_responses_sync_tool_only_and_empty_turns_are_unchanged():
    """A turn with nothing at all still answers with an (empty) message: the
    client needs some output to read, and this is the documented shape."""
    empty = orp.encode_response(None, ir.AssistantTurn(text=""), "m", "r")
    assert [o["type"] for o in empty["output"]] == ["message"]
    tool = orp.encode_response(
        None, ir.AssistantTurn(text="", tool_calls=[ir.ToolUsePart("c1", "f")]), "m", "r")
    assert [o["type"] for o in tool["output"]] == ["function_call"]


def test_responses_reasoning_only_turn_replays_to_anthropic_without_placeholders():
    """End to end: an Anthropic upstream that thinks and stops is served to a
    Responses client, then replayed — no empty assistant turn, no ``" "`
    placeholder blocks."""
    upstream = json.dumps({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude",
        "content": [{"type": "thinking", "thinking": "plan", "signature": "SIG"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3}}).encode()
    turn = AnthropicAdapter().decode_response(200, upstream)
    served = orp.encode_response(None, turn, "claude", "r1")
    assert [o["type"] for o in served["output"]] == ["reasoning"]
    replay = orp.decode_request({
        "model": "claude",
        "input": [{"role": "user", "content": "hi"}] + served["output"]
                 + [{"role": "user", "content": "next"}]})
    body = AnthropicAdapter().encode_request(replay, "claude", {})
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": " "}]},
        {"role": "user", "content": [{"type": "text", "text": "next"}]}]


# -- 7. stream flags are booleans, not truthiness ----------------------------


def _decoders():
    """Every inbound codec, each driven with its own minimal valid body."""
    return [
        ("chat", oc.decode_request, {**_BASE}),
        ("completions", ocmpl.decode_request, {"model": "m", "prompt": "hi"}),
        ("anthropic", am.decode_request,
         {"model": "m", "max_tokens": 8, "messages": [
             {"role": "user", "content": "hi"}]}),
        ("responses", orp.decode_request, {"model": "m", "input": "hi"}),
    ]


def test_stream_flag_ignores_a_string_false_on_every_surface():
    for name, decode, body in _decoders():
        assert decode({**body, "stream": "false"}).stream is False, name


def test_stream_flag_ignores_other_truthy_junk():
    for name, decode, body in _decoders():
        for junk in ("yes", "true", {}, [1]):
            assert decode({**body, "stream": junk}).stream is False, (name, junk)


def test_stream_flag_still_accepts_true():
    for name, decode, body in _decoders():
        for value in (True, 1):
            assert decode({**body, "stream": value}).stream is True, (name, value)


def test_stream_flag_defaults_to_false():
    for name, decode, body in _decoders():
        assert decode(body).stream is False, name


def test_include_usage_ignores_a_string_false_on_the_openai_surfaces():
    """Only Chat and Completions read ``stream_options.include_usage``; the
    Anthropic and Responses codecs derive their usage frames elsewhere."""
    req = oc.decode_request({**_BASE, "stream": True,
                             "stream_options": {"include_usage": "false"}})
    assert req.stream_options_include_usage is False
    req = ocmpl.decode_request({"model": "m", "prompt": "hi", "stream": True,
                                "stream_options": {"include_usage": "false"}})
    assert req.stream_options_include_usage is False


def test_include_usage_still_accepts_true_on_the_openai_surfaces():
    req = oc.decode_request({**_BASE, "stream": True,
                             "stream_options": {"include_usage": True}})
    assert req.stream_options_include_usage is True
    req = ocmpl.decode_request({"model": "m", "prompt": "hi", "stream": True,
                                "stream_options": {"include_usage": True}})
    assert req.stream_options_include_usage is True


def test_chat_stream_flag_string_true_still_streams():
    """Only booleans and 0/1 are honoured; the string ``"true"`` is not."""
    req = oc.decode_request({**_BASE, "stream": "true"})
    assert req.stream is False


# -- 8. legacy function keys are not forwarded twice --------------------------


def test_legacy_functions_do_not_ride_extras():
    req = oc.decode_request({**_BASE, "functions": [{"name": "f",
                                                      "parameters": {"type": "object"}}]})
    assert "functions" not in req.extras
    assert [t.name for t in req.tools] == ["f"]


def test_legacy_function_call_choice_does_not_ride_extras():
    req = oc.decode_request({**_BASE, "function_call": {"name": "f"}})
    assert "function_call" not in req.extras
    assert isinstance(req.tool_choice, ir.ToolChoiceNamed)


def test_modern_tool_keys_never_ride_extras():
    req = oc.decode_request({**_BASE,
                             "tools": [{"type": "function",
                                        "function": {"name": "f"}}],
                             "tool_choice": "auto"})
    assert req.extras == {}

def test_legacy_functions_reach_an_openai_upstream_once():
    from wiwi.providers.registry import fresh_adapter

    req = oc.decode_request({**_BASE, "functions": [{"name": "f",
                                                      "parameters": {"type": "object"}}]})
    body = fresh_adapter("openai").encode_request(req, "m", {})
    assert [t["function"]["name"] for t in body["tools"]] == ["f"]
    assert "functions" not in body and "function_call" not in body


def test_anthropic_upstream_receives_the_legacy_tool_as_a_native_tool():
    req = oc.decode_request({**_BASE, "functions": [{"name": "f",
                                                      "parameters": {"type": "object"}}]})
    body = AnthropicAdapter().encode_request(req, "m", {})
    assert [t["name"] for t in body["tools"]] == ["f"]


def test_legacy_function_choice_reaches_the_upstream_as_tool_choice():
    from wiwi.providers.registry import fresh_adapter

    req = oc.decode_request({**_BASE, "functions": [{"name": "f"}],
                             "function_call": {"name": "f"}})
    body = fresh_adapter("openai").encode_request(req, "m", {})
    assert body["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    assert "function_call" not in body


def test_legacy_functions_served_by_anthropic_inbound_round_trip():
    """A legacy Chat client against a Messages upstream still gets the tool,
    decoded from the modern shape the adapter emits."""
    req = oc.decode_request({**_BASE, "functions": [{"name": "f"}]})
    body = AnthropicAdapter().encode_request(req, "m", {})
    assert body["tools"] == [{"name": "f", "description": "",
                              "input_schema": {"type": "object"}}]


# -- 9. a replayed refusal is not dropped ------------------------------------


def test_chat_assistant_refusal_field_decodes_as_text():
    """OpenAI puts a content-filter refusal in ``message.refusal`` with
    ``content: null``. A client echoing that turn back got an empty one."""
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "refusal": "I can't help with that"},
        {"role": "user", "content": "why?"}]})
    assistant = req.messages[1]
    assert [type(p).__name__ for p in assistant.parts] == ["TextPart"]
    assert assistant.parts[0].text == "I can't help with that"


def test_chat_assistant_refusal_part_decodes_as_text():
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "assistant", "content": [
            {"type": "refusal", "refusal": "no"}]}]})
    assert [p.text for p in req.messages[0].parts] == ["no"]


def test_chat_refusal_survives_content_null_and_tool_calls():
    """A refusal turn may still carry tool calls; both are kept."""
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "assistant", "content": None, "refusal": "no",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "f", "arguments": "{}"}}]}]})
    parts = req.messages[0].parts
    assert [(type(p).__name__, getattr(p, "text", None) or p.id)
            for p in parts] == [("TextPart", "no"), ("ToolUsePart", "call_1")]


def test_chat_refusal_beside_text_keeps_both_in_order():
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "assistant", "content": "partial answer", "refusal": "but no"}]})
    assert [p.text for p in req.messages[0].parts] == ["partial answer", "but no"]


def test_chat_typed_wrong_refusal_is_ignored():
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "assistant", "content": None, "refusal": ["no"], "name": "a"}]})
    assert req.messages[0].parts == []


def test_chat_user_refusal_is_not_an_assistant_refusal():
    """``refusal`` is an assistant-turn field; elsewhere it is not model
    history."""
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "user", "content": "hi", "refusal": "x"}]})
    assert [p.text for p in req.messages[0].parts] == ["hi"]


def test_chat_refusal_replays_to_an_openai_upstream():
    from wiwi.providers.registry import fresh_adapter

    req = oc.decode_request({**_BASE, "messages": [
        {"role": "assistant", "content": None, "refusal": "I can't help"}]})
    body = fresh_adapter("openai").encode_request(req, "m", {})
    assert body["messages"] == [{"role": "assistant", "content": "I can't help"}]


def test_chat_refusal_replays_to_an_anthropic_upstream():
    req = oc.decode_request({**_BASE, "messages": [
        {"role": "assistant", "content": None, "refusal": "I can't help"}]})
    body = AnthropicAdapter().encode_request(req, "m", {})
    assert body["messages"] == [
        {"role": "assistant", "content": [
            {"type": "text", "text": "I can't help"}]}]


def test_openai_upstream_refusal_then_chat_replay_round_trips():
    """End to end: an upstream refusal is served to the Chat client, and the
    client's echoed history carries it back instead of an empty turn."""
    from wiwi.providers.registry import fresh_adapter

    upstream = json.dumps({
        "id": "chatcmpl-1", "object": "chat.completion", "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": "content_filter",
                     "message": {"role": "assistant", "content": None,
                                 "refusal": "I can't help with that"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
    turn = fresh_adapter("openai").decode_response(200, upstream)
    served = oc.encode_response(None, turn, "gpt-x", "r1")
    replay = oc.decode_request({
        "model": "gpt-x",
        "messages": [{"role": "user", "content": "do it"},
                     served["choices"][0]["message"],
                     {"role": "user", "content": "why?"}]})
    assert replay.messages[1].parts[0].text == "I can't help with that"


# -- 10. ``strict`` is a boolean ------------------------------------------------


def test_strict_typed_wrong_is_dropped_on_every_surface():
    bodies = [
        (oc.decode_request, {**_BASE, "tools": [
            {"type": "function", "function": {"name": "f", "strict": "yes"}}]}),
        (orp.decode_request, {"model": "m", "input": "hi", "tools": [
            {"type": "function", "name": "f", "strict": "yes"}]}),
        (am.decode_request, {"model": "m", "max_tokens": 8, "messages": [
            {"role": "user", "content": "hi"}],
            "tools": [{"name": "f", "input_schema": {"type": "object"},
                       "strict": "yes"}]}),
    ]
    for decode, body in bodies:
        assert decode(body).tools[0].strict is None, body


def test_strict_still_accepts_booleans_and_zero():
    for value, want in ((True, True), (False, False), (0, False), (1, True)):
        req = oc.decode_request({**_BASE, "tools": [
            {"type": "function", "function": {"name": "f", "strict": value}}]})
        assert req.tools[0].strict is want, value


def test_strict_survives_to_the_upstream_when_well_formed():
    from wiwi.providers.registry import fresh_adapter

    req = oc.decode_request({**_BASE, "tools": [
        {"type": "function", "function": {"name": "f", "strict": True}}]})
    assert fresh_adapter("openai").encode_request(req, "m", {})["tools"][0][
        "function"]["strict"] is True


def test_response_format_strict_typed_wrong_is_dropped():
    for decode, body in (
            (oc.decode_request, {**_BASE, "response_format": {
                "type": "json_schema", "json_schema": {"name": "s",
                                                       "schema": {"type": "object"},
                                                       "strict": "yes"}}}),
            (orp.decode_request, {"model": "m", "input": "hi", "text": {"format": {
                "type": "json_schema", "name": "s", "schema": {"type": "object"},
                "strict": "yes"}}})):
        assert decode(body).gen_params.response_format.strict is None, body


# -- an unknown usage convention is not re-based -----------------------------


def test_unknown_usage_convention_is_emitted_verbatim():
    """``cached_in_prompt=None`` means the decoder did not say. The codec has no
    deployment to infer from, so it must not add or subtract the cached count."""
    turn = ir.AssistantTurn(text="x", usage=ir.Usage(
        prompt_tokens=120, completion_tokens=5, cached_tokens=100,
        cached_in_prompt=None))
    assert oc.encode_response(None, turn, "m", "r")["usage"]["prompt_tokens"] == 120
    assert ocmpl.encode_response(None, turn, "m", "r")["usage"]["prompt_tokens"] == 120
    assert orp.encode_response(None, turn, "m", "r")["usage"]["input_tokens"] == 120
    assert am.encode_response(None, turn, "m", "r")["usage"]["input_tokens"] == 120
    # The cached count itself is still reported.
    assert orp.encode_response(None, turn, "m", "r")["usage"][
        "input_tokens_details"]["cached_tokens"] == 100
    assert am.encode_response(None, turn, "m", "r")["usage"][
        "cache_read_input_tokens"] == 100


def _unknown_convention_prompt_count(make_encoder):
    final = dl.UsageFinal(prompt=120, cached=100, output=5, cached_in_prompt=None)
    enc = make_encoder()
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(final)
    enc.feed(dl.Finish("stop"))
    tail = (enc._completed() if isinstance(enc, orp.ResponsesStreamEncoder)
            else enc.final_frame()).decode()
    return tail.replace(" ", "")


def test_unknown_usage_convention_stream_is_emitted_verbatim():
    assert '"prompt_tokens":120' in _unknown_convention_prompt_count(
        lambda: oc.ChatStreamEncoder("m", "r", include_usage=True))
    assert '"prompt_tokens":120' in _unknown_convention_prompt_count(
        lambda: ocmpl.CompletionStreamEncoder("m", "r", include_usage=True))
    assert '"input_tokens":120' in _unknown_convention_prompt_count(
        lambda: am.AnthropicStreamEncoder("m", "r"))
    assert '"input_tokens":120' in _unknown_convention_prompt_count(
        lambda: orp.ResponsesStreamEncoder("m", "r"))


def test_known_usage_conventions_are_still_rebased():
    """Control: the two conventions the decoders record keep their re-base."""
    exclusive = ir.AssistantTurn(text="x", usage=ir.Usage(
        prompt_tokens=100, completion_tokens=5, cached_tokens=40,
        cached_in_prompt=False))
    assert oc.encode_response(None, exclusive, "m", "r")["usage"]["prompt_tokens"] == 140
    inclusive = ir.AssistantTurn(text="x", usage=ir.Usage(
        prompt_tokens=140, completion_tokens=5, cached_tokens=40,
        cached_in_prompt=True))
    assert am.encode_response(None, inclusive, "m", "r")["usage"]["input_tokens"] == 100


def test_anthropic_message_start_does_not_rebase_an_unknown_convention():
    enc = am.AnthropicStreamEncoder("m", "r")
    raw = enc.feed(dl.StreamStart(model="m", prompt=120, cached=100))
    start = _events(raw)[0]
    assert start["message"]["usage"]["input_tokens"] == 120


def test_positive_budget_is_unchanged():
    g = ir.GenParams(thinking_budget=8000)
    assert g.effective_thinking_budget() == 8000
    assert g.effective_reasoning_effort() == "medium"
