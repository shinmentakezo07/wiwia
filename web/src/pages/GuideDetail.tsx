// Guides detail — per-tool setup instructions for /guides/:slug. Content is
// grounded in README's "Connecting clients" table and each tool's official
// config format (opencode.jsonc verified against the installed CLI).

import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Card } from "@/components/ui";

interface GuideStep {
  title: string;
  body: string;
  code?: string;
  codeLabel?: string;
}

interface Guide {
  title: string;
  category: string;
  intro: string;
  steps: GuideStep[];
}

const GUIDES: Record<string, Guide> = {
  "claude-code": {
    title: "Claude Code",
    category: "Terminal",
    intro:
      "Point Claude Code's Anthropic transport at the gateway. Claude Code speaks the Anthropic Messages dialect, so every model behind wiwi — GPT, Gemini, DeepSeek — works without Claude Code knowing.",
    steps: [
      {
        title: "Mint a virtual key",
        body: "Virtual keys (sk-wiwi-…) are the only credential clients ever see. Create one in the admin UI or via the keys API.",
      },
      {
        title: "Point Claude Code at the gateway",
        body: "Claude Code reads its endpoint from environment variables — no config file needed.",
        code: `export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=sk-wiwi-…`,
        codeLabel: "env",
      },
      {
        title: "Run it",
        body: "Start Claude Code as usual. Any model name your wiwi config exposes is selectable.",
        code: "claude",
        codeLabel: "terminal",
      },
    ],
  },
  cursor: {
    title: "Cursor IDE",
    category: "IDE",
    intro:
      "Use the gateway as a custom OpenAI-compatible endpoint in Cursor. Agent and chat modes route through wiwi; Cursor's tab autocomplete keeps using Cursor's own backend.",
    steps: [
      {
        title: "Open model settings",
        body: "Cursor Settings → Models → OpenAI API Key. Enable the override so Cursor sends requests to your endpoint instead of its own.",
      },
      {
        title: "Point Cursor at the gateway",
        body: "Set the base URL to your wiwi deployment's /v1 path and paste a virtual key as the API key.",
        code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…`,
        codeLabel: "cursor settings",
      },
      {
        title: "Add models",
        body: "Add the model names exactly as they appear in your wiwi model list. Requests arrive on /v1/chat/completions and are translated by the hub.",
      },
    ],
  },
  cline: {
    title: "Cline (VS Code)",
    category: "IDE",
    intro:
      "Configure Cline with an OpenAI-compatible provider pointing at the gateway for AI-powered coding assistance directly in VS Code.",
    steps: [
      {
        title: "Open Cline settings",
        body: "In VS Code, open the Cline extension settings and pick API Provider → OpenAI Compatible.",
      },
      {
        title: "Enter the gateway endpoint",
        body: "Set the base URL and paste a virtual key. Cline sends standard Chat Completions requests.",
        code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…
Model ID:  gpt-4o   # any model in your wiwi model list`,
        codeLabel: "cline settings",
      },
      {
        title: "Verify",
        body: "Send a test prompt. The request shows up in wiwi's request logs (Console → Request logs) with model, tokens, and cost.",
      },
    ],
  },
  "codex-cli": {
    title: "Codex CLI",
    category: "Terminal",
    intro:
      "Codex CLI speaks the OpenAI Responses dialect natively — one of wiwi's three inbound surfaces. Point it at the gateway and every outbound provider becomes available to Codex.",
    steps: [
      {
        title: "Point Codex at the gateway",
        body: "Codex reads its endpoint from the environment.",
        code: "export OPENAI_BASE_URL=http://localhost:4000/v1",
        codeLabel: "env",
      },
      {
        title: "Authenticate",
        body: "Log in with a virtual key as the API key (or set OPENAI_API_KEY).",
        code: "export OPENAI_API_KEY=sk-wiwi-…",
        codeLabel: "env",
      },
      {
        title: "Run it",
        body: "Pick any model exposed by your wiwi config — the Responses dialect is translated to whatever the backing provider speaks.",
        code: "codex --model gpt-4o",
        codeLabel: "terminal",
      },
    ],
  },
  "devpass-code": {
    title: "DevPass Code",
    category: "Terminal",
    intro:
      "DevPass Code is an open-source terminal coding agent built for the gateway — one browser login, every model, no per-provider keys.",
    steps: [
      {
        title: "Log in once",
        body: "DevPass Code authorizes against the gateway in the browser; no API keys to copy.",
      },
      {
        title: "Pick a model and go",
        body: "Every model in the gateway's catalog is available. Requests ride the same virtual-key path as any other client.",
      },
    ],
  },
  n8n: {
    title: "n8n Workflows",
    category: "Automation",
    intro:
      "Connect n8n workflow automation to the gateway for AI-powered automation pipelines. n8n's OpenAI and OpenAI-compatible nodes work unchanged.",
    steps: [
      {
        title: "Add OpenAI credentials",
        body: "In n8n, create OpenAI credentials with your gateway URL and a virtual key.",
        code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…`,
        codeLabel: "n8n credentials",
      },
      {
        title: "Use the OpenAI node",
        body: "Standard Chat Model / completion nodes send /v1/chat/completions. Set the model field to any model your wiwi config exposes.",
      },
      {
        title: "Observe",
        body: "Each workflow execution appears in wiwi's logs with per-step token usage and cost — useful for budgeting automation workloads.",
      },
    ],
  },
  opencode: {
    title: "OpenCode",
    category: "Terminal",
    intro:
      "OpenCode's provider system is config-file driven: declare a custom provider pointing at the gateway's OpenAI-compatible endpoint and its models become available in the TUI and `opencode run`.",
    steps: [
      {
        title: "Mint a virtual key",
        body: "Create a virtual key (sk-wiwi-…) in the admin UI — OpenCode never needs a real provider key.",
      },
      {
        title: "Declare the provider",
        body: "Add a provider block to opencode.json (project root) or ~/.config/opencode/opencode.json. The npm field selects OpenCode's OpenAI-compatible SDK adapter.",
        code: `{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "wiwi": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "wiwi",
      "options": {
        "baseURL": "http://localhost:4000/v1",
        "apiKey": "sk-wiwi-…"
      },
      "models": {
        "gpt-4o": { "name": "gpt-4o" },
        "claude-sonnet-4": { "name": "claude-sonnet-4" }
      }
    }
  }
}`,
        codeLabel: "opencode.json",
      },
      {
        title: "Run it",
        body: "Start the TUI and pick a wiwi model, or run one-shot with the provider/model slug.",
        code: `opencode                       # TUI — select a wiwi model
opencode run -m wiwi/gpt-4o "hi"`,
        codeLabel: "terminal",
      },
      {
        title: "Why it works",
        body: "wiwi speaks the OpenAI Chat Completions dialect on /v1, so OpenCode's openai-compatible adapter handles the transport while wiwi translates to whichever provider backs the model.",
      },
    ],
  },
  continue: {
    title: "Continue CLI",
    category: "Terminal",
    intro:
      "Use the gateway with Continue's open-source AI code assistant CLI. Continue supports OpenAI-compatible providers via its config file.",
    steps: [
      {
        title: "Add a provider block",
        body: "In Continue's config, define a provider with the gateway's base URL and a virtual key.",
        code: `name: wiwi
version: 1
models:
  - name: gpt-4o
    provider: openai
    model: gpt-4o
    apiBase: http://localhost:4000/v1
    apiKey: sk-wiwi-…`,
        codeLabel: "config.yaml",
      },
      {
        title: "Run it",
        body: "Continue sends Chat Completions to the gateway; wiwi routes to whichever provider backs the model.",
      },
    ],
  },
  "github-copilot": {
    title: "GitHub Copilot app",
    category: "Desktop",
    intro:
      "Use the gateway as a model provider in GitHub's Copilot desktop app for agent sessions with any model. The app's provider list accepts custom OpenAI-compatible endpoints (BYOK).",
    steps: [
      {
        title: "Add a custom provider",
        body: "In the Copilot app's model settings, add a provider with an OpenAI-compatible endpoint.",
        code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…`,
        codeLabel: "copilot settings",
      },
      {
        title: "Select models",
        body: "Models from your wiwi config appear in the picker. Agent sessions run against wiwi and are logged with full usage and cost.",
      },
    ],
  },
};

