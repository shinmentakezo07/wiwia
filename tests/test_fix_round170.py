"""Round 170 — four IR gaps, each a value that existed on one side of a
translation and had nowhere to live on the other.

The theme is the same in all four: the thing was *observed* (a provider's
usage object, a cache breakpoint, a sampling parameter, a block spelling) and
then had no carrier in ``wiwi/ir/types.py``, so it was either inferred from the
deployment's provider type or dropped silently.

1. **``Usage`` did not say whether ``prompt_tokens`` includes cached tokens.**
   That is a property of the wire's usage object — OpenAI totals the prompt,
   Anthropic's ``input_tokens`` excludes cache reads — but the IR did not carry
   it, so ``gateway.prompt_includes_cached(dep)`` inferred it from the
   deployment's wire shape. The inference has already been wrong twice
   (AUDIT #9, #227): the second time an opencode deployment serving ``claude-*``
   speaks the Messages wire, so a provider-type test took the OpenAI branch and
   billed the fresh-input term at $0 for a whole Claude Code session.
   ``Usage.cached_in_prompt`` (and its streaming twin on ``UsageFinal``) records
   the convention where the body is decoded; ``None`` keeps the old inference
   for decoders that have not learned to record it.

2. **``ToolUsePart`` had no ``cache_control``.** ``TextPart``, ``ImagePart``,
   ``DocumentPart``, ``ToolResultPart`` and ``Tool`` all carry one. Anthropic
   accepts a breakpoint on a ``tool_use`` block, so dropping the field on decode
   lost the marker on replay and every token before that block was re-read at
   1x — exactly the defect ``ImagePart.cache_control`` documents.

3. **The Chat codec did not decode ``top_k``.** ``GenParams.top_k`` exists and
   the Anthropic and Responses codecs populate it, which makes a non-OpenAI
   backend emit a caller-visible "top_k was dropped" warning. On the Chat
   surface the parameter fell into ``Request.extras`` instead, where the OpenAI
   adapter's ``_STANDARD`` set does not list it, so ``drop_params`` discarded it
   with **no warning at all** — the adapter's warning only fires when the value
   reached ``GenParams.top_k``.

4. **``block_type`` was an untyped ``str``.** The spelling is a replay
   requirement (an ``mcp_tool_use`` paired with an ``mcp_tool_result`` must go
   back under the same name or Anthropic rejects the history), yet a typo or a
   junk upstream value propagated verbatim to the wire. The two closed
   vocabularies are now ``Literal``s and are normalized at construction; the
   ``*_tool_result`` family stays open on purpose (see ``ToolResultPart``).
"""

from __future__ import annotations

import json
import typing

import pytest

from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.router.router import Deployment, ProviderAccount
from wiwi.streaming import deltas as dl
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# 1 fresh input token = 1e-5, 1 cached token = 1e-6, 1 output token = 2e-5.
# Usage of ``prompt_tokens=1000, cached_tokens=1000`` therefore costs 0.011 when
# the prompt total EXCLUDES the cached count (1000 fresh + 1000 cached) and
# 0.001 when it INCLUDES it (1000 total, all of it cached) — an 11x difference,
# which is why the convention cannot be a guess.
PRICES = {
    "gpt-x": {
        "input_cost_per_token": 1e-5,
        "output_cost_per_token": 2e-5,
        "cache_read_input_cost_per_token": 1e-6,
    }
}


def _dep(provider_type: str = "openai", model_id: str = "gpt-x") -> Deployment:
    acct = ProviderAccount(name=provider_type, provider_type=provider_type,
                           base_url="https://example.invalid/v1")
    return Deployment(group="m", provider=acct, model_id=model_id)


def _gateway() -> Gateway:
    """A Gateway with only what ``_price``/``_price_stream`` actually read.

    ``Gateway.__init__`` opens an ``httpx.AsyncClient`` and needs a ``Router``;
    pricing is a pure function of the cost engine, the deployment and the usage
    object, so building the rest would only add cleanup this test never uses.
    """
    gw = Gateway.__new__(Gateway)
    gw.cost = CostEngine(PRICES)
    return gw


