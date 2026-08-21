# wiwi — Tech Stack

Recommended Python stack for building the unified LLM gateway described in `ARCHITECTURE.md`. Every pick lists the alternatives considered and why they lost, so decisions can be revisited without re-researching. Last reviewed: 2026-08.

---

## 1. The stack at a glance

| Layer | Pick | Version | Confidence |
|---|---|---|---|
| Language | Python | 3.12+ (3.13 fine) | high |
| Package manager | uv | latest | high |
| API framework | FastAPI | >= 0.115 | high |
| ASGI server | uvicorn (default) · granian (perf option) | >= 0.32 / >= 0.27 | high |
| Upstream HTTP client | httpx (async, HTTP/2) | >= 0.28 | high |
| SSE serve / parse | sse-starlette · hand-rolled parser over httpx stream | latest | medium-high |
| Validation & types | pydantic v2 + pydantic-settings | >= 2.9 | high |
| Hot-path JSON (optional) | msgspec (decode) · orjson (encode) | >= 0.19 / >= 3.10 | medium |
| Config format | PyYAML → typed pydantic models | PyYAML >= 6 | high |
| Database | SQLite (aiosqlite) default · PostgreSQL (asyncpg) prod | — | high |
| ORM / migrations | SQLAlchemy 2.x async + Alembic | >= 2.0 | high |
| Cache / rate-limit store | in-memory (default) · redis-py asyncio (optional) | redis >= 5 | high |
| Token counting | provider usage first · tiktoken · chars/4 heuristic | tiktoken >= 0.8 | high |
| Pricing data | bundled `model_prices.json` (LiteLLM-shaped) | — | high |
| Logging | structlog (JSON lines) | >= 24 | high |
| Retry/backoff | hand-rolled in router (~50 lines) | — | high |
| Testing | pytest + pytest-asyncio + respx + syrupy | latest | high |
| Lint / types | ruff + basedpyright | latest | high |
| Container | Docker multi-stage, non-root | — | high |

Total runtime dependencies: ~12. That count is a feature.

---

## 2. Decisions and trade-offs

### 2.1 API framework: FastAPI ✅ (over Litestar, BlackSheep, Starlette raw)

- **Why**: A gateway's p99 is dominated by upstream provider latency, not framework overhead. FastAPI gives pydantic-v2-native validation, dependency injection for auth/rate-limit middleware, automatic OpenAPI docs for the admin API, and the largest ecosystem of examples/hiring.
- **Litestar** benchmarks 1.5–2× faster on synthetic JSON routes and has a cleaner DI story. Revisit if profiling shows framework overhead > 5% of request time. Its smaller ecosystem is the tiebreaker against it today.
- **BlackSheep** is fastest in some benchmarks but weaker typing/docs integration.
- **Raw Starlette** saves nothing that matters and loses the docs/validation layer.
- **SSE note**: streaming responses bypass JSON serialization entirely (`StreamingResponse` yielding pre-encoded bytes), so FastAPI's serialization cost never sits in the stream path.

### 2.2 ASGI server: uvicorn default, granian as a flag ✅

- **uvicorn**: the safe default; HTTPTools+h11, battle-tested with FastAPI, standard `--workers N` model.
- **Granian** (Rust HTTP server, ASGI interface): recent benchmarks show 2–4× higher throughput and lower CPU on plain routes. Worth a `wiwi --server granian` option after M6 load testing; keep the app server-agnostic (pure ASGI callable, no uvicorn-specific APIs).
- **Hypercorn**: only if HTTP/3 to clients becomes a requirement. Not now.
- **Gunicorn**: not needed; uvicorn/granian both handle multi-worker process management directly.

### 2.3 Upstream HTTP: httpx ✅ (over aiohttp, curl_cffi)

