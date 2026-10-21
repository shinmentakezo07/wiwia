// Docs — public API documentation for the gateway.
//
// A reference manual with an editorial spine: the masthead states the
// product's core idea (many dialects → one canonical IR → many providers) as
// a live animated diagram, then the body is a two-column reference — a sticky
// glass rail that tracks the reader, hairline-separated sections (no floating
// cards), tabbed code samples with copy buttons, and a searchable endpoint
// reference. Matches the dark design system shared with the admin console.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUp,
  BookOpen,
  Boxes,
  Check,
  ChevronDown,
  Coins,
  Copy,
  Database,
  Hash,
  HeartPulse,
  KeyRound,
  Layers,
  Network,
  RefreshCw,
  ScrollText,
  Search,
  Settings2,
  Shield,
  Terminal,
  Wallet,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AnimatedBeam } from "@/components/AnimatedBeam";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";
import { highlight, langFromLabel, LANG_DOT, MONO, type Lang } from "@/lib/code-highlight";

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

// Hoisted so the scroll-spy effect does not re-observe on every render.
const SECTION_IDS = SECTIONS.map((s) => s.id);

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

// Reports true the first time an element enters the viewport, then stops.
// Used to stagger the feature tiles once the reader actually reaches them —
// content is never gated behind the observer (no IO means "reveal now").
function useInViewOnce(ref: RefObject<HTMLElement | null>) {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (seen || !el) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { threshold: 0 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, seen]);
  return seen;
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
      className="docs-copybtn"
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
      className="docs-pathcopy"
      aria-label={`Copy ${props.text}`}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  );
}

// ── code block ─────────────────────────────────────────────────────────────

// Splits code into physical lines so the gutter can show line numbers. Only
// rendered for multi-line snippets — a one-liner's gutter adds noise, not
// reference value.
function codeLines(code: string): string[] {
  return code.split("\n");
}