def _ctx() -> RequestContext:
    return RequestContext(surface="chat",
                          ir_req=ir.Request(model="gpt-x", messages=[]))


def _params(provider_type: str = "anthropic") -> dict:
    return {
        "max_tokens": 64,
        "extra_body": {},
        "drop_params": True,
        "prompt_cache": False,
        "prompt_cache_min_tokens": None,
        "opencode_free_tier_cloak": True,
        "provider_type": provider_type,
    }


# ---------------------------------------------------------------------------
# 1. Usage.cached_in_prompt
# ---------------------------------------------------------------------------

def test_usage_flag_overrides_the_wire_heuristic():
    """A decoder that says the prompt EXCLUDES cache reads is believed.

    The deployment is OpenAI-wired, where the heuristic assumes the opposite,
    so this fails if ``_price`` ignores the flag.
    """
    ctx = _ctx()
    usage = ir.Usage(prompt_tokens=1000, completion_tokens=0, cached_tokens=1000,
                     cached_in_prompt=False)
    _gateway()._price(ctx, _dep(), usage)
    assert ctx.cost == pytest.approx(0.011, abs=1e-9)


def test_usage_flag_none_keeps_the_existing_heuristic():
    """No recorded convention behaves exactly as before (OpenAI totals prompt)."""
    ctx = _ctx()
    usage = ir.Usage(prompt_tokens=1000, completion_tokens=0, cached_tokens=1000)
    _gateway()._price(ctx, _dep(), usage)
    assert ctx.cost == pytest.approx(0.001, abs=1e-9)


def test_stream_usage_flag_reaches_pricing():
    """The streaming path carries the same fact, not just the sync one.

    Claude Code, Codex and the playground are all served as streams, so a fix
    that only reached ``_price`` would be inert for every real client.
    """
    ctx = _ctx()
    _gateway()._price_stream(
        ctx, _dep(),
        dl.UsageFinal(prompt=1000, cached=1000, cached_in_prompt=False))
    assert ctx.cost == pytest.approx(0.011, abs=1e-9)
    assert ctx.usage is not None
    assert ctx.usage.cached_in_prompt is False


def test_anthropic_decode_records_excluding_convention():
    body = json.dumps({
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "claude-x",
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "cache_read_input_tokens": 3,
                  "cache_creation_input_tokens": 2},
    }).encode()
    turn = AnthropicAdapter().decode_response(200, body)
    assert turn.usage.cached_in_prompt is False
    assert turn.usage.cached_tokens == 3


def test_openai_decode_records_including_convention():
    body = json.dumps({
        "id": "chatcmpl-1", "object": "chat.completion", "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                  "prompt_tokens_details": {"cached_tokens": 3}},
    }).encode()
    turn = OpenAIAdapter().decode_response(200, body)
    assert turn.usage.cached_in_prompt is True
    assert turn.usage.cached_tokens == 3


# ---------------------------------------------------------------------------
# 2. ToolUsePart.cache_control
# ---------------------------------------------------------------------------

TOOL_TURN = {
    "model": "claude-x",
    "max_tokens": 64,
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "read",
             "input": {"path": "a.py"},
             "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"},
        ]},
    ],
}


