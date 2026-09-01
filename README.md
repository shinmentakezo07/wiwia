<div align="center">

# 🌀 wiwi

### One gateway. Every dialect. Any provider.

**Speak OpenAI · OpenAI Responses (Codex CLI) · Anthropic Messages (Claude Code) on the inbound.**
**Route to OpenAI · Anthropic · Gemini · OpenRouter · NVIDIA NIM · Cline · B.AI · GMI · any OpenAI-compatible endpoint.**

<p>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white&style=for-the-badge">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white&style=for-the-badge">
  <img alt="React 19" src="https://img.shields.io/badge/React-19-149eca?logo=react&logoColor=white&style=for-the-badge">
  <img alt="Vite" src="https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white&style=for-the-badge">
</p>

<p>
  <img alt="Tests" src="https://img.shields.io/badge/tests-979%20collected-34d399?style=for-the-badge">
  <img alt="Lint" src="https://img.shields.io/badge/lint-ruff-9CA3AF?logo=ruff&logoColor=white&style=for-the-badge">
  <img alt="Built with uv" src="https://img.shields.io/badge/built%20with-uv-7C3AED?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge">
</p>

</div>

---

```
   OpenAI Chat ─┐
 OpenAI Resp. ──┤    ┌── Canonical IR ── router ──┬── openai
Anthropic Msg. ─┘    │     (wiwi/ir)      (WRR,  ├── anthropic
        ▲            │                    retries,├── gemini
        │            ▼                    cooldown├── openrouter
   Dialect-correct   Pricing · Logging · Coalesce ├── nvidia-nim
   responses back    · Rate limits · Auth         ├── cline (OAuth)
   to the caller     · Streaming IR deltas        ├── workbuddy
                                                     ├── gmicloud
                                                     ├── bai
                                                     └── openai-compatible
```

## Contents

