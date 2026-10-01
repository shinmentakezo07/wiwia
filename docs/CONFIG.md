# wiwi — Config Reference

Full reference for `wiwi.yaml` and environment variable interpolation. Config is parsed by Pydantic v2 models in `wiwi/config.py`. All fields have defaults; only `general_settings.master_key` (or `WIWI_MASTER_KEY`) is required to start.

## Config precedence

```
--config flag  >  WIWI_CONFIG env  >  wiwi.yaml
```

Config is loaded in this order:
1. `load_env()` — python-dotenv loads `.env` into `os.environ` (override=False, fills gaps only)
2. Pydantic reads the chosen config file (YAML → typed models)
3. `os.environ/NAME` interpolation resolves inside string values at parse time

## File format

```yaml
# wiwi.yaml — top-level keys (all optional except noted)
general_settings:    {...}
router_settings:     {...}
cache_settings:      {...}
healer:              {...}
model_list:          [...]     # required to serve models
providers:           {...}     # providers referenced by model_list
```

## `general_settings`

```yaml
general_settings:
  master_key: sk-wiwi-master-test        # required: admin auth key (WIWI_MASTER_KEY)
  database_url: os.environ/DATABASE_URL  # default: SQLite in repo root
  redis_url:                             # optional; needs the [redis] extra
  max_keys_per_user: 50                  # live virtual keys per owner (admins included)
  trusted_proxies: os.environ/WIWI_TRUSTED_PROXIES  # CSV of proxy CIDRs
```

| Field | Default | Notes |
|---|---|---|
| `master_key` | — | Admin auth. Use `WIWI_MASTER_KEY` env in production instead of hardcoding. |
| `database_url` | `sqlite+aiosqlite:///wiwi.db` | SQLAlchemy URL. Postgres: `postgresql+asyncpg://...`. `DATABASE_URL` env overrides. |
| `redis_url` | `""` | Redis for the shared response cache; requires the `[redis]` extra. `REDIS_URL` env overrides. |
| `max_keys_per_user` | `50` | Ceiling on live virtual keys per owner. Applies to real accounts, admins included; only the synthetic master (no `users` row) mints un-owned keys. |
| `trusted_proxies` | `[]` | CIDRs of reverse-proxy peers whose `X-Forwarded-For` may key the abuse throttles **and** whose `X-Forwarded-Proto: https` may set the session cookie's `Secure` flag and the Cline OAuth callback scheme. Empty means those headers are never trusted. Put your proxy here when TLS terminates in front of wiwi. `WIWI_TRUSTED_PROXIES` env overrides, comma- or whitespace-separated. |

> **TLS-terminating proxies need `trusted_proxies`.** Uvicorn only maps
> `X-Forwarded-Proto` into the request scheme for `forwarded_allow_ips`
> (loopback by default), so behind an *external* terminator the scheme is
> `http` and wiwi would otherwise set the session cookie without `Secure` and
> build `http://` OAuth callback URLs. Listing the proxy here is what makes
> those decisions correct (rounds 105/106).

> **`host` / `port` / `max_request_body_mb` are not `general_settings`
> fields.** They live under `wiwi_settings` (see below). `log_level` is not a
> config field at all — uvicorn is started with `log_level="info"` and only the
> `--reload` developer path passes it.

> **No `admin_ui_dir` field.** Earlier revisions of this doc listed one. The
> built SPA is located from the `WIWI_STATIC_DIR` environment variable, falling
> back to `Path(__file__).parent / "static"` (`wiwi/server/app.py`); there is no
> config key. The SPA is also mounted at **`/`** with history fallback, not at
> `/admin/ui`.

## `wiwi_settings`

```yaml
wiwi_settings:
  host: 0.0.0.0                   # bind address
  port: 4000                      # bind port
  public_url: ""                  # absolute base URL; when set it wins over the request Host
  drop_params: true               # silently drop params the target provider does not support
  max_request_body_mb: 50         # request body ceiling (Content-Length and chunked/HTTP2)
  store_prompts_in_spend_logs: false
  # Responses API state (spec B). A completed /v1/responses call is stored and
  # may be continued with previous_response_id. ttl_s <= 0 keeps rows forever.
  store_responses: true
  response_store_ttl_s: 86400
  log_retention_days: 30          # prune raw request_logs older than this; 0 = keep forever
  log_max_rows: 10000             # keep at most N raw rows; 0 = unlimited
  log_prune_interval_s: 3600      # seconds between prune sweeps; 0 = startup only
```

