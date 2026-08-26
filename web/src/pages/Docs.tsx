// Docs — public API documentation for the gateway. Static content: quickstart,
// the three inbound dialects, auth via virtual keys, and curl + Python SDK
// code examples pointed at the gateway base_url.

import { Link } from "react-router-dom";
import { BookOpen, KeyRound, Terminal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

function CodeBlock(props: { children: string; label?: string }) {
  return (
    <div className="overflow-hidden rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
      {props.label && (
        <div className="border-b border-[var(--admin-border)] px-3 py-1.5">
          <span className="admin-label text-[10px]">{props.label}</span>
        </div>
      )}
      <pre className="overflow-x-auto px-3.5 py-3 text-[12px] leading-relaxed" style={{ fontFamily: MONO }}>
        <code className="text-[var(--admin-text-muted)]">{props.children}</code>
      </pre>
    </div>
  );
}

function Section(props: {
  icon: LucideIcon;
  title: string;
  desc?: string;
  children: React.ReactNode;
}) {
  const Icon = props.icon;
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
          <Icon className="h-3.5 w-3.5" style={{ color: "rgba(59,130,246,0.85)" }} />
        </span>
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.title}
        </h3>
      </div>
      {props.desc && <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{props.desc}</p>}
      <div className="space-y-3">{props.children}</div>
    </section>
  );
}

const DIALECTS = [
  {
    name: "OpenAI Chat Completions",
    path: "/v1/chat/completions",
    note: "The classic /v1/chat/completions surface — every OpenAI-compatible client works out of the box.",
    curl: `curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello, wiwi."}]
  }'`,
    py: `from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-wiwi-…",
)
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello, wiwi."}],
)
print(resp.choices[0].message.content)`,
  },
  {
    name: "OpenAI Responses (Codex CLI)",
    path: "/v1/responses",
    note: "The /v1/responses surface used by the Codex CLI and the OpenAI Responses SDK.",
    curl: `curl http://localhost:4000/v1/responses \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4o",
    "input": "Refactor this function to be pure."
  }'`,
    py: `from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-wiwi-…",
)
resp = client.responses.create(
    model="gpt-4o",
    input="Refactor this function to be pure.",
)
print(resp.output_text)`,
  },
  {
    name: "Anthropic Messages (Claude Code)",
    path: "/v1/messages",
    note: "The /v1/messages surface — point Claude Code or the Anthropic SDK at the gateway and back it with any provider.",
    curl: `curl http://localhost:4000/v1/messages \\
  -H "x-api-key: sk-wiwi-…" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "claude-3-5-sonnet",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Hello, wiwi."}]
  }'`,
    py: `import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:4000",
    api_key="sk-wiwi-…",
)
resp = client.messages.create(
    model="claude-3-5-sonnet",
    max_tokens=256,
    messages=[{"role": "user", "content": "Hello, wiwi."}],
)
print(resp.content[0].text)`,
  },
];

export function DocsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <div>
        <div className="mb-2 flex items-center gap-2">
          <BookOpen size={16} className="text-[var(--admin-text-dim)]" />
          <span className="admin-label">Documentation</span>
        </div>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          Point any client at wiwi
        </h1>
        <p className="mt-3 text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          wiwi is a single endpoint that speaks every inbound dialect and routes to every
          outbound provider. Bring your own client — OpenAI SDK, Anthropic SDK, Codex CLI,
          Claude Code, or plain <code style={{ fontFamily: MONO }}>curl</code> — retarget it
          at the gateway, and authenticate with a virtual key.
        </p>
      </div>

      {/* quickstart */}
      <Card className="p-5">
        <Section
          icon={Terminal}
          title="Quickstart"
          desc="Assuming wiwi is running on http://localhost:4000, every path below is relative to /v1. The Authorization header (or x-api-key for Anthropic) carries a virtual key you mint in the console."
        >
          <CodeBlock label="bash">
{`# 1. Mint a virtual key in the admin UI (http://localhost:4000/admin)
#    or via the master key against /admin/keys/generate.

# 2. Point any OpenAI-compatible client at the gateway:
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…`}
          </CodeBlock>
        </Section>
      </Card>

      {/* auth */}
      <Card className="p-5">
        <Section
          icon={KeyRound}
          title="Authentication"
          desc="Callers authenticate with a virtual key — never a provider key. Virtual keys are SHA-256-hashed at rest; per-key budgets, rate limits, and model allowlists are enforced before a request ever leaves the gateway."
        >
          <div className="flex flex-wrap gap-2 text-[12px]">
            <Badge tone="blue">
              <span style={{ fontFamily: MONO }}>Authorization: Bearer sk-wiwi-…</span>
            </Badge>
            <Badge tone="violet">
              <span style={{ fontFamily: MONO }}>x-api-key: sk-wiwi-…</span>
            </Badge>
            <Badge tone="gray">OpenAI · Responses · Anthropic</Badge>
          </div>
          <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
            The same key works across all three surfaces — wiwi decodes the inbound dialect
            and routes to the configured provider, so e.g. Claude Code can be backed by GPT.
          </p>
        </Section>
      </Card>

      {/* dialects */}
      <Card className="p-5">
        <Section
          icon={BookOpen}
          title="Inbound dialects"
          desc="Each surface maps onto the same canonical internal representation. Responses are re-encoded in the caller's dialect on the way back out."
        >
          <div className="space-y-5">
            {DIALECTS.map((d) => (
              <div key={d.name} className="space-y-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <h4 className="text-[14px] font-semibold text-[var(--admin-text)]">{d.name}</h4>
                  <Badge tone="gray">
                    <span style={{ fontFamily: MONO }}>{d.path}</span>
                  </Badge>
                </div>
                <p className="text-[12px] leading-relaxed text-[var(--admin-text-muted)]">{d.note}</p>
                <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                  <CodeBlock label="curl">{d.curl}</CodeBlock>
                  <CodeBlock label="python">{d.py}</CodeBlock>
                </div>
              </div>
            ))}
          </div>
        </Section>
      </Card>

      {/* cross-provider */}
      <Card className="p-5">
        <Section
          icon={Terminal}
          title="Cross-provider routing"
          desc="Because every direction goes dialect → IR → provider, the caller's dialect is decoupled from the upstream provider. A Claude Code session (Anthropic Messages) can be backed by GPT, and vice versa — wiwi re-encodes on both sides."
        >
          <CodeBlock label="wiwi.yaml">{`model_list:
  - model_name: gpt-4o            # caller asks for this
    wiwi_params:
      provider_account: openai-prod
      model: gpt-4o
  - model_name: claude-3-5-sonnet
    wiwi_params:
      provider_account: anthropic-prod
      model: claude-3-5-sonnet`}</CodeBlock>
          <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
            Clients request a <code style={{ fontFamily: MONO }}>model_name</code>; wiwi routes
            to the configured provider account and native model id. Key pools, retries,
            cooldowns, and fallbacks are wired in the same config.
          </p>
        </Section>
      </Card>

      <div className="rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-6 text-center">
        <p className="text-[14px] text-[var(--admin-text-muted)]">
          Ready to try it?{" "}
          <Link to="/playground" className="text-blue-300 hover:text-blue-200">
            Open the playground
          </Link>{" "}
          or{" "}
          <Link to="/signup" className="text-blue-300 hover:text-blue-200">
            create an account
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
