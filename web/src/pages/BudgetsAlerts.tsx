// BudgetsAlerts — per-key budget overview with projected month-end spend and
// alert-rule management (rules are storage-only server-side for now).

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import {
  getAlertRules,
  getRequestLogs,
  listKeys,
  patchKey,
  putAlertRules,
} from "@/api/client";
import type { AlertRule, RequestLogEntry, VirtualKey } from "@/api/types";
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
  ProgressBar,
  Select,
  Spinner,
  StatCard,
  Table,
  TD,
} from "@/components/ui";
import { fmtUsd } from "@/lib/format";

const SEVEN_DAYS_S = 7 * 86400;
const PROJECTION_TOOLTIP =
  "trailing-7d rate × days in month; estimate from in-memory ring";

/** "" → null (means "leave unchanged"); unparsable → NaN; otherwise the number. */
function tryParse(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  return Number.isFinite(Number(t)) ? Number(t) : NaN;
}

// -- month-end projection -----------------------------------------------------

type Projections = { byKey: Map<string, number>; total: number; hasData: boolean };

/**
 * Projected month-end spend per key alias, from the in-memory request-log ring:
 * trailing-7d cost rate × days in the current month. The window denominator is
 * min(now − oldest log ts, 7d) so a partially-filled ring doesn't understate.
 */
function computeProjections(logs: RequestLogEntry[], now: Date): Projections {
  const nowS = now.getTime() / 1000;
  let oldest: number | null = null;
  let recentCost = 0;
  const costByKey = new Map<string, number>();
  for (const log of logs) {
    if (oldest == null || log.ts < oldest) oldest = log.ts;
    if (log.ts >= nowS - SEVEN_DAYS_S) {
      recentCost += log.cost;
      costByKey.set(log.key_alias, (costByKey.get(log.key_alias) ?? 0) + log.cost);
    }
  }
  if (oldest == null) return { byKey: new Map(), total: 0, hasData: false };

  // Require at least 1 day of history before projecting. With less data the
  // per-day rate is wildly unstable (a partially-filled ring can inflate the
  // month-end estimate by up to ~720×), so report "insufficient data" instead.
  const windowDays = Math.min(nowS - oldest, SEVEN_DAYS_S) / 86400;
  if (windowDays < 1) return { byKey: new Map(), total: 0, hasData: false };

  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const scale = daysInMonth / windowDays;

  const byKey = new Map<string, number>();
  for (const [alias, cost] of costByKey) byKey.set(alias, cost * scale);
  return { byKey, total: recentCost * scale, hasData: true };
}

// -- key budgets --------------------------------------------------------------

function BudgetRow(props: {
  k: VirtualKey;
  projected: number | null;
  onEdit: (k: VirtualKey) => void;
}) {
  const { k } = props;
  return (
    <tr>
      <TD className="font-medium">{k.alias}</TD>
      <TD className="font-mono tabular-nums">{fmtUsd(k.spend_to_date)}</TD>
      <TD className="font-mono tabular-nums">
        {k.max_budget != null ? (
          fmtUsd(k.max_budget)
        ) : (
          <span className="text-[var(--admin-text-dim)]">no limit</span>
        )}
      </TD>
      <TD>
        <div className="w-32">
          <ProgressBar
            value={
              k.max_budget != null && k.max_budget > 0
                ? k.spend_to_date / k.max_budget
                : k.max_budget === 0
                  ? 1
                  : 0
            }
          />
        </div>
      </TD>
      <TD className="font-mono tabular-nums">
        {props.projected == null ? (
          "—"
        ) : (
          <span title={PROJECTION_TOOLTIP}>{fmtUsd(props.projected)}</span>
        )}
      </TD>
      <TD className="text-right">
        <Button variant="outline" onClick={() => props.onEdit(k)}>
          Edit
        </Button>
      </TD>
    </tr>
  );
}

// -- alert rules --------------------------------------------------------------

/** Local editable shape; threshold stays a string until save. */
interface RuleDraft {
  id: string;
  name: string;
  webhook_url: string;
  metric: AlertRule["metric"];
  threshold: string;
}

function toDraft(r: AlertRule): RuleDraft {
  return {
    id: r.id,
    name: r.name ?? "",
    webhook_url: r.webhook_url,
    metric: r.metric,
    threshold: String(r.threshold),
  };
}

function draftToRule(d: RuleDraft): AlertRule {
  return {
    id: d.id,
    ...(d.name.trim() ? { name: d.name.trim() } : {}),
    webhook_url: d.webhook_url.trim(),
    metric: d.metric,
    threshold: Number(d.threshold.trim()),
  };
}

function draftsValid(drafts: RuleDraft[]): boolean {
  return drafts.every(
    (d) => d.webhook_url.trim() !== "" && d.threshold.trim() !== "" && Number.isFinite(Number(d.threshold)),
  );
}

