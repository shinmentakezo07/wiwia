// Products — product overview with all four products. Adapted from the
// llmgateway.io products sub-pages (AI Gateway, DevPass, Lounge, Observability),
// combined into a single page in the dark design system.

import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  AudioLines,
  ChartColumnBig,
  CircleDollarSign,
  Database,
  Film,
  Folder,
  Gauge,
  ImagePlus,
  KeyRound,
  MessageSquare,
  Network,
  PenTool,
  RefreshCw,
  Route,
  ScrollText,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Terminal,
  Users,
  UsersRound,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const CODE_EXAMPLE = `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://api.llmgateway.io/v1",
  apiKey: process.env.LLM_GATEWAY_API_KEY,
});

const completion = await client.chat.completions.create({
  model: "openai/gpt-5", // or anthropic/claude-*, google/gemini-*, ...
  messages: [{ role: "user", content: "Hello!" }],
});`;

// ── AI Gateway ──────────────────────────────────────────────────────────────

const AI_GATEWAY_FEATURES: { icon: LucideIcon; title: string; description: string }[] = [
  { icon: Network, title: "OpenAI-compatible API", description: "Keep your existing SDK and change the base URL — chat completions, embeddings, images, video, speech, and transcription all speak the same format." },
  { icon: Route, title: "Smart routing & fallback", description: "Route by price, latency, throughput, or uptime, and fall back to the next healthy provider automatically when one degrades." },
  { icon: Database, title: "Response caching", description: "Serve repeated requests from Redis-backed cache and pass provider cache controls through — cutting both latency and spend." },
  { icon: ShieldCheck, title: "Guardrails", description: "Prompt-injection protection, PII detection and redaction, secrets detection, and a custom rules engine — enforced at the gateway." },
  { icon: SlidersHorizontal, title: "Key management", description: "Centralized, encrypted provider keys with project-scoped API keys, usage and spending limits, and full audit trails." },
  { icon: Server, title: "Cloud or self-hosted", description: "Use the hosted gateway or deploy the AGPLv3-licensed source on your own infrastructure with Docker, Compose, or Kubernetes." },
];

// ── DevPass ─────────────────────────────────────────────────────────────────

const DEVPASS_FEATURES: { icon: LucideIcon; title: string; description: string }[] = [
  { icon: CircleDollarSign, title: "3× usage value", description: "Every dollar becomes $3 of model usage, metered transparently at provider rates. Lite $29/mo → $87, Pro $79/mo → $237, Max $179/mo → $537." },
  { icon: KeyRound, title: "One key, every agent", description: "DevPass Code, Claude Code, OpenCode, Cursor, Cline, Aider, Continue — set two env vars and any OpenAI-compatible tool is stamped in." },
  { icon: Terminal, title: "Every flagship model", description: "Claude Opus, GPT-5, Gemini Pro, plus the strongest open-weight coders — switch models freely mid-project with no extra cost." },
  { icon: Gauge, title: "Real-time metering", description: "A live dashboard shows per-request cost, model breakdowns, and your remaining allowance — no token math required." },
  { icon: RefreshCw, title: "Reset Passes", description: "Burned through your weekly premium allowance? Reset Passes instantly restore it — Pro and Max plans include them every month." },
  { icon: Wrench, title: "No lock-in", description: "Runs on the open-source gateway. Barely used your first month? Refund it yourself from the billing dashboard — no email, no cancellation fee." },
];

// ── Lounge ─────────────────────────────────────────────────────────────────

const LOUNGE_STUDIOS: { icon: LucideIcon; title: string; description: string }[] = [
  { icon: MessageSquare, title: "Chat", description: "Talk to GPT, Claude, Gemini, Grok, and 200+ more models in one conversation view. Fork conversations and share read-only snapshots via public links." },
  { icon: Users, title: "Group Chat", description: "Send the same prompt to any combination of models and watch responses stream side by side — compare latency, token counts, and cost per response." },
  { icon: ImagePlus, title: "Image Studio", description: "Generate images with DALL·E, Flux, Stable Diffusion, Seedream, and more. Create 1, 2, or 4 images per prompt and compare outputs in a grid." },
  { icon: Film, title: "Video Studio", description: "Create AI-generated videos with Sora, Veo, Kling, and more. Set resolution, duration, and audio options, then preview results inline." },
  { icon: AudioLines, title: "Audio Studio", description: "Generate speech with ElevenLabs, OpenAI TTS, and Gemini TTS. Pick from dozens of voices, control format and speed, and download the result." },
  { icon: PenTool, title: "Canvas", description: "Build UIs from JSON specs with a live preview, then export the result to PDF or PNG." },
  { icon: Folder, title: "Projects", description: "Organize chats and generated media into projects so long-running work stays grouped and easy to find." },
  { icon: ScrollText, title: "Skills", description: "Reusable instruction sets that shape how models respond — apply them to any conversation." },
];

// ── Observability ──────────────────────────────────────────────────────────

