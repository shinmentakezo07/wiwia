# Repository Guidelines

Self-hosted unified LLM gateway proxy. FastAPI backend + React (Vite) admin SPA. Three inbound dialects (OpenAI Chat Completions, OpenAI Responses, Anthropic Messages) flow through one canonical IR out to any of eleven provider adapters and are re-encoded in the caller's dialect on the way back.

## Project Overview

wiwi is a hub-and-spoke LLM gateway — no pairwise converters, one canonical IR:

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

Inbound surfaces (HTTP routes):

- `POST /v1/chat/completions` — OpenAI Chat dialect
- `POST /v1/responses` — OpenAI Responses dialect
- `POST /v1/messages` — Anthropic Messages dialect
- `POST /v1/messages/count_tokens` — Anthropic token counting
- `GET /v1/models` — client-visible model catalog; `GET /public/models` unauthenticated
- `GET /health` → `{"status":"ok","groups":N,"providers":N}`; `GET /metrics` Prometheus text, master-key gated, 8 `wiwi_*` metrics

Outbound provider types (`PROVIDER_TYPES`, `wiwi/config.py:36-48` — the single source of truth): `openai`, `anthropic`, `gemini`, `openai-compatible`, `openrouter`, `gmicloud`, `bai`, `nvidia-nim`, `cline`, `workbuddy`, `opencode`.

Admin surface: `/admin/*` (master-key auth), `/auth/*` (cookie session), and the SPA at `/admin/ui`. Every response carries `x-wiwi-request-id` and `x-wiwi-latency-ms`; bodies over `max_request_body_mb` (default 50) get 413.

## Architecture & Data Flow

`wiwi/server/app.py` is a ~3.9k-line FastAPI factory: `RequestIdMiddleware`, the `run_chat_like` pipeline, the three inbound surfaces plus `count_tokens`/`/v1/models`/`/health`/`/public/models`, ~45 `/admin/*` + `/auth/*` routes, lifespan, and the SPA static mount. Do not read it top to bottom — start at `run_chat_like`.

### Request pipeline (`wiwi/server/app.py:run_chat_like`)

1. Parse JSON body (`app.json_body`).
2. Wire decode → IR (`wiwi/wire/<dialect>.py:codec_decode`).
3. Authenticate (`wiwi/auth/service.py:AuthService`) — master key for `/admin/*`, virtual key `sk-wiwi-…` for client traffic.
4. Resolve group (`wiwi/router/router.py:Router.resolve_group` — provider `alias_id` registry first, then a bounded 8-hop `model_group_alias` walk; `app.py` keeps a mirrored `_ALIAS_CHAIN_MAX_HOPS = 8`).
5. Enforce rate limit (`wiwi/ratelimit/{memory,redis}.py`).
6. Cache lookup (`wiwi/cache/`, non-streaming only, off by default).
7. Build `RequestContext` (`wiwi/core/context.py`).
8. Dispatch: `ir_req.stream ? gateway.stream(ctx) : gateway.complete(ctx)`. Both wrapped by `router.execute_with_retries` for failover over deployments + fallback groups.
9. Provider call: `wiwi/providers/registry.py:fresh_adapter(type)` → `adapter.encode_request / _call / decode_stream_event`.
10. Stream pump: `wiwi/core/gateway.py:_pump_once` over `wiwi/streaming/` (SSE parse/encode, StreamTape failover+resume, partial JSON, loop detection, coalesce, schema validation).
11. Outbound encode: `_encoder_for(surface)` → `ChatStreamEncoder | ResponsesStreamEncoder | AnthropicStreamEncoder` (streaming) or `codec_encode_response` (non-streaming).
12. Post: log request, record TPM, update spend (budget cap → 402), translate `WiwiError` → per-surface `error_body`.

`Gateway.complete/_pump_once` call `build_log_event` per attempt and `ctx.note_attempt(...)` per try (deployment, provider, key label, status, latency). `LogEvent` fans out to an SSE ring buffer, Prometheus, and the DB sink.

### Startup / shutdown

