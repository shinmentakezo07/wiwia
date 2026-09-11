# wiwi — OpenCode Repository Guide

For OpenCode work, treat this file as canonical; `CLAUDE.md` duplicates parts of it and can lag.

## System shape and entrypoints

wiwi is a self-hosted FastAPI gateway. OpenAI Chat, OpenAI Responses, and Anthropic Messages enter as three wire dialects, become one canonical IR, route through a provider adapter, then return in the caller's dialect:

```text
inbound wire -> canonical IR -> provider adapter -> canonical IR -> caller wire
```

`wiwi/config.py:PROVIDER_TYPES` is the outbound-type source of truth. The main HTTP surfaces are `POST /v1/chat/completions`, `POST /v1/responses`, `POST /v1/messages`, `POST /v1/messages/count_tokens`, `GET /v1/models`, `GET /public/models`, and `GET /health`; `/admin/*` is API access and `/auth/*` is user-session access.

For request behavior, start at `wiwi/server/app.py:create_app`, `AppState`, and `run_chat_like`, then follow `RequestContext` through `wiwi/core/gateway.py`, `wiwi/router/router.py`, `wiwi/providers/registry.py`, and the surface encoder. Do not read the ~4k-line app factory top to bottom. For streaming, read `wiwi/streaming/deltas.py` first.

The built SPA mounts at `/` after API routes and has history fallback. The current admin console is `/console`; `/app/*` is a legacy redirect. Older references to `/admin/ui` are stale.

## Ownership and hard contracts

- `wiwi/wire/` owns inbound dialect decode/encode/error translation. `wiwi/providers/` owns outbound adapters and provider quirks.
- `core/`, `router/`, `auth/`, `streaming/`, `cache/`, `cost/`, `logging_core/`, and `ir/` stay dialect- and provider-agnostic. Never import wire/provider symbols there.
- Adding a provider requires an adapter, a `registry.get_adapter()` branch (or documented OpenAI-wire fallback), and catalog/assert coverage. The registry assert catches missing branches, not misplaced branching.
- Use `fresh_adapter(type)` on request hot paths. `get_adapter(type)` returns a resettable singleton safe only for synchronous, non-await-held use; adapters retain stream-decoding state across awaits.
- `RequestContext` is the single mutable object threaded through a request. Keep request-specific mutation there or on the request-owned adapter, not on frozen IR/stream deltas.
- Adapters must emit this stream order; encoders assume it is legal:

```text
StreamStart (once, first)
  TextDelta* | ThinkingDelta*
  ToolCallOpen -> ToolCallArgsDelta* -> ToolCallClose (nested per index)
UsageFinal (once, after the last content delta)
Finish (once)
StreamEnd xor StreamError
```

`StreamError` may terminate at any point without `Finish`. All ten delta variants are frozen dataclasses; mutate adapter state, never a delta.

## Configuration and runtime

- Config precedence is `--config`/`-c` > `WIWI_CONFIG` inline YAML > `wiwi.yaml`. `.env` loads with `override=False`; real environment variables win.
- `os.environ/NAME` values interpolate recursively. Missing values become `""`, and validation drops providers whose resolved keys are empty.
- Startup fails closed unless `WIWI_SESSION_SECRET` or `general_settings.master_key` is set. The session secret otherwise derives from the master key.
- `DATABASE_URL` > configured database URL > `sqlite+aiosqlite:///wiwi.db`. Postgres URLs are normalized for `asyncpg`; schema uses startup `CREATE TABLE IF NOT EXISTS`, with no Alembic or migrations.
- YAML config loads first; DB-stored providers, keys, deployments, aliases, and settings layer over it, skipping YAML entries with the same names.
- Production rate limiting uses `wiwi/ratelimit/memory.py`. `RedisRateLimiter` exists but is not wired into `AppState`; Redis currently selects the optional response-cache backend.
- Exact-match response cache is off by default. Keep `cache_hit` (provider prompt cache) distinct from `response_cache_hit` (wiwi cache); a response-cache hit must leave `cache_hit=False`.
- Durable stream journals are on by default in `.wiwi/journals` with a 600-second TTL and 1 MiB cap. Never commit `.wiwi/`.

