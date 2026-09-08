"""Round-31 regression tests: thinking-budget/effort mapping, Responses decode
gaps, and multimodal part translation.

Scope (all verified against source before fixing):

1. ``ir.types.thinking_budget_to_effort`` returned ``"low"`` for a budget of 0,
   so a caller that explicitly DISABLED thinking by asking for a zero budget got
   ``reasoning_effort="low"`` — turning thinking ON. ``effort_to_thinking_budget(
   "none")`` already returns ``None`` for the same intent; the inverse had no
   "none" path at all, and ``thinking_budget_to_effort`` can never return
   "none". Budget 0 now maps to "none".

   NOT changed: the documented ``minimal``->1024->``low`` and
   ``max``->64000->``xhigh`` collisions. ``test_translation_enhancements.py::
   test_minimal_and_max_effort_levels`` pins them as intended ("the inverse
   keeps its pre-existing boundaries for the collisions").

2. ``wire.openai_responses.decode_request`` dropped parameters and input item
   types that its sibling codecs handle and that adapters already consume:
     - ``Request.extras`` was never populated, so every unmapped Responses
       param (``prompt_cache_key``, ``safety_identifier``, ``store``,
       ``metadata``, ``truncation``, ...) vanished even though
       ``openai_adapter`` forwards exactly those names upstream.
     - tool-result images in ``function_call_output`` were flattened to a JSON
       blob or dropped; ``ToolResultPart.images`` was never populated.
     - ``input_file`` / ``input_audio`` content blocks produced nothing, so
       Responses had no ``DocumentPart``/``AudioPart`` producer.
     - ``stop`` / ``seed`` / ``top_k`` were never read into ``GenParams``.
     - a named HOSTED ``tool_choice`` (``{"type": "web_search"}``) collapsed
       to ``None`` rather than a ``ToolChoiceNamed``.
     - a non-dict item or content block raised ``AttributeError`` -> HTTP 500;
       both sibling codecs guard with ``isinstance``.
     - ``content_filter`` reported ``status: "completed"``.

3. ``AudioPart`` had zero consumers: ``openai_chat`` decodes ``input_audio``,
   then ``openai_adapter._role_parts_to_content`` silently skipped the part,
   so an audio-only turn round-tripped to an empty message. The OpenAI adapter
   now re-emits ``input_audio``.

4. ``GenParams.top_k`` was never emitted by the Gemini adapter despite the IR
   comment naming Gemini as a supported surface.

Unsupported input items now log a warning and are skipped (never rejected),
matching the existing A1/A2 carve-out policy.
"""

from __future__ import annotations

import pytest

from wiwi.ir import types as ir
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.wire import openai_responses as orp

# ---------------------------------------------------------------------------
# 1. thinking budget <-> reasoning effort
# ---------------------------------------------------------------------------


def test_budget_zero_maps_to_none_not_low():
    """A zero budget means 'thinking disabled', not the lowest effort."""
    assert ir.thinking_budget_to_effort(0) == "none"
    assert ir.effort_to_thinking_budget("none") is None


def test_budget_zero_disables_thinking_end_to_end():
    """thinking_budget=0 must not switch reasoning ON for an OpenAI upstream."""
    req = ir.Request(
        model="gpt-5",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=0, max_tokens=64),
    )
    body = OpenAIAdapter().encode_request(
        req, "gpt-5", {"provider_type": "openai"})
    assert body.get("reasoning_effort") in (None, "none")


def test_budget_zero_gemini_disables_thinking():
    req = ir.Request(
        model="gemini-2.5-pro",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=0, max_tokens=64),
    )
    body = GeminiAdapter().encode_request(req, "gemini-2.5-pro", {})
    cfg = body.get("generationConfig", {})
    assert cfg.get("thinkingConfig", {}).get("thinkingBudget") == 0


def test_documented_effort_collisions_unchanged():
    """Guard the intended behaviour so a later 'fix' cannot regress it."""
    assert ir.effort_to_thinking_budget("minimal") == 1024
    assert ir.effort_to_thinking_budget("max") == 64000
    assert ir.thinking_budget_to_effort(1024) == "low"
    assert ir.thinking_budget_to_effort(64000) == "xhigh"
    assert ir.thinking_budget_to_effort(8000) == "medium"
    assert ir.thinking_budget_to_effort(32000) == "high"


# ---------------------------------------------------------------------------
# 2. Responses decode gaps
# ---------------------------------------------------------------------------


