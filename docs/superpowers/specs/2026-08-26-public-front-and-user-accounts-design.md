# Design: Public front + user accounts + role-based dashboards

**Date:** 2026-08-26
**Status:** Approved (brainstorming complete)
**Scope:** Three subsystems, built together in one plan.

## Goal

Turn the admin-only wiwi console into a hybrid application:

1. **Public front** (no auth) — a marketing landing page, a public model catalog, and docs, making `/` a public "about wiwi LLM gateway" page instead of the admin dashboard.
2. **User accounts** (backend) — username+password sign-up/login with DB persistence and an HttpOnly session cookie, distinct from the existing master-key admin.
3. **Role-based dashboards** (frontend) — normal users see a limited, data-scoped set of pages; admins see the full console. The dashboard moves off `/` into a guarded area.

## Confirmed decisions

| Decision | Choice |
|---|---|
| Public pages | Landing `/`, Playground `/playground`, Models browse `/models`, Docs `/docs` |
| Routing layout | Public at root; **all guarded pages nest under `/app/*`**; role branches inside the guarded area |
| Session type | HttpOnly session cookie (`wiwi_session`) |
| Admin login | Master key (`WIWI_MASTER_KEY`) still logs in as admin; promote users to admin from the admin UI |
| User ↔ keys | Each user **owns** the virtual keys they create; `vkeys.owner_id → users.id` |
| User data scope | Request Logs, Usage, Analytics, Budgets scoped to the user's own keys; Models read-only for users |
| Playground auth | Login required; user picks/creates one of their own keys to chat |
| Models browse `/models` | Public catalog of available model groups (provider, context, pricing); no secrets |
| Docs `/docs` | Custom docs page in the app (quickstart, 3 dialects, auth, examples) |

## Architecture

### URL space

```
PUBLIC (no auth) — PublicLayout: top-nav only (logo, Playground / Models / Docs / Sign in)
  /                        Landing (marketing)
  /models                  Public model catalog
  /docs                    Docs
  /playground              Playground (login wall; not in anon nav)

AUTH (redirect to /app if already authed)
  /login                   Login (master key OR username+password — one form, server decides)
  /signup                  Username+password signup → creates user role

GUARDED (/app/*) — AdminLayout: sidebar shell; RequireSession guard
  /app                     Dashboard (role-adaptive content)
  shared user+admin (data scoped to owner for users; global for admin):
    /app/keys              Virtual Keys (scoped)
    /app/models            Models (user = read-only; admin = edit weights/strategy)
    /app/request-logs      Request Logs (scoped)
    /app/usage             Usage (scoped)
    /app/analytics         Analytics (scoped)
    /app/budgets           Budgets & Alerts (scoped to owner's keys)
  admin-only (role gate → 403/redirect for users):
    /app/providers         Providers
    /app/builtin-providers Built-in Providers
    /app/proxy-logs        Proxy Logs
    /app/settings          Settings
    /app/users             User management (list users, promote/demote roles)
```

### Subsystem A — User auth backbone (backend)

**New module `wiwi/auth/users.py`** (sibling to `service.py`), owning the users table and session logic. Follows the existing no-ORM, raw-DDL, dialect-portable pattern of `config_store.py` and `auth/service.py`.

#### `users` table

```sql
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,            -- "u" + 8 hex
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,    -- "pbkdf2_sha256$iterations$salt_hex$hash_hex"
  role TEXT NOT NULL DEFAULT 'user',   -- "user" | "admin"
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
```

Password hashing uses stdlib `hashlib.pbkdf2_hmac` — **no new dependency**. Format string `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` is parseable for verification. Verification is constant-time via `hmac.compare_digest`.

#### `vkeys` migration: add `owner_id`

`auth/service.py` `CREATE_SQL` gains a nullable `owner_id` column via the existing idempotent-migrate pattern (see `config_store._migrate`). SQLite `ALTER TABLE vkeys ADD COLUMN owner_id TEXT` is idempotent-guarded by a column-existence check; PG by `information_schema`. Existing keys get `owner_id = NULL` (admin/system-owned).

