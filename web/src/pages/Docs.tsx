// Docs — public API documentation for the gateway. A reference-manual layout:
// a numbered left rail that tracks the reader, hairline-separated sections
// (no floating cards), tabbed code examples with copy buttons, and an endpoint
// reference list. Matches the dark design system shared with the admin console.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUp,
  BookOpen,
  Boxes,
  Check,
  ChevronDown,
  Copy,
  Hash,
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

// ── reading progress ───────────────────────────────────────────────────────

function useScrollProgress() {
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      const doc = document.documentElement;
      const max = doc.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0);
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  return progress;
}

function BackToTop() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 700);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <button
      type="button"
      aria-label="Back to top"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      className={`docs-backtop ${visible ? "is-visible" : ""}`}
    >
      <ArrowUp size={16} />
    </button>
  );
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
      className="inline-flex h-11 min-w-[72px] shrink-0 items-center justify-center gap-1.5 rounded-md border border-white/[0.06] bg-white/[0.03] px-2.5 text-[10px] font-medium text-[var(--admin-text-dim)] transition-colors hover:border-white/[0.14] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
      aria-label="Copy code"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// Small inline copy button for endpoint paths.
function PathCopyBtn(props: { text: string }) {
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
      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-white/[0.06] bg-white/[0.02] text-[var(--admin-text-dim)] transition-colors hover:border-white/[0.14] hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
      aria-label={`Copy ${props.text}`}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  );
}

// ── syntax highlighting (dependency-free, line-safe token coloring) ────────

type Lang = "bash" | "python" | "yaml";

// Ordered alternation — earlier groups win at the same position.
const LANG_REGEX: Record<Lang, RegExp> = {
  bash: /(?<com>#.*$)|(?<url>https?:\/\/[^\s"']+)|(?<str>"[^"\n]*"|'[^'\n]*')|(?<var>\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)|(?<flag>\s--?[A-Za-z][\w-]*)|(?<cmd>^export\s|^curl\b)/gm,
  python: /(?<com>#.*$)|(?<str>f?"[^"\n]*"|f?'[^'\n]*')|(?<kw>\b(?:from|import|print|def|return|class|True|False|None)\b)|(?<num>\b\d+\b)/gm,
  yaml: /(?<com>(?:^|\s)#.*$)|(?<key>^[ \t]*-?[ \t]*[\w.-]+(?=:))|(?<str>"[^"\n]*"|'[^'\n]*')|(?<bool>\b(?:true|false)\b)|(?<num>\b\d+(?:\.\d+)?\b)/gm,
};

const TOKEN_CLASS: Record<string, string> = {
  com: "tok-com",
  str: "tok-str",
  var: "tok-var",
  flag: "tok-flag",
  cmd: "tok-kw",
  kw: "tok-kw",
  key: "tok-key",
  num: "tok-num",
  bool: "tok-bool",
  url: "tok-url",
};

function highlight(code: string, lang: Lang): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of code.matchAll(LANG_REGEX[lang])) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(code.slice(last, idx));
    const groups = m.groups ?? {};
    const name = Object.keys(groups).find((g) => groups[g] !== undefined);
    out.push(
      <span key={k++} className={name ? TOKEN_CLASS[name] : undefined}>
        {m[0]}
      </span>,
    );
    last = idx + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}

function langFromLabel(label: string): Lang {
  const l = label.toLowerCase();
  if (l.includes("python")) return "python";
  if (l.includes("yaml") || l.includes("yml")) return "yaml";
  return "bash";
}

// Language identity dots for the tab bar: bash/curl emerald, python amber,
// yaml violet — the same hue family the syntax tokens already speak.
const LANG_DOT: Record<Lang, string> = {
  bash: "bg-emerald-400",
  python: "bg-amber-400",
  yaml: "bg-violet-400",
};

// ── code block ─────────────────────────────────────────────────────────────

function CodeBlock(props: { code: string; label?: string; lang?: Lang }) {
  const lang = props.lang ?? (props.label ? langFromLabel(props.label) : "bash");
  return (
    <div className="docs-codeblock group">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--admin-border)] bg-white/[0.015] px-3 py-1.5">
        <span className="admin-label truncate text-[10px]">{props.label ?? lang}</span>
        <CopyBtn text={props.code} />
      </div>
      <pre
        tabIndex={0}
        role="group"
        aria-label={props.label ? `${props.label} code example` : "Code example"}
        className="overflow-x-auto px-3.5 py-3 text-[12px] leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
        style={{ fontFamily: MONO }}
      >
        <code className="text-[var(--admin-text-muted)]">{highlight(props.code, lang)}</code>
      </pre>
    </div>
  );
}

