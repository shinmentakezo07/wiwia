# wiwi — Build Plan

How we get from empty repo to the MVP defined in `MVP.md`, using the architecture in `ARCHITECTURE.md`. Work is organized as 6 phases matching M1–M6. Each phase ends with a demo gate that must pass before the next begins.

---

## 1. Tech stack (recommended Python choices)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | async-first, ecosystem fit |
| API framework | **FastAPI + uvicorn** | pydantic v2 validation, SSE via `StreamingResponse`, OpenAPI docs free. Litestar/Blacksheep are faster on synthetic benchmarks, but a gateway's latency is dominated by upstream providers; FastAPI's ecosystem wins |
| HTTP client | **httpx** (async, `http2=True`) | streaming, timeouts, connection pooling, respx mocking |
| SSE | **sse-starlette** (serve) + hand-rolled/httpx-sse parser (upstream) | battle-tested framing both directions |
| Validation/types | **pydantic v2** (pydantic-core) | strict config + wire models with Rust-speed validation |
| Hot-path JSON (optional) | **msgspec** Structs for stream chunk decoding; **orjson** for serialization | measurable win at high chunk rates; drop-in later |
| Config | pydantic-settings + PyYAML | typed config, precise startup errors |
| DB/ORM | **SQLAlchemy 2.x async + Alembic**; aiosqlite default, asyncpg for Postgres | zero-config dev, production path |
| Cache/limiter | in-memory (default), **redis-py** asyncio (optional) | exact single-instance; correct multi-instance |
| Tokens/cost | provider usage first; **tiktoken**; chars/4 heuristic fallback; bundled `model_prices.json` | accuracy where it matters, no heavy deps |
| Logging | **structlog** (JSON lines) | structured, redaction-friendly |
| Tests | **pytest + pytest-asyncio + respx**; syrupy snapshots for golden files | fast CI, no live-provider flakiness |
| Quality | ruff (lint+format), basedpyright | modern, fast |
| Packaging | **uv** + hatchling; Docker multi-stage | one-command run |

Non-goals for stack: no Celery, no Kafka, no Kubernetes manifests until needed.

## 2. Repository bootstrap (Phase 0, ~half day)

```
git init, src layout per ARCHITECTURE.md §9
pyproject.toml deps: fastapi, uvicorn[standard], sse-starlette, httpx[http2],
  pydantic-settings, pyyaml, sqlalchemy[asyncio], alembic, tiktoken,
  structlog, orjson; extras: redis, pg
pre-commit: ruff + basedpyright basic
CI skeleton: lint + unit tests on every push
wiwi.yaml.example + README quickstart stub
```

## 3. Phase plan

### Phase 1 — Walking skeleton (M1)

Goal: one OpenAI chat request travels client → wiwi → OpenAI → client.

1. Config loader: parse `wiwi.yaml`, interpolate `os.environ/NAME` recursively, validate via pydantic, fail with file/line context. Include `model_group_alias` resolution (G7).
2. Deployment registry: build model groups from `model_list`; lookup by `model_name`.
3. FastAPI app factory: request-id middleware (echo `x-wiwi-request-id`, G6), OpenAI error envelope, `/health`.
4. `POST /v1/chat/completions` non-streaming: resolve group → pick first healthy deployment → openai adapter transform → httpx call → near-identity passthrough (proper IR lands in Phase 2).
5. Unknown-model 404; upstream-error normalization.
6. Unit tests: config edge cases; integration test with respx-mocked OpenAI.

**Gate:** curl through wiwi to real OpenAI works; mocked tests green.

### Phase 2 — IR, wire codecs, streaming, all MVP adapters (M2)

Goal: all three surfaces live; cross-dialect tool loops work; streams flow end-to-end; Claude Code and Codex client-compat essentials satisfied.

