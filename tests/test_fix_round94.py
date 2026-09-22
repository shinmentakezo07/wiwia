"""Tool-call correctness across the translation path (AUDIT #271 + siblings).

A tool turn is reported to the client through a stop reason, and every surface
spells it differently (OpenAI ``tool_calls``, Anthropic ``tool_use``, Responses
a ``function_call`` item). Clients — the OpenAI SDK, Codex CLI, Claude Code —
branch on that reason to decide whether to dispatch tools, so emitting it with
no actionable tool call behind it either stalls the agent or ends a tool turn
early. The rule was re-derived, slightly differently and incompletely, in each
encoder; these tests pin the shared predicate and each surface's use of it.

* **#271** — the OpenAI Chat STREAMING encoder emitted ``finish_reason:
  "tool_calls"`` with no ``tool_calls`` array, because its A1 guard fired only
  when a builtin had been suppressed. Round-90 widened the reachable set by
  decoding the Anthropic spelling ``tool_use``. The non-streaming encoder on
  the same surface already guarded unconditionally.
* **server-tool stop** — the Anthropic encoder downgraded ``tool_use`` to
  ``end_turn`` when the turn was made only of provider-hosted calls, because
  the flag it keyed on was set in the client-dispatched path alone.
* **Responses** — no guard at all: a ``tool_call`` stop with no item.
* **validation** — truncated args (the common case) were flagged as invalid
  JSON while the non-streaming path repaired and accepted the same payload.
* **resume tape** — builtin tool deltas counted against the byte budget though
  they are excluded from the continuation, letting a search-heavy stream evict
  the deltas the continuation needs.
* **Gemini ids** — synthetic tool ids were per-stream, so two turns that called
  the same tool produced identical ids.
"""

from __future__ import annotations

from wiwi.ir import translation as tr
from wiwi.providers.gemini_adapter import _synthetic_tool_id
from wiwi.streaming import deltas as dl
from wiwi.streaming.resume import _delta_size
from wiwi.streaming.validation import validate_tool_args
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orr

# --------------------------------------------------------------------------
# the shared predicate
# --------------------------------------------------------------------------


def test_predicate_only_governs_tool_call():
    # Every non-tool_call reason is vacuously valid: the predicate answers
    # exactly one question, so a caller downgrades on False alone.
    for reason in ("stop", "length", "content_filter", "pause_turn",
                   "stop_sequence", "context_window_exceeded", "compaction"):
        assert tr.tool_call_finish_is_valid(reason) is True


def test_predicate_requires_a_call_behind_tool_call():
    assert tr.tool_call_finish_is_valid("tool_call") is False
    assert tr.tool_call_finish_is_valid("tool_call", emitted_calls=1) is True
    assert tr.tool_call_finish_is_valid(
        "tool_call", emitted_server_calls=1) is True
    assert tr.tool_call_finish_is_valid(
        "tool_call", emitted_calls=2, emitted_server_calls=3) is True


# --------------------------------------------------------------------------
# #271 — OpenAI Chat, streaming and non-streaming
# --------------------------------------------------------------------------


def test_chat_stream_tool_call_with_no_calls_downgrades():
    """The exact #271 shape: a tool_call turn that produced no tool frame."""
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    enc.feed(dl.Finish("tool_call"))
    blob = enc.final_frame().decode()
    assert '"tool_calls"' not in blob
    assert '"finish_reason":"stop"' in blob


def test_chat_stream_tool_call_with_a_real_call_is_preserved():
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    enc.feed(dl.ToolCallOpen(index=0, id="c1", name="f"))
    enc.feed(dl.ToolCallArgsDelta(index=0, args_fragment='{"a":1}'))
    enc.feed(dl.ToolCallClose(index=0))
    enc.feed(dl.Finish("tool_call"))
    blob = enc.final_frame().decode()
    assert '"finish_reason":"tool_calls"' in blob


def test_chat_stream_guard_also_applies_to_an_explicit_stop_argument():
    """final_frame(stop=...) bypasses feed(Finish), so the guard must live
    there too — a failover/resume path rebuilds the frame from state that
    never went through feed."""
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    blob = enc.final_frame(stop="tool_call").decode()
    assert '"finish_reason":"stop"' in blob


