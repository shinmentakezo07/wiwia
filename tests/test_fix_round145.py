"""Round 145: the ``priority`` lane was write-only — actually write-*impossible*.

``vkeys.priority`` is the operator's concurrency-lane lever
(``RouterSettings.priority_lanes``): ``router.py`` reads it off the cached
``KeyInfo`` and routes the key's traffic to the named lane, so it genuinely
works at request time. It is also in ``UPDATABLE_FIELDS``, so ``PATCH
/admin/keys/{id}`` accepts it.

Two things stood between an operator and setting it:

1. **The write path rejected it.** ``update_key`` runs every non-``models``,
   non-``ttl_seconds`` field through ``_coerce_limit``, so ``priority=
   "interactive"`` hit ``float("interactive")`` and raised ``ValueError:
   priority must be a number`` → HTTP 400. A lane name is a string; the
   numeric validator was never taught about it.
2. **The read path dropped it.** ``list_keys`` selects 11 of the 15 ``vkeys``
   columns and omits ``priority``; ``list_keys_for_owner`` omits ``priority``
   *and* ``budget_reserved``. ``get_key`` is built on ``list_keys``, so
   ``update_key``'s return value — and therefore the 200 body of
   ``PATCH /admin/keys/{id}`` — omitted it too. The only place the value was
   visible at all was ``GET /admin/backup``.

A setting that cannot be written *and* is invisible when read back is worse
than an absent one: the API's 200 falsely implies it was applied.

The last test here is a **guard** against the sibling class of defect. The DB
row reader and ``public_dict(LogEvent)`` return 5 fields the TypeScript
``RequestLogEntry`` interface does not declare (``key_id``,
``tok_cache_creation``, ``usage_estimated``, ``response_cache_hit``,
``translation_warnings``). TypeScript's structural typing accepts the extra
runtime keys silently, so ``RequestLogs.tsx`` renders an incomplete row — it
can show ``tok_cached``/``cache_hit`` but never cache-*creation* tokens, an
estimated-usage flag, a response-cache hit, or the translation warnings that
``gateway._record_translation_warnings`` exists to surface. Parsing the ``.ts``
file and comparing it against ``dataclasses.fields(LogEvent)`` is the same
drift-guard shape as ``tests/test_fix_round141.py``'s
``db_sink._COLS`` vs ``backup._REQUEST_LOG_COLUMNS`` check.
"""

from __future__ import annotations

import contextlib
import dataclasses
import re

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import GeneralSettings, WiwiConfig
from wiwi.logging_core.events import LogEvent
from wiwi.server.app import create_app

H = {"Authorization": "Bearer sk-wiwi-master-test"}

TYPES_TS = "web/src/api/types.ts"


def _config() -> WiwiConfig:
    return WiwiConfig(
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )


@contextlib.asynccontextmanager
async def client_for():
    app = create_app(_config())
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


# ---------------------------------------------------------------------------
# 1. the write path accepts a lane name, and the read path shows it back
# ---------------------------------------------------------------------------


async def test_patch_key_accepts_a_priority_lane(client):
    """``PATCH /admin/keys/{id}`` with a lane name must not 400.

    This is the step that was broken before the fix: ``update_key`` fed every
    non-``models``/``ttl_seconds`` field to ``_coerce_limit``, so a lane string
    raised ``ValueError`` and the endpoint answered 400 — the operator could
    not set a lane at all through the admin API.
    """
    key_id = (await client.post("/admin/keys/generate", headers=H,
                                json={"alias": "lane"})).json()["id"]

    r = await client.patch(f"/admin/keys/{key_id}", headers=H,
                           json={"priority": "interactive"})
    assert r.status_code == 200, (
        f"setting a lane returned {r.status_code}: {r.text} — a lane name is "
        f"a string, not a number")
    assert r.json()["key"]["priority"] == "interactive", (
        "the 200 body omits the priority that was just applied — the API "
        "falsely implies the write took effect while hiding the value")


async def test_list_keys_reports_the_priority_lane(client):
    """``GET /admin/keys`` must carry the lane, not just ``GET /admin/backup``."""
    key_id = (await client.post("/admin/keys/generate", headers=H,
                                json={"alias": "lane"})).json()["id"]
    await client.patch(f"/admin/keys/{key_id}", headers=H,
                       json={"priority": "interactive"})

    listed = {k["id"]: k for k in (await client.get("/admin/keys",
                                                    headers=H)).json()["keys"]}
    assert listed[key_id]["priority"] == "interactive", (
        "the admin key list projects priority away — an operator-set lane is "
        "invisible everywhere it is read back except the backup export")

    # get_key is built on list_keys, so it has the same obligation.
    assert (await client.wiwi.auth.get_key(key_id))["priority"] == "interactive"


