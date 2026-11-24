// About — mission, values, and links. Flat editorial language shared with the
// restyled public front: mono micro-labels, hairline rules, numbered ledger
// rows. No boxed gradient hero, no gradient text, no glow tiles.

import { Link } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { ArrowRight, Github, Globe2, BookOpen, Terminal } from "lucide-react";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const FACTS: string[] = ["MIT licensed", "Self-hosted", "One binary, one config"];

const VALUES: { title: string; body: string }[] = [
  {
    title: "Open source first",
    body: "The entire platform — gateway, dashboard, and API — is MIT-licensed. Anyone can audit the code, self-host it, or contribute. No proprietary core, no open-core bait-and-switch.",
  },
  {
    title: "Self-hosted by default",
    body: "Your data never has to leave your infrastructure. Deploy with a single Docker command and keep full control of every request, every key, every token.",
  },
  {
    title: "Privacy-first",
    body: "Provider keys enter via environment interpolation — they are never stored in plaintext config. Virtual keys are SHA-256-hashed at rest with constant-time comparison.",
  },
];

const LINKS: { icon: LucideIcon; label: string; href: string; external?: boolean }[] = [
  { icon: Github, label: "GitHub", href: "https://github.com/shinmentakezo07/wiwia", external: true },
  { icon: BookOpen, label: "Documentation", href: "/docs" },
  { icon: Terminal, label: "Playground", href: "/playground" },
];

function SectionLabel(props: { index: string; label: string }) {
  return (
    <p
      className="font-mono text-[10.5px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)]"
      style={{ fontFamily: MONO }}
    >
      <span className="text-blue-400/80">{props.index}</span>
      <span aria-hidden> · </span>
      {props.label}
    </p>
  );
}

export function AboutPage() {
  return (
    <div className="mx-auto max-w-3xl pb-16">
      {/* Header — quiet typographic open */}
      <header className="pb-8 pt-4 sm:pt-8">
        <p className="font-mono text-[10.5px] uppercase tracking-[0.16em] text-[var(--admin-text-dim)]">
          <span className="text-blue-400/80">about</span>
          <span aria-hidden> · </span>
          wiwi
        </p>
        <h1 className="mt-3 max-w-xl text-[clamp(2.1rem,5vw,3.35rem)] font-semibold leading-[1.06] tracking-[-0.04em] text-[var(--admin-text)]">
          Built for developers, by developers
        </h1>
        <p className="mt-5 max-w-[52ch] text-[14.5px] leading-[1.75] text-[var(--admin-text-muted)] sm:text-[15px]">
          wiwi is a self-hosted, unified LLM gateway proxy. One binary, one config, one endpoint
          — speaking every inbound dialect and routing to every outbound provider. We build it in
          the open because the best infrastructure is the kind you fully own.
        </p>
        <p className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-[var(--admin-border)] pt-4 font-mono text-[11px] tracking-[0.04em] text-[var(--admin-text-dim)]">
          {FACTS.map((fact, i) => (
            <span key={fact} className="flex items-center gap-3">
              {i > 0 && <span aria-hidden>·</span>}
              {fact}
            </span>
          ))}
        </p>
      </header>

      <div className="space-y-10">
        {/* ── mission ── */}
        <section aria-labelledby="mission-heading">
          <SectionLabel index="01" label="Our mission" />
          <h2 id="mission-heading" className="sr-only">
            Our mission
          </h2>
          <div className="mt-4 space-y-4 text-[14px] leading-[1.8] text-[var(--admin-text-muted)]">
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
              <code style={{ fontFamily: MONO }} className="text-[var(--admin-text)]">
                wire/
              </code>
              ; adding a provider is one new adapter in{" "}
              <code style={{ fontFamily: MONO }} className="text-[var(--admin-text)]">
                providers/
              </code>
              . Core code never branches on dialect or provider name, so the gateway stays small,
              fast, and maintainable.
            </p>
          </div>
        </section>

        {/* ── values — numbered ledger rows ── */}
        <section aria-labelledby="values-heading">
          <SectionLabel index="02" label="What we believe" />
          <h2 id="values-heading" className="sr-only">
            What we believe
          </h2>
          <ul className="mt-3 divide-y divide-[var(--admin-border)] border-t border-[var(--admin-border)]">
            {VALUES.map((v, i) => (
              <li key={v.title} className="grid gap-2 py-5 sm:grid-cols-[3rem_minmax(0,1fr)] sm:gap-4">
                <span
                  className="font-mono text-[11px] leading-[1.9] text-[var(--admin-text-dim)]"
                  style={{ fontFamily: MONO }}
                >
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                    {v.title}
                  </h3>
                  <p className="mt-1.5 text-[13.5px] leading-relaxed text-[var(--admin-text-muted)]">
                    {v.body}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* ── links — quiet full-width rows ── */}
        <section aria-labelledby="involved-heading">
          <SectionLabel index="03" label="Get involved" />
          <h2 id="involved-heading" className="sr-only">
            Get involved
          </h2>
          <ul className="mt-3 divide-y divide-[var(--admin-border)] border-t border-[var(--admin-border)]">
            {LINKS.map((l) => {
              const Icon = l.icon;
              const inner = (
                <>
                  <Icon
                    size={15}
                    className="shrink-0 text-[var(--admin-text-dim)] transition-colors group-hover:text-blue-300"
                    aria-hidden
                  />
                  <span className="text-[14px] font-medium text-[var(--admin-text)]">{l.label}</span>
                  <ArrowRight
                    size={15}
                    className="ml-auto shrink-0 text-[var(--admin-text-dim)] transition-transform duration-200 group-hover:translate-x-1 group-hover:text-blue-300"
                    aria-hidden
                  />
                </>
              );
              return (
                <li key={l.label}>
                  {l.external ? (
                    <a
                      href={l.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="group flex min-h-11 items-center gap-3 py-1 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500/60"
                    >
                      {inner}
                    </a>
                  ) : (
                    <Link
                      to={l.href}
                      className="group flex min-h-11 items-center gap-3 py-1 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500/60"
                    >
                      {inner}
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="mt-5 flex items-center gap-2 text-[12px] text-[var(--admin-text-dim)]">
            <Globe2 size={13} aria-hidden />
            <span>
              Developed in the open by a distributed team and community of contributors.
            </span>
          </p>
        </section>
      </div>
    </div>
  );
}
