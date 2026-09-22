// Template catalog data for /templates and /templates/:slug. The gallery
// mirrors the template list adapted for the gateway; quickstarts use wiwi's
// OpenAI-compatible /v1 surface and virtual keys.

import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Image as ImageIcon,
  LayoutGrid,
  MessageSquare,
  PanelTop,
  PenLine,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import type { DetailStep } from "@/components/detail-page";

export interface TemplateDetail {
  slug: string;
  name: string;
  icon: LucideIcon;
  gradient: string;
  tags: string[];
  featured?: boolean;
  summary: string;
  intro: string;
  facts: { label: string; value: string }[];
  features: string[];
  steps: DetailStep[];
  githubUrl: string;
  demoUrl?: string;
  demoLabel?: string;
}

export const TEMPLATES: TemplateDetail[] = [
  {
    slug: "embeddable-credits",
    name: "Embeddable Credits",
    icon: Wallet,
    gradient: "from-emerald-500/20 via-teal-500/20 to-cyan-500/20",
    tags: ["TypeScript", "Next.js", "Embeddable SDK"],
    featured: true,
    summary:
      "Monetize your AI app in 5 minutes. Drop in a wallet + checkout so your end-users buy credits and use AI in-app, billed to their own balance.",
    intro:
      "The monetization template: a credit wallet and checkout embedded in any Next.js app, so your users pay for the AI they consume instead of you absorbing every token. Each in-app model call routes through the gateway with per-user virtual keys, so a heavy user burns their own balance, not yours — and the gateway's 402 on exhausted budget is your hard stop.",
    facts: [
      { label: "Stack", value: "Next.js 15 · TypeScript · Tailwind 4" },
      { label: "Billing model", value: "User credits → gateway virtual keys" },
      { label: "Time to running", value: "~5 minutes" },
      { label: "Gateway surface", value: "/v1/chat/completions" },
    ],
    features: [
      "Embedded wallet UI with balance, top-up, and spend history",
      "Checkout flow wired for usage-based credit packages",
      "One virtual key per user — per-key budgets and rate limits enforced at the gateway",
      "Powered-by badge and copy-paste SDK component",
    ],
    steps: [
      {
        title: "Clone and install",
        body: "Start from the template repo, install dependencies, and run the dev server locally.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/embeddable-credits
npm install && npm run dev`,
        },
      },
      {
        title: "Point it at your gateway",
        body: "The template reads a base URL and key from the environment. Give it your wiwi deployment and a virtual key minted for app traffic — the app then issues per-user budget-capped sub-keys through the admin API.",
        code: {
          label: ".env",
          code: `WIWI_BASE_URL=http://localhost:4000/v1
WIWI_APP_KEY=sk-wiwi-…        # virtual key, budget-capped
WIWI_MASTER_KEY=…            # server-side only: mint sub-keys`,
        },
      },
      {
        title: "Ship the checkout",
        body: "Wire any payment provider you like; the credit ledger is in the template's DB schema already. When a user runs out, the gateway rejects over-budget keys with 402 — your UI just renders the error as a top-up prompt.",
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/embeddable-credits",
  },
  {
    slug: "image-generation",
    name: "Image Generation",
    icon: ImageIcon,
    gradient: "from-violet-500/20 via-fuchsia-500/20 to-pink-500/20",
    tags: ["TypeScript", "Next.js", "AI SDK"],
    featured: true,
    summary:
      "Generate images with AI using multiple providers behind a unified API — one prompt, any backend.",
    intro:
      "A prompt-in, gallery-out image app that treats the provider as a detail: the same UI runs against whichever image-capable model your gateway config exposes, and switching backends is a model name, not a rewrite.",
    facts: [
      { label: "Stack", value: "Next.js · AI SDK UI" },
      { label: "Gateway surface", value: "Model-backed image gen" },
      { label: "Swap providers", value: "Change model_list entry" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    features: [
      "Prompt form with aspect-ratio and count controls",
      "Result gallery with download and regenerate",
      "Provider-agnostic via the gateway's model mapping",
      "Per-request cost visible in the gateway's logs",
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Standard Next.js install; the template ships with the gateway client pre-wired.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/image-generation
npm install && npm run dev`,
        },
      },
      {
        title: "Expose an image model",
        body: "Add an image-capable model to your wiwi.yaml model_list so the template's model picker has something to route to.",
      },
      {
        title: "Set the env",
        body: "Base URL and virtual key, same as every other template — the app never learns which provider answered.",
        code: {
          label: ".env.local",
          code: `WIWI_BASE_URL=http://localhost:4000/v1
WIWI_API_KEY=sk-wiwi-…`,
        },
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/image-generation",
    demoUrl: "https://llmgateway-templates-image-generation-124.meetploy.app",
  },
  {
    slug: "ai-chatbot",
    name: "AI Chatbot",
    icon: MessageSquare,
    gradient: "from-sky-500/20 via-blue-500/20 to-indigo-500/20",
    tags: ["TypeScript", "Next.js", "AI SDK"],
    featured: true,
    summary:
      "Streaming chat with conversation history and a live model selector. Switch providers mid-conversation.",
    intro:
      "The canonical streaming demo: token-by-token chat, persisted history, and a model dropdown wired to the gateway's model list — change the backing provider between messages without changing a line of app code.",
    facts: [
      { label: "Stack", value: "Next.js · AI SDK streamText" },
      { label: "Gateway surface", value: "SSE streaming, journals on by default" },
      { label: "Reconnect", value: "Resumes from the stream journal" },
      { label: "Time to running", value: "~5 minutes" },
    ],
    features: [
      "Real token streaming with stop/regenerate",
      "Conversation history in the app's own storage",
      "Model selector sourced from the gateway catalog",
      "Reconnect-safe: encoded frames journal server-side",
    ],
    steps: [
      {
        title: "Clone and run",
        body: "The chat UI needs only Node and an environment pair to work.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/ai-chatbot
npm install && npm run dev`,
        },
      },
      {
        title: "Point at the gateway",
        body: "Set the base URL to your deployment and paste a virtual key. The model list populates from GET /v1/models.",
        code: {
          label: ".env.local",
          code: `WIWI_BASE_URL=http://localhost:4000/v1
WIWI_API_KEY=sk-wiwi-…`,
        },
      },
      {
        title: "Try mid-thread switching",
        body: "Start a message in gpt-4o, switch to claude-sonnet-4, send again — history survives because both dialects map through the same IR.",
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/ai-chatbot",
    demoUrl: "https://llmgateway-templates-ai-chatbot-108.meetploy.app",
  },
  {
    slug: "og-image-generator",
    name: "OG Image Generator",
    icon: PanelTop,
    gradient: "from-orange-500/20 via-amber-500/20 to-yellow-500/20",
    tags: ["TypeScript", "Next.js", "AI SDK"],
    summary:
      "AI Open Graph image generator with live preview, themes, and one-click download using structured output.",
    intro:
      "Paste a URL or title; the model drafts the copy (title, subtitle, call-to-action) as structured output, and the app renders it onto a themed card with a live preview and PNG download. A tight demonstration of JSON-schema-constrained generation through the gateway.",
    facts: [
      { label: "Stack", value: "Next.js · Satori rendering" },
      { label: "Gateway feature", value: "Structured output, any provider" },
      { label: "Themes", value: "Pluggable card styles" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    features: [
      "Structured-output prompt with theme variables",
      "Live preview re-render as the copy edits",
      "One-click PNG export",
      "Model-agnostic: retarget the draft model in config",
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Install, set the environment pair, and the generator runs on your machine.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/og-image-generator
npm install && npm run dev`,
        },
      },
      {
        title: "Give it a drafting model",
        body: "The template calls whatever model you name — a cheap fast model is usually the right pick for copy drafts.",
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/og-image-generator",
    demoUrl: "https://llmgateway-templates-og-image-generator-926.meetploy.app",
  },
  {
    slug: "feedback-dashboard",
    name: "Feedback Dashboard",
    icon: BarChart3,
    gradient: "from-emerald-500/20 via-green-500/20 to-teal-500/20",
    tags: ["TypeScript", "Next.js", "AI SDK"],
    summary:
      "Paste reviews for batch sentiment analysis: scores, key themes, and an individual breakdown per review.",
    intro:
      "A batch-analysis demo: drop in customer reviews, get sentiment scores, extracted themes, and per-review breakdowns in a dashboard — structured output applied at the volume where a gateway's per-request cost tracking starts to matter.",
    facts: [
      { label: "Stack", value: "Next.js · AI SDK generateObject" },
      { label: "Pattern", value: "Batch structured extraction" },
      { label: "Cost truth", value: "Per-batch usage in gateway logs" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    features: [
      "CSV/paste ingestion with chunked batch requests",
      "Sentiment + theme rollups with confidence",
      "Per-review detail drill-down",
      "Model-per-stage: cheap batch model, expensive summary model",
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Same install dance; point it at the gateway and pick your batch model in settings.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/feedback-dashboard
npm install && npm run dev`,
        },
      },
      {
        title: "Route batch and summary separately",
        body: "Two model names in the config: an economy model for per-review scoring, a frontier model for the rollup summary. Both through one virtual key, both metered.",
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/feedback-dashboard",
    demoUrl: "https://llmgateway-templates-feedback-dashboard-189.meetploy.app",
  },
  {
    slug: "writing-assistant",
    name: "Writing Assistant",
    icon: PenLine,
    gradient: "from-rose-500/20 via-pink-500/20 to-fuchsia-500/20",
    tags: ["TypeScript", "Next.js", "AI SDK"],
    summary:
      "Text actions — rewrite, summarize, expand, grammar, tone — with selectable tone presets.",
    intro:
      "A focused editor with AI actions instead of a chat box: select text, choose an action and a tone preset, get a rewrite. Small surface, good demonstration of streaming partial edits and prompt presets kept out of application code.",
    facts: [
      { label: "Stack", value: "Next.js · TipTap editor" },
      { label: "Gateway feature", value: "Streaming rewrites" },
      { label: "Presets", value: "Tone profiles in config" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    features: [
      "Inline rewrite, summarize, expand, grammar fix",
      "Tone presets from casual to academic",
      "Token-streamed results into the editor selection",
      "Per-action model choice via gateway model names",
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Standard install; the editor is a single page with an actions toolbar.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/writing-assistant
npm install && npm run dev`,
        },
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/writing-assistant",
    demoUrl: "https://llmgateway-templates-writing-assistant-229.meetploy.app",
  },
  {
    slug: "qa-agent",
    name: "QA Agent",
    icon: ShieldCheck,
    gradient: "from-cyan-500/20 via-teal-500/20 to-blue-500/20",
    tags: ["TypeScript", "Next.js", "AI SDK"],
    summary:
      "Browser-automation QA: describe tests in plain English and watch the agent execute step-by-step.",
    intro:
      "An agentic tester: point it at a running web app, write the scenario in English, and it drives a real browser with tool calls — navigating, asserting, screenshotting — with a live action timeline. The deepest tool-calling demo in the gallery, and a reason to watch tool-dialect translation across providers.",
    facts: [
      { label: "Stack", value: "Next.js · Playwright · tool calling" },
      { label: "Gateway feature", value: "Tool-call translation, any provider" },
      { label: "Trace", value: "Per-request logs with tool calls" },
      { label: "Time to running", value: "~20 minutes" },
    ],
    features: [
      "Plain-English test scenarios compiled to steps",
      "Real browser execution with step timeline",
      "Screenshot evidence per assertion",
      "Works across providers because tools round-trip the IR",
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Install dependencies (Playwright downloads its browser on first run), then point at the gateway.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/qa-agent
npm install && npx playwright install
npm run dev`,
        },
      },
      {
        title: "Choose a tool-strong model",
        body: "Agentic flows reward strict tool-call validity. The gateway's OpenAI ↔ Anthropic tool translation means you can evaluate either family without app changes.",
      },
      {
        title: "Watch the receipts",
        body: "Every step's tool calls, arguments, and token usage land in the gateway's request logs — a QA report falls out of the observability you already run.",
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/qa-agent",
    demoUrl: "https://youtu.be/-ai9eVvXvZE",
    demoLabel: "Watch Demo",
  },
  {
    slug: "showcase",
    name: "Showcase",
    icon: LayoutGrid,
    gradient: "from-amber-500/20 via-orange-500/20 to-rose-500/20",
    tags: ["TypeScript", "Next.js", "Tailwind CSS"],
    summary:
      "A deployable gallery of apps built with the templates: filtering, submissions, and a powered-by badge.",
    intro:
      "Not an AI app itself — it's the directory you host for the apps you build. Tag and type filtering, a submit-your-app flow, and a powered-by badge convention, so a team (or a community) can display what the gateway actually powers.",
    facts: [
      { label: "Stack", value: "Static Next.js · Tailwind 4" },
      { label: "AI calls", value: "None — it's the gallery" },
      { label: "Deploy", value: "Any static host" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    features: [
      "Filterable card grid by tag and type",
      "Submission flow via issues/PRs",
      "Badge convention for powered-by footers",
      "Zero runtime dependencies on the gateway",
    ],
    steps: [
      {
        title: "Clone, fill the data file, deploy",
        body: "Entries live in a single typed data file — add your apps, build statically, host anywhere.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/templates/showcase
npm install && npm run build   # static export`,
        },
      },
    ],
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/templates/showcase",
  },
];

export function templateBySlug(slug: string): TemplateDetail | undefined {
  return TEMPLATES.find((t) => t.slug === slug);
}