`create_app` fails closed without `WIWI_MASTER_KEY` and a session secret (`WIWI_SESSION_SECRET`, else master key) — this prevents forged admin cookies. `AppState.init_db` resolves the DB URL (`DATABASE_URL` env > `general_settings.database_url` > `sqlite+aiosqlite:///wiwi.db`), normalizes it (`sqlite:///`→`sqlite+aiosqlite:///`; `postgres://|postgresql://`→`postgresql+asyncpg://`; `sslmode`→`ssl`, `channel_binding` dropped), then starts AuthService/UserService/DBSink/ConfigStore, merges the DB config overlay onto the YAML-built router, and starts the gateway, the journal sweeper, the Cline/WorkBuddy/opencode version-refresh workers, and `HealthHealer` (reverse on shutdown).

### Hard invariants

- All dialect/provider branching stays inside `wiwi/wire/` and `wiwi/providers/`. `core/`, `router/`, `auth/`, `streaming/`, `cache/`, `cost/`, `logging_core/`, `ir/` must not import dialect or provider symbols. `wiwi/core/recovery.py` states this in its module docstring and also must never import `wiwi.router` or `wiwi.core.gateway` (cycle avoidance); the `providers`→`server` edge is `TYPE_CHECKING`-only.
- `wiwi/providers/registry.py:97` asserts at import time that every `PROVIDER_TYPES` entry has a matching `get_adapter()` branch; `wiwi/router/router.py:726` asserts the admin catalog matches. Adding a provider = new adapter + one branch + catalog entry. The asserts catch *missing* branches, not out-of-place ones.
- `RequestContext` (`wiwi/core/context.py`) is the single mutable object threaded through every stage; `AttemptRecord` lives alongside it.
- `fresh_adapter()` on the request hot path (private instance — adapters hold per-stream decode state across awaits); `get_adapter()` returns a shared instance that is `reset()` on hand-out and is only safe for synchronous/non-await use. Using the singleton in the hot path lets a concurrent request wipe an in-flight stream.
- Streaming event ordering is a contract every adapter MUST obey; encoders never defend against malformed sequences:

  ```
  StreamStart  (exactly one, first)
    TextDelta* | ThinkingDelta*
    ToolCallOpen → ToolCallArgsDelta* → ToolCallClose   (strictly nested per index)
  UsageFinal   (exactly one, after last content delta)
  Finish       (exactly one)
  StreamEnd xor StreamError
  ```

  Asymmetry: `StreamError` may terminate at ANY point with no preceding `Finish`.
