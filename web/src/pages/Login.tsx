// Login — the console's front door.
// Linear-look aesthetic adapted to the Dra-style dark console: dark moody
// background with colorful blurry glows, super-thin hairlines, circuitry-style
// gateway diagram with animated data-flow pulses, animated conic border,
// glassmorphism card, subtle gradient heading.

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Eye, EyeOff, KeyRound, Loader2, ShieldCheck, TriangleAlert, UserRound } from "lucide-react";
import { useAuth } from "@/api/auth";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** Hub-and-spoke architecture as a circuit board schematic. Three inbound
 *  dialects converge into the wiwi node and fan out to providers. Data-flow
 *  dots travel along the dashed paths toward/from the hub. */
function GatewayDiagram() {
  const inbound = [
    { y: 18, label: "chat", d: "M 74 18 C 112 18, 126 42, 164 42" },
    { y: 42, label: "responses", d: "M 74 42 H 164" },
    { y: 66, label: "messages", d: "M 74 66 C 112 66, 126 42, 164 42" },
  ];
  const outbound = [
    { y: 18, label: "openai", d: "M 190 42 C 222 42, 230 18, 262 18" },
    { y: 42, label: "anthropic", d: "M 190 42 H 262" },
    { y: 66, label: "gemini", d: "M 190 42 C 222 42, 230 66, 262 66" },
  ];
  return (
    <svg viewBox="0 0 336 84" className="h-auto w-full" aria-hidden="true">
      <defs>
        <linearGradient id="lg-mark-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
        <linearGradient id="lg-path-in" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="rgba(59,130,246,0.06)" />
          <stop offset="100%" stopColor="rgba(59,130,246,0.4)" />
        </linearGradient>
        <linearGradient id="lg-path-out" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="rgba(168,85,247,0.4)" />
          <stop offset="100%" stopColor="rgba(168,85,247,0.06)" />
        </linearGradient>
        <radialGradient id="lg-hub-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="rgba(59,130,246,0.2)" />
          <stop offset="100%" stopColor="rgba(59,130,246,0)" />
        </radialGradient>
      </defs>

      {/* hub glow halo */}
      <circle cx={177} cy={42} r={36} fill="url(#lg-hub-glow)" className="wiwi-hub-pulse" />

      {/* inbound paths + labels */}
      {inbound.map((r, i) => (
        <g key={r.label}>
          <circle
            cx={8}
            cy={r.y}
            r={2.5}
            fill="rgba(59,130,246,0.4)"
            className="wiwi-pulse"
            style={{ animationDelay: `${i * 0.4}s` }}
          />
          <text x={16} y={r.y + 3} fontSize={8} fontFamily={MONO} fill="#6b7280">
            {r.label}
          </text>
          <path
            d={r.d}
            fill="none"
            strokeWidth={1}
            strokeDasharray="4 4"
            stroke="url(#lg-path-in)"
            className="wiwi-flow"
            style={{ animationDelay: `${i * 0.25}s` }}
          />
          {/* data-flow dot traveling toward hub */}
          <circle r={1.8} fill="#3b82f6" className="wiwi-dot-in" style={{ animationDelay: `${i * 0.6}s` }}>
            <animateMotion dur="2.4s" repeatCount="indefinite" path={r.d} keyPoints="0;1" keyTimes="0;1" />
          </circle>
        </g>
      ))}

      {/* hub node */}
      <circle cx={177} cy={42} r={20} fill="rgba(59,130,246,0.05)" />
      <rect x={165} y={30} width={24} height={24} rx={7} fill="url(#lg-mark-grad)" />
      <text x={177} y={38} textAnchor="middle" fontSize={12} fontWeight={700} fill="#fff" fontFamily={MONO}>
        w
      </text>

      {/* outbound paths + labels */}
      {outbound.map((r, i) => (
        <g key={r.label}>
          <path
            d={r.d}
            fill="none"
            strokeWidth={1}
            strokeDasharray="1.5 4"
            stroke="url(#lg-path-out)"
            className="wiwi-flow"
            style={{ animationDelay: `${i * 0.25 + 0.5}s` }}
          />
          {/* data-flow dot traveling away from hub */}
          <circle r={1.8} fill="#a855f7" className="wiwi-dot-out" style={{ animationDelay: `${i * 0.6 + 0.3}s` }}>
            <animateMotion dur="2.4s" repeatCount="indefinite" path={r.d} keyPoints="0;1" keyTimes="0;1" />
          </circle>
          <circle cx={264} cy={r.y} r={2.5} fill="rgba(168,85,247,0.4)" />
          <text x={270} y={r.y + 3} fontSize={8} fontFamily={MONO} fill="#6b7280">
            {r.label}
          </text>
        </g>
      ))}
    </svg>
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

  const masterReady = !!key.trim();
  const usernameReady = username.trim().length >= 3 && password.length >= 8;
  const canSubmit = (mode === "master" ? masterReady : usernameReady) && !busy;

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
      navigate("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-admin className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[var(--admin-bg)]">
      {/* ═══ ambient backdrop (Linear-look: dark + colorful glows + grid) ═══ */}
      <div className="pointer-events-none fixed inset-0" style={{ zIndex: 0 }}>
        {/* faint grid */}
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
        {/* aurora color blobs — large, blurred, drifting */}
        <div className="wiwi-aurora absolute -left-32 -top-24 h-[560px] w-[560px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.07) 0%, transparent 65%)", animationDelay: "0s" }}
        />
        <div className="wiwi-aurora absolute -bottom-32 -right-24 h-[480px] w-[480px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 65%)", animationDelay: "-8s" }}
        />
        <div className="wiwi-aurora absolute left-1/2 top-1/2 h-[400px] w-[400px] -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{ background: "radial-gradient(circle, rgba(124,58,237,0.04) 0%, transparent 60%)", animationDelay: "-14s" }}
        />
      </div>

      {/* ═══ card ═══ */}
      <div className="wiwi-enter relative z-10 w-full max-w-[420px] px-4">
        {/* animated conic-gradient border wrapper */}
        <div className="wiwi-conic-border relative rounded-2xl">
          <div className="wiwi-glass-card relative overflow-hidden rounded-2xl">
            {/* top specular highlight line */}
            <span className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px" />

            {/* ── brand header ── */}
            <div className="flex items-center gap-3.5 border-b border-[var(--admin-border)] px-7 py-5">
              <img src="/wiwi-logo.png" alt="wiwi" className="h-10 w-10 shrink-0 rounded-[12px] object-cover shadow-lg shadow-brand-600/20 ring-1 ring-white/[0.06] ring-inset" />
              <div>
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

            {/* ── animated circuit schematic ── */}
            <div className="border-b border-[var(--admin-border)] bg-white/[0.003] px-7 py-6">
              <p className="admin-label mb-4 text-center">hub-and-spoke translation</p>
              <GatewayDiagram />
            </div>

            {/* ── mode toggle ── */}
            <div className="flex gap-1 border-b border-[var(--admin-border)] px-4 pt-3">
              <button
                type="button"
                onClick={() => { setMode("master"); setError(null); }}
                className={`flex-1 rounded-[8px] px-3 py-2 text-[13px] font-medium transition-colors ${
                  mode === "master"
                    ? "bg-blue-500/[0.06] text-blue-200"
                    : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                }`}
              >
                <KeyRound size={13} className="mr-1.5 inline" />
                Master key
              </button>
              <button
                type="button"
                onClick={() => { setMode("username"); setError(null); }}
                className={`flex-1 rounded-[8px] px-3 py-2 text-[13px] font-medium transition-colors ${
                  mode === "username"
                    ? "bg-blue-500/[0.06] text-blue-200"
                    : "text-[var(--admin-text-muted)] hover:bg-white/[0.02] hover:text-[var(--admin-text)]"
                }`}
              >
                <UserRound size={13} className="mr-1.5 inline" />
                Username
              </button>
            </div>

            {/* ── form ── */}
            <form onSubmit={submit} className="space-y-4 px-7 pb-6 pt-6">
              {mode === "master" ? (
                <label htmlFor="master-key" className="block">
                  <span className="admin-label mb-2 block">Master Key</span>
                  <span className="relative block">
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
                      className="admin-input h-11 pl-9 pr-10 font-mono text-sm disabled:opacity-60"
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
                    <span className="relative block">
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
                        className="admin-input h-11 pl-9 pr-3 text-sm disabled:opacity-60"
                      />
                    </span>
                  </label>
                  <label htmlFor="li-password" className="block">
                    <span className="admin-label mb-2 block">Password</span>
                    <input
                      id="li-password"
                      type="password"
                      value={password}
                      autoComplete="current-password"
                      disabled={busy}
                      placeholder="••••••••"
                      onChange={(e) => setPassword(e.target.value)}
                      className="admin-input h-11 px-3 text-sm disabled:opacity-60"
                    />
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
                className="wiwi-shimmer group inline-flex h-11 w-full items-center justify-center gap-2 rounded-[8px] bg-gradient-to-b from-brand-500 to-brand-700 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-[transform,filter,box-shadow] duration-150 hover:brightness-110 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/70 disabled:pointer-events-none disabled:opacity-50 disabled:saturate-50"
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

              <p className="pt-1 text-center text-[12px] text-[var(--admin-text-dim)]">
                No account?{" "}
                <Link to="/signup" className="text-blue-300 hover:text-blue-200">
                  Sign up
                </Link>
              </p>
            </form>

            {/* ── trust footer ── */}
            <div className="flex items-center gap-2 border-t border-[var(--admin-border)] px-7 py-3.5 text-[11px] text-[var(--admin-text-dim)]">
              <ShieldCheck size={12} className="shrink-0" aria-hidden="true" />
              {mode === "master"
                ? "Key stays in this browser — checked once against your gateway."
                : "Session held in a server-side cookie. We never see your password."}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