1. **Canonical IR** (`ir/types.py`): parts, messages, tools (+`strict`), tool_choice, gen params incl. `response_format`, `parallel_tool_calls`, `n` policy (G3); reserved multimodal part kinds (`audio`/`video`/`document`); `cache_control` on text parts (G8); usage, stop reasons, stream deltas; property-based round-trip tests.
2. **SSE plumbing**: relay loop, heartbeat comments/pings, client-disconnect cancellation, per-chunk translation interface; `stream_options.include_usage` honored/forced (G4).
3. **Wire codec `openai_chat`**: parse/encode requests, responses, chunk streams, errors; serve `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/models`.
4. **Wire codec `anthropic_messages`**: content blocks (`text`, `tool_use`, `tool_result`, `thinking`), top-level `system`, mandatory `max_tokens` defaulting, stop-reason mapping, SSE event folding/unfolding; serve `/v1/messages` accepting `x-api-key` + `anthropic-version`; **serve `/v1/messages/count_tokens`** via local estimate (G1).
5. **Wire codec `openai_responses`**: input items (`message`, `function_call`, `function_call_output`, `reasoning`), `instructions`, flattened function tools, streaming events (`response.created` … `response.completed`), `store:false` stateless mode; serve `/v1/responses`.
6. **Header forwarding allowlist** (G2): `anthropic-version`, `anthropic-beta` (merged), `openai-organization/project/beta`; strip everything else; deployment-level beta defaults in config.
7. **Provider adapters** against IR: anthropic (SSE folding, tool blocks, cache_control passthrough), gemini (`contents`/`parts`, `alt=sse`), openai-compatible (base_url + optional key).
8. **Golden-file harness**: record real captures from OpenAI SDK, Codex CLI, Claude Code, and each provider; CI replays through codecs/adapters in both directions.
9. Cross-surface matrix tests: each surface × each provider family, streaming + tools + structured outputs.

**Gate:** OpenAI SDK, Claude Code (`ANTHROPIC_BASE_URL=wiwi`), and Codex CLI (wiwi as model provider) each complete a streamed, multi-turn tool-calling session through wiwi; count_tokens and beta-header features verified against real Claude Code.

### Phase 3 — Router (M3)

Goal: resilience semantics identical to LiteLLM's router.

1. Strategies: `simple-shuffle` (rpm/tpm-weighted), `least-busy` (in-flight counter), `latency-based` (rolling p95).
2. **`CredentialProvider` seam** (G5): key pools reference credentials through the interface (static-key impl now; Entra-ID/OAuth/SigV4 impls post-MVP).
3. Retry wrapper: retryable-status set, exponential backoff + jitter, honor `Retry-After`, `num_retries` within group.
4. Cooldowns: `allowed_fails` in `cooldown_time` → skip; half-open recovery probe.
5. Fallback engine: group-level `fallbacks`, `context_window_fallbacks`; preserve original error if all groups fail.
6. Per-deployment timeout override; request-scoped deadline propagation.
7. Chaos tests: mock deployments returning 429/500/timeouts; assert selection/retry/fallback sequences exactly.

**Gate:** kill-one-deployment demo shifts traffic automatically; sequence assertions pass.

### Phase 4 — Control plane (M4)

Goal: keys, limits, budgets enforced on every request, on every surface.

1. Key service: generate (`sk-wiwi-` + secrets token), SHA-256 storage, constant-time verify, plaintext shown once.
2. Auth middleware: cache-first lookup (memory TTL 60s; Redis when enabled) → DB miss → checks: exists, enabled, not expired, budget remaining, model allowed. Accept `Bearer` everywhere + `x-api-key` on `/v1/messages`.
3. Admin endpoints: `/key/generate|info|update|delete` guarded by master key; cache eviction on write.
4. Rate limiter: sliding-window rpm/tpm, global + key scopes; memory backend with Redis-parity interface; 429 + `Retry-After`; background accounting.
5. Budgets: `max_budget`, `budget_duration` reset scheduler; soft pre-check + authoritative post-request upsert.
6. Alembic migrations for `keys`, `budgets`.

**Gate:** over-limit and over-budget requests rejected with dialect-correct envelopes on all three surfaces; key CRUD lifecycle tested end-to-end.

### Phase 5 — Money + observability (M5)

Goal: every request costs out and lands in queryable logs.

