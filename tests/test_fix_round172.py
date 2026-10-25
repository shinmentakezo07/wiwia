"""Round-172 regressions: IR/codec/adapter audit fixes.

Covers the seam audit of 2026-10-25: streaming halves of fields the sync
paths already emitted (stop_sequence, logprobs), the deferred
encrypted_content blob, usage-convention re-basing at every wire encoder,
file_id handling, thinking_type propagation, and the decoder coercion
class. Each test names the defect it pins.
"""

from __future__ import annotations

import json

import wiwi.ir.types as ir
import wiwi.streaming.deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as ocomp
from wiwi.wire import openai_responses as oresp


def _turn(**kw) -> ir.AssistantTurn:
    return ir.AssistantTurn(text=kw.pop("text", "ok"), **kw)


# --- streaming stop_sequence (AUDIT #497's missing halves) -------------------


def _flat(s: str) -> str:
    """orjson emits no space after ':' — normalize for substring asserts."""
    return s.replace('": ', '":')


def test_chat_stream_emits_stop_sequence():
    enc = oc.ChatStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.Finish("stop_sequence", stop_sequence="END"))
    out = enc.final_frame().decode()
    assert '"stop_sequence":"END"' in _flat(out)
    # and absent when none matched
    enc2 = oc.ChatStreamEncoder("m", "r2")
    enc2.feed(dl.StreamStart(model="m"))
    enc2.feed(dl.Finish("stop"))
    assert "stop_sequence" not in enc2.final_frame().decode()


def test_chat_stream_clears_stop_sequence_on_tool_downgrade():
    """A downgraded tool turn never matched a caller sequence."""
    enc = oc.ChatStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.Finish("tool_call", stop_sequence="END"))
    out = enc.final_frame().decode()
    assert "stop_sequence" not in out
    assert '"finish_reason":"stop"' in _flat(out)


def test_responses_stream_emits_stop_sequence():
    enc = oresp.ResponsesStreamEncoder("m", "r1")
    for d in [dl.StreamStart(model="m"), dl.TextDelta("hi"),
              dl.Finish("stop_sequence", stop_sequence="END")]:
        enc.feed(d)
    tail = enc._completed().decode()
    assert '"stop_sequence":"END"' in _flat(tail)


# --- streaming logprobs (AUDIT #326's missing half) --------------------------


def test_responses_stream_emits_logprobs():
    enc = oresp.ResponsesStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.LogprobsDelta(logprobs={"content": [{"token": "a"}]}))
    enc.feed(dl.Finish("stop"))
    tail = enc._completed().decode()
    assert "logprobs" in tail
    assert '"token":"a"' in _flat(tail)


# --- deferred encrypted_content blob (AUDIT #355 interleave hole) ------------


def test_responses_deferred_thinking_keeps_blob():
    enc = oresp.ResponsesStreamEncoder("m", "r1")
    for d in [dl.StreamStart(model="m"),
              dl.ToolCallOpen(index=0, id="c1", name="get"),
              dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
              dl.ThinkingDelta(text="hmm", encrypted_content="BLOB"),
              dl.ToolCallClose(index=0),
              dl.Finish("tool_call")]:
        enc.feed(d)
    tail = enc._completed().decode()
    assert "BLOB" in tail


def test_responses_blob_only_deferred_thinking_survives():
    """No text at all: the blob alone must still open a reasoning item."""
    enc = oresp.ResponsesStreamEncoder("m", "r1")
    for d in [dl.StreamStart(model="m"),
              dl.ToolCallOpen(index=0, id="c1", name="get"),
              dl.ThinkingDelta(text="", encrypted_content="ONLYBLOB"),
              dl.ToolCallClose(index=0),
              dl.Finish("tool_call")]:
        enc.feed(d)
    tail = enc._completed().decode()
    assert "ONLYBLOB" in tail


# --- usage-convention re-base (cached_in_prompt consulted on emit) -----------


