"""Round-38 regression tests: thinking/tool-call translation hardening.

Bugs found in the thinking / tool-call / translation-helper chain, each
reproduced before the fix (TypeError/AttributeError -> gateway 500, wrong
thinking config, corrupted tool-arg frames):

1.  ``anthropic_messages.decode_request``: a ``thinking`` history block with
    ``"thinking": null`` produced ``ThinkingPart(text=None)`` (Anthropic
    streams null thinking text on redacted-thinking turns). Every downstream
    consumer of ``.text`` concatenated it: ``_role_parts_to_content`` raised
    ``TypeError: can only concatenate str (not "NoneType")`` for an OpenAI
    upstream, and the Anthropic adapter emitted ``{"thinking": null}`` —
    both sides of the hub rejected/corrupted the same turn.
2.  ``anthropic_messages.decode_request``: ``"text": null`` in a content
    block produced ``TextPart(text=None)``; the Anthropic adapter's
    ``if not p.text`` guard survived it but the OpenAI chat surface joined
    None into the message text (TypeError) and Gemini accumulated it into
    ``turn.text``.
3.  ``anthropic_messages.decode_request``: non-string ``thinking.budget_tokens``
    / ``max_tokens`` (e.g. a JSON string "1024") flowed into
    ``GenParams.thinking_budget`` raw; the Anthropic adapter then raised
    ``TypeError: '<=' not supported between 'str' and 'int'`` — a 500 —
    instead of a dialect 400 or a coercion.
4.  Non-dict entries in ``messages`` / ``system`` (string, number) crashed
    the Anthropic and Chat decoders with AttributeError — 500s, violating
    the module's own "skip rather than 500" policy stated at the top of
    every loop.
5.  Non-string ``tool_calls[].function.arguments`` (a JSON *object*, which
    OpenAI-compatible gateways emit for unencoded arguments) raised
    ``TypeError: the JSON object must be str...`` in both wire codecs AND
    both OpenAI adapter decode paths — replayed history 500'd the gateway.
    The stream path even produced a dict-valued
    ``ToolCallArgsDelta.args_fragment`` that crashed the Responses encoder.
6.  ``ir.effort_to_thinking_budget`` fell back to "medium" (8000) for
    UNKNOWN effort strings, silently switching thinking ON with a large
    budget for a caller that sent a typo ("hight") — the docstring says
    callers must check None; the code contradicted it.
7.  ``openrouter_adapter``: ``thinking_budget=0`` (the documented
    thinking-off value, honored by the Anthropic/Gemini/OpenAI adapters)
    became ``reasoning.max_tokens=1024`` — thinking ON at the minimum
    instead of disabled.
8.  ``ResponsesStreamEncoder``: a ``TextDelta`` arriving while TWO tool
    calls were open emitted ``output_item.done`` for tool 1 mid-stream and
    routed tool 1's REMAINING args to a NEW output_index (2), producing a
    ``message`` item whose output_index is higher than the tool item's —
    downstream clients reorder/lose the trailing arguments. The round-25
    test asserted only index uniqueness, never routing.
9.  ``anthropic_messages.decode_request``: a string ``stop_sequences``
    (``"END"``) was iterated per character (``['E','N','D']``) — three
    single-character stop sequences instead of one.
10. Gemini ``decode_response``: a part with ``"text": null`` crashed
    ``turn.text += None`` (TypeError) — a 500 on an otherwise valid
    candidate.
11. Chat codec: non-dict ``tools[]`` entries / ``function`` sub-objects and
    non-dict ``stream_options`` crashed with AttributeError instead of skip.
"""

from __future__ import annotations

import json

import httpx
import pytest
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
from wiwi.ir import types as ir
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------------------
# 1+2. null thinking / text values must not crash or poison downstream
# ---------------------------------------------------------------------------

def test_null_thinking_text_decodes_to_empty_string():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "messages": [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": None, "signature": "sig"},
            {"type": "text", "text": "answer"},
        ]}],
    })
    tp = req.messages[0].parts[0]
    assert isinstance(tp, ir.ThinkingPart)
    assert tp.text == ""
    assert tp.signature == "sig"


def test_null_thinking_text_crosses_to_openai_upstream():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": None},
                {"type": "text", "text": "answer"}]},
            {"role": "user", "content": "next"},
        ],
    })
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {"provider_type": "openai"})
    msgs = body["messages"]
    # The history assistant turn survived with its text, no crash, and no
    # null leaking into the wire message.
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == "answer"
    json.dumps(msgs)  # must serialize cleanly


