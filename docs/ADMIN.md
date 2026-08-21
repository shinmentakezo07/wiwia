# wiwi — Admin Panel & Key-Pool Design

How administration works: provider accounts with multiple API keys per provider, round-robin key selection, cross-provider balancing for shared model ids, virtual client keys (custom or random) created from the UI, full usage analytics (TPS, input/cached/reasoning/output tokens) with charts, realtime UI updates, and dedicated request-log and proxy-log pages. This doc extends `ARCHITECTURE.md` (§4.4) and defines the web panel referenced in `PLAN.md` Phase 6.

---

## 1. Concepts and relationships

```
Provider (openai)                Provider (azure-oai)            Provider (ollama-local)
├─ base_url, timeout             ├─ base_url                     ├─ base_url
└─ KeyPool                       └─ KeyPool                      └─ KeyPool
   ├─ key #1 (weight 3)             └─ key #1                       └─ key #1
   ├─ key #2 (weight 1)
   └─ key #3 (weight 1, cooling)

Model group "gpt-4o"  ←── clients request this name
├─ deployment: provider=openai,     model_id=gpt-4o, weight=2
└─ deployment: provider=azure-oai,  model_id=gpt-4o, weight=1

Virtual key "sk-wiwi-…" (client credential)
├─ allowed models, budget, rpm/tpm, expiry        ← what apps use
```

- **Provider**: an upstream vendor/endpoint (openai, anthropic, gemini, any openai-compatible URL). Holds connection defaults and a **key pool**.
- **Provider key**: one real upstream credential. Has: label, secret (encrypted at rest), weight, enabled flag, health state (`active | cooling | invalid | disabled`), cooldown-until, per-window counters, last-used, totals.
- **Deployment**: provider + `model_id` + overrides (timeout, max_tokens default, rpm/tpm cap). Belongs to a model group.
- **Model group** (`model_name`): what clients request. May span **multiple providers** — if two providers serve the same model id, both appear as deployments and share traffic.
- **Virtual key**: client-facing credential issued from the admin UI (custom string or randomly generated).

## 2. Routing logic: two-tier selection

Tier 1 balances across deployments (which may be different providers serving the same model id); tier 2 round-robins across that provider's keys.

```python
def route(group, ctx):
    # Tier 1 — deployment selection (existing router strategies)
    deps = [d for d in group.deployments if d.provider.has_healthy_keys()]
    dep = ctx.strategy.pick(deps)          # simple-shuffle (weight-weighted),
                                           # least-busy, latency-based
    # Tier 2 — key selection inside the chosen provider
    key = dep.provider.pool.pick()         # smooth weighted round-robin,
                                           # skipping cooling/invalid/disabled
    return dep, key
```

### Round-robin within a provider's key pool

- **Smooth weighted round-robin** (nginx algorithm): each key keeps `current_weight`; every pick adds its configured weight, the highest-current key wins and subtracts total_weight. Gives even long-run distribution with weights, unlike naive modulo RR.
- Counter is an atomic in-memory integer per process; when Redis is enabled, `INCR pool:{id}:rr` makes all replicas share one sequence.
- Keys skipped while `cooling` (429 backoff) or `invalid` (401/403). If **all** keys are cooling, return 429 upstream-style with the soonest `Retry-After`; if all invalid/disabled, fail the deployment and let tier-1 fallbacks take over.

### Failure semantics (per upstream response)

| Upstream result | Key action | Deployment/router action |
|---|---|---|
| 200 | counters++, last_used=now | normal |
| 429 | key → `cooling` for `Retry-After` (or default 30s); proxy-log warn | retry immediately on **next key** in same pool, then next deployment (counts toward `num_retries`) |
| 401 / 403 | key → `invalid`, disabled, admin alert | retry next key/deployment |
| 408 / 5xx / timeout / conn error | nothing (key is fine) | router retry/cooldown as usual |
| context overflow | nothing | `context_window_fallbacks` |

Every transition emits a `key.status` event on the admin SSE stream and a row in proxy logs.

