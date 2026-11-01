// Legal — the honest version for a self-hosted project: the software is MIT,
// there is no hosted service behind this site, and the operator of a
// deployment is responsible for its own terms and provider agreements.

import { Link } from "react-router-dom";
import { ArrowUpRight, Database, Scale, Server, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const GITHUB = "https://github.com/shinmentakezo07/wiwia";

interface LegalItem {
  title: string;
  body: string;
  icon: LucideIcon;
  href?: string;
  linkLabel?: string;
}

const ITEMS: LegalItem[] = [
  {
    title: "License",
    body: "wiwi is released under the MIT License. You may run, modify, and redistribute it, including commercially, provided the copyright and permission notice are retained. There is no warranty.",
    icon: Scale,
    href: `${GITHUB}/blob/main/LICENSE`,
    linkLabel: "Read the LICENSE",
  },
  {
    title: "No hosted service",
    body: "This site documents and demonstrates self-hosted software. It is not a managed gateway and does not hold your provider credentials or traffic. Whoever deploys a gateway is its operator.",
    icon: Server,
  },
  {
    title: "Your data stays with you",
    body: "Provider keys enter through environment interpolation, virtual keys are SHA-256-hashed at rest, and request logs live in your database. The project does not receive them.",
    icon: ShieldCheck,
  },
  {
    title: "Provider terms still apply",
    body: "Requests routed through the gateway are governed by the terms of the upstream provider you configured. Review those terms before routing production traffic.",
    icon: Database,
  },
];

export function LegalPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      <section>
        <span className="admin-badge admin-badge-gray mb-4 inline-flex items-center gap-1.5">
          <Scale size={11} /> Legal
        </span>
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          Legal information
        </h1>
        <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Licensing, data handling, and the boundary between this project and the
          deployment you run.
        </p>
      </section>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.title} className="flex flex-col p-5">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                  <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.85)" }} />
                </span>
                <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  {item.title}
                </h2>
              </div>
              <p className="mt-3 flex-1 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {item.body}
              </p>
              {item.href && (
                <a
                  href={item.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-4 inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--admin-accent)] transition-colors hover:text-blue-300"
                >
                  {item.linkLabel}
                  <ArrowUpRight size={14} />
                </a>
              )}
            </Card>
          );
        })}
      </section>

      <p className="text-center text-[13px] text-[var(--admin-text-muted)]">
        The source of truth is the repository:{" "}
        <Link to="/open-source" className="underline">
          self-hosting
        </Link>{" "}
        and the{" "}
        <a
          href={GITHUB}
          target="_blank"
          rel="noopener noreferrer"
          className="underline"
        >
          GitHub project
        </a>
        .
      </p>
    </div>
  );
}
