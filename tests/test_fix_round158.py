"""Regression round 158 — an inbound-surface audit sweep across eight findings.

Every defect here has the same shape: two surfaces, or two paths of one surface,
disagreeing about the same event, so the dialect the caller happened to use
decided what they were told. Entry numbers map to ``AUDIT.md``.

* #477 — the Responses *streaming* terminal event keyed on ``stop == "length"``
  alone while its payload's ``status`` came from a table, so a
  content-filtered turn announced ``response.completed`` around
  ``"status": "incomplete"``.
* #478 — both OpenAI-shaped stream encoders hardcoded ``api_error`` on a
  mid-stream failure, so a 429/529/timeout reached an OpenAI client
  unclassified while the identical failure reached Claude Code correctly.
* #479 — Anthropic tool-use identity: decode forwarded a non-``str``
  server-tool ``id`` (``server_tool_use``/``mcp_tool_use``) into the IR, and the
  server-tool fallback emitted a result block whose ``tool_use_id`` named no
  call it shipped with.
* #480 — the stored copy of a streamed hosted search claimed an empty query,
  so a client continuing by ``previous_response_id`` read back a different turn
  than the one it saw streamed.
* #481 — ``LoopDetector``'s repeat floor of 1 fired on a single coincidental
  match, aborting a healthy stream.
* #482 — truncated-JSON repair appended closers to a half-written token, so
  every argument in the object was lost at the boundaries that occur most.
* #483 — ``StreamTape`` evicted the very entry it had just appended, leaving
  the tape empty and ``head_evicted`` unable to prove the loss.
* #484 — the *sync* Anthropic error body rendered wiwi's own ``timeout`` as
  ``api_error`` and had no 504 fallback, so the pre-first-byte path disagreed
  with the streaming one about the same failure.
"""

from __future__ import annotations

import httpx
import orjson
import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.streaming import partial_json as pj
from wiwi.streaming.loopdetect import MIN_LOOP_REPEATS, LoopDetector
from wiwi.streaming.resume import StreamTape
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as orc
from wiwi.wire import openai_responses as orp

_AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}
_ANTH_HEADERS = {"anthropic-version": "2023-06-01"}

_ANTHROPIC_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: anthropic
    provider: anthropic
    base_url: https://api.anthropic.com/v1
    keys: [{label: default, key: sk-upstream}]
model_list:
  - model_name: claude-x
    wiwi_params: {provider: anthropic, model: claude-x}
"""

_OPENAI_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: openai
    provider: openai
    keys: [{label: default, key: sk-upstream}]
model_list:
  - model_name: gpt-4o
    wiwi_params: {provider: openai, model: gpt-4o}
"""

_ANTH_UP = "https://api.anthropic.com/v1/messages"
_OA_UP = "https://api.openai.com/v1/chat/completions"


async def _client(cfg: str):
    app = create_app(load_config_from_string(cfg))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    return mgr, AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test", headers=dict(_AUTH))


# ---------------------------------------------------------------------------
# #477 — a content-filtered turn completed cleanly on the Responses stream
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stop,event,status", [
    ("content_filter", "response.incomplete", "incomplete"),
    ("length", "response.incomplete", "incomplete"),
    ("stop", "response.completed", "completed"),
])
def test_responses_stream_terminal_event_matches_its_own_payload(stop, event, status):
    """The event name and the payload's ``status`` must never disagree.

    ``_response_obj`` keys ``status`` off ``_INCOMPLETE_REASONS``; the event
    type keyed off ``stop == "length"``. Any other incomplete stop — in
    practice ``content_filter`` — therefore shipped a clean-completion event
    wrapping a payload that said the answer had been cut, and a client
    switching on the event type never learned it was truncated.
    """
    enc = orp.ResponsesStreamEncoder("gpt-4o", "r1")
    frames = [enc.feed(dl.StreamStart("gpt-4o")),
              enc.feed(dl.TextDelta("part")),
              enc.feed(dl.UsageFinal(prompt=1, output=1)),
              enc.feed(dl.Finish(stop))]
    blob = b"".join(f for f in frames if f) + enc._completed()
    text = blob.decode()
    assert event in text, f"expected {event} for stop={stop!r}: {text[:300]}"
    assert f'"status":"{status}"' in text.replace(" ", ""), text[:300]
    # The two copies of one turn must agree, whichever way the client reads it.
    other = "response.completed" if event == "response.incomplete" \
        else "response.incomplete"
    assert other not in text


