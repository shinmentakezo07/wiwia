"""Round-42 audit regressions — AUDIT #89-#111.

Findings from an audit of paths the thematic suite does not cover. Each test
fails against the pre-fix source and pins the observable contract, not the
implementation.

Covered here (the ones reproducible without a live upstream):
- #90 non-streaming over-budget response is logged twice
- #91 streaming 401 has no on-demand token refresh
- #92 a 200 whose body fails to decode bypasses retries/failover
- #93 deployment cooldown counts absolute failures with no success decay
- #94 Gemini ``thought: true`` parts leak as visible text
- #95 OpenRouter ``decode_response`` crashes on dict-form tool arguments
- #96 HealthHealer treats any HTTP 200 as healthy without decoding the body
- #97 HealthHealer restores a retired key from a model-error probe
- #98 resume credits the key at connect time (double ``req_count``)
- #99 ``stream_grace_drain_s > 1`` is truncated by the 1 s consumer cancel grace
- #100 malformed ``text.format`` 500s the Responses surface
- #102 Gemini request encoder drops ``ThinkingPart``
- #103 Anthropic stream decoder drops ``redacted_thinking``
- #104 ``_arg_bufs`` accumulation is unbounded / O(n^2)
- #106 resume discards the resumed attempt's usage and cost
- #107 mid-stream resume builds an unanswered assistant ``tool_use`` turn
- #108 loop detection is charged to provider/key health
- #109 alias chains beyond 8 hops silently truncate / cycles resolve arbitrarily
- #110 non-dict SSE frames crash decoders
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx
import orjson
import pytest
import respx
from asgi_lifespan import LifespanManager

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
from wiwi.ir.types import Request
from wiwi.router.router import Router
from wiwi.streaming import deltas as dl


def _cfg(**router_overrides) -> WiwiConfig:
    rs = RouterSettings(num_retries=0, allowed_fails=2, cooldown_time=0.05)
    for k, v in router_overrides.items():
        setattr(rs, k, v)
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="sk-aaaaaaaaaaaaaaaa")]),
        ],
        model_list=[
            ModelEntry(model_name="gpt-4o",
                       wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
        ],
        router_settings=rs,
    )


# ---------------------------------------------------------------------------
# #89 — the signup throttle must actually consume slots
# ---------------------------------------------------------------------------

async def test_signup_throttle_counts_attempts():
    """Repeated signups from one address must eventually be throttled.

    Pre-fix ``auth_signup`` never called ``record_failure``, so ``check`` always
    saw an empty bucket and one IP could mint unlimited accounts — each of
    which mints a playground key and runs a 200k-iteration PBKDF2 hash
    (AUDIT #89).
    """
    import wiwi.server.app as app_mod

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-aaaaaaaaaaaaaaaa")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            codes = []
            for i in range(8):
                r = await c.post("/auth/signup", json={
                    "username": f"user{i}", "password": "password1"})
                codes.append(r.status_code)
    # The 5-per-hour cap must bite before all 8 attempts succeed.
    assert 429 in codes, (
        f"signup throttle never fired for 8 attempts: {codes}"
    )
    assert codes.count(201) <= 5, (
        f"signup throttle allowed {codes.count(201)} accounts (cap is 5): {codes}"
    )


# ---------------------------------------------------------------------------
# #90 — an over-budget response must be logged once
# ---------------------------------------------------------------------------

async def test_over_budget_response_logged_once():
    """A non-streaming 402 must not also emit the success log event.

    Pre-fix ``run_chat_like`` logged the 200 event and then logged a second
    event after the budget check rejected the spend update, so the request was
    double-counted in stats/rollups (AUDIT #90). The budget check is the
    last-write crossing: a request that pushes spend past ``max_budget`` is
    served a 402, and only that status may be recorded.
    """
    import wiwi.server.app as app_mod

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-aaaaaaaaaaaaaaaa")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=master,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = app_mod.create_app(cfg)
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }))
        async with LifespanManager(app):
            state = app.state.wiwi
            seen: list[str] = []

            state.logs.log_request = lambda ev: seen.append(ev.request_id)  # type: ignore[assignment]
            # Reject the post-success spend update, as a key crossing its hard
            # cap on this request would (the auth layer admits it because the
            # crossing only becomes visible when the cost is written).
            async def _reject(_key_id, _cost):
                return False

            state.auth.update_spend = _reject  # type: ignore[assignment]

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as c:
                auth = {"Authorization": f"Bearer {master}"}
                created = await c.post("/admin/keys/generate", headers=auth, json={
                    "name": "budgeted", "max_budget": 10.0})
                assert created.status_code in (200, 201), created.text
                sk = created.json().get("key") or created.json().get("plaintext")
                assert sk, created.text
                resp = await c.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {sk}"},
                    json={"model": "gpt-4o",
                          "messages": [{"role": "user", "content": "hi"}]})

    assert resp.status_code == 402, resp.text
    # Exactly one event must be recorded for the request.
    assert len(seen) == 1, (
        f"over-budget request logged {len(seen)} events (expected 1): {seen}"
    )


# ---------------------------------------------------------------------------
# #92 — an undecodable 200 must be a retryable WiwiError
# ---------------------------------------------------------------------------

async def test_undecodable_200_is_retryable_wiwi_error():
    """A 200 with a non-JSON body must fail over, not propagate a 500.

    Pre-fix ``adapter.decode_response`` ran outside any try/except, so
    ``JSONDecodeError`` escaped past ``execute_with_retries`` (which catches
    only ``WiwiError``) with no retry, fallback, or key penalty (AUDIT #92).
    """
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine

    r = Router(_cfg())
    gw = Gateway(r, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[]))
    ctx.group = "gpt-4o"
    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]

    async def scenario():
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200, text="<html>cloudflare interstitial</html>",
                    headers={"content-type": "text/html"}))
            with pytest.raises(Exception) as ei:
                await gw._call_once(dep, key, ctx)
        return ei.value

    exc = await scenario()
    from wiwi.providers.base import WiwiError

    assert isinstance(exc, WiwiError), (
        f"undecodable 200 raised {type(exc).__name__}, not WiwiError: {exc!r}"
    )
    assert exc.retryable is True, (
        f"undecodable 200 was not retryable (status={exc.status})"
    )


# ---------------------------------------------------------------------------
# #93 — success must decay the deployment failure count
# ---------------------------------------------------------------------------

async def test_streaming_401_is_refreshed_and_retried():
    """A streaming 401 must trigger the on-demand token refresh, then retry.

    ``_call_once`` and ``_complete_via_stream`` already refresh on 401, but the
    pump that serves every streaming client request did not: it surfaced the
    recoverable auth error (and, in any_error mode, penalised a healthy key)
    instead of retrying with the rotated token (AUDIT #91).
    """
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine

    cfg = _cfg()
    r = Router(cfg)
    gw = Gateway(r, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[],
                                        stream=True))
    ctx.group = "gpt-4o"

    rotated: list[tuple[str, str]] = []

    async def _refresh(provider_name, key_label):
        rotated.append((provider_name, key_label))
        return True

    # Register the hook on the provider type used by this deployment.
    gw._on_demand_refresh_hooks["openai"] = _refresh

    def _responder(request: httpx.Request) -> httpx.Response:
        if not rotated:
            return httpx.Response(401, json={"error": "expired token"})
        body = (json.dumps({"choices": [{"index": 0, "delta": {
            "content": "hi"}}]}) + "\n"
            + json.dumps({"choices": [{"index": 0, "delta": {},
                                       "finish_reason": "stop"}]}) + "\n"
            + "[DONE]\n")
        return httpx.Response(
            200, text="".join(f"data: {line}\n\n" for line in body.strip().splitlines()),
            headers={"content-type": "text/event-stream"})

    with respx.mock:
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=_responder)
        text = ""
        errored = None
        async for d in gw.stream(ctx):
            if isinstance(d, dl.TextDelta):
                text += d.text
            elif isinstance(d, dl.StreamError):
                errored = d
                break

    assert rotated, "streaming 401 did not trigger the on-demand refresh"
    assert errored is None, f"stream surfaced an error despite refresh: {errored}"
    assert text == "hi", f"retry after refresh produced {text!r}"
    assert route.call_count >= 2, "the stream did not retry after the 401"


async def test_success_decays_deployment_failures():
    """A successful completion must reset/prune ``dep.fails``.

    Pre-fix ``record_fail`` only appended timestamps, so a high-volume
    deployment with a tiny error rate accumulated ``allowed_fails`` and was
    cooled continuously (AUDIT #93).
    """
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine

    r = Router(_cfg(allowed_fails=3))
    gw = Gateway(r, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[]))
    ctx.group = "gpt-4o"
    dep = r.groups["gpt-4o"][0]

    # Two failures, then a success: the success must decay the streak so stale
    # timestamps do not accumulate toward the cooldown threshold.
    dep.record_fail(allowed_fails=3, cooldown_time=1.0)
    dep.record_fail(allowed_fails=3, cooldown_time=1.0)
    assert len(dep.fails) == 2

    async def scenario():
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={
                    "id": "x", "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": "hi"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }))
            # `complete` runs the full execute_with_retries path, where success
            # accounting lives.
            await gw.complete(ctx)

    await scenario()
    assert len(dep.fails) == 1, (
        f"a successful completion did not decay the failure streak "
        f"(2 -> {len(dep.fails)})"
    )


# ---------------------------------------------------------------------------
# #94 — Gemini thought parts must not leak as visible text
# ---------------------------------------------------------------------------

def test_gemini_thought_part_is_not_visible_text():
    """A ``thought: true`` part must become thinking, not assistant text.

    Pre-fix the stream matched ``if "text" in part`` before checking
    ``part.get("thought")``, so chain-of-thought was emitted as TextDelta and
    returned as the reply (AUDIT #94).
    """
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    out = a.decode_stream_event("", orjson.dumps({
        "candidates": [{"content": {"parts": [
            {"text": "secret reasoning", "thought": True,
             "thoughtSignature": "sig-1"}]}}],
    }).decode())

    visible = "".join(d.text for d in out if isinstance(d, dl.TextDelta))
    thinking = "".join(d.text for d in out if isinstance(d, dl.ThinkingDelta))
    assert "secret reasoning" not in visible, (
        f"chain-of-thought leaked as visible text: {visible!r}"
    )
    assert "secret reasoning" in thinking, (
        f"thought part was dropped entirely: {out}"
    )


# ---------------------------------------------------------------------------
# #95 — OpenRouter decode_response must accept dict-form tool arguments
# ---------------------------------------------------------------------------

def test_openrouter_dict_form_tool_arguments_do_not_crash():
    """``arguments`` as a JSON object must decode, not raise TypeError.

    Pre-fix ``json.loads(raw_args)`` on a dict raised TypeError, which the
    ``except json.JSONDecodeError`` did not catch, 500ing every replayed
    turn (AUDIT #95).
    """
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    a = OpenRouterAdapter()
    body = orjson.dumps({
        "id": "x", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": [{
                                     "id": "call_1", "type": "function",
                                     "function": {"name": "foo",
                                                  "arguments": {"a": 1}},
                                 }]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    turn = a.decode_response(200, body)
    assert turn.tool_calls, f"dict-form tool args produced no call: {turn}"
    assert turn.tool_calls[0].args == {"a": 1}, (
        f"dict-form args not preserved: {turn.tool_calls[0].args!r}"
    )


# ---------------------------------------------------------------------------
# #96 / #97 — HealthHealer verdicts
# ---------------------------------------------------------------------------

def test_healer_probe_treats_200_envelope_error_as_unhealthy():
    """A 200 body carrying a WorkBuddy error envelope is not HEALTHY.

    Pre-fix ``_probe`` returned HEALTHY on ``status_code == 200`` without
    inspecting the body, so a dead session was restored into rotation
    (AUDIT #96).
    """
    from wiwi.core.recovery import ProbeVerdict, probe_verdict

    verdict = probe_verdict(200, body=orjson.dumps(
        {"code": 12153, "msg": "Offline user session"}))
    assert verdict is not ProbeVerdict.HEALTHY, (
        f"200 with a dead-session envelope classified as {verdict}"
    )
    # A genuine 200 body is still healthy.
    healthy = probe_verdict(200, body=orjson.dumps(
        {"choices": [{"message": {"content": "ok"}}]}))
    assert healthy is ProbeVerdict.HEALTHY


async def test_healer_does_not_restore_key_on_model_error_probe():
    """A 400/404 probe must not grow the *key* restore streak.

    Pre-fix ``_probe_pair`` incremented the key restore streak and restored
    the key from ``CREDS_VALID_MODEL_BAD`` even though the probe never
    exercised the key successfully (AUDIT #97).
    """
    from wiwi.core.recovery import (
        HealerSettings,
        HealthHealer,
        ProbeVerdict,
        _TargetState,
    )

    cfg = _cfg()
    r = Router(cfg)
    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]

    healer = HealthHealer(r, HealerSettings(probes_to_restore=1))

    async def fake_probe(_dep, _key):
        return ProbeVerdict.CREDS_VALID_MODEL_BAD, "model not found", None

    healer._probe = fake_probe  # type: ignore[assignment]
    await healer._probe_pair(dep, key)

    kst = healer._state.get(("key", (dep.provider.name, key.label)), _TargetState())
    assert kst.streak == 0, (
        f"a model-error probe grew the key restore streak to {kst.streak}"
    )


# ---------------------------------------------------------------------------
# #98 — resume must not credit the key at connect time
# ---------------------------------------------------------------------------

async def test_resume_does_not_credit_key_at_connect():
    """A resume must credit the key once, at clean completion.

    Pre-fix ``_attempt_resume`` called ``on_result_locked(key, 200, None)`` at
    connect, then the pump credited the same key again on completion —
    double ``req_count`` — and a connect-then-die resume reset ``err_count``
    (the AUDIT #6 defect) (AUDIT #98).
    """
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.streaming.resume import StreamTape

    r = Router(_cfg())
    gw = Gateway(r, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[],
                                        stream=True))
    ctx.group = "gpt-4o"
    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]
    key.req_count = 0
    key.err_count = 0

    # A tape that already contains some content, so the resume is eligible.
    tape = StreamTape()
    tape.append(dl.TextDelta("partial answer already delivered"))
    queue: asyncio.Queue = asyncio.Queue()

    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, text="",
                headers={"content-type": "text/event-stream"}))
        resumed, new_task = await gw._attempt_resume(
            ctx, tape, queue)

    assert resumed is True, "resume did not connect"
    # The connect must NOT have credited the key; only a completed pump may.
    assert key.req_count == 0, (
        f"resume connect credited the key at connect time "
        f"(req_count={key.req_count})"
    )
    if new_task is not None:
        new_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await new_task


# ---------------------------------------------------------------------------
# #99 — a configured grace drain above 1 s must not be truncated
# ---------------------------------------------------------------------------

def test_pump_cancel_grace_follows_configured_drain():
    """The consumer's cancel grace must cover ``stream_grace_drain_s``.

    Pre-fix it was a hardcoded 1 s, so any configured drain above 1 s was cut
    off with no log (AUDIT #99).
    """
    from wiwi.core import gateway as gw_mod

    # A helper must exist that derives the wait from the configured drain.
    assert hasattr(gw_mod, "pump_cancel_grace"), (
        "no helper derives the cancel grace from stream_grace_drain_s"
    )
    assert gw_mod.pump_cancel_grace(5.0) >= 5.0, (
        "configured 5 s drain yielded a shorter cancel grace"
    )
    assert gw_mod.pump_cancel_grace(0.0) >= 1.0, (
        "cancel grace must keep its 1 s floor"
    )


# ---------------------------------------------------------------------------
# #100 — a malformed text.format must be a client error
# ---------------------------------------------------------------------------

def test_responses_malformed_text_format_is_not_a_500():
    """``"text": {"format": "text"}`` must not raise AttributeError.

    Pre-fix ``text_field.get("format") or {}`` returned the truthy string,
    then ``fmt.get("type")`` raised and surfaced as an unhandled 500
    (AUDIT #100).
    """
    from wiwi.wire import openai_responses as orp

    req = orp.decode_request({"model": "m", "input": "x",
                              "text": {"format": "text"}})
    assert req.gen_params.response_format is None, (
        f"malformed text.format produced {req.gen_params.response_format!r}"
    )


# ---------------------------------------------------------------------------
# #102 — Gemini request encoder must not drop ThinkingPart
# ---------------------------------------------------------------------------

def test_gemini_encoder_preserves_thinking_part():
    """A ``ThinkingPart`` in history must reach the Gemini request body.

    Pre-fix the encoder's part loop handled only Text/Image/ToolUse/ToolResult
    and silently dropped ThinkingPart (AUDIT #102).
    """
    from wiwi.ir.types import Message, TextPart, ThinkingPart
    from wiwi.providers.gemini_adapter import GeminiAdapter

    a = GeminiAdapter()
    req = Request(model="gemini-2.5-flash", messages=[
        Message(role="user", parts=[TextPart(text="hi")]),
        Message(role="assistant", parts=[
            ThinkingPart(text="prior reasoning"),
            TextPart(text="answer"),
        ]),
        Message(role="user", parts=[TextPart(text="more")]),
    ])
    body = a.encode_request(req, "gemini-2.5-flash", {})

    serialized = json.dumps(body)
    assert "prior reasoning" in serialized, (
        f"ThinkingPart was dropped from the Gemini body: {serialized}"
    )


# ---------------------------------------------------------------------------
# #103 — Anthropic stream decoder must preserve redacted_thinking
# ---------------------------------------------------------------------------

def test_anthropic_stream_preserves_redacted_thinking():
    """A ``redacted_thinking`` block must survive the streaming path.

    Pre-fix ``content_block_start`` recognized only tool blocks, so the
    encrypted blob emitted nothing and ``content_block_stop`` was a no-op; a
    client replaying the stream omitted the block and Anthropic rejected the
    history (AUDIT #103). The contract is the wire: decoding the upstream event
    and re-encoding must yield a ``redacted_thinking`` content block carrying
    the blob.
    """
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    from wiwi.wire import anthropic_messages as am

    a = AnthropicAdapter()
    deltas = a.decode_stream_event("content_block_start", orjson.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "redacted_thinking", "data": "ENCRYPTED"},
    }).decode())
    assert deltas, "redacted_thinking block start produced no delta"

    enc = am.AnthropicStreamEncoder("claude-x", "req-1")
    wire = b"".join(f for d in deltas for f in [enc.feed(d)] if f)
    payload = wire.decode()
    assert '"redacted_thinking"' in payload, (
        f"encoder did not re-emit a redacted_thinking block: {payload!r}"
    )
    assert "ENCRYPTED" in payload, (
        f"redacted blob lost on the streaming path: {payload!r}"
    )


# ---------------------------------------------------------------------------
# #104 — _arg_bufs accumulation must be bounded / not O(n^2)
# ---------------------------------------------------------------------------

async def test_arg_buf_accumulation_reassembles_fragments():
    """Many arg fragments must reassemble into the exact original payload.

    Pre-fix each fragment did ``buf = buf + fragment`` — O(n^2) copying and
    unbounded for a multi-MB payload (AUDIT #104). The observable contract is
    that a stream split into N fragments still yields the joined args; a
    list-append/join strategy satisfies both correctness and the cost bound.
    """
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine
    from wiwi.providers.openai_adapter import OpenAIAdapter

    r = Router(_cfg())
    gw = Gateway(r, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[], stream=True))
    ctx.group = "gpt-4o"
    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]

    # Split a large JSON object into many small fragments, as a streaming
    # provider would. The reassembled call must equal the original.
    payload = {"k" + str(i): "v" * 8 for i in range(500)}
    blob = json.dumps(payload, separators=(",", ":"))
    step = 7
    fragments = [blob[i:i + step] for i in range(0, len(blob), step)]

    def _sse(delta_obj: dict) -> str:
        return "data: " + json.dumps(delta_obj) + "\n\n"

    body = ""
    body += _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "foo", "arguments": ""}}]}}]})
    for frag in fragments:
        body += _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": frag}}]}}]})
    body += _sse({"choices": [{"index": 0, "delta": {},
                               "finish_reason": "tool_calls"}]})
    body += "data: [DONE]\n\n"

    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}))
        turn = await gw._complete_via_stream(
            dep, key, ctx, OpenAIAdapter())

    assert turn.tool_calls, "no tool call reassembled from fragments"
    assert turn.tool_calls[0].args == payload, (
        f"fragmented args did not reassemble: {turn.tool_calls[0].args!r}"
    )


# ---------------------------------------------------------------------------
# #106 — resume must fold its usage/cost into the originating context
# ---------------------------------------------------------------------------

def test_resume_merges_usage_into_originating_context():
    """A resumed attempt's usage must be reconciled to the originating ctx.

    Pre-fix the resumed pump priced into a throwaway ``resume_ctx`` and the
    caller only swapped the task reference, so resumed tokens were never
    charged or reconciled (AUDIT #106).
    """
    from wiwi.core.context import RequestContext
    from wiwi.ir.types import Request, Usage

    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[]))
    resume_ctx = RequestContext(surface="chat",
                                ir_req=Request(model="gpt-4o", messages=[]))
    resume_ctx.usage = Usage(prompt_tokens=5, completion_tokens=7)
    resume_ctx.cost = 0.25

    from wiwi.core import gateway as gw_mod

    assert hasattr(gw_mod, "merge_resume_context"), (
        "no helper folds the resume context back into the origin"
    )
    gw_mod.merge_resume_context(ctx, resume_ctx)
    assert ctx.cost == 0.25, f"resume cost not merged: {ctx.cost}"
    assert ctx.usage is not None or ctx._stream_usage is not None, (
        "resume usage not merged"
    )


# ---------------------------------------------------------------------------
# #107 — a resumed turn with tool_use must answer it
# ---------------------------------------------------------------------------

def test_resume_answers_replayed_tool_use():
    """A replay with tool calls must synthesize a tool_result for each.

    Pre-fix the follow-up user turn carried only plain "Continue" text, so
    Anthropic rejected the resumed request 400 (AUDIT #107).
    """
    from wiwi.ir.types import ToolResultPart
    from wiwi.streaming import deltas as dl
    from wiwi.streaming.resume import StreamTape, build_continuation_messages

    tape = StreamTape()
    tape.append(dl.ToolCallOpen(index=0, id="call_1", name="foo"))
    tape.append(dl.ToolCallArgsDelta(index=0, args_fragment="{}"))
    tape.append(dl.ToolCallClose(index=0))

    msgs = build_continuation_messages(tape, [])
    follow_up = msgs[-1]
    assert follow_up.role == "user"
    answered = {p.tool_use_id for p in follow_up.parts
                if isinstance(p, ToolResultPart)}
    assert "call_1" in answered, (
        f"replayed tool_use was not answered in the follow-up: {follow_up.parts}"
    )


# ---------------------------------------------------------------------------
# #108 — loop detection must not penalise provider/key health
# ---------------------------------------------------------------------------

async def test_loop_detection_does_not_penalise_key_health():
    """A repetition loop is a model-quality failure, not a key failure.

    Pre-fix the loop branch called ``_note_stream_failure``, incrementing
    ``err_count`` and cooling the deployment, eventually retiring a healthy
    key and provider (AUDIT #108).
    """
    from wiwi.core.gateway import Gateway
    from wiwi.cost.pricing import CostEngine

    cfg = _cfg(stream_loop_detection=True, stream_loop_limit=3)
    r = Router(cfg)
    gw = Gateway(r, CostEngine())
    ctx = RequestContext(surface="chat",
                         ir_req=Request(model="gpt-4o", messages=[],
                                        stream=True))
    ctx.group = "gpt-4o"
    dep = r.groups["gpt-4o"][0]
    key = dep.provider.keys[0]
    err_before = key.err_count
    fails_before = len(dep.fails)
    cooldown_before = dep.cooldown_until

    # A stream that repeats the same text delta forever: the loop detector
    # must abort it while leaving key/deployment health untouched.
    chunk = json.dumps({"choices": [{"index": 0, "delta": {
        "content": "spinning "}}]})
    body = "".join(f"data: {chunk}\n\n" for _ in range(50))

    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}))
        errors: list[dl.StreamError] = []
        async for d in gw.stream(ctx):
            if isinstance(d, dl.StreamError):
                errors.append(d)
                break

    assert errors, "loop detector never aborted the repeating stream"
    assert "loop" in errors[0].message.lower(), errors[0].message
    assert key.err_count == err_before, (
        f"loop abort changed key err_count: {err_before} -> {key.err_count}"
    )
    assert len(dep.fails) == fails_before, (
        f"loop abort recorded a deployment failure: {fails_before} -> "
        f"{len(dep.fails)}"
    )
    assert dep.cooldown_until == cooldown_before, (
        "loop abort cooled a healthy deployment"
    )


# ---------------------------------------------------------------------------
# #109 — alias cycle / runaway chains must not resolve arbitrarily
# ---------------------------------------------------------------------------

def test_alias_cycle_resolves_to_nothing():
    """An alias cycle must not silently route to an intermediate group.

    Pre-fix ``resolve_group`` walked 8 hops and returned whatever intermediate
    group it stopped at (AUDIT #109).
    """
    from wiwi.router.router import Router

    cfg = _cfg()
    cfg.model_list = [
        ModelEntry(model_name="real-a",
                   wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
        ModelEntry(model_name="real-b",
                   wiwi_params=DeploymentParams(provider="p1", model="gpt-4o")),
    ]
    # A cycle whose members are *real* groups: the pre-fix bounded walk would
    # land on one of them and resolve to it instead of failing closed.
    cfg.router_settings.model_group_alias = {"real-a": "real-b",
                                             "real-b": "real-a"}
    r = Router(cfg)

    # A cycle must be rejected, not resolved arbitrarily.
    group, deps = r.resolve_group("real-a")
    assert not deps, (
        f"alias cycle resolved to {deps!r} instead of failing closed"
    )
    assert group is None, f"alias cycle returned group name {group!r}"


# ---------------------------------------------------------------------------
# #110 — non-dict SSE frames must not crash decoders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", ["null", '"text"', "[1,2,3]", "42"])
def test_openrouter_non_dict_sse_frame_does_not_crash(payload):
    """A non-dict SSE frame must yield no deltas, never an exception.

    Pre-fix ``chunk.get(...)`` raised AttributeError outside the JSON handler
    (AUDIT #110).
    """
    from wiwi.providers.openrouter_adapter import OpenRouterAdapter

    a = OpenRouterAdapter()
    a.decode_stream_event("", payload)  # must not raise


def test_openai_choice_non_dict_does_not_crash():
    """A non-dict ``choices[0]`` must not crash the OpenAI decoder (AUDIT #110)."""
    from wiwi.providers.openai_adapter import OpenAIAdapter

    a = OpenAIAdapter()
    a.decode_stream_event("", orjson.dumps(
        {"choices": ["not-a-dict"]}).decode())  # must not raise
