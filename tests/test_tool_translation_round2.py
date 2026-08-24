"""Regression tests for tool-call translation upgrades (Round 2).

Covers gaps found by reading the latest official OpenAI and Anthropic docs:

1. Anthropic wire codec: decode tool_choice "auto" and "none" (were silently dropped)
2. Anthropic wire codec: decode disable_parallel_tool_use from tool_choice object
3. Anthropic wire codec: decode strict / input_examples / cache_control from tools
4. Anthropic adapter: forward strict on tool definitions
5. Anthropic adapter: forward input_examples on tool definitions
6. Anthropic adapter: forward cache_control on tool definitions
7. Anthropic adapter: forward disable_parallel_tool_use in tool_choice
8. Anthropic adapter: emit tool_choice auto+disable_parallel when only disable is set
9. OpenAI adapter: forward strict on tool definitions
10. OpenAI adapter: map disable_parallel_tool_use to parallel_tool_calls=false
11. OpenAI Chat codec: decode parallel_tool_calls=false into disable_parallel_tool_use
12. OpenAI Responses codec: decode parallel_tool_calls=false into disable_parallel_tool_use
13. Cross-provider: OpenAI parallel_tool_calls=false → Anthropic disable_parallel_tool_use
14. Cross-provider: Anthropic disable_parallel_tool_use=true → OpenAI parallel_tool_calls=false
15. Cross-provider: OpenAI strict=true → Anthropic strict=true
16. Cross-provider: Anthropic strict=true → OpenAI strict=true
"""

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as oresp

# -- 1. Anthropic wire codec: decode tool_choice "auto" and "none" -------------

def test_anthropic_decode_tool_choice_auto():
    """tool_choice {"type": "auto"} was silently dropped (only any/tool handled)."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"},
    })
    assert isinstance(req.tool_choice, ir.ToolChoiceAuto)


def test_anthropic_decode_tool_choice_none():
    """tool_choice {"type": "none"} was silently dropped."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "none"},
    })
    assert isinstance(req.tool_choice, ir.ToolChoiceNone)


def test_anthropic_decode_tool_choice_any_still_works():
    """Regression: existing 'any' decoding must not break."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "any"},
    })
    assert isinstance(req.tool_choice, ir.ToolChoiceRequired)


def test_anthropic_decode_tool_choice_tool_still_works():
    """Regression: existing 'tool' decoding must not break."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "f"},
    })
    assert isinstance(req.tool_choice, ir.ToolChoiceNamed)
    assert req.tool_choice.name == "f"


# -- 2. Anthropic wire codec: decode disable_parallel_tool_use ----------------

def test_anthropic_decode_disable_parallel_tool_use_auto():
    """disable_parallel_tool_use inside tool_choice {type: auto}."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
    })
    assert req.gen_params.disable_parallel_tool_use is True


def test_anthropic_decode_disable_parallel_tool_use_any():
    """disable_parallel_tool_use inside tool_choice {type: any}."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
    })
    assert req.gen_params.disable_parallel_tool_use is True


def test_anthropic_decode_disable_parallel_tool_use_false():
    """disable_parallel_tool_use=False must be preserved."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": False},
    })
    assert req.gen_params.disable_parallel_tool_use is False


def test_anthropic_decode_no_disable_parallel_when_absent():
    """disable_parallel_tool_use absent → None (not False)."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"},
    })
    assert req.gen_params.disable_parallel_tool_use is None


# -- 3. Anthropic wire codec: decode strict / input_examples / cache_control --

def test_anthropic_decode_tool_strict():
    """Anthropic supports strict on tool definitions; decode into IR."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d",
                   "input_schema": {"type": "object"}, "strict": True}],
    })
    assert req.tools[0].strict is True


def test_anthropic_decode_tool_input_examples():
    """input_examples is an Anthropic-specific tool property."""
    examples = [{"location": "SF"}, {"location": "NYC"}]
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "get_weather", "description": "d",
                   "input_schema": {"type": "object"},
                   "input_examples": examples}],
    })
    assert req.tools[0].input_examples == examples


def test_anthropic_decode_tool_cache_control():
    """cache_control on a tool definition enables Anthropic prompt caching."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d",
                   "input_schema": {"type": "object"},
                   "cache_control": {"type": "ephemeral"}}],
    })
    assert req.tools[0].cache_control == {"type": "ephemeral"}


