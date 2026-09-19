"""Round 89: the free-tier decoy tools must never answer as tool calls.

Round 88's cloak satisfies the free tier's tool-payload condition by injecting
`bash` and `read` into every free-model request. Injecting a tool the client
never declared has a consequence the request side cannot fix: the model may
*call* it. Probed live on 2026-09-19 — `mimo-v2.5-free`, asked "Read the file
config.py.", answered with a `read` tool call — and a wiwi client has no `read`
implementation, so it would receive a call it cannot execute and a
`tool_calls` finish it cannot resolve.

The decoys therefore have to be filtered on the way out, keyed on the names
*this request* injected: a client that declares its own `bash` keeps it, the
cloak never replaces an existing entry, and its calls must reach the caller.
Both decode paths need the rule (streaming deltas and the aggregated turn), the
`Finish` reason has to follow the surviving calls, and a non-free request must
stay untouched.
"""

from __future__ import annotations

import time

import orjson
import pytest

from wiwi.ir import types as ir
from wiwi.providers import opencode_adapter as oa
from wiwi.providers import opencode_version as ov
from wiwi.streaming import deltas as dl
from wiwi.wire import openai_chat as oc

FREE_CHAT = "mimo-v2.5-free"
FREE_RESPONSES = "muse-spark-1.3-contributor-free"
PAID_CHAT = "glm-5.3-flash"


@pytest.fixture(autouse=True)
def _seed_ua_and_clock():
    ov._set_cached_for_tests("1.18.31", time.monotonic())
    oa._reset_stable_sessions_for_tests()


def _req(*, tools: list[str] | None = None) -> ir.Request:
    body: dict = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": n, "description": "d",
            "parameters": {"type": "object", "properties": {}}}} for n in tools]
    return oc.decode_request(body)


def _cloaked(model: str, req: ir.Request | None = None) -> oa.OpencodeAdapter:
    a = oa.OpencodeAdapter()
    a.encode_request(req or _req(), model, {})
    return a


def _open(index: int, name: str) -> dl.ToolCallOpen:
    return dl.ToolCallOpen(index=index, id=f"call_{index}", name=name)


# -- the cloak reports what it injected ----------------------------------------


def test_cloak_returns_the_names_it_injected():
    a = _cloaked(FREE_CHAT)
    assert a._decoy_names == frozenset({"bash", "read"})


def test_cloak_does_not_claim_a_name_the_client_already_owns():
    # A client tool called `bash` is real: the cloak adds only `read`, so only
    # `read` may be filtered. `bash` calls must still reach the caller.
    req = _req(tools=["bash"])
    body = oa.OpencodeAdapter().encode_request(req, FREE_CHAT, {})
    names = [t["function"]["name"] for t in body["tools"]]
    assert names.count("read") == 1
    a = _cloaked(FREE_CHAT, req)
    assert a._decoy_names == frozenset({"read"})


def test_paid_request_arms_no_filter():
    a = _cloaked(PAID_CHAT)
    assert a._decoy_names == frozenset()


def test_encode_resets_the_state_between_requests():
    a = _cloaked(FREE_CHAT)
    assert a._decoy_names == frozenset({"bash", "read"})
    a.encode_request(_req(), PAID_CHAT, {})
    assert a._decoy_names == frozenset()
    assert a._decoy_indices == set()


# -- streaming decode ----------------------------------------------------------


def test_streaming_decoy_call_is_dropped_whole():
    a = _cloaked(FREE_CHAT)
    got = []
    for d in [_open(0, "read"),
              dl.ToolCallArgsDelta(index=0, args_fragment='{"path":'),
              dl.ToolCallArgsDelta(index=0, args_fragment='"a"}'),
              dl.ToolCallClose(index=0),
              dl.Finish("tool_call"),
              dl.StreamEnd()]:
        got.extend(a._filter_decoys([d]))
    assert [type(x) for x in got] == [dl.Finish, dl.StreamEnd]
    assert got[0].stop_reason == "stop", "client would wait on a dropped call"


def test_streaming_keeps_a_real_call_beside_a_decoy():
    a = _cloaked(FREE_CHAT)
    got = []
    for d in [_open(0, "read"), dl.ToolCallClose(index=0),
              _open(1, "get_weather"),
              dl.ToolCallArgsDelta(index=1, args_fragment="{}"),
              dl.ToolCallClose(index=1),
              dl.Finish("tool_call"), dl.StreamEnd()]:
        got.extend(a._filter_decoys([d]))
    opens = [x for x in got if isinstance(x, dl.ToolCallOpen)]
    assert [x.name for x in opens] == ["get_weather"]
    assert got[-2].stop_reason == "tool_call", "a real call survived"
    # The dropped index must not shadow the kept one.
    assert 1 not in a._decoy_indices


def test_client_owned_bash_call_passes_through():
    req = _req(tools=["bash"])
    a = _cloaked(FREE_CHAT, req)
    got = a._filter_decoys([_open(0, "bash"), dl.ToolCallClose(index=0)])
    assert [type(x) for x in got] == [dl.ToolCallOpen, dl.ToolCallClose]


def test_no_decoys_means_no_filtering():
    a = _cloaked(PAID_CHAT)
    deltas = [_open(0, "read"), dl.ToolCallClose(index=0), dl.Finish("tool_call")]
    assert a._filter_decoys(deltas) == deltas


def test_deltas_after_a_dropped_open_are_dropped():
    # The filter is fed one event at a time, so the ArgsDelta arrives without
    # the Open that already set the index.
    a = _cloaked(FREE_CHAT)
    assert a._filter_decoys([_open(0, "bash")]) == []
    assert a._filter_decoys([dl.ToolCallArgsDelta(index=0,
                                                  args_fragment="{}")]) == []
    assert a._filter_decoys([dl.ToolCallClose(index=0)]) == []


# -- aggregated (non-streaming) decode -----------------------------------------


def _turn(names: list[str]) -> ir.AssistantTurn:
    return ir.AssistantTurn(
        text="sure",
        tool_calls=[ir.ToolUsePart(id=f"c{i}", name=n, args={})
                    for i, n in enumerate(names)],
        stop_reason="tool_call")


def test_aggregated_turn_drops_decoy_calls():
    a = _cloaked(FREE_CHAT)
    turn = a._filter_decoys_turn(_turn(["read"]))
    assert turn.tool_calls == []
    assert turn.stop_reason == "stop"
    assert turn.text == "sure"


def test_aggregated_turn_keeps_a_real_call_and_its_finish():
    a = _cloaked(FREE_CHAT)
    turn = a._filter_decoys_turn(_turn(["bash", "get_weather"]))
    assert [c.name for c in turn.tool_calls] == ["get_weather"]
    assert turn.stop_reason == "tool_call"


def test_paid_turn_is_returned_untouched():
    a = _cloaked(PAID_CHAT)
    turn = _turn(["read"])
    assert a._filter_decoys_turn(turn) is turn


def test_decoded_chat_turn_loses_the_decoy_call():
    a = _cloaked(FREE_CHAT)
    payload = orjson.dumps({
        "id": "c", "object": "chat.completion", "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": [{
                                     "id": "call_0", "type": "function",
                                     "function": {"name": "read",
                                                  "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    turn = a.decode_response(200, payload)
    assert turn.tool_calls == []
    assert turn.stop_reason == "stop"
