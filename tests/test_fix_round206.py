"""Round 206 — AUDIT #598/#599: two typed-wrong values that rode the wire codecs.

#598 — the Anthropic codec's two BUILTIN tool paths read the tool name with
`t.get("name") or <fallback>`, which defaults only a MISSING or falsy key. A
truthy non-string name (a list, a dict, an int) passed straight into
`Tool.name` (typed `str`, no `__post_init__`): the prefix estimator's
`len(t.name or "")` raised TypeError inside `encode_request` (a 500 on the hot
path) for an int, and the adapter re-emitted the junk verbatim as
`{"type": ..., "name": ["evil"]}` — an upstream 400 naming no field the caller
sent. The FUNCTION-tool path in the same loop already coerced (isinstance), as
do the Chat codec and the Responses codec (which documents this exact hazard on
its own unknown-builtin path).

#599 — the Responses codec's `input_audio` arm built the mime as
`f"audio/{ia.get('format') or 'wav'}"`, so a truthy non-string format (5, a
list) became `audio/5` on the IR and rode upstream. The Chat twin of the same
arm guards the field with isinstance.
"""

from __future__ import annotations

import orjson

from wiwi.providers.anthropic_adapter import AnthropicAdapter, _prefix_token_estimate
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_responses as resp
from wiwi.wire.openai_chat import decode_request as chat_decode


def _anthropic_body(tools: list[dict]) -> dict:
    return {"model": "claude-sonnet-4-5", "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": tools}


# --- AUDIT #598: Anthropic builtin tool names -------------------------------

def test_anthropic_builtin_non_string_name_coerces_to_canonical():
    """A truthy non-string name on a KNOWN builtin must not reach Tool.name."""
    req = am.decode_request(_anthropic_body(
        [{"type": "web_search_20250305", "name": ["evil"]},
         {"type": "web_search_20250305", "name": {"a": 1}},
         {"type": "web_search_20250305", "name": 5}]))
    assert [t.name for t in req.tools] == ["web_search"] * 3
    assert all(isinstance(t.name, str) for t in req.tools)


def test_anthropic_unknown_builtin_non_string_name_coerces_to_wire_type():
    """Same guard on the unknown-builtin passthrough path (falls back to type)."""
    req = am.decode_request(_anthropic_body(
        [{"type": "computer_20250124", "name": {"a": 1}},
         {"type": "code_execution_20250522", "name": 7}]))
    assert [t.name for t in req.tools] == ["computer_20250124",
                                           "code_execution_20250522"]
    assert all(isinstance(t.name, str) for t in req.tools)


def test_anthropic_builtin_string_name_still_wins():
    """A well-formed name round-trips unchanged — the guard only eats junk."""
    req = am.decode_request(_anthropic_body(
        [{"type": "web_search_20250305", "name": "my_search"}]))
    assert req.tools[0].name == "my_search"


def test_anthropic_builtin_missing_and_null_name_fall_back():
    """Missing and explicit-null names keep the pre-existing fallback."""
    req = am.decode_request(_anthropic_body(
        [{"type": "web_search_20250305"},
         {"type": "web_search_20250305", "name": None}]))
    assert [t.name for t in req.tools] == ["web_search", "web_search"]


def test_anthropic_int_tool_name_does_not_crash_prefix_estimator():
    """The estimator's len() ran inside encode_request — an int name 500'd."""
    req = am.decode_request(_anthropic_body(
        [{"type": "web_search_20250305", "name": 5}]))
    assert _prefix_token_estimate(req) >= 1


def test_anthropic_adapter_reemits_string_names_only():
    """The junk name must not ride the outbound body (upstream 400 bait)."""
    req = am.decode_request(_anthropic_body(
        [{"type": "web_search_20250305", "name": ["evil"]},
         {"type": "computer_20250124", "name": {"a": 1}}]))
    adapter = AnthropicAdapter()
    adapter.reset()
    body = adapter.encode_request(req, "claude-sonnet-4-5", {})
    names = [t["name"] for t in body["tools"]]
    assert names == ["web_search", "computer_20250124"]
    assert all(isinstance(n, str) for n in names)
    assert orjson.dumps(body["tools"])  # serializable


# --- AUDIT #599: Responses input_audio format -------------------------------

def test_responses_input_audio_non_string_format_defaults_to_wav():
    """A truthy non-string format must not become 'audio/5' on the IR."""
    body = {"model": "gpt-4o",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_audio",
                                    "input_audio": {"data": "AAAA",
                                                    "format": 5}}]}]}
    req = resp.decode_request(body)
    mimes = [p.mime for m in req.messages for p in m.parts
             if isinstance(p, ir_audio())]
    assert mimes == ["audio/wav"]


def test_responses_input_audio_string_format_kept():
    """A legal format still round-trips."""
    body = {"model": "gpt-4o",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_audio",
                                    "input_audio": {"data": "AAAA",
                                                    "format": "mp3"}}]}]}
    req = resp.decode_request(body)
    mimes = [p.mime for m in req.messages for p in m.parts
             if isinstance(p, ir_audio())]
    assert mimes == ["audio/mp3"]


def test_chat_input_audio_parity():
    """The Chat twin already guarded this — pin both surfaces agree."""
    body = {"model": "gpt-4o",
            "messages": [{"role": "user",
                          "content": [{"type": "input_audio",
                                       "input_audio": {"data": "AAAA",
                                                       "format": 5}}]}]}
    req = chat_decode(body)
    mimes = [p.mime for m in req.messages for p in m.parts
             if isinstance(p, ir_audio())]
    assert mimes == ["audio/wav"]


def ir_audio():
    from wiwi.ir import types as ir
    return ir.AudioPart