# -- 4-6. Anthropic adapter: forward strict / input_examples / cache_control ---

def test_anthropic_encode_forwards_strict():
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"}, strict=True)],
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tools"][0]["strict"] is True


def test_anthropic_encode_omits_strict_when_none():
    """strict=None must not produce a strict key."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert "strict" not in body["tools"][0]


def test_anthropic_encode_forwards_input_examples():
    examples = [{"location": "SF"}, {"location": "NYC"}]
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="get_weather", description="d",
                       parameters_json_schema={"type": "object"},
                       input_examples=examples)],
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tools"][0]["input_examples"] == examples


def test_anthropic_encode_forwards_cache_control():
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"},
                       cache_control={"type": "ephemeral"})],
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}


# -- 7-8. Anthropic adapter: disable_parallel_tool_use forwarding --------------

def test_anthropic_encode_disable_parallel_with_auto():
    """disable_parallel_tool_use + auto tool_choice."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceAuto(),
        gen_params=ir.GenParams(disable_parallel_tool_use=True),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"]["type"] == "auto"
    assert body["tool_choice"]["disable_parallel_tool_use"] is True


def test_anthropic_encode_disable_parallel_with_any():
    """disable_parallel_tool_use + any tool_choice."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceRequired(),
        gen_params=ir.GenParams(disable_parallel_tool_use=True),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"]["type"] == "any"
    assert body["tool_choice"]["disable_parallel_tool_use"] is True


def test_anthropic_encode_disable_parallel_with_named():
    """disable_parallel_tool_use + named tool_choice."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceNamed("f"),
        gen_params=ir.GenParams(disable_parallel_tool_use=True),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"]["type"] == "tool"
    assert body["tool_choice"]["name"] == "f"
    assert body["tool_choice"]["disable_parallel_tool_use"] is True


def test_anthropic_encode_disable_parallel_no_tool_choice():
    """disable_parallel_tool_use set but no explicit tool_choice → use auto as
    carrier (Anthropic requires it inside a tool_choice object)."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        gen_params=ir.GenParams(disable_parallel_tool_use=True),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"]["type"] == "auto"
    assert body["tool_choice"]["disable_parallel_tool_use"] is True


def test_anthropic_encode_no_disable_parallel_when_absent():
    """No disable_parallel_tool_use → no key in tool_choice."""
    req = ir.Request(
        model="claude",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        tool_choice=ir.ToolChoiceAuto(),
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert "disable_parallel_tool_use" not in body["tool_choice"]


# -- 9. OpenAI adapter: forward strict on tool definitions --------------------

def test_openai_encode_forwards_strict():
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"}, strict=True)],
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["tools"][0]["function"]["strict"] is True


def test_openai_encode_omits_strict_when_none():
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert "strict" not in body["tools"][0]["function"]


# -- 10. OpenAI adapter: disable_parallel_tool_use → parallel_tool_calls ------

def test_openai_encode_disable_parallel_true():
    """disable_parallel_tool_use=True → parallel_tool_calls=false."""
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        gen_params=ir.GenParams(disable_parallel_tool_use=True),
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["parallel_tool_calls"] is False


def test_openai_encode_disable_parallel_false():
    """disable_parallel_tool_use=False → parallel_tool_calls=true."""
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
        gen_params=ir.GenParams(disable_parallel_tool_use=False),
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["parallel_tool_calls"] is True


def test_openai_encode_no_disable_parallel_when_absent():
    """No disable_parallel_tool_use → parallel_tool_calls not set by it."""
    req = ir.Request(
        model="gpt-4o",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"})],
    )
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert "parallel_tool_calls" not in body


# -- 11. OpenAI Chat codec: decode parallel_tool_calls=false ------------------

def test_openai_chat_decode_parallel_false():
    """parallel_tool_calls=false must decode into disable_parallel_tool_use=True."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "parallel_tool_calls": False,
    })
    assert req.gen_params.parallel_tool_calls is False
    # parallel_tool_calls=false → parallel disabled → disable_parallel_tool_use=True
    assert req.gen_params.disable_parallel_tool_use is True


