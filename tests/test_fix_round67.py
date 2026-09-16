"""Round 67 — Claude Code fidelity: tool search, effort, and server-tool traces.

Six defects on the Anthropic ``/v1/messages`` path, all of which made a real
Claude Code session behave differently through the proxy than against the API:

1. ``tool_search_tool_regex_20251119`` / ``_bm25_`` was dropped on EVERY route
   including Anthropic→Anthropic: ``canonical_for`` only recognized the
   ``web_search_*`` family, so the encoder logged ``dropping_unhostable_builtin``
   and the model never got a search step (every deferred tool loaded up front).
2. ``defer_loading`` was not in the IR at all — decoded nowhere, re-encoded
   nowhere — so even a correct search tool would have loaded the whole catalog.
3. ``tool_reference`` blocks nested inside a ``tool_result`` were discarded
   whenever the result also carried text: the model was told "found 1 tool" and
   never which one.
4. ``output_config.effort`` was shadowed by ``thinking.budget_tokens``, so
   ``/effort``, ``--effort`` and ``CLAUDE_CODE_EFFORT_LEVEL`` were no-ops on
   every non-Anthropic backend (8000 tokens → always "medium").
5. A provider-executed tool's result block (``web_search_tool_result``,
   ``tool_search_tool_result``) was dropped on BOTH decode paths, and its paired
   ``server_tool_use`` was re-emitted against OpenAI/Gemini backends as a
   ``tool_calls`` entry with no ``role:"tool"`` answer.
6. ``anthropic-beta`` was dropped on the opencode route to a real Messages
   endpoint, and on mid-stream resume.
"""

from __future__ import annotations

import json

import orjson
import pytest

from wiwi.ir import builtin_tools as bt
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am

# -- 1. tool_search builtin family is registered and hosted ---------------------


def test_tool_search_wire_types_map_to_canonical():
    assert bt.canonical_for("anthropic", "tool_search_tool_regex_20251119") \
        == "tool_search_regex"
    assert bt.canonical_for("anthropic", "tool_search_tool_bm25_20251119") \
        == "tool_search_bm25"
    # Responses has one generic spelling; the first-declared canonical owns it.
    assert bt.canonical_for("openai_responses", "tool_search") == "tool_search_bm25"


