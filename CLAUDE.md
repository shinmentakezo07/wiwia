# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

wiwi is a self-hosted unified LLM gateway proxy (LiteLLM-style). Three inbound API dialects — OpenAI Chat Completions, OpenAI Responses (Codex CLI), Anthropic Messages (Claude Code) — all route through one canonical internal representation (IR) to any outbound provider (OpenAI, Anthropic, Gemini, any OpenAI-compatible URL). Responses are always re-encoded in the caller's inbound dialect, so e.g. Claude Code can be backed by GPT. Adds virtual keys, budgets, rate limits, key pools with smooth weighted round-robin, retries/cooldowns/fallbacks, cost tracking, request logs, and an admin web UI.

The venv lives at `.venv` (a symlink) — use `.venv/bin/python`, `.venv/bin/ruff` directly. The admin UI (`web/`) is React 19 + TypeScript + Vite + Tailwind 4, built with bun (not npm).

## Commands

```bash
# setup
uv venv && uv pip install -e ".[dev]"

# run server (default 0.0.0.0:4000; needs wiwi.yaml — cp wiwi.yaml.example wiwi.yaml)
wiwi --config wiwi.yaml [--host H] [--port P]
DATABASE_URL=postgresql+asyncpg://... wiwi --config wiwi.yaml   # Postgres (default is SQLite at wiwi.db)

# tests
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_codecs.py -q            # single file
.venv/bin/python -m pytest tests/test_router.py -k cooldown   # single test by name

# lint (ruff, line-length 100, target py311)
.venv/bin/ruff check wiwi/ tests/

# admin UI
cd web && bun install && bun run dev     # dev server (proxies to running gateway)
cd web && bun run build                  # tsc -b && vite build → wiwi/server/static/
cd web && bun run lint                   # eslint src (web/ is NOT covered by ruff)
./start.sh                               # backend (:4000) + Vite (:5173) together, prefixed logs

# docker
docker compose up --build
docker compose --profile pg up --build   # with Postgres instead of SQLite
```

## Architecture

### Hub-and-spoke translation (the core idea)

No pairwise converters. Every direction goes dialect → IR → provider:

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

Adding an inbound surface = one new module in `wiwi/wire/`; adding a provider = one new adapter in `wiwi/providers/` + a line in `registry.get_adapter()`. Core code (`core/gateway.py`) never branches on dialect or provider name. Request flow end-to-end is best traced through `server/app.py:run_chat_like` (decode → auth → rate limit → router retries/fallbacks → gateway complete/stream) and back out through the wire encoders; `core/context.py:RequestContext` is the single mutable holder threaded through all of it.

### `wiwi/` layout

| Path | Role |
|---|---|
| `wire/` | Inbound codecs (OpenAI Chat, OpenAI Responses/Codex, Anthropic Messages) |
| `providers/` | Outbound adapters (`<provider>_adapter.py`) — openai, anthropic, gemini, openrouter, nim, cline (+ `cline_oauth.py`, `cline_auto_refresh.py`), plus `base.py` + `registry.py` |
| `core/` | Engine: gateway, request context |
| `ir/` | Internal representation (canonical request/response types in `types.py`) |
| `streaming/` | `IRStreamDelta` taxonomy + `sse.py` / `coalesce.py` / `resume.py` / `partial_json.py` / `validation.py` — the contract between adapters and encoders |
| `router/` | Key pools, weighted round-robin, retries, cooldowns, fallbacks |
| `auth/` | Virtual keys, budgets, rate limits (`keys.py`, `service.py`, `users.py`) |
| `ratelimit/` | Rate-limit enforcement (memory + redis backends) |
| `cost/` | Token/cost calculation (`pricing.py`, `model_prices.json`) |
| `logging_core/` | Structured logging (structlog) + DB sink + event taxonomy |
| `server/` | FastAPI app, admin API, stats rollups, static SPA serving |
| `web/` | Admin UI (React 19 + TypeScript + Vite + Tailwind 4) |
| `tests/` | Pytest suite — thematic regression files (`test_audit_fixes.py`, `test_fix_roundN.py`, …) for bugfixes |
| `docs/` | Design specs (intentionally run ahead of implementation) |

Two adapters carry provider-specific quirks that live in `providers/` (never in `core/`):

- **NVIDIA NIM** (`nim_adapter.py` + `nim_tool_schema.py` + `nim_native_tools.py`) — NIM is vLLM-backed and rejects JSON Schema boolean subschemas (`"additionalProperties": true`) and parameters named `type`, which collide with the schema keyword inside vLLM's tool parser. `nim_tool_schema.py` strips the former and aliases the latter to `_nim_arg_<name>`, keeping a mapping so agent-facing names are restored on the way back.
- **Cline** (`cline_adapter.py` + `cline_oauth.py` + `cline_auto_refresh.py`) — OAuth-based; tokens refresh on demand, which is what makes Cline-backed requests survive a 401.

`registry.get_adapter()` dispatches on provider type, and an import-time `assert` at the bottom of `registry.py` fails loudly if a type is added to `PROVIDER_TYPES` without a matching branch — so adding a provider is safe-by-construction: new adapter + one branch, and the assert catches the case where you forget the branch.

### Non-negotiable streaming contract

`streaming/deltas.py` defines the `IRStreamDelta` taxonomy — the contract between adapters and encoders: exactly one `StreamStart`; `ToolCallOpen→ArgsDelta*→Close` nested per index; exactly one `UsageFinal` after last content delta; then `Finish`; then `StreamEnd` xor `StreamError`. Adapters guarantee legality; encoders never defend against malformed sequences.

