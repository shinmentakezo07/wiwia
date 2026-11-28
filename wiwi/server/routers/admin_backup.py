"""Whole-gateway backup / restore (admin HTTP surface).

Extracted verbatim from ``server/app.py``: same paths, same status codes,
same audit trail, same error bodies. The logic lives in
``wiwi/server/backup.py``; these handlers are thin wrappers so the
admin-auth, audit, and error-shape conventions stay in one place. They
close over per-app objects handed in by ``create_app`` (``state``,
``config``, ``json_body`` and ``_err``), so the router is built once per
app — auth is the module-level ``Depends`` guard (``AdminUser`` from
``wiwi/server/dependencies``).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from wiwi.server import backup
from wiwi.server.dependencies import AdminUser, ORJSONResponse


def make_router(state, config, *, json_body, _err, _apply_cline_default_models):
    """Build the /admin/backup router bound to the per-app helpers.

    ``_apply_cline_default_models`` is the module-level Cline reconciler
    from ``server/app.py``, handed in so the import path can re-target the
    router's deployments exactly as it did inline.
    """
    api = APIRouter(tags=["admin-backup"])

    # -- whole-gateway backup / restore ---------------------------------------
    #
    # The logic lives in wiwi/server/backup.py; these are thin wrappers so the
    # admin-auth, audit, and error-shape conventions stay in one place.

    def _backup_import_limit_mb() -> float:
        """Bound for the restore path's oversized body.

        Four times the configured cap, floored at 200 MiB. Enough for a
        configuration-plus-logs restore; not so large that a single admin
        request can exhaust the process's memory. Past it, import in sections.
        """
        return max(200.0, config.wiwi_settings.max_request_body_mb * 4)

    @api.get("/admin/backup")
    async def admin_export_backup(request: Request, actor: AdminUser):
        """Export every persisted table as one merge-restorable JSON document.

        Query parameters (all optional):
          ?include_secrets=1   provider key plaintexts and Cline refresh
                               tokens. The only live credentials in the file.
          ?include_bodies=1    request/response payload text — a SECOND,
                               independent opt-in, so pulling credentials can
                               never also pull user prompts.
          ?sections=a,b        subset of the format's sections. Bulk sections
                               (logs, rollups, audit, responses) are excluded
                               by default; the file stays a size you can
                               download and a browser can parse.
          ?log_days=30         age bound on log sections; 0 means all time.
          ?log_limit=0         cap on raw log rows; 0 means no cap. A cap that
                               bites is reported in the document's `truncated`.

        Virtual keys and user passwords travel as SHA-256 / PBKDF2 hashes and
        restore BY HASH — the same plaintext key keeps working on the restored
        gateway, but this file cannot reveal it.
        """
        qp = request.query_params
        try:
            sections = backup.parse_sections(qp.get("sections"))
            doc = await backup.build_backup(
                state,
                include_secrets=backup.truthy(qp.get("include_secrets")),
                include_bodies=backup.truthy(qp.get("include_bodies")),
                sections=sections,
                log_days=int(qp.get("log_days") or 30),
                log_limit=int(qp.get("log_limit") or 0))
        except backup.BackupError as exc:
            return _err(exc.status, exc.etype, exc.message, request)
        except ValueError as exc:
            return _err(400, "invalid_request_error", str(exc), request)
        await state.logs.log_audit(actor=actor.username, action="backup.export",
                                   target=",".join(sections),
                                   diff=doc["counts"])
        # no-store: this response is a credential dump.
        return ORJSONResponse(doc, headers={"Cache-Control": "no-store"})

    @api.post("/admin/backup/import")
    async def admin_import_backup(request: Request, actor: AdminUser):
        """Merge a backup document. MERGE ONLY — upsert by natural key, never
        deletes.

        The whole document is VALIDATED before anything is written, so a
        malformed document is rejected with nothing applied. ``?dry_run=1``
        runs that validation and returns the per-section plan without
        writing — which is what the console's Import panel shows before the
        Merge button enables.

        Validation is not the same as atomicity, and the difference matters
        if a merge fails partway. The three bulk sections (``logs``,
        ``rollups``, ``responses``) are applied first, in ONE transaction, so
        a malformed row rolls the whole bulk pass back with nothing else
        touched. The configuration sections then run in order, and each
        persists through ``ConfigStore`` in its OWN transaction — so a
        failure in, say, ``prices`` leaves every earlier config section
        (providers, keys, deployments, aliases, settings) durably committed.
        Nothing rolls them back. A per-record failure inside a config
        section is logged and counted rather than raised, so the response's
        per-section counts are the report of what actually landed.

        The audit section is validated, counted, and reported but never
        replayed: replaying another instance's mutation trail would forge this
        one's operator history.
        """
        body, jerr = await json_body(request, limit_mb=_backup_import_limit_mb())
        if jerr:
            return jerr
        dry_run = backup.truthy(request.query_params.get("dry_run"))
        try:
            # The selection: the caller's ``?sections=`` when it sent one,
            # otherwise the sections the document itself carries. See
            # ``backup.parse_import_sections`` (AUDIT #510).
            sections = backup.parse_import_sections(
                request.query_params.get("sections"), body)
            plan = await backup.validate_backup(state, body, sections=sections)
            if dry_run:
                return ORJSONResponse({"dry_run": True, **backup.summarize(plan)})
            result = await backup.apply_backup(state, plan)
        except backup.BackupError as exc:
            return _err(exc.status, exc.etype, exc.message, request)
        cline_models = result.pop("cline_default_models", None)
        if cline_models is not None:
            # Reconciling Cline default models is an app.py-owned reconciler
            # that mutates the router; backup.py must not reach across.
            await _apply_cline_default_models(state, cline_models)
        await state.logs.log_audit(actor=actor.username, action="backup.import", target="",
                                   diff=result)
        return ORJSONResponse(result)

    return api
