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
| Realtime | `WS /v1/realtime` | Full (auth incl. `?key=`, model allowlist, budget/rate/unpriced admission, deployment/key pick, usage-peak billing + request log) |
| Models / health / metrics | `/v1/models`, `/public/models`, `/health`, `/metrics` | Full (Python series names/types, admin-gated, disabled 404 body, lifecycle loss counters) |

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
- **OpenCode**: per-model wire routing (responses / messages / gemini / chat), each
  wire's canonical base and credential header (`Bearer`, `x-api-key`, `x-goog-api-key`),
  live npm `User-Agent`, v2 session fingerprint headers, free-tier decoy tools,
  decoy-call filtering.
- **NIM**: native MiniMax/StepFun tool-markup framer + parser, schema memoization.
- **IFM**: `chat_template_kwargs` effort, `X-Session-ID` affinity, `reasoning_content`
  replay, platform-param drops.
- **Live version helpers**: npm-registry version cache for OpenCode/Cline/WorkBuddy.
- **DeepSeek**: thinking-mode `reasoning_content` round-trip — every history
  assistant turn is padded when the model is DeepSeek, thinking is on and tools
  are present (stripped when no tools), shared by every OpenAI-wire adapter that
  fronts a DeepSeek model.

## Recently closed gaps
- **Shared-database Responses state** — the Go store now writes Python's
  `stored_responses` table (input/output split, float epochs, key scoping) and
  reads rows Python wrote; the Go-only `responses` table is gone. A response id
  saved by either backend resolves on the other.
- **Real log/spend loss counters** — failed request-log writes, failed audit
  writes and failed batched spend settlements are counted by the subsystem that
  owns them and exported through `/health` (`failed_request_log_writes`,
  `failed_audit_log_writes`, `spend_flush_failures`) and `/metrics`
  (`wiwi_request_logs_failed_total`, `wiwi_audit_logs_failed_total`). They were
  previously hard-coded to zero.
- **Operator metrics parity** — `/metrics` is always registered (disabled = 404
  with the Python body), requires the master key or an admin session when
  enabled, and renders the Python series names/types (`wiwi_requests_total`,
  `wiwi_tokens_total{kind=...}`, prompt/response cache hits, estimated usage,
  cost, stream errors, status/provider breakdowns, duration/TTFT/TPS
  quantiles, `tps_sample_ratio`, loss counters). Process-lifetime counters are
  carried by `Totals`/`AppState`, never re-derived from the event ring.
- **Realtime admission and billing** — the socket now passes the same gates as
  an HTTP request (model allowlist, per-key and global RPM/TPM, unpriced
  policy, budget reservation, deployment/key pool pick with realtime-capability
  check) before the upgrade, so refusals are real 4xx/5xx responses. The
  session's peak usage (top-level `usage` or `response.usage`, including the
  final `response.done` after a max-session grace) is priced, settled against
  the reservation, logged, counted in totals/metrics, and published on the
  request bus. A zero-usage session increments `realtime_usage_missing`.
- **Response-store lifecycle** — same pending-write sweeper semantics as Python
  (idle stores issue no SQL; every row still crosses a due boundary), `ttl <= 0`
  keeps rows forever, `master` may read/delete across key scopes, `store:false`
  is honored at every save site, and cache hits persist a rebased copy so the
  returned id stays resolvable. Multi-turn replay decodes the raw stored
  `_input` and `output` (tool calls/results and images survive), not text only.
- **Forced streaming** — Cline, WorkBuddy and OpenCode have no non-streaming
  upstream mode (Python `force_stream = True`). A plain `/v1/*` caller now gets
  SSE requested, pumped, and reassembled into one `AssistantTurn`; Cline and
  WorkBuddy never send `stream_options`, and each applies its own default
  reasoning effort. OpenCode chat adds `stream_options` only on this forced path.
- **Config/HTTP pool fields** — `http_keepalive_expiry_s`,
  `singleflight_max_flights`, `singleflight_max_waiters`,
  `response_store_sweep_interval_s`, and `telemetry.export_timeout_s` are
  parsed, defaulted, validated and wired (outbound pool limits, flight limits,
  store sweeper, OTLP export deadline).
- **Cline redirect OAuth** — `POST /admin/cline/auto-connect` supports the
  browser redirect flow (`auth_url`, `state`, 600 s TTL, `/console` clamp), the
  public single-use `GET /cline/oauth/callback` is registered, and
  status/refresh/disconnect honor the `provider` selector.
- **Singleflight joiners** — a joiner now receives the leader's usage in its
  log row at `cost=0`, a rebased response id, the `x-wiwi-singleflight: JOINED`
  header, and (Responses surface) a persisted rebased transcript, matching
  Python's join path. The typed result carries payload/usage/cost.
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
- **Unknown reasoning effort leaves thinking OFF** — `GenParams.EffectiveThinkingBudget`
  returns on the effort→budget map like Python (round-124 contract) instead of
  skipping an unknown level and falling through to the next knob, which silently
  re-enabled thinking at the medium default on a typo'd effort. Pinned in
  `internal/ir/types_test.go`.


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

The OpenCode free-tier decoys (chat and Responses shapes, streaming and
non-streaming filtering, forced-auto `tool_choice`, `big-pickle`, and the
`opencode_free_tier_cloak` opt-out) and WorkBuddy's request-shape rules
(tool_choice normalization, system-message prepend, prompt sanitization) are
now ported and covered by `shape_parity_test.go`.
