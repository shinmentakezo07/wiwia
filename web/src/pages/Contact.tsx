// Contact — the form does not send anything itself. It drafts a prefilled
// GitHub issue and hands control to the user, so the right-hand panel shows
// that draft live instead of pretending there is a hidden inbox behind it.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  BookOpen,
  Check,
  Copy,
  Github,
  GitPullRequest,
  MessageSquare,
  Send,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button, Card, Input } from "@/components/ui";
import { HERO_BEAMS_COMPACT, HeroBeamBackdrop } from "@/components/HeroBeamBackdrop";

const REPO_ISSUES = "https://github.com/shinmentakezo07/wiwia/issues";
const REPO_NEW_ISSUE = "https://github.com/shinmentakezo07/wiwia/issues/new";

const TEMPLATES: { label: string; body: string }[] = [
  {
    label: "Self-hosting",
    body: "Deployment:\n- wiwi version / commit:\n- How it runs (Docker, systemd, service):\n- Provider type and model:\n- What you expected vs what happened:\n- Relevant config (redact keys):\n",
  },
  {
    label: "Routing & failover",
    body: "Routing:\n- Model group and deployments:\n- Strategy / retries / cooldowns / fallbacks:\n- Request ID (x-wiwi-request-id):\n- Observed behaviour:\n",
  },
  {
    label: "Provider support",
    body: "Provider:\n- Provider type and model ID:\n- Dialect (Chat / Responses / Messages):\n- Redacted upstream error:\n- Does the vendor's own client work?\n",
  },
  {
    label: "Custom adapter",
    body: "What you need:\n- Upstream API or wire format:\n- Models and auth shape:\n- What the adapter must translate:\n- Timeline / who maintains it:\n",
  },
];

const INCLUDE: string[] = [
  "wiwi version and how it is deployed",
  "Provider type and native model ID",
  "Redacted upstream error or request ID",
  "What you expected, and what actually happened",
];

const CHANNELS: {
  icon: LucideIcon;
  label: string;
  value: string;
  href: string;
  external?: boolean;
}[] = [
  {
    icon: Github,
    label: "GitHub issues",
    value: "shinmentakezo07/wiwia",
    href: REPO_ISSUES,
    external: true,
  },
  {
    icon: BookOpen,
    label: "Documentation",
    value: "Read the docs",
    href: "/docs",
  },
  {
    icon: GitPullRequest,
    label: "Contribute",
    value: "Open a pull request",
    href: "https://github.com/shinmentakezo07/wiwia",
    external: true,
  },
];

function buildIssueUrl(name: string, email: string, message: string) {
  const title = `Contact: ${name}`.slice(0, 120);
  const body = `${message}\n\n— ${name} <${email}>`;
  const params = new URLSearchParams({ title, body });
  return `${REPO_NEW_ISSUE}?${params.toString()}`;
}

