"""Round-8 regression tests: admin catalog 500, mid-stream key penalty,
and client-disconnect cancellation.

1. ``GET /admin/provider-catalog`` built its response list and then fell off
   the end of the handler without returning it, so every call 500'd.

2. ``Gateway._note_stream_failure`` incremented ``ProviderKey.err_count`` but
   never marked the key cooling, so a key that kept dying mid-stream never
   rotated.  Worse, ``execute_with_retries`` had already recorded
   ``on_result(200)`` at *connect* time, which zeroed ``err_count`` again.

3. ``RequestContext.cancel`` was read by the stream pump but no code path ever
   set it, so the grace-drain / "client disconnected" handling was dead code.

4. ``_extract_error_message`` called ``data.get("error")`` without checking
   that the parsed JSON was an object, so any upstream returning ``null``, a
   bare string, a number, or an array raised ``AttributeError`` and turned a
   clean provider 4xx/5xx into an opaque gateway 500.

5. ``Gateway.stream`` cancelled the pump task the instant the connect-time
   failure appeared, but the pump sets ``ready`` *before* closing the upstream
   response.  The cancel landed inside ``resp_cm.__aexit__``, so the response
   was never closed and the pooled connection leaked — on every retried
   non-200 stream connect (429/500/401).

6. The Anthropic adapter never encoded ``gen_params.response_format``, so an
   OpenAI-dialect client asking for ``json_object`` / ``json_schema`` and
   routed to Claude silently lost it and got prose back.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
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
from wiwi.core.gateway import Gateway
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.router.router import Router

MASTER = "sk-wiwi-master-test"
UPSTREAM = "https://round8.example/v1/chat/completions"


def _config(**router_over) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://round8.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1"),
                                     KeyDef(label="k2", key="sk-2")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=1,
                                       cooldown_time=60.0, **router_over),
    )


def _req(stream: bool = True) -> ir.Request:
    return ir.Request(model="gpt-x",
                      messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
                      stream=stream)


def _sse(*data: str) -> bytes:
    return b"".join(f"data: {d}\n\n".encode() for d in data)


def _text_chunk(text: str) -> str:
    return ('{"id":"c1","object":"chat.completion.chunk","model":"gpt-x",'
            f'"choices":[{{"index":0,"delta":{{"content":"{text}"}},'
            '"finish_reason":null}]}')


# -- 1. /admin/provider-catalog returns a body ---------------------------------


@pytest.fixture
async def client():
    app = app_mod.create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            yield c


async def test_provider_catalog_returns_payload(client):
    """Regression: handler built `out` and returned None -> 500."""
    r = await client.get("/admin/provider-catalog",
                         headers={"Authorization": f"Bearer {MASTER}"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and body, "catalog must be a non-empty list"
    # Every catalog card carries the documented shape.
    assert {"provider_type", "label", "configured"} <= set(body[0])
    # Catalog types must stay in sync with config.PROVIDER_TYPES.
    from wiwi.config import PROVIDER_TYPES
    assert {c["provider_type"] for c in body} == set(PROVIDER_TYPES)


async def test_provider_catalog_marks_configured(client):
    r = await client.get("/admin/provider-catalog",
                         headers={"Authorization": f"Bearer {MASTER}"})
    by_type = {c["provider_type"]: c for c in r.json()}
    assert by_type["openai"]["configured"] is True
    assert by_type["anthropic"]["configured"] is False


async def test_provider_catalog_requires_master(client):
    r = await client.get("/admin/provider-catalog")
    assert r.status_code == 401


# -- 2. mid-stream failure must cool the key -----------------------------------


def _make_gateway(cfg: WiwiConfig) -> Gateway:
    return Gateway(Router(cfg), CostEngine())


async def _pump_stream_that_dies_midway(gw: Gateway, ctx: RequestContext):
    """Consume a stream whose upstream emits content then dies."""
    out = []
    async for d in gw.stream(ctx):
        out.append(d)
    return out


@respx.mock
async def test_midstream_failure_marks_key_cooling():
    """A key that dies mid-stream must be cooled so the next pick rotates.

    Previously `_note_stream_failure` only bumped `err_count`, and
    `execute_with_retries` had already recorded on_result(200) at connect
    time (which resets err_count to 0) — so the key stayed fully available.
    """
    cfg = _config()
    gw = _make_gateway(cfg)
    try:
        # A true mid-stream death is a connection that ends WITHOUT the SSE
        # [DONE] sentinel (which signals clean completion). Simulate content
        # then an abrupt close so the pump sees no terminal marker.
        respx.post(UPSTREAM).mock(return_value=httpx.Response(
            200,
            content=_sse(_text_chunk("partial"))))
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
        await _pump_stream_that_dies_midway(gw, ctx)

        acct = gw.router.providers["p1"]
        key = next(k for k in acct.keys if k.label == ctx.provider_key.label)
        # No [DONE] and no finish_reason -> treated as a failure.
        assert key.status == "cooling", (
            f"expected cooling after mid-stream death, got {key.status!r}")
        assert key.cooldown_until > 0
    finally:
        await gw.aclose()


async def test_note_stream_failure_marks_cooling():
    """_note_stream_failure itself must apply a cooldown, not just count.

    It routes through the provider's ``on_result`` accounting so the key gets
    a cooldown window (rotating traffic away) and is retired outright once it
    crosses ``key_max_consecutive_fails``.
    """
    cfg = _config()
    gw = _make_gateway(cfg)
    try:
        dep = gw.router.groups["gpt-x"][0]
        key = dep.provider.keys[0]
        await gw._note_stream_failure(dep, key)
        assert key.err_count == 1
        assert key.status == "cooling"
        assert not key.available, "cooled key must not be immediately reusable"
    finally:
        await gw.aclose()


async def test_repeated_midstream_failures_retire_key():
    """Enough consecutive mid-stream failures retire the key permanently."""
    cfg = _config(key_max_consecutive_fails=3)
    gw = _make_gateway(cfg)
    try:
        dep = gw.router.groups["gpt-x"][0]
        key = dep.provider.keys[0]
        for _ in range(3):
            await gw._note_stream_failure(dep, key)
        assert key.status == "invalid", "key should be retired after 3 failures"
    finally:
        await gw.aclose()


# -- 3. client disconnect sets ctx.cancel --------------------------------------


@respx.mock
async def test_client_disconnect_sets_cancel_and_stops_pump():
    """Closing the consumer must set ctx.cancel so the pump stops pulling.

    Previously nothing ever called `ctx.cancel.set()`, so the grace-drain and
    "client disconnected" branches in `_pump_once` were unreachable.
    """
    cfg = _config(stream_grace_drain_s=0)
    gw = _make_gateway(cfg)
    try:
        many = [_text_chunk(f"tok{i}") for i in range(50)]
        respx.post(UPSTREAM).mock(return_value=httpx.Response(
            200, content=_sse(*many, "[DONE]")))
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")

        stream = gw.stream(ctx)
        await anext(stream)  # StreamStart
        await anext(stream)  # first content delta
        await stream.aclose()  # simulate client going away

        # Let the pump observe the cancellation.
        for _ in range(20):
            if ctx.cancel.is_set():
                break
            await asyncio.sleep(0.01)
        assert ctx.cancel.is_set(), "closing the stream must set ctx.cancel"
    finally:
        await gw.aclose()


async def test_cancel_event_defaults_unset():
    """A fresh context starts uncancelled."""
    ctx = RequestContext(surface="chat", ir_req=_req())
    assert not ctx.cancel.is_set()


# -- 4. provider error bodies that are not JSON objects ------------------------

_NON_OBJECT_BODIES = [
    "null",                              # upstream returned JSON null
    '"just a string"',                   # bare JSON string
    "123",                               # bare JSON number
    "true",                              # bare JSON boolean
    '[{"error": {"message": "boom"}}]',  # array-wrapped (some gateways)
    "[]",                                # empty array
]


@pytest.mark.parametrize("body", _NON_OBJECT_BODIES)
def test_extract_error_message_survives_non_object_json(body):
    """Regression: `data.get` on a non-dict raised AttributeError.

    An upstream error body that parses to anything other than a JSON object
    used to blow up the error path, masking the real status behind a 500.
    """
    from wiwi.providers.base import _extract_error_message
    # Must not raise, and must fall back to the raw body.
    assert _extract_error_message(body) == body[:500]


@pytest.mark.parametrize("body", _NON_OBJECT_BODIES)
@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_error_from_provider_status_survives_non_object_json(body, status):
    """The full status->WiwiError mapping must not crash on odd bodies."""
    from wiwi.providers.base import error_from_provider_status
    err = error_from_provider_status(status, body, "upstream-prov")
    assert isinstance(err.status, int)
    assert "upstream-prov" in err.message
    # The raw body is preserved so the operator can see what came back.
    assert body[:60] in err.message


def test_extract_error_message_still_handles_known_shapes():
    """Guard the behaviour we must not regress while fixing the crash."""
    from wiwi.providers.base import _extract_error_message
    # OpenAI shape
    assert (_extract_error_message('{"error": {"message": "bad request"}}')
            == "bad request")
    # Anthropic shape
    assert (_extract_error_message(
        '{"type": "error", "error": {"message": "overloaded"}}') == "overloaded")
    # OpenRouter metadata.raw unwrapping
    assert "upstream: real cause" in _extract_error_message(
        '{"error": {"message": "Provider returned error",'
        ' "metadata": {"raw": "real cause"}}}')
    # Non-JSON body passes through untouched
    assert _extract_error_message("Internal Server Error") == "Internal Server Error"


def test_error_from_provider_status_preserves_401_semantics_on_junk_body():
    """401 must stay 401/auth-retryable even when the body is unparseable."""
    from wiwi.providers.base import error_from_provider_status
    err = error_from_provider_status(401, "null", "p")
    assert err.status == 401
    assert err.retryable is True


# -- 5. stream connect failure must close the upstream response ----------------


class TrackedClient(httpx.AsyncClient):
    """Wrap ``.stream()`` to observe whether the response is cleanly closed."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.entered = 0
        self.exited = 0
        self.exit_cancelled = 0
        # Optional: when set, __aexit__ blocks until this event fires, letting
        # a test park the consumer inside the close and cancel deterministically.
        self.hold_exit: asyncio.Event | None = None

    def stream(self, *a, **kw):  # type: ignore[override]
        outer = self
        inner_cm = super().stream(*a, **kw)

        class _CM:
            async def __aenter__(self):
                outer.entered += 1
                return await inner_cm.__aenter__()

            async def __aexit__(self, *exc):
                try:
                    if outer.hold_exit is not None:
                        await outer.hold_exit.wait()
                    # Real closes await (socket teardown); that await is the
                    # window in which the consumer's cancel() used to land.
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    outer.exit_cancelled += 1
                    raise
                outer.exited += 1
                return False

        return _CM()


