"""Round 161 — the /console/settings Import path: what a merge claims to
restore versus what actually reaches the database.

Every defect here was found by *driving* the merge against a live gateway
and then reading the database back, not by reading the code. The pattern is
the same in all five: the merge answers ``200`` with a per-section count, the
console renders "merged N …", and the value is either gone on the next
restart or never worked in the first place.

1. **Aliases were applied in memory but never written to the database.**
   ``_apply_aliases`` merged the overlay into
   ``router.settings.model_group_alias`` and returned. Nothing called
   ``config_store.set_setting("model_group_alias", …)``, so the alias lived
   exactly as long as the process: the next restart re-read the settings
   table, found no overlay, and the alias was gone. The admin alias route
   (``POST /admin/aliases``) persists the same overlay, so the two writers
   of one feature disagreed.

2. **An imported virtual key's ``owner_id`` pointed at the SOURCE user id.**
   Users are keyed by ``username``; the target may already have that
   username under a different id. ``_apply_users`` preserves the target's
   id for an existing username (correct — ``vkeys.owner_id`` references it),
   but ``_apply_vkeys`` wrote the source's ``owner_id`` verbatim. The key
   therefore referenced a user row that does not exist, and the auth check
   — which fails CLOSED on a dangling owner (``auth/service.py``'s
   ``EXISTS`` subquery) — rejected it. The key authenticated on the source
   and was dead on the target, while the merge reported it as applied.

3. **A secret-less backup overwrote a live ``cline_oauth:*`` record with
   the redacted placeholder.** The export redacts the refresh token and
   marks the record ``{"redacted": True}``; the docstring on
   ``_redact_oauth`` says import "skips a redacted record rather than
   writing the placeholder over a live token". Nothing skipped it. The
   target's working Cline token was replaced by a record carrying no
   token at all.

4. **A provider key imported from a secret-less backup was never
   persisted.** ``_apply_keys`` writes the DB row only when the document
   carries a secret — a deliberate choice for *not clobbering* a live
   credential — but a key that does not exist yet on the target was
   therefore not created either. The key existed in memory with an empty
   secret and vanished on the next restart. Its ``weight``/``enabled``
   edits were lost the same way.

5. **A virtual key whose ``id`` collided with a live row was silently
   dropped but still counted as applied.** The insert is
   ``INSERT OR IGNORE`` / ``ON CONFLICT (id) DO NOTHING``, so a colliding
   id makes the row vanish without an error — and ``_apply_vkeys``
   increments its counter regardless. The console reported "merged 1
   virtual key" for a key that is not in the database.
"""

from __future__ import annotations

import contextlib
import json
import tempfile

import httpx
import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager

from wiwi.config import GeneralSettings, WiwiConfig
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}