def test_chat_sync_rebases_anthropic_convention_usage():
    """Anthropic input_tokens EXCLUDE cached; OpenAI clients expect inclusive."""
    turn = _turn(usage=ir.Usage(prompt_tokens=100, cached_tokens=40,
                                cached_in_prompt=False, completion_tokens=5))
    resp = oc.encode_response(None, turn, "m", "r1")
    assert resp["usage"]["prompt_tokens"] == 140
    assert resp["usage"]["total_tokens"] == 145
    assert resp["usage"]["prompt_tokens_details"]["cached_tokens"] == 40


def test_chat_sync_keeps_openai_convention_usage():
    turn = _turn(usage=ir.Usage(prompt_tokens=140, cached_tokens=40,
                                cached_in_prompt=True, completion_tokens=5))
    resp = oc.encode_response(None, turn, "m", "r1")
    assert resp["usage"]["prompt_tokens"] == 140


def test_chat_stream_rebases_usage():
    enc = oc.ChatStreamEncoder("m", "r1", include_usage=True)
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.UsageFinal(prompt=100, cached=40, cached_in_prompt=False,
                           output=5))
    enc.feed(dl.Finish("stop"))
    out = enc.final_frame().decode()
    assert '"prompt_tokens":140' in _flat(out)


def test_anthropic_sync_rebases_openai_convention_usage():
    """OpenAI prompt INCLUDES cached; Messages clients expect exclusive."""
    turn = _turn(usage=ir.Usage(prompt_tokens=140, cached_tokens=40,
                                cached_in_prompt=True, completion_tokens=5))
    resp = am.encode_response(None, turn, "m", "r1")
    assert resp["usage"]["input_tokens"] == 100
    assert resp["usage"]["cache_read_input_tokens"] == 40


def test_anthropic_stream_rebases_usage():
    enc = am.AnthropicStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.UsageFinal(prompt=140, cached=40, cached_in_prompt=True,
                           output=5))
    enc.feed(dl.Finish("stop"))
    out = enc.final_frame().decode()
    assert '"input_tokens":100' in _flat(out)


def test_completions_rebase_sync_and_stream():
    turn = _turn(usage=ir.Usage(prompt_tokens=10, cached_tokens=4,
                                cached_in_prompt=False, completion_tokens=2))
    resp = ocomp.encode_response(None, turn, "m", "r1")
    assert resp["usage"]["prompt_tokens"] == 14
    enc = ocomp.CompletionStreamEncoder("m", "r1", include_usage=True)
    enc.feed(dl.UsageFinal(prompt=10, cached=4, cached_in_prompt=False,
                           output=2))
    enc.feed(dl.Finish("stop"))
    assert '"prompt_tokens":14' in _flat(enc.final_frame().decode())


def test_responses_rebase_sync_and_stream():
    turn = _turn(usage=ir.Usage(prompt_tokens=100, cached_tokens=40,
                                cached_in_prompt=False, completion_tokens=7))
    resp = oresp.encode_response(None, turn, "m", "r1")
    assert resp["usage"]["input_tokens"] == 140
    enc = oresp.ResponsesStreamEncoder("m", "r1")
    enc.feed(dl.StreamStart(model="m"))
    enc.feed(dl.UsageFinal(prompt=100, cached=40, cached_in_prompt=False,
                           output=7))
    enc.feed(dl.Finish("stop"))
    assert '"input_tokens":140' in _flat(enc._completed().decode())


# --- GenParams.thinking_type propagates (was invisible to adapters) ----------


def test_thinking_type_enabled_derives_effort_and_budget():
    g = ir.GenParams(thinking_type="enabled")
    assert g.effective_reasoning_effort() == "medium"
    assert g.effective_thinking_budget() == 8000


def test_thinking_type_adaptive_and_disabled():
    """Adaptive is model-driven: no level derived (the provider's own default
    applies — round-53 pins that for WorkBuddy); disabled maps to none."""
    assert ir.GenParams(thinking_type="adaptive").effective_reasoning_effort() \
        is None
    assert ir.GenParams(thinking_type="disabled").effective_reasoning_effort() \
        == "none"


def test_genparams_has_no_metadata_field():
    """Dead field removed; the response-cache gate must not reference it."""
    assert not hasattr(ir.GenParams(), "metadata")


