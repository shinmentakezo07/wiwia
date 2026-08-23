# wiwi

Unified LLM gateway proxy — speak **OpenAI Chat Completions**, **OpenAI Responses (Codex CLI)**, or **Anthropic Messages (Claude Code)** on the inbound side; route to **OpenAI, Anthropic, Gemini, OpenRouter, or any OpenAI-compatible endpoint** on the outbound side. LiteLLM-style `wiwi.yaml` config with provider key pools (multiple API keys per provider, smooth weighted round-robin), retries, cooldowns, fallbacks, virtual keys, budgets, RPM/TPM rate limits, spend tracking, request logs, and a built-in admin web UI.

Any surface reaches any provider — Claude Code can be backed by GPT, Codex by Gemini, and responses always come back in the caller's dialect.

Reasoning translates across dialects too: `reasoning_effort` (OpenAI), `thinking.budget_tokens` (Anthropic), and OpenRouter's unified `reasoning` object are all mapped through one canonical IR form (`low`/`medium`/`high`/… ↔ token budgets), and thinking blocks round-trip across providers so multi-turn conversations survive a mid-conversation model switch.

## How it works

Hub-and-spoke design: every direction goes `dialect → IR → provider`. No pairwise converters — adding an inbound surface is one module in `wiwi/wire/`, adding a provider is one adapter in `wiwi/providers/` plus a line in the registry. Core code never branches on dialect or provider name.

```
Client (openai SDK / Codex CLI / Claude Code)
   │  inbound dialect
   ▼
wiwi/wire/* ──► Canonical IR (wiwi/ir) ──► router (key pools, WRR, retries, cooldowns)
                                                │
                                                ▼
Client ◄── outbound dialect ◄── wire encoder ◄── providers/* (openai · anthropic · gemini · openrouter · openai-compatible)
```

## Project structure

| Path | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …`) |
| `wiwi/config.py` | YAML → pydantic models; env interpolation; fail-fast validation |
| `wiwi/ir/types.py` | Canonical IR: tagged parts, messages, tools, params, usage |
| `wiwi/wire/` | Inbound codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` |
| `wiwi/providers/` | Outbound adapters: `openai`, `anthropic`, `gemini`, `openrouter`, `openai-compatible` + `base.py` protocol |
| `wiwi/router/router.py` | Model groups, key pools (smooth WRR), cooldowns, retries, fallbacks |
| `wiwi/core/gateway.py` | Surface-agnostic execution engine, pricing, log events |
| `wiwi/core/context.py` | `RequestContext` — mutable holder threaded through the pipeline |
| `wiwi/streaming/` | `IRStreamDelta` taxonomy + SSE helpers |
| `wiwi/auth/` | Key generation/hashing + budget/spend service |
| `wiwi/cost/pricing.py` | Cost engine + token estimation fallback |
| `wiwi/ratelimit/` | RPM/TPM sliding-window limits |
| `wiwi/logging_core/` | Log events + SSE ring buffer for admin tail |
| `wiwi/server/app.py` | FastAPI factory: proxy routes, middleware, `/admin/*` |
| `web/` | Admin UI source (React 19 + TypeScript + Vite + Tailwind 4) |
| `tests/` | Pytest suite: unit (`respx` HTTP mocks), ASGI end-to-end, Hypothesis property round-trips |
| `bench.py` | Stress / latency / TPS tester for wiwi and any OpenAI-compatible proxy |
| `docs/` | Architecture and design specs |

## Requirements

- Python ≥ 3.11
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Docker (optional)
- Bun or Node (only for rebuilding the admin UI)

## Quickstart

```bash
# 1. config
cp wiwi.yaml.example wiwi.yaml        # then edit providers/keys/model_list

# 2. provider keys + admin key
export OPENAI_API_KEY=sk-... \
       ANTHROPIC_API_KEY=sk-ant-... \
       WIWI_MASTER_KEY=sk-wiwi-master-mysecret

# 3. install & run (Python 3.11+)
uv venv && uv pip install -e ".[dev]"
wiwi --config wiwi.yaml               # serves http://0.0.0.0:4000
# or: wiwi -c wiwi.yaml --host 0.0.0.0 --port 4000
```

