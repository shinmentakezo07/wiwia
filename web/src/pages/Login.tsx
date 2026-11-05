// Login — the console's front door. The routing schematic uses the same
// AnimatedBeam primitive as the marketing site: one gateway, many providers.

import { useRef, useState } from "react";
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

const PROVIDERS = [
  { label: "OpenAI", Icon: OpenAIIcon, from: "#60a5fa", to: "#a5b4fc" },
  { label: "Anthropic", Icon: AnthropicIcon, from: "#a5b4fc", to: "#c4b5fd" },
  { label: "Gemini", Icon: GeminiIcon, from: "#8b5cf6", to: "#60a5fa" },
  { label: "Moonshot", Icon: MoonshotIcon, from: "#a5b4fc", to: "#818cf8" },
];

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
  return (
    <div
      ref={ref}
      className={`relative z-10 flex shrink-0 items-center justify-center rounded-xl border bg-[#111114] ${
        size === "lg" ? "h-16 w-16 rounded-2xl" : "h-11 w-11"
      } ${active ? "border-indigo-400/40 bg-[#141323]" : "border-white/10"}`}
    >
      {children}
    </div>
  );
}

function Showcase() {
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
    <div ref={containerRef} className="relative h-[264px] w-full" aria-hidden="true">
      <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
        <div className="lg-bloom h-64 w-64 rounded-full opacity-70 blur-2xl" />
      </div>

      <div className="absolute left-0 top-1/2 z-10 -translate-y-1/2">
        <Node ref={clientRef}>
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-zinc-300"
            aria-hidden="true"
          >
            <rect width="20" height="14" x="2" y="3" rx="2" />
            <path d="M8 21h8M12 17v4" />
          </svg>
        </Node>
        <p className="mt-3 text-center font-mono text-[11px] text-zinc-500">Your app</p>
      </div>

      <div className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2">
        <div className="relative">
          <span
            className="lg-hub-ring pointer-events-none absolute -inset-2 rounded-2xl border border-indigo-400/20"
            aria-hidden="true"
          />
          <Node ref={hubRef} size="lg" active>
            <img src="/wiwi-logo.png" alt="" className="h-11 w-11 rounded-xl object-cover" />
          </Node>
        </div>
        <p className="mt-3 text-center text-[11px] font-medium tracking-wide text-indigo-300">
          wiwi gateway
        </p>
      </div>

      <div className="absolute right-0 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-3.5">
        {PROVIDERS.map((provider, i) => (
          <div key={provider.label} className="flex items-center justify-end gap-3">
            <span className="text-xs text-zinc-400">{provider.label}</span>
            <Node ref={providerRefs[i]}>
              <provider.Icon className="h-6 w-6 object-contain" />
            </Node>
          </div>
        ))}
      </div>

      <AnimatedBeam
        containerRef={containerRef}
        fromRef={clientRef}
        toRef={hubRef}
        pathWidth={1.5}
        pathOpacity={0.22}
        gradientStart="#818cf8"
        gradientStop="#a78bfa"
        duration={4}
      />
      {providerRefs.map((ref, i) => (
        <AnimatedBeam
          key={PROVIDERS[i].label}
          containerRef={containerRef}
          fromRef={hubRef}
          toRef={ref}
          curvature={(i - 1.5) * 18}
          delay={i * 0.45}
          pathWidth={1.5}
          pathOpacity={0.24}
          gradientStart={PROVIDERS[i].from}
          gradientStop={PROVIDERS[i].to}
          duration={4 + i * 0.35}
        />
      ))}
    </div>
  );
}

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
  const [showPassword, setShowPassword] = useState(false);

  const canSubmit =
    (mode === "master"
      ? !!key.trim()
      : username.trim().length >= 3 && password.length >= 8) && !busy;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "master") await loginWithMaster(key.trim());
      else await login(username.trim(), password);
      navigate("/console");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to sign in. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-admin className="lg-root min-h-[100dvh] bg-black text-white">
      <div className="lg-backdrop" aria-hidden="true" />
      <div className="relative z-10 mx-auto flex min-h-[100dvh] w-full max-w-[1440px] flex-col px-5 py-5 sm:px-8 lg:px-12">
        <header className="flex shrink-0 items-center justify-between gap-4">
          <Link
            to="/"
            className="inline-flex min-h-11 items-center gap-3 rounded-lg pr-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
            aria-label="wiwi home"
          >
            <img src="/wiwi-logo.png" alt="" className="h-9 w-9 rounded-[10px]" />
            <span className="text-[19px] font-semibold tracking-[-0.03em] text-white">wiwi</span>
            <span className="hidden h-4 w-px bg-white/15 sm:block" aria-hidden="true" />
            <span className="hidden text-xs text-zinc-400 sm:block">AI gateway, simplified</span>
          </Link>
          <Link
            to="/"
            className="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm text-zinc-400 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            Back to website
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
        </header>

        <main className="grid flex-1 items-center gap-16 py-10 lg:grid-cols-[1.15fr_0.85fr] lg:gap-20 lg:py-12">
          <section className="hidden lg:block" aria-labelledby="login-showcase-title">
            <div className="mb-7 inline-flex w-fit items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-xs text-zinc-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
              One gateway. Every model.
            </div>
            <h2 id="login-showcase-title" className="max-w-xl text-[clamp(2.75rem,4vw,4rem)] font-medium leading-[1.08] tracking-[-0.045em] text-white">
              Your AI stack,
              <br />
              <span className="text-zinc-500">finally in sync.</span>
            </h2>
            <p className="mt-6 max-w-md text-[15px] leading-7 text-zinc-400">
              One API for every model. Connect your favorite tools to any provider,
              without changing a line of code.
            </p>

            <div className="relative mt-10 max-w-[560px]">
              <Showcase />
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-zinc-400">
              <span className="flex items-center gap-2">
                <ShieldCheck size={14} aria-hidden="true" />
                Self-hosted
              </span>
              <span className="h-1 w-1 rounded-full bg-zinc-700" aria-hidden="true" />
              <span>Your keys stay yours</span>
              <span className="text-zinc-700" aria-hidden="true">/</span>
              <span className="font-mono text-xs">13 providers</span>
            </div>
          </section>

          <section className="w-full lg:justify-self-end" aria-labelledby="login-title">
            <div className="lg-rise mx-auto w-full max-w-[440px]">
              <div className="mb-8 lg:hidden">
                <p className="text-xs leading-relaxed text-zinc-400">
                  One gateway for every model, with your keys under your control.
                </p>
              </div>

              <div className="lg-login-card overflow-hidden rounded-2xl border border-[#302820] shadow-[0_24px_80px_-20px_rgba(0,0,0,0.6)]">
                <div className="p-5 sm:p-8">
                  <div className="mb-6 flex items-start gap-4">
                    <div className="lg-login-mark" aria-hidden="true">
                      <ShieldCheck size={20} />
                    </div>
                    <div className="min-w-0">
                      <p className="lg-eyebrow">Console access</p>
                      <h1 id="login-title" className="mt-2 text-[26px] font-semibold tracking-[-0.035em] text-white sm:text-[28px]">
                        Welcome back
                      </h1>
                      <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                        Sign in to manage your AI gateway.
                      </p>
                    </div>
                  </div>

                  <div className="lg-seg" role="tablist" aria-label="Sign-in method">
                    <span
                      className="lg-seg-indicator"
                      style={{ transform: mode === "master" ? "translateX(0)" : "translateX(calc(100% + 4px))" }}
                      aria-hidden="true"
                    />
                    <button
                      type="button"
                      role="tab"
                      aria-selected={mode === "master"}
                      aria-controls={mode === "master" ? "master-panel" : undefined}
                      onClick={() => { setMode("master"); setError(null); }}
                      className={`relative z-10 flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
                        mode === "master" ? "text-white" : "text-zinc-400 hover:text-zinc-200"
                      }`}
                    >
                      <KeyRound size={15} aria-hidden="true" />
                      Master key
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={mode === "username"}
                      aria-controls={mode === "username" ? "username-panel" : undefined}
                      onClick={() => { setMode("username"); setError(null); }}
                      className={`flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
                        mode === "username" ? "text-white" : "text-zinc-400 hover:text-zinc-200"
                      }`}
                    >
                      <UserRound size={15} aria-hidden="true" />
                      Account
                    </button>
                  </div>

                  <form onSubmit={submit} className="mt-5 space-y-4 sm:mt-6 sm:space-y-5">
                    {mode === "master" ? (
                      <div id="master-panel" role="tabpanel" aria-label="Master key">
                        <label htmlFor="master-key" className="mb-2 block text-[13px] font-medium text-zinc-300">
                          Master key
                        </label>
                        <div className="relative">
                          <KeyRound size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500" aria-hidden="true" />
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
                            className="admin-input !h-12 !rounded-[10px] !border-white/[0.08] !bg-white/[0.03] pl-10 pr-12 font-mono text-[13px]"
                          />
                          <button
                            type="button"
                            onClick={() => setShowKey((v) => !v)}
                            aria-label={showKey ? "Hide master key" : "Show master key"}
                            className="absolute right-1 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.04] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                          >
                            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
                          </button>
                        </div>
                        <p className="mt-2 text-xs leading-relaxed text-zinc-400">
                          The <code className="font-mono text-zinc-400">master_key</code> from your wiwi.yaml
                        </p>
                      </div>
                    ) : (
                      <div id="username-panel" role="tabpanel" aria-label="Account" className="space-y-4">
                        <div>
                          <label htmlFor="li-username" className="mb-2 block text-[13px] font-medium text-zinc-300">Username</label>
                          <div className="relative">
                            <UserRound size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500" aria-hidden="true" />
                            <input
                              id="li-username"
                              type="text"
                              value={username}
                              autoFocus
                              autoComplete="username"
                              spellCheck={false}
                              disabled={busy}
                              placeholder="Enter your username"
                              onChange={(e) => setUsername(e.target.value)}
                              className="admin-input !h-12 !rounded-[10px] !border-white/[0.08] !bg-white/[0.03] pl-10 pr-4 text-[14px]"
                            />
                          </div>
                        </div>
                        <div>
                          <label htmlFor="li-password" className="mb-2 block text-[13px] font-medium text-zinc-300">Password</label>
                          <div className="relative">
                            <input
                              id="li-password"
                              type={showPassword ? "text" : "password"}
                              value={password}
                              autoComplete="current-password"
                              disabled={busy}
                              placeholder="Enter your password"
                              onChange={(e) => setPassword(e.target.value)}
                              className="admin-input !h-12 !rounded-[10px] !border-white/[0.08] !bg-white/[0.03] pl-4 pr-12 text-sm"
                            />
                            <button
                              type="button"
                              onClick={() => setShowPassword((v) => !v)}
                              aria-label={showPassword ? "Hide password" : "Show password"}
                              className="absolute right-1 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                            >
                              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    {error && (
                      <p role="alert" className="flex items-start gap-2.5 rounded-lg border border-red-500/20 bg-red-500/[0.06] px-3.5 py-3 text-[13px] leading-relaxed text-red-300">
                        <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
                        {error}
                      </p>
                    )}

                    <button
                      type="submit"
                      disabled={!canSubmit}
                      className="group inline-flex h-12 w-full items-center justify-center gap-2 rounded-[10px] bg-white text-sm font-semibold text-zinc-950 transition-all duration-200 hover:bg-zinc-100 active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70 focus-visible:ring-offset-2 focus-visible:ring-offset-black disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {busy ? (
                        <>
                          <Loader2 size={16} className="animate-spin" aria-hidden="true" />
                          Signing in…
                        </>
                      ) : (
                        <>
                          Sign in to console
                          <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
                        </>
                      )}
                    </button>
                  </form>

                  <div className="mt-5 border-t border-white/[0.06] pt-4">
                    <p className="text-center text-[13px] text-zinc-400">
                      New to wiwi?{" "}
                      <Link to="/signup" className="inline-flex min-h-11 items-center font-medium text-zinc-200 underline decoration-zinc-600 underline-offset-4 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">
                        Create an account
                      </Link>
                    </p>
                  </div>
                </div>

                <div className="flex items-center justify-center gap-2 border-t border-white/[0.05] bg-white/[0.015] px-6 py-3 text-[11px] text-[var(--admin-text-dim)]">
                  <ShieldCheck size={14} className="shrink-0 text-zinc-500" aria-hidden="true" />
                  <span>Secured by your gateway. Credentials stay private.</span>
                </div>
              </div>

            </div>
          </section>
        </main>

        <footer className="mt-auto flex items-center justify-between border-t border-white/[0.05] pt-4 text-xs text-zinc-400">
          <p>© {new Date().getFullYear()} wiwi</p>
          <div className="flex items-center gap-5">
            <Link to="/docs" className="inline-flex min-h-11 items-center rounded-md px-1 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Documentation</Link>
            <Link to="/contact" className="inline-flex min-h-11 items-center rounded-md px-1 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">Support</Link>
          </div>
        </footer>
      </div>
    </div>
  );
}
