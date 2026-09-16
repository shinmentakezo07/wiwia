"""Regression tests for AUDIT #153 and #154 — decoder hardening.

- **#153** — ``AnthropicAdapter.decode_stream_event`` crashes with
  ``AttributeError`` on syntactically-valid-but-typed-wrong payload fields
  (non-dict frames, ``message: null``, ``usage: "x"``, ``content_block: null``,
  ``delta: null``, ``error: null``). The ``AttributeError`` escapes into the
  pump's generic handler: mid-stream ``StreamError`` to the client, partial
  billing, ``dep.record_fail`` and key cooldown for a frame carrying zero
  semantic content — the same frame class #110 (openai) and #136 (gemini)
  already fixed at the *frame* level but which never reached Anthropic's
  *nested* reads.

- **#154** — the same class in the OpenAI-wire decoders: a typed-wrong
  ``delta`` (truthy non-dict) or ``tool_calls`` entry crashes, and — worse — a
  non-string ``content``/``reasoning`` is *forwarded* as ``TextDelta``/
  ``ThinkingDelta``, violating the streaming contract (``text: str``) and
  breaking downstream consumers (``len(d.text)`` TypeError in the pump, or an
  invalid OpenAI chunk serialized to the client). NIM's variant feeds the
  non-string into ``MiniMaxFramer.feed`` (``str + int`` TypeError escaping
  ``_feed_safely``, which only catches ``NimToolProtocolError``); Gemini's
  nested ``functionCall``/``usageMetadata`` reads are likewise unguarded.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.streaming import deltas as dl

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _no_stream_error(out: list[dl.IRStreamDelta]) -> bool:
    return all(not isinstance(d, dl.StreamError) for d in out)


# ---------------------------------------------------------------------------
# #153 — AnthropicAdapter.decode_stream_event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("junk", ["null", "5", '"s"', "true", "[1,2,3]"])
def test_anthropic_non_dict_frame_is_ignored(junk):
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    assert a.decode_stream_event("", junk) == []  # must not raise


def test_anthropic_message_start_null_message():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    out = a.decode_stream_event(
        "message_start", '{"type":"message_start","message":null}')
    assert out == [dl.StreamStart(model="")]


def test_anthropic_non_dict_usage_reads():
    """`usage: "x"` in message_start and message_delta must read as zero."""
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    out = a.decode_stream_event(
        "message_start",
        '{"type":"message_start","message":{"model":"m","usage":"x"}}')
    assert [type(d) for d in out] == [dl.StreamStart]
    out = a.decode_stream_event(
        "message_delta",
        '{"type":"message_delta","delta":null,"usage":"x"}')
    assert out == [dl.UsageFinal(prompt=0, cached=0, cache_creation=0,
                                 reasoning=0, output=0),
                   dl.Finish("stop", stop_sequence=None)]


def test_anthropic_content_block_start_null_block():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    out = a.decode_stream_event(
        "content_block_start",
        '{"type":"content_block_start","index":0,"content_block":null}')
    assert out == []


def test_anthropic_content_block_delta_null_delta():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    out = a.decode_stream_event(
        "content_block_delta",
        '{"type":"content_block_delta","index":0,"delta":null}')
    assert out == []


def test_anthropic_error_event_null_error():
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    out = a.decode_stream_event("error", '{"type":"error","error":null}')
    assert out == [dl.StreamError(message="unknown anthropic error",
                                  kind="status")]


def test_anthropic_happy_path_unharmed():
    """Control: a normal stream still decodes identically."""
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    a = AnthropicAdapter()
    out = a.decode_stream_event(
        "message_start",
        '{"type":"message_start","message":{"model":"claude","usage":'
        '{"input_tokens":3,"cache_read_input_tokens":1}}}')
    out += a.decode_stream_event(
        "content_block_delta",
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"hi"}}')
    out += a.decode_stream_event(
        "message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":2,"output_tokens_details":'
        '{"thinking_tokens":5}}}')
    assert out == [
        # StreamStart carries the message_start usage: Claude Code reads the
        # prompt/cache counts from this frame to size its context window, so
        # the adapter must surface them rather than dropping them (AUDIT #156).
        dl.StreamStart(model="claude", prompt=3, cached=1, cache_creation=0),
        dl.TextDelta("hi"),
        dl.UsageFinal(prompt=3, cached=1, cache_creation=0, reasoning=5,
                      output=2),
        dl.Finish("stop", stop_sequence=None),
    ]


# ---------------------------------------------------------------------------
# #154 — OpenAI-wire decoders (base; inherited by compatible/gmicloud/bai/...)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [5, "x", True, []])
def test_openai_non_dict_delta_is_ignored(delta):
    """A truthy non-dict `delta` crashed on delta.get (openai_adapter:431)."""
    import orjson

    from wiwi.providers.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": delta}]}).decode())
    assert out == []


def test_openai_non_string_content_is_dropped_not_forwarded():
    """`content: 5` must not become TextDelta(text=5) — a contract break that
    crashes the pump (len(d.text)) or serializes an invalid chunk."""
    import orjson

    from wiwi.providers.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": {"content": 5}}]}).decode())
    assert not any(isinstance(d, dl.TextDelta) for d in out)
    # control: real text still flows
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": {"content": "hi"}}]}).decode())
    assert out == [dl.TextDelta("hi")]


def test_openai_non_string_reasoning_is_dropped_not_forwarded():
    import orjson

    from wiwi.providers.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter()
    for key in ("reasoning_content", "reasoning"):
        out = a.decode_stream_event(
            "", orjson.dumps({"choices": [{"delta": {key: 5}}]}).decode())
        assert not any(isinstance(d, dl.ThinkingDelta) for d in out)
        out = a.decode_stream_event(
            "", orjson.dumps({"choices": [{"delta": {key: "t"}}]}).decode())
        assert out == [dl.ThinkingDelta("t")]


def test_openai_malformed_tool_call_entries_are_skipped():
    """null entries crash at tc.get (:440); a truthy non-dict function
    crashes at fn.get (:441)."""
    import orjson

    from wiwi.providers.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter()
    for tcs in ([None], [{"index": 0, "function": "x"}], "x"):
        out = a.decode_stream_event(
            "", orjson.dumps({"choices": [
                {"delta": {"tool_calls": tcs}}]}).decode())
        assert not any(isinstance(d, (dl.ToolCallOpen, dl.ToolCallArgsDelta,
                                      dl.ToolCallClose)) for d in out)


def test_openai_non_dict_usage_is_ignored():
    import orjson

    from wiwi.providers.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [], "usage": "x"}).decode())
    assert out == []


# ---------------------------------------------------------------------------
# #154 — OpenRouter (copy-derived decoder, same three gaps)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [5, "x", True, []])
def test_openrouter_non_dict_delta_is_ignored(delta):
    import orjson

    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    a = OpenRouterAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": delta}]}).decode())
    assert out == []


def test_openrouter_non_string_content_is_dropped_not_forwarded():
    import orjson

    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    a = OpenRouterAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": {"content": 5}}]}).decode())
    assert not any(isinstance(d, dl.TextDelta) for d in out)
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": {"content": "hi"}}]}).decode())
    assert out == [dl.TextDelta("hi")]


def test_openrouter_malformed_tool_call_entries_are_skipped():
    import orjson

    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    a = OpenRouterAdapter()
    for tcs in ([None], [{"index": 0, "function": "x"}]):
        out = a.decode_stream_event(
            "", orjson.dumps({"choices": [
                {"delta": {"tool_calls": tcs}}]}).decode())
        assert not any(isinstance(d, (dl.ToolCallOpen, dl.ToolCallArgsDelta,
                                      dl.ToolCallClose)) for d in out)


# ---------------------------------------------------------------------------
# #154 — NIM (framer would see non-str content; usage/tool_calls unguarded)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delta", [5, "x", True, []])
def test_nim_non_dict_delta_is_ignored(delta):
    import orjson

    from wiwi.providers.nim_adapter import NimAdapter

    a = NimAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": delta}]}).decode())
    assert out == []


def test_nim_non_string_content_never_reaches_the_framer():
    """content: 5 escaped _feed_safely (it catches only NimToolProtocolError)
    as a str+int TypeError inside MiniMaxFramer.feed."""
    import orjson

    from wiwi.providers.nim_adapter import NimAdapter

    a = NimAdapter()
    for key in ("content", "reasoning_content", "reasoning"):
        out = a.decode_stream_event(
            "", orjson.dumps({"choices": [{"delta": {key: 5}}]}).decode())
        assert not any(isinstance(d, (dl.TextDelta, dl.ThinkingDelta))
                       for d in out)
    # control: real text still flows (and framer state stays clean)
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [{"delta": {"content": "hi"}}]}).decode())
    assert out == [dl.TextDelta("hi")]


def test_nim_non_dict_usage_is_ignored():
    import orjson

    from wiwi.providers.nim_adapter import NimAdapter

    a = NimAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"choices": [], "usage": "x"}).decode())
    assert out == []


def test_nim_malformed_tool_call_entries_are_skipped():
    import orjson

    from wiwi.providers.nim_adapter import NimAdapter

    a = NimAdapter()
    for tcs in ([None], [{"index": 0, "function": "x"}]):
        out = a.decode_stream_event(
            "", orjson.dumps({"choices": [
                {"delta": {"tool_calls": tcs}}]}).decode())
        assert not any(isinstance(d, (dl.ToolCallOpen, dl.ToolCallArgsDelta,
                                      dl.ToolCallClose)) for d in out)


# ---------------------------------------------------------------------------
# #154 — Gemini (nested functionCall / usageMetadata reads unguarded)
# ---------------------------------------------------------------------------


def test_gemini_null_function_call_part_is_skipped():
    import orjson

    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"candidates": [{"content": {"parts": [
            {"functionCall": None}]}}]}).decode())
    assert not any(isinstance(d, (dl.ToolCallOpen, dl.ToolCallArgsDelta,
                                  dl.ToolCallClose)) for d in out)
    assert _no_stream_error(out)


def test_gemini_non_dict_usage_still_finishes_cleanly():
    """usageMetadata:"x" crashed at u.get (:279) instead of finishing."""
    import orjson

    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"candidates": [{"finishReason": "STOP"}],
                          "usageMetadata": "x"}).decode())
    assert [type(d) for d in out] == [dl.StreamStart, dl.Finish, dl.StreamEnd]
    assert out[1].stop_reason == "stop"


def test_gemini_happy_path_unharmed():
    """Control: real function calls still decode."""
    import orjson

    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    out = a.decode_stream_event(
        "", orjson.dumps({"candidates": [{"content": {"parts": [
            {"text": "hi"}, {"functionCall": {"name": "f", "args": {"a": 1}}}],
        }, "finishReason": "STOP"}]}).decode())
    assert [type(d) for d in out] == [
        dl.StreamStart, dl.TextDelta, dl.ToolCallOpen, dl.ToolCallArgsDelta,
        dl.ToolCallClose, dl.Finish, dl.StreamEnd]
    assert out[5].stop_reason == "tool_call"


# ---------------------------------------------------------------------------
# Live-path smoke: a client stream with #153/#154 poison frames upstream must
# still complete — no AttributeError escape into the pump, no mid-stream
# StreamError, no key cooldown for a healthy deployment.
# ---------------------------------------------------------------------------

ANTHROPIC_POISON_SSE = (
    'event: message_start\n'
    'data: {"type":"message_start","message":{"model":"claude-test","usage":'
    '{"input_tokens":10}}}\n\n'
    # poison frames, interleaved with a normal completion:
    'data: null\n\n'
    'data: 5\n\n'
    'event: content_block_start\n'
    'data: {"type":"content_block_start","index":0,"content_block":null}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,"delta":null}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"hi"}}\n\n'
    'event: message_delta\n'
    'data: {"type":"message_delta","delta":null,"usage":"x"}\n\n'
    'event: message_stop\n'
    'data: {"type":"message_stop"}\n\n'
)


@respx.mock
async def test_anthropic_stream_survives_poison_frames_end_to_end(tmp_path):
    """Claude-Code-shaped streaming request with junk upstream frames."""
    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )
    from wiwi.server.app import create_app

    respx.post("https://api.anthropic.test/v1/messages").respond(
        text=ANTHROPIC_POISON_SSE,
        headers={"content-type": "text/event-stream"})
    config = WiwiConfig(
        providers=[ProviderDef(
            name="ant", provider="anthropic",
            base_url="https://api.anthropic.test/v1",
            keys=[KeyDef(label="default", key="sk-ant-test")])],
        model_list=[ModelEntry(
            model_name="claude-test",
            wiwi_params=DeploymentParams(provider="ant", model="claude-test"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db"),
    )
    app = create_app(config)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
                transport=transport, base_url="http://test") as c:
            r = await c.post("/v1/messages", headers=AUTH, json={
                "model": "claude-test", "max_tokens": 64, "stream": True,
                "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    body = r.text
    # the completion is delivered, not aborted by a mid-stream failure
    assert '"text":"hi"' in body or 'hi' in body
    assert "message_stop" in body
    assert '"type":"error"' not in body and '"error"' not in body, (
        f"poison frames leaked an error to the client:\n{body}")