```sql
ALTER TABLE vkeys ADD COLUMN owner_id TEXT;   -- NULL = admin/system
CREATE INDEX IF NOT EXISTS idx_vkeys_owner ON vkeys(owner_id);
```

`AuthService.create_key` gains an optional `owner_id` param; `_lookup_db`, `list_keys` carry the column.

#### `request_logs` attribution: add `key_id`

**Critical:** request logs are currently attributed by `key_alias`, which is **not unique** in `vkeys`. Two users can pick the same alias, so scoping logs by alias risks cross-user leakage. We add a `key_id` column to `request_logs` and scope logs/stats by the set of key ids a user owns.

```sql
ALTER TABLE request_logs ADD COLUMN key_id TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_request_logs_key_id ON request_logs(key_id);
```

The logging path (`db_sink.py` insert + `LogEvent`) carries `key_id` from the `AuthInfo` resolved in `authenticate()`. This is additive — existing rows get `key_id = ''` and remain visible to admin (global queries are unaffected).

#### Session cookie

Signed cookie `wiwi_session`, value = `"<user_id>.<role>.<expires_at>.<hmac>"`.

- HMAC key: `WIWI_SESSION_SECRET` env var if set; otherwise derived from `master_key` via HKDF (stdlib `hashlib`/`hmac`) so no extra config is required to run. Logged once at startup if derived.
- Cookie attrs: `HttpOnly`, `SameSite=Lax`, `Secure` when the request is HTTPS, `Path=/`, `max-age=7d`.
- Sessions are stateless (no server-side session table): the signed cookie is the source of truth, so it survives restarts and needs no DB row. A `users` row lookup on each guarded request validates the user still exists and is not disabled.

#### Endpoints (new `/auth/*` prefix)

| Method | Path | Auth | Body / Result |
|---|---|---|---|
| POST | `/auth/signup` | none | `{username, password}` → sets cookie, `{user}` (201). Validates username (3–32 chars, `[a-zA-Z0-9_-]`), password (≥8 chars). Unique constraint → 409 on conflict. New user role always `user` (never admin via signup). |
| POST | `/auth/login` | none | `{username?, password?, master_key?}` → sets cookie, `{user}` (200). Server detects: if `master_key` matches `WIWI_MASTER_KEY` → admin session (cookie value uses a synthetic admin identity). Else looks up `users` by username+password → user session. 401 on failure. |
| POST | `/auth/logout` | any | Clears cookie (200). |
| GET | `/auth/me` | session/cookie | `{user: {id, username, role} | null}`. Also accepts the existing bearer master key (for back-compat with the current admin UI flow). |

`/auth/me` is the single probe the frontend uses to decide routing: it returns the current identity (from cookie OR bearer) or `null`. No master-key leakage — only `{id, username, role}` is returned.

#### Session dependency

`wiwi/auth/users.py` exposes `current_user(request) -> UserInfo | None` that reads the cookie, verifies the HMAC, checks expiry, and confirms the `users` row exists and is not disabled. A FastAPI dependency `require_user` raises 401 when absent; `require_admin` raises 403 when present but not admin. The synthetic admin identity (from master-key login) has `role="admin"` and `id="master"`.

#### Owner-scoped data access

Existing `/admin/*` handlers currently assume master (global) access. We add an `actor` concept resolved per request:

- **Admin** (master key or admin session): global queries unchanged.
- **User**: queries filter by the user's owned key ids.

Concretely, the handlers that serve the scoped pages accept an optional `actor` and, for users, restrict:

