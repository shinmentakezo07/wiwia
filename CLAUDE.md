# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**wiwi** is a self-hosted unified LLM gateway proxy (LiteLLM-shaped): three inbound API dialects — OpenAI Chat, OpenAI Responses (Codex CLI), Anthropic Messages — all route through one canonical internal representation (IR) to any of eleven outbound provider types; responses are always re-encoded in the caller's inbound dialect (e.g. Claude Code backed by GPT). Adds virtual keys, budgets, rate limits, key pools with smooth weighted round-robin, retries/cooldowns/fallbacks, cost tracking, request logs, and an admin web UI.

`.venv` is a symlink to an empty uv venv — it has no site-packages, so do **not** use `.venv/bin/python`. Use the ambient `python3` (3.12) / `pytest` (9.1.1) / `ruff` (0.16.4) on PATH (they have the project installed). The admin UI (`web/`) is a React 19 + Vite 6 SPA built with **bun** (not npm).

## Commands

```bash
# setup — only needed on a fresh machine; the current checkout's .venv is
# empty and the project is already installed into the ambient interpreter.
uv venv && uv pip install -e ".[dev]"    # + [redis] extra for the Redis rate-limit backend

# run server (default 0.0.0.0:4000; needs wiwi.yaml — cp wiwi.yaml.example wiwi.yaml)
wiwi --config wiwi.yaml [--host H] [--port P]
wiwi --reload --reload-dir wiwi          # dev: uvicorn reload, re-imports the app in a subprocess
DATABASE_URL=postgresql+asyncpg://... wiwi --config wiwi.yaml   # Postgres (default is SQLite at wiwi.db)
# or directly: uvicorn wiwi.server.app:create_app_from_config_path --factory

# tests
python3 -m pytest tests/ -q                                 # full suite — verify green; don't trust a pinned pass-count
python3 -m pytest tests/test_codecs.py -q                   # single file
python3 -m pytest tests/test_router.py -k cooling          # single test by name (-k matches test-name substrings)

# lint (ruff, line-length 100, target py311)
ruff check wiwi/ tests/

# admin UI
cd web && bun install && bun run dev     # dev server (proxies /admin /auth /public /v1 /health → :4000)
cd web && bun run build                  # tsc -b && vite build → wiwi/server/static/
cd web && bun run lint                   # eslint src (web/ is NOT covered by ruff)
./start.sh                               # backend (:4000) + Vite (:5173) together, prefixed logs —
                                         #   stale: still uses npm, not bun. Functional, not authoritative.

# docker (Postgres is a plain service with a healthcheck-gated depends_on — there is no --profile pg)
docker compose up --build

# load test
python3 bench.py                         # async httpx; TTFT, p50/p95, output TPS, concurrency sweep
```

Config precedence: `--config`/`-c` flag > `WIWI_CONFIG` env > `wiwi.yaml`. `load_env()` (python-dotenv, `override=False`) runs before config parse.

## Architecture

### Hub-and-spoke translation (the core idea)

No pairwise converters. Every direction goes dialect → IR → provider:

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

Adding an inbound surface = one new module in `wiwi/wire/`; adding a provider = one new adapter in `wiwi/providers/` + a line in `registry.get_adapter()`. Core code (`core/gateway.py`) never branches on dialect or provider name. Request flow end-to-end is best traced through `server/app.py:run_chat_like` (decode → auth → rate limit → router retries/fallbacks → gateway complete/stream) and back out through the wire encoders; `core/context.py:RequestContext` is the single mutable holder threaded through all of it.

Inbound surfaces: `POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/messages`, `POST /v1/messages/count_tokens`; plus `GET /v1/models`, `GET /public/models`, `GET /health`, `/admin/*` (master key), `/auth/*` (user sessions).

### `wiwi/` layout

