# Repository Guidelines

## Orientation

wiwi is a self-hosted LLM gateway: OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages enter through `wiwi/wire/`, become the canonical IR in `wiwi/ir/`, route through `wiwi/router/`, call a provider adapter in `wiwi/providers/`, and return in the caller's dialect.

The main request path is `wiwi/server/app.py:run_chat_like`: parse and decode, authenticate, rate-limit, resolve a model group, run router retries/fallbacks, complete or stream through `Gateway`, then encode and log. Trace that function instead of reading the large app or gateway modules top-to-bottom.

`docs/ARCHITECTURE.md` and `docs/CORE.md` are design-forward. Treat code, config, and executable scripts as authoritative when they disagree.

## Highest-value references

- Read `AUDIT.md` before investigating or fixing a bug; record new findings there before or alongside the fix.
- Read `UPDATE.md` before changing wire codecs, the OpenAI/Anthropic/OpenRouter adapters, or reasoning, tool-result, `content: null`, `stream_options`, and upstream-error translation.
- For streaming contracts, read `wiwi/streaming/deltas.py`; for provider dispatch, read `wiwi/providers/registry.py`; for request state, read `wiwi/core/context.py`.
- `docs/DEVELOPMENT.md` is a walkthrough; this file is the compact operating guide.

## Commands

Use the interpreter and package manager that are actually installed:

```bash
# Backend verification
python3 -m pytest tests/test_codecs.py -q
python3 -m pytest tests/test_router.py -k cooldown
python3 -m pytest tests/ -q
ruff check wiwi/ tests/

# Frontend verification (from web/)
bun run lint
bun run build

# Focused full gate
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

For UI or server changes, also exercise the live path after the automated gate. `web/` is not covered by Ruff; its build runs `tsc -b && vite build`.

The checkout has no usable `.venv`; use ambient `python3` (3.12 here) unless a real venv was explicitly created. Install into the active interpreter with `uv pip install -e ".[dev]"`; add `.[redis]` only when testing the Redis response-cache backend.

```bash
wiwi --config wiwi.yaml
wiwi --reload --reload-dir wiwi
uvicorn wiwi.server.app:create_app_from_config_path --factory
cd web && bun run dev       # proxies API paths to localhost:4000
python3 bench.py            # TTFT, latency, TPS, concurrency sweep
```

`./start.sh` launches both servers but uses npm, installs dependencies, and frees ports 4000/5173; prefer the direct Bun commands for normal frontend work.

Config precedence is `--config` > `WIWI_CONFIG` > `wiwi.yaml`. `load_env()` loads `.env` first without overriding existing variables; missing `os.environ/NAME` values become empty strings. `DATABASE_URL` and `REDIS_URL` override their config values at runtime.

## Ownership and boundaries

- `wiwi/wire/`: inbound codecs, outbound wire encoders, and dialect-specific error bodies.
- `wiwi/providers/`: provider adapters, provider quirks, OAuth/refresh helpers, and the adapter registry.
- `wiwi/ir/`: canonical request/response dataclasses and builtin-tool definitions.
- `wiwi/core/`: generic orchestration and `RequestContext`; do not add concrete provider-name or dialect branches.
- `wiwi/router/`: model groups, key pools, weighted selection, retries, cooldowns, and fallbacks.
- `wiwi/streaming/`: the frozen `IRStreamDelta` contract and stream transformations.
- `wiwi/server/app.py`: composition root, HTTP routes, admin API, lifespan, and static SPA mounting.

Keep concrete dialect/provider behavior in its owning module. Generic core/router code may use provider contracts and `fresh_adapter()`, but must not branch on provider names; `server/app.py` is the composition-root exception.

Adding a provider type requires an adapter or explicit OpenAI-wire fallback, a `get_adapter()`/`fresh_adapter()` path, and a matching router catalog card. `PROVIDER_TYPES` is shared by config, registry, and router import-time assertions. Adding an inbound surface requires its wire module, route, encoder, and error-body mapping.

Use `fresh_adapter(type)` on the request hot path. `get_adapter(type)` returns a shared instance that is reset on hand-out and is only for synchronous, non-await-held use.

`RequestContext` is the single mutable object threaded through the pipeline. Preserve its fields and lifecycle rather than introducing a parallel request-state container.

## Streaming and cache contracts

Every adapter must emit this legal order:

```text
StreamStart
  TextDelta* | ThinkingDelta*
  ToolCallOpen -> ToolCallArgsDelta* -> ToolCallClose (nested per index)
