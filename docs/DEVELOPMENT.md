# wiwi — Development Guide

Dev setup, conventions, testing, and the pre-completion gate. The command source of truth is the repo-root `AGENTS.md`; this page is the developer-friendly walkthrough.

---

## 1. Setup

```bash
# Backend — use ambient python3 (3.12 in this checkout). NEVER .venv/bin/python
# (the .venv symlink here points at an empty venv with no site-packages).
python3 --version                  # 3.12
uv pip install -e .[redis]         # optional [redis] extra for the Redis rate limiter/cache

# Frontend — bun is authoritative (web/bun.lock present). Never mix package managers.
cd web && bun install
```

Tools expected on PATH: `python3` 3.12, `pytest` 9.1.1, `ruff`, `bun`.

## 2. Run

```bash
wiwi --config wiwi.yaml            # prod: load config → uvicorn
wiwi --reload                      # dev: factory import + reload
wiwi --config wiwi.yaml --port 4000 --host 0.0.0.0

# direct uvicorn
uvicorn wiwi.server.app:create_app_from_config_path --factory

# backend + Vite dev server together
./start.sh                         # env knobs: WIWI_PORT, WIWI_WEB_PORT, WIWI_RELOAD, WIWI_RELOAD_DIRS, WIWI_BIN
```

Frontend dev server (`cd web && bun run dev`) proxies `/admin /v1 /auth /public /health` → `:4000`. Production SPA build: `cd web && bun run build` → `wiwi/server/static/` (served at `/admin/ui`; gitignored — builds produce it).

Load test: `python3 bench.py` (async httpx; TTFT, p50/p95, TPS, concurrency sweep).

## 3. Testing

**Framework**: pytest 8+ + pytest-asyncio 0.23 with `asyncio_mode = "auto"` — write bare `async def test_*`, **no** `@pytest.mark.asyncio` decorator.

```bash
python3 -m pytest tests/ -q                    # full suite — keep green
python3 -m pytest tests/test_codecs.py -q      # single file
python3 -m pytest tests/test_router.py -k cooldown   # by name
```

