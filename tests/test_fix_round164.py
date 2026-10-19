"""Round 164: the translation layer's loss points — carrier, and caller-visible.

An audit of the decode → IR → re-encode pipeline found the IR was not the
problem — it was doing its job — but that three classes of loss were hiding
around it. Each finding below is a *carrier* gap (the IR has nowhere to put
something) or a *silence* gap (the caller cannot learn what was dropped).

**Carrier gaps (the IR has no field):**

* **#326 (open)** — ``logprobs`` is a full-stack gap. The client sends
  ``{"logprobs": true, "top_logprobs": 5}``; the value rides ``Request.extras``
  untyped, is forwarded upstream, and the tokens are **billed as output** — but
  no adapter decodes ``choices[0].logprobs``, no IR field holds it, and no
  encoder emits it. The client silently receives no logprobs. This round adds
  the carrier end to end: ``AssistantTurn.logprobs`` plus a ``LogprobsDelta``
  for the streaming path.

* **#496 (new)** — the same class of gap for the OTHER twelve OpenAI-2026
  platform params ``AUDIT.md`` #326 lists (``user``, ``frequency_penalty``,
  ``presence_penalty``, ``logit_bias``, ``service_tier``, ``store``,
  ``prediction``, ``modalities``, ``verbosity``, ``safety_identifier``,
  ``prompt_cache_key``, ``web_search_options``) and for assistant **audio
  output** and ``image_url.detail``: forwarded upstream and billed, never
  decoded, never returned. Some of them are *response-side* values, so the
  carrier is a general one rather than a per-field one.

* **#497 (new)** — ``stop_sequence`` is populated on the IR
  (``AssistantTurn.stop_sequence``, set by the Anthropic adapter) but no
  OpenAI-shaped encoder ever reads it. A caller learns only that the turn
  stopped, never *which* caller-supplied sequence fired — the exact
  information ``StopReason``'s ``stop_sequence`` member was added to carry.

**Silence gaps (the carrier exists, the caller is not told):**

* **#498 (new)** — the ``translation_warnings`` mechanism
  (``providers/base.take_adapter_warnings`` → ``ctx.metadata`` →
  the spend log) was built for exactly this: "a construct the caller asked for
  but this provider cannot honour ... the CALLER does not [see it]". Only
  ONE adapter out of ten populates it (Gemini, for ``strict`` and
  ``disable_parallel_tool_use``). Every other drop — the OpenAI adapter's
  builtin-tool drop, its ``top_k``/``seed`` drops, the Gemini audio drop, the
  Anthropic audio drop, the OpenRouter ``top_k`` drop — is visible only to
  the *operator* in the log. This round wires the mechanism into every
  adapter, so the caller learns what was dropped.

* **#499 (new)** — two *silent* falls-through with no branch at all:
  ``AudioPart`` on the Anthropic and Gemini adapters (the part simply vanishes
  from the ``for p in m.parts`` loop — no branch, no warning), and
  ``DocumentPart.name``/``context`` on every non-Anthropic adapter (the
  document is forwarded, its title and context are not). A caller that
  attached a PDF named ``report.pdf`` with context ``"Q3 revenue"`` gets a
  document with neither, and nothing says so.

* **#500 (new)** — the *unsigned thinking block*: ``anthropic_adapter`` sends
  ``{"type": "thinking", "thinking": ...}`` with no ``signature`` whenever
  the source had none. Anthropic rejects an unsigned thinking block on
  replay (``RESEARCH.md:67``: "Anthropic signatures MUST round-trip
  unmodified or 400"). The scenario is reachable: a Responses
  ``reasoning.encrypted_content`` blob (which no Anthropic-shaped source
  signs) replayed to an Anthropic upstream, or an OpenAI-shaped client's
  ``reasoning_content`` echoed back as history. ``AUDIT.md`` #355's fix
  sketch asked for exactly this guard ("skip (or refuse) a ``ThinkingPart``
  with no signature rather than send it unsigned") and only the
  ``encrypted_content`` half of it landed.

Two things this round deliberately does NOT do:

* It does not make every provider honour every field. The IR models the
  *intersection* of what the dialects can express; ``top_k`` on an OpenAI
  upstream genuinely has no spelling, and ``strict`` on Gemini genuinely has
  no field. Those are the design working, not a defect. The fix is that the
  caller is *told*, which is what #498 delivers.

* It does not change the wire shape of any existing response. ``logprobs``
  appears only when the upstream actually returned it, and the new warning
  channel is additive.
"""