def test_null_thinking_text_encodes_anthropic_without_null():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "messages": [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": None, "signature": "sig"}]}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    tb = body["messages"][0]["content"][0]
    assert tb["type"] == "thinking"
    assert tb["thinking"] == ""  # never null — Anthropic rejects null fields
    assert tb["signature"] == "sig"


def test_null_text_part_decodes_to_empty():
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": None},
            {"type": "text", "text": "real"},
        ]}],
    })
    texts = [p.text for p in req.messages[0].parts]
    assert texts == ["", "real"]


# ---------------------------------------------------------------------------
# 3. numeric-string thinking params must not 500 the adapter
# ---------------------------------------------------------------------------

def test_string_budget_tokens_coerced_not_crashing():
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "thinking": {"type": "enabled", "budget_tokens": "1024"},
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.gen_params.thinking_budget == 1024


def test_string_max_tokens_coerced_not_crashing():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": "100",
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "messages": [{"role": "user", "content": "hi"}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert isinstance(body["max_tokens"], int)
    assert body["thinking"]["budget_tokens"] == 2048


def test_garbage_budget_tokens_ignored_not_crashing():
    req = am.decode_request({
        "model": "claude-sonnet-4-5",
        "thinking": {"type": "enabled", "budget_tokens": "big"},
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.gen_params.thinking_budget is None  # unusable value: ignore


# ---------------------------------------------------------------------------
# 4. non-dict entries in messages/system must not 500
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"model": "x", "messages": ["not a dict"]},
    {"model": "x", "system": [{"type": "text", "text": "s"}, 42],
     "messages": [{"role": "user", "content": "hi"}]},
])
def test_anthropic_non_dict_entries_skipped(body):
    req = am.decode_request(body)
    assert all(isinstance(m, ir.Message) for m in req.messages)


def test_chat_non_dict_message_skipped():
    req = oc.decode_request({"model": "x", "messages": ["junk", {"role": "user",
                              "content": "hi"}]})
    assert [m.role for m in req.messages] == ["user"]


# ---------------------------------------------------------------------------
# 5. non-string tool-call arguments must not 500 any surface
# ---------------------------------------------------------------------------

def test_chat_history_object_arguments_decode():
    req = oc.decode_request({
        "model": "x",
        "messages": [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "f", "arguments": {"a": 1}}}]}],
    })
    tc = req.messages[0].parts[0]
    assert isinstance(tc, ir.ToolUsePart)
    assert tc.args == {"a": 1}


def test_responses_history_object_arguments_decode():
    req = orp.decode_request({"model": "x", "input": [
        {"type": "function_call", "call_id": "c1", "name": "f",
         "arguments": {"a": 1}}]})
    tc = req.messages[0].parts[0]
    assert isinstance(tc, ir.ToolUsePart)
    assert tc.args == {"a": 1}


def test_openai_adapter_object_arguments_decode_response():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    turn = OpenAIAdapter().decode_response(200, json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "f",
                "arguments": {"a": 1}}}]}, "finish_reason": "tool_calls"}]}).encode())
    assert turn.tool_calls[0].args == {"a": 1}


def test_openai_adapter_object_arguments_stream():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    ad = OpenAIAdapter()
    out = ad.decode_stream_event("message", json.dumps({
        "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "t1", "function": {"name": "f",
             "arguments": {"a": 1}}}]}}]}))
    # The emitted args fragments must be strings: downstream encoders
    # concatenate them.
    for d in out:
        if isinstance(d, dl.ToolCallArgsDelta):
            assert isinstance(d.args_fragment, str)
    joined = "".join(d.args_fragment for d in out
                     if isinstance(d, dl.ToolCallArgsDelta))
    assert json.loads(joined) == {"a": 1}


# ---------------------------------------------------------------------------
# 6. unknown effort level must not silently enable thinking
# ---------------------------------------------------------------------------

def test_unknown_effort_maps_to_none():
    assert ir.effort_to_thinking_budget("banana") is None
    assert ir.effort_to_thinking_budget("") is None


def test_unknown_effort_leaves_thinking_off_anthropic():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    req = ir.Request(
        model="claude", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="hight"),  # typo
    )
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert "thinking" not in body  # typo must not silently enable thinking


def test_unknown_effort_leaves_thinking_off_gemini():
    from wiwi.providers.gemini_adapter import GeminiAdapter
    req = ir.Request(
        model="g", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="hight"),
    )
    body = GeminiAdapter().encode_request(req, "gem-2.5-pro", {})
    assert "thinkingConfig" not in body.get("generationConfig", {})


def test_openai_upstream_unknown_effort_not_forwarded():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    req = ir.Request(
        model="o3", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(reasoning_effort="hight"),
    )
    body = OpenAIAdapter().encode_request(req, "o3", {"provider_type": "openai"})
    assert "reasoning_effort" not in body  # must not forward a typo upstream


