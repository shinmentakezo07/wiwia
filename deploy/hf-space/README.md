---
title: Yapapa
emoji: 🔥
colorFrom: pink
colorTo: gray
sdk: docker
app_port: 4000
pinned: false
---

# wiwi on HuggingFace Spaces

This Space runs the [wiwi](https://github.com/shinmentakezo07/wiwia) gateway —
a self-hosted unified LLM proxy. Three inbound dialects (OpenAI Chat, OpenAI
Responses, Anthropic Messages) route through one canonical IR to eleven
outbound provider types, and every response is re-encoded in the caller's
inbound dialect.

## Deploying

Do not edit this Space by hand. It is a deploy target, built from the repo:

```bash
# from a checkout of the wiwi repo
./deploy/hf_space.sh              # rsync the repo tree here and push
```

The script reads `HF_TOKEN` from the repo's gitignored `.env`, copies the
source tree into a scratch clone of this Space, and pushes a single
`Deploy wiwi <sha>` commit. It never force-pushes and never touches the
gateway's own git history.

## Configuration

Set these in **Settings → Variables and secrets** on this Space. Secrets are
injected as environment variables at runtime, which is exactly how
`wiwi.yaml.example` resolves provider keys (`os.environ/NAME`).

| Variable | Purpose |
|---|---|
| `WIWI_MASTER_KEY` | Admin API/UI access. **Required** — set it, or the admin surface is unusable. |
| `DATABASE_URL` | Pre-set by the deploy script to SQLite under `/data`. Override with a Postgres URL to survive restarts. |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, … | Provider keys. Providers whose key is unset are silently filtered out at config load. |

### Data persistence

The container's disk is wiped on every restart, and `/data` is the only
writable path. The deploy script therefore sets:

```
DATABASE_URL=sqlite+aiosqlite:////data/wiwi.db
```

That keeps virtual keys, budgets, deployments and request logs across a
restart of the *same* container, but **not** across a Space rebuild — the
filesystem is ephemeral. For durable state, set `DATABASE_URL` to an external
Postgres (Neon, Supabase, …) in the Space secrets and it takes precedence.

The admin SPA is baked into the image at build time by the repo's `Dockerfile`
(React 19 + Vite), so no separate frontend step is needed.

## Build

The Space builds the repo's root `Dockerfile` — the same multi-stage image used
by `docker compose up --build`, plus a `mkdir -p /data` so the SQLite database
has somewhere writable to live. The Space listens on port `4000` (`app_port`
above); the container is told so explicitly rather than relying on the default.