from __future__ import annotations

import httpx
import orjson
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
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.base import take_adapter_warnings
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.openrouter_adapter import OpenRouterAdapter
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


class _Ctx:
    """Minimal stand-in for RequestContext for the encode paths."""

    request_id = "req-164"

    def __init__(self) -> None:
        self.metadata: dict = {}


def _ctx() -> _Ctx:
    return _Ctx()


# ---------------------------------------------------------------------------
# #326 — logprobs: a full-stack carrier gap
# ---------------------------------------------------------------------------


def test_logprobs_decodes_from_an_openai_response_into_the_ir():
    """The upstream's logprobs must survive the decode hop."""
    adapter = OpenAIAdapter()
    body = orjson.dumps({
        "id": "x", "object": "chat.completion", "model": "m",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "hi"},
            "finish_reason": "stop",
            "logprobs": {
                "content": [{
                    "token": "hi", "logprob": -0.25, "bytes": [104, 105],
                    "top_logprobs": [
                        {"token": "hi", "logprob": -0.25, "bytes": [104, 105]},
                        {"token": "yo", "logprob": -1.5, "bytes": [121, 111]},
                    ],
                }],
                "refusal": None,
            },
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    turn = adapter.decode_response(200, body)
    assert turn.logprobs is not None, "logprobs must not be dropped at decode"
    content = turn.logprobs.get("content")
    assert isinstance(content, list) and content, "logprobs.content must survive"
    first = content[0]
    assert first["token"] == "hi"
    assert first["logprob"] == pytest.approx(-0.25)
    # The alternatives are the expensive half of the feature — top_logprobs
    # is what a caller uses to see near-misses.
    assert len(first["top_logprobs"]) == 2


def test_logprobs_re_encodes_into_the_callers_dialect():
    """A caller that asked for logprobs must get them back."""
    adapter = OpenAIAdapter()
    body = orjson.dumps({
        "id": "x", "object": "chat.completion", "model": "m",
        "choices": [{
            "index": 0, "message": {"role": "assistant", "content": "hi"},
            "finish_reason": "stop",
            "logprobs": {"content": [{
                "token": "hi", "logprob": -0.25, "bytes": [104, 105],
                "top_logprobs": [],
            }]},
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    turn = adapter.decode_response(200, body)
    payload = oc.encode_response(_ctx(), turn, "m", "req-164")
    choice = payload["choices"][0]
    assert "logprobs" in choice, "the OpenAI chat shape puts logprobs on the choice"
    assert choice["logprobs"]["content"][0]["token"] == "hi"


def test_logprobs_absence_emits_no_logprobs_key():
    """A response without logprobs must not grow an empty one."""
    adapter = OpenAIAdapter()
    body = orjson.dumps({
        "id": "x", "object": "chat.completion", "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    turn = adapter.decode_response(200, body)
    assert turn.logprobs is None
    payload = oc.encode_response(_ctx(), turn, "m", "req-164")
    assert "logprobs" not in payload["choices"][0]


def test_logprobs_survives_the_streaming_path():
    """The streaming path needs the same carrier the sync path gets."""
    adapter = OpenAIAdapter()
    deltas = adapter.decode_stream_event(
        "", orjson.dumps({
            "choices": [{
                "delta": {"content": "hi"},
                "logprobs": {"content": [{
                    "token": "hi", "logprob": -0.25, "bytes": [104, 105],
                    "top_logprobs": [],
                }]},
            }],
        }).decode())
    kinds = [type(d).__name__ for d in deltas]
    assert "LogprobsDelta" in kinds, f"no logprobs delta in {kinds}"


def test_logprobs_reaches_an_openai_client_through_the_stream_encoder():
    """A streaming caller that asked for logprobs must receive them."""
    enc = oc.ChatStreamEncoder("m", "req-164", include_usage=False)
    out = enc.feed(dl.LogprobsDelta(logprobs={"content": [{
        "token": "hi", "logprob": -0.25, "bytes": [104, 105], "top_logprobs": [],
    }]}))
    assert out is not None, "the logprobs delta must produce a frame"
    frame = orjson.loads(out.decode().split("data: ", 1)[1])
    assert frame["choices"][0]["logprobs"]["content"][0]["token"] == "hi"


# ---------------------------------------------------------------------------
# #497 — stop_sequence: populated on the IR, never read by the OpenAI dialects
# ---------------------------------------------------------------------------


def test_stop_sequence_reaches_an_openai_chat_caller():
    """A caller must learn WHICH sequence fired, not just that it stopped."""
    turn = ir.AssistantTurn(text="a", stop_reason="stop_sequence",
                            stop_sequence="END")
    payload = oc.encode_response(_ctx(), turn, "m", "req-164")
    choice = payload["choices"][0]
    # OpenAI has no stop_sequence field, but the information must not be
    # lost: it rides on the choice so a caller can act on it.
    assert choice.get("stop_sequence") == "END", (
        "the matched stop sequence must reach the caller")


def test_stop_sequence_reaches_a_responses_caller():
    turn = ir.AssistantTurn(text="a", stop_reason="stop_sequence",
                            stop_sequence="END")
    payload = orp.encode_response(_ctx(), turn, "m", "req-164")
    assert payload.get("stop_sequence") == "END"


def test_no_stop_sequence_means_no_key():
    turn = ir.AssistantTurn(text="a", stop_reason="stop")
    payload = oc.encode_response(_ctx(), turn, "m", "req-164")
    assert "stop_sequence" not in payload["choices"][0]


# ---------------------------------------------------------------------------
# #500 — the unsigned thinking block
# ---------------------------------------------------------------------------


def test_anthropic_upstream_never_receives_an_unsigned_thinking_block():
    """Anthropic rejects an unsigned thinking block on replay.

    ``RESEARCH.md:67``: "Anthropic signatures MUST round-trip unmodified or
    400." The scenario is reachable — a Responses ``encrypted_content`` blob
    (which no Anthropic-shaped source signs) replayed to an Anthropic
    upstream, or an OpenAI-shaped client's ``reasoning_content`` echoed back
    as history — and the request 400s on the upstream's terms, naming
    nothing the caller sent.
    """
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="assistant", parts=[
            ir.ThinkingPart(text="plan"),   # no signature
            ir.TextPart("answer"),
        ])],
    )
    body = adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    blocks = body["messages"][0]["content"]
    thinking = [b for b in blocks if b.get("type") == "thinking"]
    for b in thinking:
        assert "signature" in b, (
            "an unsigned thinking block must not be sent to Anthropic")


def test_anthropic_upstream_keeps_a_signed_thinking_block():
    """The guard must not break the signed case, which is the normal one."""
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="assistant", parts=[
            ir.ThinkingPart(text="plan", signature="SIG"),
            ir.TextPart("answer"),
        ])],
    )
    body = adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    blocks = body["messages"][0]["content"]
    thinking = [b for b in blocks if b.get("type") == "thinking"]
    assert thinking, "a signed thinking block must still be sent"
    assert thinking[0]["signature"] == "SIG"


def test_redacted_thinking_blob_still_reaches_anthropic():
    """The redacted blob IS signed material — it must not be dropped."""
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="assistant", parts=[
            ir.ThinkingPart(text="", block_type="redacted_thinking", data="BLOB"),
        ])],
    )
    body = adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    blocks = body["messages"][0]["content"]
    assert any(b.get("type") == "redacted_thinking" and b.get("data") == "BLOB"
               for b in blocks), "the redacted blob must survive"