async def test_priority_defaults_to_none_and_can_be_cleared(client):
    """An unset lane is ``None``, and an explicit ``null`` clears it.

    ``None`` is what a lane-free key reads back as; if the projection defaulted
    to ``""`` a falsy-vs-``None`` check downstream would be ambiguous.
    """
    key_id = (await client.post("/admin/keys/generate", headers=H,
                                json={"alias": "plain"})).json()["id"]
    listed = {k["id"]: k for k in (await client.get("/admin/keys",
                                                    headers=H)).json()["keys"]}
    assert listed[key_id]["priority"] is None

    await client.patch(f"/admin/keys/{key_id}", headers=H,
                       json={"priority": "interactive"})
    cleared = await client.patch(f"/admin/keys/{key_id}", headers=H,
                                 json={"priority": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["key"]["priority"] is None


async def test_update_key_rejects_a_non_string_lane(client):
    """Validation, not a 500 and not a silent numeric coercion.

    A number is just as wrong for a lane as a bad string is, and
    ``_coerce_limit`` would have turned it into a stored lane of ``2.0``.
    """
    key_id = (await client.post("/admin/keys/generate", headers=H,
                                json={"alias": "lane"})).json()["id"]
    for bad in (5, 1.5, True, ["interactive"], {"lane": "interactive"}):
        r = await client.patch(f"/admin/keys/{key_id}", headers=H,
                               json={"priority": bad})
        assert r.status_code == 400, (
            f"priority={bad!r} returned {r.status_code}; a lane name must be a "
            f"string (400), never coerced or stored")

    listed = {k["id"]: k for k in (await client.get("/admin/keys",
                                                    headers=H)).json()["keys"]}
    assert listed[key_id]["priority"] is None, (
        "a rejected lane name must not have been written")


# ---------------------------------------------------------------------------
# 2. the owner projection
# ---------------------------------------------------------------------------


async def test_list_keys_for_owner_reports_priority_and_budget_reserved(client):
    """The tenant-facing projection dropped two live columns.

    ``priority`` for the same reason as above, and ``budget_reserved`` — the
    in-flight spend hold an operator's budget ceiling relies on — which the
    admin projection carries and this one did not.
    """
    auth = client.wiwi.auth
    owner = "user-with-a-key"
    _plaintext, key_id = await auth.create_key(
        owner_id=owner, alias="tenant", max_budget=10.0)
    await auth.update_key(key_id, {"priority": "interactive"})
    await auth.reserve_budget(key_id, 1.5)

    rows = {k["id"]: k for k in await auth.list_keys_for_owner(owner)}
    assert rows[key_id]["priority"] == "interactive", (
        "list_keys_for_owner projects priority away")
    assert rows[key_id]["budget_reserved"] == pytest.approx(1.5), (
        "list_keys_for_owner projects budget_reserved away — a tenant's own "
        "in-flight budget hold is invisible to them and to the admin list "
        "they are compared against")

    # Same row, read through the admin projection: no drift between the two.
    admin_row = {k["id"]: k for k in await auth.list_keys()}[key_id]
    assert admin_row["priority"] == rows[key_id]["priority"]
    assert admin_row["budget_reserved"] == rows[key_id]["budget_reserved"]


async def test_owner_projection_stays_scoped_to_its_owner(client):
    """Adding columns must not change *which* rows come back."""
    auth = client.wiwi.auth
    _p, mine = await auth.create_key(owner_id="u1", alias="mine")
    await auth.create_key(owner_id="u2", alias="theirs")
    await auth.update_key(mine, {"priority": "interactive"})

    rows = await auth.list_keys_for_owner("u1")
    assert [r["alias"] for r in rows] == ["mine"]
    assert rows[0]["priority"] == "interactive"


async def test_both_projections_agree_on_a_key_with_no_lane(client):
    """``None``, not ``""``, on both paths — a lane-free key is unambiguous."""
    auth = client.wiwi.auth
    _p, key_id = await auth.create_key(owner_id="u1", alias="plain")
    owner_row = (await auth.list_keys_for_owner("u1"))[0]
    admin_row = {k["id"]: k for k in await auth.list_keys()}[key_id]
    assert owner_row["priority"] is None
    assert admin_row["priority"] is None


# ---------------------------------------------------------------------------
# 3. drift guard — LogEvent vs the TypeScript RequestLogEntry interface
# ---------------------------------------------------------------------------

#: ``public_dict`` is ``asdict(LogEvent)`` minus ``diff``. Everything else is
#: what the request-log row actually carries, and the TS interface has to be
#: able to name it all.
_POPULATED = tuple(f.name for f in dataclasses.fields(LogEvent)
                   if f.name != "diff")

#: Deliberately exempt, with the reason:
#:
#: * ``diff`` — ``public_dict`` pops it; it is audit-only noise.
#: * ``level``/``message``/``actor``/``action``/``target`` — audit-log fields.
#:   They ride the same ``LogEvent`` dataclass, but they describe an operator
#:   action rather than a request, and the admin UI renders them from
#:   ``/admin/logs/audit``, not from a ``RequestLogEntry``.
#:
#: ``request_body``/``response_body`` are NOT exempt: the interface declares
#: them (optional, since they are gated on ``store_prompts_in_spend_logs``) and
#: the guard is what keeps it that way.
_AUDIT_ONLY = frozenset({"level", "message", "actor", "action", "target"})


def expected_fields() -> set[str]:
    """The populated LogEvent fields the TS interface must declare."""
    return set(_POPULATED) - _AUDIT_ONLY


def _ts_interface_fields(source: str, name: str) -> set[str]:
    """Field names declared by ``export interface <name> { … }`` in a .ts file.

    Deliberately a regex rather than a TS parser: the file has no build-time
    dependency available to pytest, and the interface bodies in question are
    flat ``name: type;`` declarations. The brace matcher is depth-aware so a
    nested object type (e.g. ``response_body?: { … }``) does not end the scan
    early.
    """
    m = re.search(rf"export interface {re.escape(name)}\s*\{{", source)
    assert m, f"{name} is not declared in {TYPES_TS}"
    depth, i = 1, m.end()
    while depth and i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"unbalanced braces in interface {name}"
    body = source[m.end():i - 1]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"^\s*(?:\"|')?([A-Za-z_]\w*)(?:\"|')?\s*[?]?\s*:",
                          body, flags=re.MULTILINE))


