// Legal — public index of legal documents. Each document is a card with title,
// last-updated date, a short summary, and a link to the full text. Matches the
// dark design system shared with the admin console.

import { Link } from "react-router-dom";
import { ArrowUpRight, Building2, Network, Scale, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

interface LegalDoc {
  title: string;
  updated: string;
  summary: string;
  icon: LucideIcon;
}

const DOCS: LegalDoc[] = [
  {
    title: "Terms of Service",
    updated: "March 2025",
    summary:
      "The agreement governing access to wiwi, including accounts, billing, acceptable use, and third-party AI providers.",
    icon: Scale,
  },
  {
    title: "Privacy Policy",
    updated: "March 2025",
    summary:
      "How we collect, use, share, retain, and protect personal information and customer data across the gateway.",
    icon: ShieldCheck,
  },
  {
    title: "Provider Information",
    updated: "February 2025",
    summary:
      "Legal links, locations, data handling, retention, and compliance information for every available AI provider.",
    icon: Building2,
  },
  {
    title: "Sub-processors",
    updated: "January 2025",
    summary:
      "The third parties that process personal data for the platform, including their purpose and primary processing locations.",
    icon: Network,
  },
];

export function LegalPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── header ── */}
      <section>
        <span className="admin-badge admin-badge-gray mb-4 inline-flex items-center gap-1.5">
          <Scale size={11} /> Legal
        </span>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Legal information
        </h1>
        <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Find the documents that govern wiwi and review the policies of the AI providers
          available through the gateway.
        </p>
      </section>

      {/* ── document cards ── */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {DOCS.map((doc) => {
          const Icon = doc.icon;
          return (
            <Card key={doc.title} className="flex flex-col p-5">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                  <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.85)" }} />
                </span>
                <div className="min-w-0">
                  <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                    {doc.title}
                  </h2>
                  <p className="mt-0.5 font-mono text-[11px] text-[var(--admin-text-dim)]">
                    Last updated {doc.updated}
                  </p>
                </div>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {doc.summary}
              </p>
              <div className="mt-4 pt-1">
                <Link
                  to="#"
                  className="inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--admin-accent)] transition-colors hover:text-blue-300"
                >
                  View full document
                  <ArrowUpRight size={14} />
                </Link>
              </div>
            </Card>
          );
        })}
      </section>
    </div>
  );
}
