// Docs — public API documentation for the gateway. Sticky scroll-tracking
// sidebar, tabbed code examples with copy buttons, endpoint reference cards
// with HTTP method badges, and feature highlights. Matches the dark design
// system shared with the admin console.

import {
  AnimatedBeam,
  AnthropicIcon,
  GeminiIcon,
  OpenAIIcon,
  OpenRouterIcon,
} from "@/components/AnimatedBeam";

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

// Small inline copy button for endpoint paths (visible, not hover-revealed).
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
      className="rounded-md border border-white/[0.06] bg-white/[0.02] p-1 text-[var(--admin-text-dim)] transition-all hover:border-white/[0.12] hover:text-blue-300"
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

// ── code block ─────────────────────────────────────────────────────────────

function CodeBlock(props: { code: string; label?: string; lang?: Lang }) {
  const lang = props.lang ?? (props.label ? langFromLabel(props.label) : "bash");
  return (
    <div className="docs-codeblock group relative overflow-hidden rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
      <div className="docs-codeblock-glow" aria-hidden />
      {props.label && (
        <div className="relative z-10 flex items-center justify-between border-b border-[var(--admin-border)] bg-white/[0.015] px-3.5 py-1.5">
          <div className="flex items-center gap-1.5 pl-0.5">
            <span className="h-2 w-2 rounded-full bg-[#ff5f57]/70" aria-hidden />
            <span className="h-2 w-2 rounded-full bg-[#febc2e]/70" aria-hidden />
            <span className="h-2 w-2 rounded-full bg-[#28c840]/70" aria-hidden />
            <span className="admin-label ml-1.5 text-[10px]">{props.label}</span>
          </div>
          <CopyBtn text={props.code} />
        </div>
      )}
      {!props.label && (
        <div className="absolute right-0 top-0 z-10 px-2.5 py-2 opacity-0 transition-opacity group-hover:opacity-100">
          <CopyBtn text={props.code} />
        </div>
      )}
      <pre className="relative z-10 overflow-x-auto px-3.5 py-3 text-[12px] leading-relaxed" style={{ fontFamily: MONO }}>
        <code className="text-[var(--admin-text-muted)]">{highlight(props.code, lang)}</code>
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

const METHOD_STYLES: Record<Method, { bg: string; text: string; accent: string }> = {
  POST: { bg: "bg-amber-500/10", text: "text-amber-400", accent: "border-l-amber-500/40" },
  GET: { bg: "bg-emerald-500/10", text: "text-emerald-400", accent: "border-l-emerald-500/40" },
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
    <div className={`rounded-[12px] border border-[var(--admin-border)] border-l-2 ${ms.accent} bg-[var(--admin-surface)] p-4 transition-all hover:-translate-y-px hover:border-[var(--admin-border-hover)] hover:shadow-lg hover:shadow-black/20`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`docs-method-badge flex h-5 min-w-[48px] items-center justify-center rounded-md px-2 text-[10px] font-bold tracking-wider ${ms.bg} ${ms.text}`}>
          {method}
        </span>
        <code className="text-[13px] font-semibold text-[var(--admin-text)]" style={{ fontFamily: MONO }}>
          {path}
        </code>
        <PathCopyBtn text={path} />
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

const FEATURE_TONES: Record<string, string> = {
  blue: "text-blue-300 from-blue-500/15 to-blue-500/[0.05]",
  violet: "text-violet-300 from-violet-500/15 to-violet-500/[0.05]",
  amber: "text-amber-300 from-amber-500/15 to-amber-500/[0.05]",
  emerald: "text-emerald-300 from-emerald-500/15 to-emerald-500/[0.05]",
  cyan: "text-cyan-300 from-cyan-500/15 to-cyan-500/[0.05]",
  pink: "text-pink-300 from-pink-500/15 to-pink-500/[0.05]",
};

function FeatureCard(props: { icon: LucideIcon; title: string; body: string; tone?: keyof typeof FEATURE_TONES }) {
  const Icon = props.icon;
  const tone = FEATURE_TONES[props.tone ?? "blue"].split(" ");
  return (
    <div
      className="admin-card docs-spotlight p-4 transition-all hover:-translate-y-px hover:border-[var(--admin-border-hover)] hover:shadow-lg hover:shadow-black/20"
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
        e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
      }}
    >
      <div className="relative z-10 flex items-center gap-2.5">
        <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br ${tone[1]} ${tone[2]} ring-1 ring-white/[0.06]`}>
          <Icon className={`h-3.5 w-3.5 ${tone[0]}`} />
        </span>
        <h4 className="text-[13px] font-semibold text-[var(--admin-text)]">{props.title}</h4>
      </div>
      <p className="relative z-10 mt-2 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">{props.body}</p>
    </div>
  );
}

// ── heading anchor ─────────────────────────────────────────────────────────

// Per-section accent tones for the heading icon chip (keyed by section index).
const HEADING_TONES: Record<number, { chip: string; icon: string }> = {
  1: { chip: "from-blue-500/20 to-blue-500/[0.04]", icon: "text-blue-300" },
  2: { chip: "from-emerald-500/20 to-emerald-500/[0.04]", icon: "text-emerald-300" },
  3: { chip: "from-violet-500/20 to-violet-500/[0.04]", icon: "text-violet-300" },
  4: { chip: "from-amber-500/20 to-amber-500/[0.04]", icon: "text-amber-300" },
  5: { chip: "from-cyan-500/20 to-cyan-500/[0.04]", icon: "text-cyan-300" },
  6: { chip: "from-fuchsia-500/20 to-fuchsia-500/[0.04]", icon: "text-fuchsia-300" },
  7: { chip: "from-sky-500/20 to-sky-500/[0.04]", icon: "text-sky-300" },
  8: { chip: "from-pink-500/20 to-pink-500/[0.04]", icon: "text-pink-300" },
};

function SectionHeading(props: { icon: LucideIcon; title: string; subtitle?: string; index?: number }) {
  const Icon = props.icon;
  const tone = HEADING_TONES[props.index ?? 0] ?? {
    chip: "from-blue-500/20 to-blue-500/[0.04]",
    icon: "text-blue-300",
  };
  return (
    <div className="mb-4 flex items-center gap-3">
      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br ${tone.chip} ring-1 ring-white/[0.06]`}>
        <Icon className={`h-4 w-4 ${tone.icon}`} />
      </span>
      <div>
        <h2 className="text-[18px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          {props.index != null && (
            <span className="mr-2 font-mono text-[12px] font-normal text-[var(--admin-text-dim)]">
              {String(props.index).padStart(2, "0")}
            </span>
          )}
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