# ---------------------------------------------------------------------------
# #498 — the caller must learn what the provider could not honour
# ---------------------------------------------------------------------------


def test_openai_adapter_warns_when_it_drops_a_builtin_tool():
    """Chat Completions cannot host a provider-executed tool.

    The drop is real and already logged for the operator, but the CALLER
    asked for a tool it will now never get a result for.
    """
    adapter = OpenAIAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        tools=[ir.Tool(name="web_search", builtin="web_search",
                       builtin_config={"max_uses": 3})],
    )
    adapter.encode_request(req, "m", {
        "provider_type": "openai", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert warnings, "dropping a builtin tool must reach the caller"
    assert any("web_search" in w for w in warnings)


def test_gemini_adapter_warns_when_it_drops_a_builtin_tool():
    """Gemini cannot host the tool-search builtins.

    ``web_search`` IS hostable on Gemini (it rides as ``google_search``), so
    the unhostable case is a tool-search variant — Gemini has no equivalent.
    """
    adapter = GeminiAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        tools=[ir.Tool(name="tool_search_bm25", builtin="tool_search_bm25")],
    )
    adapter.encode_request(req, "m", {
        "provider_type": "gemini", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("tool_search_bm25" in w for w in warnings)


def test_openai_adapter_warns_when_it_drops_top_k():
    """``top_k`` has no OpenAI spelling — the caller must be told."""
    adapter = OpenAIAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        gen_params=ir.GenParams(top_k=40),
    )
    adapter.encode_request(req, "m", {
        "provider_type": "openai", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("top_k" in w for w in warnings)


def test_openrouter_adapter_warns_when_it_drops_top_k():
    adapter = OpenRouterAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        gen_params=ir.GenParams(top_k=40),
    )
    adapter.encode_request(req, "m", {
        "provider_type": "openrouter", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("top_k" in w for w in warnings)


def test_no_warning_when_nothing_was_dropped():
    """The warning channel must stay quiet on a clean request."""
    adapter = OpenAIAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        gen_params=ir.GenParams(temperature=0.2),
    )
    adapter.encode_request(req, "m", {
        "provider_type": "openai", "drop_params": True, "extra_body": {},
    })
    assert take_adapter_warnings(adapter) == []


# ---------------------------------------------------------------------------
# #499 — the silent fall-throughs
# ---------------------------------------------------------------------------


def test_anthropic_adapter_does_not_silently_drop_an_audio_part():
    """Anthropic has no audio block — but the caller must be told."""
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.TextPart("what is this?"),
            ir.AudioPart(b64="AUDB64", mime="audio/wav"),
        ])],
    )
    adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("audio" in w.lower() for w in warnings), (
        "a dropped audio part must reach the caller")


