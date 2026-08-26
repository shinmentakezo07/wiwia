// Models page — model groups with deployment chips (inline weight edit),
// routing strategy selector, per-deployment health dots.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getModels, patchModelGroup } from "@/api/client";
import type { ModelGroup } from "@/api/types";
import { useAuth } from "@/api/auth";
import {
  Badge,
  Card,
  ErrorText,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "@/components/ui";

const STRATEGIES = [
  { value: "simple-shuffle", label: "Weighted random (simple-shuffle)" },
  { value: "least-busy", label: "Least busy" },
  { value: "latency-based", label: "Latency based" },
];

function HealthDot(props: { ok: boolean; title: string }) {
  return (
    <span
      title={props.title}
      className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
      style={{ backgroundColor: props.ok ? "#34d399" : "#f87171" }}
    />
  );
}

function WeightChip(props: {
  group: string;
  provider: string;
  modelId: string;
  weight: number;
  ok: boolean;
  title: string;
  editable: boolean;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(props.weight));
  const ident = `${props.provider}/${props.modelId}`;

  const patch = useMutation({
    mutationFn: () => patchModelGroup(props.group, { weights: { [ident]: parseInt(value, 10) || 1 } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => props.onError(e.message),
  });

  if (!props.editable) {
    // Read-only for non-admins: same chip, no click-to-edit.
    return (
      <div
        title={props.editable ? "Click to edit weight" : "Weight (admin-only)"}
        className="flex items-center gap-2 rounded-[10px] border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2 text-left text-[13px]"
      >
        <HealthDot ok={props.ok} title={props.title} />
        <span>
          <span className="font-medium text-[var(--admin-text)]">{props.provider}</span>
          <span className="text-[var(--admin-text-dim)]"> · </span>
          <span className="font-mono text-[12px] text-[var(--admin-text-muted)]">{props.modelId}</span>
        </span>
        <Badge tone="gray" title="Deployment weight">
          w {props.weight}
        </Badge>
      </div>
    );
  }

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        title="Click to edit weight"
        className="flex items-center gap-2 rounded-[10px] border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2 text-left text-[13px] transition-colors hover:border-blue-500/20"
      >
        <HealthDot ok={props.ok} title={props.title} />
        <span>
          <span className="font-medium text-[var(--admin-text)]">{props.provider}</span>
          <span className="text-[var(--admin-text-dim)]"> · </span>
          <span className="font-mono text-[12px] text-[var(--admin-text-muted)]">{props.modelId}</span>
        </span>
        <Badge tone="gray" title="Deployment weight">
          w {props.weight}
        </Badge>
      </button>
    );
  }
  return (
    <form
      className="flex items-center gap-1 rounded-[10px] border border-blue-500/30 bg-white/[0.02] px-2 py-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        patch.mutate();
        setEditing(false);
      }}
    >
      <Input
        autoFocus
        className="h-auto w-16 text-[12px]"
        type="number"
        min={1}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => setEditing(false)}
      />
      <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">{ident}</span>
      {patch.isPending && <Spinner className="h-4 w-4" />}
    </form>
  );
}

function GroupCard(props: { g: ModelGroup; strategy: string; aliases: Record<string, string>; editable: boolean; onError: (m: string) => void }) {
  return (
    <Card className="p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-[14px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.g.name}
        </h3>
        {Object.entries(props.aliases)
          .filter(([, target]) => target === props.g.name)
          .map(([alias]) => (
            <Badge key={alias} tone="blue" title={`alias → ${props.g.name}`}>
              alias: {alias}
            </Badge>
          ))}
        <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
          {props.g.deployments.length} deployment{props.g.deployments.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {props.g.deployments.map((d) => (
          <WeightChip
            key={`${d.provider}/${d.model_id}`}
            group={props.g.name}
            provider={d.provider}
            modelId={d.model_id}
            weight={d.weight}
            ok={d.available}
            editable={props.editable}
            title={
              d.available
                ? `healthy · inflight ${d.inflight} · p95 ${Math.round(d.p95_latency_ms)}ms`
                : `unavailable${d.cooldown_remaining_s > 0 ? ` (cooling ${Math.ceil(d.cooldown_remaining_s)}s)` : ""}`
            }
            onError={props.onError}
          />
        ))}
      </div>
    </Card>
  );
}

export function ModelsPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const editable = user?.role === "admin";
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["models"], queryFn: getModels, refetchInterval: 10_000 });

  if (query.isLoading) return <Spinner />;
  if (query.error) return <ErrorText>{query.error.message}</ErrorText>;

  const data = query.data!;

  const setStrategy = useMutation({
    mutationFn: (strategy: string) => {
      if (!data.groups.length) return Promise.reject(new Error("no groups to patch"));
      // strategy is global router state; any known group path applies it
      return patchModelGroup(data.groups[0].name, { strategy });
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e) => setError(e.message),
  });

  // Strategy label for the read-only (non-admin) view.
  const strategyLabel = STRATEGIES.find((s) => s.value === data.strategy)?.label ?? data.strategy;

  return (
    <div>
      <PageHeader
        title="Models"
        subtitle={
          editable
            ? "Model groups and their deployments. Click a chip's weight to rebalance traffic."
            : "Model groups and their deployments. (Read-only — ask an admin to change weights.)"
        }
        right={
          editable ? (
            <Select
              value={data.strategy}
              onChange={(s) => {
                if (!data.groups.length) return;
                // PATCH applies globally on the router settings; any group path works.
                setStrategy.mutate(s);
              }}
              options={STRATEGIES}
            />
          ) : (
            <Badge tone="gray" title="Routing strategy">
              {strategyLabel}
            </Badge>
          )
        }
      />
      {error && (
        <div className="mb-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}
      {editable && setStrategy.isPending && <Spinner />}
      <div className="space-y-4">
        {data.groups.map((g) => (
          <GroupCard
            key={g.name}
            g={g}
            strategy={data.strategy}
            aliases={data.aliases}
            editable={editable}
            onError={setError}
          />
        ))}
        {data.groups.length === 0 && (
          <Card className="px-4 py-12 text-center text-[13px] text-[var(--admin-text-dim)]">
            No model groups configured.
          </Card>
        )}
      </div>
    </div>
  );
}

