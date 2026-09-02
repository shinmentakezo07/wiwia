"""Built-in (provider-hosted) web search tool translation.

Covers the registry (wiwi/ir/builtin_tools.py), wire decode of builtin tool
defs on the Anthropic and Responses surfaces, provider encode across
Anthropic/Gemini/OpenRouter/native-OpenAI, streaming (ToolCallOpen.builtin,
per-surface item emission/suppression), and cross-provider history replay.

Scope notes (see the plan):
- Citations/annotations are round 2 — response-side search traces are
  suppressed on Anthropic/Chat surfaces (A1) and only the Responses surface
  emits web_search_call items (self-contained, replay-safe).
- Responses input history keeps skipping web_search_call items (A2): decoding
  them would replay as an unpaired server_tool_use on an Anthropic upstream.
"""

from __future__ import annotations

import httpx
import orjson
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as resp
from wiwi.wire.anthropic_messages import decode_request as am_decode

# -- registry -------------------------------------------------------------------

def test_registry_maps_each_surface():
    assert bt.wire_type_for("anthropic", "web_search") == "web_search_20250305"
    assert bt.wire_type_for("openai_responses", "web_search") == "web_search"
    assert bt.wire_type_for("openrouter", "web_search") == "openrouter:web_search"
    assert bt.wire_type_for("gemini", "web_search") == "google_search"
    assert bt.wire_type_for("openai_chat", "web_search") is None


def test_registry_reverse_roundtrip():
    for surface in bt.SURFACES:
        wt = bt.wire_type_for(surface, "web_search")
        if wt is not None:
            assert bt.canonical_for(surface, wt) == "web_search"


def test_registry_accepts_anthropic_version_family():
    # Known older versions...
    for wt in ("web_search_20250305", "web_search_20260209", "web_search_20260318"):
        assert bt.canonical_for("anthropic", wt) == "web_search", wt
    # ...and a future/unknown versioned family member.
    assert bt.canonical_for("anthropic", "web_search_20990101") == "web_search"


def test_registry_unknown_types_are_none():
    assert bt.canonical_for("anthropic", "code_execution_20250522") is None
    assert bt.canonical_for("anthropic", "computer_20250124") is None
    assert bt.canonical_for("openai_responses", "file_search") is None
    assert bt.canonical_for("anthropic", "") is None
    assert bt.canonical_for("anthropic", "function") is None


def test_registry_is_builtin_name():
    assert bt.is_builtin_name("web_search")
    assert not bt.is_builtin_name("get_weather")
    assert not bt.is_builtin_name("web_search_20250305")  # wire type, not name


# -- Anthropic wire decode --------------------------------------------------------

def _am_body(**overrides) -> dict:
    body = {
        "model": "claude-x",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(overrides)
    return body


def test_am_decodes_builtin_web_search_tool():
    req = am_decode(_am_body(tools=[{
        "type": "web_search_20250305", "name": "web_search",
        "max_uses": 3, "allowed_domains": ["example.com"],
        "blocked_domains": ["spam.example"],
        "user_location": {"type": "approximate", "city": "Tokyo"},
    }]))
    assert len(req.tools) == 1
    t = req.tools[0]
    assert t.builtin == "web_search"
    assert t.name == "web_search"
    assert t.builtin_config == {
        "max_uses": 3, "allowed_domains": ["example.com"],
        "blocked_domains": ["spam.example"],
        "user_location": {"type": "approximate", "city": "Tokyo"},
    }
    # A builtin is NOT a function tool: no schema should ride along.
    assert t.parameters_json_schema == {"type": "object"}


def test_am_decodes_future_web_search_version():
    req = am_decode(_am_body(tools=[
        {"type": "web_search_20260901", "name": "web_search", "max_uses": 1}]))
    assert req.tools[0].builtin == "web_search"
    assert req.tools[0].builtin_config == {"max_uses": 1}


def test_am_unknown_builtin_stays_builtin_not_function():
    req = am_decode(_am_body(tools=[
        {"type": "code_execution_20250522", "name": "code_execution"}]))
    t = req.tools[0]
    assert t.builtin is not None  # builtin-shaped in the IR...
    assert t.builtin != "web_search"
    assert t.builtin_config is not None
    assert t.builtin_config[bt.WIRE_TYPE_KEY] == "code_execution_20250522"


def test_am_function_tools_unchanged():
    req = am_decode(_am_body(tools=[{
        "name": "get_weather",
        "description": "weather",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    }]))
    t = req.tools[0]
    assert t.builtin is None
    assert t.name == "get_weather"
    assert t.parameters_json_schema["properties"]


def test_am_malformed_tool_entries_do_not_crash():
    req = am_decode(_am_body(tools=[
        {"type": "web_search_20250305"},          # no name
        "not-a-dict",                              # junk entry
        {"type": "web_search_20250305", "name": "web_search", "max_uses": "lots"},
    ]))
    # The junk entry is skipped; the two dicts still decode (max_uses passes
    # through verbatim — validation belongs to the upstream provider).
    assert len(req.tools) == 2
    assert all(t.builtin == "web_search" for t in req.tools)


def test_am_history_replay_preserves_block_types_and_order():
    req = am_decode(_am_body(messages=[
        {"role": "user", "content": "search this"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me search."},
            {"type": "server_tool_use", "id": "srvtoolu_1",
             "name": "web_search", "input": {"query": "wiwi proxy"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
             "content": [{"type": "web_search_result", "url": "https://e.com",
                          "title": "E", "encrypted_index": "x"}]},
        ]},
    ]))
    assistant = req.messages[-1]
    kinds = [type(p).__name__ for p in assistant.parts]
    assert kinds == ["TextPart", "ToolUsePart", "ToolResultPart"]
    use = assistant.parts[1]
    assert use.name == "web_search"
    result = assistant.parts[2]
    assert result.block_type == "web_search_tool_result"
    assert result.tool_use_id == "srvtoolu_1"


def test_am_plain_tool_result_keeps_default_block_type():
    req = am_decode(_am_body(messages=[
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"},
        ]},
    ]))
    assert req.messages[-1].parts[0].block_type == "tool_result"