def test_gemini_adapter_does_not_silently_drop_an_audio_part():
    adapter = GeminiAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.TextPart("what is this?"),
            ir.AudioPart(b64="AUDB64", mime="audio/wav"),
        ])],
    )
    adapter.encode_request(req, "m", {
        "provider_type": "gemini", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("audio" in w.lower() for w in warnings)


def test_gemini_adapter_warns_when_it_drops_a_document_title():
    """A document's title and context have no Gemini spelling."""
    adapter = GeminiAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.DocumentPart(b64="DOCB64", mime="application/pdf",
                            name="report.pdf", context="Q3 revenue"),
        ])],
    )
    adapter.encode_request(req, "m", {
        "provider_type": "gemini", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("title" in w.lower() or "context" in w.lower() for w in warnings)


def test_openai_adapter_warns_when_it_drops_a_document_title():
    """The same gap on the Chat Completions wire, which forwards the document
    but not its name or context."""
    adapter = OpenAIAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.DocumentPart(b64="DOCB64", mime="application/pdf",
                            name="report.pdf", context="Q3 revenue"),
        ])],
    )
    adapter.encode_request(req, "m", {
        "provider_type": "openai", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("title" in w.lower() or "context" in w.lower() for w in warnings), (
        "a dropped document title must reach the caller")


def test_anthropic_adapter_keeps_the_document_title():
    """Anthropic has a native spelling — the guard must not fire there."""
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[
            ir.DocumentPart(b64="DOCB64", mime="application/pdf",
                            name="report.pdf", context="Q3 revenue"),
        ])],
    )
    body = adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    doc = body["messages"][0]["content"][0]
    assert doc["title"] == "report.pdf"
    assert doc["context"] == "Q3 revenue"
    assert not take_adapter_warnings(adapter)


