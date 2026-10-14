"""Whole-gateway JSON backup and restore.

``GET /admin/providers/export`` (``app.py``) already round-trips provider
accounts, their key pools, and deployments. Nothing else does: users, virtual
keys, budgets, alert rules, model prices, router settings, and the usage log all
had no export path, so standing up a second gateway meant reading the console
and re-keying by hand.

This module is the whole-inventory version of that pair — one document, one
pass, every table the admin UI can mutate.

Five design points worth knowing before editing:

**Export reads live merged state for routing, DB rows for everything else.**
The router is built from ``wiwi.yaml`` at startup and DB rows are layered on
top, skipping any name YAML already defined (``AppState._load_db_config``).
A DB-only export would silently omit every YAML-defined provider. Reading
``state.router`` captures the merged view — which is also what a restore into a
bare-YAML instance needs. The cost is that import persists everything to the DB;
on a source instance that already has the same entries in YAML the extra rows
are skipped at load (providers/keys) or act as same-valued overrides
(deployments), so re-importing is idempotent. Users, virtual keys, and the log
have no live mirror, and the public accessors for them are deliberately lossy
(``list_keys`` omits ``key_hash``, ``list_users`` omits
``password_hash``, ``read_requests`` omits ``id``), so those are read straight
from the DB.

**Virtual-key plaintext does not exist and never did.** ``hash_key`` is an
unsalted SHA-256 (``auth/keys.py``) and authentication matches on
``WHERE vkey.key_hash = :h`` (``auth/service.py``). The export carries the
hash; importing it verbatim restores a *working* key — the same plaintext
authenticates on the target. What cannot be restored is the ability to *print*
the key again. ``users.password_hash`` is the same story. Do not add a
plaintext virtual key to this format; there is nothing to add.

**Validate, then apply.** :func:`validate_backup` reads state but writes
nothing, and returns a fully-parsed plan; :func:`apply_backup` consumes that
plan. Any *validation* error aborts before a single row changes — the same
discipline ``admin_import_providers`` uses, made structural rather than
conventional. It also makes ``?dry_run=1`` free, which the console uses to show
the operator what a file will do before they commit to it.

**The bulk sections are atomic with respect to each other and to everything
before them.** ``logs``/``rollups``/``responses`` are applied first, in one
transaction, because they are the only sections big enough to fail part-way
through — and a per-page commit is how a malformed row 1 200 once left a
gateway with half an import on disk and no way to tell how far it got. A
failure there raises a 500 ``BackupError`` and rolls the whole pass back.
Per-row failures inside ``rollups``/``responses`` are the one exception: each
row gets its own SAVEPOINT and its failure is *counted and returned*
(``{"written": n, "failed": n}``) rather than swallowed — see
:func:`_apply_bulk`.

Import is **merge-only**: every section upserts on its natural key and nothing
is ever deleted. There is no replace mode — a typo cannot wipe a gateway. The
two things that remove anything are both removals the *source* recorded as
removals: a ``detached`` deployment and an ``alias: null`` tombstone.

**What is not in the file is not round-trippable, so it is not in the file.**
The export carries what the tables can hold. The five per-deployment limits
(``rpm``/``tpm``/``timeout``/``max_tokens``/``prompt_cache``) are the standing
example: ``deployments`` has no column for any of them, so exporting them
promised a persistence the restore could not deliver — see
:func:`_export_deployments`. Absent means *unknown*, never *reset*.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import orjson
import sqlalchemy as sa
import structlog

from wiwi.auth.service import _coerce_limit, _coerce_models
from wiwi.config import (
    PROVIDER_TYPES,
    _interpolate,
    alias_value_eq,
    alias_value_to_json,
    coerce_alias_value,
)
from wiwi.router.router import _default_base_url

#: Bumped when a section changes shape incompatibly. ``validate_backup``
#: refuses a document whose version it does not recognise rather than importing
#: fields it cannot map.
BACKUP_VERSION = 1

#: Canonical section order. Order matters on restore: providers before keys and
#: deployments, so a run interrupted partway still has resolvable references.
SECTIONS: tuple[str, ...] = (
    "providers",
    "keys",
    "deployments",
    "aliases",
    "settings",
    "prices",
    "users",
    "vkeys",
    "logs",
    "rollups",
    "audit",
    "responses",
)

#: Sections that grow without bound or carry short-lived operational state.
#: Excluded unless asked for: the default backup stays a file you can download
#: and a browser can parse.
BULK_SECTIONS: frozenset[str] = frozenset({"logs", "rollups", "audit",
                                           "responses"})
DEFAULT_SECTIONS: tuple[str, ...] = tuple(
    s for s in SECTIONS if s not in BULK_SECTIONS)

#: Rows per page when streaming a bulk section out. Keeps peak memory at one
#: page rather than the whole table.
_PAGE = 2000

#: ``request_logs`` columns holding prompt/completion text. Never exported
#: without ``include_bodies`` — a separate opt-in from ``include_secrets``, so
#: pulling provider credentials can never also drag user content.
BODY_COLUMNS = ("request_body", "response_body")

#: Settings keys whose value is credential-bearing. ``cline_oauth:*`` records
#: carry a refresh token; they are gated exactly like provider key secrets.
#: A prefix rule rather than an enumeration, so a new OAuth provider is covered
#: the day it is added instead of the day someone remembers.
_SECRET_SETTING_PREFIX = "cline_oauth:"

log = structlog.get_logger("wiwi.backup")


class BackupError(Exception):
    """A backup document was rejected, or a section could not be applied.

    Carries the HTTP status and error type so the thin route handler can render
    it through ``_err`` without this module knowing anything about FastAPI
    error bodies — ``_err`` is an inner function of ``create_app`` and is not
    reachable from here.
    """

    def __init__(self, message: str, *, status: int = 400,
                 etype: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.etype = etype


def parse_sections(raw: str | None, *, default: tuple[str, ...]
                   = DEFAULT_SECTIONS) -> tuple[str, ...]:
    """Turn a ``?sections=a,b`` value into a validated section tuple."""
    if raw is None or not raw.strip():
        return default
    wanted = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in SECTIONS]
    if unknown:
        raise BackupError(
            f"unknown backup section(s): {', '.join(sorted(unknown))}; "
            f"valid sections are {', '.join(SECTIONS)}")
    # Preserve SECTIONS order regardless of query order, so a restore always
    # applies parents before children.
    return tuple(s for s in SECTIONS if s in set(wanted))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _engine(state):
    """The one AsyncEngine every store shares (``AppState.init_db``)."""
    store = state.config_store
    if store is not None:
        return store.engine
    if state.auth is not None:
        return state.auth.engine
    if state.users is not None:
        return state.users.engine
    raise BackupError("no database engine is initialised", status=503,
                      etype="api_error")


@contextlib.asynccontextmanager
async def _write_scope(engine, conn=None):
    """A write scope on *engine*: the caller's *conn* when one is open, else a
    fresh transaction.

    Every statement a section writes must go through one of these. A nested
    ``engine.begin()`` would check out a *second* connection — which on a pooled
    engine deadlocks against the outer transaction's row locks, and on a
    single-connection pool (SQLAlchemy's ``StaticPool``, which is what an
    in-memory SQLite database gets) hands out the same connection twice and lets
    the inner commit publish the outer work.
    """
    if conn is not None:
        yield conn
    else:
        async with engine.begin() as owned:
            yield owned


async def _conn_columns(conn, table: str) -> set[str]:
    if conn.dialect.name == "postgresql":
        rows = (await conn.execute(sa.text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            " AND table_name = :t"), {"t": table})).all()
        return {r[0] for r in rows}
    rows = (await conn.execute(sa.text(
        f"PRAGMA table_info({table})"))).all()
    return {r[1] for r in rows}


async def _table_columns(engine, table: str, *, conn=None) -> set[str]:
    """Column names of *table*, per dialect. Empty when it does not exist.

    Mirrors ``ConfigStore._table_columns`` — same two dialects, same ``(cid,
    name)`` vs ``(column_name)`` indexing difference. Pass *conn* to read
    through an already-open connection: while a write transaction is open that
    is the only connection that is safe to use.
    """
    if conn is not None:
        return await _conn_columns(conn, table)
    async with engine.connect() as owned:
        return await _conn_columns(owned, table)


def truthy(raw: str | None) -> bool:
    """Parse a query-parameter boolean. Absent or unrecognized is False."""
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BackupError(f"{field} must be a number")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BackupError(f"{field} must be an integer") from None


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BackupError(f"{field} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise BackupError(f"{field} must be a number") from None


def _as_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise BackupError(f"{field} must be a boolean")
    return value


def _as_str(value: Any, field: str, *, required: bool = True) -> str:
    if value is None:
        if required:
            raise BackupError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise BackupError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise BackupError(f"{field} is required")
    return value


def _decode(value: Any, default: Any) -> Any:
    """Parse a JSON column, falling back rather than failing the export."""
    if not isinstance(value, str):
        return value if value is not None else default
    try:
        parsed = orjson.loads(value)
    except orjson.JSONDecodeError:
        return default
    return default if parsed is None else parsed


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


async def build_backup(state, *, include_secrets: bool = False,
                       include_bodies: bool = False,
                       sections: tuple[str, ...] = DEFAULT_SECTIONS,
                       log_days: int = 30,
                       log_limit: int = 0) -> dict:
    """Build the backup document.

    *include_secrets* adds each provider key's plaintext ``secret`` and the
    ``cline_oauth:*`` refresh tokens — the only live credentials in the format.
    *include_bodies* adds ``request_body``/``response_body`` to log rows. Both
    default off. *log_days* bounds the log window (0 = all time) and
    *log_limit* caps raw log rows (0 = no cap); a cap that bites is recorded in
    ``truncated`` — ``{"request_logs": <rows dropped>}``, per table, matching
    the shape ``BackupDocument.truncated`` declares — so a restore knows the
    section is partial instead of trusting a short file to be complete.
    """
    doc: dict[str, Any] = {
        "kind": "wiwi.backup",
        "version": BACKUP_VERSION,
        "exported_at": time.time(),
        "sections": list(sections),
        "include_secrets": include_secrets,
        "include_bodies": include_bodies,
    }
    builders = {
        "providers": lambda: _export_providers(state, include_secrets),
        "keys": lambda: _export_keys(state, include_secrets),
        "deployments": lambda: _export_deployments(state),
        "aliases": lambda: _export_aliases(state),
        "settings": lambda: _export_settings(state, include_secrets),
        "prices": lambda: _export_prices(state),
        "users": lambda: _export_users(state),
        "vkeys": lambda: _export_vkeys(state),
        "logs": lambda: _export_logs(state, include_bodies=include_bodies,
                                     log_days=log_days, log_limit=log_limit),
        "rollups": lambda: _export_rollups(state, log_days=log_days),
        "audit": lambda: _export_audit(state),
        "responses": lambda: _export_responses(state),
    }
    truncated: dict[str, int] = {}
    for section in sections:
        doc[section] = await builders[section]()
        if section == "logs":
            # Keyed by table name, not by section: ``counts`` uses the same
            # inner keys, and ``BackupDocument.truncated`` is a per-table map.
            dropped = doc["logs"].pop("_truncated", 0)
            if dropped:
                truncated["request_logs"] = dropped
    if truncated:
        doc["truncated"] = truncated
    doc["counts"] = _counts(doc)
    return doc


def _counts(doc: dict) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for section in SECTIONS:
        if section not in doc:
            continue
        value = doc[section]
        # Every section is either a plain list of rows, or a wrapper dict of
        # ``table_name -> [rows]`` (``logs`` carries a ``_truncated`` marker
        # beside its row list). Counting the wrapper's *keys* reported ``1``
        # for any wrapper section, so the export's counts disagreed with the
        # dry-run preview, which counts rows per table via ``_plan_counts``.
        if isinstance(value, dict):
            counts[section] = {k: len(v) for k, v in value.items()
                               if isinstance(v, list)}
        else:
            counts[section] = len(value)
    return counts


async def _export_providers(state, include_secrets: bool) -> list[dict]:
    """Provider accounts, from live router state so YAML entries are included.

    Secrets live in the ``keys`` section, not here, so a provider export never
    carries credentials and ``include_secrets`` does not change its shape.
    """
    return [{
        "name": acct.name,
        "provider_type": acct.provider_type,
        "base_url": acct.base_url,
        "timeout_s": acct.timeout_s,
        "extra_headers": dict(acct.extra_headers),
        "round_robin": acct.round_robin,
        "alias_id": acct.alias_id,
    } for acct in (state.router.providers[name]
                   for name in sorted(state.router.providers))]


async def _export_keys(state, include_secrets: bool) -> list[dict]:
    """Key-pool entries, flattened across accounts.

    ``provider_keys.secret`` is stored in plaintext, so this is the section
    that makes a backup actually restorable. Without ``include_secrets`` the
    ``secret`` key is *absent* rather than null — a backup that carries no
    credentials should not carry the shape of one that does, and a merge can
    tell "unknown" from "intentionally empty".
    """
    out = []
    for name in sorted(state.router.providers):
        for k in state.router.providers[name].keys:
            out.append({
                "provider_name": name,
                "label": k.label,
                **({"secret": k.secret} if include_secrets else {}),
                "weight": k.weight,
                "enabled": k.enabled,
            })
    return out


async def _export_deployments(state) -> list[dict]:
    """Model-group deployments (the provider/model combos).

    Only what the ``deployments`` table can actually hold back: ``group_name``,
    ``provider_name``, ``model_id``, ``weight``. The five per-deployment limits
    — ``rpm``, ``tpm``, ``timeout``, ``max_tokens``, ``prompt_cache`` — are
    deliberately **not** exported. They are real on the live ``Deployment``,
    but the table has no column for any of them, so a restore could only ever
    set them in memory and the next restart would rebuild them at their
    defaults. Shipping them advertised a persistence the format cannot deliver:
    a restored gateway looked throttled until it was rebooted. They are
    YAML/``wiwi_params``-owned — ``model_list`` on the source is the durable
    home for them, and it moves with the instance's config.
    """
    out = []
    for gname in sorted(state.router.groups):
        for d in state.router.groups[gname]:
            out.append({
                "group_name": gname,
                "provider_name": d.provider.name,
                "model_id": d.model_id,
                "weight": d.weight,
            })
    return out


async def _export_aliases(state) -> dict[str, str | None]:
    """Model-group aliases as the admin **overlay**, not the merged map.

    The live map is the YAML base plus a DB overlay; only the overlay is
    admin-owned. Serializing the merged map would bake every YAML-defined alias
    into the backup as though an admin had created it, and importing that onto
    a differently-configured instance would pin aliases the operator never set.
    ``None`` is a tombstone — an admin removing a YAML alias, which must
    survive the round trip or the alias comes back on the next restart.
    """
    base = getattr(state, "alias_yaml_base", {}) or {}
    live = state.router.settings.model_group_alias or {}
    overlay: dict[str, Any] = {}
    for k, v in base.items():
        if k not in live:
            overlay[k] = None
        elif not alias_value_eq(live[k], v):
            overlay[k] = alias_value_to_json(live[k])
    for k, v in live.items():
        if k not in base:
            overlay[k] = alias_value_to_json(v)
    return overlay


async def _export_settings(state, include_secrets: bool) -> dict[str, Any]:
    """The ``settings`` key/value table, swept generically.

    The table is a dynamic namespace, not a fixed schema: ``alert_rules``,
    ``routing_strategy``, ``model_group_alias``,
    ``cline_settings:default_models`` and a ``cline_oauth:{name}`` row per
    connected account all live in it. A hand-enumerated list goes stale the
    moment a key is added, so read them all.
    """
    engine = _engine(state)
    out: dict[str, Any] = {}
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT key, value FROM settings ORDER BY key"))).all()
    for key, raw in rows:
        if key == "model_group_alias":
            # The overlay is recomputed from live router state, not read from
            # the row — see _export_aliases. It has its own section.
            continue
        value = _decode(raw, None)
        if key.startswith(_SECRET_SETTING_PREFIX) and not include_secrets:
            out[key] = _redact_oauth(value)
        else:
            out[key] = value
    return out


def _redact_oauth(value: Any) -> dict:
    """Strip the refresh token from a ``cline_oauth:*`` record.

    Import skips a redacted record rather than writing the placeholder over a
    live token, so an operator restoring a secret-less backup does not
    disconnect their own Cline accounts.
    """
    if not isinstance(value, dict):
        return {"connected": bool(value)}
    redacted = {k: v for k, v in value.items()
                if k not in ("refresh_token", "access_token", "token")}
    redacted["connected"] = True
    redacted["redacted"] = True
    return redacted


async def _export_prices(state) -> list[dict]:
    """Custom model prices plus their per-scope overrides.

    ``model_price_scopes.scope`` shares one namespace with provider account
    names (``config_store``'s own note), so a restore must apply providers
    before prices.
    """
    store = state.config_store
    if store is None:
        return []
    scopes_by_model: dict[str, dict[str, dict]] = {}
    for s in await store.load_price_scopes():
        scopes_by_model.setdefault(s["model_id"], {})[s["scope"]] = {
            k: v for k, v in s.items() if k not in ("model_id", "scope")}
    out = []
    for p in await store.load_prices():
        entry = dict(p)
        if entry["model_id"] in scopes_by_model:
            entry["providers"] = scopes_by_model[entry["model_id"]]
        out.append(entry)
    return out


async def _export_users(state) -> list[dict]:
    """Console accounts, password hash included.

    ``UserService.list_users`` projects the hash away on purpose — it feeds an
    admin listing with no business holding the column. A restore does: without
    it an imported user could not log in. The digest is a salted PBKDF2 hash
    (``pbkdf2_sha256$iters$salt$hash``), so it is not reversible and carries
    no usable credential on its own.
    """
    engine = _engine(state)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT id, username, password_hash, role, disabled,"
            " created_at, updated_at FROM users ORDER BY username"))).all()
    return [{"id": r[0], "username": r[1], "password_hash": r[2],
             "role": r[3], "disabled": bool(r[4]),
             "created_at": r[5], "updated_at": r[6]} for r in rows]


async def _export_vkeys(state) -> list[dict]:
    """Virtual keys with budgets, limits, and — critically — the key hash.

    ``AuthService.list_keys`` omits ``key_hash`` because it feeds a listing
    that only needs masked metadata. Restoring the hash is what makes an
    imported key authenticate. Budget state (``max_budget``,
    ``spend_to_date``, ``budget_reserved``) rides along: a migrated gateway
    should not hand its tenants a fresh allowance.
    """
    engine = _engine(state)
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT id, key_hash, key_alias, models, max_budget, spend_to_date,"
            " budget_reserved, rpm, tpm, expires_at, disabled, owner_id,"
            " priority, created_at, updated_at FROM vkeys"
            " ORDER BY created_at"))).all()
    return [{"id": r[0], "key_hash": r[1], "key_alias": r[2],
             "models": _decode(r[3], []), "max_budget": r[4],
             "spend_to_date": r[5], "budget_reserved": r[6], "rpm": r[7],
             "tpm": r[8], "expires_at": r[9], "disabled": bool(r[10]),
             "owner_id": r[11], "priority": r[12], "created_at": r[13],
             "updated_at": r[14]} for r in rows]


async def _export_logs(state, *, include_bodies: bool, log_days: int,
                       log_limit: int) -> dict:
    """Raw per-request rows.

    Read with a dedicated keyset query rather than through ``DBSink``. Both
    public readers are unusable here: ``read_requests`` and ``read_audit``
    build ``... LIMIT :l`` with no ``limit=0`` special case, so asking for
    everything returns nothing; both project away ``id``, which a row-keyed
    merge needs; and both reshape rows into ``public_dict(LogEvent)``, which
    drops columns a restore should carry.

    Paging is ``id > cursor`` rather than OFFSET, which re-walks every row
    already read — quadratic over a large log.
    """
    engine = _engine(state)
    present = await _table_columns(engine, "request_logs")
    cols = [c for c in _REQUEST_LOG_COLUMNS if c in present]
    if not include_bodies:
        cols = [c for c in cols if c not in BODY_COLUMNS]
    rows, truncated = await _read_all(
        engine, "request_logs", cols, ts_cutoff=_cutoff(log_days),
        limit=log_limit)
    return {"request_logs": rows, "_truncated": truncated}


async def _export_rollups(state, *, log_days: int) -> dict:
    """Hourly per-model aggregates.

    These outlive the raw rows: ``rollup_and_prune`` prunes ``request_logs``
    under the retention window, so on a long-running instance the rollups are
    the only surviving record of usage.
    """
    engine = _engine(state)
    present = await _table_columns(engine, "request_rollups")
    cols = [c for c in _ROLLUP_COLUMNS if c in present]
    rows, _ = await _read_all(engine, "request_rollups", cols,
                              ts_cutoff=_cutoff(log_days),
                              ts_column="bucket_ts")
    return {"request_rollups": rows}


async def _export_audit(state) -> dict:
    """The admin mutation trail. Export-only — see ``validate_backup``."""
    engine = _engine(state)
    rows, _ = await _read_all(engine, "audit_logs",
                              ["id", "ts", "actor", "action", "target",
                               "diff"])
    for r in rows:
        r["diff"] = _decode(r.get("diff"), {})
    return {"audit_logs": rows}


async def _export_responses(state) -> dict:
    """Cached Responses-API results.

    Off by default: the store has a short TTL and is a cache, not
    configuration. Restoring it mints response ids the target's clients never
    received, so it is only useful when moving a gateway wholesale.
    """
    store = state.response_store
    if store is None:
        return {"stored_responses": []}
    engine = store.engine
    rows, _ = await _read_all(
        engine, "stored_responses",
        ["id", "created_at", "key_id", "surface", "model_group",
         "input_json", "output_json"], ts_cutoff=None)
    return {"stored_responses": rows}


def _cutoff(log_days: int) -> float | None:
    return (time.time() - log_days * 86400.0) if log_days > 0 else None


#: ``request_logs`` columns the export carries. This must stay a superset-free
#: mirror of ``db_sink._COLS`` minus ``BODY_COLUMNS``: the export filters this
#: tuple against the live schema, so a column missing here is dropped from
#: every backup with no error and no warning. ``error_message`` is included
#: deliberately — it is an already-scrubbed diagnostic, not user content, so it
#: must not sit behind ``include_bodies``. ``tests/test_fix_round141.py`` guards
#: the two lists against drifting apart again.
_REQUEST_LOG_COLUMNS = (
    "ts", "request_id", "surface", "key_alias", "key_id", "model_group",
    "provider", "provider_key_label", "status", "error_code",
    "error_message", "tok_in",
    "tok_cached", "tok_cache_creation", "tok_reasoning", "tok_out",
    "usage_estimated", "tps", "ttft_ms", "latency_ms", "cost", "was_stream",
    "cache_hit", "cache_savings", "response_cache_hit", "attempts",
    "translation_warnings",
)

_ROLLUP_COLUMNS = (
    "bucket_ts", "key_id", "model_group", "provider", "serving_model",
    "requests", "errors", "estimated_requests", "cache_hits", "tok_in",
    "tok_cached", "tok_cache_creation", "tok_reasoning", "tok_out", "cost",
    "cache_savings", "tps_sum", "tps_count", "tps_p95", "ttft_p95_ms",
    "latency_p95_ms", "p95_hist", "unpriced_requests", "unpriced_tok_in",
    "unpriced_tok_cached", "unpriced_tok_cache_creation", "unpriced_tok_out",
)


async def _read_all(engine, table: str, cols: list[str], *,
                    ts_cutoff: float | None = None,
                    ts_column: str = "ts",
                    limit: int = 0) -> tuple[list[dict], int]:
    """Every row of *table*, one bounded page at a time.

    Returns ``(rows, truncated)``; *truncated* is the number of rows dropped by
    *limit*, so the caller can record that the section is partial rather than
    silently short.

    The keyset column is projected **internally** and stripped again before the
    row is handed back. Paging needs ``id``; requiring the caller to list it
    made the export's correctness depend on every column tuple remembering to,
    and neither ``_REQUEST_LOG_COLUMNS`` nor ``_ROLLUP_COLUMNS`` did — so the
    cursor was always ``None`` and every bulk export shipped exactly one page,
    the *oldest* 2000 rows, with no error and no ``truncated`` marker. A caller
    that asks for ``id`` on purpose (``audit``, ``responses``, where it is part
    of the record) keeps it.

    A table with no ``id`` cannot be keyset at all, so it falls back to OFFSET
    paging rather than silently returning its first page.
    """
    if not cols:
        return [], 0
    present = await _table_columns(engine, table)
    usable = [c for c in cols if c in present]
    if not usable:
        return [], 0
    has_id = "id" in present
    strip_id = has_id and "id" not in usable
    select_cols = (["id"] + usable) if strip_id else list(usable)
    col_sql = ", ".join(select_cols)
    out: list[dict] = []
    cursor: Any = None
    offset = 0
    truncated = 0
    first = True
    while True:
        where = [] if first else (["id > :cursor"] if has_id else [])
        params: dict[str, Any] = {}
        if first and ts_cutoff is not None and ts_column in present:
            where.append(f"{ts_column} >= :cutoff")
            params["cutoff"] = ts_cutoff
        if not first and has_id:
            params["cursor"] = cursor
        page = _PAGE
        if limit and len(out) + page > limit:
            page = limit - len(out)
        params["limit"] = page
        sql = (f"SELECT {col_sql} FROM {table}"
               + (f" WHERE {' AND '.join(where)}" if where else "")
               + " ORDER BY id LIMIT :limit")
        if not has_id:
            sql += " OFFSET :offset"
            params["offset"] = offset
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(sql), params)).all()
        if not rows:
            return out, truncated
        for r in rows:
            row = dict(zip(select_cols, r))
            if strip_id:
                row.pop("id", None)
            out.append(_coerce_row(row, select_cols))
        offset += len(rows)
        if limit and len(out) >= limit:
            # Only the capped path pays for a COUNT, so an unbounded export
            # costs exactly what it did before.
            total = await _count_where(engine, table, ts_cutoff=ts_cutoff,
                                       ts_column=ts_column, present=present)
            return out, max(0, total - len(out))
        if len(rows) < page:
            return out, truncated
        if has_id:
            cursor = rows[-1][select_cols.index("id")]
        first = False


async def _count_where(engine, table: str, *, ts_cutoff: float | None,
                       ts_column: str, present: set[str]) -> int:
    """Rows *table* would export under the same age bound — the denominator
    behind ``truncated``."""
    where = []
    params: dict[str, Any] = {}
    if ts_cutoff is not None and ts_column in present:
        where.append(f"{ts_column} >= :cutoff")
        params["cutoff"] = ts_cutoff
    sql = (f"SELECT COUNT(*) FROM {table}"
           + (f" WHERE {' AND '.join(where)}" if where else ""))
    async with engine.connect() as conn:
        return (await conn.execute(sa.text(sql), params)).scalar() or 0


def _coerce_row(row: dict, cols: list[str]) -> dict:
    """Make a raw DB row JSON-safe.

    ``attempts``/``translation_warnings``/``diff``/``p95_hist`` hold JSON
    strings and the INTEGER columns are really booleans; decoding them here
    means the restore receives lists and objects rather than re-parsing strings
    it may not have written.
    """
    for k in ("attempts", "translation_warnings"):
        if k in row:
            row[k] = _decode(row[k], [])
    if "diff" in row:
        row["diff"] = _decode(row["diff"], {})
    if "p95_hist" in row:
        row["p95_hist"] = _decode(row["p95_hist"], {})
    for k in ("disabled", "usage_estimated", "was_stream", "cache_hit",
              "response_cache_hit"):
        if k in row and row[k] is not None:
            row[k] = bool(row[k])
    return row

# ---------------------------------------------------------------------------
# validation — reads state, writes nothing, returns a fully-parsed plan
# ---------------------------------------------------------------------------

#: Top-level document keys a restore understands. Anything else is rejected
#: rather than ignored: a front-end/backend contract drift then surfaces as a
#: loud 400 instead of silently dropping a section on the floor.
_KNOWN_DOC_KEYS = frozenset({
    "kind", "version", "exported_at", "sections", "include_secrets",
    "include_bodies", "truncated", "counts", "meta", *SECTIONS,
})


async def validate_backup(state, doc: Any, *,
                          sections: tuple[str, ...] = DEFAULT_SECTIONS
                          ) -> dict[str, Any]:
    """Parse and check a backup document; return a plan ready to apply.

    Reads live state only to resolve cross-references (does the named provider
    exist, is this alias already claimed). Writes nothing, so a rejected
    document leaves the gateway exactly as it was — and ``?dry_run=1`` gets
    the same validation for free.
    """
    if not isinstance(doc, dict):
        raise BackupError("backup document must be a JSON object")
    kind = doc.get("kind")
    if kind is not None and kind != "wiwi.backup":
        raise BackupError(f"not a wiwi backup document (kind={kind!r})")
    version = doc.get("version")
    if version != BACKUP_VERSION:
        raise BackupError(
            f"unsupported backup version {version!r}; this build reads "
            f"version {BACKUP_VERSION}")
    unknown = sorted(set(doc) - _KNOWN_DOC_KEYS)
    if unknown:
        raise BackupError(
            f"unrecognized backup field(s): {', '.join(unknown)}")

    plan: dict[str, Any] = {"_validated": True, "sections": sections,
                            "counts": {}}
    _validate_providers(state, doc, plan)
    _validate_keys(state, doc, plan)
    _validate_deployments(state, doc, plan)
    _validate_aliases(doc, plan)
    _validate_settings(doc, plan)
    _validate_prices(state, doc, plan)
    _validate_users(doc, plan)
    _validate_vkeys(state, doc, plan)
    _validate_logs(doc, plan)
    _validate_rollups(doc, plan)
    _validate_audit(doc, plan)
    _validate_responses(doc, plan)
    plan["counts"] = _plan_counts(plan)
    return plan


def _plan_counts(plan: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section in SECTIONS:
        if section not in plan:
            continue
        value = plan[section]
        out[section] = ({k: len(v) for k, v in value.items()}
                        if isinstance(value, dict) and section in
                        ("logs", "rollups", "audit", "responses")
                        else len(value))
    return out


def _section_list(doc: dict, key: str, *, required: bool = False
                  ) -> list:
    value = doc.get(key)
    if value is None:
        if required:
            raise BackupError(f"'{key}' must be a non-empty list")
        return []
    if not isinstance(value, list):
        raise BackupError(f"'{key}' must be a list")
    if required and not value:
        raise BackupError(f"'{key}' must be a non-empty list")
    return value


def _future_providers(state, imported: set[str]) -> set[str]:
    """Account names after the import: what exists now, plus what arrives."""
    return set(state.router.providers) | imported


def _validate_providers(state, doc: dict, plan: dict) -> None:
    raw = _section_list(doc, "providers")
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BackupError(f"providers[{i}] is not a JSON object")
        name = _as_str(entry.get("name"), f"providers[{i}].name")
        if name in seen:
            raise BackupError(f"duplicate provider {name!r} in backup")
        seen.add(name)
        ptype = _as_str(entry.get("provider_type"),
                        f"providers[{i}].provider_type")
        if ptype not in PROVIDER_TYPES:
            raise BackupError(
                f"providers[{i}]: unsupported provider type {ptype!r}")
        raw_url = _interpolate(entry.get("base_url"))
        if raw_url is not None and not isinstance(raw_url, str):
            raise BackupError(f"providers[{i}].base_url must be a string")
        base_url = ((raw_url or "").strip() or _default_base_url(ptype))
        if not base_url:
            raise BackupError(
                f"providers[{i}]: base_url is required for type {ptype!r}")
        timeout_s = entry.get("timeout_s")
        if timeout_s is not None:
            timeout_s = _as_float(timeout_s, f"providers[{i}].timeout_s")
            if timeout_s <= 0:
                raise BackupError(f"providers[{i}].timeout_s must be positive")
        headers = entry.get("extra_headers")
        if headers is not None:
            if not isinstance(headers, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in headers.items()):
                raise BackupError(
                    f"providers[{i}].extra_headers must map strings to strings")
            headers = dict(headers)
        rr = entry.get("round_robin")
        if rr is not None and not isinstance(rr, bool):
            raise BackupError(f"providers[{i}].round_robin must be a boolean")
        alias = entry.get("alias_id")
        if alias is not None:
            alias = _as_str(alias, f"providers[{i}].alias_id")
            if any(ch.isspace() for ch in alias):
                raise BackupError(
                    f"providers[{i}].alias_id must not contain whitespace")
        out.append({"name": name, "provider_type": ptype,
                    "base_url": base_url, "timeout_s": timeout_s,
                    "extra_headers": headers, "round_robin": rr,
                    "alias_id": alias, "alias_id_set": "alias_id" in entry})

    # Alias claims are checked against the registry as it will look *after*
    # the import: mappings owned by imported providers are released first, so
    # an alias can legitimately move between two accounts in the same file.
    future = {a: n for a, n in state.router.alias_to_provider.items()
              if n not in seen}
    claimed: dict[str, str] = {}
    for p in out:
        alias = p["alias_id"]
        if not p["alias_id_set"] or alias is None:
            continue
        if alias in claimed:
            raise BackupError(
                f"alias_id {alias!r} is claimed by both {claimed[alias]!r} "
                f"and {p['name']!r}")
        claimed[alias] = p["name"]
        owner = future.get(alias)
        if owner is not None:
            raise BackupError(
                f"alias_id {alias!r} already used by provider {owner!r}",
                status=409)
    plan["providers"] = out


def _validate_keys(state, doc: dict, plan: dict) -> None:
    raw = _section_list(doc, "keys")
    known = _future_providers(state, {p["name"] for p in plan["providers"]})
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BackupError(f"keys[{i}] is not a JSON object")
        provider = _as_str(entry.get("provider_name"),
                           f"keys[{i}].provider_name")
        if provider not in known:
            raise BackupError(
                f"keys[{i}]: no provider {provider!r} in this backup or "
                "this gateway")
        label = _as_str(entry.get("label"), f"keys[{i}].label")
        if (provider, label) in seen:
            raise BackupError(
                f"duplicate key {label!r} on provider {provider!r}")
        seen.add((provider, label))
        secret = entry.get("secret")
        if secret is not None:
            secret = _as_str(secret, f"keys[{i}].secret")
        weight = entry.get("weight", 1)
        if weight is None:
            weight = 1
        weight = max(1, _as_int(weight, f"keys[{i}].weight"))
        enabled = entry.get("enabled", True)
        if enabled is None:
            enabled = True
        if not isinstance(enabled, bool):
            raise BackupError(f"keys[{i}].enabled must be a boolean")
        out.append({"provider_name": provider, "label": label,
                    "secret": secret, "weight": weight, "enabled": enabled})
    plan["keys"] = out


#: Deployment limits that exist on the live ``Deployment`` but have no column in
#: the ``deployments`` table, and are therefore not exported (see
#: :func:`_export_deployments`). A document may still carry them — an older
#: export, or a hand-written one — and they are applied to the in-memory
#: deployment so the file is honoured for the life of the process. Absent means
#: *unknown*: the target's own value is left alone rather than reset to the
#: default.
_DEPLOYMENT_LIMITS = ("rpm", "tpm", "timeout", "max_tokens", "prompt_cache")


def _validate_deployments(state, doc: dict, plan: dict) -> None:
    """Parse the deployments section.

    See :data:`_DEPLOYMENT_LIMITS`: only the fields the document actually
    carries end up in the plan, so a merge cannot clobber a limit the target
    set locally with a ``None`` the file never mentioned.
    """
    raw = _section_list(doc, "deployments")
    known = _future_providers(state, {p["name"] for p in plan["providers"]})
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BackupError(f"deployments[{i}] is not a JSON object")
        group = _as_str(entry.get("group_name"), f"deployments[{i}].group_name")
        provider = _as_str(entry.get("provider_name"),
                           f"deployments[{i}].provider_name")
        if provider not in known:
            raise BackupError(
                f"deployments[{i}]: no provider {provider!r} in this backup "
                "or this gateway")
        model_id = _as_str(entry.get("model_id"), f"deployments[{i}].model_id")
        if any(ch.isspace() for ch in model_id):
            raise BackupError(
                f"deployments[{i}].model_id must not contain whitespace")
        if (group, provider, model_id) in seen:
            raise BackupError(
                f"duplicate deployment {group}/{model_id} on "
                f"{provider!r}")
        seen.add((group, provider, model_id))
        parsed: dict[str, Any] = {
            "group_name": group, "provider_name": provider,
            "model_id": model_id,
            "weight": max(1, _as_int(entry.get("weight", 1) or 1,
                                     f"deployments[{i}].weight")),
        }
        for field in ("rpm", "tpm", "max_tokens"):
            if field not in entry:
                continue
            value = entry[field]
            parsed[field] = (None if value is None
                             else _as_int(value, f"deployments[{i}].{field}"))
        if "timeout" in entry:
            timeout = entry["timeout"]
            parsed["timeout"] = (None if timeout is None
                                 else _as_float(timeout,
                                                f"deployments[{i}].timeout"))
        if "prompt_cache" in entry:
            cache = entry["prompt_cache"]
            parsed["prompt_cache"] = _as_bool(
                False if cache is None else cache,
                f"deployments[{i}].prompt_cache")
        detached = entry.get("detached", False)
        if detached is None:
            detached = False
        parsed["detached"] = _as_bool(detached,
                                      f"deployments[{i}].detached")
        out.append(parsed)
    plan["deployments"] = out


def _validate_aliases(doc: dict, plan: dict) -> None:
    raw = doc.get("aliases")
    if raw is None:
        plan["aliases"] = {}
        return
    if not isinstance(raw, dict):
        raise BackupError("'aliases' must be a JSON object")
    out: dict[str, Any] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise BackupError("alias names must be non-empty strings")
        if value is None:
            out[name] = None          # tombstone: admin removed a YAML alias
            continue
        coerced = coerce_alias_value(value)
        if coerced is None:
            raise BackupError(f"aliases[{name!r}] is not a valid alias entry")
        out[name] = alias_value_to_json(coerced)
    plan["aliases"] = out


def _validate_settings(doc: dict, plan: dict) -> None:
    raw = doc.get("settings")
    if raw is None:
        plan["settings"] = {}
        plan["cline_default_models"] = None
        return
    if not isinstance(raw, dict):
        raise BackupError("'settings' must be a JSON object")
    strategies = ("simple-shuffle", "least-busy", "latency-based")
    out: dict[str, Any] = {}
    cline_models: list[str] | None = None
    for key, value in raw.items():
        if not isinstance(key, str):
            raise BackupError("settings keys must be strings")
        if key == "routing_strategy":
            if value not in strategies:
                raise BackupError(
                    f"settings.routing_strategy must be one of "
                    f"{', '.join(strategies)}")
        elif key == "alert_rules":
            if not isinstance(value, list):
                raise BackupError("settings.alert_rules must be a list")
        elif key == "cline_settings:default_models":
            if not isinstance(value, list) or not all(
                    isinstance(v, str) for v in value):
                raise BackupError(
                    "settings.cline_settings:default_models must be a list "
                    "of strings")
            cline_models = list(value)
        out[key] = value
    plan["settings"] = out
    plan["cline_default_models"] = cline_models


_PRICE_FIELDS = ("input_cost_per_token", "output_cost_per_token",
                 "cache_read_input_cost_per_token",
                 "cache_creation_input_cost_per_token", "max_input_tokens",
                 "max_output_tokens", "mode")


def _validate_prices(state, doc: dict, plan: dict) -> None:
    raw = _section_list(doc, "prices")
    # A scope is either an account name or a provider type, and an account name
    # may be one this very document introduces — ``providers`` is applied
    # before ``prices``, so the future set is the live one plus the imported
    # names. Checking only the live router would reject a backup restoring a
    # model whose scoped price belongs to a provider it also carries.
    valid_scopes = (_future_providers(state, {p["name"] for p in plan["providers"]})
                    | set(PROVIDER_TYPES))
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BackupError(f"prices[{i}] is not a JSON object")
        model_id = _as_str(entry.get("model_id"), f"prices[{i}].model_id")
        if model_id in seen:
            raise BackupError(f"duplicate price entry for {model_id!r}")
        seen.add(model_id)
        parsed: dict[str, Any] = {"model_id": model_id}
        for field in _PRICE_FIELDS:
            value = entry.get(field)
            if value is None:
                # OMIT, do not store None. The price map is consumed by
                # ``_entry_to_per_1m``, which branches on key *presence*
                # ("cache_read_input_cost_per_token" in p) and then
                # multiplies — a present-but-None value turns every later
                # ``GET /admin/pricing`` into a TypeError 500. Both the admin
                # PUT path and ``ConfigStore.load_prices`` omit absent fields
                # for the same reason; a restore must match that convention.
                continue
            if field in ("max_input_tokens", "max_output_tokens"):
                parsed[field] = _as_int(value, f"prices[{i}].{field}")
                continue
            parsed[field] = _as_float(value, f"prices[{i}].{field}")
        scopes = entry.get("providers")
        if scopes is not None:
            if not isinstance(scopes, dict):
                raise BackupError(
                    f"prices[{i}].providers must map a scope to its rates")
            clean: dict[str, dict] = {}
            for scope, rates in scopes.items():
                if scope not in valid_scopes:
                    raise BackupError(
                        f"prices[{i}]: unknown price scope {scope!r}")
                if not isinstance(rates, dict):
                    raise BackupError(
                        f"prices[{i}].providers[{scope!r}] must be an object")
                clean[scope] = {
                    f: _as_float(rates[f],
                                 f"prices[{i}].providers[{scope!r}].{f}")
                    for f in ("input_cost_per_token", "output_cost_per_token",
                              "cache_read_input_cost_per_token",
                              "cache_creation_input_cost_per_token")
                    if rates.get(f) is not None}
            parsed["providers"] = clean
        out.append(parsed)
    plan["prices"] = out


def _validate_users(doc: dict, plan: dict) -> None:
    raw = _section_list(doc, "users")
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BackupError(f"users[{i}] is not a JSON object")
        username = _as_str(entry.get("username"), f"users[{i}].username")
        if username in seen:
            raise BackupError(f"duplicate user {username!r} in backup")
        seen.add(username)
        role = entry.get("role", "user")
        if role not in ("user", "admin"):
            raise BackupError(f"users[{i}].role must be 'user' or 'admin'")
        disabled = entry.get("disabled", False)
        if disabled is None:
            disabled = False
        if not isinstance(disabled, bool):
            raise BackupError(f"users[{i}].disabled must be a boolean")
        # password_hash is opaque: carried through verbatim, never parsed or
        # re-derived. An absent one leaves the target's password alone rather
        # than blanking it.
        pw = entry.get("password_hash")
        if pw is not None and not isinstance(pw, str):
            raise BackupError(f"users[{i}].password_hash must be a string")
        out.append({
            "id": _as_str(entry.get("id"), f"users[{i}].id", required=False),
            "username": username, "password_hash": pw or None, "role": role,
            "disabled": disabled,
        })
    plan["users"] = out


def _validate_vkeys(state, doc: dict, plan: dict) -> None:
    raw = _section_list(doc, "vkeys")
    master_hash = getattr(state.auth, "master_hash", None)
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BackupError(f"vkeys[{i}] is not a JSON object")
        key_hash = _as_str(entry.get("key_hash"), f"vkeys[{i}].key_hash")
        if len(key_hash) != 64 or any(
                c not in "0123456789abcdef" for c in key_hash.lower()):
            raise BackupError(
                f"vkeys[{i}].key_hash must be a 64-character hex digest")
        if master_hash is not None and key_hash == master_hash:
            # The master credential lives in config, not the vkeys table. A row
            # with its hash would be a second master-equivalent secret smuggled
            # in through the restore path.
            raise BackupError(
                "vkeys contains the master key hash; the master credential is "
                "configuration, not restorable data", status=409)
        if key_hash in seen:
            raise BackupError(f"duplicate vkey hash at vkeys[{i}]")
        seen.add(key_hash)
        try:
            models = _coerce_models(entry.get("models"))
            max_budget = _coerce_limit(entry.get("max_budget"), "max_budget")
            rpm = _coerce_limit(entry.get("rpm"), "rpm")
            tpm = _coerce_limit(entry.get("tpm"), "tpm")
        except ValueError as exc:
            raise BackupError(f"vkeys[{i}]: {exc}") from None
        priority = entry.get("priority")
        if priority is not None and not isinstance(priority, str):
            raise BackupError(f"vkeys[{i}].priority must be a string or null")
        owner = entry.get("owner_id")
        if owner is not None and not isinstance(owner, str):
            raise BackupError(f"vkeys[{i}].owner_id must be a string or null")
        disabled = entry.get("disabled", False)
        if disabled is None:
            disabled = False
        if not isinstance(disabled, bool):
            raise BackupError(f"vkeys[{i}].disabled must be a boolean")
        expires = entry.get("expires_at")
        out.append({
            "id": _as_str(entry.get("id"), f"vkeys[{i}].id", required=False),
            "key_hash": key_hash,
            "key_alias": _as_str(entry.get("key_alias"),
                                 f"vkeys[{i}].key_alias", required=False),
            "models": models,
            "max_budget": max_budget,
            "spend_to_date": _as_float(
                entry.get("spend_to_date") or 0.0,
                f"vkeys[{i}].spend_to_date"),
            "budget_reserved": _as_float(
                entry.get("budget_reserved") or 0.0,
                f"vkeys[{i}].budget_reserved"),
            "rpm": rpm, "tpm": tpm,
            "expires_at": (None if expires is None
                           else _as_float(expires, f"vkeys[{i}].expires_at")),
            "disabled": disabled, "owner_id": owner, "priority": priority,
        })
    plan["vkeys"] = out


_LOG_NUMERIC = ("status", "tok_in", "tok_cached", "tok_cache_creation",
                "tok_reasoning", "tok_out", "tps", "ttft_ms", "latency_ms",
                "cost", "cache_savings")


def _validate_logs(doc: dict, plan: dict) -> None:
    raw = doc.get("logs") or {}
    if not isinstance(raw, dict):
        raise BackupError("'logs' must be a JSON object")
    rows = raw.get("request_logs") or []
    if not isinstance(rows, list):
        raise BackupError("logs.request_logs must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BackupError(f"logs.request_logs[{i}] is not a JSON object")
        # request_id is the merge key, not id: request_logs.id is
        # AUTOINCREMENT/SERIAL and is not portable across databases, so
        # re-inserting a source id would collide with whatever the target
        # assigned. uuid4().hex[:16] is unique per request in practice.
        request_id = _as_str(row.get("request_id"),
                             f"logs.request_logs[{i}].request_id")
        if request_id in seen:
            raise BackupError(
                f"logs.request_logs[{i}]: duplicate request_id "
                f"{request_id!r} in backup")
        seen.add(request_id)
        entry = dict(row)
        entry["request_id"] = request_id
        for field in _LOG_NUMERIC:
            if entry.get(field) is not None:
                entry[field] = _as_float(entry[field],
                                         f"logs.request_logs[{i}].{field}")
        out.append(entry)
    plan["logs"] = {"request_logs": out}


_ROLLUP_KEY = ("bucket_ts", "key_id", "model_group", "provider",
               "serving_model")


def _validate_rollups(doc: dict, plan: dict) -> None:
    raw = doc.get("rollups") or {}
    if not isinstance(raw, dict):
        raise BackupError("'rollups' must be a JSON object")
    rows = raw.get("request_rollups") or []
    if not isinstance(rows, list):
        raise BackupError("rollups.request_rollups must be a list")
    out: list[dict] = []
    seen: set[tuple] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BackupError(
                f"rollups.request_rollups[{i}] is not a JSON object")
        # The natural key is the DB's own UNIQUE index (db_sink's
        # idx_rollup_unique), so a re-import overwrites the same bucket rather
        # than double-counting it.
        ident = tuple(
            row.get(f, "" if f != "bucket_ts" else None)
            for f in _ROLLUP_KEY)
        if ident in seen:
            raise BackupError(
                f"rollups.request_rollups[{i}]: duplicate bucket "
                f"{ident!r} in backup")
        seen.add(ident)
        entry = dict(row)
        entry["bucket_ts"] = _as_float(row.get("bucket_ts"),
                                       f"rollups.request_rollups[{i}]."
                                       "bucket_ts")
        out.append(entry)
    plan["rollups"] = {"request_rollups": out}


def _validate_audit(doc: dict, plan: dict) -> None:
    """Accept the audit section, never replay it.

    ``audit_logs`` has no natural key — only a surrogate autoincrement ``id``
    plus ts/actor/action/target. Restoring another instance's trail would forge
    this instance's operator history, and a composite merge key would silently
    drop legitimately repeated events (the same admin clicking Save twice is
    two real rows). Export-only; the count is reported so the operator can see
    what they are not getting back.
    """
    raw = doc.get("audit") or {}
    if not isinstance(raw, dict):
        raise BackupError("'audit' must be a JSON object")
    rows = raw.get("audit_logs") or []
    if not isinstance(rows, list):
        raise BackupError("audit.audit_logs must be a list")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BackupError(f"audit.audit_logs[{i}] is not a JSON object")
        _as_str(row.get("action"), f"audit.audit_logs[{i}].action",
                required=False)
    plan["audit"] = {"audit_logs": rows}


def _validate_responses(doc: dict, plan: dict) -> None:
    raw = doc.get("responses") or {}
    if not isinstance(raw, dict):
        raise BackupError("'responses' must be a JSON object")
    rows = raw.get("stored_responses") or []
    if not isinstance(rows, list):
        raise BackupError("responses.stored_responses must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BackupError(
                f"responses.stored_responses[{i}] is not a JSON object")
        rid = _as_str(row.get("id"),
                      f"responses.stored_responses[{i}].id")
        if rid in seen:
            raise BackupError(f"duplicate stored response id {rid!r}")
        seen.add(rid)
        out.append(dict(row))
    plan["responses"] = {"stored_responses": out}


def summarize(plan: dict) -> dict:
    """Per-section row counts for the console's pre-import preview."""
    return {"sections": list(plan["sections"]), "counts": plan["counts"]}


# ---------------------------------------------------------------------------
# apply — consumes a validated plan
# ---------------------------------------------------------------------------


async def apply_backup(state, plan: dict) -> dict:
    """Merge a validated plan into the gateway. Never deletes.

    The three bulk sections — ``logs``, ``rollups``, ``responses`` — are applied
    **first**, in **one transaction**, before anything else is touched. Two
    reasons, both learned the hard way:

    * They are the only sections large enough to fail *mid-flight*. A per-page
      or per-section commit meant a malformed row 1 200 left providers, keys,
      users, vkeys and the first 1 000 log rows durable and then 500'd, with
      nothing telling the operator how far it got. Now one failure rolls the
      whole bulk pass back and nothing else has been written.
    * They reference nothing this import creates. ``request_logs.key_id``,
      ``request_rollups.key_id`` and ``stored_responses.key_id`` are free-form
      strings, so running them first costs no resolvability.

    Within that transaction each rollup/response row gets its own SAVEPOINT, so
    one un-insertable row is rolled back alone and reported as ``failed``
    instead of taking the section with it.

    The configuration sections then run in :data:`SECTIONS` order. Every
    per-record persistence failure there is logged and counted rather than
    raised: a YAML-defined provider has no DB row by design, so its in-memory
    mutation is the whole story and treating that as a 500 would make the
    import look broken when it worked. Those sections persist through
    ``ConfigStore``, which owns its own transactions, so a failure in one of
    them does not roll back the ones before it.
    """
    if plan.get("_validated") is not True:
        raise BackupError("plan was not validated; call validate_backup first")
    result: dict[str, Any] = {}
    result.update(await _apply_bulk(state, plan))
    result["providers"] = await _apply_providers(state, plan["providers"])
    result["keys"] = await _apply_keys(state, plan["keys"])
    result["deployments"] = await _apply_deployments(state, plan["deployments"])
    result["aliases"] = _apply_aliases(state, plan["aliases"])
    result["settings"] = await _apply_settings(state, plan["settings"])
    result["prices"] = await _apply_prices(state, plan["prices"])
    result["users"] = await _apply_users(state, plan["users"])
    result["vkeys"] = await _apply_vkeys(state, plan["vkeys"])
    result["audit_skipped"] = len(plan["audit"]["audit_logs"])
    if plan.get("cline_default_models") is not None:
        # Reconciling the Cline default models touches the router in a way that
        # belongs to app.py's helper, not here — the route calls it.
        result["cline_default_models"] = plan["cline_default_models"]
    return result


async def _apply_bulk(state, plan: dict) -> dict[str, Any]:
    """The bulk sections, in one transaction, as ``{section: {written, failed}}``.

    A failure anywhere in the pass raises :class:`BackupError` with a 500, so
    the route renders it and the operator learns the import was rolled back
    rather than receiving a bare stack trace out of a half-written gateway.
    """
    engine = _engine(state)
    store = state.response_store
    # ResponseStore is built with the app's own engine, so this is the normal
    # case. Guard anyway: a second engine cannot join this transaction, and
    # silently pretending it did would be exactly the class of bug this
    # function exists to remove.
    shared = store is None or store.engine is engine
    out: dict[str, Any] = {}
    try:
        async with engine.begin() as conn:
            out["logs"] = await _apply_logs(state, plan["logs"], conn=conn)
            out["rollups"] = await _apply_rollups(state, plan["rollups"],
                                                   conn=conn)
            if shared and store is not None:
                out["responses"] = await _apply_responses(
                    state, plan["responses"], conn=conn)
        if store is not None and not shared:
            log.warning("backup_responses_separate_engine")
            out["responses"] = await _apply_responses(state, plan["responses"])
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(
            f"import failed while writing the log/rollup/response sections and "
            f"was rolled back in full: {exc}",
            status=500, etype="api_error") from exc
    # After the commit: invalidating the sink's query cache before it would
    # advertise rows that the rollback then erased.
    sink = state.logs.db_sink
    if sink is not None:
        sink.invalidate_cache()
    return out


async def _apply_providers(state, entries: list[dict]) -> int:
    from wiwi.router.router import ProviderAccount  # local: avoids a cycle

    store = state.config_store
    applied = 0
    for p in entries:
        acct = state.router.providers.get(p["name"])
        if acct is None:
            acct = ProviderAccount(
                name=p["name"], provider_type=p["provider_type"],
                base_url=p["base_url"],
                timeout_s=p["timeout_s"] or 120.0,
                extra_headers=dict(p["extra_headers"] or {}),
                round_robin=(p["round_robin"]
                             if p["round_robin"] is not None else True),
                alias_id=p["alias_id"] if p["alias_id_set"] else None)
            state.router.providers[p["name"]] = acct
        else:
            acct.provider_type = p["provider_type"]
            acct.base_url = p["base_url"]
            if p["timeout_s"] is not None:
                acct.timeout_s = p["timeout_s"]
            if p["extra_headers"] is not None:
                acct.extra_headers = dict(p["extra_headers"])
            if p["round_robin"] is not None:
                acct.round_robin = p["round_robin"]
        if p["alias_id_set"]:
            try:
                state.router.set_provider_alias(p["name"], p["alias_id"])
            except ValueError as exc:
                # Validation already rejected cross-file and foreign claims, so
                # this is a live-state race, not a bad document.
                log.warning("backup_alias_rejected", provider=p["name"],
                            alias=p["alias_id"], exc_info=exc)
        if store is not None:
            # add_provider is a full upsert on both dialects, so it covers the
            # create and the update case — and, unlike update_provider, cannot
            # raise ConfigStoreNotFound on a YAML-defined account that has no
            # DB row (the AUDIT #319 trap).
            await store.add_provider(
                p["name"], p["provider_type"], acct.base_url,
                timeout_s=acct.timeout_s,
                extra_headers=dict(acct.extra_headers),
                round_robin=acct.round_robin, alias_id=acct.alias_id)
        applied += 1
    return applied


async def _apply_keys(state, entries: list[dict]) -> int:
    from wiwi.router.router import ProviderKey

    store = state.config_store
    applied = 0
    for k in entries:
        acct = state.router.providers.get(k["provider_name"])
        if acct is None:
            # A provider whose section was filtered out of this import.
            continue
        live = acct.get_key(k["label"])
        if live is None:
            acct.keys.append(ProviderKey(
                label=k["label"], secret=k["secret"] or "",
                weight=k["weight"], enabled=k["enabled"]))
        else:
            # weight/enabled are always written; the secret only when the
            # document carries one. A backup exported without secrets must not
            # blank a working credential.
            live.weight = k["weight"]
            live.enabled = k["enabled"]
            if k["secret"]:
                live.secret = k["secret"]
        if store is not None and k["secret"]:
            await store.add_key(k["provider_name"], k["label"],
                                k["secret"], k["weight"], k["enabled"])
        applied += 1
    return applied


async def _apply_deployments(state, entries: list[dict]) -> int:
    """Merge deployments, persisting only what the table can hold.

    ``add_deployment`` takes ``(group, provider, model, weight)`` — the
    ``deployments`` table has no column for the five limits in
    :data:`_DEPLOYMENT_LIMITS`, so a document that carries them is honoured on
    the in-memory ``Deployment`` for the life of the process and dropped from
    there on restart. They are not exported any more
    (:func:`_export_deployments`); this path only serves older files.
    """
    from wiwi.router.router import Deployment

    store = state.config_store
    applied = 0
    for d in entries:
        acct = state.router.providers.get(d["provider_name"])
        if acct is None:
            continue
        bucket = state.router.groups.get(d["group_name"], [])
        existing = next((dep for dep in bucket
                         if dep.provider is acct
                         and dep.model_id == d["model_id"]), None)
        if d["detached"]:
            # The source instance recorded this as removed. Removing it here
            # is that same recorded removal, not a wipe — and skipping it would
            # resurrect the deployment on the next restart.
            if existing is not None:
                bucket.remove(existing)
                if not bucket:
                    state.router.groups.pop(d["group_name"], None)
            if store is not None:
                await store.delete_deployment(d["group_name"],
                                              d["provider_name"],
                                              d["model_id"])
            applied += 1
            continue
        if existing is None:
            bucket.append(Deployment(
                group=d["group_name"], provider=acct, model_id=d["model_id"],
                weight=d["weight"],
                **{f: d[f] for f in _DEPLOYMENT_LIMITS if f in d}))
            state.router.groups[d["group_name"]] = bucket
        else:
            existing.weight = d["weight"]
            for field in _DEPLOYMENT_LIMITS:
                # Absent = unknown: leave whatever the target had.
                if field in d:
                    setattr(existing, field, d[field])
        if store is not None:
            # add_deployment is an upsert that clears any detached tombstone.
            await store.add_deployment(d["group_name"], d["provider_name"],
                                       d["model_id"], d["weight"])
        applied += 1
    # Mandatory: without it a group that gained a second provider keeps stale
    # cross-provider WRR membership.
    state.router.rebuild_cross_provider_pools()
    return applied


def _apply_aliases(state, entries: dict) -> int:
    """Merge the ``model_group_alias`` overlay into the live map.

    Same semantics as the startup merge in ``_load_db_config``: a ``None``
    value removes a YAML-defined alias, anything else is coerced through
    ``ModelAliasEntry`` so a malformed entry is dropped rather than poisoning
    the map.
    """
    merged = dict(state.router.settings.model_group_alias)
    for name, value in entries.items():
        if value is None:
            merged.pop(name, None)
            continue
        coerced = coerce_alias_value(value)
        if coerced is None:
            log.warning("backup_alias_entry_dropped", alias=name)
            continue
        merged[name] = coerced
    state.router.settings.model_group_alias = merged
    return len(entries)


async def _apply_settings(state, entries: dict) -> int:
    store = state.config_store
    applied = 0
    for key, value in entries.items():
        if key == "model_group_alias":
            # Handled by _apply_aliases from the dedicated section: writing the
            # merged map here would persist YAML-defined aliases as though an
            # admin had created them.
            continue
        if store is not None:
            await store.set_setting(key, value)
        if key == "alert_rules" and isinstance(value, list):
            state.alert_rules = value
        elif key == "routing_strategy" and isinstance(value, str):
            state.router.settings.routing_strategy = value
        applied += 1
    return applied


async def _apply_prices(state, entries: list[dict]) -> int:
    store = state.config_store
    applied = 0
    for p in entries:
        mid = p["model_id"]
        base = {k: v for k, v in p.items()
                if k not in ("model_id", "providers")}
        # Merge rather than replace: an entry may carry only the rates, and a
        # scoped override already in the live map must survive. Build the
        # whole entry in one dict and assign once — mutating
        # ``cost.prices[mid]`` and then rebinding it from a pre-scope snapshot
        # silently dropped every scoped override.
        merged = dict(state.cost.prices.get(mid, {}))
        merged.update(base)
        if store is not None:
            await store.upsert_price(mid, base)
        scoped_all = merged.setdefault("providers", {})
        for scope, rates in (p.get("providers") or {}).items():
            if store is not None:
                await store.upsert_price_scope(mid, scope, rates)
            scoped_all.setdefault(scope, {}).update(rates)
        state.cost.prices[mid] = merged
        applied += 1
    return applied


async def _apply_users(state, entries: list[dict]) -> int:
    """Upsert console accounts on ``username``, preserving the target's ``id``.

    The id is what ``vkeys.owner_id`` points at, and authentication checks it
    with an ``EXISTS`` subquery — an orphaned owner silently kills every key
    that user holds. So a user that already exists keeps its id and only its
    mutable fields are refreshed.
    """
    if state.users is None:
        return 0
    engine = state.users.engine
    now = time.time()
    applied = 0
    for u in entries:
        async with engine.begin() as conn:
            existing = (await conn.execute(sa.text(
                "SELECT id FROM users WHERE username = :u"),
                {"u": u["username"]})).first()
            if existing is not None:
                sets = ["role = :r", "disabled = :d", "updated_at = :t"]
                params: dict[str, Any] = {
                    "u": u["username"], "r": u["role"],
                    "d": int(u["disabled"]), "t": now}
                if u["password_hash"]:
                    sets.append("password_hash = :h")
                    params["h"] = u["password_hash"]
                await conn.execute(sa.text(
                    f"UPDATE users SET {', '.join(sets)}"
                    " WHERE username = :u"), params)
            else:
                # A new account keeps the source id so any vkeys.owner_id in
                # the same document resolves to a row that exists.
                await conn.execute(sa.text(
                    "INSERT INTO users (id, username, password_hash, role,"
                    " disabled, created_at, updated_at)"
                    " VALUES (:i,:u,:h,:r,:d,:c,:t)"),
                    {"i": u["id"] or f"bk{u['username']}", "u": u["username"],
                     "h": u["password_hash"] or "", "r": u["role"],
                     "d": int(u["disabled"]), "c": now, "t": now})
        applied += 1
    return applied


async def _apply_vkeys(state, entries: list[dict]) -> int:
    """Upsert virtual keys on ``key_hash``, preserving the target's ``id``.

    ``AuthService`` has no by-hash insert — ``create_key`` takes a plaintext
    and mints a fresh id — so this writes the row directly. The id is
    preserved on conflict so ``owner_id`` references and any local state keyed
    on it stay valid.
    """
    if state.auth is None:
        return 0
    engine = state.auth.engine
    is_pg = engine.dialect.name == "postgresql"
    applied = 0
    for k in entries:
        async with engine.begin() as conn:
            existing = (await conn.execute(sa.text(
                "SELECT id FROM vkeys WHERE key_hash = :h"),
                {"h": k["key_hash"]})).first()
            models = orjson.dumps(k["models"] or []).decode()
            if existing is not None:
                await conn.execute(sa.text(
                    "UPDATE vkeys SET key_alias = :a, models = :m,"
                    " max_budget = :b, spend_to_date = :s,"
                    " budget_reserved = :r, rpm = :rpm, tpm = :tpm,"
                    " expires_at = :e, disabled = :d, owner_id = :o,"
                    " priority = :p WHERE key_hash = :h"),
                    {"h": k["key_hash"], "a": k["key_alias"], "m": models,
                     "b": k["max_budget"], "s": k["spend_to_date"],
                     "r": k["budget_reserved"], "rpm": k["rpm"],
                     "tpm": k["tpm"], "e": k["expires_at"],
                     "d": int(k["disabled"]), "o": k["owner_id"],
                     "p": k["priority"]})
            else:
                uid = k["id"] or f"bk{k['key_hash'][:16]}"
                if is_pg:
                    sql = ("INSERT INTO vkeys (id, key_hash, key_alias,"
                           " models, max_budget, spend_to_date,"
                           " budget_reserved, rpm, tpm, expires_at, disabled,"
                           " owner_id, priority, created_at, updated_at)"
                           " VALUES (:i,:h,:a,:m,:b,:s,:r,:rpm,:tpm,:e,:d,:o,"
                           ":p,:c,:t)"
                           " ON CONFLICT (id) DO NOTHING")
                else:
                    sql = ("INSERT OR IGNORE INTO vkeys (id, key_hash,"
                           " key_alias, models, max_budget, spend_to_date,"
                           " budget_reserved, rpm, tpm, expires_at, disabled,"
                           " owner_id, priority, created_at, updated_at)"
                           " VALUES (:i,:h,:a,:m,:b,:s,:r,:rpm,:tpm,:e,:d,:o,"
                           ":p,:c,:t)")
                await conn.execute(sa.text(sql), {
                    "i": uid, "h": k["key_hash"], "a": k["key_alias"],
                    "m": models, "b": k["max_budget"],
                    "s": k["spend_to_date"], "r": k["budget_reserved"],
                    "rpm": k["rpm"], "tpm": k["tpm"], "e": k["expires_at"],
                    "d": int(k["disabled"]), "o": k["owner_id"],
                    "p": k["priority"], "c": time.time(), "t": time.time()})
        applied += 1
    if applied:
        # Without this a key that 401'd before the import keeps serving the
        # cached negative entry for up to the 60s auth TTL.
        state.auth.invalidate_cache()
    return applied


#: Columns re-encoded as JSON on the way back in. They are TEXT in the schema
#: and hold lists/objects, so the restore must serialize before binding.
_JSON_COLUMNS = ("attempts", "translation_warnings", "diff", "p95_hist")


def _union_columns(rows: list[dict]) -> list[str]:
    """Every key any row carries, in first-seen order.

    The column set used to be read off ``rows[0]`` alone. A document is not
    required to be homogeneous — a hand-merged file, a section stitched from two
    exports, or simply a row that never had the field — so that wrote the
    *column default* for every row, losing the value with no error at all.
    Verified: ``error_code='api_error'`` on the second row of a two-row document
    was stored as ``''`` on both.
    """
    seen: dict[str, None] = {}
    for row in rows:
        for c in row:
            seen.setdefault(c, None)
    return list(seen)


async def _insert_rows(engine, table: str, rows: list[dict], *,
                       key: str, page: int = 500, conn=None) -> int:
    """Insert rows whose *key* is not already present in *table*.

    ``request_logs.request_id`` has no unique constraint (``db_sink``'s index
    on it is deliberately non-unique), so neither INSERT OR IGNORE nor
    ON CONFLICT can dedup. The existing key values are therefore read up
    front and the survivors go in as plain multi-row inserts. That leaves a
    theoretical window if the gateway writes a colliding request while the
    import runs — acceptable for a manual admin action, and far cheaper than
    one guarded statement per row.

    The column set is the union of every row's keys (see :func:`_union_columns`)
    intersected with the target's live schema. A row that lacks one of them
    binds NULL for that row: *absent*, not *the column default*, which the two
    are indistinguishable as afterwards.

    Every page runs in one write scope, so a page that fails leaves none of the
    pages before it committed. Pass *conn* to join a caller's transaction.
    """
    if not rows:
        return 0
    written = 0
    async with _write_scope(engine, conn) as c:
        present = await _table_columns(engine, table, conn=c)
        cols = [col for col in _union_columns(rows) if col in present]
        if key not in cols:
            return 0
        col_sql = ", ".join(cols)
        existing = {r[0] for r in (await c.execute(sa.text(
            f"SELECT {key} FROM {table}"))).all()}
        for start in range(0, len(rows), page):
            params: dict[str, Any] = {}
            tuples = []
            for n, row in enumerate(rows[start:start + page]):
                ident = row.get(key)
                if ident in existing:
                    continue
                existing.add(ident)
                for col in cols:
                    value = row.get(col)
                    if col in _JSON_COLUMNS and isinstance(value, (list, dict)):
                        value = orjson.dumps(value).decode()
                    params[f"c{n}_{col}"] = value
                tuples.append("(" + ", ".join(
                    f":c{n}_{col}" for col in cols) + ")")
                written += 1
            if not tuples:
                continue
            await c.execute(sa.text(
                f"INSERT INTO {table} ({col_sql})"
                f" VALUES {', '.join(tuples)}"), params)
    return written


async def _exec_insert(engine, sql: str, params: dict[str, Any], *,
                       conn=None) -> None:
    async with _write_scope(engine, conn) as c:
        await c.execute(sa.text(sql), params)


async def _apply_logs(state, entries: dict, *, conn=None) -> int:
    """Raw log rows. A page that cannot be inserted aborts the whole import."""
    rows = entries["request_logs"]
    if not rows:
        return 0
    return await _insert_rows(_engine(state), "request_logs", rows,
                              key="request_id", conn=conn)


async def _apply_rollups(state, entries: dict, *, conn=None) -> dict[str, int]:
    """Upsert hourly aggregates, one SAVEPOINT per row.

    Returns ``{"written": n, "failed": n}``. The failure count used to be a
    ``log.warning`` and nothing else, so a target whose ``request_rollups``
    rejected every insert answered ``200 {"rollups": 0}`` -- indistinguishable
    from a file that carried no buckets at all. Each row rolls back to its own
    savepoint: on PostgreSQL a failed statement aborts the whole transaction,
    so continuing without one would poison the import it is part of.
    """
    rows = entries["request_rollups"]
    engine = _engine(state)
    if not rows:
        return {"written": 0, "failed": 0}
    is_pg = engine.dialect.name == "postgresql"
    written = failed = 0
    async with _write_scope(engine, conn) as c:
        present = await _table_columns(engine, "request_rollups", conn=c)
        cols = [x for x in _union_columns(rows) if x in present]
        for row in rows:
            clean = dict(row)
            hist = clean.get("p95_hist")
            if isinstance(hist, (list, dict)):
                clean["p95_hist"] = orjson.dumps(hist).decode()
            params = {x: clean.get(x) for x in cols}
            col_sql = ", ".join(cols)
            placeholders = ", ".join(f":{x}" for x in cols)
            where = " AND ".join(f"{x} = :{x}" for x in _ROLLUP_KEY)
            if is_pg:
                # Upsert on the DB's own UNIQUE index so a re-import overwrites
                # the same bucket rather than double-counting it.
                sql = (f"INSERT INTO request_rollups ({col_sql})"
                       f" VALUES ({placeholders})"
                       " ON CONFLICT (bucket_ts, key_id, model_group, provider,"
                       " serving_model) DO UPDATE SET "
                       + ", ".join(f"{x} = EXCLUDED.{x}"
                                   for x in cols if x not in _ROLLUP_KEY))
            else:
                sql = (f"INSERT OR REPLACE INTO request_rollups ({col_sql})"
                       f" VALUES ({placeholders})")
            try:
                async with c.begin_nested():
                    result = await c.execute(sa.text(sql), params)
                if result.rowcount == 0:
                    # INSERT OR IGNORE / ON CONFLICT DO NOTHING swallow a row
                    # that violates a constraint instead of raising. Counting
                    # that as written is the same lie as the log.warning this
                    # section used to hide behind.
                    failed += 1
                    log.warning("backup_rollup_row_ignored", row=where)
                else:
                    written += 1
            except Exception:
                failed += 1
                log.warning("backup_rollup_row_failed", row=where,
                            exc_info=True)
    if failed:
        log.warning("backup_rollup_rows_failed", failed=failed, written=written)
    return {"written": written, "failed": failed}


async def _apply_responses(state, entries: dict, *,
                           conn=None) -> dict[str, int]:
    """Insert cached Responses-API rows, one SAVEPOINT per row.

    Returns ``{"written": n, "failed": n}``. This section had no guard at all:
    one un-insertable row raised out of the import and 500'd *after* ``logs``
    and ``rollups`` were already committed, taking the response history of an
    otherwise working gateway with it.
    """
    rows = entries["stored_responses"]
    store = state.response_store
    if not rows or store is None:
        return {"written": 0, "failed": 0}
    engine = store.engine
    is_pg = engine.dialect.name == "postgresql"
    written = failed = 0
    async with _write_scope(engine, conn) as c:
        present = await _table_columns(engine, "stored_responses", conn=c)
        cols = [x for x in _union_columns(rows) if x in present]
        col_sql = ", ".join(cols)
        placeholders = ", ".join(f":{x}" for x in cols)
        if is_pg:
            sql = (f"INSERT INTO stored_responses ({col_sql})"
                   f" VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING")
        else:
            sql = (f"INSERT OR IGNORE INTO stored_responses ({col_sql})"
                   f" VALUES ({placeholders})")
        for row in rows:
            params = {x: row.get(x) for x in cols}
            try:
                async with c.begin_nested():
                    result = await c.execute(sa.text(sql), params)
                if result.rowcount == 0:
                    # OR IGNORE / ON CONFLICT DO NOTHING drop a row that breaks
                    # a constraint rather than raising: without the rowcount
                    # check a rejected row is reported as written.
                    failed += 1
                    log.warning("backup_response_row_ignored",
                                response_id=row.get("id"))
                else:
                    written += 1
            except Exception:
                failed += 1
                log.warning("backup_response_row_failed",
                            response_id=row.get("id"), exc_info=True)
    if failed:
        log.warning("backup_response_rows_failed", failed=failed,
                    written=written)
    return {"written": written, "failed": failed}
