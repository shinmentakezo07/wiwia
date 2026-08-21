# wiwi

Unified LLM gateway proxy — speak **OpenAI Chat Completions**, **OpenAI Responses (Codex CLI)**, or **Anthropic Messages (Claude Code)** on the inbound side; route to **OpenAI, Anthropic, Gemini, or any OpenAI-compatible endpoint** on the outbound side. LiteLLM-style `wiwi.yaml` config with provider key pools (multiple API keys per provider, smooth weighted round-robin), retries, cooldowns, fallbacks, virtual keys, budgets, RPM/TPM rate limits, spend tracking, request logs, and a built-in admin web UI.

Any surface reaches any provider — Claude Code can be backed by GPT, Codex by Gemini, and responses always come back in the caller's dialect.

## How it works

Hub-and-spoke design: every direction goes `dialect → IR → provider`. No pairwise converters — adding an inbound surface is one module in `wiwi/wire/`, adding a provider is one adapter in `wiwi/providers/` plus a line in the registry. Core code never branches on dialect or provider name.

```
Client (openai SDK / Codex CLI / Claude Code)
   │  inbound dialect
   ▼
wiwi/wire/* ──► Canonical IR (wiwi/ir) ──► router (key pools, WRR, retries, cooldowns)
                                                │
                                                ▼
Client ◄── outbound dialect ◄── wire encoder ◄── providers/* (openai · anthropic · gemini · openai-compatible)
```

## Project structure

| Path | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …`) |
| `wiwi/config.py` | YAML → pydantic models; env interpolation; fail-fast validation |
| `wiwi/ir/types.py` | Canonical IR: tagged parts, messages, tools, params, usage |
| `wiwi/wire/` | Inbound codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` |
| `wiwi/providers/` | Outbound adapters: `openai`, `anthropic`, `gemini`, `openai-compatible` + `base.py` protocol |
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
| `tests/` | Pytest suite with `respx` HTTP mocks and ASGI end-to-end |
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
```

On startup it prints the listen address plus the number of deployments and providers loaded.

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

Error bodies are dialect-correct per surface (OpenAI `{"error":{…}}` vs Anthropic `{"type":"error",…}`).

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
    provider: openai    # openai | anthropic | gemini | openai-compatible
    keys:
      - {label: main, key: os.environ/OPENAI_API_KEY, weight: 3}
      - {label: backup, key: os.environ/OPENAI_API_KEY_2, weight: 1}

  - name: local-ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    keys: [{label: local, key: "ollama"}]

model_list:             # model_name clients request → provider account + native model id
  - model_name: gpt-4o
    wiwi_params: {provider: openai-main, model: gpt-4o, weight: 2}
  - model_name: claude-sonnet
    wiwi_params: {provider: anthropic-main, model: claude-sonnet-4-20250514, max_tokens: 8192}

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  timeout: 120
  allowed_fails: 3
  cooldown_time: 30     # seconds a provider key cools down after failures
  fallbacks:
    claude-sonnet: ["gpt-4o"]
  model_group_alias:
    gpt-4: gpt-4o

general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: sqlite+aiosqlite:///wiwi.db

wiwi_settings:
  drop_params: true     # silently drop params the target provider doesn't support
  port: 4000
```

## Admin

All `/admin/*` endpoints require the master key (`Authorization: Bearer …`).

### Web UI

Built-in SPA at **`http://localhost:4000/admin/ui`** — login with the master key. Pages: Dashboard, Providers (key pools, add/patch/disable keys), Virtual Keys, Models (edit model groups live), Request Logs, Proxy Logs, Usage, Analytics, Budgets & Alerts, Settings. Live updates via SSE.

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

curl localhost:4000/admin/keys -H "$MK"
curl -X PATCH localhost:4000/admin/keys/<id> -H "$MK" -d '{"max_budget": 20}'
curl -X POST localhost:4000/admin/keys/<id>/disable -H "$MK"
curl -X DELETE localhost:4000/admin/keys/<id> -H "$MK"

# provider key pools — add / patch per-label keys, add providers
curl -X POST localhost:4000/admin/providers/<name>/keys -H "$MK" \
  -d '{"label": "extra", "key": "os.environ/EXTRA_KEY", "weight": 2}'
curl -X PATCH localhost:4000/admin/providers/<name>/keys/<label> -H "$MK" \
  -d '{"disabled": true, "weight": 5}'
curl -X POST localhost:4000/admin/providers -H "$MK" \
  -d '{"name": "openai-backup", "provider": "openai", "keys": [...]}'

# model groups — edit routing/weights live
curl -X PATCH localhost:4000/admin/model-groups/<name> -H "$MK" -d '{...}'

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
| `POST /admin/keys/generate` | mint a virtual key (budget/RPM/TPM limits, model allowlist, TTL) |
| `GET /admin/keys` | list virtual keys |
| `PATCH /admin/keys/{id}` | update limits/metadata |
| `POST /admin/keys/{id}/disable` | disable/enable a key |
| `DELETE /admin/keys/{id}` | revoke a key |
| `GET /admin/providers` | provider + key-pool status |
| `POST /admin/providers` | add a provider at runtime |
| `POST /admin/providers/{name}/keys` | add a key to a pool |
| `PATCH /admin/providers/{name}/keys/{label}` | patch weight/disabled per label |
| `GET /admin/models` · `PATCH /admin/model-groups/{name}` | inspect / edit routing live |
| `GET /admin/logs/requests` · `GET /admin/logs/proxy` | request + proxy logs |
| `GET /admin/stream` | SSE live tail of log events |
| `GET /admin/stats/overview` · `GET /admin/stats/timeseries` | aggregate + time-bucketed stats |
| `GET / PUT /admin/alert-rules` | spend/error alert rules |

Per-request stats tracked: input / cached / reasoning / output tokens, TPS, TTFT, latency, cost, retry chain, and which provider key served it.

## Tests & lint

```bash
.venv/bin/python -m pytest tests/ -q                          # all tests (~8s)
.venv/bin/python -m pytest tests/test_codecs.py -q            # single file
.venv/bin/python -m pytest tests/test_router.py -k cooldown   # single test by name

.venv/bin/ruff check wiwi/ tests/                             # lint (line-length 100)
```

## Docs

- `docs/ARCHITECTURE.md` — system design
- `docs/CORE.md` — handlers + streaming flow
- `docs/ADMIN.md` — admin UI/API design
- `docs/MVP.md` — scope + gap register
- `docs/PLAN.md` — build phases
- `docs/TECHSTACK.md` — technology choices

Docs intentionally run ahead of the implementation in places (handler pipeline, DeltaBus, DB schema, Postgres/Redis backends are specified but not yet built). When docs and code disagree, trust the code — or treat the doc section as the spec for work in progress.

## Guardrails

- **Never commit `wiwi.yaml` or `wiwi.db`** — they hold live provider keys and runtime state (both gitignored). Provider keys come from env via `os.environ/NAME`; master key via `WIWI_MASTER_KEY`.
- Admin endpoints (`/admin/*`) require the master key; client traffic authenticates with virtual keys (`sk-wiwi-…`).
- Virtual keys are SHA-256-hashed in storage; plaintext is returned only once at generation time.
