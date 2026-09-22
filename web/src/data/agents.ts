// Agent catalog data for /agents and /agents/:slug. Each agent is a
// tool-calling demo that runs against the gateway's OpenAI-compatible surface;
// the "why it works" sections explain the IR translation that makes the same
// agent run on any provider behind wiwi.

import type { LucideIcon } from "lucide-react";
import {
  CloudSun,
  FileText,
  Mail,
  ScanText,
  SmilePlus,
  UserSearch,
} from "lucide-react";
import type { DetailStep } from "@/components/detail-page";

export interface AgentDetail {
  slug: string;
  name: string;
  icon: LucideIcon;
  tags: string[];
  capabilities: string[];
  featured?: boolean;
  summary: string;
  intro: string;
  facts: { label: string; value: string }[];
  tools: { name: string; desc: string }[];
  steps: DetailStep[];
  why: string;
  githubUrl: string;
}

export const AGENTS: AgentDetail[] = [
  {
    slug: "weather-agent",
    name: "Weather Agent",
    icon: CloudSun,
    tags: ["TypeScript", "AI SDK", "OpenAI-compatible"],
    capabilities: ["Tool Calling", "Real-time Data", "Natural Language"],
    featured: true,
    summary:
      "Real-time weather answers via tool calling — the cleanest demo of function-calling patterns through the gateway.",
    intro:
      "Ask \"should I bring a jacket in Oslo tomorrow?\" and watch the model decide to call a lookup tool, parse the result, and answer in prose. The agent is one file of tool schemas plus a loop; everything provider-specific is wiwi's problem.",
    facts: [
      { label: "Pattern", value: "Single-shot tool loop" },
      { label: "Gateway surface", value: "/v1/chat/completions, tools" },
      { label: "Works with", value: "OpenAI · Anthropic · Gemini backends" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    tools: [
      { name: "get_weather", desc: "Return current conditions and next-24h forecast for a resolved location." },
      { name: "get_location", desc: "Resolve a place name to coordinates before the weather call." },
    ],
    steps: [
      {
        title: "Clone and run",
        body: "The agent is a small TypeScript project with the AI SDK; install and start it with the environment pair set.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/agents/weather-agent
npm install
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…
npm run dev`,
        },
      },
      {
        title: "Ask a question",
        body: "The model emits a tool call; the agent executes it; the loop feeds the result back and the final answer streams to your terminal.",
      },
      {
        title: "Switch providers",
        body: "Change the model name to one backed by a different provider account. Nothing in the agent changes — wiwi translates the tool dialects.",
      },
    ],
    why:
      "Tool calls are where dialect differences bite: OpenAI Chat, OpenAI Responses, and Anthropic Messages encode functions differently, and streams fragment arguments differently. The gateway's IR normalizes the open/delta/close sequence per tool index, so one agent loop works on any of the eleven outbound types.",
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/weather-agent",
  },
  {
    slug: "lead-agent",
    name: "Lead Agent",
    icon: UserSearch,
    tags: ["TypeScript", "AI SDK", "Web search"],
    capabilities: ["Web Search", "Profile Research", "Structured Output"],
    summary:
      "Researches a person by name or email and produces a structured profile: bio, role, background, links.",
    intro:
      "A research agent with a job to do: it plans searches, hits a web-search tool repeatedly, and assembles findings into a typed profile. A good demonstration of longer tool loops and structured extraction at the end.",
    facts: [
      { label: "Pattern", value: "Multi-turn research loop" },
      { label: "Gateway feature", value: "Streaming tool-call translation" },
      { label: "Output", value: "Typed profile object" },
      { label: "Time to running", value: "~15 minutes" },
    ],
    tools: [
      { name: "web_search", desc: "Search the public web and return ranked snippets." },
      { name: "fetch_page", desc: "Pull readable text from a result URL for deeper extraction." },
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Install, set the base URL and key, and run with a name or email argument.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/agents/lead-agent
npm install
npm run dev -- "Ada Lovelace"`,
        },
      },
      {
        title: "Watch the loop",
        body: "Each search/refine turn is one request through the gateway — visible in request logs with per-turn token counts, which is where research-agent cost actually lives.",
      },
    ],
    why:
      "Multi-step agents re-send a growing history every turn. That's the prompt-cache shape that dominates input cost, and why running them through one provider account (one key pool) is worth the routing decision.",
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/lead-agent",
  },
  {
    slug: "changelog-generator",
    name: "Changelog Generator",
    icon: FileText,
    tags: ["TypeScript", "AI SDK", "Zod"],
    capabilities: ["Tool Calling", "Git Analysis", "Structured Output"],
    summary:
      "Turns git history into a Keep a Changelog document — the model reads logs and diffs with tools, then categorizes.",
    intro:
      "Feed it a release range and it drives a git-analysis loop: read the log, inspect diffs for ambiguous commits, and emit categorized changes (Added/Changed/Fixed) in Keep a Changelog format. The practical one — every team has a release notes debt.",
    facts: [
      { label: "Pattern", value: "Tool-driven repo analysis" },
      { label: "Output", value: "Keep a Changelog Markdown" },
      { label: "Gateway surface", value: "Chat + tools, any backend" },
      { label: "Time to running", value: "~10 minutes" },
    ],
    tools: [
      { name: "git_log", desc: "List commits in the range with hashes and messages." },
      { name: "git_diff", desc: "Show the diff for a commit when the message alone is ambiguous." },
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Point it at any repository and a range; it produces the draft on stdout.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/agents/changelog-generator-agent
npm install
npm run dev -- --repo ../my-app --from v0.9.0 --to v0.10.0`,
        },
      },
    ],
    why:
      "Long diffs stress context limits differently per model. A cheap fallback model in the gateway config covers draft passes; the frontier model only sees the commits the cheap one flags ambiguous.",
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/changelog-generator-agent",
  },
  {
    slug: "email-drafter",
    name: "Email Drafter",
    icon: Mail,
    tags: ["TypeScript", "AI SDK", "Zod"],
    capabilities: ["Structured Output", "Tone Control", "Text Generation"],
    summary:
      "Rough notes in, polished email out — subject, body, and sign-off as typed structured output.",
    intro:
      "The smallest useful agent in the gallery: bullet notes plus a tone preset become a sendable email, returned as a Zod-validated object rather than free text. A tight demonstration of structured output without a provider-specific schema dialect.",
    facts: [
      { label: "Pattern", value: "Single-call structured output" },
      { label: "Validation", value: "Zod schema, provider-agnostic" },
      { label: "Presets", value: "Tone and formality controls" },
      { label: "Time to running", value: "~5 minutes" },
    ],
    tools: [
      { name: "compose_email", desc: "The model's terminal tool: returns subject/body/sign-off as validated JSON." },
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Set the environment pair and call it with notes and a tone flag.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/agents/email-drafter-agent
npm install
npm run dev -- --tone friendly "ship demo thursday, move q1 review"`,
        },
      },
    ],
    why:
      "Structured-output enforcement (JSON mode vs tool-choice vs post-hoc repair) differs per provider. The gateway normalizes tool-call validity so the schema either parses or the agent retries — one code path.",
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/email-drafter-agent",
  },
  {
    slug: "sentiment-analyzer",
    name: "Sentiment Analyzer",
    icon: SmilePlus,
    tags: ["TypeScript", "AI SDK", "Zod"],
    capabilities: ["Sentiment Analysis", "Key Phrases", "File Input"],
    summary:
      "Classifies text as positive, negative, neutral, or mixed with confidence scores and key-phrase evidence.",
    intro:
      "Paste text or pass a file; the model returns per-span sentiment with confidence and the phrases that justified the call. A small, honest NLP demo — and the kind of batch-shaped workload where model economics matter.",
    facts: [
      { label: "Pattern", value: "Classify + evidence extraction" },
      { label: "Input", value: "Inline text or file paths" },
      { label: "Output", value: "Typed per-span labels" },
      { label: "Time to running", value: "~5 minutes" },
    ],
    tools: [
      { name: "classify", desc: "Return label, confidence, and supporting phrases per text span." },
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Install, point at the gateway, feed it text.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/agents/sentiment-analyzer-agent
npm install
npm run dev -- --file ./reviews.txt`,
        },
      },
    ],
    why:
      "Classification quality is a model-tier question, not a coding question: the same agent on an economy model versus a frontier model shows the cost/quality curve right in the gateway's per-request pricing.",
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/sentiment-analyzer-agent",
  },
  {
    slug: "data-extractor",
    name: "Data Extractor",
    icon: ScanText,
    tags: ["TypeScript", "AI SDK", "Zod"],
    capabilities: ["Entity Extraction", "Structured Output", "NLP"],
    summary:
      "Pulls people, orgs, dates, amounts, locations, emails, and phones out of unstructured text.",
    intro:
      "The unglamorous workhorse: an email thread or incident report goes in, typed entities come out. Good demonstration of extraction against long inputs and why schema-stable output matters for downstream code.",
    facts: [
      { label: "Pattern", value: "Entity extraction" },
      { label: "Entity types", value: "People, orgs, dates, money, places, contacts" },
      { label: "Output", value: "Validated JSON list" },
      { label: "Time to running", value: "~5 minutes" },
    ],
    tools: [
      { name: "extract_entities", desc: "Emit entities with type, raw span, and normalized value." },
    ],
    steps: [
      {
        title: "Clone and run",
        body: "Install and pass the document to extract from.",
        code: {
          label: "bash",
          code: `git clone https://github.com/theopenco/llmgateway-templates.git
cd llmgateway-templates/agents/data-extractor-agent
npm install
npm run dev -- --file ./thread.eml`,
        },
      },
    ],
    why:
      "Extraction prompts grow with the document; the cache question again. Long prefixes that stay stable turn extraction cost from linear-in-document to mostly-pay-for-the-new-part.",
    githubUrl: "https://github.com/theopenco/llmgateway-templates/tree/main/agents/data-extractor-agent",
  },
];

export function agentBySlug(slug: string): AgentDetail | undefined {
  return AGENTS.find((a) => a.slug === slug);
}
