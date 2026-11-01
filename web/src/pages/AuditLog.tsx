// AuditLog — the admin mutation trail. Every provider/key/model/user change
// the console makes lands here with an actor, an action, a target, and the
// diff the server recorded.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, ScrollText, Search } from "lucide-react";
import { getAuditLogs } from "@/api/client";
import type { AuditLogEntry } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Spinner,
  Table,
  TD,
} from "@/components/ui";
import { fmtDateTime } from "@/lib/format";

const LIMITS = [100, 200, 500, 1000] as const;

function diffSummary(diff: Record<string, unknown>): string {
  const entries = Object.entries(diff);
  if (entries.length === 0) return "—";
  return entries
    .slice(0, 3)
    .map(([key, value]) => {
      const shown =
        typeof value === "string"
          ? value
          : value === null
            ? "null"
            : typeof value === "object"
              ? Array.isArray(value)
                ? `[${value.length}]`
                : "{…}"
              : String(value);
      return `${key}=${shown}`;
    })
    .join(" · ");
}

function actionTone(action: string): "green" | "red" | "blue" | "amber" | "gray" {
  const verb = action.toLowerCase();
  if (verb.includes("delete") || verb.includes("remove") || verb.includes("disable")) return "red";
  if (verb.includes("create") || verb.includes("add") || verb.includes("generate")) return "green";
  if (verb.includes("update") || verb.includes("patch") || verb.includes("set")) return "blue";
  if (verb.includes("reveal") || verb.includes("export") || verb.includes("import")) return "amber";
  return "gray";
}

function AuditRow({ entry }: { entry: AuditLogEntry }) {
  const [open, setOpen] = useState(false);
  const hasDiff = Object.keys(entry.diff ?? {}).length > 0;

  return (
    <>
      <tr className="align-top">
        <TD className="whitespace-nowrap font-mono text-[12px] text-[var(--admin-text-dim)]">
          {fmtDateTime(entry.ts)}
        </TD>
        <TD className="whitespace-nowrap text-[13px] text-[var(--admin-text)]">{entry.actor || "—"}</TD>
        <TD>
          <Badge tone={actionTone(entry.action)}>{entry.action || "—"}</Badge>
        </TD>
        <TD className="text-[13px] text-[var(--admin-text-muted)]">{entry.target || "—"}</TD>
        <TD>
          {hasDiff ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex min-h-11 items-center gap-2 text-left font-mono text-[12px] text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
              aria-expanded={open}
            >
              <span className="max-w-[22rem] truncate">{diffSummary(entry.diff)}</span>
              <span aria-hidden className="text-[var(--admin-text-dim)]">{open ? "▲" : "▼"}</span>
            </button>
          ) : (
            <span className="text-[12px] text-[var(--admin-text-dim)]">—</span>
          )}
        </TD>
      </tr>
      {open && hasDiff && (
        <tr>
          <TD colSpan={5} className="bg-white/[0.02]">
            <pre className="admin-scroll max-h-64 overflow-auto rounded-lg border border-[var(--admin-border)] bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">
              {JSON.stringify(entry.diff, null, 2)}
            </pre>
          </TD>
        </tr>
      )}
    </>
  );
}

export function AuditLogPage() {
  const [limit, setLimit] = useState<number>(200);
  const [query, setQuery] = useState("");

  const auditQuery = useQuery({
    queryKey: ["audit-logs", limit],
    queryFn: () => getAuditLogs(limit),
    refetchInterval: 30_000,
  });

  const rows = useMemo(() => {
    const all = auditQuery.data?.logs ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter((entry) =>
      [entry.actor, entry.action, entry.target, JSON.stringify(entry.diff ?? {})]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [auditQuery.data, query]);

  return (
    <div>
      <PageHeader
        title="Audit Log"
        subtitle="admin mutations · newest first"
        right={
          <>
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              aria-label="Rows to load"
              className="admin-input h-11 w-28"
            >
              {LIMITS.map((n) => (
                <option key={n} value={n}>
                  {n} rows
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              onClick={() => void auditQuery.refetch()}
              disabled={auditQuery.isFetching}
            >
              <RefreshCw size={14} className={auditQuery.isFetching ? "animate-spin" : ""} />
              Refresh
            </Button>
          </>
        }
      />

      <Card className="p-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--admin-border)] px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
            />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="filter actor / action / target / diff…"
              className="w-full pl-9"
              aria-label="Filter audit rows"
            />
          </div>
          <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
            {rows.length} shown
          </span>
        </div>

        {auditQuery.isLoading ? (
          <div className="flex justify-center py-16">
            <Spinner />
          </div>
        ) : auditQuery.error ? (
          <div className="px-4 py-8">
            <ErrorText>{auditQuery.error.message}</ErrorText>
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={ScrollText} title={query ? "No matching rows" : "No audit entries yet"}>
            {query
              ? "Try a different actor, action, or target."
              : "Admin mutations — provider edits, key changes, user updates — appear here as soon as they happen."}
          </EmptyState>
        ) : (
          <Table head={["Time", "Actor", "Action", "Target", "Diff"]}>
            {rows.map((entry, i) => (
              <AuditRow key={`${entry.ts}-${i}`} entry={entry} />
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
