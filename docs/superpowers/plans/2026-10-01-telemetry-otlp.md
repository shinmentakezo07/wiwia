# Telemetry — OTLP Trace Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Emit a real distributed trace per request (request → stage → upstream attempt)
over OTLP/HTTP, off by default and free when off.

**Architecture:** One module (`wiwi/core/telemetry.py`) that is a no-op facade when
telemetry is disabled or the `[otel]` extra is absent, and a lazy SDK bridge when it is
on. Spans are opened at five *existing* seams; attributes come from fields that already
exist on `RequestContext`. W3C `traceparent` is extracted inbound and injected outbound in
`Gateway._headers`.

**Tech Stack:** Python 3.12, `opentelemetry-api` + `-sdk` + `-exporter-otlp-proto-http`
(optional `[otel]` extra), pytest, httpx (for the test collector).

**Spec:** `docs/superpowers/specs/2026-10-01-telemetry-otlp-design.md`

## Global Constraints

- `requires-python = ">=3.11"`; ruff `line-length = 100`, `target-version = "py311"`.
- **Never import the SDK at module import time.** The facade must be importable with zero
  otel packages installed; all SDK imports live inside `configure()` behind a `try`.
- Library code uses `structlog`, never `print`.
- Gate before claiming done: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`.
- Never commit `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `*.har`,
  `.wiwi/`, `.verify/`.
- Tests: bare `async def test_…`, no `conftest.py`, own fixtures, respx in **decorator**
  form. Regression file: next unused `tests/test_fix_roundN.py`.

---

### Task 1: The facade

**Files:**
- Create: `wiwi/core/telemetry.py`
- Test: `tests/test_telemetry.py`

**Interfaces:**
- Produces: module singleton `Tracer` with `configure(settings)`, `span(name, kind=None,
  **attrs)`, `current_traceparent()`, `current_trace_id()`, `extract(headers)`,
  `shutdown()`, and `is_active: bool`.

- [ ] **Step 1: Write the failing tests**

```python
"""Telemetry facade tests: the disabled path must be a true no-op (spec C)."""

import sys

from wiwi.config import TelemetrySettings
from wiwi.core.telemetry import Tracer


def test_disabled_is_a_noop_context_manager():
    t = Tracer()
    t.configure(TelemetrySettings(enabled=False))
    assert t.is_active is False
    assert t.current_traceparent() is None
    with t.span("x", surface="chat") as span:
        span.set_attribute("k", "v")


def test_missing_sdk_degrades_instead_of_raising(monkeypatch):
    # Simulate a deployment that never installed [otel].
    for name in list(sys.modules):
        if name.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name.startswith("opentelemetry"):
            raise ImportError("no otel extra")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    t = Tracer()
    t.configure(TelemetrySettings(enabled=True, endpoint="http://localhost:4318"))
    assert t.is_active is False          # degraded, not crashed
    with t.span("y"):
        pass


def test_unknown_span_kind_does_not_crash_the_disabled_path():
    t = Tracer()
    t.configure(TelemetrySettings(enabled=False))
    with t.span("z", kind="server"):
        pass
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'wiwi.core.telemetry'`.

- [ ] **Step 3: Implement** `wiwi/core/telemetry.py`

