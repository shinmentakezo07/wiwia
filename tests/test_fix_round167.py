"""Round 167: DeepSeek's thinking-mode round-trip on the OpenCode adapter (AUDIT #507).

**The bug.** OpenCode Zen's catalogue includes the ``deepseek-*`` models, and
the provider *behind* Zen is DeepSeek. DeepSeek's thinking mode documents one
hard rule (api-docs.deepseek.com/guides/thinking_mode):

> For requests carrying the ``tools`` parameter, the ``reasoning_content`` must
> be fully passed back to the API in all subsequent requests — **even for turns
> where the model did not perform a tool call**. If your code does not
> correctly pass back ``reasoning_content``, the API will return a 400 error.

The 400 reads:

    400 invalid_request_error: The reasoning_content in the thinking mode must
    be passed back to the API.

The OpenCode adapter's chat route delegates to the OpenAI adapter with
``provider_type="openai"``, which emits ``reasoning_content`` on the history
assistant turns that *have* thinking — and on no others. A router rebuilds
history from the client's own transcript, so any turn whose reasoning the
client did not echo arrives with the field missing, and that turn is exactly
the one DeepSeek rejects. The B.A.I adapter already solved this for its own
route; the OpenCode route never got the rule.

**What the fix does.** The rule now lives in one place —
``wiwi/providers/deepseek.py`` — and both adapters apply it. The OpenCode
adapter applies it on the chat route, AFTER the free-tier cloak, because the
helper decides from the tools the *upstream* will see: the cloak injects
``read``/``shell`` into the body, which makes even a tool-less caller request
tool-carrying upstream, and DeepSeek's rule keys on the tools on the wire.

**What is pinned below.**

- the empty-string pad on a history assistant turn whose reasoning was
  stripped (the 400 itself);
- the real reasoning text surviving on a turn that HAS it;
- the rule NOT firing without tools, with thinking disabled, or on a
  non-DeepSeek model — a turn that has no reasoning must not grow the field
  when the rule does not apply;
- the cloak-injected-tools case, which is why the call site is ordered after
  the cloak;
- the end-to-end shape: a Claude Code (Anthropic-dialect) client replaying a
  tool turn reaches Zen with the field on every assistant turn.
"""

from __future__ import annotations

import json

import httpx
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
from wiwi.providers import opencode_adapter as oa
from wiwi.providers.deepseek import (
    apply_deepseek_reasoning_roundtrip,
    deepseek_roundtrip_required,
    deepseek_thinking_enabled,
    is_deepseek_model,
)
from wiwi.server.app import create_app
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

DEEPSEEK = "deepseek-v4-pro"
FREE_DEEPSEEK = "deepseek-v4-flash-free"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _chat_req(*, tools: bool = True, history_reasoning: str | None = None,
              reasoning_effort: str | None = None,
              model: str = DEEPSEEK) -> ir.Request:
    """An IR request shaped like the failing one: a tool turn in the history.

    ``history_reasoning=None`` builds the assistant turn with a tool call and
    NO thinking — the exact shape the 400 is about (the field is absent).
    ``history_reasoning="..."`` gives the turn real thinking, which the OpenAI
    sub-adapter writes as ``reasoning_content``.
    """
    messages = [
        ir.Message(role="user", parts=[ir.TextPart("list the files")]),
    ]
    parts: list[ir.Part] = []
    if history_reasoning:
        parts.append(ir.ThinkingPart(history_reasoning))
    parts.append(ir.ToolUsePart(id="call_1", name="read", args={"path": "a.py"}))
    messages.append(ir.Message(role="assistant", parts=parts))
    messages.append(ir.Message(role="tool", parts=[
        ir.ToolResultPart(tool_use_id="call_1", content="print(1)")]))
    messages.append(ir.Message(role="user", parts=[ir.TextPart("and now?")]))
    req = ir.Request(model=model, messages=messages,
                     gen_params=ir.GenParams(reasoning_effort=reasoning_effort))
    if tools:
        req.tools = [ir.Tool(name="read", description="read a file",
                             parameters_json_schema={"type": "object",
                                                     "properties": {"path": {"type": "string"}}})]
    return req


def _encode(req: ir.Request, model_id: str | None = None,
            params: dict | None = None) -> dict:
    """Encode *req* through the OpenCode adapter, defaulting to ``req.model``."""
    return oa.OpencodeAdapter().encode_request(
        req, model_id or req.model, params or {})


def _assistants(body: dict) -> list[dict]:
    return [m for m in body.get("messages", []) if m.get("role") == "assistant"]


# ---------------------------------------------------------------------------
# the shared rule
# ---------------------------------------------------------------------------


