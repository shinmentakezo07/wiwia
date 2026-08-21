# Repository Guidelines

## Project Overview

**wiwi** is a self-hosted unified LLM gateway proxy (LiteLLM-style). It accepts three inbound API dialects — OpenAI Chat Completions, OpenAI Responses (Codex CLI), and Anthropic Messages (Claude Code) — translates them through a canonical internal representation (IR), and routes to any outbound provider (OpenAI, Anthropic, Gemini, OpenAI-compatible). Responses are re-encoded in the caller's dialect, so any client can be backed by any provider. Python ≥3.11, FastAPI, httpx, SQLAlchemy async (SQLite default), managed with `uv`.

## Project Structure

| Path | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …`) |
| `wiwi/config.py` | YAML → pydantic models; env interpolation; fail-fast validation |
| `wiwi/ir/types.py` | Canonical IR: tagged parts, messages, tools, params, usage |
| `wiwi/wire/` | Inbound codecs: `openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` |
| `wiwi/providers/` | Outbound adapters: `openai`, `anthropic`, `gemini` + `base.py` protocol |
| `wiwi/router/router.py` | Model groups, key pools (smooth WRR), cooldowns, retries, fallbacks |
| `wiwi/core/gateway.py` | Surface-agnostic execution engine, pricing, log events |
| `wiwi/core/context.py` | `RequestContext` — mutable holder threaded through the pipeline |
| `wiwi/streaming/` | `IRStreamDelta` taxonomy + SSE helpers |
| `wiwi/auth/` | Key generation/hashing + budget/spend service |
| `wiwi/cost/pricing.py` | Cost engine + token estimation fallback |
| `wiwi/ratelimit/` | RPM/TPM sliding-window limits |
| `wiwi/logging_core/` | Log events + SSE ring buffer for admin tail |
| `wiwi/server/app.py` | FastAPI factory: routes, middleware, `/admin/*` |
| `tests/` | Pytest suite with `respx` HTTP mocks and ASGI end-to-end |
| `docs/` | Architecture and design specs |

The hub-and-spoke design means: no pairwise converters. Every direction goes `dialect → IR → provider`. Adding an inbound surface = one module in `wiwi/wire/`; adding a provider = one adapter in `wiwi/providers/` plus a line in the registry. Core code never branches on dialect or provider name.

## Build, Test, and Run

```bash
# Setup
uv venv && uv pip install -e ".[dev]"

# Run server (needs wiwi.yaml — copy and edit the example)
cp wiwi.yaml.example wiwi.yaml
wiwi --config wiwi.yaml [--host H] [--port P]   # default 0.0.0.0:4000

# Tests (all pass; ~1s)
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_codecs.py -q            # single file
.venv/bin/python -m pytest tests/test_router.py -k cooldown    # single test by name

# Lint (ruff, line-length 100, target py311)
.venv/bin/ruff check wiwi/ tests/

# Docker
docker compose up --build
```

## Coding Style & Conventions

- Python ≥3.11, ruff for lint (`line-length=100`, `target-version="py311"`).
- Follow the hub-and-spoke pattern strictly: never add dialect- or provider-specific branches in `core/`, `router/`, or `auth/`. All dialect logic lives in `wiwi/wire/`; all provider logic in `wiwi/providers/`.
- The streaming contract (`wiwi/streaming/deltas.py`) is sacred: adapters guarantee legal delta sequences; encoders never defend against malformed ones. See `docs/CORE.md` for the taxonomy.
- Pydantic v2 models for config and wire types. Async throughout (SQLAlchemy async, httpx async).
- Tests use `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed), `respx` to mock upstream HTTP, and `asgi_lifespan.LifespanManager` + `httpx.ASGITransport` for end-to-end (see `tests/test_integration.py`).

## Testing Guidelines

- Framework: pytest with `pytest-asyncio` (auto mode).
- Mocks: `respx` for upstream HTTP; `asgi_lifespan` + `httpx.ASGITransport` for app-level tests.
- Naming: `test_*.py` files, `test_*` functions, descriptive names (e.g. `test_chat_completion_streaming`).
- Run the full suite before claiming work is done: `.venv/bin/python -m pytest tests/ -q`. All tests currently pass.

## Commit & Pull Request Guidelines

- Commit messages use imperative present tense, capitalized, no prefix tags (e.g. `Add auth keys and service`, `Fix chat stream encoder to buffer usage`).
- Keep commits focused; one logical change per commit.
- Verify tests pass and ruff is clean before committing.

## Security & Configuration

- **Never commit `wiwi.yaml` or `wiwi.db`** — they hold live provider keys and runtime state. Both are gitignored.
- Use `wiwi.yaml.example` as the template; copy to `wiwi.yaml` and edit locally.
- Provider keys come from env vars via `os.environ/NAME` interpolation in the config; master key via `WIWI_MASTER_KEY`.
- Virtual keys are SHA-256-hashed in storage; the plaintext is returned only once at generation time.

## Docs vs. Code

`docs/ARCHITECTURE.md` and `docs/CORE.md` are design specs that intentionally run ahead of the implementation (handler pipeline, DeltaBus, DB schema, Postgres/Redis backends are specified but not yet built). When docs and code disagree, trust the code — or treat the doc section as the spec for work in progress. `docs/MVP.md` tracks scope gaps; `docs/PLAN.md` tracks build phases.
