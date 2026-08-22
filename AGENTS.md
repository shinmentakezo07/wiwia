# Repository Guidelines

## Project Overview

**wiwi** is a self-hosted unified LLM gateway proxy (LiteLLM-style). Three inbound API dialects — OpenAI Chat Completions (`/v1/chat/completions`), OpenAI Responses (`/v1/responses`, Codex CLI), Anthropic Messages (`/v1/messages`, Claude Code) — route through one canonical IR to any outbound provider (OpenAI, Anthropic, Gemini, any OpenAI-compatible URL). Responses are always re-encoded in the caller's dialect, so e.g. Claude Code can be backed by GPT. Adds virtual keys, budgets, RPM/TPM limits, key pools (smooth WRR), retries/cooldowns/fallbacks, cost tracking, request logs, and an admin web UI.

Python ≥3.11, FastAPI, httpx, SQLAlchemy async (SQLite default), managed with `uv`. Admin UI: React 19 + TypeScript + Vite + Tailwind 4. **Never commit `wiwi.yaml`, `key.md`, or `wiwi.db`** — live provider keys and runtime state (gitignored, along with `.env` and `.verify/`).

## Architecture & Data Flow

Hub-and-spoke: every direction goes `dialect → IR → provider`. No pairwise converters. Core code (`core/`, `router/`, `auth/`) never branches on dialect or provider name. Adding an inbound surface = one module in `wiwi/wire/`; adding a provider = one adapter implementing the `ProviderAdapter` protocol (`providers/base.py`) plus a line in `providers/registry.py::get_adapter` (`openai-compatible` deliberately shares `OpenAIAdapter`).

Trace the whole flow through **`server/app.py::run_chat_like`** (app.py:194) — the shared inbound path for all three surfaces:

```
decode (wire codec → ir.Request)
  → auth (master/virtual key, budget pre-check)
  → rate limit (global + per-key RPM/TPM windows)
  → router.execute_with_retries (deployment tier-1, key-pool smooth WRR tier-2,
                                 cooldowns on failures, fallback groups)
  → Gateway.complete / Gateway.stream (providers/* adapter over httpx)
  → price (cost/pricing.py) → build_log_event → logging sinks
  → encode back out through the caller's wire encoder (SSE when streaming)
```

Key mechanics:

- **Streaming contract is sacred** (`streaming/deltas.py`, spec in `docs/CORE.md` §7): exactly one `StreamStart` first; `ToolCallOpen → ToolCallArgsDelta* → ToolCallClose` strictly nested per index; `UsageFinal` exactly once after the last content delta; then `Finish`; then exactly one of `StreamEnd | StreamError` — `StreamError` may terminate at any point as the abnormal terminal. Adapters guarantee legal sequences; encoders never defend against malformed ones.
- **Error model**: everything normalizes to `WiwiError(status, etype, message, retryable, retry_after)` via `error_from_provider_status`. 401/403 keep their status but are `retryable=True` so the pool fails over to the next key (`ProviderKey.mark_invalid`). Each wire codec renders dialect-correct error envelopes via its own `error_body`.
- Retry/failover lives only in `router.execute_with_retries`; adapters stay single-shot.
- Logging has three streams that never mix (`logging_core/subsystem.py`): request → batched DB sink + SSE broadcast; proxy → stdout JSON + SSE; audit → synchronous DB write. Nothing blocks a response; slow sinks degrade to drop+count.

## Key Directories