Docker:

```bash
export WIWI_MASTER_KEY=sk-wiwi-master-mysecret
docker compose up --build

# optional postgres backend instead of sqlite
docker compose --profile pg up --build
```

Compose mounts `./wiwi.yaml` read-only and passes through `WIWI_MASTER_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

## All commands

### Install

```bash
uv venv && uv pip install -e .            # runtime only
uv venv && uv pip install -e ".[dev]"     # + pytest, pytest-asyncio, respx, asgi-lifespan, ruff
uv pip install -e ".[pg]"                 # optional: Postgres backend (asyncpg)
uv pip install -e ".[redis]"              # optional: Redis backend
```

### Run the gateway

```bash
wiwi --config wiwi.yaml                             # host/port from wiwi_settings
wiwi -c wiwi.yaml --host 0.0.0.0 --port 4000        # explicit overrides (-c = --config)
wiwi --reload --reload-dir wiwi                     # dev mode: restart on .py changes
```

On startup it prints the listen address plus the number of deployments and providers loaded.

`./start.sh` is a dev convenience wrapper: kills anything on the port, installs web deps, then runs `wiwi` with auto-reload (env knobs: `WIWI_PORT`, `WIWI_RELOAD`, `WIWI_RELOAD_DIRS`).

### Benchmark

```bash
.venv/bin/python bench.py                              # default sweep
.venv/bin/python bench.py -n 10 -c 1,4,16 --max-tokens 100
```

Measures TTFT, total latency, tokens, and output TPS per request; aggregates p50/p95, success rate, and throughput per concurrency level. Edit `TARGETS` / `MODEL` at the top of `bench.py` to point at your gateways.

## Surfaces

| Method | Endpoint | Dialect | Works with |
|---|---|---|---|
| POST | `/v1/chat/completions` | OpenAI Chat | openai SDK, LangChain, curl |
| POST | `/v1/responses` | OpenAI Responses | Codex CLI (`base_url` → wiwi) |
| POST | `/v1/messages`, `/v1/messages/count_tokens` | Anthropic Messages | Claude Code (`ANTHROPIC_BASE_URL` → wiwi), anthropic SDK |
| GET | `/v1/models` | model list | all |

Error bodies are dialect-correct per surface (OpenAI `{"error":{…}}` vs Anthropic `{"type":"error",…}`). Every response carries `x-wiwi-request-id` and `x-wiwi-latency-ms`; request bodies over `max_request_body_mb` (default 50) get a 413. Anthropic `cache_control` blocks pass through untouched, so Claude Code-style prompt caching works end-to-end and savings show up in stats.

## Connecting clients

**Claude Code** (backed by any provider):

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=sk-wiwi-...        # a virtual key
claude
```

**Codex CLI**:

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
codex --model gpt-4o
```

**openai SDK**:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:4000/v1", api_key="sk-wiwi-...")
```

## Configuration (`wiwi.yaml`)

Single LiteLLM-shaped file. **Any string value may be `os.environ/NAME`.**

