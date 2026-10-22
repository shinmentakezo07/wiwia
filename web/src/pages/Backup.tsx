// Import / Export — the console half of `wiwi/server/backup.py`.
//
// Two independent flows over one JSON format:
//
//   Export  choose sections, decide whether the file carries credentials or
//           user prompts, download it.
//   Import  pick a file, preview exactly what merging it would change, then
//           merge. Merge is upsert-only and never deletes; the preview is a
//           real server-side dry run, so the button stays disabled until the
//           document actually validates.
//
// The third card is documentation, not decoration: the three facts that
// otherwise turn into support questions — hashes restore working credentials
// but can never re-display them, the audit trail is export-only, and
// OAuth/WorkBuddy accounts ride in the keys section.

import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { DatabaseZap, Download, FileJson, ShieldAlert, Upload } from "lucide-react";

import {
  BACKUP_SECTIONS,
  DEFAULT_BACKUP_SECTIONS,
  datedFilename,
  downloadJson,
  exportBackup,
  importBackup,
  previewBackup,
} from "@/api/backup";
import type {
  BackupDocument,
  BackupImportResult,
  BackupPlan,
  BackupSections,
} from "@/api/types";
import { Alert, Badge, Button, Card, CardHeader, ErrorText, Field, Toggle } from "@/components/ui";

type Counts = BackupDocument["counts"] | undefined;

function countOf(counts: Counts, section: string): number | undefined {
  if (!counts) return undefined;
  const raw = (counts as Record<string, unknown>)[section];
  if (typeof raw === "number") return raw;
  if (raw && typeof raw === "object") {
    // `logs` and friends report per-table sub-counts.
    return Object.values(raw as Record<string, number>).reduce((a, b) => a + b, 0);
  }
  return undefined;
}

function plural(n: number, word: string): string {
  // Several section labels are already plural ("Usage rollups"), so a blind
  // "s" suffix rendered "900 usage rollupss".
  return `${n} ${n === 1 || word.endsWith("s") ? word : `${word}s`}`;
}

/** The two sections the server applies row-by-row, each row in its own
 *  SAVEPOINT, so each can land *partially*. Every other section is applied
 *  whole and answers with a plain count. */
const PARTIAL_SECTIONS: { id: BackupSections; row: string }[] = [
  { id: "rollups", row: "usage-rollup" },
  { id: "responses", row: "stored-response" },
];

/** Rows a section actually put on disk. Never `written + failed` — summing a
 *  per-row result reports a row the gateway rejected as though it had been
 *  restored, which is the exact lie the backend counts exist to prevent. */
function appliedOf(result: BackupImportResult, section: string): number | undefined {
  const raw = result[section as keyof BackupImportResult];
  if (typeof raw === "number") return raw;
  if (raw && typeof raw === "object") return raw.written;
  return undefined;
}

function failedOf(result: BackupImportResult, section: string): number {
  const raw = result[section as keyof BackupImportResult];
  return raw && typeof raw === "object" ? raw.failed : 0;
}

/** Rows an export left out because a cap bit, phrased for a human.
 *
 *  `truncated` is keyed by *table* (`request_logs`), not by section — the
 *  server records the denominator it counted, so the number is "dropped", not
 *  "total". */
function droppedRows(truncated: Record<string, number> | undefined): string[] {
  if (!truncated) return [];
  return Object.entries(truncated)
    .filter(([, n]) => n > 0)
    .map(([table, n]) => `${n.toLocaleString()} ${n === 1 ? "row" : "rows"} of ${table.replace(/_/g, " ")}`);
}