def test_ts_request_log_entry_declares_every_populated_logevent_field():
    """The guard this round is about.

    ``web/src/api/types.ts`` declaring 31 of ``LogEvent``'s 36 populated fields
    is invisible to both compilers: TypeScript's structural typing accepts the
    extra runtime keys, so ``RequestLogs.tsx`` renders a row that is quietly
    missing five columns. Comparing the two lists here turns the next added
    column into a suite failure instead of a silent omission.
    """
    with open(TYPES_TS, encoding="utf-8") as fh:
        source = fh.read()

    declared = _ts_interface_fields(source, "RequestLogEntry")
    expected = set(_POPULATED) - _AUDIT_ONLY
    missing = sorted(expected - declared)
    assert not missing, (
        f"RequestLogEntry does not declare {missing} — public_dict(LogEvent) "
        f"returns them and db_sink._COLS persists them, so the admin UI is "
        f"rendering an incomplete request-log row without any type error")

    stale = sorted((declared & set(_POPULATED)) - expected)
    assert not stale, (
        f"RequestLogEntry declares {stale}, which no LogEvent field backs — "
        f"the interface and the dataclass have drifted apart")


def test_public_dict_populates_exactly_the_logevent_fields():
    """The exemption set above is only trustworthy if it is what is popped."""
    from wiwi.logging_core.subsystem import public_dict

    populated = set(public_dict(LogEvent(stream="request", ts=1.0)))
    assert populated == set(_POPULATED)
    assert "diff" not in populated, "public_dict no longer pops `diff`"
    # The audit exemption must be masking fields that genuinely *are* emitted —
    # if one of these ever leaves the dataclass the guard above is hiding a
    # real gap rather than a deliberate carve-out.
    assert _AUDIT_ONLY <= set(_POPULATED), (
        "the audit exemption list names fields LogEvent no longer has")
    assert _AUDIT_ONLY <= populated
    # ...and the request-stream fields the interface declares are exactly the
    # non-audit remainder, so nothing is skipped by more than the carve-out.
    assert _AUDIT_ONLY.isdisjoint(expected_fields())