### Config example (YAML bootstrap; DB overlay after UI edits)

```yaml
providers:
  - name: openai-main
    provider: openai
    keys:
      - {label: main,       key: os.environ/OPENAI_KEY_1, weight: 3}
      - {label: backup,     key: os.environ/OPENAI_KEY_2, weight: 1}
  - name: azure-gpt4o
    provider: openai-compatible
    base_url: https://my.azure.openai.com/v1
    keys: [{label: az1, key: os.environ/AZURE_KEY}]

model_list:
  - model_name: gpt-4o          # group spans TWO providers -> tier-1 balances
    wiwi_params: {provider: openai-main, model: gpt-4o, weight: 2}
  - model_name: gpt-4o
    wiwi_params: {provider: azure-gpt4o, model: gpt-4o, weight: 1}
```

## 3. Data model additions

```text
providers        id, name, provider_type, base_url, timeout_s, enabled,
                 extra_headers(json), created_at

provider_keys    id, provider_id FK, label, secret_enc (AES-GCM ciphertext),
                 secret_last4, weight, enabled, status(active|cooling|invalid|
                 disabled), cooldown_until, last_used_at,
                 req_count_24h, tok_in_24h, tok_out_24h, err_count_24h

deployments      (extends ARCHITECTURE §4.7)
                 + provider_id FK, model_id, weight

config_version   id(single row), version int, updated_at   ← bumped on every
                 admin write; workers poll or receive NOTIFY to hot-reload

admin_audit      id, actor, action, target_type, target_id, diff(json), at
```

`request_logs` gains: `provider_key_id`, `ttft_ms`, `stream_seconds`, `tps`, and token detail columns `tok_cached`, `tok_reasoning`.

Source of truth after the UI ships: **DB for providers/keys/virtual keys/budgets**, YAML remains bootstrap + fallback. Every admin mutation bumps `config_version`; gateways hot-reload pools/groups without restart (Postgres `LISTEN/NOTIFY`, or 5s version poll on SQLite).

## 4. Token & performance metrics (what we track)

Per request, normalized into IR usage (provider quirks absorbed by adapters):

| Metric | OpenAI | Anthropic | Gemini |
|---|---|---|---|
| input tokens | `usage.prompt_tokens` | `usage.input_tokens` | `promptTokenCount` |
| cached tokens | `prompt_tokens_details.cached_tokens` | `cache_read_input_tokens` (+`cache_creation_input_tokens`) | `cachedContentTokenCount` |
| reasoning tokens | `completion_tokens_details.reasoning_tokens` | not reported → estimate from thinking-block chars ÷ 4, flagged estimated | `thoughtsTokenCount` |
| output tokens | `usage.completion_tokens` | `output_tokens` | `candidatesTokenCount` |

Derived per streaming request: **TTFT** (request start → first content byte), **stream seconds** (first → last byte), **TPS** = `output_tokens / stream_seconds`. Non-streaming rows get latency only.

## 5. Aggregations & rollups

Background logger maintains, alongside `request_logs`:

```text
hourly_stats   hour, key_id, model_group, provider_id, provider_key_id,
               requests, errors, tok_in, tok_cached, tok_reasoning, tok_out,
               cost, stream_seconds, ttft_sum_ms, tps_weighted
daily_stats    same grain at day level (feeds month views fast)
```

Dashboard queries are rollup-only; raw `request_logs` is for the logs page and ad-hoc filters. Retention: raw logs 30d default (configurable), rollups forever.

## 6. Admin API (FastAPI, session-auth)

