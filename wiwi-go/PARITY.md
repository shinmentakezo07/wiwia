# wiwi-go parity matrix

A from-scratch Go (Gin) port of the Python `wiwi/` backend (~44k LOC, 95 modules).
The architecture (hub-and-spoke IR translation) and the full request surface are
reproduced, including the provider-specific wire logic and the operational layer.
Remaining gaps are listed at the bottom.

## Inbound surfaces — Full
| Surface | Route | Status |
|---|---|---|
| OpenAI Chat | `POST /v1/chat/completions`, `/v1/playground/completions` | Full (decode/encode, stream + non-stream) |
| OpenAI Responses | `POST /v1/responses`, `GET/DELETE /v1/responses/{id}` | Full (stored state, key-scoped, TTL) |
| Anthropic Messages | `POST /v1/messages`, `/v1/messages/count_tokens` | Full (blocks, tools, thinking, stream) |
| Legacy Completions | `POST /v1/completions` | Full |
| Realtime | `WS /v1/realtime` | Relay with per-adapter `RealtimeURL` |
| Models / health / metrics | `/v1/models`, `/public/models`, `/health`, `/metrics` | Full |

## Outbound providers (13) — Full wire coverage
`openai`, `openai-compatible`, `gmicloud`, `bai`, `openrouter`, `nvidia-nim`,
`opencode`, `cline`, `workbuddy`, `hermes`, `ifm` share the OpenAI-wire adapter
with provider-specific behavior; `anthropic` and `gemini` have distinct adapters.

Ported provider logic:
- **OpenRouter**: `reasoning_effort` → unified `reasoning{effort|max_tokens|enabled}`,
  completion-cap reconciliation, history `reasoning`/`reasoning_content` stripping,
  `reasoning_details` decoding (text/summary/encrypted).
- **NVIDIA NIM**: boolean-subschema stripping, unsafe `type` parameter aliasing and
  reverse-mapping of tool-call args.
- **IFM**: per-message `reasoning_content` replay; reasoning decode.
- **WorkBuddy**: business errors that ride HTTP 200 detected and converted to retryable
  `WiwiError`.
- **Anthropic**: prompt-cache breakpoint injection (`prompt_cache`), native
  `output_config` (effort + json_schema), thinking modes, cache-control passthrough.
- **Gemini**: `google_search` builtin rendering, function declarations, usage.
- **OpenCode**: per-model wire routing (responses / messages / gemini / chat), live
  npm `User-Agent`, free-tier decoy tools, decoy-call filtering.
- **NIM**: native MiniMax/StepFun tool-markup framer + parser, schema memoization.
- **IFM**: `chat_template_kwargs` effort, `X-Session-ID` affinity, `reasoning_content`
  replay, platform-param drops.
- **Live version helpers**: npm-registry version cache for OpenCode/Cline/WorkBuddy.
- **DeepSeek**: thinking-mode `reasoning_content` round-trip — every history
  assistant turn is padded when the model is DeepSeek, thinking is on and tools
  are present (stripped when no tools), shared by every OpenAI-wire adapter that
  fronts a DeepSeek model.

## Recently closed gaps
- **Console API shape parity** — the SPA now runs unchanged against this port:
  `/admin/models`, `/admin/keys`, `/admin/users`, `/admin/pricing` (USD per 1M),
  `/admin/provider-catalog` (a bare array), `/admin/logs/proxy`,
  `/admin/logs/audit`, `/admin/alert-rules`, `/public/models`, the Cline/WorkBuddy
  account surfaces and `/admin/backup` all return the payloads Python returns.
  Key CRUD accepts the console's bodies (`ttl_seconds`, `expires_at`, `disabled`),
  deployments delete via query params (`DELETE /admin/model-groups/{g}/deployments`),
  aliases take the batch `{set, unset}` shape, and `/admin/stream` maps bus events
  to the `log.created` / `proxy.log` names the console subscribes to.
- **DeepSeek reasoning round-trip** — padded/stripped per the model rule
  (`providers/deepseek.go`), with regression tests.
