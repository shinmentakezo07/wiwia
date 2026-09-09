# wiwi — Streaming Internals

How streaming works end-to-end: the IR delta taxonomy, the pump, failover/resume, journals, and the guard subsystems (coalescing, loop detection, partial JSON, validation). Every adapter MUST obey the ordering contract here; encoders rely on it and do not defend against malformed sequences.

---

## 1. The contract: IRStreamDelta taxonomy

`wiwi/streaming/deltas.py` defines the event vocabulary pumped between adapters (upstream side) and wire encoders (client side):

| Delta | Fields | Meaning |
|---|---|---|
| `StreamStart` | `model`, `group` | Exactly one, first |
| `TextDelta` | `text` | Text content |
| `ThinkingDelta` | `text`, `signature?` | Reasoning content (signature used for Anthropic thinking blocks) |
| `ToolCallOpen` | `index`, `id`, `name`, `builtin?` | Open a tool call; `builtin` = canonical name from `ir/builtin_tools.py` when it's a provider-hosted tool (e.g. Anthropic `server_tool_use` web_search) |
| `ToolCallArgsDelta` | `index`, `args_fragment` | Partial tool args |
| `ToolCallClose` | `index` | Close the tool call |
| `UsageFinal` | `prompt`, `cached`, `reasoning`, `output`, `cache_creation`, `estimated`, `cost` | Exactly one, after the last content delta |
| `Finish` | `stop_reason` | Exactly one |
| `StreamEnd` | — | Normal terminal |
| `StreamError` | error | Abnormal terminal — may occur at ANY point, no `Finish` required |

**Ordering contract** (adapters guarantee, encoders rely on):

```
StreamStart  (exactly one, first)
  TextDelta* | ThinkingDelta*
  ToolCallOpen → ToolCallArgsDelta* → ToolCallClose   (strictly nested per index)
UsageFinal   (exactly one, after last content delta)
Finish       (exactly one)
StreamEnd xor StreamError
```

All delta variants are `@dataclass(frozen=True)` — adapters hold mutable per-stream decode state on the adapter instance, never on deltas.

---

## 2. The pump (`wiwi/core/gateway.py`)

`Gateway.stream()` executes one IR request through router → adapter → httpx and pumps deltas:

1. `execute_with_retries` picks a deployment/key and connects (connect-phase retries: 429s, 5xx, transient httpx errors → next attempt).
2. The adapter's `decode_stream_event` folds upstream SSE events into IR deltas.
3. `_pump_once()` feeds deltas through the guard chain into an `asyncio.Queue(maxsize=4096)` (backpressure) for the client-facing encoder.
4. TTFT / last-token timing recorded on `RequestContext`; partial billing on mid-stream failure (`_price_partial`); key-cooldown + deployment fail counters on mid-stream death (`_note_stream_failure`).

Client disconnect: the surface sets `ctx.cancel`; the pump notices, releases the upstream connection (grace window `_PUMP_CANCEL_GRACE_S = 1.0` before hard cancel), and stops.

**Tool-call framing in adapters** (`openai_adapter.py` reference): index-tracked `ToolCallOpen`/`Close`; re-open of a same index closes the previous; all parallel tools close at `finish_reason`; `raw_args` preserved on `ToolUsePart`.

---

## 3. StreamTape — mid-stream failover & client resume (`resume.py`)

A bounded ring buffer of emitted deltas with two roles:

1. **Mid-stream failover**: on upstream death after content has flowed, the tape holds the text deltas already emitted so a retry can prepend them as an assistant-prefix **continuation request** (the Anthropic capture-and-resume pattern). `build_continuation_messages()` synthesizes those messages from the tape; the retry lands on a fallback deployment and the client sees one continuous stream.
2. **Last-Event-ID replay**: when a client reconnects with `Last-Event-ID`, the tape re-serves deltas after that point (in-process case).

---

## 4. JournalStore — durable stream journals (`tape_store.py`)

Closes the restart-durability gap: StreamTape is in-process, so a wiwi kill mid-stream left a reconnecting client with no memory of prior content.