Conventions (binding — copy the surrounding pattern, don't invent a parallel one):

- **No `conftest.py`** anywhere. Each test file builds its own `_config()` factory and its own `LifespanManager + httpx.ASGITransport` client fixture inline.
- **Default master key** for admin-auth'd tests: `sk-wiwi-master-test` via `Authorization: Bearer …` (see `tests/test_integration.py`).
- **Upstream mocking**: `respx`, decorator form preferred (`@respx.mock` + `respx.post(url).respond(...)`; the context-manager form is broken in respx 0.23 + httpx 0.28). `side_effect=[...]` for multi-response failover tests.
- **Property-based**: `hypothesis` ≥ 6.100 (persistent cache in `.hypothesis/`). Used in `test_property_roundtrip.py`, `test_translation_enhancements.py`, `test_web_search_translation.py`, `test_tool_translation_round2.py`.
- **Fixtures**: `@pytest.fixture` and `@pytest_asyncio.fixture` both work; newer files (rounds 18+) prefer `@pytest_asyncio.fixture`.
- **No pytest-cov / no coverage config.** Don't add `--cov`.
- **Numbered bugfix regressions**: new bugfix tests go into the next unused `tests/test_fix_roundN.py`. Confirm with `ls tests/test_fix_round*.py` — never assume the number, and never back-fill into topic files like `test_codecs.py`.
- Only add a test when it defends an observable contract or a plausible bug; otherwise smoke-test the changed path live.

## 4. Lint & the pre-completion gate

Ruff only (no black/isort). `line-length = 100`, `target-version = "py311"`, `EXE002` ignored. `web/` is **not** covered by ruff — it has its own ESLint flat config (`cd web && bun run lint`), and `tsc -b` runs as part of `bun run build`.

**Both must be green before claiming any work done:**

```bash
python3 -m pytest tests/ -q && ruff check wiwi/ tests/
```

For UI/server changes, additionally exercise the live path (launch server, hit endpoint, observe) instead of only trusting tests.

## 5. Architecture invariants (binding)

1. **No dialect/provider branching outside `wiwi/wire/` and `wiwi/providers/`.** `core/`, `router/`, `auth/`, `streaming/`, `cache/`, `cost/`, `logging_core/`, `ir/` must never import symbols from `wiwi.wire` or `wiwi.providers`. Leakage is silent wrong-language routing.
2. **Import from the module that owns the symbol**, not from a re-export layer.
3. **New provider type or inbound route ⇒ update `registry.py`'s coverage assert.** `PROVIDER_TYPES` in `config.py` is the single source of truth; the import-time assert catches a forgotten branch.
4. **Prefer existing module APIs.** A second convention beside an existing one is prohibited.
5. **Run `lsp references` before editing an exported symbol** — missed callsites are bugs.
6. **Never import `wiwi.server.app` at module level in a library module** (lifespan/startup side effects). Only `wiwi/main.py` and test fixtures do.
7. **Frozen dataclasses on hot paths**: every `IRStreamDelta` variant is `@dataclass(frozen=True)`; adapters mutate their own instance state, never deltas.
8. **Adapter singletons**: `get_adapter(type)` = shared (reset on hand-out, sync use only); `fresh_adapter(type)` = private instance for the request hot path (adapters hold per-stream decode state across awaits).
9. **Async throughout**: `httpx.AsyncClient`, SQLAlchemy async, `orjson` in hot paths. Never `print` from library code — use `structlog`.

## 6. Code conventions

- **Python**: `requires-python = ">=3.11"`. Pydantic v2 for config/admin schemas; plain `@dataclass(frozen=True)` for IR and streaming hot-path types.
- **Naming**: tests `test_*.py`; bugfix regressions `test_fix_roundN.py`.
- **Commits**: imperative present tense, capitalized, no prefix tags; verify pytest + ruff before committing.
- **Never commit**: `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, anything under `.verify/` or `.wiwi/`, `opencode.json(c)` — all gitignored; they hold live provider/master keys and runtime state.
- **Frontend**: React 19, Vite 6, Tailwind 4, TanStack Query 5, Recharts, react-router-dom v7, lucide-react. TypeScript `strict` + `verbatimModuleSyntax` + `noUnusedLocals/Parameters`; path aliases per tsconfig/Vite config, no `../../../../` chains.

## 7. UI/UX compatibility rule (binding)

Every UI/UX change must work on **desktop (mouse/keyboard)** and **mobile (touch, narrow viewport)** before it's "done":

1. No layout breakage below ~375 px width (test 375×812 and wide desktop; reflow/stack/collapse, never clip).
2. Tap targets ≥ 44×44 px (Apple HIG) / 48×48 dp; nothing hover-only — every hover reveal also responds to focus and touch.
3. Keyboard navigation works: sensible Tab order, visible focus styles, modal focus trap + Escape-to-close.
4. Relative units (`rem`/`em`/`%`) over fixed `px` for type/spacing; no silent truncation that hides information.
5. Assume every page (including admin) can open on a phone.

Verify in a mobile-sized viewport and a desktop viewport; put a screenshot or short description in the commit message/PR description.

## 8. Bugfix workflow (binding)

1. Read `AUDIT.md` first — avoid redoing a known fix; record any newly discovered bug there (severity badge, file:line, trigger, fix sketch) before or alongside fixing it, and mark fixed entries `**Status: fixed**`.
2. Read `UPDATE.md` — the **binding changelog** for OpenAI ↔ Anthropic cross-provider translation, the OpenRouter adapter, and multi-turn conversation fixes. Required reading before touching `wiwi/wire/` codecs, the OpenAI/Anthropic/OpenRouter adapters, or anything handling `reasoning_effort`/`reasoning`, `tool_result`, `content: null`, `stream_options`, or upstream error extraction. Add an entry when a new fix lands in those areas.
3. Follow systematic debugging — root cause before patching.
4. Write the failing regression test FIRST into the next `test_fix_roundN.py`.
5. Implement minimally and in-module (dialect → `wiwi/wire/`; provider → `wiwi/providers/` + registry branch).
6. Self-review (requesting-code-review) before claiming done.
7. Gate: `python3 -m pytest tests/ -q && ruff check wiwi/ tests/` both green; live-path smoke test for UI/server changes.

## 9. Docs map

| Doc | Contents |
|---|---|
| [QUICKSTART.md](QUICKSTART.md) | Install, run, first request |
| [CONFIG.md](CONFIG.md) | `wiwi.yaml` reference, env vars |
| [API_REFERENCE.md](API_REFERENCE.md) | All endpoints |
| [PROVIDERS.md](PROVIDERS.md) | The 11 provider types, quirks, key pools |
| [STREAMING.md](STREAMING.md) | Delta taxonomy, pump, failover/journals, guards |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, request pipeline, recovery |
| [CORE.md](CORE.md) | Internal subsystem reference |
| [ADMIN.md](ADMIN.md) | Admin UI + admin/auth API |
| [DEVELOPMENT.md](DEVELOPMENT.md) | This page |

`docs/superpowers/{specs,plans}/` holds dated feature specs and implementation plans (historical record). `AUDIT.md` tracks live/fixed bugs; `UPDATE.md` is the translation-fix changelog.