def test_is_deepseek_model_matches_the_catalogue_shapes():
    assert is_deepseek_model("deepseek-v4-pro")
    assert is_deepseek_model("deepseek-reasoner")
    assert is_deepseek_model("deepseek-ai/DeepSeek-V3.2")
    assert is_deepseek_model("DeepSeek-R1")
    assert not is_deepseek_model("gpt-4o")
    assert not is_deepseek_model("claude-sonnet-5")
    assert not is_deepseek_model("glm-5.1")


def test_thinking_is_on_by_default_because_deepseek_defaults_it_on():
    """An absent effort must NOT read as "disabled" — DeepSeek thinks by default."""
    req = _chat_req()
    assert req.gen_params.effective_reasoning_effort() is None
    assert deepseek_thinking_enabled(req) is True


def test_an_explicit_disable_turns_the_thinking_rule_off():
    assert deepseek_thinking_enabled(
        _chat_req(reasoning_effort="none")) is False


def test_the_round_trip_needs_all_three_conditions():
    """DeepSeek model + tools + thinking on. Any one missing disables it."""
    assert deepseek_roundtrip_required(_chat_req(), DEEPSEEK) is True
    # no tools
    assert deepseek_roundtrip_required(_chat_req(tools=False), DEEPSEEK) is False
    # thinking disabled
    assert deepseek_roundtrip_required(
        _chat_req(reasoning_effort="none"), DEEPSEEK) is False
    # not a DeepSeek model
    assert deepseek_roundtrip_required(_chat_req(), "glm-5.1") is False


def test_the_helper_is_idempotent():
    """Applying it twice must not change the body the second time."""
    body = {"messages": [{"role": "assistant", "content": "x"}], "tools": [{}]}
    assert apply_deepseek_reasoning_roundtrip(
        body, _chat_req(), DEEPSEEK, has_tools=True) is True
    assert body["messages"][0]["reasoning_content"] == ""
    assert apply_deepseek_reasoning_roundtrip(
        body, _chat_req(), DEEPSEEK, has_tools=True) is False
    assert body["messages"][0]["reasoning_content"] == ""


def test_the_helper_ignores_a_body_without_messages():
    body = {"tools": [{}]}
    assert apply_deepseek_reasoning_roundtrip(
        body, _chat_req(), DEEPSEEK, has_tools=True) is False


def test_the_helper_leaves_non_assistant_messages_alone():
    body = {"messages": [{"role": "user", "content": "hi"},
                         {"role": "tool", "tool_call_id": "c", "content": "x"}],
            "tools": [{}]}
    apply_deepseek_reasoning_roundtrip(
        body, _chat_req(), DEEPSEEK, has_tools=True)
    assert "reasoning_content" not in body["messages"][0]
    assert "reasoning_content" not in body["messages"][1]


def test_has_tools_is_the_callers_answer_not_the_helpers():
    """The two adapters answer "does the upstream see tools?" differently.

    B.A.I adds no tools of its own, so the declared tools are the tools on
    the wire. The OpenCode adapter's free-tier cloak injects its own, so
    there the wire is the answer. The helper takes the answer as an
    argument precisely so neither adapter is forced into the other's
    reading — a second convention beside the first is prohibited.
    """
    body = {"messages": [{"role": "assistant", "content": "x"}], "tools": [{}]}
    # The caller says "no tools upstream" -> no round-trip, even though the
    # body happens to carry a tools key.
    assert apply_deepseek_reasoning_roundtrip(
        body, _chat_req(), DEEPSEEK, has_tools=False) is False
    assert "reasoning_content" not in body["messages"][0]
    # The caller says "yes" -> the round-trip applies.
    assert apply_deepseek_reasoning_roundtrip(
        body, _chat_req(), DEEPSEEK, has_tools=True) is True
    assert body["messages"][0]["reasoning_content"] == ""


def test_the_bai_route_keys_on_the_declared_tools_not_the_body():
    """B.A.I's behaviour is unchanged: it has always keyed on ``req.tools``.

    A request whose only tools are provider-hosted builtins has them all
    dropped by the Chat wire, so the body carries no tools — but B.A.I
    still asked for the round-trip, and still gets it.
    """
    from wiwi.providers.bai_adapter import BAIAdapter

    req = ir.Request(
        model=DEEPSEEK,
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")]),
            ir.Message(role="assistant", parts=[ir.TextPart("sure")]),
        ],
        tools=[ir.Tool(name="web_search", description="d",
                       builtin="web_search")],
    )
    body = BAIAdapter().encode_request(req, DEEPSEEK, {"provider_type": "bai"})
    assert not body.get("tools"), "the Chat wire cannot host a builtin"
    assistants = _assistants(body)
    assert "reasoning_content" in assistants[0], (
        "B.A.I's round-trip must keep keying on the declared tools")


