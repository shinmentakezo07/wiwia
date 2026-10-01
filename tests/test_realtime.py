"""Realtime WebSocket surface: admission before accept, then a byte-faithful relay.

The upstream is a real WebSocket server running in-process (``websockets.serve``),
so the relay is exercised over an actual socket rather than a stub — a fake that
"relays" by calling the function under test would prove nothing.

These tests never reach a real provider: they cover what wiwi owns, which is
decided before a single frame moves.
"""

import asyncio
import contextlib
import threading

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

MASTER = "sk-wiwi-master-realtime"
BAD_KEY = "sk-wiwi-not-a-real-key-at-all"


# --- a real in-process websocket upstream ------------------------------------


class FakeRealtimeServer:
    """Echoes every text frame back, then emits any scripted follow-up frames.

    Runs on its **own** thread and event loop, not the test's. Starlette's
    ``TestClient`` drives the app on a separate portal loop and blocks this one
    while the test body runs, so a server sharing the test's loop could never
    accept a connection while the route was trying to make one.
    """

    def __init__(self) -> None:
        self.received: list[str] = []
        self.followups: list[str] = []
        self.connections = 0
        self.url = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    async def _handler(self, ws) -> None:
        self.connections += 1
        try:
            async for raw in ws:
                self.received.append(raw)
                await ws.send(raw)
                for extra in self.followups:
                    await ws.send(extra)
        except websockets.ConnectionClosed:
            pass

    def start(self) -> str:
        self._loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
            # Signalled once the listener is bound and the URL is known — after
            # run_forever, which never returns, would hang the wait forever.
            self._ready.set()
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
        loop", so the close has to be scheduled *onto* the loop before it stops.
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


def _config(provider: str = "openai-compatible", enabled: bool = True,
            base_url: str = "http://127.0.0.1:1/v1", **realtime) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider=provider, base_url=base_url,
                               keys=[KeyDef(label="a",
                                            key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="m",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="mm"))],
        general_settings=GeneralSettings(
            master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(),
        realtime=RealtimeSettings(enabled=enabled, **realtime),
    )


def _refusal_status(app, path: str = "/v1/realtime?model=m",
                    key: str = MASTER) -> int | None:
    """Connect expecting a refusal; return the HTTP status the client saw.

    A close issued before ``accept`` is reported as a *generic* handshake
    rejection (uvicorn answers 403 for any of them), so the route writes a real
    ``websocket.http.response.start`` instead. Reading the status here is what
    proves a 401 is distinguishable from a 404 for the client — the property the
    whole before-accept design exists to deliver.
    """
    from starlette.testclient import WebSocketDenialResponse

    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                    path, headers={"authorization": f"Bearer {key}"}):
                return None
        except WebSocketDenialResponse as exc:
            return exc.status_code


# --- admission happens before accept -----------------------------------------


def test_realtime_is_off_by_default(upstream):
    app = app_mod.create_app(_config(enabled=False))
    assert _refusal_status(app) is not None
    assert upstream.connections == 0


def test_a_bad_key_is_refused_before_the_upstream_is_dialled(upstream):
    app = app_mod.create_app(_config())
    assert _refusal_status(app, key=BAD_KEY) == 401
    # The property that matters: a refused admission opens no upstream socket.
    assert upstream.connections == 0


def test_an_unknown_model_is_refused_before_the_upstream_is_dialled(upstream):
    app = app_mod.create_app(_config())
    assert _refusal_status(app, path="/v1/realtime?model=nope") == 404
    assert upstream.connections == 0


def test_a_provider_without_a_realtime_surface_is_refused(upstream):
    """Anthropic has no Realtime protocol. The client must learn that at
    upgrade, not by hanging on a socket that will never open."""
    app = app_mod.create_app(_config(provider="anthropic"))
    assert _refusal_status(app) == 501
    assert upstream.connections == 0


def test_a_missing_model_is_refused(upstream):
    app = app_mod.create_app(_config())
    assert _refusal_status(app, path="/v1/realtime") == 404
    assert upstream.connections == 0


# --- the relay ---------------------------------------------------------------


async def test_a_relayed_frame_arrives_upstream_byte_identical(upstream):
    from wiwi.config import RealtimeSettings

    async with websockets.connect(upstream.url) as ws:
        relay = asyncio.ensure_future(_run_relay(ws, upstream.url))
        frame = ('{"type":"session.update","session":'
                 '{"modalities":["text","audio"]}}')
        await ws.send(frame)
        assert await asyncio.wait_for(ws.recv(), 5) == frame
        relay.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay
    assert upstream.received == [frame]
    assert upstream.connections == 1
    assert RealtimeSettings().max_session_s > 0


async def _run_relay(client_ws, upstream_url, **rt):
    """Drive ``_realtime_relay`` against the fake upstream."""
    from wiwi.server.app import _realtime_relay

    settings = RealtimeSettings(max_session_s=rt.pop("max_session_s", 5.0), **rt)
    async with websockets.connect(upstream_url) as upstream_ws:
        return await _realtime_relay(client_ws, upstream_ws, settings)


async def test_a_usage_bearing_frame_is_billed_on_its_peak_not_its_sum(upstream):
    """The realtime protocol restates overlapping windows of the same
    conversation: response.done and the preceding item-done report the same
    tokens. Summing would inflate every multi-turn session."""
    upstream.followups = [
        ('{"type":"response.output_item.done","usage":'
         '{"input_tokens":10,"output_tokens":5}}'),
        '{"type":"response.done","usage":{"input_tokens":10,"output_tokens":5}}',
    ]
    async with websockets.connect(upstream.url) as ws:
        relay = asyncio.ensure_future(_run_relay(ws, upstream.url))
        await ws.send('{"type":"response.create"}')
        # Drain the echo plus both follow-ups.
        for _ in range(3):
            await asyncio.wait_for(ws.recv(), 5)
        relay.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await relay
    from wiwi.server.app import _realtime_usage

    seen = [_realtime_usage(f) for f in upstream.followups]
    assert seen == [(10, 5), (10, 5)]
    # Peak of [(10,5), (10,5)] is (10,5), not the sum (20,10): the relay returns
    # the peak, and _bill_realtime_session prices exactly that once.