// ── overview flow diagram ──────────────────────────────────────────────────


const INBOUND_NODES = [
  { name: "chat/completions", note: "OpenAI SDK" },
  { name: "responses", note: "Codex CLI" },
  { name: "messages", note: "Claude Code" },
];

const OUTBOUND_NODES = [
  { name: "openai", Icon: OpenAIIcon },
  { name: "anthropic", Icon: AnthropicIcon },
  { name: "gemini", Icon: GeminiIcon },
  { name: "openrouter", Icon: OpenRouterIcon },
].map((n) => ({ ...n, label: n.name }));

function FlowNode({
  ref,
  children,
  className = "",
}: {
  ref: React.RefObject<HTMLDivElement | null>;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      ref={ref}
      className={`group relative z-10 flex h-12 w-12 items-center justify-center rounded-full border-2 border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-lg shadow-black/30 backdrop-blur-sm transition-all duration-200 hover:scale-110 hover:border-white/[0.18] ${className}`}
    >
      {children}
    </div>
  );
}

function DocsFlowDiagram() {
  const containerRef = useRef<HTMLDivElement>(null);
  const centerRef = useRef<HTMLDivElement>(null);
  const inboundRefs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  const outboundRefs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];

  return (
    <Card className="mt-5 overflow-hidden p-0">
      <div ref={containerRef} className="relative h-[300px] w-full sm:h-[340px]">
        {/* radial glow behind center */}
        <div className="pointer-events-none absolute left-1/2 top-1/2 h-64 w-64 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,rgba(139,92,246,0.12)_0%,rgba(59,130,246,0.06)_40%,transparent_70%)] blur-2xl" aria-hidden />

        {/* Left: inbound dialects */}
        <div className="absolute left-4 top-1/2 flex -translate-y-1/2 flex-col gap-6 z-10 sm:left-6">
          {INBOUND_NODES.map((d, i) => (
            <div key={d.name} className="flex items-center gap-2.5">
              <FlowNode ref={inboundRefs[i]}>
                <code className="text-[9px] font-bold text-blue-300" style={{ fontFamily: MONO }}>
                  {d.name.slice(0, 1).toUpperCase()}
                </code>
              </FlowNode>
              <div className="hidden sm:block">
                <div className="text-[11px] font-mono text-blue-300">{d.name}</div>
                <div className="text-[10px] text-[var(--admin-text-dim)]">{d.note}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Center: wiwi IR */}
        <div className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2">
          <FlowNode ref={centerRef} className="!h-16 !w-16 wiwi-gateway-node">
            <img src="/wiwi-logo.png" alt="wiwi" className="h-9 w-9 rounded-full object-cover" />
          </FlowNode>
          <div className="mt-2.5 text-center">
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--admin-text-dim)]">wiwi IR</span>
          </div>
        </div>

        {/* Right: outbound providers */}
        <div className="absolute right-4 top-1/2 flex -translate-y-1/2 flex-col gap-5 z-10 sm:right-6">
          {OUTBOUND_NODES.map((node, i) => (
            <div key={node.name} className="flex items-center gap-2.5">
              <div className="hidden text-right sm:block">
                <div className="text-[11px] font-mono text-violet-300">{node.name}</div>
                <div className="text-[10px] text-[var(--admin-text-dim)]">provider</div>
              </div>
              <FlowNode ref={outboundRefs[i]} className="hover:!border-blue-500/40">
                <node.Icon className="h-5 w-5 object-contain" />
              </FlowNode>
            </div>
          ))}
        </div>

        {/* Animated beams: inbound → center */}
        {inboundRefs.map((ref, i) => {
          const hues = [
            ["#3b82f6", "#8b5cf6"], // chat → blue→violet
            ["#06b6d4", "#8b5cf6"], // responses → cyan→violet
            ["#a855f7", "#ec4899"], // messages → purple→pink
          ];
          const [g0, g1] = hues[i] ?? ["#3b82f6", "#8b5cf6"];
          return (
            <AnimatedBeam
              key={`in-${i}`}
              containerRef={containerRef}
              fromRef={ref}
              toRef={centerRef}
              curvature={(i - 1) * 18}
              delay={i * 0.3}
              pathWidth={2.5}
              gradientStart={g0}
              gradientStop={g1}
              duration={3 + i * 0.3}
            />
          );
        })}

        {/* Animated beams: center → outbound */}
        {outboundRefs.map((ref, i) => {
          const hues = [
            ["#8b5cf6", "#ec4899"], // openai → violet→pink
            ["#f59e0b", "#ef4444"], // anthropic → amber→red
            ["#22c55e", "#3b82f6"], // gemini → green→blue
            ["#06b6d4", "#8b5cf6"], // openrouter → cyan→violet
          ];
          const [g0, g1] = hues[i] ?? ["#8b5cf6", "#ec4899"];
          return (
            <AnimatedBeam
              key={`out-${i}`}
              containerRef={containerRef}
              fromRef={centerRef}
              toRef={ref}
              curvature={(i - 1.5) * 20}
              delay={0.5 + i * 0.3}
              pathWidth={2.5}
              gradientStart={g0}
              gradientStop={g1}
              duration={3 + i * 0.3}
            />
          );
        })}
      </div>

      {/* caption */}
      <div className="border-t border-[var(--admin-border)] px-5 py-3">
        <p className="text-center text-[11px] leading-relaxed text-[var(--admin-text-dim)]">
          Inbound dialect → wiwi canonical IR → outbound provider format · responses flow back through the same path
        </p>
      </div>
    </Card>
  );
}