// ── tabbed code block ──────────────────────────────────────────────────────

function TabbedCode(props: { tabs: { label: string; code: string }[] }) {
  const [idx, setIdx] = useState(0);
  const tab = props.tabs[idx];
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-1">
        {props.tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            onClick={() => setIdx(i)}
            aria-pressed={i === idx}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-md px-3 text-[11px] font-medium transition-colors sm:min-h-0 sm:py-1.5 ${
              i === idx
                ? "bg-blue-500/[0.12] text-blue-200 ring-1 ring-blue-400/20"
                : "text-[var(--admin-text-muted)] hover:bg-white/[0.03] hover:text-[var(--admin-text)]"
            } focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full transition-opacity ${LANG_DOT[langFromLabel(t.label)]} ${
                i === idx ? "opacity-100" : "opacity-40"
              }`}
              aria-hidden
            />
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

function EndpointRow(props: {
  method: Method;
  path: string;
  desc: string;
  auth?: string;
  clients?: string[];
  example?: { label: string; code: string }[];
}) {
  const { method, path, desc, auth, clients, example } = props;
  const ms = METHOD_STYLES[method];
  return (
    <article className="group/endpoint py-5">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`docs-method-badge flex h-5 min-w-[48px] items-center justify-center rounded-md px-2 text-[10px] font-bold tracking-wider ${ms.bg} ${ms.text}`}
        >
          {method}
        </span>
        <code className="text-[13px] font-semibold text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
          {path}
        </code>
        <PathCopyBtn text={path} />
        {clients && (
          <div className="docs-endpoint-clients flex flex-wrap items-center gap-1.5">
            {clients.map((c) => (
              <span
                key={c}
                className="rounded-full border border-white/[0.06] bg-white/[0.02] px-2 py-0.5 text-[9.5px] font-medium tracking-wide text-[var(--admin-text-dim)] transition-colors group-hover/endpoint:border-white/[0.1] group-hover/endpoint:text-[var(--admin-text-muted)]"
              >
                {c}
              </span>
            ))}
          </div>
        )}
      </div>
      <p className="mt-2 max-w-[74ch] text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
        {desc}
      </p>
      {auth && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-[var(--admin-text-dim)]">
          <KeyRound size={11} className="shrink-0" aria-hidden />
          <code style={{ fontFamily: MONO }}>{auth}</code>
        </div>
      )}
      {example && (
        <div className="mt-3">
          <TabbedCode tabs={example} />
        </div>
      )}
    </article>
  );
}

// ── data ───────────────────────────────────────────────────────────────────

const ENDPOINTS = [
  {
    method: "POST" as Method,
    path: "/v1/chat/completions",
    clients: ["OpenAI SDK", "any compatible client"],
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
    clients: ["Codex CLI", "Responses SDK"],
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
    clients: ["Claude Code", "Anthropic SDK"],
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
    clients: ["model discovery"],
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

// ── provider ecosystem map ────────────────────────────────────────────────

const PROVIDER_ASSETS: { label: string; src?: string; icon?: LucideIcon }[] = [
  { label: "OpenAI", src: "/logos/openai.png" },
  { label: "Anthropic", src: "/logos/anthropic.png" },
  { label: "Gemini", src: "/logos/gemini.png" },
  { label: "OpenRouter", src: "/logos/openrouter.png" },
  { label: "OpenAI-compatible", src: "/logos/openai-compatible.png" },
  { label: "GMI Cloud", src: "/logos/gmicloud.png" },
  { label: "BAI", src: "/logos/bai.png" },
  { label: "WorkBuddy", src: "/logos/workbuddy.svg" },
  { label: "NVIDIA NIM", src: "/logos/nvidia-nim.png" },
  { label: "OpenCode", src: "/logos/opencode.svg" },
  { label: "Cline", icon: Boxes },
];

function ProviderGrid() {
  return (
    <div className="mt-6">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="admin-label">Provider ecosystem</h3>
        <span className="text-[11px] text-[var(--admin-text-dim)]">11 provider types</span>
      </div>
      <div className="docs-provider-grid">
        {PROVIDER_ASSETS.map((provider) => {
          const Logo = provider.icon;
          return (
            <div
              key={provider.label}
              className="docs-provider-item flex min-h-[76px] flex-col items-center justify-center gap-2 px-2 py-3"
            >
              <div className="flex h-7 w-7 items-center justify-center">
                {provider.src ? (
                  <img src={provider.src} alt="" className="h-full w-full object-contain" />
                ) : Logo ? (
                  <Logo className="h-5 w-5 text-[var(--admin-text-muted)]" />
                ) : null}
              </div>
              <span className="max-w-full truncate text-[10px] font-medium text-[var(--admin-text-dim)]">
                {provider.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── request pipeline strip ────────────────────────────────────────────────

const PIPELINE = [
  { chip: "chat/completions", tone: "text-blue-300" },
  { chip: "responses", tone: "text-cyan-300" },
  { chip: "messages", tone: "text-fuchsia-300" },
  { chip: "wiwi IR", tone: "text-violet-300" },
  { chip: "provider adapter", tone: "text-[var(--admin-text-muted)]" },
];

function Pipeline() {
  return (
    <div className="docs-pipeline" aria-label="dialect to IR to provider pipeline">
      {PIPELINE.map((step, i) => (
        <span key={step.chip} className="inline-flex items-center gap-2">
          {i > 0 && (
            <span className="docs-pipe-arrow" aria-hidden>
              →
            </span>
          )}
          <span className={`docs-pipe-chip ${step.tone}`}>{step.chip}</span>
        </span>
      ))}
    </div>
  );
}

// ── section heading ─────────────────────────────────────────────────────────

function SectionHeading(props: {
  id: string;
  title: string;
  subtitle?: string;
  index: number;
}) {
  return (
    <div className="docs-heading group mb-5">
      <p className="admin-label mb-2">Section {String(props.index).padStart(2, "0")}</p>
      <h2 className="flex items-center gap-2 text-[20px] font-semibold tracking-[-0.015em] text-[var(--admin-text)]">
        {props.title}
        <a
          href={`#${props.id}`}
          aria-label={`Link to ${props.title}`}
          onClick={(e) => {
            e.preventDefault();
            scrollToId(props.id);
            history.replaceState(null, "", `#${props.id}`);
          }}
          className="docs-anchor rounded p-0.5 text-[var(--admin-text-dim)] hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
        >
          <Hash size={14} />
        </a>
      </h2>
      {props.subtitle && (
        <p className="mt-1 text-[12.5px] text-[var(--admin-text-dim)]">{props.subtitle}</p>
      )}
    </div>
  );
}