def _config() -> WiwiConfig:
    return WiwiConfig(
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@contextlib.asynccontextmanager
async def client_for(cfg=None):
    app = create_app(cfg or _config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            c.wiwi = app.state.wiwi
            yield c


@pytest.fixture
async def client():
    async with client_for() as c:
        yield c


async def _export(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = await client.get(f"/admin/backup?{query}" if query else "/admin/backup",
                         headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _merge(client, doc, sections=None):
    q = f"sections={','.join(sections)}" if sections else ""
    url = "/admin/backup/import" + (f"?{q}" if q else "")
    return await client.post(url, headers=H, content=json.dumps(doc))


async def _rows(engine, sql):
    async with engine.connect() as conn:
        return (await conn.execute(sa.text(sql))).all()


# ---------------------------------------------------------------------------
# 1. aliases must be persisted
# ---------------------------------------------------------------------------


async def test_imported_alias_is_persisted_to_the_settings_table(client):
    """The alias overlay must reach the ``settings`` table, not just memory.

    ``POST /admin/aliases`` persists the overlay; the import path must do
    the same or the alias silently disappears on the next restart.

    The target is a *separate* gateway: the source already persisted its own
    alias through the admin route, so merging back into the source would
    find the row already there and prove nothing.
    """
    r = await client.post("/admin/aliases", headers=H,
                           json={"set": {"fast": "gpt-4o"}})
    assert r.status_code == 200, r.text
    doc = await _export(client, sections="aliases")
    assert doc["aliases"] == {"fast": "gpt-4o"}

    async with client_for() as tgt:
        m = await _merge(tgt, doc, sections=["aliases"])
        assert m.status_code == 200, m.text
        # in-memory
        assert tgt.wiwi.router.settings.model_group_alias["fast"] == "gpt-4o"
        # and on disk — the part that was missing
        stored = await tgt.wiwi.config_store.get_setting("model_group_alias")
        assert stored is not None, "the alias overlay was never persisted"
        assert stored["fast"] == "gpt-4o"


async def test_imported_alias_survives_a_restart():
    """A fresh AppState over the same database must still see the alias.

    The target gets its own database, and the restart builds a *fresh*
    ``WiwiConfig``: ``_apply_aliases`` mutates the router's settings object
    in place, and that object belongs to the config — reusing the same
    config instance across both apps would carry the in-memory mutation
    into the "restart" and prove nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src_db = f"sqlite+aiosqlite:///{tmp}/src.db"
        tgt_db = f"sqlite+aiosqlite:///{tmp}/tgt.db"

        def cfg_for(db: str) -> WiwiConfig:
            return WiwiConfig(general_settings=GeneralSettings(
                master_key="sk-wiwi-master-test", database_url=db))

        async with client_for(cfg_for(src_db)) as src:
            await src.post("/admin/aliases", headers=H,
                           json={"set": {"fast": "gpt-4o"}})
            doc = await _export(src, sections="aliases")
        # merge into the target
        async with client_for(cfg_for(tgt_db)) as tgt:
            m = await _merge(tgt, doc, sections=["aliases"])
            assert m.status_code == 200, m.text
        # restart: a new app, a new config, the same DB
        async with client_for(cfg_for(tgt_db)) as after:
            aliases = after.wiwi.router.settings.model_group_alias
            assert aliases.get("fast") == "gpt-4o", (
                "the alias did not survive the restart")


# ---------------------------------------------------------------------------
# 2. vkey owner_id must be remapped to the target's user ids
# ---------------------------------------------------------------------------


async def test_imported_vkey_owner_id_points_at_the_targets_user(client):
    """The source's user id does not exist on the target.

    The target already has ``alice`` under a different id, so the
    imported key's ``owner_id`` must be remapped — otherwise it references
    a row that does not exist and the auth check fails closed.
    """
    r = await client.post("/auth/signup",
                           json={"username": "alice", "password": "hunter2hunter2"})
    assert r.status_code == 201, r.text
    src_uid = r.json()["user"]["id"]
    doc = await _export(client, sections="users,vkeys")
    assert doc["users"][0]["id"] == src_uid

    # a *different* target that already has alice
    async with client_for() as tgt:
        r = await tgt.post("/auth/signup",
                           json={"username": "alice", "password": "hunter2hunter2"})
        tgt_uid = r.json()["user"]["id"]
        assert tgt_uid != src_uid

        m = await _merge(tgt, doc, sections=["users", "vkeys"])
        assert m.status_code == 200, m.text

        rows = await _rows(tgt.wiwi.auth.engine,
                           "SELECT owner_id FROM vkeys")
        user_ids = {x[0] for x in await _rows(tgt.wiwi.users.engine,
                                              "SELECT id FROM users")}
        for (owner_id,) in rows:
            if owner_id is None:
                continue
            assert owner_id in user_ids, (
                f"vkey owner_id {owner_id!r} has no users row — the key is "
                "orphaned and the auth check will reject it")
        assert tgt_uid in {r_[0] for r_ in rows}


async def test_imported_vkey_with_a_dangling_owner_is_rejected_by_auth(client):
    """The end-to-end consequence: a key that worked on the source must
    work on the target after the merge."""
    r = await client.post("/auth/signup",
                           json={"username": "alice", "password": "hunter2hunter2"})
    assert r.status_code == 201, r.text
    playground_key = r.json()["playground_key"]
    assert await client.wiwi.auth.authenticate(playground_key) is not None

    doc = await _export(client, sections="users,vkeys")

    async with client_for() as tgt:
        await tgt.post("/auth/signup",
                       json={"username": "alice", "password": "hunter2hunter2"})
        m = await _merge(tgt, doc, sections=["users", "vkeys"])
        assert m.status_code == 200, m.text
        info = await tgt.wiwi.auth.authenticate(playground_key)
        assert info is not None, (
            "the imported key does not authenticate on the target — its "
            "owner_id points at a user row that does not exist here")


# ---------------------------------------------------------------------------
# 3. a redacted settings record must not overwrite a live one
# ---------------------------------------------------------------------------


async def test_redacted_oauth_record_does_not_clobber_a_live_token(client):
    """A secret-less export carries ``{"redacted": True}``; importing it must
    leave the target's live refresh token alone."""
    await client.wiwi.config_store.set_setting(
        "cline_oauth:acct1",
        {"connected": True, "refresh_token": "SRC-TOKEN", "expires_at": 1.0})
    doc = await _export(client, sections="settings")
    exported = doc["settings"]["cline_oauth:acct1"]
    assert exported.get("redacted") is True
    assert "refresh_token" not in exported

    await client.wiwi.config_store.set_setting(
        "cline_oauth:acct1",
        {"connected": True, "refresh_token": "LIVE-TOKEN", "expires_at": 2.0})

    m = await _merge(client, doc, sections=["settings"])
    assert m.status_code == 200, m.text
    after = await client.wiwi.config_store.get_setting("cline_oauth:acct1")
    assert after.get("refresh_token") == "LIVE-TOKEN", (
        "the redacted placeholder overwrote a live token")


async def test_redacted_oauth_record_is_not_written_at_all(client):
    """A redacted record carries no information worth storing — it must not
    land in the settings table in place of the real one."""
    await client.wiwi.config_store.set_setting(
        "cline_oauth:acct1",
        {"connected": True, "refresh_token": "LIVE-TOKEN", "expires_at": 2.0})
    await client.wiwi.config_store.set_setting(
        "cline_oauth:acct2",
        {"connected": True, "refresh_token": "SRC-TOKEN", "expires_at": 1.0})
    doc = await _export(client, sections="settings")

    m = await _merge(client, doc, sections=["settings"])
    assert m.status_code == 200, m.text
    a1 = await client.wiwi.config_store.get_setting("cline_oauth:acct1")
    a2 = await client.wiwi.config_store.get_setting("cline_oauth:acct2")
    assert a1.get("refresh_token") == "LIVE-TOKEN"
    assert a2.get("refresh_token") == "SRC-TOKEN"


async def test_redacted_settings_are_reported_as_skipped(client):
    """The response must say the file carried records the merge declined to
    write — otherwise "0 settings applied" reads as "the file had none"."""
    await client.wiwi.config_store.set_setting(
        "cline_oauth:acct1",
        {"connected": True, "refresh_token": "SRC-TOKEN", "expires_at": 1.0})
    doc = await _export(client, sections="settings")

    m = await _merge(client, doc, sections=["settings"])
    assert m.status_code == 200, m.text
    body = m.json()
    assert body["settings_skipped"] == 1, (
        "the redacted record was not reported as skipped")
    assert body["settings"] == 0


# ---------------------------------------------------------------------------
# 4. provider keys must be persisted, secret or not
# ---------------------------------------------------------------------------


async def test_provider_key_from_a_secretless_backup_is_persisted(client):
    """A key the target does not have yet must be created even when the
    document carries no secret — otherwise it vanishes on the next restart."""
    r = await client.post("/admin/providers", headers=H,
                           json={"name": "pX", "provider_type": "openai",
                                 "base_url": "https://api.openai.com/v1",
                                 "key": "sk-secret", "label": "k1"})
    assert r.status_code in (200, 201), r.text
    doc = await _export(client, sections="providers,keys")
    assert doc["keys"] and "secret" not in doc["keys"][0]

    async with client_for() as tgt:
        m = await _merge(tgt, doc, sections=["providers", "keys"])
        assert m.status_code == 200, m.text
        rows = await _rows(tgt.wiwi.config_store.engine,
                           "SELECT provider_name, label FROM provider_keys")
        assert ("pX", "k1") in rows, (
            "the key was not persisted — it exists in memory only and is "
            "lost on the next restart")


async def test_provider_key_weight_and_enabled_are_persisted(client):
    """A weight edit on the source must survive the merge and the restart.

    The weight is set through the admin key PATCH route — the provider
    create route does not accept one — so the source's stored row really
    does carry the weight the export then reads.
    """
    r = await client.post("/admin/providers", headers=H,
                           json={"name": "pW", "provider_type": "openai",
                                 "base_url": "https://api.openai.com/v1",
                                 "key": "sk-secret", "label": "kw"})
    assert r.status_code in (200, 201), r.text
    r = await client.patch("/admin/providers/pW/keys/kw", headers=H,
                           json={"weight": 7, "enabled": False})
    assert r.status_code == 200, r.text
    doc = await _export(client, sections="providers,keys")
    assert doc["keys"][0]["weight"] == 7
    assert doc["keys"][0]["enabled"] is False

    async with client_for() as tgt:
        m = await _merge(tgt, doc, sections=["providers", "keys"])
        assert m.status_code == 200, m.text
        rows = await _rows(tgt.wiwi.config_store.engine,
                           "SELECT label, weight, enabled FROM provider_keys")
        assert rows, "no provider_keys row was written"
        assert rows[0][1] == 7, f"weight was not persisted: {rows}"
        assert rows[0][2] == 0, f"enabled was not persisted: {rows}"


# ---------------------------------------------------------------------------
# 5. a vkey id collision must not be reported as applied
# ---------------------------------------------------------------------------


async def test_vkey_id_collision_is_not_counted_as_applied(client):
    """The target already holds a row with that id.

    The imported key cannot be stored under that id, so the merge must not
    report it as applied — and, because a merge is meant to restore the
    key, it must be stored under a fresh id instead of being dropped.
    """
    r = await client.post("/admin/keys/generate", headers=H,
                           json={"alias": "source-key"})
    assert r.status_code == 200, r.text
    doc = await _export(client, sections="vkeys")
    src_id = doc["vkeys"][0]["id"]
    src_hash = doc["vkeys"][0]["key_hash"]
    assert src_id

    async with client_for() as tgt:
        # plant a row with the same id but a different hash
        async with tgt.wiwi.auth.engine.begin() as conn:
            await conn.execute(sa.text(
                "INSERT INTO vkeys (id, key_hash, key_alias, models,"
                " max_budget, spend_to_date, budget_reserved, rpm, tpm,"
                " expires_at, disabled, owner_id, priority, created_at,"
                " updated_at) VALUES (:i,:h,:a,'[]',NULL,0,0,NULL,NULL,NULL,0,"
                " NULL,NULL,0,0)"),
                {"i": src_id, "h": "f" * 64, "a": "collision"})

        m = await _merge(tgt, doc, sections=["vkeys"])
        assert m.status_code == 200, m.text
        rows = await _rows(tgt.wiwi.auth.engine,
                           "SELECT id, key_alias, key_hash FROM vkeys")
        landed = [x for x in rows if x[2] == src_hash]
        assert landed, (
            "the imported key was dropped by the id collision — a merge that "
            "restores nothing must not report the key as applied")
        # the surviving row must be addressable, i.e. under a fresh id
        assert landed[0][0] != src_id, (
            "the imported key reused the colliding id")
        # and the pre-existing row must be untouched
        kept = [x for x in rows if x[1] == "collision"]
        assert kept and kept[0][0] == src_id, "the existing row was clobbered"
        assert m.json()["vkeys"] == len(landed), (
            "the applied count does not match what is on disk")