// ── hero terminal ──────────────────────────────────────────────────────────

// Fake "live gateway trace" terminal — a streaming curl against the gateway
// with a routing line, SSE chunks, and a usage footer. Purely decorative.
function HeroTerminal() {
  const req = `curl http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer sk-wiwi-…" \\
  -d '{"model":"gpt-4o","stream":true,
       "messages":[{"role":"user","content":"hi"}]}'`;
  return (
    <div className="docs-terminal relative hidden overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-2xl shadow-black/40 lg:block">
      <div className="docs-terminal-glow" aria-hidden />
      <div className="relative z-10 flex items-center gap-2 border-b border-[var(--admin-border)] bg-white/[0.02] px-3.5 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" aria-hidden />
        <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" aria-hidden />
        <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" aria-hidden />
        <span className="ml-2 text-[10px] tracking-wide text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
          wiwi gateway
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] font-medium text-emerald-300">
          <span className="docs-pulse-dot" aria-hidden /> live
        </span>
      </div>
      <pre className="relative z-10 min-h-[252px] overflow-x-auto px-4 py-3.5 text-[10.5px] leading-[1.75]" style={{ fontFamily: MONO }}>
        <code>
          {highlight(req, "bash")}
          {"\n"}
          <span className="text-violet-300">→ routed</span>
          <span className="text-[var(--admin-text-dim)]"> · openai-prod · </span>
          <span className="text-sky-300">gpt-4o</span>
          <span className="text-[var(--admin-text-dim)]"> · key </span>
          <span className="text-amber-300">pool-1</span>
          <span className="text-[var(--admin-text-dim)]"> (w3)</span>
          {"\n\n"}
          <span className="text-[var(--admin-text-dim)]">data: </span>
          <span className="text-[var(--admin-text-muted)]">{'{"delta":{"content":"'}</span>
          <span className="text-emerald-300">Hel</span>
          <span className="text-[var(--admin-text-muted)]">{'"}'}</span>
          {"\n"}
          <span className="text-[var(--admin-text-dim)]">data: </span>
          <span className="text-[var(--admin-text-muted)]">{'{"delta":{"content":"'}</span>
          <span className="text-emerald-300">lo, wiwi.</span>
          <span className="text-[var(--admin-text-muted)]">{'"}'}</span>
          {"\n"}
          <span className="text-[var(--admin-text-dim)]">data: </span>
          <span className="text-[var(--admin-text-muted)]">[DONE]</span>
          {"\n\n"}
          <span className="text-emerald-400">✓ 200 OK</span>
          <span className="text-[var(--admin-text-dim)]"> · 87 tok · 214 tok/s · </span>
          <span className="text-amber-300">$0.00021</span>
          <span className="docs-caret" aria-hidden />
        </code>
      </pre>
    </div>
  );
}

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
        <div className="docs-hero-aurora" aria-hidden />
        <div className="relative z-10 grid items-center gap-10 lg:grid-cols-[1.05fr_0.95fr]">
        <div>
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
            <span className="docs-gradient-text bg-gradient-to-r from-blue-400 via-fuchsia-400 to-blue-400 bg-clip-text text-transparent">
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
          <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[11px] text-[var(--admin-text-dim)]">
            {["3 inbound dialects", "4+ outbound providers", "1 canonical IR", "SSE streaming"].map(
              (s, i) => (
                <span key={s} className="inline-flex items-center gap-5">
                  {i > 0 && <span className="h-1 w-1 rounded-full bg-white/20" aria-hidden />}
                  {s}
                </span>
              ),
            )}
          </div>
        </div>
        <HeroTerminal />
        </div>
      </div>

      {/* Two-column: sticky sidebar + content */}
      <div className="docs-grid">
        {/* Sidebar */}
        <aside className="docs-sidebar">
          <nav className="sticky top-[80px] space-y-0.5">
            <span className="admin-label mb-2 block px-3">On this page</span>
            {SECTIONS.map((s, i) => {
              const Icon = s.icon;
              const isActive = active === s.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => handleClick(s.id)}
                  className={`docs-nav-item flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12px] transition-colors ${
                    isActive
                      ? "is-active bg-blue-500/[0.06] font-medium text-blue-200"
                      : "text-[var(--admin-text-dim)] hover:bg-white/[0.02] hover:text-[var(--admin-text-muted)]"
                  }`}
                >
                  <span className="font-mono text-[10px] opacity-50">{String(i + 1).padStart(2, "0")}</span>
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
              index={1}
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
<DocsFlowDiagram />
          </section>

          {/* quickstart */}
          <section id="quickstart" className="docs-section scroll-mt-20">
            <SectionHeading
              index={2}
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
              index={3}
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
              index={4}
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
              index={5}
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
              index={6}
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
              index={7}
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
              index={8}
              icon={Layers}
              title="Features"
              subtitle="Built-in for every deployment — no plugins"
            />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FeatureCard
                icon={Layers}
                tone="blue"
                title="Three inbound dialects"
                body="OpenAI Chat, OpenAI Responses (Codex CLI), and Anthropic Messages all speak the same canonical IR."
              />
              <FeatureCard
                icon={KeyRound}
                tone="violet"
                title="Virtual keys"
                body="Per-client credentials with model allowlists, expiry, and spend caps. Callers never see provider keys."
              />
              <FeatureCard
                icon={Wallet}
                tone="amber"
                title="Budgets & rate limits"
                body="Per-key spend ceilings and RPM/TPM throttles keep noisy tenants from burning your quota."
              />
              <FeatureCard
                icon={Boxes}
                tone="emerald"
                title="Key pools"
                body="Pool multiple keys per provider with smooth weighted round-robin. Exhausted keys cool down automatically."
              />
              <FeatureCard
                icon={RefreshCw}
                tone="cyan"
                title="Retries & fallbacks"
                body="Automatic retries on transient failures, per-key cooldowns, and fallback model groups."
              />
              <FeatureCard
                icon={Palette}
                tone="pink"
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