| Path | Role |
|---|---|
| `wire/` | Inbound codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` — each owns `decode_request`, `encode_response`, a `StreamEncoder`, and `error_body` |
| `providers/` | Outbound adapters: openai, anthropic, gemini, openrouter, nim, cline, bai, workbuddy (+ `cline_oauth.py`/`cline_auto_refresh.py`, `workbuddy_auth.py`, `nim_tool_schema.py`), plus `base.py` + `registry.py` |
| `core/` | Engine: `gateway.py` (~1084 lines), `context.py` (RequestContext) |
| `ir/` | Internal representation (`types.py`, `builtin_tools.py`) |
| `streaming/` | `IRStreamDelta` taxonomy (92 lines) + `sse.py` / `coalesce.py` / `resume.py` / `tape_store.py` (durable journals) / `partial_json.py` / `loopdetect.py` / `validation.py` — the contract between adapters and encoders |
| `router/` | Key pools, weighted round-robin, retries, cooldowns, fallbacks |
| `auth/` | Virtual keys, budgets, users (`keys.py`, `service.py`, `users.py`) |
| `ratelimit/` | Sliding-window rpm/tpm (memory default + redis) |
| `cache/` | Opt-in exact-match response cache (`CacheSettings`, off by default) |
| `cost/` | Token/cost calculation (`pricing.py`) |
| `logging_core/` | Three-stream logger: request (DB+SSE), proxy (stdout+SSE), audit (sync DB) |
| `server/` | FastAPI app (`app.py`, ~3.4k lines), admin API, `stats.py` rollups, `config_store.py`, static SPA serving |
| `web/` | Admin UI (React 19 + TypeScript + Vite 6 + Tailwind 4), 51 pages in `src/pages/` |
| `tests/` | Pytest suite — 61 files: thematic regressions plus numbered `test_fix_roundN.py` |
| `docs/` | Design specs (intentionally run ahead of implementation) |

Two adapters carry provider-specific quirks that live in `providers/` (never in `core/`):

- **NVIDIA NIM** (`nim_adapter.py` + `nim_tool_schema.py` + `nim_native_tools.py`) — NIM is vLLM-backed and rejects JSON Schema boolean subschemas (`"additionalProperties": true`) and parameters named `type`, which collide with the schema keyword inside vLLM's tool parser. `nim_tool_schema.py` strips the former and aliases the latter to `_nim_arg_<name>`, keeping a mapping so agent-facing names are restored on the way back.
- **Cline** (`cline_adapter.py` + `cline_oauth.py` + `cline_auto_refresh.py`) and **WorkBuddy** (`workbuddy_adapter.py` + `workbuddy_auth.py` + `workbuddy_auto_refresh.py`) — OAuth-based; tokens refresh on demand, which is what makes their requests survive a 401.

`registry.get_adapter()` dispatches on provider type, and an import-time `assert` at the bottom of `registry.py` fails loudly if a type is added to `PROVIDER_TYPES` without a matching branch or `_OPENAI_WIRE_TYPES` entry — so adding a provider is safe-by-construction.

**Adapter ownership matters.** Adapters accumulate per-stream decode state across awaits (open tool indices, name fragments, deferred tool opens, NIM aliases), so `fresh_adapter(type)` returns a *private* instance and is what the request hot path must use; `get_adapter(type)` returns a shared singleton that is `reset()` on every hand-out and is only safe for synchronous, non-await-held use. Using the shared one on the hot path lets a concurrent request wipe an in-flight stream's state.

### Non-negotiable streaming contract

`streaming/deltas.py` defines the `IRStreamDelta` taxonomy — the contract between adapters and encoders:

```
StreamStart  (exactly one, first)
  TextDelta* | ThinkingDelta*
  ToolCallOpen → ToolCallArgsDelta* → ToolCallClose   (strictly nested per index)
UsageFinal   (exactly one, after last content delta)
Finish       (exactly one)
StreamEnd xor StreamError
```

Note the one asymmetry in that contract: `StreamError` may terminate at **any** point, replacing everything after the last emitted delta. It is the abnormal-path terminal and needs no preceding `Finish`. Adapters guarantee legality; encoders never defend against malformed sequences. Every delta variant is `@dataclass(frozen=True)` — adapters mutate per-stream state on the adapter instance, never on deltas.

### Admin web UI

- Source lives in `web/`; production build output lands in `wiwi/server/static/` and is served by `app.py` under `/admin/ui` (SPA history fallback). Built bundles are gitignored.
- Dev: `cd web && bun install && bun run dev` (Vite dev server proxies to a running gateway); ship: `bun run build`.
- Backend rollups live in `wiwi/server/stats.py` (pure functions over LogEvent lists — unit-testable without DB).
- `web/src/pages/` mixes ~15 admin console pages (Dashboard, Providers, VirtualKeys, RequestLogs, …) with ~30 public marketing pages (Landing, Pricing, Blog, Docs, …) — don't assume a page is admin-facing from the directory alone.

## Where to start reading

`server/app.py` is ~3.4k lines and `core/gateway.py` ~1084 — don't read either top to bottom. For a request's full path, start at `run_chat_like` in `server/app.py` and follow the pipeline it names; `RequestContext` (`core/context.py`, 63 lines) is the single mutable object threaded through every stage, so reading its fields tells you what the pipeline carries. For streaming, read `streaming/deltas.py` (92 lines, the whole contract) before any adapter.

## Config

Single `wiwi.yaml` (LiteLLM-shaped): `providers:` (named accounts, each with a pool of keyed entries), `model_list:` (`model_name` clients request → `wiwi_params` with provider account + native model id), `router_settings:` (strategy/retries/cooldowns/fallbacks/aliases), `general_settings:` (master_key, database_url), `wiwi_settings:` (drop_params, host/port, header allowlist, stream journal, cache). Any string value may be `os.environ/NAME`.

Error bodies are dialect-correct per surface (OpenAI `{"error":{…}}` vs Anthropic `{"type":"error",…}`) — produced by the wire codecs' `error_body`.

## Testing

- `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"` — write bare `async def test_…`, no `@pytest.mark.asyncio` decorator.
- **No `conftest.py` anywhere** — each test file builds its own `_config()` factory and its own `LifespanManager + httpx.ASGITransport` client fixture inline.
- Upstream mocking with `respx`; **use the decorator form** (`@respx.mock`) — the context-manager form is broken in respx 0.23 + httpx 0.28.
- Property-based round-trip tests use `hypothesis` (persistent cache in `.hypothesis/`) — the right tool for codec/adapter invariants.
- Default master key for admin-auth'd tests: `sk-wiwi-master-test` via `Authorization: Bearer …`.
- New bug fixes go into the next unused numbered regression file (`test_fix_roundN.py`) rather than topic files. Find the next number with `ls tests/test_fix_round*.py` — never assume one (rounds 2–26 exist; round 1 is missing; `test_bugfix_round5.py` is a legacy filename).
- Run full pytest + ruff before claiming work done or committing — both green at commit time.