def test_responses_populates_extras_for_unmapped_params():
    """Unmapped Responses params must survive to the adapter that forwards them."""
    req = orp.decode_request({
        "model": "gpt-5", "input": "hi",
        "prompt_cache_key": "ck-1", "safety_identifier": "si-1",
        "store": True, "metadata": {"user_id": "u1"}, "truncation": "auto",
    })
    assert req.extras["prompt_cache_key"] == "ck-1"
    assert req.extras["safety_identifier"] == "si-1"
    assert req.extras["store"] is True
    assert req.extras["metadata"] == {"user_id": "u1"}
    assert req.extras["truncation"] == "auto"


def test_responses_extras_excludes_mapped_params():
    """Already-decoded params must not be duplicated into extras."""
    req = orp.decode_request({
        "model": "gpt-5", "input": "hi", "temperature": 0.5,
        "max_output_tokens": 100, "tool_choice": "auto", "stream": False,
    })
    for k in ("model", "input", "temperature", "max_output_tokens",
              "tool_choice", "stream"):
        assert k not in req.extras


def test_responses_extras_reaches_openai_upstream():
    """End-to-end: a Responses-only param is forwarded to an OpenAI provider."""
    req = orp.decode_request({
        "model": "gpt-5", "input": "hi", "prompt_cache_key": "ck-1",
        "safety_identifier": "si-1",
    })
    body = OpenAIAdapter().encode_request(req, "gpt-5", {})
    assert body["prompt_cache_key"] == "ck-1"
    assert body["safety_identifier"] == "si-1"


def test_responses_decodes_tool_result_images():
    """Screenshots in a function_call_output must reach ToolResultPart.images."""
    req = orp.decode_request({
        "model": "gpt-5",
        "input": [{
            "type": "function_call_output",
            "call_id": "c1",
            "output": [
                {"type": "output_text", "text": "done"},
                {"type": "input_image", "image_url": "https://x/y.png"},
            ],
        }],
    })
    msg = req.messages[-1]
    assert msg.role == "tool"
    part = msg.parts[0]
    assert part.content == "done"
    assert len(part.images) == 1
    assert part.images[0].url == "https://x/y.png"


def test_responses_decodes_tool_result_image_data_url():
    req = orp.decode_request({
        "model": "gpt-5",
        "input": [{
            "type": "function_call_output", "call_id": "c1",
            "output": [{"type": "input_image",
                        "image_url": "data:image/jpeg;base64,AAAA"}],
        }],
    })
    img = req.messages[-1].parts[0].images[0]
    assert img.b64 == "AAAA"
    assert img.mime == "image/jpeg"


def test_responses_decodes_input_file_to_document_part():
    req = orp.decode_request({
        "model": "gpt-5",
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_file", "file_data": "data:application/pdf;base64,JVBERi0=",
             "filename": "spec.pdf"},
        ]}],
    })
    parts = req.messages[-1].parts
    assert any(isinstance(p, ir.DocumentPart) for p in parts)
    doc = next(p for p in parts if isinstance(p, ir.DocumentPart))
    assert doc.b64 == "JVBERi0="
    assert doc.mime == "application/pdf"
    assert doc.name == "spec.pdf"


def test_responses_decodes_input_audio_to_audio_part():
    req = orp.decode_request({
        "model": "gpt-5",
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": "SUQz",
                                                    "format": "mp3"}},
        ]}],
    })
    parts = req.messages[-1].parts
    assert any(isinstance(p, ir.AudioPart) for p in parts)
    audio = next(p for p in parts if isinstance(p, ir.AudioPart))
    assert audio.b64 == "SUQz"
    assert audio.mime == "audio/mp3"


def test_responses_decodes_stop_seed_top_k():
    req = orp.decode_request({
        "model": "gpt-5", "input": "hi", "stop": ["END"], "seed": 42, "top_k": 5,
    })
    assert req.gen_params.stop == ["END"]
    assert req.gen_params.seed == 42
    assert req.gen_params.top_k == 5


def test_responses_stop_accepts_bare_string():
    req = orp.decode_request({"model": "gpt-5", "input": "hi", "stop": "END"})
    assert req.gen_params.stop == ["END"]


def test_responses_hosted_tool_choice_is_named():
    """{"type": "web_search"} must survive as a named choice, not vanish."""
    req = orp.decode_request({
        "model": "gpt-5", "input": "hi",
        "tool_choice": {"type": "web_search"},
    })
    assert isinstance(req.tool_choice, ir.ToolChoiceNamed)
    assert req.tool_choice.name == "web_search"


