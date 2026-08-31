"""Round-14 regression tests: B.AI adapter respects client reasoning effort.

Regression targets (see AUDIT / review of ``wiwi/providers/bai_adapter.py``):

- ``reasoning_effort: "none"`` on a *non-DeepSeek* B.AI model was forwarded
  verbatim as ``reasoning_effort: "none"``. "none" is not a valid effort value
  on an OpenAI-compatible surface (B.AI is not native OpenAI); every other
  adapter — and B.AI's own DeepSeek arm — normalizes "none" to a thinking
  disable. The adapter must never emit an unsupported "none" effort string;
  it must express "disabled" instead.
- Named effort levels (``low``/``medium``/``high``/``xhigh``/``max``) are
  forwarded as-is on plain B.AI models; ``thinking`` is not emitted.
- The DeepSeek path (the change introduced in round 13) is unaffected.
"""

from __future__ import annotations

from wiwi.ir import types as ir
from wiwi.providers.bai_adapter import BAIAdapter


def _req(model: str = "gpt-5.5", tools: bool = False, **gen) -> ir.Request:
    msgs = [
        ir.Message(role="user", parts=[ir.TextPart("weather?")]),
        ir.Message(role="assistant", parts=[
            ir.ThinkingPart("hmm"), ir.TextPart("checking…")]),
        ir.Message(role="user", parts=[ir.TextPart("more?")]),
    ]
    req = ir.Request(model=model, messages=msgs, gen_params=ir.GenParams(**gen))
    if tools:
        req.tools = [ir.Tool(name="t", description="d",
                             parameters_json_schema={"type": "object"})]
    return req


def test_plain_bai_model_none_disables_thinking():
    """'none' is not a valid OpenAI-compatible effort value; it must become
    the thinking-disable toggle, never a forwarded 'none' string."""
    body = BAIAdapter().encode_request(
        _req(reasoning_effort="none"), "gpt-5.5", {"provider_type": "bai"})
    assert "reasoning_effort" not in body
    assert body["thinking"] == {"type": "disabled"}


def test_plain_bai_model_none_with_tools_disables_and_strips_history():
    """With tools + none, no round-trip constraint applies (thinking off), and
    the strict compatible path still strips history reasoning_content."""
    body = BAIAdapter().encode_request(
        _req(tools=True, reasoning_effort="none"),
        "gpt-5.5", {"provider_type": "bai"})
    assert "reasoning_effort" not in body
    assert body["thinking"] == {"type": "disabled"}
    for m in body["messages"]:
        assert "reasoning_content" not in m


def test_plain_bai_model_forwards_named_efforts():
    """low/medium/high pass through unchanged; no thinking key is added."""
    for effort in ("low", "medium", "high", "xhigh", "max"):
        body = BAIAdapter().encode_request(
            _req(reasoning_effort=effort), "gpt-5.5", {"provider_type": "bai"})
        assert body["reasoning_effort"] == effort
        assert "thinking" not in body


def test_plain_bai_model_no_effort_no_reasoning_keys():
    body = BAIAdapter().encode_request(_req(), "gpt-5.5", {"provider_type": "bai"})
    assert "reasoning_effort" not in body
    assert "thinking" not in body
