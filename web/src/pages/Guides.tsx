// Guides — step-by-step integration tutorials. Adapted from the llmgateway.io
// guides page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Card } from "@/components/ui";

interface Guide {
  slug: string;
  title: string;
  description: string;
  category: string;
}

const GUIDES: Guide[] = [
  { slug: "claude-code", title: "Claude Code", description: "Connect Claude Code to the gateway for AI-powered terminal assistance and coding with any model.", category: "Terminal" },
  { slug: "cursor", title: "Cursor IDE", description: "Use the gateway with Cursor in plan and agent mode. Tab autocomplete and inline edit stay on Cursor's backend.", category: "IDE" },
  { slug: "cline", title: "Cline (VS Code)", description: "Integrate the gateway with Cline for AI-powered coding assistance directly in VS Code.", category: "IDE" },
  { slug: "codex-cli", title: "Codex CLI", description: "Use the gateway with OpenAI's Codex CLI for AI-powered terminal coding.", category: "Terminal" },
  { slug: "devpass-code", title: "DevPass Code", description: "Open-source terminal coding agent built for the gateway. One browser login, every model.", category: "Terminal" },
  { slug: "n8n", title: "n8n Workflows", description: "Connect n8n workflow automation to the gateway for AI-powered automation pipelines.", category: "Automation" },
  { slug: "opencode", title: "OpenCode", description: "Use the gateway with OpenCode CLI for AI-powered development workflows.", category: "Terminal" },
  { slug: "continue", title: "Continue CLI", description: "Use the gateway with Continue's open-source AI code assistant CLI.", category: "Terminal" },
  { slug: "github-copilot", title: "GitHub Copilot app", description: "Use the gateway as a model provider in GitHub's Copilot desktop app for agent sessions with any model.", category: "Desktop" },
];

export function GuidesPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          {" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Guides
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Step-by-step tutorials to help you integrate the gateway with your favorite
          development tools and workflows.
        </p>
      </section>

      {/* ── guide cards ── */}
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {GUIDES.map((guide) => (
          <Link key={guide.slug} to={`/guides/${guide.slug}`} className="group">
            <Card className="h-full p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] text-[14px] font-bold text-[var(--admin-text)]">
                  {guide.title.charAt(0)}
                </div>
                <div className="flex-1 space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="admin-badge admin-badge-gray">{guide.category}</span>
                  </div>
                  <h3 className="text-[14px] font-semibold text-[var(--admin-text)] transition-colors group-hover:text-blue-400">
                    {guide.title}
                  </h3>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {guide.description}
                  </p>
                </div>
              </div>
              <div className="mt-3 flex items-center text-[13px] font-medium text-blue-400">
                Read guide
                <ArrowRight size={14} className="ml-1 transition-transform group-hover:translate-x-0.5" />
              </div>
            </Card>
          </Link>
        ))}
      </section>
    </div>
  );
}
