# wiwi — Config Reference

Full reference for `wiwi.yaml` and environment variable interpolation. Config is parsed by Pydantic v2 models in `wiwi/config.py`. All fields have defaults; only `general_settings.master_key` is required to start.

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
providers:           [...]     # providers referenced by model_list
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
| `master_key` | — | Admin auth key. **No code reads a `WIWI_MASTER_KEY` environment variable directly** — it takes effect only if your config interpolates it, as `wiwi.yaml.example` does with `master_key: os.environ/WIWI_MASTER_KEY`. A literal `master_key:` ignores the env var. Startup fails closed unless this is set or `WIWI_SESSION_SECRET` is. |
| `database_url` | `sqlite+aiosqlite:///wiwi.db` | SQLAlchemy URL. Postgres: `postgresql+asyncpg://...`. `DATABASE_URL` env overrides. |
| `redis_url` | `""` | Redis for the shared response cache; requires the `[redis]` extra. `REDIS_URL` env overrides. |
| `max_keys_per_user` | `50` | Ceiling on live virtual keys per owner. Applies to real accounts, admins included; only the synthetic master (no `users` row) mints un-owned keys. |
| `trusted_proxies` | `[]` | CIDRs of reverse-proxy peers whose `X-Forwarded-For` may key the abuse throttles **and** whose `X-Forwarded-Proto: https` may set the session cookie's `Secure` flag and the Cline OAuth callback scheme. Empty means those headers are never trusted. Put your proxy here when TLS terminates in front of wiwi. `WIWI_TRUSTED_PROXIES` env overrides, comma- or whitespace-separated. |
| `unpriced_model_policy` | `"warn"` | What a virtual key's `max_budget` means when the resolved model has no pricing row. `warn` (default) serves and tags the request `unpriced_model` in the request log; `admit` serves silently (pre-#323 behaviour); `reject` refuses budget-capped keys with **503** `unpriced_model_error` until an admin prices the model. Uncapped keys and the master key are never affected. |

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
  routing_strategy: simple-shuffle       # simple-shuffle | least-busy | latency-based
  num_retries: 2                         # attempts per deployment before fallback
  timeout: 120.0                         # gateway-wide per-request timeout (seconds)
  allowed_fails: 3                       # consecutive fails before a key cools
  cooldown_time: 30.0                    # key cooldown seconds
  fallbacks: {}                          # model_name → [model_name, …]
  context_window_fallbacks: {}           # model_name → [larger-context model, …]
  model_group_alias: {}                  # alias name → model_name (or {model, weight})
  global_rpm: null                       # gateway-wide rpm ceiling; null = unset
  global_tpm: null                       # gateway-wide tpm ceiling; null = unset
  cycle_every_n: 3                       # advance the WRR cursor every N requests; 0 disables
  failover_mode: any_error               # any_error | standard
  key_max_consecutive_fails: 5           # permanent retirement threshold (any_error mode)
  prometheus_enabled: false
  prometheus_path: /metrics              # must be a literal path starting with "/"