- [✨ Why wiwi?](#-why-wiwi)
- [🚀 Features](#-features)
- [🧠 How it works](#-how-it-works)
- [📥 Surfaces (inbound)](#-surfaces-inbound)
- [📤 Providers (outbound)](#-providers-outbound)
- [⚡ Quickstart](#-quickstart)
- [🧰 All commands](#-all-commands)
- [🔌 Connecting clients](#-connecting-clients)
- [⚙️ Configuration (`wiwi.yaml`)](#️-configuration-wiwiyaml)
- [🗂️ Project structure](#️-project-structure)
- [🛠️ Admin](#-admin)
- [🧪 Tests & lint](#-tests--lint)
- [📚 Docs](#-docs)
- [🛡️ Guardrails](#-guardrails)

## ✨ Why wiwi?

> LiteLLM gives you routing. wiwi gives you **routing + a live control plane**.

| 😩 Problem | 💡 wiwi's answer |
|---|---|
| One client dialect, many models behind it | **Hub-and-spoke** translation: any of 3 inbound dialects ↔ any of 10 outbound providers, no pairwise converters. |
| Rate-limit pain across many keys | **Smooth weighted round-robin** key pools with per-key cooldowns, retries, fallbacks, and a dedicated cooldown reset endpoint. |
| Reasoning parameters don't line up | Canonical IR: `reasoning_effort`, `thinking.budget_tokens`, and OpenRouter's `reasoning{}` all collapse to one `low / medium / high / N tokens` form. Thinking blocks round-trip across providers — multi-turn survives a mid-conversation model switch. |
| Want a UI, not just a YAML | Built-in dark admin SPA — keys, providers, key pools, model groups, request logs, live SSE tail, per-request stats (TTFT, TPS, cost, cache savings, retry chain). |
| Mutating config means a restart | Live `/admin/*` mutations: edit routing weights, disable keys, reset cooldowns, attach deployments, all without dropping traffic. Every mutation writes an `audit_logs` event. |
| Costs and budgets | Virtual keys with budget / RPM / TPM / model allowlist / TTL. Per-request cost + token breakdown, with aggregate + timeseries stats. |

## 🚀 Features

<table>
<tr>
<td width="50%" valign="top">

### 🔀 Translation
- 3 inbound dialects × 10 outbound providers, with `drop_params` and `extra_headers` for the awkward edges.
- OpenRouter unified `reasoning{}` translation for `low`/`medium`/`high`/explicit token budgets.
- Anthropic `cache_control` blocks pass through untouched; cache hits and savings appear in stats.
- Tool/function-call translation across dialects (round-trips in `UPDATE.md`).
- Count-tokens endpoint for Anthropic Messages.

### 🧭 Routing & resilience
- Key pools with **smooth weighted round-robin** (per-key `weight`, `enabled`).
- Cooldowns on failure, `allowed_fails` threshold, configurable `cooldown_time`.
- Retries with fallbacks (`fallbacks:` table) and a separate `context_window_fallbacks:` table for overflow errors.
- Strategies: `simple-shuffle`, `least-busy`, `latency-based`.
- Aliases: `model_group_alias` rewrites legacy names.
- Cycle-N rotation: re-tries on the same provider are tracked and excluded after N attempts.

</td>
<td width="50%" valign="top">

### 🔐 Auth, cost, limits
- Virtual keys (`sk-wiwi-…`), SHA-256-hashed at rest, plaintext shown once at mint time. Optional `custom_key` (≥16 chars).
- Per-key: `max_budget`, `rpm`, `tpm`, model allowlist, TTL, enable/disable.
- Per-deployment: `rpm`, `tpm`, `timeout`, `extra_headers`, `max_tokens`.
- Optional global `global_rpm` / `global_tpm` sliding-window caps.
- Cost engine with token estimation fallback.

### 📊 Observability
- Per-request DB log: input / cached / reasoning / output tokens, TTFT, latency, TPS, cost, cache hit + savings, full retry chain, which key served it.
- Proxy-level ring-buffer log.
- `/admin/stream` SSE live tail with `Last-Event-ID` replay and keepalive pings.
- `/admin/stats/overview` + `/admin/stats/timeseries?bucket=…&metric=…`.
- Spend / error alert rules (storage; evaluation engine is post-MVP).

### 🛠️ Admin & operations
- Dark SPA at `/admin/ui`: **Dashboard**, **Providers** (+ **ProviderDetail** with key pool + live upstream model list), **BuiltinProviders**, **OAuthProviders** (Cline), **WorkBuddyAccounts**, **Virtual Keys**, **Models** / **ModelsCatalog**, **Request Logs**, **Proxy Logs**, **Usage**, **Analytics**, **Budgets & Alerts**, **Settings**, **Users**, **Playground**, **Onboarding** — all live-updated via SSE.
- Master-key-gated REST API at `/admin/*`.
- Audit trail for every mutation.
- SQLite by default; Postgres via `[pg]` extra (`DATABASE_URL=postgresql+asyncpg://…`).
- Optional Redis backend for rate limits via `[redis]` extra.
- `--reload` dev mode and `start.sh` wrapper (backend + Vite together).

</td>
</tr>
</table>

## 🧠 How it works

Hub-and-spoke design. Every direction goes `dialect → IR → provider`. Core code never branches on dialect or provider name — adding a new surface is one module in `wiwi/wire/`, adding a new provider is one adapter in `wiwi/providers/` plus a line in the registry.

```
Client (openai SDK / Codex CLI / Claude Code)
   │  inbound dialect
   ▼
wiwi/wire/* ──► Canonical IR (wiwi/ir) ──► router (key pools, WRR, retries, cooldowns)
                                                │
                                                ▼
Client ◄── outbound dialect ◄── wire encoder ◄── providers/* (openai · anthropic · gemini · openrouter · nvidia-nim · bai · cline · workbuddy · gmicloud · openai-compatible)
```

The request lifecycle lives in `wiwi/server/app.py:run_chat_like` — decode → auth → rate limit → router retries/fallbacks → gateway complete/stream — and back out through the wire encoders. `wiwi/core/context.py:RequestContext` is the single mutable holder threaded through all of it.

### 🌊 Streaming pipeline

Everything that touches a stream lives in `wiwi/streaming/` and is intentionally small. The contract between adapters and encoders is a single tagged union (`IRStreamDelta` in `deltas.py`); the surrounding modules are deterministic transformations on top of it:

| Module | Responsibility |
|---|---|
| `deltas.py` | The `IRStreamDelta` taxonomy — `StreamStart → ToolCallOpen/ArgsDelta*/Close → UsageFinal → Finish → StreamEnd\|StreamError`. Adapters guarantee legality; encoders never defend against malformed sequences. |
| `coalesce.py` | `DeltaCoalescer` — merges consecutive `TextDelta`s under backpressure (queue depth > 100, 8 KiB or 50 ms), bypassing entirely for fast consumers. Never coalesces across control deltas (`ToolCallOpen/Close`, `ThinkingDelta`, `UsageFinal`, `Finish`, `StreamEnd`, `StreamError`). |
| `loopdetect.py` | O(1)-per-token repetition detector. Tracks periods 1..8 simultaneously; aborts the stream when the window becomes periodic. Periods above 8 are deliberately uncovered (genuine period-40 loops aren't the failure mode; the quadratic scan is what was eating the hot path). |
| `resume.py` | `StreamTape` — 256 KiB ring of content-bearing deltas with monotonic event ids. Two roles: mid-stream failover (Anthropic capture-and-resume) and `Last-Event-ID` replay for reconnecting SSE clients. |
| `partial_json.py` | Vercel-AI-SDK-style incremental JSON parser for streaming tool-call args. Auto-repairs truncated JSON at close (appends missing `"`/`]`/`}`), never raises on malformed input. |
| `validation.py` | Tool-call args validated against the declared JSON schema on `ToolCallClose`. Caps payloads at 1 MiB; **never logs raw args** (only length + first 16 hex of SHA-256 fingerprint) so tool payloads containing user secrets don't leak via structlog. |
| `sse.py` | SSE framing helpers used by the wire encoders. |

## 📥 Surfaces (inbound)

| Method | Endpoint | Dialect | Works with |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | 🟢 **OpenAI Chat** | openai SDK, LangChain, curl |
| `POST` | `/v1/responses` | 🟣 **OpenAI Responses** | Codex CLI (`base_url` → wiwi) |
| `POST` | `/v1/messages` · `/v1/messages/count_tokens` | 🟠 **Anthropic Messages** | Claude Code (`ANTHROPIC_BASE_URL` → wiwi), anthropic SDK |
| `GET` | `/v1/models` | 📋 model list | all |

> 🪄 **Response shape always matches the inbound dialect.** OpenAI clients see OpenAI error envelopes (`{"error":{…}}`); Anthropic clients see Anthropic error envelopes (`{"type":"error",…}`). Every response carries `x-wiwi-request-id` and `x-wiwi-latency-ms`; request bodies over `max_request_body_mb` (default 50) get a 413.

### 🔁 Dialect × Provider translation matrix

Any inbound dialect works with any outbound provider. The IR handles translation — no pairwise converters.

| ↓ In  ╲  Out → | OpenAI | Anthropic | Gemini | OpenRouter | NVIDIA NIM | Cline (OAuth) | B.AI | GMI | WorkBuddy | OpenAI-compat |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 🟢 **OpenAI Chat**       | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🟣 **OpenAI Responses**  | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🟠 **Anthropic Messages**| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## 📤 Providers (outbound)

| Adapter | Native dialect | Notes |
|---|---|---|
| `openai` | OpenAI Chat / Responses | `base_url` configurable |
| `anthropic` | Anthropic Messages | thinking + cache_control pass through |
| `gemini` | Gemini (OpenAI-compat) | via configurable `base_url` |
| `openrouter` | OpenAI-compat | unified `reasoning{}` translation |
| `nvidia-nim` | NVIDIA NIM (OpenAI-compat) | native tool-schema adapter; see `wiwi/providers/nim_*.py` |
| `cline` | Cline (OAuth) | on-demand OAuth refresh, cross-account WRR, global default model |
| `gmicloud` | OpenAI-compat | GMI serving endpoint |
| `bai` | OpenAI-compat | B.AI unified gateway (api.b.ai); one key across Chat/Responses/Messages protocols |
| `workbuddy` | OpenAI-compat (stream-only) | WorkBuddy / CodeBuddy (Tencent); nested-JSON auth, business errors ride HTTP 200 in `{code,msg,data}` envelopes |
| `openai-compatible` | OpenAI-compat | any URL (Ollama, vLLM, LM Studio, …) |

> 🔐 Provider keys enter as `os.environ/NAME` in YAML; **nothing is committed to the repo**.

## ⚡ Quickstart

### 📋 Requirements

- 🐍 **Python ≥ 3.11**
- 📦 **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- 🐳 **Docker** (optional, for containerized)
- 🥟 **Bun or Node** (only for rebuilding the admin UI)

### 🚀 Install & run locally

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

### 🐳 Or run with Docker

```bash
export WIWI_MASTER_KEY=sk-wiwi-master-mysecret
docker compose up --build

# optional postgres backend instead of sqlite
docker compose --profile pg up --build
```

Compose mounts `./wiwi.yaml` read-only and passes through `WIWI_MASTER_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

### 🔧 Config-loading precedence

The CLI is explicit about where config comes from (order matters):

1. `--config` / `-c` flag on the CLI
2. `WIWI_CONFIG` env var (raw YAML, useful in containers)
3. `wiwi.yaml` in the working directory

A `.env` in the cwd is loaded **before** any of the above, so `WIWI_MASTER_KEY`, `DATABASE_URL`, provider keys, and `WIWI_CONFIG` are all resolved by the time YAML parsing begins. Provider keys may also be referenced as `os.environ/NAME` directly in the YAML — interpolation happens recursively, and missing env vars resolve to empty strings (so the example config loads cleanly in a fresh container; empty-key entries are filtered out by validation).

### 🎯 Three ways to connect a client

| Client | Set this | Then run |
|---|---|---|
| 🤖 **Claude Code** | `ANTHROPIC_BASE_URL=http://localhost:4000`<br>`ANTHROPIC_AUTH_TOKEN=sk-wiwi-...` | `claude` |
| ⌨️ **Codex CLI** | `OPENAI_BASE_URL=http://localhost:4000/v1` | `codex --model gpt-4o` |
| 🐍 **openai SDK** | `base_url="http://localhost:4000/v1"`<br>`api_key="sk-wiwi-..."` | `client.chat.completions.create(...)` |
| 🌐 **curl** | `Authorization: Bearer sk-wiwi-...` | `curl localhost:4000/v1/models` |

> 💡 **Tip:** every endpoint, every client — same `wiwi.yaml`, same admin console, same live SSE tail.

## 🧰 All commands

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

## 🔌 Connecting clients

**🤖 Claude Code** (backed by any provider):

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=sk-wiwi-...        # a virtual key
claude
```

**⌨️ Codex CLI**:

```bash
export OPENAI_BASE_URL=http://localhost:4000/v1
codex --model gpt-4o
```

**🐍 openai SDK**:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:4000/v1", api_key="sk-wiwi-...")
```

## ⚙️ Configuration (`wiwi.yaml`)

Single LiteLLM-shaped file. **Any string value may be `os.environ/NAME`.**

```yaml
providers:              # named provider accounts, each with a pool of keyed entries
  - name: openai-main
    provider: openai    # openai | anthropic | gemini | openai-compatible | openrouter | gmicloud | bai | nvidia-nim
    keys:
      - {label: main, key: os.environ/OPENAI_API_KEY, weight: 3}
      - {label: backup, key: os.environ/OPENAI_API_KEY_2, weight: 1}

  - name: openrouter          # OpenRouter gets unified reasoning translation
    provider: openrouter      #   (reasoning_effort/thinking_budget → reasoning{})
    base_url: https://openrouter.ai/api/v1
    keys: [{label: main, key: os.environ/OPENROUTER_API_KEY}]

  - name: nvidia-nim
    provider: nvidia-nim
    base_url: https://integrate.api.nvidia.com/v1
    keys: [{label: main, key: os.environ/NVIDIA_NIM_API_KEY}]

  - name: local-ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    keys: [{label: local, key: "ollama"}]

  # Cline (OAuth) is not declared here — accounts are added at runtime through
  # the admin UI or `POST /admin/cline/oauth/login-url` + `/connect` flow.
  # See `wiwi.yaml.example` for the full reference.

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

## 🗂️ Project structure

| Path | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …`) |
| `wiwi/config.py` | YAML → pydantic models; env interpolation; fail-fast validation |
| `wiwi/ir/types.py` | Canonical IR: tagged parts, messages, tools, params, usage |
| `wiwi/wire/` | Inbound codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` |
| `wiwi/providers/` | Outbound adapters: `openai`, `anthropic`, `gemini`, `openrouter`, `nvidia-nim` (+ `nim_tool_schema.py`, `nim_native_tools.py`), `cline` (+ `cline_oauth.py`, `cline_auto_refresh.py`), `workbuddy` (+ `workbuddy_auth.py`, `workbuddy_auto_refresh.py`), `gmicloud`, `bai`, `openai-compatible`; `base.py` protocol; `registry.py` |
| `wiwi/router/router.py` | Model groups, key pools (smooth WRR), cooldowns, retries, fallbacks |
| `wiwi/core/gateway.py` | Surface-agnostic execution engine, pricing, log events |
| `wiwi/core/context.py` | `RequestContext` — mutable holder threaded through the pipeline |
| `wiwi/streaming/` | `deltas.py` (IRStreamDelta contract) · `coalesce.py` (backpressure text merging) · `loopdetect.py` (O(1)/token repetition detector, periods 1..8) · `resume.py` (StreamTape ring buffer, 256 KiB) · `partial_json.py` (incremental + auto-repair) · `validation.py` (tool-arg schema check, 1 MiB cap, no PII in logs) · `sse.py` |
| `wiwi/auth/` | Virtual key generation/hashing + budget/spend service + user accounts |
| `wiwi/cost/pricing.py` | Cost engine + token estimation fallback |
| `wiwi/ratelimit/` | RPM/TPM sliding-window limits (memory + redis backends) |
| `wiwi/logging_core/` | Structured log events + DB sink + SSE ring buffer for admin tail |
| `wiwi/server/app.py` | FastAPI factory: proxy routes, middleware, `/admin/*` |
| `web/` | Admin UI source (React 19 + TypeScript + Vite + Tailwind 4, built with **bun**) |
| `tests/` | Pytest suite — unit (`respx` HTTP mocks), ASGI end-to-end, Hypothesis round-trips. Bugfix regressions live in `test_fix_roundN.py` (latest: `test_fix_round9.py`). |
| `bench.py` | Stress / latency / TPS tester for wiwi and any OpenAI-compatible proxy |
| `docs/` | Architecture and design specs (`ARCHITECTURE`, `CORE`, `MVP`, `PLAN`, `ADMIN`, `TECHSTACK`, `STREAMING_PERFORMANCE_RECOVERY`) |

## 🛠️ Admin

All `/admin/*` endpoints require the master key (`Authorization: Bearer …`).

### 🎨 Web UI design system

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
- **Signature diagram** (`GatewayDiagram`): an SVG of wiwi's real hub-and-spoke routes — three inbound dialects (chat, responses, messages) converge into the `w` node and fan out to four providers (OpenAI, Anthropic, Gemini, Moonshot). Inbound paths use a violet gradient stroke with animated dashes (`wiwi-flow`); outbound paths use a fuchsia gradient. The hub has a radial glow halo that breathes (`wiwi-hub-pulse`, 3.2s). Endpoint dots pulse on staggered delays.
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

### 🖥️ Web UI (running)

Built-in SPA at **`http://localhost:4000/admin/ui`** — login with the master key. Pages: Dashboard, Providers (+ per-provider detail: edit name/type/base_url, manage key pool, browse upstream models live, delete), Virtual Keys, Models (edit model groups and weights live, attach deployments), Request Logs, Proxy Logs, Usage, Analytics, Budgets & Alerts, Settings. Live updates via SSE.

```bash
# rebuild the UI from source (React 19 + TypeScript + Vite + Tailwind 4, built with bun)
cd web && bun install && bun run build   # output → wiwi/server/static/
bun run dev                              # dev server, proxies to a running gateway
```

### 🔌 API

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

# Cline (OAuth) accounts — start the redirect, connect, check status, refresh, disconnect
curl -X POST localhost:4000/admin/cline/oauth/login-url -H "$MK" -d '{}'      # {auth_url, state}
curl -X POST localhost:4000/admin/cline/oauth/connect -H "$MK" -d '{"code": "..."}'
curl -X POST localhost:4000/admin/cline/oauth/auto-connect -H "$MK" -d '{}'   # background polling
curl localhost:4000/admin/cline/oauth/status -H "$MK"
curl -X POST localhost:4000/admin/cline/oauth/refresh -H "$MK" -d '{"provider": "cline-main"}'
curl -X DELETE localhost:4000/admin/cline/oauth/disconnect -H "$MK" -d '{"provider": "cline-main"}'

# Cline global model — pick model ids once, auto-deploy to every Cline account
curl localhost:4000/admin/cline/models -H "$MK"
curl localhost:4000/admin/cline/settings -H "$MK"
curl -X PUT localhost:4000/admin/cline/settings -H "$MK" \
  -d '{"default_models": ["anthropic/claude-sonnet-4-5", "openai/gpt-4o"]}'
curl -X DELETE localhost:4000/admin/cline/settings/default-models/anthropic%2Fclaude-sonnet-4-5 -H "$MK"

# WorkBuddy (CodeBuddy) accounts — same OAuth-style lifecycle, parallel Cline API
curl localhost:4000/admin/workbuddy/accounts -H "$MK"
curl -X POST localhost:4000/admin/workbuddy/accounts -H "$MK" \
  -d '{"label": "main", "auth_json": "..."}'
curl -X POST localhost:4000/admin/workbuddy/accounts/<label>/refresh -H "$MK"
curl -X DELETE localhost:4000/admin/workbuddy/accounts/<label> -H "$MK"

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
| `GET /admin/cline/models` · `GET/PUT/DELETE /admin/cline/settings` | Cline global model catalog and the cross-account default-model list |
| `POST /admin/cline/oauth/login-url` · `POST /admin/cline/oauth/connect` · `POST /admin/cline/oauth/auto-connect` | start the Cline OAuth redirect, submit the code, or run a background auto-connect |
| `GET /admin/cline/oauth/status` · `POST /admin/cline/oauth/refresh` · `DELETE /admin/cline/oauth/disconnect` | OAuth account state, on-demand token refresh, disconnect |
| `GET/POST/DELETE /admin/workbuddy/accounts[/{label}]` · `POST /admin/workbuddy/accounts/{label}/refresh` | WorkBuddy / CodeBuddy (Tencent) account CRUD + on-demand token refresh |

Per-request stats tracked: input / cached / reasoning / output tokens, TPS, TTFT, latency, cost, cache hit + cache savings, retry chain (per-attempt deployment/provider/key/status), and which provider key served it.

Every admin mutation writes an audit event (`actor`/`action`/`target`/`diff`) to an `audit_logs` table — key lifecycle, provider/pool edits, routing changes.

## 🧪 Tests & lint

```bash
python3 -m pytest tests/ -q                                 # all tests (979 collected, currently green)
python3 -m pytest tests/test_fix_round17.py -q              # latest in-flight regression file
python3 -m pytest tests/test_codecs.py -q                  # single file
python3 -m pytest tests/test_router.py -k cooldown         # single test by name

ruff check wiwi/ tests/                                    # lint (line-length 100, target py311)
```

The suite mixes unit tests (`respx` HTTP mocks), ASGI end-to-end tests through the full app, and Hypothesis property-based round-trip tests over the dialect ↔ IR codecs. Bugfix regressions land in the next thematic `test_fix_roundN.py` file — `test_fix_round17.py` is the current in-flight one (gaps like `round1`/`round5` are real — old numbers were collapsed into `test_bugfix_round5.py` / other thematic files).

## 📚 Docs

- `UPDATE.md` — changelog for cross-provider translation fixes (read first for reasoning/thinking, tool_result, OpenRouter issues)
- `docs/ARCHITECTURE.md` — system design
- `docs/CORE.md` — handlers + streaming flow
- `docs/ADMIN.md` — admin UI/API design
- `docs/MVP.md` — scope + gap register
- `docs/PLAN.md` — build phases
- `docs/TECHSTACK.md` — technology choices
- `docs/STREAMING_PERFORMANCE_RECOVERY.md` — streaming/tool-call/recovery improvement report (proposal)

Docs intentionally run ahead of the implementation in places (handler pipeline, DeltaBus, DB schema, Postgres/Redis backends are specified but not yet built). When docs and code disagree, trust the code — or treat the doc section as the spec for work in progress.

## 🛡️ Guardrails

- **Never commit `wiwi.yaml` or `wiwi.db`** — they hold live provider keys and runtime state (both gitignored). Provider keys come from env via `os.environ/NAME`; master key via `WIWI_MASTER_KEY`.
- Admin endpoints (`/admin/*`) require the master key; client traffic authenticates with virtual keys (`sk-wiwi-…`).
- Virtual keys are SHA-256-hashed in storage; plaintext is returned only once at generation time.
