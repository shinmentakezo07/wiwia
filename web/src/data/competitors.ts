// Competitor data for /compare/:slug. Honest-by-construction: every claim is
// about architecture and operating model, each page says what the competitor
// does better, and every feature row mirrors the /compare matrix.

export interface CompareRow {
  feature: string;
  wiwi: string | boolean;
  them: string | boolean;
  note: string;
}

export interface Competitor {
  slug: string;
  name: string;
  tagline: string;
  intro: string;
  facts: { label: string; value: string }[];
  rows: CompareRow[];
  theyWin: string[];
  weWin: string[];
  pickThemWhen: string;
  pickUsWhen: string;
  migrationSlug?: string;
}

export const COMPETITORS: Competitor[] = [
  {
    slug: "openrouter",
    name: "OpenRouter",
    tagline: "Curated marketplace, unified balance",
    intro:
      "OpenRouter is a hosted model marketplace: hundreds of models behind one API and one wallet, with provider routing handled for you. wiwi is the routing layer you run — you bring provider accounts and get pass-through pricing, your own logs, and client-dialect translation OpenRouter doesn't offer.",
    facts: [
      { label: "They are", value: "Hosted marketplace, credit balance" },
      { label: "We are", value: "Self-hosted gateway, your keys" },
      { label: "Pricing", value: "Pass-through + platform fee vs provider rates only" },
      { label: "Migration", value: "Base URL + key, model names configurable" },
    ],
    rows: [
      { feature: "Model catalog", wiwi: "Your config (11 provider types)", them: "Curated marketplace", note: "They add models for you; you add them with a config line." },
      { feature: "Inbound dialects", wiwi: true, them: "OpenAI-shape only", note: "wiwi serves Chat, Responses, and Messages natively." },
      { feature: "Anthropic-native clients", wiwi: "First-class /v1/messages", them: "Via translation", note: "Claude Code can ride wiwi's Messages surface directly." },
      { feature: "Self-hosted", wiwi: true, them: false, note: "Keys, logs, and budgets stay on your infra." },
      { feature: "Open source", wiwi: "MIT", them: false, note: "Read the router, patch the router." },
      { feature: "Token markup", wiwi: "None", them: "Per-token fee on top", note: "Provider invoice = your cost at both layers, minus their fee." },
      { feature: "Key pools & budgets", wiwi: true, them: "Unified balance", note: "Different models of the same problem: pools vs shared wallet." },
      { feature: "Zero-setup provider access", wiwi: false, them: true, note: "No provider accounts needed with their wallet. That's the trade." },
    ],
    theyWin: [
      "Hundreds of models available the day they launch, no config on your side.",
      "One balance across every provider — no per-provider accounts to open.",
    ],
    weWin: [
      "Pass-through pricing: no platform fee per token.",
      "Three inbound dialects — Codex CLI and Claude Code ride their native surfaces.",
      "Your infrastructure: request logs, keys, and budgets never leave home.",
    ],
    pickThemWhen: "You want a zero-ops model marketplace today and a small fee is cheaper than running anything.",
    pickUsWhen: "You have provider accounts already, need Anthropic/Responses-native clients, or want the bill and the logs to be yours.",
    migrationSlug: "open-router",
  },
  {
    slug: "litellm",
    name: "LiteLLM",
    tagline: "The closest neighbor",
    intro:
      "LiteLLM is the best-known self-hosted Python gateway and wiwi's nearest analog: same deploy shape, similar YAML vocabulary, virtual keys on both sides. The honest differences are surface area (theirs is wider), streaming durability (ours journals SSE), and how dialect translation is structured (ours is one spine, not pairwise converters).",
    facts: [
      { label: "Both", value: "Self-hosted, Python, proxy + dashboard" },
      { label: "Their strength", value: "Provider breadth, maturity" },
      { label: "Our strength", value: "Dialect spine, journals, healer" },
      { label: "Migration", value: "Config-shape copy, keys reissued" },
    ],
    rows: [
      { feature: "Provider count", wiwi: "11 types", them: "100+ providers", note: "Their breadth is real. wiwi covers mainstream shapes; niche providers are adapters away." },
      { feature: "Inbound dialects", wiwi: "Chat + Responses + Messages", them: "Chat + passthrough endpoints", note: "Responses/Messages are first-class surfaces on wiwi." },
      { feature: "Translation structure", wiwi: "Hub IR, no pairwise converters", them: "Per-provider mappings", note: "Adding a provider: one adapter + one registry branch vs per-pair handling." },
      { feature: "Stream durability", wiwi: "Journals + resume", them: false, note: "Reconnect after a restart replays the journal; nothing lost mid-stream." },
      { feature: "Health healer", wiwi: "Opt-in probation restore", them: "Cooldowns", note: "Probing cooled keys back to service early is a wiwi feature." },
      { feature: "Maturity & community", wiwi: "Young", them: "Years, large community", note: "LiteLLM has battle scars you can search for; ours you file with us." },
      { feature: "Response cache", wiwi: "Opt-in exact-match, Redis or memory", them: "Cache layer", note: "Both cache; wiwi keeps it off streaming and builtin-tool requests by design." },
      { feature: "Cost model", wiwi: "Pass-through, no license tiers", them: "Free proxy + paid enterprise tiers", note: "Check their enterprise wall for the feature you need." },
    ],
    theyWin: [
      "Provider coverage far beyond mainstream shapes.",
      "Years of production hardening and a community that's already hit your edge case.",
    ],
    weWin: [
      "Responses and Messages are native surfaces, not emulated shapes.",
      "Stream journals survive restarts — reconnects replay, not respawn.",
      "The whole thing, including budgets and the console, is MIT with no enterprise tier.",
    ],
    pickThemWhen: "You need an exotic provider today, or want the ecosystem that maturity buys.",
    pickUsWhen: "Your clients live in Codex/Claude Code dialects and you value durable streaming and a single translation spine.",
    migrationSlug: "litellm",
  },
  {
    slug: "portkey",
    name: "Portkey",
    tagline: "Managed-first routing and guardrails",
    intro:
      "Portkey is a production AI stack: managed gateway with routing configs, guardrails, and a strong observability product, plus an open-source core. wiwi overlaps on routing and observability but not the guardrails layer, and inverts the default: everything is yours to run first.",
    facts: [
      { label: "They are", value: "Managed platform, OSS core" },
      { label: "We are", value: "Self-host-first gateway" },
      { label: "Their extra", value: "Guardrails, config graphs" },
      { label: "Our extra", value: "Dialect spine, journals" },
    ],
    rows: [
      { feature: "Routing configs", wiwi: "model_list + fallbacks", them: "Condition graphs, loads", note: "Theirs is more expressive; ours is one file you can read at a glance." },
      { feature: "Guardrails", wiwi: false, them: true, note: "Input/output validation is their layer, not ours — apps or a proxy own it here." },
      { feature: "Observability", wiwi: "Self-hosted logs + Prometheus", them: "Managed dashboards", note: "Same questions, different data residency." },
      { feature: "Open source", wiwi: "MIT, whole platform", them: "Core OSS, platform tiers", note: "Read the license lines before comparing feature lists." },
      { feature: "Self-hosted", wiwi: true, them: "Enterprise option", note: "Self-hosting Portkey is a contract; self-hosting wiwi is `docker compose up`." },
      { feature: "Virtual keys & budgets", wiwi: true, them: true, note: "Table stakes for both." },
      { feature: "Inbound dialects", wiwi: "Chat + Responses + Messages", them: "OpenAI-shape + provider routes", note: "Anthropic-native clients land better on wiwi." },
    ],
    theyWin: [
      "Guardrails and payload-condition routing we simply don't ship.",
      "A polished managed observability product with teams already on it.",
    ],
    weWin: [
      "Every line of the platform is MIT and running on your machine in a minute.",
      "Native Messages/Responses surfaces for coding-agent traffic.",
    ],
    pickThemWhen: "Guardrails, config graphs, and a managed control plane are core requirements.",
    pickUsWhen: "You want the routing layer self-owned and MIT, and your traffic is coding-agent dialects.",
    migrationSlug: "portkey",
  },
];

export function competitorBySlug(slug: string): Competitor | undefined {
  return COMPETITORS.find((c) => c.slug === slug);
}