| Field | Default | Notes |
|---|---|---|
| `host` | `0.0.0.0` | Bind address. |
| `port` | `4000` | Bind port. |
| `public_url` | `""` | Absolute base URL used when building OAuth callback URLs. Trusted operator config; when set it takes precedence over the request's `Host`, so a TLS-terminating proxy can pin the external scheme/host without `trusted_proxies`. |
| `drop_params` | `true` | Drop request params the target provider does not support instead of erroring. |
| `max_request_body_mb` | `50` | Max request body size in MB. |
| `store_prompts_in_spend_logs` | `false` | Persist full prompt/response content in spend logs. |
| `store_responses` | `true` | Persist `/v1/responses` state so `previous_response_id` and `GET/DELETE /v1/responses/{id}` work. `false` = stateless like before. |
| `response_store_ttl_s` | `86400` | TTL for stored responses; enforced on read and by a 300 s sweeper. `0` keeps them forever. |
| `log_retention_days` | `30` | Drop raw `request_logs` rows older than this; 0 keeps forever. Rows are rolled into `request_rollups` first. |
| `log_max_rows` | `10000` | Cap on raw log rows; 0 = unlimited. |
| `log_prune_interval_s` | `3600` | Seconds between prune sweeps; 0 = startup only. |

## `router_settings`

```yaml
router_settings:
  health_model: none                     # none | scored (default: none)
  health_ewma_alpha: 0.2                 # EWMA smoothing factor
  health_window: 32                      # window for success-rate EWMA
  adaptive_cooldown: false               # only meaningful with health_model: scored
  max_deployments: 50                    # max deployments in a group (WRR cap)
```

| Field | Default | Notes |
|---|---|---|
| `health_model` | `none` | `none` = legacy bit-identical behavior. `scored` = EWMA latency + success-rate scoring gates deployment selection. |
| `health_ewma_alpha` | `0.2` | EWMA smoothing: lower = more history-sensitive, higher = more reactive. |
| `health_window` | `32` | Number of recent requests feeding the success-rate EWMA. |
| `adaptive_cooldown` | `false` | When true with `scored`, keys on cooldown get health-probed before being reactivated. |
| `max_deployments` | `50` | Per-group WRR cap. |

## `cache_settings`

```yaml
cache_settings:
  enabled: false                         # default: off
  ttl_s: 3600                            # cache TTL in seconds
  max_entries: 256                       # LRU cap
  backend: memory                        # memory | redis
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Response cache is off by default. |
| `ttl_s` | `3600` | Time-to-live for cached responses. |
| `max_entries` | `256` | LRU eviction cap. |
| `backend` | `memory` | `memory` = local LRU. `redis` = shared across instances (requires `[redis]` extra). |

The response cache is exact-match: same normalized IR + group + surface + key id → same cached response. Not used for streaming requests or requests with builtin tools. Bypass per-call with `x-wiwi-no-cache: true`.

## `healer`

```yaml
healer:
  enabled: false                         # default: off
  probe_interval_s: 60                   # how often the healer wakes
  max_concurrent_probes: 8               # parallel probe slots
  probation_recovery_window_s: 300      # how long a key stays in probation
  health_model: none                     # must match router_settings.health_model
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | HealthHealer is off by default. |
| `probe_interval_s` | `60` | Seconds between healer wake-ups. |
| `max_concurrent_probes` | `8` | Parallel 1-token probe slots. |
| `probation_recovery_window_s` | `300` | Window during which a key in probation can be restored. |
| `health_model` | `none` | Must match `router_settings.health_model`. |

## `telemetry`

OpenTelemetry OTLP/HTTP trace export. **Off by default**, and free when off: with
`enabled: false` the gateway never imports the SDK. Requires the `otel` extra
(`uv pip install -e '.[otel]'`); without it, enabling this logs
`telemetry_extra_missing` and stays a no-op — tracing is never a serving
dependency, so a missing extra or an unreachable collector cannot fail a request.