## Commands and toolchain

Use ambient `python3` (Python 3.12 here). The checkout's `.venv` is an empty symlink and must not be used.

```bash
# Fresh Python setup; uv.lock is authoritative.
uv pip install -e '.[dev]'
uv pip install -e '.[redis]'       # optional Redis response-cache backend

# Backend verification.
ruff check wiwi/ tests/
python3 -m pytest tests/ -q

# Focused tests.
python3 -m pytest tests/test_codecs.py -q
python3 -m pytest tests/test_router.py -k cooldown
python3 -m pytest tests/test_integration.py::test_chat_completion_happy_path -q

# Backend server.
wiwi --config wiwi.yaml
wiwi --reload --reload-dir wiwi
uvicorn wiwi.server.app:create_app_from_config_path --factory

# Frontend; bun is authoritative, not npm.
cd web && bun install
cd web && bun run dev
cd web && bun run build              # tsc -b, then Vite -> ../wiwi/server/static/
cd web && bun run lint

# Full stack and load testing.
docker compose up --build
python3 bench.py -n 10 -c 1,4,16 --max-tokens 100
```

`web/package-lock.json` and `start.sh` are legacy npm paths; do not mix package managers in one session. There is no CI or pre-commit configuration, so the manual full pytest + ruff gate is binding.

## Testing and bugfix workflow

- pytest uses `asyncio_mode = "auto"` and there is no `conftest.py`; new async tests are bare `async def` tests. Prefer decorator-form `@respx.mock` for upstream mocking.
- Admin-auth tests normally use bearer key `sk-wiwi-master-test`.
- Put new bug regressions in the next unused `tests/test_fix_roundN.py`; find it with `ls tests/test_fix_round*.py`. Round 5 remains the legacy `test_bugfix_round5.py`.
- Read `AUDIT.md` before every bugfix. If a real defect is found, add or update its entry before/alongside the fix; mark resolved entries fixed and preserve their history.
- Read `UPDATE.md` before changing any wire codec, the OpenAI/Anthropic/OpenRouter adapters, or handling `reasoning_effort`/`reasoning`, `tool_result`, `content: null`, `stream_options`, or upstream error extraction. Add a changelog entry for new fixes in those areas.
- Diagnose root cause first, write the failing regression first, keep the implementation in the owning module, self-review, then run the full pytest + ruff gate and smoke-test changed live paths.
- Use imperative present-tense commit subjects without prefix tags, with one logical change per commit.

## Frontend rules

- `web/` is React 19 + Vite 6 + Tailwind 4. TypeScript is strict with `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, and `erasableSyntaxOnly`; use the `@/*` alias.
- `web/src/pages/` mixes public marketing pages, user pages, and guarded console pages; determine ownership from `web/src/main.tsx`, not the directory name.
- Every UI change must work at a 375×812 viewport and a wide desktop viewport. Verify touch targets, keyboard order and visible focus, modal Escape behavior, non-hover-only controls, and readable/reflowing text.

## Imports, style, and guardrails

- Import a symbol from the module that owns it, not through an internal re-export. Prefer an existing helper/API; do not invent a parallel convention.
- Never import `wiwi.server.app` at module level from library code. Before changing or removing an exported symbol, inspect all references (repository convention: `lsp references`).
- Backend code is async; use Pydantic v2 for config/admin schemas and frozen dataclasses for IR/stream hot paths. Use `structlog`, not `print`, in library code. Ruff targets Python 3.11 with line length 100 and ignores only `EXE002`.
- Never commit `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `*.har`, `.verify/`, `.wiwi/`, or built SPA assets. Use `wiwi.yaml.example` as the config reference.
- Trust executable code over prose. `detailed.md` is the code-derived technical reference; sections of `docs/ARCHITECTURE.md`, `docs/CORE.md`, and `docs/ADMIN.md` are aspirational history when they disagree with the implementation.