function CodeBlock(props: { code: string; label?: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--admin-border)] bg-zinc-950">
      {props.label && (
        <div className="flex items-center gap-2 border-b border-[var(--admin-border)] bg-white/[0.02] px-4 py-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
          <span className="ml-1.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
            {props.label}
          </span>
        </div>
      )}
      <pre className="overflow-x-auto p-4">
        <code className="font-mono text-[12.5px] leading-relaxed text-zinc-200">
          {props.code}
        </code>
      </pre>
    </div>
  );
}

export function GuideDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const guide = slug ? GUIDES[slug] : undefined;

  if (!guide) {
    return (
      <div className="mx-auto max-w-2xl space-y-6 py-20 text-center">
        <h1 className="text-2xl font-semibold text-[var(--admin-text)]">
          Guide not found
        </h1>
        <p className="text-[14px] text-[var(--admin-text-muted)]">
          No guide exists at this address.
        </p>
        <Link
          to="/guides"
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-blue-400 hover:text-blue-300"
        >
          <ArrowLeft size={14} /> All guides
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 pb-16">
      <div>
        <Link
          to="/guides"
          className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--admin-text-dim)] transition-colors hover:text-blue-400"
        >
          <ArrowLeft size={14} /> All guides
        </Link>
      </div>

      {/* ── header ── */}
      <header className="space-y-3">
        <span className="admin-badge admin-badge-gray">{guide.category}</span>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          {guide.title}
        </h1>
        <p className="max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          {guide.intro}
        </p>
      </header>

      {/* ── steps ── */}
      <ol className="space-y-8">
        {guide.steps.map((step, i) => (
          <li key={step.title}>
            <Card className="p-5">
              <div className="flex items-start gap-3.5">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--admin-border)] bg-white/[0.03] text-[12px] font-semibold text-blue-300">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1 space-y-3">
                  <h2 className="text-[15px] font-semibold text-[var(--admin-text)]">
                    {step.title}
                  </h2>
                  <p className="text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                    {step.body}
                  </p>
                  {step.code && (
                    <CodeBlock code={step.code} label={step.codeLabel} />
                  )}
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}
