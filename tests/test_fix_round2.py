"""Regression tests for the end-to-end audit fixes (2026-08-21 round 2).

Covers: Gemini streaming API-key appending, provider 401/403 key
invalidation, Gemini stop_reason sniffing, timeout cooldowns, stream
inflight accounting, Anthropic signature framing, Responses output_index,
honest truncation errors, TPM reservation replacement, and misc hardening.
"""

import asyncio
import json

import httpx
import pytest
import respx

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, _build_url
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.base import (
    ProviderKeyRef,
    WiwiError,
    error_from_provider_status,
)
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Deployment, ProviderAccount, ProviderKey, Router
from wiwi.streaming import deltas as dl
from wiwi.streaming.sse import LineSSEParser
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


def _cfg(**router_overrides) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(**{"num_retries": 1, "allowed_fails": 2,
                                          "cooldown_time": 60.0,
                                          **router_overrides}),
    )


# -- C1: Gemini *streaming* URLs must carry the API key -------------------------
def test_gemini_streaming_url_includes_key():
    acct = ProviderAccount(name="g", provider_type="gemini",
                           base_url="https://generativelanguage.googleapis.com/v1beta",
                           keys=[ProviderKey(label="k", secret="AIzaSECRET")])
    dep = Deployment(group="gem", provider=acct, model_id="gemini-pro")
    ref = ProviderKeyRef(label="k", secret="AIzaSECRET")

    url_s = _build_url(GeminiAdapter(), dep, ref, True, "chat")
    url_n = _build_url(GeminiAdapter(), dep, ref, False, "chat")
    assert url_s.endswith("key=AIzaSECRET"), url_s
    assert url_n.endswith("key=AIzaSECRET"), url_n


# -- C2: provider 401/403 keep their status so keys get invalidated -------------
def test_provider_auth_errors_preserve_status():
    e = error_from_provider_status(401, '{"error":"bad key"}', "openai")
    assert e.status == 401
    assert e.etype == "authentication_error"
    assert e.retryable is True  # fail over to the next key in the pool


def test_auth_error_invalidates_key():
    """In standard mode (historical behavior), a single 401 immediately
    invalidates the key.  In any_error mode, 401 increments err_count by 2
    and only invalidates after key_max_consecutive_fails (default 5)."""
    key = ProviderKey(label="a", secret="k")
    acct = ProviderAccount(name="p", provider_type="openai",
                           base_url="https://x/v1", keys=[key])
    # standard: 401 -> invalid immediately
    acct.on_result(key, 401, None, failover_mode="standard")
    assert key.status == "invalid"
    assert not key.available
    assert not acct.healthy
    # any_error: 401 increments err_count by 2 but does not invalidate yet
    key2 = ProviderKey(label="b", secret="k")
    acct2 = ProviderAccount(name="p", provider_type="openai",
                            base_url="https://x/v1", keys=[key2])
    acct2.on_result(key2, 401, None, failover_mode="any_error",
                    key_max_consecutive_fails=5)
    assert key2.status == "cooling"
    assert key2.err_count == 2
    # after enough consecutive auth failures, the key retires
    for _ in range(2):
        acct2.on_result(key2, 401, None, failover_mode="any_error",
                        key_max_consecutive_fails=5)
    assert key2.status == "invalid"


def test_504_is_retryable_and_cools_deployment():
    cfg = _cfg(num_retries=0, allowed_fails=1, cooldown_time=60.0)
    r = Router(cfg)
    dep = r.groups["gpt-4o"][0]
    calls = []

    async def call_one(d, k, ctx):
        calls.append("hit")
        raise WiwiError(504, "timeout", "upstream timed out", retryable=True)

    class Ctx:
        group = "gpt-4o"
        started = 0.0

    from wiwi.router.router import execute_with_retries
    with pytest.raises(WiwiError):
        asyncio.run(execute_with_retries(r, Ctx(), call_one))  # type: ignore[arg-type]
    assert dep.cooldown_until > 0, "timeout must trip the deployment cooldown"


# -- M1: Gemini stop_reason must come from finishReason/functionCall only -------
def test_gemini_text_containing_tool_keeps_stop():
    ad = GeminiAdapter()
    out = ad.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": [{"text": "here is the tool you wanted"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5}}))
    finishes = [d for d in out if isinstance(d, dl.Finish)]
    assert finishes and finishes[0].stop_reason == "stop"


