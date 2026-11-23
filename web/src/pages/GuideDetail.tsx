// Guides detail — per-tool setup instructions for /guides/:slug. Content is
// grounded in README's "Connecting clients" table and each tool's official
// config format (opencode.jsonc verified against the installed CLI). Rendered
// with the shared detail-page chrome so every public detail surface matches.

import { useParams } from "react-router-dom";
import {
  DetailHeader,
  DetailNotFound,
  StepList,
} from "@/components/detail-page";
import type { DetailStep } from "@/components/detail-page";

interface Guide {
  title: string;
  category: string;
  intro: string;
  steps: DetailStep[];
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
        code: {
          code: `export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=sk-wiwi-…`,
          label: "env",
        },
      },
      {
        title: "Run it",
        body: "Start Claude Code as usual. Any model name your wiwi config exposes is selectable.",
        code: { code: "claude", label: "terminal" },
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
        code: {
          code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…`,
          label: "cursor settings",
        },
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
        code: {
          code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…
Model ID:  gpt-4o   # any model in your wiwi model list`,
          label: "cline settings",
        },
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
        code: { code: "export OPENAI_BASE_URL=http://localhost:4000/v1", label: "env" },
      },
      {
        title: "Authenticate",
        body: "Log in with a virtual key as the API key (or set OPENAI_API_KEY).",
        code: { code: "export OPENAI_API_KEY=sk-wiwi-…", label: "env" },
      },
      {
        title: "Run it",
        body: "Pick any model exposed by your wiwi config — the Responses dialect is translated to whatever the backing provider speaks.",
        code: { code: "codex --model gpt-4o", label: "terminal" },
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
        code: {
          code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…`,
          label: "n8n credentials",
        },
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
        code: {
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
          label: "opencode.json",
        },
      },
      {
        title: "Run it",
        body: "Start the TUI and pick a wiwi model, or run one-shot with the provider/model slug.",
        code: {
          code: `opencode                       # TUI — select a wiwi model
opencode run -m wiwi/gpt-4o "hi"`,
          label: "terminal",
        },
      },
      {
        title: "Why it works",
        body: "wiwi speaks the OpenAI Chat Completions dialect on /v1, so OpenCode's openai-compatible adapter handles the transport while wiwi translates to whichever provider backs the model.",
      },
    ],
  },
  continue: {
    title: "Continue",
    category: "IDE",
    intro:
      "Use the gateway with Continue's open-source AI code assistant CLI. Continue supports OpenAI-compatible providers via its config file.",
    steps: [
      {
        title: "Add a provider block",
        body: "In Continue's config, define a provider with the gateway's base URL and a virtual key.",
        code: {
          code: `name: wiwi
version: 1
models:
  - name: gpt-4o
    provider: openai
    model: gpt-4o
    apiBase: http://localhost:4000/v1
    apiKey: sk-wiwi-…`,
          label: "config.yaml",
        },
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
        code: {
          code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…`,
          label: "copilot settings",
        },
      },
      {
        title: "Select models",
        body: "Models from your wiwi config appear in the picker. Agent sessions run against wiwi and are logged with full usage and cost.",
      },
    ],
  },
  "gemini-cli": {
    title: 'Gemini CLI',
    category: "Terminal",
    intro:
      "Run Google's open-source Gemini CLI through the gateway. The CLI talks to the Gemini API, so point it at a wiwi deployment that exposes your Gemini-family models through an OpenAI-compatible route, or use a fork such as one with OpenAI provider support.",
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Point the CLI at the gateway',
        body: 'Set the base URL and key in your shell.',
        code: {
          code: `export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…`,
          label: "env",
        },
      },
      {
        title: 'Run it',
        body: 'Start the CLI and pick a model your wiwi config exposes.',
        code: {
          code: `gemini`,
          label: "terminal",
        },
      },
    ],
  },
  "aider": {
    title: 'Aider',
    category: "Terminal",
    intro:
      'Aider supports any OpenAI-compatible endpoint. Prefix the model with openai/ so Aider uses the generic OpenAI client against wiwi.',
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Point Aider at the gateway',
        body: 'Aider reads the base URL and key from the environment.',
        code: {
          code: `export OPENAI_API_BASE=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…`,
          label: "env",
        },
      },
      {
        title: 'Run it',
        body: 'Use the openai/ prefix with any model name from your wiwi config.',
        code: {
          code: `aider --model openai/gpt-4o`,
          label: "terminal",
        },
      },
    ],
  },
  "roo-code": {
    title: 'Roo Code',
    category: "IDE",
    intro:
      'Roo Code ships an OpenAI Compatible provider. Every mode (Code, Architect, Ask, Debug) then routes through wiwi.',
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Add the provider',
        body: 'Roo Code settings → API Provider → OpenAI Compatible.',
        code: {
          code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…
Model:     gpt-4o`,
          label: "roo code settings",
        },
      },
      {
        title: 'Use it',
        body: "Pick any model wiwi exposes. Requests appear in the gateway's request logs with usage and cost.",
      },
    ],
  },
  "kilo-code": {
    title: 'Kilo Code',
    category: "IDE",
    intro:
      'Kilo Code is an open-source VS Code agent with an OpenAI Compatible provider option.',
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Add the provider',
        body: 'Kilo Code settings → API Provider → OpenAI Compatible.',
        code: {
          code: `Base URL:  http://localhost:4000/v1
API Key:   sk-wiwi-…
Model:     gpt-4o`,
          label: "kilo code settings",
        },
      },
      {
        title: 'Use it',
        body: 'Switch models freely; wiwi handles routing, failover and budgets.',
      },
    ],
  },
  "zed": {
    title: 'Zed',
    category: "IDE",
    intro:
      "Register the gateway as an OpenAI-compatible provider in Zed's settings so the Agent Panel and inline assist can use any wiwi model.",
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Add the provider',
        body: "Add this to ~/.config/zed/settings.json, then paste the virtual key when Zed prompts for the provider's API key.",
        code: {
          code: `{
  "language_models": {
    "openai_compatible": {
      "wiwi": {
        "api_url": "http://localhost:4000/v1",
        "available_models": [
          { "name": "gpt-4o", "max_tokens": 128000 }
        ]
      }
    }
  }
}`,
          label: "settings.json",
        },
      },
      {
        title: 'Select the model',
        body: 'Open the Agent Panel and choose the wiwi model.',
      },
    ],
  },
  "open-webui": {
    title: 'Open WebUI',
    category: "Desktop",
    intro:
      'Open WebUI connects to any OpenAI API. Add wiwi as a connection and every model in your config appears in the model picker.',
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Add a connection',
        body: 'Admin Panel → Settings → Connections → OpenAI API → add.',
        code: {
          code: `URL:  http://localhost:4000/v1
Key:  sk-wiwi-…`,
          label: "open webui",
        },
      },
      {
        title: 'Or set it at startup',
        body: 'Pass the connection as environment variables when running the container.',
        code: {
          code: `docker run -d -p 3000:8080 \
  -e OPENAI_API_BASE_URL=http://localhost:4000/v1 \
  -e OPENAI_API_KEY=sk-wiwi-… \
  ghcr.io/open-webui/open-webui:main`,
          label: "terminal",
        },
      },
    ],
  },
  "librechat": {
    title: 'LibreChat',
    category: "Desktop",
    intro:
      "Define wiwi as a custom endpoint in librechat.yaml. LibreChat lists the gateway's models automatically when fetch is enabled.",
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Add a custom endpoint',
        body: 'In librechat.yaml:',
        code: {
          code: `endpoints:
  custom:
    - name: "wiwi"
      apiKey: "\${WIWI_API_KEY}"
      baseURL: "http://localhost:4000/v1"
      models:
        default: ["gpt-4o"]
        fetch: true`,
          label: "librechat.yaml",
        },
      },
      {
        title: 'Set the key',
        body: "Put the virtual key in LibreChat's .env and restart.",
        code: {
          code: `WIWI_API_KEY=sk-wiwi-…`,
          label: ".env",
        },
      },
    ],
  },
  "langchain": {
    title: 'LangChain',
    category: "Automation",
    intro:
      "LangChain's OpenAI and Anthropic chat models accept a base URL, so pointing them at wiwi is a one-line change.",
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Python',
        body: "Use ChatOpenAI with the gateway's base URL.",
        code: {
          code: `from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o",
    base_url="http://localhost:4000/v1",
    api_key="sk-wiwi-…",
)
print(llm.invoke("Hello").content)`,
          label: "python",
        },
      },
      {
        title: 'Any model',
        body: 'Change model= to any name your wiwi config exposes — Claude, Gemini, DeepSeek — with no other code changes.',
      },
    ],
  },
  "vercel-ai-sdk": {
    title: 'Vercel AI SDK',
    category: "Automation",
    intro:
      'Create an OpenAI-compatible provider pointed at wiwi and use it with generateText, streamText and tool calling.',
    steps: [
      {
        title: 'Mint a virtual key',
        body: 'Create a virtual key (sk-wiwi-…) in the admin UI. Each tool gets its own key, budget and logs.',
      },
      {
        title: 'Install',
        body: 'Add the AI SDK and the OpenAI-compatible provider.',
        code: {
          code: `npm install ai @ai-sdk/openai-compatible`,
          label: "terminal",
        },
      },
      {
        title: 'Create the provider',
        body: 'Use it anywhere the SDK accepts a model.',
        code: {
          code: `import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { generateText } from "ai";

const wiwi = createOpenAICompatible({
  name: "wiwi",
  baseURL: "http://localhost:4000/v1",
  apiKey: process.env.WIWI_API_KEY,
});

const { text } = await generateText({
  model: wiwi("gpt-4o"),
  prompt: "Hello",
});`,
          label: "ts",
        },
      },
    ],
  },
};

export function GuideDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const guide = slug ? GUIDES[slug] : undefined;

  if (!guide) {
    return <DetailNotFound backTo="/guides" backLabel="All guides" what="Guide" />;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-10 pb-16">
      <DetailHeader
        backTo="/guides"
        backLabel="All guides"
        badge={guide.category}
        title={guide.title}
        intro={guide.intro}
      />
      <StepList steps={guide.steps} />
    </div>
  );
}
