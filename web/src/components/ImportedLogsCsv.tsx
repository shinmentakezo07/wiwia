// The import half of the log-CSV round trip.
//
// `logsToCsv` (`@/lib/csv`) writes the request log to a file; this picks such
// a file back up and hands its rows to the page, which merges them into the
// view it is already rendering. Two properties matter and are deliberate:
//
//   • Importing never writes to the gateway. Rows are held in page state for
//     as long as the tab is open and are gone on reload. An operator who
//     imports a file to *read* old history should not have to wonder whether
//     it mutated anything.
//   • The notice that appears afterwards says how many rows landed inside the
//     window the page is showing. Imported history is usually older than the
//     selected range, and a silently empty table would read as a broken
//     import rather than as a filter doing its job.

import { useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { FileUp, X } from "lucide-react";
import { Alert, Button } from "@/components/ui";
import type { AlertTone } from "@/components/ui";
import { fmtInt } from "@/lib/format";
import { logsFromCsv, mergeLogs } from "@/lib/csv";
import type { RequestLogEntry } from "@/api/types";

/** Guard against a browser-locking file: past this, parsing blocks the main
 *  thread long enough to look like a crash. A log export of any real gateway
 *  is orders of magnitude smaller. */
const MAX_IMPORT_BYTES = 32 * 1024 * 1024;

/** Imported rows plus whatever the last import attempt wants to say. */
export interface ImportedCsv {
  rows: RequestLogEntry[];
  note: string | null;
  tone: AlertTone;
  busy: boolean;
  /** Read the picked file and add its rows to `rows`. */
  importFile: (file: File) => Promise<void>;
  /** Drop every imported row and the notice. */
  clear: () => void;
}

export function useImportedCsv(): ImportedCsv {
  const [rows, setRows] = useState<RequestLogEntry[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [tone, setTone] = useState<AlertTone>("info");
  const [busy, setBusy] = useState(false);

  const clear = () => {
    setRows([]);
    setNote(null);
  };

  const importFile = async (file: File) => {
    setBusy(true);
    try {
      if (file.size > MAX_IMPORT_BYTES) {
        setTone("danger");
        setNote(
          `${file.name} is ${fmtInt(Math.round(file.size / (1024 * 1024)))} MB — too ` +
            `large to parse in the browser. Split it, or export a narrower time range.`,
        );
        return;
      }
      const parsed = logsFromCsv(await file.text());
      // Appending, not replacing: an operator stitching together two exports
      // should not have to re-pick the first one. `mergeLogs` drops the rows
      // a previous file already contributed.
      const merged = mergeLogs(rows, parsed.rows);
      const added = merged.length - rows.length;
      setRows(merged);
      setTone(parsed.skipped > 0 ? "warning" : "info");
      setNote(
        added === 0
          ? `Nothing new in ${file.name} — every row was already imported.`
          : `Read ${fmtInt(added)} row${added === 1 ? "" : "s"} from ${file.name}` +
            (parsed.skipped > 0
              ? `; skipped ${fmtInt(parsed.skipped)} row${
                  parsed.skipped === 1 ? "" : "s"
                } with no readable \`time\`.`
              : "."),
      );
    } catch (e) {
      setTone("danger");
      setNote(`Import failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return { rows, note, tone, busy, importFile, clear };
}

/** The "Import CSV" control. Sits next to the matching Export button; the
 *  `<input>` stays hidden and is driven by the button so the tap target is a
 *  real 44px-tall button rather than a bare file input. */
export function ImportCsvButton(props: { csv: ImportedCsv }) {
  const { csv } = props;
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button
        variant="outline"
        // 44px tap target on phones; the console's default 36px button is fine
        // for a mouse and short for a thumb.
        className="min-h-11 sm:min-h-0"
        disabled={csv.busy}
        onClick={() => fileRef.current?.click()}
      >
        <FileUp size={14} /> {csv.busy ? "Reading…" : "Import CSV"}
      </Button>
      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          const f = e.target.files?.[0];
          if (f) void csv.importFile(f);
          e.target.value = ""; // re-picking the same file must re-fire
        }}
      />
    </>
  );
}

/** What the import did, and the way back out of it.
 *
 *  `scopeNote` is supplied by the page when its own filters hide part of the
 *  imported set — e.g. Usage's range selector — so the count on screen is never
 *  silently smaller than the count in the file. */
export function ImportedCsvNotice(props: {
  csv: ImportedCsv;
  scopeNote?: string | null;
}) {
  const { csv, scopeNote } = props;
  if (!csv.note && csv.rows.length === 0) return null;

  const n = csv.rows.length;
  return (
    <Alert
      tone={n > 0 ? "info" : csv.tone}
      action={
        n > 0 ? (
          <Button variant="ghost" className="min-h-11 sm:min-h-0" onClick={csv.clear}>
            <X size={13} /> Clear imported rows
          </Button>
        ) : null
      }
    >
      {n > 0 && (
        <div>
          <span className="font-medium">
            {fmtInt(n)} imported row{n === 1 ? "" : "s"}
          </span>{" "}
          are merged into this view. Nothing was written to the gateway — imported rows
          live in this tab only, and a row the server still holds is not counted twice.
          {scopeNote && <span className="mt-1 block opacity-80">{scopeNote}</span>}
        </div>
      )}
      {csv.note && (n > 0 ? <div className="mt-1 opacity-80">{csv.note}</div> : null)}
    </Alert>
  );
}