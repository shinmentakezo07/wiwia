// Docs — public API documentation for the gateway. Sticky scroll-tracking
// sidebar, tabbed code examples with copy buttons, endpoint reference cards
// with HTTP method badges, and feature highlights. Matches the dark design
// system shared with the admin console.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  Boxes,
  Check,
  ChevronRight,
  Copy,
  KeyRound,
  Layers,
  Network,
  Palette,
  RefreshCw,
  Settings2,
  Shield,
  Terminal,
  Wallet,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// ── scroll-spy ─────────────────────────────────────────────────────────────

type Section = { id: string; label: string; icon: LucideIcon };

const SECTIONS: Section[] = [
  { id: "overview", label: "Overview", icon: BookOpen },
  { id: "quickstart", label: "Quickstart", icon: Terminal },
  { id: "authentication", label: "Authentication", icon: KeyRound },
  { id: "endpoints", label: "Endpoints", icon: Network },
  { id: "cross-provider", label: "Cross-provider", icon: RefreshCw },
  { id: "streaming", label: "Streaming", icon: Zap },
  { id: "config", label: "Configuration", icon: Settings2 },
  { id: "features", label: "Features", icon: Layers },
];

function useScrollSpy(ids: string[]) {
  const [active, setActive] = useState(ids[0] ?? "");
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-80px 0px -65% 0px", threshold: 0 },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [ids]);
  return active;
}

function scrollToId(id: string) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ── copy button ───────────────────────────────────────────────────────────

function CopyBtn(props: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(props.text);
        setCopied(true);
        timer.current = setTimeout(() => setCopied(false), 1500);
      }}
      className="absolute right-2.5 top-2.5 flex items-center gap-1 rounded-md border border-white/[0.06] bg-white/[0.02] px-2 py-1 text-[10px] font-medium text-[var(--admin-text-dim)] opacity-0 transition-all hover:text-[var(--admin-text)] group-hover:opacity-100"
      aria-label="Copy code"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// ── code block ─────────────────────────────────────────────────────────────

function CodeBlock(props: { code: string; label?: string }) {
  return (
    <div className="docs-codeblock group relative overflow-hidden rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
      <div className="docs-codeblock-glow" aria-hidden />
      {props.label && (
        <div className="relative z-10 flex items-center justify-between border-b border-[var(--admin-border)] px-3.5 py-1.5">
          <span className="admin-label text-[10px]">{props.label}</span>
          <CopyBtn text={props.code} />
        </div>
      )}
      {!props.label && (
        <div className="absolute right-0 top-0 z-10 px-2.5 py-2 opacity-0 transition-opacity group-hover:opacity-100">
          <CopyBtn text={props.code} />
        </div>
      )}
      <pre className="relative z-10 overflow-x-auto px-3.5 py-3 text-[12px] leading-relaxed" style={{ fontFamily: MONO }}>
        <code className="text-[var(--admin-text-muted)]">{props.code}</code>
      </pre>
    </div>
  );
}

// ── tabbed code block ──────────────────────────────────────────────────────

function TabbedCode(props: {
  tabs: { label: string; code: string }[];
}) {
  const [idx, setIdx] = useState(0);
  const tab = props.tabs[idx];
  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-1 rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)] p-1">
        {props.tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            onClick={() => setIdx(i)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors ${
              i === idx
                ? "bg-blue-500/10 text-blue-300"
                : "text-[var(--admin-text-dim)] hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <CodeBlock code={tab.code} label={tab.label} />
    </div>
  );
}

// ── endpoint reference ─────────────────────────────────────────────────────

type Method = "POST" | "GET";

const METHOD_STYLES: Record<Method, { bg: string; text: string }> = {
  POST: { bg: "bg-amber-500/10", text: "text-amber-400" },
  GET: { bg: "bg-emerald-500/10", text: "text-emerald-400" },
};

function EndpointCard(props: {
  method: Method;
  path: string;
  desc: string;
  auth?: string;
  example?: { label: string; code: string }[];
  children?: ReactNode;
}) {
  const { method, path, desc, auth, example, children } = props;
  const ms = METHOD_STYLES[method];
  return (
    <div className="rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 transition-colors hover:border-[var(--admin-border-hover)]">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`flex h-5 min-w-[48px] items-center justify-center rounded-md px-2 text-[10px] font-bold tracking-wider ${ms.bg} ${ms.text}`}>
          {method}
        </span>
        <code className="text-[13px] font-semibold text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
          {path}
        </code>
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">{desc}</p>
      {auth && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
          <KeyRound size={11} />
          <code style={{ fontFamily: MONO }}>{auth}</code>
        </div>
      )}
      {children}
      {example && (
        <div className="mt-3">
          <TabbedCode tabs={example} />
        </div>
      )}
    </div>
  );
}

