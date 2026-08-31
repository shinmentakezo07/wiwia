// WorkBuddy — manage WorkBuddy/CodeBuddy accounts (one per pool key).
//
// This page lists every WorkBuddy pool key across all workbuddy providers:
//   • Import — bulk-import account auth JSONs in the workbuddy2api auths/
//     file shape (nested {auth, account} objects). Each account becomes a
//     pool key; the provider is created on first import.
//   • Export — download all accounts (or one provider's) as a JSON array in
//     the same auths/ shape, for backup or re-import elsewhere.
//   • Refresh — rotate one account's access token on demand.
//   • Live expiry state — needs_refresh badge fed by the backend sweeper.

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw, Upload } from "lucide-react";
import {
  getProviders,
  workbuddyAccounts,
  workbuddyExport,
  workbuddyImport,
  workbuddyRefresh,
} from "../api/client";
import type { WorkBuddyAccount, WorkBuddyAuthFile } from "../api/types";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  Spinner,
  Table,
  TD,
} from "../components/ui";

function _fmtExpiry(expiresAt: number | null | undefined): string {
  if (!expiresAt) return "unknown";
  const days = (expiresAt * 1000 - Date.now()) / 86_400_000;
  const rel =
    days >= 1 ? `${Math.floor(days)}d` : `${Math.max(0, Math.floor(days * 24))}h`;
  return `${new Date(expiresAt * 1000).toLocaleDateString()} (${rel})`;
}

function _regionBadge(region: WorkBuddyAccount["region"]) {
  if (region === "global") return <Badge tone="violet">global</Badge>;
  return <Badge tone="blue">cn</Badge>;
}

// -- import dialog --------------------------------------------------------------
// Accepts a JSON array of auth objects (a concatenated auths/ dump) or a
// single auth object (one file). Files picked from disk are merged into the
// textarea so the admin sees exactly what will be imported; the backend
// re-validates every entry and rejects the batch atomically on any error.