# ---------------------------------------------------------------------------
# the OpenCode adapter — the 400 itself
# ---------------------------------------------------------------------------


def test_a_history_turn_whose_reasoning_was_stripped_is_padded():
    """The 400: the client rebuilt the transcript without ``reasoning_content``.

    DeepSeek validates the *presence* of the field on every history assistant
    message, so the turn is padded with an empty string.
    """
    body = _encode(_chat_req(history_reasoning=None))
    assistants = _assistants(body)
    assert len(assistants) == 1
    assert "reasoning_content" in assistants[0], (
        "DeepSeek rejects a history assistant turn without the field")
    assert assistants[0]["reasoning_content"] == ""


def test_the_real_reasoning_text_survives_on_a_turn_that_has_it():
    body = _encode(_chat_req(history_reasoning="let me call the read tool"))
    assistants = _assistants(body)
    assert assistants[0]["reasoning_content"] == "let me call the read tool"
    # the tool call itself is untouched
    assert assistants[0]["tool_calls"][0]["function"]["name"] == "read"


def test_every_assistant_turn_carries_the_field():
    """More than one history turn: ALL of them must carry it."""
    req = ir.Request(
        model=DEEPSEEK,
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("go")]),
            ir.Message(role="assistant", parts=[ir.TextPart("sure")]),
            ir.Message(role="user", parts=[ir.TextPart("again")]),
            ir.Message(role="assistant", parts=[ir.TextPart("ok")]),
        ],
        tools=[ir.Tool(name="t", description="d",
                       parameters_json_schema={"type": "object"})],
    )
    body = _encode(req)
    assistants = _assistants(body)
    assert len(assistants) == 2
    for m in assistants:
        assert "reasoning_content" in m


# ---------------------------------------------------------------------------
# the rule does not fire when it must not
# ---------------------------------------------------------------------------


def test_without_tools_the_field_is_not_forced():
    """DeepSeek ignores history reasoning without tools; older R1 400'd on it.

    The turn that HAS reasoning keeps it (the pre-existing native-OpenAI
    behaviour of this route, and DeepSeek ignores it without tools) — what
    must not happen is a turn that has NO reasoning growing an empty field.
    """
    body = _encode(_chat_req(tools=False, history_reasoning=None))
    for m in _assistants(body):
        assert "reasoning_content" not in m


def test_with_thinking_disabled_the_field_is_not_forced():
    """Thinking off means there is no reasoning to replay."""
    body = _encode(_chat_req(reasoning_effort="none", history_reasoning=None))
    for m in _assistants(body):
        assert "reasoning_content" not in m


def test_a_non_deepseek_model_is_untouched():
    """A glm model on the same route must not grow DeepSeek's field."""
    body = _encode(_chat_req(model="glm-5.1", history_reasoning=None))
    for m in _assistants(body):
        assert "reasoning_content" not in m


def test_a_free_deepseek_model_gets_the_round_trip_too():
    """The free tier is the same provider, so the same rule applies."""
    body = _encode(_chat_req(model=FREE_DEEPSEEK, history_reasoning=None))
    assistants = _assistants(body)
    assert assistants and "reasoning_content" in assistants[0]


# ---------------------------------------------------------------------------
# the cloak ordering — the tools the upstream sees are the ones that count
# ---------------------------------------------------------------------------


def test_the_cloak_injected_tools_make_the_round_trip_apply():
    """The free-tier cloak puts ``read``/``shell`` in the body.

    DeepSeek's rule keys on the tools ON THE WIRE, so a caller that declared
    no tools at all still owes the round-trip once the cloak has added them.
    This is why the adapter applies the rule AFTER the cloak.
    """
    body = _encode(_chat_req(tools=False, history_reasoning=None),
                   model_id=FREE_DEEPSEEK)
    # The cloak did add tools...
    assert body.get("tools"), "the free-tier cloak must have injected its decoys"
    # ...so the history assistant turn carries the field.
    assistants = _assistants(body)
    assert assistants and "reasoning_content" in assistants[0]


def test_the_cloak_can_be_switched_off_and_the_rule_follows_the_body():
    """With the cloak off and no caller tools, there is no round-trip."""
    body = _encode(_chat_req(tools=False, history_reasoning=None),
                   model_id=FREE_DEEPSEEK,
                   params={oa.FREE_TIER_CLOAK_PARAM: False})
    assert not body.get("tools")
    for m in _assistants(body):
        assert "reasoning_content" not in m


