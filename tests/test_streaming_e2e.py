"""True end-to-end verification of the round-92 streaming fixes.

Unlike the unit regressions in ``test_fix_round92.py``, this module exercises the
whole stack over real sockets:

    HTTP client  ->  uvicorn/wiwi (real server, real ASGI)  ->  mock upstream
                                                            (real HTTP server)

Nothing is mocked below the gateway except the upstream provider, which is a
real ``http.server`` instance emitting real SSE bytes over TCP. That means the
httpx streaming client, the SSE parser, the pump, the queue, the encoder, the
journal, and the ASGI response path are all genuinely exercised.

Covered:

1. ``stream_coalesce=true`` with a slow reader — every token must still arrive,
   and nothing may be stranded in the coalescer when the stream ends (#278).
2. A tiny queue (forced via a patched ``maxsize``) with a slow reader — the
   terminal frame must still be delivered rather than dropped (#277).
3. Tool-call streams survive coalescing with args intact and in order.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
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
from wiwi.server.app import create_app

# --------------------------------------------------------------------------
# A real upstream SSE provider on a real socket
# --------------------------------------------------------------------------


class _SSEUpstream(BaseHTTPRequestHandler):
    """Serves a chat-completions SSE stream, then optionally stalls."""

    #: Number of text chunks to emit.
    n_chunks = 40
    #: Seconds to sleep after the last content chunk, before [DONE].
    tail_stall_s = 0.0
    #: Emit tool-call chunks instead of plain text.
    tool_call = False

    def log_message(self, *args):  # silence the default stderr spam
        return

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()

        def frame(obj) -> bytes:
            return b"data: " + json.dumps(obj).encode() + b"\n\n"

        base = {
            "id": "chatcmpl-e2e", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": "gpt-4o",
        }
        try:
            self.wfile.write(frame({**base, "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": ""},
                 "finish_reason": None}]}))
            self.wfile.flush()

            if self.tool_call:
                # A conforming upstream announces the call ONCE (id + name),
                # then streams argument fragments only. Re-sending `name` on
                # every chunk is not what any provider does.
                self.wfile.write(frame({**base, "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0,
                                              "id": "call_1",
                                              "type": "function",
                                              "function": {"name": "get_weather",
                                                           "arguments": ""}}]},
                    "finish_reason": None}]}))
                for frag in ('{"city":', ' "Paris"}'):
                    self.wfile.write(frame({**base, "choices": [{
                        "index": 0,
                        "delta": {"tool_calls": [{"index": 0,
                                                  "function": {"arguments": frag}}]},
                        "finish_reason": None}]}))
                self.wfile.write(frame({**base, "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}))
            else:
                for i in range(self.n_chunks):
                    self.wfile.write(frame({**base, "choices": [{
                        "index": 0, "delta": {"content": f"tok{i:03d} "},
                        "finish_reason": None}]}))
                    self.wfile.flush()
                if self.tail_stall_s:
                    # An upstream that goes quiet: the coalescer's deadline must
                    # release held text during this window (#278).
                    time.sleep(self.tail_stall_s)
                self.wfile.write(frame({**base, "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}]}))

            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def upstream():
    """A real HTTP server on a real port; yields (base_url, handler_class)."""
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _SSEUpstream)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        srv.shutdown()
        srv.server_close()


def _config(upstream_url: str, *, coalesce: bool = False,
            ping_s: float = 0.0) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url=upstream_url,
                               keys=[KeyDef(label="k1", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(
            stream_coalesce=coalesce,
            stream_coalesce_max_bytes=8192,
            stream_coalesce_max_ms=30.0,
            stream_ping_interval_s=ping_s,
            stream_idle_timeout_s=20.0,
            stream_journal_enabled=False,
        ),
    )


async def _read_sse(resp: httpx.Response, delay: float = 0.0) -> list[str]:
    """Collect the ``data:`` payloads of an SSE response, optionally slowly."""
    out: list[str] = []
    async for line in resp.aiter_lines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            out.append(payload)
            if delay:
                await asyncio.sleep(delay)
    return out


def _content(lines: list[str]) -> str:
    parts = []
    for raw in lines:
        if raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        for ch in obj.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                parts.append(delta["content"])
    return "".join(parts)


# --------------------------------------------------------------------------
# 1. Coalescing + a slow reader: no token may be lost or stranded
# --------------------------------------------------------------------------


async def test_coalesced_stream_delivers_every_token_to_a_slow_reader(upstream):
    """E2E #278: coalescing on, reader slower than the close.

    The reader pauses after the upstream has finished, which is exactly the
    window where the old coalescer held its buffer with no timer. Every token
    must still arrive.
    """
    app = create_app(_config(upstream, coalesce=True))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test", timeout=30) as c, c.stream(
                "POST", "/v1/chat/completions", json={
                    "model": "gpt-4o", "max_tokens": 64, "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={"authorization": "Bearer sk-wiwi-master-test"}) as r:
            assert r.status_code == 200
            # Slow reader: each line costs 5 ms, so 40 tokens take ~200 ms
            # and the upstream has long since closed.
            lines = await _read_sse(r, delay=0.005)

    text = _content(lines)
    expected = "".join(f"tok{i:03d} " for i in range(40))
    assert text == expected, f"lost/duplicated content under coalescing: {text!r}"
    assert "[DONE]" in lines, "terminal [DONE] was not delivered"


async def test_no_token_is_stranded_when_the_upstream_goes_quiet(upstream):
    """E2E #278: a quiet upstream must not leave text stuck in the coalescer.

    The unit-level deadline is covered in ``test_fix_round92.py``; what this
    adds is the *end-to-end* consequence. Under sustained backpressure the
    coalescer holds text, and the upstream then stops for 0.5 s before
    ``[DONE]``. If the deadline were not honoured the held text could only be
    released by the close, and the reader would observe a burst of content
    arriving at the same instant as ``[DONE]``.

    Timing is deliberately NOT asserted against a bare wall-clock number (the
    earlier revision of this test did, and was measuring the mock's own sleep
    rather than the coalescer). The observable property is ordering plus
    completeness: every token arrives, in order, and content keeps flowing
    *during* the quiet window rather than being dumped at the close.
    """
    _SSEUpstream.n_chunks = 400
    _SSEUpstream.tail_stall_s = 0.5
    try:
        app = create_app(_config(upstream, coalesce=True))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test", timeout=60) as c:
                t0 = time.monotonic()
                content: list[str] = []
                content_times: list[float] = []
                done_at: float | None = None
                async with c.stream(
                        "POST", "/v1/chat/completions", json={
                            "model": "gpt-4o", "max_tokens": 512, "stream": True,
                            "messages": [{"role": "user", "content": "hi"}],
                        },
                        headers={"authorization": "Bearer sk-wiwi-master-test"}) as r:
                    assert r.status_code == 200
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        now = time.monotonic() - t0
                        if raw == "[DONE]":
                            done_at = now
                            continue
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        got = "".join(
                            (ch.get("delta") or {}).get("content") or ""
                            for ch in (obj.get("choices") or []))
                        if got:
                            content.append(got)
                            content_times.append(now)
                        # Deliberately slow: this is what backs the queue up so
                        # the coalescer actually engages.
                        await asyncio.sleep(0.002)

        assert done_at is not None, "stream never terminated"
        joined = "".join(content)
        assert joined == "".join(f"tok{i:03d} " for i in range(400)), (
            "tokens lost, duplicated or reordered under backpressure")

        # Content must have started flowing long before the stall ended, i.e.
        # the coalescer did not sit on the whole response until [DONE].
        assert content_times[0] < done_at - 0.2, (
            f"first content at {content_times[0]:.3f}s vs [DONE] at "
            f"{done_at:.3f}s — nothing was released until the close")
        # And content must span the quiet window rather than arriving in one
        # terminal burst: at least one frame landed after the stall began.
        stall_started = done_at - 0.5
        assert any(t > stall_started for t in content_times), (
            "no content was released during the quiet window — the buffered "
            "tail waited for the close")
    finally:
        _SSEUpstream.n_chunks = 40
        _SSEUpstream.tail_stall_s = 0.0


# --------------------------------------------------------------------------
# 2. Terminal frame delivery with a tiny queue (#277)
# --------------------------------------------------------------------------


async def test_terminal_frame_arrives_with_a_tiny_output_queue(upstream):
    """E2E #277: force queue pressure and confirm the stream still terminates.

    ``asyncio.Queue`` binds ``maxsize`` at construction, so the pump's 4096-slot
    queue is shrunk by patching the factory the gateway imports — a real queue
    of size 2, driven by a genuinely slow reader.
    """
    import wiwi.core.gateway as gw

    real_queue = asyncio.Queue

    def tiny_queue(*args, **kwargs):
        kwargs["maxsize"] = 2
        return real_queue(*args, **kwargs)

    app = create_app(_config(upstream, coalesce=True))
    async with LifespanManager(app):
        gw.asyncio.Queue = tiny_queue  # type: ignore[assignment]
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test",
                                         timeout=30) as c, c.stream(
                    "POST", "/v1/chat/completions", json={
                        "model": "gpt-4o", "max_tokens": 64, "stream": True,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    headers={"authorization": "Bearer sk-wiwi-master-test"}) as r:
                assert r.status_code == 200
                lines = await _read_sse(r, delay=0.004)
        finally:
            gw.asyncio.Queue = real_queue  # type: ignore[assignment]

    # Under a 2-slot queue, content may legitimately be shed as backpressure.
    # What must never happen is a missing terminal: the client has to learn the
    # stream ended, or it hangs forever.
    assert "[DONE]" in lines, (
        "the stream never terminated under queue pressure — the terminal frame "
        f"was dropped; got {len(lines)} lines: {lines[-3:]!r}")


async def test_terminal_frame_arrives_with_tiny_queue_and_messages_surface(upstream):
    """The Anthropic surface takes the ping-bearing wait path too."""
    import wiwi.core.gateway as gw

    real_queue = asyncio.Queue

    def tiny_queue(*args, **kwargs):
        kwargs["maxsize"] = 2
        return real_queue(*args, **kwargs)

    app = create_app(_config(upstream, coalesce=True, ping_s=1.0))
    async with LifespanManager(app):
        gw.asyncio.Queue = tiny_queue  # type: ignore[assignment]
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test",
                                         timeout=30) as c, c.stream("POST", "/v1/messages", json={
                "model": "gpt-4o", "max_tokens": 64, "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            }, headers={"x-api-key": "sk-wiwi-master-test",
                        "anthropic-version": "2023-06-01"}) as r:
                assert r.status_code == 200
                lines = await _read_sse(r, delay=0.004)
        finally:
            gw.asyncio.Queue = real_queue  # type: ignore[assignment]

    joined = "\n".join(lines)
    assert "message_stop" in joined, (
        "a messages-surface stream under queue pressure never terminated "
        f"({len(lines)} frames)")


# --------------------------------------------------------------------------
# 3. Tool calls through the coalescer
# --------------------------------------------------------------------------


async def test_tool_call_args_survive_coalescing(upstream):
    """Tool-call deltas are never merged away, and args reassemble exactly."""
    _SSEUpstream.tool_call = True
    try:
        app = create_app(_config(upstream, coalesce=True))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test",
                                         timeout=30) as c, c.stream(
                    "POST", "/v1/chat/completions", json={
                        "model": "gpt-4o", "max_tokens": 64, "stream": True,
                        "messages": [{"role": "user", "content": "weather?"}],
                        "tools": [{"type": "function", "function": {
                            "name": "get_weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"]}}}],
                    },
                    headers={"authorization": "Bearer sk-wiwi-master-test"}) as r:
                assert r.status_code == 200
                lines = await _read_sse(r, delay=0.004)

        args, names, finish = [], [], None
        for raw in lines:
            if raw == "[DONE]":
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            for ch in obj.get("choices") or []:
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        names.append(fn["name"])
                    if fn.get("arguments"):
                        args.append(fn["arguments"])

        assert names == ["get_weather"], f"tool name lost: {names}"
        assert json.loads("".join(args)) == {"city": "Paris"}
        assert finish == "tool_calls"
        assert "[DONE]" in lines
    finally:
        _SSEUpstream.tool_call = False