- All 10 `IRStreamDelta` variants in `wiwi/streaming/deltas.py` are `@dataclass(frozen=True)`. Adapters mutate per-stream state on the adapter instance, never on deltas.
- Never `print` from library code — use `structlog`. Async throughout (`httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths).

## Key Directories

| Path | Purpose |
|---|---|
| `wiwi/main.py` | argparse CLI; config precedence `--config` > `WIWI_CONFIG` (inline YAML) > `wiwi.yaml`; uvicorn dispatch (factory string under `--reload`). |
| `wiwi/config.py` | Pydantic v2 models + `PROVIDER_TYPES` (the single source of truth), `load_config`/`load_env`, `os.environ/NAME` interpolation, empty-key provider filtering, fail-fast validation. |
| `wiwi/server/` | `app.py` FastAPI factory + routes + pipeline; `config_store.py` DB persistence for providers/keys/deployments/prices/settings; `stats.py` pure rollups; `metrics.py` Prometheus text. |
| `wiwi/wire/` | Dialect codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py`. Each owns `decode_request`, `encode_response`, a `StreamEncoder`, and `error_body`. |
| `wiwi/ir/` | Canonical IR dataclasses (`types.py`: 7 `Part` variants, `Message`, `Tool`, 4 `ToolChoice` variants, `ResponseFormat`, `GenParams`, `Request`, `Usage`, `AssistantTurn`, `Response`, plus `effort`↔`budget` maps) + `builtin_tools.py` registry (per-surface `web_search` wire types, decode-only aliases). |
| `wiwi/providers/` | `base.py` (`ProviderAdapter` Protocol — 5 methods — `WiwiError`, `RETRYABLE_STATUS`, `error_from_provider_status`, `status_for_key_pool`, `ProviderKeyRef`) + per-provider adapters + `registry.py`. Quirks live here, never in core. |
| `wiwi/router/router.py` | Groups/deployments, `ProviderAccount` smooth-WRR key pools, `resolve_group` alias walk, `pick_deployment` strategies, `_CrossProviderWRR`, `execute_with_retries`, `BUILTIN_PROVIDER_TYPES` catalog + sync assert. |
| `wiwi/core/` | `gateway.py` (`complete`, `_complete_via_stream` for `force_stream` adapters, `stream`, `_pump_once`, `_attempt_resume`, `build_log_event`), `context.py` (`RequestContext`/`AttemptRecord`), `recovery.py` (`Backoff`, `CircuitBreaker`, `probe_verdict`, `parse_retry_after`, `HealthHealer`). |
| `wiwi/streaming/` | `deltas.py` taxonomy + ordering contract; `sse.py` parser/encoder (CRLF-injection guard); `resume.py` StreamTape ring buffer + mid-stream failover continuation; `tape_store.py` durable per-request JSONL journals; `partial_json.py`; `validation.py` (advisory, never fails the stream, 1 MiB cap); `coalesce.py`; `loopdetect.py` (`MAX_LOOP_PERIOD=8`). |
| `wiwi/auth/` | `keys.py` mint/hash/mask; `service.py` `AuthService` (SQLite/PG `vkeys`, 60s TTL cache, budget); `users.py` PBKDF2 + HKDF/HMAC signed session cookies. |
| `wiwi/ratelimit/` | Sliding-window rpm/tpm: `memory.py` (default) + `redis.py` (sorted sets, estimate→actual reconciliation, memory fallback). |
| `wiwi/cache/` | Opt-in exact-match response cache: `CacheBackend` Protocol, normalized-IR SHA-256 key, memory/Redis backends, `build_response_cache`. |
| `wiwi/cost/` | `CostEngine` token→USD (8-dp, `unpriced` flag), slash-trimmed lookup, tiktoken-or-chars/4 estimation. |
| `wiwi/logging_core/` | `LogEvent` (request/proxy/audit streams), `LoggingSubsystem` bounded queues + SSE broadcast sink, `DBSink` batched writes with 5s query TTL cache. |
| `web/src/pages/` | React 19 + Vite 6 + Tailwind 4 SPA; mixes ~16 admin console pages with ~30 public marketing pages — never assume a page is admin-facing from directory alone. |
| `tests/` | 83 flat `test_*.py` modules plus numbered `test_fix_roundN.py` regressions (see Testing & QA). |
| `docs/` | 13 top-level docs + `docs/superpowers/{specs,plans}/` dated design records. |

## Development Commands

The `.venv` symlink in this checkout points at an empty Python 3.14 venv with no site-packages. **Never use `.venv/bin/python`.** Use ambient `python3` (Python 3.12), `pytest` 9.1.1, `ruff` 0.16.5 on `PATH`.

```bash
# backend
python3 -m pytest tests/ -q                          # full suite — keep green, don't trust pinned pass-counts
python3 -m pytest tests/test_codecs.py -q            # single file
python3 -m pytest tests/test_router.py -k cooldown   # single test by name
python3 -m pytest tests/test_integration.py::test_chat_completion_happy_path -q
ruff check wiwi/ tests/                              # line-length 100, target py311, ignore EXE002

# pre-completion gate (both must be green before claiming done)
python3 -m pytest tests/ -q && ruff check wiwi/ tests/

# install (uv is the package manager; uv.lock is authoritative)
uv pip install -e '.[redis]'                         # [redis] extra ships in the Docker image

# run server
wiwi --config wiwi.yaml                              # prod: load object → uvicorn
wiwi --reload                                        # dev: factory import + reload
wiwi --config wiwi.yaml --port 4000 --host 0.0.0.0
# or directly: uvicorn wiwi.server.app:create_app_from_config_path --factory

# frontend (web/) — bun, not npm
cd web && bun install && bun run dev                 # Vite dev server, proxies /admin /auth /public /v1 /health → :4000
cd web && bun run build                              # tsc -b && vite build → ../wiwi/server/static/
cd web && bun run lint                               # eslint src (web/ is NOT covered by ruff)

# stress test
python3 bench.py -n 10 -c 1,4,16 --max-tokens 100    # async httpx load tester; TTFT p50/p95, latency, TPS, RPS

# full stack with both servers
./start.sh                                           # npm-based, predates bun migration (stale but functional)
```

Env knobs honored by `start.sh`: `WIWI_PORT`, `WIWI_WEB_PORT`, `WIWI_PYTHON`, `WIWI_BIN`, `WIWI_RELOAD`, `WIWI_RELOAD_DIRS`. `start.sh` pins `PYTHONPATH` to the checkout and hard-errors if `import wiwi` resolves elsewhere.

Config precedence: `--config` flag > `WIWI_CONFIG` env > `wiwi.yaml`. `.env` is loaded with `override=False` (real env wins) before config parse, and again in the uvicorn reload factory path. `os.environ/NAME` strings are interpolated recursively; missing vars resolve to `""`, and validation drops providers whose keys all resolved empty (and the `model_list` entries referencing them), so `wiwi.yaml.example` boots in a fresh container.

## Code Conventions & Common Patterns

- **Python**: `requires-python = ">=3.11"`, ruff `target-version = "py311"`, `line-length = 100`. Ruff-only lint (no black/isort); ignore `EXE002` only (the workspace mount marks files +x while git tracks 100644). `web/` is not ruff-covered.
- **Async throughout**: `httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths. Never `print` from library code — use `structlog`.
- **Data shapes**: Pydantic v2 for config and admin schemas; plain `@dataclass(frozen=True)` for IR and streaming hot-path types.
- **Dataclasses are frozen**: every `IRStreamDelta` variant in `wiwi/streaming/deltas.py` is `@dataclass(frozen=True)`. Adapters mutate per-stream state on the adapter instance, never on deltas.
- **Streaming event ordering** (the contract every adapter MUST obey):

  ```
  StreamStart  (exactly one, first)
    TextDelta* | ThinkingDelta*
    ToolCallOpen → ToolCallArgsDelta* → ToolCallClose   (strictly nested per index)
  UsageFinal   (exactly one, after last content delta)
  Finish       (exactly one)
  StreamEnd xor StreamError
  ```

  `StreamError` may terminate at ANY point (no preceding `Finish` required). Encoders do NOT defend against malformed sequences — adapters guarantee legality.
- **Adapter singletons**: `get_adapter(type)` returns a shared instance (reset on hand-out); `fresh_adapter(type)` returns a private instance for the request hot path because adapters hold per-stream decode state across awaits.
- **Provider keys**: enter via `os.environ/NAME` interpolation in `wiwi.yaml`. Master key from `WIWI_MASTER_KEY` (admin auth). Virtual keys `sk-wiwi-…` are SHA-256-hashed at rest with constant-time compare; plaintext returned only once at mint.
- **Error translation**: `WiwiError` is the unified error type. Each wire dialect owns its `error_body()` mapping (e.g., Anthropic `etype` strings).
- **Cost**: `wiwi/cost/` resolves token → USD using per-model prices from `wiwi/server/config_store.py` (DB-backed).
- **Two different cache-hit flags — do not conflate**: `cache_hit` = provider prompt-cache hit (feeds `wiwi_prompt_cache_hits_total`); `response_cache_hit` = served from wiwi's own exact-match cache (`wiwi/cache/`, `LogEvent.response_cache_hit`). A response-cache hit must leave `cache_hit=False` (`wiwi/server/app.py` response-cache branch) or prompt-cache metrics inflate. Response cache never stores streaming requests or requests with builtin tools.
- **Frontend**: React 19, Vite 6, Tailwind 4, TanStack Query 5, Recharts. `web/tsconfig.json` is `strict` + `noUnusedLocals/Parameters` + `verbatimModuleSyntax` + `erasableSyntaxOnly`; path alias `@/*` → `./src/*`, and `src/llmgateway-ref` is excluded.
- **Naming**: tests `test_*.py`; new bugfix regressions always `test_fix_roundN.py` (next unused N — see Testing & QA).
- **Imports**: prefer existing module APIs over new ones; second convention beside existing is prohibited. Always run `lsp references` before editing an exported symbol.

## Important Files

- `wiwi/main.py` — CLI, config precedence, uvicorn dispatch.
- `wiwi/server/app.py` — FastAPI app factory, routes, `run_chat_like`, lifespan, SPA static.
- `wiwi/config.py` — `PROVIDER_TYPES`, `load_config`, `load_env`, Pydantic models (`WiwiConfig`, `WiwiSettings`, `GeneralSettings`, `RouterSettings`, `ProviderDef`, `ModelEntry`, `ModelAliasEntry`, `KeyDef`, `DeploymentParams`).
- `wiwi/ir/types.py` — IR dataclasses: `Part` union (`TextPart`, `ImagePart`, `ToolUsePart`, `ToolResultPart`, `ThinkingPart`, `AudioPart`, `DocumentPart`), `Message`, `Tool`, `ToolChoice*`, `ResponseFormat`, `GenParams`, `Request`, `Usage`, `AssistantTurn`, `Response`. Helpers: `effort_to_thinking_budget`, `thinking_budget_to_effort`.
- `wiwi/streaming/deltas.py` — `IRStreamDelta` union of `StreamStart`, `TextDelta`, `ThinkingDelta`, `ToolCallOpen`, `ToolCallArgsDelta`, `ToolCallClose`, `UsageFinal`, `Finish`, `StreamEnd`, `StreamError`.
- `wiwi/core/context.py` — `RequestContext` (fields: `surface`, `ir_req`, `started`, `request_id`, `auth`, `raw_body_bytes`, `group`, `deployment`, `provider_key`, `attempts`, `first_token_at`, `last_token_at`, `usage`, `cost`, `cache_hit`, `stop_reason`, `status`, `error`, `log_buffer`, `metadata`, `cancel`, `_defer_key_credit`).
- `wiwi/providers/registry.py` — provider dispatch + import-time branch-coverage assert.
- `wiwi/providers/base.py` — `ProviderAdapter` Protocol, `WiwiError`, `ProviderKeyRef`, error mappers.
- `wiwi/router/router.py` — `Router.resolve_group`, `pick_deployment` (WRR), `execute_with_retries`.
- `wiwi/auth/service.py` — AuthService, virtual keys DB schema.
- `wiwi/ratelimit/{memory,redis}.py` — sliding-window rpm/tpm.
- `wiwi/wire/{openai_chat,openai_responses,anthropic_messages}.py` — codec decode/encode + stream encoder + error body per dialect.
- `wiwi/providers/{openai,anthropic,openrouter,gemini,nim,gmicloud,bai,cline,workbuddy,opencode}_adapter.py` — provider adapters. Quirks live here, never in core: NVIDIA NIM rejects JSON Schema boolean subschemas and params named `type` (`nim_tool_schema.py`) and frames native MiniMax XML tool markup (`nim_native_tools.py`); Cline is `force_stream=True` with WorkOS OAuth and on-demand refresh (`cline_oauth.py`, `cline_auto_refresh.py`); WorkBuddy is `force_stream=True` with a `{code,msg,data}` envelope and per-key auth-JSON refresh; OpenCode Zen routes per model across four upstream protocols and refreshes its `opencode/<version>` User-Agent live (`opencode_version.py`).
- `wiwi/server/config_store.py` — DB-backed provider/key/deployment/settings/price tables.
- `wiwi/server/static/` — built SPA, served at `/admin/ui` via `_SPAStaticFiles` subclass (index.html history fallback, mounted after all API routes).
- `wiwi.yaml` (gitignored) — runtime config. `wiwi.yaml.example` is the schema reference.
- `AUDIT.md` — live/fixed bug register; read at the start of every bugfix session.
- `UPDATE.md` — binding translation-layer changelog; read before touching `wiwi/wire/` or the OpenAI/Anthropic/OpenRouter adapters.
- `pyproject.toml` — hatchling build, `[project.scripts] wiwi = "wiwi.main:cli"`, `[tool.ruff]`, `[tool.pytest.ini_options]`.
- `Dockerfile` (3-stage: uv `python3.12-bookworm-slim` builder → `oven/bun:1` SPA build → `python:3.12-slim-bookworm` runtime; non-root user `wiwi` uid 10001; healthcheck `GET /health` every 30s; `WIWI_STATIC_DIR=/app/wiwi/server/static`; `/app/data` volume).
- `docker-compose.yml` — `postgres:16-alpine` + `redis:7-alpine` + `wiwi`, healthcheck-gated `depends_on`, port 4000.
- `start.sh` — bash wrapper launching backend + Vite dev server concurrently. Stale (uses npm, not bun) but functional.
- `bench.py` — async httpx load tester; edit the `TARGETS` dict to add endpoints.

## Runtime/Tooling Preferences

- **Backend Python**: ambient `python3` 3.12. Never `.venv/bin/python` (broken symlink in this checkout).
- **Package manager**: uv (lockfile present, `uv.lock`). Install: `uv pip install -e '.[redis]'` (the optional `[redis]` extra drives Redis rate limiting and the Redis response cache; asyncpg is a core dep).
- **Lint**: ruff only for Python (`wiwi/`, `tests/`). ESLint 9 flat config for `web/` (`react-refresh/only-export-components` warn, `@typescript-eslint/no-unused-vars` error with `^_` ignore).
- **Frontend**: bun (authoritative — `web/bun.lock` present; `Dockerfile` and the docs use bun). `web/package-lock.json` is legacy npm and `start.sh` still uses npm by design. Stick to one package manager per session.
- **Database**: SQLite default (`sqlite+aiosqlite:///wiwi.db`); Postgres normalized to `postgresql+asyncpg://`. `DATABASE_URL` env overrides config. Schema is created via inline `CREATE TABLE IF NOT EXISTS` at startup (auth, config_store, db_sink); **no Alembic, no migrations to write**.
- **Stream journals are ON by default** (`stream_journal_enabled: true`, dir `.wiwi/journals`, 600s TTL, 1 MiB/journal cap): encoded SSE frames persist per-request so a client reconnecting with `x-wiwi-stream-id` + `Last-Event-ID` replays even after a wiwi restart. `.wiwi/` is gitignored runtime scratch — never commit it.
- **Env loading**: `load_env()` (python-dotenv, `override=False`) runs before config parse.
- **Docker**: `WIWI_STATIC_DIR=/app/wiwi/server/static` is set; data lives in `/app/data` (mounted volume); `DATABASE_URL` defaults to `sqlite+aiosqlite:////app/data/wiwi.db` in the image. Default healthcheck port 4000.
- **No pre-commit framework**. Discipline is developer-driven via the documented `pytest + ruff` gate.
- **Static typing**: Python is untyped (no mypy/pyright in pyproject). TypeScript `tsc -b` runs as part of `web/` build; web/tsconfig is `strict` + `verbatimModuleSyntax` + `noUnusedLocals/Parameters`.
- **Trust the code over the docs.** Doc sections that disagree with the code are aspirational history (`docs/CORE.md` and `docs/ADMIN.md` carry explicit notes that earlier revisions described a handler pipeline/DeltaBus/Next.js UI that was never built). `detailed.md` is the ground-truth technical reference.

## Testing & QA

- **Framework**: pytest 9.1.1 + pytest-asyncio 1.4.0, `asyncio_mode = "auto"` — write bare `async def test_*`, **no** `@pytest.mark.asyncio` decorator (a few legacy files still carry it).
- **No `conftest.py`** anywhere; each test file builds its own `_config()` factory and its own `LifespanManager + httpx.ASGITransport` client fixture inline.
- **Default master key for admin-auth'd tests**: `sk-wiwi-master-test` via `Authorization: Bearer …` (see `tests/test_integration.py`; Anthropic-surface tests use `x-api-key` + `anthropic-version`).
- **Upstream mocking**: `respx` 0.23.1 (decorator form preferred; context-manager form broken in respx 0.23 + httpx 0.28). Patterns: `@respx.mock` + `respx.post(url).respond(...)`, `respx.post(...).mock(side_effect=[...])`.
- **Property-based**: `hypothesis` 6.167.0. Persistent cache in `.hypothesis/`. Used in exactly one file — `tests/test_property_roundtrip.py` (round-trip properties over generated `Request` IR).
- **No pytest-cov / no coverage config**. No `--cov` invocations. Coverage is not enforced and its absence is intentional.
- **Numbered bugfix regression files**: `tests/test_fix_roundN.py`. Existing rounds: 2–41 except 5, which lives at the legacy filename `test_bugfix_round5.py` (rounds 1 and 5 have no `test_fix_round` file). Next free number is **42**. New bugfix regressions MUST go into the next unused `test_fix_roundN.py` — confirm with `ls tests/test_fix_round*.py`, never assume the number — and never back into topic files like `test_codecs.py` or `test_router.py`.
- **Fixtures**: both `@pytest.fixture` and `@pytest_asyncio.fixture` work; newer files (rounds 18+) prefer `@pytest_asyncio.fixture`.
- **Pre-completion verification**: run the full pytest suite AND ruff — both must be green before claiming done. Smoke-test changed paths (launch server, hit endpoint, observe result) instead of adding tests by reflex; only add a test when it defends an observable contract or a plausible bug.

## Bugfix Workflow (binding)

1. Read `AUDIT.md` to avoid redoing a known fix.
2. Read `UPDATE.md` — it is the **binding changelog** for all OpenAI ↔ Anthropic cross-provider translation fixes, the OpenRouter adapter, and multi-turn conversation fixes. Any agent touching `wiwi/wire/` codecs, the OpenAI/Anthropic/OpenRouter adapters, or any code handling `reasoning_effort`/`reasoning` mapping, `tool_result` messages, `content: null`, `stream_options`, or upstream error extraction **must read UPDATE.md first** and follow the invariants it records. When extending one of those areas, check UPDATE.md for the existing fix before writing new code; when a new fix lands in one of those areas, add an entry to UPDATE.md so a later agent does not rediscover and re-fix the same thing.
3. Follow `systematic-debugging` — root cause before patching.
4. Write the failing regression test FIRST into the next `test_fix_roundN.py`.
5. Implement the fix; keep the change minimal and in-module (dialect changes → `wiwi/wire/`; provider changes → `wiwi/providers/` + registry branch).
6. Self-review via `requesting-code-review` before claiming done.
7. Verify: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/` both green; for UI/server changes, exercise the live path.

## Error Discovery & Fix-Reporting Rule (binding)

When an agent discovers a bug, error, or suspicious behavior in this codebase:

1. **Report it in `AUDIT.md` before or alongside fixing it.** Do not silently fix a bug and leave no trace. Any agent that finds a real defect must add an entry to `AUDIT.md` using the existing format: a severity badge (`🔴`/`🟠`/`🟡`/`⚪`), a short title, the exact file:line citations, a trigger description, and a one-line fix sketch. If the bug is already in `AUDIT.md`, skip to step 2.

2. **When fixing a bug that is already in `AUDIT.md`, mark it as fixed in place.** Update the entry to record that it is resolved — add a `**Status: fixed**` line and the commit or description that fixed it, or move it into a `## ✅ Fixed` section at the top of the file with the original finding preserved. Never delete a finding without a trace. The goal is that a fresh agent opening `AUDIT.md` can tell at a glance which bugs are still live and which have already been resolved, so they do not waste time re-fixing or re-investigating something that is done.

3. **Link the fix to its `AUDIT.md` entry.** When a fix lands for a bug that has an `AUDIT.md` entry, the commit message or PR description must reference the entry number or title so the connection is recoverable. If the fix introduces a new regression test in a `test_fix_roundN.py` file, mention that test in the `AUDIT.md` entry as well.

4. **Do not re-report the same bug.** Before adding a new entry to `AUDIT.md`, search it for the same file:line or same symptom. Duplicate entries confuse future agents and inflate the "live bugs" count.

5. **Read `AUDIT.md` at the start of every bugfix session.** If a previous session already discovered and reported the issue you are about to investigate, you should see it there. Starting from `AUDIT.md` avoids duplicate work and makes it obvious which fixes are still pending.

## Guardrails

- **Never commit** `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, anything under `.verify/` or `.wiwi/`, `opencode.json(c)`, `*.har`, or the built SPA assets (`wiwi/server/static/index.html`, `wiwi/server/static/assets/*.js|*.css`) — all gitignored; they hold live provider/master keys and runtime state.
- **Trust the code over the docs** for `ARCHITECTURE.md` / `CORE.md` — doc sections that disagree with the code are aspirational history (handler pipeline, DeltaBus, Postgres/Redis-only backends, deeper DB schema) that was never built.
- **No dialect/provider branching** outside `wiwi/wire/` and `wiwi/providers/` — the registry's import-time assert will catch a forgotten branch, but the cost of leakage is silent wrong-language routing.
- **Second convention beside existing is prohibited** — copy the surrounding pattern, don't invent a parallel one.
- **Always run `lsp references` before editing an exported symbol**; missed callsites are bugs.

## Import Rules (binding)

All agents editing this codebase MUST follow these import rules:

1. **No dialect or provider imports outside `wiwi/wire/` and `wiwi/providers/`.** `core/`, `router/`, `auth/`, `streaming/`, `cache/`, `cost/`, `logging_core/`, and `ir/` must never import symbols from `wiwi.wire` or `wiwi.providers`. Violating this leaks dialect/provider branching into modules that must stay generic. The registry's import-time assert will not catch this — it catches missing branches, not out-of-place ones.

2. **Import from the module that owns the symbol, not from a re-export layer.** If `wiwi.foo` re-exports `Bar` from `wiwi.foo.internal`, import `Bar` from `wiwi.foo`, not from `wiwi.foo.internal`. Re-export layers exist for a reason; bypassing them couples callers to internal layout.

3. **Never add a new top-level entry without updating `registry.py`'s coverage assert.** Adding a provider type or an inbound wire dialect without the corresponding branch in `get_adapter()` or the matching wire module will be caught at import time — but only if the assert is kept honest. Any new entry in `PROVIDER_TYPES` or any new inbound route must have its branch.

4. **Prefer existing module APIs over inventing new ones.** If a helper already exists in the owning module, use it. Do not create a parallel utility with the same job under a different name. "Second convention beside existing is prohibited."

5. **Run `lsp references` on any exported symbol before editing or removing it.** An exported symbol (a public function, class, or constant reachable from outside its module) may have unknown callers. Editing it without checking references is how regressions ship.

6. **Imports in `web/` follow TypeScript module resolution.** Do not mix relative paths arbitrarily — prefer path aliases configured in `tsconfig` / Vite config, consistent with the existing pattern in `web/src/`. Do not add bare `../../../../` chains; if the depth feels wrong, the module boundary probably is.

7. **Never import `wiwi.server.app` at module level in a library module.** `app.py` is the FastAPI application factory and its import can trigger lifespan / startup side effects. Library code (`core/`, `router/`, `auth/`, etc.) must not import it; only the CLI (`wiwi/main.py`) and test fixtures do.

## UI/UX Universal Compatibility Rule (binding)

Every UI or UX change — in `web/` or in any admin-facing HTML/template surface — MUST be verified as usable on both **desktop (mouse/keyboard)** and **mobile (touch, narrow viewport)** before being marked done. Specifically:

1. **Layout must not break below ~375 px viewport width.** Test at 375×812 (iPhone SE class) and at a wide desktop viewport. Sidebars, tables, and cards that assume a minimum width must reflow, stack, or collapse — not clip or overflow.

2. **All interactive controls must be operable by touch.** Tap targets must be at least 44×44 px (Apple HIG) or 48×48 dp (Material). Controls that only respond to hover (CSS `:hover`-only reveals, hover-dependent dropdowns) must also respond to focus and touch. No information or action may be hover-only.

3. **Keyboard and focus navigation must work.** Every focusable element must be reachable via Tab/Shift-Tab in a sensible order. Focus styles must be visible (do not suppress `outline` without providing an equivalent). Modal/dialog focus trapping and Escape-to-close must work on desktop.

4. **Text must be legible and not rely on fixed sizes.** Use relative units (`rem`, `em`, `%`, `vw`) over fixed `px` for typography and spacing where appropriate. Text in containers must not truncate silently in a way that hides information on narrow screens.

5. **Responsive is not optional for admin pages.** Admin pages in `web/src/pages/` are used on desktop but may be opened on a phone (e.g., a quick key rotation or budget check). Assume a mobile viewport is possible for every page; do not gate responsiveness behind a "this is an admin page" assumption.

6. **Verify before claiming done.** For any UI/UX change, open the page in a mobile-sized viewport (browser devtools device mode or a real device) and a desktop viewport, and confirm the change works in both. Screenshots or a short description of the verification belong in the commit message or PR description.