# ---------------------------------------------------------------------------
# the inbound dialects
# ---------------------------------------------------------------------------


def test_a_claude_code_client_replaying_a_tool_turn_reaches_zen_with_the_field():
    """The Anthropic dialect (Claude Code) is the reported caller.

    Claude Code sends ``thinking`` blocks, which the Anthropic codec lifts
    into a ``ThinkingPart``; the OpenAI sub-adapter then writes them as
    ``reasoning_content``. A turn with a tool call and no thinking block is
    the one that must be padded.
    """
    body_in = {
        "model": DEEPSEEK,
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "read",
                 "input": {"path": "a.py"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": "print(1)"}]},
        ],
        "tools": [{"name": "read", "description": "read a file",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"}}}}],
    }
    req = am.decode_request(body_in)
    body = _encode(req)
    assistants = _assistants(body)
    assert assistants, "the tool turn must be an assistant turn on the wire"
    assert "reasoning_content" in assistants[0]


def test_the_anthropic_thinking_text_is_carried_as_reasoning_content():
    body_in = {
        "model": DEEPSEEK,
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "I should call read",
                 "signature": "sig"},
                {"type": "tool_use", "id": "call_1", "name": "read",
                 "input": {"path": "a.py"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": "print(1)"}]},
        ],
        "tools": [{"name": "read", "description": "read a file",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"}}}}],
    }
    req = am.decode_request(body_in)
    body = _encode(req)
    assistants = _assistants(body)
    assert assistants[0]["reasoning_content"] == "I should call read"


def test_the_openai_dialect_keeps_the_client_reasoning_verbatim():
    body_in = {
        "model": DEEPSEEK,
        "messages": [
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": "",
             "reasoning_content": "the user wants files",
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "read",
                                          "arguments": '{"path":"a.py"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "print(1)"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "read", "description": "read a file",
            "parameters": {"type": "object",
                           "properties": {"path": {"type": "string"}}}}}],
    }
    req = oc.decode_request(body_in)
    body = _encode(req)
    assistants = _assistants(body)
    assert assistants[0]["reasoning_content"] == "the user wants files"


# ---------------------------------------------------------------------------
# the routes that must NOT be touched
# ---------------------------------------------------------------------------


def test_the_messages_route_is_not_shaped_by_the_deepseek_rule():
    """Only the chat route speaks the OpenAI Chat wire DeepSeek's rule is about."""
    req = _chat_req(history_reasoning=None)
    body = oa.OpencodeAdapter().encode_request(req, "claude-sonnet-5", {})
    # The messages route renders an Anthropic body: content blocks, not
    # ``reasoning_content``.
    assert "reasoning_content" not in json.dumps(body.get("messages", []))


# ---------------------------------------------------------------------------
# end to end: what the upstream actually receives
# ---------------------------------------------------------------------------


def _zen_cfg() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="zen", provider="opencode",
                               keys=[KeyDef(label="a", key="anonymous")])],
        model_list=[ModelEntry(
            model_name="deepseek",
            wiwi_params=DeploymentParams(provider="zen", model=DEEPSEEK))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    )


@respx.mock
async def test_the_request_that_reaches_zen_carries_the_field():
    """The whole bug, end to end: the body Zen receives is what DeepSeek checks.

    A Claude Code client replays a tool turn whose reasoning the client did
    not echo. Before the fix the assistant turn reached Zen with no
    ``reasoning_content`` and DeepSeek answered 400.
    """
    app = create_app(_zen_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post(
                "https://opencode.ai/inference/v1/chat/completions")
            route.respond(json={
                "id": "x", "object": "chat.completion", "model": DEEPSEEK,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
            r = await c.post("/v1/messages", json={
                "model": "deepseek",
                "max_tokens": 100,
                "messages": [
                    {"role": "user", "content": "list the files"},
                    {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "call_1", "name": "read",
                         "input": {"path": "a.py"}}]},
                    {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "call_1",
                         "content": "print(1)"}]},
                ],
                "tools": [{"name": "read", "description": "read a file",
                           "input_schema": {"type": "object", "properties": {
                               "path": {"type": "string"}}}}],
            }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 200, r.text
            sent = route.calls.last.request.content
            body = json.loads(sent)
            assistants = [m for m in body["messages"]
                          if m.get("role") == "assistant"]
            assert assistants, "the tool turn must be an assistant turn on the wire"
            assert "reasoning_content" in assistants[0], (
                "DeepSeek rejects a history assistant turn without the field")
            assert assistants[0]["reasoning_content"] == ""
            # the tool call itself survived the round-trip
            assert assistants[0]["tool_calls"][0]["function"]["name"] == "read"
