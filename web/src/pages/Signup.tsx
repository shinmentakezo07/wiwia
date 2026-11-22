// Signup — public username/password registration. Shares the Login page's
// split-panel layout: product framing + routing schematic on the left, the
// account card on the right. On success the auth context establishes the
// session cookie and lands the user in the console.

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
  UserPlus,
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

const USERNAME_RE = /^[a-zA-Z0-9_-]+$/;

const PROVIDERS = [
  { label: "OpenAI", Icon: OpenAIIcon, from: "#60a5fa", to: "#a5b4fc" },
  { label: "Anthropic", Icon: AnthropicIcon, from: "#a5b4fc", to: "#c4b5fd" },
  { label: "Gemini", Icon: GeminiIcon, from: "#8b5cf6", to: "#60a5fa" },
  { label: "Moonshot", Icon: MoonshotIcon, from: "#818cf8", to: "#c4b5fd" },
];

const SIGNALS = [
  "Free to start — bring your own provider keys",
  "Virtual keys, budgets & rate limits",
  "Cost and request visibility from day one",
];

export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const userRef = useRef<HTMLInputElement>(null);

  // Same rule as Login: autofocus only on hover-capable devices so touch
  // users don't get the keyboard popped over the page uninvited.
  useEffect(() => {
    if (window.matchMedia("(hover: hover)").matches) userRef.current?.focus();
  }, []);

  const u = username.trim();
  const p = password;
  const uLen = u.length;
  const pLen = p.length;
  const uBad = uLen > 0 && (uLen < 3 || uLen > 32 || !USERNAME_RE.test(u));
  const pBad = pLen > 0 && pLen < 8;
  const canSubmit = uLen >= 3 && uLen <= 32 && USERNAME_RE.test(u) && pLen >= 8 && !busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || busy) return;
    setBusy(true);
    setError(null);
    try {
      await signup(u, p);
      navigate("/console");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create your account.");
    } finally {
      setBusy(false);
    }
  };

  // Beam refs (4 providers → hub) for the showcase.
  const showcaseRef = useRef<HTMLDivElement>(null);
  const hubRef = useRef<HTMLDivElement>(null);
  const nodeRefs = [
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
    useRef<HTMLDivElement>(null),
  ];

  const node = (idx: number) => {
    const prov = PROVIDERS[idx];
    const Icon = prov.Icon;
    return (
      <div key={prov.label} ref={nodeRefs[idx]} className="relative z-10 flex flex-col items-center gap-1.5">
        <span className="lg-node-breathe pointer-events-none absolute inset-0 rounded-2xl" />
        <div
          className="flex items-center justify-center rounded-2xl border border-white/[0.1] bg-black/70 shadow-[0_12px_40px_-16px_rgba(0,0,0,0.9)] backdrop-blur-sm"
          style={{ width: "var(--lg-node)", height: "var(--lg-node)" }}
          title={prov.label}
        >
          <Icon className="h-6 w-6 text-zinc-100" />
        </div>
        <span className="text-[10px] font-medium tracking-wide text-zinc-400">{prov.label}</span>
      </div>
    );
  };

  return (
    <div data-admin className="lg-root min-h-[100dvh] text-white">
      <div className="lg-backdrop" aria-hidden="true" />

      <div className="relative mx-auto flex min-h-[100dvh] w-full max-w-6xl flex-col px-4 py-[var(--lg-pad-y)] sm:px-6 lg:px-8">
        <header className="flex items-center justify-between gap-3">
          <Link to="/" className="inline-flex min-h-11 items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">
            <img src="/wiwi-logo.png" alt="" className="h-7 w-7" aria-hidden="true" />
            <span className="text-sm font-semibold tracking-tight text-zinc-100">wiwi</span>
          </Link>
          <Link
            to="/login"
            className="inline-flex min-h-11 items-center rounded-lg px-3 text-sm font-medium text-zinc-300 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            Sign in
          </Link>
        </header>

        <main className="grid flex-1 items-center gap-5 py-4 lg:grid-cols-[1.02fr_0.98fr] lg:gap-12 lg:py-4">
          {/* Left: product framing + live schematic */}
          <section className="lg-slide-left hidden lg:flex lg:h-full lg:max-h-[min(720px,calc(100dvh-120px))] lg:flex-col lg:justify-center">
            <div className="max-w-xl">
              <p className="lg-eyebrow mb-4">Get started with wiwi</p>
              <h1
                className="font-semibold tracking-[-0.03em] text-zinc-50"
                style={{ fontSize: "clamp(30px,3.3vw,46px)", lineHeight: 1.08 }}
              >
                Your own gateway to
                <span className="bg-gradient-to-r from-indigo-200 via-white to-violet-200 bg-clip-text text-transparent"> every model.</span>
              </h1>
              <p className="mt-4 max-w-lg text-[15px] leading-relaxed text-zinc-400">
                Create an account in seconds, connect your providers, and route every request through one endpoint you control.
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
              <div ref={showcaseRef} className="relative h-full w-full" style={{ height: "100%" }}>
                <div className="lg-beam-grid pointer-events-none absolute inset-0 opacity-40" aria-hidden="true" />
                <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" aria-hidden="true">
                  <div className="lg-bloom h-40 w-40 rounded-full blur-3xl" />
                </div>

                <div className="absolute inset-x-0 top-0 flex items-start justify-between px-1 sm:px-4">
                  {node(0)}
                  {node(1)}
                </div>
                <div className="absolute inset-x-0 bottom-0 flex items-end justify-between px-1 sm:px-4">
                  {node(2)}
                  {node(3)}
                </div>

                <div
                  ref={hubRef}
                  className="absolute left-1/2 top-1/2 z-10 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1.5"
                >
                  <div
                    className="lg-hub-ring pointer-events-none absolute rounded-full border border-indigo-400/40"
                    style={{ width: "calc(var(--lg-hub) + 16px)", height: "calc(var(--lg-hub) + 16px)" }}
                    aria-hidden="true"
                  />
                  <div
                    className="relative flex items-center justify-center rounded-full border border-white/[0.14] bg-[#0b0b0f] shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_20px_60px_-20px_rgba(99,102,241,0.6)]"
                    style={{ width: "var(--lg-hub)", height: "var(--lg-hub)" }}
                  >
                    <img src="/wiwi-logo.png" alt="wiwi" className="h-7 w-7 sm:h-8 sm:w-8" />
                  </div>
                  <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-zinc-300">wiwi</span>
                </div>

                {PROVIDERS.map((prov, i) => (
                  <AnimatedBeam
                    key={prov.label}
                    containerRef={showcaseRef}
                    fromRef={nodeRefs[i]}
                    toRef={hubRef}
                    curvature={i < 2 ? 28 : -28}
                    gradientStart={prov.from}
                    gradientStop={prov.to}
                    pathColor="rgba(255,255,255,0.12)"
                    pathWidth={1.5}
                    pathOpacity={0.35}
                  />
                ))}
              </div>
            </div>
          </section>

          {/* Right: create account */}
          <section className="lg-rise flex items-center justify-center lg:justify-end">
            <h1 className="sr-only">Create a wiwi account</h1>
            <div className="w-full max-w-[440px]">
              <div className="lg-login-card overflow-hidden rounded-[20px] border">
                {/* Card header */}
                <div className="lg-card-head border-b border-white/[0.06] px-5 py-4 sm:px-8 sm:py-5">
                  <div className="flex items-start gap-4">
                    <div className="lg-login-mark" aria-hidden="true">
                      <UserPlus className="h-5 w-5" strokeWidth={1.75} />
                    </div>
                    <div className="min-w-0 pt-0.5">
                      <p className="lg-eyebrow">Create your account</p>
                      <h2 id="su-title" className="mt-1 text-xl font-semibold tracking-tight text-zinc-50 sm:text-[1.35rem]">
                        Start building
                      </h2>
                      <p className="mt-1 text-sm text-zinc-400">Your gateway, ready when you are.</p>
                    </div>
                  </div>
                </div>

                <form className="lg-card-body space-y-4 p-5 sm:p-8" onSubmit={submit} aria-labelledby="su-title">
                  <div className="space-y-1.5">
                    <label htmlFor="su-username" className="block text-[13px] font-medium text-zinc-300">Username</label>
                    <div className="group relative">
                      <UserRound
                        className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500"
                        aria-hidden="true"
                      />
                      <input
                        ref={userRef}
                        id="su-username"
                        type="text"
                        value={username}
                        autoComplete="username"
                        spellCheck={false}
                        disabled={busy}
                        placeholder="3–32 characters"
                        aria-invalid={uBad || undefined}
                        aria-describedby="su-username-hint"
                        onChange={(e) => setUsername(e.target.value)}
                        className="admin-input h-12 w-full pl-10 text-[15px]"
                      />
                    </div>
                    <p
                      id="su-username-hint"
                      className={`text-xs leading-relaxed ${uBad ? "text-red-300" : "text-zinc-500"}`}
                      aria-live="polite"
                    >
                      {uBad ? "Use 3–32 characters: " : "3–32 characters. "}
                      Letters, digits, <code className="font-mono">_</code> and <code className="font-mono">-</code>.
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <label htmlFor="su-password" className="block text-[13px] font-medium text-zinc-300">Password</label>
                    <div className="group relative">
                      <KeyRound
                        className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500"
                        aria-hidden="true"
                      />
                      <input
                        id="su-password"
                        type={showPassword ? "text" : "password"}
                        value={password}
                        autoComplete="new-password"
                        disabled={busy}
                        placeholder="At least 8 characters"
                        aria-invalid={pBad || undefined}
                        aria-describedby="su-password-hint"
                        onChange={(e) => setPassword(e.target.value)}
                        className="admin-input h-12 w-full pl-10 pr-12 text-[15px]"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword((v) => !v)}
                        aria-label={showPassword ? "Hide password" : "Show password"}
                        aria-pressed={showPassword}
                        className="absolute right-1 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                      >
                        {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    <p
                      id="su-password-hint"
                      className={`text-xs leading-relaxed ${pBad ? "text-red-300" : "text-zinc-500"}`}
                      aria-live="polite"
                    >
                      {pBad ? `Use at least 8 characters (${8 - pLen} more).` : "Use at least 8 characters."}
                    </p>
                  </div>

                  {error && (
                    <div
                      role="alert"
                      className="flex items-start gap-2.5 rounded-xl border border-red-500/20 bg-red-500/[0.06] px-3.5 py-3 text-[13px] leading-relaxed text-red-200"
                    >
                      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-300" aria-hidden="true" />
                      <span>{error}</span>
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={!canSubmit}
                    aria-busy={busy}
                    className="group mt-1 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b0b0d] disabled:cursor-not-allowed disabled:opacity-70"
                  >
                    {busy ? (
                      <><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Creating your account…</>
                    ) : (
                      <>Create account <ArrowRight className="h-4 w-4" aria-hidden="true" /></>
                    )}
                  </button>

                  <p className="lg-card-alt border-t border-white/[0.06] pt-4 text-center text-[13px] text-zinc-400">
                    Already have an account?{" "}
                    <Link
                      to="/login"
                      className="inline-flex min-h-11 items-center rounded font-medium text-zinc-200 underline decoration-zinc-600 underline-offset-4 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                    >
                      Sign in
                    </Link>
                  </p>
                </form>

                <div className="lg-card-footer border-t border-white/[0.05] bg-white/[0.015] px-6 py-3">
                  <div className="flex items-center justify-center gap-2 text-xs text-zinc-500">
                    <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span>Secured by your gateway. Credentials stay private.</span>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </main>

        <footer className="mt-auto flex items-center justify-between border-t border-white/[0.05] pt-4 text-xs text-zinc-500">
          <p>© {new Date().getFullYear()} wiwi</p>
          <div className="flex items-center gap-5">
            <Link to="/docs" className="inline-flex min-h-11 items-center rounded-md px-1 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Docs</Link>
            <Link to="/contact" className="inline-flex min-h-11 items-center rounded-md px-1 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Support</Link>
          </div>
        </footer>
      </div>
    </div>
  );
}
