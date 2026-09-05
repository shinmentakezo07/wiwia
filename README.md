<div align="center">

# 🌀 wiwi

### One gateway. Every dialect. Any provider.

**Speak** OpenAI Chat · OpenAI Responses (Codex CLI) · Anthropic Messages (Claude Code) **on the inbound.**
**Route to** OpenAI · Anthropic · Gemini · OpenRouter · NVIDIA NIM · Cline · WorkBuddy · GMI Cloud · B.AI · OpenCode Zen · any OpenAI-compatible endpoint.

<p>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white&style=for-the-badge">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white&style=for-the-badge">
  <img alt="React 19" src="https://img.shields.io/badge/React-19-149eca?logo=react&logoColor=white&style=for-the-badge">
  <img alt="Vite" src="https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white&style=for-the-badge">
</p>

<p>
  <img alt="Tests" src="https://img.shields.io/badge/tests-1225%20passing-34d399?style=for-the-badge">
  <img alt="Lint" src="https://img.shields.io/badge/lint-ruff%20clean-9CA3AF?logo=ruff&logoColor=white&style=for-the-badge">
  <img alt="Test files" src="https://img.shields.io/badge/test%20files-62-7C3AED?style=for-the-badge">
  <img alt="Self-hosted" src="https://img.shields.io/badge/self--hosted-one%20binary-F59E0B?style=for-the-badge">
</p>

**3 inbound dialects × 11 outbound providers — no pairwise converters, one canonical IR.**

</div>

---

## The shape of it

```
      INBOUND (3 dialects)                 CORE                   OUTBOUND (11 providers)
  ┌────────────────────────┐                                ┌──────────────────────────────┐
  │ 🟢 OpenAI Chat         │──┐                          ┌──│ openai                       │
  │    /v1/chat/completions│  │                          │  │ anthropic                    │
  │                        │  │                          │  │ gemini                       │
  │ 🟣 OpenAI Responses    │──┼──►  Canonical IR  ──►    ├──│ openrouter                   │
  │    /v1/responses       │  │     (wiwi/ir)            │  │ nvidia-nim                   │
  │                        │  │          │               │  │ cline          (OAuth)       │
  │ 🟠 Anthropic Messages  │──┘          │               │  │ workbuddy      (OAuth)       │
  │                        │             │               │  │ gmicloud                     │
  │                        │             │               │  │ bai                          │
  │                        │             │               │  │ opencode       (Zen)         │
  │                        │             │               │  │ openai-compatible            │
  └────────────────────────┘             │               └──────────────────────────────┘
      │                    ┌──────────────────────────┐
      │                    │ router   key pools · WRR │
      │                    │          retries · cooldown
      │                    │          fallbacks · cycle-N
      │                    ├──────────────────────────┤
      │                    │ streaming  deltas · coalesce
      │                    │            loopdetect · resume
      │                    │            partial_json · validate
      │                    ├──────────────────────────┤
      │                    │ auth · ratelimit · cost   │
      │                    │ logging · stats · audit   │
      │                    └──────────────────────────┘
      │
      └── responses re-encoded in the CALLER's dialect
          (Claude Code can be backed by GPT, and never knows)
```

**The payoff:** adding an inbound surface is one module in `wiwi/wire/`. Adding an outbound provider is one adapter in `wiwi/providers/` plus a line in the registry. Core code never branches on dialect or provider name.

---

## Contents