```

Streaming resilience and journal knobs live here too — see the tables below.

| Field | Default | Notes |
|---|---|---|
| `routing_strategy` | `simple-shuffle` | `simple-shuffle`, `least-busy`, or `latency-based`. |
| `num_retries` | `2` | Attempts per deployment before moving on. |
| `timeout` | `120.0` | Gateway-wide per-request timeout, used for any provider that does not set its own `timeout_s`. A deployment's `wiwi_params.timeout` overrides both. |
| `allowed_fails` | `3` | Consecutive failures before a key enters cooldown. |
| `cooldown_time` | `30.0` | Key cooldown, in seconds. |
| `fallbacks` | `{}` | `model_name` → ordered list of fallback model names. |
| `context_window_fallbacks` | `{}` | `model_name` → list of larger-context models to overflow into. |
| `model_group_alias` | `{}` | Client-facing alias → `model_name`, or a `{model, weight}` entry. Aliases live here, **not** on the model entry. |
| `global_rpm` / `global_tpm` | `None` | Gateway-wide ceilings. `null` = unset. |
| `cycle_every_n` | `3` | Force the WRR cursor forward every N requests served by the same provider or key, so traffic actually rotates. `0` keeps weight-driven WRR only. |
| `failover_mode` | `any_error` | `any_error` rotates to the next key on any non-200 while still counting consecutive fails; `standard` keeps 429/5xx-only cooldown behaviour. |
| `key_max_consecutive_fails` | `5` | Consecutive failures before a key is permanently retired. Only relevant in `any_error`; 401/403 count twice. |
| `prometheus_enabled` | `false` | Enables the `/metrics` endpoint. |
| `prometheus_path` | `/metrics` | Must be a literal path starting with `/` and contain no `{}` — `create_app` registers it with `@app.get(...)`, so a non-literal path fails the whole gateway at boot. |

### Streaming resilience

| Field | Default | Notes |
|---|---|---|
| `stream_idle_timeout_s` | `30.0` | Max seconds between upstream chunks before the stream is aborted. |
| `stream_ping_interval_s` | `15.0` | SSE keep-alive ping cadence — a long thinking phase emits no upstream bytes and an idle proxy reaps the connection. `0` disables. Keep below `stream_idle_timeout_s`. |
| `stream_loop_detection` | `true` | Abort on a non-terminating upstream loop. |
| `stream_loop_limit` | `100` | Identical consecutive chunks before aborting. |
| `stream_coalesce` | `false` | Coalesce `TextDelta`s under backpressure. |
| `stream_coalesce_max_bytes` | `8192` | Byte ceiling per coalesced flush. |
| `stream_coalesce_max_ms` | `50.0` | Time ceiling per coalesced flush. |
| `stream_resume` | `"off"` | `off`, `content_only`, or `enabled`. Mid-stream failover retries on a fallback deployment with partial output prepended. |
| `stream_resume_max_retries` | `1` | Mid-stream resume attempts. |
| `stream_event_ids` | `false` | Assign monotonic SSE event ids for `Last-Event-ID` resumption. |
| `stream_grace_drain_s` | `0.0` | On client disconnect, keep pumping upstream for accurate billing. `0` cancels immediately. |

### Stream journal

Encoded SSE frames are appended to a per-request JSONL file, so a client reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replays even after a wiwi restart. **On by default.** Journals are key-scoped — readable only by the virtual key that created them.

| Field | Default | Notes |
|---|---|---|
| `stream_journal_enabled` | `true` | Master switch. |
| `stream_journal_dir` | `.wiwi/journals` | Journal directory. |
| `stream_journal_ttl_s` | `600.0` | Journals older than this are swept at startup and opportunistically at finish. |
| `stream_journal_max_bytes` | `1048576` | Per-journal byte cap (1 MiB). |

## `cache_settings`

```yaml
cache_settings:
  enabled: false                         # default: off
  ttl_s: 3600.0                          # cache TTL in seconds
  max_entries: 256                       # LRU cap
  bypass_header: x-wiwi-no-cache         # request header forcing a one-call bypass
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Response cache is off by default. |
| `ttl_s` | `3600.0` | Time-to-live for cached responses. |
| `max_entries` | `256` | LRU eviction cap. |
| `bypass_header` | `x-wiwi-no-cache` | Send this header to skip the cache for one call. |

The response cache is exact-match: same normalized IR + group + surface + key id → same cached response. Not used for streaming requests or requests with builtin tools.

There is no `backend` field. The backend is chosen from `general_settings.redis_url` (or `REDIS_URL`): Redis when set **and** the `[redis]` extra imports, otherwise in-process memory.

## `healer`

```yaml
healer:
  enabled: false                         # default: off
  tick_s: 30.0                           # sweep cadence
  probe_timeout_s: 10.0                  # per-probe timeout
  max_probes_per_sweep: 8                # blast-radius cap per tick
  min_probe_interval_s: 30.0             # earliest re-probe of the same target
  probe_backoff_base_s: 60.0             # per-target circuit base on failed probes
  probe_backoff_cap_s: 3600.0            # …capped here
  probes_to_restore: 2                   # consecutive healthy probes before restore
  probation_weight: 0.5                  # WRR weight multiplier while on probation
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | `HealthHealer` is off by default — probes spend real provider money, same opt-in ethos as `cache_settings`. |
| `tick_s` | `30.0` | Seconds between sweeps. |
| `probe_timeout_s` | `10.0` | Per-probe timeout. |
| `max_probes_per_sweep` | `8` | Parallel 1-token probe slots per tick. |
| `min_probe_interval_s` | `30.0` | Earliest re-probe of the same target. |
| `probe_backoff_base_s` | `60.0` | Per-target circuit-breaker base on failed probes. |
| `probe_backoff_cap_s` | `3600.0` | Ceiling for that backoff. |
| `probes_to_restore` | `2` | Consecutive healthy probes before a target is restored. |
| `probation_weight` | `0.5` | WRR weight multiplier while a restored key sits in probation. Clamped to `(0, 1]` — at `0` the key could never be picked again and so could never graduate. |

The healer probes cooling/invalid keys and cooled deployments with a 1-token completion and restores them early into a *probation* state (reduced WRR weight until it graduates).

## `model_list`

Array of model groups. Each entry names a group (`model_name`) and one or more deployments (`wiwi_params`), each pointing at a provider **account** and a provider-native model id.

```yaml
model_list:
  - model_name: gpt-4o                    # what clients request
    wiwi_params:
      provider: openai-main               # REQUIRED: a `name` from providers:, not a provider type
      model: gpt-4o                       # REQUIRED: provider-native model id
      weight: 1                           # WRR weight within the group
      max_tokens: 4096
      timeout: 120.0                      # overrides providers[].timeout_s and router_settings.timeout
      rpm: null                           # optional per-deployment rate cap
      tpm: null