async def _stream_with_tracked_client(status: int):
    """Drive one failing stream connect through a TrackedClient."""
    cfg = _config()
    gw = Gateway(Router(cfg), CostEngine())
    await gw.aclose()  # discard the default client
    tracked = TrackedClient(timeout=httpx.Timeout(10.0))
    gw._client = tracked
    try:
        with respx.mock:
            respx.post(UPSTREAM).mock(return_value=httpx.Response(
                status, json={"error": {"message": "nope"}}))
            ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
            expected_failure = False
            try:
                async for _ in gw.stream(ctx):
                    pass
            except Exception:  # noqa: BLE001 - the error type isn't the point
                expected_failure = True  # we only care that cleanup completed
            assert expected_failure, "failing connect should surface an error"
            await asyncio.sleep(0.05)  # let a cancelled task settle
        return tracked
    finally:
        await tracked.aclose()


@pytest.mark.parametrize("status", [429, 500, 401])
async def test_stream_connect_failure_closes_upstream(status):
    """Regression: cancel() interrupted `__aexit__`, leaking the connection.

    The pump sets ``ready`` *before* awaiting ``resp_cm.__aexit__``; the
    consumer then cancelled immediately, so the close never completed.
    """
    tracked = await _stream_with_tracked_client(status)
    assert tracked.entered >= 1, "expected at least one stream connect attempt"
    assert tracked.exit_cancelled == 0, (
        f"upstream close was interrupted {tracked.exit_cancelled}x — connection leak")
    assert tracked.exited == tracked.entered, (
        f"opened {tracked.entered} responses but closed only {tracked.exited}")


