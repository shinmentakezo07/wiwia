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
| `ServerToolResultDelta` | `index`, `block`, `builtin?` | A provider-executed tool's result block, carried verbatim (e.g. Anthropic `web_search_tool_result`). Whole block, not Open/Args/Close — delivered whole upstream. Encoders for dialects with no equivalent block drop it. |
| `UsageFinal` | `prompt`, `cached`, `reasoning`, `output`, `cache_creation`, `estimated`, `cost` | At least one; the last one wins |
| `Finish` | `stop_reason` | Exactly one |
| `StreamEnd` | — | Normal terminal |
| `StreamError` | `message`, `kind?`, `status?`, `etype?` | Abnormal terminal — may occur at ANY point, no `Finish` required |

**Ordering contract** (adapters guarantee, encoders rely on):

```
StreamStart  (exactly one, first)
  TextDelta* | ThinkingDelta*
  ToolCallOpen → ToolCallArgsDelta* → ToolCallClose   (per index, one pair per call; parallel calls are SIBLINGS)
UsageFinal   (at least one; the LAST one wins)
Finish       (exactly one)
StreamEnd xor StreamError
```

**Where the code is looser than the shape above, and why it is safe.** The
diagram is the contract the *encoders* rely on; the adapters satisfy it with
help from the gateway and the encoders' buffering:

- **`ToolCallClose` is per index, not strictly nested.** Parallel tool calls are
  siblings: `openai_adapter` closes every open index in a loop at
  `finish_reason`, and Gemini emits Open/Args/Close as a batch per
  `functionCall` part. Encoders therefore do not assume nesting.
- **`UsageFinal` may arrive more than once, and before the last content delta.**
  OpenAI, NIM and Gemini all attach usage to *intermediate* chunks (Gemini
  attaches `usageMetadata` to every chunk). All three encoders buffer it
  last-write-wins and emit once from their terminal frame, so the client sees a
  single usage figure.
- **`StreamError` may terminate at any point** — it is the abnormal-path
  terminal and needs no preceding `Finish`.

All delta variants are `@dataclass(frozen=True)` — adapters hold mutable per-stream decode state on the adapter instance, never on deltas.

---

## 2. The pump (`wiwi/core/gateway.py`)

`Gateway.stream()` executes one IR request through router → adapter → httpx and pumps deltas:

1. `execute_with_retries` picks a deployment/key and connects (connect-phase retries: 429s, 5xx, transient httpx errors → next attempt).
2. The adapter's `decode_stream_event` folds upstream SSE events into IR deltas.
3. `_pump_once()` feeds deltas through the guard chain into an `asyncio.Queue(maxsize=4096)` (backpressure) for the client-facing encoder.
4. TTFT / last-token timing recorded on `RequestContext`; partial billing on mid-stream failure (`_price_partial`); key-cooldown + deployment fail counters on mid-stream death (`_note_stream_failure`).

Client disconnect: the surface sets `ctx.cancel`; the pump notices, releases the upstream connection (grace window `_PUMP_CANCEL_GRACE_S = 1.0` before hard cancel), and stops.

**What the TPS window measures** (`build_log_event`): `completion_tokens / (last_token_at - first_token_at)`, i.e. the *generation phase only* — queueing and prefill are excluded, which is why it is not a request-level average. `first_token_at` is stamped on the first text, thinking or tool-call delta; `last_token_at` on every one of those **plus** `ToolCallArgsDelta`. The arguments must be in the bound because the numerator counts their tokens: leaving them out divides 8000 tokens by the few hundred milliseconds up to the tool-call open and reports a rate no generation can produce. `ToolCallClose` is deliberately excluded — providers batch the closes of sibling calls at `finish_reason`, so stamping them would measure the batch. A non-streaming request has no separable generation phase and reports `tps = 0`, excluded from TPS aggregates; `tps_sample_ratio` reports how much of the traffic a TPS figure describes.