def test_gemini_function_call_reports_tool_call():
    ad = GeminiAdapter()
    out = ad.decode_stream_event("", json.dumps({
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "f", "args": {}}}]},
                        "finishReason": "STOP"}]}))
    finishes = [d for d in out if isinstance(d, dl.Finish)]
    assert finishes and finishes[0].stop_reason == "tool_call"
    closes = [d for d in out if isinstance(d, dl.ToolCallClose)]
    assert closes, "tool call lifecycle must be closed"


@respx.mock
async def test_stream_inflight_stays_up_during_generation():
    async def upstream():
        yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
        await asyncio.sleep(0.5)  # hold the generation open
        yield (b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
               b'"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n')
        yield b"data: [DONE]\n\n"

    respx.post("https://api.openai.com/v1/chat/completions").return_value = (
        httpx.Response(200, content=upstream()))
    g = Gateway(Router(_cfg()), CostEngine())
    try:
        req = ir.Request(model="gpt-4o", stream=True,
                         messages=[ir.Message(role="user",
                                              parts=[ir.TextPart("hi")])])
        ctx = RequestContext(surface="chat", ir_req=req, group="gpt-4o")
        dep = g.router.groups["gpt-4o"][0]
        observed = []
        async for _d in g.stream(ctx):
            observed.append(dep.inflight)
            if len(observed) >= 2:
                break
        assert observed and all(v == 1 for v in observed), observed
    finally:
        await g.aclose()


# -- M4: Anthropic encoder buffers orphan signatures ----------------------------
def test_anthropic_signature_buffered_until_thinking_block():
    enc = am.AnthropicStreamEncoder("claude-x", "abc")
    # signature arrives before any thinking content -> nothing emitted yet
    assert enc.feed(dl.ThinkingDelta("", signature="SIG")) is None
    first = enc.feed(dl.ThinkingDelta("thought"))  # opens the thinking block
    blob = (first or b"").decode()
    assert "signature_delta" not in blob
    last = enc.feed(dl.ThinkingDelta("", signature=None))
    close = enc._close_block()
    tail = ((last or b"") + b"".join(close)).decode()
    assert 'signature_delta' in tail, "buffered signature must flush before block stop"
    assert tail.index("signature_delta") < tail.index("content_block_stop")


def test_anthropic_signature_never_hits_foreign_block():
    enc = am.AnthropicStreamEncoder("claude-x", "abc")
    enc.feed(dl.TextDelta("plain"))  # opens a text block at index 0
    assert enc.feed(dl.ThinkingDelta("", signature="SIG")) is None
    blob = (enc.feed(dl.ToolCallOpen(index=0, id="t", name="f")) or b"").decode()
    assert "signature_delta" not in blob, "signature must not land on text/tool blocks"


# -- M5: Responses events carry distinct output_index per item ------------------
def test_responses_output_index_increments_per_item():
    enc = orp.ResponsesStreamEncoder("m", "abc")
    frames: list[str] = []
    for d in [dl.StreamStart("m"), dl.TextDelta("hi"), dl.ToolCallClose(0),
              dl.ToolCallOpen(index=0, id="c1", name="f"),
              dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
              dl.ToolCallClose(0)]:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk.decode())
    added: list[int] = []
    for f in frames:
        for part in f.strip().split("\n\n"):
            if part.startswith("data: ") and "output_item.added" in part:
                added.append(json.loads(part[len("data: "):])["output_index"])
    assert added == [0, 1], added


# -- m7/m8: truncated upstream surfaces an error, never a fake clean stop -------
@respx.mock
async def test_truncated_upstream_reports_stream_error():
    respx.post("https://api.openai.com/v1/chat/completions").respond(text=(
        'data: {"choices":[{"delta":{"content":"half"}}]}\n\n'))  # no finish, no usage
    g = Gateway(Router(_cfg()), CostEngine())
    try:
        req = ir.Request(model="gpt-4o", stream=True,
                         messages=[ir.Message(role="user",
                                              parts=[ir.TextPart("hi")])])
        deltas = [d async for d in g.stream(RequestContext(
            surface="chat", ir_req=req, group="gpt-4o"))]
    finally:
        await g.aclose()
    errs = [d for d in deltas if isinstance(d, dl.StreamError)]
    assert errs, "truncated upstream must yield StreamError"
    assert not any(isinstance(d, dl.Finish) for d in deltas)