function RuleRow(props: { d: RuleDraft; onChange: (patch: Partial<RuleDraft>) => void; onRemove: () => void }) {
  const { d } = props;
  return (
    <div className="flex flex-wrap items-end gap-2 rounded-[12px] border border-[var(--admin-border)] bg-white/[0.015] p-3">
      <div className="w-40">
        <Field label="Name">
          <Input
            value={d.name}
            placeholder="optional"
            onChange={(e) => props.onChange({ name: e.target.value })}
          />
        </Field>
      </div>
      <div className="min-w-56 flex-1">
        <Field label="Webhook URL">
          <Input
            value={d.webhook_url}
            placeholder="https://hooks.example.com/…"
            onChange={(e) => props.onChange({ webhook_url: e.target.value })}
          />
        </Field>
      </div>
      <Field label="Metric">
        <Select
          value={d.metric}
          onChange={(v) => props.onChange({ metric: v as AlertRule["metric"] })}
          options={[
            { value: "spend", label: "Spend" },
            { value: "error_rate", label: "Error rate" },
            { value: "requests_per_minute", label: "Requests / min" },
          ]}
        />
      </Field>
      <div className="w-28">
        <Field label="Threshold">
          <Input
            type="number"
            step="any"
            value={d.threshold}
            onChange={(e) => props.onChange({ threshold: e.target.value })}
          />
        </Field>
      </div>
      <Button variant="danger" title="Remove rule" aria-label="Remove rule" onClick={props.onRemove}>
        <Trash2 size={14} />
      </Button>
    </div>
  );
}

// -- page -----------------------------------------------------------------------