- **Virtual Keys** (`/admin/keys` list, `/admin/keys/{id}` get/patch/delete/disable): `WHERE owner_id = :uid` for users. Key creation (`/admin/keys/generate`) stamps `owner_id = :uid`. A user operating on a key they don't own → 403.
- **Request Logs** (`/admin/logs/requests`): `WHERE key_id IN (user's key ids)` for users. Needs the new `key_id` column populated.
- **Usage / Analytics** (`/admin/stats/overview`, `/admin/stats/timeseries`): `stats.py` functions currently take a `list[LogEvent]`. We filter the event list to the user's key ids before computing, keeping `stats.py` pure and unit-testable (no DB coupling added). The endpoint loads events then filters by actor.
- **Budgets & Alerts** (`/admin/alert-rules`): alert rules are global config; for users we scope the *budget display* to their keys (the alert rules themselves remain admin-managed). The page shows per-key budgets for the user's own keys.
- **Models** (`/admin/models`, `/admin/model-groups/...`): read access for both roles; `PATCH` (weights/strategy) is admin-only → 403 for users.

The admin-only pages (`/app/providers`, `/app/builtin-providers`, `/app/proxy-logs`, `/app/settings`, `/app/users`) require `require_admin`; users get 403 (the frontend hides these from the user nav, so this is defense-in-depth).

#### `/app/users` (new admin page + endpoints)

| Method | Path | Auth | Result |
|---|---|---|---|
| GET | `/admin/users` | admin | `{users: [{id, username, role, disabled, created_at}]}` |
| PATCH | `/admin/users/{id}` | admin | `{role?, disabled?}` → promote/demote, enable/disable. Cannot demote/disable the last admin (guarded). |

Audit-logged via the existing `state.logs.log_audit`.

#### Wiring

`create_app` initializes `UserService(engine)` alongside `AuthService`; runs `UserService.startup()` (DDL + migrations) in `AppState.init_db()`. The `/auth/*` routes are registered. The Vite dev proxy gains `"/auth"` → `localhost:4000`.

### Subsystem B — Role-based dashboards (frontend)

#### New auth context (`web/src/api/auth.tsx`)

Replaces the master-key-only model. `useAuth()` exposes `{ user: {id, username, role} | null, loginMaster, loginUser, signup, logout, refresh }`. On mount it calls `/auth/me` to hydrate `user`. Token (master key, when used) stays in localStorage for back-compat; session lives in the cookie and is opaque to JS.

#### Routing (`web/src/main.tsx`)

```
<BrowserRouter basename={BASE_URL}>
  <Routes>
    {/* public */}
    <Route element={<PublicLayout />}>
      <Route path="/" element={<LandingPage />} />
      <Route path="/models" element={<ModelsCatalogPage />} />
      <Route path="/docs" element={<DocsPage />} />
    </Route>
    <Route path="/playground" element={<RequireUser><PlaygroundPage /></RequireUser>} />
    {/* auth */}
    <Route path="/login" element={user ? <Navigate to="/app"/> : <LoginPage/>} />
    <Route path="/signup" element={user ? <Navigate to="/app"/> : <SignupPage/>} />
    {/* guarded */}
    <Route element={<RequireUser><AdminStreamProvider><AdminLayout/></RequireUser>}>
      <Route path="/app" element={<DashboardPage/>} />
      <Route path="/app/keys" element={<VirtualKeysPage/>} />
      <Route path="/app/models" element={<ModelsPage/>} />
      <Route path="/app/request-logs" element={<RequestLogsPage/>} />
      <Route path="/app/usage" element={<UsagePage/>} />
      <Route path="/app/analytics" element={<AnalyticsPage/>} />
      <Route path="/app/budgets" element={<BudgetsAlertsPage/>} />
      <Route path="/app/providers" element={<RequireAdmin><ProvidersPage/></RequireAdmin>} />
      <Route path="/app/providers/:name" element={<RequireAdmin><ProviderDetailPage/></RequireAdmin>} />
      <Route path="/app/builtin-providers" element={<RequireAdmin><BuiltinProvidersPage/></RequireAdmin>} />
      <Route path="/app/proxy-logs" element={<RequireAdmin><ProxyLogsPage/></RequireAdmin>} />
      <Route path="/app/settings" element={<RequireAdmin><SettingsPage/></RequireAdmin>} />
      <Route path="/app/users" element={<RequireAdmin><UsersPage/></RequireAdmin>} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace/>} />
  </Routes>
</BrowserRouter>
```