# ---------------------------------------------------------------------------
# #501 — the OpenAI-2026 platform params a non-OpenAI backend cannot honour
# ---------------------------------------------------------------------------


def test_anthropic_adapter_reports_the_platform_params_it_cannot_honour():
    """``logit_bias`` and friends are honoured and billed by OpenAI only.

    On the Messages API they are simply absent, so the caller set a
    constraint the backend never applied — and had no way to know.
    """
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        extras={"logit_bias": {"123": -100}, "service_tier": "flex",
                "unmodelled_param": 1},
    )
    adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("logit_bias" in w for w in warnings), warnings
    assert any("service_tier" in w for w in warnings), warnings
    # A param that is NOT an OpenAI platform one is not this channel's
    # business — it is an unmodelled extra, handled by the drop_params policy.
    assert not any("unmodelled_param" in w for w in warnings), warnings


def test_gemini_adapter_reports_the_platform_params_it_cannot_honour():
    adapter = GeminiAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        extras={"frequency_penalty": 0.5, "verbosity": "low"},
    )
    adapter.encode_request(req, "m", {
        "provider_type": "gemini", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("frequency_penalty" in w for w in warnings), warnings
    assert any("verbosity" in w for w in warnings), warnings


def test_the_openai_adapter_does_not_report_params_it_can_honour():
    """The OpenAI-shaped upstream DOES honour these — no warning.

    A warning here would be a false alarm on every ordinary OpenAI request
    that sets ``logprobs`` or ``service_tier``.
    """
    adapter = OpenAIAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        extras={"logit_bias": {"123": -100}, "service_tier": "flex"},
    )
    body = adapter.encode_request(req, "m", {
        "provider_type": "openai", "drop_params": True, "extra_body": {},
    })
    assert body["logit_bias"] == {"123": -100}
    assert body["service_tier"] == "flex"
    assert not take_adapter_warnings(adapter)


def test_nim_adapter_reports_the_platform_params_it_strips():
    """NIM 400s on these, so they MUST go — but the caller must be told.

    NIM strict-validates its request body ("Unsupported parameter(s):
    `prompt_cache_key`"), so the strip is mandatory. It was also silent:
    the caller set ``logit_bias`` and the request looked honoured.
    """
    from wiwi.providers.nim_adapter import NimAdapter

    adapter = NimAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        extras={"logit_bias": {"123": -100}, "prompt_cache_key": "k"},
    )
    body = adapter.encode_request(req, "m", {
        "provider_type": "nvidia-nim", "drop_params": True, "extra_body": {},
    })
    assert "logit_bias" not in body, "NIM 400s on this — it must be stripped"
    assert "prompt_cache_key" not in body
    warnings = take_adapter_warnings(adapter)
    assert any("logit_bias" in w for w in warnings), warnings
    assert any("prompt_cache_key" in w for w in warnings), warnings


def test_the_opencode_responses_route_reports_the_platform_params():
    """The OpenCode Responses route never forwards extras — so it drops
    every platform param, and says so."""
    from wiwi.providers.opencode_adapter import OpencodeAdapter

    adapter = OpencodeAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        extras={"logit_bias": {"123": -100}},
    )
    # A gpt-* model routes to the Responses wire.
    adapter.encode_request(req, "gpt-4o", {
        "provider_type": "opencode", "drop_params": True, "extra_body": {},
    })
    warnings = take_adapter_warnings(adapter)
    assert any("logit_bias" in w for w in warnings), warnings


# ---------------------------------------------------------------------------
# #496 — the other OpenAI-2026 platform params
# ---------------------------------------------------------------------------


