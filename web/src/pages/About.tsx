// About — mission, values, and links. Matches the dark design system shared
// with the admin console.

import { Link } from "react-router-dom";
import { ArrowRight, BookOpen, Github, GitFork, Globe2, Lock, Server, Terminal } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const VALUES: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: GitFork,
    title: "Open source first",
    body: "The entire platform — gateway, dashboard, and API — is AGPLv3. Anyone can audit the code, self-host it, or contribute. No proprietary core, no open-core bait-and-switch.",
  },
  {
    icon: Server,
    title: "Self-hosted by default",
    body: "Your data never has to leave your infrastructure. Deploy with a single Docker command and keep full control of every request, every key, every token.",
  },
  {
    icon: Lock,
    title: "Privacy-first",
    body: "Provider keys enter via environment interpolation — they are never stored in plaintext config. Virtual keys are SHA-256-hashed at rest with constant-time comparison.",
  },
];

const LINKS: { icon: LucideIcon; label: string; href: string; external?: boolean }[] = [
  { icon: Github, label: "GitHub", href: "https://github.com" },
  { icon: BookOpen, label: "Documentation", href: "/docs" },
  { icon: Terminal, label: "Playground", href: "/playground" },
];

export function AboutPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-gradient-to-b from-white/[0.02] to-transparent px-6 py-12 text-center sm:px-10 sm:py-16">
        <div
          className="pointer-events-none absolute -left-16 -top-16 h-[360px] w-[360px] rounded-full"
          style={{ background: "radial-gradient(circle, rgba(59,130,246,0.08) 0%, transparent 60%)" }}
          aria-hidden
        />
        <div className="relative z-10">
          <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
            Built for developers,{" "}
            <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
              by developers
            </span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            wiwi is a self-hosted, unified LLM gateway proxy. One binary, one config,
            one endpoint — speaking every inbound dialect and routing to every outbound
            provider. We build it in the open because the best infrastructure is the kind
            you fully own.
          </p>
        </div>
      </section>

      {/* ── mission ── */}
      <section>
        <span className="admin-label">Our mission</span>
        <div className="mt-3 space-y-4 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          <p>
            Teams building with large language models juggle multiple provider accounts,
            incompatible SDKs, separate invoices, and no unified view of what their AI features
            actually cost. wiwi sits between your application and every provider: you send
            requests to one endpoint, and the gateway routes them to OpenAI, Anthropic, Gemini,
            OpenRouter, or any OpenAI-compatible URL.
          </p>
          <p>
            The core idea is hub-and-spoke translation. No pairwise converters — every direction
            goes dialect → IR → provider. Adding an inbound surface is one new module in{" "}
            <code style={{ fontFamily: MONO }}>wire/</code>; adding a provider is one new adapter in{" "}
            <code style={{ fontFamily: MONO }}>providers/</code>. Core code never branches on
            dialect or provider name, so the gateway stays small, fast, and maintainable.
          </p>
        </div>
      </section>

      {/* ── values ── */}
      <section>
        <h2 className="mb-4 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          What we believe
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {VALUES.map((v) => {
            const Icon = v.icon;
            return (
              <Card key={v.title} className="p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-gradient-to-br from-blue-500/15 to-violet-500/15 ring-1 ring-white/[0.06]">
                  <Icon className="h-4 w-4" style={{ color: "rgba(59,130,246,0.85)" }} />
                </span>
                <h3 className="mt-3 text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                  {v.title}
                </h3>
                <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{v.body}</p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── team / links ── */}
      <section>
        <h2 className="mb-4 text-2xl font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Get involved
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {LINKS.map((l) => {
            const Icon = l.icon;
            return l.external ? (
              <a
                key={l.label}
                href={l.href}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center gap-3 rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-4 transition-colors hover:border-[var(--admin-border-hover)]"
              >
                <Icon className="h-4 w-4 text-[var(--admin-text-dim)] transition-colors group-hover:text-blue-400" />
                <span className="text-[14px] font-medium text-[var(--admin-text)]">{l.label}</span>
                <ArrowRight size={14} className="ml-auto text-[var(--admin-text-dim)] transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-blue-400" />
              </a>
            ) : (
              <Link
                key={l.label}
                to={l.href}
                className="group flex items-center gap-3 rounded-[12px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-5 py-4 transition-colors hover:border-[var(--admin-border-hover)]"
              >
                <Icon className="h-4 w-4 text-[var(--admin-text-dim)] transition-colors group-hover:text-blue-400" />
                <span className="text-[14px] font-medium text-[var(--admin-text)]">{l.label}</span>
                <ArrowRight size={14} className="ml-auto text-[var(--admin-text-dim)] transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-blue-400" />
              </Link>
            );
          })}
        </div>
        <div className="mt-4 flex items-center gap-2 text-[12px] text-[var(--admin-text-dim)]">
          <Globe2 size={13} />
          <span>Developed in the open by a distributed team and community of contributors.</span>
        </div>
      </section>
    </div>
  );
}