def test_tool_search_round_trips_to_an_anthropic_upstream():
    """The search tool reaches an Anthropic upstream instead of being dropped."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "tools": [{"type": "tool_search_tool_regex_20251119",
                   "name": "tool_search_tool_regex"}],
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.tools[0].builtin == "tool_search_regex"
    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    assert body["tools"] == [{"type": "tool_search_tool_regex_20251119",
                              "name": "tool_search_tool_regex"}]


def test_tool_search_regex_and_bm25_are_not_conflated():
    """Re-encoding bm25 as regex would tell the model to write patterns against
    a natural-language index, so the two canonicals stay distinct."""
    for wire, canonical in (("tool_search_tool_bm25_20251119", "tool_search_bm25"),
                            ("tool_search_tool_regex_20251119", "tool_search_regex")):
        req = am.decode_request({
            "model": "claude-sonnet-4-5", "max_tokens": 10,
            "tools": [{"type": wire, "name": "t"}],
            "messages": [{"role": "user", "content": "x"}]})
        assert req.tools[0].builtin == canonical
        out = AnthropicAdapter().encode_request(req, "claude-x", {})["tools"][0]
        assert out["type"] == wire


def test_tool_search_is_dropped_on_a_backend_that_cannot_host_it():
    """OpenAI Chat hosts no catalog search: drop with a warning, never mangle
    into a function tool the model would call and nothing would execute."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10,
        "tools": [{"type": "tool_search_tool_regex_20251119", "name": "t"},
                  {"name": "Bash", "description": "run",
                   "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "x"}]})
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    assert [t["function"]["name"] for t in body["tools"]] == ["Bash"]


# -- 2. defer_loading survives decode → encode ---------------------------------


@pytest.mark.parametrize("codec,body_key,tool_entry", [
    (am.decode_request, "input_schema", None),
])
def test_anthropic_decode_and_encode_defer_loading(codec, body_key, tool_entry):
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10,
        "tools": [{"name": "mcp__jira__create_issue", "description": "d",
                   "input_schema": {"type": "object"}, "defer_loading": True},
                  {"name": "Bash", "description": "run",
                   "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "x"}]})
    assert req.tools[0].defer_loading is True
    assert req.tools[1].defer_loading is None
    rendered = AnthropicAdapter().encode_request(req, "claude-x", {})["tools"]
    assert rendered[0]["defer_loading"] is True
    # Absent stays absent: the API rejects an explicit null.
    assert "defer_loading" not in rendered[1]


def test_defer_loading_is_never_rendered_on_the_search_tool():
    """The API rejects ``defer_loading`` on the search tool itself — it must
    stay loadable or nothing can be discovered."""
    req = ir.Request(model="m", messages=[ir.Message(role="user", parts=[
        ir.TextPart("x")])], tools=[
        ir.Tool(name="t", builtin="tool_search_regex",
                builtin_config={bt.WIRE_TYPE_KEY: "tool_search_tool_regex_20251119"},
                defer_loading=True)])
    entry = AnthropicAdapter().encode_request(req, "claude-x", {})["tools"][0]
    assert "defer_loading" not in entry


def test_responses_decodes_defer_loading():
    from wiwi.wire import openai_responses as orp
    req = orp.decode_request({
        "model": "gpt-x", "input": "hi",
        "tools": [{"type": "function", "name": "f", "parameters": {"type": "object"},
                   "defer_loading": True}]})
    assert req.tools[0].defer_loading is True


# -- 3. nested tool_result blocks survive --------------------------------------


def test_tool_reference_survives_a_text_bearing_tool_result():
    """The common shape — a summary sentence plus the discovered reference —
    used to keep only the sentence, so the model never learned the tool name."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10, "system": "s",
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                {"type": "text", "text": "Found 1 tool."},
                {"type": "tool_reference", "tool_name": "mcp__jira__create_issue"},
            ]}]}]})
    part = req.messages[-1].parts[0]
    assert part.content == "Found 1 tool."
    assert part.extra_blocks == [
        {"type": "tool_reference", "tool_name": "mcp__jira__create_issue"}]
    out = AnthropicAdapter().encode_request(req, "claude-x", {})["messages"][-1]
    content = out["content"][0]["content"]
    assert isinstance(content, list)
    assert {"type": "tool_reference", "tool_name": "mcp__jira__create_issue"} in content
    assert {"type": "text", "text": "Found 1 tool."} in content


def test_extra_blocks_do_not_leak_into_a_non_anthropic_backend():
    """Chat Completions has no ``tool_reference`` block; the flattened text is
    what the model gets, and no unknown block shape reaches the wire."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10, "system": "s",
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": "Found 1 tool."},
                {"type": "tool_reference", "tool_name": "x"}]}]}]})
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    msg = body["messages"][-1]
    assert msg["role"] == "tool"
    assert "tool_reference" not in json.dumps(msg)


def test_image_blocks_still_collected_alongside_extra_blocks():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10, "system": "s",
        "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": "shot"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": "AAA"}},
                {"type": "tool_reference", "tool_name": "x"}]}]}]})
    part = req.messages[-1].parts[0]
    assert len(part.images) == 1 and part.images[0].b64 == "AAA"
    assert part.extra_blocks == [{"type": "tool_reference", "tool_name": "x"}]


# -- 4. effort is not shadowed by the thinking budget --------------------------


def test_explicit_effort_outranks_the_thinking_budget():
    """Claude Code sends both on most turns; the budget used to win, so every
    non-Anthropic backend saw reasoning_effort "medium" whatever /effort said."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 32000, "system": "s",
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 8000},
        "output_config": {"effort": "high"}})
    assert req.gen_params.effective_reasoning_effort() == "high"
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    assert body["reasoning_effort"] == "high"


def test_effort_max_reaches_the_backend():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 32000, "system": "s",
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 8000},
        "output_config": {"effort": "max"}})
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    assert body["reasoning_effort"] == "max"


def test_budget_still_derives_effort_when_no_effort_was_set():
    """The budget remains the fallback — only the precedence changed."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 32000, "system": "s",
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 32000}})
    assert req.gen_params.effective_reasoning_effort() == "high"


def test_explicit_reasoning_effort_still_wins_over_anthropic_effort():
    g = ir.GenParams(reasoning_effort="low", effort="high", thinking_budget=64000)
    assert g.effective_reasoning_effort() == "low"


# -- 5. server-tool result blocks are carried, paired ---------------------------


