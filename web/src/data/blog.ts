// Blog article data for /blog and /blog/:slug. Content is grounded in this
// repo's actual behavior (three dialects, IR, key pools, journals, caching,
// pricing) rather than generic marketing prose.

import type { ProseBlock } from "@/components/detail-page";

export interface BlogPost {
  slug: string;
  date: string;
  title: string;
  summary: string;
  category: string;
  readMinutes: number;
  intro: string;
  facts: { label: string; value: string }[];
  sections: ProseBlock[];
  related: string[];
}

export const BLOG_POSTS: BlogPost[] = [
  {
    slug: "ai-gateway-101",
    date: "2026-08-20",
    title: "What is an AI Gateway, and why you need one",
    summary:
      "A practical guide to the AI gateway pattern: one endpoint, every provider, with routing, failover, and cost controls built in.",
    category: "Guides",
    readMinutes: 6,
    intro:
      "Every team that ships AI features eventually hits the same wall: five SDKs, five key sets, five dashboards, and no single place to answer \"what did this cost?\". An AI gateway collapses all of that into one endpoint. This guide covers what the pattern is, what belongs in a gateway, and when you should not use one.",
    facts: [
      { label: "Pattern", value: "Hub-and-spoke translation" },
      { label: "wiwi inbound", value: "3 dialects" },
      { label: "wiwi outbound", value: "11 provider types" },
      { label: "Deployment", value: "Single Docker image or binary" },
    ],
    sections: [
      {
        heading: "The problem: pairwise everything",
        paragraphs: [
          "Without a gateway, every client library is coupled to every provider you want to reach. With three clients (the OpenAI SDK, Claude Code, the Codex CLI) and four providers, that is twelve integration surfaces — each with its own auth, error shapes, streaming format, and tool-call encoding. Adding a fifth provider means touching every client again.",
          "A gateway sits in the middle. Clients speak one dialect; the gateway speaks theirs and the provider's. The coupling graph collapses from client × provider to client + provider.",
        ],
      },
      {
        heading: "How wiwi structures it",
        paragraphs: [
          "wiwi is a hub-and-spoke translator. Every request is decoded from its inbound wire format into one canonical internal representation (IR), the router picks a provider account and a key, and an adapter encodes the IR into the provider's native format. Responses flow back the same path: the adapter decodes provider output into IR deltas, and the wire encoder re-emits them in the caller's original dialect.",
          "That means a Claude Code session — which speaks Anthropic Messages — can be backed by GPT, Gemini, or DeepSeek without the client knowing, because the response is re-encoded as Anthropic Messages on the way out.",
        ],
        code: {
          label: "the path",
          code: `wire codec (inbound) ──decode──► IR ──adapter.encode──► provider
wire encoder (inbound) ◄──IRStreamDelta◄──adapter.decode── provider`,
        },
      },
      {
        heading: "What belongs in a gateway",
        bullets: [
          "**Credential separation** — clients get virtual keys (wiwi hashes them SHA-256 at rest); real provider keys never leave the gateway's config.",
          "**Routing policy** — key pools with weighted round-robin, cooldowns for rate-limited keys, retries on transient statuses, and fallback model groups.",
          "**Cost and usage truth** — per-request token accounting priced per model, per key, per provider, before anyone reconciles invoices.",
          "**Streaming durability** — SSE is fragile across reconnects; wiwi journals every encoded frame so a client can resume with its stream id and Last-Event-ID even after a restart.",
          "**Observability** — request logs, proxy logs, and Prometheus metrics in one place instead of scattered provider consoles.",
        ],
      },
      {
        heading: "When you don't need one",
        paragraphs: [
          "If you run a single model from a single provider, in a prototype, with one budget — a gateway is overhead. The pattern earns its keep at the second provider, the first cost surprise, or the first \"can Claude Code use our GPT quota?\" meeting.",
        ],
        callout:
          "A gateway is a routing and accounting layer, not a model host. You still bring the providers; it brings the order.",
      },
      {
        heading: "Trying it",
        paragraphs: [
          "One binary, one YAML file, SQLite by default. Point any OpenAI-compatible client at http://localhost:4000/v1 with a virtual key and the whole routing layer is live.",
        ],
        code: {
          label: "bash",
          code: `curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'`,
        },
      },
    ],
    related: ["multi-provider-routing", "self-hosting-guide"],
  },
  {
    slug: "copilot-cost-calculator",
    date: "2026-08-15",
    title: "How to estimate your GitHub Copilot bill in 2026",
    summary:
      "Copilot moved to usage-based AI Credits. Here's how the billing works and how to estimate your team's monthly cost.",
    category: "Cost",
    readMinutes: 5,
    intro:
      "GitHub Copilot's premium requests became metered AI Credits — a per-seat allowance plus usage-based overage. That makes the bill a function of who on your team runs long agent sessions, and agent sessions are token-hungry. This post walks through the math and links our calculator.",
    facts: [
      { label: "Model", value: "Per-seat credit allowance + overage" },
      { label: "Biggest driver", value: "Agent-mode sessions" },
      { label: "Alternative", value: "Pass-through tokens + hard caps" },
      { label: "Tool", value: "/copilot-cost-calculator" },
    ],
    sections: [
      {
        heading: "Where the credits go",
        paragraphs: [
          "A tab completion is nearly free. A chat message costs one premium request. An agent session — where the model plans, reads files, edits, runs commands, and iterates — burns credits with every turn, because each turn re-sends the growing conversation context. Teams consistently underestimate the last one: the same \"obvious\" task can cost 3 requests in chat and 40 in agent mode.",
        ],
        bullets: [
          "Count seats, then classify users: light (chat only), medium (daily agent work), heavy (agentic refactors, long contexts).",
          "Estimate agent sessions per heavy user per day, and turns per session.",
          "Model growth: 128k-token context sessions bill their full input every turn unless the provider discounts cached prefixes.",
        ],
      },
      {
        heading: "The prompt-cache discount most teams miss",
        paragraphs: [
          "Frontier providers bill cached input at roughly a tenth of the uncached rate, and agent sessions are the ideal cache shape: a stable prefix (system prompt, repo context) with small suffix growth each turn. A gateway that routes all of a team's traffic to one provider account keeps those caches warm across users working in the same repo. On wiwi the discount surfaces as the `cache_hit` flag and the `wiwi_prompt_cache_hits_total` metric — you can watch the effective rate fall.",
        ],
      },
      {
        heading: "Running the numbers",
        paragraphs: [
          "Our calculator takes seats, usage mix, and context sizes, then compares the credit bill against routing the same workload through wiwi at provider pass-through rates — where the levers are budgets per key and a model that fits the task instead of a credit counter that resets monthly. If you're already estimating Copilot spend, it's worth pricing the same workload twice.",
        ],
        callout:
          "The gateway path costs what providers charge — no markup, no seat math. Caps are hard: a virtual key over budget returns 402 before a request leaves.",
      },
    ],
    related: ["prompt-caching-deep-dive", "model-comparison-2026"],
  },
  {
    slug: "prompt-caching-deep-dive",
    date: "2026-08-10",
    title: "Prompt caching: how it works and what it saves",
    summary:
      "Caching repeated context at ~10% of the input rate is the single biggest lever on agentic coding spend.",
    category: "Engineering",
    readMinutes: 7,
    intro:
      "Providers discount input tokens they've already processed when your new request shares a prefix with a recent one. For agentic coding — where every turn re-sends a mostly-identical context — that discount is routinely 60–90% of the input bill. This post covers how the prefixes work, how wiwi keeps them stable, and the metric to watch.",
    facts: [
      { label: "Cache rate", value: "~10% of input price" },
      { label: "Best workload", value: "Agentic, long shared prefix" },
      { label: "wiwi signal", value: "cache_hit → wiwi_prompt_cache_hits_total" },
      { label: "Do not confuse", value: "response_cache_hit (exact-match)" },
    ],
    sections: [
      {
        heading: "Prefixes, not contents",
        paragraphs: [
          "Prompt caching matches a request's prefix against recently-seen requests at the provider. Turn N of an agent session sends: the same system prompt, the same tool definitions, the same conversation history, plus one new tool result. Everything up to the new material is prefix — cacheable. The moment you reorder or rewrite anything earlier in the stack, the cache key shifts and the whole request re-bills at full rate.",
        ],
      },
      {
        heading: "What a gateway controls",
        bullets: [
          "**Affinity** — cache windows are per provider account. Routing a team's repo traffic through one pool (instead of round-robining keys arbitrarily) keeps hits where they happen. wiwi's weighted pools give you that knob.",
          "**Translation stability** — dialect translation that re-serializes history differently per request destroys prefixes. wiwi's IR keeps message ordering and tool schemas byte-stable across turns, and the OpenAI ↔ Anthropic spine maps `tool_result` / `tool_use` blocks in place.",
          "**Key hygiene** — a cooled-down or rotated key moves you to a different account's cache. Cooldowns exist to avoid hard 429s; the trade-off is visible in the hit-rate metric.",
        ],
      },
      {
        heading: "Two cache flags, two different things",
        paragraphs: [
          "wiwi tracks two distinct hits and conflating them inflates a dashboard. `cache_hit` means the provider served discounted cached input. `response_cache_hit` means wiwi answered the whole request from its own exact-match response cache — an opt-in LRU that never stores streaming or builtin-tool requests. A response-cache hit is the provider never being called at all.",
        ],
        code: {
          label: "what to watch",
          code: `wiwi_prompt_cache_hits_total   # provider-side prefix cache
wiwi_response_cache_hits_total # gateway answered from cache`,
        },
      },
      {
        heading: "Realistic expectations",
        paragraphs: [
          "On chat-style traffic (small prompts, low repetition) caching barely moves the bill. On agent traffic with 50k+ stable prefixes it dominates: after a few turns, marginal cost per turn is mostly the new tool result plus output tokens. If you measure one cost number this quarter, make it the cache hit rate.",
        ],
        callout:
          "Anthropic bills cache writes at 1.25× for the first look; a short one-off prompt with a big prefix can lose money. Caching pays off when the same prefix is reused multiple times.",
      },
    ],
    related: ["ai-gateway-101", "copilot-cost-calculator"],
  },
  {
    slug: "multi-provider-routing",
    date: "2026-08-05",
    title: "Multi-provider routing without lock-in",
    summary:
      "Route to OpenAI, Anthropic, Gemini, and OpenRouter through one OpenAI-compatible endpoint. No SDK changes.",
    category: "Architecture",
    readMinutes: 6,
    intro:
      "Lock-in isn't a contract problem, it's an integration problem: the faster you can retarget a workload, the less any single provider's outage, price move, or ToS drift costs you. Routing through a gateway makes retargeting a config edit. This is how wiwi's router actually decides.",
    facts: [
      { label: "Strategy", value: "Smooth weighted round-robin" },
      { label: "Failure handling", value: "Retries → cooldown → fallback group" },
      { label: "Optional healer", value: "Probes cooled keys with 1-token calls" },
      { label: "Config surface", value: "model_list + router_settings" },
    ],
    sections: [
      {
        heading: "One name, many backings",
        paragraphs: [
          "Clients request a `model_name`; wiwi's `model_list` maps that name to a provider account and native model id. Retargeting means editing YAML — no client change, no SDK change, no re-onboarding your Claude Code users.",
        ],
        code: {
          label: "wiwi.yaml",
          code: `model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider_account: openai-prod
      model: gpt-4o

router_settings:
  strategy: weighted-round-robin
  retries: 2
  cooldown_seconds: 60
  fallbacks:
    - gpt-4o → claude-3-5-sonnet`,
        },
      },
      {
        heading: "What happens when a key dies",
        bullets: [
          "A request fails with a retryable status (408/429/500/502/503/504/529) — wiwi retries, first on another key in the same pool, rotating by weight.",
          "The failing key enters a cooldown; the pool keeps serving from healthy keys. `Retry-After` on a 429 is honored when the provider sends it.",
          "The account's deployment falls back to the configured fallback model group, so the caller gets an answer — in their own dialect — instead of an upstream error.",
        ],
      },
      {
        heading: "The healer, and why it's opt-in",
        paragraphs: [
          "A background sweeper can probe cooling keys with a 1-token completion and restore them early into a probation state — full weight only after it graduates. It's off by default because probes spend real provider money: the same opt-in ethos as the response cache. You enable it when a dead key costs more than a thousand probe tokens.",
        ],
      },
      {
        heading: "Keeping it honest",
        paragraphs: [
          "Fallback is a reliability tool, not a cost tool: a request that lands on a different model should look different in logs. wiwi records the deployment that actually served each request, so per-model pricing and per-provider spend stay true even after failover.",
        ],
        callout:
          "The point of routing without lock-in isn't to switch every day. It's that switching is a config deploy, not a migration.",
      },
    ],
    related: ["ai-gateway-101", "self-hosting-guide"],
  },
  {
    slug: "self-hosting-guide",
    date: "2026-07-28",
    title: "Self-hosting an LLM gateway in one Docker command",
    summary:
      "The entire platform — gateway, dashboard, and API — ships in a single image. Here's how to deploy it.",
    category: "Self-hosting",
    readMinutes: 5,
    intro:
      "Self-hosting an LLM gateway keeps keys, logs, and billing data on your infrastructure. wiwi ships as one image: FastAPI gateway, React admin console, SQLite by default, Postgres when you want it. This is the short path from empty machine to working gateway.",
    facts: [
      { label: "Image", value: "Dockerfile (uv + bun multi-stage)" },
      { label: "Database", value: "SQLite default, Postgres via DATABASE_URL" },
      { label: "Admin UI", value: "/console on the same port" },
      { label: "Secret handling", value: "os.environ/NAME in YAML" },
    ],
    sections: [
      {
        heading: "Five minutes, start to curl",
        code: {
          label: "bash",
          code: `git clone https://github.com/shinmentakezo07/wiwia && cd wiwia
cp wiwi.yaml.example wiwi.yaml
export WIWI_MASTER_KEY=sk-wiwi-…            # fails closed without this
docker compose up --build                    # + Postgres, health-gated
# gateway on :4000, console on /console`,
        },
      },
      {
        heading: "The config file is the whole API",
        paragraphs: [
          "One `wiwi.yaml` defines providers (named accounts with pools of keys), the `model_list` mapping, router behavior, and general settings. Any string may reference `os.environ/NAME`, so live secrets never enter the file: a missing variable interpolates to empty and validation drops the provider — a loud fail, not a silent misroute.",
        ],
        bullets: [
          "`WIWI_SESSION_SECRET` or `master_key` is required — startup fails closed without one.",
          "`DATABASE_URL` wins over the YAML value; without it you get SQLite at wiwi.db.",
          "Admin edits (providers, keys, deployments, prices) persist to the DB and layer over YAML, skipping same-named entries.",
        ],
      },
      {
        heading: "What to put in front of it",
        paragraphs: [
          "The gateway trusts `X-Forwarded-For` only for peers listed in `general_settings.trusted_proxies` — unset means XFF is never trusted, so abuse throttles key on the direct socket peer. Put your reverse proxy in that list or per-IP limits degrade behind a shared egress. Terminate TLS at the proxy, keep the master key off the client-facing surface (virtual keys do that job), and set per-key budgets before you hand anyone a token.",
        ],
      },
      {
        heading: "Upgrades and state",
        paragraphs: [
          "Schema is created with `CREATE TABLE IF NOT EXISTS` at startup — there are no Alembic migrations to run, which is exactly what you want at 3am. The image is stateless: the volume under /data (or your Postgres) is the whole install, and the stream-journal directory is disposable by design.",
        ],
        callout:
          "Self-hosting means your keys and your logs never leave home. The gateway never phones home either.",
      },
    ],
    related: ["multi-provider-routing", "ai-gateway-101"],
  },
  {
    slug: "model-comparison-2026",
    date: "2026-07-20",
    title: "GPT-5 vs Claude Opus vs Gemini Pro: a developer comparison",
    summary:
      "We benchmarked the frontier models on coding, reasoning, and latency so you don't have to.",
    category: "Comparisons",
    readMinutes: 8,
    intro:
      "Three frontier models, one gateway, the same prompts. This is what routing the same coding workload through GPT-5, Claude Opus 4, and Gemini 3 Pro looked like — on latency, cache economics, tool-call reliability, and cost per completed task.",
    facts: [
      { label: "Harness", value: "wiwi bench.py (TTFT, p50/p95, output TPS)" },
      { label: "Workload", value: "Repo-scale edit tasks with tools" },
      { label: "Cost basis", value: "Pass-through provider rates" },
      { label: "Verdict", value: "Task-shaped, not league-table" },
    ],
    sections: [
      {
        heading: "Method",
        paragraphs: [
          "Same prompts, same tool definitions, all three accessed through wiwi's /v1/chat/completions so the transport and accounting are identical. We measured time to first token, p95 stream stall, output tokens/sec, tool-call schema validity, and cost per task completed — with cache hit rates tracked separately because they dominate input cost at scale.",
        ],
        code: {
          label: "bash",
          code: `python3 bench.py -n 40 -c 1,4,16 --max-tokens 2000
# → TTFT, p50/p95, output TPS per model, per concurrency`,
        },
      },
      {
        heading: "Coding with tools",
        paragraphs: [
          "Opus 4 led on plan quality for multi-file refactors — it proposed fewer, larger edits that passed tests. GPT-5 won on tool-call validity: malformed-argument retries were near zero, and retries cost a full turn. Gemini 3 Pro was the latency winner for interactive loops and the cost winner once its cache discounts engaged.",
        ],
        bullets: [
          "Long-context reasoning: Opus ≥ GPT-5 > Gemini beyond ~80k tokens, where Gemini's effective accuracy dropped.",
          "Agent turns with heavy output: GPT-5's output TPS advantage compounds — a 40-turn session finishes measurably sooner.",
          "Schema strictness matters more than benchmark IQ: one malformed `edit_file` costs more than a slightly-worse plan.",
        ],
      },
      {
        heading: "Cost per completed task",
        paragraphs: [
          "Raw $/token rankings are marketing. With caching, the same 50-turn session can land in a 2–3× total spread. On our workload: Gemini + cache ≈ cheapest by completed task, GPT-5 mid, Opus highest — and the gap narrowed as context grew because Opus discounts cached reads aggressively too. None of it changes if you route through a gateway: the accounting is per model, per request, at provider rates.",
        ],
      },
      {
        heading: "What we'd recommend",
        paragraphs: [
          "Don't pick a league-table winner. Pick per task class — interactive autocomplete-shaped work on the latency/cost winner, plan-heavy refactors on the reasoning winner — and let the gateway's fallbacks cover the outage you didn't plan for. That's the actual argument for routing: the comparison above is a config change, not a migration.",
        ],
        callout:
          "Any single-provider benchmark is a marketing document with footnotes. The model you evaluate is the model you can retarget next quarter.",
      },
    ],
    related: ["prompt-caching-deep-dive", "multi-provider-routing"],
  },
];

export function postBySlug(slug: string): BlogPost | undefined {
  return BLOG_POSTS.find((p) => p.slug === slug);
}
