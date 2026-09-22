// Migration guide data for /migration and /migration/:slug. Each entry is
// grounded in the source service's real request shape and wiwi's actual
// surfaces (/v1/chat/completions, /v1/responses, /v1/messages, /admin API).

import type { DetailStep } from "@/components/detail-page";

export interface Migration {
  slug: string;
  title: string;
  fromProvider: string;
  icon: string;
  summary: string;
  intro: string;
  facts: { label: string; value: string }[];
  steps: DetailStep[];
  caveats: string[];
  relatedGuideSlug?: string;
}

export const MIGRATIONS: Migration[] = [
  {
    slug: "open-router",
    title: "From OpenRouter",
    fromProvider: "OpenRouter",
    icon: "OR",
    summary:
      "Switch from OpenRouter to the gateway with minimal code changes. Our OpenAI-compatible API makes it straightforward.",
    intro:
      "OpenRouter and wiwi speak the same inbound dialect — OpenAI Chat Completions — so most of a migration is a base-URL swap. What you gain: your own keys at pass-through provider rates, a key pool with weighted routing you control, and request logs on your infrastructure. What you lose: their credit system and their model marketplace's zero-config provider list. You bring provider accounts instead.",
    facts: [
      { label: "Code changes", value: "Base URL + key, usually none otherwise" },
      { label: "Model ids", value: "Same vendor/name shape, mapped in wiwi.yaml" },
      { label: "Billing", value: "Your provider accounts directly" },
      { label: "Typical time", value: "Under an hour" },
    ],
    steps: [
      {
        title: "Point the client at wiwi",
        body: "OpenRouter uses https://openrouter.ai/api/v1. Your wiwi deployment exposes the same surface at /v1. Swap the base URL and replace the key with a wiwi virtual key.",
        code: {
          label: "env",
          code: `# before
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-…

# after
OPENAI_BASE_URL=http://localhost:4000/v1
OPENAI_API_KEY=sk-wiwi-…`,
        },
      },
      {
        title: "Map the model names you use",
        body: "In wiwi.yaml, add one model_list entry per OpenRouter slug you call. The provider account and native model id are yours to choose; the caller's model_name stays whatever your code already sends.",
        code: {
          label: "wiwi.yaml",
          code: `model_list:
  - model_name: anthropic/claude-sonnet-4   # keep the OpenRouter-style name
    wiwi_params:
      provider_account: anthropic-prod
      model: claude-sonnet-4
  - model_name: openai/gpt-4o
    wiwi_params:
      provider_account: openai-prod
      model: gpt-4o`,
        },
      },
      {
        title: "Replace provider routing",
        body: "OpenRouter's :floor / :nitro suffixes and provider preferences don't exist here — routing policy lives in your config. Use key pools with weights for cost/latency preference and fallbacks for availability, which is the honest version of the same idea.",
        code: {
          label: "wiwi.yaml",
          code: `router_settings:
  strategy: weighted-round-robin
  retries: 2
  cooldown_seconds: 60
  fallbacks:
    - anthropic/claude-sonnet-4 → openai/gpt-4o`,
        },
      },
      {
        title: "Verify on the wire",
        body: "Send a streaming request and watch Console → Request logs. Confirm model, deployment, tokens, and cost per request — and that the response re-encodes cleanly in the caller's dialect.",
        code: {
          label: "bash",
          code: `curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer $OPENAI_API_KEY" \\
  -d '{"model":"openai/gpt-4o","messages":[{"role":"user","content":"hi"}],"stream":true}'`,
        },
      },
    ],
    caveats: [
      "OpenRouter's free-tier and economy slug variants have no wiwi equivalent — route to your own cheap models by adding them to the config instead.",
      "If you relied on OpenRouter's unified balance to mix providers without per-provider accounts, that's the real migration: opening provider accounts.",
    ],
    relatedGuideSlug: "codex-cli",
  },
  {
    slug: "vercel-ai-gateway",
    title: "From Vercel AI Gateway",
    fromProvider: "Vercel AI Gateway",
    icon: "▲",
    summary:
      "Migrate from Vercel AI Gateway to the gateway. Keep the same SDK, change the base URL and API key.",
    intro:
      "The Vercel AI SDK's gateway provider hits an OpenAI-compatible endpoint, so switching its base URL to wiwi keeps your application code byte-for-byte identical. Model routing — which provider actually answers a `gateway('model')` call — moves from Vercel's dashboard to your wiwi.yaml.",
    facts: [
      { label: "SDK", value: "AI SDK unchanged — custom provider or baseURL" },
      { label: "Routing", value: "From dashboard rules to wiwi.yaml" },
      { label: "Streaming", value: "SSE on /v1/chat/completions, same as before" },
      { label: "Typical time", value: "One afternoon" },
    ],
    steps: [
      {
        title: "Keep the SDK, repoint it",
        body: "The AI SDK treats any OpenAI-compatible endpoint the same. Create an openai-compatible provider for wiwi and use your model names directly.",
        code: {
          label: "app.ts",
          code: `import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

const wiwi = createOpenAICompatible({
  name: "wiwi",
  baseURL: "http://localhost:4000/v1",
  apiKey: process.env.WIWI_KEY, // sk-wiwi-…
});

const result = await generateText({
  model: wiwi("gpt-4o"), // whatever your wiwi model_list exposes
  prompt: "Refactor this function to be pure.",
});`,
        },
      },
      {
        title: "Translate routing rules into config",
        body: "Gateway's provider-priority and fallback rules map onto wiwi's router_settings: strategies and fallback groups. One model_list entry per logical model name, and fallbacks handle availability.",
      },
      {
        title: "Confirm streams end legally",
        body: "The gateway journals SSE frames by default, so client reconnects with a stream id replay from the journal. Watch for clean [DONE] frames on chat-completions streams the same way you did before.",
      },
    ],
    caveats: [
      "Vercel's gateway usage analytics live in their dashboard; wiwi's equivalents are the usage and analytics console pages plus Prometheus metrics.",
      "Vercel AI Gateway's model catalog is curated and auto-updating — with wiwi, adding a model is your config edit, which is the point.",
    ],
  },
  {
    slug: "litellm",
    title: "From LiteLLM",
    fromProvider: "LiteLLM",
    icon: "🚅",
    summary:
      "Switch from LiteLLM to the gateway. Open source, self-hostable, with virtual keys and budgets built in.",
    intro:
      "Both projects are self-hosted Python gateways, so this is the flattest migration on this page: same deploy shape, same LiteLLM-style YAML vocabulary. wiwi's differences are what it's worth evaluating: three inbound dialects (LiteLLM's proxy has more), a translation spine with no pairwise converters, and durable stream journals.",
    facts: [
      { label: "Config shape", value: "LiteLLM-style YAML, adapted keys" },
      { label: "Inbound dialects", value: "Chat, Responses, Messages" },
      { label: "Keys", value: "Virtual keys hashed SHA-256 at rest" },
      { label: "Typical time", value: "Minutes for a proxy swap" },
    ],
    steps: [
      {
        title: "Copy the shape across",
        body: "model_list and router_settings carry over almost unchanged. Provider accounts with key pools replace individual key entries — wiwi pools keys per named account, which reads closer to how you already operate.",
        code: {
          label: "wiwi.yaml",
          code: `providers:
  openai-prod:
    type: openai
    keys:
      - { label: pool-1, key: os.environ/OPENAI_API_KEY, weight: 3 }
      - { label: pool-2, key: os.environ/OPENAI_API_KEY_2, weight: 1 }

model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider_account: openai-prod
      model: gpt-4o`,
        },
      },
      {
        title: "Port keys and budgets",
        body: "Virtual keys with budgets, rate limits, model allowlists, and expiry exist on both sides. Reissue keys from wiwi's admin API (/admin/keys/generate, master key) rather than exporting LiteLLM's hashes — the wire format differs and old spend history stays in the old DB if you need it.",
      },
      {
        title: "Drop the callbacks you no longer need",
        body: "If you ran LiteLLM's langfuse/prometheus callback modules, wiwi covers the same ground natively: request logs to DB with SSE tail, Prometheus metrics at a configurable path, per-request cost at log time.",
      },
    ],
    caveats: [
      "LiteLLM exposes more inbound surfaces (Bedrock, Vertex passthroughs); check your clients' dialect before retiring the old proxy.",
      "Config vocabulary is deliberately similar but not identical — read the error at startup, it's specific about unknown fields.",
    ],
  },
  {
    slug: "github-copilot",
    title: "From GitHub Copilot",
    fromProvider: "GitHub Copilot",
    icon: "GH",
    summary:
      "Replace Copilot's metered AI Credits with pass-through token pricing and hard budget caps.",
    intro:
      "Copilot's coding-agent traffic runs on metered AI Credits: a monthly per-seat allowance with overage you can't hard-cap in the places that matter. Routing the same work through wiwi with a BYOK setup bills at provider rates with per-key budgets. The IDE plugin stays Copilot's; the agent and CLI paths are what move.",
    facts: [
      { label: "What moves", value: "Copilot CLI, agent sessions, custom tools" },
      { label: "Pricing", value: "Provider pass-through, no seat math" },
      { label: "Controls", value: "Per-key budgets and RPM/TPM caps" },
      { label: "Typical time", value: "One config session" },
    ],
    steps: [
      {
        title: "Check what can actually repoint",
        body: "GitHub's Copilot CLI and BYOK paths accept a custom OpenAI-compatible base URL and token. The inline completions inside VS Code are not one of them — autocomplete stays on GitHub's backend. Plan the migration around agent and chat sessions.",
      },
      {
        title: "Issue team keys with budgets",
        body: "Create one virtual key per developer or project from the console (or the admin API). Set spend caps and rate limits here — this is the hard ceiling credits don't offer, and the gateway refuses with a 402 before a request leaves.",
      },
      {
        title: "Route and watch",
        body: "Point the CLI at wiwi, pick a model from your config, and keep a fallback group configured so a provider outage doesn't stop the team. Every session lands in request logs with tokens and cost.",
        code: {
          label: "env",
          code: `export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…`,
        },
      },
    ],
    caveats: [
      "Copilot's included premium requests are already paid for — this migration makes sense when you're over allowance, want different models, or need cost truth per developer.",
      "Prompt caching across a shared repo benefits from pool affinity — route the team through one provider account (one pool) rather than personal keys.",
    ],
    relatedGuideSlug: "github-copilot",
  },
  {
    slug: "portkey",
    title: "From Portkey",
    fromProvider: "Portkey",
    icon: "P",
    summary:
      "Migrate from Portkey to the gateway with the same OpenAI-compatible interface and open source under MIT.",
    intro:
      "Portkey's unified API and config-based routing map well onto wiwi: base-URL swap, virtual key in place of Portkey key, and routing config replaced by model_list plus router_settings. The differences are deployment model (managed-first vs self-host-first) and where observability runs.",
    facts: [
      { label: "Inbound", value: "OpenAI-compatible /v1" },
      { label: "Routing configs", value: "Replace with model groups + fallbacks" },
      { label: "Observability", value: "Self-hosted logs + Prometheus" },
      { label: "License", value: "MIT, fully open source" },
    ],
    steps: [
      {
        title: "Swap the endpoint",
        body: "Portkey's OpenAI-compatible route lives at their gateway URL with an x-api-key header. wiwi uses standard Authorization bearer with a virtual key, or x-api-key on the Anthropic surface.",
        code: {
          label: "env",
          code: `# before
PORTKEY_API_KEY=…  + https://api.portkey.ai/v1
# after
OPENAI_BASE_URL=http://localhost:4000/v1
OPENAI_API_KEY=sk-wiwi-…`,
        },
      },
      {
        title: "Rebuild routing configs as fallbacks",
        body: "Portkey configs (fallbacks, loads, conditions) become wiwi router_settings: strategy: weighted-round-robin for load splitting, a fallbacks list for ordered failover, model allowlists per virtual key for the access-control half.",
      },
      {
        title: "Move logs to your side of the wire",
        body: "Request logs, token usage, and cost are recorded per request in wiwi's database and streamed in the console — the analytics Portkey served from their cloud now live in your Postgres or SQLite.",
      },
    ],
    caveats: [
      "Portkey's guardrails and cache plugins have no drop-in wiwi equivalent yet; that logic belongs in the calling application or at a proxy layer around it.",
      "Condition-based routing (route by payload) isn't a config primitive in wiwi — model names are the routing surface, by design.",
    ],
  },
  {
    slug: "cloudflare-ai-gateway",
    title: "From Cloudflare AI Gateway",
    fromProvider: "Cloudflare",
    icon: "CF",
    summary:
      "Switch from Cloudflare AI Gateway to the gateway for self-hostable, open source routing.",
    intro:
      "Cloudflare's AI Gateway is a proxy in front of many providers plus Workers AI, sold as a Cloudflare feature: caching, analytics, and custom-gateway passthrough on their edge. Moving to wiwi means running the routing layer yourself — your keys, your logs, your cache policy — with no edge vendor in the request path.",
    facts: [
      { label: "Shape", value: "Edge proxy → self-hosted gateway" },
      { label: "Auth", value: "Provider BYOK via virtual keys" },
      { label: "Caching", value: "Exact-match LRU, opt-in (or Redis)" },
      { label: "Typical time", value: "One config session" },
    ],
    steps: [
      {
        title: "Drop the gateway prefix",
        body: "Cloudflare wraps upstreams in a /gateway/<account>/<id>/… path. wiwi clients hit plain /v1 with the model name they want; the model_list does the upstream mapping the Cloudflare gateway config used to.",
      },
      {
        title: "Recreate caching and logging",
        body: "Enable wiwi's response cache in wiwi_settings if you used Cloudflare's cache mode; request logging is always on to the DB. Cloudflare's analytics equivalent is the console's usage and analytics pages plus Prometheus.",
      },
      {
        title: "Handle TLS and geo at the proxy",
        body: "Edge TLS, rate limiting by IP, and regional routing stay where they belong — your reverse proxy or CDN. wiwi covers trusted_proxies for X-Forwarded-For so those headers still mean something.",
      },
    ],
    caveats: [
      "Workers AI itself is not a wiwi provider — if workloads run there, they keep their own credentials and endpoint.",
      "You inherit the uptime story: the gateway is stateless and starts in seconds, but the healthcheck in front of it is now yours.",
    ],
  },
];

export function migrationBySlug(slug: string): Migration | undefined {
  return MIGRATIONS.find((m) => m.slug === slug);
}
