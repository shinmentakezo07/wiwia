"""Round-49 regressions: the round-47 open findings (#133-#141).

Every test is discriminating: it fails against the pre-fix source and passes
after the fix. Where a naive fix would overcorrect, a control is included.

  * #133 — a ``[DONE]``-terminated stream left every tool call open and made
    the gateway synthesize ``Finish("stop")`` for a turn that produced tools.
  * #134 — Gemini emitted a full terminal tail for *any* chunk carrying
    ``usageMetadata``, truncating the stream at the first such chunk.
  * #135 — OpenRouter emitted ``ToolCallArgsDelta`` with no preceding
    ``ToolCallOpen`` when a tool chunk carried args but no ``id``.
  * #136 — a non-dict SSE frame raised ``AttributeError`` out of the Gemini
    and NIM decoders (Gemini's is reachable through OpenCode's gemini route).
  * #138 — a JSON Schema ``type`` array crashed the stream pump mid-response.
  * #139 — ``_repair_truncated_json`` emitted a lone surrogate.
  * #140 — the #68 eviction guard was dead at its only call site.
  * #141 — typeless properties were always rejected.
  * #137 — ``_emitted_opens`` write-only dead state.
"""

from __future__ import annotations

import asyncio
import json

import orjson
import pytest

from wiwi.streaming import deltas as dl


def _chunk(**delta) -> str:
    return orjson.dumps({"choices": [{"index": 0, "delta": delta}]}).decode()


def _finish(reason: str) -> str:
    return orjson.dumps(
        {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}).decode()


def _kinds(deltas) -> list[str]:
    return [type(d).__name__ for d in deltas]


def _assert_open_before_args(deltas, where: str) -> None:
    """Every ToolCallArgsDelta must have a preceding, still-open ToolCallOpen
    for the same index — the streaming contract's strict nesting."""
    open_idx: set[int] = set()
    for d in deltas:
        if isinstance(d, dl.ToolCallOpen):
            open_idx.add(d.index)
        elif isinstance(d, dl.ToolCallArgsDelta):
            assert d.index in open_idx, (
                f"{where}: ToolCallArgsDelta(index={d.index}) with no open "
                f"ToolCallOpen — encoders drop it: {_kinds(deltas)}"
            )
        elif isinstance(d, dl.ToolCallClose):
            open_idx.discard(d.index)


# ---------------------------------------------------------------------------
# #133 — [DONE] must flush open tool state, not just terminate
# ---------------------------------------------------------------------------


def test_openai_done_flushes_open_tool_calls():
    """A [DONE]-terminated stream that delivered tool calls must close them
    before StreamEnd. Pre-fix the adapter returned a bare ``[StreamEnd()]``,
    leaving ``_open_tool_indices`` populated, so the gateway's ``finish is
    None`` branch synthesized ``Finish("stop")`` for a turn that produced
    tool calls."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    ad = OpenAIAdapter()
    ad.reset()
    ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "t0", "function": {"name": "get_weather",
                                              "arguments": '{"city":"SF"}'}}]))
    ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 1, "id": "t1", "function": {"name": "get_time",
                                              "arguments": '{"tz":"PT"}'}}]))
    tail = ad.decode_stream_event("", "[DONE]")

    _assert_open_before_args(tail, "[DONE] tail")
    closes = [d for d in tail if isinstance(d, dl.ToolCallClose)]
    assert {c.index for c in closes} == {0, 1}, (
        f"[DONE] left tool calls open: {_kinds(tail)}"
    )
    assert tail[-1].__class__ is dl.StreamEnd, f"StreamEnd must be last: {_kinds(tail)}"
    # The adapter's own state must not survive the flush (a reused adapter
    # would otherwise carry a stale open index into the next stream).
    assert not ad._open_tool_indices, "open indices survived [DONE]"
    assert not ad._pending_opens, "deferred opens survived [DONE]"


def test_openai_done_emits_finish_tool_call_when_tools_were_delivered():
    """The flushed tool state must also produce a content-derived Finish, so
    the gateway does not synthesize ``"stop"`` (AUDIT #133)."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    ad = OpenAIAdapter()
    ad.reset()
    ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "t0", "function": {"name": "f",
                                              "arguments": '{"a":1}'}}]))
    tail = ad.decode_stream_event("", "[DONE]")
    finishes = [d for d in tail if isinstance(d, dl.Finish)]
    assert finishes, f"[DONE] after a tool call emitted no Finish: {_kinds(tail)}"
    assert finishes[0].stop_reason == "tool_call", (
        f"stop reason must reflect the delivered tool call, got "
        f"{finishes[0].stop_reason!r}"
    )


