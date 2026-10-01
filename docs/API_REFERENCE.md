# wiwi — API Reference

All HTTP endpoints exposed by the wiwi gateway. Base URL is the wiwi process (default `http://localhost:4000`).

Auth summary:
- **Client traffic** (`/v1/*`): virtual key `sk-wiwi-…` via `Authorization: Bearer` (or `x-api-key` on `/v1/messages`).
- **Admin API** (`/admin/*`): master key via `Authorization: Bearer`.
- **User accounts** (`/auth/*`): signed HttpOnly session cookie.
- **Public** (`/public/*`): no auth.

---

## 1. Client surfaces (`/v1/*`)

### `POST /v1/chat/completions` — OpenAI Chat dialect

Accepts the OpenAI Chat Completions request shape; returns OpenAI Chat responses (or SSE stream when `"stream": true`). Routing, failover, budgets, rate limits, and logging all apply.

- Auth: `Authorization: Bearer sk-wiwi-…`
- Body: OpenAI Chat JSON (`model`, `messages`, `stream`, `tools`, `tool_choice`, `max_tokens`/`max_completion_tokens`, `temperature`, `top_p`, `stop`, `reasoning_effort`, `response_format`, …)
- Errors: OpenAI-shaped error body.

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-wiwi-…" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],"stream":true}'
```

### `POST /v1/responses` — OpenAI Responses dialect

OpenAI Responses API shape, used by Codex CLI and the OpenAI Agents SDK. Hosted tools (`web_search`, `code_interpreter`, …) translate through the IR builtin-tool registry to the backing provider.

- Auth: `Authorization: Bearer sk-wiwi-…`
- Body: Responses JSON (`model`, `input`, `stream`, `instructions`, `tools`, `store`, `previous_response_id`, …)
- Events (streaming): Responses SSE event names (`response.created`, `response.output_text.delta`, `response.completed`, …)

**State (default on).** A completed response is stored and may be continued by id: send `previous_response_id` with the next `input` instead of resending the transcript. The id in the response (`resp_<request-id>`) is wiwi's, not the upstream's, and reads/deletes are **scoped to the presenting key** — another key's id is a `404`, exactly as if it never existed. `store: false` opts a single request out of persistence, and `previous_response_id: <gone id>` is a `404 not_found_error`. Turn it off globally with `wiwi_settings.store_responses: false`.

- `GET /v1/responses/{id}` — replay the stored response object (404 when absent, expired, or another key's).
- `DELETE /v1/responses/{id}` — `{"id": …, "object": "response.deleted", "deleted": true}`; 404 when nothing was removed.

### `POST /v1/messages` — Anthropic Messages dialect

Anthropic Messages API shape, used by Claude Code and the Anthropic SDK. Also accepts `x-api-key` instead of `Authorization`.

- Auth: `Authorization: Bearer sk-wiwi-…` **or** `x-api-key: sk-wiwi-…` + `anthropic-version` header
- Body: Anthropic JSON (`model`, `messages`, `system`, `max_tokens`, `stream`, `tools`, `thinking`, …)
- Streaming events: `message_start`, `content_block_start/delta/stop`, `message_delta`, `message_stop` (+ `ping`, `error`).

A client that calls `/v1/messages` gets Anthropic-format responses even if the backing deployment is OpenAI — cross-dialect translation happens through the IR.

### `POST /v1/completions` — legacy OpenAI Completions dialect

The original OpenAI `text_completion` shape: a bare `prompt`, no roles, no tool protocol. Used by older SDKs and by clients that predate `chat/completions`.

- Auth: `Authorization: Bearer sk-wiwi-…`
- Body: `model`, `prompt` (string), `suffix`, `max_tokens`/`max_completion_tokens`, `temperature`, `top_p`, `stop`, `seed`, `n`, `stream`, `stream_options.include_usage`
- Returns: `{"id":"cmpl-<request-id>","object":"text_completion","model":…,"choices":[{"index":0,"text":…,"logprobs":null,"finish_reason":…}],"usage":{…}}`; streaming emits `text_completion` chunks then `data: [DONE]`
- Errors: OpenAI-shaped error body.

Refused with `400 invalid_request_error` (no IR representation — refusing beats silently ignoring a parameter the caller sent): `logprobs`, `best_of > 1`, `echo`, `n > 1`, and `prompt` as a token-id array or a multi-element string array. `prompt` is mapped onto one user message; a prompt string plus `suffix` is concatenated.

### `POST /v1/messages/count_tokens`

Anthropic token counting. Estimates tokens for the given Messages request (chars/4 heuristic when the provider doesn't expose a counting endpoint).

- Auth: same as `/v1/messages`
- Body: Anthropic Messages-shaped request
- Returns: `{"input_tokens": <int>}`

### `GET /v1/models`

OpenAI-style model list of the model groups visible to the authenticated key.

- Auth: `Authorization: Bearer sk-wiwi-…` (or master key)
- Returns: `{"object":"list","data":[{"id":"gpt-4o","object":"model",…}, …]}`

### Streaming headers & reconnect (all surfaces)

| Header | Meaning |
|---|---|
| `x-wiwi-request-id` | Request ID, echoed on every SSE frame's `id:` field |
| `x-wiwi-trace-id` | Trace ID (32 hex), only when `telemetry.enabled`. Pair it with the collector to pull the exact trace; see `docs/CONFIG.md` § `telemetry` |
| `x-wiwi-stream-id` | Stream journal ID — pass back on reconnect to replay |
| `x-wiwi-session-id` | Client session identity, used only when `router_settings.session_affinity` is on: pins the session to the deployment that served it so upstream prompt caches stay warm. A pin is dropped the moment that deployment is unhealthy or above this request's lane ceiling. Also accepted as a `session_id` query parameter for clients that cannot set headers; truncated to 128 chars. |
| `Last-Event-ID` | SSE standard header; reconnect replays missed frames |

Stream journals are ON by default (`.wiwi/journals/`, 600 s TTL, 1 MiB cap): a client reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replays missed frames even after a wiwi restart. Mid-stream provider death resumes from the tape on a fallback deployment (continuation messages are synthesized from partial output).

### `WS /v1/realtime`

Relays a Realtime WebSocket session to an OpenAI-shaped upstream. **Off by
default** (`realtime.enabled`); with it off the route does not exist.

```
ws://host/v1/realtime?model=<group>[&session_id=…]
Authorization: Bearer sk-wiwi-…
```

Every admission check runs **before** the upgrade, so a refusal is a readable
HTTP status rather than an opaque close: `401` bad key, `402` budget exhausted,
`403` model not allowed, `404` unknown model or surface disabled, `429` rate
limited, `501` the provider has no realtime surface, `503` every deployment is
cooling or at its concurrency cap. Once the client sees `101`, a session exists
upstream and errors arrive as close frames.

The refusal is a literal HTTP response written before the handshake completes,
not a close frame: uvicorn answers **403** to *any* close issued before accept
and discards the code, which would make a bad key indistinguishable from an
unknown model. Writing `websocket.http.response.start` by hand is what preserves
the status.

Frames pass through byte-for-byte in both directions — the session protocol is
stateful and ordered, so wiwi relays it and never rewrites it. A session holds
its deployment's concurrency slot for its whole life, so `max_inflight`,
priority lanes and session affinity apply to sessions exactly as they do to HTTP
requests.

**Auth:** header only by default. `?key=` is accepted only when
`realtime.allow_key_in_query` is on — a query string lands in proxy logs,
browser history and access logs.

**Billing:** a session is charged once, at close, on the **peak** usage its
events reported. Not the sum: the protocol restates overlapping windows of the
same conversation, so summing inflates every multi-turn session. One
request-log row is written per session.

---

## 2. Health & metrics

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness. Always HTTP 200 when the process is up (the Docker healthcheck probes it). `status` is derived, not constant: `ok` when at least one provider is configured and at least one group has an available deployment, else `degraded`. Also reports `groups`, `available_groups`, `providers`, and the log-loss counters: `dropped_request_logs` (queue full), `failed_request_log_writes` (DB write failed), `dropped_proxy_logs`, `failed_audit_log_writes`, `dropped_log_events` (their sum), plus `spend_charge_failures`. A non-zero loss counter means durable accounting is missing rows. |
| `GET /metrics` (default; path configurable) | Prometheus text exposition: `wiwi_requests_total`, `wiwi_request_duration_ms`, `wiwi_tokens_total{type=in\|out\|cached\|reasoning}`, `wiwi_cost_total`, `wiwi_ttft_ms`, `wiwi_tps`, `wiwi_stream_errors_total`, `wiwi_prompt_cache_hits_total`, `wiwi_response_cache_hits_total`, `wiwi_request_logs_dropped_total`, `wiwi_spend_charge_failures_total`. Process-lifetime counters (requests/tokens/cost/cache hits) are monotonic across scrapes; the windowed gauges and the three quantile families come from the in-memory LogEvent ring. `wiwi_request_duration_ms`/`wiwi_ttft_ms`/`wiwi_tps` are `summary` (percentiles computed at scrape time — no `_bucket`/`_sum`/`_count` series, so `histogram_quantile()` does not apply). `wiwi_tps` is output **generation** speed (completion tokens over the first-to-last-token span, excluding queueing and prefill), so only streaming requests contribute a sample; `wiwi_tps_sample_ratio` is a gauge for the share of the current window that `wiwi_tps` describes. |

---

## 3. Admin API (`/admin/*`, master-key auth)

### Virtual keys

| Endpoint | Method | Description |
|---|---|---|
| `/admin/keys` | GET | List virtual keys (hashed at rest; never returns plaintext). |
| `/admin/keys/generate` | POST | Mint a key. Body: name, models, budget, rpm/tpm, expiry, `priority` (lane name). **Plaintext `sk-wiwi-…` returned only here.** |
| `/admin/keys/{key_id}` | PATCH | Update name/models/budget/limits/lane. `priority` is operator-only: a key's owner cannot change their own lane. |
| `/admin/keys/{key_id}` | DELETE | Revoke a key. |
| `/admin/keys/{key_id}/disable` | POST | Disable without deleting. |

### Providers & key pools

| Endpoint | Method | Description |
|---|---|---|
| `/admin/provider-catalog` | GET | Catalog of the 11 provider types (from `PROVIDER_TYPES`) with required fields. |
| `/admin/providers` | GET | List configured providers + key pools + health states. |
| `/admin/providers` | POST | Add a provider account. |
| `/admin/providers/{name}` | PATCH | Edit provider (name/type/base_url/timeout). |
| `/admin/providers/{name}` | DELETE | Delete provider + its keys. |
| `/admin/providers/{name}/keys` | POST | Add a key to the provider's pool (label, secret, weight). |
| `/admin/providers/{name}/keys/{label}` | PATCH | Enable/disable, adjust weight. |
| `/admin/providers/{name}/keys/{label}` | DELETE | Remove one key from the pool. |
| `/admin/providers/{name}/keys/{label}/secret` | GET | Reveal key secret (admin action, audit-logged). |
| `/admin/providers/{name}/models` | GET | Models the provider exposes (live fetch where supported). |
| `/admin/providers/export` | GET | Export provider config (secrets masked or included per flag). |
| `/admin/providers/import` | POST | Import provider config. |

### OAuth providers (Cline, WorkBuddy)

| Endpoint | Method | Description |
|---|---|---|
| `/admin/cline/models` | GET | Models available to the connected Cline account. |
| `/admin/cline/settings` | GET / PUT | Cline account settings. |
| `/admin/cline/settings/default-models/{model_id}` | DELETE | Remove a default-model mapping. |
| `/admin/cline/oauth/login-url` | POST | Get OAuth login URL. |
| `/admin/cline/oauth/connect` | POST | Complete OAuth connect. |
| `/admin/cline/oauth/auto-connect` | POST | Initiate the redirect-based connect. **Dormant:** Cline's Google OAuth ignores `callback_url`, so the redirect never returns a `?code=`; both Cline UIs use the paste-code flow (`login-url` + `connect`) instead. The route works and is tested; it becomes usable if Cline honours `callback_url`. |
| `/admin/cline/oauth/status` | GET | Connection/refresh state. |
| `/admin/cline/oauth/refresh` | POST | Force token refresh. |
| `/admin/cline/oauth/disconnect` | DELETE | Disconnect account. |
| `/cline/oauth/callback` | GET | OAuth redirect target (browser). |
| `/admin/workbuddy/accounts` | GET | List connected WorkBuddy accounts. |
| `/admin/workbuddy/import` | POST | Import WorkBuddy account credentials. |
| `/admin/workbuddy/export` | GET | Export account data. |
| `/admin/workbuddy/refresh` | POST | Force token refresh. |

### Model groups, deployments, aliases

| Endpoint | Method | Description |
|---|---|---|
| `/admin/models` | GET | All model groups + deployments. |
| `/admin/model-groups/{name}/deployments` | POST | Add a deployment to a group. |
| `/admin/model-groups/{name}/deployments` | DELETE | Remove a deployment. |
| `/admin/model-groups/{name}` | PATCH | Edit group (routing/alias settings). |
| `/admin/aliases` | POST | Create a model alias → group mapping. |

### Pricing & alerts

| Endpoint | Method | Description |
|---|---|---|
| `/admin/pricing` | GET | Per-model token prices (DB-backed `model_prices`), each with its per-provider `scopes`. |
| `/admin/pricing/{model_id}` | PUT | Set/override price for a model. Add `?provider=<account-or-type>` to scope it to one provider; omit for the all-providers base rate. |
| `/admin/pricing/{model_id}` | DELETE | Remove override (fall back to bundled table). With `?provider=` removes only that scope. |
| `/admin/alert-rules` | GET / PUT | Spend/alert rule configuration. |

### Logs, stats, realtime

| Endpoint | Method | Description |
|---|---|---|
| `/admin/logs/requests` | GET | Request logs (paginated; supports key/model/status/time filters). |
| `/admin/logs/proxy` | GET | Proxy stream log (upstream request/response metadata). |
| `/admin/stats/overview` | GET | Rollup: requests/min, token totals, cache-hit %, avg TPS, p95 TTFT, error rate, spend. Ranges ≤ 24 h read the in-memory ring; 7d/30d/all-time read DB aggregates. |
| `/admin/stats/timeseries` | GET | Bucketed token/tps series (bucket size scales with range: 1 min → 1 day). |
| `/admin/stream` | GET (SSE) | Realtime event stream consumed by the UI (live stats, log events). |

### Users

| Endpoint | Method | Description |
|---|---|---|
| `/admin/users` | GET | List user accounts. |
| `/admin/users/{uid}` | PATCH | Change role, disable, reset. |

---

## 4. User accounts (`/auth/*`)

Cookie-session auth for the multi-user UI (separate from virtual keys). Passwords hashed with stdlib PBKDF2; sessions are signed HMAC cookies.

| Endpoint | Method | Description |
|---|---|---|
| `/auth/signup` | POST | Create an account (username, password). |
| `/auth/login` | POST | Log in; sets the session cookie. |
| `/auth/logout` | POST | Clear session. |
| `/auth/me` | GET | Current user + role. |
| `/auth/playground-key` | POST | Mint a scoped temporary virtual key for the playground. |

Roles: normal users see only their own keys' usage in `/app/*`; admins see the full console.

---

## 5. Public (`/public/*`, no auth)

| Endpoint | Method | Description |
|---|---|---|
| `/public/models` | GET | Secret-free model catalog for the public front (names + metadata, no keys, no prices beyond published ones). |

---

## 6. Error bodies

`WiwiError` is the unified internal error type; each wire dialect owns its `error_body()` mapping so clients always see errors in their own dialect:

| Surface | Shape |
|---|---|
| OpenAI Chat | `{"error": {"message", "type", "code"}}` |
| OpenAI Completions | `{"error": {"message", "type", "code"}}` (same envelope as Chat) |
| OpenAI Responses | Responses-style error event / object |
| Anthropic Messages | `{"type":"error","error":{"type":"<anthropic_error_type>","message":…}}` |

Common status codes: `401` (bad key), `402` (budget cap exceeded), `404` (unknown model/group), `413` (body too large), `429` (rate limit — a per-key or per-deployment quota with a horizon), `502`/`503` (upstream unavailable after retries/failover), `503` + `Retry-After` (shed: every deployment for the group is at its concurrency ceiling for this request's lane — saturated now, not quota spent).

## 7. Worked examples

The tables above are the contract; these are copy-pasteable equivalents.

```bash
MK="Authorization: Bearer $WIWI_MASTER_KEY"
```

### Virtual keys

```bash
# mint — budget / RPM / TPM / model allowlist / TTL / optional custom_key
curl -X POST localhost:4000/admin/keys/generate -H "$MK" \
  -d '{"name": "team-a", "max_budget": 10, "rpm": 60, "tpm": 100000,
       "models": ["gpt-4o"], "ttl_seconds": 86400}'
# → {"key":"sk-wiwi-...","id":"k...","note":"store this key now..."}

# mint into a priority lane — must name a lane in router_settings.priority_lanes,
# or the call is a 400 rather than a silent fall back to default_lane
curl -X POST localhost:4000/admin/keys/generate -H "$MK" \
  -d '{"name": "nightly", "priority": "bulk"}'

curl localhost:4000/admin/keys -H "$MK"
curl -X PATCH  localhost:4000/admin/keys/<id> -H "$MK" -d '{"max_budget": 20}'
curl -X POST   localhost:4000/admin/keys/<id>/disable -H "$MK"
curl -X DELETE localhost:4000/admin/keys/<id> -H "$MK"
```

### Providers & key pools

```bash
curl localhost:4000/admin/provider-catalog -H "$MK"     # 11 built-in cards + configured?
curl localhost:4000/admin/providers -H "$MK"            # pool status: health + cooldowns
curl -X POST localhost:4000/admin/providers -H "$MK" \
  -d '{"name": "openai-backup", "provider_type": "openai",
       "base_url": "https://api.openai.com/v1", "key": "os.environ/BACKUP_KEY"}'
curl -X PATCH  localhost:4000/admin/providers/<name> -H "$MK" -d '{"name": "openai-primary"}'
curl -X DELETE localhost:4000/admin/providers/<name> -H "$MK"   # 409 while groups reference it

# key pool
curl -X POST  localhost:4000/admin/providers/<name>/keys -H "$MK" \
  -d '{"label": "extra", "key": "os.environ/EXTRA_KEY", "weight": 2}'
curl -X PATCH localhost:4000/admin/providers/<name>/keys/<label> -H "$MK" \
  -d '{"disabled": true, "weight": 5}'          # + reset_status: true clears cooldown
curl -X DELETE localhost:4000/admin/providers/<name>/keys/<label> -H "$MK"
curl localhost:4000/admin/providers/<name>/keys/<label>/secret -H "$MK"  # audit-logged reveal
curl localhost:4000/admin/providers/<name>/models -H "$MK"   # live upstream model ids
```

### Models, groups, aliases

```bash
curl localhost:4000/admin/models -H "$MK"
curl -X PATCH localhost:4000/admin/model-groups/<name> -H "$MK" \
  -d '{"weights": {"openai-main/gpt-4o": 3}, "strategy": "least-busy"}'
curl -X POST localhost:4000/admin/model-groups/<name>/deployments -H "$MK" \
  -d '{"group": "gpt-4o", "provider": "openrouter", "model_id": "openai/gpt-4o", "weight": 1}'
curl -X DELETE localhost:4000/admin/model-groups/<name>/deployments -H "$MK" -d '{...}'
curl -X POST localhost:4000/admin/aliases -H "$MK" \
  -d '{"set": {"gpt-4": "gpt-4o"}, "unset": ["gpt-3.5"]}'
```

### Pricing, logs, stats, users

```bash
curl localhost:4000/admin/pricing -H "$MK"
curl -X PUT    localhost:4000/admin/pricing/<model_id> -H "$MK" -d '{...}'
# Per-provider prices: add ?provider=<account-or-type>. Omit it to set the base rate.
curl -X PUT "localhost:4000/admin/pricing/<model_id>?provider=openai-main" -H "$MK" \
  -d '{"input_per_1m": 1.0, "output_per_1m": 2.0}'

curl localhost:4000/admin/logs/requests -H "$MK"     # DB-backed
curl localhost:4000/admin/logs/proxy -H "$MK"        # ring buffer
curl localhost:4000/admin/stats/overview -H "$MK"    # p50/p95/p99, cost, tokens
curl "localhost:4000/admin/stats/timeseries?bucket=minute&metric=cost&minutes=60" -H "$MK"
curl localhost:4000/admin/stream -H "$MK"            # SSE live tail
curl localhost:4000/admin/alert-rules -H "$MK"
curl -X PUT localhost:4000/admin/alert-rules -H "$MK" -d '{...}'

curl localhost:4000/admin/users -H "$MK"
curl -X PATCH localhost:4000/admin/users/<uid> -H "$MK" -d '{"role": "admin"}'
```

### Session auth

```bash
curl -X POST localhost:4000/auth/signup  -d '{"email": "...", "password": "..."}'
curl -X POST localhost:4000/auth/login   -d '{"email": "...", "password": "..."}'
curl -X POST localhost:4000/auth/logout
curl localhost:4000/auth/me
curl -X POST localhost:4000/auth/playground-key       # scoped 24h session key
```

### OAuth providers (Cline / WorkBuddy)

```bash
# Cline
curl -X POST localhost:4000/admin/cline/oauth/login-url -H "$MK" -d '{}'   # {auth_url, state}
curl -X POST localhost:4000/admin/cline/oauth/connect -H "$MK" -d '{"code": "..."}'
curl -X POST localhost:4000/admin/cline/oauth/auto-connect -H "$MK" -d '{}'
curl localhost:4000/admin/cline/oauth/status -H "$MK"
curl -X POST localhost:4000/admin/cline/oauth/refresh -H "$MK" -d '{"provider": "cline-main"}'
curl -X DELETE localhost:4000/admin/cline/oauth/disconnect -H "$MK" -d '{"provider": "cline-main"}'

# Cline global model list — pick ids once, auto-deploy to every Cline account
curl localhost:4000/admin/cline/models -H "$MK"
curl -X PUT localhost:4000/admin/cline/settings -H "$MK" \
  -d '{"default_models": ["anthropic/claude-sonnet-4-5", "openai/gpt-4o"]}'

# WorkBuddy (CodeBuddy) — parallel API
curl localhost:4000/admin/workbuddy/accounts -H "$MK"
curl -X POST localhost:4000/admin/workbuddy/import -H "$MK" -d '{"accounts": [...]}'
curl -X POST localhost:4000/admin/workbuddy/export -H "$MK"
curl -X POST localhost:4000/admin/workbuddy/refresh -H "$MK" -d '{"label": "main"}'
```
