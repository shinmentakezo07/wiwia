// AgentDetail — per-agent page for /agents/:slug.

import { useParams } from "react-router-dom";
import { ArrowUpRight, Github, Wrench } from "lucide-react";
import { Card } from "@/components/ui";
import {
  DetailCta,
  DetailHeader,
  DetailNotFound,
  FactTable,
  StepList,
} from "@/components/detail-page";
import { agentBySlug } from "@/data/agents";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export function AgentDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const agent = slug ? agentBySlug(slug) : undefined;

  if (!agent) {
    return <DetailNotFound backTo="/agents" backLabel="All agents" what="Agent" />;
  }

  const Icon = agent.icon;

  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <DetailHeader
        backTo="/agents"
        backLabel="All agents"
        badge={agent.tags[1] ?? "Agent"}
        title={agent.name}
        intro={agent.intro}
      />

      <div className="relative flex h-40 items-center justify-center overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-gradient-to-br from-sky-500/15 via-cyan-500/10 to-blue-500/15">
        <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-sky-500 to-cyan-500 shadow-2xl">
          <Icon className="h-10 w-10 text-white" />
        </div>
      </div>

      <FactTable
        title="Agent at a glance"
        rows={agent.facts.map((f) => ({ label: f.label, value: f.value }))}
      />

      <section className="space-y-3">
        <h2 className="flex items-center gap-2 text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          <Wrench size={16} className="text-blue-400" /> Tools it calls
        </h2>
        <div className="space-y-2">
          {agent.tools.map((tool) => (
            <Card key={tool.name} className="flex flex-col gap-1 p-4 sm:flex-row sm:items-baseline sm:gap-4">
              <code
                className="shrink-0 rounded-md border border-white/[0.075] bg-white/[0.045] px-2 py-0.5 text-[12.5px] text-violet-200"
                style={{ fontFamily: MONO }}
              >
                {tool.name}
              </code>
              <p className="text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">{tool.desc}</p>
            </Card>
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Run it
        </h2>
        <StepList steps={agent.steps} />
      </section>

      <section>
        <Card className="p-5">
          <span className="admin-label">Why it works on any provider</span>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
            {agent.why}
          </p>
        </Card>
      </section>

      <div className="flex flex-col gap-3 sm:flex-row">
        <a
          href={agent.githubUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
        >
          <Github size={15} /> View on GitHub <ArrowUpRight size={14} />
        </a>
      </div>

      <DetailCta
        title="Give it a key"
        body="Every agent runs on a virtual key. Mint one, set a budget, and start the loop."
        primary={{ label: "Read the docs", to: "/docs" }}
        secondary={{ label: "Open the console", to: "/console" }}
      />
    </div>
  );
}
