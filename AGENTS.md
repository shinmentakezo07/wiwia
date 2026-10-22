# Repository Guidelines

Agent guide for **wiwi** — a self-hosted unified LLM gateway proxy (FastAPI; `requires-python
>=3.11`, ambient interpreter is 3.13).
Three inbound API dialects route through one canonical internal representation (IR) to twelve
outbound provider types; responses are always re-encoded in the caller's inbound dialect. Two
further inbound routes reuse that same pipeline rather than adding a fourth dialect: legacy
`POST /v1/completions` (bare `prompt` in, `choices[].text` out) and
`POST /v1/playground/completions` (the Chat pipeline admitted by the `wiwi_session` cookie
instead of a bearer key).

## Project Overview

**Purpose:** expose all three wire formats — OpenAI Chat (`/v1/chat/completions`), OpenAI
Responses (`/v1/responses`, Codex CLI), Anthropic Messages (`/v1/messages`, Claude Code) —
translating any-to-any through a neutral IR, so any client works against any backend model
(e.g. Claude Code backed by GPT and never knowing).

Beyond translation it provides: virtual keys + budgets, rate limits, key pools with smooth
weighted round-robin, retries/cooldowns/fallbacks, a health healer, cost tracking, request
logs, metrics, and a React admin SPA.

**Design rule:** no pairwise converters. Adding an inbound surface = one module in
`wiwi/wire/`. Adding a provider = one adapter in `wiwi/providers/` + one branch in
`registry.get_adapter()`. `core/` never branches on a dialect or provider name.

## Architecture & Data Flow

Hub-and-spoke translation:

```
wire codec (inbound) ─decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ── IRStreamDelta / AssistantTurn ◄── adapter.decode ── provider
```

Request life cycle (trace point: `wiwi/server/app.py:run_chat_like`):

1. Inbound codec decodes the dialect body into `wiwi.ir.Request` (`wire/*.py:decode_request`).
2. Auth + rate limit + budget admission (`auth/service.py`, `ratelimit/`).
3. Stream journal replay check (`streaming/tape_store.py`).
4. `router/router.py` resolves the model group/alias, picks a deployment + key, and drives
   retries/cooldowns/fallbacks (`execute_with_retries`).
5. `core/gateway.py:Gateway.complete` / `.stream` executes against the provider adapter,
   prices usage (`cost/pricing.py`), and builds log events.
6. The caller's `wire` encoder re-encodes the result in the inbound dialect.

`wiwi/core/context.py:RequestContext` is the single mutable holder threaded through every
stage (carries `surface`, params, auth, and the chosen dialect encoder).

### Inbound surfaces

`POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/messages`,
`POST /v1/messages/count_tokens`, `POST /v1/completions` (legacy, `wire/openai_completions.py`),
`POST /v1/playground/completions` (`wiwi_session` cookie);
`GET`/`DELETE /v1/responses/{response_id}` (wiwi-owned stored Responses state,
`server/response_store.py` — key-scoped, not a capability token); `GET /v1/models`,
`GET /public/models`, `GET /health`, a configurable Prometheus path (`server/metrics.py`),
`/admin/*` (master key), `/auth/*` (user sessions), `/cline/oauth/callback`.

That is **9 routes under `/v1` and 59 under `/admin`**. Most are defined in
the `create_app` closure in `server/app.py`; the `/admin/keys` ones live in
`wiwi/server/routers/admin_keys.py` and are mounted with `include_router`, so
a grep over `app.py` alone under-counts them. Count them with:

```bash
python3 -c "
import wiwi.server.app as m
from wiwi.config import *
app = m.create_app(WiwiConfig(general_settings=GeneralSettings(master_key='sk-x')))
routes=set()
def walk(rs):
    for r in rs:
        p=getattr(r,'path',None); meth=getattr(r,'methods',None)
        if p and meth: routes.add((tuple(sorted(meth)), p))
        o=getattr(r,'original_router',None)
        if o is not None: walk(o.routes)
walk(app.routes)
print('v1', len([r for r in routes if r[1].startswith('/v1')]),
      'admin', len([r for r in routes if r[1].startswith('/admin')]))"
```

### Outbound provider types (12)

`openai`, `openai-compatible`, `gmicloud`, `bai` (all four share the OpenAI wire shape —
`registry._OPENAI_WIRE_TYPES`), `anthropic`, `gemini`, `openrouter`, `nvidia-nim`, `opencode`
(Zen), `cline` (OAuth), `workbuddy` (OAuth), `hermes` (Nous Portal).

