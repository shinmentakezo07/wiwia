// Login — the console's front door. The routing schematic uses the same
// AnimatedBeam primitive as the marketing site: one gateway, many providers.

import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
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

const PROVIDERS = [
  { label: "OpenAI", Icon: OpenAIIcon, from: "#60a5fa", to: "#a5b4fc" },
  { label: "Anthropic", Icon: AnthropicIcon, from: "#a5b4fc", to: "#c4b5fd" },
  { label: "Gemini", Icon: GeminiIcon, from: "#8b5cf6", to: "#60a5fa" },
  { label: "Moonshot", Icon: MoonshotIcon, from: "#818cf8", to: "#c4b5fd" },
];

const SIGNALS = [
  "Virtual keys, budgets & rate limits",
  "Retries, fallbacks & cooldowns",
  "Cost and request visibility",
];

export function LoginPage() {
  const { login, loginWithMaster } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"user" | "admin">("user");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showKey, setShowKey] = useState(false);

  // Beam refs (4 providers → hub) for the showcase and compact diagram.
  const showcaseRef = useRef<HTMLDivElement>(null);
  const showcaseHubRef = useRef<HTMLDivElement>(null);
  const showcaseNodeRefs = [
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
  ];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "user") await login(username, password);
      else await loginWithMaster(key);
      navigate("/app", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  const providerNodes = (
    compact: boolean,
    container: React.RefObject<HTMLDivElement | null>,
    hub: React.RefObject<HTMLDivElement | null>,
    nodes: React.RefObject<HTMLDivElement | null>[],
  ) => (
    <div
      ref={container}
      className={`relative ${compact ? "mx-auto w-full max-w-[320px]" : "h-full w-full"}`}
      style={{ height: compact ? 200 : "100%" }}
    >
      <div className="lg-beam-grid pointer-events-none absolute inset-0 opacity-40" aria-hidden="true" />
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
        aria-hidden="true"
      >
        <div className="lg-bloom h-40 w-40 rounded-full blur-3xl" />
      </div>

      <div className="absolute inset-x-0 top-0 flex items-start justify-between px-1 sm:px-4">
        {PROVIDERS.slice(0, 2).map((p, i) => {
          const Icon = p.Icon;
          return (
            <div
              key={p.label}
              ref={nodes[i]}
              className="relative z-10 flex flex-col items-center gap-1.5"
            >
              <span className="lg-node-breathe pointer-events-none absolute inset-0 rounded-2xl" />
              <div
                className="flex items-center justify-center rounded-2xl border border-white/[0.1] bg-black/70 shadow-[0_12px_40px_-16px_rgba(0,0,0,0.9)] backdrop-blur-sm"
                style={{ width: "var(--lg-node)", height: "var(--lg-node)" }}
                title={p.label}
              >
                <Icon className={compact ? "h-5 w-5 text-zinc-100" : "h-6 w-6 text-zinc-100"} />
              </div>
              <span className="text-[10px] font-medium tracking-wide text-zinc-400">{p.label}</span>
            </div>
          );
        })}
      </div>

      <div className="absolute inset-x-0 bottom-0 flex items-end justify-between px-1 sm:px-4">
        {PROVIDERS.slice(2).map((p, i) => {
          const Icon = p.Icon;
          const idx = i + 2;
          return (
            <div
              key={p.label}
              ref={nodes[idx]}
              className="relative z-10 flex flex-col items-center gap-1.5"
            >
              <span className="lg-node-breathe pointer-events-none absolute inset-0 rounded-2xl" />
              <div
                className="flex items-center justify-center rounded-2xl border border-white/[0.1] bg-black/70 shadow-[0_12px_40px_-16px_rgba(0,0,0,0.9)] backdrop-blur-sm"
                style={{ width: "var(--lg-node)", height: "var(--lg-node)" }}
                title={p.label}
              >
                <Icon className={compact ? "h-5 w-5 text-zinc-100" : "h-6 w-6 text-zinc-100"} />
              </div>
              <span className="text-[10px] font-medium tracking-wide text-zinc-400">{p.label}</span>
            </div>
          );
        })}
      </div>

      <div
        ref={hub}
        className="absolute left-1/2 top-1/2 z-10 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1.5"
      >
        <div
          className="lg-hub-ring pointer-events-none absolute rounded-full border border-indigo-400/40"
          style={{
            width: "calc(var(--lg-hub) + 16px)",
            height: "calc(var(--lg-hub) + 16px)",
          }}
          aria-hidden="true"
        />
        <div
          className="relative flex items-center justify-center rounded-full border border-white/[0.14] bg-[#0b0b0f] shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_20px_60px_-20px_rgba(99,102,241,0.45)]"
          style={{ width: "var(--lg-hub)", height: "var(--lg-hub)" }}
        >
          <img src="/wiwi-mark.svg" alt="wiwi" className="h-7 w-7 sm:h-8 sm:w-8" />
        </div>
        <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-300">wiwi</span>
      </div>

      {PROVIDERS.map((p, i) => (
        <AnimatedBeam
          key={p.label}
          containerRef={container}
          fromRef={nodes[i]}
          toRef={hub}
          curvature={i < 2 ? 28 : -28}
          gradientStart={p.from}
          gradientStop={p.to}
          pathColor="rgba(255,255,255,0.12)"
          pathWidth={1.5}
          pathOpacity={0.35}
        />
      ))}
    </div>
  );

  return (
    <div data-admin className="lg-root min-h-[100dvh] text-white">
      <div className="lg-backdrop" aria-hidden="true" />

      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-6xl flex-col px-4 py-[var(--lg-pad-y)] sm:px-6 lg:px-8">
        <header className="flex items-center justify-between gap-3">
          <Link to="/" className="inline-flex min-h-11 items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">
            <img src="/wiwi-mark.svg" alt="" className="h-7 w-7" aria-hidden="true" />
            <span className="text-sm font-semibold tracking-tight text-zinc-100">wiwi</span>
          </Link>
          <Link
            to="/signup"
            className="inline-flex min-h-11 items-center rounded-lg px-3 text-sm font-medium text-zinc-300 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            Create account
          </Link>
        </header>

        <main className="grid flex-1 items-center gap-8 py-6 lg:grid-cols-[1.02fr_0.98fr] lg:gap-12 lg:py-4">
          {/* Left: product framing + live schematic */}
          <section className="lg-slide-left hidden lg:flex lg:h-full lg:max-h-[min(720px,calc(100dvh-120px))] lg:flex-col lg:justify-center">
            <div className="max-w-xl">
              <p className="lg-eyebrow mb-4">AI gateway console</p>
              <h1
                className="font-semibold tracking-[-0.03em] text-zinc-50"
                style={{ fontSize: "clamp(30px,3.3vw,46px)", lineHeight: 1.08 }}
              >
                One gateway for every
                <span className="bg-gradient-to-r from-indigo-200 via-white to-violet-200 bg-clip-text text-transparent"> model and dialect.</span>
              </h1>
              <p className="mt-4 max-w-lg text-[15px] leading-relaxed text-zinc-400">
                Route requests across providers, manage keys and budgets, and keep usage, cost, and reliability visible in one place.
              </p>
              <ul className="mt-7 space-y-3" aria-label="Highlights">
                {SIGNALS.map((item) => (
                  <li key={item} className="flex items-center gap-3 text-sm text-zinc-300">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-indigo-300/90" aria-hidden="true" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="relative mt-8 flex min-h-0 flex-1 items-stretch" style={{ maxHeight: "min(360px, 40vh)" }}>
              {providerNodes(false, showcaseRef, showcaseHubRef, showcaseNodeRefs)}
            </div>
          </section>

          {/* Right: sign-in */}
          <section className="lg-rise flex items-center justify-center lg:justify-end">
            <div className="w-full max-w-[440px]">
              <div className="mb-6 lg:hidden">
                <p className="lg-eyebrow mb-2">AI gateway console</p>
                <h1 className="text-[1.75rem] font-semibold leading-tight tracking-tight text-zinc-50">Sign in</h1>
                <p className="mt-2 text-sm leading-relaxed text-zinc-400">Access your gateway dashboard, keys, and usage.</p>
              </div>

              <div className="lg-login-card overflow-hidden rounded-[20px] border">
                {/* Card header */}
                <div className="border-b border-white/[0.06] px-6 py-5 sm:px-8">
                  <div className="flex items-start gap-4">
                    <div className="lg-login-mark" aria-hidden="true">
                      <KeyRound className="h-5 w-5" strokeWidth={1.75} />
                    </div>
                    <div className="min-w-0 pt-0.5">
                      <p className="lg-eyebrow">Console access</p>
                      <h2 className="mt-1 text-xl font-semibold tracking-tight text-zinc-50 sm:text-[1.35rem]">
                        {mode === "user" ? "Welcome back" : "Admin sign-in"}
                      </h2>
                      <p className="mt-1 text-sm text-zinc-400">
                        {mode === "user"
                          ? "Use your account to manage models, keys, and spend."
                          : "Authenticate with the gateway master key."}
                      </p>
                    </div>
                  </div>
                </div>

                <form
                  className="lg-card-body p-6 sm:p-8"
                  onSubmit={submit}
                  aria-label="Sign in"
                >
                  {/* Mode segmented control */}
                  <div className="lg-seg" role="tablist" aria-label="Sign-in method">
                    <div
                      className="lg-seg-indicator"
                      style={{ transform: mode === "admin" ? "translateX(calc(100% + 4px))" : "translateX(0)" }}
                      aria-hidden="true"
                    />
                    <button
                      type="button"
                      role="tab"
                      aria-selected={mode === "user"}
                      onClick={() => { setMode("user"); setError(null); }}
                      className="relative z-[1] inline-flex h-11 min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                    >
                      <UserRound className="h-4 w-4" aria-hidden="true" />
                      <span>Account</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={mode === "admin"}
                      onClick={() => { setMode("admin"); setError(null); }}
                      className="relative z-[1] inline-flex h-11 min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                    >
                      <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                      <span>Admin key</span>
                    </button>
                  </div>

                  <div className="mt-6 space-y-4">
                    {mode === "user" ? (
                      <>
                        <div className="space-y-1.5">
                          <label htmlFor="lg-user" className="block text-[13px] font-medium text-zinc-300">Username</label>
                          <div className="group relative">
                            <UserRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500 transition-colors group-focus-within:text-zinc-300" aria-hidden="true" />
                            <input
                              id="lg-user"
                              className="admin-input h-12 w-full pl-10 text-[15px]"
                              autoComplete="username"
                              value={username}
                              onChange={(e) => setUsername(e.target.value)}
                              placeholder="e.g. alex"
                              required
                              disabled={busy}
                            />
                          </div>
                        </div>
                        <div className="space-y-1.5">
                          <label htmlFor="lg-pass" className="block text-[13px] font-medium text-zinc-300">Password</label>
                          <div className="group relative">
                            <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500 transition-colors group-focus-within:text-zinc-300" aria-hidden="true" />
                            <input
                              id="lg-pass"
                              className="admin-input h-12 w-full pl-10 pr-12 text-[15px]"
                              type={showPassword ? "text" : "password"}
                              autoComplete="current-password"
                              value={password}
                              onChange={(e) => setPassword(e.target.value)}
                              placeholder="••••••••"
                              required
                              disabled={busy}
                            />
                            <button
                              type="button"
                              onClick={() => setShowPassword((s) => !s)}
                              disabled={busy}
                              className="absolute right-1.5 top-1/2 inline-flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:opacity-50"
                              aria-label={showPassword ? "Hide password" : "Show password"}
                              aria-pressed={showPassword}
                            >
                              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                            </button>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="space-y-1.5">
                        <label htmlFor="lg-key" className="block text-[13px] font-medium text-zinc-300">Master key</label>
                        <div className="group relative">
                          <ShieldCheck className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500 transition-colors group-focus-within:text-zinc-300" aria-hidden="true" />
                          <input
                            id="lg-key"
                            className="admin-input h-12 w-full pl-10 pr-12 font-mono text-[13px]"
                            type={showKey ? "text" : "password"}
                            autoComplete="off"
                            spellCheck={false}
                            value={key}
                            onChange={(e) => setKey(e.target.value)}
                            placeholder="sk-…"
                            required
                            disabled={busy}
                          />
                          <button
                            type="button"
                            onClick={() => setShowKey((s) => !s)}
                            disabled={busy}
                            className="absolute right-1.5 top-1/2 inline-flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:opacity-50"
                            aria-label={showKey ? "Hide master key" : "Show master key"}
                            aria-pressed={showKey}
                          >
                            {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </button>
                        </div>
                        <p className="pt-1 text-xs leading-relaxed text-zinc-500">
                          Full administrative access. Use a personal account for day-to-day work.
                        </p>
                      </div>
                    )}

                    {error && (
                      <div
                        className="flex items-start gap-2.5 rounded-xl border border-red-500/25 bg-red-500/[0.08] px-3.5 py-3 text-sm text-red-200"
                        role="alert"
                      >
                        <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-300" aria-hidden="true" />
                        <span>{error}</span>
                      </div>
                    )}

                    <button
                      type="submit"
                      disabled={busy}
                      className="group mt-1 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0b0d] disabled:cursor-not-allowed disabled:opacity-70"
                    >
                      {busy ? (
                        <><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Signing in…</>
                      ) : (
                        <>
                          {mode === "user" ? "Sign in" : "Continue with master key"}
                          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                        </>
                      )}
                    </button>
                  </div>

                  <div className="mt-6 flex items-center gap-3" aria-hidden="true">
                    <div className="h-px flex-1 bg-white/[0.07]" />
                    <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">or</span>
                    <div className="h-px flex-1 bg-white/[0.07]" />
                  </div>

                  <div className="mt-4 text-center text-sm text-zinc-400">
                    {mode === "user" ? (
                      <button
                        type="button"
                        onClick={() => { setMode("admin"); setError(null); }}
                        className="inline-flex min-h-11 items-center justify-center rounded-lg px-3 font-medium text-zinc-300 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                      >
                        Continue with master key
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => { setMode("user"); setError(null); }}
                        className="inline-flex min-h-11 items-center justify-center rounded-lg px-3 font-medium text-zinc-300 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                      >
                        Use account sign-in instead
                      </button>
                    )}
                  </div>
                </form>

                <div className="lg-card-footer border-t border-white/[0.06] bg-white/[0.015] px-6 py-4 sm:px-8">
                  <div className="flex items-center justify-center gap-2 text-xs text-zinc-500">
                    <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-zinc-400" aria-hidden="true" />
                    <span>Credentials are sent to your gateway and never stored in the browser.</span>
                  </div>
                </div>
              </div>

            </div>
          </section>
        </main>

        <footer className="mt-auto flex flex-col items-center justify-between gap-3 border-t border-white/[0.05] pt-4 text-xs text-zinc-500 sm:flex-row">
          <p>© {new Date().getFullYear()} wiwi</p>
          <div className="flex items-center gap-2 sm:gap-4">
            <Link to="/docs" className="inline-flex min-h-11 items-center rounded-md px-2 transition-colors hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Documentation</Link>
            <Link to="/contact" className="inline-flex min-h-11 items-center rounded-md px-2 transition-colors hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Support</Link>
          </div>
        </footer>
      </div>
    </div>
  );
}
