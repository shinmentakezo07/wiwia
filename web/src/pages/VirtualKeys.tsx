// VirtualKeys page — issue and manage client credentials: budgets, rate limits,
// model allowlists, expiry. Generated plaintext keys are revealed exactly once.

import type { ReactNode } from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Clock,
  KeyRound,
  Layers,
  Plus,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
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
  NumberInput,
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

function isBad(n: number | null): boolean {
  return n !== null && Number.isNaN(n);
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

/** Red validation line rendered under a Field (Field's own hint stays neutral). */
function FieldError(props: { children: ReactNode }) {
  return <p className="mt-1 text-[11px] font-medium text-red-400">{props.children}</p>;
}

/** Inset panel wrapping one logical group of the create form, numbered so the
 *  three groups read as an ordered pass over the key's shape. */
function FormSection(props: { index: string; icon: LucideIcon; title: string; desc?: string; children: ReactNode }) {
  const Icon = props.icon;
  return (
    <section className="rounded-xl border border-[var(--admin-border)] bg-white/[0.015] p-4">
      <div className="mb-3.5 flex items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
          <Icon className="h-3.5 w-3.5" style={{ color: "rgba(59,130,246,0.75)" }} />
        </span>
        <div className="min-w-0 leading-tight">
          <p className="admin-label">{props.title}</p>
          {props.desc && <p className="mt-0.5 text-[11px] text-[var(--admin-text-dim)]">{props.desc}</p>}
        </div>
        <span className="ml-auto shrink-0 font-mono text-[11px] text-[var(--admin-text-dim)]">
          {props.index}
        </span>
      </div>
      <div className="space-y-3">{props.children}</div>
    </section>
  );
}

/** Two-up selectable card — used for the key-source choice. */
function KeySourceCard(props: {
  active: boolean;
  icon: LucideIcon;
  title: string;
  desc: string;
  onClick: () => void;
}) {
  const Icon = props.icon;
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={props.onClick}
      className={`flex flex-1 items-start gap-2.5 rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 ${
        props.active
          ? "border-[var(--admin-accent-glow)] bg-[var(--admin-accent-soft)] ring-1 ring-[var(--admin-accent-glow)]"
          : "border-[var(--admin-border)] bg-white/[0.015] hover:border-[var(--admin-border-hover)] hover:bg-white/[0.025]"
      }`}
    >
      <span
        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
          props.active ? "bg-blue-500/15" : "bg-white/[0.04]"
        }`}
      >
        <Icon className={`h-3.5 w-3.5 ${props.active ? "text-blue-400" : "text-[var(--admin-text-dim)]"}`} />
      </span>
      <div className="min-w-0">
        <p className="text-[13px] font-medium text-[var(--admin-text)]">{props.title}</p>
        <p className="text-[11px] leading-tight text-[var(--admin-text-dim)]">{props.desc}</p>
      </div>
    </button>
  );
}

/** Quick-pick limit profile. Selecting one fills the numeric fields; the
 *  fields stay editable afterwards. */
function PresetPill(props: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={props.active}
      onClick={props.onClick}
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3.5 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 ${
        props.active
          ? "border-[var(--admin-accent-glow)] bg-[var(--admin-accent-soft)] text-[var(--admin-accent)]"
          : "border-[var(--admin-border)] bg-white/[0.015] text-[var(--admin-text-muted)] hover:border-[var(--admin-border-hover)] hover:text-[var(--admin-text)]"
      }`}
    >
      <Zap size={11} className={props.active ? "" : "text-[var(--admin-text-dim)]"} />
      {props.label}
    </button>
  );
}

/** Chip input for the model allowlist. Enter/comma commits a model, Backspace
 *  on an empty input drops the last chip, and an empty list means "all models".
 *  Remove buttons keep a 44px hit area via negative margins so the chips stay
 *  visually compact on touch. */
