# wiwi Go migration — status

Full A→Z port of the Python `wiwi/` backend to Go (Gin), SQLite + Postgres.
See `PARITY.md` for the per-subsystem coverage matrix and the explicit gaps.

## Phases

### Phase 1 — Foundation ✅
- Go module, Gin server, config loader, basic IR.

### Phase 2 — Core IR & streaming contracts ✅
- Canonical IR (`internal/ir`): tagged parts, tools, gen params, usage, assistant turn,
  reasoning effort↔budget helpers.
- Streaming delta taxonomy (`internal/streaming/deltas.go`) + SSE parse/frame, coalescer,
  loop detector, validator, stream tape, durable journal with Last-Event-ID replay.
- Provider adapter interface + registry.

### Phase 3 — HTTP API parity ✅
- `/v1/chat/completions`, `/v1/playground/completions`, `/v1/completions`,
  `/v1/messages`, `/v1/messages/count_tokens`, `/v1/responses` (+ GET/DELETE),
  `/v1/models`, `/public/models`, `/health`, `/metrics`, `WS /v1/realtime`.
- All four dialects encode/decode + stream + per-dialect error bodies.

### Phase 4 — Provider adapters ✅
- OpenAI wire (7 types), Anthropic, Gemini, and OpenAI-wire variants for
  Cline/WorkBuddy/Hermes/IFM — all 13 types resolve.
- Error classification (`WiwiError`) + key-pool status mapping.

### Phase 5 — Router & gateway ✅
- Smooth WRR (deployments + keys), aliases, cooldowns, key retirement, fallback
  groups, context-window fallbacks, strategies, retries, pricing, attempt records.

### Phase 6 — Auth & admin ✅
- Virtual keys (SHA-256 at rest), budgets reserve/settle, users (PBKDF2), HMAC
  sessions, audit log, rate limits, response cache + singleflight.
- Admin: keys CRUD, users, providers/models (read), pricing CRUD, logs, stats,
  live SSE tail, backup (partial), alert rules (stub), OAuth status (stub).

### Phase 7 — Database & cache ✅
- SQLite + Postgres via `database/sql`, normalized URLs, full schema, migrations,
  settings store, response store, request/audit logs.
- In-memory response cache (TTL+LRU) and singleflight; in-memory rate limiter.

### Phase 8 — Streaming & recovery ✅
- SSE pump, coalescing, loop detection, validation, stream tape, journal replay,
  mid-stream failover substrate.

### Phase 9 — Parity gap closure ✅
- DeepSeek thinking-mode `reasoning_content` round-trip (pad/strip by model rule).
- IR-normalized response-cache key that includes `anthropic-beta` (AUDIT #329).
- Shared OAuth expiry resolver with a `2*lead` fallback (AUDIT #425).
- Health healer wired on `healer.enabled` with per-key exponential probe backoff.
- Regression tests for every item above.

### Phase 10 — Parity closure ✅
- Response-store lifecycle (pending sweeper, `ttl<=0`, master scope, `store:false`,
  raw multi-turn replay, cache-hit persistence).
- Forced streaming for Cline/WorkBuddy/OpenCode with SSE reassembly, plus their
  stream-field quirks and default reasoning effort.
- Config/HTTP pool parity (`http_keepalive_expiry_s`, singleflight limits,
  response-store sweep interval, OTLP export timeout) and outbound pool wiring.
- Cline browser redirect OAuth (`auto-connect` state + public callback).
- OpenCode per-wire bases, credential headers, session fingerprint, Muse routes.
- OpenCode free-tier decoys on both chat and Responses wires (full client schemas,
  client tools win, streaming/non-streaming filtering, forced-auto tool_choice,
  cloak opt-out).
- WorkBuddy request shape (tool_choice normalization, system-message prepend,
  identity/fingerprint sanitization).

## Verification gate
```bash
cd wiwi-go
CGO_ENABLED=1 go build ./...
CGO_ENABLED=1 go test ./...
CGO_ENABLED=1 go test -race ./internal/server/... ./internal/core/... ./internal/streaming/... ./internal/router/...
go vet ./...
```

## Parity gaps closed (see PARITY.md)
- Per-IP abuse throttling (`trusted_proxies` + X-Forwarded-For/X-Forwarded-Proto),
  with the Python auth throttles (login 10/5 min, signup 5/h) and `Retry-After`.
- Anthropic MCP servers / container / context-management as modelled IR fields.
- Stats percentile histograms (`p95_hist`) merged with raw samples in the overview.
- Admin log filters/pagination (`limit`/`minutes`/`offset`, capped audit limit, 500-entry
  proxy tail, `no-store`).
- Explicit `drop_params` allowlists for the OpenAI/Anthropic/Gemini/Responses wires.

Remaining documented gaps are listed in `PARITY.md` (OpenCode `jev-*`); they are not
completion-API features.