- **Why**: async + sync API, HTTP/2 support (`httpx[http2]` — Anthropic and Google both benefit), connection-pool limits per deployment, precise timeout control (connect/read/write/pool separately), and **respx** lets tests mock it at the transport level with zero network.
- **aiohttp**: faster in some microbenchmarks, worse API ergonomics, no native HTTP/2.
- **curl_cffi**: only relevant for TLS-fingerprint-sensitive targets; irrelevant here.
- Pool sizing rule: `max_connections = 4 × expected concurrent upstream streams`, per provider, tunable per deployment.

### 2.4 SSE: sse-starlette to serve, custom parser upstream ✅

- **sse-starlette** (`EventSourceResponse`): correct headers, ping/keepalive support, client-disconnect propagation. One dependency, zero risk.
- **Upstream parsing**: providers emit non-standard-ish SSE (Anthropic adds `event:` names, Gemini uses `data:` JSON lines under `alt=sse`). A ~100-line incremental parser over `httpx.aiter_bytes()` handles all three dialects and keeps partial-buffer control explicit. `httpx-sse` exists but is thin; wrap or replace freely.
- Never buffer a full response to re-serialize; translate chunk-by-chunk through IR deltas (see ARCHITECTURE.md §5).

### 2.5 Validation: pydantic v2 everywhere, msgspec only if profiled ✅

- **pydantic v2** (pydantic-core, Rust): config models, wire request models, admin API schemas. Strict mode for config, lax mode for wire (clients send junk).
- **msgspec** decodes 2–8× faster than pydantic v2 on large payloads and allocates far less. The hot path where this could matter is per-chunk stream decoding at high concurrency. Decision: **do not add it until Phase 6 load testing shows chunk-decode CPU > 15% of process CPU.** The codec layer isolates parsing behind small functions, so swapping `json.loads` → `msgspec.json.decode` later is a one-line-per-codec change.
- **orjson**: adopt immediately for all outbound JSON encoding; drop-in, strictly faster than stdlib.

### 2.6 Data layer: SQLAlchemy 2 async + Alembic ✅ (over Tortoise, SQLModel, Prisma-style ORMs)

- **SQLAlchemy 2.x**: the production standard for async Python ORM work; first-class async sessions, robust Postgres support (upserts via `on_conflict_do_update` for `daily_spend`), Alembic migrations are the industry norm.
- **Tortoise ORM**: nicer API for simple CRUD, but thinner tooling/migrations story (aerich) and smaller team familiarity.
- **SQLModel**: convenience layer blending pydantic+SQLAlchemy; opinionated, lags SQLAlchemy features, mixed production reports. Not worth the abstraction for a write-heavy logging workload.
- **Drivers**: `aiosqlite` (default, zero-config dev), `asyncpg` (Postgres prod — fastest driver, used by everyone serious).
- Access pattern note: the gateway does tiny writes from background tasks only (spend logger batches rows); ORM overhead is irrelevant at that point, so SQLAlchemy's weight costs nothing in the hot path.

### 2.7 Cache / rate limiting: memory first, redis-py when scaling out ✅

- In-process sliding-window counters (sorted timestamps per key) are exact for one instance and need zero infra.
- **redis-py** asyncio client for multi-instance: `INCR`+`EXPIRE` fixed windows are good enough for rpm/tpm admission control; sorted-set sliding windows if precision matters later. Key cache uses plain GET/SETEX with TTL.
- **Valkey/Dragonfly** are drop-in compatible if licensing/perf ever matters; code targets the RESP protocol, not a vendor.

### 2.8 Tokens & pricing ✅

- **Trust provider-reported `usage` first** — always present on OpenAI/Anthropic/Gemini non-streaming; streaming final frames carry it too (all three dialects). This is the accuracy source of truth.
- **tiktoken** fallback for OpenAI-family models (fast, Rust, exact BPE for o200k/cl100k).
- **chars/4 heuristic** for everything else; document the ±10% error. Avoid pulling HuggingFace `tokenizers` + per-model vocab files into the gateway just for billing estimates; make it an optional extra `[project.optional-dependencies] tokenizers-extras` if someone needs exact counts for local models.
- Pricing table ships as JSON in LiteLLM's shape so community updates can be synced; custom overrides live in `wiwi.yaml`.