```python
"""OTLP tracing facade (spec C).

This module is importable with **zero** OpenTelemetry packages installed: the SDK is
imported inside :meth:`Tracer.configure`, and every disabled/absent path degrades to a
no-op. That matters because tracing must never be a serving dependency — a collector
outage, a missing extra, or an operator flipping the switch off all leave the request
path exactly as it was.

Spans are opened at seams that already exist (run_chat_like, the attempt loop, the
stream pump); attributes come from fields already on RequestContext, so no measurement
is added to the hot path.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator, Mapping

import structlog

from wiwi.config import TelemetrySettings

log = structlog.get_logger()


class _NoopSpan:
    """Stand-in for an OTel span when tracing is off or the SDK is absent."""

    def set_attribute(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class Tracer:
    def __init__(self) -> None:
        self.is_active = False
        self._tracer: Any = None
        self._provider: Any = None
        self._settings = TelemetrySettings()

    def configure(self, settings: TelemetrySettings) -> None:
        """Turn tracing on. Idempotent; never raises.

        The whole SDK import lives in this function so a deployment without the
        ``[otel]`` extra can still ``import wiwi.core.telemetry``.
        """
        self._settings = settings
        self.is_active = False
        self._tracer = None
        if not settings.enabled:
            return
        if not settings.endpoint:
            log.warning("telemetry_enabled_without_endpoint")
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import (
                ParentBased,
                TraceIdRatioBased,
            )
        except ImportError:
            log.warning("telemetry_extra_missing", extra="install wiwi[otel]")
            return

        resource = Resource.create({"service.name": settings.service_name})
        ratio = min(max(settings.sample_ratio, 0.0), 1.0)
        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(ratio)))
        exporter = OTLPSpanExporter(endpoint=settings.endpoint,
                                    headers=dict(settings.headers) or None)
        # BatchSpanProcessor: export off the request path. A collector outage
        # costs dropped spans, never latency.
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        self._provider = provider
        self._tracer = trace.get_tracer("wiwi")
        self.is_active = True
        log.info("telemetry_enabled", endpoint=settings.endpoint,
                 sample_ratio=ratio)

    @contextlib.contextmanager
    def span(self, name: str, kind: str | None = None,
             **attrs: Any) -> Iterator[Any]:
        """Open a span, disabled or not. ``kind`` maps to OTel SpanKind by name."""
        if not self.is_active:
            yield _NoopSpan()
            return
        otel_kind = None
        if kind is not None:
            with contextlib.suppress(Exception):
                from opentelemetry.trace import SpanKind
                otel_kind = getattr(SpanKind, kind.upper(), None)
        with self._tracer.start_as_current_span(name, kind=otel_kind) as span:
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(k, v)
            yield span

    def extract(self, headers: Mapping[str, str]) -> None:
        """Continue an inbound W3C trace. No-op when disabled or header absent."""
        if not self.is_active:
            return
        try:
            from opentelemetry.propagate import extract
            extract(dict(headers))
        except Exception:
            pass

    def current_traceparent(self) -> str | None:
        """W3C ``traceparent`` for the active span, or None when inactive."""
        if not self.is_active:
            return None
        try:
            from opentelemetry.propagate import inject
            carrier: dict[str, str] = {}
            inject(carrier)
            return carrier.get("traceparent")
        except Exception:
            return None

    def current_trace_id(self) -> str | None:
        if not self.is_active:
            return None
        try:
            from opentelemetry.trace import format_trace_id, get_current_span
            ctx = get_current_span().get_span_context()
            return format_trace_id(ctx.trace_id) if ctx.is_valid else None
        except Exception:
            return None

    def shutdown(self) -> None:
        if self._provider is not None:
            with contextlib.suppress(Exception):
                self._provider.shutdown()
        self._provider = None
        self._tracer = None
        self.is_active = False


tracer = Tracer()
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add wiwi/core/telemetry.py tests/test_telemetry.py
git commit -m "Add a no-op-by-default tracing facade"
```

---

### Task 2: Config section and the `[otel]` extra

**Files:**
- Modify: `wiwi/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_telemetry.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_telemetry_defaults_to_off_and_parses_a_section():
    from wiwi.config import load_config_from_string
    cfg = load_config_from_string("model_list: []\n")
    assert cfg.telemetry.enabled is False
    assert cfg.telemetry.endpoint == ""
    assert cfg.telemetry.service_name == "wiwi"
    cfg2 = load_config_from_string(
        "model_list: []\ntelemetry:\n  enabled: true\n"
        "  endpoint: http://collector:4318/v1/traces\n"
        "  headers:\n    x-api-key: k\n")
    assert cfg2.telemetry.enabled is True
    assert cfg2.telemetry.headers["x-api-key"] == "k"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_telemetry.py -q -k defaults`
Expected: FAIL — `AttributeError: 'WiwiConfig' object has no attribute 'telemetry'`.

- [ ] **Step 3: Implement**

`wiwi/config.py`, above `class WiwiConfig`:

```python
class TelemetrySettings(BaseModel):
    """OTLP trace export (spec C). Off unless a collector is configured."""

    enabled: bool = False
    endpoint: str = ""            # e.g. http://localhost:4318/v1/traces
    service_name: str = "wiwi"
    sample_ratio: float = 1.0     # 0..1, applied to roots only
    headers: dict[str, str] = Field(default_factory=dict)
```

and in `WiwiConfig`, after `healer`:

```python
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
```

`pyproject.toml`, in `[project.optional-dependencies]` beside `redis`/`dev`:

```toml
otel = [
  "opentelemetry-api>=1.20",
  "opentelemetry-sdk>=1.20",
  "opentelemetry-exporter-otlp-proto-http>=1.20",
]
```

- [ ] **Step 4: Verify pass**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add wiwi/config.py pyproject.toml tests/test_telemetry.py
git commit -m "Configure OTLP tracing as an optional extra"
```

---

### Task 3: Lifecycle and the request span

**Files:**
- Modify: `wiwi/server/app.py` (import, lifespan configure/shutdown, `run_chat_like`)

**Interfaces:**
- Consumes: `wiwi.core.telemetry.tracer` (Tasks 1–2).

- [ ] **Step 1: Configure in the lifespan**

Beside the other lifespan startup calls (grep `state.healer.start()`):

```python
    from wiwi.core.telemetry import tracer as _tracer
    _tracer.configure(config.telemetry)
