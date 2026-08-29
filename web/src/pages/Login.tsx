// Login — the console's front door.
//
// Split-panel composition: the left half is a *live* hub-and-spoke schematic
// drawn with real AnimatedBeam paths between DOM nodes (your app → wiwi →
// providers), the right half is the credential form. The beams are the same
// primitive the marketing site uses, so the diagram is honest about what the
// gateway actually does instead of being decorative wallpaper.

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  ShieldCheck,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import { useAuth } from "@/api/auth";
import {
  AnimatedBeam,
  AnthropicIcon,
  GeminiIcon,
  MoonshotIcon,
  OpenAIIcon,
} from "@/components/AnimatedBeam";

// ── showcase: live beam graph ────────────────────────────────────────────────

const INBOUND = [
  { name: "chat", note: "OpenAI Chat" },
  { name: "responses", note: "Codex CLI" },
  { name: "messages", note: "Claude Code" },
];

const PROVIDERS = [
  { label: "OpenAI", Icon: OpenAIIcon, from: "#3b82f6", to: "#10b981" },
  { label: "Anthropic", Icon: AnthropicIcon, from: "#f59e0b", to: "#ef4444" },
  { label: "Gemini", Icon: GeminiIcon, from: "#22c55e", to: "#3b82f6" },
  { label: "Moonshot", Icon: MoonshotIcon, from: "#ec4899", to: "#f59e0b" },
];

/** One end node of the diagram — a glass puck that holds an icon. */
function Node({
  ref,
  children,
  size = "md",
  active = false,
}: {
  ref: React.RefObject<HTMLDivElement | null>;
  children?: React.ReactNode;
  size?: "md" | "lg";
  active?: boolean;
}) {
  const dim = size === "lg" ? "h-[var(--lg-hub)] w-[var(--lg-hub)]" : "h-[var(--lg-node)] w-[var(--lg-node)]";
  return (
    <div
      ref={ref}
      className={`relative z-10 flex ${dim} shrink-0 items-center justify-center rounded-full border bg-white/[0.02] backdrop-blur-sm transition-all duration-300 ${
        active
          ? "border-blue-400/30 shadow-[0_0_36px_-6px_rgba(59,130,246,0.55)]"
          : "border-white/[0.07] shadow-[0_0_24px_-10px_rgba(0,0,0,0.8)] hover:border-white/[0.16]"
      }`}
    >
      {children}
    </div>
  );
}

/** Live hub-and-spoke graph. `minH` lets the call site floor the diagram so
 *  the in-card variant still reads on short-but-wide screens. */
function Showcase({ minH = 0 }: { minH?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const clientRef = useRef<HTMLDivElement>(null);
  const hubRef = useRef<HTMLDivElement>(null);
  const providerRefs = [
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
  ];

  return (
    <div
      ref={containerRef}
      className="relative w-full flex-1"
      style={{ minHeight: minH || undefined }}
    >
      {/* rotating conic bloom behind the hub */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
        <div
          className="lg-bloom h-[clamp(210px,30vh,300px)] w-[clamp(210px,30vh,300px)] rounded-full blur-2xl"
          aria-hidden="true"
        />
      </div>

      {/* client node */}
      <div className="absolute left-0 top-1/2 z-10 -translate-y-1/2">
        <Node ref={clientRef}>
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-blue-200"
            aria-hidden="true"
          >
            <rect width="20" height="14" x="2" y="3" rx="2" />
            <line x1="8" x2="16" y1="21" y2="21" />
            <line x1="12" x2="12" y1="17" y2="21" />
          </svg>
        </Node>
        <div className="mt-2.5 text-center">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--admin-text-muted)]">
            Your app
          </div>
        </div>
      </div>

      {/* hub node */}
      <div className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2">
        <div className="relative">
          <span
            className="lg-hub-ring pointer-events-none absolute inset-0 rounded-full border border-blue-400/30"
            aria-hidden="true"
          />
          <span
            className="lg-node-breathe pointer-events-none absolute -inset-3 rounded-full bg-[radial-gradient(circle,rgba(59,130,246,0.22),transparent_70%)] blur-md"
            aria-hidden="true"
          />
          <Node ref={hubRef} size="lg" active>
            <img src="/wiwi-logo.png" alt="wiwi" className="h-10 w-10 rounded-[10px] object-cover" />
          </Node>
        </div>
        <div className="mt-3 text-center">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-300/80">
            wiwi gateway
          </div>
        </div>
      </div>

      {/* provider nodes */}
      <div className="absolute right-0 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-4">
        {PROVIDERS.map((p, i) => (
          <div key={p.label} className="flex items-center justify-end gap-3">
            <span className="font-mono text-[10px] text-[var(--admin-text-dim)]">{p.label}</span>
            <Node ref={providerRefs[i]}>
              <p.Icon className="h-6 w-6 object-contain" />
            </Node>
          </div>
        ))}
      </div>

      {/* client → hub */}
      <AnimatedBeam
        containerRef={containerRef}
        fromRef={clientRef}
        toRef={hubRef}
        pathWidth={2}
        pathOpacity={0.35}
        gradientStart="#3b82f6"
        gradientStop="#8b5cf6"
        duration={3}
      />
      {/* hub → each provider, on staggered delays so packets leave in sequence */}
      {providerRefs.map((ref, i) => (
        <AnimatedBeam
          key={PROVIDERS[i].label}
          containerRef={containerRef}
          fromRef={hubRef}
          toRef={ref}
          curvature={(i - 1.5) * 18}
          delay={i * 0.45}
          pathWidth={2}
          pathOpacity={0.35}
          gradientStart={PROVIDERS[i].from}
          gradientStop={PROVIDERS[i].to}
          duration={3.2 + i * 0.35}
        />
      ))}
    </div>
  );
}

