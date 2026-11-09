// Docs — the public wire reference for the gateway.
//
// A reference manual, not a marketing page, so the composition is typographic
// rather than decorative: a masthead that states the thesis as a wire strip
// (five inbound surfaces → one canonical IR → thirteen provider types), a nav
// that is a sticky vertical rail on desktop and a sticky horizontal chip bar
// on narrow viewports, and eight numbered sections built from hairlines, mono
// labels and real tables.
//
// Everything asserted here is read off the code — the endpoint list from
// server/app.py, the retryable set from providers/base.py:RETRYABLE_STATUS, the
// delta order from streaming/deltas.py, the provider types from
// config.py:PROVIDER_TYPES. Change the code and this page has to follow.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import { ArrowUp, Check, Copy, Search } from "lucide-react";
import { ClosingCta } from "@/components/ClosingCta";
import { highlight, langFromLabel, LANG_DOT, type Lang } from "@/lib/code-highlight";

// ── navigation ─────────────────────────────────────────────────────────────

type Section = { id: string; num: string; label: string };

const SECTIONS: Section[] = [
  { id: "overview", num: "01", label: "Overview" },
  { id: "quickstart", num: "02", label: "Quickstart" },
  { id: "auth", num: "03", label: "Authentication" },
  { id: "endpoints", num: "04", label: "Endpoints" },
  { id: "translation", num: "05", label: "Cross-provider" },
  { id: "streaming", num: "06", label: "Streaming" },
  { id: "config", num: "07", label: "Configuration" },
  { id: "errors", num: "08", label: "Errors & limits" },
];

// Hoisted so the scroll listener does not rebuild its list on every render.
const SECTION_IDS = SECTIONS.map((s) => s.id);

// Honour the OS preference and the console's own reduce-motion toggle. The CSS
// already disables the page's transitions for both; this covers the two places
// where the browser animates scroll on our behalf.
function motionReduced() {
  return (
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    document.documentElement.classList.contains("reduce-motion")
  );
}

function scrollToId(id: string) {
  const el = document.getElementById(id);
  // `auto` would still honour `:root { scroll-behavior: smooth }`, so reduced
  // motion has to ask for `instant` explicitly.
  if (el) el.scrollIntoView({ behavior: motionReduced() ? "instant" : "smooth", block: "start" });
}

// One scroll listener drives the active section and both progress meters.
// Position is measured rather than observed: with eight sections of very
// different heights an IntersectionObserver decides the active row by whatever
// happens to cross its line, which flickers between neighbours. "The last
// heading that has passed the reading line" is the definition a reader would
// give, and it never flickers.
const READING_LINE = 152;

function useDocState() {
  const [state, setState] = useState<{ active: string; progress: number }>({
    active: SECTION_IDS[0],
    progress: 0,
  });

  useEffect(() => {
    // Measured directly on the event rather than deferred to rAF: scroll events
    // are already frame-aligned, the reads below never write, and a deferred
    // measurement is one more thing that can be throttled out from under the
    // reader (a background tab, a sandboxed frame) and leave the rail wrong.
    const measure = () => {
      const doc = document.documentElement;
      const max = doc.scrollHeight - window.innerHeight;
      const progress = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
      let active = SECTION_IDS[0];
      for (const id of SECTION_IDS) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= READING_LINE) active = id;
      }
      setState((prev) =>
        prev.active === active && Math.abs(prev.progress - progress) < 0.001
          ? prev
          : { active, progress },
      );
    };
    measure();
    window.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, []);

  return state;
}

// ── copy control ───────────────────────────────────────────────────────────

function CopyControl(props: { text: string; label: string; compact?: boolean }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(props.text);
          setCopied(true);
          timer.current = setTimeout(() => setCopied(false), 1500);
        } catch {
          // A denied clipboard permission must not look like a successful copy.
          setCopied(false);
        }
      }}
      className={props.compact ? "docs-pathcopy" : "docs-code-copy"}
      aria-label={copied ? props.label + " copied" : "Copy " + props.label}
    >
      {copied ? <Check size={props.compact ? 11 : 12} aria-hidden /> : <Copy size={props.compact ? 11 : 12} aria-hidden />}
      {props.compact ? null : <span>{copied ? "Copied" : "Copy"}</span>}
    </button>
  );
}

// ── code block ─────────────────────────────────────────────────────────────

// Line numbers only for multi-line snippets: on a one-liner the gutter is
// furniture. The contract block opts out because it is a shape, not code.
function CodeBlock(props: { code: string; label: string; lang?: Lang; gutter?: boolean }) {
  const lang = props.lang ?? langFromLabel(props.label);
  const lines = props.code.split("\n");
  const showGutter = (props.gutter ?? true) && lines.length > 1;
  return (
    <div className="docs-code">
      <div className="docs-code-head">
        <span className="docs-code-lang">
          <span className={"docs-dot " + LANG_DOT[lang]} aria-hidden />
          {props.label}
        </span>
        <CopyControl text={props.code} label={props.label} />
      </div>
      <div className="docs-code-body">
        {showGutter && (
          <div className="docs-code-gutter" aria-hidden>
            {lines.map((_, i) => <span key={i}>{i + 1}</span>)}
          </div>
        )}
        <pre tabIndex={0} role="group" aria-label={props.label + " example"} className="docs-pre">
          <code>{highlight(props.code, lang)}</code>
        </pre>
      </div>
    </div>
  );
}