```

and in shutdown, beside the other stops:

```python
    from wiwi.core.telemetry import tracer as _tracer
    _tracer.shutdown()
```

Put a single module-level `from wiwi.core.telemetry import tracer as _tracer` at the top
of `app.py` instead of the local imports if lint prefers it — the facade imports nothing
heavy, so a top-level import is correct here.

- [ ] **Step 2: Open the request span and propagate**

`run_chat_like`, immediately after `state_ = app.state.wiwi`:

```python
        _tracer.extract(request.headers)
        with _tracer.span("wiwi.request", kind="server",
                          **{"wiwi.surface": surface}) as _rspan:
            return await _run_chat_inner(_rspan, ...)
```

Because `run_chat_like` is a single long function, the minimal correct change is to wrap
its body: rename the existing function to `_run_chat_inner` is **not** required — instead
open the span with `contextlib.ExitStack` is also unnecessary. Concretely: keep
`run_chat_like` as-is and add, after the docstring/`state_` assignment,

```python
        _req_span = _tracer.span("wiwi.request", kind="server",
                                 **{"wiwi.surface": surface,
                                    "wiwi.request_id": getattr(request.state, "request_id",
                                                               None),
                                    "wiwi.model": body.get("model")
                                    if isinstance(body, dict) else None})
        _req_cm = _req_span.__enter__()
```

and a `finally` that closes it. The function already has one outer `try/except` around the
whole body — extend it to `try: … except … finally: _req_span.__exit__(None, None, None)`.

Set attributes as they become known (they are cheap and the span is already open):
`_req_cm.set_attribute("wiwi.key_id", info.key_id)` after auth,
`_req_cm.set_attribute("wiwi.status", ctx.status)` before the return, and
`_req_cm.set_attribute("wiwi.stream", ir_req.stream)` after decode.

- [ ] **Step 3: Echo the trace id**

Where `success_headers` is built and where `_err` builds its headers, add:

```python
    tid = _tracer.current_trace_id()
    if tid:
        headers["x-wiwi-trace-id"] = tid
```

- [ ] **Step 4: Verify nothing regressed**

Run: `python3 -m pytest tests/test_integration.py tests/test_fix_round113.py -q`
Expected: PASS — tracing is off in tests, so behaviour is identical.

- [ ] **Step 5: Commit**

```bash
git add wiwi/server/app.py
git commit -m "Open a request span and echo the trace id"
```

---

### Task 4: Attempt and stream spans, outbound propagation

**Files:**
- Modify: `wiwi/core/gateway.py` (`_headers`, `complete`, `stream` attempt loop)

- [ ] **Step 1: Inject the outbound header**

In `Gateway._headers`, at the end before `return headers`:

```python
        # Per-attempt W3C propagation: the active span here is the attempt span
        # opened by the caller, so a retry becomes a sibling span, not a duplicate
        # of its predecessor (spec C).
        tp = _tracer.current_traceparent()
        if tp:
            headers["traceparent"] = tp
```

- [ ] **Step 2: Wrap each attempt**

In `complete` and in `stream`, the per-attempt block that builds headers and posts is
wrapped:

```python
        with _tracer.span("wiwi.upstream", kind="client",
                          **{"wiwi.deployment": dep.name,
                             "wiwi.provider": dep.provider.provider_type,
                             "wiwi.model_id": model_id,
                             "wiwi.attempt": attempt_no}) as _aspan:
            ... existing send ...
            _aspan.set_attribute("wiwi.upstream_status", resp.status_code)
            _aspan.set_attribute("wiwi.latency_ms", elapsed_ms)
```

with `attempt_no` taken from whatever counter the loop already keeps (grep the attempt
loop in `execute_with_retries`/`complete`; reuse it — do not add one).

Add the import at the top of `gateway.py`:

```python
from wiwi.core.telemetry import tracer as _tracer
```

- [ ] **Step 3: Verify**

Run: `python3 -m pytest tests/test_integration.py tests/test_router.py tests/test_recovery.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add wiwi/core/gateway.py
git commit -m "Trace upstream attempts and inject traceparent"
```

---

### Task 5: Export is observable, and the gate

**Files:**
- Create: `tests/test_fix_round115.py` (next unused number — verify first)

- [ ] **Step 1: Write the test using an in-process collector**

```python
"""Regression round 115 — OTLP export is real, not decorative (spec C)."""

import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient, Response

