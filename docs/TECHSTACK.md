# wiwi — Tech Stack

The stack as shipped (source of truth: `pyproject.toml`, `web/package.json`, `Dockerfile`). Every pick lists the alternatives considered and why they lost, so decisions can be revisited without re-researching. Last reviewed: 2026-09.

---

## 1. The stack at a glance

### Backend (`pyproject.toml`)

| Layer | Pick | Version | Confidence |
|---|---|---|---|
| Language | Python | >= 3.11 (3.12 dev) | high |
| Package manager | uv (lockfile: `uv.lock`) · pip works | latest | high |
| API framework | FastAPI | >= 0.110 | high |
| ASGI server | uvicorn[standard] | >= 0.29 | high |
| SSE serve | sse-starlette | >= 2.0 | high |
| Upstream HTTP client | httpx (async, HTTP/2) | >= 0.27 | high |
| Validation & types | pydantic v2 + pydantic-settings | >= 2.7 | high |
| Config format | PyYAML → typed pydantic models | PyYAML >= 6 | high |
| Database | SQLite (aiosqlite) default · PostgreSQL (asyncpg) prod | — | high |
| ORM / migrations | SQLAlchemy 2.x async · inline `CREATE TABLE IF NOT EXISTS` (no Alembic) | >= 2.0.30 | high |
| JSON (hot path) | orjson | >= 3.10 | high |
| Cache / rate-limit store | in-memory (default) · redis-py asyncio (`[redis]` extra) | redis >= 5 | high |
| Pricing data | DB-backed `model_prices` + bundled fallback | — | high |
| Logging | structlog (JSON lines) | >= 24.1 | high |
| Env loading | python-dotenv | >= 1.0 | high |
| Build backend | hatchling | latest | high |

Runtime dependencies: 13. That count is a feature.

### Frontend (`web/package.json`, bun authoritative)

| Layer | Pick | Version |
|---|---|---|
| Framework | React + react-dom | ^19.1 |
| Bundler / dev server | Vite | ^6.3 |
| Styling | Tailwind CSS (+ `@tailwindcss/vite`) | ^4.1 |
| Data fetching | TanStack Query | ^5.62 |
| Charts | Recharts | ^2.15 |
| Routing | react-router-dom | ^7.6 |
| Icons | lucide-react | ^0.525 |
| Language | TypeScript (strict, `verbatimModuleSyntax`) | ~5.8 |
| Lint | ESLint 9 flat config + typescript-eslint | ^9.30 |

### Testing & quality

| Purpose | Pick |
|---|---|
| Test runner | pytest >= 8 + pytest-asyncio 0.23 (`asyncio_mode = "auto"`) |
| Upstream mocking | respx (decorator form) |
| App-level harness | asgi-lifespan `LifespanManager` + `httpx.ASGITransport` |
| Property-based | hypothesis >= 6.100 |
| Python lint | ruff only (line 100, py311, no black/isort) |
| Frontend checks | `tsc -b` (in build) + `bun run lint` (ESLint) |
| Coverage | none (no pytest-cov, not enforced) |

---

## 2. Decisions and trade-offs

**FastAPI over Litestar/Blacksheep.** A gateway's latency is dominated by upstream providers; FastAPI's ecosystem (pydantic v2, SSE, OpenAPI docs) wins. We bypass its deprecated `ORJSONResponse` shim with a trivial subclass and avoid `BaseHTTPMiddleware` entirely (pure-ASGI middleware) so streaming responses pass through untouched.

**orjson everywhere in hot paths.** Request parsing, admin payloads, and response rendering go through a single `ORJSONResponse` subclass; pydantic is reserved for config/admin schemas.

**httpx with HTTP/2.** Streaming, timeouts, connection pooling, and respx test mocking in one client.

**SQLAlchemy async without Alembic.** Schema is created via inline `CREATE TABLE IF NOT EXISTS` at startup — migrations are additive DDL, and the schema surface is small (keys, users, logs, config store, prices). Alembic would be ceremony without payoff at this size.

**No msgspec / tiktoken.** orjson covers hot-path serialization; provider-reported usage is authoritative for billing with a chars/4 estimate fallback (`cost/estimate_tokens_async`) instead of a tiktoken dependency.

**In-memory defaults, Redis opt-in.** Sliding-window rate limiting and the exact-match response cache run in-process by default (single-instance self-hosting target); the `[redis]` extra enables multi-instance correctness.

**Hand-rolled retry/backoff (~50 lines in `core/recovery.py`).** Shared `Backoff`/`CircuitBreaker` primitives serve the router, OAuth refresh services, and the HealthHealer; a tenacity dependency would not honor `Retry-After` semantics we need.

**structlog JSON lines.** Structured, redaction-friendly, three streams (request/proxy/audit) plus the LogEvent ring that feeds stats and `/metrics`.

**React + Vite over Next.js.** The admin console is an SPA served same-origin by the gateway process; SSR/routing conventions of a meta-framework buy nothing here. Router is react-router-dom v7; state is TanStack Query over the admin JSON API; charts are Recharts; styling is Tailwind 4 (CSS-first config via the Vite plugin).

**bun over npm.** `web/bun.lock` is authoritative; `web/package-lock.json` is legacy. Build output lands in `wiwi/server/static/` and is served at `/admin/ui`.

**Docker multi-stage, non-root.** uv builder → bun SPA build → `python:3.12-slim` runtime; user `wiwi`; healthcheck `GET /health` every 30 s; compose ships Postgres 16 with healthcheck-gated startup.

---

## 3. Deliberately not included

| Rejected | Why |
|---|---|
| Alembic | Additive-DDL schema, small surface; inline DDL at startup |
| tiktoken | Provider usage is authoritative; heuristic fallback suffices |
| msgspec | orjson meets the latency budget; one JSON lib to reason about |
| celery / task queue | Background tasks + lifespan services cover spend/log/refresh work |
| heavyweight UI kit (shadcn/MUI) | Hand-rolled Tailwind components; bundle stays small |
| mypy / pyright | Untyped Python by choice; TS side is strict-typed instead |
| pytest-cov | Coverage not enforced |
