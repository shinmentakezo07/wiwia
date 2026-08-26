// Signup — public username/password registration. On success the auth context
// establishes the session cookie (set server-side by /auth/signup) and we land
// the user in the console at /app.

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Loader2, ShieldCheck, TriangleAlert, UserRound } from "lucide-react";
import { useAuth } from "@/api/auth";

const USERNAME_RE = /^[a-zA-Z0-9_-]+$/;

export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
      navigate("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "signup failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-admin className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--admin-bg)]">
      {/* ambient backdrop — mirrors Login */}
      <div className="pointer-events-none fixed inset-0" style={{ zIndex: 0 }}>
        <div
          className="absolute inset-0 opacity-[0.025]"
          style={{
            backgroundImage: `
              linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px),
              linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px)
            `,
            backgroundSize: "56px 56px",
          }}
        />
        <div className="wiwi-aurora absolute -left-32 -top-24 h-[560px] w-[560px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.07) 0%, transparent 65%)", animationDelay: "0s" }}
        />
        <div className="wiwi-aurora absolute -bottom-32 -right-24 h-[480px] w-[480px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 65%)", animationDelay: "-8s" }}
        />
      </div>

      <div className="wiwi-enter relative z-10 w-full max-w-[420px] px-4">
        <div className="wiwi-conic-border relative rounded-2xl">
          <div className="wiwi-glass-card relative overflow-hidden rounded-2xl">
            <span className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px" />

            {/* brand header */}
            <div className="flex items-center gap-3.5 border-b border-[var(--admin-border)] px-7 py-5">
              <img src="/wiwi-logo.png" alt="wiwi" className="h-10 w-10 shrink-0 rounded-[12px] object-cover shadow-lg shadow-brand-600/20 ring-1 ring-white/[0.06] ring-inset" />
              <div>
                <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">wiwi</h1>
                <span
                  className="font-mono text-[9px] font-semibold uppercase tracking-[0.18em]"
                  style={{ color: "rgba(59, 130, 246, 0.5)" }}
                >
                  Create your account
                </span>
              </div>
            </div>

            <form onSubmit={submit} className="space-y-4 px-7 pb-6 pt-6">
              <label htmlFor="su-username" className="block">
                <span className="admin-label mb-2 block">Username</span>
                <span className="relative block">
                  <UserRound
                    size={14}
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--admin-text-dim)]"
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
                    placeholder="3–32 chars · letters, digits, _ -"
                    onChange={(e) => setUsername(e.target.value)}
                    className="admin-input h-11 pl-9 pr-3 font-mono text-sm disabled:opacity-60"
                  />
                </span>
                {uBad && (
                  <span className="mt-1.5 block text-[11px] text-amber-400/80">
                    3–32 characters; only letters, digits, <code className="font-mono">_</code> and{" "}
                    <code className="font-mono">-</code>.
                  </span>
                )}
              </label>

              <label htmlFor="su-password" className="block">
                <span className="admin-label mb-2 block">Password</span>
                <input
                  id="su-password"
                  type="password"
                  value={password}
                  autoComplete="new-password"
                  disabled={busy}
                  placeholder="at least 8 characters"
                  onChange={(e) => setPassword(e.target.value)}
                  className="admin-input h-11 px-3 text-sm disabled:opacity-60"
                />
                {pBad && (
                  <span className="mt-1.5 block text-[11px] text-amber-400/80">At least 8 characters.</span>
                )}
              </label>

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
                className="wiwi-shimmer group inline-flex h-11 w-full items-center justify-center gap-2 rounded-[8px] bg-gradient-to-b from-brand-500 to-brand-700 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-[transform,filter,box-shadow] duration-150 hover:brightness-110 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/70 disabled:pointer-events-none disabled:opacity-50 disabled:saturate-50"
              >
                {busy ? (
                  <>
                    <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                    Creating…
                  </>
                ) : (
                  <>
                    Sign up
                    <ArrowRight
                      size={15}
                      className="transition-transform duration-150 group-hover:translate-x-0.5"
                      aria-hidden="true"
                    />
                  </>
                )}
              </button>

              <p className="pt-1 text-center text-[12px] text-[var(--admin-text-dim)]">
                Already have an account?{" "}
                <Link to="/login" className="text-blue-300 hover:text-blue-200">
                  Sign in
                </Link>
              </p>
            </form>

            <div className="flex items-center gap-2 border-t border-[var(--admin-border)] px-7 py-3.5 text-[11px] text-[var(--admin-text-dim)]">
              <ShieldCheck size={12} className="shrink-0" aria-hidden="true" />
              Session stored in a server-held cookie. We never see your password after signup.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
