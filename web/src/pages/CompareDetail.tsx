// CompareDetail — per-competitor comparison for /compare/:slug. Honest rows:
// cells where the competitor wins are stated plainly, not hidden.

import { Link, useParams } from "react-router-dom";
import { ArrowRight, Check, Minus, ThumbsDown, ThumbsUp } from "lucide-react";
import { Card } from "@/components/ui";
import { DetailCta, DetailHeader, DetailNotFound, FactTable } from "@/components/detail-page";
import { competitorBySlug } from "@/data/competitors";

function Cell({ value }: { value: string | boolean }) {
  if (value === true) {
    return (
      <span className="inline-flex items-center gap-1.5 text-emerald-400" title="Yes">
        <Check size={14} /> <span className="sr-only">Yes</span>
      </span>
    );
  }
  if (value === false) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[var(--admin-text-dim)]" title="No">
        <Minus size={14} /> <span className="sr-only">No</span>
      </span>
    );
  }
  return <span className="text-[var(--admin-text-muted)]">{value}</span>;
}

export function CompareDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const comp = slug ? competitorBySlug(slug) : undefined;

  if (!comp) {
    return <DetailNotFound backTo="/compare" backLabel="All comparisons" what="Comparison" />;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <DetailHeader
        backTo="/compare"
        backLabel="All comparisons"
        badge={comp.tagline}
        title={`wiwi vs ${comp.name}`}
        intro={comp.intro}
      />

      <FactTable
        title="The short version"
        rows={comp.facts.map((f) => ({ label: f.label, value: f.value }))}
      />

      <section className="space-y-3">
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Feature by feature
        </h2>
        <Card className="overflow-hidden p-0">
          <div
            tabIndex={0}
            role="region"
            aria-label={`wiwi versus ${comp.name} feature comparison, scrollable`}
            className="overflow-x-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/50"
          >
            <table className="w-full min-w-[46rem] border-collapse text-left text-[13px]">
              <thead>
                <tr className="border-b border-[var(--admin-border)] bg-white/[0.02]">
                  <th scope="col" className="admin-label px-5 py-3.5">Feature</th>
                  <th scope="col" className="px-5 py-3.5 text-center font-semibold text-[var(--admin-accent)]">wiwi</th>
                  <th scope="col" className="px-5 py-3.5 text-center font-medium text-[var(--admin-text)]">{comp.name}</th>
                  <th scope="col" className="admin-label px-5 py-3.5">Why it matters</th>
                </tr>
              </thead>
              <tbody>
                {comp.rows.map((row, i) => (
                  <tr
                    key={row.feature}
                    className={i < comp.rows.length - 1 ? "border-b border-[var(--admin-border)]" : ""}
                  >
                    <th scope="row" className="px-5 py-3.5 text-left font-medium text-[var(--admin-text)]">
                      {row.feature}
                    </th>
                    <td className="px-5 py-3.5 text-center"><Cell value={row.wiwi} /></td>
                    <td className="px-5 py-3.5 text-center"><Cell value={row.them} /></td>
                    <td className="px-5 py-3.5 text-[12.5px] leading-relaxed text-[var(--admin-text-dim)]">{row.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        <Card className="p-5">
          <h3 className="flex items-center gap-2 text-[14px] font-semibold text-emerald-300">
            <ThumbsUp size={15} /> Where wiwi is ahead
          </h3>
          <ul className="mt-3 space-y-2">
            {comp.weWin.map((w) => (
              <li key={w} className="flex items-start gap-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-emerald-400" aria-hidden />
                {w}
              </li>
            ))}
          </ul>
        </Card>
        <Card className="p-5">
          <h3 className="flex items-center gap-2 text-[14px] font-semibold text-amber-300">
            <ThumbsDown size={15} /> Where {comp.name} is ahead
          </h3>
          <ul className="mt-3 space-y-2">
            {comp.theyWin.map((w) => (
              <li key={w} className="flex items-start gap-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-amber-400" aria-hidden />
                {w}
              </li>
            ))}
          </ul>
        </Card>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5">
          <span className="admin-label">Pick {comp.name} when</span>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">{comp.pickThemWhen}</p>
        </div>
        <div className="rounded-xl border border-blue-500/15 bg-blue-500/[0.04] p-5">
          <span className="admin-label">Pick wiwi when</span>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">{comp.pickUsWhen}</p>
        </div>
      </section>

      {comp.migrationSlug && (
        <Link
          to={`/migration/${comp.migrationSlug}`}
          className="group flex items-center justify-between gap-4 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 transition-colors hover:border-[var(--admin-border-hover)]"
        >
          <div>
            <span className="admin-label">Next step</span>
            <p className="mt-1 text-[14px] font-medium text-[var(--admin-text)]">
              Migrate from {comp.name} step by step
            </p>
          </div>
          <ArrowRight size={16} className="shrink-0 text-blue-400 transition-transform group-hover:translate-x-0.5" />
        </Link>
      )}

      <DetailCta
        title="Run both for a week"
        body="The gateway is one Docker command away — retarget one workload and compare the bills, not the decks."
        primary={{ label: "Read the docs", to: "/docs" }}
        secondary={{ label: "All comparisons", to: "/compare" }}
      />
    </div>
  );
}
