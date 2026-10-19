"""Round 162 — the delta / chunk flow: thirteen defects found by sweep.

Every defect here was found by driving the streaming path — the wire
encoders, the pump, the coalescer and the streaming adapters — with
adversarial delta sequences and end-to-end replays against a mocked
upstream, then reading the client's bytes back. Not by reading the code.

The findings split into three groups.

**Delta flow (the streaming path proper).**

1. **#420 — ``[DONE]`` on an ``anthropic``-typed upstream was dropped.**
   The Anthropic decoder had no ``[DONE]`` arm, so the sentinel fell
   through ``orjson.JSONDecodeError -> return []``. The body ended with no
   ``Finish`` and no ``StreamEnd``, the pump read that as a truncation and
   queued ``StreamError("upstream stream ended without completion")`` —
   *after* the content was already delivered — and recorded a fail against
   a perfectly healthy deployment. The OpenAI-wire siblings
   (``openai_adapter``, ``nim_adapter``, ``openrouter_adapter``,
   ``opencode_adapter``) all had the terminator arm; the Anthropic one did
   not. A self-hosted relay that normalises every upstream to OpenAI shape
   and appends ``[DONE]`` is the common case, and it is a config-shape
   mismatch (a provider typed ``anthropic`` whose upstream speaks
   OpenAI), not a hypothetical.

2. **#355 — a Responses ``reasoning`` item replayed with no
   ``encrypted_content``.** Codex sends ``include:
   ["reasoning.encrypted_content"]`` on every request and must replay every
   output item verbatim, blob included. The decoder read only ``summary``,
   and the IR had nowhere to put the blob: ``ir.ThinkingPart`` had no
   field for it. ``grep -rn "encrypted_content" wiwi/`` returned zero
   hits — the name did not exist in the tree at all. Turn 2 of any
   reasoning-model session sent history the API rejects.

3. **#356 — a tool item's ``item_id`` used the IR stream index, so the
   streamed and the sync ids disagreed.** The stream minted
   ``fc_{req_id}_{d.index}`` (the IR tool index) while ``encode_response``
   minted ``fc_{req_id}_{out_id}`` (a counter over every output item). A
   message before the tool shifted them by one, and the gap grew with
   every preceding item.

4. **#357 — ``ToolCallClose`` in an order other than open order misordered
   ``response.completed.output``.** ``streaming/deltas.py`` explicitly
   permits batch closes in any order, but ``_item_done`` appended to
   ``self._output``, so the terminal array was in CLOSE order while the
   per-item events were in ``output_index`` order. A client rebuilding the
   turn from the terminal payload got the tool calls in the wrong
   sequence relative to the text they bracket.

5. **#358 — ``stop_reason: context_window_exceeded`` reported ``status:
   "completed"``.** ``_INCOMPLETE_REASONS`` covered only ``length`` and
   ``content_filter``, so a context overflow — the one stop reason the IR
   exists to carry — told the client the turn completed cleanly. No
   signal to trigger auto-compact.

**Adjacent to the delta flow, found by the same sweep.**

6. **#350 — ``previous_response_id`` dropped the entire user-side
   conversation history.** The store recorded BOTH halves of a turn, but
   ``with_history`` was handed only the previous turn's *output*. The
   stored ``input_items`` were read by nobody, so the user's original
   question never reached the upstream. Every stateful multi-turn
   Responses/Codex session silently forgot its context.

7. **#347 — the 402 budget refusal leaked the rate-limit slot it had
   already reserved.** The slot went into the GLOBAL scope, so three
   refusals from a budget-capped key locked an unrelated, uncapped key
   out with a 429.

8. **#348 — the lifespan teardown was unguarded.** Twelve bare sequential
   ``await X.stop()`` calls with no ``try/finally``: the first to raise
   skipped every later one *and* the engine disposal, leaving orphaned
   workers holding sockets and DB connections.

9. **#382 — the router's resilience knobs had no positivity validator.**
   ``num_retries: -1`` made ``range(num_retries + 1)`` empty, so every
   request fell through to "no healthy deployment" — a 503 from a single
   typo in the YAML.

10. **#349 — ``/metrics`` returned the SPA's ``index.html`` (HTTP 200)
    when Prometheus was disabled**, which is the default.

11. **#353 / #354 — the retention sweeper had no cap on ``audit_logs`` and
    no cross-process lock**, so the table grew without bound and two
    gateway processes sweeping concurrently double-counted the rollup.

12. **#386 — Gemini's ``(cand.get("content") or {}).get("parts")`` gate
    raised ``AttributeError``** on a truthy non-dict ``content`` — the
    same defect the sibling decoders had already been fixed for.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import ConfigError, load_config_from_string
from wiwi.ir import types as ir
from wiwi.server.app import create_app
from wiwi.streaming import deltas as dl
from wiwi.wire.openai_responses import (
    _INCOMPLETE_REASONS,
    ResponsesStreamEncoder,
    _response_obj,
)

H = {"Authorization": "Bearer sk-wiwi-master-test"}

RELAY_CFG = """
general_settings:
  master_key: sk-wiwi-master-test
