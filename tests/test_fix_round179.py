"""Round 179 — three latent defects in ``wiwi/ir/`` and the builtin registry.

Found reading ``wiwi/ir/types.py`` and ``wiwi/ir/builtin_tools.py`` against
their consumers. None raised on a live wire path, which is why they survived:
each produced a plausible-looking result instead of an error.

1. ``GenParams.effective_thinking_budget`` ignored both explicit off-switches.
   ``thinking: {type: disabled}`` beside a ``budget_tokens`` (or
   ``reasoning_effort: "none"`` beside a direct budget) returned the budget, so
   the two consumers of the accessor (Anthropic, Gemini) turned thinking back
   ON while the sibling ``effective_reasoning_effort`` reported ``"none"``.
   Its own docstring had promised the opposite since it was written.

2. ``effective_reasoning_effort``'s docstring documented effort-before-mode
   while the code checks ``thinking_type == "disabled"`` first. The code's
   order is the correct one — an explicit off must not be re-enabled by a
   separate effort knob — so the docstring now matches it.

3. An unknown provider-hosted builtin (Anthropic ``code_execution_20250522``,
   ``bash_20250124``, …) was dropped even on an Anthropic -> Anthropic round
   trip: the encoder only consulted the canonical registry, so a client that
   asked for code execution silently got a tool-less request. The decoders now
   record the surface that declared the spelling
   (``builtin_config["_wire_surface"]``) and the Anthropic encoder re-emits it
   only for its own surface; a foreign spelling is still dropped, and the
   caller now sees a translation warning instead of only a server log.
"""

from __future__ import annotations

from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.wire import anthropic_messages as am


def _req(tools: list[ir.Tool]) -> ir.Request:
    return ir.Request(
        model="claude-x",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=tools,
    )


# ---------------------------------------------------------------------------
# 1. effective_thinking_budget honours the explicit off-switches
# ---------------------------------------------------------------------------

def test_disabled_mode_overrides_a_direct_budget():
    g = ir.GenParams(thinking_type="disabled", thinking_budget=32000)
    assert g.effective_reasoning_effort() == "none"
    assert g.effective_thinking_budget() is None


def test_explicit_reasoning_off_overrides_a_direct_budget():
    g = ir.GenParams(reasoning_effort="none", thinking_budget=8000)
    assert g.effective_thinking_budget() is None


def test_disabled_mode_overrides_an_effort_level():
    g = ir.GenParams(thinking_type="disabled", effort="high")
    assert g.effective_thinking_budget() is None


def test_direct_budget_still_wins_when_thinking_is_on():
    assert ir.GenParams(thinking_budget=8000).effective_thinking_budget() == 8000
    assert ir.GenParams(thinking_type="enabled",
                        thinking_budget=8000).effective_thinking_budget() == 8000


def test_enabled_mode_without_a_budget_still_gets_the_default_level():
    g = ir.GenParams(thinking_type="enabled")
    assert g.effective_thinking_budget() == ir.effort_to_thinking_budget("medium")


# ---------------------------------------------------------------------------
# 2. effective_reasoning_effort's documented precedence is the code's
# ---------------------------------------------------------------------------

def test_reasoning_effort_precedence_matches_the_docstring():
    # 1. explicit reasoning_effort beats everything, including "disabled"
    assert ir.GenParams(reasoning_effort="high", effort="low",
                        thinking_type="disabled").effective_reasoning_effort() == "high"
    # 2. the disabled mode beats an effort level (off is not re-enabled)
    assert ir.GenParams(effort="high",
                        thinking_type="disabled").effective_reasoning_effort() == "none"
    # 3. effort
    assert ir.GenParams(effort="high").effective_reasoning_effort() == "high"
    # 4. a budget through the inverse ladder
    assert ir.GenParams(thinking_budget=32000).effective_reasoning_effort() == "high"
    # 5. the mode as the last fallback
    assert ir.GenParams(thinking_type="enabled").effective_reasoning_effort() == "medium"


# ---------------------------------------------------------------------------
# 3. an unknown builtin survives a round trip on the surface that declared it
# ---------------------------------------------------------------------------

def test_anthropic_reemits_its_own_unknown_server_tool():
    body = {
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "code_execution_20250522", "name": "code_execution"}],
    }
    req = am.decode_request(body)
    assert req.tools[0].builtin_config[bt.WIRE_SURFACE_KEY] == "anthropic"

    out = AnthropicAdapter().encode_request(req, "claude-x", {"provider_type": "anthropic"})
    assert out["tools"] == [
        {"type": "code_execution_20250522", "name": "code_execution"}]


def test_anthropic_reemits_a_versioned_bash_tool():
    body = {
        "model": "claude-x", "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "bash_20250124", "name": "bash"}],
    }
    req = am.decode_request(body)
    out = AnthropicAdapter().encode_request(req, "claude-x", {})
    assert out["tools"] == [{"type": "bash_20250124", "name": "bash"}]


def test_anthropic_still_drops_another_surfaces_builtin():
    # A Responses spelling is meaningless to the Messages API: passthrough is
    # gated on the DECLARING surface, not on the spelling being non-empty.
    t = ir.Tool(name="file_search", builtin="file_search",
                builtin_config={bt.WIRE_TYPE_KEY: "file_search",
                                bt.WIRE_SURFACE_KEY: "openai_responses"})
    body = AnthropicAdapter().encode_request(_req([t]), "claude-x", {})
    assert "tools" not in body


def test_anthropic_warns_the_caller_when_a_builtin_is_dropped():
    adapter = AnthropicAdapter()
    t = ir.Tool(name="file_search", builtin="file_search",
                builtin_config={bt.WIRE_TYPE_KEY: "file_search",
                                bt.WIRE_SURFACE_KEY: "openai_responses"})
    adapter.encode_request(_req([t]), "claude-x", {})
    assert adapter.translation_warnings
    assert "file_search" in adapter.translation_warnings[0]


def test_other_adapters_keep_dropping_an_anthropic_unknown_builtin():
    t = ir.Tool(name="code_execution", builtin="code_execution_20250522",
                builtin_config={bt.WIRE_TYPE_KEY: "code_execution_20250522",
                                bt.WIRE_SURFACE_KEY: "anthropic"})
    assert "tools" not in GeminiAdapter().encode_request(_req([t]), "gemini-x", {})
    assert "tools" not in OpenAIAdapter().encode_request(
        _req([t]), "gpt-x", {"drop_params": True})


def test_passthrough_is_total_against_junk():
    class _Fake:
        builtin_config = "not-a-dict"

    assert bt.passthrough_wire_type("anthropic", _Fake()) is None
    assert bt.passthrough_wire_type("anthropic", ir.Tool(name="x")) is None
    t = ir.Tool(name="x", builtin="y",
                builtin_config={bt.WIRE_SURFACE_KEY: "anthropic",
                                bt.WIRE_TYPE_KEY: 7})
    assert bt.passthrough_wire_type("anthropic", t) is None
