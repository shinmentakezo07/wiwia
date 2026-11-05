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
    <div className="mx-auto max-w-6xl space-y-8 pb-16 sm:space-y-14">
      {/* ── hero: the honest pitch plus a live look at what gets sent ── */}
      <section className="relative isolate overflow-hidden rounded-[28px] border border-white/[0.08] bg-[#08090c] shadow-[0_24px_80px_-48px_rgba(0,0,0,0.9)]">
        <div className="wiwi-top-highlight pointer-events-none absolute inset-x-0 top-0 z-10 h-px opacity-70" aria-hidden />
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="hero-grid absolute inset-0 opacity-40 [mask-image:linear-gradient(to_bottom,black,transparent_90%)]" />
          <div className="absolute -left-32 -top-48 h-[420px] w-[420px] rounded-full bg-[radial-gradient(circle,rgba(59,130,246,0.13),transparent_65%)]" />
          <div className="absolute -right-32 -top-24 h-[400px] w-[400px] rounded-full bg-[radial-gradient(circle,rgba(139,92,246,0.1),transparent_65%)]" />
        </div>
        <HeroBeamBackdrop beams={HERO_BEAMS_COMPACT} className="opacity-25" />

        <div className="relative grid gap-10 px-6 py-10 sm:px-10 sm:py-14 lg:grid-cols-[minmax(0,1fr)_minmax(340px,0.8fr)] lg:items-center lg:gap-16 lg:px-14 lg:py-16">
          <div>
            <div className="flex items-center gap-2.5 font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-blue-300">
              <MessageSquare size={14} aria-hidden />
              <span>Contact the maintainers</span>
            </div>
            <h1 className="mt-6 max-w-xl text-[38px] font-semibold leading-[1.08] tracking-[-0.04em] text-[var(--admin-text)] sm:text-5xl lg:text-[56px]">
              Let’s build a better{" "}
              <span className="hero-gradient-text">gateway.</span>
            </h1>
            <p className="mt-5 max-w-xl text-[15px] leading-7 text-[var(--admin-text-muted)] sm:text-base">
              Self-hosting, routing, provider quirks, or a deployment that will not come up?
              Tell us what you’re working on. We’ll turn it into a clear GitHub issue you
              review before anything is posted.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-3 text-[12px] text-[var(--admin-text-muted)]">
              {["No hidden inbox", "Review before posting", "Keys stay redacted"].map((item) => (
                <span key={item} className="flex items-center gap-2">
                  <Check size={14} className="text-emerald-400" aria-hidden />
                  {item}
                </span>
              ))}
            </div>
          </div>

          <div className="relative self-center lg:pl-4">
            <div className="pointer-events-none absolute -inset-8 rounded-full bg-blue-500/[0.06] blur-3xl" aria-hidden />
            <div
              role="img"
              aria-label="Live preview of the GitHub issue this form will create"
              className="relative overflow-hidden rounded-2xl border border-white/[0.1] bg-[#0d1117]/95 shadow-[0_24px_70px_-32px_rgba(0,0,0,0.9)]"
            >
              <div className="flex items-center justify-between gap-3 border-b border-white/[0.08] bg-white/[0.02] px-4 py-3.5">
                <div className="flex min-w-0 items-center gap-2.5">
                  <Github size={15} className="shrink-0 text-[var(--admin-text-muted)]" aria-hidden />
                  <span className="truncate font-mono text-[11px] text-[var(--admin-text-muted)]">
                    wiwia / new issue
                  </span>
                </div>
                <span className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  Draft
                </span>
              </div>
              <div className="space-y-4 px-5 py-5">
                <p className="truncate text-[14px] font-semibold text-[var(--admin-text)]">
                  {draftTitle}
                </p>
                <p className="max-h-32 overflow-y-auto whitespace-pre-wrap text-[13px] leading-relaxed text-[var(--admin-text-muted)] sm:max-h-40">
                  {message.trim()
                    ? message
                    : "Your message appears here as you type. Nothing is sent until you review it on GitHub and hit Submit."}
                </p>
                <div className="flex flex-wrap items-center gap-2 border-t border-white/[0.07] pt-3">
                  <span className="rounded-md border border-blue-400/15 bg-blue-500/[0.08] px-2 py-1 text-[10px] font-medium text-blue-300">
                    contact
                  </span>
                  <span className="rounded-md border border-white/[0.07] px-2 py-0.5 text-[10px] text-[var(--admin-text-dim)]">
                    awaiting review
                  </span>
                  <span className="ml-auto font-mono text-[10px] tabular-nums text-[var(--admin-text-dim)]">
                    {messageCount} chars
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── form + sidebar ── */}
      <section className="grid grid-cols-1 overflow-hidden rounded-2xl border border-white/[0.08] bg-[var(--admin-surface)] shadow-[0_20px_60px_-48px_rgba(0,0,0,0.9)] lg:grid-cols-[minmax(0,1.5fr)_minmax(280px,0.85fr)]">
        <Card className="rounded-none border-0 bg-transparent p-6 shadow-none sm:p-8 lg:p-9">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/[0.07] pb-6">
            <div>
              <div className="flex items-center gap-2 font-mono text-[10px] font-medium uppercase tracking-[0.16em] text-blue-300">
                <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
                Start a conversation
              </div>
              <h2 className="mt-2.5 text-2xl font-semibold tracking-[-0.025em] text-[var(--admin-text)]">
                Send a message
              </h2>
              <p className="mt-2 text-[13px] text-[var(--admin-text-muted)]">
                A few details are all we need to get started.
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.02] px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--admin-text-muted)]">
              <Github size={12} aria-hidden />
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
            <form onSubmit={handleSubmit} className="mt-7 space-y-6">
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
                    className="min-h-12 rounded-[10px] border-white/[0.09] bg-white/[0.025] px-3.5 text-[14px] placeholder:text-[var(--admin-text-dim)] focus:border-blue-400/60 focus:bg-blue-500/[0.025] focus:ring-2 focus:ring-blue-400/20"
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
                    className="min-h-12 rounded-[10px] border-white/[0.09] bg-white/[0.025] px-3.5 text-[14px] placeholder:text-[var(--admin-text-dim)] focus:border-blue-400/60 focus:bg-blue-500/[0.025] focus:ring-2 focus:ring-blue-400/20"
                  />
                </label>
              </div>

              <fieldset className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-4">
                <legend className="admin-label px-1">Quick templates</legend>
                <p className="mt-1 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
                  Start with a structured prompt, then make it yours.
                </p>
                <div className="flex flex-wrap gap-2 pt-2">
                  {TEMPLATES.map((t) => (
                    <button
                      key={t.label}
                      type="button"
                      onClick={() => setMessage(t.body)}
                      className="min-h-11 rounded-lg border border-white/[0.09] bg-white/[0.025] px-3.5 text-[12px] font-medium text-[var(--admin-text-muted)] transition-colors hover:border-blue-500/30 hover:bg-blue-500/[0.06] hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/50 active:scale-[0.98]"
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
                  className="admin-input min-h-[160px] w-full resize-y rounded-[10px] border-white/[0.09] bg-white/[0.025] px-3.5 py-3 text-[14px] leading-6 placeholder:text-[var(--admin-text-dim)] focus:border-blue-400/60 focus:bg-blue-500/[0.025] focus:ring-2 focus:ring-blue-400/20"
                />
              </label>

              <div className="flex flex-col gap-4 border-t border-white/[0.07] pt-5 sm:flex-row sm:items-center sm:justify-between">
                <p className="order-2 flex items-start gap-2 text-[12px] leading-relaxed text-[var(--admin-text-dim)] sm:order-1 sm:max-w-xs">
                  <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" aria-hidden />
                  Nothing is posted until you review it on GitHub.
                </p>
                <Button type="submit" className="wiwi-shimmer min-h-12 w-full px-6 text-[14px] sm:order-2 sm:w-auto">
                  Draft issue
                  <Send size={15} aria-hidden />
                </Button>
              </div>
            </form>
          )}
        </Card>

        {/* sidebar */}
        <aside className="border-t border-white/[0.08] bg-white/[0.015] lg:border-l lg:border-t-0">
          <Card className="rounded-none border-0 bg-transparent p-6 shadow-none sm:p-7">
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-blue-400/15 bg-blue-500/[0.08] text-blue-300">
                <MessageSquare size={14} aria-hidden />
              </span>
              <h2 className="text-[14px] font-semibold text-[var(--admin-text)]">Other ways to connect</h2>
            </div>
            <ul className="space-y-2">
              {CHANNELS.map((c) => {
                const Icon = c.icon;
                const inner = (
                  <>
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.025] text-[var(--admin-text-muted)] transition-colors group-hover:border-blue-500/25 group-hover:text-blue-300">
                      <Icon size={16} aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[11px] text-[var(--admin-text-dim)]">
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

          <Card className="rounded-none border-0 border-t border-white/[0.08] bg-transparent p-6 shadow-none sm:p-7">
            <h2 className="text-[14px] font-semibold text-[var(--admin-text)]">A useful first message</h2>
            <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--admin-text-dim)]">
              Include these details to help us reproduce the issue.
            </p>
            <ul className="mt-4 space-y-3">
              {INCLUDE.map((item) => (
                <li key={item} className="flex items-start gap-2.5 text-[12px] leading-relaxed text-[var(--admin-text-muted)]">
                  <Check size={14} className="mt-0.5 shrink-0 text-emerald-400" aria-hidden />
                  {item}
                </li>
              ))}
            </ul>
          </Card>

          <Card className="rounded-none border-0 border-t border-white/[0.08] bg-transparent p-6 shadow-none sm:p-7">
            <span className="admin-label mb-2 block">How this reaches us</span>
            <p className="text-[12.5px] leading-relaxed text-[var(--admin-text-muted)]">
              There is no support inbox behind this site. The form opens a prefilled issue on{" "}
              <a
                href={REPO_ISSUES}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center px-1 font-medium text-blue-400 transition-colors hover:text-blue-300"
              >
                GitHub
              </a>
              . Public issues are picked up best-effort;{" "}
              <Link to="/enterprise" className="inline-flex min-h-11 items-center px-1 font-medium text-blue-400 transition-colors hover:text-blue-300">
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
        </aside>
      </section>
    </div>
  );
}
