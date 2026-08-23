# Streaming Performance, Quality & Recovery — Improvement Report

> **Scope:** Delta chunk streaming, tool-call handling, and recovery logic for the wiwi gateway.
> **Method:** Full codebase read of `wiwi/streaming/`, `wiwi/core/gateway.py`, `wiwi/router/router.py`,
> `wiwi/providers/*`, `wiwi/ir/types.py`, plus external research (LiteLLM streaming docs,
> Vercel AI SDK stream-text/partial-JSON, Anthropic streaming error-recovery guide).
> **Date:** 2026-08-23
> **Status:** Proposal — nothing here is implemented yet.

---

## 1. Current State (verified from code)

| Area | Current implementation | Location |
|---|---|---|
| **Delta taxonomy** | Clean IR delta contract: `StreamStart → TextDelta / ThinkingDelta / ToolCallOpen → ToolCallArgsDelta* → ToolCallClose → UsageFinal → Finish → StreamEnd \| StreamError`. Ordering contract documented and enforced by adapters. | `wiwi/streaming/deltas.py` |
| **SSE parsing** | Incremental `LineSSEParser`; handles BOM, CRLF, `:` comment heartbeats; multi-line `data:` joining. | `wiwi/streaming/sse.py` |
| **Stream pump** | `asyncio.Queue(maxsize=4096)` backpressure; connect-phase retry via `execute_with_retries`; TTFT / last-token timing; partial billing on mid-stream failure (`_price_partial`); key-cooldown + deployment fail counters on mid-stream death (`_note_stream_failure`). | `wiwi/core/gateway.py` `_pump_once()` |
| **Tool call deltas** | Index-tracked `ToolCallOpen/Close`, re-open on same index closes previous, closes all parallel tools at `finish_reason`. `raw_args` preserved on `ToolUsePart`. | `providers/openai_adapter.py:238–255`, `ir/types.py:34–38` |
| **Error normalization** | `WiwiError` taxonomy + `_extract_error_message` drilling into OpenAI/OpenRouter/Anthropic nested shapes; retryable-status set `{408,429,500,502,503,504,529}`; auth failures stay retryable so the pool fails over. | `providers/base.py` |
| **Retry/fallback** | Smooth weighted round-robin key pools (nginx algorithm), cooldowns, exponential backoff + jitter, fallback group walk. Retry works **only before first upstream byte**. | `router/router.py` `execute_with_retries` |
| **Usage estimation** | `estimate_tokens` chars÷4 fallback when provider omits usage; `estimated=True` flag propagated to cost engine and logs. | `core/gateway.py` `_price_partial`, `_pump_once` |

**Assessment:** the hub-and-spoke architecture and delta ordering contract are solid.
All gaps below are in what happens *after* the first delta flows — exactly the areas
the research targets (LiteLLM loop detection, Vercel AI SDK partial JSON, Anthropic
capture-and-resume).

---

## 2. Identified Gaps

1. **Mid-stream errors are terminal.** Once a single delta reached the client, any
   upstream failure ends the stream as `StreamError` ("can't retry"). No failover,
   no resume.
2. **No incremental tool-args parsing.** Fragments pass through raw; non-streaming
   path does `json.loads(raw_args) → {}` on failure (`openai_adapter.py:186–189`) —
   silent tool-call data loss on truncated JSON.
3. **Hung upstream stalls clients for up to 120 s.** No per-chunk idle watchdog.
4. **`json.loads` in the hottest loop.** `decode_stream_event` parses every streamed
   chunk with stdlib json despite orjson being a dependency.
5. **Per-process state breaks multi-worker.** Key cooldowns, WRR counters, rate
   limits are in-memory only (`ratelimit/memory.py`); `--workers > 1` silently
   breaks fairness. Redis extra declared but unused.
6. **Client disconnect cancels immediately** — no grace drain for accurate billing
   or response caching.
7. **No model-loop detection** — a stuck repeating model burns budget until max_tokens.
8. **Token estimates are crude** (chars÷4) wherever usage is missing.
9. **Some providers fragment tool-call `name` across chunks** — not accumulated.
10. **No resumable client streams** (SSE event ids / Last-Event-ID).

---

## 3. Recommendations (prioritized)

### P0 — high impact, low risk

#### 1. `wiwi/streaming/partial_json.py` — incremental tool-args parser *(quality)*

Port of Vercel AI SDK's `partial-json` approach: parse **incomplete** JSON streams so
clients can render tool arguments as they arrive, and **auto-repair truncated JSON**
at close time (append missing `"`, `]`, `}`) instead of dropping args to `{}`.

```python
# sketch
def parse_partial(fragment: str) -> tuple[Any, bool]:
    """Return (value, complete). Never raises on truncation."""
```

