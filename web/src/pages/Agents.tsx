// Agents — pre-built tool-calling AI agents. Adapted from the llmgateway.io
// agents page with inlined data, in the dark design system.

import { useState, useCallback } from "react";
import {
  ArrowUpRight,
  Bot,
  Check,
  CloudSun,
  Code2,
  Copy,
  FileText,
  Github,
  Mail,
  ScanText,
  SmilePlus,
  UserSearch,
  Wrench,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

interface Agent {
  name: string;
  description: string;
  href: string;
  icon: LucideIcon;
  capabilities: string[];
  tags: string[];
  featured?: boolean;
}

const AGENTS: Agent[] = [
  {
    name: "Weather Agent",
    description:
      "An intelligent AI agent that provides real-time weather information using tool calling. Demonstrates function calling patterns.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/weather-agent",
    icon: CloudSun,
    capabilities: ["Tool Calling", "Real-time Data", "Natural Language"],
    tags: ["TypeScript", "AI SDK", "OpenAI"],
    featured: true,
  },
  {
    name: "Lead Agent",
    description:
      "A CLI AI agent that researches a person by name or email and produces a structured profile summary including bio, role, background, and social links.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/lead-agent",
    icon: UserSearch,
    capabilities: ["Web Search", "Profile Research", "Discord Integration"],
    tags: ["TypeScript", "AI SDK", "Perplexity"],
  },
  {
    name: "Changelog Generator",
    description:
      "Generates structured changelogs from git history using the Keep a Changelog format. Analyzes git log and diff with tools to produce categorized output.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/changelog-generator-agent",
    icon: FileText,
    capabilities: ["Tool Calling", "Git Analysis", "Structured Output"],
    tags: ["TypeScript", "AI SDK", "Zod"],
  },
  {
    name: "Email Drafter",
    description:
      "Drafts polished emails from rough notes or bullet points with configurable tone. Returns structured output with subject, body, and sign-off.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/email-drafter-agent",
    icon: Mail,
    capabilities: ["Structured Output", "Tone Control", "Text Generation"],
    tags: ["TypeScript", "AI SDK", "Zod"],
  },
  {
    name: "Sentiment Analyzer",
    description:
      "Analyzes text sentiment with confidence scores and key phrase extraction. Supports direct text input or file paths and classifies as positive, negative, neutral, or mixed.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/sentiment-analyzer-agent",
    icon: SmilePlus,
    capabilities: ["Sentiment Analysis", "Key Phrases", "File Input"],
    tags: ["TypeScript", "AI SDK", "Zod"],
  },
  {
    name: "Data Extractor",
    description:
      "Extracts structured entities from unstructured text including people, organizations, dates, monetary amounts, locations, emails, and phone numbers.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/data-extractor-agent",
    icon: ScanText,
    capabilities: ["Entity Extraction", "Structured Output", "NLP"],
    tags: ["TypeScript", "AI SDK", "Zod"],
  },
];

export function AgentsPage() {
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);

  const copyToClipboard = useCallback((url: string) => {
    const templateName = url.split("/").pop();
    navigator.clipboard.writeText(`npx @llmgateway/cli init --template ${templateName}`);
    setCopiedUrl(url);
    setTimeout(() => setCopiedUrl(null), 2000);
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
          Pre-built AI agents with tool calling capabilities. Ready to integrate and
          extend for your specific needs.
        </p>
      </section>

      {/* ── agent cards ── */}
      <section className="grid gap-6 sm:grid-cols-2">
        {AGENTS.map((agent) => {
          const Icon = agent.icon;
          return (
            <Card
              key={agent.name}
              className="group relative overflow-hidden transition-colors hover:border-[var(--admin-border-hover)]"
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
                <div className="relative">
                  <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-sky-500 to-cyan-500 shadow-lg">
                    <Icon className="h-7 w-7 text-white" />
                  </div>
                </div>
                <div className="space-y-2">
                  <h3 className="text-[20px] font-bold tracking-tight text-[var(--admin-text)]">{agent.name}</h3>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{agent.description}</p>
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
                  <a
                    href={agent.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
                  >
                    <Github size={14} />
                    View on GitHub
                    <ArrowUpRight size={14} />
                  </a>
                  <button
                    onClick={() => copyToClipboard(agent.href)}
                    className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
                  >
                    {copiedUrl === agent.href ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                    {copiedUrl === agent.href ? "Copied!" : "Clone"}
                  </button>
                </div>
              </div>
            </Card>
          );
        })}

        {/* placeholder card */}
        <Card className="relative flex flex-col items-center justify-center overflow-hidden border-2 border-dashed p-5">
          <div className="space-y-4 py-8 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--admin-border)] bg-white/[0.02]">
              <Bot className="h-7 w-7 text-[var(--admin-text-dim)]" />
            </div>
            <div className="space-y-1">
              <h3 className="text-[16px] font-semibold text-[var(--admin-text-muted)]">More agents coming soon</h3>
              <p className="text-[13px] text-[var(--admin-text-dim)]">
                We&apos;re building more agents with different capabilities. Have an idea?
              </p>
            </div>
            <a
              href="https://github.com/theopenco/llmgateway-templates/issues/new"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              Request an agent
            </a>
          </div>
        </Card>
      </section>
    </div>
  );
}
