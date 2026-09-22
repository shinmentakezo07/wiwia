// MigrationDetail — per-vendor migration guide for /migration/:slug.

import { useParams } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import {
  DetailCta,
  DetailHeader,
  DetailNotFound,
  FactTable,
  StepList,
} from "@/components/detail-page";
import { migrationBySlug } from "@/data/migrations";

export function MigrationDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const migration = slug ? migrationBySlug(slug) : undefined;

  if (!migration) {
    return <DetailNotFound backTo="/migration" backLabel="All migrations" what="Guide" />;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <DetailHeader
        backTo="/migration"
        backLabel="All migrations"
        badge={`From ${migration.fromProvider}`}
        title={migration.title}
        intro={migration.intro}
      />

      <FactTable
        title="Migration at a glance"
        rows={migration.facts.map((f) => ({ label: f.label, value: f.value }))}
      />

      <section className="space-y-4">
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Step by step
        </h2>
        <StepList steps={migration.steps} />
      </section>

      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          <AlertTriangle size={16} className="text-amber-400" /> What to know before you switch
        </h2>
        <ul className="space-y-2">
          {migration.caveats.map((c, i) => (
            <li
              key={i}
              className="rounded-xl border border-amber-500/15 bg-amber-500/[0.04] px-4 py-3 text-[13.5px] leading-relaxed text-amber-100/90"
            >
              {c}
            </li>
          ))}
        </ul>
      </section>

      <DetailCta
        title="Already running wiwi?"
        body="Mint a virtual key, point the client, and the old endpoint becomes history."
        primary={{ label: "Read the docs", to: "/docs" }}
        secondary={
          migration.relatedGuideSlug
            ? { label: "See the client guide", to: `/guides/${migration.relatedGuideSlug}` }
            : { label: "Compare gateways", to: "/compare" }
        }
      />
    </div>
  );
}