| | |
|---|---|
| [✨ Why wiwi](#-why-wiwi) | the problems it actually solves |
| [🚀 Features](#-features) | translation · routing · auth · observability · admin |
| [🧠 How it works](#-how-it-works) | request lifecycle + streaming pipeline |
| [📥 Surfaces](#-surfaces-inbound) | endpoints + translation matrix |
| [📤 Providers](#-providers-outbound) | all 11, with default endpoints |
| [⚡ Quickstart](#-quickstart) | run it in 60 seconds |
| [🧰 Commands](#-commands) | install · run · benchmark |
| [🔌 Connecting clients](#-connecting-clients) | Claude Code · Codex · SDK · curl |
| [⚙️ Configuration](#-configuration-wiwiyaml) | full `wiwi.yaml` reference |
| [🗂️ Project structure](#-project-structure) | where everything lives |
| [🎨 Admin UI](#-admin-ui) | design system + console pages |
| [📡 API reference](#-api-reference) | every endpoint |
| [🧪 Tests & lint](#-tests--lint) | |
| [📚 Docs](#-docs) | |
| [🛡️ Guardrails](#-guardrails) | |
| [📜 License & terms](#-license--terms) | MIT · [TERMS.md](TERMS.md) |

---

## ✨ Why wiwi?

> LiteLLM gives you routing. wiwi gives you **routing + a live control plane**.

| 😩 Problem | 💡 wiwi's answer |
|---|---|
| One client dialect, many models behind it | **Hub-and-spoke translation.** Any of 3 inbound dialects ↔ any of 11 outbound providers. N×M coverage from N+M modules. |
| Rate-limit pain across many keys | **Smooth weighted round-robin** key pools with per-key cooldowns, `failover_mode`, retries, and per-key cooldown reset. |
| Reasoning parameters don't line up | Canonical IR collapses `reasoning_effort`, `thinking.budget_tokens`, and OpenRouter's `reasoning{}` into one form. Thinking blocks round-trip — multi-turn survives a mid-conversation model switch. |
| Provider-hosted tools fragment per dialect | **Builtin tool registry** (`wiwi/ir/builtin_tools.py`): `web_search` renders as `web_search_20250305` (Anthropic), `web_search` (Responses), `google_search` (Gemini), `openrouter:web_search` — one canonical name in the IR. |
| Want a UI, not just a YAML | Built-in dark admin SPA at `/admin/ui` — keys, providers, key pools, model groups, request logs, live SSE tail, per-request stats (TTFT, TPS, cost, cache savings, retry chain). |
| Mutating config means a restart | Live `/admin/*` mutations: edit routing weights, add/remove keys, rename providers, attach deployments — without dropping traffic. Everything persists to the DB via `ConfigStore`, **and** every mutation writes an `audit_logs` event. |
| Costs and budgets | Virtual keys with budget / RPM / TPM / model allowlist / TTL. Per-request cost + token breakdown with aggregate and timeseries rollups. |
| No idea what's happening right now | `/admin/stream` SSE live tail with `Last-Event-ID` replay, plus an optional Prometheus `/metrics` endpoint. |

---

## 🚀 Features

<table>
<tr>
<td width="50%" valign="top">

### 🔀 Translation
- **3 inbound dialects × 11 outbound providers**, with `drop_params` and `extra_headers` for the awkward edges.
- **Builtin tool translation** — `web_search` maps to each provider's native tool (`web_search_20250305` / `web_search` / `google_search` / `openrouter:web_search`), with a config subset (`max_uses`, `allowed_domains`, `blocked_domains`, `user_location`, `search_context_size`) rendered per surface.
- OpenRouter unified `reasoning{}` translation for `low` / `medium` / `high` / explicit token budgets.
- Anthropic `cache_control` blocks pass through untouched; cache hits and savings appear in stats.
- Tool/function-call translation across dialects, including **parallel tool calls** with correct `output_index` interleaving.
- Multimodal parts (image, audio, document) wired through the IR.
- `count_tokens` endpoint for the Anthropic surface.
- Anthropic native `output_config` for `json_schema` structured outputs.

### 🧭 Routing & resilience
- Key pools with **smooth weighted round-robin** (per-key `weight`, `enabled`).
- Cooldowns on failure, `allowed_fails` threshold, configurable `cooldown_time`.
- `failover_mode: any_error | standard` — rotate on any non-200, or keep historical 429/5xx-only behaviour.
- `key_max_consecutive_fails` permanently retires a dead key (401/403 count double).
- Retries with `fallbacks:` plus a separate `context_window_fallbacks:` table for overflow errors.
- Strategies: `simple-shuffle`, `least-busy`, `latency-based`.
- `cycle_every_n` forces cursor advancement so traffic actually *rotates*, not just weight-spreads.
- Rich aliases: `model_group_alias` accepts a plain string or a rich `{target, force_mapping}` entry.

</td>
<td width="50%" valign="top">

### 🌊 Streaming subsystems
- `stream_idle_timeout_s` — max seconds between upstream chunks.
- `stream_loop_detection` — O(1)-per-token repetition detector (periods 1–8).
- `stream_coalesce` — merge `TextDelta`s under backpressure (queue depth 100, 8 KiB / 50 ms).
- `stream_resume: off | content_only | enabled` — mid-stream failover with partial output prepended.
- `stream_event_ids` — monotonic SSE ids for client-side resumption.
- `stream_grace_drain_s` — keep pumping upstream after client disconnect for accurate billing.

### 🔐 Auth, cost, limits
- Virtual keys (`sk-wiwi-…`), SHA-256-hashed at rest, plaintext shown once at mint. Optional `custom_key` (≥16 chars).
- Per-key: `max_budget`, `rpm`, `tpm`, model allowlist, TTL, enable/disable.
- Per-deployment: `max_tokens`, `rpm`, `tpm`, `timeout`, `extra_headers`, `extra_body`.
- User accounts (`/auth/signup|login|logout|me`) with roles; `max_keys_per_user` caps live keys per owner (admins exempt).
- `POST /auth/playground-key` mints a scoped session key (24h TTL, 5 per user).
- Optional global `global_rpm` / `global_tpm` sliding-window caps.
- Cost engine with an explicit `unpriced` flag so unknown models are logged, not silently $0.

### 📊 Observability
- Per-request DB row: input / cached / reasoning / output tokens, TTFT, latency, TPS, cost, cache hit + savings, full retry chain, which key served it.
- Proxy-level ring-buffer log.
- `/admin/stream` SSE live tail (`Last-Event-ID` replay, keepalive pings).
- `/admin/stats/overview` + `/admin/stats/timeseries?bucket=…&metric=…`.
- Prometheus `/metrics` (opt-in via `prometheus_enabled`) — 8 metric families.
- Spend / error alert rules (storage; evaluation engine is post-MVP).

### 🛠️ Admin & operations
- Dark SPA at `/admin/ui` with **16 console pages** plus a **Playground**.
- Master-key- or session-gated REST API at `/admin/*`.
- Audit trail (`actor` / `action` / `target` / `diff`) for every mutation — *including credential reveals*.
- SQLite by default; **PostgreSQL built in** (`asyncpg` ships as a core dependency — just set `DATABASE_URL`).
- Optional Redis backend for rate limits via the `[redis]` extra.
- `--reload` dev mode, `start.sh` wrapper, multi-stage Docker build.

</td>
</tr>
</table>

---

## 🧠 How it works

Every direction goes `dialect → IR → provider`.

```
Client (openai SDK / Codex CLI / Claude Code)
   │  inbound dialect
   ▼
wiwi/wire/*  ──decode──►  Canonical IR (wiwi/ir)  ──►  router
                                                        │  key pools, WRR,
                                                        │  retries, cooldowns
                                                        ▼
                                                  providers/*  ──► upstream
Client  ◄──  wire encoder  ◄──  IRStreamDelta*  ◄──  adapter.decode
```

The request lifecycle lives in `wiwi/server/app.py:run_chat_like` — **decode → auth → rate limit → router retries/fallbacks → gateway complete/stream** — and back out through the wire encoders. `wiwi/core/context.py:RequestContext` is the single mutable holder threaded through all of it.

> 💡 `server/app.py` is ~3.3k lines and `core/gateway.py` ~970. Don't read either top to bottom. Start at `run_chat_like` and follow the pipeline it names.

### 🌊 Streaming pipeline

Everything that touches a stream lives in `wiwi/streaming/` and is intentionally small. The contract between adapters and encoders is a single tagged union (`IRStreamDelta` in `deltas.py`); the surrounding modules are deterministic transformations on top of it.

| Module | Responsibility |
|---|---|
| `deltas.py` | The `IRStreamDelta` taxonomy — 10 frozen dataclasses: `StreamStart`, `TextDelta`, `ThinkingDelta`, `ToolCallOpen`, `ToolCallArgsDelta`, `ToolCallClose`, `UsageFinal`, `Finish`, `StreamEnd`, `StreamError`. Adapters guarantee legality; encoders never defend against malformed sequences. |
| `coalesce.py` | `DeltaCoalescer` — merges consecutive `TextDelta`s under backpressure (queue depth > 100, 8 KiB or 50 ms), bypassing entirely for fast consumers. Never coalesces across control deltas. |
| `loopdetect.py` | O(1)-per-token repetition detector. Tracks periods 1..8 simultaneously; aborts when the window becomes periodic. Periods above 8 are deliberately uncovered — a genuine period-40 loop isn't the failure mode, and the quadratic scan was what ate the hot path. |
| `resume.py` | `StreamTape` — 256 KiB ring of content-bearing deltas with monotonic event ids. Two roles: mid-stream failover (Anthropic capture-and-resume) and `Last-Event-ID` replay for reconnecting SSE clients. |
| `partial_json.py` | Vercel-AI-SDK-style incremental JSON parser for streaming tool-call args. Auto-repairs truncated JSON at close (appends missing `"` / `]` / `}`), never raises on malformed input. |
| `validation.py` | Tool-call args validated against the declared JSON schema on `ToolCallClose`. Caps payloads at 1 MiB; **never logs raw args** — only length plus the first 16 hex of a SHA-256 fingerprint, so tool payloads containing user secrets don't leak via structlog. |
| `sse.py` | SSE framing helpers used by the wire encoders. |

> ⚠️ **One asymmetry in the contract:** `StreamError` may terminate at **any** point, replacing everything after the last emitted delta. It is the abnormal-path terminal and needs no preceding `Finish`.

---

## 📥 Surfaces (inbound)

| Method | Endpoint | Dialect | Works with |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | 🟢 **OpenAI Chat** | openai SDK, LangChain, curl |
| `POST` | `/v1/responses` | 🟣 **OpenAI Responses** | Codex CLI (`base_url` → wiwi) |
| `POST` | `/v1/messages` | 🟠 **Anthropic Messages** | Claude Code (`ANTHROPIC_BASE_URL` → wiwi), anthropic SDK |
| `POST` | `/v1/messages/count_tokens` | 🟠 **Anthropic** | token counting, no inference |
| `GET` | `/v1/models` | 📋 model list | all |
| `GET` | `/health` | 💚 liveness | `{status, groups, providers}` |
| `GET` | `/metrics` | 📈 Prometheus | opt-in, master-key gated |

> 🪄 **Response shape always matches the inbound dialect.** OpenAI clients see OpenAI error envelopes (`{"error":{…}}`); Anthropic clients see Anthropic error envelopes (`{"type":"error",…}`). Every response carries `x-wiwi-request-id` and `x-wiwi-latency-ms`; bodies over `max_request_body_mb` (default 50) get a 413.

### 🔁 Dialect × Provider translation matrix

Any inbound dialect works with any outbound provider. The IR handles translation — no pairwise converters.

| ↓ In  ╲  Out → | OpenAI | Anthropic | Gemini | OpenRouter | NIM | Cline | B.AI | GMI | WorkBuddy | OpenAI-compat |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 🟢 **OpenAI Chat**       | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🟣 **OpenAI Responses**  | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 🟠 **Anthropic Messages**| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 📤 Providers (outbound)

All 11 types ship in `wiwi/config.py:PROVIDER_TYPES`, which is the single source of truth — the router catalog, admin validation, and the Pydantic schema all reference it, and import-time `assert`s in both `registry.py` and `router.py` fail loudly if a type is added without a matching adapter and catalog card.

| Type | Adapter | Default endpoint | Notes |
|---|---|---|---|
| `openai` | `openai_adapter.py` | `https://api.openai.com/v1` | Chat + Responses; `base_url` configurable |
| `anthropic` | `anthropic_adapter.py` | `https://api.anthropic.com/v1` | thinking + `cache_control` pass through; native `output_config` |
| `gemini` | `gemini_adapter.py` | `https://generativelanguage.googleapis.com/v1beta` | multimodal, structured output, function calling |
| `openrouter` | `openrouter_adapter.py` | `https://openrouter.ai/api/v1` | unified `reasoning{}` translation, `reasoning_details` decoding |
| `nvidia-nim` | `nim_adapter.py` | `https://integrate.api.nvidia.com/v1` | **vLLM-backed quirks**: `nim_tool_schema.py` strips boolean JSON-Schema subschemas and aliases parameters named `type` to `_nim_arg_<name>`, restoring agent-facing names on the way back |
| `cline` | `cline_adapter.py` | `https://api.cline.bot/api/v1` | OAuth (WorkOS) with on-demand refresh, cross-account WRR, global default-model list |
| `workbuddy` | `workbuddy_adapter.py` | `https://copilot.tencent.com` | WorkBuddy / CodeBuddy (Tencent); nested-JSON auth, stream-only upstream, business errors ride HTTP 200 in `{code,msg,data}` envelopes |
| `gmicloud` | *(openai wire)* | `https://api.gmi-serving.com/v1` | GMI Cloud serving endpoint |
| `bai` | `bai_adapter.py` | `https://api.b.ai/v1` | B.AI unified gateway — one key across Chat / Responses / Messages protocols; replays `reasoning_content` on tool-call turns |
| `opencode` | `opencode_adapter.py` | `https://opencode.ai/zen/v1` | OpenCode Zen — per-model protocol routing (Responses for GPT/Grok/Muse-Spark, Messages for Claude/Qwen, Gemini for Gemini, Chat otherwise) with a live `User-Agent: opencode/<version>` refreshed every 5 min (`opencode_version.py`) |
| `openai-compatible` | *(openai wire)* | *(you supply it)* | any URL — Ollama, vLLM, LM Studio, Together, Groq, DeepSeek |

> 🔐 Provider keys enter as `os.environ/NAME` in YAML. **Nothing is committed to the repo** — `wiwi.yaml`, `wiwi.db`, `.env`, and `key.md` are all gitignored.

### 🔧 Provider-hosted builtin tools

Some tools are executed *by the provider* — the model never sees a function schema. `wiwi/ir/builtin_tools.py` holds the canonical registry; per-surface wire types are rendered at the codec/adapter boundaries.

| Canonical | Anthropic | Responses | Gemini | OpenRouter | OpenAI Chat |
|---|---|---|---|---|---|
| `web_search` | `web_search_20250305` | `web_search` | `google_search` | `openrouter:web_search` | — *(dropped with a warning)* |

Config subset carried in `Tool.builtin_config`: `max_uses`, `allowed_domains`, `blocked_domains`, `user_location`, `search_context_size`. Surfaces render what they understand and drop the rest. Unmapped builtins (e.g. Anthropic `code_execution_20250522`) stay builtin-shaped in the IR so they survive a round-trip.

---

## ⚡ Quickstart

### 📋 Requirements

- 🐍 **Python ≥ 3.11**
- 📦 **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- 🐳 **Docker** (optional)
- 🥟 **Bun or Node** (only for rebuilding the admin UI)

### 🚀 Install & run locally

```bash
# 1. config
cp wiwi.yaml.example wiwi.yaml        # then edit providers/keys/model_list

# 2. provider keys + admin key  (or put these in .env — see .env.example)
export OPENAI_API_KEY=sk-... \
       ANTHROPIC_API_KEY=sk-ant-... \
       WIWI_MASTER_KEY=sk-wiwi-master-mysecret

# 3. install & run
uv venv && uv pip install -e ".[dev]"
wiwi --config wiwi.yaml               # serves http://0.0.0.0:4000
```

Then open **<http://localhost:4000/admin/ui>** and log in with the master key.

### 🐳 Or run with Docker

```bash
export WIWI_MASTER_KEY=sk-wiwi-master-mysecret
docker compose up --build
```

The compose stack runs **Postgres 16 + wiwi** together (Postgres is a plain service, not a profile), mounts a `wiwi_data` volume, and defaults `DATABASE_URL` to the bundled Postgres. Override with `DATABASE_URL=sqlite+aiosqlite:///…` if you'd rather stay on SQLite. Provider keys pass through from `.env` / your shell.

The image is a three-stage build: `uv` installs Python deps → `bun` builds the SPA → the runtime image runs as non-root `wiwi` (uid 10001).

### 🔧 Config-loading precedence

The CLI is explicit about where config comes from (order matters):

1. `--config` / `-c` flag on the CLI
2. `WIWI_CONFIG` env var (raw YAML — useful in containers)
3. `wiwi.yaml` in the working directory

A `.env` in the cwd is loaded **before** any of the above, so `WIWI_MASTER_KEY`, `DATABASE_URL`, provider keys, and `WIWI_CONFIG` are all resolved by the time YAML parsing begins.

> 🧩 Any string value in the YAML may be `os.environ/NAME`. Interpolation is recursive. **Missing env vars resolve to empty strings** — so the example config loads cleanly in a fresh container, and empty-key entries are filtered out by validation rather than crashing startup.

### 🎯 Three ways to connect a client

| Client | Set this | Then run |
|---|---|---|
| 🤖 **Claude Code** | `ANTHROPIC_BASE_URL=http://localhost:4000`<br>`ANTHROPIC_AUTH_TOKEN=sk-wiwi-...` | `claude` |
| ⌨️ **Codex CLI** | `OPENAI_BASE_URL=http://localhost:4000/v1` | `codex --model gpt-4o` |
| 🐍 **openai SDK** | `base_url="http://localhost:4000/v1"`<br>`api_key="sk-wiwi-..."` | `client.chat.completions.create(...)` |
| 🌐 **curl** | `Authorization: Bearer sk-wiwi-...` | `curl localhost:4000/v1/models` |

---

## 🧰 Commands

### Install

```bash
uv venv && uv pip install -e .            # runtime only
uv venv && uv pip install -e ".[dev]"     # + pytest, pytest-asyncio, respx, asgi-lifespan, ruff, hypothesis
uv pip install -e ".[redis]"              # Redis rate-limit backend
```

> 📌 **Postgres needs no extra.** `asyncpg` is a core dependency — point `DATABASE_URL` at a Postgres instance and it just works. There is no `[pg]` extra.

### Run the gateway

```bash
wiwi --config wiwi.yaml                             # host/port from wiwi_settings
wiwi -c wiwi.yaml --host 0.0.0.0 --port 4000        # explicit overrides (-c = --config)
wiwi --reload --reload-dir wiwi                     # dev mode: restart on .py changes
```

On startup it prints the listen address plus the number of deployments and providers loaded.

`./start.sh` is a dev convenience wrapper: kills anything on the port, installs web deps, then runs the backend and the Vite dev server **together with prefixed, interleaved logs**. Env knobs: `WIWI_PORT`, `WIWI_WEB_PORT`, `WIWI_RELOAD`, `WIWI_RELOAD_DIRS`.

### Benchmark

```bash
python3 bench.py                                            # default sweep
python3 bench.py -n 10 -c 1,4,16 --max-tokens 100
python3 bench.py --targets wiwi,litellm --no-stream
```

Measures TTFT, total latency, tokens, and output TPS per request; aggregates p50/p95, success rate, and throughput per concurrency level. Edit `TARGETS` / `MODEL` at the top of `bench.py` to point at your gateways.

---

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

**🌐 curl** — Anthropic dialect in, OpenAI model behind it:

```bash
curl http://localhost:4000/v1/messages \
  -H "x-api-key: $WIWI_VIRTUAL_KEY" -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o","max_tokens":128,
       "messages":[{"role":"user","content":"hi"}]}'
```

---

## ⚙️ Configuration (`wiwi.yaml`)

Single LiteLLM-shaped file. **Any string value may be `os.environ/NAME`.**
```yaml
providers:              # named provider accounts, each with a pool of keyed entries
  - name: openai-main
    provider: openai    # openai | anthropic | gemini | openai-compatible | openrouter
                        # | gmicloud | bai | nvidia-nim | cline | workbuddy | opencode
    keys:
      - {label: main,   key: os.environ/OPENAI_API_KEY,   weight: 3}
      - {label: backup, key: os.environ/OPENAI_API_KEY_2, weight: 1}

  - name: openrouter          # OpenRouter gets unified reasoning translation
    provider: openrouter      #   (reasoning_effort/thinking_budget → reasoning{})
    base_url: https://openrouter.ai/api/v1
    extra_headers: {X-Title: wiwi-gateway}
    keys: [{label: main, key: os.environ/OPENROUTER_API_KEY}]

  - name: local-ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    keys: [{label: local, key: "ollama"}]

  # Cline and WorkBuddy are OAuth providers — accounts are added at runtime
  # through the admin UI or the /admin/{cline,workbuddy} OAuth endpoints,
  # not declared here.

model_list:             # model_name clients request → provider account + native model id
  - model_name: gpt-4o
    wiwi_params: {provider: openai-main, model: gpt-4o, weight: 2}
  - model_name: claude-sonnet
    # per-deployment overrides: max_tokens, rpm, tpm, timeout, extra_headers, extra_body
    wiwi_params: {provider: anthropic-main, model: claude-sonnet-4-20250514,
                  max_tokens: 8192, tpm: 100000}

router_settings:
  routing_strategy: simple-shuffle     # simple-shuffle | least-busy | latency-based
  num_retries: 2
  timeout: 120
  allowed_fails: 3
  cooldown_time: 30                    # seconds a key cools down after failures
  failover_mode: any_error             # any_error | standard
  key_max_consecutive_fails: 5         # retire a key permanently (401/403 count 2×)
  cycle_every_n: 3                     # force WRR cursor advance every N requests
  # global_rpm: 600                    # optional gateway-wide sliding-window caps
  # global_tpm: 200000
  # -- streaming resilience --
  stream_idle_timeout_s: 30
  stream_loop_detection: true
  stream_loop_limit: 100
  stream_coalesce: false               # merge TextDeltas under backpressure
  stream_coalesce_max_bytes: 8192
  stream_coalesce_max_ms: 50
  stream_resume: off                   # off | content_only | enabled
  stream_resume_max_retries: 1
  stream_event_ids: false              # monotonic SSE ids for Last-Event-ID
  stream_grace_drain_s: 0
  # -- metrics --
  prometheus_enabled: false
  prometheus_path: /metrics
  fallbacks:
    claude-sonnet: ["gpt-4o"]
  # context_window_fallbacks:          # separate table for context-overflow errors
  #   long-task: ["claude-sonnet"]
  model_group_alias:
    gpt-4: gpt-4o                      # plain string, or the rich form:
    # gpt-4: {target: gpt-4o, force_mapping: true}

general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: os.environ/DATABASE_URL   # sqlite+aiosqlite:///wiwi.db (default) or postgres
  # redis_url: redis://localhost:6379/0   # requires the [redis] extra
  max_keys_per_user: 50                   # caps live virtual keys per non-admin owner

wiwi_settings:
  drop_params: true            # silently drop params the target provider doesn't support
  max_request_body_mb: 50
  log_requests: true
  store_prompts_in_spend_logs: false
  log_retention_days: 30       # prune request_logs at startup; 0 = keep forever
  host: 0.0.0.0
  port: 4000
  # public_url: https://wiwi.example.com   # pin OAuth callbacks; ignores X-Forwarded-*
  header_allowlist: [anthropic-version, anthropic-beta,
                     openai-organization, openai-project, openai-beta]
```

### 🧩 Extensibility escape hatch

`extra_body` on a deployment merges raw JSON into the upstream request body at encode time — for provider-specific routing knobs, e.g. OpenRouter's provider filter:

```yaml
wiwi_params:
  provider: openrouter
  model: openai/gpt-4o
  extra_body: {provider: {only: ["gmicloud"]}}
```

---

## 🗂️ Project structure

```
wiwi/                      15.5k lines of Python across 58 modules
├── main.py                CLI entrypoint (wiwi --config …)
├── config.py              YAML → pydantic; env interpolation; PROVIDER_TYPES
├── ir/                    Canonical IR: types.py (tagged parts, messages,
│                          tools, params, usage) + builtin_tools.py registry
├── wire/                  Inbound codecs: openai_chat.py · openai_responses.py
│                          · anthropic_messages.py
├── providers/             Outbound adapters + base.py protocol + registry.py
├── core/                  gateway.py (execution engine, pricing, log events)
│                          context.py (RequestContext)
├── streaming/             deltas · coalesce · loopdetect · resume
│                          partial_json · validation · sse
├── router/router.py       Model groups, key pools (smooth WRR), cooldowns,
│                          retries, fallbacks, BUILTIN_PROVIDER_TYPES catalog
├── auth/                  keys.py · service.py · users.py
├── ratelimit/             memory.py + redis.py sliding-window limits
├── cost/pricing.py        Cost engine + token estimation fallback
├── logging_core/          events.py · db_sink.py · subsystem.py (ring buffer)
└── server/                app.py (FastAPI factory, proxy + /admin/*)
                           config_store.py (DB persistence for admin mutations)
                           stats.py (pure rollups) · metrics.py (Prometheus)

web/                       31.7k lines of TS/TSX — 51 page components (97 .tsx total)
tests/                     62 test files — unit (respx), ASGI e2e, Hypothesis
```

| Path | Role |
|---|---|
| `wiwi/ir/types.py` | 19 dataclasses: `TextPart`, `ImagePart`, `ToolUsePart`, `ToolResultPart`, `ThinkingPart`, `AudioPart`, `DocumentPart`, `Message`, `Tool`, four tool-choice variants (`Auto`/`None`/`Required`/`Named`), `ResponseFormat`, `GenParams`, `Request`, `Usage`, `AssistantTurn`, `Response` |
| `wiwi/server/config_store.py` | Persists admin-created providers / keys / deployments so they survive restart. **YAML entries are never written to the DB** — they're always reloaded from the file. |
| `wiwi/server/stats.py` | Pure functions over `LogEvent` lists — unit-testable with no DB. |
| `bench.py` | Stress / latency / TPS tester for wiwi and any OpenAI-compatible proxy |
| `docs/` | `ARCHITECTURE` · `CORE` · `ADMIN` · `MVP` · `PLAN` · `TECHSTACK` · `STREAMING_PERFORMANCE_RECOVERY` |

### 🗄️ Database tables

| Table | Owner | Contents |
|---|---|---|
| `request_logs` | `logging_core/db_sink.py` | per-request tokens, TTFT, latency, TPS, cost, cache, retry chain |
| `audit_logs` | `logging_core/db_sink.py` | `actor` / `action` / `target` / `diff` for every admin mutation |
| `vkeys` | `auth/service.py` | hashed virtual keys + budgets + spend |
| `users` | `auth/users.py` | user accounts and roles |
| `providers` · `provider_keys` · `deployments` · `settings` · `model_prices` | `server/config_store.py` | admin-created config that must survive restart |

---

## 🎨 Admin UI

Two surfaces ship from the same `web/` source: a **dark admin console** at `/console/*` and a **31-route public marketing site** at `/` (landing, docs, pricing, blog, model catalog, cost calculators, and more).

### 🖥️ Console pages

| Section | Pages |
|---|---|
| **Overview** | Dashboard |
| **Traffic** | Request Logs · Usage · Analytics |
| **Configuration** | Models · Combos · Virtual Keys · Providers · ProviderDetail · OAuth Cline · WorkBuddy · Built-in Providers |
| **Admin** | Budgets & Alerts · Proxy Logs · Users · Settings |
| *(outside shell)* | Playground · Login · Signup · Onboarding |

Providers, Built-in Providers, Proxy Logs, Settings, and Users are **admin-only** — the sidebar appends them only when the current user has the admin role.

### 🎨 Design system

The console is a **dark-only** SPA (React 19 + TypeScript + Vite + Tailwind 4) on a custom design system: near-black surfaces, hairline white borders, a blue primary with violet/fuchsia secondary, tiny uppercase mono labels, tabular numeric values.

**Surfaces & accents** (CSS custom properties under `[data-admin]` in `web/src/styles.css`):

| Token | Value | Use |
|---|---|---|
| `--admin-bg` | `#050505` | App background |
| `--admin-surface` | `#0a0a0a` | Cards, sidebar, tables |
| `--admin-surface-elevated` | `#0e0e0e` | Dialogs, dropdowns |
| `--admin-border` | `rgba(255,255,255,0.04)` | Hairline borders |
| `--admin-border-hover` | `rgba(255,255,255,0.08)` | Hover border state |
| `--admin-accent` | `#3b82f6` | Primary blue (links, active nav, focus rings) |
| `--admin-accent-purple` | `#a855f7` | Purple secondary |
| `--admin-accent-violet` | `#7c3aed` | Violet tertiary |
| `--admin-success` / `warning` / `danger` | `#34d399` / `#fbbf24` / `#f87171` | Status semantics |

Brand accent (login page + logo gradient): the `brand-*` Tailwind ramp in `@theme` — an indigo→violet "iris" ramp from `#f3f1ff` (50) to `#291560` (950), with `#8757f7` (500) as primary.

**Layout shell** (`components/Layout.tsx`)

- **Fixed sidebar** (260px, collapses to 72px) on `#0a0a0a`, grouped into Overview / Traffic / Configuration / Admin. Active item gets a blue left-edge bar + `blue-500/[0.06]` tint.
- **Blurred topbar** (`backdrop-filter: blur(12px)` over `rgba(5,5,5,0.75)`) with page section + title, a live/offline SSE pulse badge, a mono tabular clock, and Sign out.
- **Ambient backdrop**: fixed layer with a 64px grid at 2% opacity plus three radial glows (blue top-left, violet bottom-right, purple center).
- Content scrolls inside `main.admin-scroll` (thin gradient scrollbar), capped at `max-w-[1400px]`, with a staggered fade-up entrance.

**Component kit** (`components/ui.tsx`)

| Primitive | Notes |
|---|---|
| `Card` / `CardHeader` / `PageHeader` | `admin-card` surfaces, gradient top-line on hover, `admin-stat-highlight` |
| `Button` | `primary` (blue-tinted soft fill) · `ghost` · `danger` · `outline` |
| `Input` / `Select` / `Field` | `admin-input` with focus ring `0 0 0 3px rgba(99,102,241,0.08)` |
| `Toggle` | switch with blue glow when on |
| `Badge` | green / red / amber / gray / blue / violet — uppercase 10px, soft tinted bg |
| `StatCard` | hero metric with gradient-text value, optional 12-point sparkline, delta chip (`↑/↓ N% vs prev hour`), `waiting` pulse at zero traffic |
| `Table` / `TD` | sticky headers, uppercase 10px headers, row hover |
| `Dialog` | portal modal, overlay fade + lift/blur entrance, Escape + click-outside |
| `CopyButton` · `Spinner` · `EmptyState` · `ErrorText` · `ProgressBar` | |

**Login page** (`pages/Login.tsx`) — the one light/dark screen (the rest is dark-only), centered on a glass card:

- **Ambient backdrop**: blueprint grid (`wiwi-grid`, 44px, radial mask), two drifting aurora orbs (violet + fuchsia, 20s `wiwi-drift`), a film-grain noise layer, and a central radial bloom.
- **Glass card** (`wiwi-card-glow`): `backdrop-blur-xl` over `white/80` (light) / `zinc-900/70` (dark), layered brand box-shadow, gradient light-line across the top edge.
- **Signature diagram** (`GatewayDiagram`): an SVG of wiwi's real hub-and-spoke routes — three inbound dialects converge into the `w` node and fan out to providers. Inbound paths use a violet gradient stroke with animated dashes (`wiwi-flow`); outbound use fuchsia. The hub has a breathing radial halo (`wiwi-hub-pulse`, 3.2s); endpoint dots pulse on staggered delays.
- **Form**: master-key input with key icon, show/hide toggle, mono font. Submit has a gradient fill with a shimmer sweep on hover; errors shake (`wiwi-shake`).
- **Trust footer**: lock icon + "Key stays in this browser — checked once against your gateway."

**Motion language**

| Class | Effect | Duration |
|---|---|---|
| `admin-stagger` | Children fade-up, 60ms stagger | 0.5s each |
| `admin-pulse-dot` | Live badge pulse | 2s infinite |
| `admin-skeleton` | Shimmer placeholder | 1.8s infinite |
| `admin-waiting-pulse` | Zero-traffic stat breathing | 2.4s infinite |
| `wiwi-enter` | Login card entrance (translateY + blur) | 0.55s |
| `wiwi-flow` | Diagram dash flow | 1.5s linear infinite |
| `wiwi-aurora` / `wiwi-drift` | Background orb drift | 20s infinite |
| `wiwi-hub-pulse` | Hub glow breathing | 3.2s infinite |
| `wiwi-shimmer` | Button hover sweep | 0.7s on hover |
| `wiwi-shake` | Login error shake | 0.3s |

All motion is gated behind `@media (prefers-reduced-motion: no-preference)` and disables cleanly under `reduce`.

```bash
cd web && bun install && bun run build   # tsc -b && vite build → wiwi/server/static/
cd web && bun run dev                    # dev server, proxies to a running gateway
cd web && bun run lint                   # eslint src (web/ is NOT covered by ruff)
```

---

## 📡 API reference

All `/admin/*` endpoints require the master key (`Authorization: Bearer …`) or an authenticated admin session.

### 🔑 Virtual keys

```bash
MK="Authorization: Bearer $WIWI_MASTER_KEY"

# mint — budget / RPM / TPM / model allowlist / TTL / optional custom_key
curl -X POST localhost:4000/admin/keys/generate -H "$MK" \
  -d '{"name": "team-a", "max_budget": 10, "rpm": 60, "tpm": 100000,
       "models": ["gpt-4o"], "ttl_seconds": 86400}'
# → {"key":"sk-wiwi-...","id":"k...","note":"store this key now..."}

curl localhost:4000/admin/keys -H "$MK"
curl -X PATCH  localhost:4000/admin/keys/<id> -H "$MK" -d '{"max_budget": 20}'
curl -X POST   localhost:4000/admin/keys/<id>/disable -H "$MK"
curl -X DELETE localhost:4000/admin/keys/<id> -H "$MK"
```

### 🏢 Providers & key pools

```bash
curl localhost:4000/admin/provider-catalog -H "$MK"     # 11 built-in cards + configured?
curl localhost:4000/admin/providers -H "$MK"            # pool status: health + cooldowns
curl -X POST localhost:4000/admin/providers -H "$MK" \
  -d '{"name": "openai-backup", "provider_type": "openai",
       "base_url": "https://api.openai.com/v1", "key": "os.environ/BACKUP_KEY"}'
curl -X PATCH  localhost:4000/admin/providers/<name> -H "$MK" -d '{"name": "openai-primary"}'
curl -X DELETE localhost:4000/admin/providers/<name> -H "$MK"   # 409 while groups reference it

# key pool
curl -X POST  localhost:4000/admin/providers/<name>/keys -H "$MK" \
  -d '{"label": "extra", "key": "os.environ/EXTRA_KEY", "weight": 2}'
curl -X PATCH localhost:4000/admin/providers/<name>/keys/<label> -H "$MK" \
  -d '{"disabled": true, "weight": 5}'          # + reset_status: true clears cooldown
curl -X DELETE localhost:4000/admin/providers/<name>/keys/<label> -H "$MK"
curl localhost:4000/admin/providers/<name>/keys/<label>/secret -H "$MK"  # audit-logged reveal
curl localhost:4000/admin/providers/<name>/models -H "$MK"   # live upstream model ids
```

### 🧩 Models, groups, aliases

```bash
curl localhost:4000/admin/models -H "$MK"
curl -X PATCH localhost:4000/admin/model-groups/<name> -H "$MK" \
  -d '{"weights": {"openai-main/gpt-4o": 3}, "strategy": "least-busy"}'
curl -X POST localhost:4000/admin/model-groups/<name>/deployments -H "$MK" \
  -d '{"group": "gpt-4o", "provider": "openrouter", "model_id": "openai/gpt-4o", "weight": 1}'
curl -X DELETE localhost:4000/admin/model-groups/<name>/deployments -H "$MK" -d '{...}'
curl -X POST localhost:4000/admin/aliases -H "$MK" \
  -d '{"set": {"gpt-4": "gpt-4o"}, "unset": ["gpt-3.5"]}'
```

### 💵 Pricing, logs, stats, users

```bash
curl localhost:4000/admin/pricing -H "$MK"
curl -X PUT    localhost:4000/admin/pricing/<model_id> -H "$MK" -d '{...}'
curl -X DELETE localhost:4000/admin/pricing/<model_id> -H "$MK"

curl localhost:4000/admin/logs/requests -H "$MK"     # DB-backed
curl localhost:4000/admin/logs/proxy -H "$MK"        # ring buffer
curl localhost:4000/admin/stats/overview -H "$MK"    # p50/p95/p99, cost, tokens
curl "localhost:4000/admin/stats/timeseries?bucket=minute&metric=cost&minutes=60" -H "$MK"
curl localhost:4000/admin/stream -H "$MK"            # SSE live tail
curl localhost:4000/admin/alert-rules -H "$MK"
curl -X PUT localhost:4000/admin/alert-rules -H "$MK" -d '{...}'

curl localhost:4000/admin/users -H "$MK"
curl -X PATCH localhost:4000/admin/users/<uid> -H "$MK" -d '{"role": "admin"}'
```

### 🔐 Session auth

```bash
curl -X POST localhost:4000/auth/signup  -d '{"email": "...", "password": "..."}'
curl -X POST localhost:4000/auth/login   -d '{"email": "...", "password": "..."}'
curl -X POST localhost:4000/auth/logout
curl localhost:4000/auth/me
curl -X POST localhost:4000/auth/playground-key       # scoped 24h session key
```

### 🔗 OAuth providers (Cline / WorkBuddy)

```bash
# Cline
curl -X POST localhost:4000/admin/cline/oauth/login-url -H "$MK" -d '{}'   # {auth_url, state}
curl -X POST localhost:4000/admin/cline/oauth/connect -H "$MK" -d '{"code": "..."}'
curl -X POST localhost:4000/admin/cline/oauth/auto-connect -H "$MK" -d '{}'
curl localhost:4000/admin/cline/oauth/status -H "$MK"
curl -X POST localhost:4000/admin/cline/oauth/refresh -H "$MK" -d '{"provider": "cline-main"}'
curl -X DELETE localhost:4000/admin/cline/oauth/disconnect -H "$MK" -d '{"provider": "cline-main"}'

# Cline global model list — pick ids once, auto-deploy to every Cline account
curl localhost:4000/admin/cline/models -H "$MK"
curl -X PUT localhost:4000/admin/cline/settings -H "$MK" \
  -d '{"default_models": ["anthropic/claude-sonnet-4-5", "openai/gpt-4o"]}'
curl -X DELETE "localhost:4000/admin/cline/settings/default-models/anthropic%2Fclaude-sonnet-4-5" -H "$MK"

# WorkBuddy (CodeBuddy) — parallel API
curl localhost:4000/admin/workbuddy/accounts -H "$MK"
curl -X POST localhost:4000/admin/workbuddy/import -H "$MK" -d '{"accounts": [...]}'
curl localhost:4000/admin/workbuddy/export -H "$MK"
curl -X POST localhost:4000/admin/workbuddy/refresh -H "$MK" -d '{"label": "main"}'
```

### Route map

| Route | Purpose |
|---|---|
| **Proxy** | |
| `POST /v1/chat/completions` · `POST /v1/responses` · `POST /v1/messages` | the three inbound dialects |
| `POST /v1/messages/count_tokens` | token counting without inference |
| `GET /v1/models` · `GET /health` | discovery · liveness |
| `GET /metrics` | Prometheus (opt-in, master-key gated) |
| `GET /public/models` | unauthenticated group + alias listing |
| **Virtual keys** | |
| `POST /admin/keys/generate` | mint (budget/RPM/TPM/allowlist/TTL/`custom_key`) |
| `GET /admin/keys` · `PATCH /admin/keys/{id}` | list · update limits live (cache evicted immediately) |
| `POST /admin/keys/{id}/disable` · `DELETE /admin/keys/{id}` | disable/enable · revoke |
| **Providers** | |
| `GET /admin/provider-catalog` | 11 built-in type cards, each flagged `configured` |
| `GET /admin/providers` | provider + key-pool status (health, cooldowns, req/err counts) |
| `POST /admin/providers` · `PATCH /admin/providers/{name}` · `DELETE /admin/providers/{name}` | add · rename/re-type/re-URL · remove (409 if referenced) |
| `POST /admin/providers/{name}/keys` · `PATCH …/{label}` · `DELETE …/{label}` | pool key CRUD, weight/enabled, cooldown reset |
| `GET /admin/providers/{name}/keys/{label}/secret` | reveal plaintext — **audit-logged** |
| `GET /admin/providers/{name}/models` | live upstream model ids |
| **Models & routing** | |
| `GET /admin/models` · `PATCH /admin/model-groups/{name}` | inspect · edit routing/weights live |
| `POST /admin/model-groups/{name}/deployments` · `DELETE …` | attach/detach deployments (POST creates the group) |
| `POST /admin/aliases` | batch `set` / `unset` model group aliases |
| `GET /admin/pricing` · `PUT /admin/pricing/{id}` · `DELETE /admin/pricing/{id}` | price overrides |
| **Logs & stats** | |
| `GET /admin/logs/requests` · `GET /admin/logs/proxy` | DB request logs · ring-buffer proxy logs |
| `GET /admin/stream` | SSE live tail (`Last-Event-ID` replay, keepalives) |
| `GET /admin/stats/overview` · `GET /admin/stats/timeseries` | aggregate + time-bucketed |
| `GET / PUT /admin/alert-rules` | spend/error rules (storage; engine post-MVP) |
| **Users & sessions** | |
| `GET /admin/users` · `PATCH /admin/users/{uid}` | user admin |
| `POST /auth/signup` · `/auth/login` · `/auth/logout` · `GET /auth/me` | session auth |
| `POST /auth/playground-key` | scoped session key for the Playground |
| **OAuth providers** | |
| `GET /admin/cline/models` · `GET/PUT/DELETE /admin/cline/settings` | Cline model catalog + cross-account defaults |
| `POST /admin/cline/oauth/{login-url,connect,auto-connect}` | start redirect · submit code · background poll |
| `GET /admin/cline/oauth/status` · `POST /admin/cline/oauth/refresh` · `DELETE /admin/cline/oauth/disconnect` | state · on-demand refresh · disconnect |
| `GET /cline/oauth/callback` | OAuth redirect target |
| `GET/POST /admin/workbuddy/accounts` · `POST /admin/workbuddy/import` · `GET /admin/workbuddy/export` · `POST /admin/workbuddy/refresh` | WorkBuddy account CRUD + refresh |

**Per-request stats tracked:** input / cached / reasoning / output tokens, TPS, TTFT, latency, cost, cache hit + cache savings, retry chain (per-attempt deployment/provider/key/status), and which provider key served it.

**Every admin mutation writes an audit event** (`actor` / `action` / `target` / `diff`) — key lifecycle, provider and pool edits, routing changes, and credential reveals.

---

## 🧪 Tests & lint

```bash
python3 -m pytest tests/ -q                                 # 1225 tests, all green
python3 -m pytest tests/test_fix_round27.py -q              # latest regression file
python3 -m pytest tests/test_codecs.py -q                   # single file
python3 -m pytest tests/test_router.py -k cooldown          # single test by name

ruff check wiwi/ tests/                                     # line-length 100, target py311
cd web && bun run lint                                      # eslint (web/ is not ruff-covered)
```

The suite is **62 test files** mixing **unit tests** (`respx` HTTP mocks), **ASGI end-to-end tests** through the full app, and **Hypothesis property-based round-trips** over the dialect ↔ IR codecs. `pytest-asyncio` runs in `asyncio_mode = "auto"`, so write bare `async def test_…` — no decorator needed.

Bugfix regressions land in the next thematic `test_fix_roundN.py` file — **`test_fix_round27.py` is the current in-flight one**. Gaps in the numbering (e.g. no `round1`, no `round5`) are real: old numbers were collapsed into `test_bugfix_round5.py` and other thematic files. Find the next unused number with `ls tests/test_fix_round*.py`.

---

## 📚 Docs

| Doc | What it is |
|---|---|
| `UPDATE.md` | **Read this first** for translation issues — changelog for every OpenAI ↔ Anthropic cross-provider fix, the OpenRouter adapter, and multi-turn conversation bugs, with before/after snippets and covering tests |
| `AUDIT.md` | Known-bug register: severity, `file:line` citations, one-line fix sketches. Read before starting bugfix work. |
| `docs/ARCHITECTURE.md` | System design |
| `docs/CORE.md` | Handlers + streaming flow |
| `docs/ADMIN.md` | Admin UI/API design |
| `docs/MVP.md` | Scope + gap register |
| `docs/PLAN.md` | Build phases |
| `docs/TECHSTACK.md` | Technology choices |
| `docs/STREAMING_PERFORMANCE_RECOVERY.md` | Streaming / tool-call / recovery improvement report |

> ⚠️ `ARCHITECTURE.md` and `CORE.md` **intentionally run ahead of the implementation** (handler pipeline, DeltaBus, reasoning/cache subsystems, DB schema, and Postgres/Redis backends are specified but not yet built; their repo-layout sections show planned directories like `wire/openai_chat/` that are actually flat files). When docs and code disagree, **trust the code** — or treat the doc section as the spec for work you're about to do.

---

## 🛡️ Guardrails

- **Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, or anything under `.verify/`** — they hold live provider keys and runtime state (all gitignored). `wiwi.yaml.example` is the tracked template.
- Provider keys come from env via `os.environ/NAME`; the master key from `WIWI_MASTER_KEY`.
- Admin endpoints (`/admin/*`) require the master key or an admin session; client traffic authenticates with virtual keys (`sk-wiwi-…`).
- Virtual keys are **SHA-256-hashed at rest** with constant-time compare; plaintext is returned only once, at generation time.
- `public_url` pins OAuth callback origins — `X-Forwarded-Host` is never trusted for URL building, so an attacker can't point an OAuth callback at their own origin.
- `X-Forwarded-For` is consulted **only** for rate-limiting buckets, never for authentication.
- The gateway-wide `header_allowlist` controls which inbound headers are forwarded upstream.
- **Never add dialect- or provider-specific branches in `core/`, `router/`, or `auth/`.** Dialect logic belongs in `wire/`; provider logic belongs in `providers/`.

---

## 📜 License & terms

The code is distributed under the **MIT License** — see [LICENSE](LICENSE) for the full text.

Operating a wiwi server is governed by the [Terms of Use](TERMS.md):

- **Personal use — free.** No fee, no registration.
- **Commercial use — allowed, with conditions.** If you charge money for a wiwi-based service, you accept the [terms](TERMS.md) by doing so (no impersonation, no fraud, honor upstream provider ToS, publish an abuse contact).
- **No liability.** The author is not responsible for misuse of your deployment — including fraud — and the software ships with no warranty.

```
Copyright (c) 2026 wiwi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software — as long as the copyright notice and this permission
notice are included in all copies or substantial portions of the Software.
```

Use it, fork it, ship it commercially — the code carries no strings. If you
*operate a server for paying customers*, the [Terms of Use](TERMS.md) apply.

---

<div align="center">

<sub>MIT licensed · server operation governed by <a href="TERMS.md">Terms of Use</a> · built with Python 3.11+, FastAPI, React 19, and bun</sub>

</div>
