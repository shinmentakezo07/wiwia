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
- **Per-IP abuse throttling** (`trusted_proxies` + `X-Forwarded-For`) is not modelled;
  rate limits are per virtual key and gateway-wide, not per client IP.
- **Anthropic MCP servers / context-management** ride `Request.extras` rather than a
  modelled field (they are forwarded verbatim).
- **Stats percentile histograms** (`p95_hist`) are not kept; retention rolls up counts,
  tokens and cost, and stats read fresh rows + rollups, but not a merged histogram.
- **Admin log filters/pagination** are basic (`limit`/`since`), not the full query set.
- **`drop_params`** is implicit: adapters emit only fields the target wire documents,
  rather than applying the Python per-provider allowlist table.
- **OpenCode `jev-*`** System One models remain unroutable (the Python adapter documents
  the same limitation; they are not a completion API).