### 2.9 Observability: structlog ✅ (over stdlib logging, loguru)

- structlog: structured key-value events rendered as JSON lines in prod, pretty console in dev; plays fine with uvicorn loggers; contextvars bind `request_id`, `key_alias`, `model_group` once per request.
- **loguru** is pleasant but global-stateful and less natural for JSON pipelines.
- Metrics: expose Prometheus text endpoint post-MVP (asgiref-free, hand-rendered counters are fine at this scale); OpenTelemetry export later behind an env flag.

### 2.10 Retries: hand-rolled ✅ (over tenacity)

The router needs retry-with-cooldown-state-and-fallback-awareness, which tenacity doesn't model. Exponential backoff + jitter + `Retry-After` parsing is ~50 lines, fully testable, zero deps. Use tenacity inside tests if convenient, not in core.

### 2.11 Testing stack ✅

- **pytest + pytest-asyncio**: standard.
- **respx**: mocks httpx transports; golden-file replay of provider streams without network.
- **syrupy**: snapshot assertions for translated outputs (golden files with nice diffs).
- **hypothesis**: property tests for IR round-trips (decode→encode→decode stability) — catches codec drift cheaply.
- Client E2E (M6): scripted Codex CLI + Claude Code runs against a local wiwi in CI.

### 2.12 Quality & packaging ✅

- **ruff**: lint + format in one tool, replaces black/isort/flake8.
- **basedpyright** (or mypy): strict type checking; pydantic v2 and SQLAlchemy 2 both have good stubs now.
- **uv**: 10–100× faster installs than pip/poetry, lockfile built in, PEP 621 `pyproject.toml`.
- Docker: multi-stage (uv install layer → slim runtime), non-root user, `tini` for signal handling.

---

## 3. Rejected / avoided on purpose

| Tool | Why not |
|---|---|
| LiteLLM SDK as a library inside wiwi | would make wiwi a wrapper, not a gateway; translation quality is the product |
| Celery / task queues | background spend logging is an in-process batched queue; a broker adds ops burden for nothing |
| Kafka/Redis Streams eventing | same reason; SQLite/Postgres inserts from a background task suffice |
| GraphQL admin API | REST + OpenAPI is enough and matches LiteLLM operator expectations |
| MongoDB for logs | relational daily aggregates + cursor pagination cover MVP; Postgres full-text later if needed |
| LangChain / agent frameworks | gateway stays unopinionated about payloads |
| FastAPI's `jsonable_encoder` in stream path | allocation-heavy; streams emit pre-encoded bytes |

---

## 4. `pyproject.toml` dependency block (draft)

```toml
[project]
name = "wiwi"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "sse-starlette>=2.1",
  "httpx[http2]>=0.28",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "pyyaml>=6.0",
  "sqlalchemy[asyncio]>=2.0.36",
  "alembic>=1.14",
  "aiosqlite>=0.20",
  "redis>=5.2",            # optional at runtime; imported lazily
  "tiktoken>=0.8",
  "structlog>=24.4",
  "orjson>=3.10",
]

[project.optional-dependencies]
pg = ["asyncpg>=0.30"]
server-granian = ["granian>=0.27"]
tokens-extras = ["tokenizers>=0.20"]
dev = [
  "pytest>=8", "pytest-asyncio>=0.24", "respx>=0.21",
  "syrupy>=4.7", "hypothesis>=6.11", "ruff>=0.7", "basedpyright>=1.19",
]
```

---

## 5. Upgrade triggers (when to revisit)

| Trigger | Action |
|---|---|
| Load test: chunk-decode CPU > 15% | introduce msgspec decode in codecs |
| Framework overhead > 5% of p99 | benchmark Litestar port of route layer |
| Multi-instance rate-limit drift complaints | move counters from INCR windows to Redis sorted sets |
| Need exact token counts for local models | enable `tokens-extras` with HF tokenizers |
| HTTP/3 client demand | swap uvicorn → hypercorn behind the same ASGI app |
| Provider adds gRPC-side streaming | isolate in that provider's adapter; core untouched |