ANTHROPIC_SEARCH_RESP = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x",
    "stop_reason": "end_turn",
    "content": [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "wiwi"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
         "content": [{"type": "web_search_result", "url": "https://x",
                      "title": "X"}]},
        {"type": "text", "text": "Answer."},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


def test_sync_decode_captures_server_result_blocks():
    turn = AnthropicAdapter().decode_response(
        200, orjson.dumps(ANTHROPIC_SEARCH_RESP))
    assert len(turn.server_blocks) == 1
    assert turn.server_blocks[0]["type"] == "web_search_tool_result"
    assert turn.server_blocks[0]["tool_use_id"] == "srvtoolu_1"


def test_sync_encode_emits_the_search_trace_paired():
    """The call is immediately followed by its result. Text may sit either side
    (the API interleaves freely); what must hold is the pairing."""
    turn = AnthropicAdapter().decode_response(
        200, orjson.dumps(ANTHROPIC_SEARCH_RESP))
    body = am.encode_response(ctx=None, turn=turn, model="claude-x", req_id="r")
    types = [b["type"] for b in body["content"]]
    assert "server_tool_use" in types
    assert "web_search_tool_result" in types
    call_at = types.index("server_tool_use")
    assert types[call_at + 1] == "web_search_tool_result"
    call = body["content"][call_at]
    result = body["content"][call_at + 1]
    assert result["tool_use_id"] == call["id"]
    assert call["name"] == "web_search"
    assert any(b.get("text") == "Answer." for b in body["content"])


def test_sync_encode_drops_an_unpaired_server_call():
    body = {"id": "m", "type": "message", "role": "assistant", "model": "c",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hi"},
                        {"type": "server_tool_use", "id": "s1",
                         "name": "web_search", "input": {}}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}
    turn = AnthropicAdapter().decode_response(200, orjson.dumps(body))
    out = am.encode_response(ctx=None, turn=turn, model="c", req_id="r")
    assert [b["type"] for b in out["content"]] == ["text"]


def test_stream_decode_emits_a_server_result_delta():
    ad = AnthropicAdapter()
    ad.reset()
    ad.decode_stream_event("content_block_start", orjson.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "server_tool_use", "id": "srvtoolu_1",
                          "name": "web_search", "input": {}}}))
    out = ad.decode_stream_event("content_block_start", orjson.dumps({
        "type": "content_block_start", "index": 1,
        "content_block": {"type": "web_search_tool_result",
                          "tool_use_id": "srvtoolu_1",
                          "content": [{"type": "web_search_result"}]}}))
    assert len(out) == 1
    assert isinstance(out[0], dl.ServerToolResultDelta)
    assert out[0].builtin == "web_search"
    assert out[0].block["type"] == "web_search_tool_result"


def test_stream_encoder_emits_the_pair_in_order():
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    frames = [
        enc.feed(dl.StreamStart("claude-x")),
        enc.feed(dl.ToolCallOpen(0, "srvtoolu_1", "web_search",
                                 builtin="web_search", block_type="server_tool_use")),
        enc.feed(dl.ToolCallArgsDelta(0, '{"query": "wiwi"}')),
        enc.feed(dl.ToolCallClose(0)),
        enc.feed(dl.ServerToolResultDelta(
            index=1, block={"type": "web_search_tool_result",
                            "tool_use_id": "srvtoolu_1", "content": []},
            builtin="web_search")),
        enc.feed(dl.UsageFinal(prompt=10, output=5)),
        enc.feed(dl.Finish("stop")),
        enc.feed(dl.StreamEnd()),
    ]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert "server_tool_use" in blob
    assert "web_search_tool_result" in blob
    assert blob.index("server_tool_use") < blob.index("web_search_tool_result")
    # The call's arguments are delivered with it, not lost. SSE-escaped JSON.
    assert "input_json_delta" in blob
    assert '\\"query\\": \\"wiwi\\"' in blob
    # Never as a client-dispatchable tool_use block.
    assert '"tool_use"' not in blob