@respx.mock
async def test_content_filtered_stream_says_incomplete_end_to_end():
    """Same defect through the app: an OpenAI upstream that filters, asked on
    the Responses surface."""
    respx.post(_OA_UP).mock(return_value=Response(200, headers={
        "content-type": "text/event-stream"}, content=(
            b'data: {"id":"c","object":"chat.completion.chunk","created":1,'
            b'"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"cut"},'
            b'"finish_reason":null}]}\n\n'
            b'data: {"id":"c","object":"chat.completion.chunk","created":1,'
            b'"model":"gpt-4o","choices":[{"index":0,"delta":{},'
            b'"finish_reason":"content_filter"}]}\n\n'
            b"data: [DONE]\n\n")))
    mgr, client = await _client(_OPENAI_CFG)
    try:
        async with client.stream("POST", "/v1/responses", json={
                "model": "gpt-4o", "input": "hi", "stream": True}) as r:
            raw = "".join([c async for c in r.aiter_text()])
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 200, raw
    assert "response.incomplete" in raw, raw[:400]
    assert '"status":"incomplete"' in raw.replace(" ", ""), raw[:400]


# ---------------------------------------------------------------------------
# #478 — a mid-stream failure lost its classification on the OpenAI surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("d,expected", [
    (dl.StreamError("m", etype="overloaded_error"), "overloaded_error"),
    (dl.StreamError("m", kind="status", status=529), "overloaded_error"),
    (dl.StreamError("m", kind="status", status=429), "rate_limit_error"),
    (dl.StreamError("m", kind="timeout", status=504), "timeout"),
    (dl.StreamError("m", kind="timeout"), "timeout"),
    (dl.StreamError("m", kind="status", status=503), "api_connection_error"),
    (dl.StreamError("m"), "api_error"),
])
def test_mid_stream_classification_vocabulary(d, expected):
    """One classification, shared by every dialect's stream encoder."""
    assert dl.stream_error_type(d) == expected


def test_the_vendor_type_beats_the_status():
    """The vendor's own type is the most precise signal and wins."""
    assert dl.stream_error_type(
        dl.StreamError("m", kind="status", status=429, etype="overloaded_error")) \
        == "overloaded_error"


@pytest.mark.parametrize("encoder", [
    oc.ChatStreamEncoder("gpt-4o", "r1"),
    orc.CompletionStreamEncoder("gpt-4o", "r1"),
])
def test_openai_stream_error_frames_carry_the_classification(encoder):
    """Both OpenAI-shaped surfaces used to hardcode ``api_error``."""
    frame = encoder.feed(dl.StreamError("Slow down", kind="status",
                                        status=429, etype="rate_limit_error"))
    text = frame.decode()
    assert "rate_limit_error" in text, text
    assert "api_error" not in text, text


def test_responses_stream_error_frame_carries_the_classification():
    enc = orp.ResponsesStreamEncoder("gpt-4o", "r1")
    enc.feed(dl.StreamStart("gpt-4o"))
    frame = enc.feed(dl.StreamError("Overloaded", kind="status", status=529,
                                    etype="overloaded_error"))
    text = frame.decode()
    assert "overloaded_error" in text, text
    assert "api_error" not in text, text


@respx.mock
async def test_a_mid_stream_overload_reaches_a_chat_client_as_an_overload():
    """End-to-end on the surface the defect was invisible on.

    The Anthropic twin classified this correctly all along; the Chat encoder
    is what flattened it, so a client on Chat could not tell a saturated
    upstream from a broken deployment.
    """
    respx.post(_ANTH_UP).mock(return_value=Response(200, headers={
        "content-type": "text/event-stream"}, content=(
            b'event: message_start\ndata: {"type":"message_start","message":'
            b'{"model":"claude-x","usage":{"input_tokens":3,"output_tokens":0}}}\n\n'
            b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",'
            b'"message":"Overloaded"}}\n\n')))
    mgr, client = await _client(_ANTHROPIC_CFG)
    try:
        async with client.stream("POST", "/v1/chat/completions", json={
                "model": "claude-x", "messages": [{"role": "user", "content": "hi"}],
                "stream": True}) as r:
            raw = "".join([c async for c in r.aiter_text()])
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert "overloaded_error" in raw, raw[:400]


