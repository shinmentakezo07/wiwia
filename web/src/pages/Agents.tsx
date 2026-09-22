// Agents — pre-built tool-calling AI agents. Listing backed by the shared
// agents data module; each card opens its page at /agents/:slug.

import { useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  Code2,
  Copy,
  Github,
  Wrench,
  Zap,
} from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { AGENTS } from "@/data/agents";

export function AgentsPage() {
  const [copiedSlug, setCopiedSlug] = useState<string | null>(null);

  const copyCloneCmd = useCallback((slug: string) => {
    navigator.clipboard.writeText(
      `git clone --depth 1 https://github.com/theopenco/llmgateway-templates.git\n` +
        `cd llmgateway-templates/agents/${slug}`,
    );
    setCopiedSlug(slug);
    setTimeout(() => setCopiedSlug(null), 2000);
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          AI{" "}
          <span className="bg-gradient-to-r from-sky-400 to-cyan-400 bg-clip-text text-transparent">
            Agents
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Pre-built AI agents with tool calling capabilities. Each one runs against
          the gateway&apos;s OpenAI-compatible surface — swap the backing provider
          without touching the agent.
        </p>
      </section>

      {/* ── agent cards ── */}
      <section className="grid gap-6 sm:grid-cols-2">
        {AGENTS.map((agent) => {
          const Icon = agent.icon;
          return (
            <Card
              key={agent.slug}
              className="group relative flex flex-col overflow-hidden transition-colors hover:border-[var(--admin-border-hover)]"
            >
              {agent.featured && (
                <div className="absolute right-3 top-3 z-10">
                  <Badge tone="blue">
                    <Zap size={12} className="mr-1" />
                    Featured
                  </Badge>
                </div>
              )}
              <div className="space-y-5 p-5">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-cyan-500 shadow-lg">
                  <Icon className="h-7 w-7 text-white" />
                </div>
                <div className="space-y-2">
                  <Link
                    to={`/agents/${agent.slug}`}
                    className="block text-[20px] font-bold tracking-tight text-[var(--admin-text)] transition-colors hover:text-blue-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                  >
                    {agent.name}
                  </Link>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {agent.summary}
                  </p>
                </div>
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-[13px] font-medium text-[var(--admin-text-muted)]">
                    <Wrench size={14} />
                    Capabilities
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {agent.capabilities.map((cap) => (
                      <span key={cap} className="admin-badge admin-badge-blue">
                        {cap}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {agent.tags.map((tag) => (
                    <span key={tag} className="admin-badge admin-badge-gray">
                      <Code2 size={12} className="mr-1" />
                      {tag}
                    </span>
                  ))}
                </div>
                <div className="flex flex-col gap-2 pt-1 sm:flex-row">
                  <Link
                    to={`/agents/${agent.slug}`}
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
                  >
                    Details <ArrowRight size={14} />
                  </Link>
                  <a
                    href={agent.githubUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
                  >
                    <Github size={14} />
                    GitHub
                  </a>
                  <button
                    type="button"
                    onClick={() => copyCloneCmd(agent.slug)}
                    aria-label={`Copy clone command for ${agent.name}`}
                    className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                  >
                    {copiedSlug === agent.slug ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                    {copiedSlug === agent.slug ? "Copied!" : "Clone"}
                  </button>
                </div>
              </div>
            </Card>
          );
        })}
      </section>

      {/* ── request one ── */}
      <Card className="flex flex-col items-center gap-4 p-8 text-center">
        <h3 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Need an agent we don&apos;t have?
        </h3>
        <p className="max-w-md text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Every agent here is a tool loop against one endpoint — most ideas are a day
          of work. Ask for one, or build yours on the same shape.
        </p>
        <a
          href="https://github.com/theopenco/llmgateway-templates/issues/new"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
        >
          Request an agent
        </a>
      </Card>
    </div>
  );
}