def test_chat_stream_suppressed_builtin_still_downgrades():
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    enc.feed(dl.ToolCallOpen(index=0, id="s1", name="web_search",
                             builtin="web_search", block_type="server_tool_use"))
    enc.feed(dl.ToolCallClose(index=0))
    enc.feed(dl.Finish("tool_call"))
    blob = enc.final_frame().decode()
    assert '"finish_reason":"stop"' in blob
    assert "web_search" not in blob


def test_chat_encoder_drops_a_suppressed_builtins_args():
    """The builtin's arguments must never reach the client as a function
    frame; this is now tracked by intent, not by the index being absent from
    _tool_indices (the same condition as a contract-illegal ArgsDelta)."""
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    enc.feed(dl.ToolCallOpen(index=0, id="s1", name="web_search",
                             builtin="web_search", block_type="server_tool_use"))
    frames = [enc.feed(dl.ToolCallArgsDelta(index=0, args_fragment='{"q":1}'))]
    frames.append(enc.feed(dl.ToolCallClose(index=0)))
    assert all(f is None for f in frames)


# --------------------------------------------------------------------------
# Anthropic — provider-hosted calls count as a tool turn
# --------------------------------------------------------------------------


def _anthropic_server_turn(*, with_result: bool) -> str:
    """Every frame the encoder emits for a provider-hosted turn.

    The server call's block is emitted by feed(ServerToolResultDelta), not by
    final_frame, so the terminal frame alone does not carry it.
    """
    enc = am.AnthropicStreamEncoder("m", "r")
    chunks = [enc.feed(dl.StreamStart(model="m", group=""))]
    chunks.append(enc.feed(dl.ToolCallOpen(
        index=0, id="s1", name="web_search",
        builtin="web_search", block_type="server_tool_use")))
    chunks.append(enc.feed(dl.ToolCallClose(index=0)))
    if with_result:
        chunks.append(enc.feed(dl.ServerToolResultDelta(
            index=0,
            block={"type": "web_search_tool_result", "tool_use_id": "s1",
                   "content": []})))
    chunks.append(enc.feed(dl.Finish("tool_call")))
    chunks.append(enc.final_frame())
    return b"".join(c for c in chunks if c).decode()


def test_anthropic_server_only_turn_keeps_tool_use_stop():
    blob = _anthropic_server_turn(with_result=True)
    assert '"stop_reason":"tool_use"' in blob
    assert "server_tool_use" in blob


def test_anthropic_half_trace_still_downgrades():
    """No result block: the call is dropped, so the stop reason must follow —
    an unpaired server_tool_use with a tool_use stop is invalid."""
    blob = _anthropic_server_turn(with_result=False)
    assert '"stop_reason":"end_turn"' in blob
    assert "server_tool_use" not in blob


def test_anthropic_sync_server_only_turn_keeps_tool_use_stop():
    import wiwi.ir.types as ir
    turn = ir.AssistantTurn(
        tool_calls=[ir.ToolUsePart(id="s1", name="web_search", args={"query": "x"},
                                   builtin="web_search",
                                   block_type="server_tool_use")],
        server_blocks=[{"type": "web_search_tool_result", "tool_use_id": "s1",
                        "content": []}],
        stop_reason="tool_call")
    body = am.encode_response(None, turn, "m", "rid")  # type: ignore[arg-type]
    assert body["stop_reason"] == "tool_use"


def test_anthropic_sync_tool_call_with_nothing_downgrades():
    import wiwi.ir.types as ir
    turn = ir.AssistantTurn(text="hi", stop_reason="tool_call")
    body = am.encode_response(None, turn, "m", "rid")  # type: ignore[arg-type]
    assert body["stop_reason"] == "end_turn"


# --------------------------------------------------------------------------
# Responses — the missing guard
# --------------------------------------------------------------------------
def test_responses_surface_has_no_tool_call_stop_reason():
    """The Responses surface publishes turn shape via its output item array
    plus `status`, not a stop_reason string, so the #271-class defect cannot
    arise here: a `tool_call` stop with no item produces no function_call item
    and a plain completed status."""
    enc = orr.ResponsesStreamEncoder("m", "r")
    enc.feed(dl.StreamStart(model="m", group=""))
    enc.feed(dl.Finish("tool_call"))
    blob = enc._completed().decode()
    assert '"type":"function_call"' not in blob
    assert '"status":"completed"' in blob