# ---------------------------------------------------------------------------
# #479 — Anthropic tool-use identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("btype", ["server_tool_use", "mcp_tool_use"])
@pytest.mark.parametrize("raw_id", [None, 123, 1.5, ["x"], {"a": 1}, True])
def test_a_non_string_server_tool_use_id_never_reaches_the_ir(btype, raw_id):
    """``b.get("id", "")`` defaulted only a *missing* key.

    An explicit JSON ``null`` (or any non-string) passed through untouched, so
    the IR carried an id that is not a ``str`` and replay re-emitted the
    literal ``"None"`` on a ``*_tool_result`` pair naming nothing — a 400
    blamed on a field the caller never wrote.

    The two spellings share one decode arm, so both are covered here. The
    client-dispatched ``tool_use`` arm is deliberately NOT the target: it
    already refuses a missing or non-string id outright (AUDIT #368), which is
    why this defect could only live on the server-tool side.
    """
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 16,
        "messages": [{"role": "assistant", "content": [
            {"type": btype, "id": raw_id, "name": "web_search",
             "input": {"query": "cats"}}]}]})
    part = req.messages[0].parts[0]
    assert part.id == "", f"id {raw_id!r} reached the IR as {part.id!r}"
    # The block type is what keeps the paired result paired on replay, so the
    # guard must not have cost the arm its other invariants.
    assert part.block_type == btype
    assert part.builtin == "web_search"


def test_a_string_server_tool_use_id_is_still_preserved():
    """Control: the guard must not eat real ids."""
    req = am.decode_request({
        "model": "claude-x", "max_tokens": 16,
        "messages": [{"role": "assistant", "content": [
            {"type": "server_tool_use", "id": "srvtoolu_1",
             "name": "web_search", "input": {"query": "cats"}}]}]})
    assert req.messages[0].parts[0].id == "srvtoolu_1"


def test_a_fallback_paired_result_names_the_call_it_ships_with():
    """The single-call fallback must emit an *internally consistent* pair.

    ``_take_server_call`` falls back to the lone buffered call when the result
    names an id the adapter never saw — but the result block was then shipped
    carrying the unrecognized id while the call carried its real one. Two ids
    for one pair is exactly the unpaired-on-replay shape the buffering exists
    to prevent.
    """
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    enc.feed(dl.StreamStart("claude-x"))
    enc.feed(dl.ToolCallOpen(0, "srvtoolu_real", "web_search",
                             builtin="web_search"))
    enc.feed(dl.ToolCallArgsDelta(0, '{"query": "cats"}'))
    enc.feed(dl.ToolCallClose(0))
    out = enc.feed(dl.ServerToolResultDelta(1, {
        "type": "web_search_tool_result",
        "tool_use_id": "upstream_rewrote_this", "content": []}))
    blob = out.decode()
    # The call and its result must carry ONE id, and it must be the call's.
    assert blob.count("srvtoolu_real") >= 2, blob
    assert "upstream_rewrote_this" not in blob, (
        f"the result kept an id no emitted call has: {blob}")


def test_a_matching_result_id_is_left_alone():
    """Control: an id that already matches must not be rewritten."""
    enc = am.AnthropicStreamEncoder("claude-x", "r1")
    enc.feed(dl.StreamStart("claude-x"))
    enc.feed(dl.ToolCallOpen(0, "srv_1", "web_search", builtin="web_search"))
    enc.feed(dl.ToolCallClose(0))
    out = enc.feed(dl.ServerToolResultDelta(1, {
        "type": "web_search_tool_result", "tool_use_id": "srv_1", "content": []}))
    assert "srv_1" in out.decode()


# ---------------------------------------------------------------------------
# #480 — the stored copy of a streamed hosted search claimed an empty query
# ---------------------------------------------------------------------------


def _anth_builtin_search(query: str) -> bytes:
    args = orjson.dumps({"query": query}).decode()
    partial = orjson.dumps(args).decode()  # a JSON *string* holding JSON
    return (
        'event: message_start\ndata: {"type":"message_start","message":'
        '{"model":"claude-x","usage":{"input_tokens":5,"output_tokens":0}}}\n\n'
        'event: content_block_start\ndata: {"type":"content_block_start",'
        '"index":0,"content_block":{"type":"server_tool_use",'
        '"id":"srvtoolu_1","name":"web_search"}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta",'
        f'"index":0,"delta":{{"type":"input_json_delta","partial_json":{partial}}}}}\n\n'
        'event: content_block_stop\ndata: {"type":"content_block_stop",'
        '"index":0}\n\n'
        'event: message_delta\ndata: {"type":"message_delta",'
        '"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n').encode()