function SectionPicker(props: {
  selected: Set<BackupSections>;
  onChange: (next: Set<BackupSections>) => void;
  disabled?: boolean;
}) {
  const toggle = (id: BackupSections) => {
    const next = new Set(props.selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    props.onChange(next);
  };
  return (
    <div className="grid gap-1.5 sm:grid-cols-2">
      {BACKUP_SECTIONS.map((s) => (
        <label
          key={s.id}
          className="flex min-h-[44px] cursor-pointer items-center gap-2.5 rounded-[10px] border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2 text-[12px] text-[var(--admin-text)] transition-colors hover:border-[var(--admin-border-hover)] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-white/30"
        >
          <input
            type="checkbox"
            checked={props.selected.has(s.id)}
            disabled={props.disabled}
            onChange={() => toggle(s.id)}
            className="h-4 w-4 shrink-0 accent-blue-500"
          />
          <span className="min-w-0 flex-1">{s.label}</span>
          {s.bulk && (
            <span className="shrink-0 font-mono text-[10px] text-[var(--admin-text-dim)]">
              optional
            </span>
          )}
        </label>
      ))}
    </div>
  );
}

/** What a merge actually did.
 *
 *  `success` when every section landed whole. `warning` when one of the
 *  per-row sections lost rows to its SAVEPOINTs — the merge did succeed, so
 *  `danger` would be wrong, but reporting only the merged count would tell the
 *  operator a partial restore was a complete one. The rejected rows are named
 *  and counted, not summarised away. */
function ImportSummary(props: { result: BackupImportResult; onDismiss: () => void }) {
  const failures = describeFailures(props.result);
  const skipped = props.result.audit_skipped;
  const redacted = props.result.settings_skipped ?? 0;
  return (
    <Alert
      tone={failures.length ? "warning" : "success"}
      title={failures.length ? "Merged — some rows were rejected" : "Backup merged"}
      onDismiss={props.onDismiss}
    >
      <p>{`Merged ${describeResult(props.result)}.`}</p>
      {failures.length > 0 && (
        <>
          <p className="mt-1">
            Rejected by this gateway and not written, so this import is partial:{" "}
            <strong className="text-[var(--admin-text)]">
              {failures.map((f) => f.text).join(", ")}
            </strong>
            . Everything else landed in full — check the gateway logs for the
            reason before re-running the merge.
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {failures.map((f) => (
              <li key={f.id} className="flex items-center gap-1.5">
                <Badge tone="amber">{plural(f.failed, "row")} failed</Badge>
                <span className="text-[11px] text-[var(--admin-text-dim)]">{f.label}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {redacted > 0 && (
        <p className="mt-1">
          {plural(redacted, "setting")} in the file{" "}
          {redacted === 1 ? "carried a redacted placeholder and was" : "carried redacted placeholders and were"}{" "}
          left alone, so this gateway kept its own live records — export with
          plaintext secrets to move them.
        </p>
      )}
      {skipped > 0 && (
        <p className="mt-1">
          {plural(skipped, "audit record")} in the file{" "}
          {skipped === 1 ? "was" : "were"} deliberately not replayed — the audit
          trail stays export-only.
        </p>
      )}
    </Alert>
  );
}

export function BackupTab() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  // -- export state
  const [sections, setSections] = useState<Set<BackupSections>>(
    () => new Set(DEFAULT_BACKUP_SECTIONS),
  );
  const [includeSecrets, setIncludeSecrets] = useState(false);
  const [includeBodies, setIncludeBodies] = useState(false);
  const [logDays, setLogDays] = useState(30);

  // The sections that carry usage and stats, as opposed to configuration.
  // Called out in the hint because the default export omits them, and "the
  // file has no usage data in it" is the surprise this page exists to avoid.
  const hasUsageData = sections.has("logs") || sections.has("rollups");

  // -- import state
  const [picked, setPicked] = useState<{ name: string; doc: BackupDocument } | null>(null);
  const [plan, setPlan] = useState<BackupPlan | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "warning"; text: string } | null>(null);
  // The merge outcome is kept structured rather than flattened into `notice`:
  // a partial import has to be rendered in the warning tone *and* itemised, and
  // a string set at the call site cannot carry either.
  const [importResult, setImportResult] = useState<BackupImportResult | null>(null);

  const exportMutation = useMutation({
    mutationFn: () =>
      exportBackup({
        sections: [...sections],
        includeSecrets,
        includeBodies,
        logDays,
        logLimit: 0,
      }),
    onSuccess: (doc) => {
      downloadJson(datedFilename("wiwi-backup"), doc);
      setError(null);
      setImportResult(null);
      // `logLimit` is 0 above, so this cannot bite today — but if a cap is ever
      // wired to a control here, the operator has to hear about it from this
      // page too, not only from whoever opens the file.
      const capped = droppedRows(doc.truncated);
      setNotice({
        // A capped export is not a clean success, so it must not wear the green
        // tone: the operator has to notice that the file is partial.
        tone: capped.length ? "warning" : "success",
        text:
          `Downloaded a backup with ${describeCounts(doc.counts)}.` +
          (capped.length ? ` Left out by a row cap, so the file is incomplete: ${capped.join(", ")}.` : ""),
      });
    },
    onError: (e) => setError(e.message),
  });

  // The preview IS a validation: an unpreviewed document has not been checked,
  // so the Merge button stays disabled rather than firing a write on a file
  // the server would reject.
  const previewMutation = useMutation<BackupPlan>({
    mutationFn: () => previewBackup(picked!.doc, [...sections]),
    onSuccess: (p) => {
      setPlan(p);
      setError(null);
    },
    onError: (e) => {
      setPlan(null);
      setError(e.message);
    },
  });

  const importMutation = useMutation({
    mutationFn: () => importBackup(picked!.doc, [...sections]),
    onSuccess: async (r) => {
      setError(null);
      setNotice(null);
      setImportResult(r);
      setPicked(null);
      setPlan(null);
      // Every console surface reads from these keys; the gateway just changed
      // underneath all of them.
      await qc.invalidateQueries();
    },
    onError: (e) => setError(e.message),
  });

  const busy = exportMutation.isPending || previewMutation.isPending || importMutation.isPending;

  async function onPick(f: File) {
    setError(null);
    setNotice(null);
    setImportResult(null);
    setPlan(null);
    try {
      const doc = JSON.parse(await f.text()) as BackupDocument;
      if (doc?.kind !== "wiwi.backup") {
        setError(`"${f.name}" is not a wiwi backup file.`);
        setPicked(null);
        return;
      }
      setPicked({ name: f.name, doc });
    } catch (e) {
      setPicked(null);
      setError(`${f.name} is not valid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  const fileCounts = picked?.doc.counts;
  const fileDropped = droppedRows(picked?.doc.truncated);

  return (
    <div className="space-y-4">
      {error && <ErrorText>{error}</ErrorText>}
      {notice && (
        <Alert tone={notice.tone} onDismiss={() => setNotice(null)}>
          {notice.text}
        </Alert>
      )}
      {importResult && <ImportSummary result={importResult} onDismiss={() => setImportResult(null)} />}

      {/* -- export ---------------------------------------------------------- */}
      <Card>
        <CardHeader
          title="Export"
          subtitle="Download this gateway's configuration as one JSON file."
        />
        <div className="space-y-4 p-4 pt-0">
          {/* The quick-select buttons sit *beside* the `Field`, not inside it.
              `Field` renders its children inside a `<label>`, and a button
              nested in a label takes the label's whole text as its accessible
              name — every section name plus the hint, announced on focus. */}
          <div className="space-y-2">
            {/* The bulk sections hold the usage history and are off by
                default — a sound default, but it means the plain download
                carries no usage data at all, and the boxes that would add it
                are the four marked "optional". One click each way, so a file
                without the history is a choice rather than the default. */}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                className="min-h-[44px]"
                disabled={busy}
                onClick={() => {
                  setSections(new Set(BACKUP_SECTIONS.map((s) => s.id)));
                  // "Everything" has to mean it: the log window defaults to
                  // 30 days, and leaving it there would export a file that
                  // claims completeness while holding a slice.
                  setLogDays(0);
                }}
              >
                <DatabaseZap size={14} /> All data — usage &amp; stats
              </Button>
              <Button
                type="button"
                variant="outline"
                className="min-h-[44px]"
                disabled={busy}
                onClick={() => setSections(new Set(DEFAULT_BACKUP_SECTIONS))}
              >
                Configuration only
              </Button>
            </div>
            <Field
              label="Sections"
              hint={
                hasUsageData
                  ? "Usage and stats are included — the request log and usage rollups travel in this file."
                  : "Usage and stats are NOT in this file: they live in the request log and usage rollups sections, which are excluded by default because the log grows without limit."
              }
            >
              <SectionPicker selected={sections} onChange={setSections} disabled={busy} />
            </Field>
          </div>

          <div className="space-y-2.5 rounded-[10px] border border-[var(--admin-border)] bg-white/[0.015] p-3">
            <Toggle
              label="Include plaintext provider keys"
              checked={includeSecrets}
              onChange={setIncludeSecrets}
              disabled={busy}
            />
            <p className="text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
              {includeSecrets
                ? "The file will contain every provider key, and any connected Cline refresh token, in clear text. Treat it as a credential."
                : "Off. The file will list key labels and their weights but carry no secrets."}
            </p>
            <div className="h-px bg-[var(--admin-border)]" />
            <Toggle
              label="Include request and response bodies"
              checked={includeBodies}
              onChange={setIncludeBodies}
              disabled={busy || !sections.has("logs")}
            />
            <p className="text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
              {sections.has("logs")
                ? includeBodies
                  ? "The file will contain real user prompt and completion text. This is a separate opt-in — turning on secrets above does not enable it."
                  : "Off. Token counts, costs, and timings are included; prompt and completion text is not."
                : "Select the Request log section above to enable this."}
            </p>
          </div>

          {sections.has("logs") && (
            <Field label="Log window (days)" hint="0 exports all history. Older logs stay on the source gateway regardless.">
              <input
                type="number"
                min={0}
                value={logDays}
                onChange={(e) => setLogDays(Math.max(0, Number(e.target.value) || 0))}
                className="admin-input w-32"
              />
            </Field>
          )}

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={busy || sections.size === 0}
              onClick={() => exportMutation.mutate()}
            >
              <Download size={14} />{" "}
              {exportMutation.isPending ? "Preparing…" : "Download backup"}
            </Button>
          </div>
        </div>
      </Card>

      {/* -- import ---------------------------------------------------------- */}
      <Card>
        <CardHeader
          title="Import"
          subtitle="Merge a backup file into this gateway."
        />
        <div className="space-y-4 p-4 pt-0">
          <Alert tone="info" title="Merge only">
            Records are matched by name and updated; anything the file does not
            mention is left alone. Nothing is ever deleted, so this is safe to
            run twice. Only the sections you have selected above are merged —
            a section you did not select is not written, even when the file
            carries it.
          </Alert>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              <Upload size={14} /> Choose file…
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onPick(f);
                e.target.value = "";
              }}
            />
          </div>

          {picked && (
            <>
              <div className="rounded-[10px] border border-[var(--admin-border)] bg-white/[0.015] p-3">
                <div className="flex items-center gap-2">
                  <FileJson size={14} className="shrink-0 text-[var(--admin-text-dim)]" aria-hidden />
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-[var(--admin-text)]">
                    {picked.name}
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] text-[var(--admin-text-dim)]">
                  Exported {new Date(picked.doc.exported_at * 1000).toLocaleString()}
                  {picked.doc.include_secrets ? " · carries plaintext secrets" : " · no secrets"}
                  {picked.doc.include_bodies ? " · carries request/response bodies" : ""}
                </p>
                {fileDropped.length > 0 && (
                  <Alert
                    tone="warning"
                    title="This file is incomplete"
                    className="mt-2.5"
                  >
                    The export hit a row cap, so the counts below are not the
                    whole table. Left out of this file:{" "}
                    <strong className="break-words text-[var(--admin-text)]">
                      {fileDropped.join(", ")}
                    </strong>
                    . Merging restores only what the file carries — anything left
                    out exists solely on the gateway that exported this, so do
                    not read a complete history into a short count.
                  </Alert>
                )}
                <dl className="admin-dl mt-2.5">
                  {BACKUP_SECTIONS.filter((s) => countOf(fileCounts, s.id) !== undefined).map(
                    (s) => (
                      <div key={s.id} className="flex items-baseline justify-between gap-3">
                        <dt className="text-[12px] text-[var(--admin-text-dim)]">{s.label}</dt>
                        <dd className="font-mono text-[12px] text-[var(--admin-text)]">
                          {countOf(fileCounts, s.id)}
                        </dd>
                      </div>
                    ),
                  )}
                </dl>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => previewMutation.mutate()}
                >
                  {previewMutation.isPending ? "Checking…" : "Preview merge"}
                </Button>
                <Button
                  disabled={busy || !plan}
                  onClick={() => importMutation.mutate()}
                >
                  {importMutation.isPending ? "Merging…" : "Merge backup"}
                </Button>
              </div>

              {plan && (
                <Alert tone="success" title="Validated — nothing has been written yet">
                  Merging will update {describeCounts(plan.counts)}.
                </Alert>
              )}
            </>
          )}
        </div>
      </Card>

      {/* -- what a backup can and cannot carry ------------------------------- */}
      <Card>
        <CardHeader title="What a backup carries" />
        <div className="space-y-2.5 p-4 pt-0 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
          <p className="flex gap-2">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-400/70" aria-hidden />
            <span>
              <strong className="text-[var(--admin-text)]">Virtual keys and passwords travel as
              hashes.</strong> wiwi stores a SHA-256 digest of each virtual key, never the key
              itself. A backup restores that digest, so the same key you handed your tenant keeps
              working on the restored gateway — but no key can be printed again from a backup, and
              the file cannot be used to recover one.
            </span>
          </p>
          <p className="flex gap-2">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-400/70" aria-hidden />
            <span>
              <strong className="text-[var(--admin-text)]">The audit log is export-only.</strong>{" "}
              It is exported for your records but never merged back: it records what happened on
              <em> this</em> gateway, and replaying another instance's trail would forge your own
              operator history.
            </span>
          </p>
          <p className="flex gap-2">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-400/70" aria-hidden />
            <span>
              <strong className="text-[var(--admin-text)]">OAuth accounts need the secrets
              toggle.</strong>{" "}
              A connected Cline, WorkBuddy, or Hermes account keeps its access token on a
              provider key, and its refresh token in a gateway setting. Both are withheld
              unless plaintext secrets are included: without the toggle the file records that
              the account exists, but not the tokens — so a restore keeps your live accounts
              connected instead of overwriting them with empty records.
            </span>
          </p>
          <p className="flex gap-2">
            <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber-400/70" aria-hidden />
            <span>
              <strong className="text-[var(--admin-text)]">The master key is not in the
              backup.</strong>{" "}
              It lives in <code className="font-mono">wiwi.yaml</code> and the environment. Set{" "}
              <code className="font-mono">WIWI_MASTER_KEY</code> on the target separately, or it
              will refuse to start.
            </span>
          </p>
        </div>
      </Card>
    </div>
  );
}

function describeCounts(counts: BackupDocument["counts"]): string {
  const parts = BACKUP_SECTIONS.map((s) => {
    const n = countOf(counts, s.id);
    return n === undefined || n === 0 ? null : plural(n, s.label.toLowerCase());
  }).filter((p): p is string => p !== null);
  return parts.length ? parts.join(", ") : "nothing";
}

/** What a merge put on disk, in the same prose as `describeCounts` but read
 *  through `appliedOf` — the two per-row sections answer with `written`, never
 *  with the sum of written and failed. */
function describeResult(r: BackupImportResult): string {
  const parts = BACKUP_SECTIONS.map((s) => {
    const n = appliedOf(r, s.id);
    return n === undefined || n === 0 ? null : plural(n, s.label.toLowerCase());
  }).filter((p): p is string => p !== null);
  return parts.length ? parts.join(", ") : "nothing";
}

/** The per-row sections that lost rows to their SAVEPOINTs. */
function describeFailures(
  r: BackupImportResult,
): { id: BackupSections; label: string; text: string; failed: number }[] {
  return PARTIAL_SECTIONS.map((s) => ({
    id: s.id,
    label: BACKUP_SECTIONS.find((x) => x.id === s.id)?.label ?? s.id,
    text: plural(failedOf(r, s.id), `${s.row} row`),
    failed: failedOf(r, s.id),
  })).filter((f) => f.failed > 0);
}