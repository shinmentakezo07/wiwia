// CSV text for the console's data exports.
//
// One builder, imported by every page that offers a download: Analytics and
// Usage both export the request log, and two copies of a header row are two
// files that drift apart the first time a column is added to one of them.

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
