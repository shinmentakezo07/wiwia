// ModelsCatalog — public, secret-free model catalog from /public/models.
// No auth required. Renders a grid of model-group cards with their deployment
// providers; each card links to the gated /playground so a logged-in caller
// can try the group.

import { Link } from "react-router-dom";
import { Boxes, ArrowRight } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getPublicModels } from "@/api/client";
import { Badge, Card, EmptyState, ErrorText, Spinner } from "@/components/ui";

export function ModelsCatalogPage() {
  const query = useQuery({ queryKey: ["public-models"], queryFn: getPublicModels });

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          Models
        </h2>
        <p className="mt-0.5 font-mono text-[13px] tracking-wide text-[var(--admin-text-muted)]">
          The model groups this gateway knows about, and which providers back each one.
        </p>
      </div>

      {query.isLoading && (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      )}
      {query.error && <ErrorText>{query.error.message}</ErrorText>}

      {query.data && (
        <>
          {query.data.groups.length === 0 ? (
            <Card>
              <EmptyState>
                <Boxes size={20} className="mx-auto mb-3 opacity-40" />
                No model groups configured on this gateway yet.
              </EmptyState>
            </Card>
          ) : (
            <>
              {Object.keys(query.data.aliases).length > 0 && (
                <Card className="mb-5 p-4">
                  <span className="admin-label mb-2 block">Aliases</span>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(query.data.aliases).map(([alias, target]) => (
                      <Badge key={alias} tone="blue" title={`alias → ${target}`}>
                        <span className="font-mono">{alias}</span>
                        <span className="mx-1.5 text-[var(--admin-text-dim)]">→</span>
                        <span className="font-mono">{target}</span>
                      </Badge>
                    ))}
                  </div>
                </Card>
              )}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {query.data.groups.map((g) => (
                  <Card
                    key={g.name}
                    className="group p-5 transition-colors hover:border-[var(--admin-border-hover)]"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="font-mono text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                        {g.name}
                      </h3>
                      <Link
                        to="/playground"
                        className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-blue-300 opacity-0 transition-opacity hover:text-blue-200 group-hover:opacity-100"
                      >
                        try <ArrowRight size={11} />
                      </Link>
                    </div>
                    <p className="mt-1 text-[11px] text-[var(--admin-text-dim)]">
                      {g.deployments.length} deployment{g.deployments.length === 1 ? "" : "s"}
                    </p>
                    <div className="mt-3 space-y-1.5">
                      {g.deployments.map((d) => (
                        <div
                          key={`${d.provider}/${d.model_id}`}
                          className="flex items-center gap-2 rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-2.5 py-1.5"
                        >
                          <Badge tone="violet">{d.provider}</Badge>
                          <span className="truncate font-mono text-[12px] text-[var(--admin-text-muted)]">
                            {d.model_id}
                          </span>
                        </div>
                      ))}
                    </div>
                  </Card>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