def test_anthropic_adapter_enables_thinking_on_mode_only():
    """thinking: {type: enabled} with no budget now turns thinking ON."""
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    a = AnthropicAdapter.__new__(AnthropicAdapter)
    a.translation_warnings = []
    req = ir.Request(model="c",
                     messages=[ir.Message(role="user",
                                          parts=[ir.TextPart("x")])],
                     gen_params=ir.GenParams(thinking_type="enabled",
                                             max_tokens=100))
    body = a.encode_request(req, "claude", {})
    assert body["thinking"]["type"] == "enabled"
    assert body["thinking"]["budget_tokens"] >= 1024


# --- file_id handling --------------------------------------------------------


def test_chat_decode_reads_file_part():
    body = {"model": "gpt", "messages": [{
        "role": "user",
        "content": [{"type": "file",
                     "file": {"file_id": "file-123"}}]}]}
    r = oc.decode_request(body)
    part = r.messages[0].parts[0]
    assert isinstance(part, ir.ImagePart) and part.file_id == "file-123"


def test_chat_decode_reads_file_data_document():
    body = {"model": "gpt", "messages": [{
        "role": "user",
        "content": [{"type": "file",
                     "file": {"file_data": "https://x/y.pdf"}}]}]}
    r = oc.decode_request(body)
    part = r.messages[0].parts[0]
    assert isinstance(part, ir.DocumentPart) and part.url == "https://x/y.pdf"


def test_openai_adapter_file_id_never_becomes_corrupt_data_url():
    from wiwi.providers.openai_adapter import _role_parts_to_content
    msgs = [ir.Message(role="user",
                       parts=[ir.ImagePart(file_id="file-9")])]
    out = _role_parts_to_content(msgs)
    blob = json.dumps(out)
    assert "file-9" in blob
    assert "base64,None" not in blob


def test_responses_decode_input_image_file_id():
    body = {"model": "gpt", "input": [{
        "role": "user",
        "content": [{"type": "input_image", "file_id": "file-7"}]}]}
    r = oresp.decode_request(body)
    assert r.messages[0].parts[0].file_id == "file-7"


# --- decoder coercion (AUDIT #83 class, closed for the rest) -----------------


def test_chat_coerces_typed_wrong_sampling_params():
    body = {"model": "gpt",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": "0.7", "top_p": True, "seed": "7",
            "parallel_tool_calls": "yes"}
    r = oc.decode_request(body)
    g = r.gen_params
    assert g.temperature is None and g.top_p is None
    assert g.seed is None and g.parallel_tool_calls is None
    assert g.n == 1  # no n in the body: the default


def test_anthropic_coerces_temperature_and_disable_parallel():
    body = {"model": "c", "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": "hot", "top_p": [],
            "tool_choice": {"type": "auto",
                            "disable_parallel_tool_use": "yes"}}
    r = am.decode_request(body)
    assert r.gen_params.temperature is None
    assert r.gen_params.top_p is None
    assert r.gen_params.disable_parallel_tool_use is None


def test_responses_coerces_seed_and_parallel():
    body = {"model": "gpt", "input": "hi", "seed": True,
            "parallel_tool_calls": "false", "temperature": {}}
    r = oresp.decode_request(body)
    assert r.gen_params.seed is None
    assert r.gen_params.parallel_tool_calls is None
    assert r.gen_params.disable_parallel_tool_use is None
    assert r.gen_params.temperature is None


def test_responses_reasoning_summary_rides_extras():
    body = {"model": "gpt", "input": "hi",
            "reasoning": {"effort": "high", "summary": "detailed"}}
    r = oresp.decode_request(body)
    assert r.gen_params.reasoning_effort == "high"
    assert r.extras.get("reasoning_summary") == "detailed"


def test_completions_coerces_typed_wrong_params():
    r = ocomp.decode_request({"model": "gpt", "prompt": "hi",
                              "temperature": "x", "top_p": True})
    assert r.gen_params.temperature is None
    assert r.gen_params.top_p is None


# --- Anthropic tool-result decode: no extra_blocks duplication --------------