# -- OpenAI Responses wire decode ---------------------------------------------------

def _resp_body(**overrides) -> dict:
    body = {"model": "gpt-5", "input": "hi"}
    body.update(overrides)
    return body


def test_responses_decodes_web_search_tool():
    req = resp.decode_request(_resp_body(tools=[
        {"type": "web_search",
         "filters": {"allowed_domains": ["a.com"],
                     "blocked_domains": ["b.com"]},
         "search_context_size": "low",
         "user_location": {"type": "approximate", "country": "JP"}}]))
    t = req.tools[0]
    assert t.builtin == "web_search"
    assert t.name == "web_search"
    assert t.builtin_config == {
        "allowed_domains": ["a.com"], "blocked_domains": ["b.com"],
        "search_context_size": "low",
        "user_location": {"type": "approximate", "country": "JP"},
    }


def test_responses_decodes_legacy_preview_and_versioned():
    for wt in ("web_search_preview", "web_search_2025_08_26"):
        req = resp.decode_request(_resp_body(tools=[{"type": wt}]))
        assert req.tools[0].builtin == "web_search", wt


def test_responses_web_search_without_filters():
    req = resp.decode_request(_resp_body(tools=[{"type": "web_search"}]))
    assert req.tools[0].builtin == "web_search"
    # No config keys present: config carries only what the client set.
    assert req.tools[0].builtin_config == {}


def test_responses_function_tools_unchanged():
    req = resp.decode_request(_resp_body(tools=[
        {"type": "function", "name": "f", "description": "d",
         "parameters": {"type": "object"}, "strict": True}]))
    t = req.tools[0]
    assert t.builtin is None
    assert t.name == "f"
    assert t.strict is True


def test_responses_unknown_hosted_type_stays_builtin_not_function():
    req = resp.decode_request(_resp_body(tools=[{"type": "file_search"}]))
    t = req.tools[0]
    assert t.builtin is not None
    assert t.builtin_config[bt.WIRE_TYPE_KEY] == "file_search"


def test_responses_malformed_tools_do_not_crash():
    req = resp.decode_request(_resp_body(tools=[
        {"type": "web_search", "filters": "not-a-dict"},
        {"type": "web_search"},  # second entry fine
    ]))
    assert len(req.tools) == 2
    assert all(t.builtin == "web_search" for t in req.tools)