def test_openai_done_without_tools_still_emits_bare_stream_end():
    """Control: a plain text stream ending in [DONE] must NOT gain a Finish or
    any other frame — the gateway's round-15 synthesis owns that path, and a
    second Finish would double-terminate."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    ad = OpenAIAdapter()
    ad.reset()
    ad.decode_stream_event("", _chunk(content="hello"))
    tail = ad.decode_stream_event("", "[DONE]")
    assert _kinds(tail) == ["StreamEnd"], (
        f"a tool-free [DONE] must stay a bare StreamEnd, got {_kinds(tail)}"
    )


def test_openai_done_after_finish_reason_does_not_double_close():
    """Control: the common OpenAI shape sends finish_reason *then* [DONE]. The
    finish sweep already closed everything, so [DONE] must not emit a second
    Finish or a Close for an index that is no longer open."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    ad = OpenAIAdapter()
    ad.reset()
    ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "t0", "function": {"name": "f",
                                              "arguments": '{"a":1}'}}]))
    first = ad.decode_stream_event("", _finish("tool_calls"))
    assert any(isinstance(d, dl.ToolCallClose) for d in first)
    tail = ad.decode_stream_event("", "[DONE]")
    assert not any(isinstance(d, dl.ToolCallClose) for d in tail), (
        f"[DONE] after finish_reason emitted a duplicate Close: {_kinds(tail)}"
    )
    assert not any(isinstance(d, dl.Finish) for d in tail), (
        f"[DONE] after finish_reason emitted a duplicate Finish: {_kinds(tail)}"
    )


def test_openrouter_done_flushes_open_tool_calls():
    """OpenRouter overrides ``decode_stream_event`` and had the same bare
    ``[StreamEnd()]`` early return (AUDIT #133, second site)."""
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    ad = OpenRouterAdapter()
    ad.reset()
    ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "t0", "function": {"name": "f",
                                              "arguments": '{"a":1}'}}]))
    tail = ad.decode_stream_event("", "[DONE]")
    closes = [d for d in tail if isinstance(d, dl.ToolCallClose)]
    assert {c.index for c in closes} == {0}, (
        f"OpenRouter [DONE] left the tool call open: {_kinds(tail)}"
    )
    assert tail[-1].__class__ is dl.StreamEnd
    assert not ad._open_tool_indices
    assert not ad._synthesized_opens, "synthesized markers survived [DONE]"