| Path | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …` → `load_config` → `create_app` → uvicorn) |
| `wiwi/config.py` | YAML → pydantic models; recursive `os.environ/NAME` interpolation; fail-fast `ConfigError` with file/line context |
| `wiwi/ir/types.py` | Canonical IR: dataclass parts (`TextPart`, `ImagePart`, `ToolUsePart`, `ToolResultPart`, `ThinkingPart`, reserved audio/document), `Message`, `ToolChoice*`, `GenParams`, `Request`, `Usage`, `AssistantTurn` |
| `wiwi/wire/` | Inbound codecs: each module = `decode_request`, `encode_response`, a `*StreamEncoder` FSM, `error_body` |
| `wiwi/providers/` | Outbound adapters (`openai_adapter.py`, `anthropic_adapter.py`, `gemini_adapter.py`) + `base.py` protocol/errors + `registry.py` |
| `wiwi/router/router.py` | Model groups (`Deployment`), key pools (`ProviderKey`: weight/cooldown/invalid), `execute_with_retries`, fallbacks, `model_group_alias` |
| `wiwi/core/gateway.py` | Surface-agnostic engine: `complete`/`stream`, stream pump owns `dep.inflight` until the last delta, pricing hooks, `build_log_event` |
| `wiwi/core/context.py` | `RequestContext` — single mutable holder threaded through handlers, router, pump (attempts, stream state, usage, cost, outcomes) |
| `wiwi/streaming/` | `deltas.py` delta taxonomy; `sse.py` incremental upstream SSE parser + framing |
| `wiwi/auth/` | `keys.py`: generate/SHA-256-hash/constant-time verify (`sk-wiwi-` prefixes); `service.py`: `AuthService`, SQLite `vkeys` table, 60 s cache with active eviction |
| `wiwi/cost/pricing.py` | `CostEngine` over LiteLLM-shaped `model_prices.json`; USD/token rounded to 8 dp; unpriced models cost 0; `estimate_tokens` chars/4 fallback |
| `wiwi/ratelimit/memory.py` | Sliding-window RPM/TPM, global + per-key scopes; prospective estimated-token reservation replaced by `record_tokens()` |
| `wiwi/logging_core/` | Log events, queues/workers/sinks, SSE ring buffers with `Last-Event-ID` replay |
| `wiwi/server/app.py` | FastAPI factory; `run_chat_like()` shared inbound flow; `/admin/*` API; SPA static mount at `/admin/ui`; `/health` |
| `wiwi/server/stats.py` | Admin rollups as pure functions over LogEvent lists (unit-testable, no DB) |
| `web/` | Admin UI source (pages in `web/src/pages/`, API client in `web/src/api/client.ts`) |
| `tests/` · `docs/` | Pytest suite · design specs |

## Development Commands

```bash
# setup
uv venv && uv pip install -e ".[dev]"        # + pytest, pytest-asyncio, respx, asgi-lifespan, ruff
uv pip install -e ".[pg]"                    # optional Postgres backend (asyncpg); ".[redis]" also exists

# run gateway (default 0.0.0.0:4000; needs wiwi.yaml — cp wiwi.yaml.example wiwi.yaml)
export OPENAI_API_KEY=... WIWI_MASTER_KEY=sk-wiwi-master-...
wiwi --config wiwi.yaml [--host H] [--port P]

# tests (~12 s full suite, all green expected)
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_codecs.py -q            # single file
.venv/bin/python -m pytest tests/test_router.py -k cooldown   # by name substring

# lint — no CI configured; ruff IS the gate
.venv/bin/ruff check wiwi/ tests/            # line-length 100, target py311

# admin UI
cd web && bun install
bun run dev                                  # Vite dev server; proxies /admin and /v1 → localhost:4000
bun run build                                # tsc -b && vite build --base=/admin/ui/ → wiwi/server/static/

# docker / bench
docker compose up --build                    # --profile pg adds postgres 16
.venv/bin/python bench.py [-n 10 -c 1,4,16]  # TTFT/latency/TPS vs a RUNNING gateway (edit TARGETS/MODEL at top)
```

Run full pytest + ruff before claiming work done or committing.

## Code Conventions & Common Patterns

- Ruff only (`line-length = 100`, py311). Pydantic v2 for config; plain (frozen where possible) dataclasses for IR/streaming hot paths — don't convert IR types to pydantic.
- Async throughout: SQLAlchemy async, `httpx.AsyncClient`, `asyncio.Queue` stream pumps; `orjson` in hot paths; `structlog` for logging — never `print` from library code.
- Env interpolation: any string value in `wiwi.yaml` may be `os.environ/NAME`.
- Secrets: virtual keys SHA-256-hashed at rest, constant-time compare, plaintext shown once at generation; masked display via `auth.keys.mask_key`.
- Naming: wire modules named after dialect (`openai_chat.py`); adapters `<provider>_adapter.py`; tests `test_<area>.py` with descriptive names.
- Commits: imperative present tense, capitalized, no prefix tags (`Add auth keys and service`). One logical change per commit.
- UI conventions: one routed page per admin concern in `web/src/pages/*.tsx`; shared formatting in `web/src/lib/format.ts`; production bundle must land in `wiwi/server/static/` served at `/admin/ui` with SPA history fallback (`SPAStaticFiles`, app.py:823) — rebuild after UI changes if testing against the Python server instead of the Vite dev server.
- UI work follows a spec-first flow: dated design docs in `docs/superpowers/specs/` and plans in `docs/superpowers/plans/`.

## Important Files

- `pyproject.toml` — deps, `[project.scripts] wiwi = "wiwi.main:cli"`, `[tool.pytest.ini_options]` (`asyncio_mode="auto"`, `testpaths=["tests"]`), ruff config.
- `wiwi.yaml.example` — tracked template: `providers:` (key pools w/ weights), `model_list:` (`wiwi_params`), `router_settings:` (retries/cooldowns/fallbacks/aliases), `general_settings:` (master_key, database_url), `wiwi_settings:` (`drop_params`, port).
- `Dockerfile` — multi-stage (uv builder → `python:3.12-slim-bookworm`), non-root, `EXPOSE 4000`, `/health` healthcheck, `ENTRYPOINT ["wiwi"]`.
- `docker-compose.yml` — mounts `./wiwi.yaml:ro`; passes `WIWI_MASTER_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`; optional `pg` profile.
- `bench.py` — standalone benchmark harness against a running gateway.
- `web/vite.config.ts` — dev proxy (`/admin`, `/v1` → :4000) and prod `outDir: ../wiwi/server/static`.
- `tests/test_integration.py` — reference pattern for app-level tests (see Testing & QA).
- `docs/` — `ARCHITECTURE.md` (system design), `CORE.md` (runtime/streaming spec), `ADMIN.md` (admin/key-pool design), `MVP.md` (scope + gap register, stable "G" ids), `PLAN.md` (phases M1–M6), `TECHSTACK.md` (decisions + upgrade triggers).

## Runtime/Tooling Preferences

- Package management: **uv**; `.venv` is a symlink into a shared uv env — invoke tools directly as `.venv/bin/python`, `.venv/bin/ruff`.
- HTTP: `httpx[http2]` only upstream; mock with `respx` in tests. Serve SSE with `sse-starlette`; parse upstream SSE with the custom parser in `streaming/sse.py`.
- Node tooling: **Bun is authoritative for `web/`** (`bun.lock`; a stale `package-lock.json` also exists — don't switch managers casually).
- Server: uvicorn; keep the app a pure ASGI callable (no uvicorn-specific APIs).

## Testing & QA

- pytest + pytest-asyncio with `asyncio_mode = "auto"` — bare `async def test_…`, **no** `@pytest.mark.asyncio`.
- Upstream mocking: `@respx.mock` + `respx.post("https://api.openai.com/v1/chat/completions").respond(json=…)`.
- End-to-end skeleton (copy from `tests/test_integration.py`): inline `WiwiConfig` (master key `"sk-wiwi-master-test"`, `database_url="sqlite+aiosqlite:///:memory:"`) → `asgi_lifespan.LifespanManager(app)` → `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`.
- Codec tests: pure decode→encode round-trips plus scripted `IRStreamDelta` lists fed to stream encoders asserting exact frames (`tests/test_codecs.py`).
- Regression files are thematic: `test_audit_fixes.py` (bugs 1–20), `test_fix_round2.py` (Gemini key appending, 401/403 key invalidation, timeout cooldowns, TPM reservation replacement), `test_fix_round3.py` (negative auth-cache staleness, allowlist model-listing bypass, admin weight validation), `test_cache_hardening.py` (streaming usage parsing, `ctx.cache_hit`, Anthropic system `cache_control`). Put new bug-fix regressions alongside these.
- No shared `conftest.py` — fixtures live per-file (e.g. the `client` fixture). Follow suit for small suites.
- New features need tests covering observable contract (surface behavior, routing semantics, delta legality), not plumbing.

## Docs vs. Code

`docs/` specs intentionally run **ahead of the implementation**: handler pipeline (`handlers/` package in CORE.md §1), DeltaBus-style runtime organization, Alembic migrations, AES-GCM key encryption, Redis backends, and ADMIN.md's session-cookie auth + Next.js UI are specified but not built (shipped reality: flat modules, React/Vite SPA, Bearer master-key auth). When docs and code disagree, trust the code — or treat the doc section as the spec for work you're about to do.
