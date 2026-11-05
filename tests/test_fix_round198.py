"""Round 198 — wire review fixes for AUDIT #583-#585."""

from wiwi.ir import types as ir
from wiwi.wire import openai_responses as orp


def test_responses_input_file_id_reaches_anthropic_as_a_document():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    req = orp.decode_request({"model": "gpt", "input": [{
        "role": "user", "content": [
            {"type": "input_text", "text": "summarize this PDF"},
            {"type": "input_file", "file_id": "file-123", "filename": "spec.pdf"},
        ]}]})
    doc = next(p for p in req.messages[0].parts if isinstance(p, ir.DocumentPart))
    assert doc.file_id == "file-123"
    assert doc.name == "spec.pdf"

    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.translation_warnings = []
    body = adapter.encode_request(req, "claude", {})
    assert body["messages"][0]["content"][1] == {
        "type": "document",
        "source": {"type": "file", "file_id": "file-123"},
        "title": "spec.pdf",
    }


def test_openai_zero_token_cap_overrides_deployment_default():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    from wiwi.wire import openai_completions as ocmp

    req = ocmp.decode_request({"model": "m", "prompt": "warm cache", "max_tokens": 0})
    body = OpenAIAdapter().encode_request(req, "m", {"max_tokens": 77})
    assert body["max_tokens"] == 0


def test_zero_token_cap_is_not_dropped_without_a_deployment_default():
    from wiwi.providers.openai_adapter import OpenAIAdapter

    req = ir.Request(model="m", messages=[], gen_params=ir.GenParams(max_tokens=0))
    body = OpenAIAdapter().encode_request(req, "m", {})
    assert body["max_tokens"] == 0


def test_gemini_and_opencode_preserve_zero_token_caps():
    from wiwi.providers.gemini_adapter import GeminiAdapter
    from wiwi.providers.opencode_adapter import _encode_responses_request

    req = ir.Request(model="m", messages=[], gen_params=ir.GenParams(max_tokens=0))
    gemini = GeminiAdapter()
    gemini.translation_warnings = []
    assert gemini.encode_request(req, "m", {"max_tokens": 77})[
        "generationConfig"]["maxOutputTokens"] == 0
    opencode = _encode_responses_request(req, "m", {"max_tokens": 77})
    assert opencode["max_output_tokens"] == 0