// ── feature grid ───────────────────────────────────────────────────────────

const FEATURES: { icon: LucideIcon; title: string; body: string; tone: string }[] = [
  {
    icon: Layers,
    tone: "text-blue-300",
    title: "Three inbound dialects",
    body: "OpenAI Chat, OpenAI Responses (Codex CLI), and Anthropic Messages all speak the same canonical IR.",
  },
  {
    icon: KeyRound,
    tone: "text-violet-300",
    title: "Virtual keys",
    body: "Per-client credentials with model allowlists, expiry, and spend caps. Callers never see provider keys.",
  },
  {
    icon: Wallet,
    tone: "text-amber-300",
    title: "Budgets & rate limits",
    body: "Per-key spend ceilings and RPM/TPM throttles keep noisy tenants from burning your quota.",
  },
  {
    icon: Boxes,
    tone: "text-emerald-300",
    title: "Key pools",
    body: "Pool multiple keys per provider with smooth weighted round-robin. Exhausted keys cool down automatically.",
  },
  {
    icon: RefreshCw,
    tone: "text-cyan-300",
    title: "Retries & fallbacks",
    body: "Automatic retries on transient failures, per-key cooldowns, and fallback model groups.",
  },
  {
    icon: Palette,
    tone: "text-pink-300",
    title: "Cost tracking",
    body: "Token usage and cost calculation for every call, per key, per model, per provider.",
  },
];