// ── page ─────────────────────────────────────────────────────────────────────

export function LoginPage() {
  const { login, loginWithMaster } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"master" | "username">("master");
  const [key, setKey] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  const masterReady = !!key.trim();
  const usernameReady = username.trim().length >= 3 && password.length >= 8;
  const canSubmit = (mode === "master" ? masterReady : usernameReady) && !busy;

  // Report the cursor position into CSS custom props so the form panel can
  // carry a soft light that follows the pointer.
  useEffect(() => {
    const el = formRef.current;
    if (!el) return;
    const onMove = (e: MouseEvent) => {
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${e.clientX - r.left}px`);
      el.style.setProperty("--my", `${e.clientY - r.top}px`);
    };
    el.addEventListener("mousemove", onMove);
    return () => el.removeEventListener("mousemove", onMove);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "master") {
        await loginWithMaster(key.trim());
      } else {
        await login(username.trim(), password);
      }
      navigate("/console");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-admin
      className="lg-root relative flex h-[100dvh] items-stretch overflow-hidden bg-black"
    >
      {/* ═══ ambient backdrop: hero-style lighting over pure black ═══ */}
      <div className="pointer-events-none fixed inset-0" style={{ zIndex: 0 }}>
        {/* rotated white light streaks, screened over the black base */}
        <div
          aria-hidden="true"
          className="lg-streaks absolute inset-0 hidden lg:block"
        >
          <div className="lg-streak-1" />
          <div className="lg-streak-2" />
        </div>

        {/* grid wash — keeps the beam graph feeling like it sits on a surface */}
        <div className="lg-beam-grid absolute inset-0 opacity-40" />

        {/* color wash, kept faint so black stays black */}
        <div
          className="wiwi-aurora absolute -left-40 top-0 h-[520px] w-[520px] rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(59,130,246,0.07) 0%, transparent 65%)",
            animationDelay: "0s",
          }}
        />
        <div
          className="wiwi-aurora absolute -bottom-40 -right-32 h-[460px] w-[460px] rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 65%)",
            animationDelay: "-8s",
          }}
        />

        {/* bottom vignette: edges fall off to pure black */}
        <div className="lg-vignette absolute inset-0" />
      </div>

      {/* ═══ two-panel shell ═══ */}
      <div
        className="relative z-10 mx-auto flex w-full max-w-6xl items-stretch px-4 sm:px-6"
        style={{ paddingTop: "var(--lg-pad-y)", paddingBottom: "var(--lg-pad-y)" }}
      >
        {/* ── left: showcase ── */}
        <aside className="lg-slide-left relative hidden min-h-0 flex-1 flex-col lg:flex">
          <span className="lg-corner lg-corner-tl" aria-hidden="true" />
          <span className="lg-corner lg-corner-br" aria-hidden="true" />

          <div className="mb-[var(--lg-block)]">
            <span className="admin-live-badge">
              <span className="admin-pulse-dot" aria-hidden="true" />
              routing live
            </span>
          </div>

          <h2
            className="max-w-md font-semibold leading-[1.12] tracking-[-0.02em] text-[var(--admin-text)]"
            style={{ fontSize: "var(--lg-title)" }}
          >
            One gateway.
            <br />
            <span className="bg-gradient-to-r from-blue-400 via-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              Every dialect, every provider.
            </span>
          </h2>
          <p
            className="mt-[var(--lg-gap)] max-w-[26rem] leading-relaxed text-[var(--admin-text-muted)]"
            style={{ fontSize: "var(--lg-body)" }}
          >
            Three inbound dialects decode into one canonical form, then re-encode for whichever
            upstream serves the request. No pairwise converters, no per-provider branching in the
            core.
          </p>

          {/* inbound dialect chips */}
          <div className="mt-[var(--lg-gap)] flex flex-wrap gap-2">
            {INBOUND.map((d) => (
              <span
                key={d.name}
                className="inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.015] py-1.5 pl-3 pr-2.5"
              >
                <code className="font-mono text-[11.5px] text-blue-300">{d.name}</code>
                <span className="text-[10.5px] text-[var(--admin-text-dim)]">{d.note}</span>
              </span>
            ))}
          </div>

          {/* absorbs whatever vertical space the block above leaves over */}
          <div className="mt-[var(--lg-block)] flex min-h-0 flex-1 flex-col">
            <Showcase />
          </div>
        </aside>

        {/* divider */}
        <div className="mx-10 hidden w-px self-stretch lg:block">
          <div className="lg-divider h-full w-px" />
        </div>

        {/* ── right: credential form ── */}
        <div className="flex min-h-0 w-full max-w-[400px] flex-col items-center justify-center lg:mx-0 lg:ml-auto">
          <div className="wiwi-conic-border relative flex max-h-full w-full flex-col rounded-2xl">
            <div className="wiwi-glass-card relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl">
              <span className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px" />

              {/* brand header */}
              <div
                className="flex shrink-0 items-center gap-3.5 border-b border-[var(--admin-border)] px-7"
                style={{ paddingTop: "var(--lg-card-y)", paddingBottom: "var(--lg-card-y)" }}
              >
                <img
                  src="/wiwi-logo.png"
                  alt="wiwi"
                  className="h-10 w-10 shrink-0 rounded-[12px] object-cover shadow-lg shadow-brand-600/20 ring-1 ring-white/[0.06] ring-inset"
                />
                <div className="min-w-0">
                  <div className="flex items-baseline gap-2">
                    <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                      wiwi
                    </h1>
                    <span className="admin-badge admin-badge-blue !px-1.5 !py-0 !text-[8px]">
                      admin
                    </span>
                  </div>
                  <span
                    className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em]"
                    style={{ color: "rgba(59, 130, 246, 0.5)" }}
                  >
                    Unified LLM Gateway
                  </span>
                </div>
              </div>

              {/* Below lg the left panel is hidden, so the diagram moves into the
                  card. Hidden entirely on short viewports (see .lg-showcase-sm). */}
              <div className="lg-showcase-sm flex shrink-0 flex-col border-b border-[var(--admin-border)] px-6 py-5">
                <p className="admin-label mb-3 text-center">hub-and-spoke translation</p>
                <Showcase minH={300} />
              </div>

              {/* segmented mode toggle */}
              <div
                className="shrink-0 px-7"
                style={{ paddingTop: "var(--lg-card-y)" }}
              >
                <div className="lg-seg" role="tablist" aria-label="Sign-in method">
                  <span
                    className="lg-seg-indicator"
                    style={{
                      transform:
                        mode === "master"
                          ? "translateX(0)"
                          : "translateX(calc(100% + 2px))",
                    }}
                    aria-hidden="true"
                  />
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mode === "master"}
                    onClick={() => {
                      setMode("master");
                      setError(null);
                    }}
                    className={`relative z-10 flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
                      mode === "master"
                        ? "text-blue-200"
                        : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                    }`}
                  >
                    <KeyRound size={13} aria-hidden="true" />
                    Master key
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={mode === "username"}
                    onClick={() => {
                      setMode("username");
                      setError(null);
                    }}
                    className={`relative z-10 flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
                      mode === "username"
                        ? "text-blue-200"
                        : "text-[var(--admin-text-muted)] hover:text-[var(--admin-text)]"
                    }`}
                  >
                    <UserRound size={13} aria-hidden="true" />
                    Username
                  </button>
                </div>
              </div>

              {/* form — only region allowed to scroll, and only when the
                  viewport is too short to hold the whole card */}
              <form
                ref={formRef}
                onSubmit={submit}
                className="lg-spotlight lg-card-body relative flex min-h-0 flex-1 flex-col gap-[var(--lg-gap)] px-7"
                style={{ paddingTop: "var(--lg-card-y)", paddingBottom: "var(--lg-card-y)" }}
              >
                {mode === "master" ? (
                  <label htmlFor="master-key" className="block">
                    <span className="admin-label mb-2 block">Master Key</span>
                    <span className="lg-field block">
                      <KeyRound
                        size={14}
                        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
                        aria-hidden="true"
                      />
                      <input
                        id="master-key"
                        type={showKey ? "text" : "password"}
                        value={key}
                        autoFocus
                        autoComplete="off"
                        spellCheck={false}
                        disabled={busy}
                        placeholder="sk-wiwi-master-…"
                        onChange={(e) => setKey(e.target.value)}
                        className="admin-input h-[var(--lg-control-h)] pl-9 pr-10 font-mono text-sm disabled:opacity-60"
                      />
                      <button
                        type="button"
                        onClick={() => setShowKey((v) => !v)}
                        aria-label={showKey ? "Hide key" : "Show key"}
                        className="absolute right-1.5 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-[var(--admin-text-dim)] transition-colors hover:bg-white/[0.03] hover:text-[var(--admin-text-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40"
                      >
                        {showKey ? <EyeOff size={13} /> : <Eye size={13} />}
                      </button>
                    </span>
                    <span className="mt-1.5 block text-[11px] text-[var(--admin-text-dim)]">
                      The <code className="font-mono">master_key</code> from your wiwi.yaml.
                    </span>
                  </label>
                ) : (
                  <>
                    <label htmlFor="li-username" className="block">
                      <span className="admin-label mb-2 block">Username</span>
                      <span className="lg-field block">
                        <UserRound
                          size={14}
                          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
                          aria-hidden="true"
                        />
                        <input
                          id="li-username"
                          type="text"
                          value={username}
                          autoFocus
                          autoComplete="username"
                          spellCheck={false}
                          disabled={busy}
                          placeholder="your username"
                          onChange={(e) => setUsername(e.target.value)}
                          className="admin-input h-[var(--lg-control-h)] pl-9 pr-3 text-sm disabled:opacity-60"
                        />
                      </span>
                    </label>
                    <label htmlFor="li-password" className="block">
                      <span className="admin-label mb-2 block">Password</span>
                      <span className="lg-field block">
                        <input
                          id="li-password"
                          type="password"
                          value={password}
                          autoComplete="current-password"
                          disabled={busy}
                          placeholder="••••••••"
                          onChange={(e) => setPassword(e.target.value)}
                          className="admin-input h-[var(--lg-control-h)] px-3 text-sm disabled:opacity-60"
                        />
                      </span>
                    </label>
                  </>
                )}

                {error && (
                  <p
                    key={error}
                    role="alert"
                    className="wiwi-shake flex items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/[0.08] px-3 py-2 text-xs leading-relaxed text-red-400"
                  >
                    <TriangleAlert size={13} className="mt-px shrink-0" aria-hidden="true" />
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="wiwi-shimmer group inline-flex w-full shrink-0 items-center justify-center gap-2 rounded-[8px] bg-gradient-to-b from-brand-500 to-brand-700 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-[transform,filter,box-shadow] duration-150 hover:brightness-110 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/70 disabled:pointer-events-none disabled:opacity-50 disabled:saturate-50"
                  style={{ height: "var(--lg-control-h)" }}
                >
                  {busy ? (
                    <>
                      <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                      Verifying…
                    </>
                  ) : (
                    <>
                      Sign in
                      <ArrowRight
                        size={15}
                        className="transition-transform duration-150 group-hover:translate-x-0.5"
                        aria-hidden="true"
                      />
                    </>
                  )}
                </button>

                <p className="shrink-0 text-center text-[12px] text-[var(--admin-text-dim)]">
                  No account?{" "}
                  <Link to="/signup" className="text-blue-300 hover:text-blue-200">
                    Sign up
                  </Link>
                </p>
              </form>

              {/* trust footer — pinned, never scrolls away */}
              <div
                className="lg-card-footer flex shrink-0 items-center gap-2 border-t border-[var(--admin-border)] px-7 text-[11px] text-[var(--admin-text-dim)]"
                style={{ paddingTop: "var(--lg-card-y)", paddingBottom: "var(--lg-card-y)" }}
              >
                <ShieldCheck size={12} className="shrink-0" aria-hidden="true" />
                {mode === "master"
                  ? "Key stays in this browser — checked once against your gateway."
                  : "Session held in a server-side cookie. We never see your password."}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