def test_responses_stream_real_call_emits_a_function_call_item():
    enc = orr.ResponsesStreamEncoder("m", "r")
    enc.feed(dl.StreamStart(model="m", group=""))
    enc.feed(dl.ToolCallOpen(index=0, id="c1", name="f"))
    enc.feed(dl.ToolCallArgsDelta(index=0, args_fragment="{}"))
    enc.feed(dl.ToolCallClose(index=0))
    enc.feed(dl.Finish("tool_call"))
    blob = enc._completed().decode()
    assert '"type":"function_call"' in blob
    assert '"name":"f"' in blob


def test_responses_sync_tool_call_with_no_items_downgrades():
    import wiwi.ir.types as ir
    turn = ir.AssistantTurn(text="hi", stop_reason="tool_call")
    body = orr.encode_response(None, turn, "m", "rid")  # type: ignore[arg-type]
    assert body["status"] == "completed"
    assert body["output"][-1]["type"] == "message"


# --------------------------------------------------------------------------
# validation repairs before flagging
# --------------------------------------------------------------------------


def test_validation_repairs_truncated_args_before_flagging():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}},
              "required": ["a"]}
    ok, msg = validate_tool_args("f", '{"a": 1', schema)
    assert ok, msg


def test_validation_still_flags_unrepairable_args():
    schema = {"type": "object"}
    ok, msg = validate_tool_args("f", "not json at all", schema)
    assert not ok
    assert "not valid JSON" in msg


def test_validation_flags_a_property_type_mismatch_after_repair():
    """Repair only rescues SHAPE; a schema violation inside the repaired
    object is still reported."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    ok, msg = validate_tool_args("f", '{"a": [1, 2', schema)
    assert not ok
    assert "a" in msg


# --------------------------------------------------------------------------
# resume tape accounting excludes builtins
# --------------------------------------------------------------------------


def test_tape_accounting_ignores_builtin_tool_deltas():
    # A hosted call and its result never re-enter the continuation, so they
    # must not consume the budget the client-dispatched deltas need.
    assert _delta_size(dl.ToolCallOpen(
        index=0, id="s1", name="web_search", builtin="web_search")) == 0
    assert _delta_size(dl.ServerToolResultDelta(
        index=0, block={"big": "x" * 5000})) == 0


def test_tape_accounting_still_charges_client_calls():
    assert _delta_size(dl.ToolCallOpen(index=0, id="c1", name="f")) > 0
    assert _delta_size(dl.ToolCallArgsDelta(index=0, args_fragment="12345")) == 5
    assert _delta_size(dl.ToolCallClose(index=0)) > 0


# --------------------------------------------------------------------------
# Gemini synthetic tool ids are unique across turns
# --------------------------------------------------------------------------


def test_gemini_synthetic_ids_do_not_collide_across_turns():
    first = [_synthetic_tool_id("get_weather") for _ in range(2)]
    # A second turn, in a separate request, for the same tool.
    second = [_synthetic_tool_id("get_weather") for _ in range(2)]
    assert set(first).isdisjoint(second)
    assert len(set(first + second)) == 4
    assert all(i.startswith("call_get_weather_") for i in first + second)


def test_gemini_synthetic_id_handles_empty_name():
    assert _synthetic_tool_id("").startswith("call_x_")


# --------------------------------------------------------------------------
# adapter translation warnings reach the request context
# --------------------------------------------------------------------------


def test_gemini_records_disable_parallel_warning():
    from wiwi.ir import types as ir
    from wiwi.providers.gemini_adapter import GeminiAdapter
    ad = GeminiAdapter()
    req = ir.Request(
        model="g", messages=[ir.Message(role="user",
                                        parts=[ir.TextPart("hi")])],
        tools=[ir.Tool(name="f", description="", parameters_json_schema={})],
        gen_params=ir.GenParams(disable_parallel_tool_use=True))
    ad.encode_request(req, "gemini-2.5-pro", {})
    assert any("disable_parallel_tool_use" in w
               for w in ad.translation_warnings)
    # Drained once, then empty.
    from wiwi.providers.base import take_adapter_warnings
    drained = take_adapter_warnings(ad)
    assert drained and take_adapter_warnings(ad) == []


def test_take_adapter_warnings_is_total_for_adapters_without_the_attr():
    from wiwi.providers.base import take_adapter_warnings

    class Bare:
        pass

    assert take_adapter_warnings(Bare()) == []
    assert take_adapter_warnings(object()) == []
