// Shared models — add a model once and deploy it to every account of one
// OAuth provider type, so an operator never has to repeat the same model id
// per account.
//
// Each shared model becomes a normal model group named `<prefix>:<model_id>`
// (the same convention the Cline default-model reconciler already uses), with
// one deployment per account of that type. Requests to `workbuddy:<id>` or
// `hermes:<id>` therefore smooth-WRR across all of those accounts. Deployments
// belonging to other provider types are never touched.

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Loader2, Plus, Trash2 } from "lucide-react";
import { addDeployment, deleteDeployment, getModels } from "@/api/client";
import type { Provider } from "@/api/types";
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
  Spinner,
} from "@/components/ui";

export function SharedModelsCard(props: {
  /** Provider type the deployments are scoped to, e.g. "workbuddy". */
  providerType: string;
  /** Group namespace, e.g. "workbuddy" → groups named `workbuddy:<model>`. */
  prefix: string;
  /** Every provider in the gateway; the card filters by `providerType`. */
  providers: Provider[];
  /** Human label for the provider type. Defaults to `providerType`. */
  label?: string;
  /** Key accounts across those providers — used only for the copy. Defaults
   *  to the provider count when the caller has no per-key list. */
  accountCount?: number;
  onError: (m: string) => void;
}) {
  const qc = useQueryClient();
  const groupsQ = useQuery({ queryKey: ["model-groups"], queryFn: getModels });
  const [modelId, setModelId] = useState("");
  const [weight, setWeight] = useState("1");
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const providers = useMemo(
    () => props.providers.filter((p) => p.provider_type === props.providerType),
    [props.providers, props.providerType],
  );
  const providerNames = useMemo(() => new Set(providers.map((p) => p.name)), [providers]);
  const label = props.label ?? props.providerType;
  const accountCount = props.accountCount ?? providers.length;

  const sharedGroups = useMemo(
    () =>
      (groupsQ.data?.groups ?? []).filter(
        (g) =>
          g.name.startsWith(`${props.prefix}:`) &&
          g.deployments.some((d) => providerNames.has(d.provider)),
      ),
    [groupsQ.data, props.prefix, providerNames],
  );

  const add = useMutation({
    mutationFn: async () => {
      const mid = modelId.trim();
      if (!mid) throw new Error("Enter a model id.");
      if (/\s/.test(mid)) throw new Error("A model id cannot contain spaces.");
      if (providers.length === 0) throw new Error("No accounts of this type yet.");
      const group = `${props.prefix}:${mid}`;
      const w = Math.max(1, parseInt(weight, 10) || 1);
      const deployed = new Set(
        (groupsQ.data?.groups.find((g) => g.name === group)?.deployments ?? [])
          .filter((d) => providerNames.has(d.provider))
          .map((d) => d.provider),
      );
      for (const p of providers) {
        if (deployed.has(p.name)) continue;
        try {
          await addDeployment(group, { provider: p.name, model_id: mid, weight: w });
        } catch (e) {
          // A concurrent add (or a stale cache) can make the backend report the
          // deployment already exists; that is the desired end state.
          if (e instanceof Error && /already attached/i.test(e.message)) continue;
          throw e;
        }
      }
    },
    onSuccess: () => {
      setModelId("");
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
    },
    onError: (e) => props.onError(e.message),
  });

  // Serialized on purpose: each DELETE is a read-modify-write on the group, so
  // firing them concurrently would let one response overwrite another.
  const remove = useMutation({
    mutationFn: async (group: string) => {
      const g = groupsQ.data?.groups.find((x) => x.name === group);
      if (!g) return;
      for (const d of g.deployments) {
        if (providerNames.has(d.provider)) {
          await deleteDeployment(group, d.provider, d.model_id);
        }
      }
    },
    onSuccess: () => {
      setConfirmRemove(null);
      void qc.invalidateQueries({ queryKey: ["model-groups"] });
    },
    onError: (e) => props.onError(e.message),
  });

  const removing = confirmRemove;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Layers size={15} className="shrink-0 text-[var(--admin-text-muted)]" aria-hidden />
            Shared models
          </span>
        }
        subtitle={`One model id, deployed to every ${label} account`}
        right={
          <Badge tone={sharedGroups.length > 0 ? "green" : "gray"}>
            {sharedGroups.length} model{sharedGroups.length !== 1 ? "s" : ""}
          </Badge>
        }
      />
      <div className="space-y-4 px-5 pb-5 pt-4">
        <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
          Adding a model here deploys it to all {accountCount} {label} account
          {accountCount !== 1 ? "s" : ""} across {providers.length} provider
          pool{providers.length !== 1 ? "s" : ""} at once. Requests to{" "}
          <code className="font-mono text-[11px]">{props.prefix}:&lt;id&gt;</code>{" "}
          round-robin across those accounts — you do not need to add the model id to
          each account separately.
        </p>

        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1">
              <Field label="Model id">
                <Input
                  value={modelId}
                  placeholder="e.g. z-ai/glm-5.2"
                  className="font-mono"
                  onChange={(e) => setModelId(e.target.value)}
                />
              </Field>
            </div>
            <div className="w-full sm:w-24">
              <Field label="Weight">
                <Input
                  type="number"
                  min={1}
                  value={weight}
                  onChange={(e) => setWeight(e.target.value)}
                />
              </Field>
            </div>
            <Button
              type="submit"
              className="min-h-11"
              disabled={!modelId.trim() || add.isPending || providers.length === 0}
            >
              {add.isPending ? (
                <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Plus size={14} aria-hidden />
              )}
              {add.isPending ? "Adding…" : `Add to all ${accountCount}`}
            </Button>
          </div>
          {add.error && <ErrorText>{add.error.message}</ErrorText>}
        </form>

        {groupsQ.isLoading ? (
          <Spinner />
        ) : sharedGroups.length === 0 ? (
          <EmptyState>
            No shared {label} models yet. Add one above and it is attached to
            every account in this list.
          </EmptyState>
        ) : (
          <div className="space-y-2">
            {sharedGroups.map((g) => (
              <div
                key={g.name}
                className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <code className="min-w-0 truncate font-mono text-[12px] text-[var(--admin-text)]">
                    {g.name}
                  </code>
                  <Button
                    variant="ghost"
                    className="min-h-11"
                    disabled={remove.isPending}
                    onClick={() => setConfirmRemove(g.name)}
                  >
                    <Trash2 size={13} aria-hidden />
                    Remove
                  </Button>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {g.deployments.map((d) => (
                    <span
                      key={`${d.provider}/${d.model_id}`}
                      className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                        providerNames.has(d.provider)
                          ? "bg-white/[0.06] text-[var(--admin-text-muted)]"
                          : "bg-white/[0.02] text-[var(--admin-text-dim)]"
                      }`}
                    >
                      {d.provider}/{d.model_id}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Dialog
        open={removing !== null}
        onClose={() => setConfirmRemove(null)}
        icon={Trash2}
        title="Remove this shared model?"
        footer={
          <>
            <Button variant="ghost" className="min-h-11" onClick={() => setConfirmRemove(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              className="min-h-11"
              disabled={remove.isPending}
              onClick={() => removing && remove.mutate(removing)}
            >
              {remove.isPending ? (
                <Loader2 size={14} aria-hidden className="animate-spin motion-reduce:animate-none" />
              ) : (
                <Trash2 size={14} aria-hidden />
              )}
              {remove.isPending ? "Removing…" : "Remove"}
            </Button>
          </>
        }
      >
        <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
          Removes <code className="font-mono text-[12px]">{removing}</code> from all{" "}
          {accountCount} {label} account{accountCount !== 1 ? "s" : ""}. Deployments
          belonging to other provider types are left alone.
        </p>
      </Dialog>
    </Card>
  );
}
