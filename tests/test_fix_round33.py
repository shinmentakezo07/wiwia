"""Round-33 regression tests: type-safety in the round-31 Responses decoders
and the Anthropic zero-thinking-budget path.

Scope (all reproduced against source before fixing):

1. The round-31 helpers in ``wire.openai_responses`` accepted any JSON value
   where the protocol guarantees a string or object. A client that sends a
   well-formed-but-wrong-shaped value (a Chat-style ``image_url`` dict, a
   non-string ``file_data``, a string ``input_audio`` payload, a non-string
   ``url`` inside a tool result) raised ``AttributeError`` — which
   ``run_chat_like`` does not catch — and surfaced as HTTP 500. All other
   malformed inputs in these codecs are skipped per the documented
   "skip rather than 500 on .get" policy; these paths must follow it.

2. ``providers.anthropic_adapter.encode_request`` treated ``thinking_budget=0``
   as "thinking enabled with the minimum budget" (1024). Round 31 defined
   budget 0 as *disabled* (``thinking_budget_to_effort(0) == "none"``) and made
   Gemini honor it (``thinkingBudget: 0``), but the Anthropic adapter still
   turned the disable request ON. ``thinking_type="disabled"`` is the codec's
   canonical disable form, so budget 0 now takes the same path.
"""

from __future__ import annotations

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.wire import openai_responses as orp

# ---------------------------------------------------------------------------
# 1. Responses decoders: wrong-shaped values skip, never 500
# ---------------------------------------------------------------------------


def test_responses_dict_image_url_does_not_500():
    """A Chat-style ``image_url`` dict (the shape Chat Completions documents)
    must be skipped, not raise AttributeError -> 500."""
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_image", "image_url": {"url": "https://x/y.png"}},
        ]}]})
    msg = req.messages[-1]
    assert msg.parts == [], "unparseable image must be skipped, not crash"


def test_responses_string_image_url_still_decodes():
    """Guard: the legal string form keeps decoding after the type guard."""
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_image", "image_url": "https://x/y.png"},
        ]}]})
    img = req.messages[-1].parts[0]
    assert img.url == "https://x/y.png"


def test_responses_data_url_image_still_decodes():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"},
        ]}]})
    img = req.messages[-1].parts[0]
    assert img.b64 == "AAAA"
    assert img.mime == "image/jpeg"


def test_responses_non_string_file_data_does_not_500():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_file", "file_data": {"parts": ["x"]}},
        ]}]})
    assert req.messages[-1].parts == []


def test_responses_string_file_data_still_decodes():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_file", "file_data": "data:application/pdf;base64,JVBERi0=",
             "filename": "spec.pdf"},
        ]}]})
    doc = req.messages[-1].parts[0]
    assert doc.b64 == "JVBERi0="
    assert doc.name == "spec.pdf"


def test_responses_non_dict_input_audio_does_not_500():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_audio", "input_audio": "SUQz"},
        ]}]})
    assert req.messages[-1].parts == []


def test_responses_dict_input_audio_still_decodes():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": "SUQz", "format": "mp3"}},
        ]}]})
    audio = req.messages[-1].parts[0]
    assert audio.b64 == "SUQz"
    assert audio.mime == "audio/mp3"


def test_responses_tool_result_non_string_image_url_does_not_500():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "function_call_output", "call_id": "c1",
         "output": [{"type": "input_image", "image_url": ["a"]}]},
    ]})
    part = req.messages[-1].parts[0]
    # content falls back to the JSON dump of the block list (pre-existing
    # _item_text behaviour); the point is that a non-string image_url is
    # skipped, not raised.
    assert part.images == [], "unparseable image url must be skipped"


def test_responses_tool_result_string_image_url_still_decodes():
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "function_call_output", "call_id": "c1",
         "output": [{"type": "input_image", "image_url": "https://x/y.png"}]},
    ]})
    part = req.messages[-1].parts[0]
    assert len(part.images) == 1
    assert part.images[0].url == "https://x/y.png"


def test_responses_mixed_content_one_bad_block_keeps_good_blocks():
    """A junk block among legal ones skips only the junk."""
    req = orp.decode_request({"model": "gpt-5", "input": [
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "hello"},
            {"type": "input_image", "image_url": {"url": "x"}},
            {"type": "input_text", "text": "world"},
        ]}]})
    texts = [p.text for p in req.messages[-1].parts if isinstance(p, ir.TextPart)]
    assert texts == ["hello", "world"]


# ---------------------------------------------------------------------------
# 2. Anthropic adapter: budget 0 means disabled, not minimum-budget enabled
# ---------------------------------------------------------------------------


def test_anthropic_budget_zero_disables_thinking():
    """thinking_budget=0 must omit the thinking block, like thinking_type
    'disabled' — not clamp to the 1024 minimum and turn thinking ON."""
    req = ir.Request(
        model="claude-sonnet-4-5",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=0, max_tokens=4096),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert "thinking" not in body


def test_anthropic_budget_zero_matches_disabled_mode():
    """Budget 0 and the explicit 'disabled' mode must produce the same wire."""
    msgs = [ir.Message(role="user", parts=[ir.TextPart("hi")])]
    zero = AnthropicAdapter().encode_request(
        ir.Request(model="claude-sonnet-4-5", messages=msgs,
                   gen_params=ir.GenParams(thinking_budget=0, max_tokens=4096)),
        "claude-sonnet-4-5", {})
    disabled = AnthropicAdapter().encode_request(
        ir.Request(model="claude-sonnet-4-5", messages=msgs,
                   gen_params=ir.GenParams(thinking_type="disabled", max_tokens=4096)),
        "claude-sonnet-4-5", {})
    assert "thinking" in zero or "thinking" not in zero  # shape guard
    assert ("thinking" in zero) == ("thinking" in disabled)
    assert "thinking" not in disabled


def test_anthropic_positive_budget_still_enables_thinking():
    """Guard: real budgets keep enabling thinking with clamping."""
    req = ir.Request(
        model="claude-sonnet-4-5",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=5000, max_tokens=4096),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 5000}


def test_anthropic_small_budget_still_clamps_to_minimum():
    """A small positive budget is still clamped to the API minimum (1024)."""
    req = ir.Request(
        model="claude-sonnet-4-5",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=1, max_tokens=4096),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["thinking"]["budget_tokens"] == 1024


def test_gemini_budget_zero_still_disables():
    """Guard the round-31 Gemini behavior so this round cannot regress it."""
    from wiwi.providers.gemini_adapter import GeminiAdapter
    req = ir.Request(
        model="gemini-2.5-pro",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=0, max_tokens=64),
    )
    body = GeminiAdapter().encode_request(req, "gemini-2.5-pro", {})
    assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
