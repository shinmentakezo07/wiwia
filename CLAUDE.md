# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**wiwi** is a self-hosted unified LLM gateway proxy (LiteLLM-shaped): three inbound API dialects — OpenAI Chat, OpenAI Responses (Codex CLI), Anthropic Messages — all route through one canonical internal representation (IR) to any of eleven outbound provider types; responses are always re-encoded in the caller's inbound dialect (e.g. Claude Code backed by GPT). Adds virtual keys, budgets, rate limits, key pools with smooth weighted round-robin, retries/cooldowns/fallbacks, a health healer, cost tracking, request logs, and an admin web UI.

**Use the ambient `python3` (3.12) / `pytest` (9.1.1) / `ruff` (0.16.4) on PATH** — they have the project installed (`import wiwi` resolves to this checkout). There is **no usable `.venv`**: the directory does not exist in this checkout, and the docs that describe it as an empty uv-venv symlink are stale either way. Never invoke `.venv/bin/python`.

`AGENTS.md` is the OpenCode-facing guide and claims canonical status for OpenCode work; it is accurate and slightly more current than this file in places. Where the two disagree with the code, trust the code.

## Commands

```bash
# setup — only needed on a fresh machine; this checkout is already installed
uv pip install -e ".[dev]"               # + ".[redis]" for the Redis response-cache backend
                                         # (asyncpg is a core dep — Postgres needs no extra)

# run server (default 0.0.0.0:4000; needs wiwi.yaml — cp wiwi.yaml.example wiwi.yaml)
wiwi --config wiwi.yaml [--host H] [--port P]
wiwi --reload --reload-dir wiwi          # dev: uvicorn reload, re-imports the app in a subprocess
DATABASE_URL=postgresql+asyncpg://... wiwi --config wiwi.yaml   # Postgres (default SQLite at wiwi.db)
# or directly: uvicorn wiwi.server.app:create_app_from_config_path --factory

# tests
python3 -m pytest tests/ -q                                 # full suite — verify green; don't trust a pinned pass-count
python3 -m pytest tests/test_codecs.py -q                   # single file
python3 -m pytest tests/test_router.py -k cooling           # -k matches test-name substrings
python3 -m pytest tests/test_integration.py::test_chat_completion_happy_path -q   # single test

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
python3 bench.py -n 10 -c 1,4,16 --max-tokens 100
```

**Bun is authoritative for `web/`; npm is not.** `web/package-lock.json` and `start.sh` are legacy npm paths — don't mix package managers in one session.

There is **no CI and no pre-commit config**. The manual `pytest` + `ruff` gate is binding: run both, both green, before claiming work done or committing.

Config precedence: `--config`/`-c` flag > `WIWI_CONFIG` env (inline YAML) > `wiwi.yaml`. `load_env()` (python-dotenv, `override=False`) runs before config parse, so real environment variables win.

## Architecture

### Hub-and-spoke translation (the core idea)

No pairwise converters. Every direction goes dialect → IR → provider:

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

Adding an inbound surface = one new module in `wiwi/wire/`; adding a provider = one new adapter in `wiwi/providers/` + a branch in `registry.get_adapter()` (or a documented entry in `_OPENAI_WIRE_TYPES`). Core code (`core/gateway.py`) never branches on dialect or provider name. Request flow end-to-end is best traced through `server/app.py:run_chat_like` (decode → auth → rate limit → journal replay check → router retries/fallbacks → gateway complete/stream) and back out through the wire encoders; `core/context.py:RequestContext` (63 lines) is the single mutable holder threaded through all of it.

Inbound surfaces: `POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/messages`, `POST /v1/messages/count_tokens`; plus `GET /v1/models`, `GET /public/models`, `GET /health`, a configurable Prometheus metrics path (`server/metrics.py`), `/admin/*` (master key), `/auth/*` (user sessions), `/cline/oauth/callback`.