def test_stream_encoder_still_drops_a_half_pair():
    """A call with no result keeps the old A1 behaviour: dropped entirely."""
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    frames = [
        enc.feed(dl.StreamStart("claude-x")),
        enc.feed(dl.ToolCallOpen(0, "srvtoolu_1", "web_search",
                                 builtin="web_search", block_type="server_tool_use")),
        enc.feed(dl.ToolCallClose(0)),
        enc.feed(dl.Finish("stop")),
        enc.feed(dl.StreamEnd()),
    ]
    blob = b"".join(f for f in frames if f).decode() + enc.final_frame().decode()
    assert "server_tool_use" not in blob
    assert "srvtoolu_1" not in blob


def test_stream_encoder_never_opens_a_block_inside_an_open_text_block():
    """The result block must be preceded by closing whatever was open, or the
    client sees two content_block_starts for one index."""
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    enc.feed(dl.StreamStart("claude-x"))
    enc.feed(dl.TextDelta("thinking about it"))
    blob = enc.feed(dl.ServerToolResultDelta(
        index=0, block={"type": "tool_search_tool_result",
                        "tool_use_id": "s1", "content": {}})).decode()
    # The text block (index 0) closes before the result block (index 1) opens.
    text_stop = blob.index('{"type":"content_block_stop","index":0}')
    result_start = blob.index("tool_search_tool_result")
    assert text_stop < result_start
    assert '"index":1' in blob.replace(" ", "")


def test_anthropic_history_replay_preserves_mcp_tool_use_spelling():
    """``mcp_tool_use`` must come back as ``mcp_tool_use``: its paired
    ``mcp_tool_result`` is unpaired otherwise."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10, "system": "s",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [
                {"type": "mcp_tool_use", "id": "mcptu_1", "name": "search",
                 "input": {"q": "x"}},
                {"type": "mcp_tool_result", "tool_use_id": "mcptu_1",
                 "content": [{"type": "text", "text": "hit"}]}]}]})
    msgs = AnthropicAdapter().encode_request(req, "claude-x", {})["messages"]
    assistant = msgs[-1]["content"]
    assert assistant[0]["type"] == "mcp_tool_use"
    assert assistant[1]["type"] == "mcp_tool_result"


def test_openai_wire_does_not_emit_an_unanswerable_tool_call():
    """A replayed server-tool turn must not become a tool_calls entry with no
    role:"tool" answer — OpenAI-compatible upstreams reject that history."""
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10, "system": "s",
        "messages": [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": [
                {"type": "server_tool_use", "id": "srvtoolu_1",
                 "name": "web_search", "input": {"query": "wiwi"}},
                {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                 "content": [{"type": "web_search_result", "url": "https://x"}]},
                {"type": "text", "text": "Found it."}]}]})
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    assert "tool_calls" not in assistant
    # The search payload is still visible to the model.
    assert "web_search_result" in json.dumps(assistant)
    # Every role:"tool" message answers a call that exists.
    call_ids = {tc["id"] for m in body["messages"]
                for tc in m.get("tool_calls") or []}
    for m in body["messages"]:
        if m["role"] == "tool":
            assert m["tool_call_id"] in call_ids


def test_gemini_does_not_emit_an_orphan_function_response():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 10, "system": "s",
        "messages": [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": [
                {"type": "server_tool_use", "id": "s1", "name": "web_search",
                 "input": {}},
                {"type": "web_search_tool_result", "tool_use_id": "s1",
                 "content": [{"type": "web_search_result"}]}]}]})
    body = GeminiAdapter().encode_request(req, "gem-x", {})
    rendered = json.dumps(body["contents"])
    assert "functionResponse" not in rendered
    assert "functionCall" not in rendered
    assert "web_search_result" in rendered


# -- 6. tool_choice and parallel survive an all-builtin tool list ---------------


def test_parallel_tool_calls_survives_when_every_tool_was_dropped():
    """The constraint used to be nested inside ``if encoded_tools``, so a
    request whose tools were all provider-hosted lost it silently."""
    req = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        tools=[ir.Tool(name="web_search", builtin="web_search", builtin_config={})],
        gen_params=ir.GenParams(disable_parallel_tool_use=True))
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    assert "tools" not in body  # the builtin is unhostable here
    assert body["parallel_tool_calls"] is False


def test_explicit_parallel_tool_calls_still_wins():
    req = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        gen_params=ir.GenParams(parallel_tool_calls=True,
                                disable_parallel_tool_use=True))
    body = OpenAIAdapter().encode_request(req, "gpt-x", {"provider_type": "openai"})
    assert body["parallel_tool_calls"] is True


# -- 7. input_examples reach backends with no native field ---------------------


def test_input_examples_ride_the_description_on_openai_wire():
    examples = [{"location": "SF"}, {"location": "NYC"}]
    req = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        tools=[ir.Tool(name="f", description="Get weather",
                       parameters_json_schema={"type": "object"},
                       input_examples=examples)])
    fn = OpenAIAdapter().encode_request(
        req, "gpt-x", {"provider_type": "openai"})["tools"][0]["function"]
    assert fn["description"].startswith("Get weather")
    assert json.dumps(examples) in fn["description"]


def test_input_examples_still_native_on_anthropic():
    examples = [{"location": "SF"}]
    req = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        tools=[ir.Tool(name="f", description="d",
                       parameters_json_schema={"type": "object"},
                       input_examples=examples)])
    entry = AnthropicAdapter().encode_request(req, "claude-x", {})["tools"][0]
    assert entry["input_examples"] == examples
    assert entry["description"] == "d"  # not folded into the description


# -- 8. beta forwarding follows the Messages route ------------------------------


def test_headers_forward_beta_to_an_opencode_messages_route():
    """Zen serves ``claude-*`` over a real Messages endpoint, so it needs the
    caller's betas as much as an ``anthropic`` provider does."""
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    from wiwi.providers.base import ProviderKeyRef
    from wiwi.router.router import Deployment, ProviderAccount

    def _dep(ptype: str, model_id: str) -> Deployment:
        return Deployment(
            group="g", model_id=model_id, weight=1, timeout=1.0,
            provider=ProviderAccount(name="p", provider_type=ptype, base_url=""),
        )

    ctx = RequestContext(surface="messages",
                         ir_req=ir.Request(model="m", messages=[]),
                         forward_headers={"anthropic-beta": "context-1m-2025-08-07"})
    key = ProviderKeyRef(label="k", secret="s")

    def _hdr(ptype: str, model_id: str) -> dict[str, str]:
        return Gateway._headers(AnthropicAdapter(), key, _dep(ptype, model_id), ctx)

    # Anthropic: forwarded.
    assert _hdr("anthropic", "claude-x")["anthropic-beta"] == "context-1m-2025-08-07"
    # Zen on a claude-* model: routed to Messages, so forwarded too.
    assert _hdr("opencode", "claude-sonnet-4-5")["anthropic-beta"] \
        == "context-1m-2025-08-07"
    # Zen on a non-Messages model: meaningless there, so withheld.
    assert "anthropic-beta" not in _hdr("opencode", "gpt-5-codex")
    # An unrelated provider: withheld.
    assert "anthropic-beta" not in _hdr("gemini", "gemini-2.5-pro")