`RequireUser` → `<Navigate to="/login" />` when no session. `RequireAdmin` → `<Navigate to="/app" />` (with a 403 toast) when not admin. Old flat paths (`/`, `/keys`, `/providers`, …) are redirected: `/keys`→`/app/keys`, `/providers`→`/app/providers`, etc. via a small redirect map, so existing bookmarks survive. The catch-all `*` already goes to `/`.

#### Layouts

- **`PublicLayout`** (new): minimal top-nav (logo + Playground / Models / Docs / Sign in), renders `<Outlet/>` against the ambient background reused from `AdminLayout`. Footer with "wiwi · self-hosted LLM gateway".
- **`AdminLayout`** (refactor of existing `Layout.tsx`): sidebar nav becomes role-aware. `NAV_SECTIONS` filtered by `user.role`:
  - **user:** Overview (Dashboard), Traffic (Request Logs, Usage, Analytics), Configuration (Models RO, Virtual Keys), Admin (Budgets & Alerts)
  - **admin:** all current sections + a new **Admin → Users** item.
  Page meta map keys move from `/` to `/app`, `/keys`→`/app/keys`, etc.

#### Page scoping

The scoped pages (Keys, Request Logs, Usage, Analytics, Budgets) call the same `/admin/*` endpoints; the backend filters by the session actor. No per-page frontend branching needed for data — the same components render less data for users. **Models** page hides the weight-edit `WeightChip` and the strategy `Select` when `user.role !== "admin"` (read-only). The admin-only pages are simply not in the user nav and are `RequireAdmin`-guarded.

#### New pages

