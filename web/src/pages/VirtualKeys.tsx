// VirtualKeys page — issue and manage client credentials: budgets, rate limits,
// model allowlists, expiry. Generated plaintext keys are revealed exactly once.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { deleteKey, disableKey, generateKey, listKeys, patchKey } from "@/api/client";
import type { VirtualKey } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  CopyButton,
  Dialog,
  EmptyState,
  ErrorText,
  Field,
  Input,
  PageHeader,
  ProgressBar,
  Select,
  Spinner,
  Table,
  TD,
} from "@/components/ui";
import { fmtDateTime, fmtInt, fmtUsd } from "@/lib/format";

/** "" → null (means "leave unchanged"); unparsable → NaN; otherwise the number. */
function tryParse(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  return Number.isFinite(Number(t)) ? Number(t) : NaN;
}

function numbersValid(ns: (number | null)[]): boolean {
  return ns.every((n) => n === null || !Number.isNaN(n));
}

/** "a, b,,c" → ["a","b","c"]; "" → [] (= all models). */
function parseCsv(s: string): string[] {
  return s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

type KeyStatus = { label: "active" | "expired" | "disabled"; tone: "green" | "amber" | "gray" };

function keyStatus(k: VirtualKey): KeyStatus {
  if (k.disabled) return { label: "disabled", tone: "gray" };
  if (k.expires_at != null && k.expires_at * 1000 < Date.now()) {
    return { label: "expired", tone: "amber" };
  }
  return { label: "active", tone: "green" };
}

function BudgetCell(props: { k: VirtualKey }) {
  const { k } = props;
  return (
    <div className="w-40 space-y-1">
      {k.max_budget != null && (
        <ProgressBar value={k.max_budget > 0 ? k.spend_to_date / k.max_budget : 1} />
      )}
      <span className="font-mono text-[12px] tabular-nums text-[var(--admin-text)]">
        {fmtUsd(k.spend_to_date)}
        {k.max_budget != null && (
          <span className="text-[var(--admin-text-dim)]"> / {fmtUsd(k.max_budget)}</span>
        )}
      </span>
    </div>
  );
}

function KeyRow(props: { k: VirtualKey; onEdit: (k: VirtualKey) => void; onError: (m: string) => void }) {
  const qc = useQueryClient();
  const disable = useMutation({
    mutationFn: () => disableKey(props.k.id, !props.k.disabled),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["keys"] }),
    onError: (e) => props.onError(e.message),
  });
  const revoke = useMutation({
    mutationFn: () => deleteKey(props.k.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["keys"] }),
    onError: (e) => props.onError(e.message),
  });
  const status = keyStatus(props.k);
  return (
    <tr>
      <TD className="font-medium">{props.k.alias}</TD>
      <TD>
        <Badge tone={status.tone}>{status.label}</Badge>
      </TD>
      <TD>
        {props.k.models.length === 0 ? (
          <span className="text-[var(--admin-text-dim)]" title="No allowlist: every model group is reachable">
            all
          </span>
        ) : (
          <span className="flex flex-wrap gap-1">
            {props.k.models.map((m) => (
              <Badge key={m} tone="blue">
                {m}
              </Badge>
            ))}
          </span>
        )}
      </TD>
      <TD>
        <BudgetCell k={props.k} />
      </TD>
      <TD className="font-mono tabular-nums">{props.k.rpm != null ? fmtInt(props.k.rpm) : "—"}</TD>
      <TD className="font-mono tabular-nums">{props.k.tpm != null ? fmtInt(props.k.tpm) : "—"}</TD>
      <TD className="font-mono text-[12px] text-[var(--admin-text-dim)]">
        {props.k.expires_at != null ? fmtDateTime(props.k.expires_at) : "—"}
      </TD>
      <TD>
        <div className="flex justify-end gap-1.5">
          <Button variant="outline" onClick={() => props.onEdit(props.k)}>
            Edit
          </Button>
          <Button variant="outline" disabled={disable.isPending} onClick={() => disable.mutate()}>
            {props.k.disabled ? "Enable" : "Disable"}
          </Button>
          <Button
            variant="danger"
            disabled={revoke.isPending}
            onClick={() => {
              if (window.confirm(`Revoke key "${props.k.alias}"? Clients using it will stop working. This cannot be undone.`)) {
                revoke.mutate();
              }
            }}
          >
            Revoke
          </Button>
        </div>
      </TD>
    </tr>
  );
}