@respx.mock
async def test_the_stored_search_reports_the_same_query_it_streamed():
    """The accumulator records only raw argument fragments.

    ``_streamed_response_payload`` read ``tc.get("query", "")`` — a key the
    accumulator never writes — so the stored item claimed a hosted search ran
    with an EMPTY query while the live SSE frame for the same turn carried the
    real one. A client continuing by ``previous_response_id`` read back a turn
    it never saw.
    """
    respx.post(_ANTH_UP).mock(return_value=Response(200, headers={
        "content-type": "text/event-stream"},
        content=_anth_builtin_search("wiwi proxy")))
    mgr, client = await _client(_ANTHROPIC_CFG)
    try:
        async with client.stream("POST", "/v1/responses", json={
                "model": "claude-x", "input": "search", "stream": True,
                "tools": [{"type": "web_search"}]}) as r:
            raw = "".join([c async for c in r.aiter_text()])
        assert r.status_code == 200, raw
        rid = ""
        for line in raw.split("\n"):
            if line.startswith("data: ") and '"response.created"' in line:
                rid = orjson.loads(line[6:])["response"]["id"]
                break
        assert rid, f"no response id in the stream: {raw[:400]}"
        stored = await client.get(f"/v1/responses/{rid}")
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert stored.status_code == 200, stored.text
    searches = [i for i in stored.json()["output"]
                if i.get("type") == "web_search_call"]
    assert searches, stored.json()["output"]
    # The live frame's spelling of the same turn. Compared against the stream
    # as-is: the encoder emits compact JSON, and normalising whitespace away
    # would eat the space inside the query itself, making this pass vacuously.
    assert '"query":"wiwi proxy"' in raw, raw[:600]
    assert searches[0]["action"]["query"] == "wiwi proxy", (
        "the stored turn disagrees with the streamed one: "
        f"{searches[0]}")


# ---------------------------------------------------------------------------
# #481 — one coincidental match was enough to abort a healthy stream
# ---------------------------------------------------------------------------


def test_one_coincidence_is_not_a_loop():
    """Two chunks that merely coincide ``limit - 1`` apart are ordinary prose.

    At ``stream_loop_limit: 33`` the arithmetic floor went to 1 for period 32,
    so a single matching lag — a sentence reused 32 chunks later — aborted the
    stream mid-answer.
    """
    det = LoopDetector(33)
    for i in range(33):
        assert det.feed(f"c{i}") is False
    assert det.feed("c1") is False, "one coincidental match is not a loop"


def test_the_floor_is_three_at_any_period():
    """The knob decides *how many repeats* are required, so the floor is the
    smallest count that is evidence of a loop rather than a coincidence."""
    det = LoopDetector(33)
    assert det._threshold(32) == MIN_LOOP_REPEATS
    assert det._threshold(1) == 32
    # Dormant at the shipped limit: every period ≤ MAX_LOOP_PERIOD needs ≥ 68.
    assert LoopDetector(100)._threshold(32) == 68


def test_a_real_loop_still_fires_at_a_small_limit():
    """The floor must not disarm the detector where it is meant to fire."""
    det = LoopDetector(10)
    fires = [det.feed("same") for _ in range(12)]
    assert fires[9] is True, fires
    assert not any(fires[:9]), fires


def test_genuine_short_period_loop_still_fires():
    """A 3-chunk cycle repeated to the threshold is caught."""
    det = LoopDetector(10)
    fires = [det.feed("a"), det.feed("b"), det.feed("c")]
    for _ in range(12):
        fires += [det.feed("a"), det.feed("b"), det.feed("c")]
    assert any(fires), "a repeating cycle at period 3 must still be detected"


# ---------------------------------------------------------------------------
# #482 — closers appended to a half-written token lost every argument
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1,', {"a": 1}),                  # mid-separator
    ('{"b":', {}),                           # dangling key
    ('{"a": tru', {"a": True}),              # half-written literal, completed
    ('{"a": fals', {"a": False}),
    ('{"a": nul', {"a": None}),
    ('{"n": 1.', {}),                        # a number that cannot terminate
    ('{"n": 1e', {}),
    ('{"n": 1e+', {}),
    ('{"a": "b"', {"a": "b"}),               # a value string, worth closing
    ('{"qu', {}),                            # mid-key
    ('{"a": [1,', {"a": [1]}),
])
def test_a_truncated_tool_call_keeps_what_it_had(text, expected):
    """Closing brackets is only sufficient when the cut landed between tokens.

    Closing a half-written token produced ``{"a": 1,}`` / ``{"b": }`` /
    ``{"a": tru}`` — none of which is JSON, so every consumer fell back to
    ``{}`` and lost EVERY argument at the boundaries that occur most.
    """
    value, complete = pj.parse_partial(text)
    assert complete is False
    assert value == expected, f"{text!r} -> {value!r}"


def test_a_complete_document_is_reported_complete():
    """Control: the trimmer must not touch well-formed input."""
    doc = '{"query": "wiwi proxy", "limit": 5}'
    assert pj.parse_partial(doc) == ({"query": "wiwi proxy", "limit": 5}, True)


