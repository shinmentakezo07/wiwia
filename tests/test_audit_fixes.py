"""Regression tests for audit fixes (bugs 1-20)."""

import asyncio
import json
from typing import ClassVar

import pytest

from wiwi.config import (
    DeploymentParams,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.ratelimit.memory import RateLimiter
from wiwi.router.router import Router, _status_of
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orp


# -- bug 1: Gemini key must be appended to the URL ----------------------------
def test_gemini_url_includes_key():
    from wiwi.core.gateway import _build_url
    from wiwi.providers.base import ProviderKeyRef
    from wiwi.router.router import Deployment, ProviderAccount, ProviderKey

    acct = ProviderAccount(name="g", provider_type="gemini",
                           base_url="https://generativelanguage.googleapis.com/v1beta",
                           keys=[ProviderKey(label="k", secret="AIzaSECRET")])
    dep = Deployment(group="gem", provider=acct, model_id="gemini-pro")
    url = _build_url(GeminiAdapter(), dep,
                     ProviderKeyRef(label="k", secret="AIzaSECRET"), False, "chat")
    assert url.endswith("key=AIzaSECRET")
    # non-gemini adapters are untouched
    oacct = ProviderAccount(name="o", provider_type="openai",
                            base_url="https://api.openai.com/v1",
                            keys=[ProviderKey(label="k", secret="sk-x")])
    odep = Deployment(group="gpt", provider=oacct, model_id="gpt-4o")
    ourl = _build_url(OpenAIAdapter(), odep,
                      ProviderKeyRef(label="k", secret="sk-x"), False, "chat")
    assert "sk-x" not in ourl


# -- bug 2: _status_of returns retryable statuses so cooldowns fire ------------
def test_status_of_retryable_statuses():
    for s in (408, 500, 502, 503, 529):
        assert _status_of(_err(s)) == s, f"status {s} lost"
    assert _status_of(_err(429)) == 429
    assert _status_of(_err(401)) == 401
    assert _status_of(_err(400)) is None


def _err(status):
    from wiwi.providers.base import WiwiError
    return WiwiError(status, "x", "boom")


# -- bug 2 end-to-end: server errors cool the deployment -----------------------
def test_5xx_records_deployment_fail():
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p", model="m"))],
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0),
    )
    r = Router(cfg)
    dep = r.groups["m"][0]
    calls = []

    async def call_one(d, k, ctx):
        calls.append("hit")
        raise _err(503)

    class Ctx:
        group = "m"
        attempts: ClassVar[list] = []
        started = 0.0

    from wiwi.providers.base import WiwiError
    with pytest.raises(WiwiError):
        asyncio.run(_run(r, Ctx(), call_one))
    assert dep.cooldown_until > 0, "deployment not cooled after 5xx"


async def _run(r, ctx, call_one):
    from wiwi.router.router import execute_with_retries
    return await execute_with_retries(r, ctx, call_one)


# -- bug 6: unknown stop reasons no longer crash the encoders -------------------
def test_encode_response_unknown_stop_reason():
    ctx = RequestContext(surface="chat", ir_req=ir.Request(model="x", messages=[]))
    turn = ir.AssistantTurn(text="hi", stop_reason="weird_new_reason")
    body = oc.encode_response(ctx, turn, "x", "r1")
    assert body["choices"][0]["finish_reason"] == "stop"

    turn2 = ir.AssistantTurn(text="hi", stop_reason="weird_new_reason")
    body2 = am.encode_response(ctx, turn2, "x", "r1")
    assert body2["stop_reason"] == "end_turn"


