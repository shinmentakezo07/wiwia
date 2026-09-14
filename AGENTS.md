# Repository Guidelines

## Project Structure & Module Organization

wiwi is a self-hosted unified LLM gateway (FastAPI, Python 3.12): three inbound API dialects route through one internal representation to eleven outbound provider types, then respond in the caller's dialect.

- `wiwi/wire/` — inbound codecs (`openai_chat.py`, `openai_responses.py`, `anthropic_messages.py`): decode/encode/error bodies per dialect.
- `wiwi/providers/` — outbound adapters (`<provider>_adapter.py`) plus `registry.py`, which dispatches on provider type.
- `wiwi/core/`, `wiwi/ir/`, `wiwi/streaming/`, `wiwi/router/`, `wiwi/auth/`, `wiwi/ratelimit/`, `wiwi/cache/`, `wiwi/cost/`, `wiwi/logging_core/` — dialect- and provider-agnostic engine code. Never import `wire`/`providers` symbols here.
- `wiwi/server/` — FastAPI app factory, admin API, metrics.
- `web/` — admin UI and public site (React 19 + TypeScript strict + Vite 6 + Tailwind 4); builds to `wiwi/server/static/`.
- `tests/` — pytest suite; `docs/` — design specs.

## Build, Test, and Development Commands

```bash
python3 -m pytest tests/ -q        # full test suite (must be green)
ruff check wiwi/ tests/            # lint (must be green)
wiwi --config wiwi.yaml            # run server on :4000
cd web && bun run dev              # dev server for the web UI
cd web && bun run build            # tsc -b && vite build
cd web && bun run lint             # eslint web/src
```

Use the ambient `python3`; there is no usable `.venv`. **Bun, not npm**, is authoritative for `web/`. There is no CI or pre-commit hook — run the full `pytest` + `ruff` gate before committing.

## Coding Style & Naming Conventions

- Ruff only: line length 100, target py311. No mypy.
- Async throughout; Pydantic v2 for config/admin schemas, frozen dataclasses for IR/streaming types. Use `structlog`, never `print`, in library code.
- Wire modules named after the dialect; adapters as `<provider>_adapter.py`; core code must not branch on dialect or provider names.

## Testing Guidelines

- pytest with `asyncio_mode = "auto"`: write bare `async def test_…`, no decorators. Mock upstreams with decorator-form `@respx.mock`.
- No `conftest.py`; each test file builds its own config/app fixtures.
- New bug regressions go in the next unused `tests/test_fix_roundN.py` (check with `ls tests/test_fix_round*.py`).

## Commit & Pull Request Guidelines

- Commits: imperative present tense, capitalized, no prefix tags (`Add auth keys and service`). One logical change per commit.
- Work directly on `main` — no feature branches or PRs in this single-developer repo.
- Bugfixes: read `AUDIT.md` first; report found bugs there and link fixes to entries. Read `UPDATE.md` before touching translation-layer code.

## Security & Configuration Tips

Never commit `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.jsonc`, `*.har`, or anything under `.wiwi/` or `.verify/` — they hold live keys and runtime state. Provider keys enter via `os.environ/NAME` interpolation in config; admin endpoints require the master key (`WIWI_MASTER_KEY`).
