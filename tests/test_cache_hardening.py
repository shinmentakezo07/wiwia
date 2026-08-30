"""Regression tests for cache logic hardening (spec 2026-08-21).

A. streaming usage parsed even when chunk carries choices
B. ctx.cache_hit set when provider reports cached tokens
C. cache_savings computed in log events
D. anthropic system cache_control preserved
"""

import json

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, build_log_event
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am


# -- A: usage in a choices-bearing chunk must still produce UsageFinal ----------
def test_stream_usage_in_choices_chunk_parsed():
    ad = OpenAIAdapter()
    # the exact shape OpenRouter/OpenAI send: final chunk has finish_reason AND usage
    out = ad.decode_stream_event("", json.dumps({
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 149, "completion_tokens": 241,
                  "prompt_tokens_details": {"cached_tokens": 128},
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }))
    kinds = [type(x).__name__ for x in out]
    assert "UsageFinal" in kinds, f"usage dropped when choices present: {kinds}"
    u = next(x for x in out if type(x).__name__ == "UsageFinal")
    assert u.prompt == 149 and u.cached == 128 and u.output == 241
    assert "Finish" in kinds  # finish_reason still mapped


def test_stream_usage_last_wins():
    """Cumulative usage updates: later UsageFinal replaces earlier."""
    ad = OpenAIAdapter()
    ad.decode_stream_event("", json.dumps({
        "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}))
    out = ad.decode_stream_event("", json.dumps({
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 149, "completion_tokens": 241,
                  "prompt_tokens_details": {"cached_tokens": 128}}}))
    finals = [x for x in out if isinstance(x, dl.UsageFinal)]
    assert finals and finals[-1].cached == 128 and finals[-1].output == 241


# -- B: cache_hit flag ----------------------------------------------------------
def _gateway_ctx() -> tuple[Gateway, RequestContext]:
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p", model="m"))],
        general_settings=GeneralSettings(master_key="x"),
    )
    g = Gateway(Router(cfg), CostEngine())
    req = ir.Request(model="m", messages=[ir.Message(role="user",
                                                     parts=[ir.TextPart("hi")])])
    return g, RequestContext(surface="chat", ir_req=req)


def test_price_sets_cache_hit_on_cached_tokens():
    g, ctx = _gateway_ctx()
    dep = g.router.groups["m"][0]
    turn = ir.AssistantTurn(text="ok",
                            usage=ir.Usage(prompt_tokens=100, completion_tokens=5,
                                           cached_tokens=64))
    g._price(ctx, dep, turn.usage)
    assert ctx.cache_hit is True


def test_price_leaves_cache_hit_false_when_uncached():
    g, ctx = _gateway_ctx()
    dep = g.router.groups["m"][0]
    g._price(ctx, dep, ir.Usage(prompt_tokens=100, completion_tokens=5,
                                cached_tokens=0))
    assert ctx.cache_hit is False


def test_price_stream_sets_cache_hit():
    g, ctx = _gateway_ctx()
    dep = g.router.groups["m"][0]
    g._price_stream(ctx, dep, dl.UsageFinal(prompt=100, output=5, cached=32))
    assert ctx.cache_hit is True


# -- C: cache_savings in log events ---------------------------------------------
def test_log_event_has_cache_savings_computed():
    ce = CostEngine()
    ce.register("m", input_per_token=0.000001, output_per_token=0.000002)
    ce.prices["m"]["cache_read_input_cost_per_token"] = 0.0000001
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p", model="m"))],
        general_settings=GeneralSettings(master_key="x"),
    )
    g = Gateway(Router(cfg), ce)
    req = ir.Request(model="m", messages=[ir.Message(role="user",
                                                     parts=[ir.TextPart("hi")])])
    ctx = RequestContext(surface="chat", ir_req=req, group="m")
    dep = g.router.groups["m"][0]
    g._price(ctx, dep, ir.Usage(prompt_tokens=100, completion_tokens=10,
                                cached_tokens=80))
    evt = build_log_event(ctx)
    expected = 80 * (0.000001 - 0.0000001)  # 7.2e-05
    assert abs(evt.cache_savings - expected) < 1e-9
    assert evt.cache_hit is True


def test_cache_savings_zero_for_unpriced_model():
    g, ctx = _gateway_ctx()
    dep = g.router.groups["m"][0]
    g._price(ctx, dep, ir.Usage(prompt_tokens=100, completion_tokens=10,
                                cached_tokens=80))
    evt = build_log_event(ctx)
    assert evt.cache_savings == 0.0


# -- D: anthropic system cache_control passthrough -------------------------------
def test_anthropic_system_cache_control_preserved():
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 10,
        "system": [{"type": "text", "text": "be brief",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "hi"}],
    })
    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    sysd = body["system"]
    assert isinstance(sysd, list), f"expected block form, got {sysd!r}"
    assert sysd[0]["cache_control"] == {"type": "ephemeral"}
    assert sysd[0]["text"] == "be brief"


def test_anthropic_system_plain_string_without_cache_control():
    body = AnthropicAdapter().encode_request(
        am.decode_request({"model": "c", "max_tokens": 8, "system": "plain",
                           "messages": [{"role": "user", "content": "hi"}]}),
        "c", {})
    assert body["system"] == "plain"
