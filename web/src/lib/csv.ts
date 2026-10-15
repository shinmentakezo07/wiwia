// CSV text for the console's data exports — and the reader that goes with it.
//
// One builder, imported by every page that offers a download: Analytics and
// Usage both export the request log, and two copies of a header row are two
// files that drift apart the first time a column is added to one of them.
//
// The reader closes the loop: a file this module wrote can be read back by
// this module, so "Export CSV" is not a one-way door. Reading is column-driven
// (matched by header name, not by position) so a file that has been through a
// spreadsheet — reordered columns, extra columns — still imports.

import type { RequestLogEntry } from "@/api/types";

/** Quote a cell only when it has to be — commas, quotes, newlines. */
function csvEscape(v: string | number): string {
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

/** One row per request, in the order passed (the API serves newest-first).
 *
 *  Headers are the raw field names the API returns rather than prose labels:
 *  these files are read by spreadsheets and scripts, and a stable machine
 *  name survives a rename of the display label.
 *
 *  `cached` is a *subset* of `tok_in` and `reasoning` a subset of `tok_out`,
 *  not additional tokens — the same accounting `_playground_metrics` documents
 *  for its `total_tokens` — so the columns do not sum to a row total. */
export function logsToCsv(logs: RequestLogEntry[]): string {
  const header = [
    "time", "key", "model", "provider", "status",
    "tok_in", "cached", "reasoning", "out",
    "tps", "ttft_ms", "latency_ms", "cost",
  ];
  const lines = [header.join(",")];
  for (const l of logs) {
    lines.push(
      [
        new Date(l.ts * 1000).toISOString(),
        l.key_alias, l.model_group, l.provider, l.status,
        l.tok_in, l.tok_cached, l.tok_reasoning, l.tok_out,
        l.tps, l.ttft_ms, l.latency_ms, l.cost,
      ].map(csvEscape).join(","),
    );
  }
  return lines.join("\n");
}

// ── reading a log CSV back ───────────────────────────────────────────────

/** Split CSV text into rows of cells (RFC 4180).
 *
 *  Handles quoted cells containing commas, newlines and doubled quotes, plus
 *  CRLF endings and a leading BOM — the three things a file picks up on its
 *  way through a spreadsheet or a Windows editor. */
export function parseCsv(text: string): string[][] {
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;

  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (quoted) {
      if (ch !== '"') {
        cell += ch;
      } else if (src[i + 1] === '"') {
        cell += '"';
        i++;
      } else {
        quoted = false;
      }
      continue;
    }
    if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (ch !== "\r") {
      cell += ch;
    }
  }
  if (cell !== "" || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

/** What `logsFromCsv` managed to read. */
export interface CsvLogImport {
  rows: RequestLogEntry[];
  /** Data rows dropped because they carried no usable `time`. */
  skipped: number;
}

/** A blank or unparseable number reads as 0. */
function cellNum(v: string): number {
  const n = Number(v.trim());
  return Number.isFinite(n) ? n : 0;
}

/** `time` column → epoch seconds. Accepts ISO 8601 (what we write), bare
 *  epoch seconds, and epoch milliseconds; anything else is unusable. */
function cellTs(v: string): number | null {
  const s = v.trim();
  if (!s) return null;
  if (/^\d+$/.test(s)) {
    const n = Number(s);
    return n > 1e12 ? Math.floor(n / 1000) : n;
  }
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** Read a request-log CSV back into entries the log pages can render.
 *
 *  The round trip is **lossy by design**: a log entry carries ~30 fields
 *  (request id, error text, per-attempt routing, cache flags) and the export
 *  carries 13 of them. An imported row therefore has blank values for the
 *  fields the file does not carry — it is good for counts, tokens, spend,
 *  latency and status, which is what these pages chart. `cache_hit` stays
 *  false and `cache_savings` stays 0 rather than being guessed.
 *
 *  Throws only when the file is not a log export at all (no `time` column);
 *  individual bad rows are counted in `skipped` instead of failing the file. */
export function logsFromCsv(text: string): CsvLogImport {
  const table = parseCsv(text);
  if (table.length === 0) throw new Error("That file is empty.");

  const header = (table[0] ?? []).map((h) => h.trim().toLowerCase());
  const idx = (name: string) => header.indexOf(name);
  if (idx("time") < 0) {
    throw new Error(
      "Not a wiwi log export — its header has no `time` column. " +
        "Export CSV from Usage or Analytics, then import that file.",
    );
  }

  const time = idx("time");
  const key = idx("key");
  const model = idx("model");
  const provider = idx("provider");
  const status = idx("status");
  const tokIn = idx("tok_in");
  const cached = idx("cached");
  const reasoning = idx("reasoning");
  const out = idx("out");
  const tps = idx("tps");
  const ttft = idx("ttft_ms");
  const latency = idx("latency_ms");
  const cost = idx("cost");

  const rows: RequestLogEntry[] = [];
  let skipped = 0;

  for (let i = 1; i < table.length; i++) {
    const cells = table[i] ?? [];
    if (cells.every((c) => c.trim() === "")) continue; // blank line
    const at = (n: number) => (n < 0 ? "" : (cells[n] ?? ""));

    const ts = cellTs(at(time));
    if (ts === null) {
      skipped++;
      continue;
    }

    // A blank status is a spreadsheet edit, not a 0 response; read it as a
    // success so the row does not show up in the error mix as "status 0".
    const rawStatus = at(status).trim();
    const parsedStatus = rawStatus === "" ? NaN : Number(rawStatus);

    rows.push({
      stream: "request",
      ts,
      request_id: "",
      surface: "",
      key_alias: at(key),
      key_id: "",
      model_group: at(model),
      provider: at(provider),
      provider_key_label: "",
      status: Number.isFinite(parsedStatus) ? Math.round(parsedStatus) : 200,
      error_code: "",
      error_message: "",
      tok_in: cellNum(at(tokIn)),
      tok_cached: cellNum(at(cached)),
      tok_cache_creation: 0,
      tok_reasoning: cellNum(at(reasoning)),
      tok_out: cellNum(at(out)),
      usage_estimated: false,
      tps: cellNum(at(tps)),
      ttft_ms: cellNum(at(ttft)),
      latency_ms: cellNum(at(latency)),
      cost: cellNum(at(cost)),
      was_stream: false,
      cache_hit: false,
      cache_savings: 0,
      response_cache_hit: false,
      translation_warnings: [],
      attempts: [],
    });
  }

  return { rows, skipped };
}

/** Identity of a request as far as a CSV file can express it: the second it
 *  happened, the dimensions the file carries, and its measured volume.
 *
 *  The volume fields are in the key on purpose. A file records whole seconds,
 *  so on a fast gateway two genuinely different requests can share a second, a
 *  key, a model, a provider and a status; keying on those five alone would
 *  silently collapse that pair into one row. Two rows that agree on all of it
 *  — token counts, latency and cost included — are the same request as far as
 *  this file can tell, which is what makes re-importing a file idempotent. */
function rowIdentity(l: RequestLogEntry): string {
  return [
    l.ts, l.key_alias, l.model_group, l.provider, l.status,
    l.tok_in, l.tok_cached, l.tok_reasoning, l.tok_out,
    l.tps, l.ttft_ms, l.latency_ms, l.cost,
  ].join("|");
}

/** Fold imported rows into a live log list without double-counting.
 *
 *  `base` wins a collision: a server row carries the fields the file dropped,
 *  so it is strictly the better copy of the same request. The result keeps
 *  `base`'s order and appends only the rows the server no longer has — which
 *  is the point of importing, to read history that has aged out of the ring. */
export function mergeLogs(
  base: RequestLogEntry[],
  extra: RequestLogEntry[],
): RequestLogEntry[] {
  if (extra.length === 0) return base;
  const seen = new Set(base.map(rowIdentity));
  const added = extra.filter((l) => !seen.has(rowIdentity(l)));
  return added.length > 0 ? [...base, ...added] : base;
}