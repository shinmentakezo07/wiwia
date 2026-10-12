// Whole-gateway backup / restore: export, preview, merge, and the shared
// file-download helper the console had been duplicating page by page.

import { api } from "./client";
import type {
  BackupDocument,
  BackupImportResult,
  BackupPlan,
  BackupSections,
} from "./types";

/** Sections the format knows, in the order the server applies them.
 *
 *  Kept in sync with `SECTIONS` in `wiwi/server/backup.py`. */
export const BACKUP_SECTIONS: { id: BackupSections; label: string; bulk?: boolean }[] = [
  { id: "providers", label: "Providers" },
  { id: "keys", label: "Provider keys" },
  { id: "deployments", label: "Model deployments" },
  { id: "aliases", label: "Model aliases" },
  { id: "settings", label: "Settings & alerts" },
  { id: "prices", label: "Model prices" },
  { id: "users", label: "Users" },
  { id: "vkeys", label: "Virtual keys & budgets" },
  { id: "logs", label: "Request log", bulk: true },
  { id: "rollups", label: "Usage rollups", bulk: true },
  { id: "audit", label: "Audit log", bulk: true },
  { id: "responses", label: "Stored responses", bulk: true },
];

/** The default export: everything durable, nothing unbounded.
 *
 *  Bulk sections are opt-in — the request log grows without limit and would
 *  otherwise make every backup a file nobody can download. */
export const DEFAULT_BACKUP_SECTIONS: BackupSections[] = BACKUP_SECTIONS.filter(
  (s) => !s.bulk,
).map((s) => s.id);

function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Fetch the backup document. Sent as JSON rather than streamed — the API
 *  client is JSON-only by construction, so the blob is built here. */
export function exportBackup(opts: {
  sections: BackupSections[];
  includeSecrets: boolean;
  includeBodies: boolean;
  logDays: number;
  logLimit: number;
}): Promise<BackupDocument> {
  return api<BackupDocument>(
    `/admin/backup${query({
      sections: opts.sections.join(","),
      include_secrets: opts.includeSecrets ? 1 : undefined,
      include_bodies: opts.includeBodies ? 1 : undefined,
      log_days: opts.logDays || undefined,
      log_limit: opts.logLimit || undefined,
    })}`,
  );
}

/** Validate a document and report what it would change, without writing.
 *  The Merge button stays disabled until this succeeds. */
export function previewBackup(
  doc: BackupDocument,
  sections: BackupSections[],
): Promise<BackupPlan> {
  return postBackup<BackupPlan>(doc, sections, true);
}

/** POST a document to the merge endpoint.
 *
 *  The dry run and the real merge send byte-identical bodies and differ only
 *  in a flag, so the preview cannot drift from what the merge actually does. */
function postBackup<T>(
  doc: BackupDocument,
  sections: BackupSections[],
  dryRun: boolean,
): Promise<T> {
  return api<T>(`/admin/backup/import${query({
    sections: sections.join(","),
    dry_run: dryRun ? 1 : undefined,
  })}`, {
    method: "POST",
    body: JSON.stringify(doc),
  });
}

/** Merge a document. Merge only: upserts on each section's natural key and
 *  never deletes. */
export function importBackup(
  doc: BackupDocument,
  sections: BackupSections[],
): Promise<BackupImportResult> {
  return postBackup<BackupImportResult>(doc, sections, false);
}

/** Trigger a client-side download of `data` as pretty-printed JSON.
 *
 *  This replaces the blob → anchor → revoke sequence that had been copy-pasted
 *  into the Providers, WorkBuddy, and Analytics pages. The anchor is attached
 *  to the document before `.click()`: Firefox ignores a click on a detached
 *  element, which is why the WorkBuddy export silently did nothing there. */
export function downloadJson(filename: string, data: unknown): void {
  downloadBlob(filename, new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  }));
}

/** As `downloadJson`, for pre-serialized text (CSV and friends). */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** The console's filename convention: kebab-case plus an ISO date. */
export function datedFilename(prefix: string, ext = "json"): string {
  return `${prefix}-${new Date().toISOString().slice(0, 10)}.${ext}`;
}