```yaml
telemetry:
  enabled: false
  endpoint: http://localhost:4318/v1/traces   # OTLP/HTTP (the /v1/traces path)
  service_name: wiwi
  sample_ratio: 1.0
  export_timeout_s: 10.0
  headers:
    authorization: Bearer <token>
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Master switch. |
| `endpoint` | `""` | Collector URL. Enabling without one warns and stays off. |
| `service_name` | `wiwi` | Becomes the `service.name` resource attribute. |
| `sample_ratio` | `1.0` | Applied to trace **roots** only (`ParentBased`): a sampling decision already made by the caller is kept, so a sampled inbound request is always recorded. |
| `export_timeout_s` | `10.0` | Bounds one export attempt. The exporter is synchronous and retries a dead collector; without a cap a single unreachable endpoint can stall process shutdown. |
| `headers` | `{}` | Extra HTTP headers for the collector (e.g. a vendor token). |

**What is emitted.** One `wiwi.request` span per request (`wiwi.surface`,
`wiwi.request_id`, `wiwi.model`, then `wiwi.status`, `wiwi.cost`,
`wiwi.errored`, and — for streams — `wiwi.streamed`, `wiwi.chunks`,
`wiwi.ttft_ms`), with children:

| Span | Meaning |
|---|---|
| `wiwi.upstream` | One per upstream attempt (so each retry is its own span): `wiwi.deployment`, `wiwi.provider`, `wiwi.attempt`, `wiwi.attempt_status`, `wiwi.latency_ms`. |
| `wiwi.retrieve` | A `previous_response_id` transcript read (stateful Responses): `wiwi.found`. |
| `wiwi.persist` | A stored response write: `wiwi.store_id`. |

Responses carry `x-wiwi-trace-id: <32-hex>` alongside `x-wiwi-request-id` when
tracing is on, so a caller can quote the exact trace.

**Propagation.** A valid inbound W3C `traceparent` is continued — the request
span becomes its child and keeps the caller's trace id. Each outbound upstream
request carries the `traceparent` of **its own attempt span**, so retries appear
as distinct children rather than duplicates under one hop.

**Never emitted:** prompt or response text. `store_prompts_in_spend_logs`
governs content capture and writes to the database, not to a collector.

## `model_list`

Array of model groups. Each entry:

```yaml
model_list:
  - model_name: gpt-4o                    # what clients request
    wiwi_params:
      model: openai/gpt-4o                # provider/type + model_id
      api_key: os.environ/OPENAI_API_KEY  # provider key (optional; uses provider's key pool if absent)
      weight: 1                           # WRR weight in group
      timeout_s: 120                      # per-request timeout
      max_tokens_default: 4096            # default max_tokens if not in request
    model_aliases:                        # optional: additional names → this model
      - gpt-4o-api
```

| Field | Required | Notes |
|---|---|---|
| `model_name` | yes | Canonical name clients use. |
| `wiwi_params.model` | yes | `<provider_type>/<model_id>` or just `<model_id>` for openai. |
| `wiwi_params.api_key` | no | Overrides the provider's key for this deployment. Uses provider key pool if absent. |
| `wiwi_params.weight` | no | Default `1`. WRR weight within the group. Must be `>= 1`; a `0` or negative weight starves the group's other deployments. |
| `wiwi_params.timeout_s` | no | Per-request timeout for this deployment. |
| `wiwi_params.max_tokens_default` | no | Default `max_tokens` when the client doesn't send one. |
| `model_aliases` | no | Additional names clients can use to reach this model. |

## `providers`

Map of provider type → provider config. Each provider type has its own required fields.

```yaml
providers:
  openai:
    api_key: os.environ/OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    timeout_s: 120
    budget_cap: 50.0                       # optional per-provider budget cap (USD)
```

### Provider types and their fields

**`openai`** — OpenAI API
```yaml
providers:
  openai:
    api_key: os.environ/OPENAI_API_KEY
    base_url: https://api.openai.com/v1     # default
    timeout_s: 120                          # default: 120
    budget_cap: 50.0                        # optional USD cap
```

**`anthropic`** — Anthropic Messages API
```yaml
providers:
  anthropic:
    api_key: os.environ/ANTHROPIC_API_KEY
    base_url: https://api.anthropic.com      # default
    timeout_s: 120
    budget_cap: 50.0