### Streaming contract (`wiwi/streaming/deltas.py`) — binding

One `StreamStart`; `ToolCallOpen → ArgsDelta* → Close` nested per tool index; exactly one
`UsageFinal`; then `Finish`; then `StreamEnd` xor `StreamError`. Adapters **guarantee**
legality; client encoders **never defend**. Exception: `StreamError` may terminate anytime
with no `Finish`. All delta variants are `@dataclass(frozen=True)`.

## Key Directories

| Path | Purpose |
|---|---|
| `wiwi/wire/` | Inbound codecs + encoders per dialect: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py`, `openai_completions.py` (legacy `/v1/completions`) |
| `wiwi/ir/` | Canonical IR: `types.py` (tagged parts, messages, tools, `GenParams`, `Usage`), `builtin_tools.py`, `translation.py` (dialect↔IR translation helpers) |
| `wiwi/providers/` | Outbound adapters (`<provider>_adapter.py`) + `base.py` (`ProviderAdapter` protocol, `WiwiError`), `registry.py`, `token_expiry.py`, and the OAuth support files `cline_oauth.py`, `cline_auto_refresh.py`, `cline_version.py`, `workbuddy_auth.py`, `workbuddy_auto_refresh.py`, `workbuddy_version.py`, `hermes_oauth.py`, `hermes_auto_refresh.py`, `opencode_version.py`, `nim_tool_schema.py`, `nim_native_tools.py`. `openai-compatible` and `gmicloud` have no adapter file — they resolve to the `openai` adapter via `_OPENAI_WIRE_TYPES` |
| `wiwi/router/` | `router.py`: `ProviderKey`/`ProviderAccount`/`Deployment`/`Router`, smooth WRR, cooldowns, fallbacks |
| `wiwi/core/` | `gateway.py` (~2.6k lines; execution engine, pricing, log events), `context.py` (`RequestContext`), `recovery.py`, `telemetry.py` |
| `wiwi/streaming/` | `deltas.py` (taxonomy), `sse.py`, `partial_json.py`, `validation.py`, `resume.py` (`StreamTape`), `tape_store.py` (`JournalStore`), `coalesce.py`, `loopdetect.py` |
| `wiwi/auth/` | `service.py` (`AuthService`: virtual keys SHA-256 at rest, budgets), `users.py`, `keys.py` |
| `wiwi/ratelimit/` | `memory.py`, `redis.py` |
| `wiwi/cache/` | `build_response_cache()`, `keygen.py`, `response_cache.py`, `redis_cache.py`, `interface.py` |
| `wiwi/cost/` | `pricing.py` |
| `wiwi/logging_core/` | `db_sink.py`, `events.py`, `subsystem.py` (structlog workers), `hist.py`, `retention.py` |
| `wiwi/server/` | `app.py` (~7.2k lines: `create_app`, `AppState`, `lifespan`, the error handlers, and the routes that are still inline closures), `config_store.py` (SQL DDL + admin persistence), `response_store.py` (Responses-API state), `backup.py`, `metrics.py`, `stats.py`, `static/` (built SPA) |
| `wiwi/server/routers/` | HTTP routers, one module per admin surface. A router owns the HTTP shape (path, method, request/response models) and delegates the rules to `wiwi/server/services/`. Built per app in `create_app` (the service is per-app); the auth guard is a module-level dependency |
| `wiwi/server/schemas/` | Pydantic v2 request/response models per surface. `extra="ignore"`, and a validator raises `ValueError` (a `TypeError` escapes as a 500) |
| `wiwi/server/services/` | The business rules behind the routes (authorization, invariants), raising `APIError`. No FastAPI imports, so the rules are testable without an HTTP client |
| `wiwi/server/dependencies.py` | The auth guards as importable FastAPI dependencies (`require_user`, `require_admin`), reading the app state via `request.app.state.wiwi`. They must stay module-level: a per-app closure is invisible to FastAPI's annotation resolver, which silently demotes the parameter to a body field |
| `wiwi/server/errors.py` | `APIError` (status + error type + dialect) and the per-dialect renderer. The app's exception handlers turn a raised `APIError` into the caller's dialect body, and a request-validation failure into a **400** (not FastAPI's 422) |
| `wiwi/config.py` | Pydantic v2 config models + `load_config`; `PROVIDER_TYPES` is the single source of truth |
| `wiwi/main.py` | CLI entrypoint (`wiwi.main:cli`) |
| `web/` | Admin SPA + public site (React 19, TS strict, Vite 6, Tailwind 4) — 64 components in `src/pages/`, builds to `wiwi/server/static/` |
| `tests/` | pytest suite — 213 files collecting ~3,642 tests; bugfix regressions in `test_fix_roundN.py` |
| `docs/` | Design specs (see **Important Files**) |

## Development Commands

```bash
# run server (needs wiwi.yaml; cp wiwi.yaml.example wiwi.yaml first)
wiwi --config wiwi.yaml                     # default 0.0.0.0:4000
wiwi --reload --reload-dir wiwi             # dev: uvicorn reload (factory create_app_from_config_path)
DATABASE_URL=postgresql+asyncpg://... wiwi --config wiwi.yaml   # Postgres (default SQLite wiwi.db)