// ── feature pill ────────────────────────────────────────────────────────────

function FeatureCard(props: { icon: LucideIcon; title: string; body: string }) {
  const Icon = props.icon;
  return (
    <Card className="p-4 transition-colors hover:border-[var(--admin-border-hover)]">
      <div className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
          <Icon className="h-3.5 w-3.5" style={{ color: "rgba(59,130,246,0.85)" }} />
        </span>
        <h4 className="text-[13px] font-semibold text-[var(--admin-text)]">{props.title}</h4>
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">{props.body}</p>
    </Card>
  );
}

// ── heading anchor ─────────────────────────────────────────────────────────

function SectionHeading(props: { icon: LucideIcon; title: string; subtitle?: string }) {
  const Icon = props.icon;
  return (
    <div className="mb-4 flex items-center gap-3">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
        <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.85)" }} />
      </span>
      <div>
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.title}
        </h2>
        {props.subtitle && (
          <p className="mt-0.5 text-[12px] text-[var(--admin-text-dim)]">{props.subtitle}</p>
        )}
      </div>
    </div>
  );
}

// ── data ───────────────────────────────────────────────────────────────────

const ENDPOINTS = [
  {
    method: "POST" as Method,
    path: "/v1/chat/completions",
    desc: "The classic OpenAI Chat Completions surface. Every OpenAI-compatible client works out of the box.",
    auth: "Authorization: Bearer sk-wiwi-…",
    example: [
      {
        label: "curl",
        code: `curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello, wiwi."}]
  }'`,
      },
      {
        label: "python",
        code: `from openai import OpenAI

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
    ],
  },
  {
    method: "POST" as Method,
    path: "/v1/responses",
    desc: "The OpenAI Responses surface used by the Codex CLI and the Responses SDK.",
    auth: "Authorization: Bearer sk-wiwi-…",
    example: [
      {
        label: "curl",
        code: `curl http://localhost:4000/v1/responses \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4o",
    "input": "Refactor this function to be pure."
  }'`,
      },
      {
        label: "python",
        code: `from openai import OpenAI

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
    ],
  },
  {
    method: "POST" as Method,
    path: "/v1/messages",
    desc: "The Anthropic Messages surface. Point Claude Code or the Anthropic SDK at the gateway and back it with any provider.",
    auth: "x-api-key: sk-wiwi-…",
    example: [
      {
        label: "curl",
        code: `curl http://localhost:4000/v1/messages \\
  -H "x-api-key: sk-wiwi-…" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "claude-3-5-sonnet",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Hello, wiwi."}]
  }'`,
      },
      {
        label: "python",
        code: `import anthropic

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
    ],
  },
  {
    method: "GET" as Method,
    path: "/v1/models",
    desc: "List available models. Returns an OpenAI-compatible list of model objects the caller may request.",
    auth: "Authorization: Bearer sk-wiwi-…",
    example: [
      {
        label: "curl",
        code: `curl http://localhost:4000/v1/models \\
  -H "Authorization: Bearer sk-wiwi-…"`,
      },
    ],
  },
];

// ── page ────────────────────────────────────────────────────────────────────

export function DocsPage() {
  const ids = SECTIONS.map((s) => s.id);
  const active = useScrollSpy(ids);
  const handleClick = useCallback((id: string) => { scrollToId(id); }, []);

  return (
    <div className="relative">
      {/* Hero banner */}
      <div className="docs-hero relative mb-10 overflow-hidden rounded-2xl border border-[var(--admin-border)] px-6 py-10 sm:px-10 sm:py-14">
        <div className="docs-hero-glow" aria-hidden />
        <div className="relative z-10">
          <div className="mb-3 flex items-center gap-2">
            <span className="admin-badge admin-badge-blue inline-flex items-center gap-1.5">
              <BookOpen size={11} /> Documentation
            </span>
            <span className="admin-badge admin-badge-gray inline-flex items-center gap-1.5">
              <Zap size={11} /> v0.1.0
            </span>
          </div>
          <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
            Point any client at{" "}
            <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
              wiwi
            </span>
          </h1>
          <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            wiwi is a single endpoint that speaks every inbound dialect and routes to every
            outbound provider. Bring your own client — OpenAI SDK, Anthropic SDK, Codex CLI,
            Claude Code, or plain <code style={{ fontFamily: MONO }}>curl</code> — retarget it
            at the gateway, and authenticate with a virtual key.
          </p>
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <button
              onClick={() => handleClick("quickstart")}
              className="wiwi-shimmer group inline-flex h-10 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110"
            >
              <Terminal size={14} /> Quickstart
              <ArrowRight size={13} className="transition-transform duration-150 group-hover:translate-x-0.5" />
            </button>
            <button
              onClick={() => handleClick("endpoints")}
              className="inline-flex h-10 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04]"
            >
              <Network size={14} /> API reference
            </button>
          </div>
        </div>
      </div>

      {/* Two-column: sticky sidebar + content */}
      <div className="docs-grid">
        {/* Sidebar */}
        <aside className="docs-sidebar">
          <nav className="sticky top-[80px] space-y-0.5">
            <span className="admin-label mb-2 block px-3">On this page</span>
            {SECTIONS.map((s) => {
              const Icon = s.icon;
              const isActive = active === s.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => handleClick(s.id)}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12px] transition-colors ${
                    isActive
                      ? "bg-blue-500/[0.06] font-medium text-blue-200"
                      : "text-[var(--admin-text-dim)] hover:bg-white/[0.02] hover:text-[var(--admin-text-muted)]"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className="flex-1">{s.label}</span>
                  {isActive && <ChevronRight size={12} className="text-blue-400" />}
                </button>
              );
            })}
            <div className="mt-4 border-t border-[var(--admin-border)] px-3 pt-4">
              <Link
                to="/playground"
                className="flex items-center gap-2 text-[12px] text-[var(--admin-text-muted)] transition-colors hover:text-blue-300"
              >
                <Terminal size={13} /> Open playground
              </Link>
            </div>
          </nav>
        </aside>

        {/* Content */}
        <div className="docs-content space-y-16">
          {/* overview */}
          <section id="overview" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={BookOpen}
              title="Overview"
              subtitle="How the gateway translates and routes requests"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Every request follows the same hub-and-spoke path: the wire codec for the
              inbound dialect decodes the request into a canonical internal representation (IR),
              the router selects a provider and key from the pool, and the adapter encodes
              the IR into the provider's native format. Responses flow back through the same
              path — the adapter decodes the provider response into IR deltas, and the wire
              encoder re-encodes them in the caller's original dialect.
            </p>
            <Card className="mt-5 p-0">
              <div className="grid grid-cols-1 gap-px bg-[var(--admin-border)] md:grid-cols-3">
                <div className="bg-[var(--admin-surface)] p-5">
                  <span className="admin-label mb-2 block">Inbound</span>
                  <div className="space-y-1.5">
                    {["chat/completions", "responses", "messages"].map((p) => (
                      <div key={p} className="rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2">
                        <code className="text-[12px] text-blue-300" style={{ fontFamily: MONO }}>{p}</code>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="flex flex-col items-center justify-center bg-[var(--admin-surface)] p-5">
                  <span className="admin-label mb-2 block">wiwi IR</span>
                  <div className="flex h-12 w-12 items-center justify-center rounded-[12px] shadow-lg shadow-brand-600/20 ring-1 ring-white/[0.06] ring-inset">
                    <img src="/wiwi-logo.png" alt="wiwi" className="h-12 w-12 rounded-[12px] object-cover" />
                  </div>
                  <p className="mt-3 text-center text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
                    canonical decode → encode
                  </p>
                </div>
                <div className="bg-[var(--admin-surface)] p-5">
                  <span className="admin-label mb-2 block">Outbound</span>
                  <div className="space-y-1.5">
                    {["openai", "anthropic", "gemini", "openrouter"].map((p) => (
                      <div key={p} className="rounded-lg border border-[var(--admin-border)] bg-white/[0.015] px-3 py-2">
                        <code className="text-[12px] text-violet-300" style={{ fontFamily: MONO }}>{p}</code>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </Card>
          </section>

          {/* quickstart */}
          <section id="quickstart" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={Terminal}
              title="Quickstart"
              subtitle="Running locally in under a minute"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Assuming wiwi is running on <code style={{ fontFamily: MONO }}>http://localhost:4000</code>,
              every path below is relative to <code style={{ fontFamily: MONO }}>/v1</code>. The
              Authorization header (or <code style={{ fontFamily: MONO }}>x-api-key</code> for Anthropic)
              carries a virtual key you mint in the console.
            </p>
            <div className="mt-5">
              <CodeBlock
                label="bash"
                code={`# 1. Mint a virtual key in the admin UI (http://localhost:4000/admin)
#    or via the master key against /admin/keys/generate.

# 2. Point any OpenAI-compatible client at the gateway:
export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…

# 3. Make a request:
curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer $OPENAI_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'`}
              />
            </div>
          </section>

          {/* authentication */}
          <section id="authentication" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={KeyRound}
              title="Authentication"
              subtitle="Virtual keys — never provider keys"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Callers authenticate with a virtual key — never a provider key. Virtual keys are
              SHA-256-hashed at rest with constant-time comparison; per-key budgets, rate limits,
              and model allowlists are enforced before a request ever leaves the gateway.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Badge tone="blue">
                <code style={{ fontFamily: MONO }}>Authorization: Bearer sk-wiwi-…</code>
              </Badge>
              <Badge tone="violet">
                <code style={{ fontFamily: MONO }}>x-api-key: sk-wiwi-…</code>
              </Badge>
              <Badge tone="gray">OpenAI · Responses · Anthropic</Badge>
            </div>
            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Card className="p-4">
                <div className="flex items-center gap-2">
                  <Shield size={14} className="text-[var(--admin-accent)]" />
                  <h4 className="text-[13px] font-semibold text-[var(--admin-text)]">Hashed at rest</h4>
                </div>
                <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                  SHA-256 with constant-time compare — the plaintext is shown once at creation.
                </p>
              </Card>
              <Card className="p-4">
                <div className="flex items-center gap-2">
                  <Wallet size={14} className="text-amber-400" />
                  <h4 className="text-[13px] font-semibold text-[var(--admin-text)]">Per-key budgets</h4>
                </div>
                <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                  Spend ceilings, model allowlists, and RPM/TPM throttles per key.
                </p>
              </Card>
              <Card className="p-4">
                <div className="flex items-center gap-2">
                  <KeyRound size={14} className="text-violet-400" />
                  <h4 className="text-[13px] font-semibold text-[var(--admin-text)]">One key, all surfaces</h4>
                </div>
                <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                  The same key works across all three inbound dialects.
                </p>
              </Card>
            </div>
          </section>

          {/* endpoints */}
          <section id="endpoints" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={Network}
              title="Endpoints"
              subtitle="The three inbound surfaces plus model listing"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Each surface maps onto the same canonical IR. Responses are re-encoded in the
              caller's dialect on the way back out — so a Claude Code session (Anthropic Messages)
              can be backed by GPT, and vice versa.
            </p>
            <div className="mt-5 space-y-3">
              {ENDPOINTS.map((ep) => (
                <EndpointCard
                  key={ep.path}
                  method={ep.method}
                  path={ep.path}
                  desc={ep.desc}
                  auth={ep.auth}
                  example={ep.example}
                />
              ))}
            </div>
          </section>

          {/* cross-provider */}
          <section id="cross-provider" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={RefreshCw}
              title="Cross-provider routing"
              subtitle="Decouple the caller's dialect from the upstream provider"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Because every direction goes dialect → IR → provider, the caller's dialect is
              decoupled from the upstream provider. Clients request a{" "}
              <code style={{ fontFamily: MONO }}>model_name</code>; wiwi routes to the configured
              provider account and native model id. Key pools, retries, cooldowns, and fallbacks
              are wired in the same config.
            </p>
            <div className="mt-5">
              <CodeBlock
                label="wiwi.yaml"
                code={`model_list:
  - model_name: gpt-4o            # caller asks for this
    wiwi_params:
      provider_account: openai-prod
      model: gpt-4o
  - model_name: claude-3-5-sonnet
    wiwi_params:
      provider_account: anthropic-prod
      model: claude-3-5-sonnet

router_settings:
  strategy: weighted-round-robin
  retries: 2
  cooldown_seconds: 60
  fallbacks:
    - gpt-4o → claude-3-5-sonnet`}
              />
            </div>
          </section>

          {/* streaming */}
          <section id="streaming" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={Zap}
              title="Streaming"
              subtitle="Server-sent events across all three dialects"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Streaming is supported across all three surfaces. Set{" "}
              <code style={{ fontFamily: MONO }}>"stream": true</code> in the request body. The
              gateway decodes the provider's stream into <code style={{ fontFamily: MONO }}>IRStreamDelta</code>{" "}
              events and re-encodes them as SSE in the caller's dialect —{" "}
              <code style={{ fontFamily: MONO }}>data: {"{...}"}\n\n</code> chunks for OpenAI,
              and the Anthropic event taxonomy for Messages.
            </p>
            <div className="mt-5">
              <CodeBlock
                label="curl (streaming)"
                code={`curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Write a haiku."}],
    "stream": true
  }'`}
              />
            </div>
            <p className="mt-3 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
              The streaming contract guarantees: exactly one <code style={{ fontFamily: MONO }}>StreamStart</code>,
              then <code style={{ fontFamily: MONO }}>ToolCallOpen → ArgsDelta* → Close</code> per index,
              then <code style={{ fontFamily: MONO }}>UsageFinal</code>, then{" "}
              <code style={{ fontFamily: MONO }}>Finish</code>, then{" "}
              <code style={{ fontFamily: MONO }}>StreamEnd</code> or{" "}
              <code style={{ fontFamily: MONO }}>StreamError</code>.
            </p>
          </section>

          {/* configuration */}
          <section id="config" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={Settings2}
              title="Configuration"
              subtitle="A single wiwi.yaml — LiteLLM-shaped"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              The entire gateway is configured through one YAML file. Providers hold named
              accounts with pools of keyed entries; <code style={{ fontFamily: MONO }}>model_list</code>{" "}
              maps client-requested names to provider accounts; router settings control strategy,
              retries, cooldowns, and fallbacks. Any string value may reference{" "}
              <code style={{ fontFamily: MONO }}>os.environ/NAME</code> for secret interpolation.
            </p>
            <div className="mt-5">
              <CodeBlock
                label="wiwi.yaml"
                code={`general_settings:
  master_key: os.environ/WIWI_MASTER_KEY
  database_url: os.environ/DATABASE_URL   # postgresql+asyncpg://…

providers:
  openai-prod:
    type: openai
    keys:
      - label: pool-1
        key: os.environ/OPENAI_API_KEY
        weight: 3
      - label: pool-2
        key: os.environ/OPENAI_API_KEY_2
        weight: 1

  anthropic-prod:
    type: anthropic
    keys:
      - label: primary
        key: os.environ/ANTHROPIC_API_KEY

model_list:
  - model_name: gpt-4o
    wiwi_params:
      provider_account: openai-prod
      model: gpt-4o

router_settings:
  strategy: weighted-round-robin
  retries: 2
  cooldown_seconds: 60`}
              />
            </div>
          </section>

          {/* features */}
          <section id="features" className="docs-section scroll-mt-20">
            <SectionHeading
              icon={Layers}
              title="Features"
              subtitle="Built-in for every deployment — no plugins"
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FeatureCard
                icon={Layers}
                title="Three inbound dialects"
                body="OpenAI Chat, OpenAI Responses (Codex CLI), and Anthropic Messages all speak the same canonical IR."
              />
              <FeatureCard
                icon={KeyRound}
                title="Virtual keys"
                body="Per-client credentials with model allowlists, expiry, and spend caps. Callers never see provider keys."
              />
              <FeatureCard
                icon={Wallet}
                title="Budgets & rate limits"
                body="Per-key spend ceilings and RPM/TPM throttles keep noisy tenants from burning your quota."
              />
              <FeatureCard
                icon={Boxes}
                title="Key pools"
                body="Pool multiple keys per provider with smooth weighted round-robin. Exhausted keys cool down automatically."
              />
              <FeatureCard
                icon={RefreshCw}
                title="Retries & fallbacks"
                body="Automatic retries on transient failures, per-key cooldowns, and fallback model groups."
              />
              <FeatureCard
                icon={Palette}
                title="Cost tracking"
                body="Token usage and cost calculation for every call, per key, per model, per provider."
              />
            </div>
          </section>

          {/* CTA */}
          <div className="docs-cta rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-8 text-center">
            <h2 className="text-xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
              Ready to try it?
            </h2>
            <p className="mx-auto mt-2 max-w-md text-[14px] text-[var(--admin-text-muted)]">
              Spin up a gateway in a minute, or jump straight into the playground.
            </p>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/playground"
                className="inline-flex h-10 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110"
              >
                <Terminal size={14} /> Open playground
              </Link>
              <Link
                to="/signup"
                className="inline-flex h-10 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
              >
                Create an account <ArrowRight size={13} />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