const OBSERVABILITY_FEATURES: { icon: LucideIcon; title: string; description: string }[] = [
  { icon: Activity, title: "Request-level activity", description: "Inspect every API request — prompt, response, tokens, cost, latency, finish reason, and the provider that served it." },
  { icon: CircleDollarSign, title: "Cost-aware analytics", description: "See requests, tokens, total spend, and average cost per 1K tokens across 7 or 30 days — split by credits and provider keys." },
  { icon: ChartColumnBig, title: "Model & provider breakdown", description: "Break down usage and spend by provider and model to spot expensive outliers quickly." },
  { icon: AlertTriangle, title: "Errors & reliability", description: "Monitor error rate, cache hit rate, and reliability trends directly from the dashboard — before your users notice." },
  { icon: UsersRound, title: "Org & member analytics", description: "Roll costs up across every project in your organization and break usage down per team member." },
  { icon: ScrollText, title: "Audit logs", description: "Track who did what, when — comprehensive audit trails for SOC 2 and HIPAA-ready compliance workflows." },
];

// ── product card helper ────────────────────────────────────────────────────

interface ProductHero {
  accent: string;
  eyebrow: string;
  title: string;
  subtitle: string;
  description: string;
  stats: { value: string; label: string }[];
  ctas: { label: string; to: string; external?: boolean }[];
}

const PRODUCTS: ProductHero[] = [
  {
    accent: "blue",
    eyebrow: "Product · AI Gateway",
    title: "One API for every LLM",
    subtitle: "200+ models. 40+ providers. Zero code changes.",
    description:
      "Stop juggling API keys and provider dashboards. The AI Gateway routes your requests across every major provider through one OpenAI-compatible endpoint — with smart routing, automatic fallback, caching, and guardrails built in.",
    stats: [
      { value: "200+", label: "Models" },
      { value: "40+", label: "Providers" },
      { value: "50B+", label: "Tokens routed" },
    ],
    ctas: [
      { label: "Get my API key", to: "/signup" },
      { label: "Read the docs", to: "/docs" },
    ],
  },
  {
    accent: "emerald",
    eyebrow: "Product · DevPass",
    title: "One key. Every model. Three flat prices.",
    subtitle: "All-access dev plans for AI coding.",
    description:
      "DevPass turns every dollar into $3 of model usage at provider rates — metered transparently, with no token math and no lock-in. Best in DevPass Code, our first-party agent, and drop-in for every OpenAI-compatible tool.",
    stats: [
      { value: "200+", label: "Models" },
      { value: "3×", label: "Usage value" },
      { value: "$29/mo", label: "Starting price" },
    ],
    ctas: [
      { label: "Get DevPass", to: "https://devpass.llmgateway.io", external: true },
      { label: "See pricing", to: "/pricing" },
    ],
  },
  {
    accent: "amber",
    eyebrow: "Product · Lounge",
    title: "Every frontier model. One membership.",
    subtitle: "The members' lounge for AI.",
    description:
      "Chat with GPT, Claude, and Gemini, generate images and video, and run multi-model group chats. Lounge replaces ChatGPT Plus, Claude Pro, and Gemini Advanced with one membership — starting at $9/mo.",
    stats: [
      { value: "200+", label: "Models" },
      { value: "6", label: "Studios" },
      { value: "$9/mo", label: "Starting price" },
    ],
    ctas: [
      { label: "Open Lounge", to: "/playground" },
      { label: "See pricing", to: "/pricing" },
    ],
  },
  {
    accent: "violet",
    eyebrow: "Product · Observability",
    title: "See every request. Know every cost.",
    subtitle: "Real-time analytics for your entire LLM stack.",
    description:
      "The gateway records every request that flows through it — usage, spend, latency, errors, and cache performance — and turns it into dashboards your whole team can act on. No agents to install, no separate tracing SDK.",
    stats: [
      { value: "100%", label: "Requests captured" },
      { value: "7/30d", label: "Analytics windows" },
      { value: "90d", label: "Enterprise retention" },
    ],
    ctas: [
      { label: "Open dashboard", to: "/dashboard" },
      { label: "Explore features", to: "/docs" },
    ],
  },
];

const accentGlow: Record<string, string> = {
  blue: "rgba(59,130,246,0.10)",
  emerald: "rgba(16,185,129,0.10)",
  amber: "rgba(217,119,6,0.10)",
  violet: "rgba(139,92,246,0.10)",
};

const accentText: Record<string, string> = {
  blue: "text-blue-400",
  emerald: "text-emerald-400",
  amber: "text-amber-400",
  violet: "text-violet-400",
};

