# wiwi — Architecture

wiwi is a self-hosted LLM gateway and proxy. It exposes **three native API surfaces** — OpenAI Chat Completions, OpenAI Responses (for Codex CLI / Agents SDK), and Anthropic Messages (for Claude Code / Anthropic SDK) — routes every request to the right provider behind the scenes, and adds the operational layer LiteLLM is known for: virtual keys, budgets, rate limits, load balancing, retries, fallbacks, spend tracking, and request logs.

Any inbound surface can reach any provider. A client that calls `/v1/messages` gets Anthropic-format responses even if the backing deployment is OpenAI, and vice versa. Translation between all wire formats goes through one canonical internal representation (IR), so adding a surface or a provider costs exactly two codecs, never N×N converters.

The design mirrors LiteLLM's mental model (config-driven `model_list`, model groups of deployments, a central router, async spend logging) so anyone who knows LiteLLM can operate wiwi.

> **Trust the code over any doc** — this file describes the system as built (see `wiwi/` and `web/`). Companion docs: [CORE.md](CORE.md) (module-by-module internals), [STREAMING.md](STREAMING.md) (delta contract, failover/journals), [API_REFERENCE.md](API_REFERENCE.md), [CONFIG.md](CONFIG.md), [PROVIDERS.md](PROVIDERS.md), [ADMIN.md](ADMIN.md), [DEVELOPMENT.md](DEVELOPMENT.md).

---

## 1. Design principles

1. **Clients never change.** Whatever dialect a client speaks — OpenAI SDK, Codex CLI, Claude Code, LangChain — wiwi answers natively in that dialect.
2. **One canonical IR.** All translation is hub-and-spoke: dialect → IR → provider. No pairwise translators.
3. **Nothing blocking in the hot path.** Auth reads are cache-first. Spend writes, rate-limit accounting, and log writes happen after the response is sent, as background tasks.
4. **Config file first, database second.** A single `wiwi.yaml` bootstraps everything; the DB (SQLite/Postgres) backs admin-managed state (providers, keys, deployments, prices, users, logs) so the UI can mutate it at runtime.
5. **Dialects and providers are leaf modules.** All branching on dialect/provider lives in `wiwi/wire/` and `wiwi/providers/`; `core/`, `router/`, `auth/`, `streaming/` are generic.
6. **Failures are first-class.** Retries, failover, cooldowns, mid-stream resume, durable journals, and an opt-in healer are built into the request path, not bolted on.

## 2. High-level view

```
             ┌────────────────────────  wiwi  ────────────────────────┐
 Clients     │                                                        │
 OpenAI SDK ─┼─► /v1/chat/completions ─┐                              │
 Codex CLI ──┼─► /v1/responses ────────┤  wire codecs (decode)        │
 Claude Code ┼─► /v1/messages ─────────┘          │                   │
             │                                    ▼                   │
             │                    canonical IR (wiwi/ir/types.py)     │
             │                                    │                   │
             │              auth ─ ratelimit ─ router (WRR+health)    │
             │                                    │                   │
             │                     gateway pump (streaming guards)    │
             │                                    │                   │
             │        ┌──────────┬──────────┬────┴─────┬─────────┐    │
 Admin UI ───┼─► /admin/* + /auth/* + /public/*        │         │    │
 (SPA)       │        ▼          ▼          ▼          ▼         ▼    │
             │   11 provider adapters (openai, anthropic, gemini,     │
             │   openai-compatible, openrouter, gmicloud, bai,        │
             │   nvidia-nim, cline, workbuddy, opencode)              │
             └────────────────────────────────────────────────────────┘
```

## 3. Request life cycle (`wiwi/server/app.py:run_chat_like`)