# tests / lint — the binding pre-completion gate
python3 -m pytest tests/ -q
python3 -m pytest tests/test_codecs.py -q                       # single file
python3 -m pytest tests/test_router.py -k cooling               # -k substring
python3 -m pytest tests/test_integration.py::test_chat_completion_happy_path -q
ruff check wiwi/ tests/
python3 -m pytest tests/ -q && ruff check wiwi/ tests/          # run BOTH before claiming done

# admin UI (npm)
cd web && npm install && npm run dev        # Vite :5173, proxies /admin /auth /public /v1 /health → :4000
cd web && npm run build                     # tsc -b && vite build → wiwi/server/static/
cd web && npm run lint                      # eslint src (web/ is NOT covered by ruff)

# docker (Postgres + Redis have healthcheck-gated depends_on; no --profile)
docker compose up --build

# deploy the gateway to the HuggingFace Docker Space (shimen/yapapa)
./deploy/hf_space.sh --dry-run              # list the exact file set that would ship
./deploy/hf_space.sh                        # export committed HEAD into the Space and push

# load test
python3 bench.py -n 10 -c 1,4,16 --max-tokens 100               # TTFT, p50/p95, output TPS
```

## Code Conventions & Common Patterns

- **Async throughout.** `httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths.
  Library code uses `structlog`, never `print`.
- **Pydantic v2** for config + admin schemas; **frozen dataclasses** for IR and streaming
  hot-path types. Adapters mutate their own instance state, never deltas.
- **Adapter instances:** `get_adapter(type)` = shared singleton (reset on hand-out, sync use
  only); `fresh_adapter(type)` = private instance for the request hot path (adapters hold
  per-stream decode state across awaits).
- **Import from the module that owns the symbol**, never a re-export layer. Prefer existing
  module APIs — a second convention beside an existing one is prohibited.
- **Run `lsp references` before editing an exported symbol**; missed callsites are bugs.
- **Error handling:** raise `providers/base.WiwiError(status, etype, message)`; retryable
  statuses are `RETRYABLE_STATUS = {408,429,500,502,503,504,529}`. Errors are surfaced to the
  caller through each dialect's `error_body()`, preserving provider error wording where the
  client matches on it.
- **Registry coverage:** adding a provider type requires a branch in `registry.get_adapter()`;
  an import-time assert catches a forgotten branch (`PROVIDER_TYPES` in `config.py` is truth).
- **Never import `wiwi.server.app` at module level in a library module** (lifespan/startup
  side effects) — only `wiwi/main.py` and test fixtures do.
- **Frontend:** TypeScript `strict` + `verbatimModuleSyntax` + `noUnusedLocals/Parameters`;
  `@/*` path alias (tsconfig + Vite). No `../../../../` import chains.
- **UI/UX rule (binding):** every UI change must work at 375px and desktop — no clipping, tap
  targets ≥44px, hover reveals also respond to focus/touch, keyboard + focus-visible.
- **Commits:** imperative present tense, capitalized, no prefix tags. One logical change each.
  Work directly on `main` (single-developer repo; no feature branches/PRs).

## Important Files

- `wiwi/main.py` — CLI; config precedence `--config` flag > `WIWI_CONFIG` env (inline YAML) >
  `wiwi.yaml`; `load_env()` (python-dotenv, `override=False`) runs before config parse so real
  env vars win.
- `wiwi/config.py` — Pydantic config models; `load_config`, `load_config_from_string`;
  `os.environ/NAME` interpolation is resolved here.
- `wiwi/server/app.py` — ~7.2k lines in one `create_app` closure: `create_app(config)`,
  `create_app_from_config_path` (uvicorn reload factory), `AppState`, `lifespan`, `run_chat_like`,
  the error handlers, and most of the routes (the `/admin/keys` ones live in
  `wiwi/server/routers/admin_keys.py`). Navigate it by grepping for a route prefix, not by reading.