function FeatureGrid() {
  return (
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-2">
      {FEATURES.map((feature) => {
        const Icon = feature.icon;
        return (
          <div key={feature.title} className="bg-[var(--admin-surface)] p-4">
            <div className="flex items-center gap-2">
              <Icon className={`h-3.5 w-3.5 shrink-0 ${feature.tone}`} aria-hidden />
              <h3 className="text-[13px] font-semibold text-[var(--admin-text)]">{feature.title}</h3>
            </div>
            <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
              {feature.body}
            </p>
          </div>
        );
      })}
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────

export function DocsPage() {
  const ids = SECTIONS.map((s) => s.id);
  const active = useScrollSpy(ids);
  const progress = useScrollProgress();
  const handleClick = useCallback((id: string) => { scrollToId(id); }, []);

  // Deep links (/docs#streaming) land on their section once it exists.
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash && SECTIONS.some((s) => s.id === hash)) {
      const t = setTimeout(() => scrollToId(hash), 60);
      return () => clearTimeout(t);
    }
  }, []);

  const activeIndex = Math.max(1, SECTIONS.findIndex((s) => s.id === active) + 1);

  return (
    <div className="relative">
      <div className="docs-progress" aria-hidden>
        <div
          className="docs-progress-fill"
          style={{ "--docs-progress": progress.toFixed(4) } as CSSProperties}
        />
      </div>
      <BackToTop />

      {/* Masthead */}
      <header className="docs-hero mb-10">
        <div className="docs-hero-glow" aria-hidden />
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="admin-badge admin-badge-blue inline-flex items-center gap-1.5">
            <BookOpen size={11} /> Documentation
          </span>
          <span className="admin-badge admin-badge-gray inline-flex items-center gap-1.5">
            v0.1.0
          </span>
        </div>
        <h1 className="max-w-3xl text-3xl font-semibold tracking-[-0.025em] text-[var(--admin-text)] sm:text-4xl">
          Point any client at <span className="text-blue-300">wiwi</span>
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          wiwi is a single endpoint that speaks every inbound dialect and routes to every
          outbound provider. Bring your own client — OpenAI SDK, Anthropic SDK, Codex CLI,
          Claude Code, or plain <code style={{ fontFamily: MONO }}>curl</code> — retarget it
          at the gateway, and authenticate with a virtual key.
        </p>
        <div className="docs-hero-actions mt-5 flex flex-wrap items-center gap-2">
          <button
            onClick={() => handleClick("quickstart")}
            className="inline-flex h-11 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          >
            <Terminal size={14} /> Quickstart
            <ArrowRight size={13} />
          </button>
          <button
            onClick={() => handleClick("endpoints")}
            className="inline-flex h-11 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
          >
            <Network size={14} /> API reference
          </button>
        </div>

        <div className="docs-hero-meta mt-8">
          <div className="min-w-0">
            <p className="admin-label">Base URL</p>
            <code
              className="mt-1 block truncate text-[13px] text-[var(--admin-text)]"
              style={{ fontFamily: MONO }}
            >
              http://localhost:4000
            </code>
          </div>
          <div>
            <p className="admin-label">Facts</p>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[var(--admin-text-muted)]">
              <span>3 inbound dialects</span>
              <span>11 provider types</span>
              <span>1 canonical IR</span>
              <span>SSE streaming</span>
            </div>
          </div>
        </div>
      </header>

      {/* Mobile jump control */}
      <div className="docs-mobile-jump mb-6 lg:hidden">
        <label
          htmlFor="docs-section-select"
          className="mb-2 block text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--admin-text-muted)]"
        >
          On this page
        </label>
        <div className="relative">
          <select
            id="docs-section-select"
            value={active}
            onChange={(event) => handleClick(event.currentTarget.value)}
            className="docs-section-select h-11 w-full appearance-none rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 pr-10 text-[13px] text-[var(--admin-text)] shadow-sm focus:border-blue-400/50 focus:outline-none focus:ring-2 focus:ring-blue-400/20"
          >
            {SECTIONS.map((section) => (
              <option key={section.id} value={section.id}>
                {section.label}
              </option>
            ))}
          </select>
          <ChevronDown
            className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--admin-text-dim)]"
            aria-hidden
          />
        </div>
      </div>

      {/* Two-column: left rail + document */}
      <div className="docs-grid">
        <aside className="docs-sidebar">
          <nav className="space-y-0.5" aria-label="Documentation sections">
            <div className="mb-3 flex items-baseline justify-between pr-2">
              <span className="admin-label">On this page</span>
              <span className="font-mono text-[9px] tabular-nums text-[var(--admin-text-dim)]">
                {String(activeIndex).padStart(2, "0")}
                <span className="opacity-50"> / {String(SECTIONS.length).padStart(2, "0")}</span>
              </span>
            </div>
            {SECTIONS.map((s, i) => {
              const Icon = s.icon;
              const isActive = active === s.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => handleClick(s.id)}
                  aria-current={isActive ? "true" : undefined}
                  className={`docs-nav-item flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/40 ${
                    isActive ? "is-active" : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                  }`}
                >
                  <span className="font-mono text-[10px] opacity-50">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                  <span className="flex-1">{s.label}</span>
                </button>
              );
            })}
            <div className="mt-5 border-t border-[var(--admin-border)] pt-4">
              <Link
                to="/playground"
                className="group/play flex items-center gap-2 rounded-lg px-2 py-2 text-[12px] font-medium text-[var(--admin-text-muted)] transition-colors hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/40"
              >
                <Terminal size={13} className="text-blue-300/80" aria-hidden />
                <span className="flex-1">Open playground</span>
                <ArrowRight
                  size={12}
                  className="opacity-0 transition-opacity group-hover/play:opacity-100 group-focus-visible/play:opacity-100"
                  aria-hidden
                />
              </Link>
            </div>
          </nav>
        </aside>

        {/* Document */}
        <div className="docs-content">
          <section id="overview" className="docs-section scroll-mt-20">
            <SectionHeading
              id="overview"
              index={1}
              title="Overview"
              subtitle="How the gateway translates and routes requests"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Every request follows the same hub-and-spoke path: the wire codec for the inbound
              dialect decodes the request into a canonical internal representation (IR), the router
              selects a provider and key from the pool, and the adapter encodes the IR into the
              provider&apos;s native format. Responses flow back through the same path — the adapter
              decodes the provider response into IR deltas, and the wire encoder re-encodes them in
              the caller&apos;s original dialect.
            </p>
            <div className="mt-5 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4">
              <p className="admin-label mb-3">Translation path</p>
              <Pipeline />
            </div>
            <ProviderGrid />
          </section>

          <section id="quickstart" className="docs-section scroll-mt-20">
            <SectionHeading
              id="quickstart"
              index={2}
              title="Quickstart"
              subtitle="Running locally in under a minute"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Assuming wiwi is running on{" "}
              <code style={{ fontFamily: MONO }}>http://localhost:4000</code>, every path below
              is relative to <code style={{ fontFamily: MONO }}>/v1</code>. The Authorization
              header (or <code style={{ fontFamily: MONO }}>x-api-key</code> for Anthropic)
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

          <section id="authentication" className="docs-section scroll-mt-20">
            <SectionHeading
              id="authentication"
              index={3}
              title="Authentication"
              subtitle="Virtual keys — never provider keys"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Callers authenticate with a virtual key — never a provider key. Virtual keys are
              SHA-256-hashed at rest with constant-time comparison; per-key budgets, rate limits,
              and model allowlists are enforced before a request ever leaves the gateway.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <span className="inline-flex min-h-11 items-center rounded-full border border-white/[0.07] bg-white/[0.02] px-3 text-[12px] text-[var(--admin-text-muted)]">
                <code style={{ fontFamily: MONO }}>Authorization: Bearer sk-wiwi-…</code>
              </span>
              <span className="inline-flex min-h-11 items-center rounded-full border border-white/[0.07] bg-white/[0.02] px-3 text-[12px] text-[var(--admin-text-muted)]">
                <code style={{ fontFamily: MONO }}>x-api-key: sk-wiwi-…</code>
              </span>
              <span className="inline-flex min-h-11 items-center rounded-full border border-white/[0.07] bg-white/[0.02] px-3 text-[12px] text-[var(--admin-text-dim)]">
                OpenAI · Responses · Anthropic
              </span>
            </div>
            <div className="mt-5 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-3">
              {[
                {
                  icon: Shield,
                  tone: "text-blue-300",
                  title: "Hashed at rest",
                  body: "SHA-256 with constant-time compare — the plaintext is shown once at creation.",
                },
                {
                  icon: Wallet,
                  tone: "text-amber-300",
                  title: "Per-key budgets",
                  body: "Spend ceilings, model allowlists, and RPM/TPM throttles per key.",
                },
                {
                  icon: KeyRound,
                  tone: "text-violet-300",
                  title: "One key, all surfaces",
                  body: "The same key works across all three inbound dialects.",
                },
              ].map((item) => {
                const Icon = item.icon;
                return (
                  <div key={item.title} className="bg-[var(--admin-surface)] p-4">
                    <div className="flex items-center gap-2">
                      <Icon className={`h-3.5 w-3.5 shrink-0 ${item.tone}`} aria-hidden />
                      <h3 className="text-[13px] font-semibold text-[var(--admin-text)]">
                        {item.title}
                      </h3>
                    </div>
                    <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                      {item.body}
                    </p>
                  </div>
                );
              })}
            </div>
          </section>

          <section id="endpoints" className="docs-section scroll-mt-20">
            <SectionHeading
              id="endpoints"
              index={4}
              title="Endpoints"
              subtitle="The three inbound surfaces plus model listing"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Each surface maps onto the same canonical IR. Responses are re-encoded in the
              caller&apos;s dialect on the way back out — so a Claude Code session (Anthropic
              Messages) can be backed by GPT, and vice versa.
            </p>
            <div className="mt-4 divide-y divide-[var(--admin-border)]">
              {ENDPOINTS.map((ep) => (
                <EndpointRow
                  key={ep.path}
                  method={ep.method}
                  path={ep.path}
                  desc={ep.desc}
                  auth={ep.auth}
                  clients={ep.clients}
                  example={ep.example}
                />
              ))}
            </div>
          </section>

          <section id="cross-provider" className="docs-section scroll-mt-20">
            <SectionHeading
              id="cross-provider"
              index={5}
              title="Cross-provider routing"
              subtitle="Decouple the caller's dialect from the upstream provider"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Because every direction goes dialect → IR → provider, the caller&apos;s dialect is
              decoupled from the upstream provider. Clients request a{" "}
              <code style={{ fontFamily: MONO }}>model_name</code>; wiwi routes to the
              configured provider account and native model id. Key pools, retries, cooldowns, and
              fallbacks are wired in the same config.
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

          <section id="streaming" className="docs-section scroll-mt-20">
            <SectionHeading
              id="streaming"
              index={6}
              title="Streaming"
              subtitle="Server-sent events across all three dialects"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              Streaming is supported across all three surfaces. Set{" "}
              <code style={{ fontFamily: MONO }}>&quot;stream&quot;: true</code> in the request
              body. The gateway decodes the provider&apos;s stream into{" "}
              <code style={{ fontFamily: MONO }}>IRStreamDelta</code> events and re-encodes them
              as SSE in the caller&apos;s dialect —{" "}
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
              The streaming contract guarantees: exactly one{" "}
              <code style={{ fontFamily: MONO }}>StreamStart</code>, then{" "}
              <code style={{ fontFamily: MONO }}>ToolCallOpen → ArgsDelta* → Close</code> per
              index, then <code style={{ fontFamily: MONO }}>UsageFinal</code>, then{" "}
              <code style={{ fontFamily: MONO }}>Finish</code>, then{" "}
              <code style={{ fontFamily: MONO }}>StreamEnd</code> or{" "}
              <code style={{ fontFamily: MONO }}>StreamError</code>.
            </p>
          </section>

          <section id="config" className="docs-section scroll-mt-20">
            <SectionHeading
              id="config"
              index={7}
              title="Configuration"
              subtitle="A single wiwi.yaml — LiteLLM-shaped"
            />
            <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
              The entire gateway is configured through one YAML file. Providers hold named
              accounts with pools of keyed entries;{" "}
              <code style={{ fontFamily: MONO }}>model_list</code> maps client-requested names
              to provider accounts; router settings control strategy, retries, cooldowns, and
              fallbacks. Any string value may reference{" "}
              <code style={{ fontFamily: MONO }}>os.environ/NAME</code> for secret
              interpolation.
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

          <section id="features" className="docs-section scroll-mt-20">
            <SectionHeading
              id="features"
              index={8}
              title="Features"
              subtitle="Built-in for every deployment — no plugins"
            />
            <FeatureGrid />
          </section>

          {/* CTA */}
          <div className="docs-cta mt-12 p-8 text-center">
            <h2 className="text-xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
              Ready to try it?
            </h2>
            <p className="mx-auto mt-2 max-w-md text-[14px] text-[var(--admin-text-muted)]">
              Spin up a gateway in a minute, or jump straight into the playground.
            </p>
            <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
              <Link
                to="/playground"
                className="inline-flex h-11 items-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 text-[13px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
              >
                <Terminal size={14} /> Open playground
              </Link>
              <Link
                to="/signup"
                className="inline-flex h-11 items-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-[13px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
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