function TabbedCode(props: { tabs: { label: string; code: string }[] }) {
  const [idx, setIdx] = useState(0);
  const active = props.tabs[idx] ?? props.tabs[0];
  return (
    <div className="docs-tabbed">
      <div className="docs-tabs" role="tablist" aria-label="Code language">
        {props.tabs.map((t, i) => (
          <button
            key={t.label}
            type="button"
            role="tab"
            aria-selected={i === idx}
            tabIndex={i === idx ? 0 : -1}
            onClick={() => setIdx(i)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") setIdx((i + 1) % props.tabs.length);
              if (e.key === "ArrowLeft") setIdx((i - 1 + props.tabs.length) % props.tabs.length);
            }}
            className="docs-tab"
            data-active={i === idx}
          >
            <span className={"docs-dot " + LANG_DOT[langFromLabel(t.label)]} aria-hidden />
            {t.label}
          </button>
        ))}
      </div>
      <CodeBlock code={active.code} label={active.label} />
    </div>
  );
}

// ── page furniture ─────────────────────────────────────────────────────────

function SectionHead(props: { id: string; num: string; title: string; lede: string }) {
  return (
    <header className="docs-head">
      <div className="docs-head-meta">
        <span className="docs-head-num">{props.num}</span>
        <span className="docs-head-rule" aria-hidden />
      </div>
      <h2 className="docs-head-title">
        {props.title}
        <a className="docs-head-anchor" href={"#" + props.id} aria-label={"Permalink to " + props.title}>
          #
        </a>
      </h2>
      <p className="docs-head-lede">{props.lede}</p>
    </header>
  );
}

function Note(props: { label: string; children: ReactNode }) {
  return (
    <div className="docs-note">
      <p className="docs-note-label">{props.label}</p>
      <div className="docs-note-body">{props.children}</div>
    </div>
  );
}

function BackToTop() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 900);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <button
      type="button"
      aria-label="Back to top"
      onClick={() => window.scrollTo({ top: 0, behavior: motionReduced() ? "instant" : "smooth" })}
      className={"docs-backtop" + (visible ? " is-visible" : "")}
    >
      <ArrowUp size={16} aria-hidden />
    </button>
  );
}


// ── masthead ───────────────────────────────────────────────────────────────

// The five inbound surfaces in the order the code declares them, each with the
// body shape the caller actually sends — that shape is what decides whether a
// given client can talk to the gateway at all.
const INBOUND = [
  { path: "/v1/chat/completions", body: "messages" },
  { path: "/v1/responses", body: "input" },
  { path: "/v1/messages", body: "content blocks" },
  { path: "/v1/completions", body: "prompt" },
  { path: "/v1/playground/completions", body: "session cookie" },
];

function WireStrip() {
  return (
    <div className="docs-wire">
      <div className="docs-wire-cell">
        <p className="docs-wire-label">Inbound</p>
        <ul className="docs-wire-list">
          {INBOUND.map((s) => (
            <li key={s.path} className="docs-wire-chip">
              <span className="docs-wire-method">POST</span>
              <span className="docs-wire-path">{s.path}</span>
              <span className="docs-wire-body">{s.body}</span>
            </li>
          ))}
        </ul>
        <p className="docs-wire-note">Decoded by the wire codec for that dialect.</p>
      </div>

      <span className="docs-wire-arrow" aria-hidden>→</span>

      <div className="docs-wire-cell docs-wire-cell-core">
        <p className="docs-wire-label">Canonical</p>
        <div className="docs-wire-node">
          <img src="/wiwi-logo.png" alt="" className="docs-wire-node-logo" />
          <span className="docs-wire-node-text">wiwi IR</span>
        </div>
        <p className="docs-wire-note">
          One request type. No dialect and no provider name reaches core.
        </p>
      </div>

      <span className="docs-wire-arrow" aria-hidden>→</span>

      <div className="docs-wire-cell">
        <p className="docs-wire-label">Outbound</p>
        <div className="docs-wire-out">
          <span className="docs-wire-count">13</span>
          <span className="docs-wire-out-text">
            provider types
            <span className="docs-wire-out-sub">chat wire · native · SSE-only</span>
          </span>
        </div>
        <p className="docs-wire-note">One adapter per type, behind one registry branch.</p>
      </div>
    </div>
  );
}

// ── request path (01) ──────────────────────────────────────────────────────

const PATH_STEPS = [
  {
    name: "decode",
    title: "Dialect → IR",
    desc: "The inbound codec reads the body into a canonical Request. This is the only place a dialect is named.",
    where: "wire/*.py",
  },
  {
    name: "admit",
    title: "Key, budget, rate limit",
    desc: "The virtual key resolves to a budget and an rpm/tpm window; a cap is refused before a provider is touched.",
    where: "auth/, ratelimit/",
  },
  {
    name: "replay",
    title: "Journal check",
    desc: "A reconnecting stream asks for the frames it already has, so a dropped connection resumes instead of restarting.",
    where: "streaming/tape_store.py",
  },
  {
    name: "route",
    title: "Group → deployment → key",
    desc: "The model group and its aliases resolve to a deployment and a key, with retries, cooldowns and fallbacks driving the choice.",
    where: "router/router.py",
  },
  {
    name: "execute",
    title: "IR → provider wire",
    desc: "The adapter encodes the request, prices the usage that comes back, and builds the request's log event.",
    where: "core/gateway.py",
  },
  {
    name: "re-encode",
    title: "IR → caller's dialect",
    desc: "The caller's own wire encoder rebuilds the reply. Claude Code backed by GPT never learns it was not Claude.",
    where: "wire/*.py",
  },
];

