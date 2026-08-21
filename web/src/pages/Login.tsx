// Login: validates the master key once (probe GET /admin/keys), stores it.
// Visual identity: hub-and-spoke diagram of wiwi's real routes, ambient grid.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Eye, EyeOff, KeyRound, Loader2, Lock, TriangleAlert } from "lucide-react";
import { useAuth } from "@/api/auth";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

/** Signature element: the actual hub-and-spoke architecture — three inbound
 *  dialect routes converge into the wiwi node and fan out to providers. */
function GatewayDiagram() {
  const inbound = [
    { y: 12, label: "chat", d: "M 66 12 C 104 12, 116 32, 150 32" },
    { y: 32, label: "responses", d: "M 66 32 H 150" },
    { y: 52, label: "messages", d: "M 66 52 C 104 52, 116 32, 150 32" },
  ];
  const outbound = [
    { y: 12, label: "openai", d: "M 174 32 C 208 32, 216 12, 250 12" },
    { y: 32, label: "anthropic", d: "M 174 32 H 250" },
    { y: 52, label: "gemini", d: "M 174 32 C 208 32, 216 52, 250 52" },
  ];
  return (
    <svg viewBox="0 0 320 64" className="h-auto w-full" aria-hidden="true">
      <defs>
        <linearGradient id="wiwi-mark-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#8757f7" />
          <stop offset="100%" stopColor="#c026d3" />
        </linearGradient>
      </defs>
      {inbound.map((r, i) => (
        <g key={r.label}>
          <circle
            cx={8}
            cy={r.y}
            r={2.5}
            className="fill-zinc-400 dark:fill-zinc-500 wiwi-pulse"
            style={{ animationDelay: `${i * 0.4}s` }}
          />
          <text
            x={16}
            y={r.y + 3}
            fontSize={8.5}
            fontFamily={MONO}
            className="fill-zinc-500 dark:fill-zinc-400"
          >
            {r.label}
          </text>
          <path
            d={r.d}
            fill="none"
            strokeWidth={1}
            strokeDasharray="4 4"
            className="stroke-zinc-300 dark:stroke-zinc-600 wiwi-flow"
            style={{ animationDelay: `${i * 0.25}s` }}
          />
        </g>
      ))}
      <circle cx={162} cy={32} r={17} className="fill-brand-500/15 dark:fill-brand-500/20" />
      <rect x={150} y={20} width={24} height={24} rx={7} fill="url(#wiwi-mark-grad)" />
      <text
        x={162}
        y={36}
        textAnchor="middle"
        fontSize={12}
        fontWeight={700}
        fill="#fff"
        fontFamily={MONO}
      >
        w
      </text>
      {outbound.map((r) => (
        <g key={r.label}>
          <path
            d={r.d}
            fill="none"
            strokeWidth={1}
            strokeDasharray="1.5 4"
            className="stroke-zinc-300/80 dark:stroke-zinc-700"
          />
          <circle cx={254} cy={r.y} r={2.5} className="fill-zinc-400 dark:fill-zinc-500" />
          <text
            x={260}
            y={r.y + 3}
            fontSize={8.5}
            fontFamily={MONO}
            className="fill-zinc-500 dark:fill-zinc-400"
          >
            {r.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showKey, setShowKey] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim() || busy) return;
    setBusy(true);
    setError(null);
    const err = await login(key.trim());
    setBusy(false);
    if (err) setError(err);
    else navigate("/");
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-zinc-100 p-4 dark:bg-zinc-950">
      {/* ambient backdrop: blueprint grid + single glow */}
      <div className="wiwi-grid pointer-events-none absolute inset-0" aria-hidden="true" />
      <div
        className="pointer-events-none absolute -top-48 left-1/2 h-[420px] w-[680px] -translate-x-1/2 rounded-full bg-brand-500/10 blur-[110px] dark:bg-brand-600/20"
        aria-hidden="true"
      />

      <div className="wiwi-enter relative z-10 w-full max-w-[400px]">
        <div className="overflow-hidden rounded-2xl border border-zinc-200/80 bg-white/85 shadow-xl shadow-zinc-950/5 backdrop-blur-xl dark:border-white/10 dark:bg-zinc-900/70 dark:shadow-black/40 dark:[box-shadow:0_0_90px_-24px_rgb(59_130_246/0.35)]">
          {/* brand */}
          <div className="px-6 pb-5 pt-6">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-fuchsia-600 font-mono text-lg font-bold text-white shadow-lg shadow-brand-600/30 ring-1 ring-white/25 ring-inset">
                w
              </span>
              <div>
                <div className="flex items-baseline gap-2">
                  <h1 className="text-lg font-semibold tracking-tight">wiwi</h1>
                  <span className="rounded border border-zinc-300 px-1.5 py-px font-mono text-[10px] uppercase tracking-wider text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                    admin
                  </span>
                </div>
                <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-brand-600/80 dark:text-brand-400/80">
                  unified llm gateway
                </p>
              </div>
            </div>
          </div>

          {/* signature: live route map */}
          <div className="border-y border-zinc-200/70 bg-zinc-50/60 px-6 py-4 dark:border-white/[0.06] dark:bg-white/[0.02]">
            <GatewayDiagram />
          </div>

          {/* form */}
          <form onSubmit={submit} className="space-y-4 px-6 pb-6 pt-5">
            <label htmlFor="master-key" className="block">
              <span className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-300">
                Master key
              </span>
              <span className="relative block">
                <KeyRound
                  size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400"
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
                  className="h-10 w-full rounded-lg border border-zinc-300 bg-white pl-9 pr-10 font-mono text-sm outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-zinc-400 focus:border-brand-500 focus:ring-4 focus:ring-brand-500/15 disabled:opacity-60 dark:border-zinc-700/80 dark:bg-zinc-950/60 dark:placeholder:text-zinc-600"
                />
                <button
                  type="button"
                  onClick={() => setShowKey((v) => !v)}
                  aria-label={showKey ? "Hide key" : "Show key"}
                  className="absolute right-1.5 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/60 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
                >
                  {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </span>
              <span className="mt-1.5 block text-xs text-zinc-400 dark:text-zinc-500">
                The <code className="font-mono text-[11px]">master_key</code> from your wiwi.yaml.
              </span>
            </label>

            {error && (
              <p
                key={error}
                role="alert"
                className="wiwi-shake flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs leading-relaxed text-red-600 dark:text-red-400"
              >
                <TriangleAlert size={13} className="mt-px shrink-0" aria-hidden="true" />
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={!key.trim() || busy}
              className="group inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-b from-brand-500 to-brand-700 text-sm font-medium text-white shadow-lg shadow-brand-600/25 transition-[transform,filter,box-shadow] duration-150 hover:brightness-110 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400/70 disabled:pointer-events-none disabled:opacity-55 disabled:saturate-50"
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
          </form>

          {/* trust footer */}
          <div className="flex items-center gap-2 border-t border-zinc-200/70 px-6 py-3 text-[11px] text-zinc-500 dark:border-white/[0.06] dark:text-zinc-500">
            <Lock size={12} className="shrink-0" aria-hidden="true" />
            Key stays in this browser — checked once against your gateway.
          </div>
        </div>
      </div>
    </div>
  );
}