```

**`gemini`** — Google Gemini
```yaml
providers:
  gemini:
    api_key: os.environ/GEMINI_API_KEY
    base_url: https://generativelanguage.googleapis.com/v1beta/openai   # default
    timeout_s: 120
    budget_cap: 50.0
```

**`openai-compatible`** — Any OpenAI-format endpoint
```yaml
providers:
  my-ollama:
    type: openai-compatible
    api_key: ""                               # often not needed
    base_url: http://localhost:11434/v1      # REQUIRED for this type
    timeout_s: 120
    budget_cap: 0                            # local models: no cost
```

**`openrouter`** — OpenRouter
```yaml
providers:
  openrouter:
    api_key: os.environ/OPENROUTER_API_KEY
    base_url: https://openrouter.ai/api/v1   # default
    timeout_s: 120
    budget_cap: 50.0
```

**`gmicloud`** — GMICloud (OpenAI-format)
```yaml
providers:
  gmicloud:
    api_key: os.environ/GMI_API_KEY
    base_url: https://api.gmicloud.ai/v1     # default
    timeout_s: 120
    budget_cap: 50.0
```

**`bai`** — Baidu AI (OpenAI-format)
```yaml
providers:
  bai:
    api_key: os.environ/BAIDU_API_KEY
    base_url: https://aip.baidubce.com/rpc/BDljKG3w/invoke   # default
    timeout_s: 120
    budget_cap: 50.0
```

**`nvidia-nim`** — NVIDIA NIM (OpenAI-format, with quirks)
```yaml
providers:
  nvidia-nim:
    api_key: os.environ/NVIDIA_API_KEY
    base_url: https://integrate.api.nvidia.com/v1   # default
    timeout_s: 120
    budget_cap: 50.0
```
NIM has JSON Schema quirks: rejects boolean subschemas and params named `type`. wiwi adapts tool schemas automatically via `nim_tool_schema.py`. See [PROVIDERS.md](PROVIDERS.md).

**`cline`** — Cline (OAuth + auto-refresh)
```yaml
providers:
  cline:
    client_id: os.environ/CLINE_CLIENT_ID
    client_secret: os.environ/CLINE_CLIENT_SECRET
    base_url: https://cline.bot/v1            # default
    timeout_s: 120
    budget_cap: 50.0
```
Cline uses OAuth with on-demand refresh. `cline_oauth.py` + `cline_auto_refresh.py` manage tokens. The adapter holds per-stream OAuth state. See [PROVIDERS.md](PROVIDERS.md).

**`workbuddy`** — WorkBuddy (OAuth + auto-refresh)
```yaml
providers:
  workbuddy:
    client_id: os.environ/WB_CLIENT_ID
    client_secret: os.environ/WB_CLIENT_SECRET
    base_url: https://api.workbuddy.app/v1     # default
    timeout_s: 120
    budget_cap: 50.0
```
WorkBuddy uses OAuth with on-demand refresh. See [PROVIDERS.md](PROVIDERS.md).

**`opencode`** — OpenCode Zen (multi-upstream routing)
```yaml
providers:
  opencode:
    api_key: os.environ/OPENCODE_API_KEY
    base_url: https://opencode.z_bot.io/v1      # default
    timeout_s: 120
    budget_cap: 50.0
```
OpenCode routes per-model across four upstream protocols and refreshes its `opencode/<version>` User-Agent live. See [PROVIDERS.md](PROVIDERS.md).

### Provider key pool (admin-managed, not in config file)

In addition to config-file provider keys, the admin API manages a per-provider key pool:

```yaml
# Not in config — managed via /admin/providers/{name}/keys
providers:
  openai:
    api_key: os.environ/OPENAI_API_KEY       # primary key from config
    # + additional keys added via admin API, each with weight, enabled flag
```

The key pool concept: a provider can have multiple real API keys with weights (each must be `>= 1`). The router picks a key from the pool on each request. Keys can be in states: `active`, `cooling` (cooldown after error), `invalid` (failed auth), `disabled`.

## `os.environ/NAME` interpolation

Any string value in config can use `os.environ/NAME` to read from environment. Missing env vars resolve to empty string (doesn't crash). Providers with empty keys after interpolation are filtered out at startup.

```yaml
api_key: os.environ/OPENAI_API_KEY     # reads OPENAI_API_KEY env
base_url: os.environ/BASE_URL          # reads BASE_URL env
```

This is the recommended way to keep secrets out of config files.

## `DATABASE_URL` env override

`DATABASE_URL` overrides `general_settings.database_url` regardless of config file content:

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host/db wiwi --config wiwi.yaml
```