def test_anthropic_tool_result_blocks_not_duplicated_into_content():
    body = {"model": "c", "max_tokens": 8, "messages": [{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1",
                     "content": [{"type": "tool_reference",
                                  "name": "search"}]}]}]}
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    r = am.decode_request(body)
    part = r.messages[0].parts[0]
    assert part.extra_blocks == [{"type": "tool_reference", "name": "search"}]
    # The flattened fallback stays on the IR for block-less dialects; the
    # ANTHROPIC re-encode (which carries the blocks natively) must not also
    # stringify them as a text block.
    out = AnthropicAdapter().encode_request(r, "c", {})
    tr_block = out["messages"][0]["content"][0]
    types = [b["type"] for b in tr_block["content"]]
    assert types == ["tool_reference"]


def test_anthropic_text_plus_blocks_keeps_text_content():
    body = {"model": "c", "max_tokens": 8, "messages": [{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1",
                     "content": [
                         {"type": "text", "text": "Found 1."},
                         {"type": "tool_reference", "name": "search"}]}]}]}
    r = am.decode_request(body)
    part = r.messages[0].parts[0]
    assert part.content == "Found 1."
    assert len(part.extra_blocks) == 1


# --- Anthropic stream error type vocabulary ----------------------------------


def test_anthropic_stream_error_maps_connection_type():
    """api_connection_error is wiwi's own; Anthropic says api_error."""
    err = dl.StreamError(message="boom", kind="status", status=503)
    enc = am.AnthropicStreamEncoder("m", "r1")
    out = enc.feed(err).decode()
    assert '"api_error"' in out
    assert "api_connection_error" not in out


def test_anthropic_stream_error_keeps_overloaded():
    err = dl.StreamError(message="overloaded", kind="status", status=529)
    enc = am.AnthropicStreamEncoder("m", "r1")
    out = enc.feed(err).decode()
    assert "overloaded_error" in out


# --- gemini zero budget + input examples -------------------------------------


def test_gemini_zero_budget_disables_thinking():
    from wiwi.providers.gemini_adapter import GeminiAdapter
    a = GeminiAdapter.__new__(GeminiAdapter)
    a.translation_warnings = []
    req = ir.Request(model="g",
                     messages=[ir.Message(role="user",
                                          parts=[ir.TextPart("x")])],
                     gen_params=ir.GenParams(thinking_budget=0))
    body = a.encode_request(req, "gem-2.5", {})
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_gemini_renders_input_examples_into_description():
    from wiwi.providers.gemini_adapter import GeminiAdapter
    a = GeminiAdapter.__new__(GeminiAdapter)
    a.translation_warnings = []
    req = ir.Request(model="g",
                     messages=[ir.Message(role="user",
                                          parts=[ir.TextPart("x")])],
                     tools=[ir.Tool(name="t", input_examples=[{"q": 1}])])
    body = a.encode_request(req, "gem-2.5", {})
    desc = body["tools"][0]["functionDeclarations"][0]["description"]
    assert "Example inputs" in desc and '"q": 1' in desc


def test_gemini_records_usage_convention():
    from wiwi.providers.gemini_adapter import GeminiAdapter
    a = GeminiAdapter.__new__(GeminiAdapter)
    import orjson as _oj
    payload = _oj.dumps({
        "candidates": [{"content": {"parts": [{"text": "hi"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 50,
                          "cachedContentTokenCount": 20,
                          "candidatesTokenCount": 3}}).decode()
    turn = a.decode_response(200, payload.encode())
    assert turn.usage.cached_in_prompt is True


def test_anthropic_tool_result_image_keeps_cache_control_on_replay():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    a = AnthropicAdapter.__new__(AnthropicAdapter)
    a.translation_warnings = []
    req = ir.Request(model="c", messages=[ir.Message(role="user", parts=[
        ir.ToolResultPart(
            tool_use_id="t1", content="shot",
            images=[ir.ImagePart(b64="AAAA",
                                 cache_control={"type": "ephemeral"})])])])
    body = a.encode_request(req, "claude", {})
    blocks = body["messages"][0]["content"][0]["content"]
    img = next(b for b in blocks if b["type"] == "image")
    assert img["cache_control"] == {"type": "ephemeral"}
