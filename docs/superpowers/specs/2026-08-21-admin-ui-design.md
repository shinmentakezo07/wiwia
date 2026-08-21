# Admin Web UI — Design

Date: 2026-08-21
Status: Approved
Scope: Full admin panel served by the FastAPI process on the same port as the API. Vite + React SPA, 10 pages, master-key auth via localStorage.

## Architecture

One port, one process:

```
Browser ──► :4000/admin/ui          (SPA)
        ──► :4000/admin/*           (existing + new JSON APIs)
        ──► :4000/admin/stream      (existing SSE)
```

- FastAPI mounts the built SPA from `wiwi/server/static/` (`/admin/ui` → `index.html`, assets under `/admin/ui/assets`). ~15 lines in `server/app.py`.
- SPA source lives in `web/` at the repo root. Build: `cd web && bun install && bun run build` → outputs to `wiwi/server/static/`. `wiwi/server/static/` is gitignored; CI/local builds produce it.
- Stack: Vite, React 19, TypeScript strict, Tailwind CSS v4, Recharts, TanStack Query v5, lucide-react. Minimal hand-rolled component set (buttons, cards, tables, dialogs, badges) — no heavyweight UI kit dependency.
- Auth: login screen validates the master key once (probe `GET /admin/keys`), stores it in `localStorage`, sends `Authorization: Bearer <key>` on every call. Accepted trade-off (user decision): localStorage is XSS-readable; fine for single-admin self-hosting.
- No CORS work needed — same origin.

## Pages (10)

1. **Dashboard** — stat cards: requests/min, tok_in / tok_cached / tok_reasoning / tok_out totals, cache-hit %, avg TPS, p95 TTFT, error rate, spend; stacked area tokens/min (4 token types); requests & errors line; live sparklines off SSE `stats.tick` (client-aggregated).
2. **Providers** — card per provider; key table: label, masked key, weight, status badge (active/cooling/invalid/disabled), req/err counters, last used; actions: enable/disable key, adjust weight, add provider/key dialog.
3. **Virtual Keys** — list with usage + spend-vs-budget progress; create dialog (random or custom ≥16 chars, model allowlist, budget/rpm/tpm/ttl); reveal-once screen with copy; disable toggle; revoke.
4. **Models** — groups with deployment chips (provider + model_id + weight inline-edit), routing strategy selector, health dot per deployment.
5. **Request Logs** — virtualized table: time, key, model, provider+pool key, status, **tok_in, tok_cached, tok_reasoning, tok_out**, **TPS**, TTFT, latency, cost, cache badge; filter bar (key/model/provider/status/surface/time range); detail sheet adds retry chain (attempts); live tail toggle via SSE `log.created`.
6. **Proxy Logs** — same table shell on the proxy stream; level filter; live tail.
7. **Usage** (dedicated metrics page) — totals cards for range (requests, input/cached/reasoning/output/total tokens, cost, avg TPS); Usage table with one row per request and TPS as a sortable first-class column; charts: stacked token-type areas, TPS over time (avg + p95 lines), donut of token share; group-by switcher (model / key / provider).
8. **Analytics** — group-by/metric switchers, hourly heatmap (day × hour volume), spend-per-key bars, cache-savings card, CSV export.
9. **Budgets & Alerts** — per-key budget editors, projected month-end spend (trailing 7-day average), alert-rule storage (webhook URL + thresholds; alert evaluation engine post-MVP).
10. **Settings** — masked master-key info, retention display, theme (dark/light) toggle.

TPS visibility requirement (user): per-request TPS appears in the Request Logs table + detail sheet, the Usage table, dashboard cards, and the TPS-over-time chart.

## New backend endpoints

Existing endpoints kept unchanged: `/admin/keys/generate|list|delete/{id}/disable`, `/admin/logs/requests`, `/admin/stream`. Additions (all master-key guarded, mutations audit-logged):

```
GET   /admin/providers                      # accounts + key pools w/ live counters
PATCH /admin/providers/{name}/keys/{label}  # {enabled?, weight?} → mutates live pool
GET   /admin/models                         # groups, deployments, health dots
PATCH /admin/model-groups/{name}            # {weights?, strategy?} on live router
PATCH /admin/keys/{key_id}                  # {max_budget?, rpm?, tpm?, models?, expires_at?}
GET   /admin/stats/overview?minutes=N       # totals for cards from request-event ring
GET   /admin/stats/timeseries?bucket=minute&metric=tokens|tps&minutes=N
```

Stats are computed from an in-memory rollup over the request-event ring buffer (no DB schema migration in v1). `cache_savings` (already on LogEvent) feeds the Analytics savings card directly.

## Data flow

TanStack Query fetches JSON; a single EventSource on `/admin/stream` pushes `log.created` events that patch query caches (live tail without refetch). Stats pages poll their endpoints on a 10s interval; SSE `stats.tick` does not exist server-side yet, so the client derives live sparklines from `log.created` cadence.

## Security notes

- Master key never rendered after login (masked display only).
- All new mutation endpoints reuse the existing `is_admin()` constant-time check.
- Provider key secrets stay server-side; UI shows labels/masked values only (the API already returns no secrets).
- Audit stream receives every mutation (actor=master).

## Testing

- Backend: pytest coverage per new endpoint — auth guard rejects non-master, PATCH effects visible in live pool/router state, stats math on synthetic event rings (deterministic fixtures).
- Frontend: `bun run build` must pass TS strict + Vite build; ESLint clean.
- E2E smoke (agent-browser): login → create virtual key → key appears in list → disable it → delete it; logs page renders columns incl. TPS; usage page totals match sum of listed rows for a small fixture window.

## Non-goals (this phase)

- Session cookies/RBAC, Redis-backed anything, response caching, alert evaluation engine, DB-backed config overlay (YAML remains bootstrap; live-pool mutations are runtime-only until restart).
