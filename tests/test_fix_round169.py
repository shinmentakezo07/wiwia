"""Round 169 — the /console/settings Import panel: four defects found by
driving the console's own API against a live gateway.

Every defect here was found by *driving* the panel's API (not by reading the
React) and then reading the result back. The pattern in the first two is the
same as round 161: the endpoint answers ``200`` with a per-section count, the
console renders a confident sentence, and what actually happened on disk is
something else.

1. **A no-secrets export still wrote Hermes OAuth tokens in clear text**
   (AUDIT #509). The export redacts credential-bearing settings by matching
   a single prefix, ``"cline_oauth:"``. The Hermes OAuth record is stored
   under ``"hermes_oauth:<provider>"``, so it never matched the rule and
   went into the downloaded file verbatim — refresh token and all — even
   though the console says the file "carry[s] no secrets" when the toggle is
   off.

2. **The import's ``?sections=`` filter was ignored — every section was
   merged** (AUDIT #510). ``validate_backup`` stored the requested sections
   in ``plan["sections"]`` and ``summarize()`` reported them, but no
   ``_apply_*`` function ever read that key: ``apply_backup`` called all of
   them unconditionally. The section picker is the console's only scope
   control on the import side, and unchecking "Request log" merged the
   request log anyway. The dry-run preview made it worse — it reported the
   *document's* counts, not the *selection's*, so the preview the operator
   approved was not the merge that ran.

3. **The About tab stated facts the code contradicted** (AUDIT #511). It
   claimed "DB persistence lands post-MVP" (the DB sink is created
   unconditionally and is the primary stats source), listed three inbound
   surfaces when the gateway serves five, and linked its "Models —
   configure deployments" tile to the *public* catalog page instead of the
   admin deployments page.

4. **The General tab's "Last checked" ignored the 24-hour clock
   preference** (AUDIT #512). It was hardcoded ``hour12: false``, so the
   console showed two different clock formats at once.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
import time

import httpx
import pytest
import sqlalchemy as sa
from asgi_lifespan import LifespanManager

from wiwi.config import GeneralSettings, WiwiConfig
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}

#: The web source, for the two findings that live in the console's React.
WEB = pathlib.Path(__file__).resolve().parents[1] / "web" / "src"


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


async def _merge(client, doc, sections=None, dry_run=False):
    q = f"sections={','.join(sections)}" if sections else ""
    if dry_run:
        q = f"{q}&dry_run=1" if q else "dry_run=1"
    url = "/admin/backup/import" + (f"?{q}" if q else "")
    return await client.post(url, headers=H, content=json.dumps(doc))


async def _add_provider(client, name="srcprov", key="sk-SECRET-VALUE"):
    r = await client.post("/admin/providers", headers=H, json={
        "name": name, "provider_type": "openai",
        "base_url": "https://api.openai.com/v1",
        "label": "k1", "key": key})
    assert r.status_code == 200, r.text


async def _add_log_row(client, request_id="req-abc-123"):
    async with client.wiwi.config_store.engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO request_logs (ts, request_id, surface, key_alias,"
            " key_id, model_group, provider, provider_key_label, status,"
            " tok_in, tok_out, cost, was_stream, latency_ms)"
            " VALUES (:ts, :rid, 'chat', 'ka', 'kid', 'gpt-4o', 'openai',"
            " 'k1', 200, 10, 5, 0.001, 0, 12)"),
            {"ts": time.time(), "rid": request_id})


async def _count(client, table):
    async with client.wiwi.config_store.engine.connect() as conn:
        return (await conn.execute(sa.text(
            f"SELECT COUNT(*) FROM {table}"))).scalar()


# ---------------------------------------------------------------------------
# 1. AUDIT #509 — a no-secrets export must not carry OAuth credentials
# ---------------------------------------------------------------------------


async def test_no_secrets_export_redacts_hermes_oauth_record(client):
    """A Hermes OAuth record must be redacted like a Cline one.

    The redaction rule keyed on ONE prefix (``cline_oauth:``), so the
    Hermes record — stored under ``hermes_oauth:<provider>`` — was written
    into the downloaded file with its refresh token intact, even with the
    "Include plaintext provider keys" toggle off.
    """
    await client.wiwi.config_store.set_setting(
        "hermes_oauth:myhermes",
        {"connected": True, "email": "a@b.c",
         "refresh_token": "REFRESH-SECRET-abc123",
         "access_token": "ACCESS-SECRET-def456"})
    await client.wiwi.config_store.set_setting(
        "cline_oauth:mycline",
        {"connected": True, "email": "a@b.c",
         "refresh_token": "CLINE-REFRESH-xyz789"})

    doc = await _export(client, sections="settings")

    assert doc["include_secrets"] is False
    settings = doc["settings"]
    for key, record in settings.items():
        if not isinstance(record, dict):
            continue
        for field in ("refresh_token", "access_token", "token"):
            assert field not in record, (
                f"settings[{key!r}] leaked {field} into a no-secrets export: "
                f"{record!r}")
        assert record.get("redacted") is True, (
            f"settings[{key!r}] is an OAuth record but was not marked "
            f"redacted: {record!r}")


async def test_no_secrets_export_leaks_no_secret_anywhere_in_the_document(client):
    """No part of a no-secrets document may contain a credential marker.

    A whole-document sweep, because the settings table is a dynamic
    namespace: an enumeration of the keys that happen to hold a secret
    today is the defect that produced #509 in the first place.
    """
    await client.wiwi.config_store.set_setting(
        "hermes_oauth:myhermes",
        {"connected": True, "email": "a@b.c",
         "refresh_token": "REFRESH-SECRET-abc123"})
    await _add_provider(client, key="sk-SECRET-VALUE")

    doc = await _export(client)

    blob = json.dumps(doc)
    assert "REFRESH-SECRET-abc123" not in blob
    assert "sk-SECRET-VALUE" not in blob


async def test_secrets_export_still_carries_the_hermes_refresh_token(client):
    """The redaction is opt-out, not opt-in: with the toggle on, the record
    must still be there — otherwise the fix would silently break a
    credential backup."""
    await client.wiwi.config_store.set_setting(
        "hermes_oauth:myhermes",
        {"connected": True, "email": "a@b.c",
         "refresh_token": "REFRESH-SECRET-abc123"})

    doc = await _export(client, sections="settings", include_secrets="1")

    record = doc["settings"]["hermes_oauth:myhermes"]
    assert record.get("refresh_token") == "REFRESH-SECRET-abc123"
    assert "redacted" not in record


# ---------------------------------------------------------------------------
# 2. AUDIT #510 — the import must honour the section filter
# ---------------------------------------------------------------------------


async def test_import_merges_only_the_requested_sections(client):
    """Unchecking "Request log" must keep the request log out.

    The picker is the console's only scope control on the import side. The
    selection reached the server, was echoed back in the plan, and was then
    ignored by every apply function.
    """
    await _add_provider(client)
    await client.post("/admin/aliases", headers=H,
                      json={"set": {"fast": "gpt-4o"}})
    await _add_log_row(client)

    doc = await _export(client, sections="providers,keys,aliases,logs")
    assert len(doc["logs"]["request_logs"]) == 1

    async with client_for() as target:
        r = await _merge(target, doc, sections=["providers", "keys", "aliases"])
        assert r.status_code == 200, r.text

        # The selected sections landed...
        assert "srcprov" in target.wiwi.router.providers
        assert target.wiwi.router.settings.model_group_alias == {"fast": "gpt-4o"}
        # ...and the deselected one did not.
        assert await _count(target, "request_logs") == 0


async def test_import_counts_report_the_selection_not_the_document(client):
    """The merge's per-section counts must describe what was merged.

    They are what the console renders ("merged 3 providers, 1 alias…"), so a
    count of a section that was not merged is a lie in the operator's face.
    """
    await _add_provider(client)
    await _add_log_row(client)
    doc = await _export(client, sections="providers,keys,logs")

    async with client_for() as target:
        r = await _merge(target, doc, sections=["providers", "keys"])
        assert r.status_code == 200, r.text
        result = r.json()

        assert result["providers"] == 1
        assert result["keys"] == 1
        # The logs section was not selected, so it must not be reported as
        # merged — and it must not have been written.
        assert not result.get("logs"), (
            f"a deselected section was reported as merged: {result}")
        assert await _count(target, "request_logs") == 0


async def test_dry_run_preview_matches_what_the_merge_will_do(client):
    """The preview IS the validation, so it must describe the selection.

    The console enables the Merge button on the strength of the preview, so
    a preview that counts the whole document tells the operator the merge
    will do more than the selection they picked.
    """
    await _add_provider(client)
    await _add_log_row(client)
    doc = await _export(client, sections="providers,keys,logs")

    async with client_for() as target:
        preview = await _merge(target, doc, sections=["providers", "keys"],
                               dry_run=True)
        assert preview.status_code == 200, preview.text
        plan = preview.json()

        assert plan["dry_run"] is True
        assert plan["counts"]["providers"] == 1
        assert plan["counts"]["keys"] == 1
        assert "logs" not in plan["counts"], (
            f"the preview counted a deselected section: {plan}")


async def test_import_still_merges_everything_when_no_filter_is_given(client):
    """The default (no ``?sections=``) must keep merging the whole
    document — the fix gates on the selection, and the default selection is
    every section."""
    await _add_provider(client)
    await _add_log_row(client)
    doc = await _export(client, sections="providers,keys,logs")

    async with client_for() as target:
        r = await _merge(target, doc)
        assert r.status_code == 200, r.text

        assert "srcprov" in target.wiwi.router.providers
        assert await _count(target, "request_logs") == 1


async def test_no_filter_import_merges_the_sections_the_file_actually_carries(client):
    """With no filter, the file's CONTENT is the selection.

    A document's ``sections`` field is a *claim* about what it holds, and it
    is not always truthful — a file exported with ``?sections=logs`` and then
    hand-edited can carry a section it does not list. Trusting the claim
    dropped real content: the ``rollups`` section was present in the file
    but absent from its declared list, and the merge skipped it silently.

    The content is the truth; the declared list is only a hint.
    """
    await _add_log_row(client)
    doc = await _export(client, sections="logs")
    # A file that carries a section it does not declare.
    doc["rollups"] = {"request_rollups": [
        {"bucket_ts": 1000.0, "key_id": "k1", "model_group": "gpt-4o",
         "provider": "p1", "serving_model": "gpt-4o", "requests": 3}]}
    assert "rollups" not in doc["sections"]

    async with client_for() as target:
        r = await _merge(target, doc)
        assert r.status_code == 200, r.text
        result = r.json()

        assert result.get("rollups", {}).get("written") == 1, (
            f"a carried section was dropped because the file did not "
            f"declare it: {result}")


# ---------------------------------------------------------------------------
# 3. AUDIT #511 — the About tab must not state false facts
# ---------------------------------------------------------------------------


def _settings_source() -> str:
    return (WEB / "pages" / "Settings.tsx").read_text()


def _strip_comments(source: str) -> str:
    """Remove JSX and line comments from a TSX source.

    The assertions below are about what the console *says*, so the source
    must be compared without the prose that explains it — otherwise a
    comment naming the old wording ("DB persistence lands post-MVP") is
    mistaken for the wording itself.
    """
    without_jsx = re.sub(r"\{/\*.*?\*/\}", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", without_jsx)


def _section(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end)]


def test_about_tab_does_not_claim_db_persistence_is_post_mvp():
    """The DB sink is the primary stats store, not a future feature.

    ``AppState.init_db`` creates it unconditionally and
    ``/admin/stats/overview`` reads from it whenever it exists, precisely so
    that usage stats stop resetting on every redeploy. The 500-event ring
    is the fallback.
    """
    source = _strip_comments(_settings_source())
    about = _section(source, "function AboutTab", "function InfoTile")

    assert "post-MVP" not in about, (
        "the About tab still claims DB persistence is post-MVP; the DB sink "
        "is created unconditionally and is the primary stats source")


def test_about_tab_lists_every_inbound_surface():
    """The gateway serves five inbound surfaces, not three.

    The legacy ``POST /v1/completions`` and the cookie-gated
    ``POST /v1/playground/completions`` are real routes; omitting them from
    the console's own inventory understates the gateway.
    """
    source = _settings_source()
    about = _section(source, "function AboutTab", "function InfoTile")

    for label in ("OpenAI Chat", "OpenAI Responses", "Anthropic Messages",
                  "OpenAI Completions", "Playground"):
        assert label in about, (
            f"the About tab's inbound-surface list is missing {label!r}")


def test_about_tab_links_the_models_tile_to_the_admin_page():
    """The tile says "configure deployments", so it must go to the admin
    deployments page — not the public catalog."""
    source = _settings_source()
    tile = re.search(r'<InfoTile[^>]*title="Models"[^>]*/>', source)
    assert tile is not None, "the About tab's Models tile is gone"
    assert 'to="/console/models"' in tile.group(0), (
        f"the Models tile points somewhere else: {tile.group(0)}")


# ---------------------------------------------------------------------------
# 4. AUDIT #512 — the General tab's clock must honour the preference
# ---------------------------------------------------------------------------


def test_general_tab_clock_honours_the_24_hour_preference():
    """The "Last checked" row must not hardcode a 24-hour clock.

    The topbar honours the pref, so a hardcoded ``hour12: false`` left the
    console showing two different clock formats at the same time.
    """
    source = _strip_comments(_settings_source())
    general = _section(source, "function GeneralTab", "function AppearanceTab")

    assert "hour12: false" not in general, (
        "the General tab still hardcodes a 24-hour clock for 'Last checked'")
    assert "prefs.clock24h" in general or "useClientPrefs" in general, (
        "the General tab does not read the clock preference at all")