1. **Parse** JSON body (`app.json_body`) — with a body-size guard and request-ID middleware wrapping every route.
2. **Wire decode → IR** (`wiwi/wire/<dialect>.py:codec_decode`).
3. **Authenticate** (`wiwi/auth/service.py:AuthService`) — master key for `/admin/*`; virtual key `sk-wiwi-…` (SHA-256-hashed at rest, constant-time compare) for client traffic; session cookie for `/auth/*` users.
4. **Resolve group** (`wiwi/router/router.py:Router.resolve_group`) — alias chain + `alias_to_provider`.
5. **Enforce rate limit** (`wiwi/ratelimit/{memory,redis}.py`) — sliding-window rpm/tpm per key.
6. **Build `RequestContext`** (`wiwi/core/context.py`) — the single mutable object threaded through every stage.
7. **Dispatch**: `ir_req.stream ? gateway.stream(ctx) : gateway.complete(ctx)` — both wrapped by `router.execute_with_retries` for failover over deployments + fallback groups.
8. **Provider call**: `wiwi/providers/registry.py:fresh_adapter(type)` → `adapter.encode_request` → `_call` → `decode_stream_event`.
9. **Stream pump** (`wiwi/streaming/` + `wiwi/core/gateway.py`): SSE parse, coalescing, loop detection, partial-JSON tool args, schema validation, StreamTape failover/resume, journal writes.
10. **Outbound encode**: `_encoder_for(surface)` → `ChatStreamEncoder | ResponsesStreamEncoder | AnthropicStreamEncoder` (streaming) or `codec_encode_response` (non-streaming).
11. **Post** (after the response is on the wire): log the request, record TPM, update spend (budget cap → 402), translate `WiwiError` → per-surface `error_body`.

### Hard invariants

- All dialect/provider branching stays inside `wiwi/wire/` and `wiwi/providers/`.
- `wiwi/providers/registry.py` has an import-time `assert` that fails loudly when a `PROVIDER_TYPES` entry has no matching branch.
- `RequestContext` is the single mutable object threaded through every stage.

## 4. Component map

| Layer | Location | What it does |
|---|---|---|
| HTTP app | `wiwi/server/app.py` | FastAPI factory, pure-ASGI middleware chain (request id, body-size guard, latency headers), all routes, lifespan, SPA mount |
| Wire codecs | `wiwi/wire/{openai_chat,openai_responses,anthropic_messages}.py` | `decode_request`, `encode_response`, `StreamEncoder`, `error_body` per dialect |
| IR | `wiwi/ir/types.py`, `ir/builtin_tools.py` | `Part` union (`TextPart`, `ImagePart`, `ToolUsePart`, `ToolResultPart`, `ThinkingPart`, `AudioPart`, `DocumentPart`), `Message`, `Tool`, `ToolChoice*`, `ResponseFormat`, `GenParams`, `Request`, `Usage`, `AssistantTurn`, `Response`; hosted-tool registry |
| Router | `wiwi/router/router.py` | Groups/deployments/keys, WRR picking, alias chains, `execute_with_retries`, health scoring (EWMA latency + success rate), adaptive cooldowns |
| Gateway | `wiwi/core/gateway.py` | Executes one IR request router → adapter → httpx; pumps deltas; TTFT/TPS timing; partial billing; stream-failure accounting |
| Context | `wiwi/core/context.py` | `RequestContext` (surface, ir_req, auth, group, deployment, provider_key, attempts, usage, cost, stop_reason, status, error, log_buffer, metadata, cancel, …) |
| Recovery | `wiwi/core/recovery.py` | Shared `Backoff`, `CircuitBreaker`, `parse_retry_after`, `build_url`; opt-in `HealthHealer` (1-token probes, graduated probation recovery). Used by router retries, Cline/WorkBuddy refresh services, healer. Imports nothing from router/gateway (cycle safety) |
| Streaming | `wiwi/streaming/` | `deltas.py` taxonomy, `sse.py` parse/encode, `resume.py` StreamTape, `tape_store.py` JournalStore, `partial_json.py`, `validation.py`, `coalesce.py`, `loopdetect.py` — see [STREAMING.md](STREAMING.md) |
| Cache | `wiwi/cache/` | Opt-in exact-match response cache (`CacheSettings`, off by default): non-streaming only, keyed on normalized IR + group + surface + key id; bypass via `x-wiwi-no-cache`; memory or Redis backend |
| Rate limit | `wiwi/ratelimit/` | Sliding-window rpm/tpm; `memory.py` default, `redis.py` for multi-instance |
| Auth | `wiwi/auth/` | `service.py` AuthService, `keys.py` virtual keys (hashed), `users.py` user accounts + PBKDF2 passwords + signed cookies |
| Cost | `wiwi/cost/` | Token → USD engine; per-model prices from the DB (`config_store`) with bundled fallback |
| Logging | `wiwi/logging_core/` | Three-stream logger: request (DB + SSE), proxy (stdout + SSE), audit (sync DB); `LogEvent` ring buffer feeds stats/metrics |
| Config | `wiwi/config.py` | Pydantic v2 models, `load_config`/`load_env`, `PROVIDER_TYPES`, `os.environ/NAME` interpolation |
| Config store | `wiwi/server/config_store.py` | DB-backed providers/keys/deployments/settings/model_prices (runtime-mutable) |
| Stats/Metrics | `wiwi/server/stats.py`, `server/metrics.py` | Admin rollups (ring buffer for short ranges, DB aggregates for 7d/30d/all-time) and Prometheus `/metrics` |
| Frontend | `web/` | React 19 + Vite 6 + Tailwind 4 SPA; admin console + public front; built to `wiwi/server/static/`, served at `/admin/ui` |

