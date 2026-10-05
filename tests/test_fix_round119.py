"""Regression tests for AUDIT #356 — a long-silent upstream was cut, not retried.

The bug, as reported: ``upstream idle >30s between chunks``. A model that was
still legitimately working (a reasoning model thinking through a code task, a
cold prompt against a big context) produced no bytes for longer than
``stream_idle_timeout_s`` and the gateway declared the turn dead. Three separate
defects sat behind that one message:

1. **One budget for two phases.** The inter-chunk watchdog also governed the
   pre-first-content phase, where silence is normal rather than diagnostic.
2. **No failover.** ``ready`` was signalled when the 200 *headers* arrived, so
   by the time a pre-first-chunk fault was detected ``execute_with_retries`` had
   already returned success. The pump's pre-``started`` branch — the one that
   boxes a retryable error — was unreachable for this class of fault.
3. **A blank message.** httpx's timeout exceptions carry no message, and both
   stream arms passed ``str(e)`` straight through, so the client got a
   ``StreamError`` with an empty string.

The fixes are in ``wiwi/config.py`` (``stream_first_chunk_timeout_s``) and
``wiwi/core/gateway.py`` (a first-byte ``ready``, a two-phase idle budget, the
lifted httpx read deadline, and ``_transport_message``).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

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
from wiwi.core.gateway import (
    Gateway,
    _fmt_secs,
    _transport_message,
    stream_read_timeout,
)
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef, WiwiError
from wiwi.router.router import Deployment, ProviderAccount, Router
from wiwi.streaming import deltas as dl

AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path, *, first_chunk_s: float = 30.0, idle_s: float = 0.5,
         timeout: float = 120.0, num_retries: int = 1,
         ping_s: float = 0.0) -> WiwiConfig:
    """Primary ``a`` + sibling deployment ``b`` in the same group.

    ``b`` exists so a failover has somewhere to go; ``fallbacks`` maps the group
    so the retry loop reaches it after the primary's deployment is excluded.
    """
    return WiwiConfig(
        providers=[
            ProviderDef(name="a", provider="openai",
                        base_url="https://a.example/v1",
                        keys=[KeyDef(label="ka", key="sk-a")]),
            ProviderDef(name="b", provider="openai",
                        base_url="https://b.example/v1",
                        keys=[KeyDef(label="kb", key="sk-b")]),
        ],
        model_list=[
            ModelEntry(model_name="m",
                       wiwi_params=DeploymentParams(provider="a", model="m")),
            ModelEntry(model_name="m-b",
                       wiwi_params=DeploymentParams(provider="b", model="m-b")),
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db"),
        router_settings=RouterSettings(
            num_retries=num_retries, allowed_fails=1, cooldown_time=60.0,
            timeout=timeout,
            stream_idle_timeout_s=idle_s,
            stream_first_chunk_timeout_s=first_chunk_s,
            stream_ping_interval_s=ping_s,
            fallbacks={"m": ["m-b"]}),
    )


def _context() -> RequestContext:
    return RequestContext(
        surface="chat",
        ir_req=ir.Request(
            model="m", stream=True,
            messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])]),
        group="m")


def _chunk(content: str) -> bytes:
    return (f'data: {{"choices":[{{"delta":{{"content":{json.dumps(content)}}},'
            '"index":0}]}\n\n').encode()


_DONE = (b'data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}],'
         b'"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\ndata: [DONE]\n\n')


async def _answer(*parts: str) -> None:
    for p in parts:
        yield _chunk(p)
    yield _DONE


async def _stall_forever() -> None:
    await asyncio.sleep(3600)
    yield b""  # pragma: no cover — never reached; makes this an async generator


async def _drain(gw: Gateway, ctx: RequestContext) -> list:
    out = []
    async for d in gw.stream(ctx):
        out.append(d)
    return out


def _text(deltas) -> str:
    return "".join(d.text for d in deltas if isinstance(d, dl.TextDelta))


def _errors(deltas) -> list[dl.StreamError]:
    return [d for d in deltas if isinstance(d, dl.StreamError)]


# ---------------------------------------------------------------------------
# 1. The headline defect: a pre-first-chunk stall must fail over, not cut.
# ---------------------------------------------------------------------------

@respx.mock
async def test_stalled_pre_first_chunk_upstream_fails_over(tmp_path):
    """Deployment A accepts the request then goes silent.

    Pre-fix the client received ``StreamError("upstream idle >30s between
    chunks")`` and deployment B was never contacted, even with ``num_retries=1``
    and a healthy sibling — because ``ready`` fired on the 200 headers, so the
    retry loop had already returned success by the time the stall was visible.
    """
    stalled = respx.post("https://a.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_stall_forever(),
            headers={"content-type": "text/event-stream"}))
    good = respx.post("https://b.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_answer("hello ", "world"),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=0.4)), CostEngine())
    try:
        out = await asyncio.wait_for(_drain(gw, _context()), timeout=10)
    finally:
        await gw.aclose()

    assert stalled.call_count == 1, "the stalled upstream was not attempted"
    assert good.call_count == 1, (
        "the request did not fail over to the healthy sibling; a pre-first-chunk "
        "stall must be a retryable pre-content failure, not a client-visible error")
    assert _errors(out) == [], f"the client still saw an error: {_errors(out)}"
    assert _text(out) == "hello world", _text(out)
    # A clean failover, not a doubled one: exactly one terminal.
    terminals = [d for d in out
                 if isinstance(d, (dl.StreamEnd, dl.StreamError))]
    assert len(terminals) == 1, [type(d).__name__ for d in out]


@respx.mock
async def test_the_retryable_stall_is_logged_and_charged_once(tmp_path):
    """The stall must be visible in the request log, and the key fed once.

    ``execute_with_retries`` owns key-pool and cooldown accounting for a
    pre-content failure, so the pump must NOT also call
    ``_note_stream_failure`` — that would double-charge the same failure, which
    is the AUDIT #174 shape (keys retired at half the configured threshold).
    """
    respx.post("https://a.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_stall_forever(),
            headers={"content-type": "text/event-stream"}))
    respx.post("https://b.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_answer("ok"),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=0.4)), CostEngine())
    ctx = _context()
    try:
        await asyncio.wait_for(_drain(gw, ctx), timeout=10)
    finally:
        await gw.aclose()

    errors = [a.status for a in ctx.attempts]
    assert "idle_timeout" in errors, (
        f"the pre-content stall was not recorded in the request log: {errors}")
    assert errors.count("idle_timeout") == 1, (
        f"the same stall was recorded {errors.count('idle_timeout')} times; the "
        f"pump and the retry loop must not both account for it")


@respx.mock
async def test_a_200_that_never_produces_a_byte_is_retryable(tmp_path):
    """A 200 with an empty body is a pre-content failure, not a dead stream.

    Pre-fix this reached the mid-stream ``_fail_stream`` arm and the client saw
    "stream ended without completion"; the err_box branch that would have let
    the retry loop take over was unreachable because ``started`` was set at
    connect (AUDIT #356).
    """
    empty = respx.post("https://a.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=b"",
            headers={"content-type": "text/event-stream"}))
    good = respx.post("https://b.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_answer("recovered"),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_cfg(tmp_path)), CostEngine())
    try:
        out = await asyncio.wait_for(_drain(gw, _context()), timeout=10)
    finally:
        await gw.aclose()

    assert empty.call_count == 1
    assert good.call_count == 1, (
        "an empty 200 body must be retryable — nothing reached the client, so a "
        "different deployment can still answer the request whole")
    assert _text(out) == "recovered", _text(out)


# ---------------------------------------------------------------------------
# 2. The other half of the report: a model that is genuinely still working.
# ---------------------------------------------------------------------------

@respx.mock
async def test_a_long_pre_content_phase_is_not_cut(tmp_path):
    """Silence longer than the inter-chunk budget, inside the pre-content one.

    This is the reported scenario: a reasoning model thinking through a code
    task. The pre-content budget is the larger one, so the turn survives and the
    answer that eventually arrives reaches the client whole.
    """
    async def _slow_but_working() -> None:
        # 6x the inter-chunk budget below: well past the watchdog that used to
        # fire, well inside the pre-content budget.
        await asyncio.sleep(3.0)
        yield _chunk("late ")
        yield _chunk("answer")
        yield _DONE

    only = respx.post("https://a.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_slow_but_working(),
            headers={"content-type": "text/event-stream"}))
    respx.post("https://b.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_answer("wrong answer"),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=30.0, idle_s=0.5)), CostEngine())
    try:
        out = await asyncio.wait_for(_drain(gw, _context()), timeout=20)
    finally:
        await gw.aclose()

    assert _errors(out) == [], (
        f"a healthy long generation was cut: "
        f"{[e.message for e in _errors(out)]}")
    assert _text(out) == "late answer", _text(out)
    assert only.call_count == 1, "the working upstream must not have been retried"


@respx.mock
async def test_the_tight_budget_still_applies_between_content_chunks(tmp_path):
    """The generous pre-content budget must not become a blanket leniency.

    A gap *between* content chunks is a dead connection, not a thinking phase,
    so it is still charged against ``stream_idle_timeout_s`` and the stream is
    cut. Widening the pre-content budget must not quietly disable the watchdog.
    """
    async def _partial_then_stall() -> None:
        yield _chunk("partial")
        await asyncio.sleep(3600)

    stalled = respx.post("https://a.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_partial_then_stall(),
            headers={"content-type": "text/event-stream"}))
    good = respx.post("https://b.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_answer("replay"),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(
        # first_chunk_s far above idle_s: only the inter-chunk watchdog can fire.
        Router(_cfg(tmp_path, first_chunk_s=300.0, idle_s=0.4)), CostEngine())
    try:
        out = await asyncio.wait_for(_drain(gw, _context()), timeout=10)
    finally:
        await gw.aclose()

    errs = _errors(out)
    assert len(errs) == 1, (
        f"a mid-stream stall must terminate the stream; got {[type(d).__name__ for d in out]}")
    assert "idle" in errs[0].message, errs[0].message
    assert good.call_count == 0, (
        "the request was replayed after content had already reached the client; "
        "the partial text would be duplicated in the client's transcript")
    assert stalled.call_count == 1
    # And the partial the client did receive is preserved, exactly once.
    assert _text(out) == "partial", _text(out)


@respx.mock
async def test_a_first_chunk_timeout_of_zero_falls_back_to_the_idle_budget(
        tmp_path):
    """``<= 0`` means "use the inter-chunk budget", i.e. the pre-#356 behaviour.

    An operator who wants the old strictness back can say so without having to
    remember which of the two numbers to set — and ``0`` must not quietly become
    "wait forever", which would turn a stall into a hang.
    """
    stalled = respx.post("https://a.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_stall_forever(),
            headers={"content-type": "text/event-stream"}))
    good = respx.post("https://b.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_answer("served"),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=0.0, idle_s=0.4)),
                 CostEngine())
    ctx = _context()
    try:
        # A 10 s ceiling on a 0.4 s budget: this is about the stall being cut at
        # the inter-chunk budget, not about the failover.
        out = await asyncio.wait_for(_drain(gw, ctx), timeout=10)
    finally:
        await gw.aclose()

    assert stalled.call_count == 1, good.call_count
    assert "idle_timeout" in [a.status for a in ctx.attempts], (
        "0 did not fall back to the inter-chunk budget")
    assert _text(out) == "served", _text(out)


# ---------------------------------------------------------------------------
# 3. The blank-message defect.
# ---------------------------------------------------------------------------

def test_transport_message_is_never_blank():
    """httpx's timeout exceptions carry no message of their own.

    The no-arg form is what httpx itself raises from its timeout machinery, and
    ``str()`` of it is empty — the premise the old ``str(e)`` handling rested on.
    """
    # This is the shape httpx actually produces: httpcore raises its timeouts
    # with no arguments and httpx maps them with ``str(exc)``.
    blank = httpx.ReadTimeout("")
    assert str(blank) == "", "premise: httpx leaves a read timeout's str empty"

    msg = _transport_message(blank)
    assert msg.strip(), "a blank error reached the client"
    # With nothing to go on, the class name still says what happened.
    assert msg == "upstream ReadTimeout", msg
    # A transport error that DOES carry a message keeps it, without duplication.
    assert _transport_message(httpx.ReadError("boom")) == "upstream ReadError: boom"
    # A non-httpx exception still renders something usable.
    assert _transport_message(ValueError("bad")).startswith("upstream ")


@respx.mock
async def test_a_mid_stream_read_timeout_names_itself_to_the_client(tmp_path):
    """The client's error must say what happened.

    Pre-fix the mid-stream arm passed ``str(e)`` through, and for
    ``httpx.ReadTimeout`` that is ``""`` — a ``StreamError`` with a blank
    message, indistinguishable from a gateway bug (AUDIT #356).
    """

    async def _content_then_read_timeout() -> None:
        yield _chunk("hi")
        raise httpx.ReadTimeout("timed out")

    respx.post("https://a.example/v1/chat/completions").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_content_then_read_timeout(),
            headers={"content-type": "text/event-stream"}))

    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=300.0, idle_s=30.0)), CostEngine())
    try:
        out = await asyncio.wait_for(_drain(gw, _context()), timeout=10)
    finally:
        await gw.aclose()

    errs = _errors(out)
    assert len(errs) == 1, [type(d).__name__ for d in out]
    assert errs[0].message.strip(), (
        "the client received a message-less StreamError")
    assert "ReadTimeout" in errs[0].message, errs[0].message
    assert errs[0].kind == "timeout", errs[0].kind


def test_fmt_secs_never_renders_a_duration_as_zero():
    """``f"{0.5:.0f}"`` is ``"0"``, which named no duration at all."""
    assert _fmt_secs(30.0) == "30"
    assert _fmt_secs(0.5) == "0.5"
    assert _fmt_secs(0.4) == "0.4"
    # Sub-10s keeps one decimal: "0" and "2" are both wrong for a real budget.
    assert _fmt_secs(2.0) == "2.0"
    for v in (0.1, 0.4, 0.5, 2.0, 9.9):
        assert _fmt_secs(v) != "0", v


# ---------------------------------------------------------------------------
# 4. The httpx read deadline must not pre-empt the gateway's watchdog.
# ---------------------------------------------------------------------------

def test_stream_read_timeout_lifts_only_the_read_deadline():
    """A scalar timeout makes httpx apply the same value to *read*.

    That pre-empted the gateway's own no-progress watchdog: a stream that
    legitimately went quiet for longer than ``router_settings.timeout`` was
    killed by httpx, and — because a read timeout carries no message — reached
    the client as a blank error with no failover (AUDIT #356).
    """
    dep = Deployment.__new__(Deployment)
    dep.timeout = 5.0
    dep.provider = type("P", (), {"timeout_s": 5.0})()

    armed = stream_read_timeout(dep, watchdog_s=30.0)
    assert armed.read is None, (
        "httpx's read deadline must not pre-empt the gateway's watchdog")
    # connect/write/pool keep the configured value — that is what a scalar was
    # actually good for.
    assert armed.connect == 5.0
    assert armed.write == 5.0
    assert armed.pool == 5.0


def test_stream_read_timeout_keeps_the_deadline_when_unarmed():
    """A non-positive watchdog means no authority, so do not leave the body
    unbounded — keep the operator's configured deadline."""
    dep = Deployment.__new__(Deployment)
    dep.timeout = 5.0
    dep.provider = type("P", (), {"timeout_s": 5.0})()

    assert stream_read_timeout(dep, watchdog_s=0.0) == 5.0


def test_stream_read_timeout_falls_back_to_the_provider_timeout():
    dep = Deployment.__new__(Deployment)
    dep.timeout = None
    dep.provider = type("P", (), {"timeout_s": 7.0})()

    to = stream_read_timeout(dep, watchdog_s=30.0)
    assert to.read is None
    assert to.connect == 7.0


# ---------------------------------------------------------------------------
# 5. The keep-alive must keep working while the driver waits (AUDIT #177).
# ---------------------------------------------------------------------------

@respx.mock
async def test_the_keep_alive_fires_while_the_upstream_has_sent_nothing(
        tmp_path):
    """``ready`` now waits for the upstream's first byte, so the consumer may
    not exist yet for a long time.

    Pre-fix the gateway told the client it was waiting on a thinking phase and
    then said nothing for the whole of it, which is exactly the idle-proxy reap
    the ping exists to prevent (AUDIT #177). The driver wait is interleaved with
    the pings for that reason, and this drives the messages surface where the
    ping is emitted.
    """
    from wiwi.server.app import create_app

    async def _slow_anthropic() -> None:
        # Several ping intervals of total silence before anything arrives.
        await asyncio.sleep(0.9)
        yield (b'event: message_start\ndata: {"type":"message_start","message":'
               b'{"model":"claude-x","usage":{"input_tokens":5}}}\n\n')
        yield (b'event: content_block_start\ndata: {"type":"content_block_start",'
               b'"index":0,"content_block":{"type":"text","text":""}}\n\n')
        yield (b'event: content_block_delta\ndata: {"type":"content_block_delta",'
               b'"index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n')
        yield (b'event: message_delta\ndata: {"type":"message_delta",'
               b'"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
               )
        yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    cfg = WiwiConfig(
        providers=[ProviderDef(
            name="ant", provider="anthropic",
            base_url="https://ant.example/v1",
            keys=[KeyDef(label="k", key="sk-ant-test")])],
        model_list=[ModelEntry(
            model_name="claude-x",
            wiwi_params=DeploymentParams(provider="ant", model="claude-x"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db"),
        router_settings=RouterSettings(
            stream_ping_interval_s=0.15,
            # Both budgets comfortably outlast the 0.9 s silence, so only the
            # keep-alive can be responsible for what the client sees.
            stream_idle_timeout_s=30.0, stream_first_chunk_timeout_s=60.0),
    )
    respx.post("https://ant.example/v1/messages").mock(
        side_effect=lambda r: httpx.Response(
            200, content=_slow_anthropic(),
            headers={"content-type": "text/event-stream"}))

    app = create_app(cfg)
    from asgi_lifespan import LifespanManager
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as c:
            r = await asyncio.wait_for(c.post(
                "/v1/messages", headers=AUTH, json={
                    "model": "claude-x", "max_tokens": 64, "stream": True,
                    "messages": [{"role": "user", "content": "think"}]}), 20)

    body = r.text
    assert r.status_code == 200, body
    assert 'event: ping\ndata: {"type": "ping"}\n\n' in body, (
        "the keep-alive did not fire while the upstream was still silent; a "
        f"client left in silence that long is reaped by an idle proxy\n{body}")
    # And the turn still completes normally around it.
    assert "message_stop" in body, body
    assert '"text":"hi"' in body, body


# ---------------------------------------------------------------------------
# 6. Non-streaming callers of a force_stream provider get the same treatment.
# ---------------------------------------------------------------------------

class _ScriptedForceStreamAdapter:
    """Minimal ``force_stream`` adapter (Cline/OpenCode/WorkBuddy shape).

    ``force_stream = True`` is what makes ``_call_once`` route a non-streaming
    caller through ``_complete_via_stream``, so the arm under test is reached
    without a real OAuth provider in the way.
    """

    force_stream = True

    def __init__(self) -> None:
        self.text: list[str] = []

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        return base_url.rstrip("/") + "/chat/completions"

    def encode_request(self, ir_req, model_id, params):
        return {}

    def headers(self, key) -> dict[str, str]:
        return {}

    def decode_stream_event(self, event: str, data: str):
        if data == "[DONE]":
            return []
        chunk = json.loads(data)
        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
        out = []
        if "content" in delta:
            self.text.append(delta["content"])
            out.append(dl.TextDelta(delta["content"]))
        if chunk.get("usage"):
            u = chunk["usage"]
            out.append(dl.UsageFinal(prompt=u.get("prompt_tokens", 0),
                                    output=u.get("completion_tokens", 0)))
        if (chunk.get("choices") or [{}])[0].get("finish_reason"):
            out.append(dl.Finish("stop"))
        return out


def _force_dep() -> Deployment:
    acct = ProviderAccount(name="a", provider_type="openai",
                           base_url="https://a.example/v1")
    return Deployment(group="m", provider=acct, model_id="m")


@respx.mock
async def test_complete_via_stream_tolerates_a_long_pre_content_phase(tmp_path):
    """The non-streaming arm ran the same single-budget watchdog.

    ``_complete_via_stream`` is how a non-streaming caller reaches a
    streaming-only upstream (Cline, OpenCode, WorkBuddy), and it had the same
    30 s ceiling on the pre-content phase — so a reasoning turn through one of
    those was cut even though the caller never asked for a stream.
    """
    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=30.0, idle_s=0.5)),
                 CostEngine())
    adapter = _ScriptedForceStreamAdapter()
    try:
        async def _slow() -> None:
            # 6x the inter-chunk budget: past the watchdog that used to fire,
            # inside the pre-content budget.
            await asyncio.sleep(3.0)
            yield _chunk("late ")
            yield _chunk("answer")
            yield _DONE

        respx.post("https://a.example/v1/chat/completions").mock(
            side_effect=lambda r: httpx.Response(
                200, content=_slow(),
                headers={"content-type": "text/event-stream"}))
        turn = await asyncio.wait_for(gw._complete_via_stream(
            _force_dep(), ProviderKeyRef(label="ka", secret="sk-a"),
            _context(), adapter), timeout=20)
    finally:
        await gw.aclose()

    assert turn.text == "late answer", (
        f"a long pre-content phase cut a healthy non-streaming request: "
        f"{turn.text!r}")
    assert turn.stop_reason == "stop", turn.stop_reason


@respx.mock
async def test_complete_via_stream_reports_a_read_timeout_by_name(tmp_path):
    """The same blank-message defect, on the non-streaming arm.

    Both budgets are set far above the injected failure so only the transport
    fault can fire.
    """
    gw = Gateway(Router(_cfg(tmp_path, first_chunk_s=300.0, idle_s=300.0)),
                 CostEngine())
    try:
        async def _content_then_read_timeout() -> None:
            yield _chunk("hi")
            raise httpx.ReadTimeout("")

        respx.post("https://a.example/v1/chat/completions").mock(
            side_effect=lambda r: httpx.Response(
                200, content=_content_then_read_timeout(),
                headers={"content-type": "text/event-stream"}))
        with pytest.raises(WiwiError) as ei:
            await asyncio.wait_for(gw._complete_via_stream(
                _force_dep(), ProviderKeyRef(label="ka", secret="sk-a"),
                _context(), _ScriptedForceStreamAdapter()), timeout=10)
    finally:
        await gw.aclose()

    assert ei.value.message.strip(), (
        "the caller received a message-less error")
    assert "ReadTimeout" in ei.value.message, ei.value.message
    assert ei.value.retryable, "a transport fault must stay retryable"