def test_resume_request_context_preserves_forward_headers():
    """``_attempt_resume`` builds a fresh RequestContext; the client's headers
    must be copied into it or a beta-gated body field 400s on failover."""
    import inspect

    from wiwi.core.gateway import Gateway

    src = inspect.getsource(Gateway._attempt_resume)
    assert "forward_headers=dict(ctx.forward_headers)" in src


# -- 9. continuation drops provider-executed calls -----------------------------


def test_continuation_omits_provider_executed_calls():
    """Synthesizing a ``tool_result`` for a server tool would create the
    unpaired history the API rejects."""
    from wiwi.streaming.resume import StreamTape, build_continuation_messages

    tape = StreamTape()
    tape.append(dl.StreamStart("m"))
    tape.append(dl.ToolCallOpen(0, "srvtoolu_1", "web_search",
                                builtin="web_search", block_type="server_tool_use"))
    tape.append(dl.ToolCallArgsDelta(0, '{"query": "x"}'))
    tape.append(dl.ToolCallClose(0))
    tape.append(dl.ToolCallOpen(1, "toolu_1", "Bash"))
    tape.append(dl.ToolCallArgsDelta(1, '{"command": "ls"}'))
    tape.append(dl.ToolCallClose(1))

    msgs = build_continuation_messages(tape, [])
    assistant = next(m for m in msgs if m.role == "assistant")
    names = [p.name for p in assistant.parts if isinstance(p, ir.ToolUsePart)]
    assert names == ["Bash"]
    follow_up = next(m for m in msgs if m.role == "user")
    ids = [p.tool_use_id for p in follow_up.parts
           if isinstance(p, ir.ToolResultPart)]
    assert ids == ["toolu_1"]