Used by:
- wire encoders that expose structured partial tool calls downstream;
- `decode_response` fallback: repair before giving up;
- `ToolCallClose` validation hook (P2 #10).

#### 2. Mid-stream transparent failover *(recovery — biggest gap)*

New module `wiwi/streaming/resume.py` with a `StreamTape`:

- ring-buffer every delta emitted to the client (bounded bytes, e.g. 256 KB);
- on mid-stream failure:
  - **before first content delta** → already retried at connect phase; extend to
    also cover the "connected but zero deltas" case;
  - **after content** → retry on a fallback deployment, prepend buffered text as an
    assistant-prefix continuation request (Anthropic capture-and-resume pattern),
    stitch the two streams seamlessly behind the same client SSE connection;
- gated by config (`stream_resume: enabled | content_only | off`) since stitched
  output may mix providers.

#### 3. Idle-stream watchdog *(performance/recovery)*

Per-chunk idle timeout instead of relying on the global 120 s deadline:

```python
async with asyncio.timeout(idle_s):
    line = await resp.aiter_lines().__anext__()
```

Emit `StreamError(kind="timeout")` fast (default ~30 s between chunks, configurable
per provider via `ProviderAccount`). Feed `_note_stream_failure` so repeat offenders
cool off.

#### 4. orjson in the hot path *(performance)*

Replace `json.loads` with `orjson.loads` in every adapter `decode_stream_event` /
`decode_response` and codec hot path. `orjson.JSONDecodeError` subclasses
`json.JSONDecodeError`, so existing except-clauses keep working. Expect 20–40 %
parse win on the per-token loop.

### P1 — strong value, moderate effort

#### 5. Resumable client streams (SSE `id:` + `Last-Event-ID`) *(quality)*

Extend `sse_frame()` to attach monotonic event ids. On reconnect with
`Last-Event-ID`, replay missed deltas from the `StreamTape` (#2). Standard SSE
semantics; big win for flaky mobile clients. Requires tape lifetime of a few
minutes per request id.

#### 6. Delta coalescer under backpressure *(performance)*

`Queue.put` blocking on full gives correct backpressure, but slow clients then
receive thousands of micro-frames. Add a coalescing stage in the pump→queue path:

- merge consecutive `TextDelta`s while queue depth > threshold, up to N bytes or M ms;
- window collapses to zero for fast consumers (no behavior change);
- never coalesce across `ToolCall*` / control deltas (ordering contract).

#### 7. Model-loop detection *(quality/cost)*

LiteLLM's approach: if identical chunk content repeats N times (default 100),
abort with `StreamError` + `_note_stream_failure`. Prevents runaway spend from
degenerate repeats. ~15 lines in the pump.

#### 8. Accurate token estimation *(quality)*

Optional lazy dependency (`tiktoken` or HF `tokenizers`) used by `estimate_tokens`
per model family; keeps chars÷4 as final fallback. Improves cost accuracy,
context-window guardrails, and estimated billing when usage is absent.

#### 9. Shared state via Redis *(scale/correctness)*

Backend seam for: provider-key cooldown/WRR state, rate limits, deployment health.
`pyproject.toml` already declares `redis = ["redis>=5.0"]` — wire an async Redis
implementation behind the existing interfaces so multi-worker deployments share
cooldowns and limits. In-memory stays default for single-process dev.

### P2 — nice to have

10. **Schema validation on `ToolCallClose`** — validate accumulated args against
    `Tool.parameters_json_schema`; log violations via structlog and attach a warning
    to request metadata rather than failing the stream.
11. **Client-gone grace drain** — on disconnect, optionally keep pumping upstream X s
    to finish billing precisely and cache the completed response.
12. **OTel spans / Prometheus metrics** — ttft/tps/latency already computed in
    `build_log_event`; export them as metrics for dashboards.
13. **HTTP/2 upstream pooling** — `httpx[http2]` is installed but the client at
    `gateway.py:33` doesn't pass `http2=True`; enabling improves connection reuse
    against h2-capable upstreams (Anthropic, Google).
14. **Split tool-call names** — accumulate `function.name` fragments in decoder state
    (some OpenAI-compatible providers fragment the name across chunks), mirroring how
    args fragments already accumulate.

---

## 4. Recommended Build Order

```
Phase 1 (quick wins):   #4 orjson · #3 idle watchdog · #7 loop detection
Phase 2 (quality):      #1 partial JSON · #8 tokenizer · #14 split names
Phase 3 (resilience):   #2 StreamTape + mid-stream failover · #5 Last-Event-ID
Phase 4 (scale):        #9 Redis shared state · #6 coalescer
```

Each phase is independently shippable.

## 5. Constraints & Testing Notes

- All new streaming logic lives under `wiwi/streaming/` — no dialect branches in
  `core/`, `router/`, `auth/` (repo rule).
- Keep deltas as frozen dataclasses (hot path); orjson only at parse boundaries.
- Tests follow thematic regression convention: new file
  `tests/test_stream_recovery.py` covering idle-timeout, loop detection, tape
  replay, partial-JSON repair; use `respx` for upstream mocking and
  `.verify/fake_upstream.py` patterns for SSE fixtures.
- Gate before commit: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check wiwi/ tests/`.
- Config additions go in `RouterSettings`/provider settings (Pydantic v2) with
  safe defaults matching current behavior (resume off, idle timeout generous).

## 6. Research References

- **LiteLLM — Streaming + Async:** repeated-chunk loop detection
  (`REPEATED_STREAMING_CHUNK_LIMIT`), `stream_chunk_builder` reconstruction helper.
  https://docs.litellm.ai/docs/completion/stream
- **Vercel AI SDK — streamText:** typed stream parts, per-step performance metrics
  (TTFT, output tokens/sec), partial tool-call argument streaming.
  https://sdk.vercel.ai/docs/ai-sdk-core/stream-text
- **Anthropic — Streaming messages:** official error-recovery guidance — capture
  partial response, construct continuation request, resume streaming; fine-grained
  tool-input JSON streaming without server-side buffering.
  https://docs.anthropic.com/en/docs/build-with-claude/streaming