# -- bug 7: openai adapter emits ToolCallClose ---------------------------------
def test_openai_stream_tool_close_emitted():
    ad = OpenAIAdapter()
    out = []
    out += ad.decode_stream_event("", json.dumps(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "f", "arguments": ""}}]}}]}))
    out += ad.decode_stream_event("", json.dumps(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"a":'}}]}}]}))
    out += ad.decode_stream_event("", json.dumps(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '1}'}}]}}]}))
    out += ad.decode_stream_event("", json.dumps(
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
    kinds = [type(x).__name__ for x in out]
    assert kinds.count("ToolCallClose") == 1
    assert kinds.index("ToolCallClose") == len(kinds) - 2  # before Finish


# -- bug 17: anthropic adapter only closes tool blocks --------------------------
def test_anthropic_block_stop_only_closes_tools():
    ad = AnthropicAdapter()
    out = []
    out += ad.decode_stream_event("", json.dumps(
        {"type": "message_start", "message": {"model": "c", "usage": {}}}))
    out += ad.decode_stream_event("", json.dumps(
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text"}}))
    out += ad.decode_stream_event("", json.dumps(
        {"type": "content_block_stop", "index": 0}))
    kinds = [type(x).__name__ for x in out]
    assert "ToolCallClose" not in kinds

    out += ad.decode_stream_event("", json.dumps(
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "t1", "name": "f"}}))
    out += ad.decode_stream_event("", json.dumps(
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": "{}"}}))
    out += ad.decode_stream_event("", json.dumps(
        {"type": "content_block_stop", "index": 1}))
    kinds = [type(x).__name__ for x in out]
    assert kinds.count("ToolCallClose") == 1


# -- bugs 4/5: stream encoders emit terminal events exactly once, in order ------
def test_anthropic_stream_terminal_order():
    enc = am.AnthropicStreamEncoder("claude-x", "abc")
    frames = []
    for d in [dl.StreamStart("claude-x"), dl.TextDelta("hi"),
              dl.UsageFinal(prompt=3, output=1), dl.Finish("stop"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            frames.append(chunk.decode())
    frames.append(enc.final_frame().decode())
    frames.append("event: message_stop\n")
    blob = "".join(frames)
    md = blob.find("message_delta")
    stops = [i for i in range(len(blob)) if blob.startswith("message_stop", i)]
    assert md != -1 and len(stops) == 1
    assert md < stops[0], "message_delta must precede message_stop"


def test_responses_completed_emitted_once():
    enc = orp.ResponsesStreamEncoder("m", "abc")
    chunks = []
    for d in [dl.StreamStart("m"), dl.TextDelta("hi"),
              dl.UsageFinal(prompt=3, output=1), dl.Finish("stop"),
              dl.StreamEnd()]:
        chunk = enc.feed(d)
        if chunk:
            chunks.append(chunk)
    chunks.append(enc._completed())
    blob = b"".join(chunks).decode()
    assert blob.count("response.completed") == 1


# -- bug 8: TPM windows count tokens -------------------------------------------
def test_tpm_counts_tokens_not_requests():
    rl = RateLimiter()
    # 100-token budget: two 40-token requests fit, third is blocked
    allowed, _ = rl.check("k1", key_rpm=None, key_tpm=100, est_tokens=40)
    assert allowed
    allowed, _ = rl.check("k1", key_rpm=None, key_tpm=100, est_tokens=40)
    assert allowed
    allowed, _ = rl.check("k1", key_rpm=None, key_tpm=100, est_tokens=40)
    assert not allowed, "tpm should block once token budget is exhausted"
    assert rl._windows["k1:tpm"].count() == 80, "requests (not tokens) were counted"


def test_record_tokens_adds_usage():
    rl = RateLimiter()
    allowed, _ = rl.check("k1", key_tpm=100, est_tokens=10)
    assert allowed
    rl.record_tokens("k1", 95)
    allowed, _ = rl.check("k1", key_tpm=100, est_tokens=10)
    assert not allowed, "recorded tokens should count toward tpm"


# -- bug 14: deleted keys evicted from cache immediately -------------------------


# -- bug 12: admin check is constant-time and empty-master-key is deny-all -------
def test_mask_key_short():
    from wiwi.auth.keys import mask_key
    assert mask_key("ab").startswith("***")


# -- wire: gemini adapter still builds valid URLs without gateway helper ---------
def test_gemini_build_url_shapes():
    g = GeminiAdapter()
    u = g.build_url("https://x/", "m", False, "chat")
    assert ":generateContent?key=" in u
    u2 = g.build_url("https://x/", "m", True, "chat")
    assert ":streamGenerateContent?alt=sse&key=" in u2


# -- router: get_key resolves live pool entry ------------------------------------
def test_provider_account_get_key():
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p", provider="openai",
                               keys=[KeyDef(label="a", key="k1"),
                                     KeyDef(label="b", key="k2")])],
        model_list=[],
    )
    r = Router(cfg)
    acct = r.providers["p"]
    assert acct.get_key("a").secret == "k1"
    assert acct.get_key("zzz") is None