- **Response cache key** now hashes the normalized IR (not the raw body) and
  includes `anthropic-beta`, so two Anthropic requests differing only in that
  header no longer share an entry (AUDIT #329).
- **OAuth expiry fallback** (`oauth.ResolveExpiresAt`) — a missing / zero /
  boolean / `<=lead` `expires_in` on Cline/Hermes/WorkBuddy resolves to `2*lead`
  instead of landing in the past, so a single-use refresh grant can't be burned
  on every sweep (AUDIT #425).
- **Health healer** — now wired on `healer.enabled` and backs off per key
  (`probe_backoff_base_s` / `probe_backoff_cap_s`) instead of re-probing a dead
  key every tick.
- **Per-IP abuse throttling** — `general_settings.trusted_proxies` now gates
  `X-Forwarded-For`/`X-Forwarded-Proto` exactly as Python does, and the auth
  surface carries the same fixed-window attempt throttles (10 failed logins /
  5 min per IP+account, 5 signups / hour per IP) with `Retry-After`.
- **Percentile histograms** — `request_logs` records `tps`; `request_rollups`
  keeps the `tps`/`ttft_ms`/`latency_ms` distributions in `p95_hist` (log-scale
  bins) plus the legacy scalar p95 columns. `/admin/stats/overview` unions raw
  samples with the rolled-up histogram, so pruning no longer makes percentiles
  vanish (a weighted mean of two p95s is not a quantile).
- **Admin log filters/pagination** — `/admin/logs/requests` accepts
  `limit` (≤50k), `minutes`, and `offset` with the Python ordering contract;
  `/admin/logs/audit` defaults to 200 and caps at 5k; `/admin/logs/proxy`
  serves the newest 500; all three are `Cache-Control: no-store`.
- **`drop_params` allowlist** — the OpenAI and Anthropic adapters forward only
  the extras their wire documents unless `wiwi_settings.drop_params` is false;
  Gemini forwards extras only in that mode, and the Responses wire no longer
  leaks the control key into the upstream body.
- **Anthropic MCP / context-management** — `mcp_servers`, `container`, and
  `context_management` are modelled fields on the IR (and part of the response
  cache key) instead of opaque `extras`; only the Anthropic adapter forwards
  them.


## Core pipeline — Full
| Subsystem | Status |
|---|---|
| Canonical IR | Full (tagged parts, tools, gen params, reasoning effort↔budget, usage) |
| Builtin/hosted tool registry | Full (`web_search`, `tool_search_bm25/regex`, passthrough wire types) |
| Wire codecs | Full (4 dialects, decode/encode, stream encoders, error bodies) |
| Partial-JSON repair | Full (streaming args parser + truncated-JSON repair) |
| Router | Full (smooth WRR, aliases, cooldowns, key retirement, fallbacks, health scoring EWMA, adaptive cooldowns, max-inflight, priority lanes, session/prefix affinity) |
| Gateway | Full (complete + stream, retries, deployment/key failover, fallback groups, pricing, loop detection, stream coalescing, span tracing) |
| Streaming | Full taxonomy, SSE, coalescer, loop detector, validator, stream tape, durable journal + Last-Event-ID replay |
| Auth | Full (SHA-256 vkeys, budgets reserve/settle, **batched spend settlement**, PBKDF2 users, HMAC sessions, audit) |
| Rate limit | Full (in-memory sharded sliding window **and Redis** shared limiter) |
| Response cache | Full (in-memory TTL+LRU **and Redis**, singleflight wired into serving) |
| Cost | Full (bundled prices + overrides + DB-persisted, priced/unpriced flag) |
| Logging/stats | Full (ring bus + SSE subscribers, totals, request/audit DB rows) |
| Persistence | Full (SQLite + Postgres, schema, migrations, settings) |
| Config store | Full (DB overlay for providers/deployments/prices; survives restart) |
| Healer | Full (opt-in background prober with 1-token probes and restore) |
| Telemetry | In-process span recorder published to the event bus (off by default) |

## Admin / auth API
| Group | Status |
|---|---|
| Virtual keys CRUD + budgets | Full |
| Users, signup/login/logout/me, playground key | Full |
| Providers CRUD, key-pool add/patch/delete, secret reveal (audited) | Full (DB-persisted) |
| Model-group deployments add/delete, aliases | Full (DB-persisted) |
| Providers export/import | Full |
| Pricing CRUD | Full (DB + engine) |
| Logs (requests/audit/proxy), request metrics, live SSE tail | Full |
| Stats overview/timeseries | Full (DB aggregates) |
| Backup export/import | Full (config, keys, users, prices, providers, deployments, settings) |
| Alert rules | Full (evaluated per request, events on the bus) |
| OAuth: Cline login-url/connect/auto-connect/status/refresh/disconnect/models/settings | Full |
| OAuth: Hermes device-code/poll/status/refresh/disconnect | Full |
| OAuth: WorkBuddy login/start/poll, accounts, import/export, refresh | Full |
| OAuth auto-refresh loop (token rotation before expiry) | Full |

## Remaining gaps (minor / documented)
- **OpenCode `jev-*`** System One models remain unroutable (the Python adapter documents
  the same limitation; they are not a completion API).
