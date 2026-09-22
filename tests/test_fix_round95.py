"""Regression tests for the round-95 audit fixes.

* **#270** — three adapters (NIM, OpenRouter stream, OpenRouter sync) each kept
  a *private* finish-reason map narrower than the shared
  ``wiwi.ir.translation.OPENAI_FINISH_TO_IR``. The shared map accepts the
  non-standard spellings OpenAI-compatible servers actually emit
  (``tool_use``/``function_call`` for a tool stop, ``max_tokens`` for a length
  stop, ``end_turn`` for a plain stop); the inline copies did not, so an
  upstream that spelled its tool-call stop ``tool_use`` reached the client as
  ``stop`` while the turn carried real tool calls — the exact class of defect
  the shared map exists to close. OpenRouter's inline copy additionally mapped
  ``"error" -> "stop"``, which ``normalize_finish_reason`` also produces for
  that unknown spelling, so delegating loses nothing.

Each test drives the adapter's real decode path with a finish-only chunk.
"""

from __future__ import annotations

import json

from wiwi.ir import translation as tr
from wiwi.providers import registry
from wiwi.streaming import deltas as dl

# Non-standard spellings the inline maps used to drop on the floor, with the IR
# reason the shared map produces for each.
_SPELLINGS = {
    "tool_use": "tool_call",
    "function_call": "tool_call",
    "max_tokens": "length",
    "end_turn": "stop",
    "error": "stop",
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_call",
    "content_filter": "content_filter",
}


def _finish_chunk(reason: str) -> str:
    return json.dumps(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]})


def _finish_of(deltas: list[dl.IRStreamDelta]) -> str:
    finishes = [d for d in deltas if isinstance(d, dl.Finish)]
    assert len(finishes) == 1, f"expected one Finish, got {deltas!r}"
    return finishes[0].stop_reason


# --------------------------------------------------------------------------
# the shared map is the reference
# --------------------------------------------------------------------------


def test_shared_map_accepts_the_nonstandard_spellings():
    for spelling, expected in _SPELLINGS.items():
        assert tr.normalize_finish_reason(spelling) == expected


# --------------------------------------------------------------------------
# #270 — NIM streaming
# --------------------------------------------------------------------------


def test_nim_stream_uses_shared_finish_map():
    for spelling, expected in _SPELLINGS.items():
        adapter = registry.fresh_adapter("nvidia-nim")
        deltas = adapter.decode_stream_event("", _finish_chunk(spelling))
        assert _finish_of(deltas) == expected, spelling


def test_nim_stream_tool_use_stop_is_not_downgraded_to_stop():
    """The reported shape: a vLLM-backed NIM spelling the tool stop 'tool_use'."""
    adapter = registry.fresh_adapter("nvidia-nim")
    deltas = adapter.decode_stream_event("", _finish_chunk("tool_use"))
    assert _finish_of(deltas) == "tool_call"


# --------------------------------------------------------------------------
# #270 — OpenRouter streaming
# --------------------------------------------------------------------------


def test_openrouter_stream_uses_shared_finish_map():
    for spelling, expected in _SPELLINGS.items():
        adapter = registry.fresh_adapter("openrouter")
        deltas = adapter.decode_stream_event("", _finish_chunk(spelling))
        assert _finish_of(deltas) == expected, spelling


def test_openrouter_stream_keeps_error_to_stop():
    """OpenRouter-specific: 'error' still lands on 'stop' after delegating."""
    adapter = registry.fresh_adapter("openrouter")
    deltas = adapter.decode_stream_event("", _finish_chunk("error"))
    assert _finish_of(deltas) == "stop"


# --------------------------------------------------------------------------
# #270 — OpenRouter non-streaming
# --------------------------------------------------------------------------


