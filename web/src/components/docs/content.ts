// Docs content registry — imports the real markdown files from /docs via
// Vite `?raw` imports (aliased as @docs) and maps them into the docs UI:
// slugs, categories, search keywords, and reading order for prev/next
// pagination. Adding a doc = drop the .md in /docs and add one entry here.

import {
  Boxes,
  Cpu,
  Flag,
  Gauge,
  KeyRound,
  Layers,
  Map,
  Network,
  Settings2,
  Shield,
  Terminal,
  Zap,
  type LucideIcon,
} from "lucide-react";

import quickstartMd from "@docs/QUICKSTART.md?raw";
import architectureMd from "@docs/ARCHITECTURE.md?raw";
import coreMd from "@docs/CORE.md?raw";
import streamingMd from "@docs/STREAMING.md?raw";
import streamingPerfMd from "@docs/STREAMING_PERFORMANCE_RECOVERY.md?raw";
import configMd from "@docs/CONFIG.md?raw";
import apiRefMd from "@docs/API_REFERENCE.md?raw";
import adminMd from "@docs/ADMIN.md?raw";
import providersMd from "@docs/PROVIDERS.md?raw";
import developmentMd from "@docs/DEVELOPMENT.md?raw";
import techstackMd from "@docs/TECHSTACK.md?raw";
import mvpMd from "@docs/MVP.md?raw";
import planMd from "@docs/PLAN.md?raw";

export interface DocMeta {
  slug: string;
  /** Original filename stem in /docs (used to resolve .md cross-links). */
  stem: string;
  title: string;
  category: string;
  description: string;
  icon: LucideIcon;
  keywords: string[];
  md: string;
}

export const DOCS: DocMeta[] = [
  {
    slug: "quickstart",
    stem: "QUICKSTART",
    title: "Quickstart",
    category: "Getting started",
    description: "Install, configure, and run the gateway locally in under a minute.",
    icon: Zap,
    keywords: ["install", "run", "uvicorn", "pip", "uv", "start", "setup", "port"],
    md: quickstartMd,
  },
  {
    slug: "architecture",
    stem: "ARCHITECTURE",
    title: "Architecture",
    category: "Core concepts",
    description: "The hub-and-spoke pipeline: wire codecs, canonical IR, adapters, router.",
    icon: Boxes,
    keywords: ["pipeline", "ir", "router", "failover", "retries", "design", "overview"],
    md: architectureMd,
  },
  {
    slug: "core",
    stem: "CORE",
    title: "Core Internals",
    category: "Core concepts",
    description: "Module-by-module walkthrough of the gateway internals.",
    icon: Layers,
    keywords: ["internals", "modules", "context", "gateway", "code", "source"],
    md: coreMd,
  },
  {
    slug: "streaming",
    stem: "STREAMING",
    title: "Streaming",
    category: "Core concepts",
    description: "SSE pipeline, the IRStreamDelta contract, failover, journals, and resume.",
    icon: Network,
    keywords: ["sse", "stream", "deltas", "tape", "journal", "resume", "reconnect"],
    md: streamingMd,
  },
  {
    slug: "streaming-performance",
    stem: "STREAMING_PERFORMANCE_RECOVERY",
    title: "Streaming Performance",
    category: "Core concepts",
    description: "How a stalled streaming path was diagnosed and recovered — methodology included.",
    icon: Gauge,
    keywords: ["performance", "ttft", "latency", "benchmark", "postmortem", "recovery"],
    md: streamingPerfMd,
  },
  {
    slug: "configuration",
    stem: "CONFIG",
    title: "Configuration",
    category: "Configuration",
    description: "The wiwi.yaml reference: providers, model_list, router, auth, database.",
    icon: Settings2,
    keywords: ["yaml", "wiwi.yaml", "providers", "model_list", "router_settings", "env"],
    md: configMd,
  },
  {
    slug: "api-reference",
    stem: "API_REFERENCE",
    title: "API Reference",
    category: "API & Reference",
    description: "Every client-facing HTTP surface: chat completions, responses, messages, tokens.",
    icon: Terminal,
    keywords: ["endpoints", "http", "rest", "chat completions", "responses", "messages", "curl"],
    md: apiRefMd,
  },
  {
    slug: "admin-api",
    stem: "ADMIN",
    title: "Admin API",
    category: "API & Reference",
    description: "Master-key admin surface: virtual keys, users, config, budgets, metrics.",
    icon: Shield,
    keywords: ["master key", "keys", "users", "budgets", "spend", "prometheus", "metrics"],
    md: adminMd,
  },
  {
    slug: "providers",
    stem: "PROVIDERS",
    title: "Providers",
    category: "API & Reference",
    description: "All eleven provider adapters, their config shape, and per-provider quirks.",
    icon: Boxes,
    keywords: ["openai", "anthropic", "gemini", "openrouter", "nim", "adapters", "keys"],
    md: providersMd,
  },
  {
    slug: "development",
    stem: "DEVELOPMENT",
    title: "Development",
    category: "Development",
    description: "Tooling, test suite, lint gate, and contribution workflow.",
    icon: KeyRound,
    keywords: ["pytest", "ruff", "bun", "vite", "contributing", "tests", "build"],
    md: developmentMd,
  },
  {
    slug: "tech-stack",
    stem: "TECHSTACK",
    title: "Tech Stack",
    category: "Project",
    description: "The chosen stack and the reasoning behind each dependency.",
    icon: Cpu,
    keywords: ["fastapi", "react", "tailwind", "sqlalchemy", "stack", "dependencies"],
    md: techstackMd,
  },
  {
    slug: "mvp",
    stem: "MVP",
    title: "MVP Scope",
    category: "Project",
    description: "The original v0.1 scope record — what was promised and what shipped.",
    icon: Flag,
    keywords: ["scope", "goals", "milestones", "history", "v0.1"],
    md: mvpMd,
  },
  {
    slug: "roadmap",
    stem: "PLAN",
    title: "Roadmap",
    category: "Project",
    description: "The plan: sequenced phases from simple proxy to full gateway platform.",
    icon: Map,
    keywords: ["plan", "phases", "future", "todo", "backlog"],
    md: planMd,
  },
];

