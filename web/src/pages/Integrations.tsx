// Integrations — connect the gateway with your favorite tools. Adapted from
// the llmgateway.io integrations page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight, Clock, Terminal, Zap } from "lucide-react";
import { Badge, Card } from "@/components/ui";

interface Integration {
  name: string;
  description: string;
  href: string;
  comingSoon: boolean;
  badge?: string;
}

const INTEGRATIONS: Integration[] = [
  { name: "DevPass Code", description: "Open-source terminal coding agent built for the gateway. One browser login, every model, no per-provider keys.", href: "/guides/devpass-code", comingSoon: false },
  { name: "Claude Code", description: "Use the gateway with Claude Code for AI-powered terminal assistance and coding.", href: "/guides/claude-code", comingSoon: false },
  { name: "Cursor", description: "Use the gateway with Cursor IDE in plan and agent mode. Tab autocomplete and inline edit stay on Cursor's backend.", href: "/docs/cursor", comingSoon: false, badge: "Plan + Agent mode" },
  { name: "Codex CLI", description: "Use the gateway with OpenAI's Codex CLI for AI-powered terminal coding.", href: "/guides/codex-cli", comingSoon: false },
  { name: "Cline", description: "Use the gateway with Cline for AI-powered coding assistance in VS Code.", href: "/docs/cline", comingSoon: false },
  { name: "Continue CLI", description: "Use the gateway with Continue's open-source AI code assistant CLI.", href: "/guides/continue", comingSoon: false },
  { name: "GitHub Copilot app", description: "Use the gateway as a model provider in GitHub's Copilot desktop app for agent sessions with any model.", href: "/guides/github-copilot", comingSoon: false, badge: "BYOK" },
  { name: "n8n", description: "Connect n8n workflow automation to the gateway for AI-powered workflows.", href: "/docs/n8n", comingSoon: false },
  { name: "OpenCode", description: "Use the gateway with OpenCode CLI for AI-powered development workflows.", href: "/guides/opencode", comingSoon: false },
  { name: "VS Code", description: "Native VS Code integration for AI-powered code completion and chat.", href: "#", comingSoon: true },
];

export function IntegrationsPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
         {" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Integrations
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Connect the gateway with your favorite tools and workflows. Access powerful AI
          capabilities wherever you work.
        </p>
      </section>

      {/* ── DevPass CTA ── */}
      <a
        href="https://devpass.llmgateway.io"
        target="_blank"
        rel="noopener noreferrer"
        className="group relative block overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] transition-all hover:border-[var(--admin-border-hover)]"
      >
        <div className="relative flex flex-col gap-6 p-6 md:flex-row md:items-center md:justify-between md:gap-10">
          <div className="flex-1 space-y-3">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--admin-text)] text-[var(--admin-bg)]">
                <Terminal size={18} strokeWidth={1.5} />
              </div>
              <h3 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                DevPass
              </h3>
              <Badge tone="blue">New</Badge>
            </div>
            <p className="max-w-lg text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Fixed-price monthly plans for Claude Code, Cursor, Cline, and every coding
              tool. One API key, 200+ models, predictable billing.
            </p>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px] text-[var(--admin-text-muted)]">
              <span className="flex items-center gap-1.5">
                <Zap size={14} />
                From $29/mo
              </span>
              <span className="hidden text-[var(--admin-text-dim)] sm:inline">|</span>
              <span>Every model included</span>
            </div>
          </div>
          <div className="shrink-0">
            <span className="inline-flex items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-2.5 text-[14px] font-medium text-white">
              Get started
              <ArrowRight size={16} className="transition-transform duration-300 group-hover:translate-x-0.5" />
            </span>
          </div>
        </div>
      </a>

      {/* ── integration cards ── */}
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {INTEGRATIONS.map((integration) => {
          const isExternal = integration.href.startsWith("http");
          const cardContent = (
            <Card
              className={`relative h-full p-5 transition-all ${
                integration.comingSoon ? "cursor-not-allowed opacity-60" : "hover:border-[var(--admin-border-hover)]"
              }`}
            >
              {integration.comingSoon && (
                <span className="admin-badge admin-badge-gray absolute right-3 top-3 inline-flex items-center gap-1">
                  <Clock size={12} />
                  Coming soon
                </span>
              )}
              {integration.badge && !integration.comingSoon && (
                <span className="admin-badge admin-badge-blue absolute right-3 top-3">
                  {integration.badge}
                </span>
              )}
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] text-[14px] font-bold text-[var(--admin-text)]">
                  {integration.name.charAt(0)}
                </div>
                <div className="flex-1 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[14px] font-semibold text-[var(--admin-text)]">{integration.name}</h3>
                    {!integration.comingSoon && (
                      <ArrowRight
                        size={14}
                        className="text-[var(--admin-text-dim)] opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100"
                      />
                    )}
                  </div>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {integration.description}
                  </p>
                </div>
              </div>
            </Card>
          );

          if (integration.comingSoon) {
            return <div key={integration.name}>{cardContent}</div>;
          }
          if (isExternal) {
            return (
              <a
                key={integration.name}
                href={integration.href}
                target="_blank"
                rel="noopener noreferrer"
                className="group"
              >
                {cardContent}
              </a>
            );
          }
          return (
            <Link key={integration.name} to={integration.href} className="group">
              {cardContent}
            </Link>
          );
        })}
      </section>
    </div>
  );
}