def test_openai_synthesized_open_carries_the_tool_name():
    """The synthesized Open must not be anonymous: the encoder emits it as a
    ``tool_use`` block, and a block with ``name: ""`` cannot be dispatched."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    ad = OpenAIAdapter()
    ad.reset()
    out = ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "function": {"name": "get_weather",
                                  "arguments": '{"city":"SF"}'}}]))
    opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1 and opens[0].name == "get_weather", (
        f"synthesized Open lost the name: {opens}"
    )


def test_opencode_done_flushes_open_tool_calls_on_chat_route():
    """OpenCode's chat route delegates to a sub-adapter (AUDIT #133).

    ``glm-*`` is a chat-route model — ``gpt-*`` would take the responses
    route, where ``[DONE]`` is already handled separately.
    """
    from wiwi.providers.opencode_adapter import OpencodeAdapter, route_for_model

    assert route_for_model("glm-5.3-flash") == "chat", "test setup: need chat route"
    ad = OpencodeAdapter()
    ad.reset()
    ad.build_url("https://opencode.ai/zen/v1", "glm-5.3-flash", True)
    ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "t0", "function": {"name": "f",
                                              "arguments": '{"a":1}'}}]))
    tail = ad.decode_stream_event("", "[DONE]")
    closes = [d for d in tail if isinstance(d, dl.ToolCallClose)]
    assert {c.index for c in closes} == {0}, (
        f"OpenCode [DONE] left the tool call open: {_kinds(tail)}"
    )


# ---------------------------------------------------------------------------
# #134 — Gemini: usageMetadata on a NON-terminal chunk must not terminate
# ---------------------------------------------------------------------------


def test_gemini_intermediate_usage_does_not_terminate_the_stream():
    """Gemini 2.5 / Vertex attach ``usageMetadata`` to intermediate chunks.
    Pre-fix the ``elif u:`` arm emitted UsageFinal+Finish+StreamEnd for any
    usage-bearing frame, so the consumer broke on the first StreamEnd and the
    rest of the answer was silently truncated at HTTP 200."""
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    first = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [{"text": "Hel"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "totalTokenCount": 5},
    }).decode())
    kinds = _kinds(first)
    assert "TextDelta" in kinds
    assert "StreamEnd" not in kinds, (
        f"an intermediate usage-bearing chunk terminated the stream: {kinds}"
    )
    assert "Finish" not in kinds, f"intermediate chunk emitted Finish: {kinds}"

    # The stream continues to deliver content.
    second = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [{"text": "lo!"}]}}]}).decode())
    assert [d.text for d in second if isinstance(d, dl.TextDelta)] == ["lo!"]


def test_gemini_terminal_usage_still_completes_cleanly():
    """Control for #76: the genuine terminal shape (usage, empty parts, no
    finishReason) must still emit the full tail."""
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [{"text": "hi"}]}}]}).decode())
    tail = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": []}}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    }).decode())
    kinds = _kinds(tail)
    assert "UsageFinal" in kinds, f"terminal usage frame emitted no UsageFinal: {kinds}"
    assert "Finish" in kinds, f"terminal usage frame emitted no Finish: {kinds}"
    assert "StreamEnd" in kinds, f"terminal usage frame emitted no StreamEnd: {kinds}"


def test_gemini_finish_reason_with_usage_still_completes():
    """Control: an explicit finishReason still terminates regardless of parts."""
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    tail = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [{"text": "done"}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    }).decode())
    kinds = _kinds(tail)
    assert "Finish" in kinds and "StreamEnd" in kinds, kinds


def test_gemini_usage_without_parts_but_with_candidate_text_still_continues():
    """Control: a chunk that carries BOTH usage and real content parts must
    never be mistaken for the terminal frame."""
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    out = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [{"text": "more"}]}}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }).decode())
    kinds = _kinds(out)
    assert "TextDelta" in kinds and "StreamEnd" not in kinds, kinds


# ---------------------------------------------------------------------------
# #135 — OpenRouter must synthesize an Open when args arrive with no id
# ---------------------------------------------------------------------------


def test_openrouter_args_without_id_synthesizes_open():
    """Without the synthesize branch, args for an unopened index are emitted
    bare; every encoder then drops them and the tool call vanishes."""
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    ad = OpenRouterAdapter()
    ad.reset()
    out: list[dl.IRStreamDelta] = []
    for data in [
        _chunk(tool_calls=[{"index": 0, "function": {"name": "f",
                                                     "arguments": '{"a":'}}]),
        _chunk(tool_calls=[{"index": 0, "function": {"arguments": '1}'}}]),
        _finish("tool_calls"),
    ]:
        out.extend(ad.decode_stream_event("", data))

    _assert_open_before_args(out, "openrouter args-without-id")
    opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1, f"expected exactly one synthesized Open: {_kinds(out)}"
    assert opens[0].index == 0
    # The synthesized Open must carry the name from the same chunk — emitting
    # an empty name leaves the client with a tool_use block it cannot dispatch.
    assert opens[0].name == "f", (
        f"the synthesized Open lost the tool name: {opens[0]!r}"
    )


def test_openrouter_args_without_id_matches_openai_adapter():
    """The two adapters must agree on the same three chunks — that divergence
    is what made this a bug."""
    from wiwi.providers.openai_adapter import OpenAIAdapter
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    frames = [
        _chunk(tool_calls=[{"index": 0, "function": {"name": "f",
                                                     "arguments": '{"a":'}}]),
        _chunk(tool_calls=[{"index": 0, "function": {"arguments": '1}'}}]),
        _finish("tool_calls"),
    ]

    def drive(ad):
        ad.reset()
        out = []
        for f in frames:
            out.extend(ad.decode_stream_event("", f))
        return _kinds(out)

    assert drive(OpenRouterAdapter()) == drive(OpenAIAdapter())


def test_openrouter_late_id_after_synthesized_open_is_adopted():
    """A real id arriving after a synthesized Open must be adopted, not open a
    second tool call on the same index."""
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    ad = OpenRouterAdapter()
    ad.reset()
    out: list[dl.IRStreamDelta] = []
    out.extend(ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "function": {"name": "f", "arguments": '{"a":'}}])))
    out.extend(ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "call_real", "function": {"arguments": '1}'}}])))
    opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1, (
        f"a late id must not open a second call on the same index: {_kinds(out)}"
    )
    _assert_open_before_args(out, "openrouter late id")


def test_openrouter_id_first_then_args_unchanged():
    """Control: the ordinary shape (id on the first chunk) must keep deferring
    the Open until args arrive, exactly as before."""
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    ad = OpenRouterAdapter()
    ad.reset()
    out: list[dl.IRStreamDelta] = []
    out.extend(ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "id": "c0", "function": {"name": "f", "arguments": ""}}])))
    assert not any(isinstance(d, dl.ToolCallOpen) for d in out), (
        "the Open must stay deferred until args or finish"
    )
    out.extend(ad.decode_stream_event("", _chunk(tool_calls=[
        {"index": 0, "function": {"arguments": '{"a":1}'}}])))
    _assert_open_before_args(out, "openrouter id-first")
    opens = [d for d in out if isinstance(d, dl.ToolCallOpen)]
    assert len(opens) == 1 and opens[0].id == "c0" and opens[0].name == "f"


# ---------------------------------------------------------------------------
# #136 — non-dict SSE frames must be ignored, not raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("junk", ["null", "42", '"hi"', "[1,2,3]"])
def test_gemini_non_dict_frame_is_ignored(junk):
    """A junk frame landed in the pump's generic handler, which cools the
    deployment and feeds the key's retirement ladder for a frame carrying no
    semantic content."""
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    out = a.decode_stream_event("", junk)  # must not raise
    assert out == [] or all(not isinstance(d, dl.StreamError) for d in out), (
        f"junk frame produced {_kinds(out)}"
    )


@pytest.mark.parametrize("junk", ["null", "42", '"hi"', "[1,2,3]"])
def test_nim_non_dict_frame_is_ignored(junk):
    from wiwi.providers.nim_adapter import NimAdapter

    a = NimAdapter()
    out = a.decode_stream_event("", junk)  # must not raise
    assert out == [] or all(not isinstance(d, dl.StreamError) for d in out)


@pytest.mark.parametrize("junk", ["null", "42", '"hi"', "[1,2,3]"])
def test_opencode_gemini_route_non_dict_frame_is_ignored(junk):
    """OpenCode's gemini route delegates to GeminiAdapter, so it inherited the
    same crash."""
    from wiwi.providers.opencode_adapter import OpencodeAdapter

    a = OpencodeAdapter()
    a.reset()
    a.build_url("https://opencode.ai/zen/v1", "gemini-3.1-pro", True)
    a.decode_stream_event("", junk)  # must not raise


# ---------------------------------------------------------------------------
# #138 — a JSON Schema `type` array must not crash the validator
# ---------------------------------------------------------------------------


def test_union_type_array_in_property_does_not_crash():
    """``{"type": ["string","null"]}`` is the canonical nullable encoding from
    OpenAI structured outputs, Pydantic v2, and Claude Code's own tool
    definitions. Pre-fix the ``list`` was hashed by ``type_map.get(...)`` and
    raised ``TypeError: unhashable type: 'list'`` out of the stream pump."""
    from wiwi.streaming.validation import validate_tool_args

    ok, msg = validate_tool_args(
        "t", '{"a": "x"}',
        {"type": "object", "properties": {"a": {"type": ["string", "null"]}}})
    assert ok, f"a legal union type was rejected: {msg}"


def test_union_type_array_at_top_level_does_not_crash():
    from wiwi.streaming.validation import validate_tool_args

    ok, msg = validate_tool_args(
        "t", '{"a": 1}',
        {"type": ["object", "null"], "properties": {"a": {"type": "integer"}}})
    assert ok, f"a legal top-level union was rejected: {msg}"


def test_union_type_array_still_rejects_a_real_mismatch():
    """Control: treating a list as a union must not disable checking — a value
    matching none of the members is still a violation."""
    from wiwi.streaming.validation import validate_tool_args

    ok, _ = validate_tool_args(
        "t", '{"a": 42}',
        {"type": "object", "properties": {"a": {"type": ["string", "null"]}}})
    assert not ok, "a value matching no union member must still be rejected"


def test_union_type_array_accepts_null_for_nullable_property():
    from wiwi.streaming.validation import validate_tool_args

    ok, msg = validate_tool_args(
        "t", '{"a": null}',
        {"type": "object", "properties": {"a": {"type": ["string", "null"]}}})
    assert ok, f"null must satisfy a [string,null] union: {msg}"


# ---------------------------------------------------------------------------
# #139 — the repair must not emit a lone surrogate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("truncated", [
    '{"emoji": "\\ud83d',        # high surrogate, pair cut
    '{"emoji": "\\ud83d\\ude',   # mid low-surrogate
    '{"emoji": "\\ude00',        # bare low surrogate
])
def test_repaired_truncated_json_is_serializable(truncated):
    """A token-limit cut mid-surrogate-pair repaired to a lone surrogate, which
    ``json.loads`` accepts but ``orjson.dumps``/httpx reject — the user's turn
    was lost to a 500."""
    from wiwi.streaming.partial_json import _repair_truncated_json

    repaired = _repair_truncated_json(truncated)
    parsed = json.loads(repaired)  # must stay parseable
    orjson.dumps(parsed)  # pre-fix: TypeError on the lone surrogate


def test_valid_escapes_are_not_mangled_by_the_surrogate_fix():
    """Control: complete escapes and escaped backslashes must survive intact."""
    from wiwi.streaming.partial_json import _repair_truncated_json

    cases = [
        ('{"e": "\\ud83d\\ude00"}', "\U0001f600"),   # complete pair
        ('{"p": "C:\\\\u0f"}', "C:\\u0f"),           # escaped backslash + literal
        ('{"s": "\\u0041"}', "A"),                   # complete BMP escape
    ]
    for text, expected in cases:
        assert json.loads(_repair_truncated_json(text)) == json.loads(text), (
            f"a complete escape was mangled: {text!r}"
        )
        assert list(json.loads(_repair_truncated_json(text)).values()) == [expected]


def test_lone_surrogate_is_actually_removed_not_merely_escaped():
    """The repaired value must contain no surrogate code points at all — an
    escaped-but-present surrogate still breaks ``ensure_ascii=False`` encoders."""
    from wiwi.streaming.partial_json import _repair_truncated_json

    parsed = json.loads(_repair_truncated_json('{"emoji": "\\ud83d'))
    for value in parsed.values():
        for ch in value:
            assert not (0xD800 <= ord(ch) <= 0xDFFF), (
                f"surrogate U+{ord(ch):04X} survived the repair"
            )


# ---------------------------------------------------------------------------
# #140 — the #68 eviction guard must be reachable at its call site
# ---------------------------------------------------------------------------


def test_head_evicted_guard_fires_for_the_last_consumed_seq():
    """``_attempt_resume`` passed ``tape.seq - 1`` — the *next* seq minus one,
    which is always >= the first surviving seq, so ``first > last_seq + 1`` was
    structurally False. The guard must be able to fire for the argument the
    call site actually computes."""
    from wiwi.streaming import deltas as dl
    from wiwi.streaming.resume import StreamTape

    tape = StreamTape(max_bytes=96)
    tape.append(dl.TextDelta("keep"))          # seq 1
    tape.append(dl.ToolCallOpen(index=0, id="c0", name="f0"))
    tape.append(dl.TextDelta("y" * 4096))      # evicts the head
    tape.append(dl.TextDelta("z" * 4096))
    tape.append(dl.ToolCallClose(index=0))
    assert tape._entries, "test setup: survivors must remain"
    assert tape._entries[0].seq > 1, "test setup: head must be evicted"
    # The old call-site argument can never trip the guard...
    assert tape.head_evicted(tape.seq - 1) is False, (
        "pre-fix argument unexpectedly fired — the finding needs re-checking"
    )
    # ...but the last seq the consumer actually saw can.
    assert tape.head_evicted(0) is True


def test_attempt_resume_refuses_when_the_tape_head_was_evicted():
    """End-to-end: a resume whose continuation prefix would be silently
    partial must be refused, so the caller retries as a fresh attempt.

    ``_pump`` is mocked to succeed, so the ONLY thing that can produce a
    refusal here is the #68 eviction guard — pre-fix it was dead and this
    call returned ``(True, task)``.
    """
    from wiwi.config import (
        DeploymentParams,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.ir import types as ir
    from wiwi.router.router import Router
    from wiwi.streaming.resume import StreamTape

    config = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai-compatible",
                               base_url="http://up1",
                               keys=[KeyDef(label="k1", key="secret")]),
                   ProviderDef(name="p2", provider="openai-compatible",
                               base_url="http://up2",
                               keys=[KeyDef(label="k2", key="secret")])],
        model_list=[ModelEntry(model_name="primary",
                               wiwi_params=DeploymentParams(provider="p1", model="m1")),
                    ModelEntry(model_name="fb",
                               wiwi_params=DeploymentParams(provider="p2", model="m2"))],
        router_settings=RouterSettings(stream_resume="content_only",
                                       stream_resume_max_retries=1,
                                       fallbacks={"primary": ["fb"]}),
    )
    gw = Gateway(Router(config), CostEngine())
    ctx = RequestContext(
        surface="chat",
        ir_req=ir.Request(model="primary", messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")])]),
        group="primary")

    async def mock_pump(dep, key, resume_ctx, q, ready, err_box):
        ready.set()
        err_box[0] = None

    gw._pump = mock_pump

    # A tape whose head (including the tool call's Open) was evicted, so
    # replay_tool_calls() would drop a call the client already saw. A cap of
    # 64 bytes evicts the Open (12 bytes) and the oversized delta after it,
    # while the short trailing delta survives — so the refusal cannot come
    # from the empty-replay path, only from the eviction guard.
    tape = StreamTape(max_bytes=64)
    tape.append(dl.ToolCallOpen(index=0, id="c0", name="f0"))  # seq 1
    tape.append(dl.TextDelta("y" * 4096))                      # evicts seq 1
    tape.append(dl.TextDelta("z" * 60))                        # seq 3, survives
    assert tape._entries and tape._entries[0].seq > 1, "test setup: head evicted"
    assert tape.replay_text(), "test setup: content survived, so only the guard can refuse"

    queue: asyncio.Queue = asyncio.Queue()
    resumed, task = asyncio.run(gw._attempt_resume(ctx, tape, queue))
    assert resumed is False, "a partial continuation prefix must be refused"
    assert task is None


def test_attempt_resume_still_proceeds_when_the_tape_is_intact():
    """Control: an un-evicted tape must still resume — the guard must not
    refuse every resume."""
    from wiwi.config import (
        DeploymentParams,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.ir import types as ir
    from wiwi.router.router import Router
    from wiwi.streaming.resume import StreamTape

    config = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai-compatible",
                               base_url="http://up1",
                               keys=[KeyDef(label="k1", key="secret")]),
                   ProviderDef(name="p2", provider="openai-compatible",
                               base_url="http://up2",
                               keys=[KeyDef(label="k2", key="secret")])],
        model_list=[ModelEntry(model_name="primary",
                               wiwi_params=DeploymentParams(provider="p1", model="m1")),
                    ModelEntry(model_name="fb",
                               wiwi_params=DeploymentParams(provider="p2", model="m2"))],
        router_settings=RouterSettings(stream_resume="content_only",
                                       stream_resume_max_retries=1,
                                       fallbacks={"primary": ["fb"]}),
    )
    gw = Gateway(Router(config), CostEngine())
    ctx = RequestContext(
        surface="chat",
        ir_req=ir.Request(model="primary", messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")])]),
        group="primary")

    tape = StreamTape(max_bytes=1 << 20)  # no eviction
    tape.append(dl.TextDelta("partial response"))
    queue: asyncio.Queue = asyncio.Queue()

    async def mock_pump(dep, key, resume_ctx, q, ready, err_box):
        ready.set()
        err_box[0] = None

    gw._pump = mock_pump
    resumed, task = asyncio.run(gw._attempt_resume(ctx, tape, queue))
    assert resumed is True, "an intact tape must still resume"
    if task is not None:
        task.cancel()


# ---------------------------------------------------------------------------
# #141 — a property with no `type` is unconstrained, not "must be null"
# ---------------------------------------------------------------------------


def test_typeless_property_is_not_rejected():
    """Absent ``type`` means unconstrained. Pre-fix the ``not want``
    short-circuit rejected before ``_check_type(value, None)`` — which returns
    True — was ever consulted."""
    from wiwi.streaming.validation import validate_tool_args

    ok, msg = validate_tool_args(
        "t", '{"a": "x"}',
        {"type": "object", "properties": {"a": {"description": "a path"}}})
    assert ok, f"a typeless (unconstrained) property was rejected: {msg}"


@pytest.mark.parametrize("spec", [
    {"description": "d"},                      # description-only
    {"enum": ["x", "y"]},                      # enum-only
    {"anyOf": [{"type": "string"}]},           # anyOf-only
    {"$ref": "#/$defs/Thing"},                 # $ref-only
    {"const": "fixed"},                        # const-only
])
def test_all_typeless_property_shapes_pass(spec):
    from wiwi.streaming.validation import validate_tool_args

    ok, msg = validate_tool_args(
        "t", '{"a": "x"}', {"type": "object", "properties": {"a": spec}})
    assert ok, f"typeless shape {spec} rejected: {msg}"


def test_declared_property_type_is_still_enforced():
    """Control: removing the ``not want`` short-circuit must not disable the
    per-property check that validation.py:78-80 added as a correctness fix."""
    from wiwi.streaming.validation import validate_tool_args

    ok, _ = validate_tool_args(
        "t", '{"a": "x"}',
        {"type": "object", "properties": {"a": {"type": "integer"}}})
    assert not ok, "a declared-type mismatch must still be rejected"


# ---------------------------------------------------------------------------
# #137 — _emitted_opens must not be unmarked dead state
# ---------------------------------------------------------------------------


def test_emitted_opens_is_not_write_only_dead_state():
    """The field was written once and never read, under a comment claiming it
    serves "tool_result correlation". Either the feature exists (the field is
    read) or the field is gone — not left as unmarked dead state."""
    import ast
    import inspect
    import textwrap

    from wiwi.providers import openai_adapter as mod

    src = textwrap.dedent(inspect.getsource(mod))
    tree = ast.parse(src)
    reads: list[str] = []
    for node in ast.walk(tree):
        # A read is any Attribute access that is NOT the target of an
        # assignment / deletion and NOT part of a plain call on it.
        if isinstance(node, ast.Attribute) and node.attr == "_emitted_opens":
            reads.append(f"line {node.lineno}")
    # Every remaining reference must be an assignment target (Store) or a
    # delete; a bare Load means something reads it.
    loads: list[int] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "_emitted_opens"
                and isinstance(node.ctx, ast.Load)):
            loads.append(node.lineno)
    assert not loads, (
        f"_emitted_opens is still referenced as a value at lines {loads} but "
        f"nothing consumes it; either surface the id or delete the field. "
        f"All references: {reads}"
    )
