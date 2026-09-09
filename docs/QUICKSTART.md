# wiwi — Quick Start

Self-hosted unified LLM gateway proxy. Three inbound dialects (OpenAI Chat Completions, OpenAI Responses, Anthropic Messages) flow through one canonical IR out to any of eleven provider adapters and re-encode in the caller's dialect on the way back.

## Prerequisites

- Python 3.11+ (3.12 recommended)
- A `wiwi.yaml` config (copy `wiwi.yaml.example` and fill in your keys)
- Provider API keys for the models you want to proxy

## Install

```bash
# From the repo root
pip install -e .            # or: uv pip install -e .
# Optional: Redis rate limiter
pip install -e .[redis]
```

## Run

```bash
# Production mode (loads config file → uvicorn)
wiwi --config wiwi.yaml

# Dev mode with reload
wiwi --reload

# Custom port/host
wiwi --config wiwi.yaml --port 4000 --host 0.0.0.0

# Or directly via uvicorn
uvicorn wiwi.server.app:create_app_from_config_path --factory --port 4000
```

Default port is 4000. Health check at `http://localhost:4000/health`.

## Minimal config

```yaml
# wiwi.yaml
general_settings:
  master_key: sk-wiwi-master-test    # set WIWI_MASTER_KEY env in production

model_list:
  - model_name: gpt-4o
    wiwi_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

providers:
  openai:
    api_key: os.environ/OPENAI_API_KEY
    base_url: https://api.openai.com/v1
```

Values can use `os.environ/NAME` interpolation — missing env vars resolve to empty string (providers with empty keys are filtered out).

Config precedence: `--config` flag > `WIWI_CONFIG` env > `wiwi.yaml`.

## Test it

```bash
# Health check
curl http://localhost:4000/health

# List models
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-wiwi-master-test"

# Chat completion (non-streaming)
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-wiwi-master-test" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 10}'
```

Use a virtual key (not the master key) for client traffic. Generate one via the admin API:

```bash
curl http://localhost:4000/admin/keys/generate \
  -H "Authorization: Bearer sk-wiwi-master-test" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app-key", "models": ["gpt-4o"], "max_budget": 10.0}'
```

The response gives you a `sk-wiwi-...` key. Use it like any OpenAI key:

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-...
```

## Three API surfaces

| Endpoint | Dialect | Clients |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat | OpenAI SDK, LangChain, curl |
| `POST /v1/responses` | OpenAI Responses | Codex CLI, OpenAI Agents SDK |
| `POST /v1/messages` | Anthropic Messages | Claude Code, Anthropic SDK |
| `POST /v1/messages/count_tokens` | Anthropic token counting | Claude Code |
| `GET /v1/models` | OpenAI-style list | all |

All surfaces accept `Authorization: Bearer <key>`. `/v1/messages` also accepts `x-api-key` (Claude Code sends that).

## Admin UI

The built SPA is served at `http://localhost:4000/admin/ui`. Log in with the master key.

- Dashboard: live stats, token charts, spend
- Providers: manage provider accounts + key pools
- Virtual Keys: create/manage client keys
- Models: view model groups and deployments
- Request Logs: per-request details with TPS, TTFT, cost
- Usage: token analytics with range filters
- Analytics: deep-dive charts, exports

## Full stack (backend + Vite dev server)

```bash
# From repo root
WIWI_PORT=4000 WIWI_WEB_PORT=5173 ./start.sh
```

The Vite dev server proxies `/admin`, `/v1`, `/auth`, `/public`, `/health` to the backend. Edit `web/src/` and see live changes.

## Frontend build

```bash
cd web && bun install && bun run build   # → wiwi/server/static/
```

The built SPA is served from `wiwi/server/static/` at `/admin/ui`. `wiwi/server/static/` is gitignored — builds produce it locally.

## Database

Default: SQLite at `wiwi.db` (gitignored). Set `DATABASE_URL` env to use Postgres:

```
DATABASE_URL=postgresql+asyncpg://user:pass@host/db
```

Schema is created at startup via inline `CREATE TABLE IF NOT EXISTS` — no Alembic.

## Stream journals

Enabled by default (`stream_journal_enabled: true`). Encoded SSE frames persist per-request to `.wiwi/journals/` (gitignored). Clients reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replay even after a restart. Default TTL 600s, 1 MiB/journal cap.

## Environment knobs

- `WIWI_PORT` — backend port (default 4000)
- `WIWI_CONFIG` — config file path
- `WIWI_MASTER_KEY` — master key (admin auth)
- `DATABASE_URL` — overrides config DB URL
- `WIWI_SESSION_SECRET` — session signing key (derived from master key if unset)

## What's next

- [CONFIG.md](CONFIG.md) — full config reference
- [API_REFERENCE.md](API_REFERENCE.md) — all endpoints
- [ADMIN.md](ADMIN.md) — admin UI + API
- [PROVIDERS.md](PROVIDERS.md) — provider setup for all 11 types
- [DEVELOPMENT.md](DEVELOPMENT.md) — dev setup, conventions, testing
- [STREAMING.md](STREAMING.md) — streaming internals, failover and resume
- [ARCHITECTURE.md](ARCHITECTURE.md) — system architecture and request pipeline