def test_openai_chat_decode_parallel_true():
    """parallel_tool_calls=true must not set disable_parallel_tool_use."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "parallel_tool_calls": True,
    })
    assert req.gen_params.parallel_tool_calls is True
    # True means parallel is allowed, so disable should be None (not False)
    assert req.gen_params.disable_parallel_tool_use is None


# -- 12. OpenAI Responses codec: decode parallel_tool_calls=false -------------

def test_openai_responses_decode_parallel_false():
    req = oresp.decode_request({
        "model": "gpt-5",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "name": "f", "parameters": {"type": "object"}}],
        "parallel_tool_calls": False,
    })
    assert req.gen_params.parallel_tool_calls is False
    # parallel_tool_calls=false → parallel disabled → disable_parallel_tool_use=True
    assert req.gen_params.disable_parallel_tool_use is True


# -- 13. Cross-provider: OpenAI → Anthropic parallel_tool_calls ---------------

def test_cross_provider_openai_to_anthropic_disable_parallel():
    """OpenAI client sends parallel_tool_calls=false, routed to Anthropic.
    The Anthropic request must have disable_parallel_tool_use=true."""
    req = oc.decode_request({
        "model": "claude-via-openai",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "parallel_tool_calls": False,
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tool_choice"]["disable_parallel_tool_use"] is True


def test_cross_provider_openai_to_anthropic_parallel_enabled():
    """OpenAI client sends parallel_tool_calls=true, routed to Anthropic.
    disable_parallel_tool_use must not be forced to True."""
    req = oc.decode_request({
        "model": "claude-via-openai",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "parallel_tool_calls": True,
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    # disable_parallel_tool_use should not be True
    tc = body.get("tool_choice", {})
    assert tc.get("disable_parallel_tool_use") is not True


# -- 14. Cross-provider: Anthropic → OpenAI disable_parallel_tool_use ---------

def test_cross_provider_anthropic_to_openai_disable_parallel():
    """Anthropic client sends disable_parallel_tool_use=true, routed to OpenAI.
    The OpenAI request must have parallel_tool_calls=false."""
    req = am.decode_request({
        "model": "gpt-via-anthropic",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
    })
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["parallel_tool_calls"] is False


# -- 15. Cross-provider: OpenAI strict → Anthropic strict ---------------------

def test_cross_provider_openai_to_anthropic_strict():
    """OpenAI strict=true on a tool def must be forwarded to Anthropic."""
    req = oc.decode_request({
        "model": "claude-via-openai",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f",
                   "parameters": {"type": "object"}, "strict": True}}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tools"][0]["strict"] is True


# -- 16. Cross-provider: Anthropic strict → OpenAI strict ---------------------

def test_cross_provider_anthropic_to_openai_strict():
    """Anthropic strict=true on a tool def must be forwarded to OpenAI."""
    req = am.decode_request({
        "model": "gpt-via-anthropic",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d",
                   "input_schema": {"type": "object"}, "strict": True}],
    })
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    assert body["tools"][0]["function"]["strict"] is True


# -- Cross-provider: Anthropic input_examples pass-through -------------------

def test_cross_provider_anthropic_input_examples_round_trip():
    """Anthropic input_examples must survive the full decode→encode cycle."""
    examples = [{"location": "SF"}, {"location": "NYC"}]
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "get_weather", "description": "d",
                   "input_schema": {"type": "object"},
                   "input_examples": examples}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tools"][0]["input_examples"] == examples


# -- Cross-provider: Anthropic cache_control on tools -------------------------

def test_cross_provider_anthropic_cache_control_round_trip():
    """cache_control on tool definitions must survive decode→encode."""
    req = am.decode_request({
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "f", "description": "d",
                   "input_schema": {"type": "object"},
                   "cache_control": {"type": "ephemeral"}}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-20250514", {})
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}


# -- OpenAI Chat codec: decode strict from function tools ---------------------

def test_openai_chat_decode_strict():
    """OpenAI Chat codec must decode strict from function tool definitions."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f",
                   "parameters": {"type": "object"}, "strict": True}}],
    })
    assert req.tools[0].strict is True


# -- OpenAI Responses codec: decode strict from function tools ----------------

def test_openai_responses_decode_strict():
    """OpenAI Responses codec must decode strict from function tool definitions."""
    req = oresp.decode_request({
        "model": "gpt-5",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "name": "f",
                   "parameters": {"type": "object"}, "strict": True}],
    })
    assert req.tools[0].strict is True
