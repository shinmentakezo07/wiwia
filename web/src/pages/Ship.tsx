// Ship — ship an AI app in 10 minutes. Adapted from the llmgateway.io ship page
// with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const TEMPLATES: { name: string; description: string; command: string; tags: string[] }[] = [
  { name: "Embeddable Credits", description: "Monetize your AI app — a drop-in wallet and checkout so end-users buy credits and use AI in-app.", command: "npx @llmgateway/cli init --template embeddable-credits", tags: ["Next.js", "Embeddable SDK", "Monetization"] },
  { name: "AI Chatbot", description: "Streaming chat with conversation history and model switching.", command: "npx @llmgateway/cli init --template ai-chatbot", tags: ["Next.js", "AI SDK", "Streaming"] },
  { name: "Image Generation", description: "Generate images with DALL-E, Stable Diffusion, and more through a unified API.", command: "npx @llmgateway/cli init --template image-generation", tags: ["Next.js", "AI SDK", "Multi-provider"] },
  { name: "Writing Assistant", description: "Text actions including rewrite, summarize, expand, and tone adjustment.", command: "npx @llmgateway/cli init --template writing-assistant", tags: ["Next.js", "AI SDK", "Structured Output"] },
  { name: "Feedback Dashboard", description: "Sentiment analysis dashboard with batch AI analysis and key theme extraction.", command: "npx @llmgateway/cli init --template feedback-dashboard", tags: ["Next.js", "AI SDK", "Analytics"] },
  { name: "OG Image Generator", description: "AI-powered Open Graph image generator with live preview and themes.", command: "npx @llmgateway/cli init --template og-image-generator", tags: ["Next.js", "AI SDK", "Structured Output"] },
  { name: "QA Agent", description: "AI-powered QA testing agent with browser automation and real-time action timeline.", command: "npx @llmgateway/cli init --template qa-agent", tags: ["Next.js", "AI SDK", "Browser Automation"] },
  { name: "Showcase", description: "A static, deployable gallery of apps built with templates, with filtering and submissions.", command: "npx @llmgateway/cli init --template showcase", tags: ["Next.js", "Tailwind CSS", "Static"] },
];

const STEPS: { number: string; title: string; description: string; code: string }[] = [
  { number: "1", title: "Install the CLI", description: "One command to get the CLI.", code: "npm i -g @llmgateway/cli" },
  { number: "2", title: "Choose a Template", description: "Pick a template and clone it instantly.", code: "npx @llmgateway/cli init --template ai-chatbot" },
  { number: "3", title: "Add Your API Key & Deploy", description: "Set your API key and ship it.", code: 'echo "LLMGATEWAY_API_KEY=your_key" > .env.local && npm run dev' },
];

export function ShipPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Ship an AI App in{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            10 Minutes
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Production-ready templates powered by the gateway. Clone, configure, and deploy
          — with access to 200+ models from every major provider.
        </p>
        <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-semibold text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Get started free
          </Link>
          <Link
            to="/templates"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            Browse templates
          </Link>
        </div>
      </section>

      {/* ── steps ── */}
      <section>
        <h2 className="mb-6 text-center text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Three steps to production
        </h2>
        <div className="space-y-6">
          {STEPS.map((step) => (
            <div key={step.number} className="flex gap-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-brand-500 to-brand-700 text-[16px] font-bold text-white">
                {step.number}
              </div>
              <div className="flex-1 space-y-2">
                <h3 className="text-[16px] font-semibold text-[var(--admin-text)]">{step.title}</h3>
                <p className="text-[14px] text-[var(--admin-text-muted)]">{step.description}</p>
                <pre className="mt-2 overflow-x-auto rounded-lg border border-[var(--admin-border)] bg-zinc-950 p-4">
                  <code className="font-mono text-[13px] text-zinc-200" style={{ fontFamily: MONO }}>{step.code}</code>
                </pre>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── templates ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">Pick a template</h2>
          <p className="mt-2 text-[14px] text-[var(--admin-text-muted)]">
            Each template is a complete Next.js application with AI features built in.
            Clone and customize.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {TEMPLATES.map((template) => (
            <Card key={template.name} className="space-y-4 p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div>
                <h3 className="text-[15px] font-semibold text-[var(--admin-text)]">{template.name}</h3>
                <p className="mt-1 text-[13px] text-[var(--admin-text-muted)]">{template.description}</p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {template.tags.map((tag) => (
                  <span key={tag} className="admin-badge admin-badge-gray">{tag}</span>
                ))}
              </div>
              <pre className="overflow-x-auto rounded-lg bg-white/[0.02] p-3">
                <code className="font-mono text-[12px] text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>{template.command}</code>
              </pre>
            </Card>
          ))}
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center">
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">Ready to ship?</h2>
        <p className="mx-auto mt-2 max-w-xl text-[15px] text-[var(--admin-text-muted)]">
          Create a free account, grab an API key, and start building with any of our 200+
          supported models.
        </p>
        <div className="mt-5 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-semibold text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Create free account
          </Link>
          <Link
            to="/models"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            Explore models
          </Link>
        </div>
      </section>
    </div>
  );
}