function ImportAccountsDialog(props: {
  open: boolean;
  onClose: () => void;
  onImported: (provider: string, count: number) => void;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [provider, setProvider] = useState("workbuddy-main");
  const [raw, setRaw] = useState("");
  const [importError, setImportError] = useState<string | null>(null);

  const doImport = useMutation({
    mutationFn: () => {
      let parsed: WorkBuddyAuthFile | WorkBuddyAuthFile[];
      try {
        const value = JSON.parse(raw) as unknown;
        if (Array.isArray(value)) {
          parsed = value as WorkBuddyAuthFile[];
        } else if (value !== null && typeof value === "object" && "auth" in value) {
          parsed = value as WorkBuddyAuthFile;
        } else {
          throw new Error("expected an auth object ({auth, account}) or an array of them");
        }
      } catch (e) {
        throw new Error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      }
      return workbuddyImport(provider.trim() || "workbuddy-main", parsed);
    },
    onSuccess: (d) => {
      setRaw("");
      setImportError(null);
      void qc.invalidateQueries({ queryKey: ["workbuddy-accounts"] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
      props.onClose();
      props.onImported(d.provider, d.imported);
    },
    onError: (e) => setImportError(e.message),
  });

  const readFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const merged: WorkBuddyAuthFile[] = [];
    try {
      for (const f of Array.from(files)) {
        const value = JSON.parse(await f.text()) as unknown;
        if (Array.isArray(value)) {
          merged.push(...(value as WorkBuddyAuthFile[]));
        } else {
          merged.push(value as WorkBuddyAuthFile);
        }
      }
    } catch (e) {
      setImportError(`Could not read file(s): ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setRaw(JSON.stringify(merged, null, 2));
    setImportError(null);
  };

  return (
    <Dialog open={props.open} title="Import WorkBuddy accounts" onClose={props.onClose}>
      <p className="text-[13px] text-[var(--admin-text-muted)]">
        Import WorkBuddy auth JSONs — the files produced by the CodeBuddy
        plugin / CPA panel (the workbuddy2api <code>auths/</code> shape).
        Each account becomes a pool key on the provider below; the provider
        is created on first import.
      </p>
      <div className="mt-4 space-y-3">
        <Field label="Provider name">
          <Input
            value={provider}
            placeholder="workbuddy-main"
            onChange={(e) => setProvider(e.target.value)}
          />
        </Field>
        <Field label="Auth JSON" hint="single object or array — [{auth, account}, …]">
          <textarea
            className="admin-input min-h-40 resize-y font-mono text-[12px]"
            placeholder='[{"auth": {"accessToken": "…", "refreshToken": "…", "expiresAt": 1786000000, "domain": "https://www.workbuddy.ai"}, "account": {"uid": "…", "enterpriseId": "", "nickname": "…"}}]'
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
        <div className="flex items-center justify-between">
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => {
              void readFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <Button variant="ghost" onClick={() => fileRef.current?.click()}>
            <Upload size={14} /> Choose auths/ files…
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={props.onClose}>
              Cancel
            </Button>
            <Button
              disabled={!raw.trim() || doImport.isPending}
              onClick={() => doImport.mutate()}
            >
              {doImport.isPending ? "Importing…" : "Import"}
            </Button>
          </div>
        </div>
        {importError && <ErrorText>{importError}</ErrorText>}
      </div>
    </Dialog>
  );
}

// -- accounts table --------------------------------------------------------------

function AccountsTable(props: {
  accounts: WorkBuddyAccount[];
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState<string | null>(null);

  const refresh = useMutation({
    mutationFn: (a: WorkBuddyAccount) => workbuddyRefresh(a.provider, a.label),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workbuddy-accounts"] });
      void qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const refreshOne = (a: WorkBuddyAccount) => {
    setRefreshing(`${a.provider}/${a.label}`);
    refresh.mutate(a, {
      onSettled: () => setRefreshing(null),
    });
  };

  return (
    <Table head={["Account", "Provider", "Region", "Token", "Expires", "Status", ""]}>
      {props.accounts.map((a) => (
        <tr key={`${a.provider}/${a.label}`}>
          <TD>
            <div className="font-medium">{a.nickname || a.label}</div>
            <div className="text-[11px] text-[var(--admin-text-muted)]">
              {a.uid || "—"} · {a.label}
            </div>
          </TD>
          <TD>{a.provider}</TD>
          <TD>{_regionBadge(a.region)}</TD>
          <TD>
            <code className="text-[11px] text-[var(--admin-text-muted)]">
              {a.access_token_masked ?? "—"}
            </code>
          </TD>
          <TD>{_fmtExpiry(a.expires_at)}</TD>
          <TD>
            {!a.valid_auth ? (
              <Badge tone="red">invalid auth</Badge>
            ) : a.needs_refresh ? (
              <Badge tone="amber">needs refresh</Badge>
            ) : (
              <Badge tone="green">valid</Badge>
            )}
            {a.enabled === false && <Badge tone="gray">disabled</Badge>}
          </TD>
          <TD>
            <Button
              variant="ghost"
              disabled={!a.has_refresh_token || refreshing !== null}
              onClick={() => refreshOne(a)}
            >
              <RefreshCw
                size={13}
                className={refreshing === `${a.provider}/${a.label}` ? "animate-spin" : ""}
              />
              Refresh
            </Button>
          </TD>
        </tr>
      ))}
    </Table>
  );
}

// -- page -------------------------------------------------------------------------

export function WorkBuddyAccountsPage() {
  const [importOpen, setImportOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const accountsQ = useQuery({
    queryKey: ["workbuddy-accounts"],
    queryFn: workbuddyAccounts,
    refetchInterval: 15_000,
  });
  const providersQ = useQuery({
    queryKey: ["providers"],
    queryFn: getProviders,
    refetchInterval: 15_000,
  });

  const accounts = accountsQ.data?.accounts ?? [];
  const workbuddyProviders = (providersQ.data?.providers ?? []).filter(
    (p) => p.provider_type === "workbuddy",
  );
  const dueCount = accounts.filter((a) => a.needs_refresh).length;

  const doExport = useMutation({
    mutationFn: () => workbuddyExport(),
    onSuccess: (d) => {
      const blob = new Blob([JSON.stringify(d.accounts, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `workbuddy-auths-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (e) => setError(e.message),
  });

  return (
    <div>
      <PageHeader
        title="WorkBuddy"
        subtitle="WorkBuddy / CodeBuddy accounts — one pool key per account"
        right={
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <Upload size={14} /> Import accounts
            </Button>
            <Button
              variant="ghost"
              disabled={doExport.isPending || accounts.length === 0}
              onClick={() => doExport.mutate()}
            >
              <Download size={14} />
              {doExport.isPending ? "Exporting…" : "Export JSON"}
            </Button>
          </div>
        }
      />
      {error && <div className="mb-3"><ErrorText>{error}</ErrorText></div>}
      {notice && (
        <div className="mb-3 text-[12px] text-[var(--admin-text-muted)]">{notice}</div>
      )}

      <div className="mb-4 grid grid-cols-3 gap-4">
        <Card>
          <div className="px-4 py-3">
            <div className="text-[10px] uppercase tracking-wide text-[var(--admin-text-muted)]">
              Accounts
            </div>
            <div className="mt-1 text-xl font-semibold">{accounts.length}</div>
          </div>
        </Card>
        <Card>
          <div className="px-4 py-3">
            <div className="text-[10px] uppercase tracking-wide text-[var(--admin-text-muted)]">
              Providers
            </div>
            <div className="mt-1 text-xl font-semibold">{workbuddyProviders.length}</div>
          </div>
        </Card>
        <Card>
          <div className="px-4 py-3">
            <div className="text-[10px] uppercase tracking-wide text-[var(--admin-text-muted)]">
              Need refresh
            </div>
            <div className="mt-1 text-xl font-semibold">
              {dueCount}
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Connected accounts"
          subtitle="auths/ JSON format — export to back up, import to add"
        />
        <div className="px-4 pb-4 pt-2">
          {accountsQ.isLoading ? (
            <Spinner />
          ) : accounts.length === 0 ? (
            <EmptyState>
              No WorkBuddy accounts yet. Import auth JSONs from the CodeBuddy
              plugin (auths/ directory) to get started.
            </EmptyState>
          ) : (
            <AccountsTable accounts={accounts} onError={setError} />
          )}
        </div>
      </Card>

      <ImportAccountsDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={(provider, count) =>
          setNotice(`Imported ${count} account${count !== 1 ? "s" : ""} into ${provider}.`)
        }
      />
    </div>
  );
}
