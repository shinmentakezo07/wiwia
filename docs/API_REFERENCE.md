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
- Body: Responses JSON (`model`, `input`, `stream`, `instructions`, `tools`, …)
- Events (streaming): Responses SSE event names (`response.created`, `response.output_text.delta`, `response.completed`, …)

### `POST /v1/messages` — Anthropic Messages dialect

Anthropic Messages API shape, used by Claude Code and the Anthropic SDK. Also accepts `x-api-key` instead of `Authorization`.

- Auth: `Authorization: Bearer sk-wiwi-…` **or** `x-api-key: sk-wiwi-…` + `anthropic-version` header
- Body: Anthropic JSON (`model`, `messages`, `system`, `max_tokens`, `stream`, `tools`, `thinking`, …)
- Streaming events: `message_start`, `content_block_start/delta/stop`, `message_delta`, `message_stop` (+ `ping`, `error`).

A client that calls `/v1/messages` gets Anthropic-format responses even if the backing deployment is OpenAI — cross-dialect translation happens through the IR.

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
| `x-wiwi-stream-id` | Stream journal ID — pass back on reconnect to replay |
| `Last-Event-ID` | SSE standard header; reconnect replays missed frames |

Stream journals are ON by default (`.wiwi/journals/`, 600 s TTL, 1 MiB cap): a client reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replays missed frames even after a wiwi restart. Mid-stream provider death resumes from the tape on a fallback deployment (continuation messages are synthesized from partial output).

---

## 2. Health & metrics

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness. Returns 200 when the process is up. Used by Docker healthcheck. |
| `GET /metrics` (default; path configurable) | Prometheus text exposition: `wiwi_requests_total`, `wiwi_request_duration_ms`, `wiwi_tokens_total{type=in\|out\|cached\|reasoning}`, `wiwi_cost_total`, `wiwi_ttft_ms`, `wiwi_tps`, `wiwi_stream_errors_total`, `wiwi_provider_cooldowns`. Computed from the in-memory LogEvent ring buffer. |

---

## 3. Admin API (`/admin/*`, master-key auth)

### Virtual keys

| Endpoint | Method | Description |
|---|---|---|
| `/admin/keys` | GET | List virtual keys (hashed at rest; never returns plaintext). |
| `/admin/keys/generate` | POST | Mint a key. Body: name, models, budget, rpm/tpm, expiry. **Plaintext `sk-wiwi-…` returned only here.** |
| `/admin/keys/{key_id}` | PATCH | Update name/models/budget/limits. |
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
| `/admin/cline/oauth/auto-connect` | POST | Connect via stored credentials. |
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
| `/admin/pricing` | GET | Per-model token prices (DB-backed `model_prices`). |
| `/admin/pricing/{model_id}` | PUT | Set/override price for a model. |
| `/admin/pricing/{model_id}` | DELETE | Remove override (fall back to bundled table). |
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
| OpenAI Responses | Responses-style error event / object |
| Anthropic Messages | `{"type":"error","error":{"type":"<anthropic_error_type>","message":…}}` |

Common status codes: `401` (bad key), `402` (budget cap exceeded), `404` (unknown model/group), `413` (body too large), `429` (rate limit), `502`/`503` (upstream unavailable after retries/failover).
