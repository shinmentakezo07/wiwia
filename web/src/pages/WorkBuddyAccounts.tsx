// WorkBuddy — manage WorkBuddy/CodeBuddy accounts (one per pool key).
//
// This page lists every WorkBuddy pool key across all workbuddy providers:
//   • Log in — browser login against the CN or global WorkBuddy API. The
//     login URL is handed back by the upstream, never pinned here.
//   • Import — bulk-import account auth JSONs in the workbuddy2api auths/
//     file shape (nested {auth, account} objects). Each account becomes a
//     pool key; the provider is created on first import.
//   • Export — download all accounts (or one provider's) as a JSON array in
//     the same auths/ shape, for backup or re-import elsewhere.
//   • Refresh — rotate one account's access token on demand.
//   • Live expiry state — needs_refresh badge fed by the backend sweeper.
//
// The login machine and the account table live in ./workbuddy-shared so the
// always-visible Account login page (/console/login) drives the same flow
// instead of growing a second copy of it. This page keeps its modal wrapper —
// the flow itself is the shared part.

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LogIn, Upload } from "lucide-react";
import { getProviders, workbuddyAccounts, workbuddyExport, workbuddyImport } from "../api/client";
import type { WorkBuddyAuthFile, WorkBuddyLoginConnected } from "../api/types";
import { datedFilename, downloadJson } from "@/api/backup";
import {
  WorkBuddyAccountsTable,
  WorkBuddyLoginForm,
  useWorkBuddyLogin,
} from "@/pages/workbuddy-shared";
import {
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
} from "../components/ui";

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
        throw new Error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`, {
          cause: e,
        });
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

// -- login dialog ---------------------------------------------------------------
// WorkBuddy credentials are not static API keys: they are OAuth pairs whose
// refresh grant rotates, and a revoked account can only be revived by logging
// in again. Before this dialog the only way in was to log in somewhere else
// (the CodeBuddy plugin) and import the resulting auths/ JSON by hand — so a
// revoke left the operator with nothing to do inside wiwi.
//
// The flow itself (start → open the upstream-returned URL → poll → the account
// is saved with every detail) is `useWorkBuddyLogin` in ./workbuddy-shared,
// which the Account login page also drives. Only the modal chrome is here.

function LoginDialog(props: {
  open: boolean;
  onClose: () => void;
  onConnected: (provider: string, label: string) => void;
}) {
  const state = useWorkBuddyLogin({
    onConnected: (d: WorkBuddyLoginConnected) => props.onConnected(d.provider, d.key_label),
  });
  const reset = state.reset;

  // A reopened dialog must start from a blank form, not the previous session's
  // waiting state — the session it was polling is gone the moment it closes.
  useEffect(() => {
    if (!props.open) reset();
  }, [props.open, reset]);

  return (
    <Dialog open={props.open} title="Log in to WorkBuddy" onClose={props.onClose}>
      <WorkBuddyLoginForm state={state} onDismiss={props.onClose} />
    </Dialog>
  );
}

// -- page -------------------------------------------------------------------------

export function WorkBuddyAccountsPage() {
  const [importOpen, setImportOpen] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
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
      // The anchor was never attached to the document here, so Firefox
      // ignored the click and the export silently did nothing.
      downloadJson(datedFilename("workbuddy-auths"), d.accounts);
    },
    onError: (e) => setError(e.message),
  });

  return (
    <div>
      <PageHeader
        title="WorkBuddy"
        subtitle="WorkBuddy / CodeBuddy accounts — one pool key per account"
        right={
          <div className="flex flex-wrap gap-2">
            {/* Every control in this row meets the 44 px touch target: the
                shared `.admin-btn` padding lands at 36 px, so call sites raise
                it — the same `min-h-11` pattern `.admin-number-stepper` uses in
                styles.css. */}
            <Button className="min-h-11" onClick={() => setLoginOpen(true)}>
              <LogIn size={14} /> Log in
            </Button>
            <Button
              variant="outline"
              className="min-h-11"
              onClick={() => setImportOpen(true)}
            >
              <Upload size={14} /> Import accounts
            </Button>
            <Button
              variant="ghost"
              className="min-h-11"
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

      {accountsQ.isError || providersQ.isError ? (
        <Card className="mb-4">
          <div className="px-4 py-6">
            <ErrorText>
              Failed to load WorkBuddy data:{" "}
              {accountsQ.error?.message ?? providersQ.error?.message ?? "request failed"}
            </ErrorText>
          </div>
        </Card>
      ) : (
        <>
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
                  No WorkBuddy accounts yet. Log in with a WorkBuddy /
                  CodeBuddy account, or import auth JSONs from the plugin
                  (auths/ directory) to get started.
                </EmptyState>
              ) : (
                <WorkBuddyAccountsTable accounts={accounts} onError={setError} />
              )}
            </div>
          </Card>
        </>
      )}

      <LoginDialog
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        onConnected={(provider, label) =>
          setNotice(`Logged in to ${provider} as ${label}.`)
        }
      />

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
