// Integrations — connect the gateway with your favorite tools.
//
// Deliberately monochrome: black surfaces, white type, hairline borders. The
// only colour on the page comes from each client's own logo, so the brands
// carry the visual weight. No glows, gradients or neon accents.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpRight } from "lucide-react";

type Category = "Terminal" | "IDE" | "Desktop" | "Automation";

interface Integration {
  name: string;
  description: string;
  slug: string;
  category: Category;
  logo: string;
}

const L = (f: string) => `/logos/clients/${f}`;

const INTEGRATIONS: Integration[] = [
  { name: "Claude Code", description: "Anthropic's terminal coding agent. Point ANTHROPIC_BASE_URL at the gateway and run it on any model.", slug: "claude-code", category: "Terminal", logo: L("claude.svg") },
  { name: "Codex CLI", description: "OpenAI's terminal agent, served over the Responses API with full tool calling.", slug: "codex-cli", category: "Terminal", logo: L("openai.svg") },
  { name: "OpenCode", description: "Open-source terminal agent. Add the gateway as a custom provider and pick any model.", slug: "opencode", category: "Terminal", logo: L("opencode.svg") },
  { name: "Gemini CLI", description: "Google's open-source terminal agent, routed through the gateway's OpenAI-compatible endpoint.", slug: "gemini-cli", category: "Terminal", logo: L("gemini.svg") },
  { name: "Aider", description: "Pair-programming in your terminal. Set OPENAI_API_BASE to the gateway and use any model.", slug: "aider", category: "Terminal", logo: L("aider.png") },
  { name: "Cursor", description: "Use gateway models in Cursor's chat, plan and agent modes. Tab and inline edit stay on Cursor's backend.", slug: "cursor", category: "IDE", logo: L("cursor.svg") },
  { name: "Cline", description: "Autonomous coding agent for VS Code. Choose the OpenAI-compatible provider and enter the gateway URL.", slug: "cline", category: "IDE", logo: L("cline.svg") },
  { name: "Continue", description: "Open-source assistant for VS Code and JetBrains. Add the gateway as an OpenAI provider in config.", slug: "continue", category: "IDE", logo: L("continue.png") },
  { name: "Roo Code", description: "Multi-mode coding agent for VS Code with an OpenAI-compatible provider option.", slug: "roo-code", category: "IDE", logo: L("roo-code.svg") },
  { name: "Kilo Code", description: "Open-source VS Code agent. Select the OpenAI-compatible provider and use your gateway key.", slug: "kilo-code", category: "IDE", logo: L("kilo-code.svg") },
  { name: "Zed", description: "High-performance editor. Register the gateway as an OpenAI-compatible provider in settings.json.", slug: "zed", category: "IDE", logo: L("zedindustries.svg") },
  { name: "GitHub Copilot app", description: "Bring your own key: use the gateway as a model provider for Copilot agent sessions.", slug: "github-copilot", category: "Desktop", logo: L("githubcopilot.svg") },
  { name: "Open WebUI", description: "Self-hosted ChatGPT-style interface. Add the gateway as an OpenAI API connection.", slug: "open-webui", category: "Desktop", logo: L("open-webui.svg") },
  { name: "LibreChat", description: "Open-source multi-user chat app. Define the gateway as a custom endpoint in librechat.yaml.", slug: "librechat", category: "Desktop", logo: L("librechat.png") },
  { name: "n8n", description: "Workflow automation. Use the OpenAI credential with a custom base URL in any AI node.", slug: "n8n", category: "Automation", logo: L("n8n.svg") },
  { name: "LangChain", description: "Use ChatOpenAI or ChatAnthropic with base_url set to the gateway — nothing else changes.", slug: "langchain", category: "Automation", logo: L("langchain.svg") },
  { name: "Vercel AI SDK", description: "TypeScript SDK. Create an OpenAI-compatible provider pointed at the gateway.", slug: "vercel-ai-sdk", category: "Automation", logo: L("vercel.svg") },
];

const CATEGORIES: (Category | "All")[] = ["All", "Terminal", "IDE", "Desktop", "Automation"];

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 focus-visible:ring-offset-2 focus-visible:ring-offset-black";

function Logo({ src, size = "md" }: { src: string; size?: "sm" | "md" }) {
  const box = size === "sm" ? "size-9 rounded-lg" : "size-11 rounded-xl";
  const img = size === "sm" ? "size-5" : "size-6";
  return (
    <span className={`flex ${box} shrink-0 items-center justify-center border border-white/10 bg-white/[0.04]`}>
      <img src={src} alt="" loading="lazy" className={`${img} rounded-[4px] object-contain`} />
    </span>
  );
}

