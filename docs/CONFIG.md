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
  master_key: sk-wiwi-master-test        # required: admin auth key
  session_secret:                        # optional: session signing key (derived from master_key if unset)
  host: 0.0.0.0                          # default: 0.0.0.0
  port: 4000                             # default: 4000
  log_level: info                        # default: info (structlog)
  db_url: sqlite+aiosqlite:///wiwi.db    # default: SQLite in repo root
  admin_ui_dir: wiwi/server/static       # where built SPA lives
  max_request_body_mb: 10               # default: 10
```

| Field | Default | Notes |
|---|---|---|
| `master_key` | — | Admin auth. Use `WIWI_MASTER_KEY` env in production instead of hardcoding. |
| `session_secret` | derived from master_key | 32-byte hex string for signing session cookies. Override for key rotation without master key change. |
| `host` | `0.0.0.0` | Bind address. |
| `port` | `4000` | Bind port. |
| `log_level` | `info` | structlog level: `debug`, `info`, `warning`, `error`. |
| `db_url` | `sqlite+aiosqlite:///wiwi.db` | SQLAlchemy URL. Postgres: `postgresql+asyncpg://...`. `DATABASE_URL` env overrides. |
| `admin_ui_dir` | `wiwi/server/static` | Path to built SPA. Set to empty to disable the `/admin/ui` mount. |
| `max_request_body_mb` | `10` | Max request body size in MB. Honored for both `Content-Length` and chunked/HTTP2 bodies. |

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
| `wiwi_params.weight` | no | Default `1`. WRR weight within the group. |
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

The key pool concept: a provider can have multiple real API keys with weights. The router picks a key from the pool on each request. Keys can be in states: `active`, `cooling` (cooldown after error), `invalid` (failed auth), `disabled`.

## `os.environ/NAME` interpolation

Any string value in config can use `os.environ/NAME` to read from environment. Missing env vars resolve to empty string (doesn't crash). Providers with empty keys after interpolation are filtered out at startup.

```yaml
api_key: os.environ/OPENAI_API_KEY     # reads OPENAI_API_KEY env
base_url: os.environ/BASE_URL          # reads BASE_URL env
```

This is the recommended way to keep secrets out of config files.

## `DATABASE_URL` env override

`DATABASE_URL` overrides `general_settings.db_url` regardless of config file content:

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host/db wiwi --config wiwi.yaml
```

## `.env` loading

`load_env()` (python-dotenv) loads `.env` from the CWD if it exists. Existing environment variables are never overwritten — `.env` only fills gaps. Provider keys, `DATABASE_URL`, `WIWI_MASTER_KEY`, `WIWI_CONFIG` all work from `.env`.

## Environment variables (runtime knobs)

| Variable | Effect |
|---|---|
| `WIWI_PORT` | Backend port (default 4000). Overrides config. |
| `WIWI_CONFIG` | Config file path. Overrides default `wiwi.yaml`. |
| `WIWI_MASTER_KEY` | Master key for admin auth. |
| `DATABASE_URL` | Overrides `general_settings.db_url`. |
| `WIWI_SESSION_SECRET` | Session signing key (32-byte hex). Defaults to SHA-256 of master key. |
| `WIWI_RELOAD` | Set to enable `--reload` mode. |
| `WIWI_RELOAD_DIRS` | Comma-separated dirs to watch in reload mode. |
| `WIWI_BIN` | Path to the wiwi binary (for start.sh). |

## config.py models

All config is parsed through Pydantic v2 models in `wiwi/config.py`:

- `WiwiConfig` — top-level aggregate
- `GeneralSettings` — host, port, master_key, db_url, etc.
- `RouterSettings` — health_model, ewma_alpha, window, adaptive_cooldown
- `CacheSettings` — enabled, ttl_s, max_entries, backend
- `HealerSettings` — enabled, probe_interval_s, etc.
- `ProviderDef` — per-provider config (base_url, timeout_s, budget_cap, keys)
- `ModelEntry` — model_list entry (model_name, wiwi_params, aliases)
- `ModelAliasEntry` — alias → model_name mapping
- `KeyDef` — virtual key definition
- `DeploymentParams` — per-deployment overrides

`PROVIDER_TYPES` is a module-level tuple in `config.py` listing all 11 provider types. It is the single source of truth — the router, admin API, and Pydantic schema all reference it.

## Validation at startup

Config fails fast at startup:
- Unknown provider types in `model_list` → error
- Provider referenced by `model_list` but not in `providers` → error
- Invalid URL fields → error
- Empty required fields → error

The import-time assert in `wiwi/providers/registry.py` checks that every `PROVIDER_TYPES` entry has a matching branch in `get_adapter()`. Adding a provider = new adapter + branch in registry.
