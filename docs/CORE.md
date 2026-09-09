# wiwi — Core Runtime: Internals Reference

Module-by-module description of the runtime as built: the request holder, the gateway pump, router internals, recovery primitives, and each subsystem's public interface. Companion to [ARCHITECTURE.md](ARCHITECTURE.md) (system view) and [STREAMING.md](STREAMING.md) (streaming contract).

> Note: earlier revisions of this doc described an aspirational handler pipeline (`core/handlers/`, DeltaBus, `telemetry/`). Those were never built; the shipped design is the **Gateway + execute_with_retries** model described here.

---

## 1. Module map

```
wiwi/
├── main.py               # argparse CLI; wiwi --config wiwi.yaml → uvicorn
├── config.py             # Pydantic v2 config models, load_config/load_env, PROVIDER_TYPES
├── ir/
│   ├── types.py          # Request/Response/Message/Part/Tool/Usage dataclasses (IR)
│   └── builtin_tools.py  # canonical registry of provider-hosted tools (web_search, …)
├── wire/                 # DIALECT LAYER (the only dialect branching)
│   ├── openai_chat.py        # decode_request · encode_response · ChatStreamEncoder · error_body
│   ├── openai_responses.py   # … Responses dialect (Codex CLI / Agents SDK)
│   └── anthropic_messages.py # … Anthropic dialect (Claude Code / SDK)
├── server/
│   ├── app.py            # FastAPI factory, middleware, routes, run_chat_like, lifespan, SPA mount
│   ├── config_store.py   # DB-backed providers/keys/deployments/settings/model_prices
│   ├── stats.py          # rollup math over the LogEvent ring (pure functions)
│   └── metrics.py        # Prometheus /metrics renderer
├── core/
│   ├── context.py        # RequestContext — the single holder threaded through everything
│   ├── gateway.py        # Gateway.complete / Gateway.stream — router → adapter → pump
│   └── recovery.py       # Backoff · CircuitBreaker · parse_retry_after · build_url · HealthHealer
├── router/
│   └── router.py         # Router: groups/deployments/keys, WRR, aliases, execute_with_retries
├── providers/            # PROVIDER LAYER (the only provider branching)
│   ├── base.py           # ProviderAdapter protocol · WiwiError · ProviderKeyRef · error mappers
│   ├── registry.py       # get_adapter / fresh_adapter + import-time branch-coverage assert
│   ├── openai_adapter.py # reference adapter (also serves openai-compatible/gmicloud/bai shapes)
│   ├── anthropic_adapter.py · gemini_adapter.py · openrouter_adapter.py · nim_adapter.py
│   ├── cline_adapter.py (+ cline_oauth.py, cline_auto_refresh.py)
│   ├── workbuddy_adapter.py (+ workbuddy_auth.py, workbuddy_auto_refresh.py)
│   └── opencode_adapter.py (+ opencode_version.py)
├── streaming/            # delta taxonomy + guards + tape/journals (see STREAMING.md)
│   ├── deltas.py · sse.py · resume.py · tape_store.py
│   ├── coalesce.py · loopdetect.py · partial_json.py · validation.py
├── cache/                # exact-match response cache (off by default)
│   ├── interface.py · response_cache.py · redis_cache.py · keygen.py
├── ratelimit/            # sliding-window rpm/tpm
│   ├── memory.py · redis.py
├── auth/
│   ├── service.py        # AuthService: master key + virtual key auth
│   ├── keys.py           # virtual-key mint/hash/compare (SHA-256, constant-time)
│   └── users.py          # user accounts, PBKDF2 passwords, signed session cookies
├── cost/
│   └── pricing.py        # CostEngine: token → USD; estimate_tokens_async fallback
└── logging_core/
    └── events.py         # LogEvent (request | proxy | audit); sinks live in server/app wiring

web/                      # React 19 + Vite 6 + Tailwind 4 SPA (admin console + public front)
```

---

## 2. RequestContext (`core/context.py`)

The single mutable holder passed through every stage. Fields:

`surface` (`chat | responses | messages`), `ir_req`, `started`, `request_id`, `auth`, `raw_body_bytes`, `group`, `deployment`, `provider_key`, `attempts: list[AttemptRecord]`, `first_token_at`, `last_token_at`, `usage`, `cost`, `cache_hit`, `stop_reason`, `status`, `error`, `log_buffer`, `metadata`, `cancel: asyncio.Event`, `_defer_key_credit`.

- `metadata` accumulates advisory notes (e.g. `tool_args_violations`).
- `_defer_key_credit` marks the streaming path: `execute_with_retries` must NOT credit the key at connect time; the pump credits once output actually flows.

## 3. Gateway (`core/gateway.py`)

Surface-agnostic engine executing one IR request:

- `Gateway.complete(ctx)` — non-streaming: encode → call → decode → IR `Response`.
- `Gateway.stream(ctx)` — async generator of IR deltas: encode → connect (via `execute_with_retries`) → `_pump_once()` decoding upstream SSE → guard chain → `asyncio.Queue(maxsize=4096)` → caller's encoder.
- Owns: TTFT/last-token timing, partial billing on mid-stream death (`_price_partial`), `_note_stream_failure` (key cooldown + deployment fail counters), client-disconnect handling (`ctx.cancel` + `_PUMP_CANCEL_GRACE_S` grace before hard cancel), tool-args validation flags, DeltaCoalescer and LoopDetector wiring.
- Knows nothing about dialects or providers — those live in `wire/` and `providers/`.

## 4. Router (`router/router.py`)