- **`LandingPage`** (`web/src/pages/Landing.tsx`): hero ("One gateway, every model"), feature grid (unified inbound dialects, virtual keys, budgets, key pools, retries, observability), how-it-works (dialect → IR → provider diagram), CTA buttons → `/signup`, `/playground`, `/docs`. Reuses the admin design tokens (dark, hairline cards) for visual continuity.
- **`SignupPage`** (`web/src/pages/Signup.tsx`): username+password form, calls `/auth/signup`, then `/app`.
- **`LoginPage`** (refactor existing): one form with username+password fields + a "or sign in with master key" toggle. Calls `/auth/login`. Links to `/signup`.
- **`UsersPage`** (`web/src/pages/Users.tsx`): admin table of users with role select (user/admin) and enable/disable toggles; calls `/admin/users`.
- **`PlaygroundPage`** (`web/src/pages/Playground.tsx`): key picker (lists user's keys via scoped `/admin/keys`; "Create key" inline), model selector (from `/admin/models` groups), message list + composer. Sends `POST /v1/chat/completions` with `Authorization: Bearer <chosen key>`. Renders streamed/non-streamed responses; shows usage/cost. No master key needed — uses the user's own virtual key.

### Subsystem C — Public front (frontend)

Covered above (Landing, Models catalog, Docs, Playground). The public model catalog (`/models`) reads `/admin/models` (read access allowed for both roles and for the catalog — we expose a public, secret-free variant `/public/models` returning group names, providers, context windows, and pricing; no key health/inflight/cooldown). Docs is static content.

## Data flow

**Signup:**
```
POST /auth/signup {username,password}
 → hash password, INSERT users (role=user)
 → set wiwi_session cookie (signed)
 → 201 {user}
 → frontend → /app
```

**Login (master key):**
```
POST /auth/login {master_key}
 → hmac compare vs WIWI_MASTER_KEY
 → set cookie with synthetic admin identity {id:master, role:admin}
 → 200 {user:{id:master, role:admin, username:master}}
 → frontend → /app (admin sees full nav)
```

**Login (user):**
```
POST /auth/login {username,password}
 → SELECT user, verify pbkdf2
 → set cookie {id, role:user}
 → 200 {user}
 → frontend → /app (user sees scoped nav)
```

**Guarded request:**
```
GET /admin/keys  (cookie: wiwi_session)
 → current_user(request) verifies cookie → UserInfo
 → require_user: 401 if none
 → actor = UserInfo; admin ⇒ global; user ⇒ WHERE owner_id = uid
 → return scoped keys
```

**Playground chat:**
```
POST /v1/chat/completions  Authorization: Bearer <user's virtual key>
 → existing authenticate() resolves AuthInfo by key hash (unchanged)
 → routes through gateway normally
 → key_id stamped into request_logs
```

## Error handling

- **Auth:** 401 on missing/invalid/expired session (cookie tamper → HMAC mismatch → treated as anonymous). 403 on role mismatch (user hitting admin endpoint) or ownership violation (user patching another user's key).
- **Signup:** 409 username taken; 400 invalid username/password (length/charset). Username normalized (lowercased, trimmed) before unique check.
- **Sessions:** tampered/expired cookie → treated as logged out, no error surfaced beyond redirect to `/login`. Cookie cleared on logout and on failed `users` lookup (disabled/deleted user).
- **Last-admin guard:** demoting or disabling the final admin is rejected (400) with a clear message.
- **Public catalog:** `/public/models` never errors on auth — it's unauthenticated; returns empty list if no groups configured.

## Testing

Backend (pytest, bare `async def test_…`, no decorators), in a new thematic regression file `tests/test_user_accounts.py`:

- `test_signup_creates_user_sets_cookie` — 201, cookie present, `/auth/me` returns role=user.
- `test_signup_duplicate_409`, `test_signup_short_password_400`, `test_signup_bad_username_400`.
- `test_login_user_success`, `test_login_user_wrong_password_401`, `test_login_master_key_sets_admin`.
- `test_logout_clears_cookie`.
- `test_auth_me_returns_null_when_anonymous`, `test_auth_me_after_login`.
- `test_session_cookie_tamper_rejected` — flip a byte → treated as anon.
- `test_disabled_user_session_rejected` — disable user, `/auth/me` → null, guarded endpoint → 401.
- `test_user_keys_scoped` — user A creates key, user B's `/admin/keys` does not list it; user A's does.
- `test_user_cannot_patch_others_key_403`, `test_user_cannot_access_admin_endpoint_403`.
- `test_admin_sees_all_keys`, `test_user_create_key_stamps_owner`.
- `test_request_logs_scoped_by_key_id` — two users' keys produce logs; each user sees only theirs.
- `test_stats_scoped_for_user` — overview/timeseries filtered to user's keys.
- `test_models_patch_admin_only_403_for_user`.
- `test_promote_user_to_admin`, `test_cannot_demote_last_admin_400`, `test_disable_user`.
- `test_public_models_no_secrets` — `/public/models` omits health/inflight/cooldown.

Frontend: type-check (`tsc -b`) and `bun run build` must pass; manual smoke of the three flows (signup→scoped dashboard, master→full dashboard, playground chat). No JS test framework is configured in the repo, so frontend coverage is build + type-check + manual.

## Conventions & guardrails honored

- No new Python dependencies (stdlib `hashlib.pbkdf2_hmac`, `hmac`, `secrets`, `hashlib` for HKDF).
- No ORM; raw DDL, dialect-portable (SQLite + PG), idempotent migrations matching `config_store.py`.
- No dialect/provider branches in `core/`/`router/`/`auth/` — new logic lives in `auth/users.py` and `server/app.py` handlers.
- Async throughout; `orjson` in hot paths; `structlog` (never print).
- Ruff line-length 100, py311.
- `vkeys`/`request_logs` migrations are additive and backward-compatible (nullable columns, default `''`/`NULL`).
- Master-key bearer auth and all existing `/admin/*` endpoints keep working (back-compat): `/auth/me` accepts a bearer master key too.
- Never commit `wiwi.yaml`, `wiwi.db`, secrets.

## Open / out of scope

- Password reset / email — not included (no email infra). Users can be re-enabled by admin; passwords reset by an admin via a future endpoint if needed.
- OAuth/SSO — out of scope.
- Session revocation list — out of scope (stateless cookies; disabling the user row is the revocation).
- Public `/docs` rendering from `docs/` markdown — out of scope; docs are hand-authored React content for v1.
