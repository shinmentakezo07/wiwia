# wiwi — Tri-Format Gateway: Deep Research Report

*Generated: 2026-08-21 | Sources: ~60 (official API docs, LiteLLM source/issues, gateway implementations, benchmarks) | Confidence: High*

**Scope:** What it takes for wiwi to expose all three wire formats — OpenAI Chat Completions (`/v1/chat/completions`), OpenAI Responses (`/v1/responses`), Anthropic Messages (`/v1/messages`) — translating any-to-any through a canonical IR, so Claude Code and Codex CLI work against any backend model.

---

## Executive Summary

1. **Tri-format ingress is now table stakes.** Codex CLI removed `wire_api = "chat"` entirely (hard cutoff Feb 2026) — `/v1/responses` is mandatory to serve Codex. Claude Code speaks Anthropic Messages only. A gateway with both surfaces serves both clients against any backend.
2. **Canonical IR beats pairwise converters.** rosetta-llm proves the neutral-IR design works in production Python; LiteLLM's OpenAI-hub "bridge chain" accumulates special cases. wiwi should define `UnifiedRequest/Response/StreamEvent` and 2 codecs per format.
3. **The hard parts are known and enumerable:** reasoning-state round-tripping across providers, streaming tool-argument deltas, usage cache-token semantics, history sanitization before Anthropic ingress, error-envelope fidelity, and client-disconnect cancellation. Each has a documented solution pattern from existing projects.
4. **Stack verdict:** FastAPI/Starlette + uvicorn on the front, httpx (tuned) or aiohttp transport upstream, vendored byte-level SSE decoder, dicts+orjson in the hot loop, pydantic v2 discriminated unions at ingress only. Golden-file SSE fixtures + hypothesis round-trip tests are non-negotiable.

---

## 1. Why both OpenAI endpoints AND Anthropic endpoint