- `_stream_response` appends every encoded SSE chunk (post id-injection, base64) to `.wiwi/journals/<request_id>.jsonl`.
- A reconnecting client sends `x-wiwi-stream-id: <request_id>` + `Last-Event-ID: <chunk seq>`; the same surface replays chunks > last_event_id from the journal, then tails live.
- Defaults (journal settings): **enabled by default**, dir `.wiwi/journals`, TTL 600 s, 1 MiB per journal.
- `.wiwi/` is gitignored runtime scratch — never commit it.

Journals survive wiwi restarts; StreamTape handles the common in-process case without disk I/O.

---

## 5. Guard subsystems

All guards sit between the adapter and the client encoder and degrade gracefully (advisory, not fatal) unless stated otherwise.

### Coalescer (`coalesce.py`)
Merges consecutive `TextDelta`s under backpressure. Activates only when queue depth exceeds a threshold; fast consumers see per-token granularity, slow consumers get fewer, larger frames — cutting SSE frame overhead and client parse work.

### Loop detector (`loopdetect.py`)
Aborts a stream when the model starts emitting the same content repeatedly (degenerate loop that would otherwise run to the token limit). Uses an incremental periodicity check — the naive whole-window check was O(n²) per token and was replaced with incremental updates; on detection the stream terminates with a `Finish` (not an error), so clients see a well-formed end.

### Partial JSON (`partial_json.py`)
Ports the Vercel AI SDK partial-json approach for streaming tool-call arguments: clients can render args as they arrive, and **auto-repair truncated JSON** at close time (appends missing `"`, `]`, `}`) instead of dropping args to `{}`.

### Tool-args validation (`validation.py`)
On `ToolCallClose`, validates accumulated args against the tool's JSON schema (`MAX_TOOL_ARGS_BYTES` caps accumulation). Violations are logged via structlog and attached to request metadata (`tool_args_violations`) — the client still receives the tool call, flagged as advisory.

---

## 6. Wire encoders (client side)

Each dialect owns a `StreamEncoder` in `wiwi/wire/`:

| Encoder | Surface | Emits |
|---|---|---|
| `ChatStreamEncoder` | `/v1/chat/completions` | OpenAI chunk objects (`chat.completion.chunk`) |
| `ResponsesStreamEncoder` | `/v1/responses` | Responses SSE events (`response.output_text.delta`, …) |
| `AnthropicStreamEncoder` | `/v1/messages` | `message_start`, `content_block_*`, `message_delta`, `message_stop`, `ping` |

Encoders consume the delta taxonomy in order and do NOT defend against malformed sequences — legality is the adapters' guarantee. SSE framing (BOM, CRLF, `:` heartbeat comments, multi-line `data:` joining) is handled by `LineSSEParser` upstream; outbound framing helpers live in `sse.py`.

---

## 7. SSE parse/encode (`sse.py`)

- **Upstream**: incremental `LineSSEParser` → `SSEEvent` (handles BOM, CRLF, comment heartbeats, multi-line data).
- **Downstream**: frame-writing helpers used by encoders and the journal store.

---

## 8. Recovery flows (summary)

| Failure | Mechanism | Client-visible result |
|---|---|---|
| Connect fails (429/5xx) | `execute_with_retries` → next attempt/deployment | Delayed first token only |
| Death before first token | Failover, fresh request | Seamless |
| Death mid-stream, content flowed | StreamTape continuation on fallback deployment | One continuous stream |
| Client disconnect + reconnect | `x-wiwi-stream-id` + `Last-Event-ID` → tape/journal replay | Missed frames replayed, then live tail |
| wiwi restart mid-stream | Journal replay from disk | Same as above |
| Degenerate repetition | Loop detector terminates | Well-formed `Finish` early |

`Backoff`, `CircuitBreaker`, and the opt-in `HealthHealer` (1-token probes, graduated probation recovery) live in `wiwi/core/recovery.py` and feed the router — see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Historical note

`docs/STREAMING_PERFORMANCE_RECOVERY.md` is the 2026-08-23 improvement report whose P0/P1/P2 items (tape, resume, journals, coalescer, loop detector, partial JSON, validation) are all **implemented** — this page is the live description of the shipped system; the report is kept as historical record.
