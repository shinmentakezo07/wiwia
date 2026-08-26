// Changelog — timeline of releases. Each entry is a card with a version badge,
// date, and a bulleted list of changes. Matches the dark design system shared
// with the admin console.

import { History } from "lucide-react";
import { Badge, Card } from "@/components/ui";

interface ChangelogEntry {
  version: string;
  date: string;
  changes: string[];
}

const ENTRIES: ChangelogEntry[] = [
  {
    version: "0.6.0",
    date: "August 2025",
    changes: [
      "Add session auth with public/guarded route split and role-aware admin layout",
      "Add Landing, Signup, Users, Playground, ModelsCatalog, and Docs pages",
      "Add dual-mode Login (master-key admin vs user account)",
      "Fix post-login navigation to /app for master-key admin",
    ],
  },
  {
    version: "0.5.0",
    date: "August 2025",
    changes: [
      "Add cross-provider routing: Claude Code backed by GPT and vice versa",
      "Add OpenRouter adapter with reasoning parameter translation",
      "Fix reasoning/thinking parameter translation between OpenAI and Anthropic",
      "Fix multi-turn conversation tool_result message handling",
    ],
  },
  {
    version: "0.4.0",
    date: "July 2025",
    changes: [
      "Add key pools with smooth weighted round-robin routing",
      "Add per-key cooldowns and automatic retries on transient failures",
      "Add fallback model groups so a flaky upstream never reaches the caller",
      "Add cost tracking with per-key, per-model, per-provider breakdowns",
    ],
  },
  {
    version: "0.3.0",
    date: "July 2025",
    changes: [
      "Add virtual keys with model allowlists, expiry, and spend caps",
      "Add per-key budgets and RPM/TPM rate limits",
      "Add structured request logs with token usage and latency",
      "Add admin web UI (React 19 + TypeScript + Vite + Tailwind 4)",
    ],
  },
  {
    version: "0.2.0",
    date: "June 2025",
    changes: [
      "Add OpenAI Responses (Codex CLI) inbound dialect",
      "Add Anthropic Messages (Claude Code) inbound dialect",
      "Define IRStreamDelta taxonomy as the streaming contract",
      "Add Gemini provider adapter",
    ],
  },
];

export function ChangelogPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── header ── */}
      <section>
        <span className="admin-badge admin-badge-violet mb-4 inline-flex items-center gap-1.5">
          <History size={11} /> Changelog
        </span>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          What's new
        </h1>
        <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          New features, improvements, and fixes shipped to wiwi. Stay up to date with
          everything that moves through the gateway.
        </p>
      </section>

      {/* ── timeline ── */}
      <section className="space-y-4">
        {ENTRIES.map((entry, i) => (
          <div key={entry.version} className="relative">
            {/* connector line */}
            {i < ENTRIES.length - 1 && (
              <span
                className="absolute left-[18px] top-[52px] h-[calc(100%-40px)] w-px bg-[var(--admin-border)]"
                aria-hidden
              />
            )}
            <Card className="p-5">
              <div className="flex items-start gap-4">
                {/* node */}
                <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                  <span className="h-2 w-2 rounded-full bg-blue-400" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="blue">{entry.version}</Badge>
                    <span className="font-mono text-[11px] text-[var(--admin-text-dim)]">
                      {entry.date}
                    </span>
                  </div>
                  <ul className="mt-3 space-y-1.5">
                    {entry.changes.map((change, j) => (
                      <li
                        key={j}
                        className="flex items-start gap-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]"
                      >
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-[var(--admin-text-dim)]" />
                        {change}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </Card>
          </div>
        ))}
      </section>
    </div>
  );
}