UsageFinal
Finish
StreamEnd xor StreamError
```

`StreamError` may terminate at any point without `Finish`. All `IRStreamDelta` variants are frozen; adapters mutate adapter-local decode state, never deltas.

Do not conflate cache signals: `ctx.cache_hit` means an upstream prompt-cache hit, while `LogEvent.response_cache_hit` means wiwi's exact-match response cache. A response-cache hit must leave `cache_hit=False`. The response cache is non-streaming and deterministic only; `x-wiwi-no-cache` bypasses one request.

Redis backs the response cache only. `wiwi/ratelimit/redis.py` exists, but the app currently constructs the in-memory `RateLimiter`; do not describe Redis as an active rate-limit backend.

## Configuration, data, and runtime

SQLite is the default; PostgreSQL uses `asyncpg`. `DATABASE_URL` is normalized at startup. Schema creation and additive migrations are inline in `ConfigStore`, `DBSink`, and `AuthService`; there is no Alembic workflow.

YAML providers/model entries are rebuilt on every startup. Admin-created providers, keys, deployments, pricing, routing settings, aliases, and cache settings are persisted in the database and layered over YAML at startup. Do not write YAML-sourced entries into the DB.

Stream journals are enabled by default in `.wiwi/journals` (600-second TTL, 1 MiB/request cap) for `x-wiwi-stream-id` plus `Last-Event-ID` replay. `.wiwi/` is runtime scratch and must never be committed.

The built SPA is served from `wiwi/server/static/` at the web root (`/`) when that directory exists; the Vite dev server is standalone and proxies `/admin`, `/auth`, `/public`, `/v1`, and `/health` to port 4000. Do not assume the production UI is mounted at `/admin/ui`.

## Testing conventions

Pytest uses `asyncio_mode = "auto"`; write bare `async def test_*` functions without `@pytest.mark.asyncio`. There is no `conftest.py`: each test file creates its own config/app and `LifespanManager` plus `httpx.ASGITransport` fixture.

Use `respx` for upstream HTTP mocks and Hypothesis for codec/translation invariants. The default admin test key is `sk-wiwi-master-test`. Do not add `--cov`; coverage is not configured or enforced.

New bugfix regressions go in the next unused `tests/test_fix_roundN.py`; find it with `ls tests/test_fix_round*.py` and never backfill a topic test file. Add a test when it protects an observable contract or plausible bug; otherwise smoke-test the changed path live.

## Conventions and guardrails

- Python targets 3.11+, Ruff uses a 100-column line length and ignores only `EXE002`; use Ruff, not Black or isort.
- Use async SQLAlchemy/httpx and `orjson` in hot paths. Library code logs through `structlog` rather than `print`.
- Prefer the owning module's existing API and the surrounding convention; do not create a parallel utility or import through an internal re-export layer.
- Before changing an exported symbol, inspect its references (for example with LSP references) so callers are not missed.
- Do not add new module-level imports of `wiwi.server.app`; it is the FastAPI composition root and reload factory.
- Never commit `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `.verify/`, `.wiwi/`, HAR files, or built/static runtime secrets.
- TypeScript is strict with `verbatimModuleSyntax`, `noUnusedLocals`, and `noUnusedParameters`; use the `@/*` alias and configured path resolution rather than deep relative chains.
- For every UI change, verify desktop and mobile behavior, including 375px width, touch targets, keyboard focus, modal Escape handling, and non-hover-only controls.

## Bugfix workflow

1. Read `AUDIT.md` and search for the same symptom before investigating.
2. Read `UPDATE.md` when the change touches translation/provider edge behavior.
3. Reproduce the failure and add the regression in the next `test_fix_roundN.py` before patching.
4. Keep the fix minimal and in-module; update registry/catalog assertions when extending providers or surfaces.
5. Run the focused test, then the full pytest-plus-Ruff gate; smoke-test live UI/server paths when applicable.
6. Review the diff for unrelated changes, secret leakage, and stale documentation before finishing.