```yaml
providers:              # named provider accounts, each with a pool of keyed entries
  - name: openai-main
    provider: openai    # openai | anthropic | gemini | openrouter | openai-compatible
    keys:
      - {label: main, key: os.environ/OPENAI_API_KEY, weight: 3}
      - {label: backup, key: os.environ/OPENAI_API_KEY_2, weight: 1}

  - name: openrouter          # OpenRouter gets unified reasoning translation
    provider: openrouter      #   (reasoning_effort/thinking_budget → reasoning{})
    base_url: https://openrouter.ai/api/v1
    keys: [{label: main, key: os.environ/OPENROUTER_API_KEY}]

  - name: local-ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    keys: [{label: local, key: "ollama"}]

model_list:             # model_name clients request → provider account + native model id
  - model_name: gpt-4o
    wiwi_params: {provider: openai-main, model: gpt-4o, weight: 2}
  - model_name: claude-sonnet
    # per-deployment overrides: max_tokens, rpm, tpm, timeout, extra_headers
    wiwi_params: {provider: anthropic-main, model: claude-sonnet-4-20250514, max_tokens: 8192}

router_settings:
  routing_strategy: simple-shuffle     # simple-shuffle | least-busy | latency-based
  num_retries: 2
  timeout: 120
  allowed_fails: 3
  cooldown_time: 30     # seconds a provider key cools down after failures
  # global_rpm: 600      # optional gateway-wide sliding-window caps
  # global_tpm: 200000
  fallbacks:
    claude-sonnet: ["gpt-4o"]
  # context_window_fallbacks:            # separate table for context-overflow errors
  #   long-task: ["claude-sonnet"]
  model_group_alias:
    gpt-4: gpt-4o

general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: sqlite+aiosqlite:///wiwi.db   # postgres via [pg] extra also works

wiwi_settings:
  drop_params: true     # silently drop params the target provider doesn't support
  port: 4000
```

## Admin

All `/admin/*` endpoints require the master key (`Authorization: Bearer …`).

### Web UI design system

The admin console is a **dark-only** single-page app (React 19 + TypeScript + Vite + Tailwind 4) built on a custom design system. The visual language deliberately echoes developer tools like the Dra console: near-black surfaces, hairline white borders, a blue primary accent with a violet/fuchsia secondary, tiny uppercase mono labels, and tabular numeric values. Source lives in `web/src/`; production build output lands in `wiwi/server/static/` and is served at `/admin/ui`.

#### Color & surface tokens

Defined as CSS custom properties under the `[data-admin]` scope in `web/src/styles.css`:

| Token | Value | Use |
|---|---|---|
| `--admin-bg` | `#050505` | App background (near-black) |
| `--admin-surface` | `#0a0a0a` | Cards, sidebar, tables |
| `--admin-surface-elevated` | `#0e0e0e` | Dialogs, dropdowns |
| `--admin-border` | `rgba(255,255,255,0.04)` | Hairline borders |
| `--admin-border-hover` | `rgba(255,255,255,0.08)` | Hover border state |
| `--admin-accent` | `#3b82f6` | Primary blue (links, active nav, focus rings) |
| `--admin-accent-purple` | `#a855f7` | Purple secondary |
| `--admin-accent-violet` | `#7c3aed` | Violet tertiary |
| `--admin-success` / `warning` / `danger` | `#34d399` / `#fbbf24` / `#f87171` | Status semantics |

Brand accent (the login page and logo gradient): the `brand-*` Tailwind ramp in `@theme` — an indigo→violet "iris" ramp from `#f3f1ff` (50) to `#291560` (950), with `#8757f7` (500) as the primary brand.

#### Layout shell (`components/Layout.tsx`)

- **Fixed sidebar** (260px, collapses to 72px) on `#0a0a0a`, grouped into four sections: Overview, Traffic, Configuration, Admin. Active item gets a blue left-edge bar + `blue-500/[0.06]` tint; inactive items are `text-muted` with `hover:bg-white/[0.02]`.
- **Blurred topbar** (`backdrop-filter: blur(12px)` on `rgba(5,5,5,0.75)`) showing page section + title, a live/offline SSE pulse badge, a mono tabular clock, and a Sign out button.
- **Ambient backdrop**: a fixed layer with a 64px grid at 2% opacity plus three radial color glows (blue top-left, violet bottom-right, purple center) for depth.
- Content scrolls inside `main.admin-scroll` (thin gradient scrollbar), capped at `max-w-[1400px]`, with a staggered fade-up entrance.

