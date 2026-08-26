// Compare — side-by-side feature matrix of wiwi against OpenRouter, LiteLLM,
// and Portkey. Styled table with checkmarks and dashes. Matches the dark
// design system shared with the admin console.

import { Scale } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const PRODUCTS = ["wiwi", "OpenRouter", "LiteLLM", "Portkey"];

const FEATURES: { label: string; values: boolean[] }[] = [
  { label: "Self-hosted", values: [true, false, true, true] },
  { label: "Open source", values: [true, false, true, false] },
  { label: "Virtual keys", values: [true, true, true, true] },
  { label: "Key pools", values: [true, false, true, true] },
  { label: "Cost tracking", values: [true, true, true, true] },
  { label: "Three inbound dialects", values: [true, false, false, false] },
  { label: "Retries / fallbacks", values: [true, true, true, true] },
  { label: "Budgets / rate limits", values: [true, false, true, true] },
];

function Cell({ ok }: { ok: boolean }) {
  if (ok) {
    return (
      <span
        className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-400"
        aria-label="Yes"
      >
        <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
          <path
            d="M2.5 6.2l2.3 2.3L9.5 3.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  return (
    <span className="font-mono text-[14px] text-[var(--admin-text-dim)]" aria-label="No">
      —
    </span>
  );
}

export function ComparePage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── header ── */}
      <section>
        <span className="admin-badge admin-badge-blue mb-4 inline-flex items-center gap-1.5">
          <Scale size={11} /> Comparisons
        </span>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          wiwi vs the alternatives
        </h1>
        <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          An open-source AI gateway that routes requests to 40+ providers through one
          endpoint. Here's where it differs from the gateways teams usually evaluate
          alongside it.
        </p>
      </section>

      {/* ── comparison table ── */}
      <section>
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse text-left text-[13px]">
              <thead>
                <tr className="border-b border-[var(--admin-border)] bg-white/[0.02]">
                  <th scope="col" className="px-5 py-3.5 admin-label">
                    Feature
                  </th>
                  {PRODUCTS.map((p) => (
                    <th
                      key={p}
                      scope="col"
                      className="px-5 py-3.5 text-center"
                    >
                      <span
                        className={
                          p === "wiwi"
                            ? "font-semibold text-[var(--admin-accent)]"
                            : "font-medium text-[var(--admin-text)]"
                        }
                        style={p === "wiwi" ? { fontFamily: MONO } : undefined}
                      >
                        {p}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {FEATURES.map((row, i) => (
                  <tr
                    key={row.label}
                    className={
                      i < FEATURES.length - 1
                        ? "border-b border-[var(--admin-border)]"
                        : ""
                    }
                  >
                    <th scope="row" className="px-5 py-3.5 text-left font-medium text-[var(--admin-text)]">
                      {row.label}
                    </th>
                    {row.values.map((ok, j) => (
                      <td key={j} className="px-5 py-3.5 text-center">
                        <Cell ok={ok} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      {/* ── guidance ── */}
      <section className="rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent p-8 sm:p-10">
        <h2 className="text-xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Which one should you choose?
        </h2>
        <p className="mt-3 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          If you need one endpoint across many providers, want the routing layer to be
          inspectable and self-hostable, and care about paying provider rates rather than a
          marked-up bill, wiwi is built for that. The whole platform is open source, so you
          can run it on your own infrastructure or use a managed deployment.
        </p>
        <p className="mt-3 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          The matrix above names where wiwi differs. Where another product does something
          better, it's worth knowing before you commit.
        </p>
      </section>
    </div>
  );
}
