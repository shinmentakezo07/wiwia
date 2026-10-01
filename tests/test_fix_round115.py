"""Regression round 115 — OTLP tracing is real, not decorative (spec C).

These tests run a genuine OTLP/HTTP collector in-process rather than mocking the
exporter: ``respx`` cannot intercept it, because the OTLP HTTP exporter uses a
synchronous ``requests`` session, not ``httpx``. That is also why the assertions
here are worth something — the spans below travelled through the real protobuf
encoder and the real exporter. (The encoder is unforgiving in a way that is easy
to get wrong: passing ``kind=None`` drops the entire batch with
``KeyError: None``, which is exactly the bug these tests exist to catch.)
"""

import contextlib
import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

pytest.importorskip("opentelemetry.sdk", reason="tracing tests need wiwi[otel]")

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

_UP = "https://api.openai.com/v1/chat/completions"
_AUTH = {"Authorization": "Bearer sk-wiwi-master-test"}

_CFG = """
telemetry:
  enabled: true
  endpoint: http://127.0.0.1:__PORT__/v1/traces
  export_timeout_s: 5.0
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

_CFG_NO_TELEMETRY = _CFG.split("general_settings:", 1)[1]
_CFG_NO_TELEMETRY = "general_settings:" + _CFG_NO_TELEMETRY


def _value(any_value):
    field = any_value.WhichOneof("value")
    return getattr(any_value, field) if field else None


class _Span:
    def __init__(self, pb):
        self.name = pb.name
        self.trace_id = pb.trace_id.hex()
        self.span_id = pb.span_id.hex()
        self.parent_id = pb.parent_span_id.hex() or None
        self.attrs = {a.key: _value(a.value) for a in pb.attributes}

    def __repr__(self):
        return f"<{self.name} parent={self.parent_id} attrs={self.attrs}>"


class _Collector:
    """Minimal OTLP/HTTP receiver on an ephemeral port."""

    def __init__(self):
        self.spans: list[_Span] = []
        self._lock = threading.Lock()
        collector = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                if self.headers.get("content-encoding") == "gzip":
                    raw = gzip.decompress(raw)
                collector._ingest(raw)
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", "2")
                # The OTLP exporter reuses one session/connection; without this
                # the handler parks in keep-alive and a single-threaded server
                # would never accept the next export (and ``stop()`` would hang).
                self.send_header("connection", "close")
                self.end_headers()
                self.wfile.write(b"{}")
                self.close_connection = True

            def log_message(self, *args):  # silence the per-request stderr noise
                pass

        # Threaded + daemon so a lingering keep-alive handler cannot block
        # ``stop()``, which only stops the accept loop.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def _ingest(self, raw: bytes) -> None:
        request = ExportTraceServiceRequest()
        request.ParseFromString(raw)
        with self._lock:
            for resource_spans in request.resource_spans:
                for scope_spans in resource_spans.scope_spans:
                    self.spans.extend(_Span(s) for s in scope_spans.spans)

    def named(self, name: str) -> list[_Span]:
        with self._lock:
            return [s for s in self.spans if s.name == name]

    def roots(self) -> list[_Span]:
        with self._lock:
            return [s for s in self.spans if s.parent_id is None]


def _chat(text: str = "hi") -> Response:
    return Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def _client_for(config: str, collector: _Collector, headers: dict | None = None):
    app = create_app(load_config_from_string(config.replace("__PORT__", str(collector.port))))
    h = dict(_AUTH)
    h.update(headers or {})
    return app, LifespanManager(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=h)


async def _drain_spans(collector: _Collector) -> None:
    """Force the exporter to flush, then wait for the collector to receive it.

    The batch processor exports from a background thread, so ``force_flush``
    guarantees the HTTP POST is done but not that the collector's handler
    finished appending — hence the bounded poll rather than an assertion that
    would be flaky under load.
    """
    import asyncio

    from wiwi.core.telemetry import tracer

    tracer.force_flush()
    for _ in range(100):
        if collector.spans:
            return
        await asyncio.sleep(0.01)


def _run(config, collector, headers=None):
    """Async-context helper: yields the client, closes it and the lifespan after."""

    @contextlib.asynccontextmanager
    async def _inner():
        _, mgr, client = _client_for(config, collector, headers)
        async with mgr:
            try:
                yield client
            finally:
                await client.aclose()

    return _inner()


@respx.mock
async def test_request_span_with_an_upstream_child_is_exported():
    collector = _Collector().start()
    respx.post(_UP).mock(return_value=_chat())
    try:
        async with _run(_CFG, collector) as client:
            r = await client.post("/v1/chat/completions",
                                  json={"model": "gpt-4o",
                                        "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200
            traced_id = r.headers["x-wiwi-trace-id"]
            assert len(traced_id) == 32 and int(traced_id, 16) > 0
    finally:
        await _drain_spans(collector)
        collector.stop()

    request_spans = collector.named("wiwi.request")
    upstream_spans = collector.named("wiwi.upstream")
    assert len(request_spans) == 1 and len(upstream_spans) == 1
    root, attempt = request_spans[0], upstream_spans[0]
    # The header echoes the root trace id, which lets an operator pull the exact
    # trace from a user-visible response.
    assert traced_id == root.trace_id
    assert root.parent_id is None
    assert root.attrs["wiwi.surface"] == "chat"
    assert root.attrs["wiwi.status"] == 200
    assert root.attrs["wiwi.model"] == "gpt-4o"
    # Nesting: the attempt hangs off the request, in the same trace.
    assert attempt.parent_id == root.span_id
    assert attempt.trace_id == root.trace_id
    assert attempt.attrs["wiwi.provider"] == "openai"
    assert attempt.attrs["wiwi.attempt"] == 1
    assert attempt.attrs["wiwi.attempt_status"] == "ok"


@respx.mock
async def test_inbound_traceparent_is_continued_and_propagated_upstream():
    collector = _Collector().start()
    route = respx.post(_UP).mock(return_value=_chat())
    inbound = "00-11111111111111111111111111111111-2222222222222222-01"
    try:
        async with _run(_CFG, collector, headers={"traceparent": inbound}) as client:
            r = await client.post("/v1/chat/completions",
                                  json={"model": "gpt-4o",
                                        "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200
    finally:
        await _drain_spans(collector)
        collector.stop()

    root = collector.named("wiwi.request")[0]
    # The caller's trace is continued, not replaced: the root keeps their trace id
    # and points at their span as its parent.
    assert root.trace_id == "11111111111111111111111111111111"
    assert root.parent_id == "2222222222222222"
    assert r.headers["x-wiwi-trace-id"] == "11111111111111111111111111111111"

    # And the upstream call carries this attempt's span, so a collector can join
    # the provider's spans to ours rather than showing two unrelated traces.
    upstream = collector.named("wiwi.upstream")[0]
    sent = route.calls[0].request.headers["traceparent"]
    assert sent == f"00-{upstream.trace_id}-{upstream.span_id}-01"
    assert sent.split("-")[1] == root.trace_id


@respx.mock
async def test_failed_request_is_exported_with_its_status():
    collector = _Collector().start()
    respx.post(_UP).mock(return_value=_chat())
    try:
        async with _run(_CFG, collector) as client:
            r = await client.post("/v1/chat/completions",
                                  json={"model": "does-not-exist",
                                        "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 404
    finally:
        await _drain_spans(collector)
        collector.stop()

    root = collector.named("wiwi.request")[0]
    assert root.attrs["wiwi.status"] == 404
    assert root.attrs["wiwi.error_type"] == "not_found_error"
    assert root.attrs["wiwi.model"] == "does-not-exist"
    # No upstream was called, so there must be no attempt span.
    assert collector.named("wiwi.upstream") == []


@respx.mock
async def test_chained_response_records_retrieve_and_persist_spans():
    collector = _Collector().start()
    respx.post(_UP).mock(return_value=_chat("turn text"))
    try:
        async with _run(_CFG, collector) as client:
            first = await client.post("/v1/responses",
                                      json={"model": "gpt-4o", "input": "one"})
            rid = first.json()["id"]
            second = await client.post("/v1/responses",
                                       json={"model": "gpt-4o", "input": "two",
                                             "previous_response_id": rid})
            assert second.status_code == 200
    finally:
        await _drain_spans(collector)
        collector.stop()

    requests = collector.named("wiwi.request")
    assert len(requests) == 2
    # Turn 1 stores; turn 2 retrieves and stores again. Both are children of
    # their own request span, so the transcript read/write is attributable.
    assert len(collector.named("wiwi.persist")) == 2
    retrieve = collector.named("wiwi.retrieve")
    assert len(retrieve) == 1
    assert retrieve[0].attrs["wiwi.found"] is True  # stored only for the turn-2 read
    assert retrieve[0].attrs["wiwi.previous_response_id"] == rid
    second_root = [s for s in requests if s.span_id == retrieve[0].parent_id]
    assert len(second_root) == 1
    for span in collector.named("wiwi.persist"):
        assert span.parent_id in {s.span_id for s in requests}
        assert span.trace_id in {s.trace_id for s in requests}


@respx.mock
async def test_telemetry_disabled_exports_nothing_and_sets_no_header():
    collector = _Collector().start()
    respx.post(_UP).mock(return_value=_chat())
    try:
        async with _run(_CFG_NO_TELEMETRY, collector) as client:
            r = await client.post("/v1/chat/completions",
                                  json={"model": "gpt-4o",
                                        "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200
            assert "x-wiwi-trace-id" not in r.headers
    finally:
        await _drain_spans(collector)
        collector.stop()

    assert collector.spans == []


@respx.mock
async def test_streaming_request_is_traced_and_the_span_outlives_the_response():
    collector = _Collector().start()
    sse = (b'data: {"id":"c","object":"chat.completion.chunk","choices":'
           b'[{"index":0,"delta":{"role":"assistant","content":"hi"},"finish_reason":null}]}\n\n'
           b'data: {"id":"c","object":"chat.completion.chunk","choices":'
           b'[{"index":0,"delta":{},"finish_reason":"stop"}],'
           b'"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
           b"data: [DONE]\n\n")
    respx.post(_UP).mock(return_value=Response(
        200, headers={"content-type": "text/event-stream"}, content=sse))
    try:
        async with _run(_CFG, collector) as client:
            r = await client.post("/v1/chat/completions",
                                  json={"model": "gpt-4o", "stream": True,
                                        "messages": [{"role": "user", "content": "hi"}]})
            assert r.status_code == 200
            body = "".join([chunk async for chunk in r.aiter_text()])
            assert "[DONE]" in body
    finally:
        await _drain_spans(collector)
        collector.stop()

    # A stream ends in its teardown tail, long after the entrypoint returned — the
    # span must still be there, with the stream-only attributes the tail computed.
    root = collector.named("wiwi.request")[0]
    assert root.attrs["wiwi.status"] == 200
    assert root.attrs["wiwi.streamed"] is True
    assert root.attrs["wiwi.chunks"] >= 1
    assert collector.named("wiwi.upstream")[0].attrs["wiwi.attempt_status"] == "ok"