function ModelChips(props: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState("");
  const chips = parseCsv(props.value);

  function commit(raw: string) {
    const parts = raw
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    const next = [...chips, ...parts.filter((p) => !chips.includes(p))];
    props.onChange(next.join(", "));
    setDraft("");
  }

  return (
    <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-1.5 transition-colors focus-within:border-[var(--admin-accent-glow)]">
      {chips.length > 0 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {chips.map((m) => (
            <span
              key={m}
              className="inline-flex items-center gap-1 rounded-md bg-blue-500/10 py-1 pl-2 pr-1 text-[12px] font-medium text-blue-300 ring-1 ring-blue-500/15"
            >
              <Layers size={10} className="text-blue-400/60" />
              <span className="font-mono">{m}</span>
              <button
                type="button"
                aria-label={`Remove ${m}`}
                onClick={() => props.onChange(chips.filter((c) => c !== m).join(", "))}
                className="-my-2.5 -mr-1.5 flex h-11 w-11 items-center justify-center rounded-md text-blue-300/50 transition-colors hover:bg-white/[0.06] hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        value={draft}
        onChange={(e) => {
          const v = e.target.value;
          if (v.includes(",")) commit(v);
          else setDraft(v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(draft);
          } else if (e.key === "Backspace" && draft === "" && chips.length > 0) {
            props.onChange(chips.slice(0, -1).join(", "));
          }
        }}
        onBlur={() => commit(draft)}
        placeholder={chips.length === 0 ? "model-a, model-b — empty = all models" : "Add model…"}
        className="w-full min-w-0 bg-transparent px-1.5 py-1 text-[13px] text-[var(--admin-text)] outline-none placeholder:text-[var(--admin-text-dim)]"
      />
    </div>
  );
}

/** Quick limit profiles: budget (USD), rpm, tpm, expiry (hours). */
const LIMIT_PRESETS: { id: string; label: string; values: { budget: string; rpm: string; tpm: string; ttl: string } }[] = [
  { id: "open", label: "Unrestricted", values: { budget: "", rpm: "", tpm: "", ttl: "" } },
  { id: "standard", label: "Standard", values: { budget: "25", rpm: "60", tpm: "100000", ttl: "" } },
  { id: "strict", label: "Strict", values: { budget: "5", rpm: "20", tpm: "20000", ttl: "24" } },
];

/** Expiry durations offered as one-click choices; "custom" reveals an hours input. */
const EXPIRY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Never expires" },
  { value: "24", label: "24 hours" },
  { value: "168", label: "7 days" },
  { value: "720", label: "30 days" },
  { value: "2160", label: "90 days" },
  { value: "custom", label: "Custom…" },
];

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
  const [created, setCreated] = useState<{ key: string; name: string } | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [authMode, setAuthMode] = useState<"random" | "custom">("random");
  const [customKey, setCustomKey] = useState("");
  const [modelsCsv, setModelsCsv] = useState("");
  const [budget, setBudget] = useState("");
  const [rpm, setRpm] = useState("");
  const [tpm, setTpm] = useState("");
  const [ttlHours, setTtlHours] = useState("");
  const [expiryCustom, setExpiryCustom] = useState(false);

  const budgetN = tryParse(budget);
  const rpmN = tryParse(rpm);
  const tpmN = tryParse(tpm);
  const ttlHN = tryParse(ttlHours);
  const numsOk = numbersValid([budgetN, rpmN, tpmN, ttlHN]);
  const customTooShort = authMode === "custom" && customKey.trim().length < 16;

  function openCreate() {
    setName("");
    setNameTouched(false);
    setAuthMode("random");
    setCustomKey("");
    setModelsCsv("");
    setBudget("");
    setRpm("");
    setTpm("");
    setTtlHours("");
    setExpiryCustom(false);
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
    onSuccess: (data) => setCreated({ key: data.key, name: name.trim() }),
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
  const [clearBudget, setClearBudget] = useState(false);
  const [clearRpm, setClearRpm] = useState(false);
  const [clearTpm, setClearTpm] = useState(false);
  const [clearModels, setClearModels] = useState(false);

  const editBudgetN = tryParse(editBudget);
  const editRpmN = tryParse(editRpm);
  const editTpmN = tryParse(editTpm);
  const editNumsOk = numbersValid([editBudgetN, editRpmN, editTpmN]);

  function openEdit(k: VirtualKey) {
    setEditTarget(k);
    // Seed every editable field from the key being edited. The inputs are the
    // only source the patch is built from, so an unseeded field would either
    // send a stale value from the previously edited key or read as "empty"
    // and overwrite the stored one (models = [] silently means all-allowed).
    setEditBudget(k.max_budget != null ? String(k.max_budget) : "");
    setEditRpm(k.rpm != null ? String(k.rpm) : "");
    setEditTpm(k.tpm != null ? String(k.tpm) : "");
    setEditModels(k.models.join(", "));
    setClearExpiry(false);
    setClearBudget(false);
    setClearRpm(false);
    setClearTpm(false);
    setClearModels(false);
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

  // -- create-form derived state ------------------------------------------------
  const models = parseCsv(modelsCsv);
  const activePreset = LIMIT_PRESETS.find(
    (p) =>
      p.values.budget === budget &&
      p.values.rpm === rpm &&
      p.values.tpm === tpm &&
      p.values.ttl === ttlHours,
  );
  const expiryPicked =
    expiryCustom || (ttlHours !== "" && !EXPIRY_OPTIONS.some((o) => o.value !== "custom" && o.value === ttlHours))
      ? "custom"
      : ttlHours;
  const canCreate = name.trim() !== "" && numsOk && !customTooShort && !create.isPending;
  const blockedWhy = !name.trim()
    ? "Name is required"
    : !numsOk
      ? "A limit field holds something that isn't a number"
      : customTooShort
        ? "A custom key must be at least 16 characters"
        : create.isPending
          ? "Creating…"
          : null;

  function pickExpiry(v: string) {
    if (v === "custom") {
      setExpiryCustom(true);
    } else {
      setExpiryCustom(false);
      setTtlHours(v);
    }
  }

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
        wide
        contained
        title={created ? "Key created" : "New virtual key"}
        onClose={closeCreate}
      >
        {created ? (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 ring-1 ring-emerald-500/20">
                <Check className="h-5 w-5 text-emerald-400" />
              </span>
              <div className="min-w-0">
                <p className="text-[15px] font-semibold text-[var(--admin-text)]">
                  {created.name || "Key"} created
                </p>
                <p className="text-[12px] text-[var(--admin-text-muted)]">
                  Copy it now — the plaintext is never shown again.
                </p>
              </div>
            </div>

            <div className="rounded-xl border border-blue-500/15 bg-blue-500/[0.04] p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="admin-label mb-1.5">Secret key</p>
                  <p className="break-all font-mono text-[15px] tracking-wide text-blue-300">
                    {created.key}
                  </p>
                </div>
                <CopyButton text={created.key} />
              </div>
            </div>

            <div className="flex items-start gap-2.5 rounded-lg border border-amber-500/15 bg-amber-500/[0.05] px-3.5 py-3">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400/80" />
              <p className="text-[12px] leading-relaxed text-amber-200/80">
                This is the only time the plaintext is shown. wiwi stores a SHA-256 hash — keep the
                key in your secret manager.
              </p>
            </div>

            <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-3.5 py-3">
              <p className="admin-label mb-1.5">Authenticate with</p>
              <p className="break-all font-mono text-[12px] text-[var(--admin-text-muted)]">
                Authorization: Bearer <span className="text-[var(--admin-text)]">{created.key}</span>
              </p>
              <p className="mt-1.5 text-[11px] text-[var(--admin-text-dim)]">
                Works on /v1/chat/completions, /v1/responses and /v1/messages (x-api-key is also
                accepted on the Anthropic surface).
              </p>
            </div>

            <div className="flex justify-end pt-1">
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
            className="flex min-h-0 flex-1 flex-col"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim() || !numsOk || customTooShort) return;
              setCreateError(null);
              const modelList = parseCsv(modelsCsv);
              create.mutate({
                name: name.trim(),
                custom_key: authMode === "custom" ? customKey.trim() : undefined,
                ...(modelList.length > 0 ? { models: modelList } : {}),
                max_budget: budgetN ?? undefined,
                rpm: rpmN ?? undefined,
                tpm: tpmN ?? undefined,
                ttl_seconds: ttlHN != null ? Math.round(ttlHN * 3600) : undefined,
              });
            }}
          >
            <div className="-mr-1 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              <p className="flex items-start gap-2.5 rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-3.5 py-3 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                <ShieldCheck size={14} className="mt-0.5 shrink-0 text-[var(--admin-text-dim)]" />
                <span>
                  Limits are optional — leave any of them empty for unrestricted access. Every limit
                  can be changed later from the key&apos;s Edit action.
                </span>
              </p>

              <FormSection index="01" icon={KeyRound} title="Identity" desc="Name this key and choose how it's generated.">
                <div>
                  <Field label="Name" hint="A label for where this key will be used.">
                    <Input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      onBlur={() => setNameTouched(true)}
                      placeholder="ci-pipeline"
                      autoFocus
                    />
                  </Field>
                  {nameTouched && !name.trim() && <FieldError>Name is required</FieldError>}
                </div>
                <div>
                  <span className="admin-label mb-1.5 block">Key source</span>
                  <div className="flex flex-col gap-3 sm:flex-row">
                    <KeySourceCard
                      active={authMode === "random"}
                      icon={Sparkles}
                      title="Generate random"
                      desc="Wiwi creates a strong key."
                      onClick={() => setAuthMode("random")}
                    />
                    <KeySourceCard
                      active={authMode === "custom"}
                      icon={Upload}
                      title="Bring your own"
                      desc="Use an existing secret."
                      onClick={() => setAuthMode("custom")}
                    />
                  </div>
                </div>
                {authMode === "custom" && (
                  <div>
                    <Field label="Custom key" hint="At least 16 characters; stored hashed.">
                      <Input
                        value={customKey}
                        onChange={(e) => setCustomKey(e.target.value)}
                        placeholder="sk-my-own-value…"
                        className="font-mono"
                      />
                    </Field>
                    {customKey.trim().length > 0 && customTooShort && (
                      <FieldError>Custom keys must be at least 16 characters</FieldError>
                    )}
                  </div>
                )}
              </FormSection>

              <FormSection
                index="02"
                icon={ShieldCheck}
                title="Access & limits"
                desc="Constrain which models this key can reach and how much it can spend."
              >
                <div>
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="admin-label">Model allowlist</span>
                    {models.length === 0 && (
                      <span className="text-[11px] text-[var(--admin-text-dim)]">empty = all models</span>
                    )}
                  </div>
                  <ModelChips value={modelsCsv} onChange={setModelsCsv} />
                </div>

                <div>
                  <span className="admin-label mb-1.5 block">Quick presets</span>
                  <div className="flex flex-wrap gap-2">
                    {LIMIT_PRESETS.map((p) => (
                      <PresetPill
                        key={p.id}
                        label={p.label}
                        active={activePreset?.id === p.id}
                        onClick={() => {
                          setBudget(p.values.budget);
                          setRpm(p.values.rpm);
                          setTpm(p.values.tpm);
                          setTtlHours(p.values.ttl);
                          setExpiryCustom(false);
                        }}
                      />
                    ))}
                  </div>
                </div>

                <div className="grid gap-3 sm:grid-cols-3">
                  <div>
                    <Field label="Budget" hint="Lifetime spend cap; empty = unlimited.">
                      <NumberInput
                        min={0}
                        step="any"
                        value={budget}
                        onChange={setBudget}
                        placeholder="25"
                        suffix="USD"
                      />
                    </Field>
                    {isBad(budgetN) && <FieldError>Not a number</FieldError>}
                  </div>
                  <div>
                    <Field label="RPM" hint="Requests/min; empty = unlimited.">
                      <NumberInput
                        min={0}
                        value={rpm}
                        onChange={setRpm}
                        placeholder="60"
                      />
                    </Field>
                    {isBad(rpmN) && <FieldError>Not a number</FieldError>}
                  </div>
                  <div>
                    <Field label="TPM" hint="Tokens/min; empty = unlimited.">
                      <NumberInput
                        min={0}
                        value={tpm}
                        onChange={setTpm}
                        placeholder="100000"
                      />
                    </Field>
                    {isBad(tpmN) && <FieldError>Not a number</FieldError>}
                  </div>
                </div>
              </FormSection>

              <FormSection index="03" icon={Clock} title="Lifetime" desc="When this key stops working.">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Expires in" hint="Pick a duration or set a custom one.">
                    <Select
                      value={expiryPicked}
                      onChange={pickExpiry}
                      options={EXPIRY_OPTIONS}
                    />
                  </Field>
                  {expiryPicked === "custom" && (
                    <div>
                      <Field label="Custom hours" hint="Hours from now; empty = never expires.">
                        <NumberInput
                          min={0}
                          step="any"
                          value={ttlHours}
                          onChange={setTtlHours}
                          placeholder="720"
                          suffix="hrs"
                        />
                      </Field>
                      {isBad(ttlHN) && <FieldError>Not a number</FieldError>}
                    </div>
                  )}
                </div>
              </FormSection>

              {createError && <ErrorText>{createError}</ErrorText>}
            </div>

            <div className="mt-5 flex items-center justify-between gap-3 border-t border-white/[0.04] pt-4">
              <p className="hidden text-[11px] text-[var(--admin-text-dim)] sm:block">
                The plaintext key is shown once, after creation.
              </p>
              <div className="flex gap-2">
                <Button variant="ghost" type="button" onClick={closeCreate}>
                  Cancel
                </Button>
                <Button type="submit" disabled={!canCreate} title={blockedWhy ?? undefined}>
                  <Sparkles size={14} /> Create key
                </Button>
              </div>
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
              if (clearBudget) patch.max_budget = null;
              else if (editBudgetN != null) patch.max_budget = editBudgetN;
              if (clearRpm) patch.rpm = null;
              else if (editRpmN != null) patch.rpm = editRpmN;
              if (clearTpm) patch.tpm = null;
              else if (editTpmN != null) patch.tpm = editTpmN;
              // The allowlist follows the same rule as the numeric fields: a
              // non-empty list overwrites, an emptied input means "leave
              // unchanged" and only the checkbox sends the all-models [].
              if (clearModels) patch.models = [];
              else if (editModels.trim()) patch.models = parseCsv(editModels);
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
              <NumberInput
                min={0}
                step="any"
                value={editBudget}
                onChange={setEditBudget}
                placeholder="unchanged"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="RPM" hint="Empty = leave unchanged.">
                <NumberInput
                  min={0}
                  value={editRpm}
                  onChange={setEditRpm}
                  placeholder="unchanged"
                />
              </Field>
              <Field label="TPM" hint="Empty = leave unchanged.">
                <NumberInput
                  min={0}
                  value={editTpm}
                  onChange={setEditTpm}
                  placeholder="unchanged"
                />
              </Field>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-[var(--admin-text-muted)]">
              <label className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  checked={clearBudget}
                  onChange={(e) => setClearBudget(e.target.checked)}
                  className="h-4 w-4 rounded border-[var(--admin-border)] accent-blue-500"
                />
                Clear budget → unlimited
              </label>
              <label className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  checked={clearRpm}
                  onChange={(e) => setClearRpm(e.target.checked)}
                  className="h-4 w-4 rounded border-[var(--admin-border)] accent-blue-500"
                />
                Clear RPM → unlimited
              </label>
              <label className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  checked={clearTpm}
                  onChange={(e) => setClearTpm(e.target.checked)}
                  className="h-4 w-4 rounded border-[var(--admin-border)] accent-blue-500"
                />
                Clear TPM → unlimited
              </label>
            </div>
            <Field
              label="Model allowlist"
              hint={`Currently ${
                editTarget.models.length > 0 ? editTarget.models.join(", ") : "all models"
              }. Empty = leave unchanged.`}
            >
              <Input
                value={editModels}
                onChange={(e) => setEditModels(e.target.value)}
                placeholder="unchanged"
              />
            </Field>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-[var(--admin-text-muted)]">
              <label className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  checked={clearModels}
                  onChange={(e) => setClearModels(e.target.checked)}
                  className="h-4 w-4 rounded border-[var(--admin-border)] accent-blue-500"
                />
                Clear allowlist → all models
              </label>
              <label className="flex min-h-11 items-center gap-2">
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
            </div>
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
