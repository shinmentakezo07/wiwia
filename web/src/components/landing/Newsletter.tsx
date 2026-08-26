// Newsletter — email capture card. Replaces framer-motion stagger with CSS
// AnimatedGroup, replaces useApi mutation with a simple fetch to a local
// endpoint (graceful no-op if the backend is absent), and uses inline input
// + shimmer-styled button instead of radix/shadcn components.

import { useState } from "react";
import { CheckCircle2, Mail, Sparkles, Zap } from "lucide-react";
import { AnimatedGroup } from "./AnimatedGroup";

const perks = [
  { icon: Zap, label: "New models & providers as they drop" },
  { icon: Sparkles, label: "Tips to cut latency & costs" },
  { icon: Mail, label: "Early access to beta features" },
];

export function Newsletter() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "pending" | "success" | "error">("idle");
  const [message, setMessage] = useState<string>("");

  const subscribe = async (e: React.FormEvent) => {
    e.preventDefault();
    if (status === "pending") return;
    setStatus("pending");
    try {
      const res = await fetch("/public/newsletter/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error("Request failed");
      const data = await res.json().catch(() => ({}));
      setMessage(data.message ?? "Check your inbox — we'll send you the good stuff, no filler.");
      setStatus("success");
    } catch {
      setStatus("error");
    }
  };

  return (
    <section className="relative pb-16">
      <div className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.03] to-white/[0.01]">
        {/* Ambient glow */}
        <div className="pointer-events-none absolute -top-24 left-1/2 h-48 w-[70%] -translate-x-1/2 rounded-full bg-blue-500/[0.07] blur-3xl" />

        {/* Gradient top border accent */}
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-blue-500/40 to-transparent" />

        <div className="relative px-6 py-12 sm:px-10 sm:py-14 md:px-14 md:py-16">
          {status === "success" ? (
            <div className="flex flex-col items-center gap-5 py-4 text-center">
              <div className="flex size-14 items-center justify-center rounded-full bg-green-500/10 ring-1 ring-green-500/20">
                <CheckCircle2 className="size-7 text-green-500" />
              </div>
              <div className="space-y-2">
                <h3 className="text-2xl font-bold tracking-tight text-[var(--admin-text)]">
                  You're in!
                </h3>
                <p className="mx-auto max-w-sm text-sm leading-relaxed text-[var(--admin-text-muted)]">
                  {message}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-10 md:flex-row md:items-center md:justify-between md:gap-16">
              {/* Left: copy & perks */}
              <div className="flex-1 space-y-6">
                <AnimatedGroup preset="blur-slide" className="space-y-6">
                  <div className="space-y-3">
                    <p className="text-xs font-semibold uppercase tracking-widest text-blue-400">
                      Newsletter
                    </p>
                    <h3 className="text-2xl font-bold tracking-tight text-[var(--admin-text)] sm:text-3xl">
                      Stay ahead of the curve
                    </h3>
                    <p className="max-w-md text-sm leading-relaxed text-[var(--admin-text-muted)]">
                      Join developers who get weekly insights on LLM routing, new model
                      launches, and cost optimization — straight to their inbox.
                    </p>
                  </div>

                  <ul className="flex flex-col gap-3">
                    {perks.map((perk) => (
                      <li
                        key={perk.label}
                        className="flex items-center gap-3 text-sm text-[var(--admin-text-muted)]"
                      >
                        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-white/[0.05] ring-1 ring-[var(--admin-border)]">
                          <perk.icon className="size-3.5 text-[var(--admin-text-muted)]" />
                        </span>
                        {perk.label}
                      </li>
                    ))}
                  </ul>
                </AnimatedGroup>
              </div>

              {/* Right: form */}
              <div className="w-full md:w-auto md:min-w-[340px]">
                <form className="flex flex-col gap-3" onSubmit={subscribe}>
                  <input
                    type="email"
                    placeholder="you@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    className="h-12 rounded-xl border border-[var(--admin-border)] bg-[var(--admin-bg)]/80 px-4 text-base text-[var(--admin-text)] outline-none backdrop-blur-sm transition-colors focus:border-blue-500/50 focus:ring-2 focus:ring-blue-500/20"
                  />
                  <button
                    type="submit"
                    disabled={status === "pending"}
                    className="wiwi-shimmer h-12 w-full rounded-xl bg-blue-600 text-sm font-semibold text-white shadow-lg shadow-blue-500/20 transition-colors hover:bg-blue-500 disabled:opacity-60"
                  >
                    {status === "pending" ? "Subscribing..." : "Subscribe — it's free"}
                  </button>
                  <p className="text-center text-[11px] text-[var(--admin-text-dim)]">
                    No spam. Unsubscribe anytime.
                  </p>
                </form>

                {status === "error" && (
                  <p className="mt-3 text-center text-sm text-red-400">
                    Something went wrong. Please try again.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