def test_no_fully_written_argument_is_lost_at_any_cut():
    """The property the fix exists for, over every prefix of a real payload.

    For each key whose value token is complete in the prefix, the repaired
    parse must carry that key at that value — the stream is cut at an arbitrary
    byte, not at a token boundary the model chose.
    """
    doc = '{"query": "wiwi proxy", "limit": 5, "tags": ["a", "b"]}'
    done = [
        (doc.index('"wiwi proxy"') + len('"wiwi proxy"'), "query", "wiwi proxy"),
        (doc.index('"limit": 5') + len('"limit": 5'), "limit", 5),
        (doc.index('["a", "b"]') + len('["a", "b"]'), "tags", ["a", "b"]),
    ]
    for cut in range(len(doc) + 1):
        value, _ = pj.parse_partial(doc[:cut])
        assert isinstance(value, dict), f"cut {cut}: {doc[:cut]!r} -> {value!r}"
        for end, key, want in done:
            if cut >= end:
                assert value.get(key) == want, (
                    f"cut {cut} lost {key!r}: {doc[:cut]!r} -> {value!r}")


# ---------------------------------------------------------------------------
# #483 — the tape evicted the entry it had just appended
# ---------------------------------------------------------------------------


def test_an_oversized_delta_survives_on_the_tape():
    """A delta bigger than the whole budget was popped right after appending.

    The tape then held nothing: ``bytes == 0`` and no head, so the continuation
    guard could not tell "nothing was ever emitted" from "everything was
    dropped" and rebuilt the assistant prefix from an empty tape.
    """
    tape = StreamTape(max_bytes=8)
    tape.append(dl.TextDelta("x" * 100))
    assert tape.replay_text() == "x" * 100
    assert tape.bytes > 0


def test_a_tape_that_dropped_an_entry_still_proves_the_gap():
    """``head_evicted`` is the only signal that a continuation is partial.

    With the last entry also evicted the tape went empty, and the empty case
    answers False by design — so a tape that had lost everything claimed
    nothing had been lost.
    """
    tape = StreamTape(max_bytes=8)
    tape.append(dl.TextDelta("aaa"))            # seq 1, evicted by the next
    tape.append(dl.TextDelta("b" * 100))        # seq 2, overshoots on its own
    assert tape.head_evicted(0) is True, (
        "the tape dropped seq 1 and must not claim the prefix is complete")
    assert tape.replay(0) == [dl.TextDelta("b" * 100)]


# ---------------------------------------------------------------------------
# #484 — the sync Anthropic path flattened a timeout the streaming path kept
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,etype,expected", [
    # wiwi's own spelling of a timeout, as providers/base.py produces it.
    (504, "timeout", "timeout_error"),
    # A status with no etype of its own left the client nothing to act on.
    (504, "brand_new_vendor_type", "timeout_error"),
    # The streaming twin's spelling, still accepted.
    (504, "timeout_error", "timeout_error"),
])
def test_a_timeout_reaches_claude_code_as_a_timeout(status, etype, expected):
    """Both paths must render the same failure the same way.

    ``providers/base.py`` returns ``WiwiError(504, "timeout", …)``; the
    streaming twin mapped ``timeout`` onto Anthropic's ``timeout_error`` while
    the sync table mapped it onto ``api_error`` — so the pre-first-byte path
    flattened exactly the classification the client backs off from.
    """
    body = am.error_body(status, etype, "upstream timed out")
    assert body["error"]["type"] == expected


def test_the_sync_and_stream_paths_agree_about_a_timeout():
    """Pinned together so the two surfaces cannot drift again."""
    streamed = am._stream_error_type(dl.StreamError(
        "timed out", kind="timeout", status=504))
    synced = am.error_body(504, "timeout", "timed out")["error"]["type"]
    assert synced == streamed == "timeout_error"


def test_the_etype_table_still_beats_the_status():
    """Control: precedence is deliberate.

    The table carries wiwi's vocabulary with no one-status-one-answer mapping —
    a budget breach arrives as a 402 and must stay ``permission_error``.
    """
    assert am.error_body(402, "budget_exceeded", "over budget")["error"]["type"] \
        == "permission_error"


@respx.mock
async def test_a_pre_first_byte_timeout_reaches_claude_code_as_one():
    """End-to-end through the app on the path the defect lived on."""
    respx.post(_ANTH_UP).mock(side_effect=httpx.ConnectTimeout("timed out"))
    mgr, client = await _client(_ANTHROPIC_CFG)
    try:
        r = await client.post("/v1/messages", headers=_ANTH_HEADERS, json={
            "model": "claude-x", "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}]})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 504, r.text
    assert r.json()["error"]["type"] == "timeout_error", r.text