export function ContactPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [sent, setSent] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);

  const issueUrl = useMemo(() => buildIssueUrl(name, email, message), [name, email, message]);
  const draftTitle = `Contact: ${name.trim() || "your name"}`;
  const messageCount = message.length;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Same flow as before: hand the prefilled issue straight to the browser.
    // The confirmation panel still carries an explicit link, because a popup
    // blocker can swallow this and the draft must not become unreachable.
    window.open(issueUrl, "_blank", "noopener,noreferrer");
    setSent(true);
  }

  function resetForm() {
    setSent(false);
    setName("");
    setEmail("");
    setMessage("");
    setCopied(false);
    setCopyFailed(false);
  }

  async function copyDraft() {
    try {
      await navigator.clipboard.writeText(`${draftTitle}\n\n${message}`);
      setCopied(true);
      setCopyFailed(false);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
      setCopyFailed(true);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-12 pb-16">
      {/* ── hero: the honest pitch plus a live look at what gets sent ── */}
      <section className="relative overflow-hidden rounded-[24px] border border-[var(--admin-border)] bg-[var(--admin-surface)]">
        <div
          className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/[0.03] via-transparent to-transparent"
          aria-hidden
        />
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 h-px opacity-60" aria-hidden />

        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-x-0 top-0 h-[420px] opacity-60" />
          <div
            className="absolute -left-28 -top-32 h-[380px] w-[380px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(59,130,246,0.14) 0%, transparent 62%)" }}
          />
          <div
            className="absolute -right-32 top-10 h-[340px] w-[340px] rounded-full"
            style={{ background: "radial-gradient(circle, rgba(139,92,246,0.12) 0%, transparent 62%)" }}
          />
        </div>

        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="absolute inset-0 opacity-40" />

        <div className="relative grid gap-10 px-6 py-12 sm:px-10 sm:py-14 lg:grid-cols-[minmax(0,1.05fr)_minmax(320px,0.95fr)] lg:px-12 lg:py-16">
          <div className="self-center">
            <div className="inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/[0.06] px-3.5 py-1.5 text-blue-300">
              <MessageSquare size={12} aria-hidden />
              <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em]">
                Contact
              </span>
            </div>

            <h1 className="mt-5 text-3xl font-semibold leading-[1.1] tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl lg:text-5xl">
              Talk to the team{" "}
              <span className="hero-gradient-text">behind the gateway.</span>
            </h1>
            <p className="mt-5 max-w-lg text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
              Questions about self-hosting, routing, provider quirks, or a deployment that
              will not come up? The issue tracker is the direct line — this form drafts a
              clear, prefilled issue you review before anything is sent.
            </p>

            <ul className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2.5 text-[13px] text-[var(--admin-text-muted)]">
              {["No hidden inbox", "You review before it posts", "Keys stay redacted"].map((item) => (
                <li key={item} className="flex items-center gap-1.5">
                  <Check size={13} className="text-emerald-400" aria-hidden />
                  {item}
                </li>
              ))}
            </ul>
          </div>

          {/* Live draft — mirrors type-to-send, no surprises later */}
          <div
            role="img"
            aria-label="Live preview of the GitHub issue this form will create"
            className="self-center overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0d1117]/90 shadow-[0_28px_80px_-24px_rgba(0,0,0,0.8)] ring-1 ring-white/[0.04]"
          >
            <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-3">
              <div className="flex min-w-0 items-center gap-2">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/[0.06] bg-white/[0.03]">
                  <Github size={14} className="text-[var(--admin-text-muted)]" />
                </span>
                <span className="truncate font-mono text-[11px] text-[var(--admin-text-dim)]">
                  wiwia · new issue
                </span>
              </div>
              <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                Draft
              </span>
            </div>
            <div className="space-y-3 px-4 py-4 sm:px-5 sm:py-5">
              <p className="truncate text-[14px] font-semibold text-[var(--admin-text)]">
                {draftTitle}
              </p>
              <p className="max-h-32 overflow-y-auto whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--admin-text-muted)] sm:max-h-40">
                {message.trim()
                  ? message
                  : "Your message appears here as you type. Until you open it on GitHub and hit Submit, nothing is sent anywhere."}
              </p>
              <div className="flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-3">
                <span className="rounded-full border border-blue-400/15 bg-blue-500/[0.08] px-2 py-0.5 text-[10px] font-medium text-blue-300">
                  contact
                </span>
                <span className="rounded-full border border-white/[0.06] bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-[var(--admin-text-dim)]">
                  awaiting review
                </span>
                <span className="ml-auto font-mono text-[10px] text-[var(--admin-text-dim)]">
                  {messageCount} chars
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── form + sidebar ── */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(280px,1fr)]">
        {/* form */}
        <Card className="p-6 sm:p-7">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
                Send a message
              </h2>
              <p className="mt-1 text-[12px] text-[var(--admin-text-dim)]">
                Opens a prefilled GitHub issue.{" "}
                <span className="text-blue-400">*</span> required.
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.02] px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--admin-text-dim)]">
              <Github size={11} aria-hidden />
              Draft only
            </span>
          </div>

          {sent ? (
            <div
              role="status"
              className="mt-6 flex flex-col items-center justify-center rounded-2xl border border-emerald-400/15 bg-emerald-500/[0.04] px-6 py-12 text-center"
            >
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-emerald-400/20 bg-emerald-400/10">
                <Check size={24} className="text-emerald-400" aria-hidden />
              </div>
              <h3 className="mt-5 text-[17px] font-semibold text-[var(--admin-text)]">
                Issue drafted, nothing sent yet
              </h3>
              <p className="mt-1.5 max-w-md text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                Open the prefilled issue on GitHub, give it one last read, and hit{" "}
                <span className="font-medium text-[var(--admin-text)]">Create issue</span>. We do not
                see or store the draft until you submit it.
              </p>
              <div className="mt-6 flex flex-col gap-2 sm:flex-row">
                <Button href={issueUrl} target="_blank" rel="noopener noreferrer" className="wiwi-shimmer min-h-11 px-5">
                  Review issue on GitHub
                  <ArrowUpRight size={15} aria-hidden />
                </Button>
                <Button variant="ghost" className="min-h-11 px-5" onClick={resetForm}>
                  Start over
                </Button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="mt-6 space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="admin-label mb-1.5 block">
                    Name <span className="text-blue-400">*</span>
                  </span>
                  <Input
                    required
                    autoComplete="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Ada Lovelace"
                    className="min-h-11"
                  />
                </label>
                <label className="block">
                  <span className="admin-label mb-1.5 block">
                    Email <span className="text-blue-400">*</span>
                  </span>
                  <Input
                    type="email"
                    required
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="min-h-11"
                  />
                </label>
              </div>

              <fieldset className="space-y-2">
                <legend className="admin-label">Quick templates</legend>
                <p className="text-[11.5px] text-[var(--admin-text-dim)]">
                  Insert a structured prompt for what you are working on.
                </p>
                <div className="flex flex-wrap gap-2 pt-1">
                  {TEMPLATES.map((t) => (
                    <button
                      key={t.label}
                      type="button"
                      onClick={() => setMessage(t.body)}
                      className="min-h-11 rounded-full border border-white/[0.07] bg-white/[0.02] px-3.5 text-[12.5px] font-medium text-[var(--admin-text-muted)] transition-colors hover:border-blue-500/30 hover:bg-blue-500/[0.06] hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 active:scale-[0.98]"
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </fieldset>

              <label className="block">
                <span className="mb-1.5 flex items-baseline justify-between gap-3">
                  <span className="admin-label">
                    Message <span className="text-blue-400">*</span>
                  </span>
                  <span className="font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                    {messageCount.toLocaleString()} chars
                  </span>
                </span>
                <textarea
                  required
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={7}
                  placeholder="Tell us what you are running, what you expected, and what happened…"
                  className="admin-input min-h-[132px] w-full resize-y"
                />
              </label>

              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="order-2 text-[11.5px] leading-snug text-[var(--admin-text-dim)] sm:order-1 sm:max-w-xs">
                  Nothing is posted until you review and submit the issue on GitHub.
                </p>
                <Button type="submit" className="wiwi-shimmer min-h-12 px-6 text-[14px] sm:order-2">
                  Draft issue
                  <Send size={15} aria-hidden />
                </Button>
              </div>
            </form>
          )}
        </Card>

        {/* sidebar */}
        <div className="space-y-4">
          <Card className="p-5">
            <span className="admin-label mb-3 block">Fastest channels</span>
            <ul className="space-y-2">
              {CHANNELS.map((c) => {
                const Icon = c.icon;
                const inner = (
                  <>
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/[0.06] bg-gradient-to-br from-blue-500/15 to-violet-500/10 text-blue-300 transition-colors group-hover:border-blue-500/25 group-hover:text-blue-200">
                      <Icon size={17} aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[12px] text-[var(--admin-text-dim)]">
                        {c.label}
                      </span>
                      <span className="mt-0.5 block truncate text-[13px] font-medium text-[var(--admin-text)] transition-colors group-hover:text-blue-300">
                        {c.value}
                      </span>
                    </span>
                    {c.external && (
                      <ArrowUpRight
                        size={16}
                        className="shrink-0 text-[var(--admin-text-dim)] transition-all group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-blue-400"
                        aria-hidden
                      />
                    )}
                  </>
                );
                const className =
                  "group flex min-h-11 items-center gap-3 rounded-xl border border-transparent px-2 py-2 transition-colors hover:border-white/[0.06] hover:bg-white/[0.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50";
                return (
                  <li key={c.label}>
                    {c.external ? (
                      <a
                        href={c.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={className}
                      >
                        {inner}
                        <span className="sr-only">(opens in a new tab)</span>
                      </a>
                    ) : (
                      <Link to={c.href} className={className}>
                        {inner}
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
          </Card>

          <Card className="p-5">
            <span className="admin-label mb-3 block">What to include</span>
            <ul className="space-y-2.5">
              {INCLUDE.map((item) => (
                <li key={item} className="flex items-start gap-2 text-[12.5px] leading-snug text-[var(--admin-text-muted)]">
                  <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" aria-hidden />
                  {item}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="p-5">
            <span className="admin-label mb-2 block">How this reaches us</span>
            <p className="text-[12.5px] leading-relaxed text-[var(--admin-text-muted)]">
              There is no support inbox behind this site. The form opens a prefilled issue on{" "}
              <a
                href={REPO_ISSUES}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center font-medium text-blue-400 transition-colors hover:text-blue-300"
              >
                GitHub
              </a>
              . Public issues are picked up best-effort;{" "}
              <Link to="/enterprise" className="inline-flex min-h-11 items-center font-medium text-blue-400 transition-colors hover:text-blue-300">
                Enterprise
              </Link>{" "}
              gets a direct line and priority triage.
            </p>
            <button
              type="button"
              onClick={copyDraft}
              className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-[10px] border border-white/[0.07] bg-white/[0.02] px-3.5 text-[12.5px] font-medium text-[var(--admin-text-muted)] transition-colors hover:border-white/[0.12] hover:text-[var(--admin-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50"
            >
              {copied ? (
                <Check size={14} className="text-emerald-400" aria-hidden />
              ) : (
                <Copy size={14} className={copyFailed ? "text-amber-400" : undefined} aria-hidden />
              )}
              {copied ? "Draft copied" : copyFailed ? "Copy blocked — select the text" : "Copy draft"}
            </button>
          </Card>
        </div>
      </section>
    </div>
  );
}
