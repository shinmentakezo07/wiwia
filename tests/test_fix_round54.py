"""Round 54 — two decode/encode gaps that corrupt agent history.

AUDIT #124: the Chat codec's tool-arg parser only guarded the JSON-*object*
shape. A scalar-truthy ``arguments`` (``true``, ``5``, ``1.5``, ``["a"]``)
fell through ``raw_args or "{}"`` into ``json.loads``, which raises
``TypeError`` — not ``JSONDecodeError`` — so the ``except`` clause never
caught it and the whole request 500'd. The Responses codec already defends
this exact case (``_load_args`` catches ``TypeError``); the Chat codec is the
only surface that did not.

AUDIT #125: the *streaming* Anthropic encoder emits ``redacted_thinking``
blocks verbatim (#103), and the upstream adapter replays them
(``anthropic_adapter.py:337-341``), but the client-facing **sync**
``encode_response`` had no redacted branch: it rendered the opaque blob as
``{"type": "thinking", "thinking": ""}`` — the data silently dropped and the
block left unsigned, so the client's next-turn replay is 400-bait.

Both fixes are pinned at the wire boundary — decode/encode in, wire out —
because that is where the contract lives.
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
from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.server.app import create_app
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

MASTER = "sk-wiwi-master-test"


# ---------------------------------------------------------------------------
# #124 — scalar-truthy tool arguments must not 500 the Chat surface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arguments", [True, 5, 1.5, ["a"]])
def test_chat_scalar_truthy_tool_arguments_do_not_crash(arguments):
    """``json.loads`` raises TypeError on a non-string scalar.

    A client replaying history whose upstream emitted ``arguments: true``
    (or any other truthy scalar) must decode to an empty args dict rather
    than raising out of ``decode_request`` (AUDIT #124).
    """
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "f", "arguments": arguments},
            }],
        }],
    })
    part = req.messages[0].parts[0]
    assert isinstance(part, ir.ToolUsePart)
    assert part.args == {}, f"expected empty args, got {part.args!r}"
    assert part.name == "f"


def test_chat_string_tool_arguments_still_parse():
    """The guard must not disturb the spec-correct string path."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "f", "arguments": '{"city": "SF"}'},
            }],
        }],
    })
    assert req.messages[0].parts[0].args == {"city": "SF"}


def test_chat_dict_tool_arguments_still_parse():
    """The pre-existing dict guard (#39.1) must survive the new branch."""
    req = oc.decode_request({
        "model": "gpt-4o",
        "messages": [{
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "f", "arguments": {"city": "SF"}},
            }],
        }],
    })
    assert req.messages[0].parts[0].args == {"city": "SF"}


def _chat_config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


OPENAI_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}


@respx.mock
async def test_chat_surface_replayed_scalar_arguments_is_not_a_500():
    """End-to-end: the 500 the audit observed, through the real app.

    ``run_chat_like`` catches only ``(DialectError, ValueError)`` at decode,
    so a TypeError escaping the codec surfaced as an unhandled 500.
    """
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_BODY)
    app = create_app(_chat_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            r = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "assistant", "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "f", "arguments": True}}]},
                    {"role": "user", "content": "go"},
                ]},
                headers={"Authorization": f"Bearer {MASTER}"})
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text!r}"


# ---------------------------------------------------------------------------
# #125 — sync /v1/messages encode must preserve redacted_thinking
# ---------------------------------------------------------------------------

REDACTED_BLOB = "ENCRYPTED_BLOB_XYZ"


def test_sync_anthropic_encode_preserves_redacted_thinking():
    """A redacted ThinkingPart must re-emit as a ``redacted_thinking`` block.

    Pre-fix the sync encoder rendered it as ``{"type": "thinking",
    "thinking": ""}`` — blob dropped, no signature — while the streaming
    encoder (#103) preserved it. The client replays this content on the next
    turn, so the data must survive the round trip (AUDIT #125).
    """
    turn = ir.AssistantTurn(
        text="visible answer",
        thinking=[ir.ThinkingPart(text="", block_type="redacted_thinking",
                                  data=REDACTED_BLOB)],
    )
    ctx = RequestContext(surface="messages",
                         ir_req=ir.Request(model="claude-x", messages=[]))
    out = am.encode_response(ctx, turn, "claude-x", "req-1")

    types = [b["type"] for b in out["content"]]
    assert "redacted_thinking" in types, (
        f"redacted_thinking block missing from sync encode: {types}"
    )
    block = next(b for b in out["content"] if b["type"] == "redacted_thinking")
    assert block["data"] == REDACTED_BLOB, (
        f"encrypted blob lost: {block!r}"
    )
    # The visible text must still be there, in order after the thinking block.
    assert out["content"][-1] == {"type": "text", "text": "visible answer"}


def test_sync_anthropic_encode_keeps_ordinary_thinking_shape():
    """Ordinary (signed) thinking must not be rerouted by the new branch."""
    turn = ir.AssistantTurn(
        thinking=[ir.ThinkingPart(text="reasoning", signature="sig-1")],
    )
    ctx = RequestContext(surface="messages",
                         ir_req=ir.Request(model="claude-x", messages=[]))
    out = am.encode_response(ctx, turn, "claude-x", "req-1")
    assert out["content"][0] == {"type": "thinking", "thinking": "reasoning",
                                 "signature": "sig-1"}