## Conventions & guardrails

- Ruff only (`line-length = 100`, target `py311`, ignore `EXE002` only). Pydantic v2 for config and admin schemas; plain `@dataclass(frozen=True)` for IR / streaming hot paths. No mypy/pyright on the Python side; `web/` is `strict` TypeScript checked by `tsc -b` during build.
- Async throughout (`httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths). Never `print` from library code — use `structlog`.
- Naming: wire modules named after dialect (`openai_chat.py`); adapters `<provider>_adapter.py`; tests `test_<area>.py`. **Bun** is authoritative for `web/` (not npm).
- Database: SQLite default, Postgres via `DATABASE_URL`. Schema is created with inline `CREATE TABLE IF NOT EXISTS` at startup — **no Alembic**; there are no migrations to write.
- Virtual keys are SHA-256-hashed at rest with constant-time compare; provider keys enter via `os.environ/NAME` interpolation in config.
- **Two different cache-hit flags — do not conflate.** `cache_hit` = provider prompt-cache hit (feeds `wiwi_prompt_cache_hits_total`); `response_cache_hit` = served from wiwi's own exact-match cache (`wiwi/cache/`, `LogEvent.response_cache_hit`). A response-cache hit must leave `cache_hit=False` or prompt-cache metrics inflate. The response cache never stores streaming requests or requests with builtin tools.
- **Stream journals are ON by default** (`stream_journal_enabled: true`, dir `.wiwi/journals`, 600s TTL, 1 MiB/journal cap): encoded SSE frames persist per-request so a client reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replays even after a wiwi restart.
- **Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, `opencode.json(c)`, anything under `.verify/` or `.wiwi/`, or `*.har`** — they hold live provider keys and runtime state (all gitignored). Master key comes from `WIWI_MASTER_KEY`.
- Never add dialect- or provider-specific branches in `core/`, `router/`, `auth/`, or `streaming/` — dialect logic belongs in `wire/`, provider logic in `providers/`.
- Admin API endpoints (`/admin/*`) require the master key.
- Commits: imperative present tense, capitalized, no prefix tags (e.g. `Add auth keys and service`). One logical change per commit.

## Docs vs. code

`docs/ARCHITECTURE.md` and `docs/CORE.md` are design specifications that intentionally run ahead of the implementation (handler pipeline, DeltaBus, reasoning subsystems, a deeper DB schema, Postgres/Redis backends are specified but not yet built; their repo-layout sections show planned directories like `wire/openai_chat/` that are actually flat files). When docs and code disagree, trust the code — or treat the doc section as the spec for work you're about to do. `docs/MVP.md` tracks scope gaps; `docs/PLAN.md` tracks build phases; `docs/ADMIN.md` documents the admin UI/API design.

## UPDATE.md — changelog for translation work

`UPDATE.md` is the changelog for all OpenAI ↔ Anthropic cross-provider translation fixes, the OpenRouter adapter, and multi-turn conversation fixes. **Read it first** when encountering issues with: reasoning/thinking parameter translation, tool_result message handling, `content: null` errors, OpenRouter `reasoning` parameter, `stream_options`, or error message extraction from upstream providers. It documents every fix with before/after code snippets, the files changed, and the tests that cover them.

## AUDIT.md — known-bug register

`AUDIT.md` enumerates bugs verified by source-reading, with severity, file:line citations, and a one-line fix sketch. Read it before starting any bugfix work so you don't redo an already-known fix or miss a related one.

## Project rules & skills

- For bug fixes, follow the workflow in `.claude/rules/wiwi-bugfix-workflow.md` (TDD via `.claude/skills/test-driven-development`, root-cause via `.claude/skills/systematic-debugging`, review via `.claude/skills/requesting-code-review`). New bugfix tests go into the next `test_fix_roundN.py` file (see Testing above for how to find the next number).
- Superpowers skills (`/test-driven-development`, `/systematic-debugging`, `/brainstorming`, `/writing-plans`, `/executing-plans`, `/verification-before-completion`, `/using-git-worktrees`) are the methodology for plan → TDD → debug → review → verify.
- ECC skills are the domain library (Python/FastAPI/React/agent orchestration, security scan, etc.); invoke the matching skill for the task.
