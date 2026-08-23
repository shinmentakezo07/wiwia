# Provider Edit & Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make provider accounts fully editable and deletable from the admin UI, plus per-key deletion, by adding three backend endpoints and wiring them into the Providers page and Provider detail page.

**Architecture:** Three new admin endpoints in `wiwi/server/app.py` (`DELETE /admin/providers/{name}`, `PATCH /admin/providers/{name}`, `DELETE /admin/providers/{name}/keys/{label}`), each master-key guarded and audit-logged. The frontend API client gains three typed helpers; `Providers.tsx` gets a delete-provider button per card; `ProviderDetail.tsx` gets an account-settings card (name/type/base_url edit), a delete-provider button, and a per-key delete action.

**Tech Stack:** Python 3.11 / FastAPI / pytest-asyncio (backend); React 19 + TypeScript + TanStack Query + Tailwind 4 + bun (frontend).

## Global Constraints

- Ruff line-length 100, target py311. No dialect/provider branches in `core/`, `router/`, or `auth/` (admin endpoints in `server/app.py` are the exception — they are the management surface).
- pytest `asyncio_mode = "auto"` — bare `async def test_…`, no `@pytest.mark.asyncio`. Upstream mocking with `respx`. App-level tests use inline `WiwiConfig` + `asgi_lifespan.LifespanManager` + `httpx.ASGITransport` (copy the `client` fixture from `tests/test_integration.py`).
- No shared `conftest.py` — fixtures live per-file.
- Commits: imperative present tense, capitalized, no prefix tags. Verify pytest + ruff pass before committing.
- Never commit `wiwi.yaml` or `wiwi.db`.
- Admin endpoints require master key (use the existing `_require_admin` helper).
- Admin endpoints return JSON errors via the existing `_err(status, etype, message, request)` helper.
- The `Button` component supports `variant="danger"` (renders `admin-btn-danger`).
- The `Dialog` component signature: `{ open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }`.

---

## File Structure

| File | Responsibility |
|---|---|
| `wiwi/server/app.py` | Add three admin handlers (DELETE provider, PATCH provider, DELETE key) |
| `web/src/api/client.ts` | Add `deleteProvider`, `patchProvider`, `deleteProviderKey` helpers |
| `web/src/pages/Providers.tsx` | Add delete-provider button + confirmation dialog per card |
| `web/src/pages/ProviderDetail.tsx` | Add account-settings card, delete-provider button, per-key delete action |
| `tests/test_provider_admin.py` | New test suite for the three endpoints |

---

### Task 1: Backend — DELETE provider key endpoint

**Files:**
- Modify: `wiwi/server/app.py` (add handler after `admin_add_provider_key`, ~line 610)
- Test: `tests/test_provider_admin.py` (new file)

**Interfaces:**
- Consumes: `state.router.providers` (dict[str, ProviderAccount]), `ProviderAccount.get_key(label)`, `state.logs.log_audit`, `_require_admin`, `_err`, `_key_view`
- Produces: `DELETE /admin/providers/{name}/keys/{label}` → `{"deleted": bool, "label": str}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provider_admin.py`:

```python
"""Admin provider management: delete-key, patch-provider, delete-provider endpoints."""

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.server.app import create_app


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai",
                        keys=[KeyDef(label="a", key="test-key"),
                              KeyDef(label="b", key="test-key-2")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@pytest.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


H = {"Authorization": "Bearer sk-wiwi-master-test"}


async def test_delete_provider_key_happy(client):
    r = await client.delete("/admin/providers/p1/keys/a", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "label": "a"}
    # key gone from listing
    listing = (await client.get("/admin/providers", headers=H)).json()
    labels = [k["label"] for k in listing["providers"][0]["keys"]]
    assert "a" not in labels
    assert "b" in labels


async def test_delete_provider_key_unknown_provider(client):
    r = await client.delete("/admin/providers/nope/keys/a", headers=H)
    assert r.status_code == 404


async def test_delete_provider_key_unknown_label(client):
    r = await client.delete("/admin/providers/p1/keys/nope", headers=H)
    assert r.status_code == 404


async def test_delete_provider_key_requires_admin(client):
    r = await client.delete("/admin/providers/p1/keys/a")
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider_admin.py -q`
Expected: 3-4 failures (404s / 405 method not allowed because the route doesn't exist yet).

- [ ] **Step 3: Add the DELETE key handler**

In `wiwi/server/app.py`, insert immediately after the `admin_add_provider_key` function (which ends right before `@app.post("/admin/providers")`):

```python
    @app.delete("/admin/providers/{name}/keys/{label}")
    async def admin_delete_provider_key(name: str, label: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        key = acct.get_key(label)
        if key is None:
            return _err(404, "not_found_error",
                        f"unknown key '{label}' on provider '{name}'", request)
        acct.keys = [k for k in acct.keys if k.label != label]
        await state.logs.log_audit(actor="master", action="provider_key.delete",
                                   target=f"{name}/{label}")
        return JSONResponse({"deleted": True, "label": label})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider_admin.py -q`
Expected: 4 passed.

- [ ] **Step 5: Run full suite + lint**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check wiwi/ tests/`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add wiwi/server/app.py tests/test_provider_admin.py
git commit -m "Add delete provider key endpoint"
```

---

### Task 2: Backend — DELETE provider account endpoint

**Files:**
- Modify: `wiwi/server/app.py` (add handler after `admin_add_provider`)
- Test: `tests/test_provider_admin.py` (append cases)

**Interfaces:**
- Consumes: `state.router.providers`, `state.router.groups` (dict[str, list[Deployment]]), `Deployment.provider` (holds a `ProviderAccount` ref), `state.logs.log_audit`, `_require_admin`, `_err`
- Produces: `DELETE /admin/providers/{name}` → `{"deleted": bool, "name": str}`. On referenced-by-group conflict: `409` with message listing the groups.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_admin.py` (before the fixtures are done — add at module level after the existing tests):

```python
async def test_delete_provider_referenced_by_group(client):
    """Provider still used by a model group → 409 with group names in the message."""
    r = await client.delete("/admin/providers/p1", headers=H)
    assert r.status_code == 409
    assert "gpt-4o" in r.json()["error"]["message"]


async def test_delete_provider_happy(client):
    """Delete a provider that no group references."""
    # first remove the only deployment by deleting the group's key is not enough;
    # instead, build a fresh config with an unreferenced provider.
    cfg = WiwiConfig(
        providers=[
            ProviderDef(name="p1", provider="openai", keys=[KeyDef(label="a", key="k1")]),
            ProviderDef(name="orphan", provider="openai", keys=[KeyDef(label="a", key="k2")]),
        ],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1", model="gpt-4o"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete("/admin/providers/orphan", headers=H)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": True, "name": "orphan"}
            listing = (await c.get("/admin/providers", headers=H)).json()
            names = [p["name"] for p in listing["providers"]]
            assert "orphan" not in names
            assert "p1" in names


async def test_delete_provider_unknown(client):
    r = await client.delete("/admin/providers/nope", headers=H)
    assert r.status_code == 404


async def test_delete_provider_requires_admin(client):
    r = await client.delete("/admin/providers/p1")
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider_admin.py::test_delete_provider_referenced_by_group tests/test_provider_admin.py::test_delete_provider_happy -q`
Expected: failures (route doesn't exist yet).

- [ ] **Step 3: Add the DELETE provider handler**

In `wiwi/server/app.py`, insert immediately after the `admin_add_provider` function (which ends right before `@app.get("/admin/providers/{name}/models")`):

```python
    @app.delete("/admin/providers/{name}")
    async def admin_delete_provider(name: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        # Block if any model group still references this provider.
        referencing = sorted(
            gname for gname, deps in state.router.groups.items()
            if any(d.provider is acct for d in deps)
        )
        if referencing:
            return _err(409, "invalid_request_error",
                        f"provider still referenced by groups: "
                        f"{', '.join(referencing)} — remove those deployments first",
                        request)
        del state.router.providers[name]
        await state.logs.log_audit(actor="master", action="provider.delete", target=name)
        return JSONResponse({"deleted": True, "name": name})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider_admin.py -q`
Expected: all pass.

- [ ] **Step 5: Run full suite + lint**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check wiwi/ tests/`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add wiwi/server/app.py tests/test_provider_admin.py
git commit -m "Add delete provider endpoint with group-reference guard"
```

---

### Task 3: Backend — PATCH provider account metadata endpoint

**Files:**
- Modify: `wiwi/server/app.py` (add handler after the DELETE provider handler from Task 2)
- Test: `tests/test_provider_admin.py` (append cases)

**Interfaces:**
- Consumes: `state.router.providers`, `state.router.groups` + `Deployment.provider`, `ProviderAccount` fields (`name`, `provider_type`, `base_url`), `_default_base_url`, `_require_admin`, `_err`, `json_body`, `state.logs.log_audit`
- Produces: `PATCH /admin/providers/{name}` → returns the updated provider view (same shape as one entry from `GET /admin/providers`: `{name, provider_type, base_url, healthy, keys}`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_admin.py`:

```python
async def test_patch_provider_rename(client):
    r = await client.patch("/admin/providers/p1", json={"name": "p1-renamed"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "p1-renamed"
    listing = (await client.get("/admin/providers", headers=H)).json()
    names = [p["name"] for p in listing["providers"]]
    assert "p1-renamed" in names
    assert "p1" not in names


async def test_patch_provider_rename_collision(client):
    # add a second provider first
    await client.post("/admin/providers", json={
        "name": "p2", "provider_type": "openai",
        "label": "a", "key": "sk-x"}, headers=H)
    r = await client.patch("/admin/providers/p1", json={"name": "p2"}, headers=H)
    assert r.status_code == 409


async def test_patch_provider_change_base_url(client):
    r = await client.patch("/admin/providers/p1",
                           json={"base_url": "https://custom.example.com/v1"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["base_url"] == "https://custom.example.com/v1"


async def test_patch_provider_change_type(client):
    r = await client.patch("/admin/providers/p1", json={"provider_type": "anthropic"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["provider_type"] == "anthropic"


async def test_patch_provider_bad_type(client):
    r = await client.patch("/admin/providers/p1", json={"provider_type": "bogus"}, headers=H)
    assert r.status_code == 400


async def test_patch_provider_unknown(client):
    r = await client.patch("/admin/providers/nope", json={"name": "x"}, headers=H)
    assert r.status_code == 404


async def test_patch_provider_requires_admin(client):
    r = await client.patch("/admin/providers/p1", json={"name": "x"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider_admin.py::test_patch_provider_rename -q`
Expected: failure (route doesn't exist yet).

- [ ] **Step 3: Add the PATCH provider handler**

In `wiwi/server/app.py`, insert immediately after the `admin_delete_provider` function:

```python
    @app.patch("/admin/providers/{name}")
    async def admin_patch_provider(name: str, request: Request):
        resp = _require_admin(request)
        if resp:
            return resp
        acct = state.router.providers.get(name)
        if acct is None:
            return _err(404, "not_found_error", f"unknown provider '{name}'", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        diff: dict[str, Any] = {}
        new_name: str | None = None
        if "name" in body:
            new_name = str(body["name"]).strip()
            if not new_name:
                return _err(400, "invalid_request_error", "name must be non-empty",
                            request)
            if new_name != name and new_name in state.router.providers:
                return _err(409, "invalid_request_error",
                            f"provider '{new_name}' already exists", request)
            diff["name"] = new_name
        if "provider_type" in body:
            ptype = str(body["provider_type"])
            if ptype not in ("openai", "anthropic", "gemini", "openai-compatible"):
                return _err(400, "invalid_request_error",
                            f"unsupported provider type '{ptype}'", request)
            acct.provider_type = ptype
            diff["provider_type"] = ptype
        if "base_url" in body:
            base_url = str(_interpolate(body["base_url"])) or ""
            if not base_url:
                return _err(400, "invalid_request_error",
                            "base_url must be non-empty", request)
            acct.base_url = base_url
            diff["base_url"] = base_url
        # apply rename last so identity-based deployment refs stay valid
        if new_name is not None and new_name != name:
            acct.name = new_name
            state.router.providers[new_name] = acct
            del state.router.providers[name]
            target = f"{name}→{new_name}"
        else:
            target = name
        await state.logs.log_audit(actor="master", action="provider.update",
                                   target=target, diff=diff)
        mono, wall = time.monotonic(), time.time()
        return JSONResponse({
            "name": acct.name,
            "provider_type": acct.provider_type,
            "base_url": acct.base_url,
            "healthy": acct.healthy,
            "keys": [_key_view(k, mono, wall) for k in acct.keys],
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider_admin.py -q`
Expected: all pass.

- [ ] **Step 5: Run full suite + lint**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check wiwi/ tests/`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add wiwi/server/app.py tests/test_provider_admin.py
git commit -m "Add patch provider metadata endpoint"
```

---

### Task 4: Frontend — API client helpers

**Files:**
- Modify: `web/src/api/client.ts` (append after the existing `addProvider` helper, ~line 73)

**Interfaces:**
- Consumes: `api()`, `Provider` type from `./types`
- Produces: `deleteProvider(name)`, `patchProvider(name, patch)`, `deleteProviderKey(provider, label)`

- [ ] **Step 1: Add the three helpers**

In `web/src/api/client.ts`, immediately after the existing `addProvider` export, add:

```ts
export const deleteProvider = (name: string) =>
  api<{ deleted: boolean; name: string }>(
    `/admin/providers/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );

export const patchProvider = (
  name: string,
  patch: { name?: string; base_url?: string; provider_type?: string },
) =>
  api<Provider>(`/admin/providers/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteProviderKey = (provider: string, label: string) =>
  api<{ deleted: boolean; label: string }>(
    `/admin/providers/${encodeURIComponent(provider)}/keys/${encodeURIComponent(label)}`,
    { method: "DELETE" },
  );
```

- [ ] **Step 2: Verify it type-checks**

Run: `cd web && bunx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/api/client.ts
git commit -m "Add provider delete and patch API client helpers"
```

---

### Task 5: Frontend — Delete button on Providers page

**Files:**
- Modify: `web/src/pages/Providers.tsx`

**Interfaces:**
- Consumes: `deleteProvider` from `@/api/client`, `Dialog`, `Button`, `useMutation`, `useQueryClient`, `Trash2` icon from lucide-react
- Produces: each `ProviderCard` shows a red Delete button that opens a confirmation dialog and calls `deleteProvider(name)`.

- [ ] **Step 1: Add imports**

In `web/src/pages/Providers.tsx`, update the lucide-react import line to include `Trash2`:

Change:
```tsx
import { Pencil, Plus, RefreshCw } from "lucide-react";
```
to:
```tsx
import { Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
```

Update the `@/api/client` import to include `deleteProvider`:

Change:
```tsx
import {
  addProvider,
  addProviderKey,
  getProviders,
  patchProviderKey,
} from "@/api/client";
```
to:
```tsx
import {
  addProvider,
  addProviderKey,
  deleteProvider,
  getProviders,
  patchProviderKey,
} from "@/api/client";
```

- [ ] **Step 2: Add the delete mutation and dialog to `ProviderCard`**

Inside `ProviderCard`, add state and a mutation. After the existing `addKey` mutation definition (the `const addKey = useMutation({...})` block), add:

```tsx
  const [delOpen, setDelOpen] = useState(false);
  const delProvider = useMutation({
    mutationFn: () => deleteProvider(props.p.name),
    onSuccess: () => {
      setDelOpen(false);
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });
```

- [ ] **Step 3: Add the Delete button to the card header**

In `ProviderCard`, in the `<div className="flex items-center gap-2">` that holds Edit and Add key, add a Delete button before the Edit link:

Change:
```tsx
        <div className="flex items-center gap-2">
          <Link
            to={`/providers/${encodeURIComponent(props.p.name)}`}
            className="inline-flex"
            title="Open provider detail"
          >
            <Button variant="outline">
              <Pencil size={14} /> Edit
            </Button>
          </Link>
          <Button variant="outline" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add key
          </Button>
        </div>
```
to:
```tsx
        <div className="flex items-center gap-2">
          <Link
            to={`/providers/${encodeURIComponent(props.p.name)}`}
            className="inline-flex"
            title="Open provider detail"
          >
            <Button variant="outline">
              <Pencil size={14} /> Edit
            </Button>
          </Link>
          <Button variant="outline" onClick={() => setAddOpen(true)}>
            <Plus size={14} /> Add key
          </Button>
          <Button variant="danger" onClick={() => setDelOpen(true)}>
            <Trash2 size={14} /> Delete
          </Button>
        </div>
```

- [ ] **Step 4: Add the confirmation Dialog**

After the existing add-key `<Dialog>` (the one with `open={addOpen}`) inside `ProviderCard`, add:

```tsx
      <Dialog open={delOpen} title={`Delete provider ${props.p.name}?`} onClose={() => setDelOpen(false)}>
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          This removes the account and all its keys from the live pool. If any
          model group still references it, deletion will be blocked.
        </p>
        {delProvider.error && (
          <div className="mt-3">
            <ErrorText>{delProvider.error.message}</ErrorText>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="ghost" type="button" onClick={() => setDelOpen(false)}>
            Cancel
          </Button>
          <Button variant="danger" disabled={delProvider.isPending} onClick={() => delProvider.mutate()}>
            Delete
          </Button>
        </div>
      </Dialog>
```

- [ ] **Step 5: Verify it type-checks and builds**

Run: `cd web && bunx tsc --noEmit && bun run build`
Expected: no type errors, build succeeds.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/Providers.tsx
git commit -m "Add delete provider button to providers page"
```

---

### Task 6: Frontend — Account settings card + delete on Provider detail page

**Files:**
- Modify: `web/src/pages/ProviderDetail.tsx`

**Interfaces:**
- Consumes: `patchProvider`, `deleteProvider`, `deleteProviderKey` from `@/api/client`; `useNavigate` from react-router-dom; `Dialog`, `Button`, `Card`, `CardHeader`, `Field`, `Input`, `Select`, `ErrorText`, `Badge` from `@/components/ui`; `Trash2` icon from lucide-react
- Produces: an "Account settings" card at the top of the grid (editable name/type/base_url with Save), a delete-provider button in the page header, and a per-key Delete column in the key pool table.

- [ ] **Step 1: Update imports**

In `web/src/pages/ProviderDetail.tsx`:

Change:
```tsx
import { Link, useParams } from "react-router-dom";
```
to:
```tsx
import { Link, useNavigate, useParams } from "react-router-dom";
```

Change:
```tsx
import { ArrowLeft, Plus, RefreshCw, Search, X } from "lucide-react";
```
to:
```tsx
import { ArrowLeft, Plus, RefreshCw, Search, Trash2, X } from "lucide-react";
```

Change:
```tsx
import {
  addDeployment,
  fetchProviderModels,
  getModels,
  getProviders,
  patchProviderKey,
  addProviderKey,
} from "@/api/client";
```
to:
```tsx
import {
  addDeployment,
  deleteProvider,
  deleteProviderKey,
  fetchProviderModels,
  getModels,
  getProviders,
  patchProvider,
  patchProviderKey,
  addProviderKey,
} from "@/api/client";
```

- [ ] **Step 2: Add per-key delete to the KeyRow component**

In the `KeyRow` function inside `ProviderDetail.tsx`, add a delete mutation and a delete button.

After the existing `patch` mutation (`const patch = useMutation({...})`), add:

```tsx
  const del = useMutation({
    mutationFn: () => deleteProviderKey(props.provider, props.k.label),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["providers"] }),
    onError: (e) => props.onError(e.message),
  });
```

Update the `<Table head={...}>` call in `KeyPoolCard` to add a delete column header. Change:
```tsx
        <Table head={["Label", "Key", "Weight", "Status", ""]}>
```
to:
```tsx
        <Table head={["Label", "Key", "Weight", "Status", "Action"]}>
```

Add a delete button cell in the `KeyRow` return. After the existing `<TD>` that holds the Enable/Disable button, add a new `<TD>` with the delete button. The last `<TD>` currently is:

```tsx
      <TD>
        <Button variant="outline" disabled={patch.isPending}
                onClick={() => patch.mutate({ enabled: !props.k.enabled })}>
          {props.k.enabled ? "Disable" : "Enable"}
        </Button>
      </TD>
    </tr>
```

Change it to:

```tsx
      <TD>
        <Button variant="outline" disabled={patch.isPending}
                onClick={() => patch.mutate({ enabled: !props.k.enabled })}>
          {props.k.enabled ? "Disable" : "Enable"}
        </Button>
      </TD>
      <TD>
        <Button
          variant="danger"
          aria-label="Delete key"
          disabled={del.isPending}
          onClick={() => {
            if (window.confirm(`Delete key "${props.k.label}"? This cannot be undone.`)) {
              del.mutate();
            }
          }}
        >
          <Trash2 size={14} />
        </Button>
      </TD>
    </tr>
```

- [ ] **Step 3: Add the AccountSettingsCard component**

Add this new component before the `ProviderDetailPage` function in `ProviderDetail.tsx`:

```tsx
// -- account settings (edit metadata) -------------------------------------------

function AccountSettingsCard(props: { p: Provider; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(props.p.name);
  const [type, setType] = useState(props.p.provider_type);
  const [baseUrl, setBaseUrl] = useState(props.p.base_url);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const patch: { name?: string; base_url?: string; provider_type?: string } = {};
      if (name.trim() !== props.p.name) patch.name = name.trim();
      if (type !== props.p.provider_type) patch.provider_type = type;
      if (baseUrl.trim() !== props.p.base_url) patch.base_url = baseUrl.trim();
      return patchProvider(props.p.name, patch);
    },
    onSuccess: (data) => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["providers"] });
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
      if (data.name !== props.p.name) {
        navigate(`/providers/${encodeURIComponent(data.name)}`, { replace: true });
      }
    },
    onError: (e) => setError(e.message),
  });

  const dirty =
    name.trim() !== props.p.name ||
    type !== props.p.provider_type ||
    baseUrl.trim() !== props.p.base_url;

  return (
    <Card className="xl:col-span-3">
      <CardHeader title="Account settings" subtitle="Rename, change type or base URL." />
      <div className="space-y-3 px-4 pb-4 pt-2">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="openai-backup" />
          </Field>
          <Field label="Type">
            <Select
              value={type}
              onChange={setType}
              options={[
                { value: "openai", label: "OpenAI" },
                { value: "anthropic", label: "Anthropic" },
                { value: "gemini", label: "Gemini" },
                { value: "openai-compatible", label: "OpenAI-compatible URL" },
              ]}
            />
          </Field>
        </div>
        <Field label="Base URL" hint="Optional for openai/anthropic/gemini. Required for compatible URLs.">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
        </Field>
        {error && <ErrorText>{error}</ErrorText>}
        <div className="flex justify-end">
          <Button disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
```

- [ ] **Step 4: Add the delete-provider button + dialog to the page**

In the `ProviderDetailPage` function, add the delete-provider mutation. After the `const q = useQuery(...)` line and before the `const p = ...` line, add:

```tsx
  const navigate = useNavigate();
  const [delOpen, setDelOpen] = useState(false);
  const delProvider = useMutation({
    mutationFn: () => deleteProvider(name),
    onSuccess: () => {
      setDelOpen(false);
      navigate("/providers");
    },
    onError: (e) => setError(e.message),
  });
```

In the `PageHeader`'s `right` prop, add a delete button to the existing `<div className="flex items-center gap-2">`. Change:

```tsx
        right={
          <div className="flex items-center gap-2">
            <Badge tone={p.healthy ? "green" : "red"}>
              {p.healthy ? "healthy" : "no healthy keys"}
            </Badge>
            <Button variant="outline" onClick={() => void q.refetch()}>
              <RefreshCw size={14} /> Refresh
            </Button>
          </div>
        }
```

to:

```tsx
        right={
          <div className="flex items-center gap-2">
            <Badge tone={p.healthy ? "green" : "red"}>
              {p.healthy ? "healthy" : "no healthy keys"}
            </Badge>
            <Button variant="outline" onClick={() => void q.refetch()}>
              <RefreshCw size={14} /> Refresh
            </Button>
            <Button variant="danger" onClick={() => setDelOpen(true)}>
              <Trash2 size={14} /> Delete provider
            </Button>
          </div>
        }
```

After the `{error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}` line and before the grid `<div className="grid grid-cols-1 gap-4 xl:grid-cols-3">`, add the delete confirmation dialog:

```tsx
      <Dialog open={delOpen} title={`Delete provider ${p.name}?`} onClose={() => setDelOpen(false)}>
        <p className="text-[13px] text-[var(--admin-text-muted)]">
          This removes the account and all its keys from the live pool. If any
          model group still references it, deletion will be blocked.
        </p>
        {delProvider.error && (
          <div className="mt-3">
            <ErrorText>{delProvider.error.message}</ErrorText>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-4">
          <Button variant="ghost" type="button" onClick={() => setDelOpen(false)}>
            Cancel
          </Button>
          <Button variant="danger" disabled={delProvider.isPending} onClick={() => delProvider.mutate()}>
            Delete
          </Button>
        </div>
      </Dialog>
```

- [ ] **Step 5: Add AccountSettingsCard to the grid**

In the `ProviderDetailPage` return, add `<AccountSettingsCard>` as the first child inside the grid. Change:

```tsx
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <KeyPoolCard p={p} onError={setError} />
```

to:

```tsx
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <AccountSettingsCard p={p} onError={setError} />
        <KeyPoolCard p={p} onError={setError} />
```

- [ ] **Step 6: Add the missing imports for Dialog and Trash2 in the UI import**

Ensure the `@/components/ui` import in `ProviderDetail.tsx` includes `Dialog`. Change:

```tsx
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Spinner,
  Table,
  TD,
} from "@/components/ui";
```

to:

```tsx
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Spinner,
  Table,
  TD,
} from "@/components/ui";
```

- [ ] **Step 7: Verify it type-checks and builds**

Run: `cd web && bunx tsc --noEmit && bun run build`
Expected: no type errors, build succeeds. The build output lands in `wiwi/server/static/`.

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/ProviderDetail.tsx wiwi/server/static/
git commit -m "Add provider edit page with account settings and key deletion"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run full backend test suite + lint**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check wiwi/ tests/`
Expected: all pass, no lint errors.

- [ ] **Step 2: Verify frontend build is current**

Run: `cd web && bun run build`
Expected: build succeeds, output in `wiwi/server/static/`.

- [ ] **Step 3: Manual smoke test (if a gateway is running)**

Start the gateway (if a `wiwi.yaml` exists):
```bash
wiwi --config wiwi.yaml &
cd web && bun run dev
```
Open `http://localhost:5173/providers`:
1. Each provider card should show Edit, Add key, and Delete buttons.
2. Click Delete on an unreferenced provider → confirmation → deleted.
3. Click Delete on a referenced provider → 409 error shown in the dialog.
4. Click Edit → opens `/providers/:name` detail page.
5. Account settings card at top: edit name → Save → URL updates to new name. Edit base_url/type → Save → reflected in list.
6. Delete provider button in page header → confirmation → navigates back to `/providers`.
7. Key pool table: each row has a delete (trash) button → confirm → key removed.

- [ ] **Step 4: Final commit if any build artifacts changed**

```bash
git status --short
# if wiwi/server/static/ changed and isn't already committed:
git add wiwi/server/static/
git commit -m "Rebuild admin UI static assets"
```