| Client | Speaks | Requires |
|---|---|---|
| OpenAI SDK, LangChain, most tools | Chat Completions | `/v1/chat/completions` |
| **Codex CLI** | Responses only | `POST {base_url}/responses`; `wire_api="chat"` removed Feb 2026 ([codex discussion #7782](https://github.com/openai/codex/discussions/7782)) |
| **Claude Code** | Anthropic Messages only | `POST /v1/messages?beta=true`, `/v1/messages/count_tokens`, optional `GET /v1/models` |

Codex sends (`store:false`, `stream:true`, always stateless): `instructions`, `input[]` items (message/function_call/function_call_output/reasoning), `reasoning:{effort,summary}`, `include:["reasoning.encrypted_content"]`, `prompt_cache_key`. No `previous_response_id` on the HTTP path — full history resent every turn.

Claude Code config: `ANTHROPIC_BASE_URL` + exactly one of `ANTHROPIC_AUTH_TOKEN` (→ Bearer) / `ANTHROPIC_API_KEY` (→ x-api-key). Model aliases via `ANTHROPIC_DEFAULT_OPUS/SONNET/HAIKU_MODEL`. Gateway model discovery opt-in via `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` → `GET /v1/models?limit=1000`, filters ids containing "claude"/"anthropic" (rosetta's trick: prefix foreign models `claude-code/` when session header present).

Claude Code gotchas that break proxies:
- Retries overload ONLY on HTTP 529 or `"type":"overloaded_error"` — error envelopes must be Anthropic-shaped end-to-end (LiteLLM #36655).
- Non-streaming fallback on mid-stream failure can duplicate tool executions → document `CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK=1`.
- Sends `thinking:{type:"adaptive"}` even to unknown model names → backends 400; kill-switches exist (`CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`, `_DISABLE_EXPERIMENTAL_BETAS`, `MAX_THINKING_TOKENS=0`).
- Forward `anthropic-beta` header verbatim; forward provider error bodies unmodified (its auto-recovery matches error wording).
- Streams need keepalive pings; byte-level watchdog aborts silent streams (300s); body idle timeout 5 min.
- Empty text blocks in replayed history → sanitize server-side (see §4).

---

## 2. Translation matrix (the core reference)

### Tools
| | Anthropic | Chat Completions | Responses |
|---|---|---|---|
| Definition | `{name, description, input_schema}` | `{type:"function", function:{name, description, parameters}}` | `{type:"function", name, description, parameters}` (flat) |
| Invocation | content block `{type:"tool_use", id:"toolu_…", name, input:{object}}` | `message.tool_calls[]{id:"call_…", function:{name, arguments:"<json str>"}}` | top-level item `{type:"function_call", call_id, name, arguments:"<json str>"}` |
| Result | user block `{type:"tool_result", tool_use_id, content:str\|blocks}` | message `{role:"tool", tool_call_id, content}` | item `{type:"function_call_output", call_id, output:str}` |

- JSON asymmetry everywhere: Anthropic `input` = object; OpenAI `arguments` = string. Convert at boundary; store as string in IR (rosetta pattern).
- Parallel calls: multiple blocks / index-keyed array entries / sibling items — all native.
- `tool_choice`: `auto↔auto`, `any↔required`, `tool{name}↔function{name}`, `none↔none`.
- Mixed user messages (tool_result + text): split — results become separate tool messages/items BEFORE residual text.
- Server tools (`web_search` etc., `srvtoolu_` ids): the #1 replay-breaker. Either surface as native equivalents, drop both halves consistently, or short-circuit standalone search requests (LiteLLM's Tavily fallback).

### Streaming dialects
- **Anthropic**: named events; `message_start` → per-block `content_block_start`/`content_block_delta`(text_delta|input_json_delta|thinking_delta|signature_delta)/`content_block_stop` → `message_delta`(stop_reason+cumulative usage) → `message_stop`; `ping` anywhere.
- **Chat**: unnamed `data:` chunks; first carries `delta:{role}`, text via `delta.content`, tools via index-keyed `delta.tool_calls[].function.arguments` fragments; finish chunk; THEN trailing usage chunk (`choices:[]`) only if `stream_options.include_usage` — proxies must hold Anthropic-style `message_delta` until usage arrives ("heldMessageDelta" pattern).
- **Responses**: typed events with sequence numbers; identity-keyed items (`item_id`); `response.output_item.added/done`, `response.output_text.delta`, `response.function_call_arguments.delta/.done` (done carries full item), `response.completed` with usage.
- Normalize wild `reasoning_content` / `reasoning` / `thinking_blocks` chunk extensions into one IR delta type.

### Thinking/reasoning (hardest problem)
Three portability strategies observed:
1. **Drop** (empero, Portkey adapter path) — simplest, loses quality.
2. **Summarize, signature=None** (LiteLLM responses path) — lossy, breaks strict replay.
3. **Carrier-encode into `signature`** (rosetta `<encrypted>@<id>`, raine `ccp:codex:v1:<b64(id)>:<enc>`) — lossless cross-provider round-trip. **Recommended for wiwi**, behind a config flag.

Mappings: `budget_tokens` ↔ effort bucketing (≥10k high / ≥5k medium / ≥2k low / else minimal). Anthropic signatures MUST round-trip unmodified or 400. Responses stateless mode requires replaying every output item verbatim incl. `encrypted_content`.

### Stop reasons
`end_turn↔stop↔completed`; `max_tokens↔length↔incomplete(max_output_tokens)`; `tool_use↔tool_calls↔completed+function_call present`; `refusal≈content_filter`; `stop_sequence→stop` (infer); `pause_turn` has no equivalent — flattening breaks long server-tool loops.

### Usage (semantic trap)
Anthropic `input_tokens` EXCLUDES cached reads; OpenAI `prompt_tokens` INCLUDES them. Correct split: `input_tokens = prompt_tokens − cached_tokens`, `cache_read_input_tokens = cached_tokens`. Naive copying double-counts cache reads. Also map `cache_creation_input_tokens` (no OpenAI equivalent → 0), `reasoning_tokens` both directions.

### Params & content
`system`(str/blocks+cache_control) ↔ system/developer message ↔ `instructions`; `max_tokens`(required!) ↔ `max_completion_tokens` ↔ `max_output_tokens`; `stop_sequences` ↔ `stop` ↔ dropped; `top_k` Anthropic-only; images via `data:{media};base64,{data}` formula; PDFs: document blocks ↔ file parts ↔ input_file (lossy).

---

## 3. How existing implementations do it

| Project | Architecture | Lesson for wiwi |
|---|---|---|
| **LiteLLM** | OpenAI-chat hub + bridge chain (adapter→bridge→provider); session reconstruction from spend logs for `previous_response_id` | Hub-and-spoke works but accretes special cases; stateful Responses needs storage or explicit refusal |
| **rosetta-llm** | True neutral IR: discriminated-union Parts, canonical stream events, pure codec functions, passthrough fast path | **Best architectural template.** Copy the IR shape and pipeline invariants |
| **claude2openai** | Per-model protocol routing (`auto\|chat\|responses`); stable UUID `prompt_cache_key` pinning | Per-deployment upstream-format override is worth having; cache-key trick is cheap and additive |
| **raine/claude-code-proxy** (Rust) | Signature-carrier encoding for reasoning; two-lane codex support | Reference for lossless reasoning round-trip encoding details |
| **Portkey** | Tri-format ingress, native-vs-adapter split; silently drops thinking/cache_control/top_k | The commercial bar; their silent drops are what wiwi can beat |
| Kong/Helicone/Cloudflare | OpenAI-shaped or passthrough only | No competition on tri-format translation |

Universal patterns: passthrough fast path when formats match; history sanitization trio before Anthropic dispatch; errors re-wrapped per inbound dialect.

---

## 4. Mandatory gotcha checklist (each = a golden test)

1. Strip empty/whitespace text blocks from history (Anthropic 400s otherwise; its own SDK emits them).
2. Sanitize tool-use ids to `^[a-zA-Z0-9_-]+$` on BOTH request and response paths (asymmetry bug LiteLLM #34516); beware `_` collisions.
3. Inject `max_tokens` floor (1024+) for Anthropic-bound requests; rename to `max_completion_tokens` for o-series.
4. Usage cache-split math (§2); hold final usage frame until after finish_reason.
5. Tool args: handle providers sending complete arguments in one chunk (emit synthetic deltas); never drop argument fragments.
6. Error envelopes per dialect incl. 529/overloaded_error; forward upstream error bodies verbatim where possible.
7. Client disconnect → shielded `aclose()` of upstream response in ALL exit paths incl. pre-first-chunk (499); deterministic lsof regression test.
8. Byte-level SSE parsing only (`str.splitlines()` splits U+2028 inside JSON — LiteLLM lost spend logs to this).
9. No O(n²) accumulation of streamed JSON; parse-on-close-brace or list-shard.
10. `previous_response_id`: reject cleanly (stateless mode) post-MVP; never half-support.
11. Flatten synthesized web_search results lacking `encrypted_content` to plain text (fakes 400 on replay).
12. Forward `anthropic-beta` verbatim; tolerate unknown `x-codex-*` headers.

---

## 5. Recommended stack (2026)

| Layer | Pick | Evidence |
|---|---|---|
| Framework | FastAPI routes for admin/non-stream; raw Starlette `StreamingResponse` (no response_model, no BaseHTTPMiddleware) on stream routes | BaseHTTPMiddleware breaks disconnect detection; FastAPI validation overhead multiplies per chunk |
| Server | uvicorn + uvloop, workers=cores, `--limit-concurrency`, keep-alive > LB idle | Granian faster (~2x RPS) but weak mid-stream disconnect propagation — revisit later |
| Upstream | Single app-lifetime `httpx.AsyncClient`, tuned `Limits`, explicit per-phase timeouts; aiohttp transport swap if >32 concurrent/host contention | LiteLLM defaults to aiohttp transport for throughput; httpx pool O(n²) pathology documented |
| SSE | Vendor OpenAI's byte-level `SSEDecoder` for ingest; f-string frames out; 15s comment pings; `X-Accel-Buffering: no`; no gzip | LiteLLM PR #28566; WHATWG spec |
| Schemas | Pydantic v2 discriminated unions at ingress; dicts + orjson in stream loop; module-level TypeAdapters; concrete-type serialization (union dump is ~3.4x slower) | pydantic perf docs; LiteLLM orjson fast path (-8.6% TTFT p95) |
| Cancellation | anyio task group racing stream vs disconnect; shielded cleanup; CancelledError-aware accounting | LiteLLM PRs #30245/#31499/#30522 |
| Testing | respx + committed raw-SSE golden fixtures per feature; hypothesis round-trip A→IR→A'; disconnect unit tests; inline-snapshot style | openai-python test practices; llm-mock record/replay precedent |

---

## 6. Proposed wiwi module layout

```
wiwi/
├── formats/                      # NEW — wire formats
│   ├── ir/
│   │   ├── request.py            # UnifiedRequest, Part union (Text/Image/Document/
│   │   │                         #   ToolCall[arguments_json_text]/ToolResult/Reasoning/
│   │   │                         #   Refusal), ReasoningConfig, raw_extras
│   │   ├── response.py           # UnifiedResponse, Usage(cache_*), StopInfo{normalized,raw}
│   │   └── events.py             # MessageStart/PartStart/PartDelta(text|json|reasoning|
│   │                             #   signature)/PartStop/MessageDelta/MessageStop/Ping/Error
│   ├── anthropic/                # codec.py (parse/render req+resp), stream.py (state machines),
│   │                             #   errors.py (529 envelope), sanitize.py (empty-block strip,
│   │                             #   tool-id normalize, web-search flatten)
│   ├── openai_chat/              # codec.py, stream.py, errors.py
│   ├── openai_responses/        # codec.py, stream.py, errors.py, stateless.py
│   └── carriers.py               # signature-carrier encode/decode for reasoning round-trip
├── server/routes/
│   ├── openai.py                 # /v1/chat/completions, /v1/responses, /v1/models
│   ├── anthropic.py              # /v1/messages, /v1/messages/count_tokens
│   └── ...
├── pipeline.py                   # decode→IR→(sanitize)→encode; passthrough fast path;
│                                 # held-usage framing; ping injection for anthropic out
├── providers/                    # adapters consume IR (unchanged from ARCHITECTURE.md §4.2)
└── ...
```

Pipeline invariants (from rosetta, validated): JSON decoded once; inbound==provider format → verbatim relay (model rewritten on copy); pings injected only when outbound is Anthropic; retries only pre-first-byte; one request_logs row per stream regardless of outcome.

---

## Key takeaways

1. Build IR + codecs first, TDD with golden fixtures — everything else hangs off this.
2. Ship order: chat completions → Anthropic ingress (unlocks Claude Code) → Responses ingress (unlocks Codex) → count_tokens + model discovery polish.
3. Adopt the signature-carrier reasoning strategy early (flag-gated) — retrofitting it later means breaking history compat.
4. The sanitization trio + error envelopes + disconnect handling are what separate "works in demo" from "survives Claude Code daily driving."

## Sources (primary)

- LiteLLM: docs.litellm.ai/docs/anthropic_unified/messages_to_responses_mapping, /docs/response_api; github.com/BerriAI/litellm — transformation.py files, issues #22930, #36655, #33546, #30486, #34516, PRs #27832, #28566, #29296, #30245, #31499, #30522, #27858
- rosetta-llm: github.com/Lokesh-Chimakurthi/rosetta-llm; llm-rosetta: github.com/Oaklight/llm-rosetta
- Proxies: empero-org/claude-code-proxy, fuergaosi233/claude-code-proxy, WqyJh/a2o, Rakanrkk/claude2openai, raine/claude-code-proxy
- Official docs: docs.claude.com (streaming, messages API, thinking, stop reasons), code.claude.com (env-vars, llm-gateway-protocol, model-config, network-config), developers.openai.com (responses streaming events, codex config)
- Codex: github.com/openai/codex — codex-rs/core/src/client.rs, model-provider-info/src/lib.rs, discussion #7782, issue #13628
- Stack: pydantic performance docs, github.com/emmett-framework/granian (benchmarks, issues #286/#412/#650), encode/httpx#3215, NVIDIA NeMo Gym aiohttp note, sse-starlette, respx, msgspec benchmarks

## Methodology

4 parallel research agents; ~25 distinct search queries; deep-reads of LiteLLM converter sources, official API references, and 6 proxy codebases; GitHub issue forensics for failure modes. Sub-questions: translation semantics, implementation architectures, Python stack, client requirements.