- `wiwi/server/config_store.py` — SQL DDL + migrations for admin-managed providers, keys,
  deployments, prices, settings.
- `wiwi/server/response_store.py` — persisted Responses-API state behind
  `store` + `previous_response_id`; ids are key-scoped and expiry is enforced on read.
- `wiwi/server/backup.py` — whole-gateway backup/restore behind `/admin/backup`.
- `wiwi/ir/types.py`, `wiwi/core/gateway.py`, `wiwi/core/context.py`, `wiwi/router/router.py`,
  `wiwi/providers/registry.py`, `wiwi/providers/base.py`, `wiwi/streaming/deltas.py`,
  `wiwi/auth/service.py` — core seams.
- `pyproject.toml` — deps, entry point, ruff + pytest config.
- `wiwi.yaml.example` / `.env.example` — config shape and env vars (`WIWI_MASTER_KEY`,
  `DATABASE_URL`, provider keys).
- `Dockerfile`, `docker-compose.yml`, `start.sh` (npm; runs backend + Vite together),
  `bench.py`.
- `deploy/` — `hf_space.sh` pushes the gateway to the HuggingFace Docker Space
  `shimen/yapapa` (`git archive HEAD` → scratch clone → one commit; the repo's
  `README.md` never travels, because the Space's `README.md` is its YAML manifest —
  `deploy/hf-space/README.md` is copied over it). Deploy target only; see `CLAUDE.md`.
- `AUDIT.md` — bug register: severity badge, file:line, trigger, fix sketch; fixed entries
  marked `**Status: fixed**`. Read first for bugfix work; record new bugs here.
- `UPDATE.md` — **binding** translation-fix changelog (OpenAI↔Anthropic translation, OpenRouter
  adapter, multi-turn fixes, `reasoning_effort`/`reasoning`, `tool_result`, `content: null`,
  `stream_options`, upstream error extraction). **Required reading before touching
  `wiwi/wire/` codecs or provider adapters**; add an entry when a fix lands there.
- `docs/` — `QUICKSTART.md`, `CONFIG.md`, `API_REFERENCE.md`, `PROVIDERS.md`, `UPSTREAM_DIAGNOSIS.md`,
  `OPENCODE.md`, `STREAMING.md`, `STREAMING_PERFORMANCE_RECOVERY.md`, `ARCHITECTURE.md`, `CORE.md`,
  `ADMIN.md`, `DEVELOPMENT.md`
  (binding invariants §5, UI rule §7, bugfix workflow §8), `TECHSTACK.md`, `PLAN.md`, `MVP.md`;
  `docs/superpowers/{specs,plans}/` is a historical record.
- **Repo-root docs that are *not* under `docs/`** — `RESEARCH.md` (a `docs/RESEARCH.md` does not
  exist), `detailed.md` (code-derived technical reference), `knowledge.md`, `AUDIT_REPORT.md`
  (point-in-time deep audit), `REMINDERS.md` (manual follow-ups; its status claims go stale —
  check `git status` rather than trusting them), `TERMS.md`.