#### Component kit (`components/ui.tsx`)

Reusable primitives all built on the `admin-*` CSS classes:

- `Card` / `CardHeader` / `PageHeader` — `admin-card` surfaces with hairline borders, a gradient top-line on hover, and `admin-stat-highlight` for featured metrics.
- `Button` — variants: `primary` (blue-tinted soft fill), `ghost`, `danger`, `outline`.
- `Input` / `Select` / `Field` — `admin-input` with focus ring (`box-shadow: 0 0 0 3px rgba(99,102,241,0.08)`).
- `Toggle` — switch with blue glow when on.
- `Badge` — tones: green / red / amber / gray / blue / violet, all uppercase 10px with soft tinted backgrounds.
- `StatCard` — hero metric with gradient-text value (`admin-stat-value`), optional 12-point sparkline, delta chip (`↑/↓ N% vs prev hour`), and a `waiting` pulse state for zero-traffic.
- `Table` / `TD` — sticky-header tables with uppercase 10px headers and row hover.
- `Dialog` — portal modal with overlay fade + dialog lift+blur entrance, Escape to close, click-outside to dismiss.
- `CopyButton`, `Spinner`, `EmptyState`, `ErrorText`, `ProgressBar`.

#### Login page (`pages/Login.tsx`)

A standalone light/dark screen (the rest of the app is dark-only) centered on a glass card with:

- **Ambient backdrop**: a blueprint grid (`wiwi-grid`, 44px with a radial mask), two drifting aurora orbs (brand violet + fuchsia, 20s `wiwi-drift` loop), a film-grain noise layer, and a central radial bloom.
- **Premium glass card** (`wiwi-card-glow`): `backdrop-blur-xl` on `white/80` (light) / `zinc-900/70` (dark), with a layered brand-colored box-shadow and a gradient light-line across the top edge.
- **Signature diagram** (`GatewayDiagram`): an SVG of wiwi's real hub-and-spoke routes — three inbound dialects (chat, responses, messages) converge into the `w` node and fan out to three providers (openai, anthropic, gemini). Inbound paths use a violet gradient stroke with animated dashes (`wiwi-flow`); outbound paths use a fuchsia gradient. The hub has a radial glow halo that breathes (`wiwi-hub-pulse`, 3.2s). Endpoint dots pulse on staggered delays.
- **Form**: master-key input with key icon, show/hide toggle, mono font, focus ring. Submit button has a gradient fill and a shimmer sweep on hover (`wiwi-shimmer`). Errors trigger a shake animation (`wiwi-shake`).
- **Trust footer**: lock icon + "Key stays in this browser — checked once against your gateway."
- All motion is gated behind `@media (prefers-reduced-motion: no-preference)`.

#### Motion language

| Class | Effect | Duration |
|---|---|---|
| `admin-stagger` | Children fade-up with 60ms stagger | 0.5s each |
| `admin-pulse-dot` | Live badge dot scale+opacity pulse | 2s infinite |
| `admin-skeleton` | Shimmer placeholder | 1.8s infinite |
| `admin-waiting-pulse` | Zero-traffic stat value breathing | 2.4s infinite |
| `wiwi-enter` | Login card entrance (translateY + blur) | 0.55s |
| `wiwi-flow` | Login diagram dash flow | 1.5s linear infinite |
| `wiwi-aurora` / `wiwi-drift` | Login background orb drift | 20s infinite |
| `wiwi-hub-pulse` | Login hub glow breathing | 3.2s infinite |
| `wiwi-shimmer` | Login button hover sweep | 0.7s on hover |
| `wiwi-shake` | Login error shake | 0.3s |

All animations disable cleanly under `prefers-reduced-motion: reduce`.

### Web UI (running)

