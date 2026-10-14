"""Round 139: the realtime surface must not be a hole in the gateway's spine.

Three defects, all on ``WS /v1/realtime``, all invisible at default log levels:

1. **The pre-flight was re-implemented, not shared.** The route called
   ``state.auth.authenticate`` directly and hand-rolled disabled/expires,
   ``over_budget`` and the model allowlist — but not the
   ``unpriced_model_policy == "reject"`` gate from AUDIT #323, which lived only
   in the HTTP ``authenticate()`` closure. So with the policy set to reject, an
   unpriced model 503'd on ``/v1/chat/completions`` and was admitted over the
   socket: ``CostEngine`` priced the session at $0, ``apply_spend_trueup``
   no-ops on a non-positive amount, and the budget cap never tripped. The one
   control that stops unpriced spend was a no-op on the easiest surface to
   bypass. Key-level RPM/TPM was unenforced there too, while every HTTP
   surface was throttled.

2. **A session with no usage produced no row at all.** ``_bill_realtime_session``
   returned early on ``prompt_tokens <= 0 and completion_tokens <= 0`` —
   *before* ``log_request`` — and the call site additionally gated on
   ``if usage is not None``, so a relay that never saw a usage frame was never
   entered. A handshake timeout, an upstream 401/5xx, or ``max_session_s``
   firing mid-turn therefore left no charge, no request-log row, no status and
   no stop reason: a key whose only traffic is realtime shows a permanently
   flat ``spend_to_date`` and an empty Request Logs tab, with nothing to
   reconcile against the upstream bill. Every HTTP surface logs unconditionally.

3. **One binary frame killed the relay.** ``websockets.connect`` was opened
   without ``decode=True``, so ``recv()`` yields ``str | bytes``. The upstream
   direction ran ``'"usage"' in frame`` (TypeError on bytes) and
   ``client_ws.send_text(frame)`` (str-only). The TypeError was swallowed by
   the blanket ``except``, the ``finally`` closed the *client* socket, and the
   teardown was logged at ``debug`` — so a single binary audio frame dropped a
   live session with no trace. The client→upstream direction was already clean.

The upstream is a real WebSocket server on its own thread/event loop, so the
relay is exercised over an actual socket rather than a stub that would call the
function under test and prove nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest
import websockets
from starlette.testclient import TestClient

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RealtimeSettings,
    RouterSettings,
    WiwiConfig,
)
from wiwi.server import app as app_mod

MASTER = "sk-wiwi-master-test"
MODEL_ID = "gpt-4o-realtime-preview"  # deliberately unpriced


# --- a real in-process websocket upstream ------------------------------------


class FakeRealtimeServer:
    """Echoes text frames, plus an optional scripted push and per-echo followups.

    ``script`` entries are ``(delay_seconds, payload)`` sent on its own task as
    soon as the connection opens, so a test can drive the upstream without the
    client saying anything — that is what a turn that starts itself looks like.
    ``after_echo`` is sent after every echoed client frame.

    Runs on its **own** thread and event loop: Starlette's ``TestClient`` drives
    the app on a separate portal loop, so a server sharing the test's loop could
    never accept a connection while the route was dialling one.
    """

    def __init__(self) -> None:
        self.received: list[str] = []
        self.after_echo: list = []
        self.script: list[tuple[float, object]] = []
        self.close_after_echo: int | None = None
        self.connections = 0
        self.url = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    async def _pump(self, ws) -> None:
        for delay, payload in self.script:
            await asyncio.sleep(delay)
            await ws.send(payload)

    async def _handler(self, ws) -> None:
        self.connections += 1
        task = asyncio.ensure_future(self._pump(ws))
        echoed = 0
        try:
            async for raw in ws:
                self.received.append(raw)
                await ws.send(raw)
                for extra in self.after_echo:
                    await ws.send(extra)
                echoed += 1
                if self.close_after_echo is not None and echoed >= self.close_after_echo:
                    # Ending the session from the UPSTREAM side is the only way
                    # to make the route run to completion under TestClient:
                    # leaving the websocket context calls ``cs.cancel()`` on the
                    # app task, which kills the route mid-teardown.
                    await ws.close()
                    return
        except websockets.ConnectionClosed:
            pass
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def start(self) -> str:
        self._loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
            self._ready.set()  # after the listener is bound and url is known
            self._loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("fake realtime upstream did not start")
        return self.url

    async def _serve(self) -> None:
        server = await websockets.serve(
            self._handler, "127.0.0.1", 0, compression=None)
        self.url = (f"ws://127.0.0.1:"
                    f"{next(iter(server.sockets)).getsockname()[1]}")
        self._server = server

    def stop(self) -> None:
        """Stop the loop and close it on its own thread.

        ``loop.close()`` from another thread raises "Cannot close a running event
        loop", so the close must be scheduled *onto* the loop before it stops.
        """
        if self._loop is None:
            return
        closed = threading.Event()

        def shutdown() -> None:
            self._loop.stop()
            self._loop.close()
            closed.set()

        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(shutdown)
        closed.wait(timeout=5)


@pytest.fixture
def upstream():
    server = FakeRealtimeServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def _reset_rt_counters():
    """The realtime counters are process-lifetime; zero them per test.

    Tolerant of the dict not existing yet so that the other regressions in this
    file report on their own behaviour rather than on a missing counter.
    """
    counters = getattr(app_mod, "_RT_COUNTERS", None)
    if counters is not None:
        counters["usage_missing"] = 0
    yield
    if counters is not None:
        counters["usage_missing"] = 0


# --- config / client helpers -------------------------------------------------


def _config(policy: str = "warn", base_url: str = "http://127.0.0.1:1/v1",
            **realtime) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai-compatible",
                               base_url=base_url,
                               keys=[KeyDef(label="a",
                                            key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model=MODEL_ID))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:",
            unpriced_model_policy=policy),
        router_settings=RouterSettings(),
        realtime=RealtimeSettings(enabled=True, **realtime),
    )


def _refusal_status(app, key: str = MASTER,
                    path: str = "/v1/realtime?model=m") -> int | None:
    """Connect expecting a refusal; return the HTTP status the client saw.

    A close issued before ``accept`` is reported to the client as a *generic*
    handshake rejection (uvicorn answers 403 for any of them), which is why the
    route writes a real ``websocket.http.response.start`` instead. Reading the
    status is what proves a 503 is distinguishable from a 404.
    """
    from starlette.testclient import WebSocketDenialResponse

    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                    path, headers={"authorization": f"Bearer {key}"}):
                return None
        except WebSocketDenialResponse as exc:
            return exc.status_code


def _refusal_status_on(client, key: str = MASTER,
                       path: str = "/v1/realtime?model=m") -> int | None:
    """Same, against an already-running TestClient (keeps its app state)."""
    from starlette.testclient import WebSocketDenialResponse

    try:
        with client.websocket_connect(
                path, headers={"authorization": f"Bearer {key}"}):
            return None
    except WebSocketDenialResponse as exc:
        return exc.status_code


def _mint_key(client, **body) -> str:
    """Create a virtual key through the admin API and return its plaintext."""
    payload = {"name": "rt"}
    payload.update(body)
    resp = client.post("/admin/keys/generate", json=payload,
                       headers={"authorization": f"Bearer {MASTER}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["key"]


def _run_session(client, key: str = MASTER, model: str = "m"):
    """Open a realtime session, drive one turn, and let it end cleanly.

    The upstream is told to close after the first echo (see
    ``close_after_echo``), which ends the relay. The test then stays *inside*
    the websocket context while the route finishes: leaving it calls
    ``cs.cancel()`` on the app task, which would kill the route before — or
    during — its billing teardown and make every post-session assertion below
    meaningless.
    """
    from starlette.testclient import WebSocketDisconnect

    ws = client.websocket_connect(f"/v1/realtime?model={model}",
                                  headers={"authorization": f"Bearer {key}"})
    with ws as session:
        session.send_text('{"type":"response.create"}')
        assert session.receive_text() == '{"type":"response.create"}'
        while True:
            try:
                session.receive_text()
            except WebSocketDisconnect:
                break
        # The route writes its close frame in a ``finally`` that runs BEFORE
        # billing, so let the rest of the teardown land.
        time.sleep(1.0)


def _realtime_rows(client) -> list[dict]:
    """Request-log rows for group ``m``, newest first (bounded wait)."""
    from starlette.testclient import WebSocketDisconnect  # noqa: F401

    deadline = time.time() + 5.0
    rows: list[dict] = []
    while time.time() < deadline:
        resp = client.get("/admin/logs/requests?limit=200",
                          headers={"authorization": f"Bearer {MASTER}"})
        assert resp.status_code == 200, resp.text
        rows = [r for r in resp.json()["logs"] if r.get("model_group") == "m"]
        if rows:
            return rows
        time.sleep(0.1)
    return rows


async def _run_relay(client_ws, upstream_url, **rt):
    """Drive ``_realtime_relay`` against the fake upstream.

    ``client_ws`` must be a socket whose *peer* is what the relay forwards to —
    connecting the test's own client straight to the fake server as well would
    let it observe the upstream's frames without the relay ever running.
    """
    from wiwi.server.app import _realtime_relay

    settings = RealtimeSettings(max_session_s=rt.pop("max_session_s", 5.0), **rt)
    async with websockets.connect(upstream_url) as upstream_ws:
        return await _realtime_relay(client_ws, upstream_ws, settings)


class _StubClientWs:
    """The client half of a relay pair: records what the relay forwards.

    ``send_text``/``send_bytes`` are the two methods the upstream direction
    calls, so a relay that sends the wrong one raises here — which is the point.
    """

    def __init__(self) -> None:
        self.text: list[str] = []
        self.binary: list[bytes] = []
        self.closed = False
        self._in: asyncio.Queue = asyncio.Queue()

    async def send_text(self, frame: str) -> None:
        self.text.append(frame)

    async def send_bytes(self, data) -> None:
        self.binary.append(bytes(data))

    def iter_text(self):
        async def _gen():
            while True:
                item = await self._in.get()
                if item is None:
                    return
                yield item

        return _gen()

    async def feed(self, frame: str) -> None:
        await self._in.put(frame)

    async def hangup(self) -> None:
        await self._in.put(None)

    async def close(self) -> None:
        self.closed = True


# --- bug 1: the unpriced-model gate, and key-level rate limits ----------------


def test_realtime_refuses_an_unpriced_model_for_a_budget_capped_key(upstream):
    """AUDIT #323's reject gate must hold on the WebSocket surface too.

    Before the fix: policy=reject, key with max_budget, unpriced model ->
    the socket was accepted and the session served for free.
    """
    app = app_mod.create_app(_config(policy="reject"))
    with TestClient(app) as client:
        key = _mint_key(client, max_budget=1.0)
        assert _refusal_status_on(client, key=key) == 503
    # The property that matters: a refused admission opens no upstream socket.
    assert upstream.connections == 0


def test_the_unpriced_gate_reads_identically_on_both_surfaces(upstream):
    """Pin the pair together so they cannot drift again.

    The gate itself lives in the HTTP ``authenticate()`` closure; the realtime
    route mirrors it because it authenticates its own socket credential. This
    test is what keeps the mirror honest: same key, same model, same policy,
    same verdict on both surfaces.
    """
    app = app_mod.create_app(_config(policy="reject"))
    with TestClient(app) as client:
        key = _mint_key(client, max_budget=1.0)
        ws_status = _refusal_status_on(client, key=key)
        http = client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": f"Bearer {key}"})
    assert ws_status == 503, "realtime skipped the reject gate"
    assert http.status_code == 503, "HTTP surface disagrees"
    assert http.json()["error"]["type"] == "unpriced_model_error"
    assert upstream.connections == 0


def test_an_uncapped_key_is_not_refused_by_the_unpriced_gate(upstream):
    """The gate is scoped to budget-capped keys, exactly as in authenticate().

    A key with no max_budget cannot be overrun by an unpriced model, so the
    503 would be a lie — pinning the guard's shape, not just its presence.
    """
    app = app_mod.create_app(_config(policy="reject", base_url=upstream.url))
    with TestClient(app) as client:
        key = _mint_key(client)
        with client.websocket_connect(
                "/v1/realtime?model=m",
                headers={"authorization": f"Bearer {key}"}) as ws:
            ws.send_text('{"type":"response.create"}')
            assert ws.receive_text() == '{"type":"response.create"}'
    assert upstream.received == ['{"type":"response.create"}']


def test_realtime_enforces_the_key_rpm(upstream):
    """Key-level RPM was unenforced on realtime while every HTTP surface
    enforced it — a session can hold a deployment slot for an hour.

    ``pick_deployment`` only enforces the *per-deployment* caps; the key's own
    rpm/tpm windows were reserved exclusively by ``enforce_rate_limit``, whose
    one call site was the HTTP path. An uncapped deployment therefore let a key
    open unlimited concurrent sessions.
    """
    upstream.after_echo = [
        '{"type":"response.done","usage":{"input_tokens":8,"output_tokens":4}}']
    app = app_mod.create_app(_config(base_url=upstream.url))
    with TestClient(app) as client:
        key = _mint_key(client, rpm=1)
        with client.websocket_connect(
                "/v1/realtime?model=m",
                headers={"authorization": f"Bearer {key}"}) as ws:
            ws.send_text('{"type":"response.create"}')
            # The echo, then the usage frame — the session really ran.
            assert ws.receive_text() == '{"type":"response.create"}'
            assert ws.receive_text().startswith('{"type":"response.done"')
        # The first session's rpm slot is still inside the 60 s window, so the
        # next admission is refused rather than served.
        assert _refusal_status_on(client, key=key) == 429
    assert upstream.connections == 1


# --- bug 2: a session with no usage is still logged ---------------------------


async def test_a_session_with_no_usage_still_writes_a_request_log_row(upstream):
    """Every session that passed admission produces a row.

    Before the fix ``_bill_realtime_session`` returned before ``log_request``
    when both token counts were 0, so a failed handshake, an upstream 401/5xx
    or the ``max_session_s`` cap left nothing at all.
    """
    logged: list = []

    class _Logs:
        def log_request(self, event) -> None:
            logged.append(event)

    class _State:
        def __init__(self) -> None:
            from wiwi.cost.pricing import CostEngine

            self.auth = None
            self.cost = CostEngine()
            self.logs = _Logs()
            self.spend_charge_failures = 0

    app = app_mod.create_app(_config(base_url=upstream.url))
    state = _State()
    dep = app.state.wiwi.router.groups["m"][0]

    from wiwi.core.context import RequestContext
    from wiwi.ir import types as ir
    from wiwi.server.app import _bill_realtime_session

    ctx = RequestContext(surface="chat",
                         ir_req=ir.Request(model="m", messages=[],
                                           gen_params=ir.GenParams()),
                         group="m", request_id="rt-nil-usage")

    # usage=None: exactly what the relay returns for a session that saw no
    # usage frame, and what the route passed when it used to skip billing
    # entirely (``if usage is not None``).
    await _bill_realtime_session(state, None, ctx, dep, None)

    assert len(logged) == 1, "a session with no usage logged no row"
    row = logged[0]
    assert row.request_id == "rt-nil-usage"
    assert row.model_group == "m"
    assert row.status == 200
    assert row.provider == "p1"
    # The counts are zero because nothing was reported — not because the
    # provider said zero. usage_estimated is exactly that distinction
    # (AUDIT #131), so a spend report cannot present it as fact.
    assert row.tok_in == 0 and row.tok_out == 0
    assert row.usage_estimated is True
    assert ctx.stop_reason == "realtime_session"
    assert ctx.usage is not None
    assert app_mod._RT_COUNTERS["usage_missing"] == 1


def test_a_session_with_no_usage_is_counted_on_health(upstream):
    """The loss must be visible, not just logged.

    A row saying "no usage" is only findable if you already suspect one; the
    counter answers "how many did I not price" without reading logs, alongside
    ``spend_charge_failures``.
    """
    upstream.close_after_echo = 1  # end the session; no usage is ever reported
    app = app_mod.create_app(_config(base_url=upstream.url))
    with TestClient(app) as client:
        _run_session(client)
        health = client.get("/health").json()
        assert health["realtime_usage_missing"] == 1
        assert app_mod._RT_COUNTERS["usage_missing"] == 1
        # And the row itself, which the early return used to skip entirely.
        rows = _realtime_rows(client)
        assert len(rows) == 1, "the session produced no request-log row"
        assert bool(rows[0]["usage_estimated"]) is True
        assert rows[0]["status"] == 200


def test_a_priced_session_does_not_increment_the_missing_usage_counter(upstream):
    """The counter must not be a session counter in disguise.

    Asserted against the row as well, so it cannot pass by never billing.
    """
    upstream.after_echo = [
        '{"type":"response.done","usage":{"input_tokens":8,"output_tokens":4}}']
    upstream.close_after_echo = 1
    app = app_mod.create_app(_config(base_url=upstream.url))
    with TestClient(app) as client:
        _run_session(client)
        assert client.get("/health").json()["realtime_usage_missing"] == 0
        rows = _realtime_rows(client)
        assert len(rows) == 1
        # Provider-reported counts: not an estimate, and 8/4 tokens landed.
        assert bool(rows[0]["usage_estimated"]) is False
        assert rows[0]["tok_in"] == 8 and rows[0]["tok_out"] == 4


async def test_usage_that_arrives_after_the_cap_is_still_billed(upstream):
    """``response.done`` is the *last* frame of a turn, so a session cut at
    ``max_session_s`` is cut exactly where its usage event lives.

    Cancelling both pump tasks the instant the cap fired discarded that frame
    and billed real tokens at $0. The relay now gives the upstream a short
    grace window before cancelling.
    """
    upstream.script = [(0.3, ('{"type":"response.done","usage":'
                              '{"input_tokens":7,"output_tokens":3}}'))]
    stub = _StubClientWs()
    # The client says nothing; the cap fires first, then the usage frame.
    assert await asyncio.wait_for(
        _run_relay(stub, upstream.url, max_session_s=0.1), 5) == (7, 3)
    assert stub.text == [upstream.script[0][1]]


# --- bug 3: a binary frame must not kill the relay ---------------------------


async def test_a_binary_frame_does_not_kill_the_session(upstream):
    """``recv()`` yields ``str | bytes`` unless the connection sets
    ``decode=True``, and audio responses are binary.

    Before the fix the first binary frame raised ``TypeError: a bytes-like
    object is required`` on ``'"usage"' in frame``, the blanket ``except``
    swallowed it, and the ``finally`` closed the client socket — one audio
    chunk dropped a live session, at ``debug`` log level.
    """
    upstream.script = [
        (0.0, b"\x00\x01\x02binary-audio"),
        (0.1, '{"type":"response.done"}'),
    ]
    stub = _StubClientWs()
    await asyncio.wait_for(_run_relay(stub, upstream.url, max_session_s=1.0), 5)

    assert stub.binary == [b"\x00\x01\x02binary-audio"], (
        "the binary frame did not reach the client as bytes")
    # The session survived it: the next text frame was forwarded too.
    assert stub.text == ['{"type":"response.done"}']


async def test_a_usage_frame_after_a_binary_frame_is_still_billed(upstream):
    """Binary frames must not desynchronise usage accounting either.

    The bytes path returns before the ``"usage"`` substring test (which only
    makes sense for ``str``), so a binary frame can never be mistaken for a
    usage event — and must not stop the next text frame from being read as one.
    """
    upstream.script = [
        (0.0, b"\x00\x01"),
        (0.1, ('{"type":"response.done","usage":'
               '{"input_tokens":5,"output_tokens":2}}')),
    ]
    stub = _StubClientWs()
    assert await asyncio.wait_for(
        _run_relay(stub, upstream.url, max_session_s=1.0), 5) == (5, 2)


async def test_the_route_forwards_a_binary_frame_to_the_client(upstream):
    """End-to-end through the route, so the fix is proven on the live path and
    not only against a hand-built pair of sockets."""
    upstream.script = [(0.0, b"\x00binary")]
    app = app_mod.create_app(_config(base_url=upstream.url))
    with TestClient(app) as client, client.websocket_connect(
            "/v1/realtime?model=m",
            headers={"authorization": f"Bearer {MASTER}"}) as ws:
        assert ws.receive_bytes() == b"\x00binary"
        ws.send_text('{"type":"response.create"}')
        assert ws.receive_text() == '{"type":"response.create"}'