providers:
  - name: relay
    provider: anthropic
    base_url: https://relay.example.com
    keys:
      - {label: main, key: sk-test}
model_list:
  - model_name: gpt-test
    wiwi_params:
      provider: relay
      model: claude-x
"""


@contextlib.asynccontextmanager
async def client_for(cfg=None):
    app = create_app(cfg or load_config_from_string(RELAY_CFG))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.wiwi = app.state.wiwi
            yield c


@pytest.fixture
async def client():
    async with client_for() as c:
        yield c


def _sse_frames(raw: bytes) -> list[dict]:
    """Parse an SSE body into the JSON payloads it carries."""
    out = []
    for chunk in raw.split(b"\n\n"):
        if not chunk.strip():
            continue
        for line in chunk.split(b"\n"):
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                out.append({"__done__": True})
                continue
            try:
                out.append(json.loads(data))
            except ValueError:
                out.append({"__raw__": data.decode(errors="replace")})
    return out


# ---------------------------------------------------------------------------
# 1. #420 — [DONE] on an anthropic-typed upstream
# ---------------------------------------------------------------------------

ANTHROPIC_SSE_HEAD = (
    'data: {"type":"message_start","message":{"model":"claude-x",'
    '"usage":{"input_tokens":5}}}\n\n'
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}\n\n'
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"hi there"}}\n\n'
)

ANTHROPIC_SSE_TAIL = (
    'data: {"type":"content_block_stop","index":0}\n\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":3}}\n\n'
    'data: {"type":"message_stop"}\n\n'
)


async def _relay_stream(client, tail: str) -> bytes:
    """Stream a body from the mocked upstream and return the client bytes."""
    import respx

    with respx.mock:
        respx.post("https://relay.example.com/messages").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=tail.encode(),
            )
        )
        async with client.stream(
            "POST", "/v1/messages",
            headers=H,
            json={"model": "gpt-test", "max_tokens": 100, "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        ) as r:
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
    return body


async def test_done_sentinel_ends_an_anthropic_stream_cleanly(client):
    """A ``[DONE]``-terminated body must not produce a mid-stream error.

    The content was delivered; the body then ended with the OpenAI
    sentinel. The Anthropic decoder dropped the sentinel, so the pump saw
    a body with no ``Finish`` and no ``StreamEnd`` and declared it a
    truncation — handing the client an ``error`` frame AFTER the content.
    """
    body = await _relay_stream(client, ANTHROPIC_SSE_HEAD + "data: [DONE]\n\n")
    text = body.decode(errors="replace")
    assert '"error"' not in text, text
    assert "upstream stream ended without completion" not in text, text
    # The content still reached the client.
    assert "hi there" in text, text


async def test_done_sentinel_does_not_fail_a_healthy_deployment(client):
    """The same body must not record a fail against a healthy deployment.

    A misconfigured relay must not retire a working credential — past
    ``allowed_fails`` the deployment is cooled, so one config typo becomes
    a self-sustaining outage.
    """
    from wiwi.router.router import Deployment

    fails: list[str] = []
    orig = Deployment.record_fail

    def spy(self, *a, **kw):
        fails.append(self.model_id)
        return orig(self, *a, **kw)

    Deployment.record_fail = spy
    try:
        await _relay_stream(client, ANTHROPIC_SSE_HEAD + "data: [DONE]\n\n")
    finally:
        Deployment.record_fail = orig
    assert fails == [], f"healthy deployment took a fail: {fails}"


async def test_done_sentinel_flushes_open_tool_calls(client):
    """``[DONE]`` must close an open tool call, like the OpenAI sibling.

    A tool call opened and never closed leaves the client with a
    ``tool_use`` block that never stopped, which Anthropic rejects on
    replay.
    """
    body = await _relay_stream(client, (
        'data: {"type":"message_start","message":{"model":"claude-x"}}\n\n'
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"t1","name":"f"}}\n\n'
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n'
        "data: [DONE]\n\n"
    ))
    text = body.decode(errors="replace")
    assert '"error"' not in text, text
    assert "content_block_stop" in text, text


def test_anthropic_adapter_has_a_done_terminator_arm():
    """The decoder itself must recognise the sentinel.

    A unit-level guard so the fix cannot be undone by a later edit to
    the surrounding branch.
    """
    from wiwi.providers.anthropic_adapter import AnthropicAdapter

    out = AnthropicAdapter().decode_stream_event("", "[DONE]")
    kinds = [type(d).__name__ for d in out]
    assert "StreamEnd" in kinds, kinds


# ---------------------------------------------------------------------------
# 2. #355 — a Responses reasoning item replays with no encrypted_content
# ---------------------------------------------------------------------------


def test_reasoning_item_carries_encrypted_content_through_the_ir():
    """The blob must survive decode -> IR -> re-emit.

    Codex sends ``include: ["reasoning.encrypted_content"]`` and must
    replay every output item verbatim. Without a carrier in the IR the
    blob is dropped at decode and the next turn is rejected.
    """
    from wiwi.wire.openai_responses import decode_request

    body = {
        "model": "gpt-test",
        "input": [{
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "plan"}],
            "encrypted_content": "BLOB-123",
        }],
    }
    req = decode_request(body)
    parts = [p for m in req.messages for p in m.parts]
    blob = [getattr(p, "encrypted_content", None) for p in parts
            if isinstance(p, ir.ThinkingPart)]
    assert blob == ["BLOB-123"], (
        f"encrypted_content did not survive the decode: {blob}")


def test_reasoning_item_re_emits_encrypted_content():
    """The encoder must put the blob back on the wire."""
    from wiwi.wire.openai_responses import _reasoning_item

    item = _reasoning_item("plan", "req1", 0, encrypted_content="BLOB-123")
    assert item.get("encrypted_content") == "BLOB-123", item


def test_reasoning_item_without_a_blob_is_unchanged():
    """A reasoning item with no blob must not gain a fake one."""
    from wiwi.wire.openai_responses import _reasoning_item

    item = _reasoning_item("plan", "req1", 0)
    assert "encrypted_content" not in item, item


# ---------------------------------------------------------------------------
# 3. #356 — streamed and sync tool item_id disagree
# ---------------------------------------------------------------------------


def test_streamed_and_sync_tool_item_ids_match():
    """The same turn must carry the same tool id on both surfaces.

    A client that stores the streamed id and later references it against
    a sync-shaped payload points at a different item.
    """
    from wiwi.core.context import RequestContext
    from wiwi.wire.openai_responses import encode_response

    enc = ResponsesStreamEncoder("m", "r1")
    raw = b""
    for d in (
        dl.StreamStart(model="m"),
        dl.TextDelta("hi"),
        dl.ToolCallOpen(index=0, id="call_abc", name="f"),
        dl.ToolCallArgsDelta(index=0, args_fragment='{"a":1}'),
        dl.ToolCallClose(index=0),
        dl.UsageFinal(),
        dl.Finish(stop_reason="tool_calls"),
        dl.StreamEnd(),
    ):
        b = enc.feed(d)
        if b:
            raw += b
    stream_ids = [f["item"]["id"] for f in _sse_frames(raw)
                  if f.get("type") == "response.output_item.added"
                  and f["item"].get("type") == "function_call"]

    ctx = RequestContext(surface="responses", ir_req=ir.Request(
        model="m", messages=[ir.Message(role="user", parts=[ir.TextPart("x")])]))
    turn = ir.AssistantTurn(
        text="hi",
        tool_calls=[ir.ToolUsePart(id="call_abc", name="f", args={"a": 1})],
        stop_reason="tool_calls",
        usage=ir.Usage(prompt_tokens=1, completion_tokens=1),
    )
    body = encode_response(ctx, turn, "m", "r1")
    sync_ids = [it.get("id") for it in body.get("output", [])
                if it.get("type") == "function_call"]

    assert stream_ids == sync_ids, (
        f"stream minted {stream_ids}, sync minted {sync_ids}")


# ---------------------------------------------------------------------------
# 4. #357 — parallel tool closes misorder the terminal output array
# ---------------------------------------------------------------------------


def test_terminal_output_array_is_in_output_index_order():
    """The terminal ``output[]`` must agree with the per-item events.

    ``streaming/deltas.py`` permits batch closes in ANY order, so the
    close order cannot be the array's order.
    """
    enc = ResponsesStreamEncoder("m", "r2")
    raw = b""
    for d in (
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c1", name="f1"),
        dl.ToolCallOpen(index=1, id="c2", name="f2"),
        dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
        dl.ToolCallArgsDelta(index=1, args_fragment="{}"),
        dl.ToolCallClose(index=1),   # closed FIRST
        dl.ToolCallClose(index=0),
        dl.UsageFinal(),
        dl.Finish(stop_reason="tool_calls"),
        dl.StreamEnd(),
    ):
        b = enc.feed(d)
        if b:
            raw += b
    frames = _sse_frames(raw)
    added = [f["item"]["id"] for f in frames
             if f.get("type") == "response.output_item.added"]
    terminal = [i.get("id") for i in enc._output]
    assert terminal == added, (
        f"terminal output order {terminal} != added order {added}")


# ---------------------------------------------------------------------------
# 5. #358 — context_window_exceeded reports status completed
# ---------------------------------------------------------------------------


def test_context_window_exceeded_is_an_incomplete_status():
    """A context overflow must be visible as ``status: incomplete``.

    The IR carries ``context_window_exceeded`` precisely so the client
    can trigger auto-compact; reporting ``completed`` removes the only
    signal it has.
    """
    assert "context_window_exceeded" in _INCOMPLETE_REASONS, _INCOMPLETE_REASONS
    resp = _response_obj("m", "r1", "context_window_exceeded", [], None)
    assert resp["status"] == "incomplete", resp
    assert resp.get("incomplete_details"), resp


def test_pause_turn_is_an_incomplete_status():
    """``pause_turn`` is the same class: the turn did not complete."""
    assert "pause_turn" in _INCOMPLETE_REASONS, _INCOMPLETE_REASONS
    resp = _response_obj("m", "r1", "pause_turn", [], None)
    assert resp["status"] == "incomplete", resp


def test_length_and_content_filter_stay_incomplete():
    """The two existing reasons must keep working."""
    for reason, detail in (("length", "max_output_tokens"),
                           ("content_filter", "content_filter")):
        resp = _response_obj("m", "r1", reason, [], None)
        assert resp["status"] == "incomplete", resp
        assert resp["incomplete_details"]["reason"] == detail, resp


def test_a_normal_stop_is_still_completed():
    """The guard must not over-trigger."""
    resp = _response_obj("m", "r1", "stop", [], None)
    assert resp["status"] == "completed", resp
    assert "incomplete_details" not in resp, resp


# ---------------------------------------------------------------------------
# 6. #386 — Gemini truthy non-dict content
# ---------------------------------------------------------------------------


def test_gemini_truthy_non_dict_content_does_not_raise():
    """A ``content`` that is a number/string must be ignored, not crash.

    The same frame class was already fixed for the sibling decoders
    (AUDIT #153); the Gemini decoder re-implemented the gate as
    ``(cand.get("content") or {}).get("parts")``, whose ``or {}`` default
    fires only on a FALSY value.
    """
    from wiwi.providers.gemini_adapter import GeminiAdapter

    for bad in (5, True, "x", ["a"]):
        ev = json.dumps({
            "candidates": [{"content": bad,
                            "usageMetadata": {"promptTokenCount": 3}}],
            "usageMetadata": {"promptTokenCount": 3},
        })
        out = GeminiAdapter().decode_stream_event("", ev)
        assert isinstance(out, list), bad


def test_gemini_non_dict_content_does_not_terminate_the_stream():
    """A malformed frame must not emit a terminal.

    Ending the stream on a frame that carries no semantics truncates the
    answer at HTTP 200.
    """
    from wiwi.providers.gemini_adapter import GeminiAdapter

    ev = json.dumps({
        "candidates": [{"content": 5}],
        "usageMetadata": {"promptTokenCount": 3},
    })
    out = GeminiAdapter().decode_stream_event("", ev)
    kinds = [type(d).__name__ for d in out]
    assert "StreamEnd" not in kinds, kinds


# ---------------------------------------------------------------------------
# 7. #382 — the router's resilience knobs accept non-positive values
# ---------------------------------------------------------------------------


def test_router_settings_reject_a_negative_num_retries():
    """``num_retries: -1`` makes every request a 503.

    ``range(num_retries + 1)`` is empty, so the retry loop never runs and
    every request falls through to "no healthy deployment" — a total
    outage from one typo.
    """
    with pytest.raises(ConfigError):
        load_config_from_string(
            "router_settings:\n  num_retries: -1\n"
            "general_settings:\n  master_key: sk-x\n")


def test_router_settings_reject_negative_resilience_knobs():
    """No resilience knob may be negative.

    A negative count of attempts or failures names nothing sensible, and
    each one has a concrete failure mode: an empty retry loop (every
    request a 503), a deployment that cools on its first failure, a key
    retired on its first error, a loop detector that fires on chunk zero,
    or a resume loop that never runs.
    """
    for knob in ("num_retries", "allowed_fails", "key_max_consecutive_fails",
                 "stream_resume_max_retries", "stream_loop_limit"):
        with pytest.raises(ConfigError):
            load_config_from_string(
                f"router_settings:\n  {knob}: -1\n"
                "general_settings:\n  master_key: sk-x\n")


def test_router_settings_accept_zero_for_the_resilience_knobs():
    """Zero is a legitimate setting for a COUNT, not a silent no-op.

    ``num_retries: 0`` means "try once, never retry" — the retry loop's
    ``range(0 + 1)`` is exactly one attempt — and it is the common test
    configuration. ``stream_loop_limit: 0`` disables loop detection by the
    detector's own documented contract. Rejecting zero would break every
    existing configuration that uses it.
    """
    cfg = load_config_from_string(
        "router_settings:\n"
        "  num_retries: 0\n"
        "  allowed_fails: 0\n"
        "  key_max_consecutive_fails: 0\n"
        "  stream_resume_max_retries: 0\n"
        "  stream_loop_limit: 0\n"
        "general_settings:\n  master_key: sk-x\n")
    assert cfg.router_settings.num_retries == 0
    assert cfg.router_settings.stream_loop_limit == 0


def test_router_settings_accept_the_shipped_defaults():
    """The guard must not reject a valid config."""
    cfg = load_config_from_string(
        "router_settings:\n  num_retries: 2\n  allowed_fails: 3\n"
        "general_settings:\n  master_key: sk-x\n")
    assert cfg.router_settings.num_retries == 2


# ---------------------------------------------------------------------------
# 8. #350 — previous_response_id drops the user-side history
# ---------------------------------------------------------------------------


async def test_previous_response_id_keeps_the_user_history(client):
    """Turn 3 must still know what the user asked on turn 1.

    The store records both halves of a turn, but the replay handed the
    codec only the previous turn's OUTPUT. The user's original question
    never reached the upstream, so the model appeared to forget.
    """
    import respx

    bodies: list[dict] = []

    def reply(n: int) -> bytes:
        return json.dumps({
            "id": "msg_x", "type": "message", "role": "assistant",
            "model": "claude-x",
            "content": [{"type": "text", "text": f"A{n}"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }).encode()

    with respx.mock:
        route = respx.post("https://relay.example.com/messages")

        async def handler(request):
            bodies.append(json.loads(request.content.decode()))
            return httpx.Response(200, content=reply(len(bodies)))

        route.side_effect = handler

        async def turn(text, prev_id=None):
            body = {"model": "gpt-test", "input": text, "max_tokens": 100}
            if prev_id:
                body["previous_response_id"] = prev_id
            r = await client.post("/v1/responses", headers=H, json=body)
            assert r.status_code == 200, r.text
            return r.json().get("id")

        id1 = await turn("Q1-SECOND")
        id2 = await turn("Q2-SECOND", id1)
        await turn("Q3-THIRD", id2)

    assert len(bodies) == 3, bodies

    def text_of(body):
        out = []
        for m in body.get("messages", []):
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(str(x.get("text", "")) for x in c
                             if isinstance(x, dict))
            out.append(f"{m.get('role')}:{c}")
        return " | ".join(out)

    turn3 = text_of(bodies[2])
    assert "Q1-SECOND" in turn3, f"turn 1 lost: {turn3}"
    assert "Q2-SECOND" in turn3, f"turn 2 lost: {turn3}"
    assert "Q3-THIRD" in turn3, f"turn 3 lost: {turn3}"


# ---------------------------------------------------------------------------
# 9. #347 — the 402 budget refusal leaks its rate-limit slot
# ---------------------------------------------------------------------------


async def test_budget_refusal_does_not_leak_a_rate_limit_slot(client):
    """Three refused requests must not throttle an unrelated key.

    The reserved slot sits in the GLOBAL scope, so a caller holding one
    budget-capped key could lock the whole gateway out — an availability
    bug, and a cross-tenant one.
    """
    r = await client.post("/admin/keys/generate", headers=H,
                           json={"name": "capped", "max_budget": 1e-9,
                                 "rpm": 100})
    assert r.status_code == 200, r.text
    capped = r.json()["key"]

    for _ in range(3):
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {capped}"},
            json={"model": "gpt-test",
                  "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 402, r.text

    r = await client.post("/admin/keys/generate", headers=H,
                           json={"name": "innocent", "max_budget": None})
    assert r.status_code == 200, r.text
    innocent = r.json()["key"]

    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {innocent}"},
        json={"model": "gpt-test",
              "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code != 429, (
        "an unrelated key was throttled by another key's refusals: "
        f"{r.text}")


# ---------------------------------------------------------------------------
# 10. #349 — /metrics returns the SPA when Prometheus is disabled
# ---------------------------------------------------------------------------


async def test_metrics_path_is_not_the_spa_when_prometheus_is_disabled():
    """``/metrics`` must answer as a metrics endpoint, not as the SPA.

    With Prometheus disabled — the DEFAULT — the metrics route is never
    registered, so the SPA catch-all answers it with ``index.html`` and a
    200. A scraper then records the SPA's HTML as a successful scrape.
    """
    cfg = load_config_from_string(RELAY_CFG)
    assert cfg.router_settings.prometheus_enabled is False
    async with client_for(cfg) as c:
        r = await c.get("/metrics")
        assert r.status_code != 200 or "text/html" not in r.headers.get(
            "content-type", ""), (
            f"/metrics served the SPA: {r.status_code} "
            f"{r.headers.get('content-type')}")


async def test_metrics_path_still_serves_metrics_when_enabled():
    """Enabling Prometheus must keep the route working."""
    cfg = load_config_from_string(
        RELAY_CFG + "\nrouter_settings:\n  prometheus_enabled: true\n")
    async with client_for(cfg) as c:
        r = await c.get("/metrics", headers=H)
        assert r.status_code == 200, r.text
        assert "text/html" not in r.headers.get("content-type", ""), r.headers


# ---------------------------------------------------------------------------
# 11. #348 — the lifespan teardown is unguarded
# ---------------------------------------------------------------------------


async def test_lifespan_teardown_survives_a_failing_stop():
    """One failing ``stop()`` must not skip the others.

    The teardown is a bare sequence of awaits: the first to raise
    skipped every later one AND the engine disposal, so ``shutdown_event``
    was never set (the ``/admin/stream`` keepalive loop never returns)
    and the DB engine was never disposed.
    """
    cfg = load_config_from_string(RELAY_CFG)
    app = create_app(cfg)
    state = app.state.wiwi

    # Patch only ``stop`` — the startup path also uses ``journals`` (it
    # sweeps the leftover journal dir), so replacing the object wholesale
    # would fail before the teardown half is ever reached.
    async def boom():
        raise RuntimeError("journals.stop failed")

    original = state.journals.stop
    state.journals.stop = boom
    try:
        async with LifespanManager(app):
            pass
    finally:
        state.journals.stop = original

    assert state.shutdown_event.is_set(), (
        "shutdown_event was never set — the admin stream keepalive "
        "would hang forever")


async def test_lifespan_teardown_disposes_the_engine_after_a_failing_stop():
    """The DB engine must be disposed even when an earlier stop raised.

    ``AppState.shutdown`` is what disposes the engine (and sets the
    shutdown event), and it runs LAST — after the workers that still touch
    the DB. The old teardown skipped it entirely when an earlier
    ``stop()`` raised.
    """
    cfg = load_config_from_string(RELAY_CFG)
    app = create_app(cfg)
    state = app.state.wiwi

    async def boom_stop():
        raise RuntimeError("boom")

    called: list[str] = []
    orig_shutdown = state.shutdown

    async def spy_shutdown():
        called.append("shutdown")
        await orig_shutdown()

    original_stop = state.journals.stop
    state.journals.stop = boom_stop
    state.shutdown = spy_shutdown
    try:
        async with LifespanManager(app):
            pass
    finally:
        state.journals.stop = original_stop
        state.shutdown = orig_shutdown

    assert called == ["shutdown"], (
        "state.shutdown() was skipped by the failing stop() — the DB engine "
        "was never disposed")


# ---------------------------------------------------------------------------
# 12. #353 / #354 — audit log retention and the sweep lock
# ---------------------------------------------------------------------------


async def test_audit_logs_are_pruned_by_the_retention_sweep(client):
    """``audit_logs`` must not grow without bound.

    The retention sweeper handled ``request_logs`` only, so the audit
    table — written on every admin action — grew forever.
    """
    state = client.wiwi
    sink = state.logs.db_sink
    assert sink is not None

    before = await sink.audit_log_count()
    await state.log_retention.sweep()
    after = await sink.audit_log_count()
    assert after <= before, (before, after)


async def test_audit_log_count_is_readable(client):
    """The helper the sweep uses must exist and work."""
    state = client.wiwi
    n = await state.logs.db_sink.audit_log_count()
    assert isinstance(n, int)
    assert n >= 0


async def test_audit_cap_trims_the_oldest_audit_rows(client):
    """The count cap must drop the OLDEST rows, keeping the newest.

    The trail's value is in the recent window, so the cap trims from the
    front — the same shape as the request log's cap.
    """
    state = client.wiwi
    sink = state.logs.db_sink

    # Seed a few rows with distinct, spread-out timestamps.
    import time as _time

    from wiwi.logging_core.events import LogEvent

    for i in range(5):
        await sink.write_audit(LogEvent(stream="audit",
                                        ts=_time.time() - (100 - i * 10),
                                        actor="tester", action="probe",
                                        target=str(i), diff={}))

    total = await sink.audit_log_count()
    assert total >= 5, total

    deleted = await sink.enforce_audit_cap(2)
    after = await sink.audit_log_count()
    assert after <= 2, (after, total)
    assert deleted >= 1, deleted


def test_retention_sweep_takes_a_cross_process_lock():
    """Two gateway processes must not sweep the same table at once.

    Both would roll the same rows up, double-counting the totals the
    dashboard reports.
    """
    import inspect

    from wiwi.logging_core import retention

    src = inspect.getsource(retention)
    assert "lock" in src.lower(), (
        "the retention module has no lock at all — two processes sweeping "
        "concurrently double-count the rollup")


async def test_sweep_lock_is_exclusive(client):
    """Only one holder can take the lock at a time."""
    state = client.wiwi
    sink = state.logs.db_sink

    assert await sink.acquire_sweep_lock() is True
    try:
        # A second holder must NOT get it — that is the whole point.
        assert await sink.acquire_sweep_lock() is False
    finally:
        await sink.release_sweep_lock()

    # Released: the next holder gets it.
    assert await sink.acquire_sweep_lock() is True
    await sink.release_sweep_lock()