- `docs/UPSTREAM_DIAGNOSIS.md` — **binding when any provider returns a `4xx` you cannot explain.**
  An upstream refusal has three causes with different fixes (wrong credential / account not entitled /
  our request does not look like the vendor's client) and the error text picks between them *wrongly*:
  wiwi's own classification has already renamed a request-shape refusal "requires billing". Get the raw
  upstream error type first, then separate the three by two questions — does a different credential change
  it, and does the vendor's own client work cleanly from this machine on the same model? Never conclude an
  offer was withdrawn from one failing probe, and never let a "control" share infrastructure with the path
  it is meant to compare against.
- `docs/OPENCODE.md` — **required reading before touching the `opencode` adapter.** That provider's
  access depends on the request shape matching the current OpenCode client, and the shape has already
  moved once (v1 → v2). Carries the full measured factor matrix, the per-wire routing table, and the
  procedure for re-deriving the shape when the client moves again.
- `.claude/rules/wiwi-bugfix-workflow.md` — bugfix workflow rules.

> **Docs run ahead of the code.** Some specs describe unbuilt pipelines. When docs and code
> disagree, **trust the code**.

## Runtime/Tooling Preferences

- **Use the ambient `python3` (3.13.5 at time of writing — *not* 3.12) /
  `python3 -m pytest` (9.1.1) / `ruff` (0.17.1)** — the
  project is installed on PATH and `import wiwi` resolves to this checkout. **There is no
  usable `.venv`**; never invoke `.venv/bin/python`.
- `requires-python = ">=3.11"`; ruff `line-length = 100`, `target-version = "py311"`,
  `ignore = ["EXE002"]` (meaningless +x bits on this mount). Ruff only — no black/isort/mypy.
- **npm is authoritative for `web/`.** `web/package-lock.json` and `start.sh` use npm; do not
  mix package managers in one session.
- Redis is an optional extra (`.[redis]`); without it the response cache silently falls back to
  the in-memory LRU. `asyncpg` (Postgres) is a core dep.
- Build backend hatchling; entry point `wiwi = "wiwi.main:cli"`.
- **No CI and no pre-commit config.** The manual `pytest` + `ruff` gate is binding.
- **Never commit:** `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `*.har`, or
  anything under `.wiwi/` or `.verify/` — live keys and runtime state. Provider keys enter via
  `os.environ/NAME` in config; admin endpoints require `WIWI_MASTER_KEY`.
- **`HF_TOKEN`** (HuggingFace write token for the `shimen/yapapa` Space) lives in the
  gitignored `.env`; `.env.example` carries the name with an empty value. It is read only by
  `deploy/hf_space.sh` — the gateway itself never reads it. The Space is **public**, so a
  token or key pasted into any tracked file is world-readable: never put a live value in
  `.env.example` (see `AUDIT.md` #154).
- **`git-lfs` is a hard prerequisite of `deploy/hf_space.sh`.** The tracked tree carries
  PNGs and HF rejects a push containing binaries that are not in LFS/xet, so the script
  tracks `*.png` and pushes those objects through LFS. Without git-lfs the failure lands
  *after* the clone and the commit — the Space is left untouched and the error is a bare
  `git: 'lfs' is not a git command`. The script now preflights it and prints install
  instructions (no-root path: the official binary in `~/.local/bin`, already on PATH here).

## Testing & QA

- **Framework:** pytest under `[tool.pytest.ini_options]` in `pyproject.toml` —
  `asyncio_mode = "auto"` (write bare `async def test_…`, no decorators),
  `asyncio_default_fixture_loop_scope = "function"`. No custom markers, no xdist, no coverage
  plugin. **No `conftest.py`** — each test file builds its own config/app fixtures.
- **Mocking:** `respx` in **decorator form** (`@respx.mock`) for upstream HTTP (the
  context-manager form is broken under respx 0.23 + httpx 0.28). App-level tests drive
  `create_app(config)` through `httpx.ASGITransport` inside `asgi_lifespan.LifespanManager`.
  Env is controlled with `monkeypatch`; internal seams with `unittest.mock.patch`.
  `autouse` fixtures seed version/OAuth caches for determinism.
- **Property tests:** `hypothesis` (`test_property_roundtrip.py`) for IR/codec round-trips and
  stream-SSE legality invariants.
- **Regression convention:** new bugfix regressions go in the next unused
  `tests/test_fix_roundN.py`. Latest present: `test_fix_round168.py` → next is `round169`
  (confirm numerically, never with a plain `ls` — it sorts lexicographically, so `round9.py`
  looks newest). 165 such files exist; gaps at 1, 5 (`test_bugfix_round5.py` is the legacy
  filename), 102 and 147.
- **Subsystem map (rough):** `test_codecs.py` / `test_extra_body.py` — wire unit; `test_router.py`,
  `test_round_robin.py`, `test_fix_cycle_failover.py` — routing; `test_admin_api.py`,
  `test_config*.py`, `test_provider_admin.py`, `test_pricing_admin.py` — server/admin;
  `test_integration.py` — full dialect→provider→dialect; `test_streaming_improvements.py`,
  `test_recovery.py`, `test_cache_and_journal.py` — streaming/recovery; `test_<provider>_adapter.py`
  — per-adapter.
- **Coverage expectation:** none configured; the bare gate is the full suite green. Don't trust a
  pinned pass-count in docs/README badges — run the suite. Measured baseline 2026-10-22:
  `3632 passed, 11 skipped, 5 warnings in 254.29s` (3,642 collected). Some warnings are `aiosqlite`
  worker threads outliving their event loop (`AUDIT.md` #475) — harmless, but escalating that
  warning class to an error charges the failure to an unrelated test, so don't.
- **Verification gate before claiming done:** `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`
  both green, plus a live-path smoke test (server or UI) for user-visible changes.