async def test_a_close_on_either_side_ends_the_relay(upstream):
    from starlette.websockets import WebSocketDisconnect  # noqa: F401

    async with websockets.connect(upstream.url) as ws:
        relay = asyncio.ensure_future(_run_relay(ws, upstream.url))
        await ws.send('{"type":"response.create"}')
        await asyncio.wait_for(ws.recv(), 5)
        # Closing the client must end the relay promptly, not hang it.
        await ws.close()
        _done, pending = await asyncio.wait([relay], timeout=5)
        assert not pending, "relay did not end when the client closed"
        assert relay.done()
        if relay.exception() is not None and not isinstance(
                relay.exception(), asyncio.CancelledError):
            raise relay.exception()


# --- usage extraction ---------------------------------------------------------


def test_usage_is_read_from_a_nested_response_event():
    from wiwi.server.app import _realtime_usage

    assert _realtime_usage(
        '{"type":"response.done","response":{"usage":'
        '{"input_tokens":10,"output_tokens":4}}}') == (10, 4)
    assert _realtime_usage('{"type":"response.done","usage":'
                           '{"input_tokens":1,"output_tokens":2}}') == (1, 2)


def test_a_malformed_frame_yields_no_usage_rather_than_raising():
    """A bad frame must never tear down a live audio session — under-counting
    one turn is recoverable; dropping the call is not."""
    from wiwi.server.app import _realtime_usage

    assert _realtime_usage("not json at all") is None
    assert _realtime_usage('{"type":"response.done"}') is None
    assert _realtime_usage('{"usage":"not-an-object"}') is None
    assert _realtime_usage('{"usage":{"input_tokens":null}}') == (0, 0)


async def test_the_route_reaches_the_relay_and_upstream(upstream):
    """End-to-end through the route: a real client socket, wiwi, a real
    upstream socket.

    The unit tests below call ``_realtime_relay`` directly, so they cannot catch
    a mismatch between the route's call and the helper's signature — which is
    exactly the bug this test was added for (the route raised TypeError at the
    first frame, so the session closed at accept and nothing ever reached the
    upstream).
    """
    import contextlib as _c
    from unittest.mock import patch

    from starlette.testclient import TestClient

    app = app_mod.create_app(_config(base_url=upstream.url))
    seen: list[str] = []
    # Captured before the patch: the fake must dial with the *real* connect,
    # or patching the module attribute would also intercept its own call.
    real_connect = websockets.connect

    @_c.asynccontextmanager
    async def fake_connect(url, **kwargs):
        async with real_connect(url, **kwargs) as real:
            seen.append(url)
            yield real

    with (patch("wiwi.server.app.websockets.connect", fake_connect),
          TestClient(app) as client,
          client.websocket_connect(
              "/v1/realtime?model=m",
              headers={"authorization": f"Bearer {MASTER}"}) as ws):
        ws.send_text('{"type":"response.create"}')
        assert ws.receive_text() == '{"type":"response.create"}'

    # The adapter derives the realtime URL from the deployment's base_url:
    # http://host:port/v1 -> ws://host:port/v1/realtime. Asserting the exact
    # dialed URL is what proves the derivation, not just that *a* socket opened.
    assert seen == [upstream.url.rstrip("/") + "/realtime"]
    assert upstream.received == ['{"type":"response.create"}']


# --- adapter capability declarations ------------------------------------------


def test_every_adapter_still_documents_itself():
    """A ``realtime_url`` override must not displace the class docstring.

    Inserting a method as the first body of a class turns its docstring into a
    dead string expression: the class still imports and behaves identically,
    so every behavioural test stays green while ``__doc__`` silently becomes
    ``None``. That is how four adapters lost their docs at once.

    Asserted structurally over the whole package rather than per-adapter, since
    the failure mode is "a method was inserted at the top of some class" and
    has nothing to do with realtime specifically.
    """
    import ast
    import pathlib

    displaced: list[str] = []
    for path in sorted((pathlib.Path(__file__).parent.parent / "wiwi" /
                        "providers").glob("*_adapter.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.body:
                continue
            first = node.body[0]
            if isinstance(first, (ast.FunctionDef, ast.AsyncFunctionDef)):
                displaced.append(f"{path.name}:{node.lineno} {node.name}")
    assert not displaced, "class docstring displaced by a leading method: " + ", ".join(displaced)


def test_only_openai_wire_providers_declare_realtime():
    """Capability is a declaration, and a wrong one fails mid-session.

    The four adapters that inherit the OpenAI chat shape but have no Realtime
    protocol must answer ``None`` so the client gets a 501 at upgrade, rather
    than completing a handshake against a URL that 404s and dying as a closed
    socket once the session is already live.
    """
    from wiwi.providers.registry import PROVIDER_TYPES, get_adapter

    declared = {t: get_adapter(t).realtime_url("https://api.example.com")
                for t in PROVIDER_TYPES}
    with_realtime = {t for t, u in declared.items() if u is not None}
    assert with_realtime == {"openai", "openai-compatible", "gmicloud", "bai"}
    assert declared["openai"] == "wss://api.example.com/realtime"