function CodeBlock(props: { code: string; label?: string; lang?: Lang }) {
  const lang = props.lang ?? (props.label ? langFromLabel(props.label) : "bash");
  const lines = codeLines(props.code);
  const showGutter = lines.length > 1;
  return (
    <div className="docs-codeblock group">
      <div className="docs-codehead">
        <span className="flex min-w-0 items-center gap-2">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${LANG_DOT[lang]}`}
            aria-hidden
          />
          <span className="admin-label truncate text-[10px]">{props.label ?? lang}</span>
        </span>
        <CopyBtn text={props.code} />
      </div>
      <div className="docs-codebody">
        {showGutter && (
          <div className="docs-codegutter" aria-hidden>
            {lines.map((_, i) => (
              <span key={i}>{i + 1}</span>
            ))}
          </div>
        )}
        <pre
          tabIndex={0}
          role="group"
          aria-label={props.label ? `${props.label} code example` : "Code example"}
          className="docs-pre"
        >
          <code className="text-[var(--admin-text-muted)]">{highlight(props.code, lang)}</code>
        </pre>
      </div>
    </div>
  );
}

// ── tabbed code block ──────────────────────────────────────────────────────

function TabbedCode(props: { tabs: { label: string; code: string }[] }) {
  const [idx, setIdx] = useState(0);
  const tab = props.tabs[idx];
  return (
    <div className="space-y-2.5">
      <div className="docs-tabs" role="tablist" aria-label="Code language">
        {props.tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            role="tab"
            aria-selected={i === idx}
            onClick={() => setIdx(i)}
            className={`docs-tab ${i === idx ? "is-active" : ""}`}
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
        <code
          className="docs-endpoint-path text-[13px] font-semibold text-[var(--admin-text)]"
          style={{ fontFamily: MONO }}
        >
          {path}
        </code>
        <PathCopyBtn text={path} />
        {clients && (
          <div className="docs-endpoint-clients flex flex-wrap items-center gap-1.5">
            {clients.map((c) => (
              <span key={c} className="docs-client-chip">
                {c}
              </span>
            ))}
          </div>
        )}
      </div>
      <p className="mt-2 max-w-[74ch] text-[12.5px] leading-relaxed text-[var(--admin-text-muted)]">
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

// How wiwi speaks to each provider type upstream. The grouping is read off the
// adapters rather than invented: most types share one Chat Completions codec
// (`registry._OPENAI_WIRE_TYPES` plus the OpenAI-adapter specializations),
// `anthropic` and `gemini` speak their vendor's own protocol, and the three
// adapters that declare `force_stream = True` have no non-streaming upstream
// mode. A tile's dot and wash take its family's hue; the legend above the grid
// spells the same mapping out in text, so hue is never the only carrier.
type WireFamily = "chat" | "native" | "sse";

const WIRE_FAMILIES: { id: WireFamily; label: string; note: string; hue: number }[] = [
  {
    id: "chat",
    label: "OpenAI Chat wire",
    note: "The upstream speaks Chat Completions. Per-vendor quirks live in the adapter, not in core.",
    hue: 213,
  },
  {
    id: "native",
    label: "Native protocol",
    note: "The upstream speaks its own protocol — Anthropic Messages, Gemini generateContent.",
    hue: 156,
  },
  {
    id: "sse",
    label: "SSE-only upstream",
    note: "No non-streaming upstream mode; the gateway reassembles a reply for non-streaming callers.",
    hue: 27,
  },
];

// Derived, so the tile accents can never drift from the legend above them.
const FAMILY_HUE = Object.fromEntries(
  WIRE_FAMILIES.map((f) => [f.id, f.hue]),
) as Record<WireFamily, number>;
const FAMILY_LABEL = Object.fromEntries(
  WIRE_FAMILIES.map((f) => [f.id, f.label]),
) as Record<WireFamily, string>;

// `id` is the value a reader puts in `provider:` in wiwi.yaml — it is the
// provider type from `wiwi/config.py:PROVIDER_TYPES`, listed in the same order.
// `src` points at the canonical provider badge in `web/public/logos/` — the
// same 120×120 tiles the README embeds, so the docs and the repo front page
// show the same marks.
const PROVIDERS: {
  label: string;
  id: string;
  src: string;
  family: WireFamily;
}[] = [
  { label: "OpenAI", id: "openai", src: "/logos/openai.svg", family: "chat" },
  {
    label: "OpenAI-compatible",
    id: "openai-compatible",
    src: "/logos/openai-compatible.svg",
    family: "chat",
  },
  { label: "GMI Cloud", id: "gmicloud", src: "/logos/gmicloud.svg", family: "chat" },
  { label: "B.AI", id: "bai", src: "/logos/bai.svg", family: "chat" },
  { label: "OpenRouter", id: "openrouter", src: "/logos/openrouter.svg", family: "chat" },
  { label: "NVIDIA NIM", id: "nvidia-nim", src: "/logos/nvidia-nim.svg", family: "chat" },
  { label: "Hermes", id: "hermes", src: "/logos/hermes.svg", family: "chat" },
  { label: "Anthropic", id: "anthropic", src: "/logos/anthropic.svg", family: "native" },
  { label: "Gemini", id: "gemini", src: "/logos/gemini.svg", family: "native" },
  { label: "Cline", id: "cline", src: "/logos/cline.svg", family: "sse" },
  { label: "WorkBuddy", id: "workbuddy", src: "/logos/workbuddy.svg", family: "sse" },
  { label: "OpenCode Zen", id: "opencode", src: "/logos/opencode.svg", family: "sse" },
];

function ProviderGrid() {
  const gridRef = useRef<HTMLDivElement>(null);
  const revealed = useInViewOnce(gridRef);

  return (
    <div className="mt-6">
      <div className="docs-provider-head">
        <div className="min-w-0">
          <h3 className="admin-label">Provider ecosystem</h3>
          <p className="docs-provider-sub">
            Where each type lands on the wire — grouped by what the upstream actually speaks.
          </p>
        </div>
        <span className="docs-count-chip">
          <b>{PROVIDERS.length}</b> provider types
          <span className="docs-count-sep" aria-hidden />
          <b>{WIRE_FAMILIES.length}</b> wire families
        </span>
      </div>

      {/* Legend for the dots below. Every tile also names its family to screen
          readers, so the mapping survives both greyscale and assistive tech. */}
      <ul className="docs-provider-legend">
        {WIRE_FAMILIES.map((family) => (
          <li
            key={family.id}
            className="docs-provider-legend-item"
            style={{ "--f-hue": family.hue } as CSSProperties}
          >
            <span className="docs-provider-legend-top">
              <span className="docs-provider-dot" aria-hidden />
              <span className="docs-provider-legend-label">{family.label}</span>
              <span className="docs-provider-legend-count">
                {PROVIDERS.filter((p) => p.family === family.id).length}
              </span>
            </span>
            <span className="docs-provider-legend-note">{family.note}</span>
          </li>
        ))}
      </ul>

      <div
        ref={gridRef}
        className={`docs-provider-grid${revealed ? " is-revealed" : ""}`}
        role="list"
      >
        {PROVIDERS.map((provider, i) => {
          return (
            <div
              key={provider.id}
              role="listitem"
              className="docs-provider-item"
              style={
                { "--f-hue": FAMILY_HUE[provider.family], "--f-i": i } as CSSProperties
              }
              onPointerMove={trackSpotlight}
            >
              <span className="docs-spotlight" aria-hidden />
              <span className="docs-provider-logo">
                <img src={provider.src} alt="" className="h-full w-full object-contain" />
              </span>
              <span className="docs-provider-name">{provider.label}</span>
              <span className="docs-provider-id">
                <span className="docs-provider-dot" aria-hidden />
                <span className="docs-provider-id-text" style={{ fontFamily: MONO }}>
                  {provider.id}
                </span>
                <span className="sr-only"> — {FAMILY_LABEL[provider.family]}</span>
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
    // `role="img"` makes the flow strip's label permitted: a bare aria-label on
    // a div is prohibited, and role="list"/"listitem" would then demand the
    // connector spans be list items too, which would break the reading order.
    <div
      className="docs-pipeline"
      role="img"
      aria-label="dialect to IR to provider pipeline"
    >
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

// ── how it works: the live translation diagram ────────────────────────────

const HERO_DIALECTS = [
  { chip: "chat/completions", tone: "text-blue-300" },
  { chip: "responses", tone: "text-cyan-300" },
  { chip: "messages", tone: "text-fuchsia-300" },
];

const HERO_PROVIDERS = [
  { label: "OpenAI", src: "/logos/openai.svg" },
  { label: "Anthropic", src: "/logos/anthropic.svg" },
  { label: "Gemini", src: "/logos/gemini.svg" },
  { label: "OpenRouter", src: "/logos/openrouter.svg" },
];

// The provider pills under the masthead, in the landing page's style: a
// rounded chip carrying the brand mark and the name. The marks are the same
// canonical badges the provider grid uses.
const HERO_PROVIDER_PILLS: { label: string; src: string }[] = [
  { label: "OpenAI", src: "/logos/openai.svg" },
  { label: "Anthropic", src: "/logos/anthropic.svg" },
  { label: "Gemini", src: "/logos/gemini.svg" },
  { label: "OpenRouter", src: "/logos/openrouter.svg" },
  { label: "NVIDIA NIM", src: "/logos/nvidia-nim.svg" },
  { label: "OpenCode", src: "/logos/opencode.svg" },
];

// The live diagram: three inbound dialects, the wiwi IR hub, four upstream
// providers, and a beam travelling along every edge. It is the product's
// central claim drawn as a picture — and, like the landing page's own graph
// section, it stands free on the page rather than sitting inside a card.
function TranslationCore() {
  const containerRef = useRef<HTMLDivElement>(null);
  const d0 = useRef<HTMLDivElement>(null);
  const d1 = useRef<HTMLDivElement>(null);
  const d2 = useRef<HTMLDivElement>(null);
  const hub = useRef<HTMLDivElement>(null);
  const p0 = useRef<HTMLDivElement>(null);
  const p1 = useRef<HTMLDivElement>(null);
  const p2 = useRef<HTMLDivElement>(null);
  const p3 = useRef<HTMLDivElement>(null);

  const dialectRefs = [d0, d1, d2];
  const providerRefs = [p0, p1, p2, p3];
  const providerHues = [
    ["#3b82f6", "#8b5cf6"],
    ["#f59e0b", "#ef4444"],
    ["#22c55e", "#3b82f6"],
    ["#06b6d4", "#8b5cf6"],
  ];

  return (
    <div className="docs-core">
      {/* A vertical flow — dialects in, hub in the middle, providers out. The
          panel is a narrow column, so top-to-bottom is the only arrangement
          that gives every node room and still reads as one diagram. */}
      {/* A vertical flow — dialects in, hub in the middle, providers out. The
          panel is a narrow column, so top-to-bottom is the only arrangement
          that gives every node room and still reads as one diagram. */}
      <div className="docs-core-body" ref={containerRef}>
        {/* Inbound dialects */}
        <div className="docs-core-row docs-core-row-in">
          {HERO_DIALECTS.map((d, i) => (
            <div key={d.chip} ref={dialectRefs[i]} className="docs-core-dialect">
              <span className={`docs-core-dialect-text ${d.tone}`} style={{ fontFamily: MONO }}>
                {d.chip}
              </span>
            </div>
          ))}
        </div>

        {/* The hub */}
        <div className="docs-core-hub-wrap">
          <div ref={hub} className="docs-core-hub">
            <span className="docs-core-hub-ring" aria-hidden />
            <img src="/wiwi-logo.png" alt="" className="docs-core-hub-logo" />
          </div>
          <span className="docs-core-hub-label">wiwi IR</span>
        </div>

        {/* Upstream providers */}
        <div className="docs-core-row docs-core-row-out">
          {HERO_PROVIDERS.map((p, i) => (
            <div
              key={p.label}
              ref={providerRefs[i]}
              className="docs-core-provider"
              title={p.label}
            >
              <img src={p.src} alt={p.label} className="h-full w-full object-contain" />
            </div>
          ))}
        </div>

        {/* Beams — inbound to hub, hub to outbound. A negative curvature puts
            the control point below the start, so each beam leaves its node
            travelling down the panel instead of cutting sideways through
            the row it starts in. */}
        {dialectRefs.map((ref, i) => (
          <AnimatedBeam
            key={`in-${i}`}
            containerRef={containerRef}
            fromRef={ref}
            toRef={hub}
            curvature={-30}
            pathWidth={2}
            pathOpacity={0.3}
            gradientStart="#60a5fa"
            gradientStop="#a78bfa"
            duration={3.2}
            delay={i * 0.35}
          />
        ))}
        {providerRefs.map((ref, i) => {
          const [g0, g1] = providerHues[i] ?? ["#8b5cf6", "#ec4899"];
          return (
            <AnimatedBeam
              key={`out-${i}`}
              containerRef={containerRef}
              fromRef={hub}
              toRef={ref}
              curvature={-26}
              pathWidth={2}
              pathOpacity={0.3}
              gradientStart={g0}
              gradientStop={g1}
              duration={3.6}
              delay={0.6 + i * 0.45}
            />
          );
        })}
      </div>

      <div className="docs-core-foot">
        <span className="docs-core-foot-item">
          <span className="docs-core-foot-dot" style={{ background: "#60a5fa" }} aria-hidden />
          decode
        </span>
        <span className="docs-core-foot-item">
          <span className="docs-core-foot-dot" style={{ background: "#a78bfa" }} aria-hidden />
          canonical IR
        </span>
        <span className="docs-core-foot-item">
          <span className="docs-core-foot-dot" style={{ background: "#34d399" }} aria-hidden />
          re-encode
        </span>
      </div>
    </div>
  );
}

// ── section heading ─────────────────────────────────────────────────────────

// Quiet "jump to the next section" control, rendered under the heading. A
// reference manual is read in order, and a keyboard-only reader should be able
// to advance without hunting through the rail.
function SectionNext(props: { nextId: string; nextLabel: string; onGo: (id: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => props.onGo(props.nextId)}
      className="docs-next group/next mt-3 inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-3.5 py-2 text-[11.5px] font-medium text-[var(--admin-text-muted)] transition-colors hover:border-blue-400/30 hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
    >
      <span>Next: {props.nextLabel}</span>
      <ArrowRight
        size={12}
        className="transition-transform group-hover/next:translate-x-0.5 group-focus-visible/next:translate-x-0.5"
        aria-hidden
      />
    </button>
  );
}

function SectionHeading(props: {
  id: string;
  title: string;
  subtitle?: string;
  index: number;
  aside?: ReactNode;
}) {
  return (
    <div className="docs-heading group mb-5">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <span className="docs-eyebrow">
          <span className="docs-eyebrow-num">{String(props.index).padStart(2, "0")}</span>
          <span className="docs-eyebrow-rule" aria-hidden />
          <span>Section</span>
        </span>
        {props.aside && <div>{props.aside}</div>}
      </div>
      <h2 className="docs-h2">
        <span className="docs-h2-text">{props.title}</span>
        <a
          href={`#${props.id}`}
          aria-label={`Link to ${props.title}`}
          onClick={(e) => {
            e.preventDefault();
            scrollToId(props.id);
            history.replaceState(null, "", `#${props.id}`);
            // Deep links are the only way to share a specific section, so the
            // absolute URL goes to the clipboard — the # glyph alone is far
            // too small a target to be the whole affordance on touch.
            const url = `${window.location.origin}${window.location.pathname}#${props.id}`;
            void navigator.clipboard?.writeText(url).catch(() => {});
          }}
          className="docs-anchor rounded p-0.5 text-[var(--admin-text-dim)] hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
        >
          <Hash size={14} />
        </a>
      </h2>
      {props.subtitle && <p className="docs-subtitle">{props.subtitle}</p>}
    </div>
  );
}

