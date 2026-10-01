# Telemetry — OTLP trace export

**Date:** 2026-10-01
**Status:** Accepted — user approved 2026-10-01
**Sub-project:** C of five (A: `/v1/completions` · B: stateful Responses · **C: telemetry** ·
D: router shedding/lanes/affinity · E: Realtime WebSocket proxy)

## Goal

Export a real distributed trace per request over OTLP/HTTP — one span per request with
child spans per pipeline stage and per upstream attempt — with W3C `traceparent`
propagation in both directions, disabled by default and free when disabled.

## Why (context)

`wiwi/server/metrics.py` is a hand-rolled Prometheus text exposition: counters and
histograms, no notion of a request's *shape over time*. When a request is slow the
operator sees `p95` rise and can correlate nothing — which of the 3 stages (auth, route,
upstream), which deployment, which retry burned the time. `AUDIT`/`docs` never planned
tracing; the missing piece is enumerated in the design inventory as "OpenTelemetry / trace
export — 0 hits".

The data is already there and already computed: `RequestContext` carries `started`,
`first_token_at`, the attempt list, usage and cost; `LogEvent` serializes it. Tracing is
therefore an *encoder*, not new instrumentation.

**Rejected: auto-instrumentation** (`opentelemetry-instrumentation-fastapi` +
`-httpx`). It knows nothing about deployments, keys, retries or dialects, so it would emit
one opaque HTTP span per hop and a FastAPI span that flattens exactly the structure worth
having. It also installs middleware of its own, conflicting with this repo's
no-`BaseHTTPMiddleware` stance (`AUDIT #154` context). ~40 lines of explicit spans beat a
framework that models the wrong thing.

## Design

### 1. Dependency and config

`pyproject.toml` — a new optional extra; the gateway imports neither without it:

```toml
[project.optional-dependencies]
otel = [
  "opentelemetry-api>=1.20",
  "opentelemetry-sdk>=1.20",
  "opentelemetry-exporter-otlp-proto-http>=1.20",
]
```

`wiwi/config.py` — a new top-level section (nested model, like `CacheSettings`):

```python
class TelemetrySettings(BaseModel):
    enabled: bool = False          # off unless a collector is configured
    endpoint: str = ""             # e.g. http://localhost:4318/v1/traces
    service_name: str = "wiwi"
    sample_ratio: float = 1.0      # 0..1; ParentBased(ALWAYS_ON) sampler at 1.0
    headers: dict[str, str] = Field(default_factory=dict)
```

`WiwiConfig` gains `telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)`.

### 2. `wiwi/core/telemetry.py` (new)

One module, two halves: a **no-op facade** (always importable, zero deps) and a **lazy
SDK bridge** imported only when `enabled`.

```python
class _NoopSpan:                       # returned when disabled or SDK absent
    def set_attribute(self, *a, **k) -> None: ...
    def set_status(...) -> None: ...
    def record_exception(self, *a, **k) -> None: ...
    def end(self) -> None: ...
    def __enter__(self): return self
    def __exit__(self, *exc) -> bool: return False

class Tracer:                          # module singleton
    def configure(self, settings: TelemetrySettings) -> None
    def span(self, name, kind=None, **attrs) -> AbstractContextManager
    def current_traceparent(self) -> str | None      # for outbound injection
    def extract(self, headers: Mapping[str, str]) -> None   # from the inbound request
    def shutdown(self) -> None
```

- `configure()` imports the SDK inside the `try`; when `enabled` is false **or** the
  import fails (extra not installed) it logs once at `info`/`warning` and stays no-op.
  This is the whole reason for the facade: a deployment that does not install `[otel]`
  pays one boolean test per span and never touches the SDK.
- `span()` uses `trace.get_tracer` + `start_as_current_span` when active, else the no-op.
- Sampling: `ParentBased(TraceIdRatioBased(sample_ratio))` — a caller's sampling decision
  is honoured, wiwi only decides for roots.
- Exporter: `OTLPSpanExporter(endpoint=…, headers=…)` + `BatchSpanProcessor`, so export
  never blocks the request path; `shutdown()` flushes (wired into the lifespan).

### 3. Span placement (five seams, all existing)

| Span | Where | Attributes |
|---|---|---|
| `wiwi.request` | `run_chat_like`, wrapping the whole call | `surface`, `model`, `stream`, `wiwi.request_id`, `key_id`, `status` |
| `wiwi.retrieve` | the `previous_response_id` block (spec B) | `previous_response_id`, `found` |
| `wiwi.upstream` | per attempt in `Gateway.complete` / `.stream` | `deployment`, `provider_type`, `model_id`, `status`, `latency_ms`, `attempt` |
| `wiwi.stream` | the streaming pump in `_stream_response` | `ttft_ms`, `chunks`, `errored` |
| `wiwi.persist` | the Responses store write | `stored`, `store_id` |

Root `wiwi.request` is opened **once** per request; children use the current context, so
they nest automatically. Attributes come from fields that already exist — no new
measurement is added to the hot path.

### 4. Propagation

- **Inbound:** at the top of `run_chat_like`, `Tracer.extract(request.headers)`; with no
  `traceparent` the SDK generates a root id (we do not invent one).
- **Outbound:** in `Gateway._headers`, append `traceparent` from
  `Tracer.current_traceparent()` when tracing is active. Doing it there rather than in the
  HTTP client means it is per-attempt and uses the attempt's span as parent — a retry is a
  sibling span, not a duplicate.
- **Response:** `x-wiwi-trace-id: <32-hex trace id>` on success and error responses,
  beside the existing `x-wiwi-request-id`, so a caller can quote it to the operator.

### 5. What this does **not** do

- No metrics change: the Prometheus exposition stays. Traces are a second, optional
  sink — a collector outage must never affect serving.
- No PII: prompt/response text is never an attribute (`store_prompts_in_spend_logs` stays
  the only switch that captures content, and it goes to the DB, not the collector).
- No auto-instrumentation, no log correlation.

## Testing

1. **Disabled is no-op**: with `enabled: false` (and with the SDK import forced to fail via
   `monkeypatch`), `Tracer.span()` yields a working context manager and `configure()` does
   not raise. The gateway must run unchanged.
2. **Spans are exported**: point the exporter at a real in-process HTTP receiver (a tiny
   ASGI app) or use `InMemorySpanExporter` via a test-only injection hook; assert one
   `wiwi.request` root with an `wiwi.upstream` child carrying the deployment/status, for
   both a successful and a failed attempt.
3. **Propagation**: an inbound `traceparent` continues the caller's trace id (assert the
   exported root's trace id equals the inbound one); outbound request headers carry a
   `traceparent` whose parent is the attempt span; the response carries `x-wiwi-trace-id`.
4. Full gate + a live smoke against a real collector if one is reachable, otherwise the
   in-process receiver *is* the evidence — stated as such, not claimed as a collector run.

## Non-goals

- Trace-level dashboards in the SPA.
- OTLP/gRPC (HTTP/protobuf only).
- Instrumenting the admin/auth surfaces beyond the request span.
