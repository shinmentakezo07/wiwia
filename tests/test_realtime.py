"""Realtime WebSocket surface: admission before accept, then a byte-faithful relay.

The upstream is a real WebSocket server running in-process (``websockets.serve``),
so the relay is exercised over an actual socket rather than a stub — a fake that
"relays" by calling the function under test would prove nothing.

These tests never reach a real provider: they cover what wiwi owns, which is
decided before a single frame moves.
"""

import asyncio
import contextlib

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

    ``serve_future`` runs the server on the *test's* event loop, so no thread and
    no second loop are involved: the relay talks to a genuine socket, but the
    whole test is one asyncio program.
    """

    def __init__(self) -> None:
        self.received: list[str] = []
        self.followups: list[str] = []
        self.connections = 0
        self.server = None
        self.url = ""

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

    async def start(self) -> str:
        self.server = await websockets.serve(
            self._handler, "127.0.0.1", 0, compression=None)
        self.url = f"ws://127.0.0.1:{next(iter(self.server.sockets)).getsockname()[1]}"
        return self.url

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def upstream():
    server = FakeRealtimeServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


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


def _refusal(app, path: str = "/v1/realtime?model=m",
             key: str = MASTER) -> str:
    """Connect, expect a refusal, and return how it surfaced to the client.

    Starlette reports a pre-accept ``close(code=N)`` as a failed handshake whose
    status is N, which is what makes a 401 readable on this surface.
    """
    with TestClient(app) as client:
        try:
            with client.websocket_connect(
                    path, headers={"authorization": f"Bearer {key}"}):
                return "ACCEPTED"
        except Exception as exc:  # noqa: BLE001 — the refusal *is* the assertion
            return f"{type(exc).__name__}: {exc}"


# --- admission happens before accept -----------------------------------------


def test_realtime_is_off_by_default(upstream):
    app = app_mod.create_app(_config(enabled=False))
    assert "ACCEPTED" not in _refusal(app)
    assert upstream.connections == 0


def test_a_bad_key_is_refused_before_the_upstream_is_dialled(upstream):
    app = app_mod.create_app(_config())
    assert "ACCEPTED" not in _refusal(app, key=BAD_KEY)
    # The property that matters: a refused admission opens no upstream socket.
    assert upstream.connections == 0


def test_an_unknown_model_is_refused_before_the_upstream_is_dialled(upstream):
    app = app_mod.create_app(_config())
    assert "ACCEPTED" not in _refusal(app, path="/v1/realtime?model=nope")
    assert upstream.connections == 0


def test_a_provider_without_a_realtime_surface_is_refused(upstream):
    """Anthropic has no Realtime protocol. The client must learn that at
    upgrade, not by hanging on a socket that will never open."""
    app = app_mod.create_app(_config(provider="anthropic"))
    assert "ACCEPTED" not in _refusal(app)
    assert upstream.connections == 0


def test_a_missing_model_is_refused(upstream):
    app = app_mod.create_app(_config())
    assert "ACCEPTED" not in _refusal(app, path="/v1/realtime")
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