def test_response_format_reaches_a_gemini_upstream():
    """``json_schema`` has a Gemini spelling: ``responseSchema``.

    The Gemini adapter currently handles only ``json_object``
    (``responseMimeType``); a ``json_schema`` request is dropped entirely,
    so a caller asking for a structured object gets prose back and no error.
    """
    adapter = GeminiAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        gen_params=ir.GenParams(response_format=ir.ResponseFormat(
            type="json_schema",
            json_schema={"type": "object",
                         "properties": {"city": {"type": "string"}}})),
    )
    body = adapter.encode_request(req, "m", {
        "provider_type": "gemini", "drop_params": True, "extra_body": {},
    })
    gen = body.get("generationConfig", {})
    assert "responseSchema" in gen, (
        "a json_schema request must reach Gemini as responseSchema")
    assert gen.get("responseMimeType") == "application/json"


def test_response_format_reaches_an_anthropic_upstream():
    """Anthropic has a native ``output_config.format`` — the schema must ride it.

    A ``json_schema`` request that fell through to the system-prompt
    instruction path would still work, but the native field is strictly
    better: it is enforced by the API rather than by the model's good
    behaviour, and it survives a caller that sends no system prompt.
    """
    adapter = AnthropicAdapter()
    req = ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart("x")])],
        gen_params=ir.GenParams(response_format=ir.ResponseFormat(
            type="json_schema",
            json_schema={"type": "object",
                         "properties": {"city": {"type": "string"}}})),
    )
    body = adapter.encode_request(req, "claude-sonnet-4-5", {
        "provider_type": "anthropic", "drop_params": True, "extra_body": {},
    })
    fmt = body.get("output_config", {}).get("format")
    assert fmt is not None, "the schema must reach Anthropic as output_config.format"
    assert fmt["type"] == "json_schema"
    assert "city" in str(fmt["schema"])


# ---------------------------------------------------------------------------
# #498 (end to end) — the warning must reach the CALLER, not just the log
# ---------------------------------------------------------------------------


def _cfg() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    )


@respx.mock
async def test_the_caller_learns_that_a_builtin_tool_was_dropped():
    """The whole point of the warning channel: the CALLER sees it.

    An Anthropic-dialect caller declares a provider-hosted ``web_search``
    tool; the deployment is an OpenAI-shaped upstream, which cannot host it.
    The result will never arrive, so the caller must be told rather than
    left to wonder.
    """
    app = create_app(_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json={
                "id": "x", "object": "chat.completion", "model": "gpt-4o",
                "choices": [{"index": 0, "message": {"role": "assistant",
                                                     "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
            r = await c.post("/v1/messages", json={
                "model": "gpt-4o",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "search please"}],
                "tools": [{"type": "web_search_20250305", "name": "web_search",
                           "max_uses": 3}],
            }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 200
            warning = r.headers.get("x-wiwi-translation-warnings")
            assert warning, (
                "the caller must learn the hosted tool was dropped")
            assert "web_search" in warning, warning


@respx.mock
async def test_a_clean_request_carries_no_warning_header():
    """The header must be absent when nothing was dropped."""
    app = create_app(_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json={
                "id": "x", "object": "chat.completion", "model": "gpt-4o",
                "choices": [{"index": 0, "message": {"role": "assistant",
                                                     "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
            r = await c.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 200
            assert "x-wiwi-translation-warnings" not in r.headers


@respx.mock
async def test_a_caller_defined_function_tool_is_not_reported_as_dropped():
    """A function tool NAMED ``web_search`` is the caller's own tool.

    The suppression rule keys on the ``builtin`` FLAG, never the name (the
    name match deleted a real call once — AUDIT #156). The warning channel
    must key on the flag too, or it would report a perfectly honoured tool
    as dropped.
    """
    app = create_app(_cfg())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            route = respx.post("https://api.openai.com/v1/chat/completions")
            route.respond(json={
                "id": "x", "object": "chat.completion", "model": "gpt-4o",
                "choices": [{"index": 0, "message": {"role": "assistant",
                                                     "content": "hi"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
            r = await c.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "search please"}],
                "tools": [{"type": "function", "function": {
                    "name": "web_search", "parameters": {"type": "object"}}}],
            }, headers={"Authorization": "Bearer sk-wiwi-master-test"})
            assert r.status_code == 200
            assert "x-wiwi-translation-warnings" not in r.headers, (
                "a caller-defined function tool must not be reported as a "
                f"dropped builtin ({r.headers.get('x-wiwi-translation-warnings')})")
