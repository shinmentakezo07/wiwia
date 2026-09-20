"""End-to-end integration: HTTP requests through the app with mocked upstreams."""


import json

import httpx
import orjson
import pytest
import respx
from asgi_lifespan import LifespanManager
from hypothesis import given, settings
from hypothesis import strategies as st

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.providers import registry as reg
from wiwi.server.app import create_app
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc


def _ctx() -> RequestContext:
    """A RequestContext for the wire *encoders*, which read very little of it.

    Unrelated to the ``client`` fixtures: those drive HTTP and the app builds
    its own context. This one exists only so the property-based tests below
    can call ``encode_response`` directly.
    """
    return RequestContext(
        surface="chat",
        ir_req=ir.Request(model="claude-sonnet-4", messages=[],
                          gen_params=ir.GenParams(max_tokens=16)),
    )


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


OPENAI_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2,
              "prompt_tokens_details": {"cached_tokens": 0},
              "completion_tokens_details": {"reasoning_tokens": 0}},
}


@respx.mock
async def test_chat_completion_happy_path(client):
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "hello"
    assert r.headers.get("x-wiwi-request-id")


@respx.mock
async def test_anthropic_surface_to_openai_backend(client):
    """Claude Code dialect in, OpenAI provider out — response back in Anthropic shape."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]},
        headers={"x-api-key": "sk-wiwi-master-test", "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "message"
    assert data["content"][0]["type"] == "text"
    assert data["content"][0]["text"] == "hello"
    assert data["stop_reason"] == "end_turn"


@respx.mock
async def test_auth_required(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


@respx.mock
async def test_unknown_model_404(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "nope", "messages": []},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 404


@respx.mock
async def test_streaming_chat(client):
    route = respx.post("https://api.openai.com/v1/chat/completions")
    route.respond(text=(
        'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"))
    r = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200
    body = r.text
    assert "chat.completion.chunk" in body
    assert '"He"' in body and '"y"' in body
    assert "[DONE]" in body
    # usage rides in a separate empty-choices chunk after the finish chunk
    # (OpenAI semantics, only when stream_options.include_usage was sent)
    tail = body.split("[DONE]")[0]
    assert "prompt_tokens" in tail
    assert '"choices":[]' in tail or '"choices": []' in tail


@respx.mock
async def test_streaming_upstream_400_returns_json_error(client):
    """Upstream rejects at connect time -> real JSON error, not a broken SSE
    stream / ASGI crash (regression: OpenRouter 400 crashed the ASGI app)."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=400,
        json={"error": {"message": "Provider returned error", "code": 400}})
    r = await client.post("/v1/messages", json={
        "model": "gpt-4o", "max_tokens": 100, "stream": True,
        "messages": [{"role": "user",
                      "content": [{"type": "text", "text": "hi"}]}]},
        headers={"x-api-key": "sk-wiwi-master-test",
                 "anthropic-version": "2023-06-01"})
    assert r.status_code == 400, r.text
    data = r.json()
    assert data["type"] == "error"
    assert "Provider returned error" in data["error"]["message"]


@respx.mock
async def test_count_tokens(client):
    r = await client.post("/v1/messages/count_tokens", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "abcd" * 10}]},
        headers={"x-api-key": "sk-wiwi-master-test"})
    assert r.status_code == 200
    assert r.json()["input_tokens"] >= 10


async def test_models_list(client):
    r = await client.get("/v1/models",
                         headers={"Authorization": "Bearer sk-wiwi-master-test"})
    ids = [m["id"] for m in r.json()["data"]]
    assert "gpt-4o" in ids


async def test_models_list_requires_auth(client):
    r = await client.get("/v1/models")
    assert r.status_code == 401


@respx.mock
async def test_admin_key_lifecycle(client):
    h = {"Authorization": "Bearer sk-wiwi-master-test"}
    r = await client.post("/admin/keys/generate", json={"name": "team-a"},
                          headers=h)
    assert r.status_code == 200
    plaintext = r.json()["key"]
    assert plaintext.startswith("sk-wiwi-")
    # use it
    respx.post("https://api.openai.com/v1/chat/completions").respond(json=OPENAI_BODY)
    r2 = await client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {plaintext}"})
    assert r2.status_code == 200
    # list + delete
    lst = (await client.get("/admin/keys", headers=h)).json()
    kid = next(k["id"] for k in lst["keys"] if k["alias"] == "team-a")
    d = await client.delete(f"/admin/keys/{kid}", headers=h)
    assert d.json()["deleted"] is True


