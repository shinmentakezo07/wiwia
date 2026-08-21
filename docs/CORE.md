# wiwi — Core Runtime: Handlers, Subsystems, Streaming Flow

This doc specifies the internal runtime organization referenced by `ARCHITECTURE.md`: one shared **RequestContext** (the holder), an ordered **handler pipeline** where every cross-cutting concern is a pluggable handler, and four strictly separated subsystems — **logging**, **reasoning**, **cache**, **streaming** — each in its own package with its own public interface. Nothing in core branches on provider names or dialects; all specialization lives in codecs/adapters/handlers.

---

## 1. Module map

```
wiwi/
├── core/
│   ├── context.py        # RequestContext — the single holder passed through everything
│   ├── handlers/
│   │   ├── base.py       # Handler protocol, HandlerResult, registry, ordering
│   │   ├── auth.py       # key validation, budget pre-check        (order 10)
│   │   ├── ratelimit.py  # rpm/tpm admission                      (order 20)
│   │   ├── cache.py      # response-cache lookup/store            (order 30)
│   │   ├── reasoning.py  # request-side reasoning transforms      (order 40)
│   │   ├── logging.py    # observes deltas, emits LogEvents       (order 90)
│   │   └── metrics.py    # tps/ttft/in-flight gauges              (order 95)
│   ├── bus.py            # DeltaBus — ordered fan-out of stream deltas
├── logging_core/         # SUBSYSTEM: all logics for logging (no business code here)
│   ├── events.py         # LogEvent types (request | proxy | audit)
│   ├── queue.py          # bounded async queue + spill-to-disk
│   ├── worker.py         # batch writer loop, graceful drain
│   ├── sinks.py          # DBSink · SSEBroadcastSink · StdoutJSONSink
│   └── redact.py         # secret redaction before any sink
├── reasoning/            # SUBSYSTEM: all logics for thinking/reasoning
│   ├── parts.py          # ThinkingPart/ThinkingDelta semantics, signature handling
│   ├── policy.py         # pass_through | strip | strip_warn | error  (per route)
│   ├── translate.py      # cross-dialect rules (anthropic ↔ responses ↔ chat)
│   └── tokens.py         # reported vs estimated reasoning-token accounting
├── cache/                # SUBSYSTEM: all logics for caching
│   ├── keygen.py         # normalized request hashing
│   ├── response_cache.py # exact-match store (memory/redis backends)
│   ├── prompt_stats.py   # provider prompt-cache accounting + $ savings
│   └── interface.py      # CacheBackend protocol (semantic cache plugs in later)
├── streaming/            # SUBSYSTEM: all logics for delta translation
│   ├── deltas.py         # IRStreamDelta taxonomy (single source of truth)
│   ├── fsm_chat.py       # chat.completion.chunk encoder state machine
│   ├── fsm_anthropic.py  # message/content_block encoder state machine
│   ├── fsm_responses.py  # response.output_* encoder state machine
│   └── sse.py            # incremental parser + frame writer + heartbeats
```

Dependency rule: `handlers/*` may import subsystems; subsystems never import each other or handlers. `streaming/deltas.py` is imported by everyone but imports nothing internal.

## 2. RequestContext — the holder

One mutable dataclass created at request start, passed to every handler, the router, and the stream loop. It is the only shared state; handlers communicate exclusively through it.

```python
@dataclass
class RequestContext:
    # identity
    request_id: str; started: float
    surface: Literal["chat", "responses", "messages"]
    auth: AuthInfo | None            # virtual key row, master?, team, budget snapshot
    # payloads
    ir_req: IRRequest                # decoded by wire codec before handlers run
    raw_body_bytes: int
    # routing (filled by router)
    group: str | None
    deployment: Deployment | None
    provider_key: ProviderKeyRef | None
    attempts: list[AttemptRecord]    # every try: dep, key, status, latency
    # stream state (filled by streaming subsystem)
    first_token_at: float | None     # TTFT
    last_token_at: float | None
    deltas_seen: DeltaCounters       # text/thinking/tool counts
    usage: IRUsage | None            # finalized at UsageFinal
    # outcomes
    cache_hit: bool = False
    stop_reason: str | None = None
    error: WiwiError | None = None
    log_buffer: list[LogEvent]       # handler-emitted events, drained by logging
    cancel: asyncio.Event            # client disconnected
```