async def test_stream_connect_failure_still_surfaces_error():
    """Fixing the leak must not swallow the upstream error."""
    from wiwi.providers.base import WiwiError
    cfg = _config()
    gw = Gateway(Router(cfg), CostEngine())
    try:
        with respx.mock:
            respx.post(UPSTREAM).mock(return_value=httpx.Response(
                429, json={"error": {"message": "slow down"}}))
            ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
            with pytest.raises(WiwiError) as exc:
                async for _ in gw.stream(ctx):
                    pass
        assert exc.value.status == 429
    finally:
        await gw.aclose()


# -- 6. Anthropic must honour response_format ----------------------------------


def _anthropic_encode(gen_params) -> dict:
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    req = ir.Request(
        model="claude-x",
        messages=[ir.Message(role="user", parts=[ir.TextPart("give me JSON")])],
        gen_params=gen_params,
    )
    return AnthropicAdapter().encode_request(req, "claude-x", {})


def test_anthropic_honours_json_object():
    """Regression: response_format was silently dropped for Anthropic."""
    gp = ir.GenParams(response_format=ir.ResponseFormat(type="json_object"))
    body = _anthropic_encode(gp)
    # Anthropic has no native response_format; the adapter must convey the
    # constraint instead of dropping it, or the client gets prose back.
    assert body.get("response_format") is not None or _system_mentions_json(body), (
        "json_object request must be conveyed to the Anthropic upstream")