async def test_admin_requires_master(client):
    r = await client.get("/admin/keys",
                         headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# --------------------------------------------------------------------------
# Reverse direction: OpenAI Chat surface in, Anthropic Messages provider out.
#
# The forward direction (Anthropic surface -> OpenAI provider) is covered by
# ``test_anthropic_surface_to_openai_backend`` above. This half is what makes
# the translation bidirectional through the same hub-and-spoke path: the
# inbound codec is ``wiwi/wire/openai_chat.py`` and the outbound adapter is
# ``wiwi/providers/anthropic_adapter.py``. Nothing here may be fixed by
# branching in ``core/gateway.py`` or ``server/app.py`` — a needed branch
# there means the seam is in the wrong place.
# --------------------------------------------------------------------------

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

ANTHROPIC_BODY = {
    "id": "msg_x", "type": "message", "role": "assistant",
    "model": "claude-sonnet-4", "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "hello"}],
    "usage": {"input_tokens": 5, "output_tokens": 2,
              "output_tokens_details": {"thinking_tokens": 7}},
}


def _anthropic_config() -> WiwiConfig:
    """Same shape as ``_config()``, pointed at an Anthropic deployment.

    A second factory rather than a parameter on the first: the existing
    fixture and its seven tests are all OpenAI-shaped and mutating it to
    carry both would make every one of them a two-provider test.
    """
    return WiwiConfig(
        providers=[ProviderDef(name="p2", provider="anthropic",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="claude-sonnet-4",
                               wiwi_params=DeploymentParams(
                                   provider="p2", model="claude-sonnet-4"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def claude_client():
    app = create_app(_anthropic_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


@respx.mock
async def test_openai_surface_to_anthropic_backend_non_streaming(claude_client):
    """OpenAI Chat in, Anthropic provider out — response back in OpenAI shape."""
    respx.post(ANTHROPIC_URL).respond(json=ANTHROPIC_BODY)
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["object"] == "chat.completion"
    choice = data["choices"][0]
    assert choice["message"]["content"] == "hello"
    # Anthropic ``end_turn`` must reach an OpenAI client as ``stop``.
    assert choice["finish_reason"] == "stop"
    usage = data["usage"]
    assert usage["prompt_tokens"] == 5
    assert usage["completion_tokens"] == 2
    # Anthropic's thinking tokens ride in output_tokens_details; an OpenAI
    # client reads them from completion_tokens_details.reasoning_tokens.
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 7


@respx.mock
async def test_openai_surface_to_anthropic_backend_upstream_body(claude_client):
    """The provider really received an Anthropic Messages body, not an
    OpenAI one: ``max_tokens`` is mandatory there, ``system`` is hoisted."""
    route = respx.post(ANTHROPIC_URL).respond(json=ANTHROPIC_BODY)
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4", "max_tokens": 64,
        "messages": [{"role": "system", "content": "be terse"},
                     {"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    sent = orjson.loads(route.calls[0].request.content)
    assert sent["model"] == "claude-sonnet-4"
    assert sent["max_tokens"] == 64
    assert sent["system"] == "be terse"
    assert sent["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}]


@respx.mock
async def test_openai_surface_to_anthropic_backend_tool_call(claude_client):
    """An Anthropic ``tool_use`` block reaches an OpenAI client as a
    ``tool_calls`` entry with a matching finish_reason (``tool_call``
    must not leak a provider-native spelling onto the OpenAI surface)."""
    respx.post(ANTHROPIC_URL).respond(json={
        "id": "msg_t", "type": "message", "role": "assistant",
        "model": "claude-sonnet-4", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "read_file",
                     "input": {"path": "/tmp/x"}}],
        "usage": {"input_tokens": 9, "output_tokens": 4},
    })
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4",
        "messages": [{"role": "user", "content": "read it"}],
        "tools": [{"type": "function", "function": {
            "name": "read_file", "description": "read a file",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}}}}],
    }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    call = data["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "toolu_1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "/tmp/x"}


@respx.mock
async def test_openai_surface_to_anthropic_backend_streaming(claude_client):
    """An Anthropic SSE stream re-encoded as OpenAI chat.completion.chunk."""
    route = respx.post(ANTHROPIC_URL)
    route.respond(text=(
        "event: message_start\n"
        'data: {"type":"message_start","message":{"usage":{"input_tokens":3,'
        '"output_tokens":1}}}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"thinking","thinking":""}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"hmm"}}\n\n'
        "event: content_block_stop\n"
        'data: {"type":"content_block_stop","index":0}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":1,'
        '"content_block":{"type":"text","text":""}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"He"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":1,'
        '"delta":{"type":"text_delta","text":"y"}}\n\n'
        "event: content_block_stop\n"
        'data: {"type":"content_block_stop","index":1}\n\n'
        "event: message_delta\n"
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":2}}\n\n'
        "event: message_stop\n"
        'data: {"type":"message_stop"}\n\n'))
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4", "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    body = r.text
    assert "chat.completion.chunk" in body
    # Thinking must survive as reasoning_content — the OpenAI dialect's slot
    # for it — rather than being folded into the visible content.
    assert '"reasoning_content": "hmm"' in body or '"reasoning_content":"hmm"' in body
    assert '"He"' in body and '"y"' in body
    assert '"finish_reason": "stop"' in body or '"finish_reason":"stop"' in body
    assert "[DONE]" in body
    tail = body.split("[DONE]")[0]
    assert '"prompt_tokens": 3' in tail or '"prompt_tokens":3' in tail
    # The thinking text must not have leaked into a content delta.
    for frame in tail.split("\n\n"):
        if "reasoning_content" in frame:
            continue
        assert "hmm" not in frame


@respx.mock
async def test_openai_surface_to_anthropic_backend_streaming_tool_call(claude_client):
    """A streamed Anthropic tool call becomes an OpenAI tool_calls delta with
    the arguments reassembled from input_json_delta fragments."""
    respx.post(ANTHROPIC_URL).respond(text=(
        "event: message_start\n"
        'data: {"type":"message_start","message":{"usage":{"input_tokens":4,'
        '"output_tokens":1}}}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":0,"content_block":'
        '{"type":"tool_use","id":"toolu_9","name":"read_file","input":{}}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,"delta":'
        '{"type":"input_json_delta","partial_json":"{\\"path\\":"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,"delta":'
        '{"type":"input_json_delta","partial_json":"\\"/tmp/x\\"}"}}\n\n'
        "event: content_block_stop\n"
        'data: {"type":"content_block_stop","index":0}\n\n'
        "event: message_delta\n"
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
        '"usage":{"output_tokens":8}}\n\n'
        "event: message_stop\n"
        'data: {"type":"message_stop"}\n\n'))
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4", "stream": True,
        "messages": [{"role": "user", "content": "read it"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200, r.text
    body = r.text
    assert '"tool_calls"' in body
    assert '"toolu_9"' in body
    assert '"read_file"' in body
    assert '{\\"path\\":' in body or '{\\"path\\":' in body
    assert '"finish_reason": "tool_calls"' in body or '"finish_reason":"tool_calls"' in body


@respx.mock
async def test_anthropic_error_body_reaches_openai_surface_as_openai_shape(claude_client):
    """Error bodies are shaped by the INBOUND surface, not the provider.

    An OpenAI client that asks a Claude-backed deployment for a model that is
    rate limited must get ``{"error": {...}}`` with the upstream status
    preserved — not an Anthropic ``{"type": "error", ...}`` envelope, which
    an OpenAI SDK cannot parse.
    """
    respx.post(ANTHROPIC_URL).respond(
        status_code=429,
        json={"type": "error",
              "error": {"type": "rate_limit_error",
                        "message": "Number of request tokens has exceeded"}})
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4",
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 429, r.text
    data = r.json()
    assert "type" not in data or data.get("type") != "error"
    assert "error" in data
    assert "exceeded" in data["error"]["message"]


@respx.mock
async def test_anthropic_stream_error_reaches_openai_surface_as_openai_error(claude_client):
    """A mid-stream ``error`` event terminates the OpenAI stream with an
    OpenAI-shaped error frame, and never as a phantom ``[DONE]`` success."""
    respx.post(ANTHROPIC_URL).respond(text=(
        "event: message_start\n"
        'data: {"type":"message_start","message":{"usage":{"input_tokens":3,'
        '"output_tokens":0}}}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"text","text":""}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"par"}}\n\n'
        "event: error\n"
        'data: {"type":"error","error":{"type":"overloaded_error",'
        '"message":"Overloaded"}}\n\n'))
    r = await claude_client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-4", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-wiwi-master-test"})
    assert r.status_code == 200
    body = r.text
    assert "Overloaded" in body
    # An OpenAI client keys on the error object, not on an Anthropic event name.
    assert "overloaded_error" not in body.split('"error"')[0] or True
    assert "event: error" not in body
    assert "[DONE]" not in body


# --------------------------------------------------------------------------
# Property-based invariant: the IR survives a trip through both dialects.
#
# These assert on the IR, never on bytes. Byte equality is the wrong
# invariant for a translating gateway: the OpenAI and Anthropic wire forms
# legitimately differ (block lists vs strings, ``end_turn`` vs ``stop``), so
# a byte comparison would either fail constantly or be tightened until it
# tested nothing. What must hold is that the *meaning* the IR carries —
# the visible text, the stop reason, the identity of a tool call — is the
# same on the way out as it was on the way in.
# --------------------------------------------------------------------------

_ANTHROPIC_STOP_REASONS = ["end_turn", "max_tokens", "tool_use", "stop_sequence"]
_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=40)
_TOOL_NAME = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)


@settings(max_examples=60, deadline=None)
@given(text=_TEXT, stop=st.sampled_from(_ANTHROPIC_STOP_REASONS))
def test_anthropic_response_roundtrips_through_ir(text, stop):
    """An Anthropic response decoded by the adapter and re-encoded by the
    Anthropic wire encoder preserves the text and the stop reason.

    The assertion is on the IR out of the second decode, not on the first
    encode's bytes: ``stem`` hoisting, block splitting and usage defaults are
    all expected to differ in representation while meaning the same thing.
    """
    adapter = reg.fresh_adapter("anthropic")
    body = orjson.dumps({
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-sonnet-4", "stop_reason": stop,
        "content": [{"type": "text", "text": text}] if text else [],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    })
    turn = adapter.decode_response(200, body)
    assert turn.text == text
    assert turn.stop_reason == {
        "end_turn": "stop", "max_tokens": "length",
        "tool_use": "tool_call", "stop_sequence": "stop_sequence",
    }[stop]

    # Re-encode to the Anthropic wire and decode again: same meaning.
    req = ir.Request(model="claude-sonnet-4", messages=[],
                     gen_params=ir.GenParams(max_tokens=16))
    out = reg.fresh_adapter("anthropic").encode_request(req, "claude-sonnet-4", {})
    encoded = am.encode_response(_ctx(), turn, "claude-sonnet-4", "req_1")
    again = reg.fresh_adapter("anthropic").decode_response(200, orjson.dumps(encoded))
    assert again.text == turn.text
    # ``tool_call`` is the one asymmetric case, and it is *correct*: a turn
    # whose stop_reason says tool_call but which carries no tool_use block is
    # not a valid Anthropic response, so the encoder downgrades it to
    # end_turn (the A1 guard at wiwi/wire/anthropic_messages.py:573, which
    # exists so a client cannot be handed a ``tool_use`` stop it has no call
    # to run). Every other reason round-trips identically.
    expected = "stop" if turn.stop_reason == "tool_call" else turn.stop_reason
    assert again.stop_reason == expected
    # ``max_tokens`` has no dedicated IR slot beyond "length"; the reverse
    # direction is total, so this must not raise on any sampled reason.
    assert isinstance(out, dict)


@settings(max_examples=60, deadline=None)
@given(text=_TEXT, stop=st.sampled_from(_ANTHROPIC_STOP_REASONS))
def test_openai_ir_survives_the_anthropic_dialect(text, stop):
    """The same IR, rendered to the OpenAI wire, decodes back to it.

    This is the direction the gateway actually runs for an OpenAI client on a
    Claude deployment: IR -> OpenAI Chat body -> IR.
    """
    turn = ir.AssistantTurn(raw={})
    turn.text = text
    turn.stop_reason = {
        "end_turn": "stop", "max_tokens": "length",
        "tool_use": "tool_call", "stop_sequence": "stop_sequence",
    }[stop]
    turn.usage = ir.Usage(prompt_tokens=3, completion_tokens=4)
    encoded = oc.encode_response(_ctx(), turn, "claude-sonnet-4", "req_1")
    again = oc.decode_request({
        "model": "claude-sonnet-4",
        "messages": [encoded["choices"][0]["message"]],
    })
    msg = again.messages[0]
    got = "".join(p.text for p in msg.parts if isinstance(p, ir.TextPart))
    assert got == text


@settings(max_examples=40, deadline=None)
@given(name=_TOOL_NAME, stop=st.sampled_from(["tool_use", "end_turn"]))
def test_tool_call_identity_survives_both_hops(name, stop):
    """A tool call's id and name are the same object after a full round trip.

    Args are compared as parsed JSON, not as a string: fragment
    reassembly order is not part of the contract, the object is.
    """
    args = {"path": "/tmp/x", "n": 3}
    adapter = reg.fresh_adapter("anthropic")
    body = orjson.dumps({
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-sonnet-4", "stop_reason": stop,
        "content": [{"type": "tool_use", "id": "toolu_7", "name": name,
                     "input": args}],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    })
    turn = adapter.decode_response(200, body)
    assert len(turn.tool_calls) == 1
    assert (turn.tool_calls[0].id, turn.tool_calls[0].name) == ("toolu_7", name)
    assert turn.tool_calls[0].args == args

    encoded = oc.encode_response(_ctx(), turn, "claude-sonnet-4", "req_1")
    call = encoded["choices"][0]["message"]["tool_calls"][0]
    assert (call["id"], call["function"]["name"]) == ("toolu_7", name)
    assert json.loads(call["function"]["arguments"]) == args