// ── feature grid ───────────────────────────────────────────────────────────

// One capability per tile. `hue` drives every accent on the tile (icon chip,
// left spine, spotlight, index) through CSS custom properties, so the grid
// reads as one spectrum instead of six unrelated colors. `meta` is the module
// that owns the capability — the tile doubles as a map of the codebase.
// `span` breaks the grid into a bento rhythm instead of a uniform matrix.
const FEATURES: {
  icon: LucideIcon;
  title: string;
  body: string;
  meta: string;
  hue: number;
  span?: 2;
}[] = [
  {
    icon: Layers,
    hue: 213,
    title: "Three inbound dialects",
    body: "OpenAI Chat, OpenAI Responses (Codex CLI), and Anthropic Messages all decode into the same canonical IR — so a Claude Code session can be backed by GPT, and vice versa.",
    meta: "wiwi/wire/",
    span: 2,
  },
  {
    icon: KeyRound,
    hue: 262,
    title: "Virtual keys",
    body: "Per-client credentials with model allowlists, expiry, and spend caps. Callers never see provider keys.",
    meta: "wiwi/auth/",
  },
  {
    icon: Wallet,
    hue: 43,
    title: "Budgets & rate limits",
    body: "Per-key spend ceilings plus RPM and TPM throttles keep noisy tenants from burning your quota.",
    meta: "wiwi/ratelimit/",
  },
  {
    icon: Boxes,
    hue: 156,
    title: "Key pools",
    body: "Pool multiple keys per provider with smooth weighted round-robin. Exhausted keys cool down automatically.",
    meta: "wiwi/router/",
  },
  {
    icon: RefreshCw,
    hue: 187,
    title: "Retries & fallbacks",
    body: "Automatic retries on transient failures, per-key cooldowns, and ordered fallback model groups.",
    meta: "wiwi/router/",
  },
  {
    icon: Coins,
    hue: 330,
    title: "Cost tracking",
    body: "Token usage and cost for every call, priced per model and sliced by key, model, and provider.",
    meta: "wiwi/cost/",
  },
  {
    icon: HeartPulse,
    hue: 27,
    title: "Health healing",
    body: "Keys that start failing cool off, get probed with a one-token completion, and come back on their own — no operator paging, no wasted spend.",
    meta: "wiwi/core/recovery.py",
    span: 2,
  },
  {
    icon: Database,
    hue: 235,
    title: "Response cache",
    body: "Identical requests answer from Redis, or from an in-memory LRU when no cache server is configured.",
    meta: "wiwi/cache/",
  },
  {
    icon: ScrollText,
    hue: 172,
    title: "Logs & metrics",
    body: "Every call is journalled and scraped by Prometheus, with a React admin console sitting over the top.",
    meta: "wiwi/logging_core/",
  },
];