function IntegrationCard({ item }: { item: Integration }) {
  return (
    <Link
      to={`/guides/${item.slug}`}
      className={`group flex h-full gap-4 rounded-2xl border border-white/10 bg-black p-4 transition-colors duration-200 hover:border-white/30 hover:bg-white/[0.025] motion-reduce:transition-none sm:p-5 ${FOCUS}`}
    >
      <Logo src={item.logo} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-[15px] font-semibold tracking-[-0.01em] text-white">{item.name}</h3>
          <ArrowUpRight
            size={15}
            aria-hidden
            className="ml-auto shrink-0 text-white/25 transition-[color,transform] duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-white group-focus-visible:text-white motion-reduce:transition-none"
          />
        </div>
        <p className="mt-0.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-white/35">{item.category}</p>
        <p className="mt-2.5 text-[13px] leading-relaxed text-white/55">{item.description}</p>
      </div>
    </Link>
  );
}

export function IntegrationsPage() {
  const [category, setCategory] = useState<Category | "All">("All");

  const counts = useMemo(() => {
    const c: Record<string, number> = { All: INTEGRATIONS.length };
    for (const i of INTEGRATIONS) c[i.category] = (c[i.category] ?? 0) + 1;
    return c;
  }, []);

  const filtered = category === "All" ? INTEGRATIONS : INTEGRATIONS.filter((i) => i.category === category);

  return (
    <div className="mx-auto max-w-6xl space-y-16 px-1 pb-24">
      {/* ── hero ── */}
      <section aria-labelledby="integrations-title" className="pt-6 sm:pt-10">
        <div className="grid items-center gap-10 lg:grid-cols-[1fr_1fr] lg:gap-14">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-white/45">Integrations · {INTEGRATIONS.length} clients</p>
            <h1 id="integrations-title" className="mt-5 text-4xl font-bold leading-[1.03] tracking-[-0.04em] text-white sm:text-5xl lg:text-[64px]">
              One gateway.
              <br />
              <span className="text-white/40">Every tool you use.</span>
            </h1>
            <p className="mt-6 max-w-md text-[15px] leading-relaxed text-white/55">
              Swap one base URL and the clients you already rely on run on any model — with keys, budgets and logs in one place.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/guides" className={`inline-flex min-h-11 items-center gap-1.5 rounded-xl bg-white px-5 text-[13.5px] font-semibold text-black transition-colors hover:bg-white/85 ${FOCUS}`}>
                Browse setup guides <ArrowRight size={14} aria-hidden />
              </Link>
              <Link to="/docs" className={`inline-flex min-h-11 items-center rounded-xl border border-white/15 px-5 text-[13.5px] font-medium text-white transition-colors hover:border-white/40 ${FOCUS}`}>
                API docs
              </Link>
            </div>
          </div>

          {/* logo wall — the brands are the only colour on the page */}
          <ul aria-label="Supported clients" className="grid grid-cols-3 gap-px overflow-hidden rounded-2xl border border-white/10 bg-white/10 sm:grid-cols-4">
            {INTEGRATIONS.slice(0, 12).map((i) => (
              <li key={i.slug} className="bg-black">
                <Link
                  to={`/guides/${i.slug}`}
                  className={`group flex aspect-[4/3] flex-col items-center justify-center gap-2.5 transition-colors hover:bg-white/[0.04] focus-visible:bg-white/[0.04] focus-visible:outline-none`}
                >
                  <img src={i.logo} alt="" loading="lazy" className="size-7 rounded-[5px] object-contain transition-transform duration-200 group-hover:scale-110 motion-reduce:transition-none" />
                  <span className="px-1 text-center text-[11px] leading-tight text-white/45 transition-colors group-hover:text-white">{i.name}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ── grid ── */}
      <section aria-label="All integrations" className="space-y-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-[13px] font-semibold text-white">
            {category === "All" ? "All integrations" : category}
            <span className="ml-2 font-mono text-[12px] font-normal text-white/40" aria-live="polite">{filtered.length}</span>
          </h2>
          <div role="group" aria-label="Filter by category" className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 sm:pb-0">
            {CATEGORIES.map((c) => {
              const active = c === category;
              return (
                <button
                  key={c}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setCategory(c)}
                  className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-full border px-4 text-[12.5px] font-medium transition-colors ${FOCUS} ${
                    active ? "border-white bg-white text-black" : "border-white/12 text-white/60 hover:border-white/35 hover:text-white"
                  }`}
                >
                  {c}
                  <span className={`font-mono text-[10.5px] ${active ? "text-black/50" : "text-white/35"}`}>{counts[c] ?? 0}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((item) => (
            <IntegrationCard key={item.slug} item={item} />
          ))}
        </div>
      </section>

      {/* ── missing tool ── */}
      <section className="flex flex-col items-start justify-between gap-5 rounded-2xl border border-white/10 p-6 sm:flex-row sm:items-center sm:p-8">
        <div>
          <h2 className="text-lg font-semibold tracking-[-0.01em] text-white">Don't see your tool?</h2>
          <p className="mt-1.5 max-w-lg text-[13.5px] leading-relaxed text-white/55">
            Anything that speaks OpenAI Chat, OpenAI Responses or Anthropic Messages works — point it at the gateway's base URL.
          </p>
        </div>
        <Link to="/docs" className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl border border-white/15 px-5 text-[13px] font-medium text-white transition-colors hover:border-white/40 ${FOCUS}`}>
          Read the API docs <ArrowRight size={14} aria-hidden />
        </Link>
      </section>
    </div>
  );
}