# -- m8: estimate keeps provider-reported output tokens -------------------------
def test_estimate_preserves_reported_output():
    from wiwi.core.gateway import Gateway as G
    ce = G.__new__(G)  # only _price_stream under test
    object.__setattr__(ce, "cost", CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=ir.Request(model="x", messages=[]))
    dep = Deployment(group="x", provider=ProviderAccount(
        name="p", provider_type="openai", base_url="", keys=[]), model_id="m")
    u = dl.UsageFinal(prompt=0, output=17, reasoning=9)
    ce._price_stream(ctx, dep, u)
    assert ctx.usage.completion_tokens == 17
    assert ctx.usage.reasoning_tokens == 9


# -- m1: OpenAI tool strict lives inside function --------------------------------
def test_chat_decode_tool_strict():
    req = oc.decode_request({"model": "gpt-4o", "messages": [], "tools": [
        {"type": "function",
         "function": {"name": "f", "parameters": {"type": "object"},
                      "strict": True}}]})
    assert req.tools[0].strict is True


# -- m2: deployment max_tokens applies on openai/gemini too ----------------------
def test_deployment_max_tokens_openai():
    body = OpenAIAdapter().encode_request(
        ir.Request(model="gpt-4o", messages=[]), "gpt-4o",
        {"max_tokens": 777, "extra_body": {}})
    assert body["max_tokens"] == 777


def test_deployment_max_tokens_gemini():
    body = GeminiAdapter().encode_request(
        ir.Request(model="gem", messages=[]), "gem",
        {"max_tokens": 777, "extra_body": {}})
    assert body["generationConfig"]["maxOutputTokens"] == 777


# -- m10/m11: tool_result cache_control + structured content ---------------------
def test_anthropic_tool_result_cache_control_and_list_content():
    req = am.decode_request({
        "model": "c", "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
                 "cache_control": {"type": "ephemeral"},
                 "content": [{"type": "text", "text": "sunny"},
                             {"type": "text", "text": "warm"}]}]}]})
    part = req.messages[0].parts[0]
    assert part.content == "sunny warm"
    assert part.cache_control == {"type": "ephemeral"}

    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    blk = body["messages"][0]["content"][0]
    assert blk["cache_control"] == {"type": "ephemeral"}, \
        "cache_control must survive the round-trip to Anthropic outbound"

    none_req = am.decode_request({
        "model": "c", "messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2"}]}]})
    assert none_req.messages[0].parts[0].content == ""


def test_responses_function_call_output_structured_content():
    req = orp.decode_request({
        "model": "gpt-4o",
        "input": [{"type": "function_call_output", "call_id": "c1",
                   "output": [{"type": "output_text", "text": "42"}]}]})
    assert req.messages[0].parts[0].content == "42"


# -- m6: recorded usage replaces the estimate instead of stacking on it ----------
async def test_record_tokens_replaces_reservation():
    from wiwi.ratelimit.memory import RateLimiter
    rl = RateLimiter()
    assert (await rl.check("k1", key_tpm=100, est_tokens=80))[0]
    await rl.record_tokens("k1", 20)  # actual was far below estimate
    w = rl._windows["k1:tpm"]
    assert w.count() == 20, "actual must replace the estimated reservation"
    assert (await rl.check("k1", key_tpm=100, est_tokens=60))[0], \
        "no double counting of estimate + actual"


# -- m12: empty provider secrets are config errors --------------------------------
def test_empty_provider_key_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        KeyDef(label="a", key="   ")


# -- m14: SSE parser strips exactly one leading space ------------------------------
def test_sse_parser_single_space_rule():
    p = LineSSEParser()
    p.feed_line("data: payload")
    p.feed_line("")
    p2 = LineSSEParser()
    p2.feed_line("data:   spaced  ")
    evt = p2.feed_line("")
    assert evt is not None
    assert evt.data == "  spaced  ", "only one leading space may be stripped"


# -- m15: shutdown must not hang when log queues are full --------------------------
async def test_logging_stop_with_full_queue():
    from wiwi.logging_core.events import LogEvent
    from wiwi.logging_core.subsystem import LoggingSubsystem
    sub = LoggingSubsystem()
    await sub.start()
    evt = LogEvent(stream="request", ts=0.0)
    # log_request drops instead of raising when full; just overfill a bounded count
    for _ in range(60_000):
        sub.log_request(evt)
    assert sub.dropped_request_logs > 0
    await asyncio.wait_for(sub.stop(), timeout=5)


def test_adapter_protocol_has_no_is_done():
    from wiwi.providers import base
    assert not hasattr(base.ProviderAdapter, "is_done")
    for cls in (OpenAIAdapter(), AnthropicAdapter(), GeminiAdapter()):
        assert not hasattr(cls, "is_done")
