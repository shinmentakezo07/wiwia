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
| `WIWI_MASTER_KEY` | Admin API/UI access. **Required and already set.** The gateway refuses to start without it (`no session secret configured … Refusing to start with a default secret, which would allow forged admin sessions`), so a fresh Space reaches `RUNTIME_ERROR` until this secret exists. |
| `DATABASE_URL` | Already set by the deploy to SQLite under `/data`. Override with a Postgres URL to survive restarts. |
| `WIWI_STREAM_JOURNAL_DIR` | Already set by the image to `/data/journals`. Must be under `/data` (the only writable path in a Space container). Set it if you move `DATABASE_URL` elsewhere and want the stream journal to follow. An unwritable journal directory is reported at startup as `stream_journal_dir_unwritable` and costs SSE reconnect-resume, not availability. |
| `WIWI_TRUSTED_PROXIES` | Comma-separated proxy CIDRs, e.g. `10.0.0.0/8`. Set this to make wiwi trust the Space's ingress' `X-Forwarded-Proto`, which is what turns on the session cookie's `Secure` flag and `https://` OAuth callback URLs behind the Space's TLS terminator. Unset means no forwarded header is trusted (the safe default), so sessions still work — the cookie is just not marked `Secure`. No master key needs to be published to set it. |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, … | Provider keys. Providers whose key is unset are silently filtered out at config load. |

A Space with no provider key still boots and serves `/health`, the admin SPA and
`/v1/models` — the model list is simply empty, since a model only appears once a
provider can serve it.

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