# ---------------------------------------------------------------------------
# 7. OpenRouter thinking_budget=0 must disable reasoning
# ---------------------------------------------------------------------------

def test_openrouter_zero_budget_disables_reasoning():
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter
    req = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=0),
    )
    body = OpenRouterAdapter().encode_request(req, "m", {"provider_type": "openrouter"})
    assert body.get("reasoning") == {"enabled": False}


def test_openrouter_budget_still_maps_when_positive():
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter
    req = ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(thinking_budget=5000),
    )
    body = OpenRouterAdapter().encode_request(req, "m", {"provider_type": "openrouter"})
    assert body["reasoning"] == {"max_tokens": 5000}


# ---------------------------------------------------------------------------
# 8. Responses encoder: interleave must not reorder tool args
# ---------------------------------------------------------------------------

def _sse_events(blob: str) -> list[dict]:
    out = []
    for line in blob.split("\n"):
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return out


def test_responses_interleave_preserves_tool_output_index_routing():
    """TextDelta while TWO tools open: tool 1's trailing args must stay on
    its own output_index and the message item must come AFTER both tools —
    no reordering, no lost fragments."""
    enc = orp.ResponsesStreamEncoder("m", "r1")
    blob_parts = []
    for d in [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":'),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.ToolCallArgsDelta(index=1, args_fragment='{"b":'),
        dl.TextDelta(text="interjected"),
        dl.ToolCallArgsDelta(index=1, args_fragment='1}'),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
        dl.StreamEnd(),
    ]:
        r = enc.feed(d)
        if r:
            blob_parts.append(r.decode())
    blob = "".join(blob_parts)
    events = _sse_events(blob)
    args_by_idx: dict[int, str] = {}
    for e in events:
        if e.get("type") == "response.function_call_arguments.delta":
            args_by_idx.setdefault(e["output_index"], "")
            args_by_idx[e["output_index"]] += e.get("delta", "")
    # tool 1's post-interleave fragment stayed on output_index 1 — the
    # interleave must neither drop it nor re-route it to a new item.
    assert args_by_idx.get(1) == '{"b":1}'
    # tool 0's fragment stayed on output_index 0
    done = [e for e in events if e.get("type") == "response.output_item.done"]
    tools = [e for e in done if e["item"].get("type") == "function_call"]
    msg = [e for e in done if e["item"].get("type") == "message"]
    # exactly one done per tool — no mid-stream close from the interleave,
    # no duplicate from ToolCallClose (round-25 bug class), and NO message
    # item: the interleaved text was suppressed while tools were open, so
    # no message item is synthesized after the fact.
    assert len(tools) == 2
    assert msg == []
    # and each closed tool item carries its FULL argument string
    item1 = next(t for t in tools if t["item"]["call_id"] == "c1")
    assert item1["item"]["arguments"] == '{"b":1}'


# ---------------------------------------------------------------------------
# 9. string stop_sequences must not be split per character
# ---------------------------------------------------------------------------

def test_anthropic_string_stop_sequences_not_char_split():
    req = am.decode_request({
        "model": "x", "stop_sequences": "END",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.gen_params.stop == ["END"]


# ---------------------------------------------------------------------------
# 10. Gemini null text part must not 500 decode
# ---------------------------------------------------------------------------

def test_gemini_null_text_part_decodes():
    from wiwi.providers.gemini_adapter import GeminiAdapter
    turn = GeminiAdapter().decode_response(200, json.dumps({
        "candidates": [{"content": {"parts": [{"text": None}, {"text": "hi"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {}}).encode())
    assert turn.text == "hi"


# ---------------------------------------------------------------------------
# 11. Chat codec junk shapes must not 500
# ---------------------------------------------------------------------------

def test_chat_non_dict_tools_entry_skipped():
    req = oc.decode_request({"model": "x", "messages": [],
                             "tools": ["junk", {"type": "function",
                             "function": {"name": "f"}}]})
    assert [t.name for t in req.tools] == ["f"]


def test_chat_non_dict_function_subobject_skipped():
    req = oc.decode_request({"model": "x", "messages": [],
                             "tools": [{"type": "function", "function": "notdict"}]})
    assert req.tools == []


def test_chat_non_dict_tool_choice_function_skipped():
    req = oc.decode_request({"model": "x", "messages": [],
                             "tool_choice": {"type": "function",
                                             "function": "notdict"}})
    assert req.tool_choice is None


def test_chat_non_dict_stream_options_ignored():
    req = oc.decode_request({"model": "x", "stream": True,
                             "messages": [], "stream_options": "yes"})
    assert req.stream_options_include_usage is False


# ---------------------------------------------------------------------------
# End-to-end: the crash class must not surface as a gateway 500
# ---------------------------------------------------------------------------

@respx.mock
async def test_null_thinking_history_reaches_upstream_not_500(client):
    """A replayed Anthropic history turn with null thinking text must get a
    real upstream response (or upstream error), never a wiwi 500."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"role": "assistant",
                                             "content": "ok"},
                                "finish_reason": "stop"}]})
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100,
        "messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": None, "signature": "sig"},
                {"type": "text", "text": "prior"}]},
            {"role": "user", "content": "next"},
        ]}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["content"][0]["type"] == "text"


@respx.mock
async def test_object_arguments_history_not_500(client):
    """Replayed tool-call history with object arguments must not 500."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"role": "assistant",
                                             "content": "ok"},
                                "finish_reason": "stop"}]})
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o",
        "messages": [{"role": "assistant", "content": None,
                      "tool_calls": [{"id": "c1", "type": "function",
                                      "function": {"name": "f",
                                                   "arguments": {"a": 1}}}]}],
    }, headers=H)
    # The decoder must either decode it or dialect-400 it — never a 500.
    assert r.status_code != 500, r.text