```
POST /admin/auth/login            {master_key} → HttpOnly session cookie (12h)
POST /admin/auth/logout

GET/POST/PATCH/DELETE /admin/providers
POST   /admin/providers/{id}/keys           {label, key?, weight}  # key omitted → must paste; placeholder gen for compat providers
PATCH  /admin/provider-keys/{id}            {enabled, weight, label}
POST   /admin/provider-keys/{id}/test       → live ping through that key
GET    /admin/provider-keys/{id}/stats

GET/POST/PATCH/DELETE /admin/keys           # virtual keys
POST   /admin/keys/generate                 {name, custom_key?, models?, budget?, rpm?, tpm?, expires?}
                                            # custom_key present → use it (≥16 chars, unique);
                                            # absent → random sk-wiwi-<token_urlsafe(32)>; plaintext returned ONCE

GET    /admin/models                        # groups, deployments, weights, live health
PATCH  /admin/model-groups/{name}           {weights, strategy, fallbacks}

GET    /admin/stats/overview?range=&group_by=
GET    /admin/stats/timeseries?metric=tokens|requests|cost|tps&bucket=minute|hour|day
GET    /admin/logs/requests?filters…&cursor=        # cursor pagination
GET    /admin/logs/proxy?level=&cursor=
GET    /admin/stream                        # SSE (below)
```

All mutations write `admin_audit` and bump `config_version`.

## 7. Realtime channel (SSE)

Single endpoint `GET /admin/stream` (EventSource-friendly, cookie auth). Event types:

```json
event: log.created
data: {"id":"…","ts":"…","key":"team-a","model":"gpt-4o","provider":"openai",
       "provider_key":"main","surface":"/v1/chat/completions","status":200,
       "tok_in":120,"tok_cached":80,"tok_reasoning":0,"tok_out":356,
       "tps":48.2,"ttft_ms":310,"latency_ms":7700,"cost":0.0042}

event: stats.tick        // 1s rolled aggregates for live cards/sparklines
data: {"rpm":42,"tps_total":1830,"err_rate":0.01,"spend_today":13.37}

event: key.status
data: {"provider":"openai","key_label":"backup","status":"cooling",
       "cooldown_until":"…","reason":"429"}

event: proxy.log
data: {"ts":"…","level":"warn","msg":"retry 2/2 on deployment azure-gpt4o/gpt-4o"}

event: config.updated
data: {"version":137,"what":"provider_keys"}   // clients refetch affected queries
```

Server keeps a ring buffer (last 500 events) so reconnecting clients replay via `Last-Event-ID`. TanStack Query subscribers patch caches on `log.created`/`stats.tick` and invalidate on `config.updated` — so creating a key or toggling one reflects everywhere within a second, no manual refresh.

## 8. Web UI (Next.js + shadcn/ui)

### Stack

| Concern | Pick |
|---|---|
| Framework | Next.js (App Router) + React 19, TypeScript strict |
| Components | shadcn/ui + Tailwind (buttons, dialogs, forms, data-table primitives, toasts via sonner) |
| Charts | shadcn charts (Recharts under the hood): Area, Bar, Line, Pie/Donut, RadialBar |
| Tables | TanStack Table v8 + @tanstack/react-virtual (logs pages stay smooth at 100k rows) |
| Data | TanStack Query v5 + the SSE stream above |
| Forms | react-hook-form + zod (shared schemas with API via zod package) |
| Icons/misc | lucide-react, date-fns, nuclide date-range picker pattern |

### Pages