def test_openrouter_sync_uses_shared_finish_map():
    for spelling, expected in _SPELLINGS.items():
        adapter = registry.fresh_adapter("openrouter")
        body = {
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": spelling,
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        turn = adapter.decode_response(200, json.dumps(body).encode())
        assert turn.stop_reason == expected, spelling


# --------------------------------------------------------------------------
# #231 — OpenRouter streaming rejects a truthy non-list `reasoning_details`
# --------------------------------------------------------------------------


def test_openrouter_stream_tolerates_truthy_nonlist_reasoning_details():
    """`or []` defaults only a falsy value; `5`/`true` used to raise mid-stream."""
    for bad in (5, True, "abc", {"a": 1}, 3.5):
        adapter = registry.fresh_adapter("openrouter")
        chunk = json.dumps({"choices": [{
            "index": 0,
            "delta": {"content": "hi", "reasoning_details": bad},
        }]})
        deltas = adapter.decode_stream_event("", chunk)
        assert [d.text for d in deltas if isinstance(d, dl.TextDelta)] == ["hi"], bad
        assert not any(isinstance(d, dl.ThinkingDelta) for d in deltas), bad


def test_openrouter_stream_still_decodes_a_real_reasoning_details_list():
    adapter = registry.fresh_adapter("openrouter")
    chunk = json.dumps({"choices": [{
        "index": 0,
        "delta": {"reasoning_details": [{"type": "reasoning.text", "text": "tv"}]},
    }]})
    deltas = adapter.decode_stream_event("", chunk)
    assert [d.text for d in deltas if isinstance(d, dl.ThinkingDelta)] == ["tv"]


# --------------------------------------------------------------------------
# #232 — an explicit JSON null tool name/id must not reach the client
# --------------------------------------------------------------------------


def test_gemini_sync_null_tool_name_becomes_empty_string():
    adapter = registry.fresh_adapter("gemini")
    body = {
        "candidates": [{
            "content": {"parts": [{"functionCall": {"name": None, "args": {}}}]},
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    turn = adapter.decode_response(200, json.dumps(body).encode())
    assert [tc.name for tc in turn.tool_calls] == [""]
    assert turn.tool_calls[0].id != "call_None_0"


def test_gemini_stream_null_tool_name_becomes_empty_string():
    adapter = registry.fresh_adapter("gemini")
    chunk = json.dumps({
        "candidates": [{"content": {"parts": [{"functionCall": {"name": None, "args": {}}}]}}],
    })
    deltas = adapter.decode_stream_event("", chunk)
    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    assert [d.name for d in opens] == [""]
    assert opens[0].id != "call_None_0"


def test_anthropic_sync_null_tool_id_and_name_become_empty():
    adapter = registry.fresh_adapter("anthropic")
    body = {
        "id": "m", "type": "message", "role": "assistant", "model": "claude",
        "content": [{"type": "tool_use", "id": None, "name": None, "input": {}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    turn = adapter.decode_response(200, json.dumps(body).encode())
    assert [(tc.id, tc.name) for tc in turn.tool_calls] == [("", "")]


def test_anthropic_stream_null_tool_id_and_name_become_empty():
    adapter = registry.fresh_adapter("anthropic")
    frame = json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": None, "name": None},
    })
    deltas = adapter.decode_stream_event("content_block_start", frame)
    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    assert [(d.id, d.name) for d in opens] == [("", "")]


def test_anthropic_stream_valid_tool_id_and_name_are_preserved():
    adapter = registry.fresh_adapter("anthropic")
    frame = json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "tu_1", "name": "Grep"},
    })
    deltas = adapter.decode_stream_event("content_block_start", frame)
    opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
    assert [(d.id, d.name) for d in opens] == [("tu_1", "Grep")]


# --------------------------------------------------------------------------
# #233 — OpenCode's Responses route coerces usage instead of raw int()
# --------------------------------------------------------------------------


def test_opencode_responses_stream_tolerates_typed_wrong_usage():
    for bad in ("abc", [1], {"a": 1}, None, True):
        adapter = registry.fresh_adapter("opencode")
        adapter._last_route = "responses"
        chunk = json.dumps({"type": "response.completed", "response": {
            "usage": {"input_tokens": bad, "output_tokens": bad}}})
        deltas = adapter.decode_stream_event("response.completed", chunk)
        usage = [d for d in deltas if isinstance(d, dl.UsageFinal)]
        assert len(usage) == 1, bad
        assert usage[0].prompt == 0 and usage[0].output == 0, bad


def test_opencode_responses_stream_reads_real_usage():
    adapter = registry.fresh_adapter("opencode")
    adapter._last_route = "responses"
    chunk = json.dumps({"type": "response.completed", "response": {
        "usage": {"input_tokens": 11, "output_tokens": 7}}})
    deltas = adapter.decode_stream_event("response.completed", chunk)
    usage = next(d for d in deltas if isinstance(d, dl.UsageFinal))
    assert (usage.prompt, usage.output) == (11, 7)


# --------------------------------------------------------------------------
# #238 — probation_weight is clamped into (0, 1]
# --------------------------------------------------------------------------


def test_probation_weight_is_clamped_reachable():
    from wiwi.config import HealerSettings

    assert HealerSettings(probation_weight=0.0).probation_weight > 0
    assert HealerSettings(probation_weight=-1.0).probation_weight > 0
    assert HealerSettings(probation_weight=0.5).probation_weight == 0.5
    assert HealerSettings(probation_weight=1.0).probation_weight == 1.0
    assert HealerSettings(probation_weight=2.0).probation_weight == 1.0
    assert HealerSettings().probation_weight == 0.5


# --------------------------------------------------------------------------
# #251 — a literal `_nim_arg_*` parameter is not a minted alias
# --------------------------------------------------------------------------


def _nim_tool_aliases(props):
    from wiwi.ir import types as ir

    adapter = registry.fresh_adapter("nvidia-nim")
    req = ir.Request(model="m", messages=[], tools=[ir.Tool(
        name="t", description="",
        parameters_json_schema={"type": "object", "properties": props})])
    body = adapter.encode_request(
        req, "m", {"max_tokens": 100, "extra_body": {}, "drop_params": True})
    adapter.set_tool_context(body)
    return body["tools"][0]["function"]["parameters"], adapter._tool_aliases


def test_literal_alias_prefix_param_is_not_reversed():
    """`_nim_arg_foo` declared by the caller must stay `_nim_arg_foo`."""
    params, aliases = _nim_tool_aliases({"_nim_arg_foo": {"type": "string"}})
    assert "_nim_arg_foo" in params["properties"]
    assert aliases == {}


def test_alias_prefix_param_survives_a_collision_with_its_original():
    """`foo` + `_nim_arg_foo` are two declared params, not one."""
    params, aliases = _nim_tool_aliases({
        "foo": {"type": "string"},
        "_nim_arg_foo": {"type": "string"},
    })
    assert set(params["properties"]) == {"foo", "_nim_arg_foo"}
    assert aliases == {}


def test_genuine_unsafe_param_is_still_aliased_and_reversible():
    params, aliases = _nim_tool_aliases({"type": {"type": "string"}})
    assert "_nim_arg_type" in params["properties"]
    assert aliases == {"t": {"_nim_arg_type": "type"}}


def test_nested_unsafe_param_inside_items_is_aliased_and_collected():
    """The `Edit`-shaped schema: `items.properties.type` (AUDIT #246 + #251)."""
    params, aliases = _nim_tool_aliases({
        "edits": {"type": "array", "items": {
            "type": "object", "properties": {"type": {"type": "string"}}}},
    })
    items_props = params["properties"]["edits"]["items"]["properties"]
    assert "_nim_arg_type" in items_props
    assert aliases == {"t": {"_nim_arg_type": "type"}}


# --------------------------------------------------------------------------
# #226 — cycle_every_n must not saturate into a permanent no-op
# --------------------------------------------------------------------------


async def test_cycle_every_n_does_not_saturate_over_a_long_run():
    """A skewed pool must keep rotating after every key has served N times.

    Pre-fix the per-key counter only reset on *error*, so once every key in the
    pool reached ``cycle_every_n`` the exclusion set held them all, ``pick_key``
    took its "every key excluded" fallback, and the cadence became a permanent
    no-op — indistinguishable from ``cycle_every_n=0``. The existing
    ``test_fix_round41`` regression runs only 4 picks, which plain smooth-WRR
    satisfies.
    """
    from wiwi.config import (
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.core.context import RequestContext
    from wiwi.ir.types import Request
    from wiwi.router.router import Router, execute_with_retries

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="strong", key="sk-aaaaaaaaaaaaaaaa", weight=10),
                                     KeyDef(label="weak", key="sk-bbbbbbbbbbbbbbbb", weight=1)])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params={"provider": "p1", "model": "gpt-4o"})],
        router_settings=RouterSettings(cycle_every_n=3),
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    router = Router(cfg)

    def ctx_for(rid: str) -> RequestContext:
        c = RequestContext(surface="chat",
                           ir_req=Request(model="gpt-4o", messages=[]),
                           request_id=rid)
        c.group = "gpt-4o"
        return c

    async def call_one(dep, key, ctx):
        return key.label

    picks = [await execute_with_retries(router, ctx_for(f"r{i}"), call_one)
             for i in range(60)]

    best = cur = 1
    for i in range(1, len(picks)):
        cur = cur + 1 if picks[i] == picks[i - 1] else 1
        best = max(best, cur)
    assert best <= 3, (
        f"cadence saturated: longest run {best} over {len(picks)} picks ({picks})")
    # And the counters must not be allowed to climb without bound.
    assert all(n <= 3 for n in router._key_consec.values()), router._key_consec