## `.env` loading

`load_env()` (python-dotenv) loads `.env` from the CWD if it exists. Existing environment variables are never overwritten — `.env` only fills gaps. Provider keys, `DATABASE_URL`, `WIWI_MASTER_KEY`, `WIWI_CONFIG` all work from `.env`.

## Environment variables (runtime knobs)

| Variable | Effect |
|---|---|
| `WIWI_CONFIG` | Config file path or inline YAML. Overrides the `wiwi.yaml` default. |
| `WIWI_MASTER_KEY` | Master key for admin auth. |
| `DATABASE_URL` | Overrides `general_settings.database_url`. |
| `REDIS_URL` | Overrides `general_settings.redis_url`. |
| `WIWI_TRUSTED_PROXIES` | Overrides `general_settings.trusted_proxies`. Comma- or whitespace-separated CIDRs, e.g. `10.0.0.0/8,127.0.0.1/32`. Unset/empty trusts no forwarded header (fail-closed, AUDIT #73). This is the knob to set when TLS terminates at a proxy in front of a container whose `wiwi.yaml` is baked into the image. |
| `WIWI_SESSION_SECRET` | Session signing key (32-byte hex). Defaults to the master key (the process refuses to start with neither). |
| `WIWI_STATIC_DIR` | Directory holding the built SPA (default: `wiwi/server/static`). |
| `WIWI_PORT` | Backend port used by `start.sh` (default 4000). The CLI's own `--port` flag is authoritative when given. |
| `WIWI_RELOAD` | `0` disables `--reload` in `start.sh` (reload is on by default there; the CLI flag is `--reload`). |
| `WIWI_RELOAD_DIRS` | Comma-separated dirs to watch in reload mode. |
| `WIWI_BIN` | Path to the wiwi binary (for `start.sh`). |
| `FORWARDED_ALLOW_IPS` | Uvicorn's own trusted-proxy list for `X-Forwarded-*` mapping into the request scheme/client. Independent of `general_settings.trusted_proxies`, which wiwi applies itself. |

## config.py models

All config is parsed through Pydantic v2 models in `wiwi/config.py`:

- `WiwiConfig` — top-level aggregate (`providers`, `model_list`, `router_settings`, `general_settings`, `wiwi_settings`, `cache_settings`, `healer`)
- `GeneralSettings` — `master_key`, `database_url`, `redis_url`, `max_keys_per_user`, `trusted_proxies`
- `WiwiSettings` — `host`, `port`, `public_url`, `drop_params`, `max_request_body_mb`, `store_prompts_in_spend_logs`, `store_responses`, `response_store_ttl_s`, `log_retention_days`, `log_max_rows`, `log_prune_interval_s`
- `RouterSettings` — health_model, ewma_alpha, window, adaptive_cooldown
- `CacheSettings` — enabled, ttl_s, max_entries, backend
- `HealerSettings` — enabled, probe_interval_s, etc.
- `ProviderDef` — per-provider config (`base_url`, `timeout_s`, `extra_headers`, `round_robin`, `keys`, `alias_id`)
- `KeyDef` — provider key entry (`label`, `key`, `weight`, `enabled`)
- `DeploymentParams` — per-deployment overrides (`provider`, `model`, `weight`, `max_tokens`, `rpm`, `tpm`, `timeout`, `extra_headers`, `extra_body`, `prompt_cache`, …)
- `ModelEntry` — model_list entry (`model_name`, `wiwi_params`)

`PROVIDER_TYPES` is a module-level tuple in `config.py` listing all 11 provider types. It is the single source of truth — the router, admin API, and Pydantic schema all reference it.

## Validation at startup

Config fails fast at startup:
- Unknown provider types in `model_list` → error
- Provider referenced by `model_list` but not in `providers` → error
- Invalid URL fields → error
- Empty required fields → error

The import-time assert in `wiwi/providers/registry.py` checks that every `PROVIDER_TYPES` entry has a matching branch in `get_adapter()`. Adding a provider = new adapter + branch in registry.
