// Templates — production-ready AI app starters. Adapted from the llmgateway.io
// templates page with inlined data, in the dark design system.

import { useState, useCallback } from "react";
import {
  ArrowUpRight,
  Check,
  Code2,
  Copy,
  ExternalLink,
  Github,
  Image as ImageIcon,
  LayoutGrid,
  MessageSquare,
  PanelTop,
  BarChart3,
  PenLine,
  Play,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

interface Template {
  name: string;
  description: string;
  href: string;
  demoUrl?: string;
  demoLabel?: string;
  icon: LucideIcon;
  tags: string[];
  gradient: string;
  featured?: boolean;
}

const TEMPLATES: Template[] = [
  {
    name: "Embeddable Credits",
    description:
      "Monetize your AI app in 5 minutes. Drop in a wallet + checkout so your end-users buy credits and use AI in-app, billed to their own balance — the flagship monetization template.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/embeddable-credits",
    icon: Wallet,
    tags: ["TypeScript", "Next.js", "Embeddable SDK"],
    gradient: "from-emerald-500/20 via-teal-500/20 to-cyan-500/20",
    featured: true,
  },
  {
    name: "Image Generation",
    description:
      "Generate stunning images with AI using multiple providers. Supports DALL-E, Stable Diffusion, and more through a unified API.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/image-generation",
    demoUrl: "https://llmgateway-templates-image-generation-124.meetploy.app",
    icon: ImageIcon,
    tags: ["TypeScript", "Next.js", "AI SDK"],
    gradient: "from-violet-500/20 via-fuchsia-500/20 to-pink-500/20",
    featured: true,
  },
  {
    name: "AI Chatbot",
    description:
      "Streaming chat interface with conversation history and model selector. Switch between LLM providers on the fly with real-time token delivery.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/ai-chatbot",
    demoUrl: "https://llmgateway-templates-ai-chatbot-108.meetploy.app",
    icon: MessageSquare,
    tags: ["TypeScript", "Next.js", "AI SDK"],
    gradient: "from-sky-500/20 via-blue-500/20 to-indigo-500/20",
    featured: true,
  },
  {
    name: "OG Image Generator",
    description:
      "AI-powered Open Graph image generator with live preview, multiple themes, and one-click download. Uses structured output to generate title, subtitle, and call-to-action copy.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/og-image-generator",
    demoUrl: "https://llmgateway-templates-og-image-generator-926.meetploy.app",
    icon: PanelTop,
    tags: ["TypeScript", "Next.js", "AI SDK"],
    gradient: "from-orange-500/20 via-amber-500/20 to-yellow-500/20",
  },
  {
    name: "Feedback Dashboard",
    description:
      "Customer feedback sentiment analysis dashboard. Paste reviews for batch AI analysis with sentiment scores, key themes extraction, and individual review breakdowns.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/feedback-dashboard",
    demoUrl: "https://llmgateway-templates-feedback-dashboard-189.meetploy.app",
    icon: BarChart3,
    tags: ["TypeScript", "Next.js", "AI SDK"],
    gradient: "from-emerald-500/20 via-green-500/20 to-teal-500/20",
  },
  {
    name: "Writing Assistant",
    description:
      "AI writing assistant with text actions including rewrite, summarize, expand, fix grammar, and change tone. Supports multiple tone presets from professional to academic.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/writing-assistant",
    demoUrl: "https://llmgateway-templates-writing-assistant-229.meetploy.app",
    icon: PenLine,
    tags: ["TypeScript", "Next.js", "AI SDK"],
    gradient: "from-rose-500/20 via-pink-500/20 to-fuchsia-500/20",
  },
  {
    name: "QA Agent",
    description:
      "AI-powered QA testing agent that uses browser automation to interact with your running web app. Describe tests in plain English and watch it execute step-by-step with a real-time action timeline.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/qa-agent",
    demoUrl: "https://youtu.be/-ai9eVvXvZE",
    demoLabel: "Watch Demo",
    icon: ShieldCheck,
    tags: ["TypeScript", "Next.js", "AI SDK"],
    gradient: "from-cyan-500/20 via-teal-500/20 to-blue-500/20",
  },
  {
    name: "Showcase",
    description:
      "A static, deployable gallery of apps built with templates. Tag and type filtering, a Submit your app flow, and a Powered-By badge baked in — fork it or use the community directory.",
    href: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/showcase",
    icon: LayoutGrid,
    tags: ["TypeScript", "Next.js", "Tailwind CSS"],
    gradient: "from-amber-500/20 via-orange-500/20 to-rose-500/20",
  },
];

export function TemplatesPage() {
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
          AI App{" "}
          <span className="bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
            Templates
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Production-ready templates to help you build AI-powered applications faster.
          Clone, customize, and deploy.
        </p>
      </section>

      {/* ── template cards ── */}
      <section className="grid gap-6 sm:grid-cols-2">
        {TEMPLATES.map((template) => {
          const Icon = template.icon;
          return (
            <Card
              key={template.name}
              className="group relative flex flex-col overflow-hidden transition-colors hover:border-[var(--admin-border-hover)]"
            >
              {template.featured && (
                <div className="absolute right-3 top-3 z-10">
                  <Badge tone="violet">
                    <Sparkles size={12} className="mr-1" />
                    Featured
                  </Badge>
                </div>
              )}
              <div className="relative flex h-40 items-center justify-center overflow-hidden">
                <div className={`flex h-full w-full items-center justify-center bg-gradient-to-br ${template.gradient}`}>
                  <Icon className="h-16 w-16 text-[var(--admin-text)]/70" />
                </div>
              </div>
              <div className="relative flex flex-1 flex-col space-y-4 p-5">
                <div className="space-y-2">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 shadow-lg">
                      <Icon className="h-5 w-5 text-white" />
                    </div>
                    <h3 className="text-[18px] font-bold tracking-tight text-[var(--admin-text)]">{template.name}</h3>
                  </div>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{template.description}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {template.tags.map((tag) => (
                    <span key={tag} className="admin-badge admin-badge-gray">
                      <Code2 size={12} className="mr-1" />
                      {tag}
                    </span>
                  ))}
                </div>
                <div className="mt-auto flex flex-col gap-2 pt-2 sm:flex-row">
                  {template.demoUrl && (
                    <a
                      href={template.demoUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex flex-1 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
                    >
                      {template.demoLabel ? <Play size={14} /> : <ExternalLink size={14} />}
                      {template.demoLabel ?? "Live Demo"}
                      <ArrowUpRight size={14} />
                    </a>
                  )}
                  <a
                    href={template.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`inline-flex items-center justify-center gap-2 rounded-[10px] px-4 py-2 text-[13px] font-semibold transition-colors ${
                      template.demoUrl
                        ? "border border-white/[0.08] bg-white/[0.02] text-[var(--admin-text)] hover:bg-white/[0.04]"
                        : "flex-1 bg-gradient-to-b from-brand-500 to-brand-700 text-white transition-[filter] hover:brightness-110"
                    }`}
                  >
                    <Github size={14} />
                    GitHub
                  </a>
                  <button
                    onClick={() => copyToClipboard(template.href)}
                    className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
                  >
                    {copiedUrl === template.href ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                    {copiedUrl === template.href ? "Copied!" : "Clone"}
                  </button>
                </div>
              </div>
            </Card>
          );
        })}

        {/* placeholder card */}
        <Card className="relative flex flex-col items-center justify-center overflow-hidden border-2 border-dashed p-6 text-center">
          <div className="space-y-4 py-8">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-[var(--admin-border)] bg-white/[0.02]">
              <Sparkles className="h-8 w-8 text-[var(--admin-text-dim)]" />
            </div>
            <div className="space-y-1">
              <h3 className="text-[16px] font-semibold text-[var(--admin-text-muted)]">More coming soon</h3>
              <p className="text-[13px] text-[var(--admin-text-dim)]">We&apos;re working on more templates. Have a suggestion?</p>
            </div>
            <a
              href="https://github.com/theopenco/llmgateway-templates/issues/new"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              Request a template
            </a>
          </div>
        </Card>
      </section>

      {/* ── showcase + powered-by ── */}
      <section className="grid gap-4 md:grid-cols-2">
        <Card className="flex flex-col p-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-amber-500 to-rose-500 shadow-lg">
            <LayoutGrid className="h-6 w-6 text-white" />
          </div>
          <h3 className="mt-5 text-[18px] font-bold tracking-tight text-[var(--admin-text)]">
            Built something? Get featured.
          </h3>
          <p className="mt-2 flex-1 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Ship an app on any template and add it to the Showcase — a public, filterable
            gallery of apps built with the gateway. It&apos;s a deployable template itself,
            so you can host your own.
          </p>
          <div className="mt-5 flex flex-col gap-2 sm:flex-row">
            <a
              href="https://github.com/theopenco/llmgateway-templates/issues/new?template=showcase-submission.yml"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
            >
              Submit your app
            </a>
            <a
              href="https://github.com/theopenco/llmgateway-templates/tree/main/templates/showcase"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              View the showcase
            </a>
          </div>
        </Card>
        <Card className="flex flex-col p-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-500 to-cyan-500 shadow-lg">
            <Wallet className="h-6 w-6 text-white" />
          </div>
          <h3 className="mt-5 text-[18px] font-bold tracking-tight text-[var(--admin-text)]">
            Add the Powered-By badge
          </h3>
          <p className="mt-2 flex-1 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Every app you deploy can carry a small &ldquo;Powered by the gateway&rdquo;
            badge. It ships with the embeddable SDK (
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[12px]" style={{ fontFamily: MONO }}>
              &lt;PoweredBy /&gt;
            </code>
            ) and as a dependency-free copy you can drop into any footer.
          </p>
          <div className="mt-5">
            <a
              href="https://docs.llmgateway.io/features/llm-sdk"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              Read the SDK docs
            </a>
          </div>
        </Card>
      </section>
    </div>
  );
}