def _tool_use_blocks(messages: list[dict]) -> list[dict]:
    return [b for m in messages for b in m.get("content", [])
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def test_tool_use_cache_breakpoint_survives_decode():
    req = am.decode_request(TOOL_TURN)
    calls = [p for m in req.messages for p in m.parts
             if isinstance(p, ir.ToolUsePart)]
    assert len(calls) == 1
    assert calls[0].cache_control == {"type": "ephemeral"}


def test_tool_use_cache_breakpoint_survives_replay():
    req = am.decode_request(TOOL_TURN)
    body = AnthropicAdapter().encode_request(req, "claude-x", _params())
    block = _tool_use_blocks(body["messages"])[0]
    assert block["cache_control"] == {"type": "ephemeral"}
    # The breakpoint must not disturb the fields the API pairs the call by.
    assert block["id"] == "call_1" and block["input"] == {"path": "a.py"}


def test_tool_use_without_a_breakpoint_emits_no_field():
    body = json.loads(json.dumps(TOOL_TURN))
    del body["messages"][1]["content"][0]["cache_control"]
    req = am.decode_request(body)
    out = AnthropicAdapter().encode_request(req, "claude-x", _params())
    assert "cache_control" not in _tool_use_blocks(out["messages"])[0]


# ---------------------------------------------------------------------------
# 3. top_k on the Chat surface
# ---------------------------------------------------------------------------

def _chat_body(**extra) -> dict:
    body = {"model": "gpt-x",
            "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return body


def test_chat_decodes_top_k_into_gen_params():
    req = oc.decode_request(_chat_body(top_k=5))
    assert req.gen_params.top_k == 5
    # Modelled now, so it must not ALSO ride the pass-through extras: an extra
    # copy is invisible to the warning path and gets dropped by drop_params.
    assert "top_k" not in req.extras


def test_chat_rejects_a_non_numeric_top_k():
    assert oc.decode_request(_chat_body(top_k=True)).gen_params.top_k is None
    assert oc.decode_request(_chat_body(top_k="5")).gen_params.top_k is None
    assert oc.decode_request(_chat_body(top_k=[5])).gen_params.top_k is None


def test_chat_top_k_reaches_the_caller_visible_warning():
    """The silent loss this closes: Chat used to drop ``top_k`` with no signal.

    A non-OpenAI upstream cannot honour ``top_k``, and the adapter warns only
    when the value is in the IR (not in ``extras``). Before the decode fix this
    list stayed empty on the Chat surface while the Anthropic and Responses
    surfaces reported the drop.
    """
    req = oc.decode_request(_chat_body(top_k=5))
    adapter = OpenAIAdapter()
    adapter.encode_request(req, "gpt-x", _params(provider_type="openai"))
    assert any("top_k" in w for w in adapter.translation_warnings)


# ---------------------------------------------------------------------------
# 4. block_type vocabulary
# ---------------------------------------------------------------------------

def test_block_type_annotations_are_literals():
    """Pins the typing intent: these were bare ``str`` fields."""
    tool_use = typing.get_type_hints(ir.ToolUsePart)["block_type"]
    assert typing.get_args(tool_use) == ("tool_use", "server_tool_use",
                                         "mcp_tool_use")
    thinking = typing.get_type_hints(ir.ThinkingPart)["block_type"]
    assert typing.get_args(thinking) == ("thinking", "redacted_thinking")
    # The result family is deliberately OPEN: the decoder accepts any
    # ``*_tool_result`` spelling, so a Literal there would reject next year's
    # server tool and break replay of an otherwise valid history.
    assert typing.get_type_hints(ir.ToolResultPart)["block_type"] is str


def test_unknown_tool_use_block_type_cannot_reach_the_wire():
    assert ir.ToolUsePart(id="c", name="f",
                          block_type="bogus").block_type == "tool_use"
    assert ir.ToolUsePart(id="c", name="f",
                          block_type=[1]).block_type == "tool_use"
    for legal in ("tool_use", "server_tool_use", "mcp_tool_use"):
        assert ir.ToolUsePart(id="c", name="f", block_type=legal).block_type == legal


def test_unknown_thinking_block_type_cannot_reach_the_wire():
    assert ir.ThinkingPart("t", block_type="bogus").block_type == "thinking"
    assert ir.ThinkingPart("t", block_type=None).block_type == "thinking"
    assert (ir.ThinkingPart("", block_type="redacted_thinking", data="x")
            .block_type == "redacted_thinking")