`PROVIDER_TYPES` (`wiwi/config.py`) is the outbound-type source of truth — eleven: `openai`, `anthropic`, `gemini`, `openai-compatible`, `openrouter`, `gmicloud`, `bai`, `nvidia-nim`, `cline`, `workbuddy`, `opencode`.

### `wiwi/` layout

| Path | Role |
|---|---|
| `wire/` | Inbound codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` — each owns `decode_request`, `encode_response`, a `StreamEncoder`, and `error_body` |
| `providers/` | Outbound adapters: openai, anthropic, gemini, openrouter, nim, cline, bai, workbuddy, opencode (+ `cline_oauth.py`/`cline_auto_refresh.py`/`cline_version.py`, `workbuddy_auth.py`/`workbuddy_auto_refresh.py`, `opencode_version.py`, `nim_tool_schema.py`, `nim_native_tools.py`), plus `base.py` + `registry.py` |
| `core/` | Engine: `gateway.py` (~1294 lines), `context.py` (RequestContext), `recovery.py` (Backoff, CircuitBreaker, ProbeVerdict, HealthHealer) |
| `ir/` | Internal representation (`types.py`, `builtin_tools.py`) |
| `streaming/` | `IRStreamDelta` taxonomy (98 lines) + `sse.py` / `coalesce.py` / `resume.py` / `tape_store.py` (durable journals) / `partial_json.py` / `loopdetect.py` / `validation.py` — the contract between adapters and encoders |
| `router/` | Key pools, weighted round-robin, retries, cooldowns, fallbacks, probation |
| `auth/` | Virtual keys, budgets, users (`keys.py`, `service.py`, `users.py`) |
| `ratelimit/` | Sliding-window rpm/tpm (memory default + redis) |
| `cache/` | Opt-in exact-match response cache (`CacheSettings`, off by default) |
| `cost/` | Token/cost calculation (`pricing.py`) |
| `logging_core/` | Three-stream logger: request (DB+SSE), proxy (stdout+SSE), audit (sync DB) |
| `server/` | FastAPI app (`app.py`, ~4k lines), admin API, `stats.py` rollups, `metrics.py` (Prometheus), `config_store.py`, static SPA serving |
| `web/` | Admin UI + public site (React 19 + TypeScript + Vite 6 + Tailwind 4), 53 page components in `src/pages/` |
| `tests/` | Pytest suite — 79 files: thematic regressions plus numbered `test_fix_roundN.py` |
| `docs/` | Design specs (intentionally run ahead of implementation) + `docs/superpowers/{plans,specs}` |

Two adapters carry provider-specific quirks that live in `providers/` (never in `core/`):

- **NVIDIA NIM** (`nim_adapter.py` + `nim_tool_schema.py` + `nim_native_tools.py`) — NIM is vLLM-backed and rejects JSON Schema boolean subschemas (`"additionalProperties": true`) and parameters named `type`, which collide with the schema keyword inside vLLM's tool parser. `nim_tool_schema.py` strips the former and aliases the latter to `_nim_arg_<name>`, keeping a mapping so agent-facing names are restored on the way back.
- **Cline** (`cline_adapter.py` + `cline_oauth.py` + `cline_auto_refresh.py`) and **WorkBuddy** (`workbuddy_adapter.py` + `workbuddy_auth.py` + `workbuddy_auto_refresh.py`) — OAuth-based; tokens refresh on demand, which is what makes their requests survive a 401.

`registry.get_adapter()` dispatches on provider type, and an import-time `assert` at the bottom of `registry.py` fails loudly if a type is added to `PROVIDER_TYPES` without a matching branch or `_OPENAI_WIRE_TYPES` entry — so adding a provider is safe-by-construction. Note the assert catches *missing* branches, not *misplaced* branching.

**Adapter ownership matters.** Adapters accumulate per-stream decode state across awaits (open tool indices, name fragments, deferred tool opens, NIM aliases), so `fresh_adapter(type)` returns a *private* instance and is what the request hot path must use; `get_adapter(type)` returns a shared singleton that is `reset()` on every hand-out and is only safe for synchronous, non-await-held use. Using the shared one on the hot path lets a concurrent request wipe an in-flight stream's state.

**`core/recovery.py`** holds the resilience primitives: `Backoff` (shared by the router's retry sleep — see the note at `router.py` about keeping the math in sync with `tests/test_recovery.py`), `CircuitBreaker` (shared by the Cline/WorkBuddy token refreshers), `ProbeVerdict`/`parse_retry_after` (shared upstream-error classification), and `HealthHealer` — a background sweeper that probes cooling/invalid keys and cooled deployments with a 1-token completion and restores them early into a *probation* state (reduced WRR weight until it graduates). The healer is **off by default** (`healer.enabled`) because probes spend real provider money — same opt-in ethos as the response cache. It is wired into the app lifespan in `server/app.py`.

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

## Admin web UI

- Source lives in `web/`; production build output lands in `wiwi/server/static/` and the SPA is mounted at **`/`** with history fallback (`SPAStaticFiles`), *after* all API routes so `/admin/*`, `/v1/*`, `/auth/*`, `/public/*`, `/health` still return JSON. Older references to `/admin/ui` are stale. The current admin console is **`/console`**; `/app/*` is a legacy redirect. Built bundles are gitignored.
- Dev: `cd web && bun install && bun run dev` (Vite dev server proxies to a running gateway); ship: `bun run build`.
- Backend rollups live in `wiwi/server/stats.py` (pure functions over LogEvent lists — unit-testable without DB).
- `web/src/pages/` mixes console pages (Dashboard, Providers, VirtualKeys, RequestLogs, …) with public marketing, user, and documentation pages (Landing, Pricing, Blog, Docs, `docs/*`, …) — **determine ownership from `web/src/main.tsx` route guards, not from the directory**. TypeScript is `strict` with `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`; the `@/*` path alias maps to `src/`.

## Config

Single `wiwi.yaml` (LiteLLM-shaped): `providers:` (named accounts, each with a pool of keyed entries), `model_list:` (`model_name` clients request → `wiwi_params` with provider account + native model id), `router_settings:` (strategy/retries/cooldowns/fallbacks/aliases), `general_settings:` (master_key, database_url, redis_url, max_keys_per_user, trusted_proxies), `wiwi_settings:` (drop_params, host/port, header allowlist, stream journal, cache), `healer:`. Any string value may be `os.environ/NAME`; missing vars interpolate to `""` and validation drops providers whose resolved keys are empty.

Startup **fails closed** unless `WIWI_SESSION_SECRET` or `general_settings.master_key` is set; the session secret otherwise derives from the master key. `DATABASE_URL` > configured database URL > `sqlite+aiosqlite:///wiwi.db`. YAML loads first; DB-stored providers/keys/deployments/aliases/settings layer over it, skipping same-named YAML entries.

Error bodies are dialect-correct per surface (OpenAI `{"error":{…}}` vs Anthropic `{"type":"error",…}`) — produced by the wire codecs' `error_body`.

## Testing

- `pytest` + `pytest-asyncio` with `asyncio_mode = "auto"` — write bare `async def test_…`, no `@pytest.mark.asyncio` decorator.
- **No `conftest.py` anywhere** — each test file builds its own `_config()` factory and its own `LifespanManager + httpx.ASGITransport` client fixture inline.
- Upstream mocking with `respx`; **use the decorator form** (`@respx.mock`) — the context-manager form is broken in respx 0.23 + httpx 0.28.
- Property-based round-trip tests use `hypothesis` (persistent cache in `.hypothesis/`) — the right tool for codec/adapter invariants.
- Default master key for admin-auth'd tests: `sk-wiwi-master-test` via `Authorization: Bearer …`.
- New bug fixes go into the next unused numbered regression file (`test_fix_roundN.py`) rather than topic files. Find the next number with `ls tests/test_fix_round*.py` — **never assume one** (rounds 2–43 exist; round 1 is missing; `test_bugfix_round5.py` is a legacy filename).
- Run full pytest + ruff before claiming work done or committing — both green at commit time.

## Conventions & guardrails

- Ruff only (`line-length = 100`, target `py311`, ignore `EXE002` only). Pydantic v2 for config and admin schemas; plain `@dataclass(frozen=True)` for IR / streaming hot paths. No mypy/pyright on the Python side; `web/` is `strict` TypeScript checked by `tsc -b` during build.
- Async throughout (`httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths). Never `print` from library code — use `structlog`.
- Naming: wire modules named after dialect (`openai_chat.py`); adapters `<provider>_adapter.py`; tests `test_<area>.py`. **Bun** is authoritative for `web/` (not npm).
- Database: SQLite default, Postgres via `DATABASE_URL`. Schema is created with inline `CREATE TABLE IF NOT EXISTS` at startup — **no Alembic**; there are no migrations to write.
- Virtual keys are SHA-256-hashed at rest with constant-time compare; provider keys enter via `os.environ/NAME` interpolation in config.
- **Two different cache-hit flags — do not conflate.** `cache_hit` = provider prompt-cache hit (feeds `wiwi_prompt_cache_hits_total`); `response_cache_hit` = served from wiwi's own exact-match cache (`wiwi/cache/`, `LogEvent.response_cache_hit`). A response-cache hit must leave `cache_hit=False` or prompt-cache metrics inflate. The response cache never stores streaming requests or requests with builtin tools.
- **Redis currently backs only the response cache** (`build_response_cache` picks Redis when `general_settings.redis_url`/`REDIS_URL` is set and the `redis` extra imports; otherwise memory). `wiwi/ratelimit/redis.py:RedisRateLimiter` exists but is **not wired into `AppState`** — production rate limiting uses `wiwi/ratelimit/memory.py`.
- **Stream journals are ON by default** (`stream_journal_enabled: true`, dir `.wiwi/journals`, 600s TTL, 1 MiB/journal cap): encoded SSE frames persist per-request so a client reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replays even after a wiwi restart. Journals are key-scoped — readable only by the virtual key that created them.
- **Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, `opencode.json(c)`, anything under `.verify/` or `.wiwi/`, or `*.har`** — they hold live provider keys and runtime state (all gitignored). Master key comes from `WIWI_MASTER_KEY`.
- Never add dialect- or provider-specific branches in `core/`, `router/`, `auth/`, or `streaming/` — dialect logic belongs in `wire/`, provider logic in `providers/`.
- Admin API endpoints (`/admin/*`) require the master key.
- Commits: imperative present tense, capitalized, no prefix tags (e.g. `Add auth keys and service`). One logical change per commit.

## Import Rules (binding)

1. **No dialect or provider imports outside `wiwi/wire/` and `wiwi/providers/`.** `core/`, `router/`, `auth/`, `streaming/`, `cache/`, `cost/`, `logging_core/`, and `ir/` must never import symbols from `wiwi.wire` or `wiwi.providers`. Violating this leaks dialect/provider branching into modules that must stay generic. The registry's import-time assert will not catch this — it catches missing branches, not out-of-place ones.
2. **Import from the module that owns the symbol, not from a re-export layer.** If `wiwi.foo` re-exports `Bar` from `wiwi.foo.internal`, import `Bar` from `wiwi.foo`, not from `wiwi.foo.internal`. Re-export layers exist for a reason; bypassing them couples callers to internal layout.
3. **Never add a new top-level import path without updating `registry.py`'s coverage assert.** Adding a provider type or an inbound wire dialect without the corresponding branch in `get_adapter()` or the matching wire module will be caught at import time — but only if the assert is kept honest. Any new entry in `PROVIDER_TYPES` or any new inbound route must have its branch.
4. **Prefer existing module APIs over inventing new ones.** If a helper already exists in the owning module, use it. Do not create a parallel utility with the same job under a different name. "Second convention beside existing is prohibited."
5. **Run `lsp references` on any exported symbol before editing or removing it.** An exported symbol (a public function, class, or constant reachable from outside its module) may have unknown callers. Editing it without checking references is how regressions ship.
6. **Imports in `web/` follow TypeScript module resolution.** Do not mix relative paths arbitrarily — prefer the `@/*` alias configured in `tsconfig.json` / Vite config, consistent with the existing pattern in `web/src/`. Do not add bare `../../../../` chains; if the depth feels wrong, the module boundary probably is.
7. **Never import `wiwi.server.app` at module level in a library module.** `app.py` is the FastAPI application factory and its import can trigger lifespan / startup side effects. Library code (`core/`, `router/`, `auth/`, etc.) must not import it; only the CLI (`wiwi/main.py`) and test fixtures do.

## UI/UX Universal Compatibility Rule (binding)

Every UI or UX change — in `web/` or in any admin-facing HTML/template surface — MUST be verified as usable on both **desktop (mouse/keyboard)** and **mobile (touch, narrow viewport)** before being marked done. Specifically:

1. **Layout must not break below ~375 px viewport width.** Test at 375×812 (iPhone SE class) and at a wide desktop viewport. Sidebars, tables, and cards that assume a minimum width must reflow, stack, or collapse — not clip or overflow.
2. **All interactive controls must be operable by touch.** Tap targets must be at least 44×44 px (Apple HIG) or 48×48 dp (Material). Controls that only respond to hover (CSS `:hover`-only reveals, hover-dependent dropdowns) must also respond to focus and touch. No information or action may be hover-only.
3. **Keyboard and focus navigation must work.** Every focusable element must be reachable via Tab/Shift-Tab in a sensible order. Focus styles must be visible (do not suppress `outline` without providing an equivalent). Modal/dialog focus trapping and Escape-to-close must work on desktop.
4. **Text must be legible and not rely on fixed sizes.** Use relative units (`rem`, `em`, `%`, `vw`) over fixed `px` for typography and spacing where appropriate. Text in containers must not truncate silently in a way that hides information on narrow screens.
5. **Responsive is not optional for admin pages.** Admin pages in `web/src/pages/` are used on desktop but may be opened on a phone (e.g., a quick key rotation or budget check). Assume a mobile viewport is possible for every page; do not gate responsiveness behind a "this is an admin page" assumption.
6. **Verify before claiming done.** For any UI/UX change, open the page in a mobile-sized viewport (browser devtools device mode or a real device) and a desktop viewport, and confirm the change works in both. Screenshots or a short description of the verification belong in the commit message or PR description.

## Where to start reading

`server/app.py` is ~4k lines and `core/gateway.py` ~1294 — don't read either top to bottom. For a request's full path, start at `run_chat_like` in `server/app.py` (and `create_app`/`AppState` above it) and follow the pipeline it names; `RequestContext` (`core/context.py`, 63 lines) is the single mutable object threaded through every stage, so reading its fields tells you what the pipeline carries. For streaming, read `streaming/deltas.py` (98 lines, the whole contract) before any adapter. `docs/QUICKSTART.md`, `docs/API_REFERENCE.md`, `docs/CONFIG.md`, `docs/PROVIDERS.md`, and `docs/STREAMING.md` are the practical companion docs; `detailed.md` is a code-derived technical reference.

## Docs vs. code

`docs/ARCHITECTURE.md` and `docs/CORE.md` are design specifications that intentionally run ahead of the implementation (handler pipeline, DeltaBus, reasoning subsystems, a deeper DB schema are specified but not yet built; their repo-layout sections show planned directories like `wire/openai_chat/` that are actually flat files). When docs and code disagree, trust the code — or treat the doc section as the spec for work you're about to do. `docs/MVP.md` tracks scope gaps; `docs/PLAN.md` tracks build phases; `docs/ADMIN.md` documents the admin UI/API design.

## UPDATE.md — changelog for translation work

`UPDATE.md` is the **binding changelog** for all OpenAI ↔ Anthropic cross-provider translation fixes, the OpenRouter adapter, and multi-turn conversation fixes. Any agent touching the translation layer — `wiwi/wire/{openai_chat,openai_responses,anthropic_messages}.py`, `wiwi/providers/{openai,anthropic,openrouter}_adapter.py`, or any code handling `reasoning_effort`/`reasoning` mapping, `tool_result` messages, `content: null`, `stream_options`, or upstream error extraction — **must read UPDATE.md first** and follow the invariants it records. It documents every fix with before/after code snippets, the files changed, and the tests that cover them. When extending one of those areas, check UPDATE.md for the existing fix before writing new code; when a new fix lands in one of those areas, add an entry to UPDATE.md so a later agent does not rediscover and re-fix the same thing.

## AUDIT.md — known-bug register

`AUDIT.md` enumerates bugs verified by source-reading, with severity, file:line citations, and a one-line fix sketch. Read it before starting any bugfix work so you don't redo an already-known fix or miss a related one.

## Error discovery & fix-reporting rule (binding)

When an agent discovers a bug, error, or suspicious behavior in this codebase:

1. **Report it in `AUDIT.md` before or alongside fixing it.** Do not silently fix a bug and leave no trace. Any agent that finds a real defect must add an entry to `AUDIT.md` using the existing format: a severity badge (`🔴`/`🟠`/`🟡`/`⚪`), a short title, the exact file:line citations, a trigger description, and a one-line fix sketch. If the bug is already in `AUDIT.md`, skip to step 2.

2. **When fixing a bug that is already in `AUDIT.md`, mark it as fixed in place.** Update the entry to record that it is resolved — add a `**Status: fixed**` line and the commit or description that fixed it, or move it into the `## ✅ Fixed` section at the top of the file with the original finding preserved. Never delete a finding without a trace. The goal is that a fresh agent opening `AUDIT.md` can tell at a glance which bugs are still live and which have already been resolved, so they do not waste time re-fixing or re-investigating something that is done.

3. **Link the fix to its AUDIT.md entry.** When a fix lands for a bug that has an `AUDIT.md` entry, the commit message or PR description must reference the entry number or title so the connection is recoverable. If the fix introduces a new regression test in a `test_fix_roundN.py` file, mention that test in the `AUDIT.md` entry as well.

4. **Do not re-report the same bug.** Before adding a new entry to `AUDIT.md`, search it for the same file:line or same symptom. Duplicate entries confuse future agents and inflate the "live bugs" count.

5. **Read `AUDIT.md` at the start of every bugfix session.** If a previous session already discovered and reported the issue you are about to investigate, you should see it there. Starting from `AUDIT.md` avoids duplicate work and makes it obvious which fixes are still pending.

## Project rules & skills

- For bug fixes, follow the workflow in `.claude/rules/wiwi-bugfix-workflow.md` (TDD via `.claude/skills/test-driven-development`, root-cause via `.claude/skills/systematic-debugging`, review via `.claude/skills/requesting-code-review`). New bugfix tests go into the next `test_fix_roundN.py` file (see Testing above for how to find the next number).
- Superpowers skills (`/test-driven-development`, `/systematic-debugging`, `/brainstorming`, `/writing-plans`, `/executing-plans`, `/verification-before-completion`, `/using-git-worktrees`) are the methodology for plan → TDD → debug → review → verify.
- ECC skills are the domain library (Python/FastAPI/React/agent orchestration, security scan, etc.); invoke the matching skill for the task.