**Tool-call framing in adapters** (`openai_adapter.py` reference): index-tracked `ToolCallOpen`/`Close`, **one pair per call**; a repeated `id` on an open index continues that call (OpenAI sends `id`+`name` only on a call's first chunk, but several OpenAI-compatible upstreams echo both on every arguments chunk), and only a *different* id closes the previous call and opens a new one; all parallel tools close at `finish_reason`; `raw_args` preserved on `ToolUsePart`.

When that close-and-reopen repeats an index, the adapter must emit the `ToolCallClose` for the superseded call **before** the new `ToolCallOpen` — never just replace its own bookkeeping. Any encoder that keys blocks by index loses the handle to the first block otherwise, and the client is told to open a block it is never told to close (AUDIT #243 fixed the adapters, #523 the encoders; both halves are required, and the encoders now close a superseded entry defensively as well).

---

## 3. StreamTape — mid-stream failover & client resume (`resume.py`)

A bounded ring buffer of emitted deltas with two roles:

1. **Mid-stream failover**: on upstream death after content has flowed, the tape holds the text deltas already emitted so a retry can prepend them as an assistant-prefix **continuation request** (the Anthropic capture-and-resume pattern). `build_continuation_messages()` synthesizes those messages from the tape; the retry lands on a fallback deployment and the client sees one continuous stream.
2. **Last-Event-ID replay**: when a client reconnects with `Last-Event-ID`, the **journal** store re-serves frames after that point. (`StreamTape.replay` is a separate in-process mechanism with no production caller — see the Historical note. It returns the recorded entries verbatim, failing over to `build_continuation_messages` only when the entries were evicted; it is *not* a filtered client view.)

**The byte budget charges every stored delta.** `_delta_size` is the eviction currency, so a delta that is primitive-typed but left out of the sum makes the ring unbounded in practice. `LogprobsDelta` was one: recorded on the tape, charged 0 bytes, so a `logprobs` stream retained ~5 MB while `tape.bytes` stayed at 0 and the 256 KiB bound never evicted (AUDIT #521). It is charged its serialized size now. `ServerToolResultDelta` is deliberately still 0 — a `web_search`-heavy turn would otherwise evict the client-dispatched deltas the continuation needs. Do not confuse "not consumed by the continuation" with "not stored": builtin deltas are stored and are returned by `replay()`; only `build_continuation_messages` filters them (`tc.builtin is None`).

---

## 4. JournalStore — durable stream journals (`tape_store.py`)

Closes the restart-durability gap: StreamTape is in-process, so a wiwi kill mid-stream left a reconnecting client with no memory of prior content.

- `_stream_response` appends every encoded SSE chunk (post id-injection, base64) to `.wiwi/journals/<request_id>.jsonl`.
- A reconnecting client sends `x-wiwi-stream-id: <request_id>` + `Last-Event-ID: <chunk seq>`; the same surface replays chunks > last_event_id from the journal, then tails live.
- Defaults (journal settings): **enabled by default**, dir `.wiwi/journals`, TTL 600 s, 1 MiB per journal.
- `.wiwi/` is gitignored runtime scratch — never commit it.
- **The dir is relative**, so it resolves against the process CWD. The Docker image cannot write there (non-root `wiwi` user, root-owned `/app`), so it sets `WIWI_STREAM_JOURNAL_DIR=/data/journals`; override the env var to move it (e.g. onto a persistent volume, since Railway's filesystem is ephemeral). An unwritable dir is logged once at startup as `stream_journal_dir_unwritable` with the resolved absolute path. Per-request it costs reconnect-resume only, not availability: when the journal's owner line cannot be made durable, the journal is dropped and the stream proceeds (AUDIT #380).

Journals survive wiwi restarts; StreamTape handles the common in-process case without disk I/O.

---

## 5. Guard subsystems

All guards sit between the adapter and the client encoder and degrade gracefully (advisory, not fatal) unless stated otherwise.

### Coalescer (`coalesce.py`)
Merges consecutive `TextDelta`s under backpressure. Activates only when queue depth exceeds a threshold; fast consumers see per-token granularity, slow consumers get fewer, larger frames — cutting SSE frame overhead and client parse work.

**Off by default, and the threshold is not configurable.** `stream_coalesce`
defaults to `False`, and the gateway constructs `DeltaCoalescer` with only
`max_bytes`/`max_ms` — it never passes `threshold`, so the trigger sits at the
class default of 100 against a queue of `maxsize=4096`. There is no
`stream_coalesce_threshold` knob. Tune `stream_coalesce_max_bytes` /
`stream_coalesce_max_ms` instead; the depth trigger is effectively a constant.

### Loop detector (`loopdetect.py`)
Aborts a stream when the model starts emitting the same content repeatedly (degenerate loop that would otherwise run to the token limit). Uses an incremental periodicity check — the naive whole-window check was O(n²) per token and was replaced with incremental updates. On detection the stream terminates with a **`StreamError`**, not a clean `Finish`: the partial output is priced first, then the error is surfaced. Crucially the failure is charged to the *model*, not the provider — `_note_stream_failure` is deliberately **not** called, so a low-quality model cannot cool a healthy deployment or retire a healthy key (AUDIT #108, pinned by `tests/test_fix_round43.py::test_loop_detection_does_not_penalise_key_health`).

### Partial JSON (`partial_json.py`)
Ports the Vercel AI SDK partial-json approach for streaming tool-call arguments. Only the **repair** half is wired in production: truncated JSON is auto-repaired at close time (appending missing `"`, `]`, `}`) instead of dropping args to `{}`. The incremental-render half (`PartialJSONParser`, `parse_partial`) has **no production caller** — see the Historical note.

### Tool-args validation (`validation.py`)
On `ToolCallClose`, validates accumulated args against the tool's JSON schema (`MAX_TOOL_ARGS_BYTES` caps accumulation). Violations are logged via structlog and attached to request metadata (`tool_args_violations`) — the client still receives the tool call, flagged as advisory.

`validate_tool_args` is a **never-raise** seam: it sits inside the pump's mid-stream handler, where an exception costs the caller a `StreamError` and cools the deployment/key for a payload that carries no validation semantics. Every caller-controlled shape is therefore guarded rather than trusted — a non-dict *schema*, a non-list `required`, a non-dict `properties`, an unhashable `required` member, and (since AUDIT #522) a non-`str` `raw_args` are all degraded to "skip this check", never raised on. Anything added here must keep that property.

---

## 6. Wire encoders (client side)

Each dialect owns a `StreamEncoder` in `wiwi/wire/`:

| Encoder | Surface | Emits |
|---|---|---|
| `ChatStreamEncoder` | `/v1/chat/completions` | OpenAI chunk objects (`chat.completion.chunk`) |
| `ResponsesStreamEncoder` | `/v1/responses` | Responses SSE events (`response.output_text.delta`, …) |
| `AnthropicStreamEncoder` | `/v1/messages` | `message_start`, `content_block_*`, `message_delta`, `message_stop` |
| (gateway pump, not the encoder) | `/v1/messages` | `ping` — emitted by `core/gateway.py` when the upstream goes quiet for `stream_ping_interval_s`, gated on `ctx.surface == "messages"`. The encoder does not produce it. |

Encoders consume the delta taxonomy in order; legality is the adapters' guarantee, so encoders do not attempt full defensive parsing. They do carry a small number of **targeted drops** for sequences that would otherwise corrupt the client's view — an `ToolCallArgsDelta` arriving with no preceding `ToolCallOpen` is dropped rather than synthesized against the wrong `output_index` (all three encoders), and signature-only `ThinkingDelta`s are ignored on surfaces with no representation for them. These are narrow guards, not general malformed-input handling. SSE framing (BOM, CRLF, `:` heartbeat comments, multi-line `data:` joining) is handled by `LineSSEParser` upstream; outbound framing helpers live in `sse.py`.

**A repeated `ToolCallOpen` on a still-open index is handled, not dropped.** It is *legal* IR — the adapters produce it deliberately, closing the previous call before re-opening (see the adapter rule in §2, and `_flush_open_tools`), and all four do. The block-oriented encoders used to key their per-block state by IR index and overwrite it unconditionally, losing the only handle to the first block: `AnthropicStreamEncoder` then emitted a `content_block_start` with no matching `content_block_stop`, and `ResponsesStreamEncoder` an `output_item.added` with no `output_item.done` — both render as a truncated call on a message that otherwise finishes normally (AUDIT #523). They now close the superseded entry first. `ChatStreamEncoder` needs no equivalent: a chunk stream has no open/close invariant, so a repeat Open merges into the same `tool_calls[index]` with the id replaced.

The broader rule this illustrates: **a delta is "malformed" only if the adapters cannot produce it.** Anything a well-behaved adapter emits is the encoder's problem to render, and an open/close pair it is told to start must be one it finishes.


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
| Degenerate repetition | Loop detector terminates | `StreamError` (partial output priced; provider/key health NOT penalised — AUDIT #108) |

`Backoff`, `CircuitBreaker`, and the opt-in `HealthHealer` (1-token probes, graduated probation recovery) live in `wiwi/core/recovery.py` and feed the router — see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Historical note

`docs/STREAMING_PERFORMANCE_RECOVERY.md` is the 2026-08-23 improvement report whose P0/P1/P2 items (tape, resume, journals, coalescer, loop detector, partial JSON, validation) are **implemented** — this page is the live description of the shipped system; the report is kept as historical record.

**Two of those are narrower in the shipped code than the report implies**, and
the distinction matters when reading this page:

- **Partial JSON** — only the *repair* half is wired. `_repair_truncated_json`
  is called from seven sites (both wire codecs, the OpenAI/OpenRouter adapters,
  the gateway, `resume.py` and `validation.py`), but `PartialJSONParser` and `parse_partial`,
  which implement "render tool arguments as they arrive", have **no production
  caller** — they are exercised by tests only. Clients do not currently receive
  incremental argument rendering.
- **Tape replay** — `StreamTape` is populated only when `stream_resume != "off"`
  (off by default), and `StreamTape.replay` has no production caller. A
  reconnecting client is served by the **journal** store
  (`streaming/tape_store.py`, wired in `server/app.py`), which is a different,
  disk-backed mechanism. The tape exists to serve the *mid-stream resume* path,
  not client-facing `Last-Event-ID` replay.
