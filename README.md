# wiwi

Unified LLM gateway proxy — speak OpenAI Chat, OpenAI Responses (Codex), or Anthropic Messages (Claude Code) on the inbound side; route to OpenAI, Anthropic, Gemini, or any OpenAI-compatible endpoint on the outbound side. LiteLLM-style config with provider key pools (multiple API keys per provider, smooth weighted round-robin), retries, cooldowns, fallbacks, virtual keys, budgets, rate limits, spend tracking, and request logs.

## Quickstart

```bash
cp wiwi.yaml.example wiwi.yaml        # edit providers/keys
export OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-...
export WIWI_MASTER_KEY=sk-wiwi-master-mysecret
uv venv && uv pip install -e .
wiwi --config wiwi.yaml               # serves http://0.0.0.0:4000
```

Docker:

```bash
docker compose up --build
```

## Surfaces

| Endpoint | Dialect | Works with |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat | openai SDK, LangChain |
| `POST /v1/responses` | OpenAI Responses | Codex CLI (`base_url` → wiwi) |
| `POST /v1/messages` + `/v1/messages/count_tokens` | Anthropic Messages | Claude Code (`ANTHROPIC_BASE_URL` → wiwi), anthropic SDK |
| `GET /v1/models` | model list | all |

Any surface reaches any provider — a Claude Code session can be backed by GPT and vice versa; responses always come back in the caller's dialect.

## Admin

```bash
# create a virtual key (random or custom)
curl -X POST localhost:4000/admin/keys/generate \
  -H "Authorization: Bearer $WIWI_MASTER_KEY" \
  -d '{"name": "team-a", "max_budget": 10, "rpm": 60}'
# {"key":"sk-wiwi-...","id":"k...","note":"store this key now..."}

# usage logs (SSE live tail: /admin/stream)
curl localhost:4000/admin/logs/requests -H "Authorization: Bearer $WIWI_MASTER_KEY"
```

Per-request stats tracked: input / cached / reasoning / output tokens, TPS, TTFT, latency, cost, retry chain, which provider key served it.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Docs: `docs/ARCHITECTURE.md` (system design), `docs/CORE.md` (handlers + streaming flow), `docs/ADMIN.md` (key pools + UI plan), `docs/MVP.md` (scope + gap register), `docs/PLAN.md` (build phases), `docs/TECHSTACK.md` (choices).
