// Login — the console's front door. The routing schematic uses the same
// AnimatedBeam primitive as the marketing site: one gateway, many providers.

import { useEffect, useRef, useState } from "react";
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
  const userRef = useRef<HTMLInputElement>(null);
  const passRef = useRef<HTMLInputElement>(null);
  const keyRef = useRef<HTMLInputElement>(null);
  const retryRef = useRef(false);

  // Land on the credential field, not the page chrome — but only on
  // mouse-capable devices: autofocus on touch pops the on-screen keyboard
  // over half the viewport before the user asked to type. `hover: hover`
  // rather than `pointer: fine` — some desktop engines report no pointer.
  useEffect(() => {
    if (window.matchMedia("(hover: hover)").matches) userRef.current?.focus();
  }, []);

  // A rejected sign-in selects the credential for a quick retype. This must
  // run after the busy=false render re-enables the inputs — a disabled field
  // silently drops .focus(), and a setTimeout races React's commit.
  useEffect(() => {
    if (busy || !retryRef.current) return;
    retryRef.current = false;
    const field = (mode === "user" ? passRef : keyRef).current;
    field?.focus();
    field?.select();
  }, [busy, mode]);

  // Beam refs (4 providers → hub) for the showcase and compact diagram.
  const showcaseRef = useRef<HTMLDivElement>(null);
  const showcaseHubRef = useRef<HTMLDivElement>(null);
  const showcaseNodeRefs = [
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
  ];

  function switchMode(next: "user" | "admin") {
    setMode(next);
    setError(null);
    // Continue where the operator was: into the first field of the new mode.
    (next === "user" ? userRef : keyRef).current?.focus();
  }

  // The form is noValidate so an empty submit gets the same inline alert
  // as a rejected credential, with focus sent to the field to fix —
  // native validation bubbles would float outside the card's design.
  function missingField(): { ref: React.RefObject<HTMLInputElement | null>; msg: string } | null {
    if (mode === "user") {
      if (!username.trim()) return { ref: userRef, msg: "Enter your username." };
      if (!password) return { ref: passRef, msg: "Enter your password." };
    } else if (!key.trim()) {
      return { ref: keyRef, msg: "Enter the gateway master key." };
    }
    return null;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const missing = missingField();
    if (missing) {
      setError(missing.msg);
      missing.ref.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "user") await login(username.trim(), password);
      else await loginWithMaster(key.trim());
      navigate("/console", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      retryRef.current = true;
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

        <main className="grid flex-1 items-center gap-5 py-4 lg:grid-cols-[1.02fr_0.98fr] lg:gap-12 lg:py-4">
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
            <h1 className="sr-only">Sign in to wiwi</h1>
            <div className="w-full max-w-[440px]">
              <div className="lg-login-card overflow-hidden rounded-[20px] border">
                {/* Card header */}
                <div className="lg-card-head border-b border-white/[0.06] px-5 py-4 sm:px-8 sm:py-5">
                  <div className="flex items-start gap-4">
                    <div className="lg-login-mark" aria-hidden="true">
                      <KeyRound className="h-5 w-5" strokeWidth={1.75} />
                    </div>
                    <div className="min-w-0 pt-0.5">
                      <p className="lg-eyebrow">Console access</p>
                      <h2 id="lg-title" className="mt-1 text-xl font-semibold tracking-tight text-zinc-50 sm:text-[1.35rem]">
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
                  className="lg-card-body p-5 sm:p-8"
                  onSubmit={submit}
                  aria-labelledby="lg-title"
                  noValidate
                >
                  {/* Mode choice — radios, not tabs: there is no tabpanel, the
                      form below simply swaps its credential fields. Native
                      inputs give arrow-key selection and correct semantics;
                      the visible label carries the 44px target. */}
                  <fieldset className="lg-seg">
                    <legend className="sr-only">Sign-in method</legend>
                    <div
                      className="lg-seg-indicator"
                      style={{ transform: mode === "admin" ? "translateX(calc(100% + 4px))" : "translateX(0)" }}
                      aria-hidden="true"
                    />
                    <label>
                      <input
                        type="radio"
                        name="lg-mode"
                        value="user"
                        className="sr-only"
                        checked={mode === "user"}
                        disabled={busy}
                        onChange={() => switchMode("user")}
                      />
                      <span className="lg-seg-option relative z-[1] inline-flex h-11 min-h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors">
                        <UserRound className="h-4 w-4" aria-hidden="true" />
                        <span>Account</span>
                      </span>
                    </label>
                    <label>
                      <input
                        type="radio"
                        name="lg-mode"
                        value="admin"
                        className="sr-only"
                        checked={mode === "admin"}
                        disabled={busy}
                        onChange={() => switchMode("admin")}
                      />
                      <span className="lg-seg-option relative z-[1] inline-flex h-11 min-h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors">
                        <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                        <span>Admin key</span>
                      </span>
                    </label>
                  </fieldset>

                  <div className="lg-card-form mt-5 space-y-4 sm:mt-6">
                    {mode === "user" ? (
                      <>
                        <div className="space-y-1.5">
                          <label htmlFor="lg-user" className="block text-[13px] font-medium text-zinc-300">Username</label>
                          <div className="group relative">
                            <UserRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500 transition-colors group-focus-within:text-zinc-300" aria-hidden="true" />
                            <input
                              id="lg-user"
                              ref={userRef}
                              className="admin-input h-12 w-full pl-10 text-[15px]"
                              autoComplete="username"
                              value={username}
                              onChange={(e) => { setUsername(e.target.value); setError(null); }}
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
                              ref={passRef}
                              className="admin-input h-12 w-full pl-10 pr-12 text-[15px]"
                              type={showPassword ? "text" : "password"}
                              autoComplete="current-password"
                              value={password}
                              onChange={(e) => { setPassword(e.target.value); setError(null); }}
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
                            ref={keyRef}
                            className="admin-input h-12 w-full pl-10 pr-12 font-mono text-[13px]"
                            type={showKey ? "text" : "password"}
                            autoComplete="off"
                            spellCheck={false}
                            value={key}
                            onChange={(e) => { setKey(e.target.value); setError(null); }}
                            placeholder="sk-…"
                            required
                            disabled={busy}
                            autoCapitalize="none"
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
                      aria-busy={busy}
                      className="group mt-1 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0b0d] disabled:cursor-not-allowed disabled:opacity-70"
                    >
                      {busy ? (
                        <><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Signing in…</>
                      ) : (
                        <>
                          {mode === "user" ? "Sign in" : "Sign in with master key"}
                          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                        </>
                      )}
                    </button>
                  </div>

                  <p className="lg-card-alt mt-5 border-t border-white/[0.06] pt-3 text-center text-[13px] text-zinc-400 sm:mt-6 sm:pt-4">
                    New to this gateway?{" "}
                    <Link
                      to="/signup"
                      className="inline-flex min-h-11 items-center font-medium text-zinc-200 underline decoration-zinc-600 underline-offset-4 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                    >
                      Create an account
                    </Link>
                  </p>
                </form>

                <div className="lg-card-footer border-t border-white/[0.06] bg-white/[0.015] px-5 py-3 sm:px-8 sm:py-4">
                  <div className="flex items-center justify-center gap-2 text-xs text-zinc-500">
                    <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-zinc-400" aria-hidden="true" />
                    <span>
                      {mode === "user"
                        ? "Sent only to your gateway — sign-in uses a secure session cookie."
                        : "The master key stays in this browser to authorize admin requests."}
                    </span>
                  </div>
                </div>
              </div>

            </div>
          </section>
        </main>

        <footer className="mt-auto flex flex-row items-center justify-between gap-2 border-t border-white/[0.05] pt-3 text-xs text-zinc-500">
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