export function VirtualKeysPage() {
  const qc = useQueryClient();

  // -- list -------------------------------------------------------------------
  const [pageError, setPageError] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["keys"], queryFn: listKeys, refetchInterval: 15_000 });

  // -- create dialog ------------------------------------------------------------
  const [createOpen, setCreateOpen] = useState(false);
  const [created, setCreated] = useState<{ key: string } | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [authMode, setAuthMode] = useState<"random" | "custom">("random");
  const [customKey, setCustomKey] = useState("");
  const [modelsCsv, setModelsCsv] = useState("");
  const [budget, setBudget] = useState("");
  const [rpm, setRpm] = useState("");
  const [tpm, setTpm] = useState("");
  const [ttlHours, setTtlHours] = useState("");

  const budgetN = tryParse(budget);
  const rpmN = tryParse(rpm);
  const tpmN = tryParse(tpm);
  const ttlHN = tryParse(ttlHours);
  const numsOk = numbersValid([budgetN, rpmN, tpmN, ttlHN]);
  const customTooShort = authMode === "custom" && customKey.trim().length < 16;

  function openCreate() {
    setName("");
    setAuthMode("random");
    setCustomKey("");
    setModelsCsv("");
    setBudget("");
    setRpm("");
    setTpm("");
    setTtlHours("");
    setCreated(null);
    setCreateError(null);
    setCreateOpen(true);
  }

  function closeCreate() {
    setCreateOpen(false);
    setCreated(null);
  }

  const create = useMutation({
    mutationFn: (body: Parameters<typeof generateKey>[0]) => generateKey(body),
    onSuccess: (data) => setCreated({ key: data.key }),
    onError: (e) => setCreateError(e.message),
  });

  // -- edit dialog --------------------------------------------------------------
  const [editTarget, setEditTarget] = useState<VirtualKey | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [editBudget, setEditBudget] = useState("");
  const [editRpm, setEditRpm] = useState("");
  const [editTpm, setEditTpm] = useState("");
  const [editModels, setEditModels] = useState("");
  const [clearExpiry, setClearExpiry] = useState(false);

  const editBudgetN = tryParse(editBudget);
  const editRpmN = tryParse(editRpm);
  const editTpmN = tryParse(editTpm);
  const editNumsOk = numbersValid([editBudgetN, editRpmN, editTpmN]);

  function openEdit(k: VirtualKey) {
    setEditTarget(k);
    setEditBudget(k.max_budget != null ? String(k.max_budget) : "");
    setEditRpm(k.rpm != null ? String(k.rpm) : "");
    setEditTpm(k.tpm != null ? String(k.tpm) : "");
    setEditModels(k.models.join(", "));
    setClearExpiry(false);
    setEditError(null);
  }

  function closeEdit() {
    setEditTarget(null);
    setEditError(null);
  }

  const editSave = useMutation({
    mutationFn: (args: { id: string; patch: Parameters<typeof patchKey>[1] }) =>
      patchKey(args.id, args.patch),
    onSuccess: () => {
      closeEdit();
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => setEditError(e.message),
  });

  return (
    <div>
      <PageHeader
        title="Virtual Keys"
        subtitle="Client credentials callers authenticate with — per-key budgets, rate limits, and model access."
        right={
          <Button onClick={openCreate}>
            <Plus size={14} /> New key
          </Button>
        }
      />

      {pageError && (
        <div className="mb-3">
          <ErrorText>{pageError}</ErrorText>
        </div>
      )}

      <Card>
        {query.isLoading && (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        )}
        {query.error && (
          <div className="p-4">
            <ErrorText>{query.error.message}</ErrorText>
          </div>
        )}
        {query.data &&
          (query.data.keys.length === 0 ? (
            <EmptyState>No virtual keys yet. Issue one with “New key”.</EmptyState>
          ) : (
            <Table
              head={["Name", "Status", "Models", "Budget", "RPM", "TPM", "Expires", ""]}
            >
              {query.data.keys.map((k) => (
                <KeyRow key={k.id} k={k} onEdit={openEdit} onError={setPageError} />
              ))}
            </Table>
          ))}
      </Card>

      {/* -- create / reveal-once ------------------------------------------- */}
      <Dialog
        open={createOpen}
        title={created ? "Key created" : "New virtual key"}
        onClose={closeCreate}
      >
        {created ? (
          <div className="space-y-3">
            <p className="text-[13px] text-[var(--admin-text-muted)]">
              Copy your new key now — <strong>you won&apos;t see this again</strong>.
            </p>
            <div className="rounded-[12px] border border-blue-500/15 bg-blue-500/[0.04] p-4">
              <p className="break-all font-mono text-[15px] tracking-wide text-blue-300">
                {created.key}
              </p>
            </div>
            <p className="text-[12px] text-amber-400/70">
              This is the only time the plaintext is shown. Store it somewhere safe.
            </p>
            <div className="flex items-center justify-between pt-2">
              <CopyButton text={created.key} />
              <Button
                onClick={() => {
                  closeCreate();
                  void qc.invalidateQueries({ queryKey: ["keys"] });
                }}
              >
                Done
              </Button>
            </div>
          </div>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim() || !numsOk || customTooShort) return;
              setCreateError(null);
              const models = parseCsv(modelsCsv);
              create.mutate({
                name: name.trim(),
                custom_key: authMode === "custom" ? customKey.trim() : undefined,
                ...(models.length > 0 ? { models } : {}),
                max_budget: budgetN ?? undefined,
                rpm: rpmN ?? undefined,
                tpm: tpmN ?? undefined,
                ttl_seconds: ttlHN != null ? Math.round(ttlHN * 3600) : undefined,
              });
            }}
          >
            <Field label="Name">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ci-pipeline"
                autoFocus
              />
            </Field>
            <Field label="Key source">
              <Select
                value={authMode}
                onChange={(v) => setAuthMode(v === "custom" ? "custom" : "random")}
                options={[
                  { value: "random", label: "Generate random key" },
                  { value: "custom", label: "Bring your own key" },
                ]}
              />
            </Field>
            {authMode === "custom" && (
              <Field label="Custom key" hint="At least 16 characters.">
                <Input
                  value={customKey}
                  onChange={(e) => setCustomKey(e.target.value)}
                  placeholder="sk-my-own-value…"
                  className="font-mono"
                />
              </Field>
            )}
            <Field label="Model allowlist" hint="Comma-separated model names; empty = all models.">
              <Input
                value={modelsCsv}
                onChange={(e) => setModelsCsv(e.target.value)}
                placeholder="model-a, model-b"
              />
            </Field>
            <Field label="Budget (USD)" hint="Total lifetime spend cap; empty = unlimited.">
              <Input
                type="number"
                min={0}
                step="any"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="25"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="RPM" hint="Requests/min; empty = unlimited.">
                <Input
                  type="number"
                  min={0}
                  value={rpm}
                  onChange={(e) => setRpm(e.target.value)}
                  placeholder="60"
                />
              </Field>
              <Field label="TPM" hint="Tokens/min; empty = unlimited.">
                <Input
                  type="number"
                  min={0}
                  value={tpm}
                  onChange={(e) => setTpm(e.target.value)}
                  placeholder="100000"
                />
              </Field>
            </div>
            <Field label="Expires in (hours)" hint="Empty = never expires.">
              <Input
                type="number"
                min={0}
                step="any"
                value={ttlHours}
                onChange={(e) => setTtlHours(e.target.value)}
                placeholder="720"
              />
            </Field>
            {createError && <ErrorText>{createError}</ErrorText>}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" type="button" onClick={closeCreate}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={!name.trim() || !numsOk || customTooShort || create.isPending}
              >
                Create key
              </Button>
            </div>
          </form>
        )}
      </Dialog>

      {/* -- edit ------------------------------------------------------------ */}
      <Dialog
        open={editTarget != null}
        title={editTarget ? `Edit ${editTarget.alias}` : "Edit key"}
        onClose={closeEdit}
      >
        {editTarget && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!editNumsOk) return;
              setEditError(null);
              const patch: Parameters<typeof patchKey>[1] = {};
              // Empty input = leave unchanged (field omitted); a value overwrites;
              // clearing happens only via the explicit checkboxes/nulls below.
              if (editBudgetN != null) patch.max_budget = editBudgetN;
              if (editRpmN != null) patch.rpm = editRpmN;
              if (editTpmN != null) patch.tpm = editTpmN;
              patch.models = parseCsv(editModels);
              if (clearExpiry) patch.expires_at = null;
              editSave.mutate({ id: editTarget.id, patch });
            }}
          >
            <Field
              label="Budget (USD)"
              hint={`Currently ${
                editTarget.max_budget != null ? fmtUsd(editTarget.max_budget) : "unlimited"
              }. Empty = leave unchanged.`}
            >
              <Input
                type="number"
                min={0}
                step="any"
                value={editBudget}
                onChange={(e) => setEditBudget(e.target.value)}
                placeholder="unchanged"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="RPM" hint="Empty = leave unchanged.">
                <Input
                  type="number"
                  min={0}
                  value={editRpm}
                  onChange={(e) => setEditRpm(e.target.value)}
                  placeholder="unchanged"
                />
              </Field>
              <Field label="TPM" hint="Empty = leave unchanged.">
                <Input
                  type="number"
                  min={0}
                  value={editTpm}
                  onChange={(e) => setEditTpm(e.target.value)}
                  placeholder="unchanged"
                />
              </Field>
            </div>
            <Field
              label="Model allowlist"
              hint="Comma-separated; empty list = all models."
            >
              <Input
                value={editModels}
                onChange={(e) => setEditModels(e.target.value)}
                placeholder="model-a, model-b"
              />
            </Field>
            <label className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]">
              <input
                type="checkbox"
                checked={clearExpiry}
                onChange={(e) => setClearExpiry(e.target.checked)}
                className="h-4 w-4 rounded border-[var(--admin-border)] accent-blue-500"
              />
              Clear expiry
              {editTarget.expires_at != null && (
                <span className="text-[11px] text-[var(--admin-text-dim)]">
                  (currently {fmtDateTime(editTarget.expires_at)})
                </span>
              )}
            </label>
            {editError && <ErrorText>{editError}</ErrorText>}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" type="button" onClick={closeEdit}>
                Cancel
              </Button>
              <Button type="submit" disabled={!editNumsOk || editSave.isPending}>
                Save
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </div>
  );
}