```

| Field | Required | Notes |
|---|---|---|
| `model_name` | yes | Canonical group name clients request. |
| `wiwi_params.provider` | yes | The `name` of a `providers:` entry — an *account*, not a provider type. Startup fails if it does not resolve. |
| `wiwi_params.model` | yes | Provider-native model id, e.g. `gpt-4o` or `anthropic/claude-…`. |
| `wiwi_params.weight` | no | Default `1`. WRR weight within the group. Must be `>= 1`; a `0` starves the group's other deployments. |
| `wiwi_params.max_tokens` | no | Cap on `max_tokens` for this deployment. |
| `wiwi_params.rpm` / `tpm` | no | Per-deployment rate ceilings. |
| `wiwi_params.timeout` | no | Overrides both `providers[].timeout_s` and `router_settings.timeout`. |
| `wiwi_params.extra_headers` | no | Extra headers merged into the upstream request. |
| `wiwi_params.extra_body` | no | Extra JSON merged into the upstream body — e.g. OpenRouter's `provider: {only: [...]}` routing pin. |
| `wiwi_params.prompt_cache` | no | Opt-in Anthropic `cache_control` breakpoints on the stable prefix. |
| `wiwi_params.prompt_cache_min_tokens` | no | Minimum estimated prefix tokens before a breakpoint is added; `null` uses the adapter default (1024). |

To give a model a second name, use `router_settings.model_group_alias` — there is no `model_aliases` field on a model entry. Repeat a `model_name` with another `wiwi_params` block to add a deployment to a group.

## `providers`

**A list of provider *accounts*, not a map.** Each entry has a `name` (what `wiwi_params.provider` refers to) and a `provider` (one of the 11 `PROVIDER_TYPES`), plus a pool of `keys`.

```yaml
providers:
  - name: openai-main                     # account name; what model_list points at
    provider: openai                      # one of PROVIDER_TYPES
    base_url: https://api.openai.com/v1    # optional; this is the default
    timeout_s: 120                        # optional; falls back to router_settings.timeout
    round_robin: true                     # true = smooth weighted round-robin; false = label order
    extra_headers: {}                     # optional
    alias_id: null                        # optional caller-facing alias
    keys:                                 # at least one required
      - {label: main, key: os.environ/OPENAI_API_KEY, weight: 3}
      - {label: backup, key: os.environ/OPENAI_API_KEY_2, weight: 1}
```

There is no `api_key` field and no `budget_cap` field on a provider — keys live in the `keys` pool, and budgets belong to **virtual keys**, not providers.

| Field | Default | Notes |
|---|---|---|
| `name` | — | **Required.** Unique account name. |
| `provider` | — | **Required.** One of the 11 `PROVIDER_TYPES`. |
| `base_url` | per type | See the table below. |
| `timeout_s` | `None` | Falls back to `router_settings.timeout` when unset. |
| `round_robin` | `true` | `true` selects keys by smooth weighted round-robin; `false` takes the first available key in label order. |
| `extra_headers` | `{}` | Extra headers on every upstream request for this account. |
| `alias_id` | `None` | When set, clients can request a model by this alias. Providers sharing an alias pool into one cross-provider weighted round-robin. |
| `keys` | — | **Required**, at least one. Each has `label`, `key`, `weight` (`>= 1`), `enabled`. |

### Provider types and default base URLs

`provider:` must be one of the 11 entries in `PROVIDER_TYPES`. Defaults come from `BUILTIN_PROVIDER_TYPES` in `wiwi/router/router.py`.

| `provider` | Default `base_url` | Auth |
|---|---|---|
| `openai` | `https://api.openai.com/v1` | `Authorization: Bearer` |
| `anthropic` | `https://api.anthropic.com/v1` | `x-api-key` + `anthropic-version` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `?key=` querystring |
| `openai-compatible` | — (**required**) | `Authorization: Bearer` |
| `openrouter` | `https://openrouter.ai/api/v1` | `Authorization: Bearer` |
| `gmicloud` | `https://api.gmi-serving.com/v1` | `Authorization: Bearer` |
| `bai` | `https://api.b.ai/v1` | `Authorization: Bearer` |
| `nvidia-nim` | `https://integrate.api.nvidia.com/v1` | `Authorization: Bearer` |
| `cline` | `https://api.cline.bot/api/v1` | OAuth → `Bearer workos:<token>` |
| `workbuddy` | `https://copilot.tencent.com` | OAuth → access token |
| `opencode` | `https://opencode.ai/zen/v1` | per-wire (Bearer / `x-api-key` / `x-goog-api-key`) |