Built-in SPA at **`http://localhost:4000/admin/ui`** — login with the master key. Pages: Dashboard, Providers (+ per-provider detail: edit name/type/base_url, manage key pool, browse upstream models live, delete), Virtual Keys, Models (edit model groups and weights live, attach deployments), Request Logs, Proxy Logs, Usage, Analytics, Budgets & Alerts, Settings. Live updates via SSE.

```bash
# rebuild the UI from source (React 19 + TypeScript + Vite + Tailwind 4, built with bun)
cd web && bun install && bun run build   # output → wiwi/server/static/
bun run dev                              # dev server, proxies to a running gateway
```

### API

```bash
MK="Authorization: Bearer $WIWI_MASTER_KEY"

# virtual keys — create / list / update / disable / delete
curl -X POST localhost:4000/admin/keys/generate -H "$MK" \
  -d '{"name": "team-a", "max_budget": 10, "rpm": 60, "tpm": 100000,
       "models": ["gpt-4o"], "ttl_seconds": 86400}'
# → {"key":"sk-wiwi-...","id":"k...","note":"store this key now..."}
# (optional "custom_key": supply your own plaintext, >=16 chars)

curl localhost:4000/admin/keys -H "$MK"
curl -X PATCH localhost:4000/admin/keys/<id> -H "$MK" -d '{"max_budget": 20}'
curl -X POST localhost:4000/admin/keys/<id>/disable -H "$MK"
curl -X DELETE localhost:4000/admin/keys/<id> -H "$MK"

# providers & key pools — create / edit / delete accounts, manage per-label keys
curl -X POST localhost:4000/admin/providers -H "$MK" \
  -d '{"name": "openai-backup", "provider_type": "openai",
       "base_url": "https://api.openai.com/v1", "key": "os.environ/BACKUP_KEY"}'
curl -X PATCH localhost:4000/admin/providers/<name> -H "$MK" \
  -d '{"name": "openai-primary"}'                  # rename / re-type / base_url
curl -X DELETE localhost:4000/admin/providers/<name> -H "$MK"   # blocked while model groups reference it
curl localhost:4000/admin/providers -H "$MK"                    # pool status incl. health + cooldowns

curl -X POST localhost:4000/admin/providers/<name>/keys -H "$MK" \
  -d '{"label": "extra", "key": "os.environ/EXTRA_KEY", "weight": 2}'
curl -X PATCH localhost:4000/admin/providers/<name>/keys/<label> -H "$MK" \
  -d '{"disabled": true, "weight": 5}'             # also reset_status: true to clear cooldown
curl -X DELETE localhost:4000/admin/providers/<name>/keys/<label> -H "$MK"

# live upstream model ids for a provider (first available key)
curl localhost:4000/admin/providers/<name>/models -H "$MK"

# model groups — edit routing/weights live, attach deployments
curl -X PATCH localhost:4000/admin/model-groups/<name> -H "$MK" \
  -d '{"weights": {"openai-main/gpt-4o": 3}, "strategy": "least-busy"}'
curl -X POST localhost:4000/admin/model-groups/<name>/deployments -H "$MK" \
  -d '{"group": "gpt-4o", "provider": "openrouter", "model_id": "openai/gpt-4o", "weight": 1}'

# logs & stats
curl localhost:4000/admin/logs/requests -H "$MK"     # per-request logs
curl localhost:4000/admin/logs/proxy -H "$MK"        # proxy-level logs
curl localhost:4000/admin/stats/overview -H "$MK"    # p95 latency, cost, tokens
curl "localhost:4000/admin/stats/timeseries?bucket=minute&metric=cost&minutes=60" -H "$MK"
curl localhost:4000/admin/stream -H "$MK"            # SSE live tail
curl localhost:4000/admin/alert-rules -H "$MK"       # GET / PUT alert rules

# misc
curl localhost:4000/health
curl localhost:4000/v1/models -H "Authorization: Bearer sk-wiwi-..."
```

Route map:

