# Provider Edit & Delete

**Date:** 2026-08-23
**Status:** Spec — awaiting review

## Goal

Make provider accounts fully manageable from the admin UI: edit account metadata (name, base_url, type), delete provider accounts, and delete individual pool keys — all surfaced on the provider edit page (the existing `/providers/:name` detail page) with a delete affordance on the providers list too.

## Current state (gaps)

- **Providers page** (`web/src/pages/Providers.tsx`): cards with "Edit" (links to detail) and "Add key". No delete.
- **Provider detail page** (`web/src/pages/ProviderDetail.tsx`): key pool (enable/disable/weight), bulk add, model picker, deployments. No account-metadata edit, no delete-key, no delete-provider.
- **Backend** (`wiwi/server/app.py`): `GET /admin/providers`, `POST /admin/providers`, `PATCH/POST` keys, `GET models`. **No DELETE provider, no PATCH provider metadata, no DELETE key.**

## Design

### Backend — 3 new endpoints

All under `_require_admin` (master-key guarded), following the existing handler patterns.

#### 1. `DELETE /admin/providers/{name}`

Removes the provider account from `state.router.providers`.

- **Guard:** before removal, scan all deployments across model groups for ones whose `.provider.name == name`. If any exist, return `409 invalid_request_error` whose message lists the referencing group names (e.g. `provider still referenced by groups: gpt-4o, claude — remove those deployments first`). The frontend surfaces this message directly via the existing `api()` error parsing (`body.error.message`).
- On success: `del state.router.providers[name]`, audit-log `provider.delete` (target=name).
- Return `{"deleted": true, "name": name}`.

#### 2. `PATCH /admin/providers/{name}`

Edits account metadata. Accepts a JSON body with any of:

- `name` (string, non-empty, must not collide with another provider)
- `base_url` (string)
- `provider_type` (one of `openai`, `anthropic`, `gemini`, `openai-compatible`)

Behavior:
- Only fields present in the body are applied (partial patch).
- If `name` changes: validate uniqueness against `state.router.providers`; re-key the account in the dict (`providers[new_name] = acct; del providers[old_name]`); update `acct.name`; update every `Deployment.provider` reference that pointed to the old account object (they hold a `ProviderAccount` ref, so the object identity is fine, but any stored `.provider.name`-based lookups must be re-checked). Audit-log includes `old_name` and `new_name`.
- If `provider_type` changes: validate against the allowed set; update `acct.provider_type`. If the new type has a default base_url and `base_url` was not supplied in the same patch, keep the existing `base_url` (don't silently change the endpoint).
- Audit-log `provider.update` (target=name, diff=applied fields).
- Return the updated account view (same shape as the GET providers entry for this account).

#### 3. `DELETE /admin/providers/{name}/keys/{label}`

Removes a key from the account pool.

- 404 if provider or key not found.
- On success: `acct.keys = [k for k in acct.keys if k.label != label]`, audit-log `provider_key.delete` (target=`{name}/{label}`).
- Return `{"deleted": true, "label": label}`.

### Frontend

#### API client (`web/src/api/client.ts`)

Add three helpers:

```ts
export const deleteProvider = (name: string) =>
  api<{ deleted: boolean; name: string }>(`/admin/providers/${encodeURIComponent(name)}`, { method: "DELETE" });

export const patchProvider = (name: string, patch: { name?: string; base_url?: string; provider_type?: string }) =>
  api<Provider>(`/admin/providers/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteProviderKey = (provider: string, label: string) =>
  api<{ deleted: boolean; label: string }>(
    `/admin/providers/${encodeURIComponent(provider)}/keys/${encodeURIComponent(label)}`,
    { method: "DELETE" },
  );
```

#### Providers page (`Providers.tsx`)

- Add a **Delete** button (danger variant, `Trash2` icon) to each `ProviderCard` header, alongside Edit / Add key.
- Clicking it opens a confirmation `Dialog` ("Delete provider `<name>`? This cannot be undone."). On confirm, call `deleteProvider(name)`; on 409, surface the referencing groups in the error text so the user knows what to clean up first. On success, invalidate the `["providers"]` query.

#### Provider detail page (`ProviderDetail.tsx`)

1. **Account settings card** (new, at the top of the grid): fields for name, provider_type (Select), base_url, with a **Save** button calling `patchProvider`. Pre-filled from the loaded provider. On success:
   - If name changed, `navigate(`/providers/${encodeURIComponent(newName)}`, { replace: true })` so the URL updates immediately.
   - Invalidate `["providers"]` and `["model-groups"]` (renames affect deployment lookups).
   - Show success/error inline.

2. **Delete provider** button (danger, in the account card or page header) with a confirmation dialog. On success: invalidate queries and `navigate("/providers")`.

3. **Key pool table**: add a **Delete** column (trash icon button, danger) per `KeyRow`. Confirmation dialog ("Delete key `<label>`?"). On success, invalidate `["providers"]`.

### Error handling

- All three new endpoints reuse the existing `_err` helper for dialect-correct error envelopes (though admin endpoints return plain JSON errors, consistent with current admin routes).
- Network/401 handling is already centralized in the `api()` fetch wrapper; no per-call work needed.

## Scope (non-goals)

- No persistence to `wiwi.yaml` — providers remain runtime-only until restart, same as the existing add-provider flow. (Documented in the UI already.)
- No bulk-delete of keys.
- No reordering of keys in the pool.

## Testing

- **Backend:** extend `tests/` with a new suite (or `test_integration.py` cases) covering: DELETE provider (happy + 409 when referenced + 404 unknown), PATCH provider (rename happy + rename collision + type change + partial patch), DELETE key (happy + 404). Use the inline-config + ASGI-lifespan + httpx-async-transport pattern from `tests/test_integration.py`.
- **Frontend:** build must succeed (`bun run build`); no E2E harness exists for the panel, so manual verification against a running dev server is the acceptance check.
- Run full pytest + ruff before committing.