def test_responses_web_search_call_history_stays_dropped():
    # A2: web_search_call items in input history are NOT decoded into
    # ToolUseParts — they would replay as unpaired server_tool_use on an
    # Anthropic upstream once re-encoded.
    req = resp.decode_request({
        "model": "gpt-5",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "search"}]},
            {"type": "web_search_call", "id": "ws_1", "status": "completed",
             "action": {"type": "search", "query": "wiwi proxy"}},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Found things."}]},
        ],
    })
    tools_uses = [p for m in req.messages for p in m.parts
                  if isinstance(p, ir.ToolUsePart)]
    assert tools_uses == []
    # ...and the text turns survive.
    texts = [p.text for m in req.messages for p in m.parts
             if isinstance(p, ir.TextPart)]
    assert "Found things." in texts


# -- provider encode -------------------------------------------------------------

def _req(tools: list[ir.Tool], messages: list[ir.Message] | None = None) -> ir.Request:
    return ir.Request(model="m", messages=messages or [], tools=tools)


WEB_SEARCH_TOOL = ir.Tool(
    name="web_search", builtin="web_search",
    builtin_config={"max_uses": 3, "allowed_domains": ["example.com"],
                    "blocked_domains": ["spam.example"],
                    "user_location": {"type": "approximate", "city": "Tokyo"}})


def test_anthropic_encode_renders_native_web_search():
    body = AnthropicAdapter().encode_request(_req([WEB_SEARCH_TOOL]), "claude-x", {})
    assert body["tools"] == [{
        "type": "web_search_20250305", "name": "web_search",
        "max_uses": 3, "allowed_domains": ["example.com"],
        "blocked_domains": ["spam.example"],
        "user_location": {"type": "approximate", "city": "Tokyo"},
    }]
    # A builtin is never emitted with input_schema.
    assert "input_schema" not in body["tools"][0]


def test_anthropic_encode_mixes_function_and_builtin():
    fn = ir.Tool(name="f", description="d",
                 parameters_json_schema={"type": "object"})
    body = AnthropicAdapter().encode_request(
        _req([fn, WEB_SEARCH_TOOL]), "claude-x", {})
    assert len(body["tools"]) == 2
    assert body["tools"][0] == {"name": "f", "description": "d",
                                "input_schema": {"type": "object"}}
    assert body["tools"][1]["type"] == "web_search_20250305"


def test_anthropic_encode_drops_unhostable_builtin_with_warning(capsys):
    unknown = ir.Tool(name="code_execution", builtin="code_execution_20250522",
                      builtin_config={bt.WIRE_TYPE_KEY: "code_execution_20250522"})
    body = AnthropicAdapter().encode_request(_req([unknown]), "claude-x", {})
    assert "tools" not in body  # dropped, not mangled
    # structlog renders to stdout (caplog doesn't see the chain).
    rendered = capsys.readouterr().out
    assert "code_execution" in rendered


def test_anthropic_encode_cache_control_on_builtin():
    t = ir.Tool(name="web_search", builtin="web_search",
                builtin_config={}, cache_control={"type": "ephemeral"})
    body = AnthropicAdapter().encode_request(_req([t]), "claude-x", {})
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_encode_history_replays_server_tool_use_pair():
    """Inbound Anthropic history with server_tool_use + web_search_tool_result
    must re-emit both blocks, paired, in order — plain tool_use would be
    rejected by Anthropic's history validation."""
    req = am_decode(_am_body(messages=[
        {"role": "user", "content": "search this"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me search."},
            {"type": "server_tool_use", "id": "srvtoolu_1",
             "name": "web_search", "input": {"query": "wiwi proxy"}},
            {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
             "content": [{"type": "web_search_result", "url": "https://e.com",
                          "title": "E"}]},
        ]},
    ]))
    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    blocks = body["messages"][-1]["content"]
    assert [b["type"] for b in blocks] == [
        "text", "server_tool_use", "web_search_tool_result"]
    assert blocks[1]["name"] == "web_search"
    assert blocks[2]["tool_use_id"] == "srvtoolu_1"


