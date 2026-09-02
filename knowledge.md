# knowledge.md — wiwi agent knowledge

## What this is

Self-hosted unified LLM gateway proxy (FastAPI + React admin SPA). Three inbound dialects
(OpenAI Chat `/v1/chat/completions`, OpenAI Responses `/v1/responses`, Anthropic Messages
`/v1/messages`) are decoded into one canonical IR (`wiwi/ir/`) and re-encoded to any of 9
outbound providers (openai, anthropic, gemini, openrouter, nvidia-nim, bai, cline-OAuth,
gmicloud, openai-compatible) — always answered back in the caller's dialect.

## Where key code lives

| Path | Role |
|---|---|
| `wiwi/wire/` | Inbound codecs/encoders: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` |
| `wiwi/ir/` | Canonical IR types (tagged parts, messages, tools, params, usage) |
| `wiwi/providers/` | Outbound adapters + `registry.py` (one branch per provider type; import-time assert catches missing branches) |
| `wiwi/router/router.py` | Key pools (smooth WRR), cooldowns, retries, fallbacks |
| `wiwi/server/app.py` | FastAPI app; `run_chat_like` = decode → auth → rate limit → router → gateway |
| `wiwi/core/context.py` | `RequestContext` — single mutable object threaded through every stage |
| `wiwi/core/gateway.py` | Surface-agnostic execution engine + pricing + log events |
| `wiwi/streaming/` | IR stream delta taxonomy, SSE, coalesce, resume, validation |
| `wiwi/auth/` | Virtual keys (SHA-256 at rest), budgets, users |
| `wiwi/wire/` quirks | NVIDIA NIM tool-schema sanitizer, Cline OAuth refresh |
| `web/` | Admin SPA (React 19 + TS + Vite + Tailwind 4, **bun**) |
| `tests/` | pytest suite; bugfix regressions in `test_fix_roundN.py` |

## Commands (verified in this checkout)

```bash
python3 -m pytest tests/ -q                # full suite (~900 tests, ~52s)
python3 -m pytest tests/test_codecs.py -q  # single file
ruff check wiwi/ tests/                    # lint (line-length 100, py311)
python3 -m pytest tests/ -q && ruff check wiwi/ tests/   # verify before claiming done

# Admin UI (bun, NOT npm)
cd web && bun install && bun run dev       # Vite dev, proxies /admin /v1 /auth → :4000
cd web && bun run build                    # tsc -b && vite build → wiwi/server/static/
cd web && bun run lint                     # eslint (web/ is NOT covered by ruff)

# Run the gateway
wiwi --config wiwi.yaml                    # or: wiwi -c wiwi.yaml --host 0.0.0.0 --port 4000
```

## Gotchas / constraints (read before touching code)

- **`.venv/bin/python` is broken in this checkout** (symlink to empty Py3.14 venv). Use ambient
  `python3` / `pytest` / `ruff` (Py 3.12, pytest 9.1.1). Never `.venv/bin/python`.
- **Hub-and-spoke is binding**: dialect logic only in `wiwi/wire/`, provider logic only in
  `wiwi/providers/` + one branch in `registry.get_adapter()`. Never branch on dialect/provider
  in `core/`, `router/`, `auth/`. `registry.py` asserts loudly at import if a provider type
  lacks a branch.
- **Streaming contract** (`wiwi/streaming/deltas.py`): one `StreamStart`; `ToolCallOpen→ArgsDelta*→Close`
  nested per index; exactly one `UsageFinal`; then `Finish`; then `StreamEnd` xor `StreamError`.
  Adapters guarantee legality; encoders never defend. Exception: `StreamError` may terminate
  anytime with no `Finish`.
- **Docs run ahead of code** (`docs/ARCHITECTURE.md`, `docs/CORE.md` describe unbuilt handler
  pipeline, DeltaBus, Postgres/Redis backends). **Trust the code over docs.**
- Read `UPDATE.md` (translation-fix changelog: reasoning/thinking params, tool_result,
  `content: null`, OpenRouter `reasoning`, `stream_options`) and `AUDIT.md` (known bugs with
  file:line + fix sketches) before bugfix work.
- Tests: `asyncio_mode = "auto"` (bare `async def test_…`); mock upstream HTTP with `respx`;
  app-level tests via `asgi-lifespan` + `httpx.ASGITransport`; property round-trips via `hypothesis`.
- **New bugfix regressions go in numbered `tests/test_fix_roundN.py`** (next: round14; the
  round6/round9 pointers in CLAUDE.md and `.claude/rules/…` are stale).
- Ruff only (no black/isort), `line-length = 100`, `target-version = "py311"`. Pydantic v2 for
  config; plain dataclasses for IR/streaming hot paths; `orjson` in hot paths; `structlog`
  never `print`.
- **Never commit**: `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, `.verify/*`, `opencode.json(c)` —
  live keys/runtime state. Provider keys via `os.environ/NAME` in `wiwi.yaml`; master key via
  `WIWI_MASTER_KEY`.
- Admin endpoints need master key; client traffic uses virtual keys (`sk-wiwi-…`, SHA-256 at
  rest, plaintext shown once at mint).
- `web/` uses **bun** (not npm); `web/bun.lock` + `package-lock.json` both exist (node_modules
  from npm). `start.sh` still runs npm — stale but functional. `web/` is eslint-only, NOT ruff.
- `web/src/pages/` mixes ~15 admin pages with ~30 marketing pages — directory alone doesn't
  tell you if a page is admin-facing.
- `web/tsconfig.json` + `bun run build` output lands in `wiwi/server/static/` (served at
  `/admin/ui`); rebuild after UI changes or the served bundle is stale.

## Test conventions

- `asyncio_mode = "auto"` — bare `async def test_…`, no `@pytest.mark.asyncio`.
- Upstream HTTP mocked with `respx`; app-level tests via `asgi-lifespan` + `httpx.ASGITransport`.
- Property round-trips with `hypothesis` (`tests/test_property_roundtrip.py`).
- Full suite: ~900 tests, ~52s. Verify before claiming done:
  `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`