Rules: handlers may write only their own namespaced fields; nothing reads `usage` until `UsageFinal`; `log_buffer` is append-only and flushed exactly once at completion.

## 3. Handler pipeline

Every cross-cutting concern is a handler with fixed hook points. The router/provider call itself is the *executor*, not a handler — handlers wrap it.

```python
class Handler(Protocol):
    name: str
    order: int                                   # ascending execution order
    async def on_request(self, ctx) -> HandlerResult: ...   # may short-circuit
    def on_selected(self, ctx, dep, key) -> None: ...       # router picked targets
    def on_delta(self, ctx, d: IRStreamDelta) -> None: ...  # hot path: sync, fast
    async def on_complete(self, ctx) -> None: ...           # after last byte (bg ok)
    async def on_error(self, ctx, err: WiwiError) -> None: ...
```

`HandlerResult` is `continue` | `reject(WiwiError)` | `serve_now(response)` (only the cache handler may serve immediately).

| Order | Handler | Responsibility |
|---|---|---|
| 10 | auth | bearer/x-api-key resolve, expiry/budget/model checks → 401/403 |
| 20 | ratelimit | global/team/key rpm+tpm admission → 429 + Retry-After |
| 30 | cache | exact-match lookup → `serve_now`; registers store hook |
| 40 | reasoning | applies request-side policy (strip thinking params, inject defaults) |
| 90 | logging | subscribes to deltas, assembles the future request_logs row |
| 95 | metrics | TTFT/TPS counters, in-flight gauge |

Execution shapes:

- **Non-streaming**: `on_request` chain → router picks dep+key (`on_selected`) → adapter encode → httpx → adapter decode → IRResponse → wire encode → `on_complete`.
- **Streaming**: same until upstream connects; then the pump loop pulls provider events → IR deltas → `bus.publish(delta)` which calls every subscriber: handlers' `on_delta` (sync) + the wire encoder FSM. After `StreamEnd`: `on_complete` runs as background tasks.
- **Error anywhere**: first `on_error` in reverse order, then `on_complete` (so logs/metrics always finalize).
- **Client disconnect**: `ctx.cancel` set → pump cancels upstream httpx call → estimated usage finalized → normal completion path.

Adding a concern (e.g., guardrails later) = new handler file + order number. No core edits.

## 4. Logging subsystem (separate logics)

Three streams that never mix, each with its own event type, queue policy, and sinks:

| Stream | Event content | Queue policy | Sinks |
|---|---|---|---|
| **request logs** | one per LLM call: ids, surface, key, model, dep+pool-key used, status, tok_in/cached/reasoning/out, tps, ttft, latency, cost, retry chain | bounded 50k, **never dropped** (spill to disk file if DB slow) | DBSink (batched up to 200 rows / 500ms), SSEBroadcastSink (`log.created`) |
| **proxy logs** | gateway ops: config reloads, key cooldown/invalid, retries, fallback switches, upstream 5xx, startup/shutdown | bounded 10k, drop-oldest allowed | StdoutJSONSink, SSEBroadcastSink (`proxy.log`), optional DBSink |
| **audit logs** | admin mutations: actor, action, target, diff | synchronous write (must succeed) | DBSink (`admin_audit`) |

Flow: handlers/executor append `LogEvent`s to `ctx.log_buffer` (and proxy events go straight to the proxy queue) → completion flushes buffer into the request queue → single `LogWorker` task batches to sinks → `redact.py` scrubs anything matching secret patterns before any sink sees bytes.

