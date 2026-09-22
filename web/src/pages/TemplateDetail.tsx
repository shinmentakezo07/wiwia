// TemplateDetail — per-template page for /templates/:slug.

import { useParams } from "react-router-dom";
import { ArrowUpRight, Check, Github } from "lucide-react";
import { Card } from "@/components/ui";
import {
  DetailCta,
  DetailHeader,
  DetailNotFound,
  FactTable,
  StepList,
} from "@/components/detail-page";
import { templateBySlug } from "@/data/templates";

export function TemplateDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const template = slug ? templateBySlug(slug) : undefined;

  if (!template) {
    return <DetailNotFound backTo="/templates" backLabel="All templates" what="Template" />;
  }

  const Icon = template.icon;

  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <DetailHeader
        backTo="/templates"
        backLabel="All templates"
        badge={template.tags[0]}
        title={template.name}
        intro={template.intro}
      />

      <div className={`relative flex h-44 items-center justify-center overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-gradient-to-br ${template.gradient}`}>
        <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-violet-500 to-fuchsia-500 shadow-2xl">
          <Icon className="h-10 w-10 text-white" />
        </div>
      </div>

      <FactTable
        title="Template at a glance"
        rows={template.facts.map((f) => ({ label: f.label, value: f.value }))}
      />

      <section className="space-y-3">
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          What you get
        </h2>
        <div className="grid gap-2 sm:grid-cols-2">
          {template.features.map((f) => (
            <div
              key={f}
              className="flex items-start gap-2.5 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-3 text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]"
            >
              <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" />
              {f}
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Quickstart
        </h2>
        <StepList steps={template.steps} />
      </section>

      <section className="flex flex-col gap-3 sm:flex-row">
        <a
          href={template.githubUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
        >
          <Github size={15} /> View on GitHub <ArrowUpRight size={14} />
        </a>
        {template.demoUrl && (
          <a
            href={template.demoUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            {template.demoLabel ?? "Live Demo"} <ArrowUpRight size={14} />
          </a>
        )}
      </section>

      <Card className="p-5">
        <span className="admin-label">Other templates</span>
        <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
          Every template in the gallery uses the same gateway surface — a base URL, a
          virtual key, and model names from your config. Browse the rest on the
          templates page.
        </p>
      </Card>

      <DetailCta
        title="Need a different starting point?"
        body="The gateway speaks your client's dialect already — retarget an existing app instead of forking a template."
        primary={{ label: "Read the docs", to: "/docs" }}
        secondary={{ label: "All templates", to: "/templates" }}
      />
    </div>
  );
}