// Module coverage the grid actually touches — derived from `meta` so the
// heading chip can never drift out of sync with the tiles below it.
const FEATURE_MODULE_COUNT = new Set(
  FEATURES.map((f) => f.meta.replace(/^wiwi\//, "").split("/")[0]),
).size;

// Pointer position is written straight onto the tile as two custom properties
// — no React state, so a pointer-tracked spotlight never re-renders the grid.
// Shared by the feature grid and the provider grid.
function trackSpotlight(event: ReactPointerEvent<HTMLElement>) {
  const el = event.currentTarget;
  const rect = el.getBoundingClientRect();
  el.style.setProperty("--mx", `${((event.clientX - rect.left) / rect.width) * 100}%`);
  el.style.setProperty("--my", `${((event.clientY - rect.top) / rect.height) * 100}%`);
}

function FeatureGrid() {
  const gridRef = useRef<HTMLDivElement>(null);
  const revealed = useInViewOnce(gridRef);

  return (
    <div
      ref={gridRef}
      className={`docs-feature-grid${revealed ? " is-revealed" : ""}`}
      role="list"
    >
      {FEATURES.map((feature, i) => {
        const Icon = feature.icon;
        return (
          <article
            key={feature.title}
            role="listitem"
            className="docs-feature"
            data-span={feature.span ?? 1}
            style={{ "--f-hue": feature.hue, "--f-i": i } as CSSProperties}
            onPointerMove={trackSpotlight}
          >
            <span className="docs-spotlight" aria-hidden />
            <div className="docs-feature-top">
              <span className="docs-feature-icon">
                <Icon strokeWidth={1.75} aria-hidden />
              </span>
              <span className="docs-feature-index">{String(i + 1).padStart(2, "0")}</span>
            </div>
            <h3 className="docs-feature-title">{feature.title}</h3>
            <p className="docs-feature-body">{feature.body}</p>
            <span className="docs-feature-meta">
              <span className="docs-feature-dot" aria-hidden />
              {feature.meta}
            </span>
          </article>
        );
      })}
    </div>
  );
}

// ── endpoint filter ────────────────────────────────────────────────────────

// A live filter over the endpoint reference. Four endpoints is small, but the
// control earns its place by making the reference scannable — and it is the
// one piece of real interactivity on the page.
function EndpointFilter(props: {
  value: string;
  onChange: (v: string) => void;
  count: number;
  total: number;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="docs-filter">
      <span className="docs-filter-icon" aria-hidden>
        <Search size={13} />
      </span>
      <input
        ref={inputRef}
        type="search"
        value={props.value}
        onChange={(e) => props.onChange(e.currentTarget.value)}
        placeholder="Filter endpoints…"
        aria-label="Filter endpoints"
        className="docs-filter-input"
      />
      <kbd className="docs-filter-kbd" aria-hidden>
        /
      </kbd>
      <span className="docs-filter-count" aria-live="polite">
        {props.count === props.total
          ? `${props.total} routes`
          : `${props.count} of ${props.total}`}
      </span>
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────────

export function DocsPage() {
  const active = useScrollSpy(SECTION_IDS);
  const progress = useScrollProgress();
  const handleClick = useCallback((id: string) => { scrollToId(id); }, []);
  const [query, setQuery] = useState("");

  const visibleEndpoints = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ENDPOINTS;
    return ENDPOINTS.filter((ep) =>
      [ep.path, ep.desc, ...(ep.clients ?? [])].join(" ").toLowerCase().includes(q),
    );
  }, [query]);

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

      {/* Masthead — the site's own hero language, applied to a docs page: a
          full-bleed band standing on the page background, the same animated
          beam backdrop the landing page uses, centred type, and the same
          CTA treatment. No card, no border — the point of a hero is that it
          is the page, not a box on the page. */}
      <header className="docs-masthead full-bleed">
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-60" />
        <div className="hero-grid pointer-events-none absolute inset-0 opacity-60" aria-hidden />

        <div className="relative mx-auto max-w-4xl px-6 py-20 text-center sm:py-24">
          <div className="animate-hero-enter">
            <div className="mb-6 flex flex-wrap items-center justify-center gap-2">
              <span className="admin-badge admin-badge-blue inline-flex items-center gap-1.5">
                <BookOpen size={11} /> Documentation
              </span>
              <span className="admin-badge admin-badge-gray inline-flex items-center gap-1.5">
                v0.1.0
              </span>
              <span className="docs-chip-live">
                <span className="docs-chip-live-dot" aria-hidden />
                9 routes live
              </span>
            </div>

            <h1 className="docs-display">
              Point any client at
              <br />
              <span className="hero-gradient-text">wiwi</span>
            </h1>

            <p className="docs-lead">
              One endpoint that speaks every inbound dialect and routes to every outbound
              provider. Bring your own client — OpenAI SDK, Anthropic SDK, Codex CLI, Claude Code,
              or plain <code style={{ fontFamily: MONO }}>curl</code> — retarget it at the
              gateway, and authenticate with a virtual key.
            </p>

            <div className="docs-hero-actions mt-9 flex flex-wrap items-center justify-center gap-3">
              <div className="relative">
                <div className="hero-cta-ring" aria-hidden />
                <button
                  onClick={() => handleClick("quickstart")}
                  className="wiwi-shimmer group inline-flex h-11 items-center justify-center gap-2 rounded-full bg-gradient-to-b from-brand-400 to-brand-700 px-7 text-sm font-medium text-white shadow-lg shadow-brand-600/25 transition-[transform,filter] duration-150 hover:brightness-110 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/70"
                >
                  Quickstart
                  <ArrowRight
                    size={15}
                    className="transition-transform duration-150 group-hover:translate-x-0.5"
                  />
                </button>
              </div>
              <button
                onClick={() => handleClick("endpoints")}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:border-white/[0.14] hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent)]/60"
              >
                <Network size={15} /> API reference
              </button>
              <button
                onClick={() => handleClick("overview")}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] px-5 text-sm font-medium text-[var(--admin-text-muted)] transition-colors hover:bg-white/[0.02] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent)]/60"
              >
                <BookOpen size={15} /> How it works
              </button>
            </div>

            {/* The facts, in the landing page's trust-indicator idiom. */}
            <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[13px] text-[var(--admin-text-muted)]">
              {[
                "3 inbound dialects",
                "12 provider types",
                "1 canonical IR",
                "9 /v1 routes",
              ].map((fact) => (
                <span key={fact} className="flex items-center gap-1.5">
                  <Check size={14} className="text-emerald-400" /> {fact}
                </span>
              ))}
            </div>

            {/* Provider badges, in the landing page's idiom. */}
            <div className="mt-8 flex flex-wrap items-center justify-center gap-2.5">
              {HERO_PROVIDER_PILLS.map((p) => (
                <div
                  key={p.label}
                  className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.02] px-3.5 py-1.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]"
                >
                  <img src={p.src} alt="" className="h-5 w-5 object-contain" />
                  <span className="text-[13px] font-medium text-[var(--admin-text)]">
                    {p.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </header>

      {/* How it works — the hub-and-spoke diagram, free-standing on the page
          the way the landing page renders the same idea. */}
      <section className="docs-graph" aria-labelledby="docs-graph-title">
        <div className="docs-graph-inner">
          <p className="admin-label">How it works</p>
          <h2 id="docs-graph-title" className="docs-graph-title">
            One request in. <span className="text-brand-400">Any model out.</span>
          </h2>
          <p className="docs-graph-sub">
            Every inbound dialect decodes into the same canonical IR. The router picks a
            provider and a key from the pool, the adapter encodes the IR into that
            provider&apos;s native format, and the reply comes back re-encoded in the dialect
            you asked in.
          </p>
          <TranslationCore />
        </div>
      </section>

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
            <div className="docs-rail">
              <span className="docs-rail-line" aria-hidden />
              <span
                className="docs-rail-line-fill"
                style={{ height: `${(progress * 100).toFixed(2)}%` }}
                aria-hidden
              />
              {SECTIONS.map((s, i) => {
                const Icon = s.icon;
                const isActive = active === s.id;
                // Completed = everything the reader has already passed, so a
                // partially-read document still reads as progress.
                const isDone = i + 1 < activeIndex;
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => handleClick(s.id)}
                    aria-current={isActive ? "true" : undefined}
                    className={`docs-nav-item flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/40 ${
                      isActive
                        ? "is-active"
                        : isDone
                          ? "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                          : "text-[var(--admin-text-dim)] hover:text-[var(--admin-text)]"
                    }`}
                  >
                    <span
                      className={`docs-nav-num font-mono text-[10px] tabular-nums ${isDone ? "is-done" : "opacity-50"}`}
                      aria-hidden
                    >
                      {isDone ? <Check size={10} strokeWidth={2.5} /> : String(i + 1).padStart(2, "0")}
                    </span>
                    <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span className="flex-1">{s.label}</span>
                  </button>
                );
              })}
            </div>
            {/* Rail footer: a quiet progress meter. The filled width mirrors
                the reading-progress hairline at the top of the viewport, so
                the rail always states how much of the document remains. */}
            <div
              className="docs-rail-progress mt-5 border-t border-[var(--admin-border)] pt-4"
              aria-hidden
            >
              <div className="docs-rail-track">
                <div
                  className="docs-rail-fill"
                  style={{ transform: `scaleX(${progress.toFixed(4)})` }}
                />
              </div>
              {/* --admin-text-dim is #6b7280 on #0a0a0a, which fails 4.5:1 at
                  9px. --admin-text-muted (#9ca3af) clears it. */}
              <p className="mt-2 font-mono text-[9px] tabular-nums text-[var(--admin-text-muted)]">
                {Math.round(progress * 100)}% read
              </p>
            </div>
            <div className="mt-3">
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
            <SectionNext nextId="quickstart" nextLabel="Quickstart" onGo={handleClick} />
            <p className="docs-prose">
              Every request follows the same hub-and-spoke path: the wire codec for the inbound
              dialect decodes the request into a canonical internal representation (IR), the router
              selects a provider and key from the pool, and the adapter encodes the IR into the
              provider&apos;s native format. Responses flow back through the same path — the adapter
              decodes the provider response into IR deltas, and the wire encoder re-encodes them in
              the caller&apos;s original dialect.
            </p>
            <div className="docs-panel mt-5">
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
            <SectionNext nextId="authentication" nextLabel="Authentication" onGo={handleClick} />
            <p className="docs-prose">
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
            <SectionNext nextId="endpoints" nextLabel="Endpoints" onGo={handleClick} />
            <p className="docs-prose">
              Callers authenticate with a virtual key — never a provider key. Virtual keys are
              SHA-256-hashed at rest with constant-time comparison; per-key budgets, rate limits,
              and model allowlists are enforced before a request ever leaves the gateway.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <span className="docs-keychip">
                <code style={{ fontFamily: MONO }}>Authorization: Bearer sk-wiwi-…</code>
              </span>
              <span className="docs-keychip">
                <code style={{ fontFamily: MONO }}>x-api-key: sk-wiwi-…</code>
              </span>
              <span className="docs-keychip docs-keychip-plain">
                OpenAI · Responses · Anthropic
              </span>
            </div>
            <div className="docs-triad mt-5">
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
                  <div key={item.title} className="docs-triad-cell">
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
            <SectionNext nextId="cross-provider" nextLabel="Cross-provider" onGo={handleClick} />
            <p className="docs-prose">
              Each surface maps onto the same canonical IR. Responses are re-encoded in the
              caller&apos;s dialect on the way back out — so a Claude Code session (Anthropic
              Messages) can be backed by GPT, and vice versa.
            </p>
            <div className="mt-4">
              <EndpointFilter
                value={query}
                onChange={setQuery}
                count={visibleEndpoints.length}
                total={ENDPOINTS.length}
              />
            </div>
            <div className="mt-2 divide-y divide-[var(--admin-border)]">
              {visibleEndpoints.map((ep) => (
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
              {visibleEndpoints.length === 0 && (
                <p className="docs-empty py-6 text-center text-[13px] text-[var(--admin-text-muted)]">
                  No endpoint matches “{query}”.
                </p>
              )}
            </div>
          </section>

          <section id="cross-provider" className="docs-section scroll-mt-20">
            <SectionHeading
              id="cross-provider"
              index={5}
              title="Cross-provider routing"
              subtitle="Decouple the caller's dialect from the upstream provider"
            />
            <SectionNext nextId="streaming" nextLabel="Streaming" onGo={handleClick} />
            <p className="docs-prose">
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
            <SectionNext nextId="config" nextLabel="Configuration" onGo={handleClick} />
            <p className="docs-prose">
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
            <div className="docs-callout mt-4">
              <p className="docs-callout-label">The streaming contract</p>
              <p className="docs-callout-body">
                Exactly one <code style={{ fontFamily: MONO }}>StreamStart</code>, then{" "}
                <code style={{ fontFamily: MONO }}>ToolCallOpen → ArgsDelta* → Close</code> per
                index, then <code style={{ fontFamily: MONO }}>UsageFinal</code>, then{" "}
                <code style={{ fontFamily: MONO }}>Finish</code>, then{" "}
                <code style={{ fontFamily: MONO }}>StreamEnd</code> or{" "}
                <code style={{ fontFamily: MONO }}>StreamError</code>.
              </p>
            </div>
          </section>

          <section id="config" className="docs-section scroll-mt-20">
            <SectionHeading
              id="config"
              index={7}
              title="Configuration"
              subtitle="A single wiwi.yaml — LiteLLM-shaped"
            />
            <SectionNext nextId="features" nextLabel="Features" onGo={handleClick} />
            <p className="docs-prose">
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
              aside={
                <span className="docs-count-chip">
                  <b>{FEATURES.length}</b> capabilities
                  <span className="docs-count-sep" aria-hidden />
                  <b>{FEATURE_MODULE_COUNT}</b> modules
                </span>
              }
            />
            <FeatureGrid />
          </section>

          {/* CTA — borderless, like the landing page's closing section. */}
          <div className="docs-cta mt-16">
            <h2 className="docs-cta-title">
              Start routing requests
              <br />
              <span className="text-brand-400">in 30 seconds</span>
            </h2>
            <p className="docs-cta-body">
              Point an OpenAI, Anthropic, or Codex client at the gateway, mint a virtual key, and
              make the first call — or jump straight into the playground.
            </p>
            <div className="docs-cta-actions">
              <Link
                to="/playground"
                className="wiwi-shimmer group inline-flex h-12 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 text-base font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] duration-150 hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/70"
              >
                Open playground
                <ArrowRight
                  size={16}
                  className="transition-transform duration-150 group-hover:translate-x-0.5"
                />
              </Link>
              <Link
                to="/signup"
                className="inline-flex h-12 items-center justify-center gap-2 rounded-[10px] border border-[var(--admin-border)] bg-transparent px-6 text-base font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--admin-accent)]/60"
              >
                Create an account
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