def test_responses_allowed_tools_tool_choice_is_required():
    req = orp.decode_request({
        "model": "gpt-5", "input": "hi",
        "tool_choice": {"type": "allowed_tools", "mode": "required",
                        "tools": [{"type": "function", "name": "f"}]},
    })
    assert isinstance(req.tool_choice, ir.ToolChoiceRequired)


def test_responses_unknown_tool_choice_stays_none():
    req = orp.decode_request({"model": "gpt-5", "input": "hi",
                              "tool_choice": "bogus"})
    assert req.tool_choice is None


@pytest.mark.parametrize("bad_input", [
    [42],                                   # non-dict item
    [{"type": "message", "role": "user", "content": ["nope"]}],  # non-dict part
])
def test_responses_malformed_items_do_not_500(bad_input):
    """Malformed items are skipped, never raise AttributeError."""
    req = orp.decode_request({"model": "gpt-5", "input": bad_input})
    assert req.model == "gpt-5"


def test_responses_unknown_item_logs_warning(capsys):
    """Unsupported item types warn (visible) rather than vanishing silently.

    wiwi logs via structlog to stdout, not the stdlib logging module, so
    caplog cannot see it — capture stdout instead.
    """
    orp.decode_request({"model": "gpt-5",
                        "input": [{"type": "computer_call", "id": "cc1"}]})
    out = capsys.readouterr().out
    assert "computer_call" in out
    assert "responses_unsupported_input_item" in out


def test_responses_content_filter_reports_incomplete_not_completed():
    turn = ir.AssistantTurn(text="blocked", stop_reason="content_filter",
                            usage=ir.Usage(prompt_tokens=1, completion_tokens=1))
    body = orp.encode_response(None, turn, "gpt-5", "r1")
    assert body["status"] != "completed"
    assert "error" in body or body["status"] == "incomplete"


def test_responses_length_still_reports_incomplete():
    turn = ir.AssistantTurn(text="trunc", stop_reason="length",
                            usage=ir.Usage(prompt_tokens=1, completion_tokens=1))
    body = orp.encode_response(None, turn, "gpt-5", "r1")
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}


def test_responses_normal_stop_still_completed():
    turn = ir.AssistantTurn(text="ok", usage=ir.Usage())
    assert orp.encode_response(None, turn, "gpt-5", "r1")["status"] == "completed"


# ---------------------------------------------------------------------------
# 3. AudioPart round-trip through the OpenAI adapter
# ---------------------------------------------------------------------------


def test_openai_adapter_emits_input_audio():
    """AudioPart had zero consumers: it was decoded then silently skipped."""
    req = ir.Request(
        model="gpt-4o-audio",
        messages=[ir.Message(role="user", parts=[
            ir.TextPart("what is this?"),
            ir.AudioPart(b64="SUQz", mime="audio/mp3"),
        ])],
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o-audio", {})
    content = body["messages"][0]["content"]
    assert isinstance(content, list)
    audio = [c for c in content if c.get("type") == "input_audio"]
    assert audio, content
    assert audio[0]["input_audio"]["data"] == "SUQz"
    assert audio[0]["input_audio"]["format"] == "mp3"


def test_openai_adapter_audio_only_message_is_not_empty():
    """An audio-only turn must not degrade to content: ""."""
    req = ir.Request(
        model="gpt-4o-audio",
        messages=[ir.Message(role="user", parts=[ir.AudioPart(b64="SUQz")])],
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o-audio", {})
    content = body["messages"][0]["content"]
    assert content and content != ""
    assert any(c.get("type") == "input_audio" for c in content)


def test_openai_adapter_text_only_unchanged():
    """Regression guard: plain text still emits a bare string, not a list."""
    req = ir.Request(model="gpt-4o", messages=[
        ir.Message(role="user", parts=[ir.TextPart("hi")])])
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["messages"][0]["content"] == "hi"


# ---------------------------------------------------------------------------
# 4. Gemini top_k
# ---------------------------------------------------------------------------


def test_gemini_emits_top_k():
    """IR documents top_k as Anthropic-and-Gemini; Gemini never emitted topK."""
    req = ir.Request(
        model="gemini-2.5-pro",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(top_k=7),
    )
    body = GeminiAdapter().encode_request(req, "gemini-2.5-pro", {})
    assert body["generationConfig"]["topK"] == 7


def test_gemini_omits_top_k_when_unset():
    req = ir.Request(model="gemini-2.5-pro", messages=[
        ir.Message(role="user", parts=[ir.TextPart("hi")])])
    body = GeminiAdapter().encode_request(req, "gemini-2.5-pro", {})
    assert "topK" not in body.get("generationConfig", {})