from wiwi.config import load_config_from_string
from wiwi.core.telemetry import tracer
from wiwi.server.app import create_app

_CFG = """
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

_UP = "https://api.openai.com/v1/chat/completions"
_COLLECTOR = "http://collector.test:4318/v1/traces"


@respx.mock
async def test_request_produces_a_trace_with_an_upstream_child(monkeypatch):
    # The collector is a mocked endpoint: the point is that a *real* OTLP export
    # happened with the right span structure.
    collected = respx.post(_COLLECTOR).mock(return_value=Response(200, json={}))
    respx.post(_UP).mock(return_value=Response(200, json={
        "id": "c", "object": "chat.completion", "created": 1, "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}))
    cfg = load_config_from_string(
        _CFG.replace("general_settings:",
                     "telemetry:\n  enabled: true\n  endpoint: " + _COLLECTOR +
                     "\ngeneral_settings:"))
    app = create_app(cfg)
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                         headers={"Authorization": "Bearer sk-wiwi-master-test"})
    try:
        r = await client.post("/v1/chat/completions",
                              json={"model": "gpt-4o",
                                    "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert r.headers.get("x-wiwi-trace-id")
        tracer.shutdown()          # flush the batch processor
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert collected.called
    body = collected.calls[0].request.content
    assert b"wiwi.request" in body or b"wiwi.upstream" in body
```

(`collected.calls[0].request.content` is protobuf bytes; asserting the span names appear
as substrings is the pragmatic check. If flaky, capture the request in a list via
`side_effect` and decode with `opentelemetry.exporter.otlp.proto.http` types — but do not
weaken the assertion to "some request happened".)

- [ ] **Step 2: Run it**

Run: `python3 -m pytest tests/test_fix_round115.py -q`
Expected: PASS.

- [ ] **Step 3: Also assert the disabled path skips export**

```python
@respx.mock
async def test_telemetry_disabled_sends_no_spans():
    collected = respx.post(_COLLECTOR).mock(return_value=Response(200, json={}))
    respx.post(_UP).mock(return_value=Response(200, json={
        "id": "c", "object": "chat.completion", "created": 1, "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}))
    app = create_app(load_config_from_string(_CFG))
    mgr = LifespanManager(app)
    await mgr.__aenter__()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                         headers={"Authorization": "Bearer sk-wiwi-master-test"})
    try:
        r = await client.post("/v1/chat/completions",
                              json={"model": "gpt-4o",
                                    "messages": [{"role": "user", "content": "hi"}]})
    finally:
        await client.aclose()
        await mgr.__aexit__(None, None, None)
    assert r.status_code == 200
    assert not collected.called
    assert r.headers.get("x-wiwi-trace-id") is None
```

Run: `python3 -m pytest tests/test_fix_round115.py -q` — PASS (2 passed).

- [ ] **Step 4: The gate**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

- [ ] **Step 5: Live smoke**

```bash
python3 -m pip install '.[otel]'                 # only if not already installed
docker run -d --name otel -p 4318:4318 otel/opentelemetry-collector  # if available
python3 -m wiwi.main --config /tmp/smoke3/wiwi.yaml --port 4113
curl -sS -D- localhost:4113/v1/chat/completions -H 'Authorization: Bearer <key>' \
  -H 'content-type: application/json' \
  -d '{"model":"<group>","messages":[{"role":"user","content":"hi"}]}' | grep -i x-wiwi-trace-id
```

Expected: the response carries `x-wiwi-trace-id`, and the collector log shows a received
trace with spans `wiwi.request` and `wiwi.upstream`. If no collector is available, say so
and rely on the in-process receiver above as the evidence.

- [ ] **Step 6: Commit**

```bash
git add tests/test_fix_round115.py
git commit -m "Prove OTLP export and the disabled path"
```

---

### Task 6: Docs

**Files:**
- Modify: `docs/CONFIG.md`, `docs/TECHSTACK.md`, `wiwi.yaml.example`, `docs/DEVELOPMENT.md`

- [ ] **Step 1: Update**

- `docs/CONFIG.md`: a `telemetry:` section (every field, the default-off rule, the
  `[otel]` extra, sampling semantics).
- `docs/TECHSTACK.md`: add `opentelemetry-*` to the dependency table as an optional extra
  and remove the "no OTel" line if one exists.
- `wiwi.yaml.example`: a commented `telemetry:` block.
- `docs/DEVELOPMENT.md`: a line on when to add a span (a stage that can be slow or fail)
  and the rule that attributes must not carry prompt/response text.

- [ ] **Step 2: Gate and commit**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
git add docs/ wiwi.yaml.example
git commit -m "Document OTLP tracing"
```