1. **Dashboard** — stat cards (requests/min, total tokens in/out, cache-hit %, avg TPS, p95 TTFT, error rate, spend today vs budget gauge); area chart tokens/min stacked (input · cached · reasoning · output); line chart requests & errors; donut traffic share by model; live sparklines fed by `stats.tick`.
2. **Providers** — provider cards with key-pool tables: label, masked key (`sk-…4f2a`), weight, status badge (active/cooling/invalid), 24h usage bars, last used; actions: add key (paste real key; "test" button pings upstream), enable/disable, adjust weight; add provider dialog (type dropdown incl. openai-compatible + base_url).
3. **Virtual Keys** — create dialog with **two modes**: "Generate random key" or "Custom key" (paste your own, validated ≥16 chars + uniqueness); set name, model allowlist (multi-select from groups), budget + reset period, rpm/tpm, expiry; reveal-once screen with copy button; list with usage sparkline, spend vs budget progress, revoke.
4. **Models** — model groups with deployment chips per provider (weight editable inline), strategy selector, fallback editor, per-deployment health dots; "Test group" button fires a probe request.
5. **Request Logs** — virtualized table: time, virtual key, model, provider+pool key used, surface, status, tokens (in/cached/reasoning/out), TPS, TTFT, latency, cost; filter bar (key, model, provider, status, surface, time range, min tokens); click → detail sheet (full metadata, error body if any, retry/fallback chain); **Live tail** toggle subscribing to `log.created`.
6. **Proxy Logs** — gateway operational stream separate from request data: config reloads, key cooldowns/invalidations, retries, fallback switches, upstream 5xx, startup/shutdown; level filter, live tail, same virtualized table.
7. **Analytics** — deep dive: group-by switcher (key/model/provider/day-of-week/hour), metric switcher, chart-type toggle (bar/line/area/pie), hourly heatmap (day × hour volume), spend-per-key horizontal bars, cache savings card ($ saved by cached tokens), CSV/JSON export buttons.
8. **Budgets & Alerts** — budget editors per key, projected month-end spend (trailing 7-day average), alert rules (spend % threshold, error-rate spike, key invalid) → webhook/Slack payload preview.
9. **Settings** — session/master-key info, retention policy, webhook endpoints, dark/light theme.

### Auth flow

Login page posts master key → session cookie → middleware guards `/admin/*` routes; SSE rides the cookie. RBAC (owner/admin/viewer) is post-MVP; audit log records actor=single-admin for now.

## 9. Security notes

- Provider key secrets encrypted at rest (AES-GCM, `WIWI_ENCRYPTION_KEY` from env); decrypted only in worker memory at dispatch; UI shows last4 only.
- Virtual key plaintext returned exactly once at creation; custom keys hashed on ingest.
- Session cookies HttpOnly + Secure + SameSite=Lax; CSRF double-submit on mutations; login rate-limited.
- Admin API never proxied by the gateway routes; separate port or path prefix blocked at reverse proxy if UI is public.

## 10. Deployment modes

1. **Compose (recommended)**: `wiwi-api` + `wiwi-web` (Next.js standalone) + Postgres (+ optional Redis). Web talks to api over the internal network.
2. **Single binary**: `wiwi serve --with-ui` serves the pre-built static Next.js export from FastAPI for one-box self-hosting (no ISR/server actions used, so static export works).

## 11. Build mapping

| Work | Phase |
|---|---|
| providers/provider_keys tables, key pool + smooth WRR, failure hooks | P4 (control plane) |
| config_version hot-reload + admin_audit | P4 |
| rollups (hourly/daily), token-detail columns, TPS/TTFT capture | P5 |
| admin REST + session auth + SSE stream | P5–P6 |
| Next.js panel pages 1–9 | P6 |
| compose + single-binary UI embed | P6 |

## 12. More ideas worth adding (prioritized suggestions)

1. **Playground page** — chat with any configured model through your own gateway; side-by-side compare two providers on the same prompt; shows live token/cost estimate before send.
2. **Cost forecaster** — month-end projection per key/model with budget-burn alerts (partially covered in Budgets page).
3. **Cache savings report** — dollars saved via prompt caching per key/model; nudges teams to structure prompts for cache hits.
4. **Fallback drill button** — simulate a provider outage (blocklist toggle) and watch traffic shift in the dashboard; great for validating resilience.
5. **Upstream incident detection** — rolling 5xx/latency per provider surfaces a "provider degraded" banner automatically.
6. **Session explorer** — opt-in full request/response body capture per key for debugging agent tool loops (with redaction presets).
7. **Weekly email/PDF digest** — spend, tokens, top keys, anomalies.
8. **Multi-admin RBAC + invites** — owner/admin/viewer roles; viewer sees dashboards, cannot mutate.
9. **Terraform/OpenAPI-driven config export** — export current DB config as YAML/IaC for GitOps.
10. **Anomaly annotations** — click a spike on any chart to pin a note (deploys, incidents) visible to the team.
