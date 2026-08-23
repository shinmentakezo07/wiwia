# wiwi Proxy — Detailed Technical Reference

This document is a ground-truth reference for the wiwi proxy's internals, derived from the codebase. When docs and code disagree, the code wins.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Configuration](#2-configuration)
3. [Canonical Internal Representation (IR)](#3-canonical-internal-representation-ir)
4. [Wire Codecs (Inbound)](#4-wire-codecs-inbound)
5. [Provider Adapters (Outbound)](#5-provider-adapters-outbound)
6. [Request Lifecycle](#6-request-lifecycle)
7. [Router: Model Groups, Key Pools, Retries, Fallbacks](#7-router-model-groups-key-pools-retries-fallbacks)
8. [Gateway Engine](#8-gateway-engine)
9. [Streaming Architecture](#9-streaming-architecture)
10. [Authentication & Virtual Keys](#10-authentication--virtual-keys)
11. [Rate Limiting](#11-rate-limiting)
12. [Cost & Pricing](#12-cost--pricing)
13. [Logging & Observability](#13-logging--observability)
14. [Admin API](#14-admin-api)
15. [Admin Web UI](#15-admin-web-ui)
16. [Error Model](#16-error-model)
17. [File Map](#17-file-map)

---

## 1. Overview

wiwi is a self-hosted unified LLM gateway proxy. It accepts requests in three inbound API dialects, translates them through a single canonical IR, routes to any configured provider, and re-encodes the response back in the caller's original dialect. This means a Claude Code client can be backed by GPT, a Codex CLI client by Gemini, and so on.

### Three inbound surfaces

| Endpoint | Dialect | Wire codec | Typical client |
|---|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat | `wire/openai_chat.py` | openai SDK, LangChain, curl |
| `POST /v1/responses` | OpenAI Responses | `wire/openai_responses.py` | Codex CLI |
| `POST /v1/messages` | Anthropic Messages | `wire/anthropic_messages.py` | Claude Code, anthropic SDK |

Each surface also has its own `error_body` function so error envelopes match the dialect the client expects.

### Four outbound providers

| Provider type | Adapter | Key transport |
|---|---|---|
| `openai` | `OpenAIAdapter` | `Authorization: Bearer` header |
| `anthropic` | `AnthropicAdapter` | `x-api-key` header + `anthropic-version` |
| `gemini` | `GeminiAdapter` | `?key=` querystring |
| `openai-compatible` | `OpenAIAdapter` (shared) | `Authorization: Bearer` header |

The `openai-compatible` type deliberately shares `OpenAIAdapter` — any endpoint that speaks the OpenAI Chat Completions wire format (Ollama, vLLM, OpenRouter, LM Studio, etc.) uses it with a custom `base_url`.

### Hub-and-spoke design

No pairwise converters. Every direction goes:

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

Core code (`core/gateway.py`, `router/router.py`, `auth/`) never branches on dialect or provider name. Adding an inbound surface = one module in `wire/`. Adding a provider = one adapter in `providers/` + one line in `registry.py`.

---

## 2. Configuration

**File**: `wiwi/config.py`

Configuration is a single LiteLLM-shaped YAML file (`wiwi.yaml`). Loading is fail-fast with file/line context via `ConfigError`.

### Structure

```yaml
providers:              # named provider accounts with key pools
  - name: openai-main
    provider: openai     # openai | anthropic | gemini | openai-compatible
    base_url: ...        # optional; defaults per provider type
    timeout_s: 120.0
    extra_headers: {}
    keys:
      - {label: main, key: os.environ/OPENAI_API_KEY, weight: 3, enabled: true}

model_list:             # model_name clients request → provider + native model id
  - model_name: gpt-4o
    wiwi_params:
      provider: openai-main
      model: gpt-4o
      weight: 2
      max_tokens: 4096
      rpm: 500
      tpm: 100000
      timeout: 60.0
      extra_headers: {}

router_settings:
  routing_strategy: simple-shuffle    # simple-shuffle | least-busy | latency-based
  num_retries: 2
  timeout: 120.0
  allowed_fails: 3
  cooldown_time: 30.0
  fallbacks: {claude-sonnet: [gpt-4o]}
  context_window_fallbacks: {}
  model_group_alias: {gpt-4: gpt-4o}
  global_rpm: null
  global_tpm: null

general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: sqlite+aiosqlite:///wiwi.db
  redis_url: ""

wiwi_settings:
  drop_params: true
  max_request_body_mb: 50
  log_requests: true
  host: 0.0.0.0
  port: 4000
  header_allowlist: [anthropic-version, anthropic-beta, openai-organization, ...]
```

### Env interpolation

Any string value in the YAML may be `os.environ/NAME`. The `_interpolate()` function recursively resolves these before pydantic validation. If the env var is unset, it raises `ConfigError` immediately.

### Validation

- Pydantic v2 models validate types and constraints.
- A `@model_validator(mode="after")` checks that every `model_list` entry references a known provider name.
- `ProviderDef` requires at least one key entry.
- `KeyDef` requires a non-empty `key` string.

### Pydantic models

| Model | Purpose |
|---|---|
| `KeyDef` | One key in a provider pool: `label`, `key`, `weight`, `enabled` |
| `ProviderDef` | A named provider account: `name`, `provider` type, `base_url`, `keys` |
| `DeploymentParams` | Per-deployment routing params: `provider`, `model`, `weight`, `rpm`/`tpm`/`timeout`/`max_tokens` |
| `ModelEntry` | `model_name` → `wiwi_params: DeploymentParams` |
| `RouterSettings` | Strategy, retries, cooldowns, fallbacks, aliases, global limits |
| `GeneralSettings` | `master_key`, `database_url`, `redis_url` |
| `WiwiSettings` | `drop_params`, `max_request_body_mb`, `host`/`port`, `header_allowlist` |
| `WiwiConfig` | Top-level container; cross-validates model→provider references |

---

## 3. Canonical Internal Representation (IR)

**File**: `wiwi/ir/types.py`

The IR is the single source of truth for all translation. Every wire dialect decodes into these types; every provider adapter encodes from them. Plain dataclasses (frozen where possible) — not pydantic — for hot-path performance.

### Message parts

A `Message` has a `role` (`"system"`, `"user"`, `"assistant"`, `"tool"`) and a list of `Part` objects:

| Part | Fields | Notes |
|---|---|---|
| `TextPart` | `text`, `cache_control` | `cache_control` = `{"type": "ephemeral"}` for Anthropic prompt caching passthrough |
| `ImagePart` | `url`, `b64`, `mime`, `detail` | URL or base64-encoded |
| `ToolUsePart` | `id`, `name`, `args`, `raw_args` | Assistant tool call; `raw_args` preserves original JSON string |
| `ToolResultPart` | `tool_use_id`, `content`, `is_error`, `cache_control` | Tool execution result |
| `ThinkingPart` | `text`, `signature` | Extended thinking / reasoning |
| `AudioPart` | `b64`, `mime` | Reserved (field stable, translation later) |
| `DocumentPart` | `b64`, `url`, `mime`, `name` | Reserved (PDF support later) |

### Tools

```python
@dataclass
class Tool:
    name: str
    description: str = ""
    parameters_json_schema: dict = {"type": "object"}
    strict: bool | None = None   # OpenAI structured-output strictness
```

### Tool choice

`ToolChoice` is a union: `ToolChoiceAuto | ToolChoiceNone | ToolChoiceRequired | ToolChoiceNamed`.

### Generation parameters

`GenParams` carries: `temperature`, `top_p`, `max_tokens`, `stop`, `seed`, `n`, `response_format` (`text` / `json_object` / `json_schema`), `parallel_tool_calls`, `reasoning_effort` (`low`/`medium`/`high`), `thinking_budget`, `metadata`.

### Request

```python
@dataclass
class Request:
    model: str
    messages: list[Message]
    tools: list[Tool]
    tool_choice: ToolChoice | None
    gen_params: GenParams
    stream: bool = False
    stream_options_include_usage: bool = True
    extras: dict[str, Any]   # unmapped dialect-specific fields preserved
```

The `extras` dict captures fields the codec doesn't model but must not lose. When `drop_params=False`, these are forwarded to the upstream provider.

### Usage

```python
@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_estimated: bool = False
    cache_creation_tokens: int = 0
```

### AssistantTurn

The model's output as IR: `text`, `thinking` (list of `ThinkingPart`), `tool_calls` (list of `ToolUsePart`), `stop_reason`, `usage`, and `raw` (provider-native response dict for passthrough extras).

---

## 4. Wire Codecs (Inbound)

**Directory**: `wiwi/wire/`

Each wire module is self-contained with four exports:

1. `decode_request(body: dict) -> ir.Request` — parse inbound JSON into IR
2. `encode_response(ctx, turn, model, req_id) -> dict` — encode IR response back into the dialect
3. A `*StreamEncoder` class — FSM that converts `IRStreamDelta` objects into dialect-correct SSE frames
4. `error_body(status, etype, message) -> dict` — dialect-correct error envelope

### openai_chat.py

- **Decode**: Parses `messages` (string or multipart content), `tools` (function declarations), `tool_choice`, `gen_params`, `response_format`, and `stream_options`.
- **Encode**: Builds `chat.completion` object with `choices[0].message`, `usage` (with `prompt_tokens_details.cached_tokens` and `completion_tokens_details.reasoning_tokens`), and `finish_reason`.
- **Stream encoder** (`ChatStreamEncoder`): Emits `chat.completion.chunk` frames. `reasoning_content` in deltas maps to `ThinkingDelta`. Tool calls use index-based delta fragments. `UsageFinal` is held back and emitted with the `Finish` frame in `final_frame()`.
- **Error**: `{"error": {"message": ..., "type": ..., "code": ...}}`
- Known keys are stripped into `extras`; unknown keys are preserved for passthrough.

### anthropic_messages.py

- **Decode**: Parses Anthropic Messages format — `system` as top-level, `content` blocks (text, image, tool_use, tool_result, thinking), `tools` with `input_schema`, `tool_choice` variants, `thinking` budget.
- **Encode**: Builds Anthropic response with `content` blocks, `stop_reason` mapping, and `usage` with `input_tokens`/`output_tokens`/`cache_read_input_tokens`/`cache_creation_input_tokens`.
- **Stream encoder** (`AnthropicStreamEncoder`): Emits `message_start`, `content_block_start`/`delta`/`stop`, `message_delta`, `message_stop` events. Preserves `cache_control` on system blocks (enables Anthropic prompt caching).
- **Error**: `{"type": "error", "error": {"type": ..., "message": ...}}`
- Also handles `POST /v1/messages/count_tokens` — estimates input tokens via chars/4 heuristic.

### openai_responses.py

- **Decode/Encode**: Handles the OpenAI Responses API format (used by Codex CLI). Converts between Responses' event-based format and IR.
- **Stream encoder** (`ResponsesStreamEncoder`): Emits `response.created`, `response.output_item.added`, `response.output_text.delta`, `response.completed`, etc.

### Shared encoding contract

All three stream encoders trust the `IRStreamDelta` taxonomy. They never defend against malformed sequences — adapters guarantee legality.

---

## 5. Provider Adapters (Outbound)

**Directory**: `wiwi/providers/`

Each adapter implements the `ProviderAdapter` protocol from `base.py`:

```python
class ProviderAdapter(Protocol):
    provider_type: str
    def headers(self, key: ProviderKeyRef) -> dict[str, str]: ...
    def build_url(self, base_url, model_id, stream, kind) -> str: ...
    def encode_request(self, req: IRRequest, model_id, deployment_params) -> dict: ...
    def decode_response(self, status, body) -> AssistantTurn: ...
    def decode_stream_event(self, event, data) -> list[IRStreamDelta]: ...
```

### registry.py

A single function maps provider type to adapter instance:

```python
def get_adapter(provider_type: str) -> ProviderAdapter:
    if provider_type == "anthropic": return AnthropicAdapter()
    if provider_type == "gemini": return GeminiAdapter()
    return OpenAIAdapter()  # openai + openai-compatible
```

### openai_adapter.py

- **Headers**: `Authorization: Bearer {key}`
- **URL**: `{base_url}/chat/completions`
- **Encode**: Builds OpenAI Chat Completions body — messages (with multipart content for images), tools, tool_choice, gen params, `stream_options`, and `response_format`. `drop_params` controls whether unmapped extras are forwarded.
- **Decode response**: Parses `choices[0].message`, tool calls, `finish_reason` → IR `stop_reason`, `usage` with token details.
- **Decode stream**: Parses SSE chunks. `reasoning_content` → `ThinkingDelta`. Tool calls tracked by index with open/close state machine. `usage` may appear in any chunk (OpenAI/OpenRouter behavior) — parsed whenever present.

### anthropic_adapter.py

- **Headers**: `x-api-key: {key}`, `anthropic-version: 2023-06-01`
- **URL**: `{base_url}/messages`
- **Encode**: Builds Anthropic Messages body — system prompt (as blocks if any part has `cache_control`, as string otherwise), content blocks per message, `tool_use`/`tool_result` blocks, `thinking` budget, tools with `input_schema`.
- **Decode response**: Parses `content` blocks (text, thinking, tool_use), `stop_reason` → IR `stop_reason`, `usage` with cache fields.
- **Decode stream**: Handles Anthropic's event-folded SSE — `message_start`, `content_block_start`/`delta`/`stop`, `message_delta` (carries `usage` + `stop_reason`), `message_stop`, `error`. Prompt tokens captured from `message_start`, output tokens from `message_delta`.

### gemini_adapter.py

- **Headers**: none (key in querystring)
- **URL**: `{base_url}/models/{model_id}:generateContent?key={key}` (non-stream) or `:streamGenerateContent?alt=sse&key={key}` (stream)
- **Encode**: Builds Gemini `generateContent` body — `contents` with role mapping (`assistant` → `model`), `systemInstruction`, `generationConfig`, `tools` with `functionDeclarations`. Resolves `functionResponse.name` from matching `ToolUsePart` in history (Gemini requires the function name, not the tool use ID).
- **Decode response**: Parses `candidates[0].content.parts`, `functionCall` → `ToolUsePart`, `usageMetadata` → IR `Usage` (includes `thoughtsTokenCount` as reasoning tokens).
- **Decode stream**: Gemini emits one SSE event per chunk with candidates + parts. Tool calls are fully serialized in a single part (open + args + close emitted together). `finishReason` triggers `UsageFinal` + `Finish` + `StreamEnd`.

### Key transport summary

The gateway's `_build_url()` function handles the Gemini special case — it appends the key secret to the URL querystring after the adapter builds it:

```python
if dep.provider.provider_type == "gemini" and url.endswith(("?key=", "&key=")):
    url += key.secret
```

---

## 6. Request Lifecycle

**File**: `wiwi/server/app.py` — `run_chat_like()` (the shared inbound path)

All three surfaces route through the same function. The full pipeline:

```
1. Parse JSON body (400 on malformed JSON)
2. Decode: wire codec → ir.Request (400 on DialectError)
3. Estimate tokens: len(json_body) // 4
4. Authenticate: bearer/x-api-key → AuthService.authenticate()
   - 401 missing/invalid/disabled/expired key
   - 429 budget exceeded
   - 403 model not in key's allowlist
5. Resolve model group: Router.resolve_group() (follows aliases, 404 if not found)
6. Rate limit check: RateLimiter.check() (429 with Retry-After if exceeded)
7. Build RequestContext (surface, ir_req, auth, group)
8. Execute:
   ├─ Non-streaming: gateway.complete() → JSONResponse
   └─ Streaming: gateway.stream() → StreamingResponse (text/event-stream)
9. Log: build_log_event(ctx) → LoggingSubsystem.log_request()
10. Record TPM usage: RateLimiter.record_tokens()
11. Update spend: AuthService.update_spend() (virtual keys only)
```

### Streaming first-delta probe

For streaming requests, the gateway pulls the first delta before committing to a `StreamingResponse`. If the upstream fails during connect (bad request, auth, rate limit), `execute_with_retries` raises a `WiwiError` before any byte is sent, and the server can still answer with a proper JSON error response.

### Per-request middleware

- Assigns a 16-char hex `request_id` to `request.state.request_id`.
- Enforces `max_request_body_mb` (default 50 MiB, 413 on exceed).
- Adds `x-wiwi-request-id` and `x-wiwi-latency-ms` response headers.

### Token counting endpoint

`POST /v1/messages/count_tokens` is a lightweight endpoint that decodes the request, authenticates, and returns `{"input_tokens": N}` using a chars/4 heuristic. No upstream call is made.

### Model listing

`GET /v1/models` authenticates (any valid key), then returns the model groups configured in the router (not upstream models). Each entry: `{"id": name, "object": "model", "owned_by": "wiwi"}`.

---

## 7. Router: Model Groups, Key Pools, Retries, Fallbacks

**File**: `wiwi/router/router.py`

### Model groups

A model group (`model_name`) maps to one or more `Deployment` objects. A deployment binds a provider account to a native model ID:

```python
@dataclass
class Deployment:
    group: str
    provider: ProviderAccount
    model_id: str
    weight: int = 1
    rpm, tpm, timeout, max_tokens: optional limits
    extra_headers: dict
    # health tracking
    fails: list[float]           # recent failure timestamps (60s window)
    cooldown_until: float = 0.0
    inflight: int = 0
    latencies: deque(maxlen=50)  # for p95 latency
```

### Alias resolution

`resolve_group(requested)` follows `model_group_alias` entries, chained up to 8 hops (prevents cycles). Example: `gpt-4` → `gpt-4o`.

### Deployment selection strategies

| Strategy | Selection logic |
|---|---|
| `simple-shuffle` (default) | Weight-weighted random selection |
| `least-busy` | Lowest `inflight` count |
| `latency-based` | Lowest p95 latency (cold deployments with no samples win, so they get explored) |

### Provider key pools (smooth WRR)

Each `ProviderAccount` has a pool of `ProviderKey` entries. Key selection uses the **nginx smooth weighted round-robin algorithm**:

```python
async def pick_key(self) -> tuple[ProviderKey | None, float]:
    async with self._rr_lock:
        for k in self.keys: k.recover()
        avail = [k for k in self.keys if k.available]
        if not avail: return None, retry_in_seconds
        total = sum(k.weight for k in avail)
        for k in avail: k.current_weight += k.weight
        best = max(avail, key=lambda k: k.current_weight)
        best.current_weight -= total
        return best, 0.0
```

This distributes requests proportionally to weights over time while avoiding burst patterns.

### Key lifecycle states

| State | Meaning | Transition |
|---|---|---|
| `active` | Available for selection | Default |
| `cooling` | Temporarily excluded after 429 | Auto-recovers after `cooldown_time` |
| `invalid` | Permanently excluded (401/403) | Only manual admin reset |
| `disabled` | Manually disabled | Admin toggle |

### `ProviderKey.available` property

A key is available if: `enabled` is True, status is `active` or `cooling`, and if `cooling`, the cooldown period has elapsed.

### Retry & failover — `execute_with_retries`

This is the core routing loop:

```
for each group in the queue (primary + fallbacks):
    for attempt in range(num_retries + 1):
        dep = pick_deployment(exclude=tried_dep_ids)
        key = dep.provider.pick_key()
        try:
            result = await call_one(dep, key, ctx)
            return result
        except WiwiError as e:
            dep.provider.on_result(key, status, retry_after)
            if status in (408,500,502,503,504,529): dep.record_fail(...)
            if not e.retryable: raise
            if no fresh deployment: sleep(backoff)
```

Key mechanics:

- **Excluded deployments**: Retries prefer a different deployment (`exclude` set of `id()`s). If all are exhausted, reuse is allowed.
- **Backoff**: Exponential with jitter: `min(5.0, max(retry_after, 0.5 * 2^attempt)) + random(0, 0.25)`.
- **Fallbacks**: After exhausting retries on a group, the router enqueues fallback groups from `router_settings.fallbacks`. Example: if `claude-sonnet` fails, try `gpt-4o`.
- **Key invalidation on 401/403**: Auth errors are `retryable=True` so the pool fails over to the next key via `ProviderKey.mark_invalid()`. The HTTP status is preserved but the request doesn't hard-fail the client.

### Deployment cooldown

`record_fail(allowed_fails, cooldown_time)` appends a failure timestamp. If failures within the last 60 seconds reach `allowed_fails` (default 3), the deployment enters cooldown for `cooldown_time` (default 30s). The `fails` list is pruned and cleared on cooldown.

### p95 latency tracking

Each deployment keeps a `deque(maxlen=50)` of recent latency samples. `p95_latency()` returns the 95th percentile. Used by `latency-based` routing strategy and surfaced in the admin API.

---

## 8. Gateway Engine

**File**: `wiwi/core/gateway.py`

The `Gateway` class is surface-agnostic. It owns a shared `httpx.AsyncClient` (timeout 120s, connect 10s) and delegates routing to the `Router`.

### Non-streaming: `complete(ctx)`

```
execute_with_retries(router, ctx, call_one)
  └─ call_one(dep, key, ctx) → self._call(dep, key, ctx)
       └─ dep.inflight += 1
       └─ self._call_once(dep, key, ctx)
            └─ adapter.encode_request(ir_req, model_id, params)
            └─ httpx POST to upstream
            └─ adapter.decode_response(status, body) → AssistantTurn
            └─ self._price(ctx, dep, turn.usage)
       └─ dep.inflight -= 1
```

### URL construction

`_build_url(adapter, dep, key, stream, kind)` calls the adapter's `build_url()` and appends the Gemini key querystring if needed.

### Error handling in `_call_once`

- `httpx.TransportError`: Mapped to 504 (timeout) or 502 (connection error), `retryable=True`.
- Non-200 status: `error_from_provider_status()` produces a `WiwiError`. `Retry-After` header parsed and attached.
- Success: Latency recorded in `dep.latencies`, attempt noted in `ctx.attempts`.

### Pricing hooks

- `_price(ctx, dep, usage)`: Computes cost via `CostEngine.cost()`, sets `ctx.usage`, `ctx.cost`, `ctx.cache_hit`, and `ctx.metadata["cache_savings"]`.
- `_price_stream(ctx, dep, UsageFinal)`: Same but for streaming (converts `UsageFinal` to `ir.Usage`).
- `_price_partial(ctx, dep, usage_final, text_len)`: Prices partial delivery on mid-stream failure, so virtual-key spend reflects tokens actually consumed.
- `_cache_savings(model_key, usage)`: Computes dollars saved by provider-side prompt caching (`input_rate - cache_read_rate` × cached tokens).

### `build_log_event(ctx)`

Constructs a `LogEvent` from the context after the request completes:

- `latency_ms`: wall time from `ctx.started` to now.
- `ttft_ms`: time to first token (streaming only).
- `tps`: `completion_tokens / stream_duration` (if stream > 0.05s).
- Token breakdown: in, cached, reasoning, output.
- `attempts`: List of `AttemptRecord` with deployment, provider, key, status, latency per attempt.

---

## 9. Streaming Architecture

### IRStreamDelta taxonomy

**File**: `wiwi/streaming/deltas.py`

The contract between adapters and encoders. Adapters guarantee legal sequences; encoders never defend against malformed ones.

```
StreamStart                          (exactly one, first)
  ├─ TextDelta*                      (content text)
  ├─ ThinkingDelta*                  (reasoning text + optional signature)
  ├─ ToolCallOpen                    (index, id, name)
  │   └─ ToolCallArgsDelta*          (args_fragment)
  │   └─ ToolCallClose                (index)
  UsageFinal                          (exactly one, after last content delta)
  Finish                              (stop_reason)
  StreamEnd | StreamError             (exactly one terminal)
```

`StreamError` may terminate at any point, replacing everything after the last emitted delta. It needs no `Finish` — it is the abnormal-path terminal.

Delta types (`@dataclass(frozen=True)`):

| Delta | Fields |
|---|---|
| `StreamStart` | `model`, `group` |
| `TextDelta` | `text` |
| `ThinkingDelta` | `text`, `signature` |
| `ToolCallOpen` | `index`, `id`, `name` |
| `ToolCallArgsDelta` | `index`, `args_fragment` |
| `ToolCallClose` | `index` |
| `UsageFinal` | `prompt`, `cached`, `reasoning`, `output`, `cache_creation`, `estimated`, `cost` |
| `Finish` | `stop_reason` |
| `StreamEnd` | (none) |
| `StreamError` | `message`, `kind` (`timeout`/`connection`/`status`/`cancelled`/`unknown`), `status` |

### Stream pump (`_pump` / `_pump_once`)

The gateway's streaming path is a producer-consumer architecture:

```
gateway.stream(ctx)
  └─ queue = asyncio.Queue(maxsize=4096)
  └─ execute_with_retries(router, ctx, call_one)
       └─ call_one creates _pump task, waits for ready event
            └─ _pump(dep, key, ctx, queue, ready, err_box)
                 └─ dep.inflight += 1  (owned until pump finishes, not just connect)
                 └─ _pump_once:
                      └─ httpx.stream("POST", url, ...)
                      └─ if non-200: err_box[0] = WiwiError; ready.set()
                      └─ if 200: ready.set()  (caller starts consuming)
                      └─ for line in resp.aiter_lines():
                           └─ LineSSEParser.feed_line(line) → SSEEvent
                           └─ adapter.decode_stream_event(event, data) → [IRStreamDelta]
                           └─ queue.put(delta)
                      └─ after stream ends:
                           └─ compute/estimate UsageFinal
                           └─ self._price_stream(ctx, dep, est_usage)
                           └─ queue.put(UsageFinal)
                           └─ queue.put(Finish or StreamError)
                           └─ queue.put(StreamEnd)
  └─ yield StreamStart(model, group)
  └─ yield from queue.get() until StreamEnd | StreamError
```

### Connect-before-commit

The pump sets `ready` only after the upstream connection is established. If it fails before any data flows, `err_box[0]` gets the `WiwiError` and `ready.set()` signals the caller, which raises — allowing `execute_with_retries` to retry on a different deployment. This means connection-level failures can still be retried and the client gets a proper JSON error (not a broken SSE stream).

### Usage estimation

If the provider sends no usable usage data (`prompt == 0`), the pump estimates:

```python
est_usage = UsageFinal(
    prompt=estimate_tokens(_flatten(ctx)),    # chars/4 of all text parts
    cached=real_usage.cached,
    reasoning=real_usage.reasoning,
    output=real_usage.output or max(1, text_len // 4),
    cache_creation=real_usage.cache_creation,
    estimated=True)
```

The `estimated` flag is surfaced in logs and the admin UI.

### Client cancellation

The pump checks `ctx.cancel.is_set()` on each upstream line. If the client disconnects:

1. A `StreamError("client disconnected", "cancelled")` is queued.
2. The pump breaks its loop.
3. On `asyncio.CancelledError`, the upstream response is released via `asyncio.shield(resp_cm.__aexit__())` so the pooled httpx socket doesn't leak.

### Mid-stream failure handling

If an exception occurs after the stream has started (can't retry):

1. `_note_stream_failure(dep, real_key)`: increments `key.err_count` and triggers deployment cooldown via `record_fail()`.
2. `_price_partial(ctx, dep, usage_final, text_len)`: bills what was actually delivered.
3. A `StreamError` is queued to the client.

### SSE parsing (upstream)

**File**: `wiwi/streaming/sse.py`

`LineSSEParser` is a line-oriented incremental parser. Feed lines from `resp.aiter_lines()`; it yields `SSEEvent(event, data)` at blank-line boundaries. Handles:

- `event:` lines (event name)
- `data:` lines (joined with `\n` if multiple)
- `:` prefix lines (comments/heartbeats — ignored)
- BOM removal and `\r` stripping

### SSE framing (downstream)

`sse_frame(event, payload)` builds wire frames:

```
event: {name}\n
data: {payload}\n\n
```

Each wire codec's stream encoder uses this to produce dialect-correct SSE.

### Terminal frames per dialect

After the pump completes, the server emits terminal frames in the correct order:

| Dialect | Terminal sequence |
|---|---|
| OpenAI Chat | `final_frame()` (finish_reason + usage) →` |
| Anthropic | `final_frame()` (message_delta w/ usage+stop) → `event: message_stop` |
| Responses | `response.completed` |

On error, the encoder's `feed(StreamError)` emits the error frame and no terminal `[DONE]`/`message_stop` is sent (that would imply success).

---

## 10. Authentication & Virtual Keys

**Files**: `wiwi/auth/keys.py`, `wiwi/auth/service.py`

### Key types

| Type | Prefix | Scope |
|---|---|---|
| Master key | `sk-wiwi-master-` | Full admin + all proxy access |
| Virtual key | `sk-wiwi-` | Scoped: budget, RPM/TPM, model allowlist, TTL |

### Key generation & hashing

- `generate_virtual_key()`: `sk-wiwi-` + `secrets.token_urlsafe(32)` (43 chars of entropy).
- `hash_key(plaintext)`: SHA-256 hex digest. Keys are stored hashed at rest.
- `verify_key(plaintext, key_hash)`: `hmac.compare_digest` (constant-time).
- `mask_key(plaintext)`: `sk-wiwi…abcd` for display.
- Custom keys are allowed if ≥16 characters.

### AuthService

**File**: `wiwi/auth/service.py`

SQLite-backed via SQLAlchemy async. The `vkeys` table schema:

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `k` + 16 hex chars |
| `key_hash` | TEXT UNIQUE | SHA-256 of plaintext |
| `key_alias` | TEXT | Human-readable name |
| `models` | TEXT (JSON) | `[]` = all allowed |
| `max_budget` | REAL | USD; NULL = unlimited |
| `spend_to_date` | REAL | Running total |
| `rpm` | INTEGER | Requests per minute |
| `tpm` | INTEGER | Tokens per minute |
| `expires_at` | REAL | Unix timestamp; NULL = never |
| `disabled` | INTEGER | 0/1 |

### Authentication flow

```python
async def authenticate(self, plaintext: str) -> AuthInfo | None:
    # 1. Check master key (constant-time compare against master_hash)
    if hmac.compare_digest(hash_key(plaintext), self.master_hash):
        return AuthInfo(key_id="master", key_type="master", alias="master")
    # 2. Check cache (60s TTL, keyed by hash)
    h = hash_key(plaintext)
    hit = self._cache.get(h)
    if hit and now - hit[1] < self._ttl: return hit[0]
    # 3. DB lookup
    info = await self._lookup_db(h)
    self._cache[h] = (info, now)
    return info
```

### AuthInfo

```python
@dataclass
class AuthInfo:
    key_id: str           # "master" or "k..."
    key_type: str         # "master" | "virtual"
    alias: str
    models: list[str]     # empty = all
    max_budget: float | None
    spend_to_date: float
    rpm: int | None
    tpm: int | None
    expires_at: float | None
    disabled: bool

    @property
    def over_budget(self) -> bool:
        return self.max_budget is not None and self.spend_to_date >= self.max_budget
```

### Cache eviction

Admin mutations (disable, delete, update, create) evict the affected cache entry immediately so changes take effect without waiting for the 60s TTL. `create_key` also evicts its own hash to clear any negative-cache entry from a failed prior guess.

### Spend tracking

`update_spend(key_id, add_cost)` increments `spend_to_date` in the DB and also adjusts any cached `AuthInfo` in place, so budget limits are enforced promptly without a re-lookup. Master key spend is not tracked.

### Updatable fields

`max_budget`, `rpm`, `tpm`, `models`, `expires_at`. Absent = unchanged; explicit `null` = clear.

---

## 11. Rate Limiting

**File**: `wiwi/ratelimit/memory.py`

### Sliding-window counters

The `RateLimiter` uses 60-second sliding windows with `deque`-backed event storage. Four scopes:

| Scope | Key | What it counts |
|---|---|---|
| `global:rpm` | fixed | All requests system-wide |
| `global:tpm` | fixed | All tokens system-wide |
| `{key_id}:rpm` | per-key | Requests per virtual key |
| `{key_id}:tpm` | per-key | Tokens per virtual key |

Global limits are configured in `router_settings.global_rpm` / `global_tpm`. Per-key limits come from the virtual key's `rpm` / `tpm` fields.

### `check(key_id, key_rpm, key_tpm, est_tokens)`

Prospective admission check:

1. Prune expired events (older than 60s) from each window.
2. For each window: if `window.count() + incoming_cost > limit`, reject with `retry_after`.
3. If all pass: reserve an event (1 for RPM, `est_tokens` for TPM).

Returns `(allowed: bool, retry_after_seconds: int)`.

### `record_tokens(key_id, actual_tokens)`

Post-request confirmation. Replaces the newest estimated reservation in the TPM window with actual usage. This prevents double-counting (estimate + actual):

```python
for e in reversed(w.events):
    if e.estimated:
        e.tokens = max(0, actual_tokens)
        e.estimated = False
        break
```

### Reservation flow in the request path

1. **Pre-auth**: `authenticate()` is called with `reserve=False` — the rate limiter is not yet checked because the model group is unknown.
2. **Post-resolve**: After `resolve_group()`, `enforce_rate_limit()` checks and reserves with the estimated token count.
3. **Post-response**: `_record_tpm_usage()` replaces the estimate with actual tokens from `ctx.usage`.

---

## 12. Cost & Pricing

**File**: `wiwi/cost/pricing.py`

### CostEngine

Loads a built-in `model_prices.json` (LiteLLM-shaped pricing table). Prices are USD per token, rounded to 8 decimal places. Unpriced models cost 0.

### `cost(model_id, prompt_tokens, completion_tokens, cached_tokens)`

```python
uncached_prompt = max(0, prompt_tokens - cached_tokens)
cached_rate = p.get("cache_read_input_cost_per_token", p["input_cost_per_token"])
total = (
    uncached_prompt * p["input_cost_per_token"]
    + cached_tokens * cached_rate
    + completion_tokens * p["output_cost_per_token"]
)
return round(total, 8)
```

### Model key resolution

The cost engine tries `f"{provider_type}/{model_id}"` first (e.g., `"anthropic/claude-sonnet-4-20250514"`), then falls back to `model_id` alone. This lets the pricing table be keyed either way.

### Cache savings

`_cache_savings(model_key, usage)` computes the dollar difference between regular input rate and cache-read rate, multiplied by cached tokens:

```python
input_rate = p["input_cost_per_token"]
cache_rate = p.get("cache_read_input_cost_per_token", input_rate)
return round(cached_tokens * max(0.0, input_rate - cache_rate), 8)
```

### Token estimation fallback

`estimate_tokens(text)`: `max(1, len(text) // 4)`. Used when a provider sends no usage data in a streaming response.

### `register(model_id, input_per_token, output_per_token)`

Allows runtime price registration for custom/unlisted models.

---

## 13. Logging & Observability

**Directory**: `wiwi/logging_core/`

### Three streams that never mix

| Stream | Producers | Sinks |
|---|---|---|
| `request` | `build_log_event(ctx)` | DBSink (batched) + SSE broadcast |
| `proxy` | `log_proxy(level, message, request_id)` | stdout JSON (structlog) + SSE broadcast |
| `audit` | `log_audit(actor, action, target, diff)` | Synchronous DB write |

**Nothing here ever blocks a response.** The DB sink degrades to drop+count when slow (`dropped_request_logs` counter).

### LogEvent

**File**: `wiwi/logging_core/events.py`

A single dataclass with fields for all three streams (unused fields default to empty/zero):

- **Request fields**: `surface`, `key_alias`, `model_group`, `provider`, `provider_key_label`, `status`, `error_code`, `tok_in`/`tok_cached`/`tok_reasoning`/`tok_out`, `tps`, `ttft_ms`, `latency_ms`, `cost`, `was_stream`, `cache_hit`, `cache_savings`, `attempts` (list of attempt dicts).
- **Proxy fields**: `level` (debug/info/warn/error), `message`.
- **Audit fields**: `actor`, `action`, `target`, `diff`.

### LoggingSubsystem

**File**: `wiwi/logging_core/subsystem.py`

```
request_q (50k) ──► _pump worker ──► SSEBroadcastSink + DBSink (batched up to 200)
proxy_q   (10k) ──► _pump worker ──► SSEBroadcastSink + structlog (stdout)
audit                       ──► DBSink.write_audit (synchronous)
```

### Batch pump

The `_pump` worker drains its queue in batches: it takes the first item, then drains up to 199 more via `get_nowait()` before emitting the batch. This amortizes DB write overhead.

### SSEBroadcastSink

- **Ring buffer**: 500 events per stream (`deque(maxlen=500)`), enabling `Last-Event-ID` replay.
- **Subscribe**: Returns an `asyncio.Queue(maxsize=1000)`.
- **Publish**: Increments a global sequence counter, appends to ring, fans out to all subscriber queues. `QueueFull` → drop (slow admin client doesn't backpressure the gateway).
- **Replay**: `replay(stream, last_event_id)` returns all events with sequence > `last_event_id`.

### Admin SSE stream (`GET /admin/stream`)

- Uses `Last-Event-ID` header for replay.
- Merges request + proxy streams via two `forward` tasks into a combined `asyncio.Queue`.
- Emits `: connected\n\n` immediately (some ASGI stacks gate header forwarding on first body chunk).
- 15-second keepalive: emits `: ping\n\n` on timeout (EventSource ignores comments).
- Uses plain `StreamingResponse` (not `sse-starlette`) because `EventSourceResponse` stalls behind `BaseHTTPMiddleware` on this stack.

### DBSink

**File**: `wiwi/logging_core/db_sink.py`

SQLite persistence with two tables:

- `request_logs`: All request fields + `attempts` as JSON text. Batched inserts.
- `audit_logs`: `ts`, `actor`, `action`, `target`, `diff` (JSON). Synchronous inserts.

`read_requests(limit)` returns newest-first rows shaped like `public_dict(LogEvent)` so the admin UI treats ring-backed and DB-backed entries identically.

### Stats rollup

**File**: `wiwi/server/stats.py`

Pure functions over `LogEvent` lists — unit-testable without a DB.

- `overview(events, minutes)`: Request count, error rate, RPM, token breakdown (in/cached/reasoning/output), cache hit rate, TPS avg/p95, TTFT p95, latency p95, total cost, cache savings.
- `timeseries(events, bucket, metric, minutes)`: Bucketed series (minute granularity). Token metric returns 4 stacked sums (in/cached/reasoning/output). TPS metric returns avg + p95 per bucket.

Events with `tps == 0` or `ttft_ms == 0` (non-streaming or missing timing) are excluded from those specific aggregates only.

---

## 14. Admin API

**File**: `wiwi/server/app.py`

All `/admin/*` endpoints require the master key (`Authorization: Bearer` or `x-api-key`). Authenticated via `hmac.compare_digest` against the configured `master_key`.

### Virtual keys

| Method | Route | Purpose |
|---|---|---|
| POST | `/admin/keys/generate` | Mint a virtual key (budget/RPM/TPM/models/TTL/custom_key) |
| GET | `/admin/keys` | List all virtual keys (masked, no plaintext) |
| PATCH | `/admin/keys/{id}` | Update limits (`max_budget`/`rpm`/`tpm`/`models`/`expires_at`) |
| POST | `/admin/keys/{id}/disable` | Disable/enable a key |
| DELETE | `/admin/keys/{id}` | Revoke a key |

### Providers & key pools

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/providers` | Provider + key-pool status (masked secrets, weights, cooldowns, health) |
| POST | `/admin/providers` | Add a provider at runtime |
| GET | `/admin/providers/{name}/models` | Fetch model IDs live from upstream |
| POST | `/admin/providers/{name}/keys` | Add a key to a pool |
| PATCH | `/admin/providers/{name}/keys/{label}` | Patch weight/enabled/reset_status |

### Model groups & routing

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/models` | Inspect groups, deployments, weights, availability, p95, cooldowns |
| PATCH | `/admin/model-groups/{name}` | Update weights (atomic: all validated before any mutation) + strategy |
| POST | `/admin/model-groups/{name}/deployments` | Attach a provider deployment to a group |

### Logs & stats

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin/logs/requests` | Per-request logs (DB-backed, or ring buffer fallback) |
| GET | `/admin/logs/proxy` | Proxy-level logs (ring buffer, last 500) |
| GET | `/admin/stream` | SSE live tail with `Last-Event-ID` replay |
| GET | `/admin/stats/overview` | Aggregate stats (p95 latency, cost, tokens) |
| GET | `/admin/stats/timeseries` | Time-bucketed series (minute buckets, tokens or TPS) |
| GET/PUT | `/admin/alert-rules` | Spend/error alert rules (storage only; evaluation engine post-MVP) |

### Audit logging

All admin mutations emit an audit event (`log_audit`) with `actor="master"`, `action`, `target`, and `diff`. Written synchronously to the `audit_logs` table.

### Weight update atomicity

`PATCH /admin/model-groups/{name}` with `weights` validates every entry before mutating any live routing state:

```python
idents = {f"{d.provider.name}/{d.model_id}": d for d in deps}
unknown = set(weights) - set(idents)
if unknown: return 400
# all validated — now mutate
for ident, w in parsed.items():
    idents[ident].weight = w
```

---

## 15. Admin Web UI

**Directory**: `web/`

React 19 + TypeScript + Vite + Tailwind 4, built with bun.

### Pages

| Route | Page |
|---|---|
| `/login` | Master key login |
| `/` | Dashboard (live stats, charts, top keys) |
| `/providers` | Provider list with key-pool status |
| `/providers/:name` | Provider detail (add/patch/disable keys) |
| `/keys` | Virtual keys management |
| `/models` | Model groups (edit routing/weights live) |
| `/request-logs` | Request log table |
| `/proxy-logs` | Proxy log stream |
| `/usage` | Usage analytics |
| `/analytics` | Deep analytics |
| `/budgets` | Budgets & alerts |
| `/settings` | Settings |

### Design system

Dark-only admin console. Near-black surfaces (`#050505` / `#0a0a0a`), hairline white borders (`rgba(255,255,255,0.04)`), blue primary accent (`#3b82f6`) with violet/fuchsia secondary. Tiny uppercase mono labels, tabular numeric values. CSS custom properties under `[data-admin]` scope.

### Build

```bash
cd web && bun install && bun run build   # → wiwi/server/static/
bun run dev                               # Vite dev server, proxies to :4000
```

The built SPA is served at `/admin/ui` with SPA history fallback (`SPAStaticFiles` — unknown paths get `index.html`).

---

## 16. Error Model

**File**: `wiwi/providers/base.py`

### WiwiError

```python
class WiwiError(Exception):
    status: int
    etype: str       # invalid_request_error | authentication_error | permission_error |
                    # not_found_error | rate_limit_error | budget_exceeded |
                    # api_connection_error | timeout | service_unavailable |
                    # context_window_exceeded | content_policy_violation | api_error
    message: str
    retryable: bool
    retry_after: float | None
```

### `error_from_provider_status(status, body_text, provider)`

Normalizes upstream HTTP errors into `WiwiError`:

| HTTP status | etype | retryable |
|---|---|---|
| 401, 403 | `authentication_error` | True (failover to next key) |
| 429 | `rate_limit_error` | True |
| 504 | `timeout` | True |
| 408, 500, 502, 503, 529 | `api_connection_error` | True |
| 400 (context/max/tokens) | `context_window_exceeded` | False |
| 400 (other) | `invalid_request_error` | False |
| other ≥500 | `api_error` | True |
| other <500 | `api_error` | False |

### Retryable status set

`RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}`

### Dialect-correct error bodies

Each wire codec renders the error in the dialect the client expects:

- OpenAI Chat / Responses: `{"error": {"message": ..., "type": ..., "code": ...}}`
- Anthropic Messages: `{"type": "error", "error": {"type": ..., "message": ...}}`

### 401/403 failover

Auth failures keep their HTTP status but are `retryable=True`. This means the router invalidates the key (`ProviderKey.mark_invalid()`) and fails over to the next key in the pool instead of hard-failing the client. If all keys are invalid, the error propagates to the client.

---

## 17. File Map

### Core

| File | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …` → `load_config` → `create_app` → uvicorn) |
| `wiwi/config.py` | YAML → pydantic models; env interpolation; fail-fast validation |
| `wiwi/ir/types.py` | Canonical IR: parts, messages, tools, params, usage, request, response |
| `wiwi/core/context.py` | `RequestContext` — mutable holder threaded through the pipeline |
| `wiwi/core/gateway.py` | Surface-agnostic engine: `complete`/`stream`, pricing, log events |
| `wiwi/server/app.py` | FastAPI factory; `run_chat_like()`; `/admin/*` API; SPA mount |
| `wiwi/server/stats.py` | Admin rollups (pure functions over LogEvent lists) |

### Wire codecs (inbound)

| File | Dialect |
|---|---|
| `wiwi/wire/openai_chat.py` | OpenAI Chat Completions |
| `wiwi/wire/openai_responses.py` | OpenAI Responses (Codex CLI) |
| `wiwi/wire/anthropic_messages.py` | Anthropic Messages (Claude Code) |

### Provider adapters (outbound)

| File | Provider |
|---|---|
| `wiwi/providers/base.py` | `ProviderAdapter` protocol, `WiwiError`, `error_from_provider_status` |
| `wiwi/providers/registry.py` | `get_adapter(provider_type)` |
| `wiwi/providers/openai_adapter.py` | OpenAI + openai-compatible |
| `wiwi/providers/anthropic_adapter.py` | Anthropic |
| `wiwi/providers/gemini_adapter.py` | Google Gemini |

### Router

| File | Role |
|---|---|
| `wiwi/router/router.py` | `Deployment`, `ProviderAccount`, `ProviderKey`, `Router`, `execute_with_retries` |

### Streaming

| File | Role |
|---|---|
| `wiwi/streaming/deltas.py` | `IRStreamDelta` taxonomy (the streaming contract) |
| `wiwi/streaming/sse.py` | `LineSSEParser` (upstream), `sse_frame` (downstream) |

### Auth

| File | Role |
|---|---|
| `wiwi/auth/keys.py` | Key generation, SHA-256 hashing, constant-time verify, masking |
| `wiwi/auth/service.py` | `AuthService`, SQLite `vkeys` table, 60s cache with eviction |

### Cost & rate limiting

| File | Role |
|---|---|
| `wiwi/cost/pricing.py` | `CostEngine` over `model_prices.json`; token estimation |
| `wiwi/ratelimit/memory.py` | Sliding-window RPM/TPM; global + per-key scopes |

### Logging

| File | Role |
|---|---|
| `wiwi/logging_core/events.py` | `LogEvent` dataclass (three streams) |
| `wiwi/logging_core/subsystem.py` | `LoggingSubsystem`, `SSEBroadcastSink`, queue pumps |
| `wiwi/logging_core/db_sink.py` | SQLite `request_logs` + `audit_logs` tables |

### Web UI

| File | Role |
|---|---|
| `web/src/main.tsx` | Router, auth gate, route definitions |
| `web/src/components/Layout.tsx` | App shell: sidebar, topbar, ambient backdrop |
| `web/src/components/ui.tsx` | Reusable UI kit: Card, Button, StatCard, Table, Dialog, etc. |
| `web/src/pages/*.tsx` | One routed page per admin concern |
| `web/src/styles.css` | Tailwind 4 + admin design system CSS |
| `web/src/theme.ts` | Theme helpers (class-based dark mode) |
| `web/src/api/client.ts` | HTTP client with token storage |
| `web/src/api/auth.tsx` | Auth context provider |
| `web/src/api/stream.tsx` | SSE stream provider |

### Other

| File | Role |
|---|---|
| `wiwi.yaml.example` | Tracked config template |
| `bench.py` | Standalone benchmark harness (TTFT/latency/TPS) |
| `Dockerfile` | Multi-stage build (uv builder → python:3.12-slim) |
| `docker-compose.yml` | Compose with optional `pg` profile |
| `pyproject.toml` | Deps, entry point, pytest/ruff config |