/** Category display order + section icons. */
export const DOC_CATEGORIES: { name: string; icon: LucideIcon; docs: DocMeta[] }[] = [
  { name: "Getting started", icon: Zap, docs: [] },
  { name: "Core concepts", icon: Boxes, docs: [] },
  { name: "Configuration", icon: Settings2, docs: [] },
  { name: "API & Reference", icon: Terminal, docs: [] },
  { name: "Development", icon: KeyRound, docs: [] },
  { name: "Project", icon: Map, docs: [] },
];
for (const d of DOCS) {
  const cat = DOC_CATEGORIES.find((c) => c.name === d.category);
  if (cat) cat.docs.push(d);
}

export function getDoc(slug: string): DocMeta | undefined {
  return DOCS.find((d) => d.slug === slug);
}

/** Neighbors in reading order — powers the prev/next pager. */
export function getNeighbors(slug: string): { prev?: DocMeta; next?: DocMeta; index: number } {
  const index = DOCS.findIndex((d) => d.slug === slug);
  if (index < 0) return { index: -1 };
  return {
    index,
    prev: index > 0 ? DOCS[index - 1] : undefined,
    next: index < DOCS.length - 1 ? DOCS[index + 1] : undefined,
  };
}

/** Resolve a markdown link target ("CORE.md", "../X.md") to a /docs/<slug>. */
export function docHrefToSlug(href: string): string | null {
  const stem = (href.replace(/\.md$/i, "").split("/").pop() ?? "").toLowerCase();
  const hit = DOCS.find((d) => d.stem.toLowerCase() === stem);
  return hit ? `/docs/${hit.slug}` : null;
}

/** Simple client-side search across title, blurb, category, and keywords. */
export function searchDocs(query: string): DocMeta[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const tokens = q.split(/\s+/);
  return DOCS.filter((d) => {
    const hay = `${d.title} ${d.description} ${d.category} ${d.keywords.join(" ")}`.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  }).sort((a, b) => {
    const at = a.title.toLowerCase().startsWith(q) ? 0 : 1;
    const bt = b.title.toLowerCase().startsWith(q) ? 0 : 1;
    return at - bt;
  });
}