Note the one asymmetry in that contract: `StreamError` may terminate at **any** point, replacing everything after the last emitted delta. It is the abnormal-path terminal and needs no preceding `Finish`.

### Admin web UI

- Source lives in `web/`; production build output lands in `wiwi/server/static/` and is served by `app.py` under `/admin/ui` (SPA history fallback).
- Dev: `cd web && bun install && bun run dev` (Vite dev server proxies to a running gateway); ship: `bun run build`.
- Backend rollups live in `wiwi/server/stats.py` (pure functions over LogEvent lists — unit-testable without DB).

## Where to start reading

`server/app.py` is ~2.5k lines and `core/gateway.py` ~900 — don't read either top to bottom. For a request's full path, start at `run_chat_like` (`server/app.py:576`) and follow the pipeline it names; `RequestContext` (`core/context.py`) is the single mutable object threaded through every stage, so reading its fields tells you what the pipeline carries. For streaming, read `streaming/deltas.py` (87 lines, the whole contract) before any adapter.

## Config

Single `wiwi.yaml` (LiteLLM-shaped): `providers:` (named accounts, each with a pool of keyed entries), `model_list:` (`model_name` clients request → `wiwi_params` with provider account + native model id), `router_settings:` (strategy/retries/cooldowns/fallbacks/aliases), `general_settings:` (master_key, database_url), `wiwi_settings:` (drop_params, host/port, header allowlist). Any string value may be `os.environ/NAME`.

Error bodies are dialect-correct per surface (OpenAI `{"error":{…}}` vs Anthropic `{"type":"error",…}`) — produced by the wire codecs' `error_body`.

## Testing

- `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"` — write bare `async def test_…`, no `@pytest.mark.asyncio` decorator.
- Upstream mocking with `respx`; app-level tests via `asgi-lifespan` + `httpx.ASGITransport` (see `tests/test_integration.py`).
- Property-based round-trip tests use `hypothesis` (see `tests/test_property_roundtrip.py`) — the right tool for codec/adapter invariants.
- New bug fixes go alongside the existing thematic regression files (`test_audit_fixes.py`, `test_fix_round2.py`, … through `test_fix_round8.py`) rather than scattered into topic files. **Next unused number is `test_fix_round9.py`.**
- Run full pytest + ruff before claiming work done or committing. Both are currently green (763 tests collected).

## Conventions & guardrails

- Ruff only (`line-length = 100`, target `py311`). Pydantic v2 for config; plain dataclasses for IR / streaming hot paths.
- Async throughout (`httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths). Never `print` from library code — use `structlog`.
- Naming: wire modules named after dialect (`openai_chat.py`); adapters `<provider>_adapter.py`; tests `test_<area>.py`. UI: one routed page per admin concern in `web/src/pages/*.tsx`. **Bun** is authoritative for `web/` (not npm).
- Virtual keys are SHA-256-hashed at rest with constant-time compare; provider keys enter via `os.environ/NAME` interpolation in config.
- **Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, or anything under `.verify/`** — they hold live provider keys and runtime state (all gitignored). Master key comes from `WIWI_MASTER_KEY`.
- Never add dialect- or provider-specific branches in `core/`, `router/`, or `auth/` — dialect logic belongs in `wire/`, provider logic in `providers/`.
- Admin API endpoints (`/admin/*`) require the master key.
- Commits: imperative present tense, capitalized, no prefix tags (e.g. `Add auth keys and service`). One logical change per commit.

## Docs vs. code

`docs/ARCHITECTURE.md` and `docs/CORE.md` are design specifications that intentionally run ahead of the implementation (handler pipeline, DeltaBus, reasoning/cache subsystems, DB schema, Postgres/Redis backends are specified but not yet built; their repo-layout sections show planned directories like `wire/openai_chat/` that are actually flat files). When docs and code disagree, trust the code — or treat the doc section as the spec for work you're about to do. `docs/MVP.md` tracks scope gaps; `docs/PLAN.md` tracks build phases; `docs/ADMIN.md` documents the admin UI/API design.

## UPDATE.md — changelog for translation work

`UPDATE.md` is the changelog for all OpenAI ↔ Anthropic cross-provider translation fixes, the OpenRouter adapter, and multi-turn conversation fixes. **Read it first** when encountering issues with: reasoning/thinking parameter translation, tool_result message handling, `content: null` errors, OpenRouter `reasoning` parameter, `stream_options`, or error message extraction from upstream providers. It documents every fix with before/after code snippets, the files changed, and the tests that cover them.

## Project rules & skills

- For bug fixes, follow the workflow in `.claude/rules/wiwi-bugfix-workflow.md` (TDD via `.claude/skills/test-driven-development`, root-cause via `.claude/skills/systematic-debugging`, review via `.claude/skills/requesting-code-review`). New bugfix tests go into the next thematic file `test_fix_roundN.py` — **next unused is `test_fix_round9.py`** (`test_fix_round8.py` exists and the rule file's "round6" pointer is stale).
- Superpowers skills (`/test-driven-development`, `/systematic-debugging`, `/brainstorming`, `/writing-plans`, `/executing-plans`, `/verification-before-completion`, `/using-git-worktrees`) are the methodology for plan → TDD → debug → review → verify.
- ECC skills are the domain library (Python/FastAPI/React/agent orchestration, security scan, etc.); invoke the matching skill for the task.