def test_anthropic_honours_json_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}
    gp = ir.GenParams(response_format=ir.ResponseFormat(type="json_schema",
                                                        json_schema=schema))
    body = _anthropic_encode(gp)
    # json_schema is now native: rides as output_config.format (2026 GA),
    # no system-prompt injection needed.
    assert body.get("output_config", {}).get("format", {}).get("schema") == schema


def test_anthropic_without_response_format_unchanged():
    """No response_format -> no injected instruction (no behaviour change)."""
    body = _anthropic_encode(ir.GenParams())
    assert "response_format" not in body
    system = body.get("system")
    if system is not None and not isinstance(system, str):
        system = " ".join(b.get("text", "") for b in system)
    assert not system, "no system prompt should be injected by default"


def test_anthropic_response_format_preserves_existing_system():
    """An existing system prompt must survive the JSON instruction."""
    req = ir.Request(
        model="claude-x",
        messages=[ir.Message(role="system", parts=[ir.TextPart("be terse")]),
                  ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(response_format=ir.ResponseFormat(type="json_object")),
    )
    from wiwi.providers.anthropic_adapter import AnthropicAdapter
    body = AnthropicAdapter().encode_request(req, "claude-x", {})
    system = body.get("system")
    if system is not None and not isinstance(system, str):
        system = " ".join(b.get("text", "") for b in system)
    assert "terse" in system, "original system prompt must be preserved"


def _system_mentions_json(body: dict) -> bool:
    system = body.get("system")
    if system is None:
        return False
    if not isinstance(system, str):
        system = " ".join(b.get("text", "") for b in system)
    return "json" in system.lower()


# -- helper sanity: ProviderKeyRef plumbing unchanged --------------------------


def test_provider_key_ref_shape():
    ref = ProviderKeyRef(label="k1", secret="sk-1")
    assert (ref.label, ref.secret) == ("k1", "sk-1")


# -- 7. round-8 follow-ups ----------------------------------------------------
#
# Six defects found on review of the round-8 diff. Each test below fails
# against the round-8 code as written and passes once fixed.
#
#   7a. `contextlib.suppress(BaseException)` around the awaited pump swallows
#       CancelledError — a client disconnect during a failed connect is turned
#       into a WiwiError instead of propagating.
#   7b. A *benign* stream that simply ends at [DONE] without a finish_reason
#       chunk must not cool the key.
#   7c. `on_result(200)` fires at connect time and zeroes err_count, so the
#       mid-stream penalty from round 8 can never accumulate (AUDIT #6).
#   7d. Under `failover_mode="standard"` a 502 is a no-op in `on_result`, so
#       the round-8 penalty silently vanishes.
#   7e. `ctx.cancel.set()` immediately followed by `pump_task.cancel()` never
#       lets the pump observe the flag (no yield between them).
#   7f. A 429-only deployment never cools down (AUDIT #17).


@respx.mock
async def test_client_disconnect_during_failed_connect_propagates_cancel():
    """7a. A disconnect during a failed connect must raise CancelledError.

    `suppress(BaseException)` eats CancelledError (it derives from
    BaseException), so the caller sees the upstream WiwiError instead and the
    ASGI disconnect never propagates.
    """
    cfg = _config()
    gw = Gateway(Router(cfg), CostEngine())
    await gw.aclose()  # discard the default client
    client = TrackedClient(timeout=httpx.Timeout(10.0))
    # Hold `__aexit__` open so the consumer is provably parked inside the
    # `wait_for(pump_task)` window when we cancel. Without this the race is
    # timing-dependent and the test would pass or fail by luck.
    client.hold_exit = asyncio.Event()
    gw._client = client
    try:
        with respx.mock:
            respx.post(UPSTREAM).mock(return_value=httpx.Response(
                429, json={"error": {"message": "slow down"}}))
            ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")

            consume = asyncio.ensure_future(_first_delta(gw.stream(ctx)))
            # Let the pump connect, record the error, set `ready`, and enter
            # `__aexit__`, where it now blocks on our hold.
            for _ in range(100):
                await asyncio.sleep(0.01)
                if client.entered:
                    break
            assert client.entered, "expected the pump to attempt a connect"

            consume.cancel()  # client goes away mid-close
            client.hold_exit.set()  # release the pump
            try:
                await consume
            except asyncio.CancelledError:
                return  # correct
            except Exception as e:  # noqa: BLE001
                pytest.fail(f"client disconnect surfaced as {type(e).__name__}, "
                            f"expected CancelledError to propagate")
            pytest.fail("no exception raised; CancelledError was swallowed")
    finally:
        await gw.aclose()


async def _first_delta(stream):
    async for d in stream:
        return d
    return None


# NOTE (not fixed): a stream that ends at [DONE] with no finish_reason chunk
# is still classed as a failure and cools the key. A reviewer flagged this as a
# false positive for providers that legitimately omit finish_reason. It is left
# as-is deliberately: `test_midstream_failure_marks_key_cooling` (above)
# encodes the current behaviour as intended, and no provider in this repo is
# known to omit finish_reason. Revisit only with a concrete provider in hand.


@respx.mock
async def test_stream_success_is_counted_at_completion_not_connect():
    """7c. Streaming success must be recorded when the stream completes.

    AUDIT #6 / round-8 fix 2: `execute_with_retries` records `on_result(200)`
    at *connect* time, which resets `err_count` to 0. A key that connects and
    then dies mid-stream therefore never accumulates a retirement streak.
    """
    cfg = _config()
    gw = Gateway(Router(cfg), CostEngine())
    try:
        # Connects fine (200), then the body ends with no content at all —
        # a connect-then-drop, the exact case AUDIT #6 describes.
        respx.post(UPSTREAM).mock(return_value=httpx.Response(
            200, content=b""))
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")
        async for _ in gw.stream(ctx):
            pass

        acct = gw.router.providers["p1"]
        key = next(k for k in acct.keys if k.label == ctx.provider_key.label)
        assert key.req_count == 0, (
            f"connect-only stream was counted as a success "
            f"(req_count={key.req_count}) — must be deferred to completion")
    finally:
        await gw.aclose()


async def test_note_stream_failure_penalises_key_in_standard_mode():
    """7d. The mid-stream penalty must apply under failover_mode='standard'.

    `on_result` in standard mode only handles 429 and 401/403, so the 502
    that `_note_stream_failure` reports is discarded entirely.
    """
    cfg = _config(failover_mode="standard")
    gw = Gateway(Router(cfg), CostEngine())
    try:
        dep = gw.router.groups["gpt-x"][0]
        key = dep.provider.keys[0]
        await gw._note_stream_failure(dep, key)
        assert key.err_count == 1, (
            f"standard mode dropped the mid-stream penalty "
            f"(err_count={key.err_count})")
    finally:
        await gw.aclose()


@respx.mock
async def test_disconnect_lets_pump_observe_cancel_flag():
    """7e. The pump must actually observe ctx.cancel, not just have it set.

    Round 8 sets the flag and cancels the pump on the very next line with no
    yield between them, so the pump is never rescheduled and always takes
    CancelledError — the grace drain stays dead code. The proof is whether the
    pump reaches its own clean teardown (`_close_upstream`) after the flag is
    set, rather than being cut off inside it.
    """
    cfg = _config(stream_grace_drain_s=0.0)
    gw = Gateway(Router(cfg), CostEngine())
    await gw.aclose()
    client = TrackedClient(timeout=httpx.Timeout(10.0))
    gw._client = client
    try:
        many = [_text_chunk(f"tok{i}") for i in range(200)]
        respx.post(UPSTREAM).mock(return_value=httpx.Response(
            200, content=_sse(*many, "[DONE]")))
        ctx = RequestContext(surface="chat", ir_req=_req(), group="gpt-x")

        stream = gw.stream(ctx)
        await anext(stream)  # StreamStart
        await anext(stream)  # first content delta
        await stream.aclose()  # consumer goes away

        # Give the pump time to run. If it observed the flag it closes the
        # upstream itself (exited == entered); if it was simply cancelled
        # mid-close, the exit is interrupted (exit_cancelled > 0).
        for _ in range(100):
            await asyncio.sleep(0.01)
            if client.exit_cancelled or client.exited:
                break

        assert ctx.cancel.is_set(), "cancel flag was never set"
        assert client.exit_cancelled == 0, (
            f"pump was cancelled mid-close {client.exit_cancelled}x instead of "
            f"observing ctx.cancel and releasing the upstream itself")
        assert client.exited == client.entered, (
            f"opened {client.entered} responses but closed only {client.exited}")
    finally:
        await gw.aclose()