Guarantees: no sink sits in the response path; a dead DB degrades to spill-file + stdout; SSE fan-out is best-effort with a 500-event ring buffer per admin client and `Last-Event-ID` replay.

Retention: raw request logs default 30d (config), rollups forever; retention sweeper runs hourly inside the worker.

## 5. Reasoning subsystem (separate logics)

Owns everything "thinking". Adapters emit `ThinkingPart`/`ThinkingDelta` verbatim (including `signature` / `encrypted_content` blobs) and defer all decisions to here.

- **policy.py** — per-route config: `reasoning_policy: pass_through | strip | strip_warn | error`. Applied twice: request-side (handler 40 rewrites inbound params, e.g., drops `reasoning_effort` for a backend that can't honor it) and response-side (translate.py filters outbound parts).
- **translate.py** — the cross-dialect truth table:

| From \ To | Chat | Responses | Messages |
|---|---|---|---|
| Chat (`reasoning_effort` param only) | — | effort → `reasoning.effort` | effort → thinking budget hint (if enabled) |
| Responses (`reasoning` items, encrypted) | drop + warn (content unrecoverable) | round-trip verbatim (`store:false`) | decrypt impossible → drop + warn |
| Messages (`thinking` blocks + signature) | drop + warn | map to reasoning item if target is OpenAI reasoning model, else drop + warn | round-trip verbatim (signature validated upstream) |

  Every lossy hop increments `ctx.deltas_seen.reasoning_dropped` and emits one proxy-log warn per request (not per delta).
- **tokens.py** — usage rule: provider-reported reasoning tokens win (OpenAI `completion_tokens_details.reasoning_tokens`, Gemini `thoughtsTokenCount`); Anthropic reports none → estimate `sum(len(thinking_text)) // 4`, flag `estimated=true` on the log row. Feeds `request_logs.tok_reasoning` and hourly rollups.

## 6. Cache subsystem (separate logics)

Two unrelated concerns that both say "cache", kept in separate modules:

1. **prompt_stats.py — provider prompt-cache accounting (always on, zero behavior change).**
   Reads cached-token fields from provider usage (OpenAI `cached_tokens`, Anthropic `cache_read_input_tokens` + `cache_creation_input_tokens`, Gemini `cachedContentTokenCount`), computes dollar savings at the model's input rate, writes `tok_cached` columns + feeds the dashboard "cache savings" card. Pure observation.

2. **response_cache.py — wiwi's own exact-match response cache (opt-in).**
   - Key: SHA-256 over normalized IR (model group, messages, tools, tool_choice, sampling params, response_format) — normalized so dialect differences that decode to identical IR share entries.
   - Scope: enabled per model group and/or per virtual key in config/admin UI; TTL default 1h.
   - Semantics: non-streaming GET-style requests only in v1 (stream replay post-MVP); `cache_hit=true` logged; `Cache-Control`-style bypass header honored (`X-Wiwi-No-Cache: true`).
   - Backend: memory LRU default, Redis when multi-instance; both implement `interface.CacheBackend` so a semantic (embedding-similarity) backend can plug in later without touching handlers.

Handler 30 wires both: lookup → `serve_now` on hit; on miss, registers a completion hook to store.

## 7. Streaming delta chunk flow

### 7.1 The delta taxonomy (`streaming/deltas.py`)

```python
StreamStart(request_id, model, group)
TextDelta(text: str)
ThinkingDelta(text: str, signature: str | None)
ToolCallOpen(index: int, id: str, name: str)
ToolCallArgsDelta(index: int, args_fragment: str)     # partial JSON string
ToolCallClose(index: int)
UsageFinal(prompt: int, cached: int, reasoning: int,
           output: int, estimated: bool, cost: float)
Finish(stop_reason: StopReason)
StreamEnd()          # success terminator
StreamError(err)     # failure terminator (mutually exclusive with StreamEnd)
```

Contract: exactly one `StreamStart` first; `ToolCallOpen → ArgsDelta* → ToolCallClose` strictly nested per index; `UsageFinal` exactly once, always after the last content delta; then `Finish`; then `StreamEnd`/`StreamError`. Adapters guarantee this regardless of provider quirks — encoders downstream never defend against malformed sequences.

### 7.2 End-to-end flow

```
provider HTTP/SSE bytes
      │  streaming/sse.py — incremental parser (partial-buffer safe, heartbeats filtered)
      ▼
provider-native events
      │  providers/<x>/adapter.decode_stream_event()
      ▼
IRStreamDelta sequence  ──►  core/bus.DeltaBus.publish()  (ordered, no await in fan-out)
      │
      ├─► handlers' on_delta (logging assembles usage row; metrics computes TTFT/TPS)
      │      TTFT  = ts(first TextDelta|ToolCallOpen|ThinkingDelta) − started
      │      TPS   = usage.output ÷ (last_token_at − first_token_at)
      └─► wire encoder FSM for the INBOUND surface (fsm_chat | fsm_anthropic | fsm_responses)
             │  maintains block/index/id bookkeeping, emits legal event sequences
             ▼
        client SSE frames in the caller's dialect
```

Encoder FSM responsibilities per dialect:

| FSM | Must synthesize | Bookkeeping |
|---|---|---|
| `fsm_chat` | `chunk` shells per delta; `delta.tool_calls[i].function.arguments` fragments; final chunk carries `usage`; `[DONE]` | tool-call index reuse across provider formats |
| `fsm_anthropic` | `message_start` (with input usage), `content_block_start/stop` around every part, `input_json_delta` for tool args, `message_delta` (stop_reason, output usage), `ping` heartbeats, `event: error` on failure | content-block index counter; open-block tracking |
| `fsm_responses` | `response.created`, `output_item.added/done` per part, `output_text.delta`, `function_call_arguments.delta`, reasoning deltas, `response.completed` / `response.failed` | output_item id/sequence numbers |

Mid-stream failure mapping: `StreamError` becomes `[DONE]`-after-error-frame (chat), `event: error` + `message_stop` suppression (messages), `response.failed` (responses). Client disconnect cancels upstream, synthesizes estimated `UsageFinal` + `Finish(length)` internally so logs stay complete, but sends nothing further.

### 7.3 Worked example — Claude upstream, OpenAI-chat client

```
Anthropic SSE                          IR delta                 chat.completion.chunk
message_start                          StreamStart              (first empty chunk w/ role=assistant)
content_block_start(text)              —                        —
content_block_delta(text_delta,"Hel")  TextDelta("Hel")         choices[0].delta.content="Hel"
content_block_start(tool_use,#3,get_w) ToolCallOpen(0,…,"get_w")delta.tool_calls[0]={id,name}
content_block_delta(input_json_delta,  ToolCallArgsDelta(0,     delta.tool_calls[0].function
  '{"location":"Par"}')                  '{"location":"Par"}')    .arguments+='{"location":"Par"'
content_block_stop                     ToolCallClose(0)         —
message_delta(stop_reason=tool_use,    Finish(tool_call)        finish_reason="tool_calls"
  output_tokens=42) + UsageFinal                                final chunk: usage{…}
message_stop                           StreamEnd                [DONE]
```

The reverse direction (chat client ← OpenAI upstream already shown above) and the other eight cells of the 3×3 matrix are exercised by golden fixtures; this example is the canonical reference implementation test.

### 7.4 Testing the flow

- Unit: each FSM fed a scripted delta list asserts exact emitted frames (snapshot tests).
- Property: random valid delta sequences (hypothesis generator respecting §7.1 contract) survive decode→encode→parse round-trips for all three FSMs.
- Golden: real captures (Codex, Claude Code, OpenAI SDK) replayed through the full pump with respx.
- Chaos: injected `StreamError` at every position k of a fixture stream; assert clients receive legal terminations and logs still get complete rows.