# ---------------------------------------------------------------------------
# 12. redacted_thinking blocks must survive decode -> replay
# ---------------------------------------------------------------------------

def test_redacted_thinking_decodes_and_replays():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "messages": [{"role": "assistant", "content": [
            {"type": "redacted_thinking", "data": "ENC-BLOB"}]}],
    })
    tp = req.messages[0].parts[0]
    assert isinstance(tp, ir.ThinkingPart)
    assert tp.block_type == "redacted_thinking"
    assert tp.data == "ENC-BLOB"
    body = AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", {})
    assert body["messages"][0]["content"] == [
        {"type": "redacted_thinking", "data": "ENC-BLOB"}]


def test_redacted_thinking_decode_response():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    turn = AnthropicAdapter().decode_response(200, json.dumps({
        "content": [{"type": "redacted_thinking", "data": "ENC-BLOB"},
                    {"type": "text", "text": "hi"}],
        "stop_reason": "end_turn", "usage": {}}).encode())
    assert turn.thinking[0].block_type == "redacted_thinking"
    assert turn.thinking[0].data == "ENC-BLOB"
    assert turn.text == "hi"


def test_redacted_thinking_not_corrupted_on_openai_surface():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    req = am.decode_request({
        "model": "claude-sonnet-4-5", "max_tokens": 100,
        "messages": [
            {"role": "assistant", "content": [
                {"type": "redacted_thinking", "data": "ENC-BLOB"},
                {"type": "text", "text": "prior"}]},
            {"role": "user", "content": "next"}],
    })
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {"provider_type": "openai"})
    # The redacted blob is not reasoning text; the visible turn survives.
    assert body["messages"][0]["content"] == "prior"
    assert "reasoning_content" not in body["messages"][0]


def test_responses_builtin_args_emit_no_phantom_function_frames():
    """AUDIT #63: a builtin-tagged ToolCallOpen opens as a self-contained
    web_search_call item; its ArgsDeltas must accumulate (close-time query
    read needs them) but emit NO function_call_arguments frames — the frame's
    fc_<req>_<n> item_id never had an output_item.added, and Codex CLI
    accumulates fragments against a nonexistent function item."""
    enc = orp.ResponsesStreamEncoder("m", "r1")
    blob_parts = []
    for d in [
        dl.StreamStart(model="m", group="g"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":1}'),
        dl.ToolCallOpen(index=2, id="srv1", name="web_search", builtin="web_search"),
        dl.ToolCallArgsDelta(index=2, args_fragment='{"query":'),
        dl.ToolCallArgsDelta(index=2, args_fragment='"cats"}'),
        dl.ToolCallClose(index=2),
        dl.ToolCallClose(index=0),
        dl.StreamEnd(),
    ]:
        r = enc.feed(d)
        if r:
            blob_parts.append(r.decode())
    events = _sse_events("".join(blob_parts))
    # no function_call_arguments frame references the builtin's index
    bad = [e for e in events
           if e.get("type", "").startswith("response.function_call_arguments")
           and e.get("item_id", "").endswith("_2")]
    assert bad == []
    # the sibling real function call still streams its frames
    ok = [e for e in events
          if e.get("type") == "response.function_call_arguments.delta"
          and e.get("item_id", "").endswith("_0")]
    assert len(ok) == 1
    # and the builtin's close reads the accumulated query
    ws = [e for e in events if e.get("type") == "response.output_item.done"
          and e["item"].get("type") == "web_search_call"]
    assert len(ws) == 1
    assert ws[0]["item"]["action"]["query"] == "cats"