function ProductHeroSection({ product }: { product: ProductHero }) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-72"
        style={{ background: `radial-gradient(70% 55% at 50% 0%, ${accentGlow[product.accent]}, transparent 70%)` }}
      />
      <div className="relative p-8 text-center md:p-12">
        <p className={`mb-4 text-[13px] font-semibold uppercase tracking-[0.14em] ${accentText[product.accent]}`}>
          {product.eyebrow}
        </p>
        <h2 className="text-[28px] font-semibold tracking-[-0.02em] text-[var(--admin-text)] md:text-[32px]">
          {product.title}
        </h2>
        <p className="mx-auto mt-3 max-w-md text-[16px] font-medium tracking-[-0.01em] text-[var(--admin-text)]/80">
          {product.subtitle}
        </p>
        <p className="mx-auto mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          {product.description}
        </p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
          {product.ctas.map((cta) => {
            const cls = cta === product.ctas[0]
              ? "inline-flex items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-2.5 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
              : "inline-flex items-center justify-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-6 py-2.5 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]";
            return cta.external ? (
              <a key={cta.label} href={cta.to} target="_blank" rel="noopener noreferrer" className={cls}>
                {cta.label}
                <ArrowUpRight size={16} />
              </a>
            ) : (
              <Link key={cta.label} to={cta.to} className={cls}>
                {cta.label}
                <ArrowRight size={16} />
              </Link>
            );
          })}
        </div>
        <div className="mx-auto mt-10 grid max-w-lg grid-cols-3 divide-x divide-[var(--admin-border)]">
          {product.stats.map((stat) => (
            <div key={stat.label} className="px-4">
              <div className="font-mono text-[20px] font-semibold tabular-nums text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
                {stat.value}
              </div>
              <div className="mt-1 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--admin-text-muted)]">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function FeatureGrid({ title, features, columns = 3 }: { title: string; features: { icon: LucideIcon; title: string; description: string }[]; columns?: 3 | 4 }) {
  return (
    <section>
      <h3 className="mb-5 text-center text-[20px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
        {title}
      </h3>
      <div className={`grid grid-cols-1 gap-4 md:grid-cols-2 ${columns === 3 ? "lg:grid-cols-3" : "lg:grid-cols-4"}`}>
        {features.map((feature) => {
          const Icon = feature.icon;
          return (
            <Card key={feature.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--admin-border)] bg-white/[0.02]">
                <Icon className="h-5 w-5 text-[var(--admin-text)]/80" strokeWidth={1.8} />
              </div>
              <h4 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">{feature.title}</h4>
              <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{feature.description}</p>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

function CodeBlock({ code, filename }: { code: string; filename?: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--admin-border)] bg-zinc-950">
      <div className="flex items-center gap-2 border-b border-[var(--admin-border)] bg-white/[0.02] px-4 py-3">
        <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
        <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
        <span className="h-3 w-3 rounded-full bg-[#28c840]" />
        {filename && <span className="ml-2 font-mono text-[12px] text-[var(--admin-text-dim)]">{filename}</span>}
      </div>
      <pre className="overflow-x-auto p-5">
        <code className="font-mono text-[13px] leading-relaxed text-zinc-200" style={{ fontFamily: MONO }}>
          {code}
        </code>
      </pre>
    </div>
  );
}

export function ProductsPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-12 pb-16">
      {/* ── page hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Products built for{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            production AI
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          One gateway, four products. Route every model, write code with flat-price plans,
          chat in a members lounge, and see every request — all from one platform.
        </p>
      </section>

      {/* ── AI Gateway ── */}
      <div className="space-y-8">
        <ProductHeroSection product={PRODUCTS[0]} />
        <section>
          <div className="mb-6 text-center">
            <h2 className="text-[22px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
              Migrate by changing one line
            </h2>
            <p className="mt-2 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Point your existing OpenAI SDK at the gateway. Everything else stays the same.
            </p>
          </div>
          <CodeBlock code={CODE_EXAMPLE} filename="app.ts" />
        </section>
        <FeatureGrid title="Built for production traffic" features={AI_GATEWAY_FEATURES} />
      </div>

      {/* ── DevPass ── */}
      <div className="space-y-8">
        <ProductHeroSection product={PRODUCTS[1]} />
        <FeatureGrid title="Why DevPass" features={DEVPASS_FEATURES} />
      </div>

      {/* ── Lounge ── */}
      <div className="space-y-8">
        <ProductHeroSection product={PRODUCTS[2]} />
        <FeatureGrid title="Everything in the Lounge" features={LOUNGE_STUDIOS} columns={4} />
      </div>

      {/* ── Observability ── */}
      <div className="space-y-8">
        <ProductHeroSection product={PRODUCTS[3]} />
        <FeatureGrid title="Observability that pays for itself" features={OBSERVABILITY_FEATURES} />
      </div>

      {/* ── final CTA ── */}
      <section className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-8 text-center md:p-12">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-48"
          style={{ background: "radial-gradient(70% 70% at 50% 100%, rgba(59,130,246,0.08), transparent 70%)" }}
        />
        <div className="relative">
          <h2 className="text-[26px] font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
            Ship with any model, today
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Bring your own provider keys for free, pay as you go with a flat 5% platform fee,
            or self-host the open-source gateway.
          </p>
          <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-500 to-brand-700 px-7 py-3 text-[15px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
            >
              Get my API key
              <ArrowRight size={16} />
            </Link>
            <Link
              to="/pricing"
              className="inline-flex items-center justify-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-7 py-3 text-[15px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              See pricing
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
