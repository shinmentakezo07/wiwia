// Templates — production-ready AI app starters. Listing backed by the shared
// templates data module; each card opens its page at /templates/:slug.

import { useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Code2,
  Copy,
  ExternalLink,
  LayoutGrid,
  Play,
  Sparkles,
  Wallet,
} from "lucide-react";
import { Badge, Card } from "@/components/ui";
import { TEMPLATES } from "@/data/templates";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export function TemplatesPage() {
  const [copiedSlug, setCopiedSlug] = useState<string | null>(null);

  const copyCloneCmd = useCallback((slug: string) => {
    navigator.clipboard.writeText(
      `git clone --depth 1 https://github.com/theopenco/llmgateway-templates.git\n` +
        `cd llmgateway-templates/templates/${slug}`,
    );
    setCopiedSlug(slug);
    setTimeout(() => setCopiedSlug(null), 2000);
  }, []);

  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          AI App{" "}
          <span className="bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
            Templates
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Production-ready templates to help you build AI-powered applications faster.
          Clone, customize, and deploy — each one runs on the gateway with a base URL
          and a virtual key.
        </p>
      </section>

      {/* ── template cards ── */}
      <section className="grid gap-6 sm:grid-cols-2">
        {TEMPLATES.map((template) => {
          const Icon = template.icon;
          return (
            <Card
              key={template.slug}
              className="group relative flex flex-col overflow-hidden transition-colors hover:border-[var(--admin-border-hover)]"
            >
              {template.featured && (
                <div className="absolute right-3 top-3 z-10">
                  <Badge tone="violet">
                    <Sparkles size={12} className="mr-1" />
                    Featured
                  </Badge>
                </div>
              )}
              <Link
                to={`/templates/${template.slug}`}
                aria-label={`Open ${template.name} template details`}
                className={`relative flex h-40 items-center justify-center overflow-hidden bg-gradient-to-br ${template.gradient} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50`}
              >
                <Icon className="h-16 w-16 text-[var(--admin-text)]/70" />
              </Link>
              <div className="relative flex flex-1 flex-col space-y-4 p-5">
                <div className="space-y-2">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-500 shadow-lg">
                      <Icon className="h-5 w-5 text-white" />
                    </div>
                    <Link
                      to={`/templates/${template.slug}`}
                      className="text-[18px] font-bold tracking-tight text-[var(--admin-text)] transition-colors hover:text-blue-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                    >
                      {template.name}
                    </Link>
                  </div>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    {template.summary}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {template.tags.map((tag) => (
                    <span key={tag} className="admin-badge admin-badge-gray">
                      <Code2 size={12} className="mr-1" />
                      {tag}
                    </span>
                  ))}
                </div>
                <div className="mt-auto flex flex-col gap-2 pt-2 sm:flex-row">
                  <Link
                    to={`/templates/${template.slug}`}
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
                  >
                    Details <ArrowRight size={14} />
                  </Link>
                  {template.demoUrl && (
                    <a
                      href={template.demoUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
                    >
                      {template.demoLabel ? <Play size={14} /> : <ExternalLink size={14} />}
                      {template.demoLabel ?? "Demo"}
                      <ArrowUpRight size={14} />
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={() => copyCloneCmd(template.slug)}
                    aria-label={`Copy clone command for ${template.name}`}
                    className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
                  >
                    {copiedSlug === template.slug ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                    {copiedSlug === template.slug ? "Copied!" : "Clone"}
                  </button>
                </div>
              </div>
            </Card>
          );
        })}
      </section>

      {/* ── showcase + powered-by ── */}
      <section className="grid gap-4 md:grid-cols-2">
        <Card className="flex flex-col p-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-amber-500 to-rose-500 shadow-lg">
            <LayoutGrid className="h-6 w-6 text-white" />
          </div>
          <h3 className="mt-5 text-[18px] font-bold tracking-tight text-[var(--admin-text)]">
            Built something? Get featured.
          </h3>
          <p className="mt-2 flex-1 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Ship an app on any template and add it to the Showcase — a public, filterable
            gallery of apps built with the gateway. It&apos;s a deployable template itself,
            so you can host your own.
          </p>
          <div className="mt-5 flex flex-col gap-2 sm:flex-row">
            <Link
              to="/templates/showcase"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
            >
              See the showcase template <ArrowRight size={14} />
            </Link>
            <a
              href="https://github.com/theopenco/llmgateway-templates/issues/new?template=showcase-submission.yml"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              Submit your app
            </a>
          </div>
        </Card>
        <Card className="flex flex-col p-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-500 to-cyan-500 shadow-lg">
            <Wallet className="h-6 w-6 text-white" />
          </div>
          <h3 className="mt-5 text-[18px] font-bold tracking-tight text-[var(--admin-text)]">
            Add the Powered-By badge
          </h3>
          <p className="mt-2 flex-1 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Every app you deploy can carry a small &ldquo;Powered by the gateway&rdquo;
            badge. It ships with the embeddable credits template as a dependency-free
            copy you can drop into any footer.
          </p>
          <div className="mt-5">
            <Link
              to="/templates/embeddable-credits"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-4 py-2 text-[13px] font-semibold text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              See the template <ArrowRight size={14} />
            </Link>
          </div>
        </Card>
      </section>

      <p className="text-center text-[12px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
        all templates run against /v1 with a virtual key · model names come from your config
      </p>
    </div>
  );
}