## 5. Router & recovery

- **Model groups** (`model_name`) hold **deployments** (provider + `model_id` + weight/overrides). WRR splits traffic proportionally; `resolve_group` follows alias chains.
- **Key pools**: each provider account holds multiple real keys with weights and health states (`active | cooling | invalid | disabled`). Errors trigger cooldowns; TPM accounting credits the key once output actually flows (`_defer_key_credit` on the streaming path).
- **`execute_with_retries`** covers connect-phase retries (429/5xx/transient httpx → next attempt), deployment failover, and fallback groups.
- **Health scoring (opt-in)**: `router_settings.health_model: scored` adds EWMA latency + success-rate scoring and adaptive cooldowns (`health_ewma_alpha`, `health_window`, `adaptive_cooldown`). Default `none` keeps bit-identical legacy behavior.
- **HealthHealer (opt-in, default off)**: background service probing sick keys/deployments with 1-token requests, graduated (probation) recovery; wired in lifespan like the Cline/WorkBuddy refresh services.

## 6. Streaming

The delta taxonomy and ordering contract, the pump, tape-based mid-stream failover, durable journals with `Last-Event-ID` replay, and the guard subsystems are documented in [STREAMING.md](STREAMING.md). Key properties:

- One `StreamTape` per stream for mid-stream failover (continuation messages synthesized from partial output onto a fallback deployment).
- `JournalStore` persists encoded SSE frames to `.wiwi/journals/<request_id>.jsonl` (enabled by default, 600 s TTL, 1 MiB cap) so reconnects work across wiwi restarts.
- Backpressure via `asyncio.Queue(maxsize=4096)`; coalescer merges text deltas under pressure; loop detector terminates degenerate repetition with a clean `Finish`.

## 7. Two different cache-hit flags — do not conflate

- `cache_hit` = **provider prompt-cache** hit (feeds `wiwi_prompt_cache_hits_total`).
- `response_cache_hit` = served from wiwi's own **exact-match response cache** (`wiwi/cache/`).

A response-cache hit must leave `cache_hit=False` (`wiwi/server/app.py` response-cache branch) or prompt-cache metrics inflate. The response cache never stores streaming requests or requests with builtin tools.

## 8. Persistence

- **SQLite default** (`sqlite+aiosqlite:///wiwi.db`); Postgres auto-normalized to `postgresql+asyncpg://`; `DATABASE_URL` env overrides config.
- Schema created via inline `CREATE TABLE IF NOT EXISTS` at startup — **no Alembic**.
- Tables: virtual keys (hashed), users/sessions, request logs, provider/key pool state, DB-backed config (providers, deployments, settings, `model_prices`), audit log, DB-backed log aggregates for long-range stats.
- Runtime scratch: `.wiwi/` (stream journals) — gitignored, never committed.

## 9. Security model

- **Master key** (`WIWI_MASTER_KEY` / config) guards `/admin/*` via `Authorization: Bearer`.
- **Virtual keys** `sk-wiwi-…`: SHA-256-hashed at rest, constant-time compare, plaintext returned once at mint. Carry per-key model allowlists, budgets, rpm/tpm, expiry.
- **User accounts**: stdlib PBKDF2 password hashing, HMAC-signed HttpOnly session cookies, roles (user sees only own keys' data; admin sees all).
- **Provider secrets**: stored via config-store/admin API (encrypted at rest; the reveal endpoint is audit-logged).
- **Error translation**: `WiwiError` is the unified internal error type; each wire dialect owns `error_body()` so clients always see their own dialect's error shape.

## 10. Deployment

- **Docker**: 3-stage Dockerfile (uv builder → bun SPA build → python 3.12-slim runtime), non-root user `wiwi`, healthcheck `GET /health` every 30 s; `WIWI_STATIC_DIR=/app/wiwi/server/static`; data in `/app/data` volume. `docker-compose.yml` runs `postgres:16-alpine` + wiwi with healthcheck-gated `depends_on`.
- **Process model**: single FastAPI/uvicorn process; Redis extras for multi-instance rate limiting and response caching.
- **Observability**: structlog JSON lines, `/metrics` Prometheus endpoint, `/admin/stream` SSE, request/proxy/audit logs.
