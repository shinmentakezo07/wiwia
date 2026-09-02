# wiwi — Architecture

wiwi is a self-hosted LLM gateway and proxy. It exposes **three native API surfaces** — OpenAI Chat Completions, OpenAI Responses (for Codex CLI / Agents SDK), and Anthropic Messages (for Claude Code / Anthropic SDK) — routes every request to the right provider behind the scenes, and adds the operational layer LiteLLM is known for: virtual keys, budgets, rate limits, load balancing, retries, fallbacks, spend tracking, and request logs.

Any inbound surface can reach any provider. A client that calls `/v1/messages` gets Anthropic-format responses even if the backing deployment is OpenAI, and vice versa. Translation between all wire formats goes through one canonical internal representation (IR), so adding a surface or a provider costs exactly two codecs, never N×N converters.

The design mirrors LiteLLM's mental model (config-driven `model_list`, model groups of deployments, a central router, async spend logging) so anyone who knows LiteLLM can operate wiwi.

Companion docs: `CORE.md` (handler pipeline, RequestContext holder, and the separated logging / reasoning / cache / streaming subsystems with the delta-chunk flow), `ADMIN.md` (provider key pools, admin UI, realtime), `TECHSTACK.md` (library choices), `MVP.md` (scope), `PLAN.md` (build phases).

---

## 1. Design principles

1. **Clients never change.** Whatever dialect a client speaks — OpenAI SDK, Codex CLI, Claude Code, LangChain — wiwi answers natively in that dialect.
2. **One canonical IR.** All translation is hub-and-spoke: dialect → IR → provider. No pairwise translators.
3. **Nothing blocking in the hot path.** Auth reads are cache-first. Spend writes, rate-limit accounting, and log writes happen after the response is sent, as background tasks.
4. **Config file first, database second.** A single `wiwi.yaml` bootstraps everything. The database stores dynamic state (keys, spend, logs).
5. **Every provider is an adapter; every API surface is a codec.** Core code never branches on names.
6. **Boring, debuggable tech.** One process, one config file, SQLite by default, Postgres/Redis when you scale.

---

## 2. High-level architecture

```
   OpenAI SDK / LangChain      Codex CLI / Agents SDK      Claude Code / Anthropic SDK
   POST /v1/chat/completions   POST /v1/responses          POST /v1/messages
        Authorization: Bearer sk-wiwi-…   (or x-api-key on /v1/messages)
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────┐
│                          wiwi gateway                             │
│                                                                   │
│  0. Wire codec          decode inbound dialect → canonical IR     │
│                         (encode outbound dialect ← IR at exit)    │
│  1. Auth layer          validate key (cache first, DB on miss),   │
│                         budget check, model access check          │
│  2. Rate limiter        rpm/tpm sliding windows: global, key,     │
│                         team, deployment                          │
│  3. Router              pick deployment (load balancing),         │
│                         retries, cooldowns, fallbacks             │
│  4. Provider adapter    translate IR → provider-native request,   │
│                         stream/non-stream                         │
│  5. HTTP client         httpx async call to provider endpoint     │
│                                                                   │
│  6. Post-response (background, never blocks the client):          │
│     • token + cost calculation      • spend upserts               │
│     • request log row               • rate-limit accounting       │
│     • webhook/callback fan-out                                    │
└──────────────┬──────────────────────────────┬─────────────────────┘
               │                              │
               ▼                              ▼
        SQLite / PostgreSQL             Redis (optional)
        keys, teams, spend,             key cache, rate-limit
        request logs                    counters (multi-instance)
               │
               ▼
   LLM providers: OpenAI, Anthropic, Google AI Studio / Vertex,
   Azure OpenAI, Bedrock, Ollama, vLLM, any OpenAI-compatible URL
```

---

## 3. Life of a request

1. **Client sends request** to one of the native surfaces. The path selects the wire codec; the codec decodes the payload into IR immediately.
2. **Auth checks** (in order):
   - Extract credential: `Authorization: Bearer` on all surfaces; `x-api-key` also accepted on `/v1/messages` (Claude Code sends that).
   - Hash (SHA-256), look up in memory/Redis key cache; on miss, DB lookup, then populate cache with short TTL.
   - Reject if: unknown key, expired, disabled, over budget, or requesting a model the key may not use.
