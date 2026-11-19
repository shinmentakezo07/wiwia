"""Round 208 — a credential-less key must not be routed, must not read as 502,
and a backup restore must never present one as a live pool member.

``GET /admin/providers/<name>/models`` picked the first key whose
``ProviderKey.available`` was true; a key with an empty ``secret`` — the
placeholder a no-secrets backup import deliberately creates
(``backup._apply_keys``) — was therefore selected. The adapter then built
``Authorization: Bearer `` and httpx refused to send it at all
(``LocalProtocolError``, raised before connect), which the handler reported as
``502 could not reach``: no upstream was ever contacted, and the message named
the wrong cause.

The import side caused the state in the first place: a secret-less backup
created those placeholders *enabled*, with the same ``***`` mask as a live
credential, so a restored gateway looked configured and every fetch answered
``no available key`` with no reason given. Properties pinned here:

1. ``ProviderKey.available`` is false for an empty or whitespace-only secret,
   so no rotation path (chat traffic included) hands such a key out.
2. The admin fetch answers ``409`` naming the missing credential instead of a
   misleading ``502``, and a genuine transport fault still reports its httpx
   exception class.
3. A secret-less merge creates the placeholder **disabled**, persists that,
   and names it in ``keys_without_secret`` so the console can say so.
4. ``PATCH /admin/providers/<name>/keys/<label>`` accepts ``secret`` — the
   repair path — persists it, and refuses an empty value.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import respx
import sqlalchemy as sa
from asgi_lifespan import LifespanManager

import wiwi.server.app as app_mod
from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.router.router import ProviderKey

MASTER = "sk-wiwi-master-test"
AUTH = {"Authorization": f"Bearer {MASTER}"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="sk-test-key-abcdef123456")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


def _empty_config() -> WiwiConfig:
    return WiwiConfig(general_settings=GeneralSettings(
        master_key=MASTER, database_url="sqlite+aiosqlite:///:memory:"))


@contextlib.asynccontextmanager
async def _client(cfg: WiwiConfig | None = None):
    app = app_mod.create_app(cfg or _config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.wiwi = app.state.wiwi
            yield c


def test_credential_less_key_is_unavailable():
    for secret in ("", "   "):
        key = ProviderKey(label="x", secret=secret)
        assert key.enabled and key.status == "active"
        assert not key.available, "an empty secret cannot build an auth header"
    assert ProviderKey(label="x", secret="sk-live").available


@respx.mock
async def test_models_route_reports_missing_secret_instead_of_502():
    route = respx.get("https://api.openai.com/v1/models").respond(
        json={"data": [{"id": "gpt-4o"}]})
    async with _client() as client:
        # The pool member exists and is enabled, but carries no credential
        # — the shape a secret-less backup restore leaves behind.
        client.wiwi.router.providers["p1"].keys[0].secret = ""
        r = await client.get("/admin/providers/p1/models", headers=AUTH)
    assert r.status_code == 409, r.text
    assert "without a secret" in r.json()["error"]["message"]
    assert not route.called, "no request can be sent without a credential"


@respx.mock
async def test_models_route_names_the_transport_failure():
    respx.get("https://api.openai.com/v1/models").mock(
        side_effect=httpx.ConnectError("boom"))
    async with _client() as client:
        r = await client.get("/admin/providers/p1/models", headers=AUTH)
    assert r.status_code == 502, r.text
    assert "ConnectError" in r.json()["error"]["message"]


async def test_secretless_merge_creates_a_disabled_placeholder():
    async with _client() as src:
        doc_r = await src.get("/admin/backup?sections=providers,keys", headers=AUTH)
        assert doc_r.status_code == 200, doc_r.text
        doc = doc_r.json()
    assert doc["keys"] and "secret" not in doc["keys"][0]

    # A fresh gateway: the placeholder only gets created where the key does
    # not exist yet (an existing key keeps its live credential).
    async with _client(_empty_config()) as tgt:
        m = await tgt.post("/admin/backup/import",
                           headers=AUTH, content=json.dumps(doc))
        assert m.status_code == 200, m.text
        assert m.json()["keys_without_secret"] == ["p1/a"], m.text

        live = tgt.wiwi.router.providers["p1"].keys[0]
        assert live.secret == ""
        assert live.enabled is False, (
            "a key with no credential must not be presented as live")

        async with tgt.wiwi.config_store.engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT label, enabled FROM provider_keys"))).all()
        assert rows == [("a", 0)], f"the disabled placeholder was not persisted: {rows}"

        # Nothing can be fetched, and the reason names the credential.
        r = await tgt.get("/admin/providers/p1/models", headers=AUTH)
        assert r.status_code == 409, r.text
        assert "without a secret" in r.json()["error"]["message"]


@respx.mock
async def test_patch_key_secret_repairs_a_credential_less_key():
    upstream = respx.get("https://api.openai.com/v1/models").respond(
        json={"data": [{"id": "gpt-4o"}]})
    async with _client() as client:
        acct = client.wiwi.router.providers["p1"]
        acct.keys[0].secret = ""
        acct.keys[0].enabled = False

        listing = await client.get("/admin/providers", headers=AUTH)
        assert listing.json()["providers"][0]["keys"][0]["has_secret"] is False

        bad = await client.patch("/admin/providers/p1/keys/a",
                                 headers=AUTH, json={"secret": "  "})
        assert bad.status_code == 400, bad.text

        r = await client.patch("/admin/providers/p1/keys/a", headers=AUTH,
                               json={"secret": "sk-live-replacement",
                                     "enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["key"]["has_secret"] is True

        # Persisted, not memory-only.
        async with client.wiwi.config_store.engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT secret, enabled FROM provider_keys"))).all()
        assert rows == [("sk-live-replacement", 1)], rows

        # And the pool works again.
        fetch = await client.get("/admin/providers/p1/models", headers=AUTH)
        assert fetch.status_code == 200, fetch.text
        assert upstream.called


async def test_secret_carrying_export_with_an_empty_key_still_imports():
    """The export/import defect that started this: ``?include_secrets=1`` wrote
    ``"secret": ""`` for a credential-less key and the import validator then
    rejected the file with "keys[0].secret is required" — a gateway could not
    round-trip its own backup."""
    async with _client() as src:
        src.wiwi.router.providers["p1"].keys[0].secret = ""
        doc_r = await src.get("/admin/backup?sections=providers,keys"
                              "&include_secrets=1", headers=AUTH)
    assert doc_r.status_code == 200, doc_r.text
    doc = doc_r.json()
    assert doc["include_secrets"] is True
    assert "secret" not in doc["keys"][0], (
        "an empty credential must be exported as absent, not as an empty value")

    async with _client(_empty_config()) as tgt:
        m = await tgt.post("/admin/backup/import",
                           headers=AUTH, content=json.dumps(doc))
    assert m.status_code == 200, m.text
    assert m.json()["keys_without_secret"] == ["p1/a"]
    assert m.json()["keys"] == 1


async def test_hand_edited_empty_secret_is_accepted_as_absent():
    async with _client() as src:
        doc = (await src.get("/admin/backup?sections=providers,keys",
                             headers=AUTH)).json()
    doc["keys"][0]["secret"] = ""          # what an older export emitted
    async with _client(_empty_config()) as tgt:
        m = await tgt.post("/admin/backup/import",
                           headers=AUTH, content=json.dumps(doc))
    assert m.status_code == 200, m.text
    assert m.json()["keys_without_secret"] == ["p1/a"]


async def test_secretless_merge_does_not_erase_a_stored_credential(tmp_path):
    """The durable row outranks the in-memory pool.

    ``_apply_keys`` used to decide "does the target have this key" from
    ``acct.get_key`` alone and then persist with ``add_key``, whose upsert
    writes ``secret=EXCLUDED.secret``. When the key was missing from memory
    while its DB row carried a credential — a tombstoned or not-yet-loaded
    row — a *secret-less* merge wrote ``""`` over it. That is how a pool that
    had just served 1 500 requests could end up credential-less.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'keys.db'}"
    cfg = WiwiConfig(general_settings=GeneralSettings(
        master_key=MASTER, database_url=url))

    async with _client(cfg) as src:
        r = await src.post("/admin/providers", headers=AUTH, json={
            "name": "p1", "provider_type": "openai",
            "base_url": "https://api.openai.com/v1",
            "key": "sk-live-credential-123456", "label": "a"})
        assert r.status_code in (200, 201), r.text
        doc = (await src.get("/admin/backup?sections=providers,keys",
                             headers=AUTH)).json()
    assert "secret" not in doc["keys"][0]

    async with _client(cfg) as tgt:
        # The row is durable; only the live pool lacks it.
        tgt.wiwi.router.providers["p1"].keys.clear()
        m = await tgt.post("/admin/backup/import",
                           headers=AUTH, content=json.dumps(doc))
        assert m.status_code == 200, m.text
        assert not m.json().get("keys_without_secret"), m.text
        live = tgt.wiwi.router.providers["p1"].keys[0]
        assert live.secret == "sk-live-credential-123456"
        assert live.enabled is True

        async with tgt.wiwi.config_store.engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT secret, enabled FROM provider_keys"))).all()
        assert rows == [("sk-live-credential-123456", 1)], rows
