# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

wiwi is a self-hosted unified LLM gateway proxy (LiteLLM-style). Three inbound API dialects — OpenAI Chat Completions, OpenAI Responses (Codex CLI), Anthropic Messages (Claude Code) — all route through one canonical internal representation (IR) to any outbound provider (OpenAI, Anthropic, Gemini, any OpenAI-compatible URL). Responses are always re-encoded in the caller's inbound dialect, so e.g. Claude Code can be backed by GPT. Adds virtual keys, budgets, rate limits, key pools with smooth weighted round-robin, retries/cooldowns/fallbacks, cost tracking, and request logs.

Python ≥3.11, FastAPI, httpx, SQLAlchemy async (SQLite default). Package manager is uv; the venv lives at `.venv` (a symlink).

## Commands

```bash
# setup
uv venv && uv pip install -e ".[dev]"

# run server (default 0.0.0.0:4000; needs wiwi.yaml — cp wiwi.yaml.example wiwi.yaml)
wiwi --config wiwi.yaml [--host H] [--port P]

# tests (all pass currently; ~1s)
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_codecs.py -q            # single file
.venv/bin/python -m pytest tests/test_router.py -k cooldown   # single test by name

# lint (ruff, line-length 100, target py311)
.venv/bin/ruff check wiwi/ tests/

# docker
docker compose up --build
```

Tests use `asyncio_mode = "auto"` (no `@pytest.mark.asyncio` needed), `respx` to mock upstream HTTP, and `asgi_lifespan.LifespanManager` + `httpx.ASGITransport` to exercise the app end-to-end (see `tests/test_integration.py` for the pattern).

## Architecture

### Hub-and-spoke translation (the core idea)

No pairwise converters. Every direction goes dialect → IR → provider:

```
wire codec (inbound) ──decode──► IR ──adapter.encode_request──► provider
wire encoder (inbound) ◄──IRStreamDelta/IRResponse◄──adapter.decode─── provider
```

Adding an inbound surface = one new module in `wiwi/wire/`; adding a provider = one new adapter in `wiwi/providers/` + a line in `registry.get_adapter()`. Core code (`core/gateway.py`) never branches on dialect or provider name.

### Life of a request

1. Route path selects the wire codec (`server/app.py` → `run_chat_like`), which decodes the body into an IR `Request`.
2. Auth (`auth/service.py`): bearer token everywhere, plus `x-api-key` on `/v1/messages` (what Claude Code sends). Virtual keys stored SHA-256-hashed in SQLite; checks expiry/disabled/budget/model allowlist, then rpm/tpm sliding-window limit (`ratelimit/memory.py`).
3. Router (`router/router.py`) resolves requested `model` → model group (deployments sharing a `model_name`) → picks a deployment via smooth weighted round-robin over that provider account's **key pool**, honoring cooldowns. `execute_with_retries` retries within the group (retryable: connect errors, timeouts, 408/429/5xx), then falls back per `router_settings.fallbacks`.
4. `Gateway` (`core/gateway.py`) builds the upstream call via the provider adapter and httpx. Non-streaming returns an IR `AssistantTurn`; streaming pumps `IRStreamDelta`s from a background pump task into a queue.
5. The inbound surface's stream encoder (`wire/*.py`, classes like `ChatStreamEncoder`/`AnthropicStreamEncoder`/`ResponsesStreamEncoder`) translates deltas frame-by-frame into the caller's dialect. `app.py:_stream_response` drives feed/final-frame/[DONE].
6. After the response: log event built (`build_log_event`), spend updated, SSE broadcast to `/admin/stream`. Nothing blocking sits in the response path.

### Module map (actual code)

| Module | Role |
|---|---|
| `wiwi/main.py` | CLI entrypoint (`wiwi --config …`) |
| `wiwi/config.py` | YAML → pydantic models; `os.environ/NAME` interpolation anywhere; fail-fast validation |
| `wiwi/ir/types.py` | Canonical IR: tagged part types (text/image/tool_use/tool_result/thinking + reserved audio/document), Message, Tool, ToolChoice, GenParams, Usage, stop reasons |
| `wiwi/wire/openai_chat.py`, `openai_responses.py`, `anthropic_messages.py` | Inbound codecs: `decode_request`, `encode_response`, `error_body`, stream encoder class each |
| `wiwi/providers/base.py` | `ProviderAdapter` protocol, `WiwiError`, retryable-status mapping |
| `wiwi/providers/{openai,anthropic,gemini}_adapter.py` | Outbound adapters (openai-compatible shares the OpenAI adapter via registry) |
| `wiwi/router/router.py` | Model groups, `ProviderAccount`/`ProviderKey` pools (smooth WRR, cooldown/invalid states), retries, fallbacks |
| `wiwi/core/gateway.py` | Surface-agnostic execution engine (`complete`/`stream`), pricing hookup, `build_log_event` |
| `wiwi/core/context.py` | `RequestContext` — the single mutable holder threaded through everything (attempts, stream timing, usage, cost, outcome) |
| `wiwi/streaming/deltas.py` | `IRStreamDelta` taxonomy — the contract between adapters and encoders: exactly one `StreamStart`; `ToolCallOpen→ArgsDelta*→Close` nested per index; exactly one `UsageFinal` after last content delta; then `Finish`; then `StreamEnd` xor `StreamError`. Adapters guarantee legality; encoders never defend against malformed sequences |
| `wiwi/streaming/sse.py` | SSE parsing/frame helpers |
| `wiwi/auth/` | Key generation/hashing (`keys.py`), lookup + budget/spend service (`service.py`) |
| `wiwi/cost/pricing.py` | Cost engine + char-based token estimation fallback |
| `wiwi/logging_core/` | `LogEvent` types (request/proxy/audit streams), logging subsystem with SSE ring buffer + replay for admin tail |
| `wiwi/server/app.py` | FastAPI factory: all routes defined inline (surfaces, `/v1/models`, `/health`, `/admin/*`), middleware adds `x-wiwi-request-id` |

### Config

Single `wiwi.yaml` (LiteLLM-shaped): `providers:` (named accounts, each with a pool of keyed entries), `model_list:` (`model_name` clients request → `wiwi_params` with provider account + native model id), `router_settings:` (strategy/retries/cooldowns/fallbacks/aliases), `general_settings:` (master_key, database_url), `wiwi_settings:` (drop_params, host/port, header allowlist). Any string value may be `os.environ/NAME`.

Error bodies are dialect-correct per surface (OpenAI `{"error":{…}}` vs Anthropic `{"type":"error",…}`) — produced by the wire codecs' `error_body`.

## Docs vs. code

`docs/ARCHITECTURE.md` and `docs/CORE.md` are design specifications that intentionally run ahead of the implementation (handler pipeline, DeltaBus, reasoning/cache subsystems, DB schema, Postgres/Redis backends are specified but not yet built; their repo-layout sections show planned directories like `wire/openai_chat/` that are actually flat files). When docs and code disagree, trust the code — or treat the doc section as the spec for work you're about to do. `docs/MVP.md` tracks scope gaps; `docs/PLAN.md` tracks build phases.
