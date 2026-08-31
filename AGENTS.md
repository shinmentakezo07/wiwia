# AGENTS.md — wiwi

wiwi is a self-hosted unified LLM gateway proxy. Three inbound dialects (OpenAI Chat, OpenAI Responses, Anthropic Messages) funnel through one canonical IR out to any provider (OpenAI, Anthropic, Gemini, OpenRouter, NVIDIA NIM, B.AI, GMI, Cline/OAuth, any OpenAI-compatible URL), always re-encoded in the caller's dialect. FastAPI backend + React admin SPA.

## Verbatim commands (these actually work in this checkout)

```bash
# tests — use the ambient python3/pytest, NOT .venv
python3 -m pytest tests/ -q                          # full suite (896 pass, ~52s)
python3 -m pytest tests/test_codecs.py -q            # single file
python3 -m pytest tests/test_router.py -k cooldown   # single test by name

# lint — ruff is on PATH
ruff check wiwi/ tests/                              # line-length 100, target py311

# verify before claiming done: run pytest AND ruff, both must be green
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

**CRITICAL: `.venv/bin/python` is broken in this checkout.** `.venv` is a symlink to a Python 3.14 venv with no site-packages (no pytest, no wiwi). The project is installed into the ambient interpreter. Never use `.venv/bin/python` — use ambient `python3` / `pytest` / `ruff` (Python 3.12, pytest 9.1.1, ruff on PATH).

## Admin UI (web/, bun not npm)

```bash
cd web && bun install && bun run dev     # Vite dev server, proxies /admin /v1 /auth /public /health → :4000
cd web && bun run build                  # tsc -b && vite build → ../wiwi/server/static/ (served at /admin/ui)
cd web && bun run lint                   # eslint src — web/ is NOT covered by ruff
```

Both `web/bun.lock` and `web/package-lock.json` exist (node_modules is populated from npm). Prefer `bun`; stick to one tool per session. Note `start.sh` runs `npm install` + `npm run dev` — it predates the bun migration and is stale (but functional).

## Test conventions

- `asyncio_mode = "auto"` — write bare `async def test_…`, no `@pytest.mark.asyncio`.
- Mock upstream HTTP with `respx`; app-level tests via `asgi-lifespan` + `httpx.ASGITransport` (see `tests/test_integration.py`).
- Property-based round-trip tests use `hypothesis` (`tests/test_property_roundtrip.py`).
- **New bugfix regressions go into a numbered thematic file `tests/test_fix_roundN.py`, NOT topic files.** Next unused is `test_fix_round14.py` (13 exists; the "round6"/"round9" pointers in CLAUDE.md and `.claude/rules/wiwi-bugfix-workflow.md` are stale).

## Architecture (hub-and-spoke — do not break this)

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

- Every direction goes dialect → `wiwi/ir/` → provider. **Never** add dialect/provider branching in `core/`, `router/`, or `auth/` — dialect logic belongs in `wiwi/wire/`, provider logic in `wiwi/providers/` + a line in `registry.get_adapter()`.
- `registry.py` has an import-time `assert` that fails loudly if a provider type is added to `PROVIDER_TYPES` without a matching branch — adding a provider is: new adapter + one branch, and the assert catches a forgotten branch.
- Request flow: `server/app.py:run_chat_like` (decode → auth → rate limit → router retries/fallbacks → gateway complete/stream) → back out through wire encoders. `core/context.py:RequestContext` is the single mutable object threaded through every stage.
- **Streaming contract** (`streaming/deltas.py`): exactly one `StreamStart`; `ToolCallOpen→ArgsDelta*→Close` nested per index; exactly one `UsageFinal` after last content delta; then `Finish`; then `StreamEnd` xor `StreamError`. Adapters guarantee legality; encoders never defend against malformed sequences. Exception: `StreamError` may terminate at any point with no preceding `Finish`.
- Two provider quirks live in `providers/` (never core): NVIDIA NIM rejects JSON Schema boolean subschemas and params named `type` (`nim_tool_schema.py`); Cline is OAuth with on-demand token refresh (`cline_oauth.py`, `cline_auto_refresh.py`).

## Docs vs. code

`docs/ARCHITECTURE.md` / `docs/CORE.md` are design specs that intentionally run ahead of implementation (handler pipeline, DeltaBus, Postgres/Redis backends, deeper DB schema are specified but not built). **When docs and code disagree, trust the code.** Read these before work:
- `UPDATE.md` — changelog of cross-provider translation fixes. Read first for reasoning/thinking params, tool_result, `content: null`, OpenRouter `reasoning`, `stream_options`, upstream error extraction.
- `AUDIT.md` — register of known bugs (severity, file:line, fix sketch). Read before bugfix work so you don't redo a known fix.

## Guardrails

- **Never commit `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, anything under `.verify/`, or `opencode.json(c)`** — gitignored, they hold live provider/master keys and runtime state.
- Provider keys enter via `os.environ/NAME` interpolation in `wiwi.yaml`; master key from `WIWI_MASTER_KEY`.
- Admin endpoints (`/admin/*`) require the master key; client traffic authenticates with virtual keys (`sk-wiwi-…`).
- Virtual keys are SHA-256-hashed at rest with constant-time compare; plaintext returned only once at mint.
- Ruff-only lint (no black/isort); `line-length = 100`, `target-version = "py311"`. Pydantic v2 for config; plain dataclasses for IR/streaming hot paths. Async throughout (`httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths). Never `print` from library code — use `structlog`.
- `web/src/pages/` mixes ~15 admin console pages (Dashboard, Providers, VirtualKeys, RequestLogs…) with ~30 public marketing pages (Landing, Pricing, Blog…) — don't assume a page is admin-facing from directory alone.

## Bugfix workflow

`.claude/rules/wiwi-bugfix-workflow.md` is binding: read `systematic-debugging` first (root cause before patching), write the failing regression test first (TDD), self-review via `requesting-code-review`. New tests go in `test_fix_roundN.py`.