The `anthropic` default ends in `/v1` because the adapter appends only `/messages` — setting `https://api.anthropic.com` produces the invalid endpoint `api.anthropic.com/messages`.

```yaml
# A self-hosted OpenAI-format endpoint
providers:
  - name: my-ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1     # required for this type
    keys:
      - {label: local, key: "not-needed"}
```

**`nvidia-nim`** has JSON Schema quirks: it rejects boolean subschemas (`"additionalProperties": true`) and parameters named `type`. wiwi adapts tool schemas automatically via `nim_tool_schema.py` / `nim_native_tools.py`. See [PROVIDERS.md](PROVIDERS.md).

**`cline`** and **`workbuddy`** are OAuth, and there is **no** `client_id` / `client_secret` in config — `cline_oauth.py` states "no client_id / no PKCE" and `workbuddy_auth.py` reads no environment variables at all. Connect them through the admin UI or the OAuth endpoints, then the access/refresh token lands in the key pool. Tokens refresh on demand, which is what makes their requests survive a 401. See [PROVIDERS.md](PROVIDERS.md).

**`opencode`** routes per-model across four upstream protocols and refreshes its `opencode/<version>` User-Agent live.

### Provider key pool

A provider account holds multiple real API keys with weights (each `>= 1`). The router picks a key from the pool on each request, using smooth weighted round-robin when `round_robin: true`. Key states are `active`, `cooling` (after repeated failures), `invalid` (failed auth), `disabled`, and `probation` (healer-restored, reduced WRR weight until it graduates).

Keys are also managed at runtime through the admin API (`/admin/providers/{name}/keys`), which layers over whatever the config file declared.

## `os.environ/NAME` interpolation

Any string value in config can use `os.environ/NAME` to read from environment. Missing env vars resolve to empty string (doesn't crash). Providers with empty keys after interpolation are filtered out at startup.

```yaml
keys:
  - {label: main, key: os.environ/OPENAI_API_KEY}   # reads OPENAI_API_KEY env
base_url: os.environ/BASE_URL                        # reads BASE_URL env
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
| `WIWI_CONFIG` | Inline YAML for the whole config (not a file path — a bare path is parsed as YAML and rejected). Overrides the `wiwi.yaml` default; use `--config` for a file. |
| `WIWI_MASTER_KEY` | **Not read by code.** Works only because `wiwi.yaml.example` interpolates it as `master_key: os.environ/WIWI_MASTER_KEY`. |
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
- `RouterSettings` — routing strategy, retries, timeouts, cooldowns, fallbacks, streaming + journal knobs, Prometheus, cycle + failover policy
- `CacheSettings` — enabled, ttl_s, max_entries, bypass_header
- `HealerSettings` — enabled, tick_s, probe_timeout_s, max_probes_per_sweep, probation_weight, etc.
- `ProviderDef` — per-provider config (`base_url`, `timeout_s`, `extra_headers`, `round_robin`, `keys`, `alias_id`)
- `KeyDef` — provider key entry (`label`, `key`, `weight`, `enabled`)
- `DeploymentParams` — per-deployment overrides (`provider`, `model`, `weight`, `max_tokens`, `rpm`, `tpm`, `timeout`, `extra_headers`, `extra_body`, `prompt_cache`, …)
- `ModelEntry` — model_list entry (`model_name`, `wiwi_params`)

`PROVIDER_TYPES` is a module-level tuple in `config.py` listing all 11 provider types. It is the single source of truth — the router, admin API, and Pydantic schema all reference it.

## Validation at startup

Config fails fast at startup:
- Unknown provider types in `model_list` → error
- Provider referenced by `model_list` but not in `providers` → error
- `router_settings.prometheus_path` that is not a literal path starting with `/` → error
- Empty required fields → error

The import-time assert in `wiwi/providers/registry.py` checks that every `PROVIDER_TYPES` entry has a matching branch in `get_adapter()`. Adding a provider = new adapter + branch in registry.