- `Router.resolve_group(name)` — alias chain + `alias_to_provider` → model group.
- `pick_deployment` — smooth WRR over healthy deployments (exact-proportion semantics pinned by tests; no jitter).
- `execute_with_retries` — wraps connect + first-token phase: retry per attempt budget with `Backoff` (honors `Retry-After`), failover across deployments, then fallback groups; records `AttemptRecord`s on the context; skips/cool-downs failing keys.
- Health scoring (opt-in `router_settings.health_model: scored`): EWMA latency + success-rate window gate deployment/key selection; `adaptive_cooldown` extends cooldowns by recent failure rate.

## 5. Recovery (`core/recovery.py`)

Shared primitives (contracts only — no dialect/provider branching; must never import router/gateway to avoid a cycle):

- `Backoff(base_s, cap_s, jitter_s)` — exponential delay + jitter honoring upstream `retry_after`: `min(cap, max(retry_after, base·2^attempt)) + jitter`.
- `CircuitBreaker` — per-dependency open/half-open/closed states; used by the Cline/WorkBuddy auto-refresh services.
- `parse_retry_after`, `build_url` — small shared helpers extracted from three deduplicated sites.
- `HealthHealer` (opt-in via `HealerSettings`, default off) — background service: probes sick keys/deployments with 1-token requests, graduated probation recovery, wired in lifespan alongside the refresh services.

## 6. Wire codecs (`wire/`)

Each dialect module owns exactly four things:

| Module | decode_request | encode_response | StreamEncoder | error_body |
|---|---|---|---|---|
| `openai_chat.py` | Chat JSON → IR | IR → Chat JSON | `ChatStreamEncoder` → `chat.completion.chunk` frames | OpenAI error object |
| `openai_responses.py` | Responses JSON → IR | IR → Responses JSON | `ResponsesStreamEncoder` → `response.*` SSE events | Responses-style error |
| `anthropic_messages.py` | Messages JSON → IR (incl. `count_tokens`) | IR → Messages JSON | `AnthropicStreamEncoder` → `message_start/content_block_*/message_delta/stop` | `{"type":"error","error":{…}}` with Anthropic `etype` strings |

Encoders are one-directional state machines over the IR delta taxonomy; they assume legal ordering (guaranteed by adapters — see [STREAMING.md](STREAMING.md) §1).

## 7. Providers (`providers/`)

- `base.py`: `ProviderAdapter` protocol (`encode_request`, `_call`, `decode_stream_event`, …), `WiwiError` taxonomy, `ProviderKeyRef`, `error_from_provider_status`, `status_for_key_pool`.
- `registry.py`: `get_adapter(type)` returns the shared singleton (reset on hand-out; sync-only use) — `fresh_adapter(type)` returns a private instance (request hot path; adapters hold per-stream decode state across awaits). Import-time assert: every `PROVIDER_TYPES` entry has a branch.
- `_OPENAI_WIRE_TYPES = {openai, openai-compatible, gmicloud, bai}` fall through to `OpenAIAdapter`; the rest have dedicated adapters (see [PROVIDERS.md](PROVIDERS.md) for per-provider quirks).

## 8. Subsystems

### Auth (`auth/`)
`AuthService.authenticate` distinguishes master key (admin), virtual key (client), session cookie (user). Virtual keys: SHA-256 at rest, constant-time compare, model allowlists/budgets/rpm-tpm/expiry, owner (user) linkage. Users: PBKDF2 password hashing, HMAC-signed HttpOnly cookies, roles.

### Rate limiting (`ratelimit/`)
Sliding-window counters per key: `memory.py` (default) and `redis.py` (multi-instance). Enforced after auth, before routing.

### Response cache (`cache/`)
Exact-match, non-streaming only, no builtin tools. Key = normalized IR + group + surface + key id (`keygen.py`). Backends: `MemoryResponseCache` (LRU + lazy TTL) and Redis. Per-request bypass: `x-wiwi-no-cache`. Hit accounting uses `response_cache_hit`, never `cache_hit` (that's the provider prompt-cache flag).

### Cost (`cost/`)
`CostEngine` resolves tokens → USD from DB-backed `model_prices` (`server/config_store.py`) with a bundled fallback table; `estimate_tokens_async` provides the chars/4-style estimate when usage is missing (`UsageFinal.estimated=True`).

### Logging (`logging_core/`)
`LogEvent` streams: **request** (DB + SSE to the UI), **proxy** (stdout JSON + SSE), **audit** (sync DB for admin mutations). The in-memory ring buffer feeds `/admin/stats/*` and `/metrics`; long ranges (7d/30d/all-time) come from DB aggregates.

### Server plumbing (`server/`)
`app.py`: pure-ASGI `RequestIdMiddleware` (request id, body-size guard incl. chunked/HTTP2, latency headers — replaces BaseHTTPMiddleware so shutdown cancellation can't kill stream pumps), `ORJSONResponse`, `run_chat_like` pipeline, lifespan (DB init, refresh services, healer, journal store), `_SPAStaticFiles` mount at `/admin/ui`. `config_store.py`: DB-backed runtime config. `stats.py`/`metrics.py`: shared nearest-rank percentile so admin rollups and Prometheus cannot drift.

## 9. Cross-cutting invariants

1. No `wire`/`providers` imports outside those layers (checked by convention + review; the registry assert catches missing branches, not misplaced ones).
2. `core/recovery.py` must never import `wiwi.router` or `wiwi.core.gateway` (cycle).
3. Frozen dataclasses for IR and all stream deltas; per-stream mutable state lives on adapter instances.
4. Library modules never import `wiwi.server.app` at module level.
5. All async: `httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths; `structlog`, never `print`.
