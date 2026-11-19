// Signup — public username/password registration. On success the auth context
// establishes the session cookie and lands the user in the console.

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Eye,
  EyeOff,
  Loader2,
  ShieldCheck,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import { useAuth } from "@/api/auth";

const USERNAME_RE = /^[a-zA-Z0-9_-]+$/;

export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

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
            className="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm text-zinc-400 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          >
            Back to website
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
        </header>

        <main className="flex flex-1 items-center justify-center py-8 sm:py-12">
          <section className="w-full" aria-labelledby="signup-title">
            <div className="lg-rise mx-auto w-full max-w-[440px]">
              <div className="mb-8 lg:hidden">
                <p className="text-xs leading-relaxed text-zinc-400">
                  One gateway for every model, with your keys under your control.
                </p>
              </div>

              <div className="lg-login-card overflow-hidden rounded-2xl border border-[#333] shadow-[0_24px_80px_-20px_rgba(0,0,0,0.6)]">
                <div className="p-5 sm:p-8">
                  <div className="mb-6 flex items-start gap-4">
                    <div className="lg-login-mark" aria-hidden="true">
                      <UserRound size={20} />
                    </div>
                    <div className="min-w-0">
                      <p className="lg-eyebrow">Create your account</p>
                      <h1 id="signup-title" className="mt-2 text-[26px] font-semibold tracking-[-0.035em] text-white sm:text-[28px]">
                        Start building
                      </h1>
                      <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                        Your gateway, ready when you are.
                      </p>
                    </div>
                  </div>

                  <form onSubmit={submit} className="space-y-4 sm:space-y-5">
                    <div>
                      <label htmlFor="su-username" className="mb-2 block text-[13px] font-medium text-zinc-300">
                        Username
                      </label>
                      <div className="relative">
                        <UserRound
                          size={16}
                          className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500"
                          aria-hidden="true"
                        />
                        <input
                          id="su-username"
                          type="text"
                          value={username}
                          autoFocus
                          autoComplete="username"
                          spellCheck={false}
                          disabled={busy}
                          placeholder="3–32 characters"
                          onChange={(e) => setUsername(e.target.value)}
                          className="admin-input !h-12 !rounded-[10px] !border-white/[0.08] !bg-white/[0.03] pl-10 pr-4 font-mono text-[13px]"
                        />
                      </div>
                      {uBad ? (
                        <p className="mt-2 text-xs leading-relaxed text-red-300" role="alert">
                          Use 3–32 characters: letters, digits, <code className="font-mono">_</code> or{" "}
                          <code className="font-mono">-</code>.
                        </p>
                      ) : (
                        <p className="mt-2 text-xs leading-relaxed text-zinc-400">
                          3–32 characters. Letters, digits, <code className="font-mono">_</code> and{" "}
                          <code className="font-mono">-</code>.
                        </p>
                      )}
                    </div>

                    <div>
                      <label htmlFor="su-password" className="mb-2 block text-[13px] font-medium text-zinc-300">
                        Password
                      </label>
                      <div className="relative">
                        <input
                          id="su-password"
                          type={showPassword ? "text" : "password"}
                          value={password}
                          autoComplete="new-password"
                          disabled={busy}
                          placeholder="At least 8 characters"
                          onChange={(e) => setPassword(e.target.value)}
                          className="admin-input !h-12 !rounded-[10px] !border-white/[0.08] !bg-white/[0.03] pl-4 pr-12 text-sm"
                        />
                        <button
                          type="button"
                          onClick={() => setShowPassword((v) => !v)}
                          aria-label={showPassword ? "Hide password" : "Show password"}
                          className="absolute right-1 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                        >
                          {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                        </button>
                      </div>
                      {pBad && (
                        <p className="mt-2 text-xs leading-relaxed text-zinc-400">
                          Use at least 8 characters.
                        </p>
                      )}
                    </div>

                  {error && (
                    <p
                      role="alert"
                      className="flex items-start gap-2.5 rounded-lg border border-red-500/20 bg-red-500/[0.06] px-3.5 py-3 text-[13px] leading-relaxed text-red-300"
                    >
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
                        Creating your account…
                      </>
                    ) : (
                      <>
                        Create account
                        <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
                      </>
                    )}
                  </button>
                </form>

                <div className="mt-5 border-t border-white/[0.06] pt-4">
                  <p className="text-center text-[13px] text-zinc-400">
                    Already have an account?{" "}
                    <Link to="/login" className="inline-flex min-h-11 items-center font-medium text-zinc-200 underline decoration-zinc-600 underline-offset-4 transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400">
                      Sign in
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