def test_anthropic_encode_plain_tool_history_unchanged():
    """Regular client tool calls still emit plain tool_use/tool_result."""
    req = am_decode(_am_body(messages=[
        {"role": "user", "content": "run it"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_9", "name": "f",
             "input": {"x": 1}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_9", "content": "42"}]},
    ]))
    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"


def test_gemini_encode_renders_google_search_sibling():
    fn = ir.Tool(name="f", description="d",
                 parameters_json_schema={"type": "object"})
    body = GeminiAdapter().encode_request(
        _req([fn, WEB_SEARCH_TOOL]), "gemini-x", {})
    assert body["tools"] == [
        {"functionDeclarations": [{"name": "f", "description": "d",
                                   "parameters": {"type": "object"}}]},
        {"google_search": {}},   # config dropped: Gemini takes {} only
    ]


def test_gemini_encode_drops_unhostable_builtin():
    unknown = ir.Tool(name="code_execution", builtin="code_execution_20250522",
                      builtin_config={bt.WIRE_TYPE_KEY: "code_execution_20250522"})
    body = GeminiAdapter().encode_request(_req([unknown]), "gemini-x", {})
    assert "tools" not in body


def test_openai_chat_encode_drops_builtin_with_warning(capsys):
    body = OpenAIAdapter().encode_request(
        _req([WEB_SEARCH_TOOL]), "gpt-x", {"drop_params": True})
    assert "tools" not in body  # dropped, not mangled into a function tool
    rendered = capsys.readouterr().out
    assert "web_search" in rendered


def test_openai_chat_encode_drop_params_independent(caplog):
    """Builtin drop is capability-driven, not drop_params-driven: same
    behavior with drop_params=False."""
    body = OpenAIAdapter().encode_request(
        _req([WEB_SEARCH_TOOL]), "gpt-x", {"drop_params": False})
    assert "tools" not in body


def test_openai_chat_encode_function_tools_unchanged():
    fn = ir.Tool(name="f", description="d",
                 parameters_json_schema={"type": "object"}, strict=True)
    body = OpenAIAdapter().encode_request(_req([fn]), "gpt-x", {})
    assert body["tools"] == [{"type": "function", "function": {
        "name": "f", "description": "d", "parameters": {"type": "object"},
        "strict": True}}]


def test_openai_chat_web_search_options_extras_still_forward():
    req = ir.Request(model="m", messages=[], tools=[],
                     extras={"web_search_options": {"search_context_size": "low"}})
    body = OpenAIAdapter().encode_request(req, "gpt-x", {})
    assert body["web_search_options"] == {"search_context_size": "low"}


def test_openrouter_encode_hosts_web_search():
    body = OpenRouterAdapter().encode_request(
        _req([WEB_SEARCH_TOOL]), "openai/gpt-x", {})
    assert body["tools"] == [{
        "type": "openrouter:web_search",
        "parameters": {"max_uses": 3, "allowed_domains": ["example.com"],
                       "excluded_domains": ["spam.example"],
                       "user_location": {"type": "approximate", "city": "Tokyo"}},
    }]
    # search_context_size dropped? No — OpenRouter supports it; keep it when set.
    body2 = OpenRouterAdapter().encode_request(
        _req([ir.Tool(name="web_search", builtin="web_search",
                      builtin_config={"search_context_size": "low"})]),
        "openai/gpt-x", {})
    assert body2["tools"][0]["parameters"]["search_context_size"] == "low"


def test_openrouter_encode_function_tools_unchanged():
    fn = ir.Tool(name="f", description="d",
                 parameters_json_schema={"type": "object"})
    body = OpenRouterAdapter().encode_request(_req([fn]), "openai/gpt-x", {})
    assert body["tools"] == [{"type": "function", "function": {
        "name": "f", "description": "d", "parameters": {"type": "object"}}}]


# -- streaming (A1) -----------------------------------------------------------------







def test_stream_anthropic_encoder_suppresses_builtin_tool_call():
    """A1: the Anthropic surface never emits a server_tool_use block for a
    builtin call — the paired web_search_tool_result doesn't exist, so a
    half-trace would 400 on turn-2 replay. Orphan ArgsDelta/Close drop too."""
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    frames = [enc.feed(dl.StreamStart("claude-x")),
              enc.feed(dl.TextDelta("Searching...")),
              enc.feed(dl.ToolCallOpen(0, "srvtoolu_1", "web_search",
                                       builtin="web_search")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"query": "wiwi"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert "Searching..." in blob
    assert "server_tool_use" not in blob
    assert "tool_use" not in blob
    assert "srvtoolu_1" not in blob
    assert "input_json_delta" not in blob
    # stop_reason downgrade guard: tool_use -> end_turn with no tool_use block.
    assert '"end_turn"' in blob
    assert '"tool_use"' not in blob


def test_stream_anthropic_encoder_plain_tool_call_unchanged():
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    frames = [enc.feed(dl.StreamStart("claude-x")),
              enc.feed(dl.ToolCallOpen(0, "toolu_1", "get_weather")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"city": "Tokyo"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert '"tool_use"' in blob
    assert '"end_turn"' not in blob  # Finish("tool_call") maps to stop_reason tool_use


def test_stream_anthropic_encoder_mixed_builtin_and_function():
    """Suppression is per-delta: a builtin call drops, a sibling function call
    still emits — and stop_reason stays tool_use because a tool_use block
    survives."""
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    frames = [enc.feed(dl.StreamStart("claude-x")),
              enc.feed(dl.ToolCallOpen(0, "srvtoolu_1", "web_search",
                                       builtin="web_search")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"query": "x"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.ToolCallOpen(1, "toolu_1", "get_weather")),
              enc.feed(dl.ToolCallArgsDelta(1, '{"city": "Tokyo"}')),
              enc.feed(dl.ToolCallClose(1)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert "srvtoolu_1" not in blob
    assert "web_search" not in blob
    assert "get_weather" in blob
    assert '"tool_use"' in blob  # function call kept, stop_reason stays tool_use


def test_stream_chat_encoder_suppresses_builtin_tool_call():
    """A1 on the Chat surface: no phantom function frame for web_search."""
    enc = oc.ChatStreamEncoder("gpt-x", "r1")
    frames = [enc.feed(dl.StreamStart("gpt-x")),
              enc.feed(dl.TextDelta("Searching...")),
              enc.feed(dl.ToolCallOpen(0, "call_ws_1", "web_search",
                                       builtin="web_search")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"query": "wiwi"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert "Searching..." in blob
    assert "web_search" not in blob
    assert '"tool_calls"' not in blob
    assert '"finish_reason":"stop"' in blob.replace(" ", "")


def test_stream_chat_encoder_plain_tool_call_unchanged():
    enc = oc.ChatStreamEncoder("gpt-x", "r1")
    frames = [enc.feed(dl.StreamStart("gpt-x")),
              enc.feed(dl.ToolCallOpen(0, "call_1", "get_weather")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"city": "Tokyo"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert '"tool_calls"' in blob
    assert "get_weather" in blob
    assert '"finish_reason":"tool_calls"' in blob.replace(" ", "")


def test_stream_responses_encoder_emits_web_search_call():
    """The Responses surface DOES emit the search trace: web_search_call items
    are self-contained and replay-safe (A1's explicit carve-out)."""
    enc = resp.ResponsesStreamEncoder("gpt-x", "r1")
    frames = [enc.feed(dl.StreamStart("gpt-x")),
              enc.feed(dl.TextDelta("Found things.")),
              enc.feed(dl.ToolCallOpen(0, "ws_1", "web_search",
                                       builtin="web_search")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"query": "wiwi proxy"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc._completed().decode()
    compact = blob.replace(" ", "")
    assert '"web_search_call"' in compact
    # self-contained item shape: action carries the query
    assert '"query":"wiwiproxy"' in compact
    assert '"status":"completed"' in compact
    # NOT a function_call item
    assert '"function_call"' not in compact


def test_stream_responses_encoder_plain_tool_call_unchanged():
    enc = resp.ResponsesStreamEncoder("gpt-x", "r1")
    frames = [enc.feed(dl.StreamStart("gpt-x")),
              enc.feed(dl.ToolCallOpen(0, "call_1", "get_weather")),
              enc.feed(dl.ToolCallArgsDelta(0, '{"city": "Tokyo"}')),
              enc.feed(dl.ToolCallClose(0)),
              enc.feed(dl.UsageFinal(prompt=10, output=5)),
              enc.feed(dl.Finish("tool_call")),
              enc.feed(dl.StreamEnd())]
    blob = b"".join(f for f in frames if f).decode() + enc._completed().decode()
    assert '"function_call"' in blob
    assert "web_search_call" not in blob


def test_anthropic_adapter_stream_decoder_drops_web_search_tool_result_block():
    """Anthropic upstream web_search_tool_result content blocks (the results
    the provider fetched for the hosted search) stay dropped in the stream
    decode — no tool-lifecycle delta is emitted for them."""
    ad = AnthropicAdapter()
    ad.reset()
    ds = ad.decode_stream_event("content_block_start", orjson.dumps({
        "type": "content_block_start", "index": 1,
        "content_block": {"type": "web_search_tool_result",
                          "tool_use_id": "srvtoolu_1",
                          "content": [{"type": "web_search_result",
                                       "url": "https://x", "title": "x"}]}}).decode())
    ds += ad.decode_stream_event("content_block_stop", orjson.dumps({
        "type": "content_block_stop", "index": 1}).decode())
    ds += ad.decode_stream_event("message_delta", orjson.dumps({
        "type": "message_delta", "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 3}}).decode())
    # Only UsageFinal + Finish for the whole block: no Open/Args/Close, and no
    # error-class delta from the unhandled block type.
    kinds = [type(d).__name__ for d in ds]
    assert "ToolCallOpen" not in kinds
    assert "ToolCallArgsDelta" not in kinds
    assert "ToolCallClose" not in kinds
    assert "StreamError" not in kinds


def test_gemini_adapter_synthesizes_nothing_from_grounding():
    """Gemini groundingMetadata (grounded search results metadata) must not
    become a ToolUsePart — a synthesized call would set stop_reason=
    "tool_call" and invite clients to return a result the model never
    requested."""
    body = orjson.dumps({
        "candidates": [{
            "content": {"parts": [{"text": "Grounded answer."}]},
            "finishReason": "STOP",
            "groundingMetadata": {
                "searchEntryPoint": {"renderedContent": "x"},
                "groundingChunks": [{"web": {"uri": "https://x",
                                             "title": "x"}}],
                "groundingSupports": [{"segment": {"text": "answer"},
                                       "groundingChunkIndices": [0]}],
            }}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
    })
    turn = GeminiAdapter().decode_response(200, body)
    assert turn.text == "Grounded answer."
    assert turn.tool_calls == []
    assert turn.stop_reason == "stop"


def test_anthropic_adapter_stream_decoder_sets_builtin():
    """Anthropic upstream server_tool_use block start -> ToolCallOpen with
    builtin="web_search", so downstream encoders know to suppress/emit."""
    ad = AnthropicAdapter()
    ad.reset()
    ds = ad.decode_stream_event("content_block_start", orjson.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "server_tool_use", "id": "srvtoolu_1",
                          "name": "web_search", "input": {}}}).decode())
    assert len(ds) == 1
    assert ds[0].builtin == "web_search"
    # plain tool_use still decodes without the flag
    ad2 = AnthropicAdapter()
    ad2.reset()
    ds2 = ad2.decode_stream_event("content_block_start", orjson.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "toolu_2",
                          "name": "get_weather", "input": {}}}).decode())
    assert ds2[0].builtin is None


def test_anthropic_encode_response_suppresses_builtin_tool_calls():
    """Non-streaming A1: builtin tool_calls drop from the response body and
    stop_reason downgrades tool_call -> end_turn."""
    turn = ir.AssistantTurn(text="Found things.",
                            tool_calls=[ir.ToolUsePart(
                                id="srvtoolu_1", name="web_search",
                                args={"query": "wiwi"})],
                            stop_reason="tool_call")
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r9")
    blocks = [b["type"] for b in body["content"]]
    assert blocks == ["text"]
    assert body["stop_reason"] == "end_turn"


def test_chat_encode_response_suppresses_builtin_tool_calls():
    turn = ir.AssistantTurn(text="Found things.",
                            tool_calls=[ir.ToolUsePart(
                                id="call_ws", name="web_search",
                                args={"query": "wiwi"})],
                            stop_reason="tool_call")
    body = oc.encode_response(ctx=None, turn=turn, model="gpt-x", req_id="r9")
    assert "tool_calls" not in body["choices"][0]["message"]
    assert body["choices"][0]["finish_reason"] == "stop"


def test_responses_encode_response_emits_web_search_call():
    turn = ir.AssistantTurn(text="Found things.",
                            tool_calls=[ir.ToolUsePart(
                                id="ws_1", name="web_search",
                                args={"query": "wiwi proxy"})],
                            stop_reason="tool_call")
    body = resp.encode_response(ctx=None, turn=turn, model="gpt-x", req_id="r9")
    types = [o["type"] for o in body["output"]]
    assert types == ["message", "web_search_call"]
    ws = body["output"][1]
    assert ws["id"] == "ws_1"
    assert ws["status"] == "completed"
    assert ws["action"] == {"type": "search", "query": "wiwi proxy"}


# -- cross-provider matrix (respx end-to-end) --------------------------------------

WS_TOOL_ANTHROPIC = {"type": "web_search_20250305", "name": "web_search",
                     "max_uses": 3}
WS_TOOL_RESPONSES = {"type": "web_search", "search_context_size": "low"}

ANTHROPIC_UPSTREAM_BODY = {
    "id": "msg_x", "type": "message", "role": "assistant", "model": "claude-x",
    "content": [
        {"type": "text", "text": "I searched and found things."},
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "wiwi proxy"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
         "content": [{"type": "web_search_result", "url": "https://e.com",
                      "title": "E"}]},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


def _matrix_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="anth", provider="anthropic",
                        keys=[KeyDef(label="k", key="sk-ant")]),
            ProviderDef(name="gem", provider="gemini",
                        keys=[KeyDef(label="k", key="g")]),
            ProviderDef(name="orr", provider="openrouter",
                        base_url="https://openrouter.ai/api/v1",
                        keys=[KeyDef(label="k", key="or")]),
            ProviderDef(name="oai", provider="openai",
                        keys=[KeyDef(label="k", key="o")]),
        ],
        model_list=[
            ModelEntry(model_name="m-anth",
                       wiwi_params=DeploymentParams(provider="anth",
                                                    model="claude-x")),
            ModelEntry(model_name="m-gem",
                       wiwi_params=DeploymentParams(provider="gem",
                                                    model="gemini-x")),
            ModelEntry(model_name="m-orr",
                       wiwi_params=DeploymentParams(provider="orr",
                                                    model="openai/gpt-x")),
            ModelEntry(model_name="m-oai",
                       wiwi_params=DeploymentParams(provider="oai",
                                                    model="gpt-x")),
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    )


async def _matrix_client():
    app = create_app(_matrix_config())
    lm = LifespanManager(app)
    await lm.__aenter__()
    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    return lm, c


ANTHROPIC_RESP = {"id": "msg_x", "type": "message", "role": "assistant",
                  "model": "claude-x", "content": [
                      {"type": "text", "text": "searched"}],
                  "stop_reason": "end_turn",
                  "usage": {"input_tokens": 1, "output_tokens": 1}}


def _gemini_body() -> dict:
    return {"candidates": [{"content": {"parts": [{"text": "searched"}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}


def _openrouter_body() -> dict:
    return {"id": "x", "object": "chat.completion", "model": "openai/gpt-x",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "searched"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _openai_body() -> dict:
    return {"id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-x",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "searched"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@respx.mock
async def test_matrix_anthropic_client_web_search_tool_reaches_all_hosts():
    """An Anthropic-surface request with a web_search_20250305 tool def routes
    to each hostable provider with the provider's native spelling, and drops
    (with a warning) on plain OpenAI chat."""
    for model, url, expect, marker in [
        ("m-anth", "https://api.anthropic.com/v1/messages", "native",
         "web_search_20250305"),
        ("m-gem", ("https://generativelanguage.googleapis.com/v1beta/models/"
                   "gemini-x:generateContent"), "native", "google_search"),
        ("m-orr", "https://openrouter.ai/api/v1/chat/completions", "native",
         "openrouter:web_search"),
        ("m-oai", "https://api.openai.com/v1/chat/completions", "dropped",
         "web_search"),
    ]:
        lm, c = await _matrix_client()
        try:
            route = respx.post(url)
            route.respond(json=(ANTHROPIC_RESP if "anthropic" in url
                                else _gemini_body() if "googleapis" in url
                                else _openrouter_body() if "openrouter" in url
                                else _openai_body()))
            r = await c.post("/v1/messages", json={
                "model": model, "max_tokens": 64,
                "messages": [{"role": "user",
                              "content": [{"type": "text", "text": "search"}]}],
                "tools": [WS_TOOL_ANTHROPIC]},
                headers={"x-api-key": "sk-wiwi-master-test",
                         "anthropic-version": "2023-06-01"})
            assert r.status_code == 200, r.text
            sent = orjson.loads(route.calls[0].request.content)
            if expect == "native":
                assert marker in orjson.dumps(sent).decode()
            else:
                assert marker not in orjson.dumps(sent).decode()
        finally:
            await c.aclose()
            await lm.__aexit__(None, None, None)


@respx.mock
async def test_matrix_anthropic_client_receives_suppressed_trace():
    """A1 end-to-end: an Anthropic upstream that ran web_search returns its
    trace; the Anthropic-surface client gets text only (no server_tool_use
    half-pair, stop_reason downgraded)."""
    lm, c = await _matrix_client()
    try:
        route = respx.post("https://api.anthropic.com/v1/messages")
        route.respond(json=ANTHROPIC_UPSTREAM_BODY)
        r = await c.post("/v1/messages", json={
            "model": "m-anth", "max_tokens": 64,
            "messages": [{"role": "user",
                          "content": [{"type": "text", "text": "search"}]}],
            "tools": [WS_TOOL_ANTHROPIC]},
            headers={"x-api-key": "sk-wiwi-master-test",
                     "anthropic-version": "2023-06-01"})
        assert r.status_code == 200, r.text
        data = r.json()
        types = [b["type"] for b in data["content"]]
        assert types == ["text"]
        assert data["stop_reason"] == "end_turn"
        # Turn-2 replay: the client echoes our (text-only) response back.
        sent = orjson.loads(route.calls[0].request.content)
        assert sent["tools"] == [WS_TOOL_ANTHROPIC]
    finally:
        await c.aclose()
        await lm.__aexit__(None, None, None)


@respx.mock
async def test_matrix_anthropic_client_history_replay_preserves_pair():
    """Inbound REAL Anthropic history (server_tool_use + web_search_tool_result
    from the client's own earlier turn) re-encodes to the Anthropic upstream
    with both blocks, paired and in order."""
    lm, c = await _matrix_client()
    try:
        route = respx.post("https://api.anthropic.com/v1/messages")
        route.respond(json=ANTHROPIC_RESP)
        r = await c.post("/v1/messages", json={
            "model": "m-anth", "max_tokens": 64,
            "messages": [
                {"role": "user", "content": "search this"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Let me search."},
                    {"type": "server_tool_use", "id": "srvtoolu_1",
                     "name": "web_search", "input": {"query": "wiwi proxy"}},
                    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                     "content": [{"type": "web_search_result", "url": "https://e.com",
                                  "title": "E"}]},
                ]},
                {"role": "user", "content": "summarize"},
            ],
            "tools": [WS_TOOL_ANTHROPIC]},
            headers={"x-api-key": "sk-wiwi-master-test",
                     "anthropic-version": "2023-06-01"})
        assert r.status_code == 200, r.text
        sent = orjson.loads(route.calls[0].request.content)
        blocks = sent["messages"][1]["content"]
        assert [b["type"] for b in blocks] == [
            "text", "server_tool_use", "web_search_tool_result"]
    finally:
        await c.aclose()
        await lm.__aexit__(None, None, None)


@respx.mock
async def test_matrix_responses_client_web_search_reaches_all_hosts():
    """A Responses-surface (Codex-shaped) request with a web_search tool
    routes natively to each hostable provider."""
    for model, url, marker in [
        ("m-anth", "https://api.anthropic.com/v1/messages", "web_search_20250305"),
        ("m-gem", ("https://generativelanguage.googleapis.com/v1beta/models/"
                   "gemini-x:generateContent"), "google_search"),
        ("m-orr", "https://openrouter.ai/api/v1/chat/completions",
         "openrouter:web_search"),
    ]:
        lm, c = await _matrix_client()
        try:
            route = respx.post(url)
            route.respond(json=(ANTHROPIC_RESP if "anthropic" in url
                                else _gemini_body() if "googleapis" in url
                                else _openrouter_body()))
            r = await c.post("/v1/responses", json={
                "model": model,
                "input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": "search"}]}],
                "tools": [WS_TOOL_RESPONSES]},
                headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 200, r.text
            sent = orjson.loads(route.calls[0].request.content)
            assert marker in orjson.dumps(sent).decode()
        finally:
            await c.aclose()
            await lm.__aexit__(None, None, None)


@respx.mock
async def test_matrix_responses_client_gets_web_search_call_item():
    """A1 carve-out end-to-end: a Responses client whose upstream ran the
    hosted search gets a web_search_call output item."""
    lm, c = await _matrix_client()
    try:
        route = respx.post("https://api.anthropic.com/v1/messages")
        route.respond(json=ANTHROPIC_UPSTREAM_BODY)
        r = await c.post("/v1/responses", json={
            "model": "m-anth",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "search"}]}],
            "tools": [WS_TOOL_RESPONSES]},
            headers={"Authorization": "Bearer sk-wiwi-master-test"})
        assert r.status_code == 200, r.text
        data = r.json()
        types = [o["type"] for o in data["output"]]
        assert "web_search_call" in types
        ws = next(o for o in data["output"] if o["type"] == "web_search_call")
        assert ws["action"]["query"] == "wiwi proxy"
    finally:
        await c.aclose()
        await lm.__aexit__(None, None, None)