function RequestPath() {
  return (
    <ol className="docs-steps">
      {PATH_STEPS.map((s, i) => (
        <li key={s.name} className="docs-step">
          <span className="docs-step-num" aria-hidden>{String(i + 1).padStart(2, "0")}</span>
          <div className="docs-step-body">
            <p className="docs-step-title">
              {s.title}
              <code className="docs-step-name">{s.name}</code>
            </p>
            <p className="docs-step-desc">{s.desc}</p>
            <p className="docs-step-mod">{s.where}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

const CAPABILITIES = [
  { term: "Virtual keys", desc: "SHA-256 at rest, per-key budget, expiry and an rpm/tpm window." },
  { term: "Key pools", desc: "Smooth weighted round-robin across a provider's keys, with cooldowns." },
  { term: "Failover", desc: "Retries, deployment cooldowns, group fallbacks, mid-stream resume." },
  { term: "Health healer", desc: "Probes cooling keys and deployments with a one-token call. Opt-in." },
  { term: "Cost & logs", desc: "Every request priced from the price table and logged to the database." },
  { term: "Response cache", desc: "Exact-match, non-streaming only. Off by default." },
  { term: "Stream journals", desc: "SSE frames persisted per request, so a reconnect replays them." },
  { term: "Metrics", desc: "Prometheus counters and histograms on a configurable path." },
];

// ── endpoints (04) ─────────────────────────────────────────────────────────

type Method = "POST" | "GET" | "DELETE" | "WS";

type Endpoint = {
  method: Method;
  path: string;
  group: string;
  desc: string;
  clients?: string[];
  auth: string;
};

const ENDPOINTS: Endpoint[] = [
  {
    method: "POST",
    path: "/v1/chat/completions",
    group: "Chat",
    desc: "The classic Chat Completions surface. Any OpenAI-compatible SDK or tool works against it unchanged.",
    clients: ["OpenAI SDK", "most tools"],
    auth: "Authorization: Bearer sk-wiwi-…",
  },
  {
    method: "POST",
    path: "/v1/completions",
    group: "Chat",
    desc: "The legacy Completions shape: a bare prompt in, choices[].text out. Same pipeline, not a chat transcript.",
    clients: ["legacy SDKs"],
    auth: "Authorization: Bearer sk-wiwi-…",
  },
  {
    method: "POST",
    path: "/v1/playground/completions",
    group: "Chat",
    desc: "Chat behind the browser session instead of a bearer key. The key is resolved server-side and never reaches the page.",
    clients: ["playground UI"],
    auth: "wiwi_session cookie",
  },
  {
    method: "POST",
    path: "/v1/responses",
    group: "Responses",
    desc: "The Responses surface, with store and previous_response_id kept on the gateway side.",
    clients: ["Codex CLI", "Responses SDK"],
    auth: "Authorization: Bearer sk-wiwi-…",
  },
  {
    method: "GET",
    path: "/v1/responses/{response_id}",
    group: "Responses",
    desc: "Replay a stored response object. Ids are key-scoped, so a foreign id is a 404 rather than a capability.",
    auth: "Authorization: Bearer sk-wiwi-…",
  },
  {
    method: "DELETE",
    path: "/v1/responses/{response_id}",
    group: "Responses",
    desc: "Delete stored response state. Same key scoping as the read.",
    auth: "Authorization: Bearer sk-wiwi-…",
  },
  {
    method: "POST",
    path: "/v1/messages",
    group: "Anthropic",
    desc: "The Anthropic Messages surface. Claude Code and the Anthropic SDK are first-class callers here.",
    clients: ["Claude Code", "Anthropic SDK"],
    auth: "x-api-key sk-wiwi-…",
  },
  {
    method: "POST",
    path: "/v1/messages/count_tokens",
    group: "Anthropic",
    desc: "Input-token estimate from the same estimator the gateway bills against when an upstream omits usage.",
    clients: ["Claude Code"],
    auth: "x-api-key sk-wiwi-…",
  },
  {
    method: "GET",
    path: "/v1/models",
    group: "Utility",
    desc: "The model groups this key may request, as OpenAI-shaped model objects.",
    auth: "Authorization: Bearer sk-wiwi-…",
  },
  {
    method: "GET",
    path: "/public/models",
    group: "Utility",
    desc: "The unauthenticated catalogue the public site renders. Names and metadata only.",
    auth: "none",
  },
  {
    method: "GET",
    path: "/health",
    group: "Utility",
    desc: "Liveness in the status, readiness in the body: ok or degraded plus group and loss counters. Always 200, so an orchestrator does not restart a process that is merely unconfigured.",
    auth: "none",
  },
  {
    method: "WS",
    path: "/v1/realtime",
    group: "Utility",
    desc: "WebSocket passthrough. Off by default, and only a provider that declares a realtime_url can serve it; everything else is refused before the upgrade is accepted.",
    auth: "Authorization: Bearer sk-wiwi-…",
  },
];

const METHOD_TONE: Record<Method, string> = {
  POST: "post",
  GET: "get",
  DELETE: "delete",
  WS: "ws",
};

function EndpointList() {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // "/" focuses the filter, the way a reference manual's search does.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ENDPOINTS;
    return ENDPOINTS.filter((e) =>
      [e.path, e.group, e.desc, e.method, ...(e.clients ?? [])].join(" ").toLowerCase().includes(q),
    );
  }, [query]);

  // Grouped in the order the rows declare, so filtering never reshuffles the
  // sections a reader is learning the page by.
  const groups = useMemo(() => {
    const order: string[] = [];
    const byGroup = new Map<string, Endpoint[]>();
    for (const e of matches) {
      if (!byGroup.has(e.group)) {
        byGroup.set(e.group, []);
        order.push(e.group);
      }
      byGroup.get(e.group)?.push(e);
    }
    return order.map((g) => [g, byGroup.get(g) ?? []] as const);
  }, [matches]);

  return (
    <div className="docs-endpoints">
      <div className="docs-filter">
        <Search size={14} className="docs-filter-icon" aria-hidden />
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by path, dialect or client"
          aria-label="Filter endpoints"
          className="docs-filter-input"
        />
        <kbd className="docs-filter-kbd" aria-hidden>/</kbd>
        <span className="docs-filter-count" aria-live="polite">
          {matches.length}/{ENDPOINTS.length}
        </span>
      </div>

      {groups.length === 0 ? (
        <div className="docs-empty">
          <p className="docs-empty-title">No route matches “{query.trim()}”.</p>
          <button type="button" onClick={() => setQuery("")} className="docs-empty-reset">
            Clear the filter
          </button>
        </div>
      ) : (
        groups.map(([group, rows]) => (
          <section key={group} className="docs-ep-group" aria-label={group}>
            <h3 className="docs-ep-group-head">
              <span>{group}</span>
              <span className="docs-ep-group-rule" aria-hidden />
              <span className="docs-ep-group-count">{rows.length}</span>
            </h3>
            <ul className="docs-ep-list">
              {rows.map((e) => (
                <li key={e.method + " " + e.path} className="docs-ep">
                  <div className="docs-ep-top">
                    <span className="docs-method" data-tone={METHOD_TONE[e.method]}>{e.method}</span>
                    <code className="docs-path">{e.path}</code>
                    <CopyControl text={e.path} label={e.path} compact />
                  </div>
                  <p className="docs-ep-desc">{e.desc}</p>
                  <div className="docs-ep-foot">
                    <span className="docs-ep-auth">
                      <span className="docs-ep-auth-label">auth</span>
                      <code>{e.auth}</code>
                    </span>
                    {e.clients && (
                      <span className="docs-ep-clients">
                        {e.clients.map((c) => (
                          <span key={c} className="docs-client-chip">{c}</span>
                        ))}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}

// ── provider table (05) ────────────────────────────────────────────────────

type Wire = "chat" | "native" | "sse";

const WIRE_LABEL: Record<Wire, string> = {
  chat: "OpenAI Chat",
  native: "Native protocol",
  sse: "SSE-only",
};

const PROVIDERS: { id: string; wire: Wire; note: string; src?: string }[] = [
  { id: "openai", wire: "chat", note: "The reference Chat Completions codec.", src: "/logos/openai.svg" },
  { id: "openai-compatible", wire: "chat", note: "Same adapter; point base_url at Ollama, vLLM, or anything else that speaks the shape.", src: "/logos/openai-compatible.svg" },
  { id: "gmicloud", wire: "chat", note: "Same adapter, vendor base URL.", src: "/logos/gmicloud.svg" },
  { id: "bai", wire: "chat", note: "Same adapter, vendor base URL.", src: "/logos/bai.svg" },
  { id: "openrouter", wire: "chat", note: "Chat wire plus the routing headers OpenRouter reads, forwarded from extra_headers.", src: "/logos/openrouter.svg" },
  { id: "nvidia-nim", wire: "chat", note: "Strips boolean JSON-Schema subschemas and aliases parameters named type, which vLLM's tool parser rejects.", src: "/logos/nvidia-nim.svg" },
  { id: "hermes", wire: "chat", note: "Nous Portal over OAuth device code, with multi-account rotation.", src: "/logos/hermes.svg" },
  { id: "ifm", wire: "chat", note: "Replays reasoning_content on every assistant turn; effort rides chat_template_kwargs.", src: "/logos/ifm.svg" },
  { id: "anthropic", wire: "native", note: "Native Messages protocol, including opt-in prompt-cache breakpoints per deployment.", src: "/logos/anthropic.svg" },
  { id: "gemini", wire: "native", note: "Native generateContent.", src: "/logos/gemini.svg" },
  { id: "cline", wire: "sse", note: "OAuth. The upstream has no non-streaming mode, so replies are reassembled for non-streaming callers.", src: "/logos/cline.svg" },
  { id: "workbuddy", wire: "sse", note: "OAuth, streaming-only upstream for the same reason.", src: "/logos/workbuddy.svg" },
  { id: "opencode", wire: "sse", note: "OAuth or console key. Access depends on the request matching the current OpenCode client.", src: "/logos/opencode.svg" },
];

const TRANSLATED = [
  { term: "tools · tool_choice", desc: "Decoded into IR tool definitions, then re-encoded in each vendor's own schema." },
  { term: "parallel tool calls", desc: "Siblings in the delta stream: one open/close pair per tool index, interleaved." },
  { term: "images · files", desc: "Content parts carry media; each adapter inlines or uploads it the way its vendor expects." },
  { term: "thinking", desc: "A ThinkingDelta, re-encoded as reasoning_content, as a thinking block, or dropped where the target has no channel." },
  { term: "reasoning_effort", desc: "Mapped per provider: budget tokens for Anthropic, chat_template_kwargs for IFM, pass-through for OpenAI." },
  { term: "response_format", desc: "Carried as a structured-output request; whether the upstream honours it is per adapter." },
  { term: "prompt caching", desc: "Anthropic cache_control breakpoints on the stable prefix. Opt-in per deployment." },
  { term: "usage", desc: "One normalised Usage, priced from the price table and logged with the request." },
];

function ProviderTable() {
  return (
    <div className="docs-table-wrap">
      <table className="docs-table">
        <caption className="docs-sr">
          Provider types, the wire each one speaks, and what its adapter handles
        </caption>
        <thead>
          <tr>
            <th scope="col">provider:</th>
            <th scope="col">Upstream wire</th>
            <th scope="col">What the adapter handles</th>
          </tr>
        </thead>
        <tbody>
          {PROVIDERS.map((p) => (
            <tr key={p.id}>
              <th scope="row" className="docs-table-id">
                {p.src && <img src={p.src} alt="" className="docs-table-logo" />}
                <code>{p.id}</code>
              </th>
              <td>
                <span className="docs-wire-tag" data-wire={p.wire}>{WIRE_LABEL[p.wire]}</span>
              </td>
              <td className="docs-table-note">{p.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── streaming (06) ─────────────────────────────────────────────────────────

const CONTRACT = [
  "StreamStart            exactly one, first",
  "├─ TextDelta           any number",
  "├─ ThinkingDelta       any number",
  "├─ ToolCallOpen        per tool index",
  "│  ├─ ToolCallArgsDelta",
  "│  └─ ToolCallClose",
  "├─ UsageFinal          at least once",
  "├─ Finish              exactly once",
  "└─ StreamEnd           or StreamError, which may arrive at any point",
].join("\n");

const OPENAI_FRAMES = [
  'data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}',
  "",
  'data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hel"}}]}',
  "",
  'data: {"object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
  "",
  'data: {"object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":11,"completion_tokens":4,"total_tokens":15}}',
  "",
  "data: [DONE]",
].join("\n");

const ANTHROPIC_FRAMES = [
  "event: message_start",
  'data: {"type":"message_start","message":{"id":"msg_…","role":"assistant","content":[],"usage":{"input_tokens":11,"output_tokens":1}}}',
  "",
  "event: content_block_delta",
  'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}',
  "",
  "event: message_delta",
  'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":4}}',
  "",
  "event: message_stop",
  'data: {"type":"message_stop"}',
].join("\n");

const LIVENESS = [
  { term: "x-wiwi-stream-id", desc: "On every streamed reply. Send it back with Last-Event-ID to resume from the frame you already have." },
  { term: "Last-Event-ID", desc: "Replays from the journal, including after a gateway restart: the frames are on disk, not in memory." },
  { term: "stream_idle_timeout_s", desc: "30 s between chunks once content has flowed. A longer silence is treated as a dead upstream." },
  { term: "stream_first_chunk_timeout_s", desc: "300 s for the phase before the first token. A reasoning model may legitimately think for minutes." },
  { term: "stream_ping_interval_s", desc: "15 s keep-alive. Anthropic's wire has a named ping event for this; idle proxies reap connections without it." },
];

// ── errors (08) ────────────────────────────────────────────────────────────

const STATUSES: { code: string; type: string; when: string; retried: boolean }[] = [
  { code: "400", type: "invalid_request_error", when: "The body failed validation or decoding. Never 422.", retried: false },
  { code: "401", type: "authentication_error", when: "Missing, invalid, disabled or expired key.", retried: false },
  { code: "402", type: "budget_exceeded", when: "The key is over its budget cap. A cap does not clear on retry.", retried: false },
  { code: "404", type: "not_found_error", when: "Unknown model group, or a response id owned by another key.", retried: false },
  { code: "408", type: "request_timeout", when: "The upstream request timed out.", retried: true },
  { code: "429", type: "rate_limit_error", when: "The key's rpm/tpm window, or every candidate deployment at its own cap.", retried: true },
  { code: "500", type: "api_error", when: "Gateway fault.", retried: true },
  { code: "502", type: "provider_error", when: "The upstream body could not be decoded.", retried: true },
  { code: "503", type: "service_unavailable", when: "No deployment could serve the request, or an in-flight ceiling was hit.", retried: true },
  { code: "504", type: "timeout", when: "The upstream read timed out.", retried: true },
  { code: "529", type: "overloaded", when: "The upstream reports overload (Anthropic's own code).", retried: true },
];


// ── page ───────────────────────────────────────────────────────────────────

export function DocsPage() {
  const { active, progress } = useDocState();
  const activeIndex = Math.max(0, SECTION_IDS.indexOf(active));
  const chipRowRef = useRef<HTMLDivElement>(null);
  const chipRefs = useRef<Record<string, HTMLAnchorElement | null>>({});

  // Keep the active chip in view inside the horizontal bar without letting the
  // browser scroll the page: scrollLeft is set on the bar itself.
  useEffect(() => {
    const row = chipRowRef.current;
    const chip = chipRefs.current[active];
    if (!row || !chip) return;
    row.scrollTo({
      left: Math.max(0, chip.offsetLeft - row.clientWidth / 2 + chip.clientWidth / 2),
      behavior: motionReduced() ? "instant" : "smooth",
    });
  }, [active]);

  const onNavClick = useCallback(
    (id: string) => (e: ReactMouseEvent) => {
      e.preventDefault();
      scrollToId(id);
      // Keep the address bar honest without pushing a history entry per click.
      window.history.replaceState(null, "", "#" + id);
    },
    [],
  );

  return (
    <div className="docs-page">
      <div className="docs-progress" aria-hidden>
        <div className="docs-progress-fill" style={{ transform: "scaleX(" + progress + ")" }} />
      </div>

      {/* ── masthead ────────────────────────────────────────────────────── */}
      <header className="docs-masthead">
        <div className="docs-masthead-inner">
          <p className="docs-kicker">
            <span className="docs-kicker-dot" aria-hidden />
            Wire reference
            <span className="docs-kicker-sep" aria-hidden />
            <span className="docs-kicker-mono">/v1</span>
          </p>

          <h1 className="docs-display">
            Every dialect in.
            <br />
            <span className="docs-display-accent">Any provider out.</span>
          </h1>

          <p className="docs-lead">
            The inbound surfaces this gateway answers, the provider types it can route to, and the
            contract between them. One canonical request in the middle, so a client never has to
            know which model actually served it.
          </p>

          <div className="docs-hero-actions">
            <Link to="/playground" className="docs-btn docs-btn-primary">Open the playground</Link>
            <button type="button" onClick={() => scrollToId("quickstart")} className="docs-btn docs-btn-ghost">
              Jump to quickstart
            </button>
          </div>

          <WireStrip />
        </div>
      </header>

      {/* ── nav: rail on desktop, chips below it ─────────────────────────── */}
      <nav className="docs-chips" ref={chipRowRef} aria-label="Sections">
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={"#" + s.id}
            ref={(el) => { chipRefs.current[s.id] = el; }}
            onClick={onNavClick(s.id)}
            className="docs-chip"
            data-active={s.id === active}
            aria-current={s.id === active ? "true" : undefined}
          >
            <span className="docs-chip-num">{s.num}</span>
            {s.label}
          </a>
        ))}
      </nav>

      <div className="docs-shell">
        <div className="docs-grid">
          {/* Rail */}
          <aside className="docs-rail" aria-label="Sections">
            <div className="docs-rail-inner">
              <div className="docs-rail-head">
                <span className="docs-rail-title">Contents</span>
                <span className="docs-rail-count">
                  <b>{String(activeIndex + 1).padStart(2, "0")}</b>
                  <span className="docs-rail-count-sep">/</span>
                  {String(SECTIONS.length).padStart(2, "0")}
                </span>
              </div>

              <nav className="docs-rail-body">
                <span className="docs-rail-spine" aria-hidden>
                  <span
                    className="docs-rail-fill"
                    style={{ transform: "scaleY(" + (activeIndex + 1) / SECTIONS.length + ")" }}
                  />
                </span>
                <ul className="docs-rail-list">
                  {SECTIONS.map((s) => (
                    <li key={s.id}>
                      <a
                        href={"#" + s.id}
                        onClick={onNavClick(s.id)}
                        className="docs-rail-item"
                        data-active={s.id === active}
                        aria-current={s.id === active ? "true" : undefined}
                      >
                        <span className="docs-rail-num">{s.num}</span>
                        <span className="docs-rail-label">{s.label}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              </nav>

              <div className="docs-rail-foot">
                <div className="docs-rail-meter" aria-hidden>
                  {SECTIONS.map((s, i) => (
                    <span
                      key={s.id}
                      className="docs-rail-tick"
                      data-state={i < activeIndex ? "done" : i === activeIndex ? "current" : "todo"}
                    />
                  ))}
                </div>
                <p className="docs-rail-read">
                  <span className="docs-rail-read-num">{Math.round(progress * 100)}%</span> read
                </p>
                <Link to="/playground" className="docs-rail-cta">Try it in the playground</Link>
              </div>
            </div>
          </aside>

          {/* Document */}
          <div className="docs-doc">
            {/* 01 ───────────────────────────────────────────────────────── */}
            <section id="overview" className="docs-section">
              <SectionHead
                id="overview"
                num="01"
                title="Overview"
                lede="One request shape in the middle, and nothing else branching on a dialect or a provider name."
              />
              <p className="docs-prose">
                A request is decoded by the wire codec for the dialect it arrived in, carried as a
                canonical <code>Request</code>, and encoded by the adapter for whichever provider
                serves it. The reply travels back the same way, so the caller always receives its
                own dialect — even when the model behind it speaks something else entirely.
              </p>
              <p className="docs-prose">
                There are no pairwise converters. Adding an inbound surface is one module in{" "}
                <code>wire/</code>; adding a provider is one adapter plus one branch in{" "}
                <code>registry.get_adapter()</code>. That is why a fourth dialect did not multiply
                the work by four.
              </p>

              <div className="docs-sub">
                <h3 className="docs-h3">The path a request takes</h3>
                <RequestPath />
              </div>

              <div className="docs-sub">
                <h3 className="docs-h3">What ships with it</h3>
                <dl className="docs-defs">
                  {CAPABILITIES.map((c) => (
                    <div key={c.term} className="docs-def">
                      <dt className="docs-def-term">{c.term}</dt>
                      <dd className="docs-def-desc">{c.desc}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </section>

            {/* 02 ───────────────────────────────────────────────────────── */}
            <section id="quickstart" className="docs-section">
              <SectionHead
                id="quickstart"
                num="02"
                title="Quickstart"
                lede="Four steps from an empty config to a reply from a real model."
              />
              <ol className="docs-qs">
                <li className="docs-qs-step">
                  <p className="docs-qs-title">Start the gateway</p>
                  <p className="docs-qs-body">
                    It listens on <code>0.0.0.0:4000</code> by default, and startup fails closed
                    unless a master key or a session secret is configured.
                  </p>
                  <CodeBlock
                    label="bash"
                    code={`cp wiwi.yaml.example wiwi.yaml
export WIWI_MASTER_KEY=sk-wiwi-master-…
wiwi --config wiwi.yaml`}
                  />
                </li>
                <li className="docs-qs-step">
                  <p className="docs-qs-title">Mint a virtual key</p>
                  <p className="docs-qs-body">
                    One call against the master key. The plaintext is returned here and nowhere
                    else; the database keeps only its hash.
                  </p>
                  <CodeBlock
                    label="bash"
                    code={`curl http://localhost:4000/admin/keys/generate \\
  -H "Authorization: Bearer $WIWI_MASTER_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "laptop"}'`}
                  />
                </li>
                <li className="docs-qs-step">
                  <p className="docs-qs-title">Point a client at it</p>
                  <p className="docs-qs-body">
                    Any OpenAI-compatible client works unchanged; an Anthropic client sends{" "}
                    <code>x-api-key</code> instead of the bearer.
                  </p>
                  <CodeBlock
                    label="bash"
                    code={`export OPENAI_BASE_URL=http://localhost:4000/v1
export OPENAI_API_KEY=sk-wiwi-…`}
                  />
                </li>
                <li className="docs-qs-step">
                  <p className="docs-qs-title">Make the first call</p>
                  <p className="docs-qs-body">
                    The model name is a model group, not a provider's catalogue id.
                  </p>
                  <TabbedCode
                    tabs={[
                      {
                        label: "curl",
                        code: `curl $OPENAI_BASE_URL/chat/completions \\
  -H "Authorization: Bearer $OPENAI_API_KEY" \\
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
                    ]}
                  />
                </li>
              </ol>

              <Note label="Model groups are the routing table">
                <p>
                  A name reaches a provider only through <code>model_list</code> or the console. A
                  group can hold several deployments of the same model and the router spreads
                  across them by weight — that is what makes a key pool and a fallback mean
                  anything. A name with no group is a <code>404</code>, not a passthrough.
                </p>
              </Note>
            </section>

            {/* 03 ───────────────────────────────────────────────────────── */}
            <section id="auth" className="docs-section">
              <SectionHead
                id="auth"
                num="03"
                title="Authentication"
                lede="Three credentials, three scopes. Every route in the next section names the one it accepts."
              />
              <div className="docs-table-wrap">
                <table className="docs-table">
                  <thead>
                    <tr>
                      <th scope="col">Credential</th>
                      <th scope="col">Header</th>
                      <th scope="col">Reaches</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <th scope="row" className="docs-table-id">
                        <code>sk-wiwi-…</code>
                        <span className="docs-table-sub">virtual key</span>
                      </th>
                      <td className="docs-table-note">
                        <code>Authorization: Bearer</code> or <code>x-api-key</code>
                      </td>
                      <td className="docs-table-note">
                        Every <code>/v1</code> surface, scoped to the key's budget, model allowlist
                        and rpm/tpm window.
                      </td>
                    </tr>
                    <tr>
                      <th scope="row" className="docs-table-id">
                        <code>WIWI_MASTER_KEY</code>
                        <span className="docs-table-sub">admin</span>
                      </th>
                      <td className="docs-table-note">
                        <code>Authorization: Bearer</code>
                      </td>
                      <td className="docs-table-note">
                        <code>/admin/*</code> — keys, providers, model groups, prices, logs, backup.
                      </td>
                    </tr>
                    <tr>
                      <th scope="row" className="docs-table-id">
                        <code>wiwi_session</code>
                        <span className="docs-table-sub">browser session</span>
                      </th>
                      <td className="docs-table-note">
                        Cookie, set by <code>/auth/login</code>
                      </td>
                      <td className="docs-table-note">
                        <code>/v1/playground/completions</code> and <code>/auth/me</code>. The key
                        behind it is minted server-side and never leaves the process.
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="docs-sub">
                <h3 className="docs-h3">Claude Code</h3>
                <p className="docs-prose">
                  Anthropic clients send <code>x-api-key</code> and an{" "}
                  <code>anthropic-version</code>, and negotiate features through{" "}
                  <code>anthropic-beta</code> headers. Those headers are forwarded on an allowlist,
                  because a beta whose header is dropped becomes a hard 400 on a body field the
                  upstream then refuses.
                </p>
                <CodeBlock
                  label="bash"
                  code={`export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_AUTH_TOKEN=sk-wiwi-…
claude`}
                />
              </div>

              <Note label="Refusals are deliberate">
                <p>
                  A key over budget is <code>402</code>, not <code>429</code>: a cap does not clear
                  on retry, and <code>429</code> tells an SDK to back off and try again. Rate-limit
                  refusals are <code>429</code> with <code>Retry-After</code>.
                </p>
              </Note>
            </section>

            {/* 04 ───────────────────────────────────────────────────────── */}
            <section id="endpoints" className="docs-section">
              <SectionHead
                id="endpoints"
                num="04"
                title="Endpoints"
                lede="Every route the gateway serves, grouped by the dialect it speaks."
              />
              <EndpointList />
            </section>

            {/* 05 ───────────────────────────────────────────────────────── */}
            <section id="translation" className="docs-section">
              <SectionHead
                id="translation"
                num="05"
                title="Cross-provider"
                lede="Any inbound dialect against any outbound type, re-encoded on the way back."
              />
              <p className="docs-prose">
                Translation is not a matrix of pairwise converters — it is two halves that meet in
                the middle. Each provider type has exactly one adapter, and each adapter owns its
                vendor's quirks. A quirk lives there rather than in core, which is why one request
                shape can reach a vLLM-backed endpoint and a native Anthropic endpoint without
                either being special-cased upstream.
              </p>
              <ProviderTable />
              <div className="docs-sub">
                <h3 className="docs-h3">What survives the crossing</h3>
                <dl className="docs-defs">
                  {TRANSLATED.map((t) => (
                    <div key={t.term} className="docs-def">
                      <dt className="docs-def-term"><code>{t.term}</code></dt>
                      <dd className="docs-def-desc">{t.desc}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <Note label="Nothing is dropped silently">
                <p>
                  When an adapter has to rename or omit something it records a translation warning.
                  That reaches the request log row and the{" "}
                  <code>x-wiwi-translation-warnings</code> response header, so a surprising reply
                  can be explained from the response itself.
                </p>
              </Note>
            </section>

            {/* 06 ───────────────────────────────────────────────────────── */}
            <section id="streaming" className="docs-section">
              <SectionHead
                id="streaming"
                num="06"
                title="Streaming"
                lede="One delta taxonomy between the adapters and the encoders, so neither side defends against the other."
              />
              <p className="docs-prose">
                Adapters guarantee the sequence below; client encoders re-emit it in their own
                dialect and never re-check it. Two real streams are looser than a strict tree and
                the taxonomy absorbs both: parallel tool calls open as siblings on separate
                indices, and usage may arrive more than once.
              </p>
              <CodeBlock label="contract" lang="bash" code={CONTRACT} gutter={false} />
              <div className="docs-sub">
                <h3 className="docs-h3">The same reply, two dialects</h3>
                <TabbedCode
                  tabs={[
                    { label: "chat/completions", code: OPENAI_FRAMES },
                    { label: "messages", code: ANTHROPIC_FRAMES },
                  ]}
                />
              </div>
              <div className="docs-sub">
                <h3 className="docs-h3">Liveness and resumption</h3>
                <dl className="docs-defs">
                  {LIVENESS.map((l) => (
                    <div key={l.term} className="docs-def">
                      <dt className="docs-def-term"><code>{l.term}</code></dt>
                      <dd className="docs-def-desc">{l.desc}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <CodeBlock
                label="bash"
                code={`# resume a stream the connection dropped: ask for what you already have
curl -N http://localhost:4000/v1/chat/completions \\
  -H "Authorization: Bearer $OPENAI_API_KEY" \\
  -H "x-wiwi-stream-id: $STREAM_ID" \\
  -H "Last-Event-ID: 42" \\
  -H "Content-Type: application/json" \\
  -d '{"model": "gpt-4o", "stream": true,
       "messages": [{"role": "user", "content": "Keep going."}]}'`}
              />
            </section>

            {/* 07 ───────────────────────────────────────────────────────── */}
            <section id="config" className="docs-section">
              <SectionHead
                id="config"
                num="07"
                title="Configuration"
                lede="One YAML file, layered over by whatever the console writes to the database."
              />
              <p className="docs-prose">
                Providers carry a pool of keyed entries; model groups point at them. Any string may
                be <code>os.environ/NAME</code>, and a provider whose keys resolve empty is dropped
                at validation rather than failing on first use.
              </p>
              <CodeBlock
                label="yaml"
                code={`providers:
  - name: openai-main
    provider: openai
    keys:
      - {label: main,   key: os.environ/OPENAI_API_KEY,   weight: 3}
      - {label: backup, key: os.environ/OPENAI_API_KEY_2, weight: 1}

  - name: anthropic-main
    provider: anthropic
    keys:
      - {label: main, key: os.environ/ANTHROPIC_API_KEY}

model_list:
  - model_name: gpt-4o
    wiwi_params: {provider: openai-main, model: gpt-4o, weight: 2}
  - model_name: claude-sonnet
    wiwi_params: {provider: anthropic-main, model: claude-sonnet-4-5}

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  allowed_fails: 3
  cooldown_time: 30
  fallbacks:
    claude-sonnet: ["gpt-4o"]`}
              />
              <div className="docs-sub">
                <h3 className="docs-h3">Three things worth knowing</h3>
                <dl className="docs-defs">
                  <div className="docs-def">
                    <dt className="docs-def-term">Layering</dt>
                    <dd className="docs-def-desc">
                      YAML loads first; providers, keys, deployments, aliases and settings stored
                      through the console layer over it, skipping same-named YAML entries. Edit a
                      file-managed provider in the console and the file still wins.
                    </dd>
                  </div>
                  <div className="docs-def">
                    <dt className="docs-def-term">Opt-in semantics</dt>
                    <dd className="docs-def-desc">
                      Anything that changes behaviour defaults off: the response cache, the health
                      healer, spend batching, singleflight and prefix affinity.
                    </dd>
                  </div>
                  <div className="docs-def">
                    <dt className="docs-def-term">Database</dt>
                    <dd className="docs-def-desc">
                      SQLite by default; set <code>DATABASE_URL</code> for Postgres. The schema is
                      created inline at startup, so there are no migrations to run.
                    </dd>
                  </div>
                </dl>
              </div>
            </section>

            {/* 08 ───────────────────────────────────────────────────────── */}
            <section id="errors" className="docs-section">
              <SectionHead
                id="errors"
                num="08"
                title="Errors & limits"
                lede="Every refusal carries the caller's dialect shape, and says whether retrying could ever help."
              />
              <p className="docs-prose">
                OpenAI-shaped surfaces return <code>{'{ "error": { … } }'}</code>; the Anthropic
                surface returns <code>{'{ "type": "error", … }'}</code>. A validation failure is a{" "}
                <code>400</code>, never a <code>422</code>, because the SDKs in front of the
                gateway do not agree on what 422 means.
              </p>
              <div className="docs-table-wrap">
                <table className="docs-table docs-table-status">
                  <thead>
                    <tr>
                      <th scope="col">Status</th>
                      <th scope="col">Type</th>
                      <th scope="col">When</th>
                      <th scope="col">Failover</th>
                    </tr>
                  </thead>
                  <tbody>
                    {STATUSES.map((s) => (
                      <tr key={s.code}>
                        <td className="docs-status-code"><code>{s.code}</code></td>
                        <td className="docs-status-type"><code>{s.type}</code></td>
                        <td className="docs-table-note">{s.when}</td>
                        <td>
                          <span className="docs-status-retry" data-retried={s.retried}>
                            {s.retried ? "retried" : "final"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="docs-prose docs-prose-tight">
                The seven statuses marked retried are the router's retryable set — the same set
                that drives a key or a deployment into cooldown. Everything else goes back to the
                caller unchanged, with the upstream's own wording preserved wherever a client
                matches on it.
              </p>

              <div className="docs-sub">
                <h3 className="docs-h3">Limits you can set</h3>
                <dl className="docs-defs">
                  <div className="docs-def">
                    <dt className="docs-def-term">Per key</dt>
                    <dd className="docs-def-desc">
                      Budget cap, expiry, model allowlist and an rpm/tpm window. Windows are shared
                      across workers when Redis is configured, per process otherwise.
                    </dd>
                  </div>
                  <div className="docs-def">
                    <dt className="docs-def-term">Per deployment</dt>
                    <dd className="docs-def-desc">
                      <code>rpm</code>, <code>tpm</code> and <code>max_inflight</code>. A capped
                      deployment is skipped in favour of a sibling; when every candidate is capped
                      the request gets <code>429</code> with <code>Retry-After</code>.
                    </dd>
                  </div>
                  <div className="docs-def">
                    <dt className="docs-def-term">Gateway-wide</dt>
                    <dd className="docs-def-desc">
                      <code>global_rpm</code> and <code>global_tpm</code> apply across every key,
                      which is how one process protects an upstream account shared by many
                      callers.
                    </dd>
                  </div>
                </dl>
              </div>
            </section>

            <ClosingCta
              eyebrow="end of reference"
              title="Point a client at it and watch the translation happen."
              body="The playground runs the same pipeline as the API, against the same model groups, behind your browser session."
              primary={{ label: "Open the playground", to: "/playground" }}
              secondary={{ label: "Create an account", to: "/signup" }}
              footnote="Every route on this page is served by the same process."
              commands={[
                { title: "Point a client at the gateway", command: "export OPENAI_BASE_URL=http://localhost:4000/v1" },
                { title: "Authenticate with a virtual key", command: "export OPENAI_API_KEY=<wiwi-key>" },
                { title: "Confirm the routing table", command: "curl $OPENAI_BASE_URL/models" },
              ]}
            />
          </div>
        </div>
      </div>

      <BackToTop />
    </div>
  );
}