def test_redacted_thinking_survives_client_replay_round_trip():
    """The failure mode the audit describes: encode -> client echoes history.

    Whatever the sync encoder emits is what the client sends back next turn;
    that replay must still reach an Anthropic upstream carrying the blob.
    """
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    turn = ir.AssistantTurn(
        thinking=[ir.ThinkingPart(text="", block_type="redacted_thinking",
                                  data=REDACTED_BLOB)],
    )
    ctx = RequestContext(surface="messages",
                         ir_req=ir.Request(model="claude-x", messages=[]))
    emitted = am.encode_response(ctx, turn, "claude-x", "req-1")

    replay = am.decode_request({
        "model": "claude-x", "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": emitted["content"]},
            {"role": "user", "content": "again"},
        ],
    })
    body = AnthropicAdapter().encode_request(
        replay, "claude-x", {"max_tokens": 100, "extra_body": {}})
    serialized = json.dumps(body)
    assert REDACTED_BLOB in serialized, (
        f"redacted blob lost on replay to upstream: {serialized}"
    )
    assert '"redacted_thinking"' in serialized, (
        f"replayed block lost its type: {serialized}"
    )


# ---------------------------------------------------------------------------
# #124 (residual) — the same scalar-args defect in the provider decoders.
#
# The audit flagged these as "same unguarded pattern", noting #92's wrapper
# downgrades them to a retryable failure. They are worse than that on the
# streaming path: the scalar reaches ``ToolCallArgsDelta.args_fragment`` (typed
# str), the gateway buffers it, and the Chat encoder's ``"".join``-equivalent
# frame serialization raises mid-stream — the client gets a 200, partial
# tool_calls, and then a synthetic error frame.
# ---------------------------------------------------------------------------

def _scalar_args_frame(arguments):
    import orjson

    return orjson.dumps({
        "id": "x", "object": "chat.completion.chunk", "model": "m",
        "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "f", "arguments": arguments}}]}}],
    }).decode()


def test_openai_adapter_scalar_stream_args_are_coerced_to_str():
    """A non-string args fragment must never reach the encoder.

    ``args_fragment`` is typed ``str``; the Chat encoder serializes it into a
    JSON frame, so a bool/int fragment broke the stream mid-flight.
    """
    from wiwi.providers.openai_adapter import OpenAIAdapter
    from wiwi.streaming import deltas as dl

    for arguments in (True, 5):
        out = OpenAIAdapter().decode_stream_event(
            "", _scalar_args_frame(arguments))
        args = [d for d in out if isinstance(d, dl.ToolCallArgsDelta)]
        assert args, f"no args delta for {arguments!r}: {out}"
        for d in args:
            assert isinstance(d.args_fragment, str), (
                f"non-string args_fragment {d.args_fragment!r} for "
                f"arguments={arguments!r}"
            )


def test_openrouter_adapter_scalar_stream_args_are_coerced_to_str():
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter
    from wiwi.streaming import deltas as dl

    for arguments in (True, 5):
        out = OpenRouterAdapter().decode_stream_event(
            "", _scalar_args_frame(arguments))
        args = [d for d in out if isinstance(d, dl.ToolCallArgsDelta)]
        assert args, f"no args delta for {arguments!r}: {out}"
        for d in args:
            assert isinstance(d.args_fragment, str), (
                f"non-string args_fragment {d.args_fragment!r} for "
                f"arguments={arguments!r}"
            )


def test_nim_adapter_scalar_stream_args_are_coerced_to_str():
    from wiwi.providers.nim_adapter import NimAdapter
    from wiwi.streaming import deltas as dl

    for arguments in (True, 5):
        out = NimAdapter().decode_stream_event("", _scalar_args_frame(arguments))
        args = [d for d in out if isinstance(d, dl.ToolCallArgsDelta)]
        assert args, f"no args delta for {arguments!r}: {out}"
        for d in args:
            assert isinstance(d.args_fragment, str), (
                f"non-string args_fragment {d.args_fragment!r} for "
                f"arguments={arguments!r}"
            )


def test_openai_adapter_scalar_nonstream_args_do_not_raise():
    """The non-streaming decode raised TypeError straight out of
    ``decode_response`` — #92's wrapper turns that into a retryable 502 and
    burns the whole retry budget on a request the provider answered."""
    import orjson

    from wiwi.providers.openai_adapter import OpenAIAdapter

    body = orjson.dumps({
        "id": "x", "object": "chat.completion", "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": [
                                     {"id": "c1", "type": "function",
                                      "function": {"name": "f",
                                                   "arguments": True}}]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    turn = OpenAIAdapter().decode_response(200, body)
    assert turn.tool_calls, "tool call lost"
    assert turn.tool_calls[0].args == {}, turn.tool_calls[0].args


@respx.mock
async def test_chat_stream_scalar_upstream_args_do_not_break_the_stream():
    """End-to-end: an upstream streaming a bool args fragment.

    Pre-fix the client received HTTP 200, a partial tool_calls frame, then
    ``{"error": {"message": "sequence item 0: expected str instance, bool
    found"}}`` — a corrupt stream from a valid upstream response.
    """
    sse = (
        'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
        '"tool_calls":[{"index":0,"id":"c1","type":"function",'
        '"function":{"name":"f","arguments":true}}]}}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},'
        '"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,'
        '"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").respond(text=sse)
    app = create_app(_chat_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            r = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o", "stream": True,
                "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": f"Bearer {MASTER}"})
    assert r.status_code == 200
    assert '"error"' not in r.text, f"stream carried an error frame: {r.text!r}"
    assert "[DONE]" in r.text, f"stream did not terminate cleanly: {r.text!r}"