export function BudgetsAlertsPage() {
  const qc = useQueryClient();

  // -- keys + projections -------------------------------------------------------
  const keysQuery = useQuery({ queryKey: ["keys"], queryFn: listKeys, refetchInterval: 15_000 });
  const logsQuery = useQuery({
    queryKey: ["request-logs"],
    queryFn: getRequestLogs,
    refetchInterval: 15_000,
  });

  const projections = useMemo(
    () => computeProjections(logsQuery.data?.logs ?? [], new Date()),
    [logsQuery.data],
  );

  const keys = keysQuery.data?.keys ?? [];
  const totalSpend = keys.reduce((a, k) => a + k.spend_to_date, 0);

  // -- edit-budget dialog ---------------------------------------------------------
  const [editTarget, setEditTarget] = useState<VirtualKey | null>(null);
  const [editBudget, setEditBudget] = useState("");
  const [clearBudget, setClearBudget] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const editBudgetN = tryParse(editBudget);
  const editValid =
    editBudgetN == null || (!Number.isNaN(editBudgetN) && editBudgetN >= 0);

  function openEdit(k: VirtualKey) {
    setEditTarget(k);
    setEditBudget(k.max_budget != null ? String(k.max_budget) : "");
    setClearBudget(false);
    setEditError(null);
  }

  function closeEdit() {
    setEditTarget(null);
    setEditError(null);
  }

  const editSave = useMutation({
    mutationFn: (args: { id: string; patch: { max_budget?: number | null } }) =>
      patchKey(args.id, args.patch),
    onSuccess: () => {
      closeEdit();
      void qc.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (e) => setEditError(e.message),
  });

  // -- alert rules ------------------------------------------------------------------
  const rulesQuery = useQuery({ queryKey: ["alert-rules"], queryFn: getAlertRules });
  const [drafts, setDrafts] = useState<RuleDraft[] | null>(null);
  const [rulesError, setRulesError] = useState<string | null>(null);

  // Seed local state once from the server; afterwards edits win over refetches.
  useEffect(() => {
    if (rulesQuery.data && drafts === null) {
      setDrafts(rulesQuery.data.rules.map(toDraft));
    }
  }, [rulesQuery.data, drafts]);

  function updateRule(id: string, patch: Partial<RuleDraft>) {
    setDrafts((prev) => (prev ? prev.map((d) => (d.id === id ? { ...d, ...patch } : d)) : prev));
  }

  function addRule() {
    setDrafts((prev) => [
      ...(prev ?? []),
      { id: crypto.randomUUID(), name: "", webhook_url: "", metric: "spend", threshold: "0" },
    ]);
  }

  function removeRule(id: string) {
    setDrafts((prev) => (prev ? prev.filter((d) => d.id !== id) : prev));
  }

  const saveRules = useMutation({
    mutationFn: (rules: AlertRule[]) => putAlertRules(rules),
    onSuccess: (data) => {
      setDrafts(data.rules.map(toDraft));
      void qc.invalidateQueries({ queryKey: ["alert-rules"] });
    },
    onError: (e) => setRulesError(e.message),
  });

  return (
    <div>
      <PageHeader
        title="Budgets & Alerts"
        subtitle="Per-key spend caps with month-end projections, plus webhook alert rules."
      />

      {/* -- summary ---------------------------------------------------------- */}
      <div className="mb-4 grid grid-cols-2 gap-3">
        <StatCard
          label="Total spend"
          value={fmtUsd(totalSpend)}
          sub={`${keys.length} key${keys.length === 1 ? "" : "s"}`}
        />
        <div title={PROJECTION_TOOLTIP}>
          <StatCard
            label="Projected month-end"
            value={fmtUsd(projections.total)}
            sub={projections.hasData ? "trailing-7d rate estimate" : "no request data yet"}
          />
        </div>
      </div>

      {/* -- key budgets ------------------------------------------------------ */}
      <Card className="mb-4">
        <CardHeader title="Key budgets" />
        {keysQuery.isLoading && (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        )}
        {keysQuery.error && (
          <div className="p-4">
            <ErrorText>{keysQuery.error.message}</ErrorText>
          </div>
        )}
        {keysQuery.data &&
          (keys.length === 0 ? (
            <EmptyState>No virtual keys yet.</EmptyState>
          ) : (
            <Table head={["Key", "Spend", "Budget", "Usage", "Projected month-end", ""]}>
              {keys.map((k) => (
                <BudgetRow
                  key={k.id}
                  k={k}
                  projected={projections.hasData ? (projections.byKey.get(k.alias) ?? 0) : null}
                  onEdit={openEdit}
                />
              ))}
            </Table>
          ))}
      </Card>

      {/* -- alert rules -------------------------------------------------------- */}
      <Card>
        <CardHeader
          title="Alert rules"
          right={
            <Button variant="outline" onClick={addRule}>
              <Plus size={14} /> Add rule
            </Button>
          }
        />
        {rulesQuery.isLoading && (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        )}
        {rulesQuery.error && (
          <div className="p-4">
            <ErrorText>{rulesQuery.error.message}</ErrorText>
          </div>
        )}
        {drafts != null && (
          <form
            className="space-y-3 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!drafts || !draftsValid(drafts)) return;
              setRulesError(null);
              saveRules.mutate(drafts.map(draftToRule));
            }}
          >
            {drafts.length === 0 ? (
              <EmptyState>No alert rules yet. Add one to get notified.</EmptyState>
            ) : (
              drafts.map((d) => (
                <RuleRow
                  key={d.id}
                  d={d}
                  onChange={(patch) => updateRule(d.id, patch)}
                  onRemove={() => removeRule(d.id)}
                />
              ))
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
                storage only — evaluation engine ships post-MVP
              </span>
              <Button type="submit" disabled={!draftsValid(drafts) || saveRules.isPending}>
                Save rules
              </Button>
            </div>
          </form>
        )}
        {rulesError && (
          <div className="px-4 pb-4">
            <ErrorText>{rulesError}</ErrorText>
          </div>
        )}
      </Card>

      {/* -- edit-budget dialog ----------------------------------------------- */}
      <Dialog
        open={editTarget != null}
        title={editTarget ? `Edit budget — ${editTarget.alias}` : "Edit budget"}
        onClose={closeEdit}
      >
        {editTarget && (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!editTarget || !editValid) return;
              setEditError(null);
              // Empty input = leave unchanged (field omitted); a value overwrites;
              // clearing happens only via the explicit checkbox below.
              const patch: { max_budget?: number | null } = {};
              if (clearBudget) patch.max_budget = null;
              else if (editBudgetN != null) patch.max_budget = editBudgetN;
              editSave.mutate({ id: editTarget.id, patch });
            }}
          >
            <Field
              label="Budget (USD)"
              hint={`Currently ${
                editTarget.max_budget != null ? fmtUsd(editTarget.max_budget) : "no limit"
              }. Empty = leave unchanged.`}
            >
              <Input
                type="number"
                min={0}
                step="any"
                value={editBudget}
                disabled={clearBudget}
                autoFocus
                placeholder="unchanged"
                onChange={(e) => setEditBudget(e.target.value)}
              />
            </Field>
            <label className="flex items-center gap-2 text-[13px] text-[var(--admin-text-muted)]">
              <input
                type="checkbox"
                checked={clearBudget}
                onChange={(e) => setClearBudget(e.target.checked)}
                className="h-4 w-4 rounded border-[var(--admin-border)] accent-blue-500"
              />
              Clear budget
              {editTarget.max_budget != null && (
                <span className="text-[11px] text-[var(--admin-text-dim)]">
                  (currently {fmtUsd(editTarget.max_budget)})
                </span>
              )}
            </label>
            {editError && <ErrorText>{editError}</ErrorText>}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" type="button" onClick={closeEdit}>
                Cancel
              </Button>
              <Button type="submit" disabled={!editValid || editSave.isPending}>
                Save
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </div>
  );
}