| Route | Purpose |
|---|---|
| `POST /admin/keys/generate` | mint a virtual key (budget/RPM/TPM limits, model allowlist, TTL, optional `custom_key`) |
| `GET /admin/keys` | list virtual keys |
| `PATCH /admin/keys/{id}` | update limits/models/expiry live (cache evicted immediately) |
| `POST /admin/keys/{id}/disable` | disable/enable a key |
| `DELETE /admin/keys/{id}` | revoke a key |
| `GET /admin/providers` | provider + key-pool status (health, cooldowns, req/err counts) |
| `POST /admin/providers` | add a provider account at runtime |
| `PATCH /admin/providers/{name}` | rename / re-type / change base_url |
| `DELETE /admin/providers/{name}` | remove a provider (409 while model groups still reference it) |
| `POST /admin/providers/{name}/keys` | add a key to a pool |
| `PATCH /admin/providers/{name}/keys/{label}` | patch weight/enabled, or reset cooldown status |
| `DELETE /admin/providers/{name}/keys/{label}` | remove a pool key |
| `GET /admin/providers/{name}/models` | list upstream model ids live (first available key) |
| `GET /admin/models` · `PATCH /admin/model-groups/{name}` | inspect / edit routing + weights live |
| `POST /admin/model-groups/{name}/deployments` | attach a deployment to a group (creates the group) |
| `GET /admin/logs/requests` · `GET /admin/logs/proxy` | request logs (DB-backed) + proxy logs (ring buffer) |
| `GET /admin/stream` | SSE live tail of log events (`Last-Event-ID` replay, keepalive pings) |
| `GET /admin/stats/overview` · `GET /admin/stats/timeseries` | aggregate + time-bucketed stats |
| `GET / PUT /admin/alert-rules` | spend/error alert rules (storage; evaluation engine is post-MVP) |

Per-request stats tracked: input / cached / reasoning / output tokens, TPS, TTFT, latency, cost, cache hit + cache savings, retry chain (per-attempt deployment/provider/key/status), and which provider key served it.

Every admin mutation writes an audit event (`actor`/`action`/`target`/`diff`) to an `audit_logs` table — key lifecycle, provider/pool edits, routing changes.

## Tests & lint

```bash
.venv/bin/python -m pytest tests/ -q                          # all tests (219, ~18s)
.venv/bin/python -m pytest tests/test_codecs.py -q            # single file
.venv/bin/python -m pytest tests/test_router.py -k cooldown   # single test by name

.venv/bin/ruff check wiwi/ tests/                             # lint (line-length 100)
```

The suite mixes unit tests (`respx` HTTP mocks), ASGI end-to-end tests through the full app, and Hypothesis property-based round-trip tests over the dialect ↔ IR codecs.

## Docs

- `UPDATE.md` — changelog for cross-provider translation fixes (read first for reasoning/thinking, tool_result, OpenRouter issues)
- `docs/ARCHITECTURE.md` — system design
- `docs/CORE.md` — handlers + streaming flow
- `docs/ADMIN.md` — admin UI/API design
- `docs/MVP.md` — scope + gap register
- `docs/PLAN.md` — build phases
- `docs/TECHSTACK.md` — technology choices
- `docs/STREAMING_PERFORMANCE_RECOVERY.md` — streaming/tool-call/recovery improvement report (proposal)

Docs intentionally run ahead of the implementation in places (handler pipeline, DeltaBus, DB schema, Postgres/Redis backends are specified but not yet built). When docs and code disagree, trust the code — or treat the doc section as the spec for work in progress.

## Guardrails

- **Never commit `wiwi.yaml` or `wiwi.db`** — they hold live provider keys and runtime state (both gitignored). Provider keys come from env via `os.environ/NAME`; master key via `WIWI_MASTER_KEY`.
- Admin endpoints (`/admin/*`) require the master key; client traffic authenticates with virtual keys (`sk-wiwi-…`).
- Virtual keys are SHA-256-hashed in storage; plaintext is returned only once at generation time.