3. **Rate limiting**: sliding-window counters for global, team, and key limits. Rejection returns `429` with `Retry-After`.
4. **Route resolution**: requested `model` matched against `model_list`. Deployments sharing a `model_name` form a **model group**; router picks one deployment.
5. **Provider adapter**: encodes IR into the provider's native payload (auth headers, body shape, quirks such as Anthropic's mandatory `max_tokens`).
6. **Upstream call**: httpx sends it. On connect errors, timeouts, `408`, `429`, `5xx`: retry another deployment in the group (backoff + jitter), then fall back to backup groups per `fallbacks`.
7. **Response path**: provider bytes decode into IR deltas; the *inbound* wire encoder streams them back in the caller's dialect. A `/v1/messages` caller receives `message_start`/`content_block_delta`/… events even when the backend is GPT; a `/v1/chat/completions` caller receives `chat.completion.chunk` frames even when the backend is Claude.
8. **Background processing** (after the last byte reaches the client): token count, cost from pricing table, spend upserts, `request_logs` row, limiter accounting, latency stats, webhook fan-out.

No database transaction spans the request. If the DB is briefly down, traffic still flows; logging degrades gracefully.

---

## 4. Components

### 4.1 API layer (FastAPI)

| Endpoint | Dialect | Clients |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat Completions | OpenAI SDK, LangChain, most tools |
| `POST /v1/completions` | OpenAI legacy text | legacy SDKs |
| `POST /v1/embeddings` | OpenAI embeddings | RAG pipelines |
| `POST /v1/responses` | OpenAI Responses API | Codex CLI, OpenAI Agents SDK |
| `POST /v1/messages` | Anthropic Messages API | Claude Code, Anthropic SDK |
| `POST /v1/messages/count_tokens` | Anthropic token counting (local estimate) | Claude Code |
| `GET /v1/models` | OpenAI-style list (also serves Claude Code's gateway discovery) | all |
| `GET /health` | liveness + upstream reachability | ops |
| `POST /key/generate`, `GET /key/info`, `POST /key/update`, `POST /key/delete` | admin (master key) | admins |
| `GET /spend/report`, `GET /logs/request` | admin aggregations | admins |

Middleware chain: CORS → request id → auth → rate limit → handler.

### 4.2 Translation layer: wire codecs, IR, provider adapters

Three inbound dialects, several provider dialects. Hub-and-spoke through one IR:

```
OpenAI Chat ──┐                                        ┌── OpenAI
Responses ────┤── decode → [ IR ] → provider adapter ─►├── Anthropic
Anthropic ────┘         ◄── encode ◄──── (response) ───└── Gemini / OAI-compat
```

**Rule: the response/stream dialect always matches the inbound surface.**

#### Canonical IR (`ir/types.py`, illustrative)

```python
Role = Literal["system", "user", "assistant", "tool"]

@dataclass
class Part:            # tagged union
    kind: Literal["text", "image", "tool_use", "tool_result", "thinking",
                  "audio", "video", "document"]   # last 3 reserved (G-multimodal)
    # text: str (+ cache_control: None | {"type":"ephemeral"} for G8 passthrough)
    # image: url|b64 · tool_use: id,name,args(dict)
    # tool_result: tool_use_id, content · thinking: text, signature

Message(role, parts)
Tool(name, description, parameters_json_schema, strict: bool | None,
     builtin: str | None, builtin_config: dict | None)   # builtin = canonical hosted-tool name
ToolChoice = Auto | None_ | Required | Named(name)
GenParams(temperature, top_p, max_tokens, stop, seed, n: int = 1,
          response_format: None | JsonObjectMode | JsonSchema(schema),
          parallel_tool_calls: bool | None,
          reasoning_effort, thinking_budget, metadata)
Request(model, messages, tools, tool_choice, gen_params, stream,
        stream_options_include_usage: bool = True)
Response(message, stop_reason, usage)
StopReason = Literal["stop", "length", "tool_call", "content_filter"]
Usage(prompt_tokens, completion_tokens, cached_tokens, reasoning_tokens)
StreamDelta  # text delta | tool-call open/args delta | thinking delta | usage-final
```

Field policies: `n > 1` is rejected on backends without native support (open question #5); `response_format=json_schema` maps to provider-native structured outputs where they exist, else degrades per `drop_params`; `cache_control` on text parts round-trips verbatim to Anthropic and is dropped (with proxy-log note) elsewhere. A builtin `Tool` (`builtin="web_search"`, config in `builtin_config`) renders as the host's native hosted-tool shape — Anthropic `web_search_20250305`, Responses `web_search`, OpenRouter `openrouter:web_search`, Gemini `google_search` — and is dropped with a warning on providers without hosted search (Chat Completions has none); unknown builtins ride along builtin-shaped and drop the same way. Canonical↔surface name mapping lives in `ir/builtin_tools.py`, the neutral hub both `wire/` and `providers/` import.

**Header forwarding**: an explicit allowlist of client headers is forwarded to upstreams — `anthropic-version`, `anthropic-beta` (comma-list merged with deployment defaults), `openai-organization`, `openai-project`, `openai-beta` — everything else is stripped. Without this, beta features (interleaved thinking, prompt caching) silently break. Response header `x-wiwi-request-id` is echoed on every response for log correlation.

Codec policy: unknown fields dropped when `drop_params: true` (default) else rejected; unsupported features degrade explicitly per config (e.g., named `tool_choice` against a provider lacking it).

#### Wire codecs (one module per inbound dialect)

Each exposes: `parse_request → IR`, `encode_response(IR) → JSON`, `encode_stream(IR deltas) → SSE frames`, `encode_error → dialect-correct error body`.

#### Key mappings

**Tools and tool calls**

| Concept | OpenAI Chat | OpenAI Responses | Anthropic Messages |
|---|---|---|---|
| Declare tool | `tools:[{type:"function", function:{name, parameters}}]` | `tools:[{type:"function", name, parameters}]` | `tools:[{name, input_schema}]` |
| Model calls tool | assistant `tool_calls:[{id, function:{name, arguments:str}}]` | output item `{type:"function_call", call_id, name, arguments}` | content block `{type:"tool_use", id, name, input}` |
| Client returns result | `{role:"tool", tool_call_id, content}` | input item `{type:"function_call_output", call_id, output}` | user block `{type:"tool_result", tool_use_id, content}` |
| Force choice | `"required"` / `{type:"function", function:{name}}` | `"required"` / `{type:"function", name}` | `{type:"any"}` / `{type:"tool", name}` |

**Stop reasons**

| IR | OpenAI `finish_reason` | Anthropic `stop_reason` |
|---|---|---|
| `stop` | `stop` | `end_turn` (or `stop_sequence`) |
| `length` | `length` | `max_tokens` |
| `tool_call` | `tool_calls` | `tool_use` |
| `content_filter` | `content_filter` | `refusal` |

**System prompt placement**: Chat system/developer messages ↔ Responses `instructions` ↔ Anthropic top-level `system` (concatenated in order).

**Thinking/reasoning**: Anthropic `thinking` blocks (with signature) ↔ Responses `reasoning` items (`encrypted_content` preserved verbatim when `store:false`, so Codex round-trips) ↔ Chat `reasoning_effort` parameter (no content). Per-route fidelity documented; lossy paths degrade visibly, never silently corrupt.

**Usage**: Chat `prompt_tokens/completion_tokens` ↔ Responses `input_tokens/output_tokens` ↔ Anthropic `input_tokens/output_tokens` (+ cache token fields carried in IR extras).

#### Provider adapters (outbound)

```python
class CredentialProvider(Protocol):
    """Seam for non-static auth (G5): static keys today; Entra-ID tokens,
    Vertex service-account OAuth, Bedrock SigV4 signing later. Key pools
    reference credentials through this interface, never raw secrets."""
    async def headers(self, dep: Deployment) -> dict[str, str]: ...
    async def sign(self, req: ProviderRequest) -> ProviderRequest: ...  # bedrock

class ProviderAdapter(Protocol):
    provider: str
    def encode_request(self, req: IRRequest, dep: Deployment) -> ProviderRequest: ...
    def decode_response(self, resp: bytes) -> IRResponse: ...
    def decode_stream_event(self, evt: SSEEvent) -> IRStreamDelta | None: ...
    def map_error(self, err: ProviderError) -> WiwiError: ...
    def credentials(self, dep: Deployment) -> CredentialProvider: ...
```

MVP adapters:

| Prefix | Target | Notes |
|---|---|---|
| `openai/` | api.openai.com | reference implementation |
| `anthropic/` | api.anthropic.com | system extraction, mandatory `max_tokens`, SSE folding, tool-use blocks |
| `gemini/` | generativelanguage.googleapis.com | `contents`/`parts`, `systemInstruction`, `generationConfig`, `usageMetadata` |
| `openai-compatible/` | any base_url | Ollama, vLLM, LM Studio, Together, Groq, DeepSeek, OpenRouter |

Later: `azure/`, `bedrock/`, `vertex_ai/`.

### 4.3 Router

Concepts (same as LiteLLM):

- **Deployment**: one credential + endpoint + params for a provider model.
- **Model group**: all deployments sharing a `model_name`; this is what clients request.
- **Cooldown**: after `allowed_fails` failures within `cooldown_time`, skip deployment for that window.

Strategies: `simple-shuffle` (default, rpm/tpm-weighted), `least-busy`, `latency-based`; `usage-based-routing` post-MVP. **Model aliases** (`model_group_alias: {gpt-4: gpt-4o}`) resolve before group lookup on every surface, so renaming never duplicates deployments.

Retries stay inside the failing group (`num_retries`, exponential backoff + jitter, honor `Retry-After`; retryable: connect errors, timeouts, `408`, `429`, `500`, `502`, `503`, `529`). Fallbacks leave the group after retries exhaust: `fallbacks`, plus `context_window_fallbacks` for context-exceeded errors. Per-deployment `timeout` overrides `router_settings.timeout`.

### 4.4 Auth and keys

Provider-side credentials are organized as **provider accounts holding key pools**: multiple API keys per provider with smooth weighted round-robin, and tier-1 balancing across providers that serve the same model id. See `ADMIN.md` for the full design (admin UI, realtime updates, analytics).

- **Master key** (`sk-wiwi-master-…`): full admin, set via env/config, never stored plaintext.
- **Virtual keys** (`sk-wiwi-…`): issued via `/key/generate`; stored hashed with alias, user, models allowlist, budget/duration, rpm/tpm, expiry. Plaintext shown once.

Header rules: `Authorization: Bearer` everywhere; `x-api-key` additionally honored on `/v1/messages` so Claude Code works unmodified. Lookup: memory cache (TTL ~60s) → Redis → DB; `/key/update` evicts actively. Budget enforcement: soft pre-check, authoritative post-request accounting.

### 4.5 Rate limiter

Sliding-window counters keyed `(scope, id, window)`, scope ∈ {global, key, team}. Backends: `memory` (default, exact single-instance) and `redis` (`INCR`+`EXPIRE` / sorted-set window) for multi-instance. Check order global → team → key; any rejection returns `429` + `Retry-After`. Accounting runs post-response except in-flight caps which increment pre-dispatch.

### 4.6 Cost engine

- Bundled `model_prices.json` (LiteLLM-shaped: `input_cost_per_token`, `output_cost_per_token`, context limits, `mode`), overridable in config for private models.
- Tokens: provider-reported `usage` preferred; tiktoken fallback for OpenAI-family; chars/4 heuristic otherwise.
- Cost attributed to key, user/team, model; rounded to 8 decimals.

### 4.7 Persistence

SQLite default (`sqlite:///./wiwi.db`); Postgres for production. SQLAlchemy 2.x + Alembic.

```text
keys            id, key_hash, key_alias, key_type(master|virtual), user_id,
                team_id, models(json), budget_id, rpm, tpm, expires_at, timestamps
teams           id, team_alias, models(json), budget_id, rpm, tpm, metadata
users           id, user_email, total_budget, metadata
budgets         id, max_budget, budget_duration, spend_to_date, reset_at
request_logs    id, request_id, key_id, team_id, user_id, surface(chat|responses|messages),
                model_group, deployment_id, provider, status, error_code,
                prompt_tokens, completion_tokens, cost, latency_ms, stream(bool),
                started_at, ended_at, metadata(json)
daily_spend     date, key_id, team_id, model_group, spend, requests, tokens (unique combo)
pricing         model_id, rates, limits, mode, source(builtin|custom)
```

Credentials resolve via `os.environ/VAR` interpolation at load; secrets never logged or stored plaintext.

### 4.8 Config system

Single `wiwi.yaml`, sections mirror LiteLLM:

```yaml
model_list:
  - model_name: gpt-4o                 # what clients send (any surface)
    wiwi_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
      rpm: 500
      tpm: 200000
  - model_name: claude-sonnet
    wiwi_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY
      max_tokens: 8192                 # adapter default injection

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  timeout: 60
  allowed_fails: 3
  cooldown_time: 30
  fallbacks:
    claude-sonnet: ["gpt-4o"]

general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: os.environ/DATABASE_URL   # optional, sqlite default
  redis_url: ""                           # optional

wiwi_settings:
  drop_params: true
  max_request_body_mb: 50
  log_requests: true
```

Any value may be `os.environ/NAME`. Startup validation fails fast with file/line-precise errors. Wildcard `model_name: "*"` passthrough ships post-MVP.

---

## 5. Streaming design

The inbound surface determines the outbound event dialect; each wire encoder keeps a tiny per-stream state machine (open block indexes, tool-call ids) to emit legal sequences:

| Surface | Outbound stream shape |
|---|---|
| `/v1/chat/completions` | `chat.completion.chunk` frames; `delta.tool_calls[i].function.arguments` fragments; final frame carries `usage` |
| `/v1/messages` | `message_start` → `content_block_start` (text / `tool_use` / thinking) → `content_block_delta` (`text_delta` / `input_json_delta` / `thinking_delta`) → `content_block_stop` → `message_delta` (stop_reason, usage) → `message_stop`; `ping` heartbeats |
| `/v1/responses` | `response.created` → `response.output_item.added` → `response.output_text.delta` / `response.function_call_arguments.delta` / reasoning deltas → `output_item.done` → `response.completed` |

Upstream provider deltas land as IR deltas; encoders translate them frame-by-frame with no whole-response buffering. Heartbeat comments keep intermediaries alive on long gaps. Client disconnect cancels the upstream call; partial usage is estimated and logged. Every stream produces one complete `request_logs` row after the final frame.

Stateless Responses mode: MVP treats each `/v1/responses` call as self-contained (Codex sends full history with `store:false`). Server-side `previous_response_id` session storage remains post-MVP. Hosted-tool *translation* (the `web_search` builtin across surfaces, see G21) shipped 2026-09-02; hosted-tool *execution extras* (citations, response traces, allowlist) are post-MVP (G22).

---

## 6. Error handling

All surfaces get their own dialect-correct envelope, produced by the wire encoder:

- OpenAI surfaces: `{"error": {"message", "type", "code"}}`
- Anthropic surface: `{"type":"error","error":{"type","message"}}` (e.g., `authentication_error`, `invalid_request_error`, `rate_limit_error`, `permission_error`, `api_error`, `overloaded_error`)

| Situation | HTTP | IR error type |
|---|---|---|
| Missing/unknown/disabled key | 401 | authentication |
| Key not allowed for model | 403 | permission |
| Malformed request | 400 | invalid_request |
| Unknown model group | 404 | not_found |
| Rate limit exceeded | 429 | rate_limit (+ `Retry-After`) |
| Budget exceeded | 429 | budget_exceeded |
| Upstream timeout after retries | 504 | timeout |
| Upstream down/exhausted | 502/503 | connection / unavailable |
| Context window exceeded | 400 | context_window_exceeded (triggers context fallbacks) |
| Provider content filter | 400 | content_policy |

Provider payloads normalize via the adapter's `map_error`; original messages preserved in logs.

---

## 7. Scaling path

| Stage | Topology | State |
|---|---|---|
| Dev | 1 uvicorn process | SQLite, in-memory limiter/cache |
| Team | 1 container, N workers | Postgres, per-worker memory caches |
| Production HA | N replicas behind LB | Postgres + Redis (limiter + key cache) |

Gateway is stateless apart from caches; scaling needs only shared Redis. Config reload (`SIGHUP` or admin endpoint) re-reads YAML without dropping in-flight requests.

---

## 8. Security

- Keys hashed at rest; constant-time comparison; plaintext shown once.
- Master key required for admin endpoints; virtual keys cannot escalate.
- Secrets resolved from env at load; redacted (`sk-…***`) in logs/API responses.
- Bodies not persisted by default; `request_logs.metadata` holds ids/sizes/flags only.
- SSRF guard: `openai-compatible/` base URLs come from config only, never client input.
- Timeouts + max body size on upstream calls; client headers forwarded only from an allowlist.

---

## 9. Repository layout

```
wiwi/
├── wiwi/
│   ├── main.py                 # CLI entrypoint: wiwi --config wiwi.yaml
│   ├── config.py               # YAML load, env interpolation, validation
│   ├── ir/
│   │   ├── types.py            # canonical IR: parts, messages, tools, usage, stop
│   │   └── builtin_tools.py    # hosted-tool registry: canonical ↔ per-surface type names
│   ├── wire/                   # inbound dialect codecs (decode→IR, encode←IR)
│   │   ├── openai_chat/        # /v1/chat/completions, /v1/completions, /v1/embeddings
│   │   ├── openai_responses/   # /v1/responses (Codex, Agents SDK)
│   │   └── anthropic_messages/ # /v1/messages (Claude Code, Anthropic SDK)
│   ├── server/
│   │   ├── app.py              # FastAPI factory, middleware chain
│   │   └── routes/
│   │       ├── openai.py       # /v1/chat/completions, /v1/responses, /v1/models
│   │       ├── anthropic.py    # /v1/messages
│   │       ├── admin.py        # /key/*, /spend/*, /logs/*
│   │       └── health.py
│   ├── auth/
│   │   ├── keys.py             # generate/hash/verify (bearer + x-api-key)
│   │   └── service.py          # lookup, cache, checks
│   ├── ratelimit/
│   │   ├── base.py, memory.py, redis.py
│   ├── router/
│   │   ├── router.py           # selection, retries, fallbacks, cooldowns
│   │   ├── deployment.py
│   │   └── strategies.py
│   ├── providers/
│   │   ├── base.py, registry.py
│   │   ├── openai_adapter/
│   │   ├── anthropic_adapter/
│   │   ├── gemini_adapter/
│   │   └── openai_compat_adapter/
│   ├── cost/
│   │   ├── pricing.py, tokens.py
│   ├── telemetry/
│   │   └── spend_logger.py     # background batch writer
│   ├── db/
│   │   ├── models.py, session.py, migrations/
│   └── observability/metrics.py
├── tests/
│   ├── unit/, integration/, golden/   # golden fixtures per dialect × provider
├── wiwi.yaml                   # sample config
├── model_prices.json
├── Dockerfile, docker-compose.yml
└── pyproject.toml
```

---

## 10. Feature parity map (LiteLLM → wiwi)

| Capability | Phase |
|---|---|
| OpenAI `/v1/chat/completions` + embeddings + models | P1 |
| Streaming translation (all dialects) | P1–P2 |
| Native Anthropic `/v1/messages` (Claude Code) | P2 |
| Native OpenAI Responses `/v1/responses` (Codex) | P2 |
| Multi-provider adapters via IR | P2 |
| Load balancing, retries, cooldowns, fallbacks | P3 |
| Master + virtual keys, model access, bearer + x-api-key | P4 |
| Rate limits (rpm/tpm), budgets | P4 |
| Spend tracking, cost engine, request logs | P5 |
| Admin REST API | P5 |
| Teams/users hierarchy | P6 |
| Admin web UI | P6 |
| Response caching (exact match) | P6 |
| Stateful Responses (previous_response_id store), hosted-tool execution extras (G22) | post-MVP |
| Guardrails/hooks, pass-through endpoints, semantic cache, SSO | post-MVP |
| WebSocket Realtime proxy (G9), audio/images/moderations passthrough (G10), hedging (G11), load shedding (G12), session affinity (G13), cost-cap pre-check (G14), files/batches (G15), raw pass-through (G16), mock provider (G17), replay (G18), priority lanes (G19), webhooks (G20) | post-MVP — full register in `MVP.md` §3.1 (G21 hosted-tool translation shipped) |