1. Pricing loader: bundled `model_prices.json` + config overrides; unknown-model cost = 0 with warning.
2. Token counting: prefer provider `usage`; tiktoken fallback; chars/4 heuristic otherwise.
3. Background spend logger: queue → batch writer (request_logs insert incl. `surface`, daily_spend upsert, budget/key spend increment); graceful drain; DB-down degradation (log + drop, never block).
4. `/spend/report` aggregations (time range, group_by key/model/day); `/logs/request` with filters + cursor pagination.
5. Structured logs: one JSON line per request (ids, surface, model, deployment, tokens, cost, latency, status); secrets-redaction middleware.

**Gate:** hand-computed spend from fixture requests matches `/spend/report` exactly.

### Phase 6 — Ship (M6)

1. Dockerfile (multi-stage, non-root) + docker-compose.yml (app + volume; optional postgres + redis profiles).
2. Docs: README quickstart, config reference generated from pydantic models, adapter coverage table, "connect Codex CLI" and "connect Claude Code" guides.
3. Sample configs: personal single-key, multi-deployment HA-ish, Ollama-local.
4. Load test: 200 concurrent streams, 30-min soak; verify overhead targets from MVP.md §4; fd/memory leak check.
5. Security pass: audit script proving no plaintext secrets in DB/logs; SSRF guard tests; header allowlist check.
6. Tag v0.1.0.

**Gate:** fresh clone → `docker compose up` → working gateway in under 5 minutes.

## 4. Testing strategy

| Level | What | Tooling |
|---|---|---|
| Unit | config parsing, IR round-trips, strategies, limiter windows, cost math, key hashing | pytest |
| Contract/golden | codec + adapter translations vs recorded fixtures: 3 inbound dialects × 4 provider families, streams included | respx + committed SSE fixtures |
| Integration | route → auth → limit → log pipeline on SQLite + mocked upstreams | pytest-asyncio, httpx ASGI transport |
| Live (nightly, optional) | smoke calls to real providers behind env-guarded marker | pytest -m live |
| Client E2E | scripted Codex CLI + Claude Code sessions against a local wiwi | CI job with recorded creds |
| Load | stream concurrency, latency overhead, leak check | locust or vegeta |

Rule: no codec or adapter merges without golden files updated from real captures.

## 5. Suggested commit sequence (first two weeks)

1. `chore: scaffold project, ci, lint`
2. `feat(config): yaml loader with env interpolation`
3. `feat(ir): canonical types + round-trip property tests`
4. `feat(api): app factory, health, error envelopes`
5. `feat(wire): openai_chat codec + non-streaming route`
6. `feat(stream): sse relay + chunk framing`
7. `feat(providers): anthropic adapter with stream folding`
8. `feat(wire): anthropic_messages codec, /v1/messages route`
9. `feat(wire): openai_responses codec, /v1/responses route`
10. `test(golden): fixture harness for all dialects`

## 6. Post-MVP backlog (ordered)

1. Teams/users hierarchy + team budgets/limits
2. Admin web UI (keys, models, spend charts, logs viewer)
3. Exact-match response caching with TTL
4. Azure OpenAI + Bedrock + Vertex adapters (via the `CredentialProvider` seam: Entra-ID tokens, SigV4 signing, service-account OAuth — G5 impls)
5. Stateful Responses API: `previous_response_id` server-side storage, hosted tools (`web_search`, `file_search`)
6. Prometheus `/metrics` + OpenTelemetry trace export
7. Guardrails hook system (PII, prompt-injection, keyword blocks)
8. Pass-through provider endpoints (G16); files/batches APIs (G15); audio/images/moderations passthrough (G10)
9. WebSocket Realtime proxy for voice agents (G9) — separate WS pipeline design
10. Request hedging on slow TTFT (G11); load shedding via in-flight caps (G12); priority lanes (G19)
11. Session affinity routing per key for provider prompt-cache hits (G13); pre-call cost-cap estimate (G14)
12. Mock provider `mock/*` (G17); request replay from logs UI (G18); webhook event system with signatures (G20)
13. DB-managed model overlay (UI-added models merged over YAML)
14. Semantic caching; wildcard `model_name: "*"` passthrough
15. SSO/SAML + audit logs

Full audit register with rationale: `MVP.md` §3.1.
