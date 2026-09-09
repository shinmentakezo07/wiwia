# wiwi — Admin Console & Management

How wiwi is administered: the React SPA (admin console + public front), the master-key-guarded admin API, user accounts, and the realtime/eventing plumbing. For endpoint semantics see [API_REFERENCE.md](API_REFERENCE.md); for the key-pool/routing model see [ARCHITECTURE.md](ARCHITECTURE.md) §5.

> Historical note: earlier revisions described a Next.js/shadcn design that was never built. The shipped stack is React 19 + Vite 6 + Tailwind 4.

---

## 1. One process, one port

```
Browser ──► :4000/admin/ui          (SPA — admin console + public front)
        ──► :4000/admin/*           (admin JSON API, master-key auth)
        ──► :4000/auth/*            (user signup/login/logout, cookie session)
        ──► :4000/public/*          (secret-free catalog, no auth)
        ──► :4000/admin/stream      (SSE realtime events)
        ──► :4000/v1/*              (client traffic — virtual keys)
```

- FastAPI mounts the built SPA from `wiwi/server/static/` (`/admin/ui` → `index.html`, assets under `/admin/ui/assets`) via a `_SPAStaticFiles` subclass.
- SPA source lives in `web/`. Build: `cd web && bun install && bun run build` → `wiwi/server/static/` (gitignored; CI/local builds produce it). Dev: `bun run dev` proxies `/admin /v1 /auth /public /health` → `:4000`.
- Stack: React 19, TypeScript (strict + `verbatimModuleSyntax` + `noUnusedLocals/Parameters`), Vite 6, Tailwind CSS 4, TanStack Query v5, Recharts, react-router-dom v7, lucide-react. Hand-rolled component set (buttons, cards, tables, dialogs, badges) — no heavyweight UI kit.
- No CORS work needed — same origin.

## 2. Auth models

| Actor | Credential | Guard | Sees |
|---|---|---|---|
| Admin (console) | Master key (`WIWI_MASTER_KEY`) | `Authorization: Bearer` on `/admin/*` | Everything |
| User (console) | Username + password | Signed HttpOnly session cookie via `/auth/*` | Own keys, own usage, `/app/*` subset |
| Client app | Virtual key `sk-wiwi-…` | `Authorization: Bearer` / `x-api-key` on `/v1/*` | Allowed models only |

- Console login validates the master key once (probe call), stores it in `localStorage`, and sends `Authorization: Bearer <key>` on every admin call. Accepted trade-off (user decision): localStorage is XSS-readable; fine for single-admin self-hosting.
- User accounts (`/auth/signup|login|logout|me`) use stdlib PBKDF2 password hashing and HMAC-signed HttpOnly cookies. Roles: `user` / `admin`; admins manage accounts at `/admin/users`.
- The playground (`/auth/playground-key`) mints a scoped temporary virtual key so public-front users can try models without a real key.

## 3. Admin console pages

Admin-facing pages (under the guarded area; the sidebar is role-aware):

| Page | What it shows / does |
|---|---|
| **Dashboard** | Stat cards (requests/min, tok_in/out/cached/reasoning, cache-hit %, avg TPS, p95 TTFT, error rate, spend); stacked area tokens/min; requests & errors lines; live sparklines off SSE `stats.tick`; zero-traffic empty states; refetch holds previous render at reduced opacity |
| **Providers** | Card per provider; key table (label, masked secret, weight, status badge `active/cooling/invalid/disabled`, req/err counters, last used); add provider/key dialogs; enable/disable keys; per-key delete; provider edit (name/type/base_url) and delete; export/import provider config |
| **Provider Detail** | Per-provider account settings, key pool management, live model list (`/admin/providers/{name}/models`) |
| **Cline / WorkBuddy** | OAuth connect flows (login URL → callback `/cline/oauth/callback`), status/refresh/disconnect, account import/export, per-account settings |
| **Models** | Model groups + deployments; add/remove deployments; group patching; aliases (`/admin/aliases`) |
| **Virtual Keys** | Mint (name, models, budget, rpm/tpm, expiry — plaintext shown once), patch, disable, delete |
| **Usage** | Token analytics with 1h/24h/7d/30d/all-time ranges (short ranges from the in-memory ring, long ranges from DB aggregates; bucket size scales 1 min → 1 day) |
| **Analytics** | Deep-dive charts + CSV exports; per-key/per-model/per-deployment latency and token breakdowns |
| **Request Logs** | Paginated request log with key/model/status/time filters; per-request detail (attempts, TPS, TTFT, cost, stop reason) |
| **Proxy Logs** | Upstream request/response metadata stream |
| **Pricing** | DB-backed `model_prices`: per-model token prices, overrides, delete-to-fallback |
| **Alert Rules** | Spend/alert thresholds (`/admin/alert-rules`) |
| **Users** | Account list, role changes, disable (admin only) |

Public front pages (unauthenticated, `PublicLayout`): Landing, Models catalog (`/public/models`), Docs, Playground, Pricing, Integrations, Enterprise, OpenSource, Apps, Agents, Partners, Referrals, Ship, Migration, Timeline, About, Legal, SSO, and similar — ~30 pages mixed with ~15 console pages in `web/src/pages/`. **Never assume a page is admin-facing from the directory alone.**

## 4. Realtime

- `GET /admin/stream` (SSE) is the single realtime channel: live `stats.tick` events, request/proxy log events, provider health changes.
- The Dashboard aggregates client-side; no polling beyond TanStack Query refetch intervals for the slower pages.
- Refetch behavior: previous render held at reduced opacity — no skeleton flash.

## 5. Provider & key-pool management flow

1. **Add provider**: Providers → Add (type from `/admin/provider-catalog`, name, base_url, credentials) → `POST /admin/providers`. For Cline/WorkBuddy use the OAuth connect flow instead of raw keys.
2. **Key pool**: add multiple keys per provider with labels + weights; toggle/patch/delete individually; reveal secret (audit-logged).
3. **Health states**: `active | cooling | invalid | disabled`, updated live from request outcomes; Dashboard/Providers pages reflect cooldowns via SSE.
4. **Model deployments**: Models → group → add deployment (provider + model_id + weight); WRR and failover pick these up on the next request.
5. **Export/import**: `/admin/providers/export` / `import` for moving provider+key config between installs (secrets masked or included per flag).

## 6. Stats & data sources

| Range | Source | Bucketing |
|---|---|---|
| ≤ 24 h | In-memory LogEvent ring buffer (500 events) | 60 s |
| 7 d | DB aggregates (`logging_core` DB sink) | 1 h |
| 30 d | DB aggregates | 6 h |
| All-time | DB aggregates | 1 day |

`percentile()` in `server/stats.py` is the single source of truth for p50/p95 so admin rollups and the Prometheus exporter cannot drift. Events with `tps == 0` / `ttft_ms == 0` are excluded from those aggregates only (non-streaming / missing timing).

## 7. UI/UX compatibility rule (binding)

Every admin-UI change must be verified on desktop **and** mobile before it counts as done:

- No layout breakage below ~375 px (test 375×812 + wide desktop); reflow/stack/collapse, never clip/overflow.
- Tap targets ≥ 44×44 px; nothing hover-only (hover reveals also respond to focus/touch).
- Keyboard navigation: sensible Tab order, visible focus styles, modal focus trap + Escape-to-close.
- Relative units for type/spacing; no silent truncation hiding information.
- Admin pages may be opened on a phone (quick key rotation, budget check) — responsiveness is not optional.

## 8. Ops notes

- Master-key rotation: set a new `WIWI_MASTER_KEY`; session cookies stay valid (separate `session_secret`, derived from the master key unless set explicitly).
- `GET /admin/providers/{name}/keys/{label}/secret` is the only secret-reveal path and is audit-logged.
- Budget enforcement: per-key budgets and spend updates are applied post-response; exceeding a cap yields `402` on subsequent requests.
- Prometheus scrape: `GET /metrics` (path configurable) — see [API_REFERENCE.md](API_REFERENCE.md) §2.
